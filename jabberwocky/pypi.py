"""PyPI client: fetch metadata and resolve transitive dependency graph."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

log = logging.getLogger(__name__)

# Wheel filename pattern per PEP 427
_WHEEL_RE = re.compile(
    r"^(?P<namever>(?P<name>.+?)-(?P<ver>\d.*?))"
    r"(-(?P<build>\d.*?))?-(?P<pyver>.+?)-(?P<abi>.+?)-(?P<plat>.+?)\.whl$"
)


@dataclass
class WheelFile:
    filename: str
    url: str
    sha256: str
    requires_python: str | None
    # Parsed wheel tags
    python_tags: list[str]  # e.g. ["cp311", "cp312", "py3"]
    abi_tags: list[str]  # e.g. ["cp311", "none"]
    platform_tags: list[str]  # e.g. ["linux_x86_64", "any"]

    @property
    def is_pure(self) -> bool:
        return "any" in self.platform_tags

    def matches_platform(self, platform: str) -> bool:
        if self.is_pure:
            return True
        return any(_platform_tag_matches(plat, platform) for plat in self.platform_tags)

    def matches_python(self, python_version: str) -> bool:
        """Check if wheel is compatible with a python version string like '3.11'."""
        major, minor = python_version.split(".")
        target_major = int(major)
        target_minor = int(minor)

        cp_tag = f"cp{major}{minor}"
        py_tag = f"py{major}"
        py_full_tag = f"py{major}{minor}"

        # 1. Exact tag match
        if any(
            t in (cp_tag, py_tag, py_full_tag, "py3", "cp3") for t in self.python_tags
        ):
            return True

        # 2. ABI3 compatibility (CPython only)
        # e.g. wheel tagged "cp36" with abi_tag "abi3" works on cp37, cp38, etc.
        if "abi3" in self.abi_tags:
            for tag in self.python_tags:
                # We currently only handle cp3x abi3 wheels
                if tag.startswith("cp3") and len(tag) > 3:
                    try:
                        wheel_minor = int(tag[3:])
                        # abi3 allows forward compatibility within the same major version
                        if target_major == 3 and target_minor >= wheel_minor:
                            return True
                    except ValueError:
                        continue

        return False


@dataclass
class PackageRelease:
    name: str
    version: str
    wheels: list[WheelFile]
    metadata_url: str | None = None  # .metadata sidecar if available


@dataclass
class ResolvedPackage:
    """A package in the resolved dependency graph."""

    name: str  # canonical name
    version: str
    release: PackageRelease
    # True = serve wheels for target platforms; False = metadata only
    needs_wheels: bool = True


def _platform_tag_matches(wheel_plat: str, target_plat: str) -> bool:
    """
    Loose platform tag compatibility check.

    Handles manylinux, musllinux, and exact matches.
    e.g. manylinux_2_17_x86_64 is compatible with linux_x86_64
    """
    if wheel_plat == target_plat:
        return True
    # manylinux compat: manylinux*_x86_64 matches linux_x86_64
    if target_plat.startswith("linux_"):
        arch = target_plat[len("linux_") :]
        if wheel_plat.startswith(("manylinux", "musllinux")) and wheel_plat.endswith(
            arch
        ):
            return True
    return False


def _parse_wheel_filename(filename: str) -> WheelFile | None:
    m = _WHEEL_RE.match(filename)
    if not m:
        return None
    return WheelFile(
        filename=filename,
        url="",  # filled in by caller
        sha256="",
        requires_python=None,
        python_tags=m.group("pyver").split("."),
        abi_tags=m.group("abi").split("."),
        platform_tags=m.group("plat").split("."),
    )


def _extract_wheels(release_files: list[dict[str, Any]]) -> list[WheelFile]:
    wheels = []
    for f in release_files:
        fname = f.get("filename", "")
        if not fname.endswith(".whl"):
            continue
        wf = _parse_wheel_filename(fname)
        if wf is None:
            continue
        wf.url = f.get("url", "")
        wf.sha256 = (f.get("digests") or {}).get("sha256", "")
        wf.requires_python = f.get("requires_python")
        wheels.append(wf)
    return wheels


def _eval_marker_for_any_target(
    marker: Marker,
    python_versions: list[str],
    platforms: list[str],
) -> bool:
    """
    Return True if the marker evaluates to True for ANY combination of
    target python version + platform.  Used to decide whether a
    conditional dependency is reachable at all.
    """
    # Map our platform strings to sys_platform values
    sys_platforms = set()
    for p in platforms:
        if p.startswith("linux"):
            sys_platforms.add("linux")
        elif p.startswith("win"):
            sys_platforms.add("win32")
        elif p.startswith("macos") or p.startswith("darwin"):
            sys_platforms.add("darwin")
        else:
            sys_platforms.add(p)

    for pyver in python_versions:
        major, minor = pyver.split(".")
        for sys_platform in sys_platforms:
            env = {
                "python_version": pyver,
                "python_full_version": f"{pyver}.0",
                "sys_platform": sys_platform,
                "os_name": "nt" if sys_platform == "win32" else "posix",
                "platform_machine": "",
                "platform_system": (
                    "Windows"
                    if sys_platform == "win32"
                    else "Darwin"
                    if sys_platform == "darwin"
                    else "Linux"
                ),
                "implementation_name": "cpython",
                "extra": "",
            }
            try:
                if marker.evaluate(env):
                    return True
            except Exception:
                return True  # be conservative: include if we can't evaluate
    return False


class PyPIClient:
    def __init__(self, pypi_url: str = "https://pypi.org/pypi", concurrency: int = 10):
        self.pypi_url = pypi_url.rstrip("/")
        self._sem = asyncio.Semaphore(concurrency)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PyPIClient":
        self._client = httpx.AsyncClient(timeout=30, follow_redirects=True)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch_release(
        self, name: str, version: str | None = None
    ) -> PackageRelease | None:
        """Fetch release metadata for a package (latest if version is None)."""
        canonical = canonicalize_name(name)
        if version:
            url = f"{self.pypi_url}/{canonical}/{version}/json"
        else:
            url = f"{self.pypi_url}/{canonical}/json"

        async with self._sem:
            try:
                assert self._client is not None
                resp = await self._client.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                log.warning("Failed to fetch %s: %s", url, e)
                return None
            except httpx.RequestError as e:
                log.warning("Request error for %s: %s", url, e)
                return None

        data = resp.json()
        info = data.get("info", {})
        release_version = info.get("version", version or "unknown")

        # PyPI JSON API puts files under data["urls"] for a specific version
        # or under data["releases"][version] for the top-level endpoint
        if version:
            files = data.get("urls", [])
        else:
            files = data.get("urls", [])

        wheels = _extract_wheels(files)

        return PackageRelease(
            name=canonical,
            version=release_version,
            wheels=wheels,
        )

    async def fetch_dependencies(self, name: str, version: str) -> list[Requirement]:
        """Return the install_requires for a specific release."""
        canonical = canonicalize_name(name)
        url = f"{self.pypi_url}/{canonical}/{version}/json"

        async with self._sem:
            try:
                assert self._client is not None
                resp = await self._client.get(url)
                resp.raise_for_status()
            except Exception as e:
                log.warning("Failed to fetch deps for %s==%s: %s", name, version, e)
                return []

        data = resp.json()
        requires_dist = data.get("info", {}).get("requires_dist") or []
        reqs = []
        for r in requires_dist:
            try:
                reqs.append(Requirement(r))
            except Exception:
                log.debug("Could not parse requirement %r", r)
        return reqs


async def resolve(
    packages: list[str],
    python_versions: list[str],
    platforms: list[str],
    pypi_url: str = "https://pypi.org/pypi",
    on_update: Callable[[list[str]], None] | None = None,
) -> dict[str, ResolvedPackage]:
    """
    Resolve the full transitive dependency graph for the given packages.

    Returns a dict of canonical_name -> ResolvedPackage.

    - Packages reachable on any target platform/python get needs_wheels=True.
    - Packages only reachable via markers that exclude all targets get
      needs_wheels=False (metadata only, for global resolvability).
    """
    resolved: dict[str, ResolvedPackage] = {}
    # queue entries: (name, version_or_None, needs_wheels)
    queue: list[tuple[str, str | None, bool]] = [(pkg, None, True) for pkg in packages]
    in_flight: set[str] = set()

    async with PyPIClient(pypi_url=pypi_url) as client:
        while queue:
            # Kick off all pending fetches concurrently
            tasks = []
            batch = []
            while queue:
                name, ver, needs_wheels = queue.pop()
                canonical = canonicalize_name(name)
                if canonical in resolved or canonical in in_flight:
                    # Already resolved — but if this pass needs wheels, upgrade
                    if canonical in resolved and needs_wheels:
                        resolved[canonical].needs_wheels = True
                    continue
                in_flight.add(canonical)
                batch.append((canonical, ver, needs_wheels))
                tasks.append(client.fetch_release(canonical, ver))

            if not tasks:
                break

            if on_update:
                on_update(sorted(in_flight))

            results = await asyncio.gather(*tasks)

            # Now fetch dependencies for each result
            dep_tasks = []
            dep_meta = []  # (canonical, needs_wheels)
            for (canonical, ver, needs_wheels), release in zip(batch, results):
                in_flight.discard(canonical)
                if release is None:
                    log.warning("Could not resolve %s", canonical)
                    continue

                resolved[canonical] = ResolvedPackage(
                    name=canonical,
                    version=release.version,
                    release=release,
                    needs_wheels=needs_wheels,
                )
                log.debug(
                    "Resolved %s==%s (wheels=%s)",
                    canonical,
                    release.version,
                    needs_wheels,
                )
                dep_tasks.append(client.fetch_dependencies(canonical, release.version))
                dep_meta.append((canonical, needs_wheels))

            dep_results = await asyncio.gather(*dep_tasks)

            for (canonical, parent_needs_wheels), deps in zip(dep_meta, dep_results):
                for req in deps:
                    dep_canonical = canonicalize_name(req.name)
                    if dep_canonical in resolved:
                        if parent_needs_wheels and _dep_reachable(
                            req, python_versions, platforms
                        ):
                            resolved[dep_canonical].needs_wheels = True
                        continue

                    # Determine if this dep is reachable on any target
                    dep_reachable = _dep_reachable(req, python_versions, platforms)

                    if dep_reachable and parent_needs_wheels:
                        dep_needs_wheels = True
                    else:
                        # Dep is either unreachable on targets, or parent is metadata-only.
                        # We still include it for global resolvability but don't serve wheels.
                        dep_needs_wheels = False

                    # Pin version if specified in requirement
                    pin = _extract_pin(req)
                    queue.append((req.name, pin, dep_needs_wheels))

    return resolved


def _dep_reachable(
    req: Requirement, python_versions: list[str], platforms: list[str]
) -> bool:
    """Return True if this dependency is reachable on any target environment."""
    if req.marker is None:
        return True
    return _eval_marker_for_any_target(req.marker, python_versions, platforms)


def _extract_pin(req: Requirement) -> str | None:
    """Extract an exact version pin from a requirement, if present."""
    for spec in req.specifier:
        if spec.operator in ("==",):
            return spec.version
    return None


def reconstruct_package_from_wheel(wheel_path: Path) -> ResolvedPackage | None:
    """Create a ResolvedPackage from a local wheel file."""
    wf = _parse_wheel_filename(wheel_path.name)
    if not wf:
        return None

    m = _WHEEL_RE.match(wheel_path.name)
    if not m:
        return None

    name = canonicalize_name(m.group("name"))
    version = m.group("ver")

    # URL and sha256 not needed here; build_index will compute hash from file
    return ResolvedPackage(
        name=name,
        version=version,
        release=PackageRelease(name=name, version=version, wheels=[wf]),
        needs_wheels=True,
    )
