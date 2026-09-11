from pathlib import Path

import pytest

from lladar.output import write_dataset


def test_serialization_failure_leaves_no_partial_dataset(tmp_path: Path):
    output = tmp_path / "dataset.jsonl"

    with pytest.raises(TypeError):
        write_dataset([{"not_json": object()}], output, "jsonl")

    assert not output.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_writer_reserves_destination_without_overwriting(tmp_path: Path):
    output = tmp_path / "dataset.jsonl"
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_dataset([{"value": 1}], output, "jsonl")

    assert output.read_text(encoding="utf-8") == "keep"


def test_write_failure_removes_reservation_and_partial_temporary_file(
    tmp_path: Path, monkeypatch
):
    output = tmp_path / "dataset.jsonl"
    original_open = Path.open

    class FailingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def write(self, content):
            self.handle.write(content[:3])
            self.handle.flush()
            raise OSError("disk full")

    def unreliable_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if path.parent == tmp_path and mode in {"x", "w"}:
            return FailingWriter(handle)
        return handle

    monkeypatch.setattr(Path, "open", unreliable_open)

    with pytest.raises(OSError, match="disk full"):
        write_dataset([{"value": 1}], output, "jsonl")

    assert not output.exists()
    assert list(tmp_path.glob(".*.tmp")) == []
