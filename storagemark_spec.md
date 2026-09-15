# StorageMark — Detailed Specification

---

## 1. Overview

**StorageMark** is an interactive, terminal-based disk space analyzer and cleanup advisor for macOS and Linux. It traverses a directory tree, computes on-disk space consumption, and presents the results through multiple sorted/filtered views. A "what-if" mode lets users simulate deletions before committing to them.

**Stack:** Python (UI, orchestration, reporting) + C (high-performance traversal and stat collection via a standalone binary called by Python).

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ Python package storagemark/python/                                 │
│                                                                    │
│  ui/            Textual application                                │
│    app.py         header, tabs, footer, key routing, overlays      │
│    filelist.py    Files view: virtualized list, ~1M rows           │
│    views.py       SubDirs tree, Types, Time, What-If               │
│    remove.py      removal confirm + progress                       │
│    rules_screen.py  rule picker (f), rule from path (F)            │
│    marks.py       mark set    theme.py  Norton Commander palette   │
│         │                                                          │
│         ▼  (none of the modules below import Textual)              │
│  model.py      FileNode, DirTree: build, prune                     │
│  rules.py      rule matching, ~/.config/storagemark/rules.toml     │
│  trash.py      platform Trash / permanent delete                   │
│  clipboard.py  OSC 52 + pbcopy / wl-copy / xclip / xsel            │
│  export.py     CSV, JSON, cleanup script                           │
│  scanner.py    runs the C scanner, parses its record stream        │
│         │                                                          │
└─────────┼──────────────────────────────────────────────────────────┘
          │  subprocess; stdout pipe carrying binary records (§3.3)
   ┌──────▼─────────┐
   │ storagescanner │  C: opendir/readdir + lstat per entry
   └────────────────┘
```

**Data flow.** `scanner.py:stream_records` starts `storagescanner -b` and
parses its binary records. `DirTree.build` (§4) turns them into the tree.
The UI runs the scan in a thread worker (`app.py:scan_worker`); reading the
pipe waits on I/O, so the header's object counter keeps updating while the
scan runs (asserted by `test_interrupt_dialog_flow`). Removal runs in a
second thread worker (`delete_worker`), then `DirTree.prune` updates the
tree in place instead of rescanning.

**Scanner output.** With `-b` the scanner writes the binary format — what
`scanner.py` always passes. Without `-b` it writes newline-delimited JSON,
kept for inspecting scanner output by hand. There is no separate JSON flag.

**Non-interactive mode.** `storagemark --once` (`__main__.py`) scans, builds
the tree and prints a summary as text, JSON or CSV without starting the UI
(§8).

**Dependency direction.** `ui/` imports the modules beneath it; none of those
import Textual. `rules.py`, `trash.py` and `clipboard.py` are tested without a
UI.

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

## 5. Views

Examples below were captured from StorageMark v1.3.0 scanning the
`storagemark/` source folder with two files marked; the Files status line
(§5.2) and the SubDirs root row (§5.1) were recaptured after the v1.3.1
changes to them. Sizes are small because the folder is small; the layout is
what matters.

### 5.0 Screen layout

```
 StorageMark  baia:/Users/alex/Claude/FileManager/storagemark    Scanned: 0.0s                v1.3.0
 Total: 464.0 KB  Files: 41  Dirs: 7  Errors: 0  Marked: 2 items, 100.0 KB
 SubDirs  Files  Types  Time  What-If
   … active view …
 q Quit  ? Help  1 SubDirs  2 Files  3 Types  4 Time  5 What-If  / Filter  e CSV  E Errors  …
```

- **Header, line 1:** host and root path, scan status, version right-aligned.
  While scanning the status reads `SCANNING…  N objects`. After an
  interrupted scan the path is preceded by `⚠PARTIAL`. On a narrow terminal
  the path is cut with `…`; the version is never pushed off the line.
- **Header, line 2:** totals and the error count. `Marked: N items, X` when
  anything is marked; `Rule: <name> (Esc clears)` while a rule is applied.
  During a scan this line shows the object count and the path being walked.
- **Tabs:** `SubDirs`, `Files`, `Types`, `Time`, `What-If`, selected with
  `1`–`5`, the mouse, or `h`/`l` when the tab strip has focus. The app opens
  on **Files**.
- **Footer:** the Textual `Footer`, showing only the bindings marked visible:
  `q ? 1–5 / e E r p u D f`. It is cut off on narrow terminals. Every key is
  listed in the `?` overlay (§5.6).
- **Colours:** the Norton Commander theme (`ui/theme.py`) — CGA blue
  panels, cyan chrome, yellow marks, orange warnings.

### 5.1 SubDirs — directory tree

```
  …/Claude/FileManager/storagemark   464.0 KB  ████████████████████ 100.0%
      python                             380.0 KB  ████████████████░░░░  81.9%
      c                                   76.0 KB  ███░░░░░░░░░░░░░░░░░  16.4%
      __init__.py                          4.0 KB  ░░░░░░░░░░░░░░░░░░░░   0.9%
```

- Row: mark (`●`), name cut to 32 characters, size, 20-cell bar, percentage.
  The root row's name is its full path; when that is longer than 32
  characters it keeps the end (`…/Claude/FileManager/storagemark`), since the
  header already shows the whole path.
- **The bar and percentage are shares of the whole scan, not of the parent
  folder.**
- Size is `display_size`: a folder's whole subtree, a file's own allocation.
- Children are listed largest first. The order is fixed; `s` does not apply
  here.
- Built lazily (`Tree`): a folder's children are created when it is first
  expanded. Deleting items removes their rows without rebuilding the tree,
  so expanded folders stay expanded.
- Keys: `j`/`k` move; `l` expands; Enter toggles; `h` collapses, or moves
  to the parent if already collapsed or on a file; `Space` marks the item (a folder mark covers
  its whole subtree); `y` copies its path; `F` builds a rule from it.

### 5.2 Files — flat file list

```
●    60.0 KB     57.8 KB  2026-09-15 21:35  app.cpython-313.pyc
●    40.0 KB     39.0 KB  2026-09-15 21:33  app.py
     36.0 KB     34.4 KB  2026-07-07 22:15  storagescanner
     28.0 KB     25.8 KB  2026-09-15 14:04  rules.cpython-313.pyc
sort DISK↓ │ /Users/alex/Claude/FileManager/storagemark/python/ui/__pycache__/app.cpython-313.pyc
```

- Row: mark, size on disk, logical size, modification time, name. There is
  no column header row.
- The last line is the **status line**: the current sort, then the full path
  of the cursor row, cut from the left so the name stays visible.
- Virtualized with Textual's Line API over `DirTree.flat`: only visible rows
  are rendered, so the view handles about a million rows (§14).
- **Sort:** `s` cycles disk size → logical size → modified → accessed → name
  → extension; `S` reverses. Default: disk size, largest first. The status
  line shows it as `DISK LOGICAL MODIFIED ACCESSED NAME EXT` with `↓`
  (descending) or `↑`.
- **What the list contains** — each narrows the one before, and all combine:
  1. all scanned files, or a rule's matches, which may include folders (`f`,
     §15);
  2. an extension or age bucket chosen in Types or Time (Enter there);
  3. marked items only (`M`);
  4. the `/` filter: part of the name, `~regex`, or `!` to invert. Esc clears
     it.
  Esc in the list itself clears an applied rule.
- Keys: `Space` marks and moves down; `A` / `U` mark or unmark every row in the
  current list (`A` with nothing narrowing it asks first); `y` / `Y` copy the
  cursor path or all marked paths; `u` changes the size unit; `F` builds a rule
  from the cursor row. Mouse drag selects text for copying.

### 5.3 Types — by extension

```
EXT       COUNT  MARKED  TOTAL DISK  AVG       %      
.pyc      17     ● 1     232.0 KB    13.6 KB   50.0%  ██████████░░░░░░░░░░
.py       17     ● 1     156.0 KB     9.2 KB   33.6%  ███████░░░░░░░░░░░░░
(no ext)  2               40.0 KB    20.0 KB    8.6%  ██░░░░░░░░░░░░░░░░░░
.c        2               20.0 KB    10.0 KB    4.3%  █░░░░░░░░░░░░░░░░░░░
```

- One row per extension, largest total first. `(no ext)` collects files
  without one.
- `MARKED`: `● all` when every file of that extension is marked, `● N` when
  some are, blank otherwise.
- Keys: Enter opens Files narrowed to that extension; `Space` marks every file
  of the extension, or unmarks them all if they are already all marked.

### 5.4 Time — by age

```
BUCKET       FILES  MARKED  DISK SIZE  %      
> 2 years    0              0 B          0.0%  ░░░░░░░░░░░░░░░░░░░░
1–2 years    0              0 B          0.0%  ░░░░░░░░░░░░░░░░░░░░
6–12 months  0              0 B          0.0%  ░░░░░░░░░░░░░░░░░░░░
1–6 months   29             240.0 KB    51.7%  ██████████░░░░░░░░░░
< 1 month    12     ● 2     224.0 KB    48.3%  ██████████░░░░░░░░░░
```

- Five fixed buckets. Boundaries: 730, 365, 182 and 30 days before now.
- `t` switches which timestamp is used: modified (`mtime`, default), accessed
  (`atime`), or `ctime` — birth time on macOS (§3.2).
- `MARKED`, Enter and `Space` behave as in Types, per bucket.

### 5.5 What-If — the marked set

```
WHAT-IF  marked: 2 items   would free: 100.0 KB (21.6% of total)   after: 364.0 KB
! app.cpython-313.pyc is < 30 days old
! app.py is < 30 days old
✓  60.0 KB  /Users/alex/Claude/FileManager/storagemark/python/ui/__pycache__/app.cpython-313.pyc
✓  40.0 KB  /Users/alex/Claude/FileManager/storagemark/python/ui/app.py
```

- Summary line: count, space that removing the marks would free, and what
  would remain.
- Warnings, in orange: items modified in the last 30 days, and items inside a
  folder that is also marked. At most 8 are shown. With no warnings, the line
  lists the keys instead.
- Table: one row per marked item with its size and full path.
- Keys: `Space` unmarks the row; `x` clears every mark; `D` removes the marked
  items (§14 Phase 2); Enter writes a cleanup shell script (§9).

### 5.6 Overlays

Modal screens opened over the views. Esc closes each unless noted.

| Key | Overlay | Purpose |
|---|---|---|
| `?` | Help | every key binding, and how to use rules; scrolls with `j`/`k` on short terminals |
| `E` | Scan errors | paths that could not be read, with the reason |
| `p` | Change path | type a new root folder to scan |
| `A` with nothing narrowing the list | Confirm | `y` marks every file in the scan; any other key cancels |
| `Ctrl-C` during a scan | Interrupt | `Ctrl-Q` quits, `Ctrl-C` again keeps partial results, any other key continues (§12) |
| `D` | Remove | the marked set with warnings; `t` moves it to Trash, typing `yes` deletes permanently; a progress screen follows (§14) |
| `f` | Rules | every rule with its match count and size; Enter applies one (§15) |
| `F` | Rule from path | candidate rules for the cursor item with their reach; Enter saves one (§15) |

---

## 6. Interaction Model

This is the complete key reference. §5 describes what each key does inside
each view; §5.6 lists the overlays.

### 6.1 Keys

| Key | Where | Action |
|---|---|---|
| `1`–`5` | anywhere | switch to SubDirs, Files, Types, Time, What-If |
| `Tab` / `Shift-Tab` | anywhere | move focus between the tab strip and the view (Textual default) |
| `h` / `l` | tab strip focused | previous / next tab |
| `j` / `k`, `↓` / `↑` | lists, tables, tree, overlays | move the cursor; scroll the `?` overlay |
| `PgDn` / `PgUp` | lists, tables, tree | page |
| `g` / `G` | Files | first / last row |
| `l` / `h` | SubDirs | expand / collapse, or move to the parent when already collapsed or on a file |
| Enter | SubDirs | expand or collapse the folder |
| Enter | Types, Time | open Files narrowed to that extension or age bucket |
| Enter | What-If | write a cleanup shell script (§9) |
| `s` / `S` | Files | next sort key / reverse; shown at the left of the status line |
| `/` | anywhere | open the filter box; Enter applies it and shows Files |
| Esc | filter box | clear the filter and any Types/Time narrowing |
| Esc | Files | clear an applied rule |
| `Space` | Files, SubDirs | mark or unmark the item (a folder mark covers its subtree) |
| `Space` | Types, Time | mark every file in the group, or unmark them all if all are marked |
| `Space` | What-If | unmark the row |
| `A` / `U` | anywhere | mark / unmark every row in the Files list as currently narrowed; `A` with nothing narrowing it asks first |
| `M` | anywhere | show only marked items in Files (toggle); switches to Files |
| `x` | anywhere | clear all marks |
| `D` | anywhere | remove the marked items: Trash or permanent (§14) |
| `f` | anywhere | rule picker (§15) |
| `F` | Files, SubDirs | make a rule from the cursor item (§15) |
| `y` | Files, SubDirs | copy the cursor item's full path |
| `Y` | anywhere | copy every marked path, one per line |
| `u` | anywhere | cycle the size unit auto → GB → MB → KB → B; **affects Files only** |
| `t` | anywhere | cycle the Time view's timestamp: modified, accessed, `ctime` |
| `e` | anywhere | write the **whole scan** as CSV to `storagemark_export_<timestamp>.csv` in the current directory |
| `E` | anywhere | scan errors overlay |
| `r` | anywhere | re-scan the same root; **clears all marks** |
| `p` | anywhere | change the root folder and re-scan |
| `?` | anywhere | help overlay |
| `q`, `Ctrl-Q` | anywhere | quit; an active scan and its scanner process are stopped |
| `Ctrl-C` | text selected | copy the selection |
| `Ctrl-C` | during a scan | interrupt overlay: quit, keep partial results, or continue (§12) |
| `Ctrl-C` | otherwise | quit |

There is no Backspace binding.

### 6.2 Filter box

- Matches part of the file **name**, ignoring case: `log` finds
  `system.log`. Glob characters work inside it: `*.log`.
- `~` prefix: regular expression on the name, e.g. `~^img_\d+`.
- `!` prefix inverts either form.
- Applies to the Files list and combines with any rule, Types/Time narrowing
  and `M`. Esc in the box clears it.

### 6.3 Mouse

- Click a tab to switch views.
- Drag to select text; `cmd-C` or `Ctrl-C` copies it. Double-click selects
  the whole widget, triple-click its container.
- The scroll wheel scrolls lists, tables and overlays.

### 6.4 Clipboard

Copies (`y`, `Y`, `Ctrl-C`/`cmd-C` on a selection) go out two ways, because
neither works everywhere: an OSC 52 terminal escape (works over SSH and in
iTerm2, Ghostty, kitty, WezTerm; ignored by macOS Terminal.app), and a local
helper — `pbcopy`, or `wl-copy` / `xclip` / `xsel` on Linux. The
notification names the routes used, e.g. `(osc52 + pbcopy)`.

---

## 7. Startup & CLI

```
storagemark [OPTIONS] [path]

  path                   Root to scan (default: current directory)
  -o, --once             Non-interactive: print summary and exit
  -f, --format <fmt>     Output format for --once: text|json|csv
  -d, --depth <n>        Max scan depth
  -x, --one-filesystem   Do not cross mount points
  --skip <glob>          Skip matching paths (repeatable)
  --scanner <path>       Override path to storagescanner binary
  --version              Print version
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
│   ├── hashset.c/.h       # open-addressing inode dedup
│   └── Makefile
├── python/
│   ├── __main__.py        # entry point, arg parsing
│   ├── scanner.py         # subprocess wrapper; binary struct parser
│   ├── model.py           # FileNode (slots), DirTree (iterative build, prune)
│   ├── trash.py           # platform trash (macOS ~/.Trash, Linux XDG)
│   ├── export.py          # CSV / JSON / cleanup-script export
│   └── ui/
│       ├── app.py         # Textual app: tabs, scan worker, modals, removal
│       ├── filelist.py    # virtualized Files view (Line API)
│       ├── views.py       # SubDirs lazy tree, Types, Time, What-If
│       ├── remove.py      # removal confirm modal + progress screen
│       ├── marks.py       # version-counted MarkSet
│       └── theme.py       # Norton Commander theme (CGA palette)
├── tests/                   # pytest; fast default, -m slow = 1M-scale suites
├── storagemark/__init__.py  # package marker + __version__ (single version source)
├── setup.py                 # build hook: compiles C scanner at build time
├── pyproject.toml           # uv / setuptools metadata; version read dynamically
├── uv.lock
├── bump.py                  # bump version + commit + create v<x> git tag
├── run.sh                   # dev launcher (uv run)
└── README.md
```

The legacy curses UI (`python/tui/`) and the Textual feasibility spike
(`spike/`) were removed after v1.0.1 — both remain available at that tag.

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

---

## 14. Milestone: File Identification & Removal UX (Textual)

Decided 2026-07-06. The TUI moves from curses to **Textual** (spike passed at
988k files: Line-API widget adds ~20 MB over the model, scroll frame time
size-independent, sort 0.15–0.33 s, filter 0.19 s — see
`spike/textual_files_spike.py`). The new UX is built natively on Textual;
curses code is kept during the port behind a `--classic` flag, then removed.
This adds Textual (+Rich) as the first runtime dependencies — accepted;
`uv tool install` resolves them without a compiler.

### Phases

**Phase 0 — Textual port (feature parity).** App shell, header/footer, the
five views, errors overlay, filter bar, marking, script export. Files view
uses the Line API over `DirTree.flat` (never row-per-widget). SubDirs uses a
lazy `Tree`. Scan runs as `@work(thread=True)` posting progress messages.

**Phase 1 — Bulk marking.** `Space` on a Types row marks the whole
extension; on a Time bucket marks the bucket; `A` in Files marks all
currently filtered rows. Header shows a persistent tally:
`Marked: N items, X GB`.

**Phase 2 — In-app removal, Trash-first.** `D` opens a confirmation modal:
itemized list, total size, existing warnings (young files, nested marks).
Default action moves items to the system Trash (macOS `~/.Trash`, Linux XDG
trash) — Trash is the undo. Permanent delete requires typing `yes`.
Progress bar for large sets; per-item failures feed the errors viewer.
Afterwards the in-memory tree is pruned and totals update instantly (no
rescan). Script export remains as the audit path. Warn-don't-block on
protected roots (`~/Library`, `~/Documents`, `~/Desktop` themselves, …).

**Phase 3 — Finders (rules).** One-keystroke presets that filter the Files
view. Superseded by the design in §15: the presets are user-editable rules
combining size, age **and path patterns**, not a fixed built-in set. The
built-ins below become the shipped defaults: large-and-old (>100 MB,
untouched >1 yr), junk (caches, build artifacts: `node_modules`,
`__pycache__`, `.venv`, `build/`, `dist/`, logs), installers/archives in
Downloads (`.dmg`, `.pkg`, `.zip`), big media. Same marking/removal flow
applies — no new mental model.

**Phase 4 — Duplicates.** Same-size candidates → 64 KB partial hash → full
hash confirm. Grouped view with keep-newest / keep-first strategies; marks
feed the normal removal flow. Phased last (only piece with real algorithmic
cost). **Paused 2026-09-15 after a measured review — see §16, which
supersedes this entry and is the restart point.**

---

## 15. Design: Rules (Phase 3)

Written 2026-09-14; implemented 2026-09-15 (see "As built" at the end of
this section for where the build departed from the design). Replaces the fixed preset list in
Phase 3 with user-editable rules that match on **path patterns as well as**
size and age.

### Motivating case, measured

```
/Users/alex/Library/Application Support/Cursor/CachedExtensionVSIXs/.trash/
    openai.chatgpt-26.908.40401-darwin-arm64        225.4 MB  mtime 2026-09-12
    openai.chatgpt-26.908.31748-darwin-arm64        225.4 MB  mtime 2026-09-11
    openai.chatgpt-26.903.71938-darwin-arm64        221.2 MB  mtime 2026-09-10
    openai.chatgpt-26.901.22334-darwin-arm64        217.6 MB  mtime 2026-09-04
    openai.chatgpt-26.825.32147-darwin-arm64        216.2 MB  mtime 2026-08-28
                                             (6 files, 1.3 GB total)
```

Superseded extension installers Cursor downloaded, replaced, and moved to a
discard folder of its own making. Removable; Cursor re-downloads on demand.

**Every one is newer than 90 days.** An age test misses the whole 1.3 GB, so
path patterns must be able to stand alone rather than refine a size/age rule.
This is the reason for the redesign.

Scan of `~/Library` (494,203 files, 66.2 GB, 37 s) — the addressable class:

| Pattern | Files | Size | Untouched >90 d |
|---|---|---|---|
| `/Caches/` | 163,039 | 15.2 GB | 6.4 GB |
| `/logs/` | 5,043 | 1.9 GB | 660 MB |
| `CachedExtensionVSIXs` | 12 | 1.4 GB | 70 MB |
| `/.trash/` | 6 | 1.3 GB | 0 B |
| `/Code Cache/` | 21,122 | 676 MB | 378 MB |
| `/CachedData/` | 243 | 359 MB | 13 MB |

≈19 GB. Caches are regenerable but some cost a re-download — word their
`why` text accordingly rather than presenting them as free.

### Rules file

`~/.config/storagemark/rules.toml` on both platforms (easier to find and
edit than `~/Library/Application Support`; note this differs from
`scanner.py:_user_cache_dir()`, which is platform-split by necessity).
Written on first run pre-filled with the built-in rules, so authoring is
copy-and-modify. Parsed with stdlib `tomllib` — no new dependency, and the
same format as `pyproject.toml`.

```toml
[[rule]]
name = "App discard folders"
why  = "an app's own throw-away pile inside its cache"
type = "dir"                 # match the folder, not its contents
name_glob = [".trash"]
min_size = "10MB"
```

Conditions, all optional, combined with AND; a list within one condition is
OR:

| Key | Meaning |
|---|---|
| `path_contains` | case-insensitive substrings of the full path — no pattern syntax to learn, and the primitive most users will reach for |
| `path_glob` | `fnmatch` globs against the full path (note `*` crosses `/`) |
| `name_glob` | globs against the base name, e.g. `*.dmg` |
| `min_size`, `max_size` | `"100MB"`, parsed like the old `--threshold` |
| `older_than`, `newer_than` | `"1y"`, `"90d"`, against mtime |
| `type` | `"file"` (default) or `"dir"` |

`type = "dir"` matters for the motivating case: marking the one `.trash`
directory is a single item and a single removal, against six files.

Malformed rules must not break startup — report them in the errors viewer
and carry on with the rest.

### UI

**Rule picker (`f`).** Lists each rule with match count and total size
computed against the current scan, plus its `why`:

```
App discard folders      6 files    1.3 GB   an app's own throw-away pile
Caches                 163,039     15.2 GB   regenerable, may cost a re-download
Big and old                 41      8.7 GB   >100 MB, untouched over a year
```

Choosing one sets the Files view's `pre_filter` (the mechanism the
Types/Time drill-ins already use) and keeps the rule name and reason on
screen. Marking and removal are unchanged.

**Rule from the current path (`F`).** With the cursor on a file, offer each
component of its path as a candidate rule, each with its measured reach:

```
Make a rule matching…
  folder named .trash                          1 dir     1.3 GB
  path contains /CachedExtensionVSIXs/        12 files   1.4 GB
  path contains /Application Support/Cursor/   ...
```

The chosen candidate is appended to `rules.toml` with an editable name.

This is the answer to "the path says it's junk but I'm not sure": the
consequence of a pattern is measured and shown before it is saved, and
Trash-first removal keeps a wrong guess recoverable. Match counts come from
the in-memory tree, so each candidate costs one pass over `DirTree.flat`.

### Where the code goes

- `storagemark/python/rules.py` — rule dataclass, TOML load/save, size and
  duration parsing, `matches(node)`. Framework-agnostic and unit-testable
  without the UI, like `trash.py` and `clipboard.py`.
- `storagemark/python/ui/rules_screen.py` — picker and rule-from-path modals.
- `app.py` — `f` / `F` bindings, wire the chosen rule to
  `FileList.set_pre_filter(fn, label)`.

Estimate: engine and file ~150 lines, picker ~120, rule-from-path ~100,
tests ~150.

### As built — departures from the design above

Found while implementing; each one changed the code.

1. **Rules supply a source list, not a filter.** The Files view's
   `all_files` holds files only, so a `type = "dir"` rule applied through
   `pre_filter` could never match. `FileList.set_rule_nodes(nodes, label)`
   replaces the source instead; sorting, `/` and `M` still compose on top.
   Esc in the Files list clears the rule.
2. **Sizes compare against `display_size`.** A folder's own entry is a few
   bytes; `min_size` on a `.trash` folder must see its subtree
   (`subtree_disk`), or the motivating rule rejects the folder it targets.
3. **Built-ins live in code, not in the user's file.** Writing them into
   `rules.toml` would freeze them at install time. The file is created with
   comments and a commented-out example only; user rules are additive, and a
   rule with a built-in's `name` replaces it.
4. **Counting is synchronous.** 7 rules over 1,407,765 items: 0.93 s. A
   thread worker did not avoid the pause — the counting is pure Python and
   holds the interpreter lock, so the event loop stalled for the whole run
   anyway (measured: zero event-loop turns during it). The screens paint
   `counting…` first via `call_after_refresh`, then count. Guarded by
   `test_rules_picker_counts_within_a_bound` (< 5 s).
5. **No grand total in the picker.** Rules overlap — a `__pycache__` inside
   a `Caches` folder counts under both — so summing sizes overstates.
6. **`F` offers folders by name, nearest first — no `path_contains`
   candidates.** `/.trash/` as a substring matches a folder's contents but
   not the folder (its own path has no trailing slash). The offers are the
   item's extension (files with a real one only) followed by
   `folders named X` for each enclosing folder.
7. **Candidates carry no size floor.** A floor excluded small items,
   including the item under the cursor. Invariant, tested over every node:
   each candidate matches its origin or one of its ancestors.
8. **Literal names are escaped.** `glob.escape` for names (so
   `Movies [YTS.MX]` is not a character class), `json.dumps` for TOML
   strings (names with `"` or `\` produced invalid TOML), and Rich
   `escape` for paths and rule text shown in markup.
9. **Pseudo-extensions are not offered.** `splitext` on
   `openai.chatgpt-26.908.40401-darwin-arm64` gives `.40401-darwin-arm64`;
   only 1–6 alphanumeric, non-numeric extensions are offered.

Measured on the author's home folder (1.4M items), built-in rules:
Caches 50 folders / 22.1 GB, Build artifacts 103 folders / 14.9 GB,
Installers and archives 18 files / 2.5 GB, Big media 10 files / 2.3 GB,
Logs 173 files / 1.5 GB, Big and old 3 files / 398 MB, App discard folders
none — Cursor had emptied its `.trash` after the 2026-09-14 measurement,
which supports the original judgement that its contents were disposable.

Tests: `tests/test_rules.py` (engine, file handling, round trips),
`tests/test_rules_ui.py` (both screens, origin invariant, escaping),
`tests/conftest.py` redirects `STORAGEMARK_CONFIG_DIR` so no test touches
the real `~/.config/storagemark/rules.toml`.

---

## 16. Review: Duplicates (Phase 4) — paused, restart here

Reviewed 2026-09-15; **not implemented**. This section replaces the Phase 4
entry in §14. Nothing has been built: no code, no tests. Three decisions at
the end are open.

### What the feature is

Find files that are exact copies of each other, show them grouped, and let
the user remove the extra copies through the existing marking and
Trash-first removal (`Space`, `D`).

### Measured on the author's home folder

StorageMark v1.3.0, macOS, SSD. Scan: 63 s, 1,194,645 files. Metadata only,
plus one bounded sample of file heads that skipped iCloud placeholders.

| Files considered | Same-size groups (≥2 files) | Files | Must be read to confirm | Upper bound on space freed |
|---|---|---|---|---|
| ≥ 1 MB | 1,585 | 5,994 | 33.5 GB | 22.1 GB |
| ≥ 10 MB | 208 | 668 | 22.7 GB | 14.7 GB |

"Upper bound" assumes every same-size group is a true copy set.

- **The 64 KB head check filters poorly here.** Sample of 400 groups at the
  1 MB floor: 351 (87%) still matched after hashing the first 64 KB
  (blake2b). The spec's middle stage removes ~13% of groups, so confirming
  nearly always means reading whole files.
- **Head reads are cheap:** 2,574 heads in 1.03 s (≈0.4 ms per file).
- **Whole-file reads are not measured.** Reading 33.5 GB is estimated at
  tens of seconds to a few minutes on an SSD. Measure before committing to
  a UI shape.

### Requirements the original entry missed

1. **iCloud placeholders — hard requirement.** 147 candidates at the 1 MB
   floor have `SF_DATALESS` (`0x40000000`) set in `st_flags`. Reading one
   triggers a download. The C scanner is protected by
   `setiopolicy_np(IOPOL_..._MATERIALIZE_DATALESS_FILES_OFF)`; a Python
   content read is not. Skip any file with the flag set, checked with
   `os.lstat(path).st_flags` immediately before reading.
2. **Copies that are meant to exist.** Candidates at the 1 MB floor inside
   managed trees: `site-packages/` 722, `/Library/Caches/` 187, `.app/` 40,
   `node_modules/` 22, `/.git/` 8, `.bundle/` 2 (~16% of candidates). The
   same wheel installed in three virtual environments is a "duplicate";
   deleting one breaks that environment. Exclude these by default.
   Candidate marker list to start from: `.app/`, `.photoslibrary/`,
   `/.git/`, `node_modules/`, `site-packages/`, `.xcarchive/`,
   `/Library/Caches/`, `.bundle/`, `.framework/`.
3. **APFS clones — unverified, bounds the savings figure.** `clonefile`
   (used by Finder "Duplicate" and `cp -c`) makes files that share extents.
   They compare equal by content, but removing one frees almost nothing, and
   `st_blocks` counts the shared blocks once per file. No cheap detection was
   found. Any "space freed" figure must be labelled an upper bound. Not
   measured on this disk.
4. **Choosing the keeper is where harm happens.** Keep-newest could delete an
   original in `~/Documents` and keep a stray copy in `~/Downloads`. No
   automatic removal.
5. **Hard links — low risk.** Zero candidates shared `(dev, inode)`. The
   scanner already reports repeat links with zero size (§3.5).

### Simplest version, as proposed

- Size floor (see decision 1); skip placeholders (req. 1) and managed trees
  (req. 2).
- Group by `size_bytes`, then hash whole files. Drop the 64 KB stage given
  the 87% survival; or keep it only if the full-read measurement shows it
  saves meaningful time.
- Run in the background with a progress bar and a cancel key.
- A Duplicates view: groups sorted by wasted space (size × (copies − 1)).
  In a group, `Space` marks a copy and one key marks all but the newest.
  The user reviews, then `D` removes to Trash as usual.

### Background work — why this differs from rules (§15 note 4)

Rules counting is pure-Python computation. In a thread it held the
interpreter lock and froze the UI anyway, so it runs directly after a
`counting…` frame. Hashing is mostly waiting on the disk: `read()` releases
the lock during I/O, and `hashlib`'s blake2b releases it for large buffers.
A thread worker should therefore keep the UI responsive here. **This is
inference from CPython's behaviour, not measured.** First step on restart:
hash the 10 MB-floor candidates (~22.7 GB) in a thread while probing
event-loop latency, the same way `test_rules_picker_counts_within_a_bound`
measures counting.

### Open decisions (recommendations in brackets)

1. **Size floor:** 1 MB (reads 33.5 GB, bound 22.1 GB) or 10 MB (reads
   22.7 GB, covers 14.7 GB of the 22.1 GB bound). [10 MB, adjustable.]
2. **Exclude managed trees by default?** [Yes.]
3. **Keeper selection:** manual review with a "mark all but newest" key; no
   automatic removal. [Yes.]

### Restart checklist

1. Settle the three decisions above.
2. Measure whole-file hashing time and UI responsiveness at the 10 MB floor.
3. Only then design the view and the key bindings.
