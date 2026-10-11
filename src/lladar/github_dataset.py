"""Agent-selected remote datasets with inspectable, frozen evidence."""

from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import re
import random
import socket
import tempfile
from collections import Counter
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from .exceptions import DatasetValidationError, KnowledgeLoadError, LladarError, ProviderError
from .question_types import record_fingerprint
from .progress import ProgressReporter
from .skill_generation import generate_with_skill


GITHUB_SKILL = Path(__file__).parent / "skill_assets" / "github-dataset"
FORMATS = {".md", ".txt", ".csv", ".json", ".jsonl"}
LIMITS = {"file_bytes": 2 * 1024 * 1024, "total_bytes": 20 * 1024 * 1024,
          "requests": 100, "timeout_seconds": 20, "redirects": 5, "repository_files": 2000,
          "stream_file_bytes": 16 * 1024 * 1024, "sample_rows": 200000, "sample_size": 100,
          "selected_source_chars": 24000, "host_tool_calls": 160, "model_rounds": 80}


class _PublicAddressAdapter(requests.adapters.HTTPAdapter):
    """Connect to the checked address while retaining HTTPS hostname verification."""

    def __init__(self, parsed, address):
        super().__init__()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.scheme == "https":
            self.connection = HTTPSConnectionPool(address, port, assert_hostname=parsed.hostname,
                                                  server_hostname=parsed.hostname)
        else:
            self.connection = HTTPConnectionPool(address, port)

    def get_connection(self, url, proxies=None):
        return self.connection

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        return self.connection

    def close(self):
        self.connection.close()
        super().close()


class GithubClient:
    """Bounded public HTTP transport and commit-pinned GitHub operations."""

    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.request_count = 0
        self.total_bytes = 0

    def fetch_url(self, url: str, *, destination: Path | None = None) -> dict:
        initial_url = url
        for _ in range(LIMITS["redirects"] + 1):
            parsed = urlparse(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.fragment):
                raise KnowledgeLoadError("download requires a public HTTP(S) URL without credentials")
            try:
                addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            except OSError as error:
                raise KnowledgeLoadError("download host could not be resolved") from error
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise KnowledgeLoadError("download target must not be local, private or reserved")
            # Pin the validated address so a second DNS resolution cannot redirect
            # the connection to a private address. Preserve Host, SNI and TLS checks.
            prefix = f"{parsed.scheme}://{parsed.netloc}/"
            previous_adapter = self.session.adapters.get(prefix)
            if previous_adapter is not None:
                previous_adapter.close()
            self.session.mount(prefix, _PublicAddressAdapter(parsed, addresses[0][4][0]))
            self.request_count += 1
            if self.request_count > LIMITS["requests"]:
                raise KnowledgeLoadError("GitHub exploration request limit exceeded")
            try:
                with self.session.get(url, timeout=LIMITS["timeout_seconds"], stream=True,
                                      allow_redirects=False, headers={"User-Agent": "LLaDAR-dataset", "Host": parsed.netloc}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        target = response.headers.get("Location")
                        if not target:
                            raise KnowledgeLoadError("download redirect has no destination")
                        url = urljoin(url, target)
                        continue
                    if response.status_code != 200:
                        raise KnowledgeLoadError(f"public data download failed (HTTP {response.status_code})")
                    chunks, size = [], 0
                    sink = destination.open("wb") if destination is not None else None
                    limit = LIMITS["stream_file_bytes"] if sink else LIMITS["file_bytes"]
                    try:
                        for chunk in response.iter_content(65536):
                            size += len(chunk)
                            self.total_bytes += len(chunk)
                            if size > limit or self.total_bytes > LIMITS["total_bytes"]:
                                raise KnowledgeLoadError("dataset download size limit exceeded")
                            if sink:
                                sink.write(chunk)
                            else:
                                chunks.append(chunk)
                    finally:
                        if sink:
                            sink.close()
                    content = b"".join(chunks)
                    return {"content": content, "original_url": initial_url, "url": url,
                            "retrieved_at": datetime.now(timezone.utc).isoformat()}
            except requests.RequestException as error:
                raise KnowledgeLoadError(f"public data download failed ({type(error).__name__})") from None
        raise KnowledgeLoadError("dataset download redirect limit exceeded")

    def _json(self, url):
        try:
            return json.loads(self.fetch_url(url)["content"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise KnowledgeLoadError("GitHub response is not valid JSON") from error

    def resolve_repository(self, owner, repo):
        root = f"https://api.github.com/repos/{owner}/{repo}"
        info = self._json(root)
        branch = info["default_branch"]
        commit = self._json(root + "/commits/" + quote(branch, safe=""))
        return {"sha": commit["sha"], "default_branch": branch}

    def list_files(self, owner, repo, sha):
        data = self._json(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{sha}?recursive=1")
        if data.get("truncated"):
            raise KnowledgeLoadError("GitHub file tree is truncated; complete discovery is unavailable")
        files = [{"path": item["path"], "size": item.get("size")} for item in data["tree"]
                 if item["type"] == "blob"]
        if len(files) > LIMITS["repository_files"]:
            raise KnowledgeLoadError("repository file count limit exceeded")
        return files

    def read_file(self, owner, repo, sha, path):
        return self.fetch_url(f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{quote(path, safe='/')}")["content"]


def repository_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
            or parsed.query or parsed.fragment or not match):
        raise ValueError("--from-github requires a repository root URL: https://github.com/OWNER/REPO")
    owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    if not repo or owner in {".", ".."} or repo in {".", ".."}:
        raise ValueError("invalid GitHub repository URL")
    return owner, repo


def artifact_paths(output: Path) -> tuple[Path, Path]:
    return output.with_name(output.stem + ".generation-report.md"), output.with_name(output.stem + ".sources.json")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def normalize_file(path: str, text: str) -> tuple[str, list[dict]]:
    """Expose parsed rows without losing identifiers, values or row positions."""
    suffix = Path(urlparse(path).path if path.startswith(("http://", "https://")) else path).suffix.lower()
    if suffix not in FORMATS:
        raise KnowledgeLoadError(f"unsupported dataset format: {path}")
    if suffix in {".md", ".txt"}:
        return text, []
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline=""))
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise KnowledgeLoadError(f"CSV requires distinct column names: {path}")
        rows = list(reader)
        if any(None in row for row in rows):
            raise KnowledgeLoadError(f"CSV row has extra columns: {path}")
    elif suffix == ".jsonl":
        rows = [json.loads(line) for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    else:
        data = json.loads(text.lstrip("\ufeff"))
        rows = data if isinstance(data, list) else [data]
    chunks, locations, cursor = [], [], 0
    for index, row in enumerate(rows, 1):
        chunk = json.dumps({"data_row": index, "values": row}, ensure_ascii=False) + "\n"
        locations.append({"data_row": index, "start_char": cursor, "end_char": cursor + len(chunk), "values": row})
        cursor += len(chunk)
        chunks.append(chunk)
    if not chunks:
        raise KnowledgeLoadError(f"dataset has no rows: {path}")
    return "".join(chunks), locations


from .work_session import current_session


class Discovery:
    """Repository browsing capabilities; the Agent selects the data paths."""

    def __init__(self, url, client, reporter=None, max_selected_chars=24000):
        self.owner, self.repo = repository_identity(url)
        self.url = f"https://github.com/{self.owner}/{self.repo}"
        self.client = client
        session = current_session()
        saved = (session.data["state"].get("github_discovery") or session.data["state"].get("github_discovery_progress")) if session else None
        if saved:
            for key in ("commit", "files", "fetched", "reads", "selection", "failures"):
                setattr(self, key, saved[key])
            self.reporter = reporter
            self.max_selected_chars = max_selected_chars
            return
        resolved = client.resolve_repository(self.owner, self.repo)
        self.commit = resolved["sha"]
        if not re.fullmatch(r"[a-fA-F0-9]{40}", self.commit):
            raise KnowledgeLoadError("GitHub returned an invalid commit")
        self.files = client.list_files(self.owner, self.repo, self.commit)
        self.fetched = {}
        self.reads = {}
        self.selection = None
        self.failures = []
        self.reporter = reporter
        self.max_selected_chars = max_selected_chars
        self.checkpoint()

    def checkpoint(self):
        session = current_session()
        if session:
            session.put("github_discovery_progress", {key: getattr(self, key) for key in
                ("commit", "files", "fetched", "reads", "selection", "failures")})

    def list_repository_files(self) -> list[dict]:
        """List repository file paths and sizes at the fixed commit."""
        return self.files

    def read_repository_file(self, path: str, start_char: int = 0) -> dict:
        """Read text in 12000-character pages; tables also include a parsed preview."""
        if path not in {item["path"] for item in self.files}:
            raise ValueError("path is not a declared repository file")
        if path not in self.fetched:
            raw = self.client.read_file(self.owner, self.repo, self.commit, path)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise KnowledgeLoadError(f"source is not UTF-8 text: {path}") from error
            if text.startswith("version https://git-lfs.github.com/spec/v1"):
                raise KnowledgeLoadError(f"Git LFS objects are not supported: {path}")
            self.fetched[path] = {"path": path, "text": text, "sha256": hashlib.sha256(raw).hexdigest(),
                                  "url": f"{self.url}/blob/{self.commit}/{quote(path, safe='/')}"}
        return self._page(path, start_char)

    def read_external_dataset(self, url: str, documentation_read_id: str, start_char: int = 0) -> dict:
        """Read a public direct data URL explicitly linked in a repository document read."""
        read = self.reads.get(documentation_read_id)
        links = re.findall(r'https?://[^\s<>"\)\]]+', read["text"]) if read else []
        if read is None or read["path"] not in {item["path"] for item in self.files} or url not in [link.rstrip(".,;") for link in links]:
            raise ValueError("external dataset must be explicitly linked in a repository document read")
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in FORMATS:
            raise KnowledgeLoadError("external link must be a direct supported text dataset file")
        if url not in self.fetched:
            response = self.client.fetch_url(url)
            raw = response["content"]
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise KnowledgeLoadError("external dataset is not UTF-8 text") from error
            if re.search(r"<(?:!doctype html|html|form)\b", text[:1000], re.IGNORECASE):
                raise KnowledgeLoadError("external link returned a web or sign-in page instead of data")
            normalize_file(url, text)
            self.fetched[url] = {"path": url, "text": text, "sha256": hashlib.sha256(raw).hexdigest(),
                                 "url": response["url"], "original_url": response["original_url"],
                                 "retrieved_at": response["retrieved_at"], "origin": "external",
                                 "discovered_from": {"path": read["path"], "quote": url}}
        return self._page(url, start_char)

    def sample_dataset_rows(self, path: str, sample_size: int = 20, seed: int = 0,
                            max_rows: int = 200000) -> dict:
        """Reservoir sample complete CSV/JSONL rows within explicit download and scan budgets."""
        if path not in {item["path"] for item in self.files} or Path(path).suffix.lower() not in {".csv", ".jsonl"}:
            raise ValueError("sampling requires a declared CSV or JSONL file")
        if type(sample_size) is not int or not 1 <= sample_size <= LIMITS["sample_size"]:
            raise ValueError("sample_size must be between 1 and 100")
        if type(seed) is not int or type(max_rows) is not int or not 1 <= max_rows <= LIMITS["sample_rows"]:
            raise ValueError("invalid seed or max_rows scan budget")
        session = current_session()
        with tempfile.TemporaryDirectory(prefix="lladar-sample-") as directory:
            downloaded = Path(directory) / "source"
            if isinstance(self.client, GithubClient):
                url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.commit}/{quote(path, safe='/')}"
                self.client.fetch_url(url, destination=downloaded)
            else:
                raw = self.client.read_file(self.owner, self.repo, self.commit, path)
                if len(raw) > LIMITS["stream_file_bytes"]:
                    raise KnowledgeLoadError("stream source download size limit exceeded")
                downloaded.write_bytes(raw)
            full_hash = hashlib.sha256()
            with downloaded.open("rb") as binary:
                for chunk in iter(lambda: binary.read(65536), b""):
                    full_hash.update(chunk)
            rng, sample, total, exhausted = random.Random(seed), [], 0, True
            with downloaded.open("r", encoding="utf-8-sig", newline="") as handle:
                if Path(path).suffix.lower() == ".csv":
                    reader = csv.DictReader(handle)
                    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                        raise KnowledgeLoadError("CSV requires distinct column names")
                else:
                    reader = (json.loads(line) for line in handle if line.strip())
                for index, row in enumerate(reader, 1):
                    if index > max_rows:
                        exhausted = False
                        break
                    if not isinstance(row, dict) or None in row:
                        raise KnowledgeLoadError("dataset row must be a complete object")
                    total = index
                    location = {"data_row": index, "values": row}
                    if len(sample) < sample_size:
                        sample.append(location)
                    else:
                        choice = rng.randrange(index)
                        if choice < sample_size:
                            sample[choice] = location
                    if self.reporter and (index % 10000 == 0):
                        self.reporter.emit("SOURCE", f"scanned_rows={index} scan_budget={max_rows}")
            if not total:
                raise KnowledgeLoadError("dataset has no rows")
            sample.sort(key=lambda item: item["data_row"])
            text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in sample)
            if len(text) > self.max_selected_chars:
                raise ValueError("sample exceeds selected text budget; use a smaller sample_size")
            snapshot = None
            if session:
                raw_path = session.directory / "sources" / (full_hash.hexdigest() + Path(path).suffix.lower())
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                if not raw_path.exists():
                    import shutil
                    shutil.copyfile(downloaded, raw_path)
                snapshot = str(raw_path.relative_to(session.directory))
        scope = "full_population_sample" if exhausted else "scanned_prefix_sample"
        self.fetched[path] = {"path": path, "text": text, "sha256": full_hash.hexdigest(),
            "sample_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "url": f"{self.url}/blob/{self.commit}/{quote(path, safe='/')}",
            "sample_rows": sample, "scanned_rows": total, "total_rows": total if exhausted else None,
            "sampling": {"seed": seed, "sample_size": len(sample), "scope": scope,
                         "stop_reason": "end_of_source" if exhausted else "scan_budget"},
            "raw_snapshot": snapshot}
        return {"path": path, "rows": sample, "total_rows": total if exhausted else None,
                "scanned_rows": total, "scope": scope, "sha256": full_hash.hexdigest()}

    def normalized(self, path):
        saved = self.fetched[path]
        if "sample_rows" not in saved:
            return normalize_file(path, saved["text"])
        rows, chunks, cursor = [], [], 0
        for item in saved["sample_rows"]:
            text = json.dumps(item, ensure_ascii=False) + "\n"
            rows.append({**item, "start_char": cursor, "end_char": cursor + len(text)})
            chunks.append(text)
            cursor += len(text)
        return "".join(chunks), rows

    def _page(self, path, start_char):
        text = self.fetched[path]["text"]
        if type(start_char) is not int or not 0 <= start_char <= len(text):
            raise ValueError("invalid start_char")
        end = min(len(text), start_char + 12000)
        read = {"read_id": f"discovery_{len(self.reads) + 1:04d}", "path": path,
                "start_char": start_char, "end_char": end, "text": text[start_char:end],
                "next_start": end if end < len(text) else None}
        self.reads[read["read_id"]] = read
        result = dict(read)
        if Path(urlparse(path).path).suffix.lower() in {".csv", ".json", ".jsonl"}:
            _, rows = self.normalized(path)
            result["preview"] = rows[:5]
            result["total_rows"] = len(rows)
        return result

    def select_dataset(self, data_paths: list[str], purpose: str, task: str,
                       rationale: str, evidence: list[dict], row_selections: dict | None = None) -> dict:
        """Select actual datasets with purpose, field interpretation, task and quoted evidence."""
        if not isinstance(data_paths, list) or not data_paths or len(set(data_paths)) != len(data_paths):
            raise ValueError("select at least one distinct dataset path")
        row_selections = row_selections or {}
        if not isinstance(row_selections, dict) or set(row_selections) - set(data_paths):
            raise ValueError("row selections must refer to selected dataset paths")
        selected_chars = 0
        for path in data_paths:
            if path not in self.fetched:
                raise ValueError("read each selected dataset before selecting it")
            normalized, rows = self.normalized(path)
            if path in row_selections:
                chosen = row_selections[path]
                if (not isinstance(chosen, list) or not chosen or any(type(row) is not int or row not in {item["data_row"] for item in rows} for row in chosen)
                        or len(set(chosen)) != len(chosen)):
                    raise ValueError("row selections require distinct valid 1-based data row numbers")
                selected_chars += sum(row["end_char"] - row["start_char"] for row in rows if row["data_row"] in chosen)
            else:
                selected_chars += len(normalized)
        if selected_chars > self.max_selected_chars:
            raise ValueError(f"selected source text exceeds {self.max_selected_chars} characters; inspect rows and provide a smaller explicit row_selections sample")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("selection requires quoted evidence")
        accepted = []
        for item in evidence:
            read = self.reads.get(item.get("read_id"))
            snippet = _text(item.get("quote"), "quote")
            if read is None or snippet not in read["text"]:
                raise ValueError("selection evidence must match a repository read")
            start = read["start_char"] + read["text"].index(snippet)
            accepted.append({"path": read["path"], "quote": snippet, "start_char": start,
                             "end_char": start + len(snippet)})
        self.selection = {"data_paths": data_paths, "purpose": _text(purpose, "purpose"),
                          "task": _text(task, "task"), "rationale": _text(rationale, "rationale"),
                          "evidence": accepted, "row_selections": row_selections}
        return {"selected": data_paths}

    def read_dataset_rows(self, path: str, start_row: int = 1, limit: int = 20) -> dict:
        """Inspect parsed dataset rows by 1-based data row, retaining original identifiers."""
        if path not in self.fetched:
            raise ValueError("read the dataset file before inspecting rows")
        _, rows = self.normalized(path)
        if not rows or type(start_row) is not int or not 1 <= start_row <= len(rows) or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("row preview requires a valid start_row and limit between 1 and 20")
        return {"path": path, "total_rows": len(rows), "rows": rows[start_row - 1:start_row - 1 + limit]}

    def tools(self):
        def observed(function):
            @wraps(function)
            def invoke(*args, **kwargs):
                try:
                    result = function(*args, **kwargs)
                except (KnowledgeLoadError, ValueError, json.JSONDecodeError) as error:
                    self.failures.append({"tool": function.__name__, "source": args[0] if args else kwargs.get("path", kwargs.get("url")),
                                          "reason": str(error)})
                    self.checkpoint()
                    if self.reporter:
                        self.reporter.emit("GITHUB", f"{function.__name__}: {error}")
                    raise ValueError(str(error)) from error
                self.checkpoint()
                if self.reporter:
                    self.reporter.emit("GITHUB", f"{function.__name__} completed")
                return result
            return invoke
        return {name: observed(getattr(self, name)) for name in
                ("list_repository_files", "read_repository_file", "read_external_dataset", "read_dataset_rows", "sample_dataset_rows", "select_dataset")}


def _literal(value: str) -> str:
    """Indented code keeps untrusted Markdown/HTML readable without interpreting it."""
    return "\n".join("    " + line for line in str(value).splitlines()) + "\n"


def render_report(result, github, snapshots, sources_path):
    points = {point["id"]: point for point in result.provenance["knowledge_points"]}
    files = {item["source_id"]: item for item in snapshots["files"] if "source_id" in item}
    report = ["# 生成查驗報告", "", f"來源：{github['url']}", f"固定 commit：`{github['commit']}`",
              f"生成時間：{github['generated_at']}", "", "## 生成摘要", "",
              _literal(json.dumps(result.provenance["settings"], ensure_ascii=False, indent=2)),
              f"Skill：{result.provenance['skill']['name']}", "",
              _literal(json.dumps(github["selection"], ensure_ascii=False, indent=2)),
              f"正式輸出：{len(result.records)} 題；答案依據分布：" +
              str(dict(Counter(line["answer_basis"] for line in result.provenance["dataset"]["lines"]))),
              "", "統計範圍：本次實際探索與生成，不代表完整資料集覆蓋。", "",
              "各項計數單位與範圍：", _literal(json.dumps(github["counts"], ensure_ascii=False, indent=2)),
              "題數上限與不足原因：", _literal(json.dumps(github["count_result"], ensure_ascii=False, indent=2)),
              "## 驗證與限制", "", "程式檢查：來源引文、位置、問答格式與答案依據引用通過。",
              "模型檢查：逐題檢查答案依據、轉換與任務一致性；不等同人工 gold answer。",
              "人工檢閱：未執行。自然語言判準仍需檢閱；衍生 QA 不代表重現原 benchmark。",
              "生成判準不是來源原始答案；引文證明樣本來源，不自動證明衍生判準。", "",
              f"[保存的來源與原文]({quote(sources_path.name)})", "", "## 資料選取證據", ""]
    by_path = {item["path"]: item for item in snapshots["files"]}
    for evidence in github["selection"]["evidence"]:
        item = by_path[evidence["path"]]
        report += [f"[來源文件]({item['url']})", _literal(evidence["quote"])]
    report += ["## 逐題查驗", ""]
    for row, line in zip(result.records, result.provenance["dataset"]["lines"]):
        report += [f"### 題目 {line['line']}", "", f"題目引用：`{line['record_fingerprint']}`；JSONL 列號：{line['line']}",
                   "", "題目：", _literal(row["question"]), "預期答案／判準：", _literal(row["expected_answer"]),
                   f"答案依據：`{line['answer_basis']}`", "", "轉換理由：", _literal(line["basis_rationale"])]
        if line.get("source_answer_mapping"):
            report += ["?????????", _literal(json.dumps(line["source_answer_mapping"], ensure_ascii=False, indent=2))]
        for point_id in line["basis_point_ids"]:
            for evidence in points[point_id]["evidence"]:
                item = files[evidence["source_id"]]
                report += [f"[固定版本來源]({item['url']})", _literal(item["path"]),
                           f"內容 SHA256：`{item['sha256']}`", "證據摘錄：", _literal(evidence["quote"])]
                for location in item["rows"]:
                    if location["start_char"] < evidence["end_char"] and location["end_char"] > evidence["start_char"]:
                        report += [f"資料列：{location['data_row']}（不含表頭）", _literal(json.dumps(location["values"], ensure_ascii=False))]
        report += ["程式驗證：通過。模型檢查：通過。", _literal(line["model_review"]["reason"]),
                   "人工檢閱：未執行。", ""]
    report += ["## 跳過與未處理範圍", "", _literal(json.dumps(result.provenance["skipped"], ensure_ascii=False, indent=2)),
               "下載範圍：僅本次讀取並保存的檔案；未讀取的資料不宣稱已覆蓋。", ""]
    for item in snapshots["files"]:
        if "sampling" in item:
            report += ["????????", _literal(json.dumps({"path": item["path"],
                "sampling": item["sampling"], "scanned_rows": item["scanned_rows"],
                "total_rows": item["total_rows"], "source_sha256": item["sha256"],
                "sample_sha256": item["sample_sha256"], "raw_snapshot": item["raw_snapshot"]}, ensure_ascii=False, indent=2))]
    return "\n".join(report) + "\n"


def review_questions(result, snapshots, discovery, agent_factory, skill, agent_options, profile):
    """A separate model pass reviews frozen QA against saved evidence."""
    points = {point["id"]: point for point in result.provenance["knowledge_points"]}
    items = []
    for row, line in zip(result.records, result.provenance["dataset"]["lines"]):
        evidence = [entry for point in line["basis_point_ids"] for entry in points[point]["evidence"]]
        items.append({"record_fingerprint": line["record_fingerprint"], **row,
                      "answer_basis": line["answer_basis"], "transformation": line["basis_rationale"],
                      "evidence": evidence})
    session = current_session()
    saved = session.data["state"].get("github_reviews", {}) if session else {}
    reviews = dict(saved.get("judgments", {}))
    executions = list(saved.get("executions", []))

    def checkpoint_reviews():
        if session:
            session.put("github_reviews", {"judgments": reviews, "executions": executions})
    # Conservative character budget; each batch includes documentation and the Skill.
    budget = max(1000, profile.max_input_tokens // 2)
    context = {"stage": "github_review", "selection": discovery.selection,
               "source_locations": [{key: item[key] for key in ("source_id", "path", "url")}
                                    for item in snapshots["files"] if "source_id" in item]}
    context_size = len(json.dumps(context, ensure_ascii=False))
    batches, batch, size = [], [], context_size
    for item in items:
        item_size = len(json.dumps(item, ensure_ascii=False))
        if context_size + item_size > budget:
            raise DatasetValidationError("question evidence exceeds model review input budget")
        if batch and size + item_size > budget:
            batches.append(batch)
            batch, size = [], context_size
        batch.append(item)
        size += item_size
    if batch:
        batches.append(batch)
    for batch in batches:
        assigned = {item["record_fingerprint"] for item in batch}
        if assigned <= reviews.keys():
            continue

        def submit_generation_review(judgments: list[dict]) -> dict:
            """Review every assigned QA: record_fingerprint, approved boolean and evidence-bounded reason."""
            if not isinstance(judgments, list):
                raise ValueError("reviews must be a list")
            proposed = {}
            for judgment in judgments:
                if not isinstance(judgment, dict) or set(judgment) != {"record_fingerprint", "approved", "reason"}:
                    raise ValueError("review requires record_fingerprint, approved and reason")
                key = judgment["record_fingerprint"]
                if key not in assigned or key in proposed or type(judgment["approved"]) is not bool:
                    raise ValueError("review requires distinct assigned questions and boolean approval")
                proposed[key] = {"approved": judgment["approved"], "reason": _text(judgment["reason"], "reason")}
            if set(proposed) != assigned:
                raise ValueError("review must cover every assigned question")
            reviews.update(proposed)
            checkpoint_reviews()
            return {"reviewed": len(proposed)}

        errors = []
        for attempt in range(1, 4):
            for key in assigned:
                reviews.pop(key, None)
            agent = agent_factory(skills=[str(skill)], tools={"submit_generation_review": submit_generation_review}, **agent_options)
            try:
                execution = agent({**context, "questions": batch, "attempt": attempt,
                                   "required_skill": skill.name,
                                   "instructions": "First call load_skill for the selected skill. Then follow its github_review method and submit all assigned reviews. The target Agent sees only question text, not hidden source tables: reject lookup questions requiring unseen IDs, labels or rows unless the task provides the necessary information.",
                                   "validation_errors": errors})
            except (RuntimeError, ProviderError) as error:
                execution = {**getattr(error, "skill_evidence", {}), "error_type": type(error).__name__}
            executions.append({"attempt": attempt, **execution})
            checkpoint_reviews()
            if skill.name in execution.get("loaded_skills", []) and assigned <= reviews.keys():
                break
            errors = ["model review must load the selected skill with load_skill and cover every assigned question"]
        else:
            raise DatasetValidationError(errors[0])
    rejected_pairs = {line["pair_id"] for line in result.provenance["dataset"]["lines"]
                      if line.get("pair_id") and not reviews[line["record_fingerprint"]]["approved"]}
    kept_rows, kept_lines = [], []
    for row, line in zip(result.records, result.provenance["dataset"]["lines"]):
        review = reviews[line["record_fingerprint"]]
        if not review["approved"] or line.get("pair_id") in rejected_pairs:
            result.provenance["skipped"].append({"record_fingerprint": line["record_fingerprint"],
                                                "reason": "model_review_rejected" if not review["approved"] else "model_review_pair_rejected",
                                                "detail": review["reason"], "pair_id": line.get("pair_id")})
            continue
        line.update(line=len(kept_rows) + 1, model_review=review)
        kept_rows.append(row)
        kept_lines.append(line)
    result.provenance["github_review"] = {"executions": executions, "reviewed_questions": len(items),
                                          "judgments": reviews, "human_review": "not_performed"}
    if not kept_rows:
        raise DatasetValidationError("model review rejected all generated questions")
    result.records = kept_rows
    result.provenance["dataset"]["lines"] = kept_lines
    result.provenance["stats"]["model_review_rejected"] = len(items) - len(kept_rows)
    result.provenance["stats"]["output_records"] = len(kept_rows)


def generate_from_github(url, *, output, client, skill, agent_factory, options, force=False):
    diagnostics = {"repository": url, "stage": "github_discovery"}
    try:
        return _generate_from_github(url, output=output, client=client, skill=skill,
                                     agent_factory=agent_factory, options=options, force=force,
                                     diagnostics=diagnostics)
    except (LladarError, ValueError, OSError) as error:
        diagnostics.update(status="failed", error_type=type(error).__name__)
        # Store frozen evidence, not raw provider exception strings.
        output.parent.mkdir(parents=True, exist_ok=True)
        stem = output.stem + ".generation-failure"
        number = 0
        while True:
            path = output.with_name(stem + (f"-{number}" if number else "") + ".json")
            try:
                with path.open("x", encoding="utf-8", newline="\n") as handle:
                    json.dump(diagnostics, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                break
            except FileExistsError:
                number += 1
            except OSError:
                # Preserve the original failure when diagnostics cannot be written.
                break
        raise


def _generate_from_github(url, *, output, client, skill, agent_factory, options, force, diagnostics):
    selected_limit = min(LIMITS["selected_source_chars"], max(1000, options["profile"].max_input_tokens // 2))
    discovery = Discovery(url, client, ProgressReporter(options["verbose"]), max_selected_chars=selected_limit)
    if agent_factory is None:
        from .skill_agent import AkashaSkillAgent
        agent_factory = AkashaSkillAgent
    selected_skill = Path(skill) if skill is not None else GITHUB_SKILL
    from akasha.agent.skills.loader import load_skill_directory
    load_skill_directory(selected_skill)
    agent_options = {name: options[name] for name in ("model", "env_file", "temperature", "verbose")}
    runtime_options = {"tool_call_limit": LIMITS["host_tool_calls"], "round_limit": LIMITS["model_rounds"]}
    from .skill_agent import SYSTEM_PROMPT
    runtime_options["system_prompt"] = SYSTEM_PROMPT + (
        f"\nThe selected skill name is {selected_skill.name}. Call load_skill with "
        f"reference='{selected_skill.name}', using this name exactly. Use a skill name, "
        "not a Windows path. Do not request skill.json or other undocumented resources. "
        "Repository paths are remote identifiers, not local files. Use repository reading "
        "tools instead of opening paths with Python. The selection tool enforces the text budget. "
        "After successful select_dataset, finish discovery; no further tools are needed.\n"
    )
    agent_options.update(**runtime_options, max_input_tokens=options["profile"].max_input_tokens,
                         max_output_tokens=options["profile"].max_output_tokens)
    session = current_session()
    saved_discovery = session.data["state"].get("github_discovery") if session else None
    if saved_discovery:
        evidence = saved_discovery["evidence"]
    else:
        discovery_agent = agent_factory(skills=[str(selected_skill)], tools=discovery.tools(), **agent_options)
        try:
            evidence = discovery_agent({"stage": "github_discovery", "repository": discovery.url,
                                        "commit": discovery.commit, "question_type": options["question_type"],
                                        "count": options["count"], "resource_limits": {**LIMITS, "selected_source_chars": selected_limit}})
        except (ProviderError, RuntimeError) as error:
            evidence = getattr(error, "skill_evidence", {})
            diagnostics.update(commit=discovery.commit, selection=discovery.selection,
                               snapshots={"files": list(discovery.fetched.values())})
            if discovery.selection is None or selected_skill.name not in evidence.get("loaded_skills", []):
                raise
            evidence = {**evidence, "completion": "recovered_valid_selection", "error_type": type(error).__name__}
        if selected_skill.name not in evidence.get("loaded_skills", []) or discovery.selection is None:
            raise DatasetValidationError("GitHub discovery must load the skill and select an actual dataset")
        if session:
            session.put("github_discovery", {key: getattr(discovery, key) for key in
                ("commit", "files", "fetched", "reads", "selection", "failures")} | {"evidence": evidence})
    diagnostics.update(commit=discovery.commit, selection=discovery.selection, discovery_execution=evidence)
    bases = dict(session.data["state"].get("github_bases", {})) if session else {}

    def basis_tools(workspace):
        def record_answer_basis(question: str, expected_answer: str, answer_basis: str,
                                rationale: str, evidence_point_ids: list[str]) -> dict:
            """Record each QA's answer origin, transformation rationale and accepted point IDs."""
            if answer_basis not in {"source_answer", "source_annotation", "derived_criterion"}:
                raise ValueError("unknown answer basis")
            if (not isinstance(evidence_point_ids, list) or not evidence_point_ids
                    or any(point not in workspace.points for point in evidence_point_ids)):
                raise ValueError("answer basis requires accepted knowledge point evidence")
            original_mapping = workspace.original_answer_mapping(expected_answer,
                [item for point_id in evidence_point_ids for item in workspace.points[point_id]["evidence"]]) if answer_basis == "source_answer" else []
            if answer_basis == "source_answer" and not original_mapping:
                raise ValueError("source_answer must preserve an original saved answer field; use source_annotation or derived_criterion for transformed criteria")
            key = record_fingerprint({"question": question, "expected_answer": expected_answer})
            bases[key] = {"answer_basis": answer_basis, "basis_rationale": _text(rationale, "rationale"),
                          "basis_point_ids": list(evidence_point_ids), "source_answer_mapping": original_mapping}
            if session:
                session.put("github_bases", dict(bases))
            return {"record_fingerprint": key, "recorded": True}
        return {"record_answer_basis": record_answer_basis}

    def validate_bases(workspace):
        for qa_id, basis in workspace.answer_bases.items():
            if all(point in workspace.points for point in basis["basis_point_ids"]):
                bases[record_fingerprint(workspace.qa[qa_id])] = dict(basis)
        missing = []
        for row in list(workspace.qa.values()) + workspace.test_plans:
            fingerprint = record_fingerprint(row)
            basis = bases.get(fingerprint)
            if basis is None or any(point not in workspace.points for point in basis["basis_point_ids"]):
                missing.append(row["question"])
        if missing:
            raise ValueError("missing valid answer basis evidence; call record_answer_basis for: " + json.dumps(missing, ensure_ascii=False))

    with tempfile.TemporaryDirectory(prefix="lladar-github-") as directory:
        paths = []
        snapshots = {"files": [dict(item) for item in discovery.fetched.values()]}
        diagnostics.update(stage="generation", snapshots=snapshots, answer_bases=bases)
        for index, path in enumerate(sorted(discovery.selection["data_paths"]), 1):
            normalized, rows = discovery.normalized(path)
            total_rows = discovery.fetched[path].get("total_rows", len(rows))
            chosen = discovery.selection["row_selections"].get(path)
            if chosen:
                selected_rows, chunks, cursor = [], [], 0
                for location in rows:
                    if location["data_row"] not in chosen:
                        continue
                    chunk = normalized[location["start_char"]:location["end_char"]]
                    selected_rows.append({**location, "start_char": cursor, "end_char": cursor + len(chunk)})
                    chunks.append(chunk)
                    cursor += len(chunk)
                normalized, rows = "".join(chunks), selected_rows
            local = Path(directory) / f"source_{index:03d}.md"
            local.write_text(normalized, encoding="utf-8")
            paths.append(local)
            saved = next(item for item in snapshots["files"] if item["path"] == path)
            saved.update(source_id=f"source_{index:03d}", normalized_text=normalized, rows=rows,
                         total_data_rows=total_rows, selected_data_rows=len(rows))
        result = generate_with_skill(paths, skill=selected_skill, agent_factory=agent_factory,
                                     extra_tools_factory=basis_tools, runtime_options=runtime_options,
                                     allow_candidate_files=skill is not None,
                                     candidate_validator=validate_bases,
                                     request_context={"github_selection": discovery.selection,
                                                      "answer_basis_required": True,
                                                      "answer_basis_contract": "Prefer atomic submit_question for free QA. For legacy submit_qa or graph QA call record_answer_basis(question, expected_answer, answer_basis, rationale, evidence_point_ids). Use accepted point IDs and source_answer|source_annotation|derived_criterion."}, **options)
        point_ids = {point["id"] for point in result.provenance["knowledge_points"]}
        for line in result.provenance["dataset"]["lines"]:
            basis = bases.get(line["record_fingerprint"])
            if basis is None or any(point not in point_ids for point in basis["basis_point_ids"]):
                raise DatasetValidationError("every GitHub question requires valid answer basis evidence")
            line.update(basis)
        github = {"url": discovery.url, "commit": discovery.commit,
                  "generated_at": datetime.now(timezone.utc).isoformat(), "selection": discovery.selection,
                  "discovery_execution": evidence, "resource_limits": {**LIMITS, "selected_source_chars": selected_limit}}
        result.provenance["github"] = github
        result.provenance["skipped"].extend(discovery.failures)
        if discovery.failures or any(item.get("sampling", {}).get("scope") == "scanned_prefix_sample"
                                     for item in snapshots["files"]):
            result.provenance["status"] = "partial"
        diagnostics.update(stage="github_review", generation=result.provenance)
        review_questions(result, snapshots, discovery, agent_factory, selected_skill, agent_options, options["profile"])
        github["counts"] = {
            "downloaded_files": len(discovery.fetched),
            "selected_files": len(discovery.selection["data_paths"]),
            "selected_data_rows": sum(item.get("selected_data_rows", 0) for item in snapshots["files"]),
            "total_rows_in_selected_tables": None if any(item.get("total_data_rows", 0) is None for item in snapshots["files"])
                else sum(item.get("total_data_rows", 0) for item in snapshots["files"]),
            "qa_submissions": result.provenance["stats"]["qa_candidates"],
            "structurally_valid_candidates": result.provenance["stats"]["valid_candidates_before_selection"],
            "deduplicated_questions": result.provenance["stats"]["deduplicated_qa"],
            "count_limit_excluded": result.provenance["stats"]["count_limit_excluded"],
            "model_review_rejected": result.provenance["stats"]["model_review_rejected"],
            "output_questions": len(result.records),
        }
        below_maximum = bool(options["count"] and len(result.records) < options["count"])
        github["count_result"] = {
            "requested_maximum": options["count"], "output_questions": len(result.records),
            "below_requested_maximum": below_maximum,
            "reason": ("model_review_filtered_candidates" if result.provenance["stats"]["model_review_rejected"]
                       else "fewer_valid_candidates") if below_maximum else "within_requested_limit",
        }
        result.provenance["stats"]["output_points"] = sum(len(line["knowledge_point_ids"]) for line in result.provenance["dataset"]["lines"])
        report_path, sources_path = artifact_paths(output)
        session = current_session()
        if session:
            for item in snapshots["files"]:
                raw_snapshot = item.get("raw_snapshot")
                if raw_snapshot:
                    saved = session.directory / raw_snapshot
                    if hashlib.sha256(saved.read_bytes()).hexdigest() != item["sha256"]:
                        raise DatasetValidationError("raw source snapshot integrity check failed")
                    target = output.with_name(output.stem + ".source-" + item["sha256"][:16] + Path(item["path"]).suffix)
                    result.artifacts[target] = saved.read_bytes().decode("utf-8")
                    item["raw_snapshot"] = target.name
        result.artifacts[sources_path] = json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n"
        result.artifacts[report_path] = render_report(result, github, snapshots, sources_path)
        # Published source references point to saved evidence, not deleted temporary files.
        for source in result.provenance["sources"]:
            source["path"] = sources_path.name
        diagnostics["stage"] = "publication"
        result.publish(output, force=force)
        return result.records
