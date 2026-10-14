from pathlib import Path
import os
import venv

import pytest


@pytest.fixture
def qualified_browser_demonstration(monkeypatch):
    """Isolate post-demonstration discovery contracts from terminal input.

    Actual recording and review are exercised by the single-demonstration and
    calibration tests; these tests start from their qualified precondition.
    """
    from lladar.browser_target import BrowserTarget

    def qualified(target):
        target._probe_recorded = True
        target._calibration_qualified = True
        target._calibration_turn_id = "qualified-demonstration"
        target._calibration_question = "Previously demonstrated question"

    monkeypatch.setattr(BrowserTarget, "_capture_manual_calibration", qualified)


@pytest.fixture(scope="session")
def isolated_target_python(tmp_path_factory):
    root = tmp_path_factory.mktemp("target-python")
    venv.EnvBuilder(with_pip=False).create(root)
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
