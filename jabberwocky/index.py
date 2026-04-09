import html
from pathlib import Path


def build_pep503_index(mirror_dir: Path) -> None:
    print(f"Building index for: {mirror_dir}")
    packages: dict[str, list[Path]] = {}
    for file in mirror_dir.iterdir():
        if file.is_file():
            # print(f"Checking file: {file.name}, suffix: {file.suffix}")
            if file.suffix in (".whl", ".tar.gz", ".zip"):
                # normalise name per PEP 503
                name = file.name.split("-")[0]
                name = name.replace("_", "-").lower()
                print(f"Found package file: {file.name} -> normalized name: {name}")
                packages.setdefault(name, []).append(file)

    # root index
    root = mirror_dir / "index.html"
    with open(root, "w") as f:
        f.write("<!DOCTYPE html><html><body>\n")
        for name in sorted(packages):
            f.write(f'<a href="{name}/">{name}</a>\n')
        f.write("</body></html>\n")

    # per-package index
    for name, files in packages.items():
        pkg_dir = mirror_dir / name
        pkg_dir.mkdir(exist_ok=True)
        with open(pkg_dir / "index.html", "w") as f:
            f.write("<!DOCTYPE html><html><body>\n")
            for file in sorted(files):
                safe = html.escape(file.name)
                f.write(f'<a href="../{safe}">{safe}</a>\n')
            f.write("</body></html>\n")

    print(f"Index built: {len(packages)} packages")

def main(args=None) -> None:
    if args is None:
        import argparse
        parser = argparse.ArgumentParser(description="Build a PEP 503 index.")
        parser.add_argument("mirror_dir", type=Path)
        args = parser.parse_args()
    build_pep503_index(Path(args.mirror_dir).resolve())


if __name__ == "__main__":
    main()
    