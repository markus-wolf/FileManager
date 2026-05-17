from __future__ import annotations
import curses
from datetime import datetime, timedelta
from ...model import DirTree, FileNode, fmt_size
from ..widgets import ScrollList, draw_bar, safe_addstr, truncate, BAR_WIDTH

NOW = datetime.now()

BUCKETS = [
    ("> 2 years",   NOW - timedelta(days=730),  None),
    ("1–2 years",   NOW - timedelta(days=365),  NOW - timedelta(days=730)),
    ("6–12 months", NOW - timedelta(days=182),  NOW - timedelta(days=365)),
    ("1–6 months",  NOW - timedelta(days=30),   NOW - timedelta(days=182)),
    ("< 1 month",   None,                        NOW - timedelta(days=30)),
]

TIME_FIELDS = ["mtime", "atime", "ctime"]


class TimeView:
    def __init__(self):
        self._sl = ScrollList()
        self._field_idx = 0
        self._rows: list[dict] = []
        self._total_disk = 1
        self._vh = 10

    def load(self, tree: DirTree, _marked: set[str]):
        self._tree = tree
        self._total_disk = tree.total_disk or 1
        self._rebuild()

    def _rebuild(self):
        field = TIME_FIELDS[self._field_idx]
        rows = []
        files = [n for n in self._tree.flat if n.type == 'f']
        for label, newer_than, older_than in BUCKETS:
            bucket_files = []
            for f in files:
                t = getattr(f, field)
                if older_than and t >= older_than:
                    continue
                if newer_than and t < newer_than:
                    continue
                bucket_files.append(f)
            disk = sum(n.size_disk for n in bucket_files)
            rows.append({
                "label": label,
                "count": len(bucket_files),
                "disk":  disk,
                "nodes": bucket_files,
            })
        self._rows = rows
        self._sl.set_items(rows)

    def handle_key(self, key: int) -> str | None:
        if key in (ord('j'), curses.KEY_DOWN):
            self._sl.move(1, self._vh)
        elif key in (ord('k'), curses.KEY_UP):
            self._sl.move(-1, self._vh)
        elif key == ord('t'):
            self._field_idx = (self._field_idx + 1) % len(TIME_FIELDS)
            self._rebuild()
        elif key in (curses.KEY_ENTER, 10, 13):
            row = self._sl.selected()
            if row:
                return f"drill_time:{id(row)}"
        return None

    def draw(self, win, y0: int, x0: int, height: int, width: int):
        self._vh = height - 2
        field = TIME_FIELDS[self._field_idx]
        header = f" {'BUCKET':<16} {'FILES':>8}  {'DISK SIZE':>12}  {'%':>6}  BAR   [t] time field: {field.upper()}"
        safe_addstr(win, y0, x0, truncate(header, width), curses.color_pair(4))

        for row, idx, r in self._sl.visible(self._vh):
            y = y0 + 1 + row
            frac = r["disk"] / self._total_disk
            pct  = frac * 100
            line = (f"  {r['label']:<16} {r['count']:>8}  "
                    f"{fmt_size(r['disk']):>12}  {pct:>5.1f}%  ")
            attr = curses.A_REVERSE if idx == self._sl.cursor else 0
            safe_addstr(win, y, x0, truncate(line, width - BAR_WIDTH - 2), attr)
            draw_bar(win, y, x0 + len(line), frac)

        hint = " [Enter] drill into bucket   [t] toggle mtime/atime/ctime"
        safe_addstr(win, y0 + height - 1, x0, truncate(hint, width), curses.color_pair(2))
