# Configuration

Jabberwocky can be configured via a TOML file or entirely through CLI flags. Both approaches produce identical behaviour.

## TOML config file

```toml
# jabberwocky.toml
[mirror]
packages = ["polars", "scipy", "autograd", "gradio"]
python_versions = ["3.11", "3.12"]
platforms = ["linux_x86_64", "win_amd64"]
output_dir = "mirror"

# Optional: override the upstream index URLs
index_url = "https://pypi.org/simple"
pypi_url  = "https://pypi.org/pypi"
```

Pass it to the build command:

```bash
jabberwocky build --config jabberwocky.toml
```

CLI flags (`--python`, `--platform`, `--output`) always override the values in the TOML file if both are provided.

## Wishlist file

A wishlist file is a plain text file with one package name per line. Lines that are blank or begin with `#` are ignored.

```
# Core data stack
polars
scipy

# ML
autograd
```

Wishlist files do not contain platform or Python version information — those must be supplied via CLI flags.

```bash
jabberwocky build \
  --wishlist wishlist.txt \
  --python 3.12 \
  --platform linux_x86_64
```

## Configuration reference

| Key | CLI flag | Type | Default | Description |
|---|---|---|---|---|
| `packages` | `--wishlist` (file) | `list[str]` | — | Packages to mirror |
| `python_versions` | `--python` | `list[str]` | — | Target Python versions, e.g. `"3.12"` |
| `platforms` | `--platform` | `list[str]` | — | Target wheel platform tags |
| `output_dir` | `--output` / `-o` | `str` (path) | `"mirror"` | Directory for wheels and index files |
| `index_url` | — | `str` | `https://pypi.org/simple` | PyPI Simple API URL (not currently used for resolution) |
| `pypi_url` | — | `str` | `https://pypi.org/pypi` | PyPI JSON API URL used for dependency resolution |

## Platform tags

Platform tags must match the wheel filename convention from [PEP 425](https://peps.python.org/pep-0425/). Common values:

| OS | Architecture | Tag |
|---|---|---|
| Linux | x86-64 | `linux_x86_64` |
| Linux | ARM64 | `linux_aarch64` |
| Windows | 64-bit | `win_amd64` |
| Windows | 32-bit | `win32` |
| macOS | Intel | `macosx_10_9_x86_64` |
| macOS | Apple Silicon | `macosx_11_0_arm64` |

Jabberwocky treats `manylinux_*_x86_64` and `musllinux_*_x86_64` wheels as compatible with `linux_x86_64`, and similarly for other architectures.

## Python version format

Python versions are strings in `major.minor` format: `"3.11"`, `"3.12"`, `"3.13"`. You can target multiple versions simultaneously.

Jabberwocky matches wheels against the following tag patterns for a given version, e.g. `3.12`:
- `cp312` — CPython 3.12 ABI-specific wheel
- `py312` — pure Python, tagged for 3.12
- `py3` — pure Python, any Python 3
