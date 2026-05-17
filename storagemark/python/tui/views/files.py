from __future__ import annotations
import curses
from ...model import DirTree, FileNode, fmt_size
from ..widgets import ScrollList, FilterBar, draw_bar, safe_addstr, truncate

SORT_KEYS = ["size_disk", "size_bytes", "mtime", "atime", "name", "ext"]
SORT_LABELS = ["DISK", "LOGICAL", "MODIFIED", "ACCESSED", "NAME", "EXT"]


class FilesView:
    def __init__(self):
        self._sl = ScrollList()
        self._filter = FilterBar()
        self._sort_idx = 0
        self._sort_rev = True
        self._all: list[FileNode] = []
        self._marked: set[str] = set()
        self._vh = 10
        self._unit = "auto"
        self._ext_filter: str | None = None

    def load(self, tree: DirTree, marked: set[str], ext_filter: str | None = None):
        self._all = [n for n in tree.flat if n.type == 'f']
        self._marked = marked
        self._ext_filter = ext_filter
        self._rebuild()

    def _rebuild(self):
        nodes = self._all
        if self._ext_filter:
            nodes = [n for n in nodes if n.ext == self._ext_filter]
        if self._filter.query:
            nodes = [n for n in nodes if self._filter.matches(n.name)]
        key = SORT_KEYS[self._sort_idx % len(SORT_KEYS)]
        if key == "size_disk":
            keyfn = lambda n: n.size_disk
        elif key == "size_bytes":
            keyfn = lambda n: n.size_bytes
        elif key == "mtime":
            keyfn = lambda n: n.mtime
        elif key == "atime":
            keyfn = lambda n: n.atime
        elif key == "name":
            keyfn = lambda n: n.name.lower()
        else:
            keyfn = lambda n: n.ext
        self._sl.set_items(sorted(nodes, key=keyfn, reverse=self._sort_rev))

    def handle_key(self, key: int) -> str | None:
        if self._filter.handle_key(key):
            self._rebuild()
            return None
        if key == ord('/'):
            self._filter.toggle()
            self._ext_filter = None
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
        slabel = SORT_LABELS[self._sort_idx % len(SORT_LABELS)]
        rev_ind = "↓" if self._sort_rev else "↑"
        ext_tag = f" [ext:{self._ext_filter}]" if self._ext_filter else ""
        header = f" {'DISK':>10}  {'LOGICAL':>10}  {'MODIFIED':<19}  {'NAME'}{ext_tag}  SORT:{slabel}{rev_ind}"
        safe_addstr(win, y0, x0, truncate(header, width), curses.color_pair(4))

        name_w = max(10, width - 46)
        for row, idx, node in self._sl.visible(self._vh):
            y = y0 + 1 + row
            mark = "●" if node.path in self._marked else " "
            disk = fmt_size(node.size_disk, self._unit)
            logi = fmt_size(node.size_bytes, self._unit)
            mod  = node.mtime.strftime("%Y-%m-%d %H:%M")
            name = truncate(node.name, name_w)
            line = f"{mark} {disk}  {logi}  {mod}  {name}"
            attr = curses.A_REVERSE if idx == self._sl.cursor else 0
            safe_addstr(win, y, x0, truncate(line, width), attr)

        self._filter.draw(win, y0 + height - 1, x0, width)
