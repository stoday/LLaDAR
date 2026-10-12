"""Durable stage sessions; no provider secrets or model reasoning are persisted."""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

_ACTIVE = ContextVar("lladar_work_session", default=None)
CONTRACT_VERSION = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def current_session():
    return _ACTIVE.get()


def file_sha(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def file_identity(value):
    path = Path(value)
    if path.is_file():
        return {"name": path.name, "sha256": file_sha(path)}
    if path.is_dir():
        return {str(p.relative_to(path)): file_sha(p)
                for p in sorted(path.rglob("*")) if p.is_file() and not any(
                    part in {".git", ".venv", "__pycache__", "node_modules", ".env"}
                    for part in p.relative_to(path).parts)}
    return str(value)


class WorkSession:
    def __init__(self, directory, stage, configuration, *, resume=False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "session.json"
        self.lock = self.directory / "writer.lock"
        self.lock_handle = self.lock.open("a+b")
        self.lock_handle.seek(0, os.SEEK_END)
        if self.lock_handle.tell() == 0:
            self.lock_handle.write(b"0")
            self.lock_handle.flush()
        self.lock_handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.lock_handle.close()
            raise ValueError(f"session already has a writer: {self.directory}") from error
        self.persistence_failed = False
        self.reporters = []
        try:
            if resume:
                envelope = json.loads(self.path.read_text(encoding="utf-8"))
                self.data = envelope["data"]
                if digest(self.data) != envelope["sha256"]:
                    raise ValueError("checkpoint integrity check failed")
                differences = sorted(key for key in set(configuration) | set(self.data["configuration"])
                    if configuration.get(key) != self.data["configuration"].get(key))
                if self.data["stage"] != stage or self.data["contract_version"] != CONTRACT_VERSION:
                    differences.append("contract_version/stage")
                if differences:
                    raise ValueError("resume configuration differs: " + ", ".join(differences))
            else:
                self.data = {"session_id": uuid4().hex, "stage": stage,
                    "contract_version": CONTRACT_VERSION, "configuration": configuration,
                    "status": "working", "state": {}, "artifacts": {}}
                self.save()
        except BaseException:
            self.close()
            raise

    def save(self):
        atomic_json(self.path, {"data": self.data, "sha256": digest(self.data)})

    def put(self, key, value):
        if self.persistence_failed:
            raise OSError("checkpoint persistence failed; stop this session")
        old = self.data["state"].get(key)
        self.data["state"][key] = value
        try:
            self.save()
        except BaseException:
            self.persistence_failed = True
            if old is None:
                self.data["state"].pop(key, None)
            else:
                self.data["state"][key] = old
            raise

    def get_item(self, namespace, item_id):
        reference = self.data["state"].get(namespace, {}).get(item_id)
        if reference is None:
            return None
        path = self.directory / reference["path"]
        if not path.resolve().is_relative_to(self.directory.resolve()) or file_sha(path) != reference["sha256"]:
            raise ValueError("item checkpoint integrity check failed: " + item_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def put_item(self, namespace, item_id, value):
        if self.persistence_failed:
            raise OSError("checkpoint persistence failed; stop this session")
        path = self.directory / namespace / (digest(item_id) + "-" + uuid4().hex + ".json")
        atomic_json(path, value)
        reference = {"path": str(path.relative_to(self.directory)), "sha256": file_sha(path)}
        updated = {**self.data["state"].get(namespace, {}), item_id: reference}
        self.put(namespace, updated)

    def close(self):
        for reporter in self.reporters:
            reporter.close()
        self.lock_handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_UN)
        self.lock_handle.close()


def stage_session(stage):
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def invoke(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            resume = values.get("resume")
            if resume is not None and values.get("force"):
                raise ValueError("resume and force cannot be combined")
            output = values.get("output")
            if output is None:
                if resume is not None:
                    raise ValueError("resume requires an output")
                return function(*args, **kwargs)
            output = Path(output).resolve()
            configuration = {"output": str(output), "stage_contract_version": 2 if stage == "run-agent" else 1}
            ignored = {"output", "resume", "force", "verbose", "env_file", "github_client",
                "skill_agent_factory", "review_agent_factory", "strategy_agent_factory", "browser_target_factory",
                "extraction_provider_factory", "interface_selector", "controlled_variant_selector",
                "interactive", "confirm_browser_run", "runs_root"}
            for key, value in values.items():
                if key in ignored:
                    continue
                if key in {"dataset", "responses", "eval_output", "knowledge", "skill", "adapt", "project"} and value is not None:
                    if key == "knowledge":
                        from .loaders import load_knowledge
                        value = [{"name": str(p.resolve()), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
                                 for p, text in load_knowledge(value)]
                    elif isinstance(value, (list, tuple)):
                        value = [file_identity(p) for p in value]
                    else:
                        value = file_identity(value)
                elif callable(value):
                    value = "injected_callable"
                elif isinstance(value, Path):
                    value = str(value)
                elif isinstance(value, tuple):
                    value = list(value)
                if key in {"page_url", "service_url"} and value is not None:
                    from urllib.parse import urlparse
                    parsed = urlparse(value)
                    value = {"origin": f"{parsed.scheme}://{parsed.hostname}", "sha256": digest(value)}
                configuration[key] = value
                if key in {"dataset", "responses"}:
                    configuration[key + "_sidecars"] = {suffix: file_identity(str(values[key]) + suffix)
                        for suffix in (".generation.json", ".graph.json", ".run.json", ".trials.jsonl")
                        if Path(str(values[key]) + suffix).is_file()}
            if values.get("skill") is None:
                # Built-in method changes invalidate unfinished sessions too.
                builtin = {"generation": "github-dataset" if values.get("from_github") else "knowledge-point-qa",
                           "eval": "eval-criteria" if values.get("criteria") is not None else "eval-answer-verdict",
                           "run-agent": "run-agent-stability", "report": "report-evidence-summary"}[stage]
                configuration["builtin_skill"] = file_identity(Path(__file__).parent / "skill_assets" / builtin)
            directory = Path(resume) if resume is not None else Path(str(output) + ".work")
            if resume is None and directory.exists():
                directory = Path(str(directory) + "-" + uuid4().hex[:8])
            session = WorkSession(directory, stage, configuration, resume=resume is not None)
            token = _ACTIVE.set(session)
            try:
                if resume is None:
                    session.data["replace_authorized"] = bool(values.get("force"))
                    original = {str(path.resolve()): file_identity(path) for path in output.parent.glob(output.name + "*")
                                if path.is_file() and not path.name.endswith(".lock")}
                    if stage == "generation":
                        for path in (output.with_name(output.stem + ".generation-report.md"),
                                     output.with_name(output.stem + ".sources.json")):
                            if path.is_file():
                                original[str(path.resolve())] = file_identity(path)
                    session.data["original_artifacts"] = original
                    session.save()
                publication = session.data["state"].get("publication")
                if session.data["status"] != "complete" and publication and all(
                    file_identity(path) == expected for path, expected in publication["artifacts"].items()):
                    session.data.update(status="complete", result=publication["result"], artifacts=publication["artifacts"])
                    session.save()
                if session.data["status"] != "complete":
                    recover_publication(session)
                if session.data["status"] == "complete":
                    for path, expected in session.data["artifacts"].items():
                        if file_identity(path) != expected:
                            raise ValueError("completed session artifact changed: " + path)
                    result = session.data["result"]
                    return Path(result) if stage == "report" else result
                if resume is not None and session.data.get("replace_authorized"):
                    expected_new = (publication or {}).get("artifacts", {})
                    for path, original in session.data.get("original_artifacts", {}).items():
                        current = file_identity(path)
                        if current not in (original, expected_new.get(path)):
                            raise ValueError("original output changed during interrupted work: " + path)
                    bound.arguments["force"] = True
                result = function(*bound.args, **bound.kwargs)
                publication = session.data["state"].get("publication")
                artifacts = publication["artifacts"] if publication else {str(output): file_identity(output)}
                session.data.update(status="complete", result=str(result) if isinstance(result, Path) else result,
                                    artifacts=artifacts)
                session.save()
                return result
            except BaseException as error:
                error.work_session_hint = {"path": str(session.directory.resolve()),
                    "output": str(output), "persistence_failed": session.persistence_failed}
                raise
            finally:
                _ACTIVE.reset(token)
                session.close()
        return invoke
    return decorate


def atomic_text(path, text, *, force=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    reserved = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if not force:
            with path.open("x", encoding="utf-8"):
                pass
            reserved = True
        os.replace(temporary, path)
        reserved = False
    finally:
        Path(temporary).unlink(missing_ok=True)
        if reserved:
            path.unlink(missing_ok=True)


def publication_metadata():
    from .language import current_language
    session = current_session()
    return {"session_id": session.data["session_id"], "path": str(session.directory.resolve()),
            "contract_version": CONTRACT_VERSION, **current_language().metadata()} if session else None


def prepare_publication(payloads, result):
    session = current_session()
    if session:
        if session.persistence_failed:
            raise OSError("checkpoint persistence failed; publication refused")
        hashes = {str(Path(path).resolve()): {"name": Path(path).name,
                    "sha256": file_sha(text) if isinstance(text, Path) else
                    hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()}
                  for path, text in payloads.items()}
        session.put("publication", {"artifacts": hashes,
                    "result": str(result) if isinstance(result, Path) else result})


def publish_bundle(payloads, *, force=False, result=None, retired=()):
    """Publish the completion marker last; rollback on write/replace failure."""
    payloads = {Path(path): value for path, value in payloads.items()}
    paths = list(payloads)
    marker = paths[-1]
    marker.parent.mkdir(parents=True, exist_ok=True)
    lock = marker.with_name('.' + marker.name + '.publish.lock')
    with lock.open('x'):
        pass
    staged, backups, reservations, published = {}, {}, [], []
    retired = [Path(path) for path in retired]
    session = current_session()
    transaction = None
    try:
        for path in paths:
            if path.is_symlink() or path.exists() and not path.is_file():
                raise ValueError(f"publication target must be a regular file: {path}")
            if path.exists() and not force:
                raise FileExistsError(f"output already exists: {path}")
            temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
            staged[path] = temporary
            value = payloads[path]
            with temporary.open('xb') as handle:
                if isinstance(value, Path):
                    import shutil
                    with value.open('rb') as source:
                        shutil.copyfileobj(source, handle, 65536)
                else:
                    handle.write(value.encode('utf-8') if isinstance(value, str) else value)
                handle.flush()
                os.fsync(handle.fileno())
        prepare_publication(payloads, result)
        planned_backups = {path: path.with_name('.' + path.name + '.' + uuid4().hex + '.bak')
                           for path in paths + retired if force and path.exists()}
        transaction = {"lock": str(lock.resolve()),
                       "staged": [str(path.resolve()) for path in staged.values()],
                       "backups": {str(path.resolve()): str(backup.resolve()) for path, backup in planned_backups.items()},
                       "new_paths": [str(path.resolve()) for path in paths if not path.exists()]}
        if session:
            session.put("publication_transaction", transaction)
        if not force:
            for path in paths:
                with path.open('x'):
                    pass
                reservations.append(path)
        else:
            for path in [marker] + paths[:-1] + retired:
                if path.exists():
                    backup = planned_backups[path]
                    os.replace(path, backup)
                    backups[path] = backup
        for path in paths:
            os.replace(staged[path], path)
            published.append(path)
    except BaseException:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        for path in reservations:
            path.unlink(missing_ok=True)
        for path, backup in backups.items():
            os.replace(backup, path)
        raise
    else:
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
        if session and transaction:
            session.put("publication_transaction", None)


def recover_publication(session):
    """Undo an interrupted publication before stage preflight, preserving old files."""
    transaction = session.data["state"].get("publication_transaction")
    if not transaction:
        return
    expected = session.data["state"]["publication"]["artifacts"]
    for path_string, backup_string in transaction["backups"].items():
        path, backup = Path(path_string), Path(backup_string)
        if backup.exists():
            if path.exists() and file_identity(path) != expected.get(path_string):
                raise ValueError("publication output changed after interruption: " + path_string)
            os.replace(backup, path)
    for path_string in transaction["new_paths"]:
        path = Path(path_string)
        if path.exists():
            if file_identity(path) != expected.get(path_string) and path.stat().st_size:
                raise ValueError("publication output changed after interruption: " + path_string)
            path.unlink()
    for path_string in transaction["staged"]:
        Path(path_string).unlink(missing_ok=True)
    Path(transaction["lock"]).unlink(missing_ok=True)
    session.put("publication_transaction", None)
