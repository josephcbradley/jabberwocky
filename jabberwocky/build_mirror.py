import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from jabberwocky.combine_requirements import combine_requirements

PYTHON_VERSIONS = ["3.12", "3.13", "3.14"]


def dedup_requirements(requirements: str) -> str:
    """
    Deduplicate pinned requirements lines across combined lockfile exports.
    Preserves comments and strips duplicates, sorting for determinism.
    """
    seen = set()
    deduped = []

    for line in requirements.splitlines():
        stripped = line.strip()
        # normalise to lowercase for dedup key (package names are case-insensitive)
        key = stripped.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(stripped)

    return "\n".join(sorted(deduped))


def download_packages(
    requirements: str,
    dest: Path,
    platforms: Optional[list[str]] = None,
    python_version: str = "3.12",
) -> None:
    if platforms is None:
        platforms = ["manylinux2014_x86_64", "win_amd64", "macosx_15_0_x86_64"]

    dest.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(requirements)
        tmp_path = Path(f.name)

    try:
        # pass 1: targeted platform wheels
        subprocess.run(
            [
                "pip",
                "download",
                "-r",
                str(tmp_path),
                "--dest",
                str(dest),
                "--python-version",
                python_version,
                "--no-deps",
                *[arg for p in platforms for arg in ("--platform", p)],
            ],
            check=False,
        )  # don't fail — some packages may not have wheels for all platforms

        # pass 2: no platform constraint, catches darwin-marker packages like appnope
        result = subprocess.run(
            [
                "pip",
                "download",
                "-r",
                str(tmp_path),
                "--dest",
                str(dest),
                "--no-deps",
            ],
            capture_output=False,
            check=False,
        )

        if result.returncode != 0:
            print(
                f"Warning: pass 2 had some download failures (exit {result.returncode}) — continuing"
            )

    finally:
        tmp_path.unlink()


def build_mirror(
    requirements_dir: Path,
    output_dir: Path,
    core_filename: str = "core.in",
    python_versions: list[str] = PYTHON_VERSIONS,
    platforms: Optional[list[str]] = None,
) -> None:
    def resolve_and_download(python_version: str) -> str:
        combined = combine_requirements(
            requirements_dir,
            core_filename=core_filename,
            python_version=python_version,
        )
        deduped = dedup_requirements(combined)
        print(f"Python {python_version}: {len(deduped.splitlines())} packages")
        download_packages(
            deduped,
            dest=output_dir,
            platforms=platforms,
            python_version=python_version,
        )
        return python_version

    with ThreadPoolExecutor(max_workers=len(python_versions)) as executor:
        futures = {executor.submit(resolve_and_download, v): v for v in python_versions}
        for future in as_completed(futures):
            version = futures[future]
            try:
                future.result()
                print(f"Python {version}: done")
            except Exception as e:
                print(f"Python {version}: failed — {e}")
                raise


def main(args=None):
    import argparse

    parser = argparse.ArgumentParser(description="Build a minimal PyPI mirror.")
    parser.add_argument("requirements_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--core", default="core.in")
    args = args or parser.parse_args()
    build_mirror(args.requirements_dir, args.output_dir, core_filename=args.core)


if __name__ == "__main__":
    main()
