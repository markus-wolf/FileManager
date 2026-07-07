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
        self._add_children(self.root)
        self.root.expand()

    def _label(self, n: FileNode) -> str:
        size = n.subtree_disk if n.type == "d" else n.size_disk
        frac = size / self.total
        mark = "●" if n.path in self.marked else " "
        return (f"{mark} {n.name:<32.32} {fmt_size(size):>10}  "
                f"{bar(frac)} {frac * 100:5.1f}%")

    def _add_children(self, node: TreeNode[FileNode]) -> None:
        """Materialize one level, largest first. Called lazily on expand."""
        if node.children or node.data is None:
            return
        kids = sorted(node.data.children,
                      key=lambda c: c.subtree_disk if c.type == "d" else c.size_disk,
                      reverse=True)
        for child in kids:
            if child.type == "d":
                node.add(self._label(child), data=child,
                         allow_expand=bool(child.children))
            else:
                node.add_leaf(self._label(child), data=child)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        for child in event.node.children:
            self._add_children(child)   # pre-populate one level ahead

    def refresh_labels(self) -> None:
        """Re-render labels (marks may have changed)."""
        def walk(node: TreeNode[FileNode]) -> None:
            if node.data is not None:
                node.set_label(self._label(node.data))
            for c in node.children:
                walk(c)
        walk(self.root)

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
        # Space marks (Tree's default binding would toggle expand instead)
        if event.key == "space":
            self.toggle_mark_selected()
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
        self.add_columns("EXT", "COUNT", "TOTAL DISK", "AVG", "%", "")

    def load(self, tree: DirTree) -> None:
        self.clear()
        total = tree.total_disk or 1
        rows = []
        for ext, nodes in tree.ext_map.items():
            d = sum(n.size_disk for n in nodes)
            rows.append((ext, len(nodes), d, d // max(1, len(nodes))))
        rows.sort(key=lambda r: r[2], reverse=True)
        for ext, count, disk, avg in rows:
            frac = disk / total
            self.add_row(ext, f"{count:,}", fmt_size(disk).strip(),
                         fmt_size(avg).strip(), f"{frac * 100:5.1f}%",
                         bar(frac), key=ext)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.post_message(self.DrillExt(str(event.row_key.value)))

    def on_key(self, event) -> None:
        if event.key == "space" and self.cursor_row is not None:
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
        self.add_columns("BUCKET", "FILES", "DISK SIZE", "%", "")

    def load(self, tree: DirTree) -> None:
        self.clear()
        field = TIME_FIELDS[self.field_idx]
        total = tree.total_disk or 1
        files = [n for n in tree.flat if n.type == "f"]
        self._ranges.clear()
        for label, lo, hi in buckets(datetime.now()):
            sel = [f for f in files
                   if (lo is None or getattr(f, field) >= lo)
                   and (hi is None or getattr(f, field) < hi)]
            disk = sum(n.size_disk for n in sel)
            frac = disk / total
            self._ranges[label] = (field, lo, hi)
            self.add_row(label, f"{len(sel):,}", fmt_size(disk).strip(),
                         f"{frac * 100:5.1f}%", bar(frac), key=label)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        label = str(event.row_key.value)
        self.post_message(self.DrillBucket(label, *self._ranges[label]))

    def on_key(self, event) -> None:
        if event.key == "space" and self.cursor_row is not None:
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
        would_free = sum(n.subtree_disk if n.type == "d" else n.size_disk
                         for n in nodes)

        warn = []
        now = datetime.now()
        marked = set(self.marked)
        for n in nodes:
            if (now - n.mtime).days < 30:
                warn.append(f"! {n.name} is < 30 days old")
            parent = n.parent
            while parent is not None:
                if parent.path in marked:
                    warn.append(f"! {n.name} is inside marked {parent.name}")
                    break
                parent = parent.parent
        warn = warn[:8]

        summary = (
            f"[b]WHAT-IF[/b]  marked: {len(nodes):,} items   "
            f"would free: [b]{fmt_size(would_free).strip()}[/b] "
            f"({would_free / total * 100:.1f}% of total)   "
            f"after: {fmt_size(max(0, total - would_free)).strip()}\n"
            + ("\n".join(f"[bold {ALERT_ORANGE}]{w}[/]" for w in warn) if warn else
               "[dim]Space=unmark  x=clear all  D=remove (Trash/permanent)  "
               "Enter=export script[/dim]")
        )
        self.query_one("#whatif-summary", Static).update(summary)

        t = self.query_one("#whatif-table", DataTable)
        t.clear()
        for n in nodes:
            size = n.subtree_disk if n.type == "d" else n.size_disk
            t.add_row("✓", fmt_size(size).strip(), n.path, key=n.path)

    def on_key(self, event) -> None:
        # Space on a marked row unmarks it (bubbles up from the DataTable)
        if event.key != "space":
            return
        t = self.query_one("#whatif-table", DataTable)
        if t.has_focus and t.cursor_row is not None and t.row_count:
            row_key, _ = t.coordinate_to_cell_key((t.cursor_row, 0))
            self.marked.discard(str(row_key.value))
            self.reload()
            event.stop()
