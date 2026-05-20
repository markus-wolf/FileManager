# StorageMark

Interactive terminal disk space analyser for macOS and Linux.

Traverses a directory tree using a fast C scanner and presents results through a curses TUI with five views: directory tree, file list, file-type summary, time browser, and a what-if simulator for planning cleanups.

---

## Requirements

- macOS or Linux
- Python 3.13 (via pyenv)
- C compiler (`cc`)

---

## Setup

```sh
git clone <repo>
cd FileManager

# Create virtualenv and install
python -m venv .venv
.venv/bin/pip install -e .

# Build the C scanner
make -C storagemark/c
```

Or just use `run.sh` — it builds the scanner automatically if needed.

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
| `1`–`5` | Switch view |
| `j` / `k` | Move cursor down / up |
| `PgDn` / `PgUp` | Page down / up |
| `g` / `G` | Jump to top / bottom |
| `Enter` | Expand directory or drill into selection |
| `Space` | Toggle what-if mark |
| `/` | Filter (glob or `~regex`, prefix `!` to invert) |
| `s` / `S` | Cycle sort column / reverse sort |
| `u` | Toggle size unit (auto / GB / MB / KB / B) — works in all views |
| `t` | Toggle time field in Time view (mtime / atime / ctime) |
| `r` | Re-scan current root |
| `p` | Change root path |
| `e` | Export current view to CSV |
| `q` | Quit |
| `?` | Help overlay |

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
