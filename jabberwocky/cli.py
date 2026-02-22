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
        click.echo("Resolving dependencies...")
        resolved = await resolve(
            cfg.packages,
            cfg.python_versions,
            cfg.platforms,
            pypi_url=cfg.pypi_url,
        )
        click.echo(f"Resolved {len(resolved)} packages total.")

        wheel_count = sum(1 for p in resolved.values() if p.needs_wheels)
        meta_only = len(resolved) - wheel_count
        click.echo(f"  {wheel_count} packages need wheels")
        click.echo(f"  {meta_only} packages are metadata-only (global resolvability)")

        click.echo("Downloading wheels...")
        await download_wheels(
            resolved,
            cfg.output_dir,
            cfg.python_versions,
            cfg.platforms,
        )

        click.echo("Building index...")
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
