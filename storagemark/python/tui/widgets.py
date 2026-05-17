from __future__ import annotations
import curses
import fnmatch
import re
from typing import Any


BAR_FULL  = "█"
BAR_EMPTY = "░"
BAR_WIDTH = 20


def draw_bar(win, y: int, x: int, fraction: float, width: int = BAR_WIDTH):
    filled = min(width, max(0, round(fraction * width)))
    empty  = width - filled
    try:
        win.addstr(y, x, BAR_FULL * filled,  curses.color_pair(3))
        win.addstr(y, x + filled, BAR_EMPTY * empty, curses.color_pair(2))
    except curses.error:
        pass


def safe_addstr(win, y: int, x: int, text: str, attr: int = 0):
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def truncate(s: str, width: int) -> str:
    if len(s) <= width:
        return s.ljust(width)
    return s[:width - 1] + "…"


class ScrollList:
    """Manages cursor + scroll offset for a list of items."""
    def __init__(self):
        self.items: list[Any] = []
        self.cursor: int = 0
        self.offset: int = 0

    def set_items(self, items: list[Any]):
        self.items = items
        self.cursor = min(self.cursor, max(0, len(items) - 1))
        self.offset = min(self.offset, max(0, len(items) - 1))

    def move(self, delta: int, viewport_height: int):
        self.cursor = max(0, min(len(self.items) - 1, self.cursor + delta))
        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + viewport_height:
            self.offset = self.cursor - viewport_height + 1

    def page(self, direction: int, viewport_height: int):
        self.move(direction * viewport_height, viewport_height)

    def jump(self, pos: str, viewport_height: int):
        if pos == "top":
            self.cursor = 0
            self.offset = 0
        else:
            self.cursor = max(0, len(self.items) - 1)
            self.offset = max(0, len(self.items) - viewport_height)

    def selected(self) -> Any | None:
        if not self.items:
            return None
        return self.items[self.cursor]

    def visible(self, viewport_height: int) -> list[tuple[int, int, Any]]:
        """Yields (screen_row, list_index, item) for visible rows."""
        result = []
        for row in range(viewport_height):
            idx = self.offset + row
            if idx >= len(self.items):
                break
            result.append((row, idx, self.items[idx]))
        return result


class FilterBar:
    def __init__(self):
        self.active = False
        self.query  = ""

    def toggle(self):
        self.active = not self.active
        if not self.active:
            self.query = ""

    def handle_key(self, key: int) -> bool:
        """Returns True if key was consumed."""
        if not self.active:
            return False
        if key in (27, curses.KEY_ESCAPE if hasattr(curses, 'KEY_ESCAPE') else -1):
            self.active = False
            self.query  = ""
            return True
        if key in (curses.KEY_BACKSPACE, 127, 8):
            self.query = self.query[:-1]
            return True
        if 32 <= key <= 126:
            self.query += chr(key)
            return True
        return False

    def matches(self, s: str) -> bool:
        if not self.query:
            return True
        q = self.query
        invert = q.startswith("!")
        if invert:
            q = q[1:]
        if q.startswith("~"):
            try:
                result = bool(re.search(q[1:], s, re.IGNORECASE))
            except re.error:
                result = True
        else:
            result = fnmatch.fnmatch(s.lower(), f"*{q.lower()}*")
        return not result if invert else result

    def draw(self, win, y: int, x: int, width: int):
        label = "Filter: " if self.active else ""
        q = self.query if self.active else ""
        text = truncate(label + q + ("_" if self.active else ""), width)
        attr = curses.color_pair(5) if self.active else 0
        safe_addstr(win, y, x, text, attr)
