from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from typing import Iterator, Callable


def _find_scanner() -> str:
    candidates = [
        Path(__file__).parent.parent / "c" / "storagescanner",
        Path("/usr/local/bin/storagescanner"),
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    raise FileNotFoundError(
        "storagescanner binary not found. Run: make -C storagemark/c"
    )


def stream_records(
    root: str,
    *,
    one_fs: bool = False,
    follow_links: bool = False,
    max_depth: int = 0,
    skip: list[str] | None = None,
    scanner_path: str | None = None,
    progress_cb: Callable[[int], None] | None = None,
) -> Iterator[dict]:
    """Yield parsed JSON records from the C scanner."""
    binary = scanner_path or _find_scanner()
    cmd = [binary]
    if one_fs:
        cmd.append("-x")
    if follow_links:
        cmd.append("-L")
    if max_depth:
        cmd += ["-d", str(max_depth)]
    if skip:
        cmd += ["--skip", ":".join(skip)]
    cmd.append(os.path.abspath(root))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1 << 20,
    )

    count = 0
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                count += 1
                if progress_cb and count % 1000 == 0:
                    progress_cb(count)
                yield rec
            except json.JSONDecodeError:
                continue
    finally:
        proc.stdout.close()
        proc.wait()


def scan(root: str, **kwargs) -> list[dict]:
    """Collect all records into a list (blocking)."""
    return list(stream_records(root, **kwargs))
