"""Public CLI version reporting uses the installed distribution's metadata."""

from importlib.metadata import version

import pytest

from lladar.cli import main


def test_version_reports_installed_package_without_a_command(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    output = capsys.readouterr()
    assert output.out == f"lladar {version('lladar')}\n"
    assert output.err == ""


def test_version_follows_package_metadata_changes(monkeypatch, capsys):
    import lladar.cli as cli

    def changed_version(distribution):
        assert distribution == "lladar"
        return "7.8.9rc1"

    monkeypatch.setattr(cli.metadata, "version", changed_version)
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "lladar 7.8.9rc1\n"
