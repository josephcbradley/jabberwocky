"""Download wheels and .metadata sidecars for target platforms."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import sys
from pathlib import Path

import httpx

from .pypi import ResolvedPackage, WheelFile

log = logging.getLogger(__name__)


class _Progress:
    """Single-line, in-place progress display (no external dependencies)."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.done = 0
        self._current = ""
        self._lock = asyncio.Lock()
        self._is_tty = sys.stderr.isatty()
        self._bar_width = 20

    def _render(self) -> str:
        filled = int(self._bar_width * self.done / max(self.total, 1))
        bar = "=" * filled + " " * (self._bar_width - filled)
        # Truncate long filenames to keep the line tidy
        name = self._current
        term_width = shutil.get_terminal_size((80, 20)).columns
        prefix = f"Downloading {name}"
        suffix = f" [{bar}] {self.done}/{self.total}"
        # Trim name if it won't fit
        max_name = term_width - len(suffix) - len("Downloading ")
        if max_name > 0 and len(name) > max_name:
            name = name[: max_name - 1] + "…"
            prefix = f"Downloading {name}"
        return prefix + suffix

    async def update(self, filename: str, *, completed: bool) -> None:
        async with self._lock:
            if completed:
                self.done += 1
            self._current = filename
            if self._is_tty:
                line = self._render()
                sys.stderr.write(f"\r{line}")
                sys.stderr.flush()

    def finish(self) -> None:
        if self._is_tty:
            sys.stderr.write("\n")
            sys.stderr.flush()
        else:
            # Non-TTY: emit a single summary line
            sys.stderr.write(f"Downloaded {self.done}/{self.total} wheels\n")
            sys.stderr.flush()


async def download_wheels(
    resolved: dict[str, ResolvedPackage],
    output_dir: Path,
    python_versions: list[str],
    platforms: list[str],
    concurrency: int = 10,
) -> None:
    """
    Download all wheels that match the target platforms/python versions.

    For packages with needs_wheels=False, nothing is downloaded — their
    presence in the index is enough for global resolvability.
    """
    files_dir = output_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(concurrency)

    # Build the task list first so we know the total count
    pending: list[tuple[WheelFile, Path]] = []
    for pkg in resolved.values():
        if not pkg.needs_wheels:
            log.debug("Skipping wheels for %s (metadata only)", pkg.name)
            continue
        for wheel in pkg.release.wheels:
            if not _wheel_wanted(wheel, python_versions, platforms):
                continue
            dest = files_dir / wheel.filename
            if dest.exists():
                log.debug("Already have %s", wheel.filename)
                continue
            pending.append((wheel, dest))

    if not pending:
        return

    progress = _Progress(total=len(pending))

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        tasks = [
            _download_one(client, sem, wheel, dest, progress) for wheel, dest in pending
        ]
        await asyncio.gather(*tasks)

    progress.finish()


def _wheel_wanted(
    wheel: WheelFile, python_versions: list[str], platforms: list[str]
) -> bool:
    """Return True if the wheel is useful for any target python+platform combo."""
    python_ok = any(wheel.matches_python(pv) for pv in python_versions)
    platform_ok = any(wheel.matches_platform(pl) for pl in platforms)
    return python_ok and platform_ok


async def _download_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    wheel: WheelFile,
    dest: Path,
    progress: _Progress,
) -> None:
    async with sem:
        await progress.update(wheel.filename, completed=False)
        try:
            async with client.stream("GET", wheel.url) as resp:
                resp.raise_for_status()
                hasher = hashlib.sha256()
                tmp = dest.with_suffix(".tmp")
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        f.write(chunk)
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                if wheel.sha256 and digest != wheel.sha256:
                    tmp.unlink(missing_ok=True)
                    log.error(
                        "Hash mismatch for %s: expected %s, got %s",
                        wheel.filename,
                        wheel.sha256,
                        digest,
                    )
                else:
                    tmp.rename(dest)
                    log.debug("Saved %s", dest.name)
        except Exception as e:
            log.error("Failed to download %s: %s", wheel.filename, e)
        finally:
            await progress.update(wheel.filename, completed=True)
