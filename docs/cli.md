# CLI reference

## `jabberwocky build`

Resolve dependencies, download wheels, and build the PEP 691 index.

```
jabberwocky build [OPTIONS]
```

**Options:**

| Flag | Short | Description |
|---|---|---|
| `--config PATH` | `-c` | Path to a TOML config file |
| `--wishlist PATH` | `-w` | Path to a plaintext package wishlist |
| `--python TEXT` | `-p` | Target Python version (repeatable) |
| `--platform TEXT` | | Target platform tag (repeatable) |
| `--output PATH` | `-o` | Output directory (default: `mirror`) |
| `--verbose` | `-v` | Enable debug logging |

Either `--config` or `--wishlist` must be provided. When using `--wishlist`, `--python` and `--platform` are required.

CLI flags override values in the TOML config when both are provided.

**Examples:**

```bash
# Using a wishlist file
jabberwocky build \
  --wishlist wishlist.txt \
  --python 3.11 --python 3.12 \
  --platform linux_x86_64 --platform win_amd64

# Using a TOML config
jabberwocky build --config jabberwocky.toml

# TOML config with an output override
jabberwocky build --config jabberwocky.toml --output /mnt/mirror

# Verbose output for debugging
jabberwocky build --config jabberwocky.toml --verbose
```

**Output:**

```
Packages   : polars, scipy, autograd
Python     : 3.11, 3.12
Platforms  : linux_x86_64, win_amd64
Output dir : mirror
Resolving dependencies...
Resolved 47 packages total.
  31 packages need wheels
  16 packages are metadata-only (global resolvability)
Downloading wheels...
Building index...
Mirror built at mirror/
```

---

## `jabberwocky serve`

Serve a built mirror over HTTP.

```
jabberwocky serve [OPTIONS]
```

**Options:**

| Flag | Short | Description |
|---|---|---|
| `--mirror PATH` | `-m` | Path to the built mirror directory (default: `mirror`) |
| `--host TEXT` | | Bind host (default: `0.0.0.0`) |
| `--port INTEGER` | | Bind port (default: `8080`) |
| `--verbose` | `-v` | Enable debug logging |

**Examples:**

```bash
# Serve on the default port
jabberwocky serve

# Serve on a custom port, localhost only
jabberwocky serve --host 127.0.0.1 --port 9000

# Serve a mirror in a non-default location
jabberwocky serve --mirror /mnt/mirror --port 8080
```

**Output:**

```
Serving mirror at http://0.0.0.0:8080/simple/
Configure uv: uv add --index http://0.0.0.0:8080/simple/ <package>
```

---

## `jabberwocky update`

Incrementally update an existing mirror: archive the current state, re-resolve,
download anything new, compute a diff, and write a portable diff package for
transferring to an offline machine.

```
jabberwocky update [OPTIONS]
```

**Options:**

| Flag | Short | Description |
|---|---|---|
| `--config PATH` | `-c` | Path to a TOML config file |
| `--wishlist PATH` | `-w` | Path to a plaintext package wishlist |
| `--python TEXT` | `-p` | Target Python version (repeatable) |
| `--platform TEXT` | | Target platform tag (repeatable) |
| `--mirror PATH` | `-m` | Mirror directory to update (default: `mirror`) |
| `--archives PATH` | | Archive directory (default: `archives`) |
| `--diffs PATH` | | Diff output directory (default: `diffs`) |
| `--verbose` | `-v` | Enable debug logging |

Either `--config` or `--wishlist` must be provided.

**Steps performed:**

1. Re-resolves all packages and downloads any new wheels into a staging area.
2. Archives the current `mirror/` into `archives/<timestamp>/`.
3. Computes a diff: added wheels, removed wheels, changed/added index entries.
4. Writes a self-contained diff package to `diffs/<timestamp>/` containing only
   the changed files, a `manifest.json`, and an `APPLY.md` with copy-paste
   instructions for patching an offline machine.
5. Replaces `mirror/` with the newly resolved state.

**Examples:**

```bash
# Using a wishlist
jabberwocky update \
  --wishlist wishlist.txt \
  --python 3.12 \
  --platform linux_x86_64

# Using a TOML config
jabberwocky update --config jabberwocky.toml

# Custom directories
jabberwocky update --config jabberwocky.toml \
  --mirror /mnt/mirror \
  --archives /mnt/mirror-archives \
  --diffs /mnt/mirror-diffs
```

**Output:**

```
Packages   : polars, scipy
Python     : 3.12
Platforms  : linux_x86_64
Mirror dir : mirror
Archives   : archives
Diffs      : diffs
Resolving dependencies...
Diff: +3 wheels, -0 wheels, 2 index changes, 1 new index entries
Resolved 49 packages total (33 with wheels, 16 metadata-only).
Diff package written to diffs/20260222T134501Z/
  See diffs/20260222T134501Z/APPLY.md for offline update instructions.
Mirror updated at mirror/
```

See [UPDATING.md](../UPDATING.md) for full instructions on transferring and
applying diff packages to an offline machine.

---

## Pointing uv at the mirror

Add the mirror as an index in your project:

```toml
# pyproject.toml
[[tool.uv.index]]
url = "http://mirror-host:8080/simple/"
default = true
```

Or in `uv.toml`:

```toml
[[index]]
url = "http://mirror-host:8080/simple/"
default = true
```

Or pass it on the command line:

```bash
uv add polars --index http://mirror-host:8080/simple/
uv sync --index http://mirror-host:8080/simple/
```
