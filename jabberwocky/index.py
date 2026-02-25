"""PEP 691 JSON and PEP 503 HTML index generator."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Iterable, Any

from packaging.utils import canonicalize_name

from .pypi import ResolvedPackage, WheelFile

log = logging.getLogger(__name__)

API_VERSION = "1.0"
CONTENT_TYPE_JSON = "application/vnd.pypi.simple.v1+json"
CONTENT_TYPE_HTML = "text/html"


def build_index(
    resolved: Iterable[ResolvedPackage] | dict[str, ResolvedPackage],
    output_dir: Path,
    base_url: str = "",
) -> None:
    """
    Write the full PEP 691 JSON and PEP 503 HTML index to output_dir/simple/.

    Directory layout:
        simple/
            index.json          <- project list (JSON)
            index.html          <- project list (HTML)
            <project>/
                index.json      <- project detail (JSON)
                index.html      <- project detail (HTML)
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
    _write_project_list_html(simple_dir / "index.html", by_name.keys())
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
    # Protect against path traversal (e.g. if canonicalize_name preserves absolute paths)
    simple_dir = simple_dir.resolve()
    project_dir = (simple_dir / name).resolve()
    if not project_dir.is_relative_to(simple_dir):
        raise ValueError(
            f"Security violation: Package name '{name}' resolves to '{project_dir}' "
            f"which is outside '{simple_dir}'"
        )

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
    _write_project_detail_html(project_dir / "index.html", name, files)
    log.debug("Wrote detail for %s (%d files)", name, len(files))


def _build_file_entry(
    wheel: WheelFile,
    wheel_path: Path,
    base_url: str,
    needs_wheels: bool,
) -> dict[str, Any] | None:
    """Build a single file entry for the PEP 691 project detail page."""
    if needs_wheels and wheel_path.exists():
        # Serve from local mirror
        # Use relative paths if no base_url is provided (for file:// support)
        url = (
            f"{base_url}/files/{wheel.filename}"
            if base_url
            else f"../../files/{wheel.filename}"
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

    entry: dict[str, Any] = {
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


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_project_list_html(path: Path, projects: Iterable[str]) -> None:
    """Write PEP 503 HTML project list."""
    lines = ["<!DOCTYPE html>", "<html>", "<body>"]
    for p in sorted(projects):
        # Trailing slash is important for relative links to work correctly
        lines.append(f'<a href="{p}/">{p}</a><br>')
    lines.append("</body></html>")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_project_detail_html(
    path: Path, name: str, files: list[dict[str, Any]]
) -> None:
    """Write PEP 503 HTML project detail page."""
    lines = ["<!DOCTYPE html>", "<html>", "<body>", f"<h1>{name}</h1>"]
    for f in files:
        url = f["url"]
        fname = f["filename"]
        hash_part = ""
        if "hashes" in f and "sha256" in f["hashes"]:
            hash_part = f"#sha256={f['hashes']['sha256']}"

        attrs = []
        if "requires-python" in f:
            attrs.append(f'data-requires-python="{f["requires-python"]}"')

        attr_str = " " + " ".join(attrs) if attrs else ""
        lines.append(f'<a href="{url}{hash_part}"{attr_str}>{fname}</a><br>')
    lines.append("</body></html>")
    path.write_text("\n".join(lines), encoding="utf-8")
