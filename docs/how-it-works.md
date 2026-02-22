# How Jabberwocky works

## Overview

Jabberwocky has three stages: **resolve**, **download**, and **index**.

```
wishlist.txt  ──►  resolve  ──►  download  ──►  index  ──►  serve
                   (PyPI)        (wheels)       (JSON)      (HTTP)
```

## Stage 1: Resolve

Jabberwocky fetches package metadata from the PyPI JSON API and resolves the full transitive dependency graph starting from your wishlist.

For each package it resolves, it records whether that package **needs wheels** or is **metadata-only**:

- **Needs wheels** — the package is reachable on at least one of your target platforms and Python versions. Wheels will be downloaded.
- **Metadata-only** — the package is only reachable via environment markers that exclude all of your targets (e.g. a macOS-only dependency when you are only targeting Linux and Windows). No wheels are downloaded, but the package still appears in the index.

The metadata-only entries are what makes the mirror **globally resolvable** — see below.

### Dependency marker evaluation

When a dependency is declared with an environment marker, e.g.:

```
appnope ; sys_platform == "darwin"
```

Jabberwocky evaluates that marker against every combination of your target Python versions and platforms. If the marker is `True` for any target, the dependency gets wheels. If it is `False` for all targets, it gets a metadata-only entry.

This evaluation is conservative: if a marker cannot be parsed or evaluated, Jabberwocky includes wheels for that dependency.

## Stage 2: Download

For each package that needs wheels, Jabberwocky fetches the list of available wheel files and downloads any that are compatible with your targets.

A wheel is compatible if:
1. Its **python tag** matches at least one of your target Python versions (`cp312`, `py3`, etc.)
2. Its **platform tag** matches at least one of your target platforms (including `manylinux`/`musllinux` compatibility for Linux targets)

Pure-Python wheels (`*-py3-none-any.whl`) match all platforms.

Downloads are streamed and verified against the SHA-256 digest published by PyPI. If the digest does not match, the file is discarded and an error is logged. Files that already exist on disk are skipped.

## Stage 3: Index

Jabberwocky writes a [PEP 691](https://peps.python.org/pep-0691/) JSON index to `<output_dir>/simple/`:

```
mirror/
  simple/
    index.json            # project list
    polars/
      index.json          # file list for polars
    numpy/
      index.json
    appnope/
      index.json          # metadata-only: URLs point back to PyPI
    ...
  files/
    polars-1.0.0-cp312-cp312-linux_x86_64.whl
    polars-1.0.0-cp312-cp312-win_amd64.whl
    ...
```

- For packages with local wheels, file URLs point to `/files/<filename>` on the mirror server.
- For metadata-only packages, file URLs point directly back to PyPI. These files will never be fetched by a client targeting your configured platforms, but the resolver can see them and correctly skip them.

## Global resolvability

Consider this scenario: you are mirroring for Linux and Windows only. One of your packages depends on `ipykernel`, which in turn depends on `appnope ; sys_platform == "darwin"`.

Without `appnope` in the index, a Windows resolver would encounter `appnope` during resolution and fail with "package not found" — even though it would never actually install it.

Jabberwocky solves this by including an `appnope` entry in the index pointing back to PyPI. The resolver finds `appnope`, evaluates the marker, determines it is not needed on Windows, and continues. No `appnope` wheel is ever downloaded to your mirror.

## Serving

The built `mirror/` directory is served by a lightweight [Starlette](https://www.starlette.io/) application via `jabberwocky serve`. It implements PEP 691 content negotiation, returning `application/vnd.pypi.simple.v1+json` responses.

The mirror can also be served by any static file server capable of setting custom `Content-Type` headers (e.g. nginx, Apache, Caddy) by pointing it at the `simple/` directory and configuring it to serve `.json` files with the `application/vnd.pypi.simple.v1+json` content type.
