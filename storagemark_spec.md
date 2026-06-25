# StorageMark — Detailed Specification

---

## 1. Overview

**StorageMark** is an interactive, terminal-based disk space analyzer and cleanup advisor for macOS and Linux. It traverses a directory tree, computes on-disk space consumption, and presents the results through multiple sorted/filtered views. A "what-if" mode lets users simulate deletions before committing to them.

**Stack:** Python (UI, orchestration, reporting) + C (high-performance traversal and stat collection via a standalone binary called by Python).

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Python layer                      │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │  curses  │  │  data model  │  │  what-if engine│ │
│  │   TUI    │  │  (FileNode   │  │               │ │
│  │          │  │   tree)      │  │               │ │
│  └────┬─────┘  └──────┬───────┘  └───────┬───────┘ │
│       └───────────────┴──────────────────┘         │
│                        │                            │
│              ┌─────────▼──────────┐                │
│              │  scanner interface  │                │
│              └─────────┬──────────┘                │
└────────────────────────┼────────────────────────────┘
                         │  subprocess (binary pipe)
                ┌────────▼────────┐
                │  storagescanner │
                │  (C binary)     │
                └─────────────────┘
```

The C scanner emits a compact binary record stream (default) or newline-delimited JSON (`-j` flag for debugging). The Python layer parses this into a `FileNode` tree, then drives the TUI and all analysis.

---

## 3. C Scanner (`storagescanner`)

### 3.1 Responsibility
Walk a root path recursively using `opendir` / `readdir`, calling `lstat(2)` on every entry. Emit one record per file/directory.

### 3.2 Record fields (per entry)

| Field | Type | Source |
|---|---|---|
| `path` | string | full absolute path |
| `name` | string | basename |
| `type` | char | `f`=file, `d`=dir, `l`=symlink, `o`=other |
| `size_bytes` | uint64 | `st_size` |
| `size_disk` | uint64 | `st_blocks * 512` (actual allocated) |
| `inode` | uint64 | `st_ino` (hard-link dedup) |
| `dev` | uint64 | `st_dev` |
| `uid` | uint32 | `st_uid` |
| `mtime` | int64 | `st_mtime` (epoch seconds) |
| `atime` | int64 | `st_atime` |
| `ctime` | int64 | `st_ctime` / `st_birthtime` (macOS) |
| `depth` | uint16 | depth from root |
| `hardlink_of` | string | path of first occurrence if hard-link duplicate |
| `error` | string | non-empty if stat/opendir failed |

### 3.3 Binary output format (default)

Stream begins with 8-byte magic: `SMRK\x01\x00\x00\x00`

Each record:
```
[72-byte fixed header]  little-endian
  uint8   type
  uint16  depth
  uint8   flags         (reserved)
  uint32  uid
  uint64  size_bytes
  uint64  size_disk
  uint64  inode
  uint64  dev
  int64   mtime
  int64   atime
  int64   ctime
  uint16  path_len
  uint16  name_len
  uint16  hardlink_len
  uint16  error_len
[variable]  path  (path_len bytes, no NUL)
[variable]  name  (name_len bytes, no NUL)
[variable]  hardlink_of  (hardlink_len bytes, no NUL)
[variable]  error  (error_len bytes, no NUL)
```

Python struct format string: `'<BHBIQQQQqqqHHHH'` (72 bytes).

#### Performance comparison (1.07M files)

| Format | Parse time | Throughput |
|--------|-----------|------------|
| JSON (`json.loads`) | 4.55 s | 235K rec/s |
| Binary (`struct.unpack`) | 0.87 s | 1.23M rec/s |

### 3.4 CLI flags

```
storagescanner [OPTIONS] <path>
  -x            do not cross filesystem boundaries (like du -x)
  -L            follow symlinks
  -b            binary output mode (default when called from Python)
  -d <depth>    max depth (0 = unlimited)
  --skip <glob> colon-separated glob patterns to skip
```

JSON output (for debugging) is produced when `-b` is omitted.

### 3.5 Hard-link handling
Track `(dev, inode)` pairs in an open-addressing hash set; count size only on first occurrence. Report subsequent occurrences with `size_bytes=0`, `size_disk=0`, and `hardlink_of=<first_path>`.

---

## 4. Python Data Model

```python
@dataclass(slots=True)          # ~60% less memory vs plain dataclass
class FileNode:
    path: str
    name: str
    type: str          # 'f', 'd', 'l', 'o'
    size_bytes: int    # logical size
    size_disk: int     # st_blocks * 512 (actual allocated)
    inode: int
    dev: int
    uid: int
    mtime: datetime
    atime: datetime
    ctime: datetime
    depth: int
    ext: str           # lowercase extension, '' if none
    hardlink_of: str
    error: str
    children: list     # populated for 'd' nodes
    parent: object     # FileNode | None
    subtree_bytes: int # computed by DirTree.build()
    subtree_disk: int
    subtree_count: int
```

### DirTree.build() — four-pass algorithm

1. **Pass 1** — create `FileNode` objects from raw records, index by path.
2. **Pass 2** — link parent → child. Each node's parent is found by `os.path.dirname(path)`. Simple `append` — no duplicate check (each path is unique, duplicate check was O(n²)).
3. **Pass 3** — iterative DFS (explicit stack) to produce `flat` list in pre-order; populate `ext_map`, count files/dirs.
4. **Pass 4** — subtree aggregation: iterate `flat` in reverse (= post-order); accumulate `subtree_bytes/disk/count` bottom-up.

Raw records are freed immediately after build to halve peak memory usage.

`DirTree` holds:
- `flat: list[FileNode]` — all nodes in DFS order
- `ext_map: dict[str, list[FileNode]]`
- `errors: list[FileNode]`
- cached `file_count`, `dir_count` — O(1) after build

---

## 5. Views (TUI Panels)

All views share a common header and footer:

```
╔══════════════════════════════════════════════════════╗
║  StorageMark  /Users/alex/projects    Scanned: 42.3s ║
║  Total: 47.2 GB  Files: 183,441  Dirs: 12,008        ║
╠══════════════════════════════════════════════════════╣
║  [1]SubDirs  [2]Files  [3]Types  [4]Time  [5]WhatIf  ║
╚══════════════════════════════════════════════════════╝
   ... view content ...
[q]uit  [/]filter  [s]ort  [Space]mark  [e]xport  [?]help
```

### 5.1 View 1 — SubDirectory Tree

Interactive tree. Each row shows:
```
  ▶ node_modules/          34.1 GB  ████████████████░░░░  72%
    ├─ .cache/              8.2 GB  ████░░░░░░░░░░░░░░░░  17%
    └─ packages/           25.9 GB  ████████████░░░░░░░░  55%
```

- `▶` / `▼` to expand/collapse
- Bar scaled to root total
- Columns: name, disk size, bar, % of parent
- Sortable by: size (default), name, file count, last modified

### 5.2 View 2 — File List

Flat or tree-relative file list:
```
  SIZE(DISK)   SIZE(LOG)   MODIFIED            NAME
  12.4 GB      11.9 GB     2025-03-14 09:22    video_raw.mov
   4.1 GB       4.0 GB     2024-11-02 17:44    backup.tar.gz
   ...
```

- Paginated (j/k to scroll, PgUp/PgDn)
- Sortable: disk size, logical size, mtime, atime, name, extension
- Filter bar (`/`) accepts glob or regex
- Can be pre-filtered by extension (drill-in from View 3) or age bucket (drill-in from View 4)

### 5.3 View 3 — File Type Summary

```
  EXT       COUNT    TOTAL DISK    AVG SIZE    % OF TOTAL
  .mov        127     28.4 GB      229 MB      60.2%
  .tar.gz      14      8.1 GB      594 MB      17.1%
  (no ext)  4,201      3.3 GB      814 KB       7.0%
  .py       9,834      1.2 GB      128 KB       2.5%
  ...
```

Selecting an extension drills into View 2 pre-filtered for that type.

### 5.4 View 4 — Time Browser

Heatmap-style summary + sortable list. Time buckets:

```
  BUCKET            FILES    DISK SIZE
  > 2 years old     8,304    22.1 GB   ████████████████████
  1–2 years old     4,102     9.4 GB   ████████░░░░░░░░░░░░
  6–12 months        892      4.8 GB   ████░░░░░░░░░░░░░░░░
  1–6 months         541      2.1 GB   ██░░░░░░░░░░░░░░░░░░
  < 1 month          203    431 MB     ░░░░░░░░░░░░░░░░░░░░
```

Toggle between mtime / atime / ctime with `t`. Selecting a bucket drills into View 2 pre-filtered.

### 5.5 View 5 — What-If Simulator

Mark files/dirs for hypothetical removal using `[space]` in any view. This view shows:

```
  WHAT-IF SCENARIO
  ─────────────────────────────────────────────────────
  Marked for removal:
    ✓  node_modules/           34.1 GB
    ✓  *.mov files (127)       28.4 GB

  Would free:  62.5 GB  (of 47.2 GB used = 132%)
  After:        0.0 GB  remaining (root becomes empty)

  Conflicts / warnings:
    ! node_modules/ overlaps with marked *.mov (3 files)
    ! src/ is < 30 days old

  [Enter] Confirm & show rm commands   [x] Clear all marks
  [p] Export plan to file              [ESC] Back
```

Confirmation produces a shell script (`storagemark_cleanup_<timestamp>.sh`).

---

## 6. Interaction Model

### 6.1 Key bindings (all views)

| Key | Action |
|---|---|
| `1`–`5` | Switch view |
| `j` / `k` | Move cursor down / up |
| `PgDn` / `PgUp` | Page down / up |
| `g` / `G` | Jump to top / bottom |
| `Enter` | Expand dir or drill into selection |
| `Backspace` | Navigate up one level |
| `Space` | Toggle what-if mark on item |
| `/` | Open filter bar |
| `s` | Cycle sort column |
| `S` | Reverse sort direction |
| `u` | Toggle size unit globally (auto / GB / MB / KB / B) |
| `t` | Toggle time field in View 4 (mtime / atime / ctime) |
| `r` | Re-scan current root |
| `p` | Change root path (prompts inline) |
| `e` | Export current view to CSV |
| `q` | Quit |
| `?` | Help overlay |

### 6.2 Filter bar
- Accepts shell globs (`*.log`, `node_modules`) or regex (prefix with `~`)
- Applied as an include filter; `!` prefix inverts
- Persists until cleared with `Escape`

---

## 7. Startup & CLI

```
storagemark [OPTIONS] [path]

  path                   Root to scan (default: current directory)
  -o, --once             Non-interactive: print summary and exit
  -f, --format <fmt>     Output format for --once: text|json|csv
  -v, --view <n>         Start in view N (1–5)
  -d, --depth <n>        Max scan depth
  -x, --one-filesystem   Do not cross mount points
  --skip <glob>          Skip matching paths (repeatable)
  --threshold <size>     Hide entries smaller than size (e.g. 10MB)
  --scanner <path>       Override path to storagescanner binary
```

---

## 8. Non-Interactive / Scripting Mode (`--once`)

```
$ storagemark --once --format json /tmp
{
  "root": "/tmp",
  "total_disk_bytes": 1234567890,
  "file_count": 4201,
  "dir_count": 312,
  "top_dirs": [...],
  "top_files": [...],
  "by_extension": {...}
}
```

---

## 9. Export

From any view, `e` exports:
- **CSV**: one row per visible entry, all columns
- **JSON**: full subtree from current root node
- **Shell script** (what-if view only): generated by `export_cleanup_script()` in `export.py`

### Cleanup script behaviour

By default the script previews every item with its size and prompts for confirmation:

```
StorageMark cleanup — <timestamp>
Items to be permanently deleted (N total, X.XX GB):

  <size>  <path>
  ...

Delete all N items? This cannot be undone. [y/N]
```

Any answer other than `y` / `yes` aborts with no changes. Pass `-y` or `--yes` to skip the prompt (for pipelines / scheduled jobs):

```sh
./storagemark_cleanup_<timestamp>.sh -y
```

`set -e` is active during deletion so the script halts on the first error.

---

## 10. File Layout

```
storagemark/
├── c/
│   ├── storagescanner.c   # main scanner; binary + JSON output
│   ├── scanner.h
│   ├── hashset.c/.h       # open-addressing inode dedup
│   └── Makefile
├── python/
│   ├── __main__.py        # entry point, arg parsing
│   ├── scanner.py         # subprocess wrapper; binary struct parser
│   ├── model.py           # FileNode (slots), DirTree (iterative build)
│   ├── tui/
│   │   ├── app.py         # curses main loop, background scan thread
│   │   ├── header.py      # header / footer rendering
│   │   ├── views/
│   │   │   ├── subdirs.py
│   │   │   ├── files.py
│   │   │   ├── types.py
│   │   │   ├── time_view.py
│   │   │   └── whatif.py
│   │   └── widgets.py     # ScrollList, FilterBar, bar chart
│   └── export.py
├── storagemark/__init__.py  # package marker + __version__ (single version source)
├── setup.py                 # build hook: compiles C scanner at build time
├── pyproject.toml           # uv / setuptools metadata; version read dynamically
├── uv.lock
├── bump.py                  # bump version + commit + create v<x> git tag
├── run.sh                   # dev launcher (uv run)
└── README.md
```

### Versioning & releases

`storagemark/__init__.py:__version__` is the single source of truth;
`pyproject.toml` declares `dynamic = ["version"]` and reads it via
`[tool.setuptools.dynamic]`. `storagemark --version` prints it. `bump.py`
(`patch|minor|major|X.Y.Z`, `--push`, `--dry-run`) edits that one file,
commits "Release vX.Y.Z", and creates an annotated `vX.Y.Z` tag.

### Packaging & distribution

Distributed as a **uv tool**. End users install with:

```sh
uv tool install git+https://github.com/markus-wolf/FileManager
uv tool upgrade storagemark      # update
```

- uv provides an isolated Python 3.13 automatically (no system Python needed).
- `setup.py` defines a custom `build_py` that compiles `storagescanner` from C
  during the wheel build; the binary and the C sources are shipped as
  `package-data` under `storagemark/c/`.
- If no compiler is available at build time, the build warns but does not fail;
  `scanner.py:_compile_scanner()` recompiles from the shipped sources on first
  run (uv-tool environments are user-writable).

---

## 11. Performance

Benchmarked on a real home directory (**1.07M files, 133 GB**):

| Stage | Time |
|---|---|
| C scanner → binary pipe | ~3 s |
| Python `struct.unpack` parse | ~1.7 s |
| `DirTree.build()` (link + aggregate) | ~4.2 s |
| **Total to interactive TUI** | **~9 s** |

### Optimisations implemented

| Optimisation | Benefit |
|---|---|
| Binary record format | 5× faster parsing vs JSON |
| `@dataclass(slots=True)` on `FileNode` | ~60% less memory per object |
| Iterative DFS in `DirTree.build()` | No recursion limit; faster for 1M+ nodes |
| O(1) child append (removed `not in` check) | Eliminated O(n²) parent-linking bug |
| Raw records freed after build | Halves peak memory usage |
| `file_count`/`dir_count` cached at build | O(1) property access |

Sorting and filtering operate on the in-memory tree (no re-scan). Re-scan is triggered only explicitly (`r`).

---

## 12. Error Handling

- Permission denied on a directory: recorded in `errors` list, count shown in footer
- Scan interrupted (Ctrl-C): background scan is stopped immediately; whatever records have arrived are built into a partial `DirTree`; TUI continues with a `[PARTIAL]` badge in the header. Pass `-y` at the prompt if you later run the generated cleanup script non-interactively.
- Symlink cycles: detected via `(dev, inode)` tracking in C layer; skipped silently

---

## 13. Platform Notes

| Feature | macOS | Linux |
|---|---|---|
| Directory walk | `opendir` / `readdir` | same |
| Birth time | `st_birthtimespec.tv_sec` | unavailable (shows `--`) |
| Filesystem boundary | `-x` uses `st_dev` comparison | same |
| Disk usage | `st_blocks * 512` | same |
| Binary output | `write(STDOUT_FILENO, ...)` | same |
