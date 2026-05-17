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


@dataclass
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
    children: list[FileNode] = field(default_factory=list)
    parent: Optional[FileNode] = field(default=None, repr=False)
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
        )


class DirTree:
    def __init__(self, root: FileNode):
        self.root = root
        self.flat: list[FileNode] = []
        self.ext_map: dict[str, list[FileNode]] = {}
        self.errors: list[FileNode] = []
        self.scan_errors: list[str] = []

    @staticmethod
    def build(records: list[dict]) -> DirTree:
        if not records:
            raise ValueError("No records")

        nodes: dict[str, FileNode] = {}
        for rec in records:
            if "_storagemark" in rec:
                continue
            node = FileNode.from_record(rec)
            nodes[node.path] = node

        # Link parent/child relationships
        root_node: Optional[FileNode] = None
        for node in nodes.values():
            parent_path = os.path.dirname(node.path)
            if parent_path == node.path:
                root_node = node
                continue
            parent = nodes.get(parent_path)
            if parent is None:
                root_node = node if root_node is None else root_node
                continue
            node.parent = parent
            if node.type == 'd' or node not in parent.children:
                parent.children.append(node)

        if root_node is None:
            root_node = next(iter(nodes.values()))

        tree = DirTree(root_node)

        # Flatten and compute subtree totals bottom-up
        def _walk(n: FileNode):
            tree.flat.append(n)
            if n.error:
                tree.errors.append(n)
            if n.type == 'f':
                ext = n.ext or "(no ext)"
                tree.ext_map.setdefault(ext, []).append(n)
            for child in n.children:
                _walk(child)
            # Accumulate into parent after children are done
            if n.type == 'd':
                n.subtree_bytes = n.size_bytes + sum(c.subtree_bytes for c in n.children)
                n.subtree_disk  = n.size_disk  + sum(c.subtree_disk  for c in n.children)
                n.subtree_count = 1            + sum(c.subtree_count for c in n.children)
            else:
                n.subtree_bytes = n.size_bytes
                n.subtree_disk  = n.size_disk
                n.subtree_count = 1

        _walk(root_node)
        return tree

    @property
    def total_disk(self) -> int:
        return self.root.subtree_disk

    @property
    def total_bytes(self) -> int:
        return self.root.subtree_bytes

    @property
    def file_count(self) -> int:
        return sum(1 for n in self.flat if n.type == 'f')

    @property
    def dir_count(self) -> int:
        return sum(1 for n in self.flat if n.type == 'd')


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
