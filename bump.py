#!/usr/bin/env python3
"""Bump StorageMark's version, commit it, and create a matching git tag.

The version lives in exactly one place — storagemark/__init__.py — and
pyproject.toml reads it dynamically, so this script only edits that file.

Usage:
    ./bump.py patch          # 0.1.0 -> 0.1.1
    ./bump.py minor          # 0.1.0 -> 0.2.0
    ./bump.py major          # 0.1.0 -> 1.0.0
    ./bump.py 1.4.0          # set an explicit version
    ./bump.py patch --push   # also push the commit and tag to origin
    ./bump.py patch --dry-run # show what would happen, change nothing

Creates an annotated tag `v<version>` (e.g. v0.1.1).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INIT = ROOT / "storagemark" / "__init__.py"
VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)


def die(msg: str) -> None:
    sys.stderr.write(f"bump: {msg}\n")
    sys.exit(1)


def git(*args: str, capture: bool = False) -> str:
    res = subprocess.run(["git", *args], cwd=ROOT,
                         capture_output=capture, text=True)
    if res.returncode != 0:
        if capture and res.stderr:
            sys.stderr.write(res.stderr)
        die(f"`git {' '.join(args)}` failed")
    return (res.stdout or "").strip()


def read_version() -> str:
    m = VERSION_RE.search(INIT.read_text())
    if not m:
        die(f"could not find __version__ in {INIT}")
    return m.group(1)


def compute(current: str, bump: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", bump):
        return bump  # explicit version
    parts = current.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        die(f"current version {current!r} is not MAJOR.MINOR.PATCH")
    major, minor, patch = map(int, parts)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    die("bump must be one of: major, minor, patch, or an explicit X.Y.Z")


def main() -> None:
    ap = argparse.ArgumentParser(description="Bump version, commit, and tag.")
    ap.add_argument("bump", help="major | minor | patch | X.Y.Z")
    ap.add_argument("--push", action="store_true",
                    help="push the commit and tag to origin")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen without changing anything")
    args = ap.parse_args()

    current = read_version()
    new = compute(current, args.bump)
    tag = f"v{new}"

    if new == current:
        die(f"version is already {current}")

    print(f"  {current}  ->  {new}   (tag {tag})")
    if args.dry_run:
        print("  [dry-run] no changes made")
        return

    # Pre-flight checks (only matter when we're about to commit + tag)
    if git("tag", "-l", tag, capture=True):
        die(f"tag {tag} already exists")
    dirty = git("status", "--porcelain", capture=True)
    if dirty:
        die("working tree is not clean — commit or stash first:\n" + dirty)

    # 1. Update the single version source
    INIT.write_text(VERSION_RE.sub(f'__version__ = "{new}"', INIT.read_text()))

    # 2. Commit + annotated tag
    git("add", str(INIT.relative_to(ROOT)))
    git("commit", "-m", f"Release {tag}")
    git("tag", "-a", tag, "-m", f"StorageMark {tag}")
    print(f"  committed and tagged {tag}")

    # 3. Optionally push
    if args.push:
        branch = git("rev-parse", "--abbrev-ref", "HEAD", capture=True)
        git("push", "origin", branch)
        git("push", "origin", tag)
        print(f"  pushed {branch} and {tag} to origin")
    else:
        print(f"  to publish:  git push origin HEAD && git push origin {tag}")


if __name__ == "__main__":
    main()
