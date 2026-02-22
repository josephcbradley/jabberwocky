"""Tests for jabberwocky.config — Config loading from TOML and wishlist files."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from jabberwocky.config import Config


class TestConfigFromToml:
    def test_loads_all_fields(self, tmp_path: Path):
        cfg_file = tmp_path / "jabberwocky.toml"
        cfg_file.write_bytes(
            tomli_w.dumps(
                {
                    "mirror": {
                        "packages": ["polars", "scipy"],
                        "python_versions": ["3.11", "3.12"],
                        "platforms": ["linux_x86_64", "win_amd64"],
                        "output_dir": "my_mirror",
                        "pypi_url": "https://my-pypi.internal/pypi",
                        "index_url": "https://my-pypi.internal/simple",
                    }
                }
            ).encode()
        )
        cfg = Config.from_toml(cfg_file)
        assert cfg.packages == ["polars", "scipy"]
        assert cfg.python_versions == ["3.11", "3.12"]
        assert cfg.platforms == ["linux_x86_64", "win_amd64"]
        assert cfg.output_dir == Path("my_mirror")
        assert cfg.pypi_url == "https://my-pypi.internal/pypi"
        assert cfg.index_url == "https://my-pypi.internal/simple"

    def test_defaults_applied_for_missing_fields(self, tmp_path: Path):
        cfg_file = tmp_path / "jabberwocky.toml"
        cfg_file.write_bytes(
            tomli_w.dumps(
                {
                    "mirror": {
                        "packages": ["click"],
                        "python_versions": ["3.12"],
                        "platforms": ["linux_x86_64"],
                    }
                }
            ).encode()
        )
        cfg = Config.from_toml(cfg_file)
        assert cfg.output_dir == Path("mirror")
        assert cfg.pypi_url == "https://pypi.org/pypi"
        assert cfg.index_url == "https://pypi.org/simple"

    def test_empty_mirror_section_gives_empty_lists(self, tmp_path: Path):
        cfg_file = tmp_path / "jabberwocky.toml"
        cfg_file.write_bytes(tomli_w.dumps({"mirror": {}}).encode())
        cfg = Config.from_toml(cfg_file)
        assert cfg.packages == []
        assert cfg.python_versions == []
        assert cfg.platforms == []

    def test_missing_mirror_section_gives_defaults(self, tmp_path: Path):
        cfg_file = tmp_path / "jabberwocky.toml"
        cfg_file.write_bytes(tomli_w.dumps({}).encode())
        cfg = Config.from_toml(cfg_file)
        assert cfg.packages == []


class TestConfigFromWishlist:
    def test_loads_packages(self, tmp_path: Path):
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("polars\nscipy\nautograd\n")
        cfg = Config.from_wishlist(wishlist, ["3.12"], ["linux_x86_64"])
        assert cfg.packages == ["polars", "scipy", "autograd"]

    def test_strips_blank_lines(self, tmp_path: Path):
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("polars\n\nscipy\n\n")
        cfg = Config.from_wishlist(wishlist, ["3.12"], ["linux_x86_64"])
        assert cfg.packages == ["polars", "scipy"]

    def test_strips_comment_lines(self, tmp_path: Path):
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("# Core data stack\npolars\n# ML\nscipy\n")
        cfg = Config.from_wishlist(wishlist, ["3.12"], ["linux_x86_64"])
        assert cfg.packages == ["polars", "scipy"]

    def test_comment_line_with_leading_whitespace_not_stripped(self, tmp_path: Path):
        # Only lines starting with '#' are comments; leading whitespace is
        # NOT stripped from non-comment lines (package names don't have it in practice)
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("polars\n  scipy\n")
        cfg = Config.from_wishlist(wishlist, ["3.12"], ["linux_x86_64"])
        # strip() is called on each line, so "  scipy" becomes "scipy"
        assert "scipy" in cfg.packages

    def test_python_versions_stored(self, tmp_path: Path):
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("click\n")
        cfg = Config.from_wishlist(wishlist, ["3.11", "3.12"], ["linux_x86_64"])
        assert cfg.python_versions == ["3.11", "3.12"]

    def test_platforms_stored(self, tmp_path: Path):
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("click\n")
        cfg = Config.from_wishlist(wishlist, ["3.12"], ["linux_x86_64", "win_amd64"])
        assert cfg.platforms == ["linux_x86_64", "win_amd64"]

    def test_default_output_dir(self, tmp_path: Path):
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("click\n")
        cfg = Config.from_wishlist(wishlist, ["3.12"], ["linux_x86_64"])
        assert cfg.output_dir == Path("mirror")

    def test_empty_wishlist(self, tmp_path: Path):
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("")
        cfg = Config.from_wishlist(wishlist, ["3.12"], ["linux_x86_64"])
        assert cfg.packages == []

    def test_only_comments_gives_empty_packages(self, tmp_path: Path):
        wishlist = tmp_path / "wishlist.txt"
        wishlist.write_text("# just comments\n# nothing here\n")
        cfg = Config.from_wishlist(wishlist, ["3.12"], ["linux_x86_64"])
        assert cfg.packages == []
