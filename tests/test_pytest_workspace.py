"""The configured pytest workspace works in a clean checkout."""
from pathlib import Path
import shutil
import subprocess
import sys


def test_first_run_creates_temporary_workspace_parent(tmp_path):
    (tmp_path / 'pyproject.toml').write_text(
        '[tool.pytest.ini_options]\n'
        'addopts = "-q --basetemp=.tmp/pytest"\n'
        'cache_dir = ".tmp/pytest-cache"\n', encoding='utf-8')
    tests = tmp_path / 'tests'
    tests.mkdir()
    shutil.copyfile(Path(__file__).with_name('conftest.py'), tests / 'conftest.py')
    (tests / 'test_sample.py').write_text(
        'def test_workspace(tmp_path):\n'
        '    assert tmp_path.parent.name == "pytest"\n'
        '    assert tmp_path.parent.parent.name == ".tmp"\n', encoding='utf-8')
    assert not (tmp_path / '.tmp').exists()
    result = subprocess.run([sys.executable, '-m', 'pytest'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / '.tmp/pytest').is_dir()
    assert (tmp_path / '.tmp/pytest-cache').is_dir()
