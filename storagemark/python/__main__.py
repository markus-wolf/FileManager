from __future__ import annotations
import argparse
import json
import os
import sys
import time

from .scanner import scan, stream_records
from .model   import DirTree, fmt_size
from .export  import export_csv, export_json


def parse_args():
    from .. import __version__
    p = argparse.ArgumentParser(
        prog="storagemark",
        description="Interactive disk space analyzer",
    )
    p.add_argument("--version", action="version",
                   version=f"storagemark {__version__}")
    p.add_argument("path", nargs="?", default=".",
                   help="Root path to scan (default: current directory)")
    p.add_argument("-o", "--once", action="store_true",
                   help="Non-interactive: print summary and exit")
    p.add_argument("-f", "--format", choices=["text", "json", "csv"], default="text",
                   help="Output format for --once")
    p.add_argument("-v", "--view", type=int, choices=[1,2,3,4,5], default=1,
                   help="Start in view N (1-5)")
    p.add_argument("-d", "--depth", type=int, default=0,
                   help="Max scan depth (0 = unlimited)")
    p.add_argument("-x", "--one-filesystem", action="store_true",
                   help="Do not cross mount points")
    p.add_argument("--skip", action="append", default=[],
                   help="Skip paths matching glob (repeatable)")
    p.add_argument("--threshold", default=None,
                   help="Hide entries smaller than size (e.g. 10MB)")
    p.add_argument("--scanner", default=None,
                   help="Override path to storagescanner binary")
    p.add_argument("--classic", action="store_true",
                   help="Use the legacy curses UI instead of Textual")
    return p.parse_args()


def _parse_threshold(s: str | None) -> int:
    if not s:
        return 0
    s = s.strip().upper()
    mult = {"B": 1, "KB": 1<<10, "MB": 1<<20, "GB": 1<<30}
    for suffix, m in sorted(mult.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return int(s[:-len(suffix)]) * m
    return int(s)


def once_text(tree: DirTree, root: str):
    import socket
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = ""
    location = f"{host}:{root}" if host else root
    print(f"\nStorageMark — {location}")
    print(f"  Total disk:  {fmt_size(tree.total_disk).strip()}")
    print(f"  Total bytes: {fmt_size(tree.total_bytes).strip()}")
    print(f"  Files:       {tree.file_count:,}")
    print(f"  Dirs:        {tree.dir_count:,}")
    print(f"  Errors:      {len(tree.errors)}")
    print()
    print("  Top directories by disk usage:")
    dirs = sorted(
        [n for n in tree.flat if n.type == 'd'],
        key=lambda n: n.subtree_disk, reverse=True
    )[:15]
    for d in dirs:
        pct = d.subtree_disk / (tree.total_disk or 1) * 100
        print(f"    {fmt_size(d.subtree_disk).strip():>10}  {pct:5.1f}%  {d.path}")
    print()
    print("  Top file types by disk usage:")
    for ext, nodes in sorted(
        tree.ext_map.items(),
        key=lambda kv: sum(n.size_disk for n in kv[1]), reverse=True
    )[:10]:
        total = sum(n.size_disk for n in nodes)
        pct = total / (tree.total_disk or 1) * 100
        print(f"    {fmt_size(total).strip():>10}  {pct:5.1f}%  {ext or '(no ext)'}  ({len(nodes):,} files)")


def main():
    args = parse_args()
    root = os.path.abspath(args.path)

    if not os.path.exists(root):
        print(f"Error: path does not exist: {root}", file=sys.stderr)
        sys.exit(1)

    scanner_kwargs = dict(
        one_fs=args.one_filesystem,
        max_depth=args.depth,
        skip=args.skip or None,
        scanner_path=args.scanner,
    )

    if args.once:
        t0 = time.time()
        records = scan(root, **scanner_kwargs)
        tree = DirTree.build(records)
        elapsed = time.time() - t0
        threshold = _parse_threshold(args.threshold)
        if args.format == "json":
            print(export_json(tree))
        elif args.format == "csv":
            print(export_csv(tree.flat))
        else:
            once_text(tree, root)
            print(f"  Scanned in {elapsed:.2f}s")
    elif args.classic:
        from .tui.app import run_tui
        run_tui(root, start_view=args.view, scanner_kwargs=scanner_kwargs)
    else:
        from .ui.app import run_ui
        run_ui(root, scanner_kwargs=scanner_kwargs)


if __name__ == "__main__":
    main()
