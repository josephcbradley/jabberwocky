# Jabberwocky

[![Python application](https://github.com/josephcbradley/jabberwocky/actions/workflows/python-app.yml/badge.svg)](https://github.com/josephcbradley/jabberwocky/actions/workflows/python-app.yml)

Jabberwocky builds partial Python package mirrors. You tell it which packages you need, which Python versions and platforms your team uses, and it downloads exactly the wheels required — nothing more.

Suppose your team uses Linux and Windows machines behind a firewall. Alice uses Windows and needs `autograd`, `scipy`, and `polars`. Bob uses Linux and needs `pandas`, `autograd`, and `flask`. Jabberwocky builds a mirror that serves both of them, so Alice can run `uv add polars` with `uv` pointed at the local mirror.

**Features:**
- **Partial** — does not download a full PyPI mirror, only what your team actually needs.
- **Wheel only** — serves wheels exclusively; no source builds required on client machines.
- **Globally resolvable** — when packages declare platform-conditional dependencies, Jabberwocky includes metadata for those dependencies without downloading the wheels. This means `uv`'s resolver works correctly on all target platforms, even if the machine running the resolver is not one of them.
- **Customizable** — provide a package list, Python versions, and target platforms. Jabberwocky does the rest.
- **PEP 691** — serves a JSON-formatted Simple API index that `uv` and modern pip understand natively.

## Getting started

### Install

```bash
pip install jabberwocky
# or
uv tool install jabberwocky
```

### 1. Create a package wishlist

Create a `wishlist.txt` with one package name per line:

```
polars
scipy
autograd
gradio
```

Lines starting with `#` are treated as comments and ignored.

### 2. Build the mirror

```bash
jabberwocky build \
  --wishlist wishlist.txt \
  --python 3.11 \
  --python 3.12 \
  --platform linux_x86_64 \
  --platform win_amd64
```

Jabberwocky will:
1. Resolve the full transitive dependency graph for every package in your wishlist.
2. Download wheels for every resolved package that has files compatible with your target platforms and Python versions.
3. Include metadata-only index entries for platform-conditional dependencies that are not needed on your targets (so resolvers on those platforms still work).
4. Write a PEP 691 JSON index to `./mirror/`.

### 3. Serve the mirror

```bash
jabberwocky serve --mirror ./mirror --port 8080
```

### 4. Configure uv to use the mirror

In your project's `uv.toml` or `pyproject.toml`:

```toml
[[tool.uv.index]]
url = "http://your-mirror-host:8080/simple/"
default = true
```

Or pass it directly:

```bash
uv add polars --index http://your-mirror-host:8080/simple/
```

## TOML configuration

For repeatable builds, use a TOML config file instead of CLI flags:

```toml
# jabberwocky.toml
[mirror]
packages = ["polars", "scipy", "autograd", "gradio"]
python_versions = ["3.11", "3.12"]
platforms = ["linux_x86_64", "win_amd64"]
output_dir = "mirror"
```

Then build with:

```bash
jabberwocky build --config jabberwocky.toml
```

## Platform tag reference

Use standard wheel platform tags:

| Platform | Tag |
|---|---|
| Linux x86-64 | `linux_x86_64` |
| Linux ARM64 | `linux_aarch64` |
| Windows 64-bit | `win_amd64` |
| macOS Intel | `macosx_10_9_x86_64` |
| macOS Apple Silicon | `macosx_11_0_arm64` |

Jabberwocky also recognises `manylinux` and `musllinux` wheels as compatible with the corresponding `linux_*` target.

## How globally resolvable works

Consider `ipykernel` on Windows: its dependency tree includes `appnope`, which is macOS-only. If you are only mirroring for Windows and Linux, Jabberwocky will not download `appnope` wheels. But it will include an `appnope` entry in the index that points back to PyPI. This means a Windows resolver that encounters `appnope` during dependency resolution can find it in the index and correctly determine it is not needed — without failing with a "package not found" error.

## Keeping the mirror up to date

To pull in new package versions or add packages to an existing mirror, use `update` instead of `build`:

```bash
jabberwocky update --config jabberwocky.toml
```

This will:
1. Re-resolve and download into a staging area.
2. Archive the current `mirror/` into `archives/<timestamp>/`.
3. Compute a diff and write a portable patch to `diffs/<timestamp>/` containing only the changed files, plus an `APPLY.md` with instructions for patching the offline machine.
4. Replace `mirror/` with the new state.

To update the offline machine, transfer `diffs/<timestamp>/` via USB or any one-way link, then follow the `APPLY.md` inside — it requires only `cp` and `rm`, no Jabberwocky installation needed offline.

See [UPDATING.md](UPDATING.md) for the full workflow.

## CLI reference

```
jabberwocky build   Resolve, download, and index packages
jabberwocky update  Incrementally update an existing mirror
jabberwocky serve   Serve the mirror over HTTP
```

Run `jabberwocky <command> --help` for full option listings.

Full documentation is in the [`docs/`](docs/) directory.
