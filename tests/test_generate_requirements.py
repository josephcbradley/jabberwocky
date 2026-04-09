import pytest
from pathlib import Path
import tempfile
from jabberwocky.generate_requirements import generate_requirements


def test_generate_requirements_with_valid_requirements():
    """
    Test that generate_requirements correctly generates resolved requirements for valid input.
    """
    with tempfile.NamedTemporaryFile(suffix=".in", mode="w", delete=False) as tmp:
        tmp.write("numpy\n")
        tmp_path = Path(tmp.name)

    try:
        content = generate_requirements(tmp_path)

        # Check if the output is not empty
        assert content.strip() != ""

        # Check if numpy is in the requirements
        assert "numpy==" in content
    finally:
        tmp_path.unlink()


def test_generate_requirements_with_multiple_files():
    """
    Test that generate_requirements correctly handles multiple requirements files.
    """
    with (
        tempfile.NamedTemporaryFile(suffix="1.in", mode="w", delete=False) as tmp1,
        tempfile.NamedTemporaryFile(suffix="2.in", mode="w", delete=False) as tmp2,
    ):
        tmp1.write("numpy\n")
        tmp2.write("polars\n")
        tmp1_path = Path(tmp1.name)
        tmp2_path = Path(tmp2.name)

    try:
        content = generate_requirements([tmp1_path, tmp2_path])

        # Check if both packages are in the output
        assert "numpy==" in content
        assert "polars==" in content
    finally:
        tmp1_path.unlink()
        tmp2_path.unlink()


def test_generate_requirements_with_python_version():
    """
    Test that generate_requirements correctly generates requirements for a specific Python version.
    """
    with tempfile.NamedTemporaryFile(suffix=".in", mode="w", delete=False) as tmp:
        tmp.write("numpy\n")
        tmp_path = Path(tmp.name)

    try:
        content = generate_requirements(tmp_path, python_version="3.12")

        assert content.strip() != ""
        assert "numpy==" in content
    finally:
        tmp_path.unlink()


def test_generate_requirements_with_non_existent_file():
    """
    Test that generate_requirements raises FileNotFoundError for a non-existent file.
    """
    non_existent_path = Path("this_file_does_not_exist.in")
    with pytest.raises(FileNotFoundError):
        generate_requirements(non_existent_path)


def test_generate_requirements_with_invalid_requirements():
    """
    Test that generate_requirements raises subprocess.CalledProcessError for invalid requirements.
    """
    with tempfile.NamedTemporaryFile(suffix=".in", mode="w", delete=False) as tmp:
        tmp.write("package_that_does_not_exist_xyz\n")
        tmp_path = Path(tmp.name)

    try:
        import subprocess

        with pytest.raises(subprocess.CalledProcessError):
            generate_requirements(tmp_path)
    finally:
        tmp_path.unlink()


def test_core_in_includes_appnope():
    """
    Test that appnope is included in the dependencies for the core.in set on Darwin.
    """
    import sys

    if sys.platform != "darwin":
        pytest.skip("appnope is only included on Darwin")

    core_in_path = Path("examples/core.in")
    content = generate_requirements(core_in_path)

    assert "appnope==" in content
