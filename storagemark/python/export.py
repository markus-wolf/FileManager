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

    total_disk = sum(
        n.subtree_disk if n.type == 'd' else n.size_disk for n in marked
    )
    total_gb = total_disk / (1 << 30)

    # Build the item list used both in the preview and the rm commands
    items = []
    for n in marked:
        size = n.subtree_disk if n.type == 'd' else n.size_disk
        label = f"{size / (1 << 20):8.1f} MB  {n.path}"
        items.append((n.path, label))

    lines = [
        "#!/bin/sh",
        f"# StorageMark cleanup plan — generated {ts}",
        f"# Items: {len(items)}   Total: {total_gb:.2f} GB",
        "#",
        "# Usage:",
        "#   ./script.sh        — preview what will be deleted, then confirm",
        "#   ./script.sh -y     — delete immediately without confirmation",
        "",
        'SILENT=0',
        'if [ "$1" = "-y" ] || [ "$1" = "--yes" ]; then',
        '    SILENT=1',
        'fi',
        "",
        'if [ "$SILENT" = "0" ]; then',
        f'    echo "StorageMark cleanup — {ts}"',
        f'    echo "Items to be permanently deleted ({len(items)} total, {total_gb:.2f} GB):"',
        '    echo ""',
    ]

    for _, label in items:
        lines.append(f'    echo "  {label}"')

    lines += [
        '    echo ""',
        f'    printf "Delete all {len(items)} items? This cannot be undone. [y/N] "',
        '    read -r REPLY',
        '    case "$REPLY" in',
        '        [yY][eE][sS]|[yY]) ;;',
        '        *) echo "Aborted."; exit 0 ;;',
        '    esac',
        '    echo ""',
        'fi',
        "",
        "set -e",
        "",
    ]

    for path, _ in items:
        lines.append(f'rm -rf "{path}"')

    lines += [
        "",
        'echo "Done."',
        "",
    ]

    return "\n".join(lines)
