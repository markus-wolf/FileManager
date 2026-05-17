from __future__ import annotations
import curses
import os
import sys
import time
import threading
from ..model import DirTree, fmt_size
from ..scanner import stream_records
from ..export import export_csv, export_json
from .header import draw_header, draw_footer
from .widgets import safe_addstr, truncate
from .views.subdirs   import SubdirsView
from .views.files     import FilesView
from .views.types     import TypesView
from .views.time_view import TimeView
from .views.whatif    import WhatIfView

HEADER_ROWS = 5
FOOTER_ROWS = 1


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN,    -1)   # header text
    curses.init_pair(2, curses.COLOR_WHITE,   -1)   # dim / separators
    curses.init_pair(3, curses.COLOR_GREEN,   -1)   # bar filled
    curses.init_pair(4, curses.COLOR_YELLOW,  -1)   # column header
    curses.init_pair(5, curses.COLOR_BLACK,   curses.COLOR_CYAN)  # active tab / filter
    curses.init_pair(6, curses.COLOR_RED,     -1)   # warnings


class App:
    def __init__(self, root: str, start_view: int = 1, scanner_kwargs: dict | None = None):
        self._root = os.path.abspath(root)
        self._start_view = start_view
        self._scanner_kwargs = scanner_kwargs or {}
        self._tree: DirTree | None = None
        self._records: list[dict] = []
        self._scan_time = 0.0
        self._partial = False
        self._scan_done = threading.Event()
        self._marked: set[str] = set()
        self._view_idx = start_view
        self._views: dict[int, object] = {}
        self._help_open = False
        self._unit = "auto"

    # ------------------------------------------------------------------ #
    # Scanning (background thread)                                         #
    # ------------------------------------------------------------------ #

    def _scan_thread(self):
        t0 = time.time()
        try:
            for rec in stream_records(self._root, **self._scanner_kwargs):
                self._records.append(rec)
        except Exception:
            self._partial = True
        self._scan_time = time.time() - t0
        try:
            self._tree = DirTree.build(self._records)
        except Exception:
            pass
        self._scan_done.set()

    # ------------------------------------------------------------------ #
    # View management                                                      #
    # ------------------------------------------------------------------ #

    def _get_view(self, idx: int):
        if idx not in self._views:
            v = [None, SubdirsView(), FilesView(), TypesView(), TimeView(), WhatIfView()][idx]
            self._views[idx] = v
        return self._views[idx]

    def _load_view(self, idx: int, **kwargs):
        if not self._tree:
            return
        v = self._get_view(idx)
        if idx == 1:
            v.load(self._tree, self._marked)
        elif idx == 2:
            v.load(self._tree, self._marked, **kwargs)
        elif idx == 3:
            v.load(self._tree, self._marked)
        elif idx == 4:
            v.load(self._tree, self._marked)
        elif idx == 5:
            v.load(self._tree, self._marked)

    def _reload_all(self):
        for idx in self._views:
            self._load_view(idx)

    # ------------------------------------------------------------------ #
    # Main loop                                                            #
    # ------------------------------------------------------------------ #

    def run(self, stdscr):
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        init_colors()

        # Start background scan
        t = threading.Thread(target=self._scan_thread, daemon=True)
        t.start()

        loaded = False

        while True:
            h, w = stdscr.getmaxyx()
            view_h = h - HEADER_ROWS - FOOTER_ROWS
            view_y = HEADER_ROWS

            # Once scan completes, load all views once
            if self._scan_done.is_set() and not loaded:
                if self._tree:
                    self._reload_all()
                    loaded = True

            # Draw header
            draw_header(stdscr, self._tree, self._root, self._scan_time,
                        self._view_idx, w, self._partial)

            # Draw current view
            view = self._get_view(self._view_idx)
            if self._tree and view:
                stdscr.move(view_y, 0)
                for row in range(view_h):
                    stdscr.move(view_y + row, 0)
                    stdscr.clrtoeol()
                view.draw(stdscr, view_y, 0, view_h, w)
            elif not self._scan_done.is_set():
                dots = "." * (int(time.time() * 2) % 4)
                msg = f"  Scanning {self._root}{dots}  ({len(self._records):,} records)"
                safe_addstr(stdscr, view_y + view_h // 2, 0,
                            truncate(msg, w), curses.color_pair(1))

            draw_footer(stdscr, h, w)

            if self._help_open:
                self._draw_help(stdscr, h, w)

            stdscr.refresh()

            # Input
            try:
                key = stdscr.getch()
            except curses.error:
                key = -1

            if key == -1:
                time.sleep(0.05)
                continue

            if self._help_open:
                self._help_open = False
                continue

            # Global keys
            if key == ord('q'):
                break
            elif key == ord('?'):
                self._help_open = True
                continue
            elif key in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5')):
                new_idx = key - ord('0')
                self._view_idx = new_idx
                if self._tree and new_idx not in self._views:
                    self._load_view(new_idx)
                elif self._tree:
                    self._load_view(new_idx)
                continue
            elif key == ord('r'):
                # Re-scan
                self._records.clear()
                self._tree = None
                self._views.clear()
                self._scan_done.clear()
                loaded = False
                t = threading.Thread(target=self._scan_thread, daemon=True)
                t.start()
                continue
            elif key == ord('u'):
                units = ["auto", "GB", "MB", "KB", "B"]
                self._unit = units[(units.index(self._unit) + 1) % len(units)]
                for v in self._views.values():
                    if hasattr(v, '_unit'):
                        v._unit = self._unit
                continue
            elif key == ord('e'):
                self._do_export(view)
                continue
            elif key == ord('p'):
                self._prompt_path(stdscr, h, w)
                continue

            # Delegate to current view
            if view and self._tree:
                result = view.handle_key(key)
                if result:
                    self._handle_view_result(result)

    def _handle_view_result(self, result: str):
        if result.startswith("drill_ext:"):
            ext = result[len("drill_ext:"):]
            self._view_idx = 2
            self._load_view(2, ext_filter=ext)
        elif result.startswith("drill_time:"):
            self._view_idx = 2
            self._load_view(2)

    def _do_export(self, view):
        if not self._tree:
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        fname = f"storagemark_export_{ts}.csv"
        nodes = self._tree.flat
        with open(fname, "w") as f:
            f.write(export_csv(nodes))

    def _prompt_path(self, stdscr, h, w):
        curses.echo()
        curses.curs_set(1)
        stdscr.nodelay(False)   # block until user presses Enter
        prompt = " New path: "
        safe_addstr(stdscr, h - 1, 0, truncate(prompt, w), curses.color_pair(5))
        stdscr.refresh()
        try:
            new_path = stdscr.getstr(h - 1, len(prompt), w - len(prompt)).decode()
        except Exception:
            new_path = ""
        curses.noecho()
        curses.curs_set(0)
        stdscr.nodelay(True)    # restore non-blocking
        if new_path and os.path.isdir(new_path):
            self._root = os.path.abspath(new_path)
            self._records.clear()
            self._tree = None
            self._views.clear()
            self._scan_done.clear()
            self._marked.clear()
            t = threading.Thread(target=self._scan_thread, daemon=True)
            t.start()

    def _draw_help(self, stdscr, h, w):
        lines = [
            "  StorageMark — Key Bindings",
            "  ─────────────────────────────────────",
            "  1–5        Switch view",
            "  j/k        Move cursor down/up",
            "  PgDn/PgUp  Page down/up",
            "  g/G        Jump to top/bottom",
            "  Enter      Expand dir / drill into",
            "  Space      Toggle what-if mark",
            "  /          Filter bar (glob or ~regex)",
            "  s/S        Cycle sort / reverse sort",
            "  u          Toggle size unit",
            "  t          Toggle time field (view 4)",
            "  r          Re-scan current root",
            "  p          Change root path",
            "  e          Export current view to CSV",
            "  q          Quit",
            "  ?          This help   [any key] close",
        ]
        bh = len(lines) + 2
        bw = 46
        by = max(0, h // 2 - bh // 2)
        bx = max(0, w // 2 - bw // 2)
        for i, line in enumerate(lines):
            attr = curses.color_pair(5) if i == 0 else 0
            safe_addstr(stdscr, by + i + 1, bx, truncate(line, bw), attr)


def run_tui(root: str, start_view: int = 1, scanner_kwargs: dict | None = None):
    app = App(root, start_view, scanner_kwargs)
    curses.wrapper(app.run)
