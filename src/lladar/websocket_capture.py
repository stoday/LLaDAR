"""Browser-owned, sequential native text WebSocket capture (no protocol guessing)."""

WEBSOCKET_CAPTURE_SCRIPT = r"""
(() => {
  if (globalThis.__lladarWebSockets) return;
  const Native = globalThis.WebSocket;
  const sockets = new Map(), replays = new Map();
  let nextId = 0, marker = null, captures = [];
  const limit = 1024 * 1024, encoder = new TextEncoder();
  const countValues = (value, needle) => {
    if (typeof value === 'string') return value.split(needle).length - 1;
    if (value && typeof value === 'object') return Object.values(value).reduce((n,v) => n + countValues(v,needle),0);
    return 0;
  };
  const markerCount = data => {
    if (!marker || typeof data !== 'string') return 0;
    try { return countValues(JSON.parse(data), marker); }
    catch (_) { return data.split(marker).length - 1; }
  };
  const record = (entry, body, calibrationMarker='') => ({
    socketId: entry.id, protocols: entry.protocols, url: entry.socket.url,
    method: 'WEBSOCKET', headers: {'content-type': (() => {
      try { JSON.parse(body); return 'application/json'; } catch (_) { return 'text/plain'; }
    })()}, requestBody: body, marker: calibrationMarker,
    contentType: 'application/json', messages: [], body: '[]', receivedBytes: 0,
    done: false, failed: false, failureReason: '', overLimit: false,
    status: 0, ok: true, startedAt: performance.now(), stopped: false,
  });
  const fail = (r, reason='stream_read_failed') => {
    r.failed = true; r.failureReason = reason; r.body = ''; r.finishedAt = performance.now();
  };
  const snapshot = r => ({...r, messages: undefined,
    elapsedMs: Math.max(0,(r.finishedAt ?? performance.now())-r.startedAt)});
  const register = (socket, protocols) => {
    const entry = {id: ++nextId, socket, protocols, active: null};
    sockets.set(entry.id,entry);
    socket.addEventListener('message', event => {
      const r=entry.active;
      if (!r || r.stopped || r.failed || r.overLimit) return;
      if (typeof event.data !== 'string') {
        r.receivedBytes += event.data.size ?? event.data.byteLength ?? 0;
        if (r.receivedBytes > limit) r.overLimit=true;
        fail(r, 'unsupported_websocket_frame'); return;
      }
      r.receivedBytes += encoder.encode(event.data).byteLength;
      // Count the serialized envelope too, before retaining the frame.
      const encodedFrame = JSON.stringify(event.data);
      const bytes = encoder.encode(encodedFrame).byteLength + (r.messages.length ? 1 : 2);
      r.envelopeBytes = (r.envelopeBytes || 0) + bytes;
      if (r.receivedBytes > limit || r.envelopeBytes > limit) {
        r.overLimit=true; r.body=''; r.messages=[]; return;
      }
      r.messages.push(event.data); r.body=JSON.stringify(r.messages);
    });
    socket.addEventListener('close', event => {
      const r=entry.active;
      if (r && !r.stopped) {
        if (event.code === 1000 && event.wasClean) r.done=true;
        else fail(r);
        r.finishedAt=performance.now();
      }
      sockets.delete(entry.id);
    });
    socket.addEventListener('error', () => { if (entry.active && !entry.active.stopped) fail(entry.active,'transport_failed'); });
    const send = socket.send.bind(socket);
    socket.send = data => {
      if (entry.active && !entry.active.stopped && !entry.active.done) fail(entry.active);
      const n=markerCount(data);
      if (n) {
        const r=record(entry,data,marker);
        if (n !== 1) fail(r);
        captures.push(r); entry.active=r;
      }
      return send(data);
    };
    return entry;
  };
  globalThis.WebSocket = class extends Native {
    constructor(url, protocols) {
      super(url, ...(protocols === undefined ? [] : [protocols]));
      register(this, protocols === undefined ? [] : (Array.isArray(protocols) ? [...protocols] : [protocols]));
    }
  };
  globalThis.__lladarWebSockets = {
    setMarker(value) { this.stopCapture(); marker=value; captures=[]; },
    captureSnapshots() { return captures.map(snapshot); },
    stopCapture() { marker=null; for (const r of captures) r.stopped=true; },
    startReplay(key, options) {
      const socket=new Native(options.url, options.protocols);
      const entry=register(socket,options.protocols);
      const r=record(entry,options.body); r.ownedSocket=true; replays.set(key,r);
      entry.active=r;
      const send=() => {
        if (r.stopped) return;
        try { Native.prototype.send.call(entry.socket,options.body); }
        catch (_) { fail(r,'transport_failed'); }
      };
      entry.socket.addEventListener('open',send,{once:true});
    },
    replaySnapshot(key) { const r=replays.get(key); return r ? snapshot(r) : null; },
    stopReplay(key) {
      const r=replays.get(key);
      if (r) {
        r.stopped=true;
        if (r.ownedSocket) { const e=sockets.get(r.socketId); if(e) e.socket.close(); }
      }
      replays.delete(key);
      // Do not close the application's persistent connection or retry a request.
    },
  };
})();
"""
