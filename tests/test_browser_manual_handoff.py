"""Manual login leaves native dialogs to the user and checks the browser first."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
import pytest

from lladar.answer_extraction import ExtractionOptions
from lladar.browser_discovery_driver import GenericBrowserDriver
from lladar.browser_discovery_driver import BrowserDiscoveryError as DriverError
from lladar.browser_target import BrowserTarget, browser_failure_diagnostic


def test_login_connection_failure_is_reported_before_model_disclosure(tmp_path):
    events = []
    output = []

    class DisconnectedBrowser:
        manual = False

        @contextmanager
        def manual_interaction(self):
            self.manual = True
            events.append("manual_begin")
            try:
                yield
            finally:
                events.append("manual_end")
                self.manual = False

        def open(self):
            assert self.manual, "Protect native dialogs before the first navigation."
            events.append("open")

        def check_connection(self):
            events.append("check_connection")
            raise DriverError("browser_connection_lost")

        def inspect(self):
            pytest.fail("Do not inspect or disclose a disconnected browser.")

        def close(self):
            events.append("close")

    browser = DisconnectedBrowser()

    def login_input(_prompt):
        assert browser.manual
        events.append("login_finished")
        return ""

    def agent_factory(**_settings):
        pytest.fail("A failed login handoff must not construct a model Agent.")

    target = BrowserTarget(page_url="https://fixture.test/chat", driver=browser,
        workspace=tmp_path, agent_factory=agent_factory, confirmed=True,
        extraction_options=ExtractionOptions(transfer_approved=True),
        input_fn=login_input, output_fn=output.append)
    try:
        with pytest.raises(DriverError) as failure:
            target.prepare([], interactive=True, record_count=1, request_count=1)
        assert events[:3] == ["manual_begin", "open", "login_finished"]
        assert "check_connection" in events and "manual_end" in events
        assert not browser.manual
        diagnostic = browser_failure_diagnostic(failure.value, target.evidence)
        assert diagnostic["stage"] == "checking_browser_connection"
        assert diagnostic["reason"] == "browser_connection_lost"
        assert diagnostic["request_attempted"] is False
        assert target.evidence["status"] == "blocked"
        assert target.evidence["failure_reason"] == "browser_connection_lost"
        assert target.ledger.data["counts"] == {}
        assert not any("Website exploration uses Akasha" in value for value in output)
        report = (tmp_path / "discovery-report.md").read_text(encoding="utf-8")
        assert "Status: blocked" in report
        assert "Failure: browser_connection_lost" in report
    finally:
        target.close()


def test_open_failure_gets_a_blocked_report_with_an_opening_stage(tmp_path):
    class UnavailableBrowser:
        @contextmanager
        def manual_interaction(self):
            yield

        def open(self):
            raise DriverError("browser_open_failed")

        def close(self):
            pass

    target = BrowserTarget(page_url="https://fixture.test/chat", driver=UnavailableBrowser(),
        workspace=tmp_path, agent_factory=lambda **_: pytest.fail("No model call before a browser opens."),
        input_fn=lambda _: pytest.fail("No login prompt for an unavailable browser."), output_fn=lambda _: None)
    try:
        with pytest.raises(DriverError) as failure:
            target.prepare([], interactive=True)
        assert target.evidence["status"] == "blocked"
        assert browser_failure_diagnostic(failure.value, target.evidence)["stage"] == "opening_browser"
        assert (tmp_path / "discovery-report.md").is_file()
        assert target.ledger.data["counts"] == {}
    finally:
        target.close()


def test_login_selects_the_chat_popup_and_protects_both_manual_pages_from_probe_actions(tmp_path):
    class HandedPage:
        def wait_for_timeout(self, _milliseconds):
            pass

    original, popup = HandedPage(), HandedPage()
    mutations = []
    selections = []
    prompts = []
    feedback = []
    generated_feedback = []
    close_feedback = []
    invalid_close_feedback = []

    class ManualBrowser:
        context = SimpleNamespace(pages=[original, popup])
        page = original
        allowed_origins = {"https://portal.fixture.test"}
        # A page handed back to the user can remain host-owned after an earlier
        # interrupted probe. Manual-page protection must still take precedence.
        _owned_pages = {original, popup}

        @contextmanager
        def manual_interaction(self):
            yield

        def open(self):
            pass

        def check_connection(self):
            pass

        def manual_pages(self):
            return [{"page_index": 0, "page_id": "portal-page", "origin": "https://portal.fixture.test"},
                {"page_index": 1, "page_id": "chat-page", "origin": "https://chat.fixture.test"}]

        def select_manual_page(self, page_index, *, expected_page_id=None):
            assert page_index == 1 and expected_page_id == "chat-page"
            self.page = self.context.pages[page_index]
            selections.append(self.page)
            return self.manual_pages()[page_index]

        def inspect(self):
            index = self.context.pages.index(self.page)
            return {"frames": [], "active_page_index": index,
                "active_page_id": "chat-page" if index else "portal-page"}

        def action(self, spec, *, submit=False):
            if spec["kind"] == "select_page":
                self.page = self.context.pages[spec["page_index"]]
                return {"ok": True}
            mutations.append((self.page, spec["kind"]))
            return {"ok": True}

        def close(self):
            pass

    def human_input(prompt):
        prompts.append(prompt)
        return "" if len(prompts) == 1 else "1"

    def factory(**settings):
        def agent(_prompt):
            tools = settings["tools"]
            for index in [0, 1]:
                tools["inspect_action"](json.dumps({"kind": "select_page", "page_index": index}))
                feedback.append(json.loads(tools["inspect_action"](json.dumps({"kind": "navigate",
                    "url": "https://portal.fixture.test/probe"}))))
                for action in [{"kind": "close_page", "page_index": index}, {"kind": "close_page"}]:
                    close_feedback.append(json.loads(tools["inspect_action"](json.dumps(action))))
                source = ('def adapt(payload):\n'
                    '    if payload["phase"] == "setup":\n'
                    '        return {"actions": [{"kind": "select_page", "page_index": '+str(index)+'}, '
                    '{"kind": "navigate", "url": "https://portal.fixture.test/probe"}], "context": {}}\n'
                    '    return {"actions": [], "context": {}}\n')
                tools["submit_adapter"](source, "Verify that manual pages cannot become probe pages.")
                generated_feedback.append(json.loads(tools["test_adapter"]()))
            for index in [True, "1", 1.0, -1]:
                invalid_close_feedback.append(json.loads(tools["inspect_action"](
                    json.dumps({"kind": "close_page", "page_index": index}))))
        return agent

    target = BrowserTarget(page_url="https://portal.fixture.test", driver=ManualBrowser(),
        workspace=tmp_path, confirmed=True, agent_factory=factory,
        extraction_options=ExtractionOptions(transfer_approved=True),
        input_fn=human_input, output_fn=lambda _: None)
    try:
        with pytest.raises(RuntimeError, match="adapter_not_qualified"):
            target.prepare([], interactive=True)
        assert selections == [popup]
        assert len(prompts) == 2
        assert not mutations
        assert [item["reason"] for item in feedback] == ["probe_session_not_isolated"] * 2
        assert [item["reason"] for item in generated_feedback] == ["probe_session_not_isolated"] * 2
        assert [item["reason"] for item in close_feedback] == ["probe_session_not_isolated"] * 4
        assert [item["reason"] for item in invalid_close_feedback] == ["browser_page_unknown"] * 4
        assert target.driver.context.pages == [original, popup]
        assert "https://chat.fixture.test" in target.evidence["service_origins"]
        assert target.ledger.data["counts"].get("submissions", 0) == 0
    finally:
        target.close()


@contextmanager
def manual_login_site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = b"<!doctype html><title>Manual login fixture</title><p>Ready</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


@pytest.mark.browser
@pytest.mark.parametrize("popup", [False, True], ids=["main-page", "popup-page"])
@pytest.mark.parametrize("abort", [False, True], ids=["normal-return", "input-interrupted"])
def test_manual_dialog_stays_open_for_user_and_default_behavior_returns(tmp_path, popup, abort):
    """CDP acts as the user; the driver never accepts or dismisses this dialog."""
    with manual_login_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile",
            headless=True, timeout=3)

        def handoff():
            with driver.manual_interaction():
                driver.open()
                page = driver.context.new_page() if popup else driver.page
                if popup:
                    page.goto(url)
                session = driver.context.new_cdp_session(page)
                try:
                    session.send("Page.enable")
                    page.evaluate("""() => {
                        window.manualDialogReturned = false;
                        setTimeout(() => {
                            alert('Manual fixture dialog');
                            window.manualDialogReturned = true;
                        }, 150);
                    }""")
                    page.wait_for_timeout(250)
                    # This command fails with 'No dialog is showing' if the
                    # default Playwright handler already dismissed the alert.
                    session.send("Page.handleJavaScriptDialog", {"accept": True})
                    assert page.evaluate("() => window.manualDialogReturned") is True
                    driver.check_connection()
                    if abort:
                        raise LookupError("Synthetic interrupted terminal input")
                finally:
                    session.detach()
            return page

        try:
            if abort:
                with pytest.raises(LookupError, match="Synthetic interrupted terminal input"):
                    handoff()
                page = driver.context.pages[-1] if popup else driver.page
            else:
                page = handoff()
            # Once the manual handoff ends, normal automated operations retain
            # Playwright's default dialog dismissal, including on popup pages.
            page.evaluate("""() => {
                window.automaticDialogReturned = false;
                setTimeout(() => {
                    alert('Automatic fixture dialog');
                    window.automaticDialogReturned = true;
                }, 150);
            }""")
            page.wait_for_timeout(250)
            assert page.evaluate("() => window.automaticDialogReturned") is True
            driver.check_connection()
        finally:
            driver.close()


@pytest.mark.browser
def test_connection_check_classifies_a_closed_page_without_model_or_navigation(tmp_path):
    with manual_login_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile",
            headless=True, timeout=3)
        try:
            driver.open()
            driver.check_connection()
            driver.page.close()
            with pytest.raises(DriverError) as failure:
                driver.check_connection()
            assert failure.value.reason == "browser_connection_lost"
            assert failure.value.request_attempted is False
        finally:
            driver.close()


@pytest.mark.browser
def test_manual_popup_selection_binds_actual_page_and_lists_only_safe_origins(tmp_path):
    with manual_login_site() as url:
        driver = GenericBrowserDriver(page_url=url + "portal?token=private-portal-query",
            profile_dir=tmp_path / "profile", headless=True, timeout=3)
        try:
            driver.open()
            original = driver.page
            popup = driver.context.new_page()
            popup.goto(url + "private-chat-path?session=private-chat-query")
            popup.set_content("<title>private-chat-title</title><p>private-chat-content</p>")
            blank = driver.context.new_page()
            inventory = driver.manual_pages()
            assert [entry["page_index"] for entry in inventory] == [0, 1]
            assert all(set(entry) == {"page_index", "page_id", "origin"} for entry in inventory)
            assert all(entry["origin"] == url.rstrip("/") for entry in inventory)
            assert len({entry["page_id"] for entry in inventory}) == 2
            assert driver.page is original
            selected = inventory[1]
            driver.select_manual_page(selected["page_index"], expected_page_id=selected["page_id"])
            assert driver.page is popup
            assert driver.inspect()["active_page_id"] == selected["page_id"]
            assert popup.url == url + "private-chat-path?session=private-chat-query"
            assert not any(event["kind"] == "submission_attempted" for event in driver.events())
            with pytest.raises(DriverError) as failure:
                driver.select_manual_page(driver.context.pages.index(blank))
            assert failure.value.reason == "browser_url_invalid"
        finally:
            driver.close()


@pytest.mark.browser
def test_manual_page_selection_rejects_a_closed_replaced_popup_and_invalid_indices(tmp_path):
    with manual_login_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile",
            headless=True, timeout=3)
        try:
            driver.open()
            original = driver.page
            popup = driver.context.new_page()
            popup.goto(url)
            selected = driver.manual_pages()[1]
            popup.close()
            replacement = driver.context.new_page()
            replacement.goto(url)
            assert driver.context.pages[selected["page_index"]] is replacement
            for index, expected in [(selected["page_index"], selected["page_id"]),
                (-1, None), (999, None), (True, None), ("1", None)]:
                with pytest.raises(DriverError) as failure:
                    driver.select_manual_page(index, expected_page_id=expected)
                assert failure.value.reason == "browser_page_unknown"
                assert driver.page is original
        finally:
            driver.close()
