"""PEP 691 JSON index generator."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Iterable

from packaging.utils import canonicalize_name

from .pypi import ResolvedPackage, WheelFile

log = logging.getLogger(__name__)

API_VERSION = "1.0"
CONTENT_TYPE_JSON = "application/vnd.pypi.simple.v1+json"


def build_index(
    resolved: Iterable[ResolvedPackage] | dict[str, ResolvedPackage],
    output_dir: Path,
    base_url: str = "",
) -> None:
    """
    Write the PEP 691 JSON index and PEP 503 HTML index to output_dir/simple/.

    Directory layout:
        simple/
            index.json          <- project list JSON (GET /simple/)
            index.html          <- project list HTML
            <project>/
                index.json      <- project detail JSON (GET /simple/<project>/)
                index.html      <- project detail HTML
        files/
            *.whl               <- downloaded wheels
    """
    simple_dir = output_dir / "simple"
    simple_dir.mkdir(parents=True, exist_ok=True)
    files_dir = output_dir / "files"
    base_url = base_url.rstrip("/")

    # Normalize input to list of packages
    if isinstance(resolved, dict):
        packages_iter = resolved.values()
    else:
        packages_iter = resolved

    # Group by canonical name to support multiple versions per package
    by_name: dict[str, list[ResolvedPackage]] = {}
    for pkg in packages_iter:
        canonical = canonicalize_name(pkg.name)
        if canonical not in by_name:
            by_name[canonical] = []
        by_name[canonical].append(pkg)

    # --- Project list ---
    project_list = {
        "meta": {"api-version": API_VERSION},
        "projects": [{"name": name} for name in sorted(by_name.keys())],
    }
    _write_json(simple_dir / "index.json", project_list)
    _write_html(simple_dir / "index.html", project_list, is_root=True)
    log.info("Wrote project list (%d packages)", len(by_name))

    # --- Per-project detail pages ---
    for name, pkgs in by_name.items():
        _write_project_detail(name, pkgs, simple_dir, files_dir, base_url)


def _write_project_detail(
    name: str,
    pkgs: list[ResolvedPackage],
    simple_dir: Path,
    files_dir: Path,
    base_url: str,
) -> None:
    project_dir = simple_dir / name
    project_dir.mkdir(parents=True, exist_ok=True)

    files = []
    # Collect wheels from all versions/packages for this name
    seen_files = set()
    for pkg in pkgs:
        for wheel in pkg.release.wheels:
            if wheel.filename in seen_files:
                continue
            seen_files.add(wheel.filename)

            wheel_path = files_dir / wheel.filename
            file_entry = _build_file_entry(
                wheel, wheel_path, base_url, pkg.needs_wheels
            )
            if file_entry:
                files.append(file_entry)

    # Sort files by filename for stable output
    files.sort(key=lambda x: x["filename"])

    detail = {
        "meta": {"api-version": API_VERSION},
        "name": name,
        "files": files,
    }
    _write_json(project_dir / "index.json", detail)
    _write_html(project_dir / "index.html", detail, is_root=False)
    log.debug("Wrote detail for %s (%d files)", name, len(files))


def _build_file_entry(
    wheel: WheelFile,
    wheel_path: Path,
    base_url: str,
    needs_wheels: bool,
) -> dict | None:
    """Build a single file entry for the PEP 691 project detail page."""
    if needs_wheels and wheel_path.exists():
        # Serve from local mirror
        if base_url:
            url = f"{base_url}/files/{wheel.filename}"
        else:
            # Use relative path for portability (e.g. file:// usage)
            # From simple/<project>/ to files/<wheel>
            url = f"../../files/{wheel.filename}"
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


def _write_html(path: Path, data: dict, is_root: bool = False) -> None:
    """Write a simple PEP 503 HTML index."""
    lines = ["<!DOCTYPE html>", "<html>", "<body>"]
    if is_root:
        # Project list
        for proj in data.get("projects", []):
            name = proj["name"]
            lines.append(f'<a href="{name}/">{name}</a><br>')
    else:
        # Project detail
        for f in data.get("files", []):
            url = f["url"]
            filename = f["filename"]

            # Construct anchor attributes
            attrs = []

            # Append hash fragment if available and not already in URL
            sha256 = f.get("hashes", {}).get("sha256")
            if sha256 and "#" not in url:
                url_with_hash = f"{url}#sha256={sha256}"
                attrs.append(f'href="{url_with_hash}"')
            else:
                attrs.append(f'href="{url}"')

            if "requires-python" in f:
                attrs.append(f'data-requires-python="{f["requires-python"]}"')

            lines.append(f'<a {" ".join(attrs)}>{filename}</a><br>')

    lines.append("</body>")
    lines.append("</html>")
    path.write_text("\n".join(lines), encoding="utf-8")
