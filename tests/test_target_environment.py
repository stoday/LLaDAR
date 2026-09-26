"""Use real venvs and subprocess imports; no mocked environment inspectors."""
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

from lladar.runner import resolve_project_python
from lladar.target_environment import validate_target_python


def test_missing_target_environment_never_falls_back(tmp_path):
    with pytest.raises(ValueError, match="No target .venv"):
        resolve_project_python(tmp_path)


def test_controller_environment_is_rejected():
    with pytest.raises(ValueError, match="shares LLaDAR"):
        validate_target_python(sys.executable)


def test_actual_target_prefix_is_independent(isolated_target_python):
    info = validate_target_python(isolated_target_python)
    assert Path(info["prefix"]).resolve() != Path(sys.prefix).resolve()
    assert Path(info["prefix"]).resolve() == isolated_target_python.parent.parent.resolve()


def test_explicit_target_python_preserves_the_venv_launcher(tmp_path, isolated_target_python):
    selected = resolve_project_python(tmp_path, isolated_target_python)

    assert selected == isolated_target_python.absolute()
    validate_target_python(selected)


def test_system_site_packages_are_rejected(tmp_path):
    root = tmp_path / "shared-system-packages"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(root)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    with pytest.raises(ValueError, match="system-site-packages"):
        validate_target_python(python)


def test_conflicting_imports_and_activation_do_not_leak(tmp_path, isolated_target_python):
    controller_modules = tmp_path / "controller-packages"
    controller_modules.mkdir()
    (controller_modules / "dependency_conflict_example.py").write_text("VERSION = 'controller-v1'\n")
    location = subprocess.check_output(
        [str(isolated_target_python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        text=True).strip()
    (Path(location) / "dependency_conflict_example.py").write_text("VERSION = 'target-v2'\n")
    code = '''import json,sys,subprocess,os
from pathlib import Path
import dependency_conflict_example as dependency
from lladar.target_environment import target_environment, validate_target_python
python=Path(sys.argv[1])
info=validate_target_python(python)
environment=target_environment(python)
target=subprocess.check_output([str(python),'-c',
    "import dependency_conflict_example as d,sys,os,json; print(json.dumps({'version':d.VERSION,'prefix':sys.prefix,'activation':os.environ.get('VIRTUAL_ENV'),'pythonpath':os.environ.get('PYTHONPATH')}))"],
    env=environment,text=True)
print(json.dumps({'controller':dependency.VERSION,'target':json.loads(target)}))
'''
    environment = {**os.environ, "PYTHONPATH": str(controller_modules),
                   "VIRTUAL_ENV": sys.prefix}
    result = subprocess.run([sys.executable, "-c", code, str(isolated_target_python)],
                            env=environment, capture_output=True, text=True, check=True)
    observed = json.loads(result.stdout)
    assert observed["controller"] == "controller-v1"
    assert observed["target"]["version"] == "target-v2"
    assert observed["target"]["pythonpath"] is None
    assert Path(observed["target"]["activation"]).resolve() == isolated_target_python.parent.parent.resolve()
