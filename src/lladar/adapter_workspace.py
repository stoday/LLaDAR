"""Bounded source inspection, adapted from VIDE-TESTING exploration.py."""
from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IGNORED_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "__pycache__"}


@dataclass(slots=True)
class ExplorationBudget:
    max_tool_calls: int = 200
    max_read_characters: int = 120_000
    command_timeout_seconds: float = 20.0


@dataclass(slots=True)
class AuditEvent:
    sequence: int
    tool: str
    arguments: dict[str, Any]
    summary: str
    result_fingerprint: str | None = None


class WorkspaceExplorer:
    """Restricted capabilities exposed to the code-discovery agent."""

    def __init__(
        self,
        root: Path | str,
        *,
        budget: ExplorationBudget | None = None,
        python_executable: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"Workspace does not exist: {self.root}")
        self.budget = budget or ExplorationBudget()
        self.python_executable = python_executable or sys.executable
        self.environment = dict(environment) if environment is not None else os.environ.copy()
        self.audit_events: list[AuditEvent] = []
        self._tool_calls = 0
        self._read_characters = 0

    def _check_budget(self) -> None:
        if self._tool_calls >= self.budget.max_tool_calls:
            raise RuntimeError("Exploration tool-call budget exhausted")
        self._tool_calls += 1

    def _record(
        self,
        tool: str,
        arguments: dict[str, Any],
        summary: str,
        result_content: str | None = None,
    ) -> None:
        self.audit_events.append(
            AuditEvent(
                sequence=len(self.audit_events) + 1,
                tool=tool,
                arguments=arguments,
                summary=summary,
                result_fingerprint=(
                    hashlib.sha256(result_content.encode()).hexdigest()
                    if result_content
                    else None
                ),
            )
        )

    def _resolve(self, relative_path: str | Path) -> Path:
        target = (self.root / relative_path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Path is outside workspace: {relative_path}") from exc
        return target

    def _visible(self, path: Path) -> bool:
        return (not path.is_symlink() and path.resolve().is_relative_to(self.root)
                and not any(part.startswith(".env") for part in path.relative_to(self.root).parts)
                and not bool(set(path.relative_to(self.root).parts) & IGNORED_DIRECTORIES))

    def list_files(self, pattern: str = "**/*", limit: int = 500) -> list[str]:
        self._check_budget()
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("Glob must stay inside workspace")
        files = sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.glob(pattern)
            if path.is_file() and self._visible(path)
        )[:limit]
        self._record("list_files", {"pattern": pattern, "limit": limit}, f"{len(files)} files")
        return files

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 240,
    ) -> str:
        self._check_budget()
        target = self._resolve(path)
        if not target.is_file() or not self._visible(target):
            raise ValueError(f"Not a readable workspace file: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        content = "\n".join(lines[max(0, start_line - 1) : max(start_line, end_line)])
        if self._read_characters + len(content) > self.budget.max_read_characters:
            raise RuntimeError("Exploration source-reading budget exhausted")
        self._read_characters += len(content)
        self._record(
            "read_file",
            {"path": path, "start_line": start_line, "end_line": end_line},
            f"{len(content)} characters",
        )
        return content

    def search_code(self, query: str, pattern: str = "**/*.py", limit: int = 100) -> list[dict[str, Any]]:
        self._check_budget()
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("Glob must stay inside workspace")
        matches: list[dict[str, Any]] = []
        for path in sorted(self.root.glob(pattern)):
            if not path.is_file() or not self._visible(path):
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                if query in line:
                    matches.append(
                        {
                            "path": path.relative_to(self.root).as_posix(),
                            "line": line_number,
                            "text": line[:500],
                        }
                    )
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break
        self._record("search_code", {"query": query, "pattern": pattern}, f"{len(matches)} matches")
        return matches

    def write_harness(self, filename: str, content: str) -> str:
        self._check_budget()
        if not filename.endswith(".py") or Path(filename).name != filename or not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            raise ValueError("Harness path must be a simple filename")
        directory = self.root / ".lladar" / "harnesses"
        directory = self._resolve(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = self._resolve(directory / filename)
        target.write_text(content, encoding="utf-8")
        relative = target.relative_to(self.root).as_posix()
        self._record("write_harness", {"filename": filename}, f"wrote {len(content)} characters")
        return relative
