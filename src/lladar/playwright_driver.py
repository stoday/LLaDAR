"""Playwright adapter for authenticated, framework-neutral browser traffic."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import time
from typing import Callable
from urllib.parse import parse_qsl, urlsplit

from .browser_target import (
    CHROMIUM_SETUP_MESSAGE, BrowserAuthenticationRequired, BrowserResponseLimitExceeded,
    CapturedInteraction, RequestTemplate,
)
from .answer_extraction import ExtractionError
from .response_capture import ObservedResponse, ResponseObservationSource


_FORBIDDEN_REPLAY_HEADERS = {
    "accept-encoding",
    "connection",
    "content-length",
    "cookie",
    "host",
    "origin",
    "referer",
    "user-agent",
}

_RESPONSE_BYTE_LIMIT = 1024 * 1024
_NAVIGATION_TIMEOUT_MS = 300_000


class _CalibrationResponseBudget:
    """Count decoded transport bytes before requesting a fallback response body.

    XHR is not visible to the fetch wrapper. Its completed body may only cross
    to Python after Chromium has reported its size. Never trust Content-Length
    or encodedDataLength: both may describe a tiny compressed representation.
    No body bytes, cookies or response headers are retained by this observer.
    """

    def __init__(self, context, matches):
        self.context = context
        self.matches = matches
        self.sessions = []
        self.records = {}
        self.context.on("page", self.attach)
        for page in self.context.pages:
            self.attach(page)

    def attach(self, page) -> None:
        try:
            session = self.context.new_cdp_session(page)
            self.sessions.append(session)
            session.on("Network.requestWillBeSent", lambda event: self.start(session, event))
            session.on("Network.dataReceived", lambda event: self.receive(session, event))
            session.on("Network.loadingFinished", lambda event: self.finish(session, event))
            session.send("Network.enable", {
                "maxTotalBufferSize": 2 * _RESPONSE_BYTE_LIMIT,
                "maxResourceBufferSize": _RESPONSE_BYTE_LIMIT,
            })
        except Exception:
            # Instrumented fetch still works. A fallback without a complete,
            # correlated size measurement fails closed in check().
            return

    def start(self, session, event) -> None:
        if len(self.records) >= 2:
            return  # A second match already makes calibration ambiguous.
        raw = event["request"]
        request = SimpleNamespace(
            url=raw["url"], post_data=raw.get("postData", ""),
            headers={name.lower(): value for name, value in raw.get("headers", {}).items()},
        )
        if self.matches(request):
            self.records[(session, event["requestId"])] = {
                "url": raw["url"], "method": raw["method"], "bytes": 0, "done": False,
            }

    def receive(self, session, event) -> None:
        record = self.records.get((session, event["requestId"]))
        if record is not None:
            record["bytes"] = min(_RESPONSE_BYTE_LIMIT + 1, record["bytes"] + event["dataLength"])

    def finish(self, session, event) -> None:
        record = self.records.get((session, event["requestId"]))
        if record is not None:
            record["done"] = True

    def check(self, request) -> None:
        matches = [record for record in self.records.values()
                   if record["url"] == request.url and record["method"] == request.method]
        if len(matches) != 1 or not matches[0]["done"]:
            raise RuntimeError("Browser response size could not be verified before capture")
        if matches[0]["bytes"] > _RESPONSE_BYTE_LIMIT:
            raise BrowserResponseLimitExceeded()
        if matches[0]["bytes"] == 0:
            raise RuntimeError("Browser response has no measured answer bytes")

    def check_limit(self) -> None:
        if any(record["bytes"] > _RESPONSE_BYTE_LIMIT for record in self.records.values()):
            raise BrowserResponseLimitExceeded()

    def close(self) -> None:
        self.context.remove_listener("page", self.attach)
        for session in self.sessions:
            try:
                session.detach()
            except Exception:
                pass
        self.records.clear()
        self.sessions.clear()


_STREAM_CAPTURE_SCRIPT = r"""
(() => {
  if (globalThis.__lladarStreams) return;
  const originalFetch = globalThis.fetch.bind(globalThis);
  const responseByteLimit = __LLADAR_RESPONSE_BYTE_LIMIT__;
  const state = {marker: null, captures: [], replays: new Map()};
  const count = (text, marker) => marker ? String(text).split(marker).length - 1 : 0;
  const queryCount = (url, marker) => {
    try {
      let total = 0;
      for (const value of new URL(url).searchParams.values()) total += count(value, marker);
      return total;
    } catch (_) {
      return 0;
    }
  };
  const bodyCount = (request, body, marker) => {
    const mediaType = (request.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
    if (mediaType === 'application/x-www-form-urlencoded') {
      let total = 0;
      for (const value of new URLSearchParams(body).values()) total += count(value, marker);
      return total;
    }
    return count(body, marker);
  };
  const serializable = record => ({
    marker: record.marker || '',
    url: record.url,
    method: record.method,
    headers: record.headers,
    requestBody: record.requestBody,
    contentType: record.contentType || '',
    status: record.status || 0,
    ok: Boolean(record.ok),
    body: record.body || '',
    done: Boolean(record.done),
    timedOut: Boolean(record.timedOut),
    failed: Boolean(record.failed),
    overLimit: Boolean(record.overLimit),
  });
  const consume = async (response, record) => {
    record.contentType = response.headers.get('content-type') || '';
    record.status = response.status;
    record.ok = response.ok;
    if (!response.body) {
      record.done = true;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8', {fatal: true});
    record.reader = reader;
    let receivedBytes = 0;
    try {
      while (!record.stopped) {
        const part = await reader.read();
        if (record.stopped) return;
        if (part.done) break;
        receivedBytes += part.value.byteLength;
        if (receivedBytes > responseByteLimit) {
          record.overLimit = true;
          record.body = '';
          stop(record);
          return;
        }
        record.body += decoder.decode(part.value, {stream: true});
      }
      if (record.stopped) return;
      record.body += decoder.decode();
      record.done = true;
    } catch (error) {
      if (!record.stopped && !record.timedOut) record.failed = true;
    }
  };
  const stop = record => {
    if (!record) return;
    record.stopped = true;
    if (record.reader) void record.reader.cancel().catch(() => {});
    if (record.controller) record.controller.abort();
    if (record.timer) clearTimeout(record.timer);
  };

  globalThis.__lladarStreams = {
    setMarker(marker) {
      for (const record of state.captures) stop(record);
      state.marker = marker;
      globalThis.__lladarCalibrationMarker = marker;
      state.captures = [];
    },
    captureSnapshots() {
      return state.captures.map(serializable);
    },
    stopCapture() {
      state.marker = null;
      globalThis.__lladarCalibrationMarker = null;
      for (const record of state.captures) stop(record);
    },
    startReplay(key, options) {
      stop(state.replays.get(key));
      const controller = new AbortController();
      const record = {
        url: options.url,
        method: options.method,
        headers: {},
        requestBody: '',
        contentType: '',
        status: 0,
        ok: false,
        body: '',
        done: false,
        timedOut: false,
        failed: false,
        controller,
      };
      state.replays.set(key, record);
      record.timer = setTimeout(() => {
        record.timedOut = true;
        controller.abort();
      }, options.timeoutMs);
      void (async () => {
        try {
          const response = await originalFetch(options.url, {
            method: options.method,
            headers: options.headers,
            body: options.method === 'GET' || options.method === 'HEAD' ? undefined : options.body,
            credentials: 'include',
            cache: 'no-store',
            signal: controller.signal,
          });
          await consume(response, record);
        } catch (error) {
          if (error && error.name === 'AbortError') {
            if (!record.stopped) record.timedOut = true;
          } else {
            record.failed = true;
          }
        } finally {
          if (record.timer) clearTimeout(record.timer);
        }
      })();
    },
    replaySnapshot(key) {
      const record = state.replays.get(key);
      return record ? serializable(record) : null;
    },
    stopReplay(key) {
      const record = state.replays.get(key);
      stop(record);
      state.replays.delete(key);
    },
  };

  globalThis.fetch = async (...args) => {
    let request;
    let requestBody = '';
    try {
      request = new Request(...args);
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        requestBody = await request.clone().text();
      }
    } catch (_) {
      return originalFetch(...args);
    }
    const marker = globalThis.__lladarCalibrationMarker;
    if (!marker) return originalFetch(...args);
    if (bodyCount(request, requestBody, marker) + queryCount(request.url, marker) !== 1) {
      return originalFetch(...args);
    }
    const response = await originalFetch(...args);
    if (marker !== globalThis.__lladarCalibrationMarker) return response;
    const record = {
      marker,
      url: request.url,
      method: request.method,
      headers: Object.fromEntries(request.headers.entries()),
      requestBody,
      contentType: '',
      status: 0,
      ok: false,
      body: '',
      done: false,
      timedOut: false,
      failed: false,
    };
    state.captures.push(record);
    void consume(response.clone(), record);
    return response;
  };
})();
""".replace("__LLADAR_RESPONSE_BYTE_LIMIT__", str(_RESPONSE_BYTE_LIMIT))


class PlaywrightBrowserDriver:
    """Own one persistent Chromium context and observe/replay its real requests."""

    def __init__(
        self,
        *,
        page_url: str,
        profile_dir: str | Path,
        timeout: float,
        headless: bool = False,
    ) -> None:
        from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError, sync_playwright

        self.timeout_ms = max(1, int(timeout * 1000))
        self._observations = ResponseObservationSource()
        self.profile_dir = Path(profile_dir).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self.context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=headless,
                timeout=_NAVIGATION_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            self._playwright.stop()
            raise TimeoutError("Browser startup exceeded its 5-minute deadline") from None
        except Error as error:
            self._playwright.stop()
            raise RuntimeError(CHROMIUM_SETUP_MESSAGE) from error
        try:
            self.context.set_default_navigation_timeout(_NAVIGATION_TIMEOUT_MS)
            self.context.add_init_script(_STREAM_CAPTURE_SCRIPT)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            navigation = self.page.goto(
                page_url,
                wait_until="domcontentloaded",
                timeout=_NAVIGATION_TIMEOUT_MS,
            )
            content_type_header = (
                navigation.header_value("content-type") if navigation is not None else ""
            ) or ""
            content_type = content_type_header.partition(";")[0].strip().lower()
            if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
                raise RuntimeError(
                    "--page-url must open an interactive HTML question page, not a bare API response"
                )
        except Exception:
            try:
                self.context.close()
            finally:
                self._playwright.stop()
            raise

    @staticmethod
    def _marker_count(request, marker: str) -> int:
        body = request.post_data or ""
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        body_count = (
            sum(value.count(marker) for _name, value in parse_qsl(body, keep_blank_values=True))
            if media_type == "application/x-www-form-urlencoded"
            else body.count(marker)
        )
        query_count = sum(
            value.count(marker)
            for _name, value in parse_qsl(urlsplit(request.url).query, keep_blank_values=True)
        )
        return body_count + query_count

    @classmethod
    def _matches_marker(cls, request, marker: str) -> bool:
        return cls._marker_count(request, marker) == 1

    def capture_calibration(
        self,
        marker: str,
        prompt: Callable[[str], bool | None],
    ) -> CapturedInteraction:
        capture = self._observations.begin_request(marker)
        matches = []
        finished = []
        traffic = {"requests": 0, "body_requests": 0, "marker_requests": 0}

        def remember(request) -> None:
            traffic["requests"] += 1
            body = request.post_data or ""
            if body:
                traffic["body_requests"] += 1
            marker_count = self._marker_count(request, marker)
            if marker_count:
                traffic["marker_requests"] += 1
            if marker_count == 1:
                matches.append(request)

        def remember_finished(request) -> None:
            if self._matches_marker(request, marker):
                finished.append(request)

        self.context.on("request", remember)
        self.context.on("requestfinished", remember_finished)
        response_budget = _CalibrationResponseBudget(
            self.context, lambda request: self._matches_marker(request, marker),
        )
        self.context.add_init_script(
            "globalThis.__lladarCalibrationMarker = " + json.dumps(marker) + ";"
        )
        for page in self.context.pages:
            try:
                page.evaluate("marker => globalThis.__lladarStreams.setMarker(marker)", marker)
            except Exception:
                continue
        try:
            manual_complete = prompt(marker) is True
            deadline = time.monotonic() + self.timeout_ms / 1000
            candidate: CapturedInteraction | None = None
            last_snapshot = None
            progress_at = time.monotonic() + 5
            while time.monotonic() < deadline:
                self.page.wait_for_timeout(min(25, self.timeout_ms))
                response_budget.check_limit()
                captures = self._capture_snapshots(marker)
                if any(snapshot["overLimit"] for _page, snapshot in captures):
                    raise BrowserResponseLimitExceeded()
                if captures:
                    last_snapshot = captures[0][1]
                if time.monotonic() >= progress_at:
                    print(
                        "[browser capture] "
                        f"requests={traffic['requests']}, body_requests={traffic['body_requests']}, "
                        f"marker_requests={traffic['marker_requests']}, "
                        f"matching_requests={len(matches)}, streams={len(captures)}",
                        flush=True,
                    )
                    progress_at = time.monotonic() + 30
                if len(matches) > 1 or len(captures) > 1:
                    raise RuntimeError("Calibration marker appeared in multiple requests")
                if len(matches) == 1 and captures:
                    capture_page, snapshot = captures[0]
                    last_snapshot = snapshot
                    if snapshot["status"] in {401, 403}:
                        raise BrowserAuthenticationRequired("Manual calibration requires sign-in")
                    if snapshot["failed"]:
                        raise ExtractionError("completion_unconfirmed")
                    if snapshot["status"] and not snapshot["ok"]:
                        raise RuntimeError("Calibration response failed; no partial response accepted")
                    observation = capture.observe(
                        content_type=snapshot["contentType"],
                        body=snapshot["body"],
                        stream_closed=snapshot["done"],
                        manual_complete=manual_complete,
                    )
                    # Completion comes only from transport closure or this
                    # request's explicit local manual confirmation, never fields/events.
                    if snapshot["done"] or (manual_complete and snapshot["body"]):
                        request = matches[0]
                        self.page = capture_page
                        candidate = CapturedInteraction(
                            method=request.method,
                            url=request.url,
                            headers=request.all_headers(),
                            request_body=request.post_data or "",
                            response=observation,
                        )
                if len(matches) == 1 and finished and candidate is None:
                    request = matches[0]
                    response = request.response()
                    if response is not None:
                        if response.status in {401, 403}:
                            raise BrowserAuthenticationRequired("Manual calibration requires sign-in")
                        if not response.ok:
                            raise RuntimeError("Calibration response failed")
                        response_budget.check(request)
                        try:
                            self.page = request.frame.page
                        except Exception:
                            pass
                        candidate = CapturedInteraction(
                            method=request.method,
                            url=request.url,
                            headers=request.all_headers(),
                            request_body=request.post_data or "",
                            response=capture.observe(
                                content_type=response.header_value("content-type") or "",
                                body=response.body(),
                                stream_closed=True,
                            ),
                        )
                if candidate is not None:
                    self.page.wait_for_timeout(min(250, self.timeout_ms))
                    response_budget.check_limit()
                    captures = self._capture_snapshots(marker)
                    if any(snapshot["overLimit"] for _page, snapshot in captures):
                        raise BrowserResponseLimitExceeded()
                    if len(matches) > 1 or len(captures) > 1:
                        raise RuntimeError("Calibration marker appeared in multiple requests")
                    return candidate
            response_type = (
                last_snapshot["contentType"].partition(";")[0].strip().lower()
                if last_snapshot else "not_observed"
            )
            observed_chars = len(last_snapshot["body"]) if last_snapshot else 0
            stream_closed = bool(last_snapshot and last_snapshot["done"])
            raise TimeoutError(
                "Calibration response did not reach a supported final answer before timeout "
                f"(requests={traffic['requests']}, body_requests={traffic['body_requests']}, "
                f"marker_requests={traffic['marker_requests']}, "
                f"matching_requests={len(matches)}, instrumented_streams="
                f"{1 if last_snapshot else 0}, response_type={response_type}, "
                f"observed_chars={observed_chars}, stream_closed={str(stream_closed).lower()})"
            )
        finally:
            response_budget.close()
            self.context.add_init_script("globalThis.__lladarCalibrationMarker = null;")
            self.context.remove_listener("request", remember)
            self.context.remove_listener("requestfinished", remember_finished)
            for page in self.context.pages:
                try:
                    page.evaluate("() => globalThis.__lladarStreams.stopCapture()")
                except Exception:
                    continue

    def _capture_snapshots(self, marker: str):
        captures = []
        for page in self.context.pages:
            try:
                snapshots = page.evaluate(
                    "() => globalThis.__lladarStreams.captureSnapshots()"
                )
            except Exception:
                continue
            captures.extend(
                (page, snapshot)
                for snapshot in snapshots
                if snapshot.get("marker") == marker
            )
        return captures

    @staticmethod
    def _replay_headers(headers: dict[str, str]) -> dict[str, str]:
        return {
            name: value
            for name, value in headers.items()
            if name.lower() not in _FORBIDDEN_REPLAY_HEADERS and not name.lower().startswith("sec-")
        }

    def replay(
        self,
        template: RequestTemplate,
        question: str,
        request_id: str,
        *, timeout_seconds: float | None = None,
    ) -> ObservedResponse:
        timeout_ms = self.timeout_ms if timeout_seconds is None else min(self.timeout_ms, max(1, int(timeout_seconds * 1000)))
        capture = self._observations.begin_request(request_id)
        self.page.evaluate(
            "options => globalThis.__lladarStreams.startReplay(options.key, options)",
            {
                "key": request_id,
                "url": template.render_url(question),
                "method": template.method,
                "headers": self._replay_headers(template.headers),
                "body": template.render_body(question),
                "timeoutMs": timeout_ms,
            },
        )
        deadline = time.monotonic() + timeout_ms / 1000
        try:
            while time.monotonic() < deadline:
                self.page.wait_for_timeout(min(25, self.timeout_ms))
                result = self.page.evaluate(
                    "key => globalThis.__lladarStreams.replaySnapshot(key)",
                    request_id,
                )
                if result is None:
                    continue
                if result["overLimit"]:
                    raise BrowserResponseLimitExceeded()
                if result["timedOut"]:
                    raise TimeoutError("Browser replay timed out")
                if result["failed"]:
                    raise RuntimeError("Browser replay failed before a response was available")
                if not result["status"]:
                    continue
                if result["status"] in {401, 403}:
                    raise BrowserAuthenticationRequired(
                        f"Browser replay returned HTTP {result['status']}; manual sign-in is required"
                    )
                if not result["ok"]:
                    raise RuntimeError(f"Browser replay returned HTTP {result['status']}")
                observation = capture.observe(
                    content_type=result["contentType"],
                    body=result["body"],
                    stream_closed=result["done"],
                )
                if result["done"]:
                    return observation
            raise TimeoutError("Browser replay timed out")
        finally:
            self.page.evaluate(
                "key => globalThis.__lladarStreams.stopReplay(key)",
                request_id,
            )

    def show_for_manual_login(self) -> None:
        self.page.bring_to_front()
        self.page.reload(wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS)

    def close(self) -> None:
        try:
            self.context.close()
        finally:
            self._playwright.stop()
