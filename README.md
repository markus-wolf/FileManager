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
| `u` | Toggle size unit (auto / GB / MB / KB / B) |
| `t` | Toggle time field in Time view |
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

The script contains `rm -rf` commands for all marked items. It does **not** execute deletions itself — review it before running.

---

## Project Layout

```
storagemark/
├── c/
│   ├── storagescanner.c   # recursive walker using lstat / fts
│   ├── hashset.c/.h       # inode dedup for hard links
│   ├── scanner.h
│   └── Makefile
└── python/
    ├── __main__.py        # CLI entry point
    ├── model.py           # FileNode, DirTree, aggregation
    ├── scanner.py         # subprocess wrapper + NDJSON parser
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

| Tree size | Scan time |
|-----------|-----------|
| 10K files | < 0.5 s |
| 100K files | < 3 s |
| 1M files | < 30 s |

Sorting and filtering operate on the in-memory tree — no re-scan needed.
