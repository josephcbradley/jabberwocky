"""Download wheels and .metadata sidecars for target platforms."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import httpx

from .pypi import ResolvedPackage, WheelFile

log = logging.getLogger(__name__)


async def download_wheels(
    resolved: dict[str, ResolvedPackage],
    output_dir: Path,
    python_versions: list[str],
    platforms: list[str],
    concurrency: int = 5,
) -> None:
    """
    Download all wheels that match the target platforms/python versions.

    For packages with needs_wheels=False, nothing is downloaded — their
    presence in the index is enough for global resolvability.
    """
    files_dir = output_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        tasks = []
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
                tasks.append(_download_one(client, sem, wheel, dest))

        if tasks:
            await asyncio.gather(*tasks)


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
) -> None:
    async with sem:
        log.info("Downloading %s", wheel.filename)
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
                    return
                tmp.rename(dest)
                log.info("Saved %s", dest.name)
        except Exception as e:
            log.error("Failed to download %s: %s", wheel.filename, e)
