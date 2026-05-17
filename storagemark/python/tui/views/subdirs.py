from __future__ import annotations
import curses
from ...model import DirTree, FileNode, fmt_size
from ..widgets import ScrollList, FilterBar, draw_bar, safe_addstr, truncate, BAR_WIDTH

SORT_KEYS = ["size", "name", "count", "mtime"]


class SubdirsView:
    def __init__(self):
        self._sl = ScrollList()
        self._filter = FilterBar()
        self._sort_idx = 0
        self._sort_rev = False
        self._expanded: set[str] = set()
        self._tree: DirTree | None = None
        self._marked: set[str] = set()

    def load(self, tree: DirTree, marked: set[str]):
        self._tree = tree
        self._marked = marked
        self._expanded.add(tree.root.path)
        self._rebuild()

    def _flatten(self, node: FileNode, rows: list[FileNode]):
        rows.append(node)
        if node.type == 'd' and node.path in self._expanded:
            children = self._sorted(node.children)
            for c in children:
                self._flatten(c, rows)

    def _sorted(self, nodes: list[FileNode]) -> list[FileNode]:
        key = SORT_KEYS[self._sort_idx % len(SORT_KEYS)]
        if key == "size":
            keyfn = lambda n: n.subtree_disk
        elif key == "name":
            keyfn = lambda n: n.name.lower()
        elif key == "count":
            keyfn = lambda n: n.subtree_count
        else:
            keyfn = lambda n: n.mtime
        return sorted(nodes, key=keyfn, reverse=(key != "name") ^ self._sort_rev)

    def _rebuild(self):
        if not self._tree:
            return
        rows: list[FileNode] = []
        self._flatten(self._tree.root, rows)
        q = self._filter.query
        if q:
            rows = [r for r in rows if self._filter.matches(r.name)]
        self._sl.set_items(rows)

    def handle_key(self, key: int) -> str | None:
        if self._filter.handle_key(key):
            self._rebuild()
            return None
        if key == ord('/'):
            self._filter.toggle()
            self._rebuild()
        elif key in (ord('j'), curses.KEY_DOWN):
            self._sl.move(1, self._vh)
        elif key in (ord('k'), curses.KEY_UP):
            self._sl.move(-1, self._vh)
        elif key in (curses.KEY_NPAGE, curses.KEY_SF):
            self._sl.page(1, self._vh)
        elif key in (curses.KEY_PPAGE, curses.KEY_SR):
            self._sl.page(-1, self._vh)
        elif key == ord('g'):
            self._sl.jump("top", self._vh)
        elif key == ord('G'):
            self._sl.jump("bottom", self._vh)
        elif key in (curses.KEY_ENTER, 10, 13):
            node = self._sl.selected()
            if node and node.type == 'd':
                if node.path in self._expanded:
                    self._expanded.discard(node.path)
                else:
                    self._expanded.add(node.path)
                self._rebuild()
        elif key == ord('s'):
            self._sort_idx = (self._sort_idx + 1) % len(SORT_KEYS)
            self._rebuild()
        elif key == ord('S'):
            self._sort_rev = not self._sort_rev
            self._rebuild()
        elif key == ord(' '):
            node = self._sl.selected()
            if node:
                if node.path in self._marked:
                    self._marked.discard(node.path)
                else:
                    self._marked.add(node.path)
        return None

    def draw(self, win, y0: int, x0: int, height: int, width: int):
        self._vh = height - 1
        total = self._tree.total_disk if self._tree else 1

        header = (f" {'NAME':<40} {'DISK SIZE':>10}  {'':^{BAR_WIDTH}}  {'%':>5}  "
                  f"SORT:{SORT_KEYS[self._sort_idx % len(SORT_KEYS)].upper()}")
        safe_addstr(win, y0, x0, truncate(header, width), curses.color_pair(4))

        for row, idx, node in self._sl.visible(self._vh):
            y = y0 + 1 + row
            indent = "  " * node.depth
            marker = "▼ " if (node.type == 'd' and node.path in self._expanded) \
                else ("▶ " if node.type == 'd' else "  ")
            mark = "●" if node.path in self._marked else " "
            name_part = indent + marker + node.name
            size = node.subtree_disk if node.type == 'd' else node.size_disk
            frac = size / total if total else 0
            pct = frac * 100
            name_w = max(10, width - 42)
            line = f"{mark} {truncate(name_part, name_w)} {fmt_size(size):>10}  "
            attr = curses.A_REVERSE if idx == self._sl.cursor else 0
            safe_addstr(win, y, x0, line, attr)
            draw_bar(win, y, x0 + len(line), frac)
            pct_str = f"  {pct:5.1f}%"
            safe_addstr(win, y, x0 + len(line) + BAR_WIDTH, pct_str, attr)

        self._filter.draw(win, y0 + height - 1, x0, width)
