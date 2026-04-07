from jabberwocky.build_mirror import main as build_main
from jabberwocky.index import main as index_main
from pathlib import Path

def main():
    import argparse
    parser = argparse.ArgumentParser(prog="jabberwocky")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Resolve and download packages")
    build.add_argument("requirements_dir", type=Path)
    build.add_argument("output_dir", type=Path)
    build.add_argument("--core", default="core.in")

    index = subparsers.add_parser("index", help="Build PEP 503 index")
    index.add_argument("mirror_dir", type=Path)

    args = parser.parse_args()
    if args.command == "build":
        build_main(args)
    elif args.command == "index":
        index_main(args)
