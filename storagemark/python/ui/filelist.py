"""Virtualized file list — Textual Line API over DirTree.flat.

Renders only visible lines; measured at ~988k rows with ~20 MB of widget
overhead and frame time independent of row count (spec §14). Never hold
one widget per row.
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
from textual.selection import Selection
from textual.strip import Strip

from ..model import FileNode, fmt_size
from .theme import CGA_BLACK, CGA_CYAN, CGA_YELLOW

# Upper bound on rows pulled out by a single mouse selection (a
# select-all over ~1M rows would otherwise build a ~100 MB string).
MAX_SELECT_ROWS = 20_000

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
        """The row under the cursor changed — either the cursor moved or
        the row set was re-sorted/re-filtered under it. Carries the node
        so the path status line can render it; None when the list is empty."""

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
        self.filter_query = ""
        self.pre_filter: Callable[[FileNode], bool] | None = None  # finders/drills
        self.pre_label = ""
        self.show_marked_only = False  # 'M': pre-flight review before D
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
        if self.show_marked_only:
            src = [n for n in src if n.path in self.marked]
        if self.pre_filter is not None:
            src = [n for n in src if self.pre_filter(n)]
        if self.filter_query:
            match = make_filter(self.filter_query)
            src = [n for n in src if match(n)]
        self.rows = sorted(src, key=fn, reverse=self.sort_rev)
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))
        self.virtual_size = Size(self.size.width, len(self.rows))
        self.refresh()
        self._announce_cursor()

    def sort_label(self) -> str:
        arrow = "↓" if self.sort_rev else "↑"
        return f"{SORT_LABELS[SORT_KEYS[self.sort_idx]]}{arrow}"

    def selected(self) -> FileNode | None:
        return self.rows[self.cursor] if self.rows else None

    # -------------------------------------------------------- rendering --

    def _row_text(self, n: FileNode) -> str:
        """The row exactly as displayed. Shared by render_line and
        get_selection so mouse-selection offsets line up with the text
        that gets copied."""
        unit = self.unit_ref[0]
        mark = "●" if n.path in self.marked else " "
        return (f"{mark} {fmt_size(n.size_disk, unit):>10}  "
                f"{fmt_size(n.size_bytes, unit):>10}  "
                f"{n.mtime:%Y-%m-%d %H:%M}  {n.name}")

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        idx = y + scroll_y
        width = self.size.width
        base = self.rich_style          # widget CSS style (theme bg/fg) —
        if idx >= len(self.rows):       # Line API does NOT apply it for us
            return Strip.blank(width, base)
        n = self.rows[idx]
        is_marked = n.path in self.marked
        is_cursor = idx == self.cursor and self.has_focus
        text = self._row_text(n)
        if is_cursor:                   # NC cursor bar: black on cyan
            style = Style(color=CGA_YELLOW if is_marked else CGA_BLACK,
                          bgcolor=CGA_CYAN, bold=is_marked)
        elif is_marked:                 # NC marked file: bold yellow
            style = base + Style(color=CGA_YELLOW, bold=True)
        else:
            style = base
        padded = text[:width].ljust(width)

        # Mouse text selection: Line API widgets must paint the highlight
        # themselves (no Visual does it for us).
        selection = self.text_selection
        if selection is not None:
            span = selection.get_span(idx)
            if span is not None:
                x0, x1 = span
                if x1 == -1:
                    x1 = width
                x0, x1 = max(0, x0), min(width, x1)
                if x0 < x1:
                    sel_style = style + self.selection_style
                    return Strip([Segment(padded[:x0], style),
                                  Segment(padded[x0:x1], sel_style),
                                  Segment(padded[x1:], style)], width)
        return Strip([Segment(padded, style)], width)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Text under a mouse selection, for Screen.copy_text.

        Only the selected rows are materialized: the default
        implementation renders the whole widget, which would build a
        string over the entire ~1M-row list.
        """
        if not self.rows:
            return None
        start, end = selection
        last = len(self.rows) - 1
        y0 = 0 if start is None else max(0, start.y)
        y1 = last if end is None else min(last, end.y)
        if y0 > y1:
            return None
        y1 = min(y1, y0 + MAX_SELECT_ROWS - 1)   # bound a select-all
        lines: list[str] = []
        for y in range(y0, y1 + 1):
            span = selection.get_span(y)
            if span is None:
                continue
            x0, x1 = span
            text = self._row_text(self.rows[y])
            lines.append(text[x0:] if x1 == -1 else text[x0:x1])
        return "\n".join(lines), "\n"

    # ----------------------------------------------------------- cursor --

    def _announce_cursor(self) -> None:
        """Tell the app which row is current (drives the path status line).
        Guarded: resort() runs before mount during the initial load."""
        if self.is_mounted:
            self.post_message(self.CursorMoved(self.selected()))

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
        self._announce_cursor()

    def jump(self, where: str) -> None:
        if not self.rows:
            return
        self.cursor = 0 if where == "top" else len(self.rows) - 1
        self.scroll_to(y=self.cursor if where != "top" else 0, animate=False)
        self.refresh()
        self._announce_cursor()

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
                    if self.show_marked_only:
                        self.resort()   # row leaves the marked-only view
                    else:
                        self.move_cursor(1)
                else:
                    self.marked.add(node.path)
                    self.move_cursor(1)   # convenience: advance after toggling
        else:
            return
        event.stop()

    def mark_all_visible(self) -> int:
        """Mark every row matching the current filter ('A')."""
        self.marked.update(n.path for n in self.rows)
        self.refresh()
        return len(self.rows)

    def unmark_all_visible(self) -> int:
        """Unmark every row matching the current filter ('U' — A's inverse)."""
        n = len(self.rows)
        self.marked.difference_update(r.path for r in self.rows)
        if self.show_marked_only:
            self.resort()               # rows just vanished from this view
        else:
            self.refresh()
        return n

    def is_unfiltered(self) -> bool:
        """True when 'A' would mark the entire tree (no narrowing active)."""
        return (not self.filter_query and self.pre_filter is None
                and not self.show_marked_only)
