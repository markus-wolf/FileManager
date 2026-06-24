from __future__ import annotations
import curses
import socket
import time
from ..model import DirTree, fmt_size
from .widgets import safe_addstr, truncate

TABS = ["[1]SubDirs", "[2]Files", "[3]Types", "[4]Time", "[5]WhatIf"]
KEYBAR = " [q]uit  [/]filter  [s]ort  [S]rev  [Space]mark  [e]xport  [?]help"

# Short hostname (drop the trailing .local / domain), resolved once.
try:
    HOSTNAME = socket.gethostname().split(".")[0]
except Exception:
    HOSTNAME = ""


def draw_header(win, tree: DirTree | None, root: str, scan_time: float,
                current_view: int, width: int, partial: bool = False):
    status = "[PARTIAL] " if partial else ""
    scan_s = f"{scan_time:.1f}s" if scan_time else "scanning…"
    location = f"{HOSTNAME}:{root}" if HOSTNAME else root
    line1 = f" StorageMark  {status}{location}    Scanned: {scan_s}"
    win.move(0, 0)
    win.clrtoeol()
    safe_addstr(win, 0, 0, truncate(line1, width), curses.color_pair(1) | curses.A_BOLD)

    if tree:
        line2 = (f" Total: {fmt_size(tree.total_disk).strip()}  "
                 f"Files: {tree.file_count:,}  Dirs: {tree.dir_count:,}  "
                 f"Errors: {len(tree.errors)}")
    else:
        line2 = " Scanning…"
    win.move(1, 0)
    win.clrtoeol()
    safe_addstr(win, 1, 0, truncate(line2, width), curses.color_pair(1))

    win.move(2, 0)
    win.clrtoeol()
    safe_addstr(win, 2, 0, "─" * width, curses.color_pair(2))

    tab_line = "  ".join(
        f"[{i+1}]{name[3:]}" if i + 1 == current_view
        else TABS[i]
        for i, name in enumerate(TABS)
    )
    win.move(3, 0)
    win.clrtoeol()
    x = 0
    for i, tab in enumerate(TABS):
        label = tab
        attr = (curses.color_pair(5) | curses.A_BOLD) if i + 1 == current_view \
               else curses.color_pair(2)
        safe_addstr(win, 3, x, label, attr)
        x += len(label) + 2

    win.move(4, 0)
    win.clrtoeol()
    safe_addstr(win, 4, 0, "─" * width, curses.color_pair(2))


def draw_footer(win, height: int, width: int, extra: str = ""):
    row = height - 1
    win.move(row, 0)
    win.clrtoeol()
    text = truncate(KEYBAR + ("  " + extra if extra else ""), width)
    safe_addstr(win, row, 0, text, curses.color_pair(2))
