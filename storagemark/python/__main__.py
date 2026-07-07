from __future__ import annotations
import argparse
import json
import os
import sys
import time

from .scanner import scan
from .model   import DirTree, fmt_size, short_hostname
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
    p.add_argument("-d", "--depth", type=int, default=0,
                   help="Max scan depth (0 = unlimited)")
    p.add_argument("-x", "--one-filesystem", action="store_true",
                   help="Do not cross mount points")
    p.add_argument("--skip", action="append", default=[],
                   help="Skip paths matching glob (repeatable)")
    p.add_argument("--scanner", default=None,
                   help="Override path to storagescanner binary")
    p.add_argument("--classic", action="store_true",
                   help=argparse.SUPPRESS)   # removed in 1.1; friendly error
    return p.parse_args()


def once_text(tree: DirTree, root: str):
    host = short_hostname()
    location = f"{host}:{root}" if host else root
    print(f"\nStorageMark — {location}")
    print(f"  Total disk:  {fmt_size(tree.total_disk)}")
    print(f"  Total bytes: {fmt_size(tree.total_bytes)}")
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
        print(f"    {fmt_size(d.subtree_disk):>10}  {pct:5.1f}%  {d.path}")
    print()
    print("  Top file types by disk usage:")
    for ext, nodes in sorted(
        tree.ext_map.items(),
        key=lambda kv: sum(n.size_disk for n in kv[1]), reverse=True
    )[:10]:
        total = sum(n.size_disk for n in nodes)
        pct = total / (tree.total_disk or 1) * 100
        print(f"    {fmt_size(total):>10}  {pct:5.1f}%  {ext or '(no ext)'}  ({len(nodes):,} files)")


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
        if args.format == "json":
            print(export_json(tree))
        elif args.format == "csv":
            print(export_csv(tree.flat))
        else:
            once_text(tree, root)
            print(f"  Scanned in {elapsed:.2f}s")
    elif args.classic:
        print("The legacy curses UI was removed in v1.1 — the Textual UI "
              "is the only interface.\nFor the last curses version: "
              "uv tool install 'git+https://github.com/markus-wolf/"
              "FileManager@v1.0.1'", file=sys.stderr)
        sys.exit(1)
    else:
        from .ui.app import run_ui
        run_ui(root, scanner_kwargs=scanner_kwargs)


if __name__ == "__main__":
    main()
