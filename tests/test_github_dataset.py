"""GitHub datasets exercised through the public CLI and generation API."""

import hashlib
import json
from pathlib import Path

import pytest

from lladar.api import create_test_dataset
from lladar.cli import build_parser
from lladar.cli import main


def test_cli_accepts_github_as_the_only_source_and_rejects_mixed_sources():
    parser = build_parser()
    args = parser.parse_args([
        "create", "test-dataset", "--from-github", "https://github.com/example/corpus",
    ])
    assert args.from_github == "https://github.com/example/corpus"
    assert args.knowledge is None
    with pytest.raises(SystemExit):
        parser.parse_args([
            "create", "test-dataset", "--from-github", "https://github.com/example/corpus",
            "--knowledge", "notes.md",
        ])


class CorpusClient:
    """External GitHub boundary; paths deliberately differ from TWBias."""

    def resolve_repository(self, owner, repo):
        return {"sha": "a" * 40, "default_branch": "main"}

    def list_files(self, owner, repo, sha):
        return [{"path": "README.md", "size": 150}, {"path": "corpus/examples.csv", "size": 200}]

    def read_file(self, owner, repo, sha, path):
        return {
            "README.md": "The benchmark data is corpus/examples.csv. Columns: id, text, answer. Answers are annotations.",
            "corpus/examples.csv": 'id,text,answer\n42,"What is the retention, in days?",30 days\n',
        }[path].encode("utf-8")


class CorpusAgent:
    def __init__(self, *, skills, tools, **options):
        self.skill = Path(skills[0])
        self.tools = tools

    def __call__(self, request):
        if request["stage"] == "github_review":
            self.tools["submit_generation_review"]([
                {"record_fingerprint": item["record_fingerprint"], "approved": True,
                 "reason": "The source question and answer agree with the generated QA and declared basis."}
                for item in request["questions"]
            ])
        elif request["stage"] == "github_discovery":
            files = self.tools["list_repository_files"]()
            assert any(item["path"] == "corpus/examples.csv" for item in files)
            read = self.tools["read_repository_file"]("README.md")
            self.tools["read_repository_file"]("corpus/examples.csv")
            self.tools["select_dataset"](
                ["corpus/examples.csv"], "Retention benchmark", "Answer retention questions",
                "Use the question and answer annotations described by README.",
                [{"read_id": read["read_id"], "quote": "Columns: id, text, answer. Answers are annotations."}],
            )
        else:
            for source in self.tools["list_sources"]():
                page = self.tools["read_source"](source["source_id"])
                point = self.tools["submit_knowledge_points"]([{
                    "statement": "The annotated answer is 30 days.", "topic": "retention",
                    "evidence": [{"read_id": page["read_id"], "quote": page["text"]}],
                }])["accepted"][0]
                self.tools["submit_qa"]({
                    "knowledge_point_id": point, "question": "What is the retention, in days?",
                    "expected_answer": "30 days",
                })
                self.tools["record_answer_basis"](
                    "What is the retention, in days?", "30 days", "source_answer",
                    "Converted the text and answer columns without changing their meaning.", [point],
                )
        return {
            "loaded_skills": [self.skill.name],
            "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()},
        }


def test_github_generation_publishes_each_question_with_report_and_saved_evidence(tmp_path):
    output = tmp_path / "qa.jsonl"
    records = create_test_dataset(
        from_github="https://github.com/example/corpus", output=output,
        github_client=CorpusClient(), skill_agent_factory=CorpusAgent, verbose=False,
    )
    assert records == [{
        "question": "What is the retention, in days?", "expected_answer": "30 days", "actual_response": None,
    }]
    metadata = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    report = (tmp_path / "qa.generation-report.md").read_text(encoding="utf-8")
    snapshots = json.loads((tmp_path / "qa.sources.json").read_text(encoding="utf-8"))
    assert metadata["github"]["commit"] == "a" * 40
    assert metadata["dataset"]["lines"][0]["answer_basis"] == "source_answer"
    assert "What is the retention, in days?" in report
    assert "30 days" in report and "source_answer" in report
    assert "42" in report and "corpus/examples.csv" in report
    assert "qa.sources.json" in report
    assert any("42" in item["text"] for item in snapshots["files"])


@pytest.fixture
def github_http(monkeypatch):
    """Replace HTTP transport, keeping production URL handling and streaming real."""
    import requests
    import socket

    corpus = CorpusClient()
    responses = {
        "https://api.github.com/repos/example/corpus": {"default_branch": "main"},
        "https://api.github.com/repos/example/corpus/commits/main": {"sha": "a" * 40},
        f"https://api.github.com/repos/example/corpus/git/trees/{'a' * 40}?recursive=1": {
            "truncated": False, "tree": [dict(item, type="blob") for item in corpus.list_files(None, None, None)],
        },
    }
    for path in ("README.md", "corpus/examples.csv"):
        responses[f"https://raw.githubusercontent.com/example/corpus/{'a' * 40}/{path}"] = corpus.read_file(None, None, None, path)

    def send(adapter, request, **kwargs):
        payload = responses[request.url]
        if isinstance(payload, requests.Response):
            payload.url = request.url
            payload.request = request
            return payload
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response._content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])
    return responses


def test_cli_downloads_selected_data_and_prints_the_required_report_path(tmp_path, github_http, capsys):
    assert main([
        "create", "test-dataset", "--from-github", "https://github.com/example/corpus",
        "--output", str(tmp_path / "qa.jsonl"), "--no-verbose",
    ], skill_agent_factory=CorpusAgent) == 0
    assert (tmp_path / "qa.jsonl").exists()
    assert (tmp_path / "qa.generation-report.md").exists()
    assert "qa.generation-report.md" in capsys.readouterr().out


def test_agent_can_follow_documented_public_dataset_link(tmp_path, github_http):
    external = "https://datasets.example.org/examples.csv"
    github_http[f"https://raw.githubusercontent.com/example/corpus/{'a' * 40}/README.md"] = (
        f"Download {external}. Columns: id, text, answer. Answers are annotations."
    ).encode()
    github_http[external] = CorpusClient().read_file(None, None, None, "corpus/examples.csv")

    class ExternalAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] != "github_discovery":
                return super().__call__(request)
            page = self.tools["read_repository_file"]("README.md")
            self.tools["read_external_dataset"](external, page["read_id"])
            self.tools["select_dataset"](
                [external], "External annotated dataset", "Retention QA", "Use documented external CSV.",
                [{"read_id": page["read_id"], "quote": "Columns: id, text, answer. Answers are annotations."}],
            )
            return {"loaded_skills": [self.skill.name], "skill_files": {
                "SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest(),
            }}

    output = tmp_path / "external.jsonl"
    assert len(create_test_dataset(from_github="https://github.com/example/corpus", output=output,
                                  skill_agent_factory=ExternalAgent, verbose=False)) == 1
    snapshots = json.loads((tmp_path / "external.sources.json").read_text(encoding="utf-8"))
    source = next(item for item in snapshots["files"] if item["path"] == external)
    assert source["original_url"] == external
    assert source["retrieved_at"]
    assert source["sha256"]


def test_model_review_rejects_unsupported_answer_basis_before_publication(tmp_path):
    from lladar.exceptions import DatasetValidationError

    class RejectingAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] != "github_review":
                return super().__call__(request)
            self.tools["submit_generation_review"]([
                {"record_fingerprint": item["record_fingerprint"], "approved": False,
                 "reason": "The expected answer is unsupported by the declared annotation."}
                for item in request["questions"]
            ])
            return {"loaded_skills": [self.skill.name]}

    with pytest.raises(DatasetValidationError, match="review"):
        create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "bad.jsonl",
                            github_client=CorpusClient(), skill_agent_factory=RejectingAgent, verbose=False)
    assert not (tmp_path / "bad.jsonl").exists()
    assert not (tmp_path / "bad.generation-report.md").exists()


def test_force_replaces_dataset_report_and_evidence_together(tmp_path):
    output = tmp_path / "qa.jsonl"
    for path in (output, tmp_path / "qa.generation-report.md", tmp_path / "qa.sources.json"):
        path.write_text("old content", encoding="utf-8")
    records = create_test_dataset(from_github="https://github.com/example/corpus", output=output, force=True,
                                  github_client=CorpusClient(), skill_agent_factory=CorpusAgent, verbose=False)
    assert len(records) == 1
    assert "old content" not in (tmp_path / "qa.generation-report.md").read_text(encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["expected_answer"] == "30 days"


def test_missing_answer_evidence_is_rejected_before_publication(tmp_path):
    from lladar.exceptions import DatasetValidationError

    class MissingBasisAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] == "generation":
                self.tools["record_answer_basis"] = lambda *args: None
            return super().__call__(request)

    with pytest.raises(DatasetValidationError, match="answer basis"):
        create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "bad.jsonl",
                            github_client=CorpusClient(), skill_agent_factory=MissingBasisAgent, verbose=False)
    assert not (tmp_path / "bad.jsonl").exists()
    diagnostic = json.loads((tmp_path / "bad.generation-failure.json").read_text(encoding="utf-8"))
    assert diagnostic["status"] == "failed"
    assert diagnostic["stage"] == "generation"
    assert diagnostic["snapshots"]["files"]


@pytest.mark.parametrize("url", ["http://github.com/a/b", "https://github.com/a/b/tree/main", "https://evil.example/a/b"])
def test_invalid_repository_url_fails_before_any_model_work(tmp_path, url):
    with pytest.raises(ValueError, match="repository root"):
        create_test_dataset(from_github=url, output=tmp_path / "bad.jsonl",
                            skill_agent_factory=lambda **kw: pytest.fail("model must not start"))


def test_existing_report_is_protected_before_any_remote_or_model_work(tmp_path):
    report = tmp_path / "qa.generation-report.md"
    report.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "qa.jsonl",
                            skill_agent_factory=lambda **kw: pytest.fail("model must not start"))
    assert report.read_text(encoding="utf-8") == "keep"


def test_report_publication_failure_restores_all_previous_artifacts(tmp_path, monkeypatch):
    import os

    output = tmp_path / "qa.jsonl"
    paths = [output, Path(str(output) + ".generation.json"), tmp_path / "qa.generation-report.md", tmp_path / "qa.sources.json"]
    before = {}
    for path in paths:
        path.write_text("previous " + path.name, encoding="utf-8")
        before[path] = path.read_bytes()
    replace = os.replace

    def fail_report_once(source, target):
        if str(source).endswith(".tmp") and Path(target).name == "qa.generation-report.md":
            raise OSError("simulated report write failure")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_report_once)
    with pytest.raises(OSError, match="report write"):
        create_test_dataset(from_github="https://github.com/example/corpus", output=output, force=True,
                            github_client=CorpusClient(), skill_agent_factory=CorpusAgent, verbose=False)
    assert {path: path.read_bytes() for path in paths} == before


def test_private_external_link_is_rejected_and_does_not_publish(tmp_path, github_http, monkeypatch):
    import socket
    from lladar.exceptions import KnowledgeLoadError

    external = "http://127.0.0.1/data.csv"
    github_http[f"https://raw.githubusercontent.com/example/corpus/{'a' * 40}/README.md"] = f"Data: {external}".encode()
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: [
        (2, 1, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "8.8.8.8", 443)),
    ])

    class PrivateLinkAgent(CorpusAgent):
        def __call__(self, request):
            page = self.tools["read_repository_file"]("README.md")
            self.tools["read_external_dataset"](external, page["read_id"])

    with pytest.raises((KnowledgeLoadError, ValueError), match="private"):
        create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "bad.jsonl",
                            skill_agent_factory=PrivateLinkAgent, verbose=False)
    assert not (tmp_path / "bad.jsonl").exists()


def test_unavailable_candidate_can_be_skipped_with_its_failure_in_the_report(tmp_path):
    from lladar.exceptions import KnowledgeLoadError

    class PartialClient(CorpusClient):
        def list_files(self, *args):
            return super().list_files(*args) + [{"path": "unavailable.csv", "size": 100}]

        def read_file(self, owner, repo, sha, path):
            if path == "unavailable.csv":
                raise KnowledgeLoadError("public data download failed (HTTP 404)")
            return super().read_file(owner, repo, sha, path)

    class PartialAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] == "github_discovery":
                with pytest.raises(ValueError, match="404"):
                    self.tools["read_repository_file"]("unavailable.csv")
            return super().__call__(request)

    create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "qa.jsonl",
                        github_client=PartialClient(), skill_agent_factory=PartialAgent, verbose=False)
    report = (tmp_path / "qa.generation-report.md").read_text(encoding="utf-8")
    assert "unavailable.csv" in report and "404" in report


def test_agent_selects_specific_data_rows_and_report_retains_original_positions(tmp_path):
    class RowsClient(CorpusClient):
        def read_file(self, owner, repo, sha, path):
            if path.endswith(".csv"):
                return b'id,text,answer\n41,First question,30 days\n42,Second question,60 days\n'
            return super().read_file(owner, repo, sha, path)

    class RowsAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] == "github_discovery":
                page = self.tools["read_repository_file"]("README.md")
                self.tools["read_repository_file"]("corpus/examples.csv")
                preview = self.tools["read_dataset_rows"]("corpus/examples.csv", 2, 1)
                assert preview["rows"][0]["values"]["id"] == "42"
                self.tools["select_dataset"](
                    ["corpus/examples.csv"], "QA", "Retention", "Select only the second sample.",
                    [{"read_id": page["read_id"], "quote": "Answers are annotations."}],
                    row_selections={"corpus/examples.csv": [2]},
                )
            elif request["stage"] == "generation":
                source = self.tools["list_sources"]()[0]
                page = self.tools["read_source"](source["source_id"])
                assert "60 days" in page["text"] and "30 days" not in page["text"]
                point = self.tools["submit_knowledge_points"]([{
                    "statement": "Second question's answer is 60 days.", "topic": "Retention",
                    "evidence": [{"read_id": page["read_id"], "quote": page["text"]}],
                }])["accepted"][0]
                self.tools["submit_qa"]({"knowledge_point_id": point, "question": "Second question", "expected_answer": "60 days"})
                self.tools["record_answer_basis"]("Second question", "60 days", "source_answer", "Preserve annotation.", [point])
            else:
                return super().__call__(request)
            return {"loaded_skills": [self.skill.name], "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()}}

    rows = create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "qa.jsonl",
                               github_client=RowsClient(), skill_agent_factory=RowsAgent, verbose=False)
    assert rows[0]["expected_answer"] == "60 days"
    report = (tmp_path / "qa.generation-report.md").read_text(encoding="utf-8")
    assert "Data row: 2" in report


def test_agent_can_repair_missing_answer_basis_from_final_validation_feedback(tmp_path):
    class RepairingAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] == "generation" and request["attempt"] == 1:
                self.tools["record_answer_basis"] = lambda *args: None
            if request["stage"] == "generation" and request["attempt"] > 1:
                assert any("answer basis" in message for message in request["validation_errors"])
            return super().__call__(request)

    rows = create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "qa.jsonl",
                               github_client=CorpusClient(), skill_agent_factory=RepairingAgent, verbose=False)
    assert len(rows) == 1


def test_model_review_retries_when_the_selected_skill_was_not_loaded(tmp_path):
    class UnloadedReviewAgent(CorpusAgent):
        reviews = 0

        def __call__(self, request):
            evidence = super().__call__(request)
            if request["stage"] == "github_review":
                type(self).reviews += 1
                if type(self).reviews == 1:
                    evidence["loaded_skills"] = []
                else:
                    assert "load" in request["validation_errors"][0]
            return evidence

    rows = create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "qa.jsonl",
                               github_client=CorpusClient(), skill_agent_factory=UnloadedReviewAgent, verbose=False)
    assert len(rows) == 1
    assert UnloadedReviewAgent.reviews == 2


def test_final_report_excludes_filtered_candidates_and_explains_count_and_review_limits(tmp_path):
    class SeveralQuestionsAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] == "github_review":
                self.tools["submit_generation_review"]([
                    {"record_fingerprint": item["record_fingerprint"],
                     "approved": item["question"] != "Second candidate",
                     "reason": "Supported source question." if item["question"] != "Second candidate" else "Unsupported task."}
                    for item in request["questions"]
                ])
                return {"loaded_skills": [self.skill.name]}
            result = super().__call__(request)
            if request["stage"] == "generation":
                point = self.tools["list_knowledge_points"]()[0]["id"]
                for question in ("Second candidate", "Third candidate"):
                    self.tools["submit_qa"]({"knowledge_point_id": point, "question": question, "expected_answer": "30 days"})
                    self.tools["record_answer_basis"](question, "30 days", "derived_criterion", "Derived task.", [point])
            return result

    output = tmp_path / "qa.jsonl"
    rows = create_test_dataset(from_github="https://github.com/example/corpus", output=output, count=2,
                               github_client=CorpusClient(), skill_agent_factory=SeveralQuestionsAgent, verbose=False)
    assert len(rows) == 1
    metadata = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert metadata["github"]["counts"]["count_limit_excluded"] == 1
    assert metadata["github"]["counts"]["model_review_rejected"] == 1
    report = (tmp_path / "qa.generation-report.md").read_text(encoding="utf-8")
    assert "### Question 1" in report and "### Question 2" not in report
    assert "Third candidate" not in report


@pytest.mark.parametrize("extension,content", [
    ("json", '[{"id":"42","text":"What is the retention, in days?","answer":"30 days"}]'),
    ("jsonl", '\n{"id":"42","text":"What is the retention, in days?","answer":"30 days"}\n'),
    ("csv", '\ufeffanswer,text,id\r\n30 days,"What is the retention,\nin days?",42\r\n'),
])
def test_structured_formats_preserve_fields_identifiers_and_original_text(tmp_path, extension, content):
    path = f"records/benchmark.{extension}"

    class FormatClient(CorpusClient):
        def list_files(self, *args):
            return [{"path": "README.md", "size": 150}, {"path": path, "size": len(content.encode())}]

        def read_file(self, owner, repo, sha, requested):
            if requested == path:
                return content.encode("utf-8")
            return super().read_file(owner, repo, sha, requested)

    class FormatAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] != "github_discovery":
                return super().__call__(request)
            page = self.tools["read_repository_file"]("README.md")
            self.tools["read_repository_file"](path)
            self.tools["select_dataset"]([path], "QA", "Retention", "Use original annotations.",
                                         [{"read_id": page["read_id"], "quote": "Answers are annotations."}])
            return {"loaded_skills": [self.skill.name]}

    create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "qa.jsonl",
                        github_client=FormatClient(), skill_agent_factory=FormatAgent, verbose=False)
    snapshots = json.loads((tmp_path / "qa.sources.json").read_text(encoding="utf-8"))
    item = next(item for item in snapshots["files"] if item["path"] == path)
    assert item["text"] == content
    assert item["rows"][0]["values"]["id"] == "42"
    assert item["rows"][0]["values"]["answer"] == "30 days"


def test_redirect_to_private_network_is_rejected_before_fetching_the_destination(tmp_path, github_http, monkeypatch):
    import requests
    import socket

    response = requests.Response()
    response.status_code = 302
    response.headers["Location"] = "http://127.0.0.1/data.csv"
    response._content = b""
    response._content_consumed = True
    github_http[f"https://raw.githubusercontent.com/example/corpus/{'a' * 40}/corpus/examples.csv"] = response
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: [
        (2, 1, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "8.8.8.8", 443)),
    ])
    with pytest.raises(ValueError, match="private"):
        create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "bad.jsonl",
                            skill_agent_factory=CorpusAgent, verbose=False)
    assert not (tmp_path / "bad.jsonl").exists()


@pytest.mark.parametrize("content,reason", [
    (b"x" * (2 * 1024 * 1024 + 1), "size limit"),
    (b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 100\n", "Git LFS"),
], ids=["size-limit", "lfs-pointer"])
def test_unavailable_large_or_lfs_data_never_publishes_as_valid_qa(tmp_path, github_http, content, reason):
    github_http[f"https://raw.githubusercontent.com/example/corpus/{'a' * 40}/corpus/examples.csv"] = content
    with pytest.raises(ValueError, match=reason):
        create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "bad.jsonl",
                            skill_agent_factory=CorpusAgent, verbose=False)
    assert not (tmp_path / "bad.jsonl").exists()


def test_large_selected_tables_require_explicit_sampling_before_generation(tmp_path):
    class LargeClient(CorpusClient):
        def read_file(self, owner, repo, sha, path):
            if path.endswith(".csv"):
                return ("id,text,answer\n" + "".join(f"{i},{'sample ' * 300},30 days\n" for i in range(100))).encode()
            return super().read_file(owner, repo, sha, path)

    class SamplingAgent(CorpusAgent):
        def __call__(self, request):
            if request["stage"] != "github_discovery":
                return super().__call__(request)
            page = self.tools["read_repository_file"]("README.md")
            self.tools["read_repository_file"]("corpus/examples.csv")
            args = (["corpus/examples.csv"], "QA", "Retention", "Read one sample.",
                    [{"read_id": page["read_id"], "quote": "Answers are annotations."}])
            with pytest.raises(ValueError, match="selected source"):
                self.tools["select_dataset"](*args)
            self.tools["select_dataset"](*args, row_selections={"corpus/examples.csv": [1]})
            return {"loaded_skills": [self.skill.name]}

    rows = create_test_dataset(from_github="https://github.com/example/corpus", output=tmp_path / "qa.jsonl",
                               max_input_tokens=32000, github_client=LargeClient(), skill_agent_factory=SamplingAgent, verbose=False)
    assert len(rows) == 1

def test_native_github_generation_can_publish_twenty_evidence_backed_questions(tmp_path, monkeypatch):
    """Exercise real host tool limits through the public API with a scripted SDK."""
    from types import SimpleNamespace
    import akasha

    class ScriptedSDK(CorpusAgent):
        def __init__(self, *, skills, tools, **options):
            super().__init__(skills=skills, tools={tool.name: tool.func for tool in tools})
            self.skill_middleware = SimpleNamespace(loaded_skill_names=[self.skill.name])
            self.skill_context = SimpleNamespace(names=[self.skill.name])

        def __call__(self, payload):
            request = json.loads(payload)
            if request['stage'] != 'generation':
                return super().__call__(request)
            source = self.tools['list_sources']()[0]
            page = self.tools['read_source'](source['source_id'])
            point = self.tools['submit_knowledge_points']([{
                'statement': 'The retention annotation is 30 days.', 'topic': 'retention',
                'evidence': [{'read_id': page['read_id'], 'quote': page['text']}],
            }])['accepted'][0]
            for index in range(20):
                question = f'For retention policy scenario {index + 1}, what duration does the supplied policy require?'
                self.tools['submit_qa']({'knowledge_point_id': point, 'question': question, 'expected_answer': '30 days'})
                self.tools['record_answer_basis'](question, '30 days', 'source_annotation', 'Uses the documented duration.', [point])

    monkeypatch.setattr(akasha, 'agents', ScriptedSDK)
    output = tmp_path / 'twenty.jsonl'
    records = create_test_dataset(from_github='https://github.com/example/corpus', output=output,
                                  github_client=CorpusClient(), count=20, verbose=False)
    assert len(records) == 20
    assert '20' in (tmp_path / 'twenty.generation-report.md').read_text(encoding='utf-8')

def test_completed_discovery_survives_a_late_model_failure_with_loading_evidence(tmp_path):
    class LateFailure(CorpusAgent):
        def __call__(self, request):
            evidence = super().__call__(request)
            if request['stage'] == 'github_discovery':
                failure = RuntimeError('synthetic late provider failure')
                failure.skill_evidence = evidence
                raise failure
            return evidence
    output = tmp_path / 'qa.jsonl'
    assert create_test_dataset(from_github='https://github.com/example/corpus', output=output,
                               github_client=CorpusClient(), skill_agent_factory=LateFailure, verbose=False)
    report = (tmp_path / 'qa.generation-report.md').read_text(encoding='utf-8')
    assert '30 days' in report

def test_generated_github_jsonl_passes_existing_runner_evaluator_and_report(tmp_path):
    from lladar.runner import run_agent
    from lladar.evaluation import evaluate
    from lladar.reporting import create_report

    class WorkflowAgent:
        def __init__(self, *, skills, tools, **options):
            self.name, self.tools = Path(skills[0]).name, tools
        def __call__(self, request):
            if request['stage'] == 'plan':
                self.tools['submit_plan']({'title': 'Compatibility', 'approach': 'Compare saved responses.',
                    'dimensions': [{'name': 'correct', 'description': 'Expected answer agreement', 'kind': 'boolean'}],
                    'limitations': ['Scripted provider; no claim about a live target.']})
            elif request['stage'] == 'report':
                self.tools['submit_report']({'overview': 'Saved evidence compatibility check.',
                    'findings': 'The tables show the supplied responses.', 'limitations': 'Scripted target and evaluator.'})
            else:
                self.tools['submit_judgment']({'values': {'correct': request['actual_response'] == request['expected_answer']},
                                               'reason': 'Compared supplied answers.'})
            return {'loaded_skills': [self.name], 'skill_files': {'SKILL.md': 'scripted'}}

    dataset, responses, evaluation = [tmp_path / name for name in ['qa.jsonl', 'responses.jsonl', 'evaluation.json']]
    create_test_dataset(from_github='https://github.com/example/corpus', output=dataset,
                        github_client=CorpusClient(), skill_agent_factory=CorpusAgent, verbose=False)
    assert run_agent(dataset, responses, answer=lambda question: '30 days', verbose=False) >= 1
    result = evaluate(responses, output=evaluation, skill_agent_factory=WorkflowAgent, verbose=False)
    assert result['items'] and all(item['values']['correct'] for item in result['items'])
    report = create_report(evaluation, tmp_path / 'evaluation-report.md', skill_agent_factory=WorkflowAgent)
    assert 'What is the retention, in days?' in report.read_text(encoding='utf-8')

def test_model_review_never_publishes_half_of_a_controlled_pair(tmp_path):
    from test_controlled_variant_dataset import GenericGraphSkillAgent

    class PlanClient(CorpusClient):
        def read_file(self, owner, repo, sha, path):
            if path.endswith('.csv'):
                return b'id,text,answer\n1,Starter plan includes 10 seats.,10 seats\n2,Growth plan includes 25 seats.,25 seats\n'
            return super().read_file(owner, repo, sha, path)

    class PairAgent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] == 'github_review':
                self.tools['submit_generation_review']([
                    {'record_fingerprint': item['record_fingerprint'],
                     'approved': 'returning customer' not in item['question'], 'reason': 'Scripted source review.'}
                    for item in request['questions']])
                return {'loaded_skills': [self.skill.name]}
            if request['stage'] != 'generation':
                return super().__call__(request)
            plans = []
            def deliver(proposed):
                plans.extend(proposed)
                return self.tools['submit_test_plans'](proposed)
            graph = GenericGraphSkillAgent(skills=[str(self.skill)], tools={**self.tools, 'submit_test_plans': deliver})
            evidence = graph(request)
            points = [point['id'] for point in self.tools['list_knowledge_points']()]
            for plan in plans:
                self.tools['record_answer_basis'](plan['question'], plan['expected_answer'],
                                                  'derived_criterion', 'Source-grounded graph transformation.', points)
            return evidence

    output = tmp_path / 'pairs.jsonl'
    records = create_test_dataset(from_github='https://github.com/example/corpus', output=output,
                                  github_client=PlanClient(), skill_agent_factory=PairAgent,
                                  controlled_variant_topics=('customer_context',), verbose=False)
    assert len(records) == 2
    assert not any('customer' in row['question'] for row in records)
    metadata = json.loads(Path(str(output) + '.generation.json').read_text(encoding='utf-8'))
    assert any(item['reason'] == 'model_review_pair_rejected' for item in metadata['skipped'])

def test_completion_marker_is_published_only_after_all_github_evidence(tmp_path, monkeypatch):
    import os
    replace = os.replace
    observed = []
    def verify_delivery(source, target):
        if str(source).endswith('.tmp') and Path(target).name == 'qa.jsonl.generation.json':
            assert '30 days' in (tmp_path / 'qa.generation-report.md').read_text(encoding='utf-8')
            assert json.loads((tmp_path / 'qa.sources.json').read_text(encoding='utf-8'))['files']
            observed.append(True)
        return replace(source, target)
    monkeypatch.setattr(os, 'replace', verify_delivery)
    create_test_dataset(from_github='https://github.com/example/corpus', output=tmp_path / 'qa.jsonl',
                        github_client=CorpusClient(), skill_agent_factory=CorpusAgent, verbose=False)
    assert observed == [True]

def test_completed_review_survives_late_model_failure_with_complete_judgments(tmp_path):
    class LateReviewFailure(CorpusAgent):
        def __call__(self, request):
            evidence = super().__call__(request)
            if request['stage'] == 'github_review':
                failure = RuntimeError('synthetic late provider failure')
                failure.skill_evidence = evidence
                raise failure
            return evidence
    assert create_test_dataset(from_github='https://github.com/example/corpus', output=tmp_path / 'qa.jsonl',
                               github_client=CorpusClient(), skill_agent_factory=LateReviewFailure, verbose=False)

def test_report_explains_fewer_questions_than_requested_maximum(tmp_path):
    output = tmp_path / 'qa.jsonl'
    create_test_dataset(from_github='https://github.com/example/corpus', output=output, count=20,
                        github_client=CorpusClient(), skill_agent_factory=CorpusAgent, verbose=False)
    metadata = json.loads(Path(str(output) + '.generation.json').read_text(encoding='utf-8'))
    assert metadata['github']['count_result'] == {'requested_maximum': 20, 'output_questions': 1,
                                                 'below_requested_maximum': True,
                                                 'reason': 'fewer_valid_candidates'}
    assert 'fewer_valid_candidates' in (tmp_path / 'qa.generation-report.md').read_text(encoding='utf-8')

def test_downstream_native_evaluator_can_repair_invalid_tool_submissions(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import akasha
    from lladar.runner import run_agent
    from lladar.evaluation import evaluate

    output = tmp_path / 'qa.jsonl'
    create_test_dataset(from_github='https://github.com/example/corpus', output=output,
                        github_client=CorpusClient(), skill_agent_factory=CorpusAgent, verbose=False)
    responses = tmp_path / 'responses.jsonl'
    run_agent(output, responses, answer=lambda question: '30 days', verbose=False)

    class RepairingSDK:
        def __init__(self, *, skills, tools, **options):
            self.tools = {tool.name: tool.func for tool in tools}
            name = Path(skills[0]).name
            self.skill_context = SimpleNamespace(names=[name])
            self.skill_middleware = SimpleNamespace(loaded_skill_names=[name])
        def __call__(self, payload):
            request = json.loads(payload)
            if request['stage'] == 'plan':
                assert 'error' in self.tools['submit_plan']({'bad': 'schema'})
                self.tools['submit_plan']({'title': 'Agreement', 'approach': 'Compare saved answers.',
                    'dimensions': [{'name': 'correct', 'description': 'Answer agreement', 'kind': 'boolean'}],
                    'limitations': ['Scripted provider.']})
            else:
                assert 'error' in self.tools['submit_judgment']({'correct': True})
                self.tools['submit_judgment']({'values': {'correct': True}, 'reason': 'The answers agree.'})

    monkeypatch.setattr(akasha, 'agents', RepairingSDK)
    result = evaluate(responses, output=tmp_path / 'evaluation.json', verbose=False)
    assert result['items'] and all(item['status'] == 'evaluated' for item in result['items']), result['items']
