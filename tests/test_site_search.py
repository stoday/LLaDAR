"""Search real bilingual docs through the static deployment's public UI."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import runpy
import shutil
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = runpy.run_path(str(ROOT / "scripts/build_site_search.py"))


def test_site_search_index_covers_pages_and_ignores_navigation():
    source = BUILD["build"](ROOT / "site")
    saved = (ROOT / "site/assets/search-index.js").read_text(encoding="utf-8")
    assert source == saved, "Run python scripts/build_site_search.py after editing site HTML"
    data = json.loads(source.split("window.LLaDARSearchIndex = ", 1)[1].removesuffix(";\n"))
    pages = {p.relative_to(ROOT / "site").as_posix() for p in (ROOT / "site").rglob("*.html")}
    assert {doc["url"] for doc in data["documents"]} == pages
    assert {doc["lang"] for doc in data["documents"]} == {"en", "zh-TW"}
    doc = BUILD["Document"]('<h1>Title</h1><main><nav>Navigation-only secret</nav>'
                            '<h2>Topic <code>--count</code></h2><p>中文 usage_metadata</p></main>')
    assert BUILD["text"](doc.title_parts) == "Title"
    assert doc.sections == [{"heading": "Topic --count", "anchor": "doc-search-section-1",
                             "text": "中文 usage_metadata"}]


@pytest.fixture
def docs_browser(tmp_path):
    from playwright.sync_api import sync_playwright

    site = tmp_path / "site"
    shutil.copytree(ROOT / "site", site)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            options = {"headless": True}
            if os.environ.get("SITE_SEARCH_BROWSER_CHANNEL"):
                options["channel"] = os.environ["SITE_SEARCH_BROWSER_CHANNEL"]
            browser = playwright.chromium.launch(**options)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                yield page, f"http://127.0.0.1:{server.server_port}/", site
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.browser
def test_site_search_finds_content_and_navigates_to_generated_heading(docs_browser):
    from playwright.sync_api import expect

    page, base, _ = docs_browser
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base)
    page.get_by_label("Search docs", exact=True).fill("provider-reported")
    results = page.locator(".doc-search-link")
    expect(results.first).to_be_visible()
    assert all("/zh-TW/" not in href for href in results.evaluate_all("nodes => nodes.map(n => n.href)"))
    expect(page.locator(".doc-search-snippet mark").first).to_have_text("provider-reported")
    panel = page.locator(".doc-search-results").bounding_box()
    assert panel["y"] >= 0 and panel["y"] + panel["height"] <= 900
    # A heading without an authored id must still be a working deep link.
    page.get_by_label("Search docs", exact=True).fill("Every output question is covered")
    expect(results).to_have_count(1)
    href = results.first.get_attribute("href")
    assert "#doc-search-section-" in href
    results.first.click()
    page.wait_for_url(href)
    target = page.locator("#" + page.url.split("#", 1)[1])
    expect(target).to_be_in_viewport()
    assert not errors


@pytest.mark.browser
def test_site_search_chinese_scope_empty_results_and_keyboard(docs_browser):
    from playwright.sync_api import expect

    page, base, _ = docs_browser
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(base + "zh-TW/guides/create-dataset.html")
    page.keyboard.press("/")
    field = page.get_by_label("搜尋文件", exact=True)
    expect(field).to_be_focused()
    field.fill("用量")
    results = page.locator(".doc-search-link")
    expect(results.first).to_be_visible()
    assert all("/zh-TW/" in href for href in results.evaluate_all("nodes => nodes.map(n => n.href)"))
    field.fill("provider-reported")
    expect(page.get_by_role("status")).to_contain_text("找不到符合")
    page.get_by_label("搜尋範圍", exact=True).select_option("all")
    expect(results.first).to_be_visible()
    assert any("/zh-TW/" not in href for href in results.evaluate_all("nodes => nodes.map(n => n.href)"))
    field.fill('<script>alert("x")</script>')
    expect(page.get_by_role("status")).to_contain_text("找不到符合")
    assert page.locator(".doc-search-results script").count() == 0
    field.fill("")
    expect(results).to_have_count(0)
    expect(page.get_by_role("status")).to_contain_text("輸入字串")
    field.press("Escape")
    expect(page.locator(".doc-search-results")).to_be_hidden()
    assert all(url.startswith(base) for url in requests)


@pytest.mark.browser
def test_site_search_recovers_from_index_failure_and_fits_mobile(docs_browser):
    from playwright.sync_api import expect

    page, base, _ = docs_browser
    page.set_viewport_size({"width": 390, "height": 844})
    page.route("**/assets/search-index.js", lambda route: route.abort())
    page.goto(base + "zh-TW/index.html")
    field = page.get_by_label("搜尋文件", exact=True)
    field.fill("token")
    expect(page.get_by_role("status")).to_contain_text("無法載入")
    page.unroute("**/assets/search-index.js")
    field.fill("用量")
    expect(page.locator(".doc-search-link").first).to_be_visible()
    panel = page.locator(".doc-search-results").bounding_box()
    assert panel["x"] >= 0 and panel["x"] + panel["width"] <= 391
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_site_search_works_when_opened_as_local_html(docs_browser):
    from playwright.sync_api import expect

    page, _, site = docs_browser
    page.goto((site / "index.html").as_uri())
    page.get_by_label("Search docs", exact=True).fill("token_usage")
    expect(page.locator(".doc-search-link").first).to_be_visible()
    assert page.locator(".doc-search-link").first.get_attribute("href").startswith("file:")
