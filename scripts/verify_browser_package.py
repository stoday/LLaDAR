"""Check built archives for autonomous browser discovery; never import them."""
from __future__ import annotations

from pathlib import Path
import sys
import tarfile
import zipfile


REQUIRED = {"answer_extraction.py", "extraction_provider.py", "response_capture.py", "browser_target.py",
            "recording_store.py", "demonstration_recorder.py", "independent_adapter.py", "independent_target.py", "independent_reference.py",
            "browser_agent_loop.py", "browser_discovery_driver.py", "browser_provenance.py", "browser_inspection.py",
            "browser_reference.py", "browser_materials.py", "browser_protocol_projection.py",
            "protocol_runtime.py", "protocol_resources.py", "resource_limits.py", "terminal_review.py",
            "live_status.py", "adapter_documentation.py", "portable_api.py", "api_runtime.py"}
REMOVED = {"generated_parser.py", "parser_generation.py", "parser_runtime.py", "parser_recording.py",
           "parser_cache.py", "parser_provider.py", "response_decoder.py", "browser_operation.py",
           "browser_adapter.py", "browser_repair.py", "dom_capture.py", "websocket_capture.py", "playwright_driver.py"}


def verify(path: Path) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata = [archive.read(name).decode("utf-8") for name in names if name.endswith("/METADATA")]
    else:
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
            metadata = [archive.extractfile(name).read().decode("utf-8") for name in names if name.endswith("/PKG-INFO")]
        for required in ("scripts/cleanup_generated.py", "tests/conftest.py"):
            if not any(name.endswith("/" + required) for name in names):
                raise ValueError("Source archive is missing " + required)
    sources = {Path(name).name for name in names if name.startswith("lladar/") or "/lladar/" in name}
    if not REQUIRED <= sources or REMOVED & sources:
        raise ValueError("Browser package contains missing or obsolete modules")
    if not metadata or not all("requires-dist: pydantic-monty" in item.lower() for item in metadata):
        raise ValueError("Browser package must declare its isolated protocol runtime dependency")
    if any(part in {".lladar", "browser-profiles", "parser-cache", ".env"} for name in names for part in Path(name).parts):
        raise ValueError("Private runtime material must not be packaged")


def main(argv=None) -> int:
    root = Path((argv or sys.argv[1:])[0])
    archives = sorted(root.glob("*.whl")) + sorted(root.glob("*.tar.gz"))
    if len(archives) != 2:
        raise ValueError("Expected exactly one wheel and one sdist")
    for archive in archives:
        verify(archive)
        print("Verified browser-discovery package: " + archive.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
