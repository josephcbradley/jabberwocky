"""Command-line interface for Jabberwocky."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click

from .config import Config
from .downloader import download_wheels
from .index import build_index
from .pypi import resolve
from .updater import run_update


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
def cli() -> None:
    """Jabberwocky — build and serve a partial Python package mirror."""


@cli.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="TOML config file",
)
@click.option(
    "--wishlist",
    "-w",
    type=click.Path(exists=True, path_type=Path),
    help="Plaintext package wishlist",
)
@click.option(
    "--python",
    "-p",
    "python_versions",
    multiple=True,
    help="Target Python versions, e.g. 3.11",
)
@click.option(
    "--platform",
    "platforms",
    multiple=True,
    help="Target platforms, e.g. linux_x86_64 win_amd64",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default="mirror",
    show_default=True,
)
@click.option("--verbose", "-v", is_flag=True)
def build(
    config: Path | None,
    wishlist: Path | None,
    python_versions: tuple[str, ...],
    platforms: tuple[str, ...],
    output: Path,
    verbose: bool,
) -> None:
    """Resolve dependencies, download wheels, and build the mirror index."""
    _setup_logging(verbose)

    if config:
        cfg = Config.from_toml(config)
    elif wishlist:
        if not python_versions or not platforms:
            raise click.UsageError(
                "--python and --platform are required when using --wishlist"
            )
        cfg = Config.from_wishlist(wishlist, list(python_versions), list(platforms))
        cfg.output_dir = output
    else:
        raise click.UsageError("Provide either --config or --wishlist")

    if python_versions:
        cfg.python_versions = list(python_versions)
    if platforms:
        cfg.platforms = list(platforms)
    if output != Path("mirror"):
        cfg.output_dir = output

    click.echo(f"Packages   : {', '.join(cfg.packages)}")
    click.echo(f"Python     : {', '.join(cfg.python_versions)}")
    click.echo(f"Platforms  : {', '.join(cfg.platforms)}")
    click.echo(f"Output dir : {cfg.output_dir}")

    async def _run() -> None:
        click.echo("Resolving dependencies...", nl=False)
        resolved = await resolve(
            cfg.packages,
            cfg.python_versions,
            cfg.platforms,
            pypi_url=cfg.pypi_url,
        )
        wheel_count = sum(1 for p in resolved.values() if p.needs_wheels)
        click.echo(f" {len(resolved)} packages ({wheel_count} with wheels)")

        await download_wheels(
            resolved,
            cfg.output_dir,
            cfg.python_versions,
            cfg.platforms,
        )

        build_index(resolved, cfg.output_dir)
        click.echo(f"Mirror built at {cfg.output_dir}/")

    asyncio.run(_run())


@cli.command()
@click.option(
    "--mirror",
    "-m",
    type=click.Path(exists=True, path_type=Path),
    default="mirror",
    show_default=True,
)
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8080, show_default=True)
@click.option("--verbose", "-v", is_flag=True)
def serve(mirror: Path, host: str, port: int, verbose: bool) -> None:
    """Serve the mirror over HTTP."""
    _setup_logging(verbose)

    import uvicorn
    from .server import make_app

    app = make_app(mirror)
    click.echo(f"Serving mirror at http://{host}:{port}/simple/")
    click.echo(f"Configure uv: uv add --index http://{host}:{port}/simple/ <package>")
    uvicorn.run(app, host=host, port=port, log_level="warning")


@cli.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="TOML config file",
)
@click.option(
    "--wishlist",
    "-w",
    type=click.Path(exists=True, path_type=Path),
    help="Plaintext package wishlist",
)
@click.option(
    "--python",
    "-p",
    "python_versions",
    multiple=True,
    help="Target Python versions, e.g. 3.11",
)
@click.option(
    "--platform",
    "platforms",
    multiple=True,
    help="Target platforms, e.g. linux_x86_64 win_amd64",
)
@click.option(
    "--mirror",
    "-m",
    type=click.Path(path_type=Path),
    default="mirror",
    show_default=True,
    help="Mirror directory to update",
)
@click.option(
    "--archives",
    type=click.Path(path_type=Path),
    default="archives",
    show_default=True,
    help="Directory to store timestamped mirror archives",
)
@click.option(
    "--diffs",
    type=click.Path(path_type=Path),
    default="diffs",
    show_default=True,
    help="Directory to store timestamped diff packages",
)
@click.option("--verbose", "-v", is_flag=True)
def update(
    config: Path | None,
    wishlist: Path | None,
    python_versions: tuple[str, ...],
    platforms: tuple[str, ...],
    mirror: Path,
    archives: Path,
    diffs: Path,
    verbose: bool,
) -> None:
    """Incrementally update the mirror: archive, re-resolve, diff, and apply.

    \b
    Steps performed:
      1. Re-resolve and download packages into a staging area.
      2. Archive the current mirror/ into archives/<timestamp>/.
      3. Compute a diff (added/removed wheels, changed index entries).
      4. Write a portable diff package to diffs/<timestamp>/ with APPLY.md
         instructions for updating the offline machine.
      5. Replace mirror/ with the new state.
    """
    _setup_logging(verbose)

    if config:
        cfg = Config.from_toml(config)
    elif wishlist:
        if not python_versions or not platforms:
            raise click.UsageError(
                "--python and --platform are required when using --wishlist"
            )
        cfg = Config.from_wishlist(wishlist, list(python_versions), list(platforms))
        cfg.output_dir = mirror
    else:
        raise click.UsageError("Provide either --config or --wishlist")

    if python_versions:
        cfg.python_versions = list(python_versions)
    if platforms:
        cfg.platforms = list(platforms)
    cfg.output_dir = mirror

    click.echo(f"Packages   : {', '.join(cfg.packages)}")
    click.echo(f"Python     : {', '.join(cfg.python_versions)}")
    click.echo(f"Platforms  : {', '.join(cfg.platforms)}")
    click.echo(f"Mirror dir : {mirror}")
    click.echo(f"Archives   : {archives}")
    click.echo(f"Diffs      : {diffs}")

    archives.mkdir(parents=True, exist_ok=True)
    diffs.mkdir(parents=True, exist_ok=True)

    diff_dir, diff, resolved = run_update(
        mirror_dir=mirror,
        archives_dir=archives,
        diffs_dir=diffs,
        resolve_fn=lambda c: resolve(
            c.packages, c.python_versions, c.platforms, pypi_url=c.pypi_url
        ),
        download_fn=download_wheels,
        build_index_fn=build_index,
        cfg=cfg,
    )

    wheel_count = sum(1 for p in resolved.values() if p.needs_wheels)
    meta_only = len(resolved) - wheel_count
    click.echo(
        f"Resolved {len(resolved)} packages total ({wheel_count} with wheels, {meta_only} metadata-only)."
    )
    click.echo(
        f"Diff: +{len(diff['added_wheels'])} wheels, "
        f"-{len(diff['removed_wheels'])} wheels, "
        f"{len(diff['changed_index']) + len(diff['added_index'])} index changes."
    )
    click.echo(f"Diff package written to {diff_dir}/")
    click.echo(f"  See {diff_dir}/APPLY.md for offline update instructions.")
    click.echo(f"Mirror updated at {mirror}/")
