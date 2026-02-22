"""PEP 691 JSON index generator."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from packaging.utils import canonicalize_name

from .pypi import ResolvedPackage, WheelFile

log = logging.getLogger(__name__)

API_VERSION = "1.0"
CONTENT_TYPE_JSON = "application/vnd.pypi.simple.v1+json"


def build_index(
    resolved: dict[str, ResolvedPackage],
    output_dir: Path,
    base_url: str = "",
) -> None:
    """
    Write the full PEP 691 JSON index to output_dir/simple/.

    Directory layout:
        simple/
            index.json          <- project list  (GET /simple/)
            <project>/
                index.json      <- project detail (GET /simple/<project>/)
        files/
            *.whl               <- downloaded wheels
    """
    simple_dir = output_dir / "simple"
    simple_dir.mkdir(parents=True, exist_ok=True)
    files_dir = output_dir / "files"

    base_url = base_url.rstrip("/")

    # --- Project list ---
    project_list = {
        "meta": {"api-version": API_VERSION},
        "projects": [
            {"name": pkg.name}
            for pkg in sorted(resolved.values(), key=lambda p: p.name)
        ],
    }
    _write_json(simple_dir / "index.json", project_list)
    log.info("Wrote project list (%d packages)", len(resolved))

    # --- Per-project detail pages ---
    for pkg in resolved.values():
        _write_project_detail(pkg, simple_dir, files_dir, base_url)


def _write_project_detail(
    pkg: ResolvedPackage,
    simple_dir: Path,
    files_dir: Path,
    base_url: str,
) -> None:
    canonical = canonicalize_name(pkg.name)
    project_dir = simple_dir / canonical
    project_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for wheel in pkg.release.wheels:
        wheel_path = files_dir / wheel.filename
        file_entry = _build_file_entry(wheel, wheel_path, base_url, pkg.needs_wheels)
        if file_entry:
            files.append(file_entry)

    detail = {
        "meta": {"api-version": API_VERSION},
        "name": canonical,
        "files": files,
    }
    _write_json(project_dir / "index.json", detail)
    log.debug("Wrote detail for %s (%d files)", canonical, len(files))


def _build_file_entry(
    wheel: WheelFile,
    wheel_path: Path,
    base_url: str,
    needs_wheels: bool,
) -> dict | None:
    """Build a single file entry for the PEP 691 project detail page."""
    if needs_wheels and wheel_path.exists():
        # Serve from local mirror
        url = (
            f"{base_url}/files/{wheel.filename}"
            if base_url
            else f"/files/{wheel.filename}"
        )
        sha256 = _sha256_file(wheel_path)
    elif not needs_wheels:
        # Metadata-only: point directly at PyPI so uv can resolve but won't
        # need to actually download unless it's targeting this platform.
        url = wheel.url
        sha256 = wheel.sha256
    else:
        # needs_wheels=True but file wasn't downloaded (wrong platform/python)
        # Don't include it — it's not useful and we don't have it.
        return None

    entry: dict = {
        "filename": wheel.filename,
        "url": url,
        "hashes": {"sha256": sha256} if sha256 else {},
    }
    if wheel.requires_python:
        entry["requires-python"] = wheel.requires_python

    return entry


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
