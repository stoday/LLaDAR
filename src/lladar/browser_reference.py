"""Independent DOM references, enabled by trusted full-text qualification.

This reader never sees an integration program, its selected material, or its
answer. A separate browser traversal retains complete visible DOM subtrees.
Automatic reference is deliberately unavailable when those subtrees cannot
reproduce the original text exactly or a qualified terminal guard disappears.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import secrets
from urllib.parse import urlsplit

from .answer_extraction import ExtractionError, ExtractionOptions, ResponseExtractor, contains_sensitive_text
from .resource_limits import resource_limit
from .response_capture import ResponseObservationSource


class ReferenceUnavailable(RuntimeError):
    reason = code = 'reference_unavailable'
    def __init__(self):
        super().__init__(self.reason)


# This traversal is independent of GenericBrowserDriver's filtered snapshot.
# Limits reject the entire capture. No prefix, clipped node, or retained tail
# is presented as complete evidence. The observer is installed before submit.
_BEGIN = r'''settings => {
  const key=settings.key;
  const state={ids:new WeakMap(),next:0,records:[],overflow:false};
  state.id=node=>{if(!state.ids.has(node))state.ids.set(node,++state.next);return state.ids.get(node);};
  state.collect=records=>{
    for(let i=0;i<records.length;i++){
      const record=records[i];
      if(record.type!=='attributes'||record.attributeName!=='aria-busy')continue;
      const following=records.slice(i+1).find(r=>r.target===record.target&&r.attributeName===record.attributeName);
      const after=following?following.oldValue:record.target.getAttribute(record.attributeName);
      if(state.records.length>=settings.nodes){state.overflow=true;continue;}
      state.records.push({id:state.id(record.target),attribute:record.attributeName,before:record.oldValue,after});
    }
  };
  state.observer=new MutationObserver(state.collect);
  state.observer.observe(document,{subtree:true,attributes:true,attributeOldValue:true,attributeFilter:['aria-busy']});
  globalThis[key]=state;
  return true;
}'''

_SNAPSHOT = r'''settings => {
  const state=globalThis[settings.key];
  if(!state||!document.body)return {invalid:true};
  state.collect(state.observer.takeRecords());
  const nodes=[];let size=0;
  for(const node of [document.body,...document.body.querySelectorAll('*')]){
    if(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE'].includes(node.tagName))continue;
    if(nodes.length>=settings.nodes)return {overflow:true};
    const style=getComputedStyle(node),rect=node.getBoundingClientRect();
    const visible=style.display!=='none'&&style.visibility!=='hidden'&&rect.width>0&&rect.height>0;
    const privateField=node.matches('input[type=password],input[autocomplete="one-time-code"]')||
      (node.matches('input,textarea,[contenteditable]')&&/password|token|secret|credential|otp/i.test([node.name,node.id,node.getAttribute('autocomplete')].join(' ')));
    const value={id:state.id(node),parent:node.parentElement&&node.parentElement!==document.documentElement?state.id(node.parentElement):null,
      tag:node.tagName.toLowerCase(),visible,private:privateField,
      text:visible&&!privateField?[...node.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join(''):'',
      rendered_text:visible&&!privateField?(node.innerText||''):'',
      attributes:Object.fromEntries([...node.attributes].filter(a=>['role','aria-busy','aria-setsize','aria-posinset'].includes(a.name)).map(a=>[a.name,a.value])),
      unsupported:!!node.shadowRoot||node.tagName==='CANVAS'||node.matches('[aria-setsize]')};
    size+=JSON.stringify(value).length;
    if(size>settings.characters)return {overflow:true};
    nodes.push(value);
  }
  return {nodes,records:[...state.records],overflow:state.overflow};
}'''

_END = r'''key => {const state=globalThis[key];if(state){state.observer.disconnect();delete globalThis[key];}}'''


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


@dataclass(frozen=True, repr=False)
class ReferenceBaseline:
    turn_id: str
    _owner: object = field(repr=False)
    _page: object = field(repr=False)
    _frames: tuple = field(repr=False)
    _keys: tuple = field(repr=False)
    _before: tuple = field(repr=False)
    _capture: object = field(repr=False)
    _binding: str = field(repr=False)


@dataclass(frozen=True, repr=False)
class ReferenceTurn:
    turn_id: str
    _owner: object = field(repr=False)
    _before: tuple = field(repr=False)
    _after: tuple = field(repr=False)
    _capture: object = field(repr=False)
    _binding: str = field(repr=False)
    _page: object = field(repr=False)
    _frames: tuple = field(repr=False)


class DOMReferenceReader:
    """Tool-free extraction from a separately captured, qualified DOM source.

    ``qualify`` accepts only host-confirmed complete text. It is never a model
    tool. Three distinct same-profile turns are required by default. Automatic
    formal reading requires a fresh full subtree and the independently observed
    aria-busy true-to-false terminal transition reproduced by those turns.
    """
    def __init__(self, options: ExtractionOptions, *, model_started,
                 protected_values=(), minimum_qualifications=3):
        if (type(minimum_qualifications) is not int or minimum_qualifications < 3
                or not callable(model_started)):
            raise ReferenceUnavailable()
        self.options, self.model_started = options, model_started
        self.protected_values = tuple(protected_values)
        self.minimum_qualifications = minimum_qualifications
        self._owner, self._source = object(), ResponseObservationSource()
        self._started, self._finished, self._qualified_turns = set(), set(), set()
        self._baselines = {}
        self._profile = None

    @property
    def qualified(self):
        return len(self._qualified_turns) >= self.minimum_qualifications

    @property
    def qualification_count(self):
        return len(self._qualified_turns)

    def _settings(self, key):
        return {'key': key, 'nodes': resource_limit('DOM_NODE_LIMIT'),
                'characters': resource_limit('DOM_CAPTURE_CHARS')}

    def _capture_frames(self, frames, keys):
        result = []
        try:
            for index, (frame, key) in enumerate(zip(frames, keys, strict=True)):
                snapshot = frame.evaluate(_SNAPSHOT, self._settings(key))
                if type(snapshot) is not dict or snapshot.get('overflow') or snapshot.get('invalid'):
                    raise ReferenceUnavailable()
                parsed = urlsplit(frame.url)
                # Routing identity contains no query string, userinfo or page title.
                origin = f'{parsed.scheme}://{parsed.hostname}:{parsed.port or (443 if parsed.scheme == "https" else 80)}' if parsed.hostname else parsed.scheme
                snapshot['origin'], snapshot['frame_index'] = origin, index
                result.append(snapshot)
            encoded = _json(result)
            if (len(encoded) > resource_limit('DOM_CAPTURE_CHARS')
                    or len(encoded.encode('utf-8')) > resource_limit('MAX_CAPTURE_BYTES')
                    or contains_sensitive_text(encoded, self.protected_values,
                        allow_credential_patterns=self.options.allow_recorded_credentials)):
                raise ReferenceUnavailable()
            return tuple(result)
        except ReferenceUnavailable:
            raise
        except Exception:
            raise ReferenceUnavailable() from None

    def begin_turn(self, turn_id: str, page) -> ReferenceBaseline:
        if type(turn_id) is not str or not turn_id or turn_id in self._started:
            raise ReferenceUnavailable()
        frames = tuple(page.frames)
        keys = tuple('lladar_reference_'+secrets.token_hex(16) for _ in frames)
        if not frames:
            raise ReferenceUnavailable()
        try:
            for frame, key in zip(frames, keys, strict=True):
                frame.evaluate(_BEGIN, self._settings(key))
            before = self._capture_frames(frames, keys)
        except Exception:
            self._close(frames, keys)
            raise ReferenceUnavailable() from None
        self._started.add(turn_id)
        baseline = ReferenceBaseline(turn_id, self._owner, page, frames, keys, before,
            self._source.begin_request(turn_id), hashlib.sha256(_json(before).encode()).hexdigest())
        self._baselines[turn_id] = baseline
        return baseline

    @staticmethod
    def _close(frames, keys):
        for frame, key in zip(frames, keys):
            try:
                frame.evaluate(_END, key)
            except Exception:
                pass

    def abandon_turn(self, baseline: ReferenceBaseline) -> None:
        """Consume an unsubmitted host baseline without reading or qualifying it.

        Only the exact opaque object issued by this reader may close its frame
        observers. Its ID stays used; a repaired turn needs a new reference ID.
        """
        if (type(baseline) is not ReferenceBaseline or baseline._owner is not self._owner
                or self._baselines.get(baseline.turn_id) is not baseline
                or baseline.turn_id in self._finished):
            raise ReferenceUnavailable()
        self._finished.add(baseline.turn_id)
        del self._baselines[baseline.turn_id]
        self._close(baseline._frames, baseline._keys)

    def finish_turn(self, baseline: ReferenceBaseline, page) -> ReferenceTurn:
        if (type(baseline) is not ReferenceBaseline or baseline._owner is not self._owner
                or self._baselines.get(baseline.turn_id) is not baseline
                or baseline._page is not page or baseline.turn_id in self._finished
                or baseline._binding != hashlib.sha256(_json(baseline._before).encode()).hexdigest()):
            raise ReferenceUnavailable()
        try:
            if tuple(page.frames) != baseline._frames:
                raise ReferenceUnavailable()
            after = self._capture_frames(baseline._frames, baseline._keys)
            reproduced = self._capture_frames(baseline._frames, baseline._keys)
            if after != reproduced:
                raise ReferenceUnavailable()
            payload = {'kind': 'independent_dom_reference', 'before': baseline._before, 'after': after}
            encoded = _json(payload)
            if len(encoded.encode('utf-8')) > self.options.input_byte_limit:
                raise ReferenceUnavailable()
            return ReferenceTurn(baseline.turn_id, self._owner, baseline._before, after,
                baseline._capture, hashlib.sha256(encoded.encode()).hexdigest(), page, baseline._frames)
        finally:
            self._finished.add(baseline.turn_id)
            del self._baselines[baseline.turn_id]
            self._close(baseline._frames, baseline._keys)

    def _payload(self, reference):
        if type(reference) is not ReferenceTurn or reference._owner is not self._owner:
            raise ReferenceUnavailable()
        payload = {'kind': 'independent_dom_reference', 'before': reference._before, 'after': reference._after}
        encoded = _json(payload)
        if reference._binding != hashlib.sha256(encoded.encode()).hexdigest():
            raise ReferenceUnavailable()
        return encoded

    @staticmethod
    def _snapshot_fingerprint(frames):
        normalized = []
        for frame in frames:
            identities = {node['id']: index for index, node in enumerate(frame['nodes'])}
            nodes = [{**node, 'id': identities[node['id']], 'parent': identities.get(node['parent'])}
                     for node in frame['nodes']]
            normalized.append({'origin': frame['origin'], 'frame_index': frame['frame_index'], 'nodes': nodes})
        return hashlib.sha256(_json(normalized).encode()).hexdigest()

    def _reproduce_current_source(self, reference):
        keys = tuple('lladar_reference_'+secrets.token_hex(16) for _ in reference._frames)
        try:
            if tuple(reference._page.frames) != reference._frames:
                raise ReferenceUnavailable()
            for frame, key in zip(reference._frames, keys, strict=True):
                frame.evaluate(_BEGIN, self._settings(key))
            current = self._capture_frames(reference._frames, keys)
            if self._snapshot_fingerprint(current) != self._snapshot_fingerprint(reference._after):
                raise ReferenceUnavailable()
        except ReferenceUnavailable:
            raise
        except Exception:
            raise ReferenceUnavailable() from None
        finally:
            self._close(reference._frames, keys)

    @staticmethod
    def _has_terminal_guard(reference):
        for before_frame, frame in zip(reference._before, reference._after, strict=True):
            nodes = {node['id']: node for node in frame['nodes']}
            before = {node['id']: node for node in before_frame['nodes']}
            if any(record['attribute'] == 'aria-busy' and record['before'] == 'true'
                    and record['after'] == 'false'
                    and before.get(record['id'], {}).get('attributes', {}).get('aria-busy') != 'true'
                    and nodes.get(record['id'], {}).get('attributes', {}).get('aria-busy') == 'false'
                    for record in frame['records']):
                return True
        return False

    @staticmethod
    def _has_fresh_text(reference):
        for before_frame, frame in zip(reference._before, reference._after, strict=True):
            before = {node['id']: node for node in before_frame['nodes']}
            if any(node['visible'] and not node['private'] and node['rendered_text'].strip()
                    and node['rendered_text'] != before.get(node['id'], {}).get('rendered_text')
                    for node in frame['nodes']):
                return True
        return False

    def _extract(self, reference):
        encoded = self._payload(reference)
        if not self.options.provider_factory and self.options.expected_destination is None:
            raise ReferenceUnavailable()
        extractor = ResponseExtractor(self.options, max_calls=1, protected_values=self.protected_values)
        observation = reference._capture.observe('application/json', encoded, stream_closed=True)
        extractor.check_budget()
        self.model_started()
        try:
            text = extractor.extract(observation, request_id=reference.turn_id,
                stage='formal' if self.qualified else 'verification')
            self._reproduce_current_source(reference)
            return text
        except ExtractionError as error:
            if error.reason in {'provider_destination_changed', 'provider_destination_unconfirmed',
                    'extraction_budget_exhausted', 'extraction_time_exhausted', 'provider_cancelled'}:
                raise
            raise ReferenceUnavailable() from None

    @staticmethod
    def _ancestors(node, nodes):
        result = []
        while node is not None:
            result.append(node)
            node = nodes.get(node['parent'])
        return result

    @staticmethod
    def _shape(node, nodes):
        children = {}
        for item in nodes.values():
            children.setdefault(item['parent'], []).append(item)
        result, pending = [], [(node, 0)]
        while pending:
            current, depth = pending.pop()
            result.append([depth, current['tag'], current['visible'], current['private'],
                current['unsupported'], current['attributes'].get('role'), bool(current['text'].strip())])
            pending.extend((child, depth + 1) for child in reversed(children.get(current['id'], [])))
        return result

    def _source_profile(self, reference, text):
        matches = []
        for before_frame, frame in zip(reference._before, reference._after, strict=True):
            nodes = {node['id']: node for node in frame['nodes']}
            before = {node['id']: node for node in before_frame['nodes']}
            for node in nodes.values():
                if (node['tag'] == 'body' or not node['visible'] or node['private']
                        or node['rendered_text'] != text or before.get(node['id'], {}).get('rendered_text') == text):
                    continue
                ancestors = self._ancestors(node, nodes)
                if any(parent['tag'] != 'body' and parent['rendered_text'] == text for parent in ancestors[1:]):
                    continue
                parent = nodes.get(node['parent'], node)
                if any(item[4] or item[3] for item in self._shape(parent, nodes)):
                    continue
                guards = []
                for record in frame['records']:
                    guard = nodes.get(record['id'])
                    if (guard and record['attribute'] == 'aria-busy' and record['before'] == 'true'
                            and record['after'] == 'false' and guard['attributes'].get('aria-busy') == 'false'
                            and before.get(record['id'], {}).get('attributes', {}).get('aria-busy') != 'true'
                            and (guard in ancestors or node in self._ancestors(guard, nodes))):
                        guards.append([item['tag'] for item in self._ancestors(guard, nodes)])
                if not guards:
                    continue
                matches.append([frame['origin'], frame['frame_index'],
                    [item['tag'] for item in ancestors], self._shape(parent, nodes), sorted(guards)])
        if len(matches) != 1:
            raise ReferenceUnavailable()
        return hashlib.sha256(_json(matches[0]).encode()).hexdigest()

    def qualify(self, reference: ReferenceTurn, trusted_text: str) -> bool:
        if (type(reference) is not ReferenceTurn or type(trusted_text) is not str or not trusted_text.strip()
                or reference.turn_id in self._qualified_turns):
            raise ReferenceUnavailable()
        self._payload(reference)
        profile = self._source_profile(reference, trusted_text)
        text = self._extract(reference)
        if text != trusted_text:
            raise ReferenceUnavailable()
        if self._profile != profile:
            self._profile, self._qualified_turns = profile, set()
        self._qualified_turns.add(reference.turn_id)
        return self.qualified

    def reference(self, reference: ReferenceTurn) -> str:
        if not self.qualified or reference.turn_id in self._qualified_turns:
            raise ReferenceUnavailable()
        self._payload(reference)
        if not self._has_fresh_text(reference) or not self._has_terminal_guard(reference):
            raise ReferenceUnavailable()
        text = self._extract(reference)
        if self._source_profile(reference, text) != self._profile:
            raise ReferenceUnavailable()
        return text
