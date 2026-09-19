"""Keep the controller's Python dependencies out of target subprocesses."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def target_environment(python: Path, env_file: str | Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    if env_file is not None:
        from dotenv import dotenv_values

        for key, value in dotenv_values(env_file).items():
            if value is not None:
                environment.setdefault(key, value)
    old_bins = {str(Path(sys.executable).absolute().parent).casefold()}
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if environment.get(name):
            root = Path(environment[name])
            old_bins.update(str(root / suffix).casefold() for suffix in ("", "Scripts", "bin", "Library/bin"))
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV",
                "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_PROMPT_MODIFIER",
                "_CE_CONDA", "_CE_M", "__PYVENV_LAUNCHER__"):
        environment.pop(key, None)
    scripts = python.absolute().parent
    path = [part for part in environment.get("PATH", "").split(os.pathsep)
            if part and str(Path(part).absolute()).casefold() not in old_bins]
    environment["PATH"] = os.pathsep.join([str(scripts), *path])
    if (scripts.parent / "pyvenv.cfg").is_file():
        environment["VIRTUAL_ENV"] = str(scripts.parent)
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONNOUSERSITE="1")
    return environment


def validate_target_python(python: str | Path) -> dict[str, str]:
    # Preserve venv symlinks on POSIX: resolving python to the base executable
    # would silently escape the virtual environment.
    executable = Path(python).absolute()
    if not executable.is_file():
        raise FileNotFoundError(f"Target Python not found: {executable}")
    probe = subprocess.run(
        [str(executable), "-I", "-c",
         "import json,sys; print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"],
        env=target_environment(executable), capture_output=True, text=True,
        encoding="utf-8", timeout=15, check=False,
    )
    try:
        info = json.loads(probe.stdout)
        prefix = Path(info["prefix"]).resolve()
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"Cannot inspect target Python environment: {executable}") from error
    if probe.returncode:
        raise ValueError(f"Target Python failed its environment check: {executable}")
    if prefix == Path(sys.prefix).resolve():
        raise ValueError("Target Python shares LLaDAR's environment. Use a separate project .venv or --target-python PATH.")
    configuration = prefix / "pyvenv.cfg"
    if configuration.is_file():
        lines = configuration.read_text(encoding="utf-8").lower().splitlines()
        if any(line.partition("=")[0].strip() == "include-system-site-packages"
               and line.partition("=")[2].strip() == "true" for line in lines):
            raise ValueError("Target venv enables system-site-packages; use an isolated target environment.")
    return info
