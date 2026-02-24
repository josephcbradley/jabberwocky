"""Tests for jabberwocky.server — HTTP routing, content types, and wheel-only enforcement."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest

from jabberwocky.server import CONTENT_TYPE_JSON, run


def _build_mirror(tmp_path: Path, packages: dict | None = None) -> Path:
    """
    Create a minimal mirror directory structure for testing.

    packages: dict of canonical_name -> list of (filename, content) tuples.
    """
    simple_dir = tmp_path / "simple"
    files_dir = tmp_path / "files"
    simple_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

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
        pkg_dir.mkdir(parents=True, exist_ok=True)
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


@pytest.fixture
def server_url(tmp_path):
    """Start the server in a thread and return the base URL."""
    # Find a free port
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()

    mirror_dir = tmp_path

    # Start server
    t = threading.Thread(target=run, args=(mirror_dir, "127.0.0.1", port), daemon=True)
    t.start()

    # Wait for server to be ready? run() is synchronous but inside thread.
    # It prints "Serving...".
    time.sleep(0.5)

    yield f"http://127.0.0.1:{port}"


class TestSimpleIndex:
    def test_returns_200(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path)
        resp = httpx.get(f"{server_url}/simple/")
        assert resp.status_code == 200

    def test_content_type_is_pep691_json(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path)
        resp = httpx.get(f"{server_url}/simple/")
        assert resp.headers["content-type"].startswith(CONTENT_TYPE_JSON)

    def test_returns_project_list(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path, {"polars": [], "scipy": []})
        resp = httpx.get(f"{server_url}/simple/")
        data = resp.json()
        names = [p["name"] for p in data["projects"]]
        assert "polars" in names
        assert "scipy" in names

    def test_503_when_mirror_not_built(self, tmp_path: Path, server_url):
        # No index.json written yet (files/ dirs exist but index missing)
        (tmp_path / "simple").mkdir(parents=True, exist_ok=True)
        # Note: _build_mirror writes index.json. If we just have dirs:
        resp = httpx.get(f"{server_url}/simple/")
        assert resp.status_code == 503


class TestProjectDetail:
    def test_returns_200_for_known_package(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path, {"polars": []})
        resp = httpx.get(f"{server_url}/simple/polars/")
        assert resp.status_code == 200

    def test_content_type_is_pep691_json(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path, {"polars": []})
        resp = httpx.get(f"{server_url}/simple/polars/")
        assert resp.headers["content-type"].startswith(CONTENT_TYPE_JSON)

    def test_404_for_unknown_package(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path)
        resp = httpx.get(f"{server_url}/simple/doesnotexist/")
        assert resp.status_code == 404

    def test_name_normalisation(self, tmp_path: Path, server_url):
        # Index written under "my-package" (canonical), but requested as "My_Package"
        _build_mirror(tmp_path, {"my-package": []})
        resp = httpx.get(f"{server_url}/simple/My_Package/")
        assert resp.status_code == 200

    def test_returns_file_list(self, tmp_path: Path, server_url):
        _build_mirror(
            tmp_path, {"polars": [("polars-1.0-cp312-cp312-linux_x86_64.whl", b"fake")]}
        )
        resp = httpx.get(f"{server_url}/simple/polars/")
        data = resp.json()
        assert len(data["files"]) == 1
        assert data["files"][0]["filename"] == "polars-1.0-cp312-cp312-linux_x86_64.whl"


class TestServeFile:
    def test_serves_wheel_file(self, tmp_path: Path, server_url):
        content = b"fake wheel content"
        _build_mirror(
            tmp_path, {"polars": [("polars-1.0-cp312-cp312-linux_x86_64.whl", content)]}
        )
        resp = httpx.get(f"{server_url}/files/polars-1.0-cp312-cp312-linux_x86_64.whl")
        assert resp.status_code == 200
        assert resp.content == content

    def test_404_for_missing_wheel(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path)
        resp = httpx.get(f"{server_url}/files/nonexistent-1.0-py3-none-any.whl")
        assert resp.status_code == 404

    def test_rejects_sdist(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path)
        # Place a tarball in the files directory
        (tmp_path / "files" / "polars-1.0.tar.gz").write_bytes(b"fake sdist")
        resp = httpx.get(f"{server_url}/files/polars-1.0.tar.gz")
        assert resp.status_code == 400

    def test_rejects_arbitrary_file_extension(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path)
        (tmp_path / "files" / "script.sh").write_bytes(b"rm -rf /")
        resp = httpx.get(f"{server_url}/files/script.sh")
        assert resp.status_code == 400

    def test_rejects_zip_file(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path)
        (tmp_path / "files" / "pkg-1.0.zip").write_bytes(b"fake zip")
        resp = httpx.get(f"{server_url}/files/pkg-1.0.zip")
        assert resp.status_code == 400

    def test_rejects_path_traversal_attempt(self, tmp_path: Path, server_url):
        _build_mirror(tmp_path)
        # Write a .whl file outside the files/ directory that we try to reach
        (tmp_path / "secret.whl").write_bytes(b"secret")
        # Attempt traversal
        # httpx normally resolves paths, but we can craft it
        # Actually, http.server will collapse .. if we use path, but we used unquote(path).
        # But if the URL sent is /files/../secret.whl, requests/httpx might normalize it before sending.
        # We need to construct a raw request or assume httpx sends it if we construct it carefully.
        # But usually clients normalize.
        # However, checking if server rejects it is good.
        # If we request /files/..%2Fsecret.whl ?
        resp = httpx.get(f"{server_url}/files/..%2Fsecret.whl")
        # ..%2F is decoded to ../, which server should handle.
        # If unquote happens first, it becomes ../, then check logic.
        assert resp.status_code in (400, 404)
