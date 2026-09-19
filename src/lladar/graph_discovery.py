"""Optional, isolated Graphify AST indexing; no target imports or semantic API calls."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .interfaces import write_json
from .run_context import inventory


WORKER = r'''
import json, sys
from pathlib import Path
from importlib.metadata import version
from graphify.extract import collect_files, extract
root, output = map(Path, sys.argv[1:])
suffixes = {'.py', '.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs', '.go', '.rs',
            '.java', '.c', '.h', '.cpp', '.cs', '.rb', '.php', '.kt', '.swift',
            '.vue', '.svelte', '.scala', '.lua', '.ps1', '.sh', '.sql'}
files = [p for p in collect_files(root) if p.suffix.lower() in suffixes]
graph = extract(files, root=root, cache_root=output.parent, parallel=False)
graph['directed'] = True
graph['version'] = version('graphifyy')
graph['parsed_files'] = [p.relative_to(root).as_posix() for p in files]
output.write_text(json.dumps(graph, ensure_ascii=False), encoding='utf-8')
'''


def resolve_graph_python(explicit: str | Path | None) -> Path:
    if explicit:
        result = Path(explicit).absolute()
    else:
        # Only discover an existing uv tool environment. Never install on a run.
        uv = shutil.which("uv")
        if not uv:
            raise ValueError("Graphify unavailable: install with uv tool install graphifyy or use --graphify-python")
        probe = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True,
                               encoding="utf-8", timeout=10, check=True)
        result = Path(probe.stdout.strip()) / "graphifyy" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not result.is_file():
        raise ValueError("Graphify interpreter missing; install graphifyy in a separate tool environment")
    return result


class CodeGraph:
    def __init__(self, workspace: Path, evidence: Path, *, enabled: bool = True,
                 python: str | Path | None = None, timeout: float = 60):
        self.nodes, self.edges = [], []
        self.summary = {"status": "disabled" if not enabled else "building"}
        if not enabled:
            write_json(evidence / "graph-status.json", self.summary)
            return
        try:
            interpreter = resolve_graph_python(python)
            hashes = inventory(workspace)
            # All parsing happens on a filtered snapshot; ignored directories and
            # symlinks cannot cause resolvers to read outside the managed corpus.
            excluded = {"graphify-out", "dist", "build"}
            candidates = [p for p in hashes if not set(Path(p).parts) & excluded]
            if len(candidates) > 2000 or sum((workspace / p).stat().st_size for p in candidates) > 50_000_000:
                raise ValueError("Graph corpus exceeds 2000 files / 50 MB; using source exploration")
            snapshot = evidence / "graph-source"
            snapshot.mkdir(exist_ok=True)
            for relative in candidates:
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(workspace / relative, target)
            worker = evidence / "graph-worker.py"
            worker.write_text(WORKER, encoding="utf-8")
            output = evidence / "graph.json"
            env = {k: v for k, v in os.environ.items()
                   if k not in {"PYTHONPATH", "PYTHONHOME"} and not any(
                       marker in k.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
            completed = subprocess.run([str(interpreter), "-I", str(worker), str(snapshot), str(output)],
                                       cwd=evidence, env=env, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=timeout)
            if completed.returncode:
                raise ValueError(f"Graphify extraction exited {completed.returncode}")
            graph = json.loads(output.read_text(encoding="utf-8"))
            self.nodes, self.edges = graph["nodes"], graph["edges"]
            parsed = graph["parsed_files"]
            represented = {node.get("source_file") for node in self.nodes}
            self.summary = {"status": "ready", "version": graph["version"],
                            "nodes": len(self.nodes), "edges": len(self.edges),
                            "parser_inputs": parsed,
                            "files_without_nodes": [p for p in parsed if p not in represented],
                            "not_parsed": [p for p in candidates if p not in parsed],
                            "source_hashes": {p: hashes[p] for p in candidates},
                            "graph_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                            "note": "Directed static evidence only; cross-service links require source/runtime confirmation."}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            self.nodes, self.edges = [], []
            self.summary = {"status": "fallback", "reason": f"{type(error).__name__}: {error}"}
        write_json(evidence / "graph-status.json", self.summary)

    def query(self, query: str, limit: int = 12) -> dict:
        """Find bounded direct neighbors while retaining original edge direction."""
        limit = max(1, min(limit, 20))
        words = query.casefold().split()
        def score(node):
            text = (str(node.get("label", "")) + " " + str(node.get("source_file", ""))).casefold()
            return sum(word in text for word in words)
        matches = sorted((n for n in self.nodes if score(n)), key=score, reverse=True)[:limit]
        ids = {n["id"] for n in matches}
        edges = [e for e in self.edges if e.get("source") in ids or e.get("target") in ids][:limit * 2]
        neighbors = {e.get(key) for e in edges for key in ("source", "target")}
        nodes = matches + [n for n in self.nodes if n["id"] in neighbors - ids][:limit * 2]
        # Avoid implementation metadata and unbounded source/document content.
        keys = {"id", "label", "source_file", "source_location", "source", "target", "relation", "confidence"}
        return {"status": self.summary["status"], "directed": True,
                "nodes": [{k: str(v)[:300] for k, v in n.items() if k in keys} for n in nodes],
                "edges": [{k: str(v)[:300] for k, v in e.items() if k in keys} for e in edges],
                "note": "Bounded static neighborhood, not a complete runtime call graph. Read source to confirm."}
