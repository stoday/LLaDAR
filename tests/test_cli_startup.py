"""Startup must be observable even while application imports are blocked."""

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]["lladar"]


@pytest.mark.parametrize("route", ["console", "module"])
@pytest.mark.parametrize("argv", [
    [], ["--help"], ["--version"], ["--invalid-option"],
    ["create", "--help"], ["run-agent", "--no-verbose"],
    ["eval"], ["report"], ["plan"], ["--lang", "zh-TW", "--help"],
])
def test_startup_is_flushed_before_application_imports(route, argv):
    # Pause at the real first application import. The parent must receive the
    # banner while the child is still paused, even with stderr redirected.
    code = f"""
import importlib.abc, importlib, runpy, sys
class Barrier(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'lladar.api':
            sys.stdin.readline()
            raise RuntimeError('STARTUP_IMPORT_BARRIER')
sys.meta_path.insert(0, Barrier())
sys.argv = ['lladar'] + {argv!r}
if {route!r} == 'module':
    runpy.run_module('lladar.cli', run_name='__main__')
else:
    module, name = {ENTRY!r}.split(':')
    getattr(importlib.import_module(module), name)()
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    child = subprocess.Popen(
        [sys.executable, "-S", "-c", code], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    lines = queue.Queue()
    reader = threading.Thread(target=lambda: lines.put(child.stderr.readline()), daemon=True)
    reader.start()
    try:
        try:
            line = lines.get(timeout=5)
        except queue.Empty:
            pytest.fail("CLI produced no startup output before loading application dependencies")
        assert line.startswith("LLaDAR"), line
        assert "Starting" in line or "啟動" in line
        assert child.poll() is None, "startup should arrive while imports are still blocked"
    finally:
        child.kill()
        child.wait(timeout=5)
        reader.join(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()


def test_importing_package_is_silent_and_does_not_load_application():
    code = "import sys, lladar; assert 'lladar.api' not in sys.modules; assert 'lladar.evaluation' not in sys.modules"
    result = subprocess.run(
        [sys.executable, "-S", "-c", code],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_lazy_public_api_preserves_exports():
    import lladar
    from lladar.api import create_test_dataset
    from lladar.evaluation import evaluate
    from lladar.exceptions import EvaluationError
    from lladar.reporting import create_report
    from lladar.runner import run_agent

    assert lladar.create_test_dataset is create_test_dataset
    assert lladar.evaluate is lladar.eval is evaluate
    assert lladar.create_report is create_report
    assert lladar.run_agent is run_agent
    assert lladar.EvaluationError is EvaluationError
    assert set(lladar.__all__).issubset(dir(lladar))
    with pytest.raises(AttributeError):
        lladar.unknown_public_export


@pytest.mark.parametrize("arguments, expected", [
    (["--lang", "zh-TW"], "正在啟動"),
    (["--lang=zh-CN"], "正在启动"),
    (["--lan", "ja"], "起動中"),
    (["--lang", "ko"], "시작 중"),
    (["--lang", "fr"], "Starting"),
    (["--lang", "!invalid-private-value"], "Starting"),
    (["--", "--lang", "zh-TW"], "Starting"),
])
def test_startup_language_and_argument_privacy(monkeypatch, capsys, arguments, expected):
    from lladar._startup import announce_startup

    monkeypatch.setattr(sys, "argv", ["lladar", *arguments])
    announce_startup()
    output = capsys.readouterr()
    assert output.out == ""
    assert expected in output.err
    assert "invalid-private-value" not in output.err
