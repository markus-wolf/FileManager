# StorageMark — Detailed Specification

---

## 1. Overview

**StorageMark** is an interactive, terminal-based disk space analyzer and cleanup advisor for macOS and Linux. It traverses a directory tree, computes on-disk space consumption, and presents the results through multiple sorted/filtered views. A "what-if" mode lets users simulate deletions before committing to them.

**Stack:** Python (UI, orchestration, reporting) + C (high-performance traversal and stat collection via a compiled extension or standalone binary called by Python).

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
                         │  ctypes / subprocess
                ┌────────▼────────┐
                │   C scanner     │
                │  (storagescanner   │
                │   .so / binary) │
                └─────────────────┘
```

The C scanner emits newline-delimited JSON (or a compact binary record format) to stdout; the Python layer parses this into a `FileNode` tree, then drives the TUI and all analysis.

---

## 3. C Scanner (`storagescanner`)

### 3.1 Responsibility
Walk a root path with `nftw(3)` (Linux) / `fts_open(3)` (macOS), calling `stat(2)` / `lstat(2)` on every entry. Emit one record per file/directory.

### 3.2 Record fields (per entry)

| Field | Type | Source |
|---|---|---|
| `path` | string | full absolute path |
| `name` | string | basename |
| `type` | char | `f`=file, `d`=dir, `l`=symlink |
| `size_bytes` | uint64 | `st_size` |
| `blocks_512` | uint64 | `st_blocks` (actual disk usage) |
| `inode` | uint64 | `st_ino` (hard-link dedup) |
| `dev` | uint64 | `st_dev` |
| `uid` | uint32 | `st_uid` |
| `mtime` | int64 | `st_mtime` (epoch seconds) |
| `atime` | int64 | `st_atime` |
| `ctime` | int64 | `st_ctime` / `st_birthtime` (macOS) |
| `depth` | uint16 | depth from root |
| `error` | string | non-empty if stat failed |

### 3.3 Output format
Default: one JSON object per line (`\n`-terminated).  
Optional flag `--binary`: fixed-width binary records (faster parsing for very large trees).

### 3.4 CLI flags

```
storagescanner [OPTIONS] <path>
  -x            do not cross filesystem boundaries (like du -x)
  -L            follow symlinks
  -b            binary output mode
  -d <depth>    max depth (0 = unlimited)
  -j <threads>  parallel scan threads (default: CPU count)
  --skip <glob> colon-separated glob patterns to skip
```

### 3.5 Hard-link handling
Track `(dev, inode)` pairs in a hash set; count size only on first occurrence. Report subsequent occurrences with `size_bytes=0` and `hardlink_of=<first_path>`.

---

## 4. Python Data Model

```python
@dataclass
class FileNode:
    path: str
    name: str
    type: str          # 'f', 'd', 'l'
    size_bytes: int    # logical size
    size_disk: int     # blocks_512 * 512 (actual allocated)
    inode: int
    dev: int
    uid: int
    mtime: datetime
    atime: datetime
    ctime: datetime
    depth: int
    ext: str           # lowercase extension, '' if none
    children: list     # only populated for 'd'
    parent: 'FileNode | None'
    # computed after full scan:
    subtree_bytes: int
    subtree_disk: int
    subtree_count: int
```

`DirTree` wraps the root `FileNode` and holds:
- flat list of all nodes (for sorting/filtering)
- `ext_map: dict[str, list[FileNode]]`
- `uid_map: dict[int, list[FileNode]]`
- aggregated totals

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
[q]uit  [/]filter  [s]ort  [e]xpand  [d]elete  [?]help
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

Toggle between mtime / atime / ctime. Selecting a bucket drills into View 2 pre-filtered.

### 5.5 View 5 — What-If Simulator

Mark files/dirs for hypothetical removal using `[space]` in any view. This view shows:

```
  WHAT-IF SCENARIO
  ─────────────────────────────────────────────────────
  Marked for removal:
    ✓  node_modules/           34.1 GB
    ✓  *.mov files (127)       28.4 GB
    ✗  src/ (protected)         1.2 GB  [cannot remove]

  Would free:  62.5 GB  (of 47.2 GB used = 132%)
  After:        0.0 GB  remaining (root becomes empty)

  Conflicts / warnings:
    ! node_modules/ overlaps with marked *.mov (3 files)
    ! src/ is < 30 days old

  [Enter] Confirm & show rm commands   [x] Clear all marks
  [p] Export plan to file              [ESC] Back
```

Confirmation produces a shell script (`storagemark_cleanup_<timestamp>.sh`) — it does **not** execute deletions itself.

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
| `u` | Toggle size unit (auto / B / KB / MB / GB) |
| `x` | Toggle cross-filesystem |
| `r` | Re-scan current root |
| `p` | Change root path |
| `e` | Export current view to CSV/JSON |
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
- **Shell script** (what-if view only): `rm -rf` commands for marked items, with a safety header

---

## 10. File Layout

```
storagemark/
├── c/
│   ├── storagescanner.c      # main scanner
│   ├── scanner.h
│   ├── hashset.c/.h       # inode dedup
│   └── Makefile
├── python/
│   ├── __main__.py        # entry point, arg parsing
│   ├── scanner.py         # subprocess wrapper + parser
│   ├── model.py           # FileNode, DirTree, aggregation
│   ├── tui/
│   │   ├── app.py         # curses init, view router, key dispatch
│   │   ├── header.py
│   │   ├── views/
│   │   │   ├── subdirs.py
│   │   │   ├── files.py
│   │   │   ├── types.py
│   │   │   ├── time_view.py
│   │   │   └── whatif.py
│   │   └── widgets.py     # bar chart, scrollable list, filter bar
│   └── export.py
├── setup.py / pyproject.toml
└── README.md
```

---

## 11. Performance Targets

| Tree size | Scan time (C scanner) | TUI response |
|---|---|---|
| 10K files | < 0.5 s | < 50 ms per keypress |
| 100K files | < 3 s | < 100 ms |
| 1M files | < 30 s | < 200 ms |

Sorting and filtering operate on the in-memory tree (no re-scan). Re-scan is triggered only explicitly (`r`).

---

## 12. Error Handling

- Permission denied on a directory: record in a `scan_errors` list, display count in footer, accessible via `?e` overlay
- Scan interrupted (Ctrl-C during scan): partial results displayed with `[PARTIAL]` badge
- Symlink cycles: detected via `(dev, inode)` tracking in C layer; skipped with a warning

---

## 13. Platform Notes

| Feature | macOS | Linux |
|---|---|---|
| Directory walk | `fts_open` | `nftw` |
| Birth time | `st_birthtime` | unavailable (show `--`) |
| Filesystem boundary | `-x` uses `st_dev` | same |
| Disk usage | `st_blocks * 512` | same |
