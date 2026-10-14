"""Interactive terminal fixtures for offline CLI and local reference tests."""
import io
import sys
from contextlib import contextmanager
from unittest.mock import patch


class FixtureTerminal(io.StringIO):
    """External interactive terminal fixture; never used by production."""

    def isatty(self):
        return True


@contextmanager
def browser_cli_terminal():
    """Model the CLI's external terminal streams without disabling test capture."""
    with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True):
        yield
