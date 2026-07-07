# StorageMark

Interactive terminal disk space analyser for macOS and Linux.

Traverses a directory tree using a fast C scanner and presents results through a curses TUI with five views: directory tree, file list, file-type summary, time browser, and a what-if simulator for planning cleanups.

---

## Install (macOS / Linux)

StorageMark installs as a [uv](https://docs.astral.sh/uv/) tool. uv manages its
own isolated Python — you do **not** need a system Python, pyenv, or a venv.

**1. Install uv** (once per machine):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Install StorageMark:**

```sh
uv tool install git+https://github.com/markus-wolf/FileManager
```

That builds the C scanner and puts a `storagemark` command on your `PATH`
(usually `~/.local/bin`). Now run it from anywhere:

```sh
storagemark ~
```

### Updating

```sh
uv tool upgrade storagemark
```

### Uninstalling

```sh
uv tool uninstall storagemark
```

> **Note on the C scanner.** The fast scanner is compiled from C at install
> time, which needs Xcode Command Line Tools on macOS (`xcode-select --install`).
> If a compiler isn't available during install, StorageMark compiles the scanner
> automatically the first time you run it. No compiler ever needed if a working
> binary is already present.

---

## Install for all users (shared Mac)

The default `uv tool install` is **per-user**. To make `storagemark` available
to every account on a Mac, redirect uv's directories to shared, world-readable
locations and run the install as admin.

**Prereqs (once per machine):**

```sh
xcode-select --install     # C compiler, so the scanner builds for everyone
brew install uv            # uv available outside any single user's home
```

**Install system-wide** (run from an admin account):

```sh
sudo -H env \
  UV_TOOL_DIR=/opt/uv/tools \
  UV_TOOL_BIN_DIR=/usr/local/bin \
  UV_PYTHON_INSTALL_DIR=/opt/uv/python \
  UV_CACHE_DIR=/opt/uv/cache \
  /opt/homebrew/bin/uv tool install --managed-python --python 3.13 \
  git+https://github.com/markus-wolf/FileManager

sudo chmod -R a+rX /opt/uv /usr/local/bin/storagemark
```

Every user now gets `storagemark` because `/usr/local/bin` is on the default
PATH. Verify from a non-admin account with `storagemark ~`.

**Why each setting matters:**

| Setting | Purpose |
|---------|---------|
| `sudo -H` | Use root's HOME so uv doesn't write into the admin's `~/.cache` |
| `UV_TOOL_BIN_DIR=/usr/local/bin` | the `storagemark` command — already on everyone's PATH |
| `UV_TOOL_DIR=/opt/uv/tools` | the tool's isolated venv — shared, not under a home dir |
| `UV_PYTHON_INSTALL_DIR=/opt/uv/python` | the Python the venv uses — must be world-readable |
| `UV_CACHE_DIR=/opt/uv/cache` | build cache — keeps it out of any user's home |
| `--managed-python` | use uv's own Python, not a stray one on PATH (avoids odd `_curses`/terminfo builds) |
| `chmod -R a+rX` | open read+execute to all (sudo creates everything root-owned) |

**Updating later** (same env vars):

```sh
sudo -H env UV_TOOL_DIR=/opt/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin \
  UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_CACHE_DIR=/opt/uv/cache \
  /opt/homebrew/bin/uv tool upgrade storagemark
sudo chmod -R a+rX /opt/uv
```

> If `which uv` reports a path other than `/opt/homebrew/bin/uv`, substitute it
> in the commands above. On Apple Silicon with Homebrew it is `/opt/homebrew/bin/uv`.

### Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Stuck on "Scanning… (0 records)" | Scanner wasn't built and the shared dir is read-only. Run `storagemark ~ --once` to see the error; install Xcode CLT and reinstall as admin. |
| `Failed to initialize cache … Permission denied` | A previous `sudo` install wrote root-owned files into your `~/.cache/uv`. Fix: `sudo chown -R "$(whoami)" ~/.cache/uv` (or `sudo rm -rf ~/.cache/uv`). Use `sudo -H` + `UV_CACHE_DIR` to avoid it. |
| `could not find terminfo database` | Unusual Python build (e.g. ServBay). Workaround: `TERMINFO_DIRS=/usr/share/terminfo storagemark ~`. Reinstalling with `--managed-python` (above) avoids it. |
| Scan races ahead then crawls (a few records/min) | A slow subtree — usually a network mount (NAS/SMB), iCloud Drive (`~/Library/Mobile Documents`), or a sleeping external disk. The scanning screen shows the current path (`at: …`). Ctrl-C for partial results, then retry with `-x` (skip other volumes) or `--skip "<name>"` (skip a same-volume folder like iCloud), or scan a specific subfolder. To find the culprit live: `pgrep storagescanner \| xargs -I{} sudo lsof -p {} \| tail`. |

---

## Requirements

- macOS (tested on 15.7 and 26) or Linux
- uv (which provides Python 3.13 automatically)
- A C compiler — Xcode Command Line Tools on macOS (`cc` / `clang`)

---

## Development

```sh
git clone https://github.com/markus-wolf/FileManager
cd FileManager

uv sync          # creates .venv, builds the C scanner via the build hook
uv run storagemark ~
```

Or use the dev launcher, which rebuilds the C scanner if its source changed:

```sh
./run.sh ~
```

### Releasing a new version

The version lives in **one** place — `storagemark/__init__.py` — and
`pyproject.toml` reads it dynamically. Check it any time with:

```sh
storagemark --version
```

To cut a release, use `bump.py`. It updates the version, commits, and creates
an annotated `v<version>` git tag in one step:

```sh
./bump.py patch          # 0.1.0 -> 0.1.1
./bump.py minor          # 0.1.0 -> 0.2.0
./bump.py major          # 0.1.0 -> 1.0.0
./bump.py 1.4.0          # set an explicit version

./bump.py patch --dry-run   # preview only, change nothing
./bump.py patch --push      # also push the commit and tag to origin
```

Without `--push` it prints the exact `git push` commands to publish. End users
then get the new version with `uv tool upgrade storagemark` (uv installs from
the default branch; pushing the tag just marks the release).

---

## Usage

```sh
# Interactive TUI (current directory)
./run.sh

# Scan a specific path
./run.sh /Users/alex/Documents

# Start in a specific view (1–5)
./run.sh /Users/alex -v 3

# Non-interactive summary
./run.sh /tmp --once

# JSON or CSV output
./run.sh /tmp --once --format json
./run.sh /tmp --once --format csv

# Limit scan depth
./run.sh /Users/alex -d 4

# Stay within one filesystem
./run.sh / -x

# Skip directories
./run.sh ~ --skip .venv --skip node_modules
```

---

## Views

| Key | View | Description |
|-----|------|-------------|
| `1` | SubDirs | Expandable directory tree sorted by disk usage |
| `2` | Files | Sortable/filterable flat file list |
| `3` | Types | File-type summary with drill-in by extension |
| `4` | Time | Files grouped by age (mtime / atime / ctime) |
| `5` | What-If | Simulate deletions and export a cleanup script |

---

## Key Bindings

| Key | Action |
|-----|--------|
| `1`–`5` | Switch view (tabs are also mouse-clickable) |
| `j` / `k` | Move cursor down / up |
| `PgDn` / `PgUp` | Page down / up |
| `g` / `G` | Jump to top / bottom |
| `Enter` | Expand directory or drill into selection |
| `Space` | Mark / unmark — a file, a directory subtree, a whole extension (Types), or an age bucket (Time) |
| `A` | Mark **all** rows matching the current filter (Files view) |
| `x` | Clear all marks |
| `D` | Remove marked items (Trash or permanent — see below) |
| `/` | Filter (glob or `~regex`, prefix `!` to invert) |
| `s` / `S` | Cycle sort column / reverse sort |
| `u` | Toggle size unit (auto / GB / MB / KB / B) |
| `t` | Toggle time field in Time view (mtime / atime / ctime) |
| `r` | Re-scan current root |
| `p` | Change root path |
| `e` | Export current view to CSV |
| `E` | Show scan errors (when any) |
| `Ctrl-C` | During a scan: interrupt dialog (see below); when idle: quit |
| `q` / `Ctrl-Q` | Quit |
| `?` | Help overlay |

The legacy curses interface is still available with `storagemark --classic`.

---

## Interrupting a scan (`Ctrl-C`)

Large scans (a full home directory can be 1M+ objects) run in the
background — the header shows a live counter and the path currently being
walked. Press `Ctrl-C` while scanning to get:

```
Scan running — 412,388 objects so far

  Ctrl-Q    quit StorageMark
  Ctrl-C    stop scanning, show PARTIAL results
  any key   keep scanning
```

- **Ctrl-Q** quits cleanly — the scanner subprocess is terminated, nothing
  keeps running in the background.
- **Ctrl-C** (again) stops the scan and shows everything collected so far,
  flagged `⚠PARTIAL` in the header. Press `r` to re-scan later.
- **Any other key** dismisses the dialog; the scan was never paused. If the
  scan finishes while the dialog is open, it closes itself.

---

## Appearance

The UI ships with a custom **Norton Commander** theme built from the CGA
palette — blue panels, cyan chrome, yellow marks — applied by default.
Warnings use bright orange rather than the historical red for readability
on blue. Press `Ctrl-P` and type "theme" to switch to any of Textual's
built-in themes at runtime.

---

## Removing files (`D`)

Mark anything with `Space` (single items, whole directory subtrees, every
file of an extension from the Types view, a whole age bucket from the Time
view, or `A` for all filtered rows). The header keeps a running tally:
`Marked: N items, X GB`.

Press `D` to open the removal dialog. It shows every top-level item (nested
marks are de-duplicated automatically), the total size, and warnings for
recently-modified items or protected folders (`~/Documents`, `~/Library`, …).

Two ways out, deliberately asymmetric:

- **Move to Trash** — press `t` or click the button. Recoverable: the Trash
  *is* the undo (macOS `~/.Trash`, Linux XDG trash with restore metadata).
- **Permanent delete** — type `yes` and press Enter. Not recoverable.

A progress bar tracks large removals; failures (e.g. cross-volume Trash
moves, permissions) are collected in the `E` errors viewer. Afterwards the
in-memory tree is pruned in place — totals, charts, and views update
instantly with no rescan.

The What-If view still exports an auditable `storagemark_cleanup_*.sh`
script as an alternative to in-app removal.

---

## Scan errors

The header shows an error count (`Errors: N`) for paths that couldn't be
stat'd or opened — usually permission-denied folders macOS protects, even for
your own account. When there are any, the footer shows **`[E]rrors(N)`**.

Press **`E`** to open a scrollable list of every failed path with its error
message (`j`/`k` to scroll, `Esc` to close). Errored entries simply contribute
0 bytes to the totals; the rest of the scan is unaffected.

To see them outside the TUI, export to CSV and filter the `error` column:

```sh
storagemark ~ --once --format csv > /tmp/sm.csv
python3 -c "import csv; [print(r['error'],'→',r['path']) for r in csv.DictReader(open('/tmp/sm.csv')) if r['error']]"
```

---

## What-If / Cleanup

Mark files and directories with `Space` in any view, then switch to view 5 to see the projected space savings and any warnings (young files, overlapping marks). Press `Enter` or `p` to export a shell script:

```sh
storagemark_cleanup_<timestamp>.sh
```

Running the script without arguments shows exactly what will be deleted, the size of each item, and asks for confirmation before proceeding:

```
StorageMark cleanup — 20260520_143022
Items to be permanently deleted (3 total, 14.70 GB):

    8192.0 MB  /Users/alex/Downloads/old_backup
    4096.0 MB  /Users/alex/tmp/build_cache
    2432.0 MB  /Users/alex/.Trash/video_raw.mov

Delete all 3 items? This cannot be undone. [y/N]
```

Answer anything other than `y` / `yes` to abort with no changes made.

To skip the prompt (e.g. in a pipeline or scheduled job):

```sh
./storagemark_cleanup_<timestamp>.sh -y
# or
./storagemark_cleanup_<timestamp>.sh --yes
```

---

## Project Layout

```
storagemark/
├── c/
│   ├── storagescanner.c   # recursive walker using lstat; binary + JSON output
│   ├── hashset.c/.h       # inode dedup for hard links
│   ├── scanner.h
│   └── Makefile
└── python/
    ├── __main__.py        # CLI entry point
    ├── model.py           # FileNode (slotted), DirTree, iterative build
    ├── scanner.py         # subprocess wrapper; binary struct parser
    ├── export.py          # CSV, JSON, shell script export
    └── tui/
        ├── app.py         # curses main loop, background scan thread
        ├── header.py      # header / footer rendering
        ├── widgets.py     # ScrollList, FilterBar, bar chart
        └── views/
            ├── subdirs.py
            ├── files.py
            ├── types.py
            ├── time_view.py
            └── whatif.py
```

---

## Performance

Benchmarked on a real home directory with **1.07 million files**:

| Stage | Time |
|-------|------|
| C scanner (binary mode) | ~3 s |
| Python binary parse (`struct.unpack`) | ~1.7 s |
| `DirTree.build()` — link + aggregate | ~4.2 s |
| **Total to interactive TUI** | **~9 s** |

### Why binary mode?

The C scanner defaults to a compact binary record format (`-b` flag internally) rather than JSON. Benchmarks on 1M files:

| Format | Parse time | Throughput |
|--------|-----------|------------|
| JSON (`json.loads`) | 4.55 s | 235K rec/s |
| Binary (`struct.unpack`) | 0.87 s | 1.23M rec/s |

**5× faster** with no external dependencies. JSON output is still available for debugging (`--format json` in `--once` mode).

### Large tree optimisations

- `FileNode` uses `@dataclass(slots=True)` — ~60% less memory per object vs a plain dataclass
- Parent-linking and subtree aggregation use **iterative DFS** (no recursion limit issues)
- Raw scan records are freed immediately after `DirTree.build()` to halve peak memory
- `file_count` / `dir_count` are computed during build — O(1) lookups, not O(n) scans

Sorting and filtering operate on the in-memory tree — no re-scan needed.
