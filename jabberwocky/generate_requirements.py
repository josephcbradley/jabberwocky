import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Union


def generate_requirements(
    paths: Union[Path, List[Path]], python_version: Optional[str] = None
) -> str:
    """
    Generate resolved requirements for one or more requirements files in an isolated environment using `uv`.

    This function creates a temporary directory, initializes a new `uv` project,
    adds the requirements from the provided files, and exports the resulting
    resolved requirements in requirements.txt format.

    Args:
        paths (Union[Path, List[Path]]): One or more paths to input requirements files (e.g., .in or .txt).
        python_version (Optional[str]): The Python version to target (e.g., "3.12").
            If None, `uv` will use its default.

    Returns:
        str: The generated resolved requirements as a requirements-formatted string.

    Raises:
        subprocess.CalledProcessError: If any of the `uv` commands fail.
        FileNotFoundError: If any of the input requirements files do not exist.
    """
    if isinstance(paths, Path):
        paths = [paths]

    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Requirements file not found: {path}")

    # create a temporary directory
    with tempfile.TemporaryDirectory(prefix="jabberwocky_tmp_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        # initialize a new uv project
        cmd_init = ["uv", "init", "--no-workspace", "--directory", tmp_dir]
        if python_version:
            cmd_init.extend(["--python", python_version])
        subprocess.run(cmd_init, check=True, capture_output=True)

        # copy requirements files into the tmp dir and build the uv add command
        cmd_add = ["uv", "add", "--directory", tmp_dir, "--no-sync"]

        for path in paths:
            shutil.copy(path, tmp_path / path.name)
            cmd_add.extend(["--requirements", path.name])

        subprocess.run(cmd_add, check=True, capture_output=True)

        # export to requirements format
        cmd_export = [
            "uv",
            "export",
            "--directory",
            tmp_dir,
            "--format",
            "requirements.txt",
            "--no-hashes",
        ]
        if python_version:
            cmd_export.extend(["--python", python_version])

        result = subprocess.run(cmd_export, capture_output=True, check=True, text=True)
        clean = "\n".join(
            line
            for line in result.stdout.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        return clean


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate resolved requirements for requirements files."
    )
    parser.add_argument(
        "paths", type=Path, nargs="+", help="Paths to the requirements files."
    )
    parser.add_argument(
        "--python", dest="python_version", help="Python version to target (e.g., 3.12)."
    )

    args = parser.parse_args()

    try:
        print(generate_requirements(args.paths, python_version=args.python_version))
    except Exception as e:
        print(f"Error: {e}")
        import sys

        sys.exit(1)
