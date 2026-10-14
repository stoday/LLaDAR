"""Framework-neutral browser tools for evidence-driven website discovery.

The driver records observations; it never decides which request is the answer,
which protocol means completion, or whether a candidate is verified. Authorized
wire observations preserve their captured values; binary frames are opaque
references in Agent views. The orchestrator owns authorization, submission
budgets and validation.
"""
from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .resource_limits import resource_limit


class BrowserDiscoveryError(RuntimeError):
    """A safe, classified host failure, without private request contents."""

    def __init__(self,reason,*,request_attempted=False):
        self.reason=reason
        self.request_attempted=request_attempted
        super().__init__(reason)


_SENSITIVE = re.compile(r"password|passwd|authorization|cookie|secret|(?:^|[_-])(?:access|refresh)?token(?:$|[_-])|session|csrf|credential|otp", re.I)
_PRIVATE_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie"}
_FORBIDDEN_HEADERS = {"cookie", "host", "origin", "referer", "connection", "content-length", "user-agent"}
_CURRENT_TURN = object()
_DANGEROUS_KEYS={"__proto__","constructor","prototype"}


class _RoutedSocket:
    """Actual Playwright proxy wire supplies observations when native events stop."""
    def __init__(self,url):
        self.url=url
        self.closed=False
        self.listeners={}
        self.outbound=None
    def on(self,name,callback):
        self.listeners[name]=callback
    def emit(self,name,*args):
        if name in self.listeners:
            self.listeners[name](*args)
    def is_closed(self):
        return self.closed


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https", "ws", "wss"} or not parts.hostname or parts.username or parts.password:
        raise BrowserDiscoveryError("browser_url_invalid")
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    port = parts.port
    return f"{scheme}://{parts.hostname}" + (f":{port}" if port and port != (443 if scheme == "https" else 80) else "")


def _redact(value: Any, secrets: set[str]) -> Any:
    """Mask DOM values without changing or refusing protocol observations."""
    return _redact_values(value,secrets)


def _redact_values(value: Any, secrets: set[str]) -> Any:
    if isinstance(value, dict):
        return {str(key): "[host-private]" if _SENSITIVE.search(str(key)) else _redact_values(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_values(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            if len(secret)>1:
                value = value.replace(secret, "[host-private]")
        return value
    return value


def _safe_url(url: str, secrets: set[str]) -> str:
    p = urlsplit(url)
    query = [(k, "[host-private]" if _SENSITIVE.search(k) else str(_redact(v, secrets))) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return urlunsplit((p.scheme, p.netloc, str(_redact(p.path, secrets)), urlencode(query), ""))


_OBSERVE_SCRIPT = r"""
(() => {
  if (globalThis.__lladarDiscovery) return;
  let serial = 0;
  const sockets = new Map(), nodeIds = new WeakMap();
  const limit = __CAPTURE_LIMIT__;
  const state = {turn: null, bytes: 0, overflow: false};
  const next = prefix => prefix + (++serial);
  const textBytes = value => new TextEncoder().encode(value).byteLength;
  const emit = (kind, data = {}) => {
    if (state.overflow) return;
    const size = textBytes(JSON.stringify(data));
    state.bytes += size;
    if (state.bytes > limit) {
      state.overflow = true;
      globalThis.__lladarDiscoveryRecord({kind: 'capture_limit', turn_id: state.turn, bytes: state.bytes});
      return;
    }
    globalThis.__lladarDiscoveryRecord({kind, turn_id: state.turn, ...data});
  };
  const bytes64 = bytes => {
    let result = '';
    for (let i=0; i<bytes.length; i+=8192) result += String.fromCharCode(...bytes.subarray(i, i+8192));
    return btoa(result);
  };
  const wire = async data => {
    if (typeof data === 'string') return {text: data, encoding: 'utf8', bytes: textBytes(data)};
    const bytes = new Uint8Array(data instanceof Blob ? await data.arrayBuffer() : data);
    return {binary_b64: bytes64(bytes), encoding: 'binary', bytes: bytes.byteLength};
  };
  const originalFetch = globalThis.fetch.bind(globalThis);
  const monitoredFetch = async (...args) => {
    const request = new Request(...args), request_id = next('fetch-'), turn_id = state.turn;
    // Request metadata comes from Playwright; this bridge handles open streams.
    emit('fetch_start', {request_id, url: request.url, method: request.method, turn_id});
    try {
      const response = await originalFetch(...args);
      emit('fetch_response', {request_id, url: response.url, status: response.status,
        content_type: response.headers.get('content-type') || '', turn_id});
      const clone = response.clone();
      (async () => {
        const reader = clone.body?.getReader(), decoder = new TextDecoder('utf-8', {fatal:true});
        const type = clone.headers.get('content-type') || '';
        const textual = /text|json|javascript|xml|x-www-form-urlencoded/i.test(type);
        if (!reader) {emit('fetch_end', {request_id, turn_id}); return;}
        try {
          while (true) {
            const part = await reader.read();
            if (part.done) break;
            const body = textual ? {text: decoder.decode(part.value, {stream:true}), encoding:'utf8', bytes:part.value.byteLength}
              : {binary_b64:bytes64(part.value), encoding:'binary', bytes:part.value.byteLength};
            emit('fetch_chunk', {request_id, turn_id, ...body});
            if (state.overflow) {await reader.cancel(); return;}
          }
          if (textual) {const tail=decoder.decode(); if(tail)emit('fetch_chunk',{request_id,turn_id,text:tail,encoding:'utf8',bytes:textBytes(tail)});}
          emit('fetch_end', {request_id, turn_id});
        } catch (_) {emit('fetch_error', {request_id, turn_id, reason:'stream_read_failed'});}
      })();
      return response;
    } catch (_) {emit('fetch_error', {request_id, turn_id, reason:'request_failed'}); throw new Error('request_failed');}
  };
  globalThis.fetch = monitoredFetch;
  const OriginalWebSocket = globalThis.WebSocket;
  if (OriginalWebSocket) {
    function RecordedWebSocket(...args) {
      const socket = new OriginalWebSocket(...args), connection_id = next('ws-');
      sockets.set(connection_id, socket);
      emit('websocket_created', {connection_id, url:socket.url});
      socket.addEventListener('open', () => emit('websocket_open', {connection_id, url:socket.url}));
      socket.addEventListener('close', e => emit('websocket_close', {connection_id, code:e.code, clean:e.wasClean}));
      socket.addEventListener('error', () => emit('websocket_error', {connection_id}));
      let receive = Promise.resolve(), send = Promise.resolve();
      socket.addEventListener('message', e => {const turn_id=state.turn; receive=receive.then(async()=>emit('websocket_received',{connection_id,turn_id,...await wire(e.data)}));});
      const originalSend = socket.send.bind(socket);
      socket.send = data => {const turn_id=state.turn; originalSend(data); send=send.then(async()=>emit('websocket_sent',{connection_id,turn_id,...await wire(data)}));};
      return socket;
    }
    RecordedWebSocket.prototype = OriginalWebSocket.prototype;
    Object.setPrototypeOf(RecordedWebSocket, OriginalWebSocket);
    globalThis.WebSocket = RecordedWebSocket;
  }
  const OriginalEventSource = globalThis.EventSource;
  if (OriginalEventSource) {
    function RecordedEventSource(...args) {
      const source = new OriginalEventSource(...args), connection_id=next('sse-');
      emit('eventsource_created', {connection_id, url:source.url});
      source.addEventListener('open', () => emit('eventsource_open', {connection_id}));
      source.addEventListener('message', e => emit('eventsource_message',{connection_id,text:e.data,last_event_id:e.lastEventId}));
      source.addEventListener('error', () => emit('eventsource_state',{connection_id,ready_state:source.readyState}));
      // Listen to named events used by the application; no event names are assumed.
      const originalAdd = source.addEventListener.bind(source), seen = new Set(['open','message','error']);
      source.addEventListener = (name, listener, options) => {
        if (!seen.has(name)) {seen.add(name); originalAdd(name,e=>emit('eventsource_message',{connection_id,event_name:name,text:e.data,last_event_id:e.lastEventId}));}
        originalAdd(name, listener, options);
      };
      const originalClose = source.close.bind(source);
      source.close = () => {originalClose(); emit('eventsource_closed',{connection_id});};
      return source;
    }
    RecordedEventSource.prototype=OriginalEventSource.prototype;
    Object.setPrototypeOf(RecordedEventSource,OriginalEventSource);
    globalThis.EventSource=RecordedEventSource;
  }
  const selector = node => {
    const path=[];
    while(node && node.nodeType===1) {
      const tag=node.localName, siblings=node.parentElement ? [...node.parentElement.children].filter(x=>x.localName===tag) : [];
      path.unshift(tag+(siblings.length>1?`:nth-of-type(${siblings.indexOf(node)+1})`:'')); node=node.parentElement;
    }
    return path.join(' > ');
  };
  globalThis.__lladarDiscovery = {
    socketStates() {return [...sockets.entries()].map(([connection_id,s])=>({connection_id,url:s.url,state:s.readyState}));},
    arm(turn, reset=true) {state.turn=turn;if(reset){state.bytes=0;state.overflow=false;}},
    activate(turn) {state.turn=turn;},
    send(connection, value, binary, url) {
      let socket=sockets.get(connection);
      if (!socket && url) {const candidates=[...sockets.values()].filter(s=>s.url===url&&s.readyState===1);if(candidates.length===1)socket=candidates[0];}
      if(!socket || socket.readyState!==1)throw new Error('websocket_not_open');
      socket.send(binary?Uint8Array.from(atob(value),c=>c.charCodeAt(0)):value);
    },
    request(spec) {
      const job=next('job-');
      monitoredFetch(spec.url,{method:spec.method,headers:spec.headers,body:spec.body,credentials:'include',redirect:'error'})
        .then(()=>emit('request_started',{job})).catch(()=>emit('request_error',{job}));
      return job;
    },
    snapshot(maxNodes) {
      const nodes=[]; let total=0;
      for(const node of document.querySelectorAll('body *')) {
        if(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE'].includes(node.tagName))continue;
        const style=getComputedStyle(node), rect=node.getBoundingClientRect();
        const visible=style.display!=='none' && style.visibility!=='hidden' && rect.width>0 && rect.height>0;
        if(!visible)continue;
        const control=node.matches('input,textarea,button,select,a,[contenteditable],[role]');
        const direct=[...node.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('');
        const stateful=[...node.attributes].some(a=>a.name.startsWith('aria-')||a.name.startsWith('data-'));
        if(!control && !direct.trim() && !stateful)continue;
        total++; if(nodes.length>=maxNodes)continue;
        if(!nodeIds.has(node))nodeIds.set(node,next('node-'));
        const privateField=node.matches('input[type=password],input[autocomplete="one-time-code"]') || /password|token|secret|credential|otp/i.test([node.name,node.id,node.getAttribute('autocomplete')].join(' '));
        nodes.push({ref:nodeIds.get(node),selector:selector(node),tag:node.tagName.toLowerCase(),role:node.getAttribute('role')||'',
          name:node.getAttribute('aria-label')||node.getAttribute('title')||'',placeholder:node.getAttribute('placeholder')||'',
          text:direct,disabled:!!node.disabled,editable:node.matches('textarea,input,[contenteditable=true]'),
          private:privateField, type:node.getAttribute('type')||'', value:privateField?'[host-private]':node.matches('input,textarea,select')?node.value:undefined,
          busy:node.getAttribute('aria-busy'),expanded:node.getAttribute('aria-expanded'),
          attributes:Object.fromEntries([...node.attributes].filter(a=>a.name!=='value'&&!/^on/i.test(a.name)).map(a=>[a.name,a.value]))});
      }
      return {url:location.href,title:document.title,nodes,total_nodes:total,over_limit:total>maxNodes,text:document.body?.innerText||''};
    }
  };
  const mutations = new MutationObserver(records => {
    if (!state.turn) return;
    const changes=[];
    for (const record of records) {
      const node=record.target.nodeType===1?record.target:record.target.parentElement;
      if(!node || ['SCRIPT','STYLE','NOSCRIPT'].includes(node.tagName))continue;
      if(!nodeIds.has(node))nodeIds.set(node,next('node-'));
      const privateField=node.matches('input[type=password],input[autocomplete="one-time-code"]') || /password|token|secret|credential|otp/i.test([node.name,node.id].join(' '));
      if(privateField)continue;
      changes.push({ref:nodeIds.get(node),tag:node.tagName.toLowerCase(),type:record.type,
        attribute:record.attributeName||null,before:record.oldValue,
        after:record.type==='attributes'?node.getAttribute(record.attributeName):node.textContent});
    }
    if(changes.length)emit('dom_mutation',{changes});
  });
  mutations.observe(document,{subtree:true,childList:true,characterData:true,characterDataOldValue:true,attributes:true,attributeOldValue:true});
})();
"""


class GenericBrowserDriver:
    """One browser owner, ordered observations and generic host-mediated actions."""

    def __init__(self, *, page_url: str, profile_dir: str | Path, timeout: float = 60,
                 headless: bool = False, allowed_origins: list[str] | None = None,
                 capture_byte_limit: int | None = None, dom_node_limit: int | None = None,
                 dom_character_limit: int | None = None, recording_store=None):
        if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise BrowserDiscoveryError("browser_timeout_invalid")
        for limit in (capture_byte_limit, dom_node_limit, dom_character_limit):
            if limit is not None and (type(limit) is not int or limit <= 0):
                raise BrowserDiscoveryError("browser_resource_limit_invalid")
        self.capture_byte_limit = capture_byte_limit or resource_limit("MAX_CAPTURE_BYTES")
        self.dom_node_limit = dom_node_limit or resource_limit("DOM_NODE_LIMIT")
        self.dom_character_limit = dom_character_limit or resource_limit("DOM_CAPTURE_CHARS")
        self.page_url, self.profile_dir = page_url, Path(profile_dir)
        self.recording_store = recording_store
        self._recording_prefix = ('capture-' + uuid.uuid4().hex + '-') if recording_store is not None else ''
        self.timeout, self.headless = timeout, headless
        self.allowed_origins = {_origin(page_url), *(_origin(u) for u in (allowed_origins or []))}
        self._owner = threading.get_ident()
        self._events: list[dict[str, Any]] = []
        self._event_map: dict[str, dict[str, Any]] = {}
        self._requests: dict[Any, str] = {}
        self._request_turns: dict[Any, str | None] = {}
        self._frames: dict[Any, str] = {}
        self._frame_generations: dict[Any, int] = {}
        self._refs: dict[str, tuple[Any, dict[str, Any]]] = {}
        self._turns: dict[str, dict[str, Any]] = {}
        self._turn_pages: dict[str, Any] = {}
        self._page_turns: dict[Any, str | None] = {}
        self._page_ids: dict[Any, str] = {}
        self._current_turn: str | None = None
        self._secrets: set[str] = set()
        self._cookie_flag_names:set[str]=set()
        self._captured_bytes = 0
        self._capture_bytes_by_turn: dict[str | None,int] = {}
        self._overflow_turns: set[str | None] = set()
        self._overflow = False
        self._seen_pages: set[Any] = set()
        self._native_sockets: dict[str, Any] = {}
        self._native_socket_pages: dict[str, Any] = {}
        self._owned_pages: set[Any] = set()
        self._cdp_sessions: list[Any] = []
        self._resources: dict[str, dict[str, Any]] = {}
        self._canonical_cdp = False
        self.request_budget_enabled=False
        self.request_budget_coverage={"http":"disabled","websocket":"disabled"}
        self._budget_callback=None
        self._budget_error=None
        self._budget_start_sequence=0
        self._budget_routed_pages:set[Any]=set()
        self._routed_sockets:dict[tuple[Any,str],_RoutedSocket]={}
        self._engine = self.context = self.page = None
        self._manual_depth = 0
        self._manual_dialog_context = None
        def leave_dialog_with_user(_dialog):
            # A listener disables Playwright's default auto-dismiss. Handling
            # the native dialog remains entirely with the human during login.
            pass
        self._manual_dialog_listener = leave_dialog_with_user

    def _install_manual_dialog_listener(self):
        if self.context is not None and self._manual_dialog_context is not self.context:
            self.context.on('dialog',self._manual_dialog_listener)
            self._manual_dialog_context=self.context

    @contextmanager
    def manual_interaction(self):
        """Keep main-page and popup dialogs under human control during handoff."""
        if threading.get_ident()!=self._owner:
            raise BrowserDiscoveryError('browser_owner_thread_required')
        self._manual_depth+=1
        try:
            try:
                self._install_manual_dialog_listener()
            except Exception:
                raise BrowserDiscoveryError('browser_connection_lost') from None
            yield
        finally:
            self._manual_depth-=1
            if self._manual_depth==0 and self._manual_dialog_context is not None:
                try:
                    self._manual_dialog_context.remove_listener('dialog',self._manual_dialog_listener)
                except Exception:
                    pass  # A dead connection must not replace the original failure.
                self._manual_dialog_context=None

    def check_connection(self):
        """Probe the native connection without renderer code or website requests."""
        if threading.get_ident()!=self._owner:
            raise BrowserDiscoveryError('browser_owner_thread_required')
        session=None
        try:
            if self.context is None or self.page is None or self.page.is_closed():
                raise BrowserDiscoveryError('browser_connection_lost')
            session=self.context.new_cdp_session(self.page)
            session.send('Page.getFrameTree')
        except BrowserDiscoveryError:
            raise
        except Exception:
            raise BrowserDiscoveryError('browser_connection_lost') from None
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass

    def manual_pages(self) -> list[dict[str, Any]]:
        """List live HTTP pages for human selection without private page data."""
        self._check()
        result=[]
        for index,page in enumerate(self.context.pages):
            if page.is_closed() or urlsplit(page.url).scheme not in {'http','https'}:
                continue
            result.append({'page_index':index,'page_id':self._page_id(page),
                           'origin':_origin(page.url)})
        return result

    def select_manual_page(self, page_index: int, *, expected_page_id: str | None = None) -> dict[str, Any]:
        """Bind the user's selected live page without navigating or focusing it."""
        self._check()
        pages=self.context.pages
        if type(page_index) is not int or page_index<0 or page_index>=len(pages):
            raise BrowserDiscoveryError('browser_page_unknown')
        page=pages[page_index]
        if page.is_closed() or (expected_page_id is not None and self._page_id(page)!=expected_page_id):
            raise BrowserDiscoveryError('browser_page_unknown')
        if urlsplit(page.url).scheme not in {'http','https'}:
            raise BrowserDiscoveryError('browser_url_invalid')
        origin=_origin(page.url)
        self.page=page
        self._current_turn=self._page_turns.get(page)
        return {'page_index':page_index,'page_id':self._page_id(page),'origin':origin}

    def _check(self) -> None:
        if threading.get_ident() != self._owner:
            raise BrowserDiscoveryError("browser_owner_thread_required")
        if self.context is None:
            raise BrowserDiscoveryError("browser_not_open")
        if self.page is not None and self.page.is_closed():
            raise BrowserDiscoveryError('browser_connection_lost')
        if self._budget_error is not None:
            raise self._budget_error

    def observed_backend_origins(self) -> list[str]:
        """Host-visible destinations observed through normal browser traffic."""
        self._check()
        result=set()
        for event in self._events:
            if (event["kind"]=="http_request" and event.get("resource_type") in {"fetch","xhr"}) or event["kind"]=="websocket_created":
                try:
                    result.add(_origin(event["url"]))
                except (KeyError,BrowserDiscoveryError):
                    continue
        return sorted(result)

    def _debit_request(self) -> None:
        if self._budget_error is not None:
            raise self._budget_error
        try:
            self._budget_callback()
        except Exception as error:
            reason=getattr(error,'reason',None)
            known={'browser_request_budget_exhausted','browser_authentication_required',
                'discovery_budget_exhausted','discovery_time_exhausted',
                'checkpoint_failed','checkpoint_integrity_failed'}
            self._budget_error=error if type(reason) is str and reason in known else BrowserDiscoveryError("browser_request_budget_exhausted")
            raise self._budget_error from None

    def enable_request_budget(self, callback) -> dict[str,str]:
        """Count actual HTTP dispatch and future WebSocket outgoing frames.

        Playwright cannot retrofit routing onto a pre-existing WebSocket. Such
        sockets are counted for host websocket_send commands only; existing
        website-owned direct sends remain an explicit coverage gap.
        """
        self._check()
        if self.request_budget_enabled:
            raise BrowserDiscoveryError("browser_request_budget_already_enabled")
        if not callable(callback):
            raise BrowserDiscoveryError("browser_request_budget_invalid")
        self._budget_callback=callback
        self._budget_start_sequence=len(self._events)
        self.request_budget_enabled=True
        def intercept(route):
            try:
                self._debit_request()
            except Exception:
                route.abort("blockedbyclient")
                return
            route.continue_()
        self.context.route("**/*",intercept)
        for page in self.context.pages:
            self._route_socket_budget(page)
        self.request_budget_coverage={"http":"all_after_enable","websocket":"new_documents_and_host_sends",
                                      "existing_document_website_websocket_sends":"unintercepted"}
        return dict(self.request_budget_coverage)

    def _route_socket_budget(self,page) -> None:
        def intercept(route):
            try:
                self._debit_request()  # handshake is a remote request too
            except Exception:
                route.close(code=1008,reason="request budget exhausted")
                return
            server=route.connect_to_server()
            socket=_RoutedSocket(route.url)
            self._routed_sockets[(page,route.url)]=socket
            self._websocket(socket,page)
            def outbound(message):
                try:
                    self._debit_request()
                except Exception:
                    route.close(code=1008,reason="request budget exhausted")
                    server.close(code=1008,reason="request budget exhausted")
                    socket.closed=True
                    socket.emit("close")
                    return
                socket.emit("framesent",message)
                server.send(message)
            def inbound(message):
                socket.emit("framereceived",message)
                route.send(message)
            route.on_message(outbound)
            socket.outbound=outbound
            server.on_message(inbound)
            def client_close(code,reason):
                socket.closed=True
                socket.emit("close")
                server.close(code=code,reason=reason)
            def server_close(code,reason):
                socket.closed=True
                socket.emit("close")
                route.close(code=code,reason=reason)
            route.on_close(client_close)
            server.on_close(server_close)
        page.route_web_socket("**/*",intercept)

    def open(self, page_url: str | None = None) -> dict[str, Any]:
        if self.context is not None:
            return self.inspect()
        from playwright.sync_api import sync_playwright
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._engine = sync_playwright().start()
        try:
            self.context = self._engine.chromium.launch_persistent_context(
                str(self.profile_dir), headless=self.headless,
                timeout=resource_limit("BROWSER_STARTUP_TIMEOUT_SECONDS") * 1000,
            )
            if self._manual_depth:
                self._install_manual_dialog_listener()
            self.context.set_default_timeout(self.timeout * 1000)
            self.context.expose_binding("__lladarDiscoveryRecord", self._bridge)
            script = _OBSERVE_SCRIPT.replace("__CAPTURE_LIMIT__", str(self.capture_byte_limit))
            if self.recording_store is not None:
                script = script.replace('turn: null', "turn: 'demonstration'")
                listeners = r'''
  const privateControl = n => n?.matches?.('input[type=password],input[autocomplete="one-time-code"]') || /password|token|secret|credential|otp/i.test([n?.name,n?.id,n?.getAttribute?.('autocomplete')].join(' '));
  for (const name of ['click','input','change','keydown','submit']) {
    document.addEventListener(name, e => {
      const n=e.target;
      if(privateControl(n))return;
      emit('user_action',{action:name,selector:selector(n),tag:n?.tagName,
        key:name==='keydown'?e.key:undefined,value:n?.value,text:n?.textContent});
      if(['click','submit'].includes(name))emit('dom_snapshot',globalThis.__lladarDiscovery.snapshot(__DOM_LIMIT__));
    },true);
  }
  document.addEventListener('DOMContentLoaded',()=>emit('dom_snapshot',globalThis.__lladarDiscovery.snapshot(__DOM_LIMIT__)));
'''.replace('__DOM_LIMIT__', str(self.dom_node_limit))
                script = script.replace('  const mutations = new MutationObserver', listeners + '\n  const mutations = new MutationObserver')
            self.context.add_init_script(script)
            self.context.on("request", self._request)
            self.context.on("response", self._response)
            self.context.on("requestfinished", self._finished)
            self.context.on("requestfailed", self._failed)
            self.context.on("page", self._new_page)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            for page in self.context.pages:
                self._new_page(page)
            self.page.goto(page_url or self.page_url, wait_until="domcontentloaded",
                           timeout=resource_limit("BROWSER_NAVIGATION_TIMEOUT_SECONDS") * 1000)
            return self.inspect()
        except Exception:
            self.close()
            raise BrowserDiscoveryError("browser_open_failed") from None

    def _new_page(self, page) -> None:
        if page in self._seen_pages:
            return
        self._seen_pages.add(page)
        self._page_id(page)
        if self.request_budget_enabled:
            self._route_socket_budget(page)
        page.on("framenavigated", lambda frame: self._navigation(frame))
        page.on("websocket", lambda socket: self._websocket(socket, page))
        try:
            session = self.context.new_cdp_session(page)
            self._cdp_sessions.append(session)
            records = {}
            def start(event):
                request = event["request"]
                selected_turn=self._page_turns.get(page)
                request_event_id = self._record("http_request", {"url": request["url"], "method": request["method"],
                    "page_id":self._page_id(page),
                    "headers": request.get("headers", {}), "body": request.get("postData", ""),
                    "resource_type": event.get("type", "").lower(), "native_request_id": event["requestId"],
                    "observation_source": "browser_network"},turn_id=selected_turn)
                records[event["requestId"]] = {"url": request["url"], "type": event.get("type", ""),
                    "method": event["request"].get("method"), "request_body": event["request"].get("postData", ""),
                    "request_event_id": request_event_id, "bytes": 0, "turn_id": selected_turn}
            def matching_request(record):
                return record.get("request_event_id")
            def response(event):
                if event["requestId"] in records:
                    record = records[event["requestId"]]
                    record.update(content_type=event["response"].get("mimeType", ""),
                                                      status=event["response"]["status"])
                    self._record("http_response", {"request_event_id": record["request_event_id"], "url": record["url"],
                        "page_id":self._page_id(page),
                        "status": event["response"]["status"], "headers": event["response"].get("headers", {}),
                        "observation_source": "browser_network"}, turn_id=record["turn_id"])
            def receive(event):
                if event["requestId"] in records:
                    records[event["requestId"]]["bytes"] += event["dataLength"]
            def finish(event):
                record = records.pop(event["requestId"], None)
                if record:
                    self._record("http_finished", {"request_event_id": record["request_event_id"], "url": record["url"],
                        "page_id":self._page_id(page),
                        "observation_source": "browser_network"}, turn_id=record["turn_id"])
                if not record or record["type"] not in {"Script", "XHR", "Fetch", "Document", "Other"}:
                    return
                if record["bytes"] > self.capture_byte_limit:
                    self._record("capture_limit", {"reason": "response_body_limit"}, turn_id=record["turn_id"])
                    self._overflow = True
                    return
                if not re.search(r"text|json|javascript|xml|protobuf|octet-stream", record.get("content_type", ""), re.I):
                    return
                try:
                    body = session.send("Network.getResponseBody", {"requestId": event["requestId"]})
                    # CDP reports decoded byte counts before asking Chromium for a body.
                    if body.get("base64Encoded"):
                        material = {"binary_b64": body["body"], "encoding": "binary"}
                    else:
                        material = {"text": body["body"], "encoding": "utf8"}
                    event_id = self._record("http_body", {"url": record["url"], "request_event_id": matching_request(record),
                        "page_id":self._page_id(page),
                        "content_type": record.get("content_type", ""), "bytes": record["bytes"],
                        "observation_source": "browser_network", **material}, turn_id=record["turn_id"])
                    if record["type"] == "Script":
                        self._resources[record["url"]] = {"url": record["url"], "event_id": event_id,
                            "bytes": record["bytes"], "content_type": record.get("content_type", "")}
                except Exception as error:
                    self._record("capture_error", {"reason": "completed_body_unavailable",
                        "page_id": self._page_id(page), "url": record['url'],
                        "error_type": type(error).__name__}, turn_id=record["turn_id"])
            def eventsource_message(event):
                record = records.get(event["requestId"], {})
                self._record("eventsource_message", {"connection_id": "eventsource-" + event["requestId"],
                    "page_id":self._page_id(page),
                    "request_event_id": matching_request(record),
                    "event_name": event.get("eventName", ""), "last_event_id": event.get("eventId", ""),
                    "text": event.get("data", ""), "observation_source": "browser_network"},turn_id=self._page_turns.get(page))
            session.on("Network.requestWillBeSent", start)
            session.on("Network.responseReceived", response)
            session.on("Network.dataReceived", receive)
            session.on("Network.loadingFinished", finish)
            session.on("Network.eventSourceMessageReceived", eventsource_message)
            if self.recording_store is not None:
                session.on('Network.webSocketWillSendHandshakeRequest', lambda e: self._record('websocket_handshake', {
                    'page_id': self._page_id(page), 'request_id': e['requestId'],
                    'headers': e['request'].get('headers', {}), 'observation_source': 'browser_network'}))
            session.send("Network.enable", {"maxTotalBufferSize": 2 * self.capture_byte_limit,
                                             "maxResourceBufferSize": self.capture_byte_limit,
                                             "maxPostDataSize": self.capture_byte_limit})
            self._canonical_cdp = True
            if self.recording_store is not None:
                self._record('page_capture_attached', {'page_id': self._page_id(page),
                    'url': page.url, 'observation_source': 'browser_network'})
        except Exception as error:
            # Native Playwright events and open-stream instrumentation still work.
            self._record("capability_missing", {"capability": "completed_resource_body",
                'page_id': self._page_id(page), 'error_type': type(error).__name__})

    def _websocket(self, socket, page=None) -> None:
        if not isinstance(socket,_RoutedSocket) and (page,socket.url) in self._routed_sockets:
            return
        connection_id = f"socket-{len(self._native_sockets) + 1}"
        self._native_sockets[connection_id] = socket
        self._native_socket_pages[connection_id] = page
        self._record("websocket_created", {"connection_id": connection_id, "native_socket_id": connection_id,
                                          "url": socket.url, "page_id":self._page_id(page),
                                          "observation_source": "browser_network"},turn_id=self._page_turns.get(page))
        def frame(kind, payload):
            if isinstance(payload, bytes):
                material = {"binary_b64": base64.b64encode(payload).decode("ascii"), "encoding": "binary", "bytes": len(payload)}
            else:
                material = {"text": payload, "encoding": "utf8", "bytes": len(payload.encode("utf-8"))}
            self._record(kind, {"connection_id": connection_id, "native_socket_id": connection_id,
                               "page_id":self._page_id(page),
                               "observation_source": "browser_network", **material},turn_id=self._page_turns.get(page))
        socket.on("framesent", lambda payload: frame("websocket_sent", payload))
        socket.on("framereceived", lambda payload: frame("websocket_received", payload))
        socket.on("close", lambda: self._record("websocket_close", {"connection_id": connection_id,
                    "page_id":self._page_id(page),
                    "native_socket_id": connection_id, "observation_source": "browser_network"},turn_id=self._page_turns.get(page)))
        socket.on("socketerror", lambda _: self._record("websocket_error", {"connection_id": connection_id,
                    "page_id":self._page_id(page),
                    "native_socket_id": connection_id, "observation_source": "browser_network"},turn_id=self._page_turns.get(page)))

    def _navigation(self, frame) -> None:
        if self.request_budget_enabled and frame==frame.page.main_frame:
            self._budget_routed_pages.add(frame.page)
        self._frame_generations[frame] = self._frame_generations.get(frame, 0) + 1
        self._refs = {ref: item for ref, item in self._refs.items() if item[0] != frame}
        turn=self._page_turns.get(frame.page)
        self._record("navigation", {"url": frame.url, "frame_id": self._frame_id(frame)},turn_id=turn)
        if turn:
            try:
                frame.evaluate("turn => globalThis.__lladarDiscovery?.arm(turn)", turn)
            except Exception:
                pass

    def _frame_id(self, frame) -> str:
        if frame not in self._frames:
            self._frames[frame] = f"frame-{len(self._frames) + 1}"
        return self._frames[frame]

    def _page_id(self, page) -> str:
        if page not in self._page_ids:
            self._page_ids[page] = f"page-{len(self._page_ids) + 1}"
        return self._page_ids[page]

    def _submission_receipt(self, method: str, **details) -> None:
        """Record the host action after its caller's durable dispatch intent.

        This is an attempt receipt, not evidence that the server accepted it.
        Candidate-chosen wire references cannot change its method or page.
        """
        self._turns[self._current_turn]["submitted"] = True
        self._record("submission_attempted", {**details, "method":method,
            "page_id":self._page_id(self.page), "page_index":self.context.pages.index(self.page),
            "observation_source":"browser_host"})

    def _learn_secrets(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if (_SENSITIVE.search(str(key)) or str(key).lower() in _PRIVATE_HEADERS) and isinstance(item, str) and item:
                    self._secrets.add(item)
                else:
                    self._learn_secrets(item)
        elif isinstance(value, list):
            for item in value:
                self._learn_secrets(item)

    def _record(self, kind: str, data: dict[str, Any], *, turn_id: Any = _CURRENT_TURN) -> str:
        if self._overflow:
            return ""
        if isinstance(data.get("binary_b64"), str):
            try:
                data = {**data, "payload_sha256": hashlib.sha256(base64.b64decode(data["binary_b64"],validate=True)).hexdigest()}
            except ValueError:
                data = {"reason":"binary_capture_invalid"}
                kind = "capture_error"
        selected_turn = self._current_turn if turn_id is _CURRENT_TURN else turn_id
        if selected_turn in self._turn_pages:
            data = {"page_id":self._page_id(self._turn_pages[selected_turn]),**data}
        size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self._captured_bytes += size
        if self.recording_store is None:
            self._capture_bytes_by_turn[selected_turn]=self._capture_bytes_by_turn.get(selected_turn,0)+size
        if kind=="capture_limit":
            self._overflow_turns.add(selected_turn)
        if (self.recording_store is None and
                self._capture_bytes_by_turn[selected_turn] > self.capture_byte_limit):
            self._overflow = True
            self._overflow_turns.add(selected_turn)
            data = {"reason": "capture_limit"}
            kind = "capture_limit"
            if any(e["kind"] == kind and e["turn_id"] == selected_turn for e in self._events):
                return ""
        event_id = f"{self._recording_prefix}event-{len(self._events) + 1}"
        event = {**data, "event_id": event_id, "sequence": len(self._events) + 1,
                 "kind": kind, "turn_id": selected_turn,
                 "monotonic_seconds": time.monotonic()}
        self._learn_secrets(data)
        if self.recording_store is not None:
            try:
                self.recording_store.append(event)
            except ValueError as error:
                if str(error) != 'recording_capture_limit':
                    raise
                self._overflow = True
                self._overflow_turns.add(selected_turn)
                return ""
            if kind == 'dom_mutation' and size > 65536:
                event = {'kind': kind, 'event_id': event_id, 'turn_id': selected_turn,
                         'compressed_in_recording': True}
        self._events.append(event)
        self._event_map[event_id] = event
        return event_id

    def _bridge(self, source, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or not isinstance(payload.get("kind"), str):
            return
        event = dict(payload)
        kind, turn_id = event.pop("kind"), event.pop("turn_id", None)
        if kind.startswith("websocket_") or kind == "eventsource_message":
            kind = "instrumentation_" + kind
        event["frame_id"] = self._frame_id(source["frame"])
        event["page_id"] = self._page_id(source["frame"].page)
        # This bridge is an observation, not trusted proof of acceptance/completion.
        event["observation_source"] = "browser_instrumentation"
        if kind == "dom_mutation":
            generation = self._frame_generations.get(source["frame"], 0)
            for change in event.get("changes", []):
                change["ref"] = event["frame_id"] + ":document-" + str(generation) + ":" + change["ref"]
                if change.get("attribute") and _SENSITIVE.search(change["attribute"]):
                    self._secrets.update(v for v in (change.get("before"), change.get("after")) if isinstance(v, str) and v)
        self._record(kind, event, turn_id=turn_id)

    def _request(self, request) -> None:
        try:
            # Property access is synchronous. all_headers() pumps Playwright's
            # event loop and can record a fast response before its request.
            # Browser cookies remain host-owned and are separately refreshed.
            headers = request.headers
            body = request.post_data or ""
            try:
                owner=request.frame.page
                turn=self._page_turns.get(owner)
            except Exception:
                owner,turn=None,None
            self._learn_secrets(headers)
            try:
                self._learn_secrets(json.loads(body))
            except (ValueError, TypeError):
                self._learn_secrets(dict(parse_qsl(body)))
            self._learn_secrets(dict(parse_qsl(urlsplit(request.url).query)))
            event_id = self._record("playwright_http_request" if self._canonical_cdp else "http_request", {"url": request.url, "method": request.method,
                "page_id":self._page_id(owner),
                "headers": headers, "body": body, "resource_type": request.resource_type,
                "observation_source": "browser_network"},turn_id=turn)
            self._requests[request] = event_id
            self._request_turns[request] = turn
        except Exception:
            self._record("capture_error", {"reason": "request_metadata_unavailable"})

    def _response(self, response) -> None:
        self._record("playwright_http_response" if self._canonical_cdp else "http_response", {"request_event_id": self._requests.get(response.request),
            "page_id":self._event_map.get(self._requests.get(response.request),{}).get("page_id"),
            "url": response.url, "status": response.status, "headers": response.headers,
            "observation_source": "browser_network"}, turn_id=self._request_turns.get(response.request))

    def _finished(self, request) -> None:
        self._record("playwright_http_finished" if self._canonical_cdp else "http_finished", {"request_event_id": self._requests.get(request), "url": request.url,
            "page_id":self._event_map.get(self._requests.get(request),{}).get("page_id"),
            "observation_source": "browser_network"}, turn_id=self._request_turns.get(request))

    def _failed(self, request) -> None:
        self._record("http_failed", {"request_event_id": self._requests.get(request), "url": request.url,
            "page_id":self._event_map.get(self._requests.get(request),{}).get("page_id"),
                                     "reason": "request_failed", "observation_source": "browser_network"}, turn_id=self._request_turns.get(request))

    def _refresh_secrets(self) -> None:
        try:
            cookies=self.context.cookies()
        except Exception:
            raise BrowserDiscoveryError('browser_connection_lost') from None
        for cookie in cookies:
            if cookie.get("value"):
                if len(cookie["value"])==1:
                    self._cookie_flag_names.add(cookie["name"])
                else:
                    self._secrets.add(cookie["value"])

    def _view(self, event: dict[str, Any]) -> dict[str, Any]:
        """Preserve captured wire values for authorized Agent exploration."""
        result = copy.deepcopy(event)
        if "binary_b64" in result:
            result.pop("binary_b64")
            result["payload_ref"] = result["event_id"]
            result["opaque"] = True
        return result

    def inspect(self) -> dict[str, Any]:
        self._check()
        self._refresh_secrets()
        frames = []
        connection_states = []
        self._refs.clear()
        for page_number, page in enumerate(self.context.pages):
            for frame in page.frames:
                try:
                    snapshot = frame.evaluate("limit => globalThis.__lladarDiscovery?.snapshot(limit)", self.dom_node_limit)
                except Exception:
                    continue
                if not snapshot:
                    continue
                try:
                    states = frame.evaluate("() => globalThis.__lladarDiscovery?.socketStates() || []")
                    for state in states:
                        native = [(sid, socket) for sid, socket in self._native_sockets.items()
                                  if self._native_socket_pages.get(sid) == page and socket.url == state["url"] and not socket.is_closed()]
                        state["page_index"] = page_number
                        state["page_id"] = self._page_id(page)
                        if len(native) == 1:
                            state["connection_id"] = native[0][0]
                            state["native_socket_id"] = native[0][0]
                        connection_states.append(state)
                except Exception:
                    pass
                if snapshot["over_limit"]:
                    raise BrowserDiscoveryError("browser_dom_limit")
                fid = self._frame_id(frame)
                for node in snapshot["nodes"]:
                    ref = fid + ":document-" + str(self._frame_generations.get(frame, 0)) + ":" + node["ref"]
                    node["ref"] = ref
                    self._refs[ref] = (frame, copy.deepcopy(node))
                snapshot["frame_id"] = fid
                snapshot["page_index"] = page_number
                snapshot["page_id"] = self._page_id(page)
                snapshot["url"] = _safe_url(snapshot["url"], self._secrets)
                frames.append(_redact(snapshot, self._secrets))
        result = {"kind": "browser_observation", "active_page_index":self.context.pages.index(self.page),
                  "active_page_id":self._page_id(self.page),
                  "frames": frames, "connections": connection_states,
                  "client_resources": [copy.deepcopy(r) for r in self._resources.values()],
                  "event_count": len(self._events), "capture_over_limit": self._overflow}
        if len(json.dumps(result, ensure_ascii=False)) > self.dom_character_limit:
            raise BrowserDiscoveryError("browser_dom_limit")
        return result

    def begin_turn(self, turn_id: str) -> dict[str, Any]:
        self._check()
        if not isinstance(turn_id, str) or not turn_id or turn_id in self._turns:
            raise BrowserDiscoveryError("browser_turn_id_invalid")
        baseline = self.inspect()
        self._current_turn, self._captured_bytes, self._overflow = turn_id, 0, False
        self._capture_bytes_by_turn[turn_id]=0
        self._turns[turn_id] = {"start_sequence": len(self._events) + 1, "baseline": baseline, "submitted": False,
                              "last_page": baseline, "filled_inputs": {}}
        self._turn_pages[turn_id]=self.page
        self._page_turns[self.page]=turn_id
        for frame in self.page.frames:
            try:
                frame.evaluate("turn => globalThis.__lladarDiscovery?.arm(turn)", turn_id)
            except Exception:
                pass
        return {"turn_id": turn_id, **copy.deepcopy(self._turns[turn_id])}

    def activate_turn(self, turn_id: str) -> dict[str, Any]:
        """Resume observing an existing page/turn without clearing its evidence."""
        self._check()
        page=self._turn_pages.get(turn_id)
        if page is None or page.is_closed():
            raise BrowserDiscoveryError("browser_turn_page_lost")
        self.page=page
        self._current_turn=turn_id
        self._page_turns[page]=turn_id
        for frame in page.frames:
            try:
                frame.evaluate("turn => globalThis.__lladarDiscovery?.activate(turn)",turn_id)
            except Exception:
                pass
        return {"turn_id":turn_id,"submitted":self._turns[turn_id]["submitted"]}

    def _assert_rearm_safe(self,turn_id: str) -> None:
        state=self._turns[turn_id]
        if state.get("submitted") is not False:
            raise BrowserDiscoveryError("browser_turn_already_submitted",request_attempted=True)
        inputs=set(v for v in state.get("filled_inputs",{}).values() if isinstance(v,str) and v)
        for frame in state.get("baseline",{}).get("frames",[]):
            if frame.get("page_id")==self._page_id(self._turn_pages[turn_id]):
                inputs.update(n["value"] for n in frame.get("nodes",[]) if n.get("editable") and not n.get("private") and isinstance(n.get("value"),str) and n["value"])
        unsafe=turn_id in self._overflow_turns
        for event in self._events:
            if event["turn_id"]!=turn_id:
                continue
            if event["kind"]=="submission_attempted" and event.get("observation_source")=="browser_host":
                unsafe=True
            if event.get("observation_source")!="browser_network":
                continue
            if event["kind"]=="websocket_sent":
                unsafe=True
            if event["kind"]=="http_request" and (event.get("method","").upper() not in {"GET","HEAD","OPTIONS"}
                    or event.get("body") or any(v in unquote(event.get("url","")) for v in inputs)):
                unsafe=True
        if unsafe:
            raise BrowserDiscoveryError("submission_already_attempted",request_attempted=True)

    def rearm_unsubmitted_turn(self,turn_id: str,*,page=None) -> dict[str, Any]:
        """Refresh an unsubmitted turn without deleting evidence or budget use.

        Any host receipt, mutating native HTTP dispatch, native WebSocket send,
        identifiable input in a GET URL, or incomplete capture makes resending
        unsafe. Only the new observation boundary advances; all audit events
        and capture counters remain owned by the original turn ID.
        """
        self._check()
        if turn_id not in self._turns:
            raise BrowserDiscoveryError("browser_turn_unknown")
        self._assert_rearm_safe(turn_id)
        owner=self._turn_pages[turn_id]
        selected=owner if page is None else page
        if selected not in self.context.pages or selected.is_closed():
            raise BrowserDiscoveryError("browser_turn_page_lost")
        try:
            selected_origin=_origin(selected.url)
        except BrowserDiscoveryError:
            raise BrowserDiscoveryError("browser_origin_not_authorized") from None
        if selected_origin not in self.allowed_origins:
            raise BrowserDiscoveryError("browser_origin_not_authorized")
        if selected!=owner:
            if self._page_turns.get(owner)==turn_id:
                self._page_turns[owner]=None
                for frame in owner.frames:
                    try:
                        frame.evaluate("() => globalThis.__lladarDiscovery?.activate(null)")
                    except Exception:
                        pass
            self._turn_pages[turn_id]=selected
        self.activate_turn(turn_id)
        baseline=self.inspect()
        # Playwright evaluations pump native callbacks. Re-check after them so
        # a send that arrived during inspection cannot be hidden by advancing
        # the sequence boundary and then incorrectly authorize another send.
        self._assert_rearm_safe(turn_id)
        for frame in self.page.frames:
            try:
                frame.evaluate("turn=>globalThis.__lladarDiscovery?.arm(turn,false)",turn_id)
            except Exception:
                pass
        self._assert_rearm_safe(turn_id)
        state=self._turns[turn_id]
        state.update(start_sequence=len(self._events)+1,baseline=baseline,last_page=baseline,filled_inputs={})
        return {"turn_id":turn_id,**copy.deepcopy(state)}

    def observe_turn(self, turn_id: str) -> dict[str, Any]:
        self._check()
        if turn_id not in self._turns:
            raise BrowserDiscoveryError("browser_turn_unknown")
        self._refresh_secrets()
        page = self.inspect()
        previous = self._turns[turn_id]["last_page"]
        prior_nodes = {n["ref"]: n for f in previous["frames"] for n in f["nodes"]}
        live_nodes = {n["ref"]: n for f in page["frames"] for n in f["nodes"]}
        changes = [{"ref": ref, "before": prior_nodes.get(ref), "after": live_nodes.get(ref)}
                   for ref in sorted(set(prior_nodes) | set(live_nodes)) if prior_nodes.get(ref) != live_nodes.get(ref)]
        if changes:
            self._record("dom_changed", {"changes": changes, "observation_source": "browser_dom"}, turn_id=turn_id)
        self._turns[turn_id]["last_page"] = page
        return {"turn_id": turn_id, "submitted": self._turns[turn_id]["submitted"],
                "events": [self._view(e) for e in self._events if e["turn_id"] == turn_id
                           and e["sequence"]>=self._turns[turn_id]["start_sequence"]],
                "page": page, "capture_over_limit": turn_id in self._overflow_turns}

    def events(self, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        self._check()
        self._refresh_secrets()
        return [self._view(e) for e in self._events if e["sequence"] > after_sequence]

    def read_event(self, event_id: str, *, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        """Paginate authorized text; unknown binary stays opaque for codec tools."""
        self._check()
        self._refresh_secrets()
        if event_id not in self._event_map or offset < 0:
            raise BrowserDiscoveryError("browser_event_unknown")
        event = self._view(self._event_map[event_id])
        field = "text" if "text" in event else "body" if "body" in event else None
        if field:
            text = event[field]
            count = limit or resource_limit("PROJECT_PREVIEW_CHARS")
            if count < 1:
                raise BrowserDiscoveryError("browser_page_limit_invalid")
            event[field] = text[offset:offset + count]
            event["next_offset"] = offset + count if offset + count < len(text) else None
            event["total_chars"] = len(text)
        return event

    def raw_event(self, event_id: str) -> dict[str, Any]:
        """Host-only wire material for controlled codec/extraction operations."""
        self._check()
        if event_id not in self._event_map:
            raise BrowserDiscoveryError("browser_event_unknown")
        return copy.deepcopy(self._event_map[event_id])

    def client_resource(self, url: str, *, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        """Read only a client resource already loaded by the normal website."""
        self._check()
        if url not in self._resources:
            raise BrowserDiscoveryError("browser_client_resource_not_observed")
        return self.read_event(self._resources[url]["event_id"], offset=offset, limit=limit)

    def action(self, spec: dict[str, Any], *, submit: bool = False) -> dict[str, Any]:
        self._check()
        kind = spec.get("kind")
        if submit and not self._current_turn:
            raise BrowserDiscoveryError("browser_submission_without_turn")
        if submit and self._turns[self._current_turn]["submitted"]:
            raise BrowserDiscoveryError("browser_turn_already_submitted")
        if submit and kind in {"fill","wait","open_page","select_page","close_page"}:
            raise BrowserDiscoveryError("browser_submission_action_invalid")
        if kind == "wait":
            seconds = float(spec.get("seconds", 0))
            if not 0 <= seconds <= self.timeout:
                raise BrowserDiscoveryError("browser_wait_invalid")
            self.page.wait_for_timeout(seconds * 1000)
            return {"ok": True, "kind": kind}
        if kind == "navigate":
            if _origin(spec["url"]) not in self.allowed_origins:
                raise BrowserDiscoveryError("browser_origin_not_authorized")
            if submit:
                self._submission_receipt(kind, url=spec["url"])
            self.page.goto(spec["url"], wait_until="domcontentloaded")
            return {"ok": True, "kind": kind}
        if kind == "open_page":
            if _origin(spec["url"]) not in self.allowed_origins:
                raise BrowserDiscoveryError("browser_origin_not_authorized")
            self.page = self.context.new_page()
            self._current_turn=None
            self._owned_pages.add(self.page)
            self.page.goto(spec["url"], wait_until="domcontentloaded")
            return {"ok": True, "kind": kind, "page_index": self.context.pages.index(self.page)}
        if kind == "close_page":
            page = self.page if "page_index" not in spec else self.context.pages[int(spec["page_index"])]
            if page not in self._owned_pages:
                raise BrowserDiscoveryError("browser_page_not_owned")
            self._owned_pages.remove(page)
            page.close()
            self.page = self.context.pages[-1] if self.context.pages else None
            self._current_turn=self._page_turns.get(self.page)
            return {"ok": True, "kind": kind}
        if kind == "select_page":
            try:
                self.page = self.context.pages[int(spec["page_index"])]
                self._current_turn=self._page_turns.get(self.page)
            except (ValueError, IndexError, KeyError):
                raise BrowserDiscoveryError("browser_page_unknown") from None
            return {"ok": True, "kind": kind}
        if kind not in {"click", "fill", "press", "select", "check"}:
            raise BrowserDiscoveryError("browser_action_unknown")
        ref = spec.get("ref")
        if ref not in self._refs:
            raise BrowserDiscoveryError("browser_ref_stale")
        frame, node = self._refs[ref]
        if frame.page!=self.page:
            raise BrowserDiscoveryError("browser_action_page_not_active")
        if frame.url not in {"about:blank", "about:srcdoc"} and _origin(frame.url) not in self.allowed_origins:
            raise BrowserDiscoveryError("browser_origin_not_authorized")
        if node["private"]:
            raise BrowserDiscoveryError("browser_private_control")
        locator = frame.locator(node["selector"])
        try:
            if kind == "click":
                locator.click(trial=True)
            if submit:
                values = frame.evaluate('''() => [...document.querySelectorAll('input,textarea,[contenteditable=true]')]
                    .filter(n => n.type!=='password' && n.autocomplete!=='one-time-code' && !/password|token|secret|credential|otp/i.test([n.name,n.id].join(' ')))
                    .map(n => n.matches('input,textarea')?n.value:n.innerText)''')
                self._submission_receipt(kind, ref=ref, frame_id=self._frame_id(frame), input_values=values,
                    filled_inputs=self._turns[self._current_turn]["filled_inputs"])
            if kind == "click":
                locator.click()
            elif kind == "fill":
                locator.fill(str(spec.get("value", "")))
                if self._current_turn:
                    actual = locator.input_value() if node["tag"] in {"textarea", "input"} else locator.inner_text()
                    self._turns[self._current_turn]["filled_inputs"][ref] = actual
            elif kind == "press":
                locator.press(str(spec["key"]))
            elif kind == "select":
                locator.select_option(str(spec["value"]))
            elif kind == "check":
                locator.set_checked(bool(spec["checked"]))
            else:
                raise BrowserDiscoveryError("browser_action_unknown")
        except BrowserDiscoveryError:
            raise
        except Exception:
            raise BrowserDiscoveryError("browser_action_failed_after_submission" if submit else "browser_action_failed") from None
        return {"ok": True, "kind": kind, "submitted": submit}

    def http_request(self, spec: dict[str, Any], *, submit: bool = False) -> dict[str, Any]:
        """Start a browser-owned authenticated request; stream observations continue."""
        self._check()
        request = copy.deepcopy(spec)
        bindings=request.pop("body_bindings",None)
        if request.get("request_ref"):
            original = self.raw_event(request.pop("request_ref"))
            if original["kind"] != "http_request":
                raise BrowserDiscoveryError("browser_request_ref_invalid")
            request = {"url": original["url"], "method": original["method"], "body": original["body"],
                       "headers": original["headers"], **request}
        if bindings is not None:
            if not isinstance(bindings,list) or not bindings:
                raise BrowserDiscoveryError("browser_body_bindings_invalid")
            body=self._strict_json(request.get("body",""))
            for binding in bindings:
                if not isinstance(binding,dict) or set(binding)-{"path","value","value_ref"} or ("value" in binding)==("value_ref" in binding):
                    raise BrowserDiscoveryError("browser_body_bindings_invalid")
                path=self._binding_path(binding.get("path"))
                value=binding["value"] if "value" in binding else self._binding_value_ref(binding["value_ref"])
                if not (value is None or type(value) in {str,int,float,bool}) or (type(value) is float and not math.isfinite(value)):
                    raise BrowserDiscoveryError("browser_body_binding_value_invalid")
                container=body
                for part in path[:-1]:
                    container=self._path_item(container,part)
                self._path_item(container,path[-1])  # require an observed field, not prototype creation
                container[path[-1]]=value
            request["body"]=json.dumps(body,ensure_ascii=False,allow_nan=False,separators=(",",":"))
        if _origin(request["url"]) not in self.allowed_origins:
            raise BrowserDiscoveryError("browser_origin_not_authorized")
        request["headers"] = {k: v for k, v in request.get("headers", {}).items() if k.lower() not in _FORBIDDEN_HEADERS}
        request.setdefault("method", "GET")
        if request["method"].upper() in {"GET", "HEAD"}:
            request.pop("body", None)
        if submit:
            if not self._current_turn or self._turns[self._current_turn]["submitted"]:
                raise BrowserDiscoveryError("browser_submission_invalid")
            self._submission_receipt("http")
        try:
            job = self.page.evaluate("spec => globalThis.__lladarDiscovery.request(spec)", request)
        except Exception:
            raise BrowserDiscoveryError("browser_request_start_failed_after_submission" if submit else "browser_request_start_failed") from None
        return {"job_id": job, "started": True, "submitted": submit}

    @staticmethod
    def _strict_json(value):
        def unique(pairs):
            result={}
            for key,item in pairs:
                if key in result:
                    raise ValueError("duplicate")
                result[key]=item
            return result
        try:
            result=json.loads(value,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,TypeError,RecursionError):
            raise BrowserDiscoveryError("browser_binding_json_invalid") from None
        if not isinstance(result,(dict,list)):
            raise BrowserDiscoveryError("browser_binding_json_invalid")
        return result

    @staticmethod
    def _binding_path(path):
        if not isinstance(path,list) or not path or any(type(p) not in {str,int} or (isinstance(p,str) and p in _DANGEROUS_KEYS) or (type(p) is int and p<0) for p in path):
            raise BrowserDiscoveryError("browser_binding_path_invalid")
        return path

    @staticmethod
    def _path_item(container,part):
        try:
            if isinstance(container,dict) and isinstance(part,str):
                return container[part]
            if isinstance(container,list) and type(part) is int:
                return container[part]
        except (KeyError,IndexError):
            pass
        raise BrowserDiscoveryError("browser_binding_path_missing")

    def _binding_value_ref(self,reference):
        if not isinstance(reference,dict) or set(reference)!={"event_id","path"}:
            raise BrowserDiscoveryError("browser_value_reference_invalid")
        event=self.raw_event(reference["event_id"])
        if event["kind"] not in {"http_body","fetch_chunk","eventsource_message","websocket_received"}:
            raise BrowserDiscoveryError("browser_value_reference_invalid")
        url=event.get("url")
        if not url and event.get("request_event_id"):
            url=self.raw_event(event["request_event_id"]).get("url")
        if not url and event.get("connection_id") in self._native_sockets:
            url=self._native_sockets[event["connection_id"]].url
        if not url or _origin(url) not in self.allowed_origins:
            raise BrowserDiscoveryError("browser_value_reference_scope_invalid")
        value=self._strict_json(event.get("text",event.get("body","")))
        for part in self._binding_path(reference["path"]):
            value=self._path_item(value,part)
        return value

    def websocket_send(self, connection_id: str, value: str, *, binary: bool = False, submit: bool = False) -> dict[str, Any]:
        self._check()
        socket = self._native_sockets.get(connection_id)
        if socket is not None:
            if self._native_socket_pages.get(connection_id)!=self.page:
                raise BrowserDiscoveryError("browser_websocket_page_not_active")
            matches=[s for sid,s in self._native_sockets.items() if self._native_socket_pages.get(sid)==self.page and s.url==socket.url and not s.is_closed()]
            if len(matches)!=1:
                raise BrowserDiscoveryError("browser_websocket_ambiguous")
        url = socket.url if socket else next((e.get("url") for e in self._events if e["kind"] == "instrumentation_websocket_created" and e.get("connection_id") == connection_id), None)
        if url is None:
            raise BrowserDiscoveryError("browser_websocket_unknown")
        if _origin(url) not in self.allowed_origins:
            raise BrowserDiscoveryError("browser_origin_not_authorized")
        if self.request_budget_enabled:
            created=next((e for e in self._events if e["kind"]=="websocket_created" and e.get("connection_id")==connection_id),None)
            if created is None or created["sequence"]<=self._budget_start_sequence or self.page not in self._budget_routed_pages:
                self._debit_request()
        if binary:
            try:
                base64.b64decode(value, validate=True)
            except ValueError:
                raise BrowserDiscoveryError("browser_binary_invalid") from None
        if submit:
            if not self._current_turn or self._turns[self._current_turn]["submitted"]:
                raise BrowserDiscoveryError("browser_submission_invalid")
            self._submission_receipt("websocket", connection_id=connection_id)
        if isinstance(socket,_RoutedSocket):
            # The cached page wrapper may refer to an underlying socket below
            # Playwright's virtual router. Use the actual proxy wire operation
            # so host commands cannot bypass its budget/provenance interception.
            socket.outbound(base64.b64decode(value,validate=True) if binary else value)
            self._check()
            return {"sent":True,"submitted":submit}
        for frame in self.page.frames:
            try:
                frame.evaluate("args => globalThis.__lladarDiscovery.send(...args)", [connection_id, value, binary, url])
                return {"sent": True, "submitted": submit}
            except Exception:
                continue
        raise BrowserDiscoveryError("browser_websocket_send_failed_after_submission" if submit else "browser_websocket_unknown")

    def close(self, *, strict=False) -> None:
        failed = False
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                failed = True
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                failed = True
        self.context = self.page = self._engine = None
        self._manual_dialog_context=None
        if strict and failed:
            raise BrowserDiscoveryError('browser_connection_lost')

