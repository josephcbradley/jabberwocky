import shutil
import subprocess
import tempfile
from pathlib import Path


def generate_lockfile(path: Path) -> str:
    """
    Generate a lockfile for a requirements file in an isolated environment using `uv`.

    This function creates a temporary directory, initializes a new `uv` project,
    adds the requirements from the provided file, and exports the resulting lockfile
    in requirements.txt format.

    Args:
        path (Path): The path to the requirements file (e.g., .in or .txt).

    Returns:
        str: The generated lockfile as a requirements-formatted string.

    Raises:
        subprocess.CalledProcessError: If any of the `uv` commands fail.
        FileNotFoundError: If the input requirements file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Requirements file not found: {path}")

    # create a temporary directory
    with tempfile.TemporaryDirectory(prefix="jabberwocky_tmp_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        # copy requirements file into the tmp dir
        shutil.copy(path, tmp_path / path.name)

        # initialize a new uv project
        cmd_init = ["uv", "init", "--no-workspace", "--directory", tmp_dir]
        subprocess.run(cmd_init, check=True, capture_output=True)

        # add the requirements file
        cmd_add = [
            "uv",
            "add",
            "--requirements",
            path.name,
            "--directory",
            tmp_dir,
            "--no-sync",
        ]
        subprocess.run(cmd_add, check=True, capture_output=True)

        # export to requirements format
        cmd_export = [
            "uv",
            "export",
            "--directory",
            tmp_dir,
            "--format",
            "requirements.txt",
        ]
        result = subprocess.run(cmd_export, capture_output=True, check=True, text=True)

        return result.stdout


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(generate_lockfile(Path(sys.argv[1])))
    else:
        # Default example path
        example_path = Path("examples/core.in")
        if example_path.exists():
            print(generate_lockfile(example_path))
        else:
            print("Usage: python generate_lockfile.py <requirements_file>")
