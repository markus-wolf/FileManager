from __future__ import annotations
import curses
from ...model import DirTree, FileNode, fmt_size
from ..widgets import ScrollList, draw_bar, safe_addstr, truncate, BAR_WIDTH

SORT_KEYS  = ["disk", "count", "avg", "ext"]
SORT_LABELS = ["DISK", "COUNT", "AVG SIZE", "EXT"]


class TypesView:
    def __init__(self):
        self._sl = ScrollList()
        self._sort_idx = 0
        self._sort_rev = True
        self._rows: list[dict] = []
        self._total_disk = 1
        self._vh = 10

    def load(self, tree: DirTree, _marked: set[str]):
        self._total_disk = tree.total_disk or 1
        rows = []
        for ext, nodes in tree.ext_map.items():
            total_d = sum(n.size_disk for n in nodes)
            rows.append({
                "ext":   ext,
                "count": len(nodes),
                "disk":  total_d,
                "avg":   total_d // len(nodes) if nodes else 0,
                "nodes": nodes,
            })
        self._rows = rows
        self._rebuild()

    def _rebuild(self):
        key = SORT_KEYS[self._sort_idx % len(SORT_KEYS)]
        self._sl.set_items(
            sorted(self._rows, key=lambda r: r[key],
                   reverse=(key != "ext") ^ (not self._sort_rev))
        )

    def handle_key(self, key: int) -> str | None:
        if key in (ord('j'), curses.KEY_DOWN):
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
        elif key in (curses.KEY_ENTER, 10, 13):
            row = self._sl.selected()
            if row:
                return f"drill_ext:{row['ext']}"
        return None

    def draw(self, win, y0: int, x0: int, height: int, width: int):
        self._vh = height - 1
        slabel = SORT_LABELS[self._sort_idx % len(SORT_KEYS)]
        header = f" {'EXT':<14} {'COUNT':>8}  {'TOTAL DISK':>12}  {'AVG SIZE':>10}  {'%':>6}  BAR  SORT:{slabel}"
        safe_addstr(win, y0, x0, truncate(header, width), curses.color_pair(4))

        for row, idx, r in self._sl.visible(self._vh):
            y = y0 + 1 + row
            frac = r["disk"] / self._total_disk
            pct  = frac * 100
            line = (f"  {truncate(r['ext'], 14):<14} {r['count']:>8}  "
                    f"{fmt_size(r['disk']):>12}  {fmt_size(r['avg']):>10}  {pct:>5.1f}%  ")
            attr = curses.A_REVERSE if idx == self._sl.cursor else 0
            safe_addstr(win, y, x0, truncate(line, width - BAR_WIDTH - 2), attr)
            draw_bar(win, y, x0 + len(line), frac)

        hint = " [Enter] drill into extension"
        safe_addstr(win, y0 + height - 1, x0, truncate(hint, width), curses.color_pair(2))
