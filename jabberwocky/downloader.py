"""Download wheels and .metadata sidecars for target platforms."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from .pypi import ResolvedPackage, WheelFile

log = logging.getLogger(__name__)


@dataclass
class DownloadStatus:
    filename: str
    downloaded: int
    total: int | None
    complete: bool = False


class _Progress:
    """Multi-line, in-place progress display for parallel downloads."""

    def __init__(self, total_files: int, concurrency: int) -> None:
        self.total_files = total_files
        self.completed_files = 0
        self.concurrency = concurrency
        self._is_tty = sys.stderr.isatty()
        self._bar_width = 20
        self._lock = asyncio.Lock()
        # Map slot index to current status
        self._slots: list[DownloadStatus | None] = [None] * concurrency
        self._first_render = True

    def _render_bar(self, downloaded: int, total: int | None) -> str:
        if total is None or total <= 0:
            return "[" + "?" * self._bar_width + "]"
        filled = int(self._bar_width * downloaded / total)
        bar = "=" * filled + " " * (self._bar_width - filled)
        return f"[{bar}]"

    def _format_size(self, size: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size = size / 1024
        return f"{size:.1f}TB"

    def _render_line(self, status: DownloadStatus | None) -> str:
        term_width = shutil.get_terminal_size((80, 20)).columns
        if status is None:
            return " " * (term_width - 1)

        bar = self._render_bar(status.downloaded, status.total)
        size_str = self._format_size(status.downloaded)
        if status.total:
            size_str += f"/{self._format_size(status.total)}"

        #prefix = f"Downloading {status.filename}"
        suffix = f" {bar} {size_str}"

        max_name = term_width - len(suffix) - 13  # "Downloading "
        name = status.filename
        if max_name > 0 and len(name) > max_name:
            name = name[: max_name - 1] + "…"

        line = f"Downloading {name}{suffix}"
        return line.ljust(term_width - 1)

    async def update(
        self,
        slot: int,
        filename: str,
        downloaded: int,
        total: int | None,
        complete: bool = False,
    ) -> None:
        if not self._is_tty:
            if complete:
                async with self._lock:
                    self.completed_files += 1
                    # In non-TTY, we'll just log every 10% or so to avoid spam
                    if (
                        self.completed_files % max(1, self.total_files // 10) == 0
                        or self.completed_files == self.total_files
                    ):
                        sys.stderr.write(
                            f"Progress: {self.completed_files}/{self.total_files} files downloaded\n"
                        )
                        sys.stderr.flush()
            return

        async with self._lock:
            if complete:
                self.completed_files += 1
                self._slots[slot] = None
            else:
                self._slots[slot] = DownloadStatus(filename, downloaded, total)

            # Move cursor to the start of the progress block
            if not self._first_render:
                sys.stderr.write(f"\033[{self.concurrency + 1}A")
            self._first_render = False

            # Render overall progress
            term_width = shutil.get_terminal_size((80, 20)).columns
            overall_bar = self._render_bar(self.completed_files, self.total_files)
            overall_line = f"Overall Progress: {overall_bar} {self.completed_files}/{self.total_files} files"
            sys.stderr.write(overall_line.ljust(term_width - 1) + "\n")

            # Render each slot
            for i in range(self.concurrency):
                sys.stderr.write(self._render_line(self._slots[i]) + "\n")
            sys.stderr.flush()

    def finish(self) -> None:
        if self._is_tty:
            # Move cursor past the progress block
            # (It's already there after the last render)
            sys.stderr.write("\n")
            sys.stderr.flush()
        else:
            sys.stderr.write(
                f"Downloaded {self.completed_files}/{self.total_files} wheels\n"
            )
            sys.stderr.flush()


async def download_wheels(
    resolved: dict[str, ResolvedPackage],
    output_dir: Path,
    python_versions: list[str],
    platforms: list[str],
    concurrency: int = 4,
) -> None:
    """
    Download all wheels that match the target platforms/python versions.
    """
    files_dir = output_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # Use a slightly smaller default concurrency for better UI
    sem = asyncio.Semaphore(concurrency)

    pending: list[tuple[WheelFile, Path]] = []
    for pkg in resolved.values():
        if not pkg.needs_wheels:
            continue

        wanted_wheels = [
            w
            for w in pkg.release.wheels
            if _wheel_wanted(w, python_versions, platforms)
        ]

        if not wanted_wheels and pkg.release.wheels:
            log.warning(
                "No platform-matching wheels for %s; downloading fallbacks for offline support.",
                pkg.name,
            )
            python_matches = [
                w
                for w in pkg.release.wheels
                if any(w.matches_python(pv) for pv in python_versions)
            ]
            if python_matches:
                wanted_wheels = python_matches
            else:
                wanted_wheels = pkg.release.wheels

        for wheel in wanted_wheels:
            dest = files_dir / wheel.filename
            if dest.exists():
                continue
            pending.append((wheel, dest))

    if not pending:
        return

    progress = _Progress(total_files=len(pending), concurrency=concurrency)

    # We need a way to assign slots to workers
    slot_queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(concurrency):
        slot_queue.put_nowait(i)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        tasks = [
            _download_one(client, sem, wheel, dest, progress, slot_queue)
            for wheel, dest in pending
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
    slot_queue: asyncio.Queue[int],
) -> None:
    async with sem:
        slot = await slot_queue.get()
        try:
            await progress.update(slot, wheel.filename, 0, None)
            async with client.stream("GET", wheel.url) as resp:
                resp.raise_for_status()
                total_size = int(resp.headers.get("Content-Length", 0)) or None
                hasher = hashlib.sha256()
                tmp = dest.with_suffix(".tmp")
                downloaded = 0

                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        f.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        await progress.update(
                            slot, wheel.filename, downloaded, total_size
                        )

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
        except Exception as e:
            log.error("Failed to download %s: %s", wheel.filename, e)
        finally:
            await progress.update(slot, wheel.filename, 0, None, complete=True)
            slot_queue.put_nowait(slot)
