"""Durable, searchable recordings; tools return references before raw material."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import unquote

from .resource_limits import resource_limit


class RecordingStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'events.sqlite3'
        self._database = sqlite3.connect(self.path)
        self._database.row_factory = sqlite3.Row
        self._database.execute('PRAGMA journal_mode=WAL')
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE,
                    kind TEXT, page_id TEXT, frame_id TEXT, turn_id TEXT,
                    connection_id TEXT, request_id TEXT, timestamp REAL,
                    searchable TEXT, material TEXT, sha256 TEXT, size INTEGER);
                CREATE INDEX IF NOT EXISTS event_connection ON events(connection_id, sequence);
                CREATE INDEX IF NOT EXISTS event_request ON events(request_id, sequence);
                CREATE INDEX IF NOT EXISTS event_page ON events(page_id, sequence);
                CREATE INDEX IF NOT EXISTS event_kind ON events(kind, sequence);
                CREATE TABLE IF NOT EXISTS recording_quota (
                    id INTEGER PRIMARY KEY CHECK(id = 1), captured_bytes INTEGER NOT NULL);
                INSERT OR IGNORE INTO recording_quota(id, captured_bytes)
                    SELECT 1, COALESCE(SUM(size), 0) FROM events;
            ''')

    def _connect(self):
        return self._database

    def close(self):
        self._database.close()

    def _material(self, row):
        material = row['material']
        if '"__blob_sha256"' not in material:
            return material
        stub = json.loads(material)
        if stub.get('evicted'):
            return material
        path = self.directory / 'blobs' / (stub['__blob_sha256'] + '.json.gz')
        try:
            raw = gzip.decompress(path.read_bytes())
        except (OSError, ValueError):
            raise ValueError('recording_blob_invalid') from None
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('recording_blob_invalid')
        return raw.decode('utf-8')

    def _evict_old_dom(self, db, needed):
        """FIFO only for oversized DOM material; preserve request/response evidence."""
        removed = []
        while db.execute('SELECT captured_bytes FROM recording_quota WHERE id=1').fetchone()[0] + needed > resource_limit('MAX_RECORDING_BYTES'):
            row = db.execute("SELECT sequence,material,size FROM events WHERE kind='dom_mutation' "
                             "AND instr(material, '\"__blob_sha256\"') > 0 "
                             "AND json_extract(material, '$.evicted') IS NULL "
                             'ORDER BY sequence LIMIT 1').fetchone()
            if row is None:
                return None
            stub = json.loads(row['material'])
            if stub.get('evicted'):
                return None
            compact = json.dumps({'event_id': stub['event_id'], 'kind': 'dom_mutation',
                'evicted': True, 'reason': 'dom_fifo_retention',
                'original_sha256': stub['__blob_sha256'],
                'raw_bytes': stub['raw_bytes']}, ensure_ascii=False)
            freed = row['size'] - len(compact.encode('utf-8'))
            db.execute('UPDATE events SET material=?, searchable=?, size=? WHERE sequence=?',
                       (compact, compact, len(compact.encode('utf-8')), row['sequence']))
            db.execute('UPDATE recording_quota SET captured_bytes=captured_bytes-? WHERE id=1',
                       (freed,))
            removed.append(self.directory / 'blobs' /
                           (stub['__blob_sha256'] + '.json.gz'))
        return removed

    def recording_start(self, submission_event_id: str) -> int:
        """Exclusive cursor before the browser capture that submitted this demo."""
        prefix, marker, _ = submission_event_id.rpartition('-event-')
        if not marker or not prefix.startswith('capture-'):
            raise ValueError('recording_submission_ref_invalid')
        with self._connect() as db:
            row = db.execute('SELECT MIN(sequence) FROM events WHERE event_id GLOB ?',
                (prefix + '-event-*',)).fetchone()
        if row is None or row[0] is None:
            raise ValueError('recording_submission_ref_invalid')
        return int(row[0]) - 1

    def sequence(self, event_id: str) -> int:
        with self._connect() as db:
            row = db.execute('SELECT sequence FROM events WHERE event_id = ?', (event_id,)).fetchone()
        if row is None:
            raise ValueError('recording_event_unknown')
        return int(row[0])

    def append(self, event: dict) -> str:
        value = dict(event)
        value.setdefault('utc_time', datetime.now(timezone.utc).isoformat())
        value.setdefault('monotonic_seconds', time.monotonic())
        material = json.dumps(value, ensure_ascii=False, allow_nan=False)
        raw = material.encode('utf-8')
        searchable = material
        for field in ('url', 'body', 'text'):
            if isinstance(value.get(field), str) and '%' in value[field]:
                searchable += '\n' + unquote(value[field])
        if isinstance(value.get('binary_b64'), str):
            searchable += base64.b64decode(value['binary_b64'], validate=True).decode('utf-8', errors='replace')
        raw_sha = hashlib.sha256(raw).hexdigest()
        stored_size = len(raw)
        if value.get('kind') == 'dom_mutation' and len(raw) > 65536:
            compressed = gzip.compress(raw, compresslevel=6, mtime=0)
            blob_dir = self.directory / 'blobs'
            blob_dir.mkdir(parents=True, exist_ok=True)
            blob = blob_dir / (raw_sha + '.json.gz')
            if not blob.is_file():
                temporary = blob_dir / (raw_sha + '.' + str(time.time_ns()) + '.tmp')
                temporary.write_bytes(compressed)
                os.replace(temporary, blob)
            material = json.dumps({'event_id': value.get('event_id'),
                'kind': 'dom_mutation', '__blob_sha256': raw_sha,
                'raw_bytes': len(raw), 'utc_time': value['utc_time'],
                'monotonic_seconds': value['monotonic_seconds']}, ensure_ascii=False)
            searchable = material + '\n' + raw[:4096].decode('utf-8', errors='replace')
            stored_size = len(compressed) + len(material.encode('utf-8'))
        removed = []
        with self._connect() as db:
            if db.execute('SELECT captured_bytes FROM recording_quota WHERE id=1').fetchone()[0] + stored_size > resource_limit('MAX_RECORDING_BYTES'):
                removed = self._evict_old_dom(db, stored_size)
                if removed is None:
                    raise ValueError('recording_capture_limit')
            updated = db.execute('UPDATE recording_quota SET captured_bytes = captured_bytes + ? '
                                 'WHERE id = 1 AND captured_bytes + ? <= ?',
                                 (stored_size, stored_size, resource_limit('MAX_RECORDING_BYTES')))
            if updated.rowcount != 1:
                raise ValueError('recording_capture_limit')
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM events').fetchone()[0]
            event_id = value.get('event_id') or f'event-{sequence}'
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                sequence, event_id, value.get('kind', 'material'), value.get('page_id'),
                value.get('frame_id'), value.get('turn_id'), value.get('connection_id'),
                value.get('request_event_id', value.get('request_id')),
                value['monotonic_seconds'], searchable, material,
                raw_sha, stored_size))
        for path in removed:
            path.unlink(missing_ok=True)
        return event_id

    def query_recording(self, query: str = '', kind: str = '', page_id: str = '',
                        connection_id: str = '', request_id: str = '', turn_id: str = '',
                        after: int = 0, limit: int = 50,
                        time_start: float | None = None, time_end: float | None = None,
                        frame_id: str = '') -> dict:
        """Search ordered event metadata. after is an exclusive sequence cursor."""
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('recording_cursor_invalid')
        conditions, args = ['sequence > ?'], [after]
        for field, value in [('kind', kind), ('page_id', page_id), ('connection_id', connection_id),
                             ('request_id', request_id), ('turn_id', turn_id), ('frame_id', frame_id)]:
            if value:
                conditions.append(field + ' = ?')
                args.append(value)
        if query:
            conditions.append('instr(searchable, ?) > 0')
            args.append(query)
        for op, value in [('>=', time_start), ('<=', time_end)]:
            if value is not None:
                conditions.append('timestamp ' + op + ' ?')
                args.append(value)
        where = ' AND '.join(conditions)
        with self._connect() as db:
            rows = db.execute('SELECT sequence,event_id,kind,page_id,frame_id,turn_id,connection_id,request_id,timestamp,sha256,size, '
                              "json_extract(material,'$.url') AS url, json_extract(material,'$.resource_type') AS resource_type "
                              'FROM events WHERE ' + where + ' ORDER BY sequence LIMIT ?', [*args, limit + 1]).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {'events': items, 'has_more': len(rows) > limit,
                'next_after': items[-1]['sequence'] if len(rows) > limit else None}

    def read_material(self, event_id: str, offset: int = 0, limit: int = 16384) -> dict:
        """Read a bounded JSON-text range, with immutable hash and total length."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536:
            raise ValueError('recording_range_invalid')
        with self._connect() as db:
            row = db.execute('SELECT material,sha256,size FROM events WHERE event_id=?', (event_id,)).fetchone()
        if row is None:
            raise ValueError('recording_event_unknown')
        material = self._material(row)
        if json.loads(row['material']).get('evicted'):
            return {'event_id': event_id, 'evicted': True,
                'reason': 'dom_fifo_retention', 'text': '', 'total_chars': 0,
                'total_bytes': row['size'], 'sha256': row['sha256']}
        if offset > len(material):
            raise ValueError('recording_range_invalid')
        end = min(offset + limit, len(material))
        return {'event_id': event_id, 'text': material[offset:end], 'offset': offset,
                'next_offset': end if end < len(material) else None,
                'complete': offset == 0 and end == len(material),
                'total_chars': len(material), 'total_bytes': len(material.encode('utf-8')),
                'stored_bytes': row['size'], 'sha256': row['sha256']}

    def event(self, event_id: str) -> dict:
        """Host-only full event access; Agent tools use bounded read_material."""
        with self._connect() as db:
            row = db.execute('SELECT event_id,material,sha256 FROM events WHERE event_id=?', (event_id,)).fetchone()
        if row is None:
            raise ValueError('recording_event_unknown')
        value = json.loads(self._material(row))
        if value.get('evicted'):
            value['event_id'] = row['event_id']
        return value
