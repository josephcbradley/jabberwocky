"""Tests for jabberwocky.index — PEP 691 JSON index generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jabberwocky.index import build_index
from jabberwocky.pypi import PackageRelease, ResolvedPackage, WheelFile


def _make_wheel(
    filename: str, url: str = "https://pypi.org/files/pkg.whl", sha256: str = "abc123"
) -> WheelFile:
    return WheelFile(
        filename=filename,
        url=url,
        sha256=sha256,
        requires_python=None,
        python_tags=["cp312"],
        abi_tags=["cp312"],
        platform_tags=["linux_x86_64"],
    )


def _make_resolved(
    name: str, version: str, wheels: list[WheelFile], needs_wheels: bool = True
) -> ResolvedPackage:
    return ResolvedPackage(
        name=name,
        version=version,
        release=PackageRelease(name=name, version=version, wheels=wheels),
        needs_wheels=needs_wheels,
    )


class TestBuildIndex:
    def test_creates_simple_directory(self, tmp_path: Path):
        resolved = {"mypackage": _make_resolved("mypackage", "1.0", [])}
        build_index(resolved, tmp_path)
        assert (tmp_path / "simple").is_dir()
        assert (tmp_path / "simple" / "index.json").exists()
        assert (tmp_path / "simple" / "index.html").exists()

    def test_project_list_contains_all_packages(self, tmp_path: Path):
        resolved = {
            "alpha": _make_resolved("alpha", "1.0", []),
            "beta": _make_resolved("beta", "2.0", []),
        }
        build_index(resolved, tmp_path)
        data = json.loads((tmp_path / "simple" / "index.json").read_text())
        names = [p["name"] for p in data["projects"]]
        assert "alpha" in names
        assert "beta" in names

    def test_project_list_is_sorted(self, tmp_path: Path):
        resolved = {
            "zebra": _make_resolved("zebra", "1.0", []),
            "alpha": _make_resolved("alpha", "1.0", []),
            "middle": _make_resolved("middle", "1.0", []),
        }
        build_index(resolved, tmp_path)
        data = json.loads((tmp_path / "simple" / "index.json").read_text())
        names = [p["name"] for p in data["projects"]]
        assert names == sorted(names)

    def test_project_list_has_correct_api_version(self, tmp_path: Path):
        build_index({}, tmp_path)
        data = json.loads((tmp_path / "simple" / "index.json").read_text())
        assert data["meta"]["api-version"] == "1.0"

    def test_project_detail_created_for_each_package(self, tmp_path: Path):
        resolved = {"mypackage": _make_resolved("mypackage", "1.0", [])}
        build_index(resolved, tmp_path)
        assert (tmp_path / "simple" / "mypackage" / "index.json").exists()
        assert (tmp_path / "simple" / "mypackage" / "index.html").exists()

    def test_project_name_is_canonicalized(self, tmp_path: Path):
        # Package names with underscores/capitals should be normalized
        resolved = {"My_Package": _make_resolved("My_Package", "1.0", [])}
        build_index(resolved, tmp_path)
        # canonicalize_name("My_Package") == "my-package"
        assert (tmp_path / "simple" / "my-package" / "index.json").exists()

    def test_local_wheel_gets_local_url(self, tmp_path: Path):
        wheel = _make_wheel("pkg-1.0-cp312-cp312-linux_x86_64.whl", sha256="deadbeef")
        # Write a fake wheel file to disk so the index builder finds it
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / wheel.filename).write_bytes(b"fake wheel content")

        resolved = {"pkg": _make_resolved("pkg", "1.0", [wheel], needs_wheels=True)}
        build_index(resolved, tmp_path, base_url="http://mirror:8080")

        data = json.loads((tmp_path / "simple" / "pkg" / "index.json").read_text())
        assert len(data["files"]) == 1
        file_entry = data["files"][0]
        assert (
            file_entry["url"]
            == "http://mirror:8080/files/pkg-1.0-cp312-cp312-linux_x86_64.whl"
        )
        # SHA-256 should be recomputed from the local file, not taken from wheel.sha256
        assert "sha256" in file_entry["hashes"]

    def test_metadata_only_package_gets_pypi_url(self, tmp_path: Path):
        wheel = _make_wheel(
            "appnope-0.1.4-py3-none-any.whl",
            url="https://files.pythonhosted.org/packages/appnope-0.1.4-py3-none-any.whl",
            sha256="cafebabe",
        )
        resolved = {
            "appnope": _make_resolved("appnope", "0.1.4", [wheel], needs_wheels=False)
        }
        build_index(resolved, tmp_path)

        data = json.loads((tmp_path / "simple" / "appnope" / "index.json").read_text())
        assert len(data["files"]) == 1
        file_entry = data["files"][0]
        assert (
            file_entry["url"]
            == "https://files.pythonhosted.org/packages/appnope-0.1.4-py3-none-any.whl"
        )
        assert file_entry["hashes"]["sha256"] == "cafebabe"

    def test_missing_local_wheel_omitted_from_index(self, tmp_path: Path):
        # needs_wheels=True but the file was never downloaded (wrong platform)
        wheel = _make_wheel("pkg-1.0-cp312-cp312-win_amd64.whl")
        resolved = {"pkg": _make_resolved("pkg", "1.0", [wheel], needs_wheels=True)}
        build_index(resolved, tmp_path)

        data = json.loads((tmp_path / "simple" / "pkg" / "index.json").read_text())
        # File not on disk and needs_wheels=True → omitted
        assert data["files"] == []

    def test_requires_python_included_when_set(self, tmp_path: Path):
        wheel = _make_wheel("pkg-1.0-py3-none-any.whl")
        wheel.requires_python = ">=3.10"
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / wheel.filename).write_bytes(b"fake")

        resolved = {"pkg": _make_resolved("pkg", "1.0", [wheel], needs_wheels=True)}
        build_index(resolved, tmp_path)

        data = json.loads((tmp_path / "simple" / "pkg" / "index.json").read_text())
        assert data["files"][0]["requires-python"] == ">=3.10"

    def test_requires_python_absent_when_not_set(self, tmp_path: Path):
        wheel = _make_wheel("pkg-1.0-py3-none-any.whl")
        wheel.requires_python = None
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / wheel.filename).write_bytes(b"fake")

        resolved = {"pkg": _make_resolved("pkg", "1.0", [wheel], needs_wheels=True)}
        build_index(resolved, tmp_path)

        data = json.loads((tmp_path / "simple" / "pkg" / "index.json").read_text())
        assert "requires-python" not in data["files"][0]

    def test_local_url_without_base_url(self, tmp_path: Path):
        wheel = _make_wheel("pkg-1.0-py3-none-any.whl")
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / wheel.filename).write_bytes(b"fake")

        resolved = {"pkg": _make_resolved("pkg", "1.0", [wheel], needs_wheels=True)}
        build_index(resolved, tmp_path, base_url="")

        data = json.loads((tmp_path / "simple" / "pkg" / "index.json").read_text())
        assert data["files"][0]["url"] == "../../files/pkg-1.0-py3-none-any.whl"

        html_content = (tmp_path / "simple" / "pkg" / "index.html").read_text()
        assert 'href="../../files/pkg-1.0-py3-none-any.whl' in html_content

    def test_empty_resolved_dict(self, tmp_path: Path):
        build_index({}, tmp_path)
        data = json.loads((tmp_path / "simple" / "index.json").read_text())
        assert data["projects"] == []

    def test_merges_multiple_versions(self, tmp_path: Path):
        # Two versions of the same package
        pkg_v1 = _make_resolved("pkg", "1.0", [_make_wheel("pkg-1.0-any.whl")])
        pkg_v2 = _make_resolved("pkg", "2.0", [_make_wheel("pkg-2.0-any.whl")])

        # Write dummy files
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / "pkg-1.0-any.whl").write_bytes(b"v1")
        (files_dir / "pkg-2.0-any.whl").write_bytes(b"v2")

        # Pass list of packages
        build_index([pkg_v1, pkg_v2], tmp_path)

        data = json.loads((tmp_path / "simple" / "pkg" / "index.json").read_text())
        assert len(data["files"]) == 2
        filenames = {f["filename"] for f in data["files"]}
        assert filenames == {"pkg-1.0-any.whl", "pkg-2.0-any.whl"}
