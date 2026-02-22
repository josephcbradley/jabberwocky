"""Incremental mirror update logic.

Workflow
--------
1. Run a fresh resolve + download into a temporary staging directory.
2. Archive the current mirror/ into archives/<timestamp>/.
3. Diff staging vs current mirror:
   - added_wheels   : in staging/files/ but not in mirror/files/
   - removed_wheels : in mirror/files/ but not in staging/files/
   - changed_index  : index JSON files whose content changed
4. Apply staging to mirror/ (replace files/ and simple/ in place).
5. Write diffs/<timestamp>/ containing only the added/changed files
   plus a machine-readable manifest and a human-readable APPLY.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .pypi import reconstruct_package_from_wheel

log = logging.getLogger(__name__)

_TS_FMT = "%Y%m%dT%H%M%SZ"


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def archive_mirror(mirror_dir: Path, archives_dir: Path, timestamp: str) -> Path:
    """Copy the current mirror snapshot into archives/<timestamp>/."""
    dest = archives_dir / timestamp
    if mirror_dir.exists():
        log.debug("Archiving current mirror to %s", dest)
        shutil.copytree(mirror_dir, dest)
    else:
        log.debug("No existing mirror to archive.")
        dest.mkdir(parents=True, exist_ok=True)
    return dest


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def compute_diff(
    old_mirror: Path,
    new_mirror: Path,
) -> dict:
    """
    Compare old_mirror and new_mirror.

    Returns a dict:
        {
          "added_wheels":   [filename, ...],
          "removed_wheels": [filename, ...],
          "changed_index":  [relative_path, ...],   # relative to simple/
          "added_index":    [relative_path, ...],
        }
    """
    old_files = _wheel_sha_map(old_mirror / "files")
    new_files = _wheel_sha_map(new_mirror / "files")

    added_wheels = sorted(set(new_files) - set(old_files))
    removed_wheels = sorted(set(old_files) - set(new_files))

    old_index = _index_content_map(old_mirror / "simple")
    new_index = _index_content_map(new_mirror / "simple")

    changed_index = sorted(
        p for p in set(old_index) & set(new_index) if old_index[p] != new_index[p]
    )
    added_index = sorted(set(new_index) - set(old_index))

    return {
        "added_wheels": added_wheels,
        "removed_wheels": removed_wheels,
        "changed_index": changed_index,
        "added_index": added_index,
    }


def _wheel_sha_map(files_dir: Path) -> dict[str, str]:
    if not files_dir.exists():
        return {}
    return {p.name: _sha256(p) for p in files_dir.iterdir() if p.suffix == ".whl"}


def _index_content_map(simple_dir: Path) -> dict[str, str]:
    """Return {relative_path: sha256} for every index.json under simple_dir."""
    if not simple_dir.exists():
        return {}
    result = {}
    for p in simple_dir.rglob("index.json"):
        rel = str(p.relative_to(simple_dir))
        result[rel] = _sha256(p)
    return result


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_update(new_mirror: Path, mirror_dir: Path) -> None:
    """Replace mirror_dir contents with new_mirror contents."""
    log.debug("Applying update to %s", mirror_dir)
    if mirror_dir.exists():
        shutil.rmtree(mirror_dir)
    shutil.copytree(new_mirror, mirror_dir)


# ---------------------------------------------------------------------------
# Diff package
# ---------------------------------------------------------------------------


def write_diff_package(
    new_mirror: Path,
    diff: dict,
    diffs_dir: Path,
    timestamp: str,
) -> Path:
    """
    Write a self-contained diff package to diffs/<timestamp>/.

    Contents:
        files/          <- added wheel files only
        simple/         <- added + changed index.json files only
        manifest.json   <- machine-readable summary
        APPLY.md        <- human-readable apply instructions
    """
    diff_dir = diffs_dir / timestamp
    diff_dir.mkdir(parents=True, exist_ok=True)

    # Copy added wheels
    src_files = new_mirror / "files"
    dst_files = diff_dir / "files"
    if diff["added_wheels"]:
        dst_files.mkdir(parents=True, exist_ok=True)
        for name in diff["added_wheels"]:
            shutil.copy2(src_files / name, dst_files / name)

    # Copy added + changed index files
    src_simple = new_mirror / "simple"
    dst_simple = diff_dir / "simple"
    changed_or_added = diff["changed_index"] + diff["added_index"]
    if changed_or_added:
        for rel in changed_or_added:
            src = src_simple / rel
            dst = dst_simple / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Always include the top-level project list
    top_index_src = src_simple / "index.json"
    top_index_dst = dst_simple / "index.json"
    if top_index_src.exists() and not top_index_dst.exists():
        dst_simple.mkdir(parents=True, exist_ok=True)
        shutil.copy2(top_index_src, top_index_dst)

    # manifest.json
    manifest = {
        "timestamp": timestamp,
        "added_wheels": diff["added_wheels"],
        "removed_wheels": diff["removed_wheels"],
        "changed_index": diff["changed_index"],
        "added_index": diff["added_index"],
    }
    (diff_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    # APPLY.md
    _write_apply_md(diff_dir, diff, timestamp)

    return diff_dir


def _write_apply_md(diff_dir: Path, diff: dict, timestamp: str) -> None:
    added = len(diff["added_wheels"])
    removed = len(diff["removed_wheels"])
    changed = len(diff["changed_index"])
    added_idx = len(diff["added_index"])

    lines = [
        f"# Mirror update — {timestamp}",
        "",
        "## Summary",
        "",
        f"| Change | Count |",
        f"|--------|-------|",
        f"| Wheels added | {added} |",
        f"| Wheels removed | {removed} |",
        f"| Index entries updated | {changed} |",
        f"| Index entries added | {added_idx} |",
        "",
        "## Applying this update to the offline machine",
        "",
        "Transfer the entire `diffs/{ts}/` folder to the offline machine, "
        "then run the commands below from the directory that contains your `mirror/` folder.".format(
            ts=timestamp
        ),
        "",
        "```bash",
        f"DIFF=diffs/{timestamp}",
        "",
        "# 1. Copy new/updated wheel files",
        'cp -r "$DIFF/files/." mirror/files/',
        "",
        "# 2. Copy new/updated index entries",
        'cp -r "$DIFF/simple/." mirror/simple/',
        "",
    ]

    if diff["removed_wheels"]:
        lines += [
            "# 3. Remove wheels that are no longer in the mirror",
        ]
        for name in diff["removed_wheels"]:
            lines.append(f"rm -f mirror/files/{name}")
        lines.append("")

    lines += [
        "```",
        "",
        "## Removed wheels",
        "",
    ]

    if diff["removed_wheels"]:
        for name in diff["removed_wheels"]:
            lines.append(f"- `{name}`")
    else:
        lines.append("_(none)_")

    lines += [
        "",
        "## Added wheels",
        "",
    ]
    if diff["added_wheels"]:
        for name in diff["added_wheels"]:
            lines.append(f"- `{name}`")
    else:
        lines.append("_(none)_")

    (diff_dir / "APPLY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# High-level entry point used by CLI
# ---------------------------------------------------------------------------


def run_update(
    mirror_dir: Path,
    archives_dir: Path,
    diffs_dir: Path,
    resolve_fn,  # async callable(cfg) -> resolved dict
    download_fn,  # async callable(resolved, staging_dir, ...) -> None
    build_index_fn,  # callable(resolved, staging_dir) -> None
    cfg,
) -> tuple[Path, dict, dict]:
    """
    Orchestrate the full update pipeline synchronously.

    Returns the path to the newly created diff package.
    """
    import asyncio

    timestamp = _now_ts()

    staging_tmp = tempfile.mkdtemp(prefix="jabberwocky-staging-")
    staging = Path(staging_tmp)
    try:
        # 1. Build the new mirror state in a temp dir
        log.debug("Resolving dependencies...")

        async def _run():
            resolved = await resolve_fn(cfg)
            await download_fn(resolved, staging, cfg.python_versions, cfg.platforms)

            # --- Merge Logic: Preserve old versions ---
            files_dir = staging / "files"
            files_dir.mkdir(parents=True, exist_ok=True)
            existing_files = list((mirror_dir / "files").glob("*.whl"))

            # Track wheels covered by the new resolution
            known_wheels = {
                w.filename
                for pkg in resolved.values()
                if pkg.needs_wheels
                for w in pkg.release.wheels
            }

            all_pkgs = list(resolved.values())

            for path in existing_files:
                dest = files_dir / path.name
                if not dest.exists():
                    # Link or copy to staging to preserve the file
                    try:
                        os.link(path, dest)
                    except OSError:
                        shutil.copy2(path, dest)

                if path.name not in known_wheels:
                    # This is an old version; reconstruct a package object for indexing
                    pkg = reconstruct_package_from_wheel(dest)
                    if pkg:
                        all_pkgs.append(pkg)

            build_index_fn(all_pkgs, staging)
            return resolved

        resolved = asyncio.run(_run())

        # 2. Archive current mirror
        archive_mirror(mirror_dir, archives_dir, timestamp)

        # 3. Compute diff (old = current mirror, new = staging)
        diff = compute_diff(mirror_dir, staging)

        log.debug(
            "Diff: +%d wheels, -%d wheels, %d index changes, %d new index entries",
            len(diff["added_wheels"]),
            len(diff["removed_wheels"]),
            len(diff["changed_index"]),
            len(diff["added_index"]),
        )

        # 4. Write diff package (before we clobber mirror/)
        diff_dir = write_diff_package(staging, diff, diffs_dir, timestamp)

        # 5. Apply staging -> mirror
        apply_update(staging, mirror_dir)
    finally:
        shutil.rmtree(staging_tmp, ignore_errors=True)

    return diff_dir, diff, resolved
