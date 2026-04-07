from pathlib import Path

from jabberwocky.generate_requirements import generate_requirements

PYTHON_VERSIONS = ["3.12", "3.13", "3.14"]


def combine_requirements(
    requirements_dir: Path,
    *,
    core_filename: str = "core.in",
    python_version: str = "3.12",
) -> str:
    requirements_files = [f for f in requirements_dir.iterdir() if f.is_file()]

    # check that dir isn't empty
    if not requirements_files:
        raise RuntimeError("No files in the requirements dir!")

    # begin to build requirements
    # assume we do have a core filepath
    core_filepath = requirements_dir / core_filename
    if not core_filepath.is_file():
        raise FileNotFoundError(
            f"Core file {core_filename} does not exist in {requirements_dir}."
        )

    complete_requirements = ""

    for file in requirements_files:
        if file.name == core_filename:
            continue
        complete_requirements += "\n" + generate_requirements(
            [core_filepath, file], python_version=python_version
        )

    return complete_requirements
