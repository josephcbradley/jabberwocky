"""Tests for jabberwocky.server — HTTP routing, content types, and wheel-only enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from jabberwocky.server import CONTENT_TYPE_JSON, make_app


def _build_mirror(tmp_path: Path, packages: dict | None = None) -> Path:
    """
    Create a minimal mirror directory structure for testing.

    packages: dict of canonical_name -> list of (filename, content) tuples.
    """
    simple_dir = tmp_path / "simple"
    files_dir = tmp_path / "files"
    simple_dir.mkdir()
    files_dir.mkdir()

    package_list = list(packages.keys()) if packages else []

    # Write project list index
    (simple_dir / "index.json").write_text(
        json.dumps(
            {
                "meta": {"api-version": "1.0"},
                "projects": [{"name": name} for name in package_list],
            }
        )
    )

    # Write per-project indexes and wheel files
    for name, wheel_files in (packages or {}).items():
        pkg_dir = simple_dir / name
        pkg_dir.mkdir()
        file_entries = []
        for filename, content in wheel_files:
            (files_dir / filename).write_bytes(content)
            file_entries.append(
                {
                    "filename": filename,
                    "url": f"/files/{filename}",
                    "hashes": {"sha256": "abc"},
                }
            )
        (pkg_dir / "index.json").write_text(
            json.dumps(
                {
                    "meta": {"api-version": "1.0"},
                    "name": name,
                    "files": file_entries,
                }
            )
        )

    return tmp_path


class TestSimpleIndex:
    def test_returns_200(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path)
        client = TestClient(make_app(mirror))
        resp = client.get("/simple/")
        assert resp.status_code == 200

    def test_content_type_is_pep691_json(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path)
        client = TestClient(make_app(mirror))
        resp = client.get("/simple/")
        assert resp.headers["content-type"].startswith(CONTENT_TYPE_JSON)

    def test_returns_project_list(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path, {"polars": [], "scipy": []})
        client = TestClient(make_app(mirror))
        data = client.get("/simple/").json()
        names = [p["name"] for p in data["projects"]]
        assert "polars" in names
        assert "scipy" in names

    def test_503_when_mirror_not_built(self, tmp_path: Path):
        # No index.json written
        (tmp_path / "simple").mkdir()
        client = TestClient(make_app(tmp_path))
        resp = client.get("/simple/")
        assert resp.status_code == 503


class TestProjectDetail:
    def test_returns_200_for_known_package(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path, {"polars": []})
        client = TestClient(make_app(mirror))
        resp = client.get("/simple/polars/")
        assert resp.status_code == 200

    def test_content_type_is_pep691_json(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path, {"polars": []})
        client = TestClient(make_app(mirror))
        resp = client.get("/simple/polars/")
        assert resp.headers["content-type"].startswith(CONTENT_TYPE_JSON)

    def test_404_for_unknown_package(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path)
        client = TestClient(make_app(mirror))
        resp = client.get("/simple/doesnotexist/")
        assert resp.status_code == 404

    def test_name_normalisation(self, tmp_path: Path):
        # Index written under "my-package" (canonical), but requested as "My_Package"
        mirror = _build_mirror(tmp_path, {"my-package": []})
        client = TestClient(make_app(mirror))
        resp = client.get("/simple/My_Package/")
        assert resp.status_code == 200

    def test_returns_file_list(self, tmp_path: Path):
        mirror = _build_mirror(
            tmp_path, {"polars": [("polars-1.0-cp312-cp312-linux_x86_64.whl", b"fake")]}
        )
        client = TestClient(make_app(mirror))
        data = client.get("/simple/polars/").json()
        assert len(data["files"]) == 1
        assert data["files"][0]["filename"] == "polars-1.0-cp312-cp312-linux_x86_64.whl"


class TestServeFile:
    def test_serves_wheel_file(self, tmp_path: Path):
        content = b"fake wheel content"
        mirror = _build_mirror(
            tmp_path, {"polars": [("polars-1.0-cp312-cp312-linux_x86_64.whl", content)]}
        )
        client = TestClient(make_app(mirror))
        resp = client.get("/files/polars-1.0-cp312-cp312-linux_x86_64.whl")
        assert resp.status_code == 200
        assert resp.content == content

    def test_404_for_missing_wheel(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path)
        client = TestClient(make_app(mirror))
        resp = client.get("/files/nonexistent-1.0-py3-none-any.whl")
        assert resp.status_code == 404

    def test_rejects_sdist(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path)
        # Place a tarball in the files directory
        (tmp_path / "files" / "polars-1.0.tar.gz").write_bytes(b"fake sdist")
        client = TestClient(make_app(mirror))
        resp = client.get("/files/polars-1.0.tar.gz")
        assert resp.status_code == 400

    def test_rejects_arbitrary_file_extension(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path)
        (tmp_path / "files" / "script.sh").write_bytes(b"rm -rf /")
        client = TestClient(make_app(mirror))
        resp = client.get("/files/script.sh")
        assert resp.status_code == 400

    def test_rejects_zip_file(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path)
        (tmp_path / "files" / "pkg-1.0.zip").write_bytes(b"fake zip")
        client = TestClient(make_app(mirror))
        resp = client.get("/files/pkg-1.0.zip")
        assert resp.status_code == 400

    def test_rejects_path_traversal_attempt(self, tmp_path: Path):
        mirror = _build_mirror(tmp_path)
        # Write a .whl file outside the files/ directory that we try to reach
        (tmp_path / "secret.whl").write_bytes(b"secret")
        client = TestClient(make_app(mirror))
        resp = client.get("/files/../secret.whl")
        # Starlette normalises the path before routing, so this either 404s or 400s
        assert resp.status_code in (400, 404)
