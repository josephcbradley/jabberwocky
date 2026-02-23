
import sys
from unittest.mock import MagicMock

# Mock external dependencies
if "httpx" not in sys.modules:
    sys.modules["httpx"] = MagicMock()

if "packaging" not in sys.modules:
    mock_packaging = MagicMock()
    mock_packaging.markers.Marker = MagicMock()
    mock_packaging.requirements.Requirement = MagicMock()
    mock_packaging.utils.canonicalize_name = lambda x: x
    sys.modules["packaging"] = mock_packaging
    sys.modules["packaging.markers"] = mock_packaging.markers
    sys.modules["packaging.requirements"] = mock_packaging.requirements
    sys.modules["packaging.utils"] = mock_packaging.utils

import pytest
import asyncio
from unittest.mock import patch
from pathlib import Path

# Now import the code under test
try:
    from jabberwocky.pypi import WheelFile, PackageRelease, ResolvedPackage
    from jabberwocky.downloader import download_wheels
except ImportError:
    pass

def test_path_traversal_prevention(tmp_path):
    """Test that wheels with path traversal filenames are skipped."""
    async def _run_test():
        # Setup
        malicious_filename = "../../../tmp/evil-1.0-py3-none-any.whl"

        wheel = WheelFile(
            filename=malicious_filename,
            url="http://example.com/evil.whl",
            sha256="",
            requires_python=None,
            python_tags=["py3"],
            abi_tags=["none"],
            platform_tags=["any"]
        )

        pkg_release = PackageRelease(
            name="evil",
            version="1.0",
            wheels=[wheel]
        )

        resolved_pkg = ResolvedPackage(
            name="evil",
            version="1.0",
            release=pkg_release,
            needs_wheels=True
        )

        resolved = {"evil": resolved_pkg}
        output_dir = tmp_path / "output"

        # Run
        # We mock _download_one so we don't need real network
        # We also verify that it is NOT called
        with patch("jabberwocky.downloader._download_one") as mock_download:
            await download_wheels(
                resolved,
                output_dir,
                python_versions=["3.12"],
                platforms=["any"]
            )

            # Verify
            assert not mock_download.called, "_download_one should not be called for malicious filename"

    asyncio.run(_run_test())

def test_valid_filename_is_processed(tmp_path):
    """Test that wheels with valid filenames are processed."""
    async def _run_test():
        # Setup
        valid_filename = "good-1.0-py3-none-any.whl"

        wheel = WheelFile(
            filename=valid_filename,
            url="http://example.com/good.whl",
            sha256="",
            requires_python=None,
            python_tags=["py3"],
            abi_tags=["none"],
            platform_tags=["any"]
        )

        pkg_release = PackageRelease(
            name="good",
            version="1.0",
            wheels=[wheel]
        )

        resolved_pkg = ResolvedPackage(
            name="good",
            version="1.0",
            release=pkg_release,
            needs_wheels=True
        )

        resolved = {"good": resolved_pkg}
        output_dir = tmp_path / "output"

        # Run
        with patch("jabberwocky.downloader._download_one") as mock_download:
            await download_wheels(
                resolved,
                output_dir,
                python_versions=["3.12"],
                platforms=["any"]
            )

            # Verify
            assert mock_download.called, "_download_one should be called for valid filename"

    asyncio.run(_run_test())
