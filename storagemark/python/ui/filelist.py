"""Virtualized file list — Textual Line API over DirTree.flat.

Renders only visible lines; validated against ~1M rows in
spike/textual_files_spike.py (widget overhead ~20 MB, frame time
independent of row count). Never hold one widget per row.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Callable

from rich.segment import Segment
from rich.style import Style
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from ..model import FileNode, fmt_size

SORT_KEYS = ["size_disk", "size_bytes", "mtime", "atime", "name", "ext"]
SORT_LABELS = {"size_disk": "DISK", "size_bytes": "LOGICAL", "mtime": "MODIFIED",
               "atime": "ACCESSED", "name": "NAME", "ext": "EXT"}


def make_filter(query: str) -> Callable[[FileNode], bool]:
    """glob (substring-ish) or ~regex; '!' prefix inverts. Mirrors curses UI."""
    invert = query.startswith("!")
    if invert:
        query = query[1:]
    if query.startswith("~"):
        try:
            rx = re.compile(query[1:], re.IGNORECASE)
            base = lambda n: bool(rx.search(n.name))
        except re.error:
            base = lambda n: True
    else:
        pat = f"*{query.lower()}*"
        base = lambda n: fnmatch.fnmatch(n.name.lower(), pat)
    return (lambda n: not base(n)) if invert else base


class FileList(ScrollView, can_focus=True):
    """Sortable, filterable, markable list over a flat list of FileNodes."""

    class CursorMoved(Message):
        def __init__(self, node: FileNode | None) -> None:
            super().__init__()
            self.node = node

    def __init__(self, marked: set[str], unit_ref: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.all_files: list[FileNode] = []
        self.rows: list[FileNode] = []
        self.cursor = 0
        self.sort_idx = 0
        self.sort_rev = True
        self.query = ""
        self.pre_filter: Callable[[FileNode], bool] | None = None  # finders/drills
        self.pre_label = ""
        self.marked = marked          # shared with app
        self.unit_ref = unit_ref      # 1-elem list so app can swap unit globally

    # ------------------------------------------------------------- data --

    def set_files(self, files: list[FileNode]) -> None:
        self.all_files = files
        self.resort()

    def set_pre_filter(self, fn: Callable[[FileNode], bool] | None,
                       label: str = "") -> None:
        self.pre_filter = fn
        self.pre_label = label
        self.cursor = 0
        self.resort()

    def resort(self) -> None:
        key = SORT_KEYS[self.sort_idx]
        if key == "name":
            fn = lambda n: n.name.lower()
        elif key == "ext":
            fn = lambda n: n.ext
        else:
            fn = lambda n: getattr(n, key)
        src = self.all_files
        if self.pre_filter is not None:
            src = [n for n in src if self.pre_filter(n)]
        if self.query:
            match = make_filter(self.query)
            src = [n for n in src if match(n)]
        self.rows = sorted(src, key=fn, reverse=self.sort_rev)
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))
        self.virtual_size = Size(self.size.width, len(self.rows))
        self.refresh()

    def sort_label(self) -> str:
        arrow = "↓" if self.sort_rev else "↑"
        return f"{SORT_LABELS[SORT_KEYS[self.sort_idx]]}{arrow}"

    def selected(self) -> FileNode | None:
        return self.rows[self.cursor] if self.rows else None

    # -------------------------------------------------------- rendering --

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        idx = y + scroll_y
        width = self.size.width
        if idx >= len(self.rows):
            return Strip.blank(width)
        n = self.rows[idx]
        unit = self.unit_ref[0]
        mark = "●" if n.path in self.marked else " "
        text = (f"{mark} {fmt_size(n.size_disk, unit)}  "
                f"{fmt_size(n.size_bytes, unit)}  "
                f"{n.mtime:%Y-%m-%d %H:%M}  {n.name}")
        style = Style(reverse=(idx == self.cursor and self.has_focus))
        if n.path in self.marked:
            style += Style(color="yellow")
        return Strip([Segment(text[:width].ljust(width), style)], width)

    # ----------------------------------------------------------- cursor --

    def move_cursor(self, delta: int) -> None:
        if not self.rows:
            return
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))
        top = self.scroll_offset.y
        height = self.scrollable_content_region.height
        if self.cursor < top:
            self.scroll_to(y=self.cursor, animate=False)
        elif self.cursor >= top + height:
            self.scroll_to(y=self.cursor - height + 1, animate=False)
        self.refresh()
        self.post_message(self.CursorMoved(self.selected()))

    def jump(self, where: str) -> None:
        if not self.rows:
            return
        self.cursor = 0 if where == "top" else len(self.rows) - 1
        self.scroll_to(y=self.cursor if where != "top" else 0, animate=False)
        self.refresh()

    # ------------------------------------------------------------- keys --

    def on_key(self, event) -> None:
        k = event.key
        page = max(1, self.scrollable_content_region.height)
        if k in ("j", "down"):
            self.move_cursor(1)
        elif k in ("k", "up"):
            self.move_cursor(-1)
        elif k == "pagedown":
            self.move_cursor(page)
        elif k == "pageup":
            self.move_cursor(-page)
        elif k == "g":
            self.jump("top")
        elif k == "G":
            self.jump("bottom")
        elif k == "s":
            self.sort_idx = (self.sort_idx + 1) % len(SORT_KEYS)
            self.resort()
        elif k == "S":
            self.sort_rev = not self.sort_rev
            self.resort()
        elif k == "space":
            node = self.selected()
            if node:
                if node.path in self.marked:
                    self.marked.discard(node.path)
                else:
                    self.marked.add(node.path)
                self.move_cursor(1)   # convenience: advance after toggling
        else:
            return
        event.stop()

    def mark_all_visible(self) -> int:
        """Mark every row matching the current filter (Phase 1 'A' key)."""
        for n in self.rows:
            self.marked.add(n.path)
        self.refresh()
        return len(self.rows)
