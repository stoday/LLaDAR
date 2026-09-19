from pathlib import Path
import os
import venv

import pytest


@pytest.fixture(scope="session")
def isolated_target_python(tmp_path_factory):
    root = tmp_path_factory.mktemp("target-python")
    venv.EnvBuilder(with_pip=False).create(root)
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
