"""Authenticated browser target for framework-neutral question replay."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sys
import tempfile
from typing import Any, Callable, TextIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .answer_extraction import ExtractionError, ExtractionOptions, ResponseExtractor, response_input
from .response_capture import ObservedResponse
from .terminal_review import TerminalReview
from .progress import ProgressReporter


def request_body_marker_count(body: str, media_type: str, marker: str) -> int:
    """Count replaceable values after transport decoding, excluding JSON keys."""
    if not marker:
        return 0
    if media_type == "application/x-www-form-urlencoded":
        return sum(value.count(marker) for _name, value in parse_qsl(body, keep_blank_values=True))
    if media_type == "application/json" or media_type.endswith("+json"):
        def count(value: Any) -> int:
            if isinstance(value, str):
                return value.count(marker)
            if isinstance(value, list):
                return sum(count(item) for item in value)
            if isinstance(value, dict):
                return sum(count(item) for item in value.values())
            return 0

        try:
            return count(json.loads(body))
        except (ValueError, RecursionError):
            return 0
    return body.count(marker)


_SAFE_PATH_WORDS = {"api", "ask", "chat", "graphql", "messages", "projects", "query", "responses"}
CHROMIUM_SETUP_MESSAGE = "Playwright Chromium is unavailable; run: python -m playwright install chromium"
_EXTRACTION_ERRORS = {
    "response_transfer_not_approved": "Real-response model transfer was not approved. Review the combined YES disclosure or --allow-response-model-transfer.",
    "reference_required": "An independent local reference is required. Rerun with an interactive terminal (stderr) for MATCH review; redirected output and approval flags cannot bypass it.",
    "reference_declined": "Local verification was declined; no dataset requests were sent.",
    "reference_mismatch": "Extraction differs from the independent reference; no dataset requests were sent.",
    "provider_credentials_required": "Response extraction needs GEMINI_API_KEY or GOOGLE_API_KEY in the environment or --env-file.",
    "provider_destination_unsupported": "Response extraction currently supports gemini:MODEL at the Gemini API only.",
    "provider_max_tokens": "Model output reached its token limit. No partial answer was accepted and no automatic retry was sent.",
    "provider_safety_blocked": "The model provider blocked this response. No automatic retry was sent.",
    "sensitive_response": "Captured response may contain credentials. Transfer was blocked; no content was redacted or partially accepted.",
    "provider_input_limit": "Complete response exceeds the 120 KiB model-input budget. No truncation, chunking or automatic retry was used.",
}


def validate_browser_page_url(page_url: str):
    parsed = urlsplit(page_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Browser page URL must be HTTP(S) without embedded credentials")
    return parsed


def _redacted_path_shape(url: str) -> str:
    segments: list[str] = []
    for segment in urlsplit(url).path.split("/"):
        if not segment:
            continue
        lowered = segment.lower()
        if lowered in _SAFE_PATH_WORDS or re.fullmatch(r"v\d+", lowered):
            segments.append(segment)
        elif segment.isdigit():
            segments.append("{id}")
        else:
            segments.append("{opaque}")
    return "/" + "/".join(segments)


@dataclass(frozen=True, repr=False)
class CapturedInteraction:
    """One correlated browser request and its completed response."""

    method: str
    url: str
    headers: dict[str, str]
    request_body: str
    response: ObservedResponse


@dataclass(frozen=True, repr=False)
class RequestTemplate:
    """Ephemeral authenticated request material with one replaceable question."""

    method: str
    url: str
    headers: dict[str, str]
    request_body: str
    marker: str
    question_location: str

    @classmethod
    def from_interaction(cls, interaction: CapturedInteraction, marker: str) -> "RequestTemplate":
        content_type = cls._content_type(interaction.headers)
        if content_type.startswith("multipart/"):
            raise ValueError("Captured multipart requests are not supported for safe replay")
        query_count = sum(
            value.count(marker)
            for _name, value in parse_qsl(urlsplit(interaction.url).query, keep_blank_values=True)
        )
        body_count = request_body_marker_count(interaction.request_body, content_type, marker)
        if query_count + body_count != 1:
            raise ValueError("Calibration request must contain the marker exactly once")
        return cls(
            method=interaction.method,
            url=interaction.url,
            headers=dict(interaction.headers),
            request_body=interaction.request_body,
            marker=marker,
            question_location="query" if query_count else "body",
        )

    @staticmethod
    def _content_type(headers: dict[str, str]) -> str:
        return next(
            (value for name, value in headers.items() if name.lower() == "content-type"),
            "",
        ).partition(";")[0].strip().lower()

    def render_body(self, question: str) -> str:
        if self.question_location == "query":
            return self.request_body
        content_type = self._content_type(self.headers)
        if content_type == "application/json" or content_type.endswith("+json"):
            try:
                payload = json.loads(self.request_body)
            except json.JSONDecodeError as error:
                raise ValueError("Captured JSON request body is invalid") from error
            replaced, count = self._replace_json_marker(payload, question)
            if count != 1:
                raise ValueError("Captured JSON request no longer contains one calibration marker")
            return json.dumps(replaced, ensure_ascii=False, separators=(",", ":"))
        if content_type == "application/x-www-form-urlencoded":
            fields = parse_qsl(self.request_body, keep_blank_values=True)
            rendered: list[tuple[str, str]] = []
            count = 0
            for name, value in fields:
                count += value.count(self.marker)
                rendered.append((name, value.replace(self.marker, question)))
            if count != 1:
                raise ValueError("Captured form request no longer contains one calibration marker")
            return urlencode(rendered)
        return self.request_body.replace(self.marker, question, 1)

    def render_url(self, question: str) -> str:
        if self.question_location != "query":
            return self.url
        parsed = urlsplit(self.url)
        fields = parse_qsl(parsed.query, keep_blank_values=True)
        rendered: list[tuple[str, str]] = []
        count = 0
        for name, value in fields:
            count += value.count(self.marker)
            rendered.append((name, value.replace(self.marker, question)))
        if count != 1:
            raise ValueError("Captured URL no longer contains one calibration marker")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(rendered), parsed.fragment))

    def _replace_json_marker(self, value: Any, question: str) -> tuple[Any, int]:
        if isinstance(value, str):
            count = value.count(self.marker)
            return value.replace(self.marker, question), count
        if isinstance(value, list):
            result: list[Any] = []
            total = 0
            for item in value:
                replaced, count = self._replace_json_marker(item, question)
                result.append(replaced)
                total += count
            return result, total
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            total = 0
            for key, item in value.items():
                replaced, count = self._replace_json_marker(item, question)
                result[key] = replaced
                total += count
            return result, total
        return value, 0


class BrowserConfirmationRequired(RuntimeError):
    """Browser replay needs an explicit approval before sending a probe or dataset."""


class BrowserAuthenticationRequired(RuntimeError):
    """The controlled browser session must be restored manually."""

    def __init__(self, message: str = "Manual sign-in is required", *, http_status: int | None = None):
        self.http_status = _safe_http_status(http_status)
        super().__init__(message)


def _safe_http_status(value: Any) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


def _safe_transport_metrics(received_bytes: Any, elapsed_seconds: Any) -> dict:
    metrics = {}
    if type(received_bytes) is int and 0 <= received_bytes <= 2**53 - 1:
        metrics["received_bytes"] = received_bytes
    if type(elapsed_seconds) in {int, float} and math.isfinite(elapsed_seconds) and elapsed_seconds >= 0:
        metrics["elapsed_seconds"] = round(elapsed_seconds, 3)
    return metrics


class BrowserRequestFailure(RuntimeError):
    """Transport classification authored by the host, never remote error text."""

    def __init__(self, reason: str, *, http_status: int | None = None,
                 received_bytes: int | None = None, elapsed_seconds: float | None = None):
        self.reason = reason if reason in {
            "http_error", "transport_failed", "stream_read_failed", "utf8_decode_failed", "browser_context_failed",
        } else "browser_request_failed"
        self.http_status = _safe_http_status(http_status)
        self.received_bytes, self.elapsed_seconds = received_bytes, elapsed_seconds
        super().__init__("Browser request failed: " + self.reason)


class BrowserRequestTimeout(TimeoutError):
    """A bounded website wait expired; only numeric transport metadata is retained."""

    def __init__(self, *, timeout_seconds: float, http_status: int | None = None,
                 received_bytes: int | None = None, elapsed_seconds: float | None = None):
        self.timeout_seconds = timeout_seconds
        self.http_status = _safe_http_status(http_status)
        self.received_bytes, self.elapsed_seconds = received_bytes, elapsed_seconds
        super().__init__("Browser replay timed out")


_DIAGNOSTIC_STAGES = frozenset({
    "calibration_capture", "run_consent", "calibration_extraction", "verification_request",
    "verification_extraction", "verification_review", "ready", "dataset_preflight", "dataset_request", "dataset_extraction",
})


def browser_failure_diagnostic(error: BaseException, evidence: dict) -> dict:
    """Whitelist values at the output boundary; never inspect exception messages."""
    stage = evidence.get("stage")
    stage = stage if type(stage) is str and stage in _DIAGNOSTIC_STAGES else "unknown"
    if isinstance(error, ExtractionError):
        reason, error_type = ExtractionError(error.reason).reason, "ExtractionError"
    elif isinstance(error, BrowserRequestFailure):
        reason, error_type = BrowserRequestFailure(error.reason).reason, "BrowserRequestFailure"
    elif isinstance(error, BrowserAuthenticationRequired):
        reason, error_type = "authentication_required", "BrowserAuthenticationRequired"
    elif isinstance(error, BrowserResponseLimitExceeded):
        reason, error_type = "response_size_limit", "BrowserResponseLimitExceeded"
    elif isinstance(error, BrowserConfirmationRequired):
        reason, error_type = "confirmation_required", "BrowserConfirmationRequired"
    elif isinstance(error, TimeoutError):
        reason, error_type = "timeout", "TimeoutError"
    elif isinstance(error, ConnectionError):
        reason, error_type = "transport_failed", "ConnectionError"
    elif isinstance(error, KeyboardInterrupt):
        reason, error_type = "cancelled", "KeyboardInterrupt"
    elif isinstance(error, RuntimeError):
        reason, error_type = "browser_request_failed", "RuntimeError"
    else:
        reason, error_type = "browser_request_failed", "Exception"
    blocked = reason == "extraction_blocked"
    diagnostic = {
        "stage": stage, "reason": reason, "error_type": error_type,
        "request_attempted": not blocked and stage in {
            "calibration_capture", "calibration_extraction", "verification_request",
            "verification_extraction", "verification_review", "dataset_request", "dataset_extraction",
        },
        "requests_stopped": True,
    }
    if isinstance(error, (BrowserRequestFailure, BrowserAuthenticationRequired, BrowserRequestTimeout)):
        status = _safe_http_status(error.http_status)
        if status is not None:
            diagnostic["http_status"] = status
    if isinstance(error, BrowserRequestTimeout):
        seconds = error.timeout_seconds
        if type(seconds) in {int, float} and math.isfinite(seconds) and 0 < seconds <= 86400:
            diagnostic["timeout_seconds"] = seconds
    if isinstance(error, (BrowserRequestFailure, BrowserRequestTimeout)):
        diagnostic.update(_safe_transport_metrics(error.received_bytes, error.elapsed_seconds))
    if blocked:
        # Reclassify the saved cause through the same whitelist, rather than logging evidence verbatim.
        cause = evidence.get("failure_reason")
        if type(cause) is str:
            safe_reason = BrowserRequestFailure(cause).reason
            if cause in {"timeout", "authentication_required", "response_size_limit", "cancelled", "confirmation_required"}:
                safe_reason = cause
            elif safe_reason == "browser_request_failed" and ExtractionError(cause).reason == cause:
                safe_reason = cause
            diagnostic["blocked_by"] = safe_reason
    return diagnostic


class BrowserResponseLimitExceeded(RuntimeError):
    """A response cannot be retained within the fixed capture byte budget."""

    def __init__(self):
        super().__init__("Browser response exceeded the 1 MiB capture limit")


def browser_error_message(error: Exception) -> str:
    """Describe a browser failure without echoing transport-supplied text."""
    if isinstance(error, FileExistsError):
        return str(error)
    if str(error) == CHROMIUM_SETUP_MESSAGE:
        return CHROMIUM_SETUP_MESSAGE
    if isinstance(error, BrowserConfirmationRequired):
        return "Browser requests were not approved. Rerun interactively and review the request count before typing YES."
    if isinstance(error, BrowserAuthenticationRequired):
        return "Browser session expired. Rerun interactively to sign in manually."
    if isinstance(error, BrowserResponseLimitExceeded):
        return "Browser response exceeded the 1 MiB capture limit. No partial answer was accepted; later requests were stopped."
    if isinstance(error, ExtractionError):
        return _EXTRACTION_ERRORS.get(error.reason, "Response extraction blocked (" + error.reason + "); private diagnostics withheld; later requests were stopped.")
    if isinstance(error, TimeoutError) or type(error).__name__ == "TimeoutError":
        return (
            "Browser operation timed out. Startup and page navigation allow 5 minutes. "
            "Check the page and sign-in, submit the exact calibration question, "
            "and wait for the final answer; use --timeout to adjust website response waiting."
        )
    return (
        f"Browser operation failed ({type(error).__name__}); private diagnostics withheld. "
        "Check the page URL and sign-in, then retry with the exact calibration question."
    )


class BrowserTarget:
    """Calibrate once, verify independently, then replay a browser question workflow."""

    def __init__(
        self,
        *,
        page_url: str,
        timeout: float,
        runs_root: str | Path | None = None,
        verbose: bool = True,
        calibration_question: str | None = None,
        driver: Any | None = None,
        driver_factory: Callable[..., Any] | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        confirmed: bool = False,
        fresh_profile: bool = False,
        extraction_options: ExtractionOptions | None = None,
        review_stream: TextIO | None = None,
    ) -> None:
        parsed = validate_browser_page_url(page_url)
        if calibration_question is not None and (
            not isinstance(calibration_question, str) or not calibration_question.strip()
        ):
            raise ValueError("--calibration-question must be a non-empty question")
        self.calibration_question = calibration_question
        self.page_url = page_url
        self.timeout = timeout
        self.verbose = verbose
        self._progress = ProgressReporter(verbose)
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.confirmed = confirmed
        self.extraction_options = extraction_options or ExtractionOptions()
        self._review = TerminalReview(review_stream)
        self._interactive: bool | None = None
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self._template: RequestTemplate | None = None
        self._extractor: ResponseExtractor | None = None
        self._remaining_requests = 0
        self._profile_cleanup: tempfile.TemporaryDirectory[str] | None = None
        self.profile_mode = "provided"
        if driver is None:
            if driver_factory is None:
                from .playwright_driver import PlaywrightBrowserDriver

                driver_factory = PlaywrightBrowserDriver

            root = Path(runs_root).resolve().parent if runs_root else Path.cwd() / ".lladar"
            profile_root = root / "browser-profiles"
            profile_root.mkdir(parents=True, exist_ok=True)
            profile_key = hashlib.sha256(self.origin.encode("utf-8")).hexdigest()[:16]
            if fresh_profile:
                self.profile_mode = "fresh"
                self._profile_cleanup = tempfile.TemporaryDirectory(
                    prefix="fresh-",
                    dir=str(profile_root),
                )
                profile_dir = Path(self._profile_cleanup.name)
            else:
                profile_dir = profile_root / profile_key
                self.profile_mode = (
                    "reused" if profile_dir.is_dir() and any(profile_dir.iterdir()) else "new"
                )
            with self._progress.waiting("Opening browser and loading page"):
                driver = driver_factory(
                    page_url=page_url,
                    profile_dir=profile_dir,
                    timeout=timeout,
                )
        self.driver = driver
        self.evidence: dict[str, Any] = {
            "mode": "browser",
            "origin": self.origin,
            "browser_profile": self.profile_mode,
            "status": "unprepared",
        }

    def _manual_calibration(self, marker: str) -> bool:
        self.output_fn("A browser window is ready. Sign in if needed, then submit this exact calibration question:")
        self.output_fn(marker)
        if self._interactive is not False:
            self.input_fn("Press Enter after the website has shown its final answer: ")
            return True
        return False

    def _approve_run(
        self,
        *,
        interactive: bool | None,
        interaction: CapturedInteraction,
        record_count: int,
        request_count: int,
        options: ExtractionOptions,
    ) -> ExtractionOptions:
        parsed = urlsplit(interaction.url)
        path_shape = _redacted_path_shape(interaction.url)
        media_type = interaction.response.content_type.partition(";")[0].strip().lower()
        record_label = "record" if record_count == 1 else "records"
        self.output_fn(
            f"Captured {interaction.method.upper()} to {parsed.scheme}://{parsed.netloc}{path_shape} "
            f"with {media_type or 'unknown response type'}; "
            f"using a {self.profile_mode} browser profile; "
            f"{record_count} selected {record_label}, "
            f"1 verification and {request_count} scheduled dataset requests require approval."
        )
        self.output_fn(
            f"Real response content (including internal answers) will be sent to {options.provider_origin}; "
            f"model: {options.model_destination}. Request headers, cookies and expected answers are excluded. "
            f"At most {request_count + 2} model calls: calibration + verification + {request_count} dataset trials; "
            "one call per response, 8192 output tokens per call, one shared 60-minute deadline. "
            "Model input is limited to 120 KiB including JSON framing, with no truncation or chunking. "
            "Charges may apply. Check that these responses are permitted to leave your organization; "
            "credential detection is conservative, not a guarantee that all sensitive content is detected."
        )
        self.output_fn(
            "Local verification displays the full verification response and extracted answer in the terminal. "
            "Terminal scrollback or recording may retain this content; normal logs and sidecars do not. "
            "MATCH is a separate fidelity check, not another authorization."
        )
        approved_by_flags = self.confirmed and options.transfer_approved is True
        if not approved_by_flags:
            accepted = interactive and self.input_fn(
                "Type YES to approve BOTH the disclosed website requests AND real-response model transfer for this run: "
            ).strip() == "YES"
            if not accepted:
                if not self.confirmed:
                    raise BrowserConfirmationRequired("Browser replay was not confirmed")
                raise ExtractionError("response_transfer_not_approved")
        self.confirmed = True
        self.evidence["consent"] = {
            "website_requests": True,
            "response_model_transfer": True,
            "source": "explicit_flags" if approved_by_flags else "interactive_combined",
        }
        return replace(options, transfer_approved=True)

    def prepare(
        self,
        _probes: list[str],
        *,
        interactive: bool | None,
        record_count: int = 0,
        request_count: int = 0,
    ) -> None:
        if interactive is None:
            interactive = sys.stdin.isatty()
        self._interactive = interactive
        self.evidence["stage"] = "calibration_capture"
        marker = self.calibration_question if self.calibration_question is not None else "LLaDAR calibration " + secrets.token_hex(12)
        try:
            with self._progress.waiting("Capturing calibration request and website response"):
                interaction = self.driver.capture_calibration(marker, self._manual_calibration)
            self._response_progress(interaction.response)
            with self._progress.waiting("Parsing captured request"):
                template = RequestTemplate.from_interaction(interaction, marker)
        except BaseException as error:
            self._block(error)
            raise
        self.evidence = {
            "mode": "browser",
            "origin": self.origin,
            "path_shape": _redacted_path_shape(interaction.url),
            "method": interaction.method.upper(),
            "browser_profile": self.profile_mode,
            "response_protocol": interaction.response.content_type.partition(";")[0].strip().lower(),
            "status": "calibrated",
            "stage": "run_consent",
        }
        try:
            options = self.extraction_options
            response_input(interaction.response, _protected_values(interaction))
            if options.reference_reader is None and (
                not interactive or not self._review.available()
            ):
                raise ExtractionError("reference_required")
            options = self._approve_run(
                interactive=interactive, interaction=interaction, record_count=record_count,
                request_count=request_count, options=options,
            )
            self.evidence["status"] = "confirmed"
            self._extractor = ResponseExtractor(options, max_calls=request_count + 2,
                                                protected_values=_protected_values(interaction))
            self.evidence["extraction"] = self._extractor.evidence
            receipt = interaction.response._receipt
            self.evidence["stage"] = "calibration_extraction"
            with self._progress.waiting("Extracting calibration answer with model"):
                self._extractor.extract(interaction.response, request_id=receipt.request_id if receipt else "")
            self._extractor.check_budget()
            self.evidence["stage"] = "verification_request"
            verification_question = (self.calibration_question if self.calibration_question is not None
                                     else "LLaDAR verification " + secrets.token_hex(12))
            with self._progress.waiting("Waiting for verification website response"):
                verification = self.driver.replay(template, verification_question, "browser-verification",
                                                  timeout_seconds=self._extractor.remaining_seconds())
            self._response_progress(verification)
            self.evidence["stage"] = "verification_extraction"
            with self._progress.waiting("Extracting verification answer with model"):
                text = self._extractor.extract(verification, request_id="browser-verification")
            self.evidence["stage"] = "verification_review"
            reference = (options.reference_reader(verification, "browser-verification")
                         if options.reference_reader is not None else self._review.review(
                             verification, request_id="browser-verification", text=text, confirm_fn=self.input_fn))
            if reference is None or reference.request_id != "browser-verification" or reference.complete is not True:
                raise ExtractionError("reference_required")
            if reference.text != text:
                raise ExtractionError("reference_mismatch")
            self._template = template
            self._remaining_requests = request_count
            self.evidence["status"] = "verified"
            self.evidence["verification"] = "independent_local_reference"
            self.evidence["stage"] = "ready"
            self._progress.emit("BROWSER", "Verification passed; ready for dataset requests")
        except BrowserConfirmationRequired:
            self.evidence["status"] = "confirmation_required"
            raise
        except BaseException as error:
            self._block(error)
            raise

    def _block(self, error: BaseException) -> None:
        diagnostic = browser_failure_diagnostic(error, self.evidence)
        self.evidence["status"] = "blocked"
        self.evidence["failure_reason"] = diagnostic["reason"]
        self.evidence["diagnostic"] = diagnostic

    def answer(self, question: str, request_id: str) -> str:
        if self.evidence["status"] == "blocked":
            raise ExtractionError("extraction_blocked")
        if self._template is None or self._extractor is None:
            raise RuntimeError("Browser target is not prepared")
        try:
            self.evidence["stage"] = "dataset_preflight"
            self._extractor.check_budget()
            if self._remaining_requests <= 0:
                raise ExtractionError("extraction_budget_exhausted")
            self._remaining_requests -= 1
            self.evidence["stage"] = "dataset_request"
            progress = getattr(self, "_progress", None) or ProgressReporter(getattr(self, "verbose", False))
            with progress.waiting("Waiting for dataset website response"):
                observation = self.driver.replay(self._template, question, request_id,
                                                 timeout_seconds=self._extractor.remaining_seconds())
            self._response_progress(observation)
            self.evidence["stage"] = "dataset_extraction"
            with progress.waiting("Extracting dataset answer with model"):
                return self._extractor.extract(observation, request_id=request_id)
        except BaseException as error:
            self._block(error)
            raise

    def _response_progress(self, response: ObservedResponse) -> None:
        progress = getattr(self, "_progress", None)
        if progress is not None and isinstance(response, ObservedResponse):
            body = response.body.encode("utf-8") if isinstance(response.body, str) else response.body
            progress.emit("BROWSER", f"Website response received bytes={len(body)}")

    def close(self) -> None:
        try:
            self.driver.close()
        finally:
            if self._profile_cleanup is not None:
                self._profile_cleanup.cleanup()

def _protected_values(interaction: CapturedInteraction) -> tuple[str, ...]:
    """Known request credentials stay local; echoed values block transfer entirely."""
    values = []
    for name, value in interaction.headers.items():
        if name.lower() in {"authorization", "proxy-authorization", "x-api-key", "api-key", "x-csrf-token", "x-xsrf-token"}:
            values.extend((value, value.removeprefix("Bearer ")))
        if name.lower() == "cookie":
            values.extend(part.partition("=")[2].strip() for part in value.split(";"))
    secret_name = re.compile(r"(?i)(token|secret|password|authorization|cookie|api.?key)")
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if secret_name.search(key) and isinstance(item, str):
                    values.append(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    try:
        visit(json.loads(interaction.request_body))
    except (ValueError, TypeError):
        pass
    for key, value in [*parse_qsl(urlsplit(interaction.url).query), *parse_qsl(interaction.request_body)]:
        if secret_name.search(key):
            values.append(value)
    return tuple(value for value in values if value)
