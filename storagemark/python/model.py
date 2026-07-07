from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import os


def _dt(epoch: int) -> datetime:
    try:
        return datetime.fromtimestamp(epoch)
    except (OSError, OverflowError):
        return datetime.fromtimestamp(0)


@dataclass(slots=True)          # slots=True: ~60% less memory per object (Py 3.10+)
class FileNode:
    path: str
    name: str
    type: str                   # 'f', 'd', 'l', 'o'
    size_bytes: int
    size_disk: int
    inode: int
    dev: int
    uid: int
    mtime: datetime
    atime: datetime
    ctime: datetime
    depth: int
    ext: str
    hardlink_of: str
    error: str
    # repr=False is load-bearing: repr of any node would otherwise recurse
    # through parent to the root and back down children — stringifying the
    # entire tree (~14s at 1M nodes when anything reprs a FileNode, e.g.
    # Textual's @work building a worker description from its arguments).
    children: list = field(repr=False)      # list[FileNode]
    parent: object = field(repr=False)      # FileNode | None
    subtree_bytes: int = 0
    subtree_disk: int = 0
    subtree_count: int = 0

    @staticmethod
    def from_record(rec: dict) -> FileNode:
        path = rec.get("path", "")
        name = rec.get("name", "") or os.path.basename(path)
        raw_ext = os.path.splitext(name)[1].lower()
        return FileNode(
            path=path,
            name=name,
            type=rec.get("type", "f"),
            size_bytes=rec.get("size_bytes", 0),
            size_disk=rec.get("size_disk", 0),
            inode=rec.get("inode", 0),
            dev=rec.get("dev", 0),
            uid=rec.get("uid", 0),
            mtime=_dt(rec.get("mtime", 0)),
            atime=_dt(rec.get("atime", 0)),
            ctime=_dt(rec.get("ctime", 0)),
            depth=rec.get("depth", 0),
            ext=raw_ext,
            hardlink_of=rec.get("hardlink_of", ""),
            error=rec.get("error", ""),
            children=[],
            parent=None,
            subtree_bytes=0,
            subtree_disk=0,
            subtree_count=0,
        )


class DirTree:
    def __init__(self, root: FileNode,
                 flat: list[FileNode],
                 ext_map: dict[str, list[FileNode]],
                 errors: list[FileNode],
                 file_count: int,
                 dir_count: int):
        self.root       = root
        self.flat       = flat
        self.ext_map    = ext_map
        self.errors     = errors
        self._file_count = file_count
        self._dir_count  = dir_count

    @staticmethod
    def build(records: list[dict]) -> DirTree:
        if not records:
            raise ValueError("No records")

        # ---------------------------------------------------------- #
        # Pass 1: create FileNode objects, index by path              #
        # ---------------------------------------------------------- #
        nodes: dict[str, FileNode] = {}
        for rec in records:
            if "_storagemark" in rec:
                continue
            node = FileNode.from_record(rec)
            nodes[node.path] = node

        # ---------------------------------------------------------- #
        # Pass 2: link parent → child                                 #
        #                                                             #
        # FIX: was `node not in parent.children` — O(n) per append   #
        # making the overall loop O(n²) for large directories.        #
        # Simple append is safe: each (path, parent) pair is unique.  #
        # ---------------------------------------------------------- #
        root_node: Optional[FileNode] = None
        for node in nodes.values():
            parent_path = os.path.dirname(node.path)
            if parent_path == node.path:
                root_node = node
                continue
            parent = nodes.get(parent_path)
            if parent is None:
                # Orphan — treat as root candidate
                if root_node is None:
                    root_node = node
                continue
            node.parent = parent
            parent.children.append(node)   # O(1), no duplicate check needed

        if root_node is None:
            root_node = next(iter(nodes.values()))

        # Free the raw dict — no longer needed; saves memory at peak
        nodes.clear()

        # ---------------------------------------------------------- #
        # Pass 3: iterative DFS (pre-order) to build flat list        #
        #                                                             #
        # FIX: was recursive — hits Python's recursion limit on deep  #
        # trees and is slow for 1M+ nodes.                            #
        # ---------------------------------------------------------- #
        flat: list[FileNode] = []
        ext_map: dict[str, list[FileNode]] = {}
        errors: list[FileNode] = []
        file_count = 0
        dir_count  = 0

        stack = [root_node]
        while stack:
            node = stack.pop()
            flat.append(node)

            if node.error:
                errors.append(node)

            if node.type == 'f':
                file_count += 1
                ext = node.ext or "(no ext)"
                ext_map.setdefault(ext, []).append(node)
            elif node.type == 'd':
                dir_count += 1

            # Push children in reverse so left-to-right order is preserved
            for child in reversed(node.children):
                stack.append(child)

        # ---------------------------------------------------------- #
        # Pass 4: iterative post-order subtree aggregation            #
        #                                                             #
        # flat is in pre-order; reversing it gives a valid post-order #
        # (every child appears before its parent when reversed).      #
        # ---------------------------------------------------------- #
        for node in reversed(flat):
            if node.type == 'd':
                sb = node.size_bytes
                sd = node.size_disk
                sc = 1
                for c in node.children:
                    sb += c.subtree_bytes
                    sd += c.subtree_disk
                    sc += c.subtree_count
                node.subtree_bytes = sb
                node.subtree_disk  = sd
                node.subtree_count = sc
            else:
                node.subtree_bytes = node.size_bytes
                node.subtree_disk  = node.size_disk
                node.subtree_count = 1

        return DirTree(root_node, flat, ext_map, errors, file_count, dir_count)

    def prune(self, paths: list[str], node_map: dict[str, FileNode]) -> int:
        """Remove subtrees rooted at `paths` from the in-memory tree.

        Called after files are deleted/trashed on disk so the UI updates
        instantly instead of rescanning. Subtree aggregates of every
        ancestor are decremented; flat/ext_map/errors/counts are rebuilt
        by filtering (O(N), ~0.3s at 1M nodes). Entries for removed nodes
        are also deleted from node_map in place, so callers must NOT
        rebuild it from scratch. Returns nodes removed.
        """
        roots = [node_map[p] for p in paths if p in node_map]
        if not roots:
            return 0

        # Collect every node in the removed subtrees (iterative DFS).
        removed: set[int] = set()
        for r in roots:
            if id(r) in removed:
                continue
            stack: list[FileNode] = [r]
            while stack:
                n = stack.pop()
                if id(n) in removed:
                    continue
                removed.add(id(n))
                node_map.pop(n.path, None)   # keep node_map current, O(removed)
                stack.extend(n.children)

        # Detach top-level roots (parent not itself removed) and subtract
        # their subtree totals from every ancestor.
        for r in roots:
            p = r.parent
            if p is None or id(p) in removed:
                continue
            try:
                p.children.remove(r)
            except ValueError:
                continue   # already detached (duplicate path in input)
            sb, sd, sc = r.subtree_bytes, r.subtree_disk, r.subtree_count
            while p is not None:
                p.subtree_bytes -= sb
                p.subtree_disk -= sd
                p.subtree_count -= sc
                p = p.parent

        self.flat = [n for n in self.flat if id(n) not in removed]
        self.errors = [n for n in self.errors if id(n) not in removed]
        for ext in list(self.ext_map):
            kept = [n for n in self.ext_map[ext] if id(n) not in removed]
            if kept:
                self.ext_map[ext] = kept
            else:
                del self.ext_map[ext]
        self._file_count = sum(1 for n in self.flat if n.type == "f")
        self._dir_count = sum(1 for n in self.flat if n.type == "d")
        return len(removed)

    @property
    def total_disk(self) -> int:
        return self.root.subtree_disk

    @property
    def total_bytes(self) -> int:
        return self.root.subtree_bytes

    @property
    def file_count(self) -> int:
        return self._file_count

    @property
    def dir_count(self) -> int:
        return self._dir_count


def fmt_size(b: int, unit: str = "auto") -> str:
    units = [("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10), ("B", 1)]
    if unit == "auto":
        for suffix, div in units:
            if b >= div:
                return f"{b / div:7.1f} {suffix}"
        return f"{b:7d} B  "
    mapping = {"GB": 1 << 30, "MB": 1 << 20, "KB": 1 << 10, "B": 1}
    div = mapping.get(unit, 1)
    return f"{b / div:7.1f} {unit}"
