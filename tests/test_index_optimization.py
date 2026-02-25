
import hashlib
import json
from pathlib import Path
import pytest
from jabberwocky.index import build_index
from jabberwocky.pypi import PackageRelease, ResolvedPackage, WheelFile

def _make_wheel(filename: str, sha256: str) -> WheelFile:
    return WheelFile(
        filename=filename,
        url="http://example.com/file.whl",
        sha256=sha256,
        requires_python=None,
        python_tags=["py3"],
        abi_tags=["none"],
        platform_tags=["any"],
    )

def _make_resolved(name: str, version: str, wheels: list[WheelFile]) -> ResolvedPackage:
    return ResolvedPackage(
        name=name,
        version=version,
        release=PackageRelease(name=name, version=version, wheels=wheels),
        needs_wheels=True,
    )

class TestIndexOptimization:
    def test_uses_metadata_hash_if_available(self, tmp_path: Path):
        """Verify that we use the hash from metadata instead of recomputing it from file."""
        files_dir = tmp_path / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        # Use a fake hash in metadata, and different content on disk.
        # If optimization is used, we expect the metadata hash.
        metadata_hash = "1" * 64
        wheel = _make_wheel("pkg1-1.0-py3-none-any.whl", sha256=metadata_hash)
        (files_dir / wheel.filename).write_bytes(b"content1")

        resolved = {
            "pkg1": _make_resolved("pkg1", "1.0", [wheel]),
        }

        build_index(resolved, tmp_path)

        data = json.loads((tmp_path / "simple" / "pkg1" / "index.json").read_text())
        hash_in_index = data["files"][0]["hashes"]["sha256"]

        # This assertion should fail BEFORE the fix, and pass AFTER the fix.
        assert hash_in_index == metadata_hash

    def test_falls_back_to_calculation_if_metadata_missing(self, tmp_path: Path):
        """Verify that we calculate hash if metadata hash is missing."""
        files_dir = tmp_path / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        wheel = _make_wheel("pkg2-1.0-py3-none-any.whl", sha256="")
        content = b"content2"
        (files_dir / wheel.filename).write_bytes(content)
        expected_hash = hashlib.sha256(content).hexdigest()

        resolved = {
            "pkg2": _make_resolved("pkg2", "1.0", [wheel]),
        }

        build_index(resolved, tmp_path)

        data = json.loads((tmp_path / "simple" / "pkg2" / "index.json").read_text())
        hash_in_index = data["files"][0]["hashes"]["sha256"]

        assert hash_in_index == expected_hash
