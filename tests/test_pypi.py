"""Tests for jabberwocky.pypi — platform matching, marker evaluation, resolution logic."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from packaging.markers import Marker
from packaging.requirements import Requirement

from jabberwocky.downloader import _wheel_wanted
import httpx

from jabberwocky.pypi import (
    PackageRelease,
    PyPIClient,
    ResolvedPackage,
    WheelFile,
    _dep_reachable,
    _eval_marker_for_any_target,
    _extract_pin,
    _extract_wheels,
    _parse_wheel_filename,
    _platform_tag_matches,
)


# ---------------------------------------------------------------------------
# _platform_tag_matches
# ---------------------------------------------------------------------------


class TestPlatformTagMatches:
    def test_exact_match(self):
        assert _platform_tag_matches("linux_x86_64", "linux_x86_64")

    def test_exact_match_windows(self):
        assert _platform_tag_matches("win_amd64", "win_amd64")

    def test_manylinux_matches_linux(self):
        assert _platform_tag_matches("manylinux_2_17_x86_64", "linux_x86_64")

    def test_manylinux_old_style_matches_linux(self):
        assert _platform_tag_matches("manylinux1_x86_64", "linux_x86_64")

    def test_musllinux_matches_linux(self):
        assert _platform_tag_matches("musllinux_1_1_x86_64", "linux_x86_64")

    def test_manylinux_aarch64_matches_linux_aarch64(self):
        assert _platform_tag_matches("manylinux_2_17_aarch64", "linux_aarch64")

    def test_manylinux_does_not_match_wrong_arch(self):
        assert not _platform_tag_matches("manylinux_2_17_x86_64", "linux_aarch64")

    def test_linux_does_not_match_windows(self):
        assert not _platform_tag_matches("linux_x86_64", "win_amd64")

    def test_windows_does_not_match_linux(self):
        assert not _platform_tag_matches("win_amd64", "linux_x86_64")

    def test_no_match_completely_different(self):
        assert not _platform_tag_matches("macosx_11_0_arm64", "linux_x86_64")


# ---------------------------------------------------------------------------
# WheelFile.matches_platform
# ---------------------------------------------------------------------------


class TestWheelMatchesPlatform:
    def _make_wheel(self, platform_tags: list[str]) -> WheelFile:
        return WheelFile(
            filename="pkg-1.0-py3-none-any.whl",
            url="",
            sha256="",
            requires_python=None,
            python_tags=["py3"],
            abi_tags=["none"],
            platform_tags=platform_tags,
        )

    def test_pure_wheel_matches_any_platform(self):
        wheel = self._make_wheel(["any"])
        assert wheel.matches_platform("linux_x86_64")
        assert wheel.matches_platform("win_amd64")
        assert wheel.matches_platform("macosx_11_0_arm64")

    def test_linux_wheel_matches_linux(self):
        wheel = self._make_wheel(["linux_x86_64"])
        assert wheel.matches_platform("linux_x86_64")

    def test_linux_wheel_does_not_match_windows(self):
        wheel = self._make_wheel(["linux_x86_64"])
        assert not wheel.matches_platform("win_amd64")

    def test_manylinux_wheel_matches_linux_target(self):
        wheel = self._make_wheel(["manylinux_2_17_x86_64"])
        assert wheel.matches_platform("linux_x86_64")

    def test_wheel_with_multiple_platform_tags(self):
        # Some wheels ship with a dot-separated list of platform tags
        wheel = self._make_wheel(["manylinux_2_17_x86_64", "manylinux_2_28_x86_64"])
        assert wheel.matches_platform("linux_x86_64")

    def test_win_wheel_does_not_match_linux(self):
        wheel = self._make_wheel(["win_amd64"])
        assert not wheel.matches_platform("linux_x86_64")


# ---------------------------------------------------------------------------
# WheelFile.matches_python
# ---------------------------------------------------------------------------


class TestWheelMatchesPython:
    def _make_wheel(self, python_tags: list[str]) -> WheelFile:
        return WheelFile(
            filename="pkg-1.0-cp312-cp312-linux_x86_64.whl",
            url="",
            sha256="",
            requires_python=None,
            python_tags=python_tags,
            abi_tags=["cp312"],
            platform_tags=["linux_x86_64"],
        )

    def test_cpython_exact_match(self):
        wheel = self._make_wheel(["cp312"])
        assert wheel.matches_python("3.12")

    def test_cpython_no_match_different_minor(self):
        wheel = self._make_wheel(["cp311"])
        assert not wheel.matches_python("3.12")

    def test_py3_tag_matches_any_python3(self):
        wheel = self._make_wheel(["py3"])
        assert wheel.matches_python("3.11")
        assert wheel.matches_python("3.12")
        assert wheel.matches_python("3.13")

    def test_py_major_minor_tag(self):
        wheel = self._make_wheel(["py312"])
        assert wheel.matches_python("3.12")
        assert not wheel.matches_python("3.11")

    def test_multiple_python_tags(self):
        # Wheel supports cp311 and cp312
        wheel = self._make_wheel(["cp311", "cp312"])
        assert wheel.matches_python("3.11")
        assert wheel.matches_python("3.12")
        assert not wheel.matches_python("3.10")

    def test_cp3_tag_matches_any_cpython3(self):
        wheel = self._make_wheel(["cp3"])
        assert wheel.matches_python("3.11")
        assert wheel.matches_python("3.12")

    def test_abi3_compatibility(self):
        # cp36 abi3 wheel matches cp312
        wheel = WheelFile(
            filename="pkg-1.0-cp36-abi3-linux_x86_64.whl",
            url="",
            sha256="",
            requires_python=None,
            python_tags=["cp36"],
            abi_tags=["abi3"],
            platform_tags=["linux_x86_64"],
        )
        assert wheel.matches_python("3.12")
        assert wheel.matches_python("3.11")
        assert wheel.matches_python("3.6")
        # Should NOT match older python versions
        assert not wheel.matches_python("3.5")
        # Should NOT match Python 2
        assert not wheel.matches_python("2.7")


# ---------------------------------------------------------------------------
# _parse_wheel_filename
# ---------------------------------------------------------------------------


class TestParseWheelFilename:
    def test_standard_wheel(self):
        wf = _parse_wheel_filename("polars-1.0.0-cp312-cp312-linux_x86_64.whl")
        assert wf is not None
        assert wf.python_tags == ["cp312"]
        assert wf.abi_tags == ["cp312"]
        assert wf.platform_tags == ["linux_x86_64"]

    def test_pure_wheel(self):
        wf = _parse_wheel_filename("click-8.1.0-py3-none-any.whl")
        assert wf is not None
        assert wf.python_tags == ["py3"]
        assert wf.abi_tags == ["none"]
        assert wf.platform_tags == ["any"]
        assert wf.is_pure

    def test_multi_tag_wheel(self):
        # Wheels with dot-separated tags (e.g. cp311.cp312)
        wf = _parse_wheel_filename("pkg-1.0-cp311.cp312-abi3-linux_x86_64.whl")
        assert wf is not None
        assert wf.python_tags == ["cp311", "cp312"]

    def test_build_tag(self):
        wf = _parse_wheel_filename("pkg-1.0-1-cp312-cp312-linux_x86_64.whl")
        assert wf is not None
        assert wf.python_tags == ["cp312"]

    def test_sdist_returns_none(self):
        assert _parse_wheel_filename("polars-1.0.0.tar.gz") is None

    def test_non_wheel_returns_none(self):
        assert _parse_wheel_filename("notawheel.zip") is None

    def test_empty_string_returns_none(self):
        assert _parse_wheel_filename("") is None


# ---------------------------------------------------------------------------
# _extract_wheels
# ---------------------------------------------------------------------------


class TestExtractWheels:
    def _file_dict(
        self,
        filename: str,
        url: str = "",
        sha256: str = "abc",
        requires_python: str | None = None,
    ):
        return {
            "filename": filename,
            "url": url,
            "digests": {"sha256": sha256},
            "requires_python": requires_python,
        }

    def test_extracts_wheels_only(self):
        files = [
            self._file_dict(
                "polars-1.0.0-cp312-cp312-linux_x86_64.whl",
                url="http://example.com/polars.whl",
            ),
            self._file_dict("polars-1.0.0.tar.gz"),  # sdist — should be ignored
        ]
        wheels = _extract_wheels(files)
        assert len(wheels) == 1
        assert wheels[0].filename == "polars-1.0.0-cp312-cp312-linux_x86_64.whl"

    def test_fills_url_and_sha256(self):
        files = [
            self._file_dict(
                "pkg-1.0-py3-none-any.whl",
                url="http://x.com/pkg.whl",
                sha256="deadbeef",
            )
        ]
        wheels = _extract_wheels(files)
        assert wheels[0].url == "http://x.com/pkg.whl"
        assert wheels[0].sha256 == "deadbeef"

    def test_fills_requires_python(self):
        files = [self._file_dict("pkg-1.0-py3-none-any.whl", requires_python=">=3.10")]
        wheels = _extract_wheels(files)
        assert wheels[0].requires_python == ">=3.10"

    def test_empty_list(self):
        assert _extract_wheels([]) == []

    def test_missing_digests_key(self):
        files = [
            {"filename": "pkg-1.0-py3-none-any.whl", "url": "", "requires_python": None}
        ]
        wheels = _extract_wheels(files)
        assert wheels[0].sha256 == ""


# ---------------------------------------------------------------------------
# _eval_marker_for_any_target
# ---------------------------------------------------------------------------


class TestEvalMarkerForAnyTarget:
    def test_windows_only_marker_excluded_on_linux_target(self):
        marker = Marker("sys_platform == 'win32'")
        result = _eval_marker_for_any_target(marker, ["3.12"], ["linux_x86_64"])
        assert result is False

    def test_windows_only_marker_included_on_windows_target(self):
        marker = Marker("sys_platform == 'win32'")
        result = _eval_marker_for_any_target(marker, ["3.12"], ["win_amd64"])
        assert result is True

    def test_windows_only_marker_included_on_mixed_targets(self):
        marker = Marker("sys_platform == 'win32'")
        result = _eval_marker_for_any_target(
            marker, ["3.12"], ["linux_x86_64", "win_amd64"]
        )
        assert result is True

    def test_darwin_only_marker_excluded_on_linux_windows(self):
        marker = Marker("sys_platform == 'darwin'")
        result = _eval_marker_for_any_target(
            marker, ["3.12"], ["linux_x86_64", "win_amd64"]
        )
        assert result is False

    def test_darwin_only_marker_included_when_macos_targeted(self):
        marker = Marker("sys_platform == 'darwin'")
        result = _eval_marker_for_any_target(marker, ["3.12"], ["macosx_11_0_arm64"])
        assert result is True

    def test_python_version_marker(self):
        marker = Marker("python_version < '3.12'")
        # Only 3.11 targeted → True
        assert _eval_marker_for_any_target(marker, ["3.11"], ["linux_x86_64"]) is True
        # Only 3.12 targeted → False
        assert _eval_marker_for_any_target(marker, ["3.12"], ["linux_x86_64"]) is False
        # Both targeted → True (3.11 satisfies it)
        assert (
            _eval_marker_for_any_target(marker, ["3.11", "3.12"], ["linux_x86_64"])
            is True
        )

    def test_combined_platform_and_python_marker(self):
        # Only needed on windows with python < 3.12
        marker = Marker("sys_platform == 'win32' and python_version < '3.12'")
        # Linux only + 3.12 → False
        assert _eval_marker_for_any_target(marker, ["3.12"], ["linux_x86_64"]) is False
        # Windows + 3.11 → True
        assert _eval_marker_for_any_target(marker, ["3.11"], ["win_amd64"]) is True
        # Windows + 3.12 → False (python_version condition fails)
        assert _eval_marker_for_any_target(marker, ["3.12"], ["win_amd64"]) is False


# ---------------------------------------------------------------------------
# _dep_reachable
# ---------------------------------------------------------------------------


class TestDepReachable:
    def test_no_marker_always_reachable(self):
        req = Requirement("numpy")
        assert _dep_reachable(req, ["3.12"], ["linux_x86_64"]) is True

    def test_unreachable_marker(self):
        req = Requirement("colorama ; sys_platform == 'win32'")
        assert _dep_reachable(req, ["3.12"], ["linux_x86_64"]) is False

    def test_reachable_marker_on_matching_platform(self):
        req = Requirement("colorama ; sys_platform == 'win32'")
        assert _dep_reachable(req, ["3.12"], ["win_amd64"]) is True


# ---------------------------------------------------------------------------
# _extract_pin
# ---------------------------------------------------------------------------


class TestExtractPin:
    def test_exact_pin(self):
        req = Requirement("numpy==1.26.0")
        assert _extract_pin(req) == "1.26.0"

    def test_no_pin_returns_none(self):
        req = Requirement("numpy>=1.20")
        assert _extract_pin(req) is None

    def test_no_specifier_returns_none(self):
        req = Requirement("numpy")
        assert _extract_pin(req) is None

    def test_range_specifier_returns_none(self):
        req = Requirement("numpy>=1.20,<2.0")
        assert _extract_pin(req) is None


# ---------------------------------------------------------------------------
# _wheel_wanted
# ---------------------------------------------------------------------------


class TestWheelWanted:
    def _wheel(self, python_tags, platform_tags):
        return WheelFile(
            filename="pkg-1.0-x-x-x.whl",
            url="",
            sha256="",
            requires_python=None,
            python_tags=python_tags,
            abi_tags=["none"],
            platform_tags=platform_tags,
        )

    def test_matching_wheel_is_wanted(self):
        wheel = self._wheel(["cp312"], ["linux_x86_64"])
        assert _wheel_wanted(wheel, ["3.12"], ["linux_x86_64"])

    def test_python_mismatch_not_wanted(self):
        wheel = self._wheel(["cp311"], ["linux_x86_64"])
        assert not _wheel_wanted(wheel, ["3.12"], ["linux_x86_64"])

    def test_platform_mismatch_not_wanted(self):
        wheel = self._wheel(["cp312"], ["win_amd64"])
        assert not _wheel_wanted(wheel, ["3.12"], ["linux_x86_64"])

    def test_pure_wheel_wanted_for_any_platform(self):
        wheel = self._wheel(["py3"], ["any"])
        assert _wheel_wanted(wheel, ["3.12"], ["linux_x86_64"])
        assert _wheel_wanted(wheel, ["3.12"], ["win_amd64"])

    def test_wheel_wanted_for_one_of_multiple_targets(self):
        wheel = self._wheel(["cp312"], ["win_amd64"])
        # Only wanted for win_amd64 — still True even though linux is also a target
        assert _wheel_wanted(wheel, ["3.12"], ["linux_x86_64", "win_amd64"])

    def test_wheel_not_wanted_when_no_python_matches(self):
        wheel = self._wheel(["cp310"], ["linux_x86_64"])
        assert not _wheel_wanted(wheel, ["3.11", "3.12"], ["linux_x86_64"])


# ---------------------------------------------------------------------------
# PyPIClient.fetch_release
# ---------------------------------------------------------------------------


class TestPyPIClientFetchRelease:
    @pytest.mark.asyncio
    async def test_fetch_release_http_status_error(self, caplog):
        client = PyPIClient()
        async with client:
            # client._client is set in __aenter__
            with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
                mock_resp = MagicMock()
                mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "404 Not Found", request=MagicMock(), response=mock_resp
                )
                mock_get.return_value = mock_resp

                result = await client.fetch_release("nonexistent")

                assert result is None
                assert "Failed to fetch" in caplog.text
                assert "404 Not Found" in caplog.text

    @pytest.mark.asyncio
    async def test_fetch_release_request_error(self, caplog):
        client = PyPIClient()
        async with client:
            with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
                mock_get.side_effect = httpx.RequestError(
                    "Connection failed", request=MagicMock()
                )

                result = await client.fetch_release("somepkg")

                assert result is None
                assert "Request error for" in caplog.text
                assert "Connection failed" in caplog.text
