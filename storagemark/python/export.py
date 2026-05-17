from __future__ import annotations
import csv
import json
import io
from datetime import datetime
from .model import DirTree, FileNode


def _node_row(n: FileNode) -> dict:
    return {
        "path": n.path,
        "name": n.name,
        "type": n.type,
        "size_bytes": n.size_bytes,
        "size_disk": n.size_disk,
        "subtree_disk": n.subtree_disk,
        "ext": n.ext,
        "mtime": n.mtime.isoformat(),
        "atime": n.atime.isoformat(),
        "ctime": n.ctime.isoformat(),
        "depth": n.depth,
        "uid": n.uid,
        "inode": n.inode,
        "error": n.error,
    }


def export_csv(nodes: list[FileNode]) -> str:
    if not nodes:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(_node_row(nodes[0]).keys()))
    w.writeheader()
    for n in nodes:
        w.writerow(_node_row(n))
    return buf.getvalue()


def export_json(tree: DirTree) -> str:
    def _ser(n: FileNode) -> dict:
        d = _node_row(n)
        if n.children:
            d["children"] = [_ser(c) for c in n.children]
        return d
    return json.dumps(_ser(tree.root), indent=2)


def export_cleanup_script(marked: list[FileNode], timestamp: str | None = None) -> str:
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    lines = [
        "#!/bin/sh",
        "# StorageMark cleanup plan — generated " + ts,
        "# Review carefully before running. This script is NOT reversible.",
        "set -e",
        "",
    ]
    for n in marked:
        lines.append(f'rm -rf "{n.path}"')
    lines.append("")
    return "\n".join(lines)
