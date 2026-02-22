"""Configuration schema for Jabberwocky mirrors."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Top-level mirror configuration."""

    # Packages the user explicitly wants mirrored
    packages: list[str]

    # Target Python versions, e.g. ["3.11", "3.12"]
    python_versions: list[str]

    # Target platforms in wheel tag format, e.g. ["linux_x86_64", "win_amd64"]
    platforms: list[str]

    # Where to store downloaded wheels and generated index files
    output_dir: Path = field(default_factory=lambda: Path("mirror"))

    # PyPI Simple API base URL
    index_url: str = "https://pypi.org/simple"

    # PyPI JSON API base URL (used for dependency resolution)
    pypi_url: str = "https://pypi.org/pypi"

    @classmethod
    def from_toml(cls, path: Path) -> "Config":
        with open(path, "rb") as f:
            data = tomllib.load(f)

        mirror = data.get("mirror", {})
        return cls(
            packages=mirror.get("packages", []),
            python_versions=mirror.get("python_versions", []),
            platforms=mirror.get("platforms", []),
            output_dir=Path(mirror.get("output_dir", "mirror")),
            index_url=mirror.get("index_url", "https://pypi.org/simple"),
            pypi_url=mirror.get("pypi_url", "https://pypi.org/pypi"),
        )

    @classmethod
    def from_wishlist(
        cls, wishlist: Path, python_versions: list[str], platforms: list[str]
    ) -> "Config":
        """Build a Config from a plain wishlist file (one package per line)."""
        packages = [
            line.strip()
            for line in wishlist.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        return cls(
            packages=packages, python_versions=python_versions, platforms=platforms
        )
