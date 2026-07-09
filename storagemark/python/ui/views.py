"""Textual views: SubDirs (lazy tree), Types, Time, What-If.

The Files view lives in filelist.py (Line-API, handles ~1M rows).
These four are small-row-count views, so ordinary widgets are fine.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Static, Tree
from textual.widgets.tree import TreeNode

from ..model import DirTree, FileNode, fmt_size
from .remove import YOUNG_DAYS
from .theme import ALERT_ORANGE

BAR_FULL, BAR_EMPTY, BAR_W = "█", "░", 20


def bar(frac: float) -> str:
    filled = min(BAR_W, max(0, round(frac * BAR_W)))
    return BAR_FULL * filled + BAR_EMPTY * (BAR_W - filled)


# ------------------------------------------------------------------ #
# SubDirs — lazy Tree: children materialize on expand only            #
# ------------------------------------------------------------------ #

class SubdirsTree(Tree[FileNode]):
    """Directory tree; node labels show size, bar, % of total."""

    def __init__(self, marked: set[str], **kwargs) -> None:
        super().__init__("…", **kwargs)
        self.marked = marked
        self.total = 1
        self.show_root = True
        self.guide_depth = 2

    def load(self, tree: DirTree) -> None:
        self.total = tree.total_disk or 1
        self.clear()
        self.root.data = tree.root
        self.root.set_label(self._label(tree.root))
        self.root.expand()      # NodeExpanded handler materializes children

    def _label(self, n: FileNode) -> str:
        size = n.display_size
        frac = size / self.total
        mark = "●" if n.path in self.marked else " "
        return (f"{mark} {n.name:<32.32} {fmt_size(size):>10}  "
                f"{bar(frac)} {frac * 100:5.1f}%")

    def _add_children(self, node: TreeNode[FileNode]) -> None:
        """Materialize exactly one level, largest first, on first expand.

        Strictly just-in-time: pre-populating a level ahead materialized
        thousands of TreeNodes on big trees — ~20s of main-thread work at
        ~1M files. allow_expand comes from the FileNode itself, so the
        expand arrow is correct without materializing grandchildren.
        """
        if node.children or node.data is None:
            return
        kids = sorted(node.data.children,
                      key=lambda c: c.display_size,
                      reverse=True)
        for child in kids:
            if child.type == "d":
                node.add(self._label(child), data=child,
                         allow_expand=bool(child.children))
            else:
                node.add_leaf(self._label(child), data=child)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        self._add_children(event.node)   # just this node's children

    def refresh_labels(self) -> None:
        """Re-render labels (marks may have changed)."""
        def walk(node: TreeNode[FileNode]) -> None:
            if node.data is not None:
                node.set_label(self._label(node.data))
            for c in node.children:
                walk(c)
        walk(self.root)

    def remove_paths(self, paths: set[str]) -> int:
        """Surgically drop deleted nodes from the materialized tree.

        A full load() rebuild at ~1M nodes costs many seconds of deferred
        main-thread work (Tree re-materialization) — removing just the
        affected TreeNodes is O(materialized) and keeps expansion state.
        """
        doomed: list[TreeNode[FileNode]] = []

        def walk(node: TreeNode[FileNode]) -> None:
            for c in list(node.children):
                if c.data is not None and c.data.path in paths:
                    doomed.append(c)
                else:
                    walk(c)
        walk(self.root)
        for node in doomed:
            node.remove()
        return len(doomed)

    def toggle_mark_selected(self) -> None:
        node = self.cursor_node
        if node and node.data:
            path = node.data.path
            if path in self.marked:
                self.marked.discard(path)
            else:
                self.marked.add(path)
            node.set_label(self._label(node.data))

    def on_key(self, event) -> None:
        # Space marks (Tree's default binding would toggle expand instead).
        # h/j/k/l: vi-style cursor control (arrow keys can be flaky).
        k = event.key
        if k == "space":
            self.toggle_mark_selected()
        elif k == "j":
            self.action_cursor_down()
        elif k == "k":
            self.action_cursor_up()
        elif k == "l":                       # expand (ranger-style)
            node = self.cursor_node
            if node is not None and node.allow_expand and not node.is_expanded:
                node.expand()
        elif k == "h":                       # collapse, or jump to parent
            node = self.cursor_node
            if node is not None and node.is_expanded and node.allow_expand:
                node.collapse()
            elif node is not None and node.parent is not None:
                self.select_node(node.parent)
        else:
            return
        event.stop()
        event.prevent_default()


# ------------------------------------------------------------------ #
# Types — extension summary table                                     #
# ------------------------------------------------------------------ #

class TypesTable(DataTable):
    class DrillExt(Message):
        def __init__(self, ext: str) -> None:
            super().__init__()
            self.ext = ext

    class MarkExt(Message):
        def __init__(self, ext: str) -> None:
            super().__init__()
            self.ext = ext

    def on_mount(self) -> None:
        self.cursor_type = "row"
        cols = self.add_columns("EXT", "COUNT", "MARKED",
                                "TOTAL DISK", "AVG", "%", "")
        self._marked_col = cols[2]
        self._counts: dict[str, int] = {}      # ext -> total file count

    def load(self, tree: DirTree) -> None:
        self.clear()
        total = tree.total_disk or 1
        self._counts.clear()
        rows = []
        for ext, nodes in tree.ext_map.items():
            d = sum(n.size_disk for n in nodes)
            rows.append((ext, len(nodes), d, d // max(1, len(nodes))))
            self._counts[ext] = len(nodes)
        rows.sort(key=lambda r: r[2], reverse=True)
        for ext, count, disk, avg in rows:
            frac = disk / total
            self.add_row(ext, f"{count:,}", "", fmt_size(disk),
                         fmt_size(avg), f"{frac * 100:5.1f}%",
                         bar(frac), key=ext)

    def refresh_marks(self, marked_by_ext: dict[str, int]) -> None:
        """Update the MARKED column. O(rows); counts computed by the app."""
        for ext, total in self._counts.items():
            n = marked_by_ext.get(ext, 0)
            text = "" if n == 0 else ("● all" if n >= total else f"● {n:,}")
            try:
                self.update_cell(ext, self._marked_col, text)
            except Exception:
                pass                       # row pruned since load

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.post_message(self.DrillExt(str(event.row_key.value)))

    def on_key(self, event) -> None:
        if event.key in ("j", "k"):          # vi cursor keys
            (self.action_cursor_down if event.key == "j"
             else self.action_cursor_up)()
            event.stop()
        elif event.key == "space" and self.cursor_row is not None:
            row_key, _ = self.coordinate_to_cell_key((self.cursor_row, 0))
            self.post_message(self.MarkExt(str(row_key.value)))
            event.stop()


# ------------------------------------------------------------------ #
# Time — age buckets table                                            #
# ------------------------------------------------------------------ #

TIME_FIELDS = ["mtime", "atime", "ctime"]


def buckets(now: datetime):
    return [
        ("> 2 years",   None,                       now - timedelta(days=730)),
        ("1–2 years",   now - timedelta(days=730),  now - timedelta(days=365)),
        ("6–12 months", now - timedelta(days=365),  now - timedelta(days=182)),
        ("1–6 months",  now - timedelta(days=182),  now - timedelta(days=30)),
        ("< 1 month",   now - timedelta(days=30),   None),
    ]


class TimeTable(DataTable):
    class DrillBucket(Message):
        def __init__(self, label: str, field: str,
                     lo: datetime | None, hi: datetime | None) -> None:
            super().__init__()
            self.label, self.field, self.lo, self.hi = label, field, lo, hi

    class MarkBucket(DrillBucket):
        pass

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.field_idx = 0
        self._ranges: dict[str, tuple] = {}

    def on_mount(self) -> None:
        self.cursor_type = "row"
        cols = self.add_columns("BUCKET", "FILES", "MARKED",
                                "DISK SIZE", "%", "")
        self._marked_col = cols[2]
        self._counts: dict[str, int] = {}      # label -> total file count

    def load(self, tree: DirTree) -> None:
        self.clear()
        field = TIME_FIELDS[self.field_idx]
        total = tree.total_disk or 1
        bks = buckets(datetime.now())
        self._ranges.clear()
        self._counts.clear()

        # Single pass over ~1M files (was one pass per bucket). Buckets are
        # ordered oldest→newest; a file belongs to the first bucket whose
        # upper bound (hi) it is below.
        counts = [0] * len(bks)
        disks = [0] * len(bks)
        for n in tree.flat:
            if n.type != "f":
                continue
            t = getattr(n, field)
            for i, (_label, lo, hi) in enumerate(bks):
                if hi is None or t < hi:
                    counts[i] += 1
                    disks[i] += n.size_disk
                    break

        for i, (label, lo, hi) in enumerate(bks):
            frac = disks[i] / total
            self._ranges[label] = (field, lo, hi)
            self._counts[label] = counts[i]
            self.add_row(label, f"{counts[i]:,}", "",
                         fmt_size(disks[i]),
                         f"{frac * 100:5.1f}%", bar(frac), key=label)

    def refresh_marks(self, marked_by_bucket: dict[str, int]) -> None:
        for label, total in self._counts.items():
            n = marked_by_bucket.get(label, 0)
            text = "" if n == 0 else ("● all" if n >= total else f"● {n:,}")
            try:
                self.update_cell(label, self._marked_col, text)
            except Exception:
                pass

    def bucket_ranges(self) -> dict[str, tuple]:
        """label -> (field, lo, hi) for the current time field."""
        return dict(self._ranges)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        label = str(event.row_key.value)
        self.post_message(self.DrillBucket(label, *self._ranges[label]))

    def on_key(self, event) -> None:
        if event.key in ("j", "k"):          # vi cursor keys
            (self.action_cursor_down if event.key == "j"
             else self.action_cursor_up)()
            event.stop()
        elif event.key == "space" and self.cursor_row is not None:
            row_key, _ = self.coordinate_to_cell_key((self.cursor_row, 0))
            label = str(row_key.value)
            self.post_message(self.MarkBucket(label, *self._ranges[label]))
            event.stop()


# ------------------------------------------------------------------ #
# What-If — marked items + projected savings                          #
# ------------------------------------------------------------------ #

class WhatIfPanel(Vertical):
    def __init__(self, marked: set[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.marked = marked
        self.dir_tree: DirTree | None = None
        self.node_map: dict[str, FileNode] = {}

    def compose(self):
        yield Static(id="whatif-summary")
        yield DataTable(id="whatif-table")

    def on_mount(self) -> None:
        t = self.query_one("#whatif-table", DataTable)
        t.cursor_type = "row"
        t.add_columns("", "SIZE", "PATH")

    def load(self, tree: DirTree, node_map: dict[str, FileNode]) -> None:
        self.dir_tree = tree
        self.node_map = node_map
        self.reload()

    def reload(self) -> None:
        if self.dir_tree is None:
            return
        node_map = self.node_map
        nodes = [node_map[p] for p in sorted(self.marked) if p in node_map]
        total = self.dir_tree.total_disk or 1
        would_free = sum(n.display_size
                         for n in nodes)

        warn = []
        now = datetime.now()
        marked = set(self.marked)
        for n in nodes:
            if (now - n.mtime).days < YOUNG_DAYS:
                warn.append(f"! {n.name} is < {YOUNG_DAYS} days old")
            parent = n.parent
            while parent is not None:
                if parent.path in marked:
                    warn.append(f"! {n.name} is inside marked {parent.name}")
                    break
                parent = parent.parent
        warn = warn[:8]

        summary = (
            f"[b]WHAT-IF[/b]  marked: {len(nodes):,} items   "
            f"would free: [b]{fmt_size(would_free)}[/b] "
            f"({would_free / total * 100:.1f}% of total)   "
            f"after: {fmt_size(max(0, total - would_free))}\n"
            + ("\n".join(f"[bold {ALERT_ORANGE}]{w}[/]" for w in warn) if warn else
               "[dim]Space=unmark  x=clear all  D=remove (Trash/permanent)  "
               "Enter=export script[/dim]")
        )
        self.query_one("#whatif-summary", Static).update(summary)

        t = self.query_one("#whatif-table", DataTable)
        t.clear()
        for n in nodes:
            size = n.display_size
            t.add_row("✓", fmt_size(size), n.path, key=n.path)

    def on_key(self, event) -> None:
        # Space on a marked row unmarks it; j/k move the cursor (vi keys).
        t = self.query_one("#whatif-table", DataTable)
        if not t.has_focus:
            return
        if event.key in ("j", "k"):
            (t.action_cursor_down if event.key == "j"
             else t.action_cursor_up)()
            event.stop()
        elif event.key == "space" and t.cursor_row is not None and t.row_count:
            row_key, _ = t.coordinate_to_cell_key((t.cursor_row, 0))
            self.marked.discard(str(row_key.value))
            self.reload()
            event.stop()
