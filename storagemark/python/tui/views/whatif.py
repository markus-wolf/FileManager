from __future__ import annotations
import curses
import os
from datetime import datetime
from ...model import DirTree, FileNode, fmt_size
from ...export import export_cleanup_script
from ..widgets import ScrollList, safe_addstr, truncate

YOUNG_DAYS = 30


class WhatIfView:
    def __init__(self):
        self._sl = ScrollList()
        self._tree: DirTree | None = None
        self._marked: set[str] = set()
        self._msg = ""

    def load(self, tree: DirTree, marked: set[str]):
        self._tree = tree
        self._marked = marked
        self._rebuild()

    def _rebuild(self):
        if not self._tree:
            return
        node_map = {n.path: n for n in self._tree.flat}
        items = []
        for p in sorted(self._marked):
            node = node_map.get(p)
            if node:
                items.append(node)
        self._sl.set_items(items)

    def _warnings(self, nodes: list[FileNode]) -> list[str]:
        warnings = []
        now = datetime.now()
        marked_paths = set(self._marked)
        seen_inodes: set[int] = set()
        for n in nodes:
            age = (now - n.mtime).days
            if age < YOUNG_DAYS:
                warnings.append(f"  ! {n.name} is < {YOUNG_DAYS} days old ({age}d)")
            if n.inode in seen_inodes:
                warnings.append(f"  ! {n.name} has overlapping hard-links")
            seen_inodes.add(n.inode)
            # Check if any ancestor is already marked (redundant deletion)
            parts = n.path.split(os.sep)
            for i in range(1, len(parts)):
                ancestor = os.sep.join(parts[:i])
                if ancestor in marked_paths and ancestor != n.path:
                    warnings.append(f"  ! {n.name} is inside already-marked {ancestor}")
                    break
        return warnings[:10]  # cap display

    def handle_key(self, key: int) -> str | None:
        if key in (ord('j'), curses.KEY_DOWN):
            self._sl.move(1, 20)
        elif key in (ord('k'), curses.KEY_UP):
            self._sl.move(-1, 20)
        elif key == ord('x'):
            self._marked.clear()
            self._rebuild()
            self._msg = "All marks cleared."
        elif key == ord(' '):
            node = self._sl.selected()
            if node:
                self._marked.discard(node.path)
                self._rebuild()
        elif key in (curses.KEY_ENTER, 10, 13):
            self._export_script()
        elif key == ord('p'):
            self._export_script()
        return None

    def _export_script(self):
        if not self._tree:
            return
        node_map = {n.path: n for n in self._tree.flat}
        nodes = [node_map[p] for p in self._marked if p in node_map]
        if not nodes:
            self._msg = "Nothing marked."
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"storagemark_cleanup_{ts}.sh"
        script = export_cleanup_script(nodes, ts)
        with open(fname, "w") as f:
            f.write(script)
        os.chmod(fname, 0o755)
        self._msg = f"Script saved: {fname}"

    def draw(self, win, y0: int, x0: int, height: int, width: int):
        self._rebuild()
        if not self._tree:
            return

        node_map = {n.path: n for n in self._tree.flat}
        nodes = [node_map[p] for p in self._marked if p in node_map]
        would_free = sum(
            n.subtree_disk if n.type == 'd' else n.size_disk for n in nodes
        )
        total = self._tree.total_disk or 1

        y = y0
        safe_addstr(win, y, x0, truncate(" WHAT-IF SCENARIO", width), curses.color_pair(4))
        y += 1
        safe_addstr(win, y, x0, "─" * min(width, 60), curses.color_pair(2))
        y += 1

        if not nodes:
            safe_addstr(win, y, x0, "  No items marked. Use [Space] in other views to mark.", curses.color_pair(2))
            y += 2
        else:
            safe_addstr(win, y, x0, "  Marked for removal:", curses.A_BOLD)
            y += 1
            for row, idx, node in self._sl.visible(height - 10):
                size = node.subtree_disk if node.type == 'd' else node.size_disk
                line = f"    {'✓'} {truncate(node.name, 40):<42} {fmt_size(size):>10}"
                attr = curses.A_REVERSE if idx == self._sl.cursor else 0
                safe_addstr(win, y, x0, truncate(line, width), attr)
                y += 1
            y += 1

        pct_of_total = (would_free / total * 100) if total else 0
        safe_addstr(win, y, x0,
                    f"  Would free:  {fmt_size(would_free)}  ({pct_of_total:.1f}% of total)",
                    curses.A_BOLD)
        y += 1
        remaining = max(0, total - would_free)
        safe_addstr(win, y, x0, f"  After:       {fmt_size(remaining)} remaining")
        y += 2

        warnings = self._warnings(nodes)
        if warnings:
            safe_addstr(win, y, x0, "  Conflicts / warnings:", curses.color_pair(6))
            y += 1
            for w in warnings:
                safe_addstr(win, y, x0, truncate(w, width), curses.color_pair(6))
                y += 1
            y += 1

        hints = " [Enter]/[p] Export cleanup script   [x] Clear all   [Space] Unmark"
        safe_addstr(win, y, x0, truncate(hints, width), curses.color_pair(2))

        if self._msg:
            safe_addstr(win, y + 1, x0, f"  {self._msg}", curses.color_pair(5))
