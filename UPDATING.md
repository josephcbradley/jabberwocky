# Updating the offline mirror

This document explains how to keep the mirror on your offline machine in sync
with the online machine that runs `jabberwocky update`.

---

## Workflow overview

```
Online machine                         Offline machine
─────────────────────────────────────  ──────────────────────────────
jabberwocky update ...
  ├─ resolves & downloads packages
  ├─ archives mirror/ → archives/<ts>/
  ├─ computes diff
  └─ writes diffs/<ts>/
        ├─ files/        (new wheels)
        ├─ simple/       (changed index entries)
        ├─ manifest.json
        └─ APPLY.md      ──────────────────────► follow APPLY.md
                                                  to patch mirror/
```

Each `diffs/<timestamp>/` folder is self-contained and cumulative — it
contains **only the files that changed** since the previous run.

---

## Running an update (online machine)

```bash
jabberwocky update \
  --wishlist wishlist.txt \
  --python 3.12 \
  --platform linux_x86_64
```

Or with a TOML config:

```bash
jabberwocky update --config jabberwocky.toml
```

This produces:

```
archives/
  20260222T134501Z/   ← snapshot of mirror/ before this update
diffs/
  20260222T134501Z/
    files/            ← new wheel files
    simple/           ← updated index entries
    manifest.json     ← machine-readable diff summary
    APPLY.md          ← instructions for the offline machine
mirror/               ← updated in place
```

---

## Applying an update (offline machine)

### 1. Transfer the diff package

Copy the relevant `diffs/<timestamp>/` folder to the offline machine using
whatever transfer method you have (USB drive, sneakernet, rsync over a
one-way link, etc.):

```bash
# Example: rsync to a USB drive
rsync -av diffs/20260222T134501Z/ /media/usb/jabberwocky-update/
```

### 2. Apply the diff

On the offline machine, navigate to the directory that contains your
`mirror/` folder, then follow the instructions in `APPLY.md` inside the
diff package. The commands are always of the form:

```bash
DIFF=<path-to-diff-package>

# Copy new/updated wheel files
cp -r "$DIFF/files/." mirror/files/

# Copy new/updated index entries
cp -r "$DIFF/simple/." mirror/simple/

# Remove wheels that are no longer in the mirror (listed in APPLY.md)
rm -f mirror/files/<removed-wheel>.whl
```

### 3. Restart the server (if running)

If `jabberwocky serve` is already running, restart it to pick up the
updated index:

```bash
# Find and kill the process, then restart
jabberwocky serve --mirror ./mirror --port 8080
```

---

## Applying multiple updates in sequence

If you have missed several update cycles, apply the diff packages in
**chronological order** (oldest timestamp first):

```bash
for diff in diffs/20260220* diffs/20260221* diffs/20260222*; do
    echo "Applying $diff"
    cp -r "$diff/files/." mirror/files/
    cp -r "$diff/simple/." mirror/simple/
    # Check APPLY.md in each diff for any removals
done
```

---

## Directory reference

| Directory | Purpose |
|-----------|---------|
| `mirror/` | Live mirror served by `jabberwocky serve` |
| `archives/<timestamp>/` | Full snapshot of mirror before each update |
| `diffs/<timestamp>/` | Minimal diff package for offline transfer |
| `diffs/<timestamp>/manifest.json` | Machine-readable list of changes |
| `diffs/<timestamp>/APPLY.md` | Human-readable apply instructions |
