"""Document-driven API contracts through the public reader and runner."""
import hashlib
import pytest
import json
from pathlib import Path


GUIDE = '''POST /chat
Authentication: X-API-Key header from EXAMPLE_API_KEY environment variable.
Request: Content-Type application/json; question is the plain question text.
Request example: {"question":"hello"}
Response example: {"answer":"example answer"}
Answer: top-level answer string, unchanged.
Completion: synchronous HTTP 200, complete JSON body, nonempty answer string.
'''


def guide_plan():
    values = {"method": ("POST", 1), "path": ("/chat", 1),
              "response_mode": ("json", 7),
              "auth": ("X-API-Key from EXAMPLE_API_KEY", 2),
              "request": ("JSON question, Content-Type application/json", 3),
              "request_example": ('{"question":"hello"}', 4),
              "response_example": ('{"answer":"example answer"}', 5),
              "answer": ("top-level answer string unchanged", 6),
              "completion": ("synchronous JSON body and nonempty answer, HTTP 200", 7)}
    contract = {key: {"value": value, "evidence": [{"location": f"line:{line}", "quote": GUIDE.splitlines()[line-1]}]}
                for key, (value, line) in values.items()}
    return {"candidates": [{"id": "chat", "label": "Chat", "public_boundary": True,
                           "rationale": "Documented question API", "flow": ["HTTP", "final answer"],
                           "output": "answer", "contract": contract}], "unresolved": [], "summary": "One API"}


def document_model(monkeypatch, *, plan=None, source=None, prompts=None, read=True, documents=None):
    """Replace only the external coding-model boundary; tools and HTTP remain real."""
    import akasha
    from test_portable_api import SOURCE
    def factory(**kwargs):
        def respond(prompt):
            if prompts is not None:
                prompts.append(prompt)
            tools = {tool.name: tool for tool in kwargs["tools"]}
            if "write_harness" not in tools:
                if read:
                    next_block = 1
                    while next_block:
                        page = json.loads(tools["read_guide"].invoke({"start_block": next_block}))
                        if documents is not None:
                            documents.append(page)
                        next_block = page["next_block"]
                return json.dumps(plan() if callable(plan) else plan if plan is not None else guide_plan())
            tools["write_harness"].invoke({"filename": "adapter.py", "content": (source or SOURCE).decode()})
            return json.dumps({"harness": "adapter.py", "blockers": [], "explanation": "Documented HTTP"})
        return respond
    monkeypatch.setattr(akasha, "agents", factory)


def write_guide(path, text=GUIDE):
    """Independent authored fixtures, including native text PDF and DOCX tables."""
    if path.suffix.lower() in {".txt", ".md"}:
        path.write_text(text, encoding="utf-8")
    elif path.suffix.lower() == ".docx":
        from zipfile import ZipFile
        from xml.sax.saxutils import escape
        with ZipFile(path, "w") as archive:
            rows = ''.join(f'<w:tr><w:tc><w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p></w:tc></w:tr>' for line in text.splitlines())
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>' + rows + '</w:tbl></w:body></w:document>')
    else:
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in text.splitlines()]
        stream.set_data(('BT /F1 10 Tf 12 TL 10 700 Td ' + ' T* '.join(f'({line}) Tj' for line in lines) + ' ET').encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.add_blank_page(width=600, height=800)
        writer.write(path)


def located_plan(format):
    plan = guide_plan()
    if format in {"docx", "pdf"}:
        for field in plan["candidates"][0]["contract"].values():
            for source in field["evidence"]:
                number = source["location"].split(":")[1]
                source["location"] = f"table:1/row:{number}/cell:1/paragraph:1" if format == "docx" else "page:1"
    return plan


@pytest.mark.parametrize("format", ["md", "txt", "docx", "pdf"])
def test_cli_guide_calls_real_http_and_exports_relocatable_adapter(tmp_path, monkeypatch, format):
    from lladar.cli import main
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api, launch
    guide = tmp_path / ("guide." + format)
    write_guide(guide)
    dataset = tmp_path / "dataset.jsonl"
    question = "新的問題？"
    dataset.write_text(json.dumps({"question": question, "expected_answer": "different expected answer", "actual_response": None}) + "\n", encoding="utf-8")
    output = tmp_path / "responses.jsonl"
    document_model(monkeypatch, plan=located_plan(format))
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with api() as (url, calls):
        assert main(["run-agent", str(dataset), "--service-url", url, "--api-guide", str(guide),
                     "--output", str(output), "--no-interactive", "--no-verbose", "--lang", "en"],
                    skill_agent_factory=RandomSampleSkillAgent) == 0
        assert json.loads(output.read_text(encoding="utf-8"))["actual_response"] == "reply: " + question
        bundle = Path(str(output) + ".api-adapter")
        recipient = tmp_path / "recipient"
        recipient.mkdir()
        result = launch(bundle, recipient, url)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["output"] == "reply: hello"
        assert len(calls) == 3  # qualification, formal trial, independent relocated client
    assert not (bundle / "guide.md").exists()
    assert "recipient-private-key" not in (bundle / ".env.example").read_text()


def test_resume_reuses_verified_adapter_after_documentation_interruption(tmp_path, monkeypatch):
    import akasha
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "different", "actual_response": None}) + "\n")
    output = tmp_path / "responses.jsonl"
    document_model(monkeypatch)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    def interrupted_translation(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(akasha, "ask", interrupted_translation)
    with api() as (url, calls):
        options = dict(api_guide=guide, service_url=url, verbose=False, interactive=False,
                       strategy_agent_factory=RandomSampleSkillAgent, lang="zh-TW")
        with pytest.raises(KeyboardInterrupt):
            run_agent(dataset, output, **options)
        def unavailable_model(**kwargs):
            raise AssertionError("Accepted adapter must survive interruption without another generation")
        monkeypatch.setattr(akasha, "agents", unavailable_model)
        monkeypatch.setattr(akasha, "ask", lambda **kwargs: lambda prompt: json.dumps(json.loads(prompt)))
        assert run_agent(dataset, output, resume=Path(str(output) + ".work"), **options) == 1
    assert json.loads(output.read_text())["actual_response"] == "reply: hello"


def test_contract_cannot_claim_unread_document_evidence(tmp_path, monkeypatch):
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    document_model(monkeypatch, read=False)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    with api() as (url, calls):
        with pytest.raises(ValueError, match="unread"):
            run_agent(dataset, tmp_path / "responses.jsonl", api_guide=guide, service_url=url,
                      interactive=False, verbose=False, strategy_agent_factory=RandomSampleSkillAgent, lang="en")
        assert calls == []


def test_long_document_is_read_in_bounded_pages_without_losing_content(tmp_path):
    from lladar.api_guide import read_api_guide
    path = tmp_path / "long.txt"
    original = "x" * 30000 + "\nPOST /chat"
    path.write_text(original, encoding="utf-8")
    guide = read_api_guide(path)
    pages, next_block = [], 1
    while next_block:
        page = guide.read(start_block=next_block, max_characters=12000)
        assert sum(len(block["text"]) for block in page["blocks"]) <= 12000
        pages.extend(page["blocks"])
        next_block = page["next_block"]
    assert "".join(block["text"] for block in pages) == "x" * 30000 + "POST /chat"
    assert pages[-1]["location"] == "line:2"


def test_unsupported_response_protocol_stops_before_http(tmp_path, monkeypatch):
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    plan = guide_plan()
    plan["candidates"][0]["contract"]["response_mode"]["value"] = "websocket"
    document_model(monkeypatch, plan=plan)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    with api() as (url, calls):
        with pytest.raises(ValueError, match="Unsupported API guide response mode"):
            run_agent(dataset, tmp_path / "responses.jsonl", api_guide=guide, service_url=url,
                      interactive=False, verbose=False, strategy_agent_factory=RandomSampleSkillAgent, lang="en")
        assert calls == []


@pytest.mark.parametrize("split_value", [False, True])
def test_credential_values_in_document_are_withheld_from_model(tmp_path, monkeypatch, split_value):
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    prefix = "x" * 7992 if split_value else "Example key: "
    guide.write_text(GUIDE + prefix + "recipient-private-key\n", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    documents, prompts = [], []
    document_model(monkeypatch, documents=documents, prompts=prompts)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    with api() as (url, calls):
        run_agent(dataset, tmp_path / "responses.jsonl", api_guide=guide, service_url=url,
                  interactive=False, verbose=False, strategy_agent_factory=RandomSampleSkillAgent, lang="en")
    assert "recipient-private-key" not in json.dumps(documents + prompts)
    assert "recipient-private-key" not in "".join(block["text"] for page in documents for block in page["blocks"])
    assert "[REDACTED]" in "".join(block["text"] for page in documents for block in page["blocks"])


def test_utf8_guide_preserves_text_and_source_locations(tmp_path):
    from lladar.api_guide import read_api_guide

    path = tmp_path / "guide.MD"
    path.write_bytes(b"\xef\xbb\xbf" + "POST /chat\n問題欄位：message\n".encode("utf-8"))
    guide = read_api_guide(path)

    assert guide.format == "md"
    assert guide.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert guide.blocks[1].text == "問題欄位：message"
    assert guide.blocks[1].location == "line:2"
    assert "POST /chat" in guide.text


def test_docx_reads_body_and_table_in_document_order(tmp_path):
    from zipfile import ZipFile
    from lladar.api_guide import read_api_guide

    path = tmp_path / "guide.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>POST /chat</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>message</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>question text</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:p><w:r><w:t>JSON response.answer</w:t></w:r></w:p>
</w:body></w:document>''')
    guide = read_api_guide(path)
    assert [block.text for block in guide.blocks] == ["POST /chat", "message", "question text", "JSON response.answer"]
    assert guide.blocks[1].location == "table:1/row:1/cell:1/paragraph:1"


def test_pdf_reads_existing_text_and_discloses_empty_pages(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    from lladar.api_guide import read_api_guide

    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 200 Td (POST /chat) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_blank_page(width=400, height=400)
    path = tmp_path / "guide.pdf"
    writer.write(path)
    guide = read_api_guide(path)
    assert guide.blocks[0].text == "POST /chat"
    assert guide.blocks[0].location == "page:1"
    assert guide.empty_pages == (2,)


def test_pdf_without_text_fails_without_ocr(tmp_path):
    from pypdf import PdfWriter
    from lladar.api_guide import read_api_guide

    writer = PdfWriter()
    writer.add_blank_page(width=400, height=400)
    path = tmp_path / "scan.pdf"
    writer.write(path)
    with pytest.raises(ValueError, match="無法讀取 PDF 的文字內容"):
        read_api_guide(path)


def test_invalid_text_encoding_has_actionable_error(tmp_path):
    from lladar.api_guide import read_api_guide
    path = tmp_path / "guide.txt"
    path.write_bytes(b"\xff\xfeP\x00O\x00S\x00T\x00")
    with pytest.raises(ValueError, match="UTF-8"):
        read_api_guide(path)


def test_endpoint_must_be_present_in_cited_document_text(tmp_path, monkeypatch):
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    plan = guide_plan()
    plan["candidates"][0]["contract"]["path"]["value"] = "/invented"
    document_model(monkeypatch, plan=plan)
    with api() as (url, calls):
        with pytest.raises(ValueError, match="endpoint.*evidence"):
            run_agent(dataset, tmp_path / "responses.jsonl", api_guide=guide, service_url=url,
                      interactive=False, verbose=False, strategy_agent_factory=RandomSampleSkillAgent, lang="en")
        assert calls == []


@pytest.mark.parametrize("missing", ["auth", "request", "request_example", "response_example", "answer", "completion"])
def test_missing_contract_stops_before_any_target_request(tmp_path, monkeypatch, missing):
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    plan = guide_plan()
    del plan["candidates"][0]["contract"][missing]
    document_model(monkeypatch, plan=plan)
    output = tmp_path / "responses.jsonl"
    with api() as (url, calls):
        with pytest.raises(ValueError, match=missing):
            run_agent(dataset, output, api_guide=guide, service_url=url,
                      interactive=False, verbose=False, strategy_agent_factory=RandomSampleSkillAgent, lang="en")
        assert calls == []
    assert not Path(str(output) + ".api-adapter").exists()


@pytest.mark.parametrize("kind", ["missing", "empty", "unsupported", "encoding", "docx", "pdf", "scan", "encrypted"])
def test_unreadable_guide_fails_before_model_or_http(tmp_path, monkeypatch, capsys, kind):
    import akasha
    from lladar.cli import main
    from pypdf import PdfWriter
    path = tmp_path / {"unsupported": "guide.doc", "docx": "guide.docx",
                       "pdf": "guide.pdf", "scan": "guide.pdf", "encrypted": "guide.pdf"}.get(kind, "guide.txt")
    if kind in {"scan", "encrypted"}:
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        if kind == "encrypted":
            writer.encrypt("secret")
        writer.write(path)
    elif kind != "missing":
        path.write_bytes(b"" if kind == "empty" else b"\xff" if kind == "encoding" else b"corrupt")
    def forbidden(**kwargs):
        raise AssertionError("Invalid documents must fail before model calls")
    monkeypatch.setattr(akasha, "agents", forbidden)
    monkeypatch.setattr(akasha, "ask", forbidden)
    output = tmp_path / "responses.jsonl"
    assert main(["run-agent", "nonexistent.jsonl", "--api-guide", str(path), "--service-url", "http://127.0.0.1:1",
                 "--output", str(output), "--no-verbose", "--lang", "en"], skill_agent_factory=forbidden) == 2
    expected = {"missing": "guide.txt", "empty": "no readable text", "unsupported": "Unsupported API guide format",
                "encoding": "UTF-8", "docx": "Unable to read DOCX", "pdf": "Unable to read PDF",
                "scan": "PDF", "encrypted": "Encrypted PDF"}[kind]
    assert expected in capsys.readouterr().err
    assert not Path(str(output) + ".api-adapter").exists()


@pytest.mark.parametrize("extra", [[], ["--project", "."], ["--page-url", "https://example.test"], ["--situation-config", "config.json"]])
def test_cli_guide_rejects_conflicting_modes_before_model(tmp_path, extra):
    from lladar.cli import main
    def forbidden(**kwargs):
        raise AssertionError("Mode conflict must fail before model calls")
    argv = ["run-agent", "dataset.jsonl", "--api-guide", "guide.md", "--output", str(tmp_path / "responses.jsonl"), "--lang", "en"]
    if extra:
        argv += ["--service-url", "http://127.0.0.1:1"] + extra
    with pytest.raises(SystemExit) as error:
        main(argv, skill_agent_factory=forbidden)
    assert error.value.code == 2


def test_ambiguous_guide_requires_choice_without_probe_requests(tmp_path, monkeypatch):
    from lladar.runner import run_agent
    from lladar.interfaces import NeedsConfirmation
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    import copy
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    plan = guide_plan()
    alternate = copy.deepcopy(plan["candidates"][0])
    alternate["id"] = "other"
    plan["candidates"].append(alternate)
    document_model(monkeypatch, plan=plan)
    with api() as (url, calls):
        with pytest.raises(NeedsConfirmation):
            run_agent(dataset, tmp_path / "responses.jsonl", api_guide=guide, service_url=url,
                      interactive=False, verbose=False, strategy_agent_factory=RandomSampleSkillAgent, lang="en")
        assert calls == []


@pytest.mark.parametrize("mode", ["sse", "polling"])
@pytest.mark.parametrize("completion", ["complete", "incomplete", "uncorrelated"])
def test_documented_streaming_and_polling_require_complete_correlated_answer(tmp_path, monkeypatch, mode, completion):
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from fixture_api_guide import guide_service, SSE_SOURCE, POLL_SOURCE
    detail = ("SSE: send client_id with question; each event echoes client_id; concatenate text chunks unchanged; "
              "done=true ends the correlated stream. Example: data: {\"client_id\":\"example-id\",\"text\":\"example\",\"done\":true}"
              if mode == "sse" else
              "Polling: POST returns job_id; GET /jobs/{job_id} at most 3 times; each response echoes job_id; "
              "status=done completes the job; extract answer unchanged. Example: {\"job_id\":\"example-id\",\"status\":\"done\",\"answer\":\"example\"}")
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE + detail + "\n", encoding="utf-8")
    plan = guide_plan()
    contract = plan["candidates"][0]["contract"]
    for field in ("response_mode", "request", "response_example", "answer", "completion"):
        contract[field] = {"value": mode if field == "response_mode" else detail,
                           "evidence": [{"location": "line:8", "quote": detail}]}
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "not the example", "actual_response": None}) + "\n")
    source = SSE_SOURCE if mode == "sse" else POLL_SOURCE
    adapter = tmp_path / "existing.py"
    adapter.write_bytes(source)
    document_model(monkeypatch, plan=plan, source=source)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    output = tmp_path / "responses.jsonl"
    with guide_service(mode, complete=completion != "incomplete", correlated=completion != "uncorrelated") as (url, calls):
        options = dict(api_guide=guide, service_url=url, interactive=False, verbose=False,
                       strategy_agent_factory=RandomSampleSkillAgent, lang="en", timeout=5)
        if completion == "complete":
            assert run_agent(dataset, output, **options) == 1
            assert json.loads(output.read_text())["actual_response"] == "reply: hello"
            assert calls[-1]["question"] == "hello"
        else:
            with pytest.raises(RuntimeError, match="Existing adapter verification failed"):
                run_agent(dataset, output, adapt=adapter, **options)
            assert not output.exists()
            assert not Path(str(output) + ".api-adapter").exists()
    assert adapter.read_bytes() == source


def test_guide_resume_never_resubmits_completed_or_unknown_formal_requests(tmp_path, monkeypatch):
    import akasha
    from lladar.runner import run_agent
    from test_simple_pipeline import StrategySkillAgent
    from fixture_api_guide import guide_service, POLL_SOURCE
    detail = "Polling: POST returns job_id; GET /jobs/{job_id} at most 3 times; status=done completes a correlated job_id; extract answer unchanged."
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE + detail + "\n", encoding="utf-8")
    plan = guide_plan()
    for field in ("response_mode", "request", "response_example", "answer", "completion"):
        plan["candidates"][0]["contract"][field] = {"value": "polling" if field == "response_mode" else detail,
            "evidence": [{"location": "line:8", "quote": detail}]}
    document_model(monkeypatch, plan=plan, source=POLL_SOURCE)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    output = tmp_path / "responses.jsonl"
    with guide_service("polling", interrupt_at=3) as (url, calls):
        options = dict(api_guide=guide, service_url=url, interactive=False, verbose=False,
                       strategy_agent_factory=StrategySkillAgent, lang="en", timeout=5)
        with pytest.raises(KeyboardInterrupt):
            run_agent(dataset, output, **options)
        def unavailable(**kwargs):
            raise AssertionError("Resume must use accepted work")
        monkeypatch.setattr(akasha, "agents", unavailable)
        assert run_agent(dataset, output, resume=Path(str(output) + ".work"), **options) == 1
        assert len(calls) == 5  # two qualifications; three distinct formal trials, none resent
    trials = [json.loads(line) for line in Path(str(output) + ".trials.jsonl").read_text().splitlines()]
    assert [trial["status"] for trial in trials] == ["ok", "execution_error", "ok"]
    assert trials[1]["completion_state"] == "completion_unknown"
    assert json.loads(output.read_text())["actual_response"] == "reply: hello"


def test_chinese_help_explains_document_mode_and_no_ocr(capsys):
    from lladar.cli import main
    with pytest.raises(SystemExit) as error:
        main(["run-agent", "--help", "--lang", "zh-TW"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "API 使用說明" in help_text
    assert "不做 OCR" in help_text


def test_adapter_containing_known_credential_is_never_executed_or_exported(tmp_path, monkeypatch):
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api, SOURCE
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    adapter = tmp_path / "adapter.py"
    adapter.write_bytes(SOURCE + b"\n# recipient-private-key\n")
    document_model(monkeypatch)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    output = tmp_path / "responses.jsonl"
    with api() as (url, calls):
        with pytest.raises(ValueError, match="credential values"):
            run_agent(dataset, output, api_guide=guide, service_url=url, adapt=adapter,
                      interactive=False, verbose=False, strategy_agent_factory=RandomSampleSkillAgent, lang="en")
        assert calls == []
    assert not Path(str(output) + ".api-adapter").exists()


def test_malformed_model_candidate_can_recover_without_speculative_http(tmp_path, monkeypatch):
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    replies = iter([{"candidates": [None], "unresolved": []}, guide_plan()])
    document_model(monkeypatch, plan=lambda: next(replies))
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    with api() as (url, calls):
        output = tmp_path / "responses.jsonl"
        assert run_agent(dataset, output, api_guide=guide, service_url=url, interactive=False, verbose=False,
                         strategy_agent_factory=RandomSampleSkillAgent, lang="en") == 1
    assert json.loads(output.read_text())["actual_response"] == "reply: hello"


def test_resume_rejects_changed_accepted_contract_before_http(tmp_path, monkeypatch):
    import akasha
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    output = tmp_path / "responses.jsonl"
    document_model(monkeypatch)
    base_model = akasha.agents
    def interrupted(**kwargs):
        if any(tool.name == "write_harness" for tool in kwargs["tools"]):
            def stop(prompt):
                raise KeyboardInterrupt()
            return stop
        return base_model(**kwargs)
    monkeypatch.setattr(akasha, "agents", interrupted)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    with api() as (url, calls):
        options = dict(api_guide=guide, service_url=url, interactive=False, verbose=False,
                       strategy_agent_factory=RandomSampleSkillAgent, lang="en")
        with pytest.raises(KeyboardInterrupt):
            run_agent(dataset, output, **options)
        session = Path(str(output) + ".work")
        state = json.loads((session / "session.json").read_text())["data"]["state"]
        contract_path = Path(state["guide_workspace"]).parent / "adapter" / "interfaces.json"
        plan = json.loads(contract_path.read_text())
        plan["candidates"][0]["contract"]["answer"]["value"] = "Different accepted answer contract"
        contract_path.write_text(json.dumps(plan), encoding="utf-8")
        document_model(monkeypatch)
        with pytest.raises(ValueError, match="API guide contract changed"):
            run_agent(dataset, output, resume=session, **options)
        assert calls == []


@pytest.mark.parametrize("tamper", [False, True])
def test_resume_continues_adapter_verification_without_regenerating_code(tmp_path, monkeypatch, tamper):
    import akasha
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from fixture_api_guide import guide_service, POLL_SOURCE
    detail = "Polling: POST returns job_id; GET /jobs/{job_id} at most 3 times; status=done completes a correlated job_id; extract answer unchanged."
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE + detail + "\n", encoding="utf-8")
    plan = guide_plan()
    for field in ("response_mode", "request", "response_example", "answer", "completion"):
        plan["candidates"][0]["contract"][field] = {"value": "polling" if field == "response_mode" else detail,
            "evidence": [{"location": "line:8", "quote": detail}]}
    document_model(monkeypatch, plan=plan, source=POLL_SOURCE)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    output = tmp_path / "responses.jsonl"
    with guide_service("polling", interrupt_at=1) as (url, calls):
        options = dict(api_guide=guide, service_url=url, interactive=False, verbose=False,
                       strategy_agent_factory=RandomSampleSkillAgent, lang="en", timeout=5)
        with pytest.raises(KeyboardInterrupt):
            run_agent(dataset, output, **options)
        def unavailable(**kwargs):
            raise AssertionError("Generated code must survive verification interruption")
        monkeypatch.setattr(akasha, "agents", unavailable)
        session = Path(str(output) + ".work")
        if tamper:
            adapter = next(session.glob("guide-*/adapter/adapter.py"))
            adapter.write_bytes(POLL_SOURCE + b"\n# modified\n")
            with pytest.raises(ValueError, match="Saved API guide adapter changed"):
                run_agent(dataset, output, resume=session, **options)
            assert len(calls) == 1
        else:
            assert run_agent(dataset, output, resume=session, **options) == 1
            assert json.loads(output.read_text())["actual_response"] == "reply: hello"


@pytest.mark.parametrize("change", ["guide", "service", "checkpoint"])
def test_resume_rejects_changed_inputs_and_corrupt_checkpoint_before_model_or_http(tmp_path, monkeypatch, change):
    import akasha
    from lladar.runner import run_agent
    from test_simple_pipeline import RandomSampleSkillAgent
    from test_portable_api import api
    guide = tmp_path / "guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "unused", "actual_response": None}) + "\n")
    output = tmp_path / "responses.jsonl"
    document_model(monkeypatch)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    def stop(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(akasha, "ask", stop)
    with api() as (url, calls):
        options = dict(api_guide=guide, service_url=url, interactive=False, verbose=False,
                       strategy_agent_factory=RandomSampleSkillAgent, lang="zh-TW")
        with pytest.raises(KeyboardInterrupt):
            run_agent(dataset, output, **options)
        session = Path(str(output) + ".work")
        if change == "guide":
            guide.write_text(GUIDE + "changed\n", encoding="utf-8")
        elif change == "service":
            options["service_url"] += "/changed"
        else:
            checkpoint = session / "session.json"
            envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
            envelope["data"]["state"]["guide_contract"]["candidate_id"] = "tampered"
            checkpoint.write_text(json.dumps(envelope), encoding="utf-8")
        def unavailable(**kwargs):
            raise AssertionError("Changed inputs must be rejected before model calls")
        monkeypatch.setattr(akasha, "agents", unavailable)
        monkeypatch.setattr(akasha, "ask", unavailable)
        with pytest.raises(ValueError, match="checkpoint integrity" if change == "checkpoint" else "resume configuration differs"):
            run_agent(dataset, output, resume=session, **options)
        assert len(calls) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_standalone_adapter_has_finite_total_wait_for_endless_sse(tmp_path, legacy):
    import os
    import subprocess
    import sys
    from lladar.portable_api import write_bundle
    from fixture_api_guide import guide_service, SSE_SOURCE, LEGACY_SSE_SOURCE
    bundle = tmp_path / "client"
    source = LEGACY_SSE_SOURCE if legacy else SSE_SOURCE
    original = tmp_path / "original.py"
    original.write_bytes(source)
    write_bundle(bundle, original.read_bytes())
    environment = dict(os.environ, EXAMPLE_API_KEY="recipient-private-key", LLADAR_API_TIMEOUT="0.2",
                       NO_PROXY="127.0.0.1,localhost")
    with guide_service("sse", complete=False, endless=True) as (url, calls):
        result = subprocess.run([sys.executable, "-I", "-S", str(bundle / "adapter.py"),
                                 "--service-url", url, "--message", "hello"],
                                cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=2)
    assert result.returncode == 1
    assert not result.stdout
    assert "timed out" in result.stderr
    assert len(calls) == 1
    assert original.read_bytes() == source
