from __future__ import annotations
import io
import json
import os
import struct
import subprocess
from pathlib import Path
from typing import Iterator, Callable

# ------------------------------------------------------------------ #
# Binary record format                                                 #
# Must match storagescanner.c exactly.                                 #
# ------------------------------------------------------------------ #

MAGIC       = b"SMRK\x01\x00\x00\x00"
MAGIC_LEN   = 8
HDR_FMT     = "<BHBIQQQQqqqHHHH"   # little-endian, 72 bytes
HDR_SIZE    = struct.calcsize(HDR_FMT)   # == 72

assert HDR_SIZE == 72, f"HDR_SIZE mismatch: {HDR_SIZE}"

# Field indices in the unpacked tuple
_TYPE, _DEPTH, _FLAGS, _UID, _SIZE_BYTES, _SIZE_DISK, \
_INODE, _DEV, _MTIME, _ATIME, _CTIME, \
_PATH_LEN, _NAME_LEN, _HARDLINK_LEN, _ERROR_LEN = range(15)


def _read_exact(stream, n: int) -> bytes | None:
    """Read exactly n bytes; return None on EOF/short read."""
    if n == 0:
        return b""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        chunk = stream.read(n - got)
        if not chunk:
            return None
        view[got:got + len(chunk)] = chunk
        got += len(chunk)
    return bytes(buf)


def _stream_binary(
    stream,
    progress_cb: Callable[[int], None] | None,
) -> Iterator[dict]:
    """Parse binary records from the C scanner stream."""
    magic = _read_exact(stream, MAGIC_LEN)
    if magic != MAGIC:
        raise ValueError(f"Bad magic: {magic!r}")

    count = 0

    while True:
        chunk = _read_exact(stream, HDR_SIZE)
        if chunk is None:
            break

        fields = struct.unpack_from(HDR_FMT, chunk)

        path_len     = fields[_PATH_LEN]
        name_len     = fields[_NAME_LEN]
        hardlink_len = fields[_HARDLINK_LEN]
        error_len    = fields[_ERROR_LEN]

        var_len = path_len + name_len + hardlink_len + error_len
        var = _read_exact(stream, var_len) if var_len else b""
        if var is None:
            break

        o = 0
        path        = var[o:o + path_len].decode("utf-8", errors="replace"); o += path_len
        name        = var[o:o + name_len].decode("utf-8", errors="replace"); o += name_len
        hardlink_of = var[o:o + hardlink_len].decode("utf-8", errors="replace"); o += hardlink_len
        error       = var[o:o + error_len].decode("utf-8", errors="replace")

        yield {
            "path":        path,
            "name":        name,
            "type":        chr(fields[_TYPE]),
            "size_bytes":  fields[_SIZE_BYTES],
            "size_disk":   fields[_SIZE_DISK],
            "inode":       fields[_INODE],
            "dev":         fields[_DEV],
            "uid":         fields[_UID],
            "mtime":       fields[_MTIME],
            "atime":       fields[_ATIME],
            "ctime":       fields[_CTIME],
            "depth":       fields[_DEPTH],
            "hardlink_of": hardlink_of,
            "error":       error,
        }

        count += 1
        if progress_cb and count % 1000 == 0:
            progress_cb(count)


def _stream_json(
    stream,
    progress_cb: Callable[[int], None] | None,
) -> Iterator[dict]:
    """Parse NDJSON records (fallback / debug mode)."""
    count = 0
    for line in stream:
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


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

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
    binary: bool = True,
    progress_cb: Callable[[int], None] | None = None,
) -> Iterator[dict]:
    """Yield parsed records from the C scanner.

    binary=True (default): uses fast struct-based binary protocol.
    binary=False: falls back to NDJSON (slower, useful for debugging).
    """
    binary_exe = scanner_path or _find_scanner()
    cmd = [binary_exe]
    if one_fs:       cmd.append("-x")
    if follow_links: cmd.append("-L")
    if binary:       cmd.append("-b")
    if max_depth:    cmd += ["-d", str(max_depth)]
    if skip:         cmd += ["--skip", ":".join(skip)]
    cmd.append(os.path.abspath(root))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=1 << 20,   # 1 MB pipe buffer
    )

    try:
        raw = proc.stdout
        if binary:
            yield from _stream_binary(raw, progress_cb)
        else:
            yield from _stream_json(
                io.TextIOWrapper(raw, encoding="utf-8", errors="replace"),
                progress_cb,
            )
    finally:
        proc.stdout.close()
        proc.wait()


def scan(root: str, **kwargs) -> list[dict]:
    """Collect all records into a list (blocking)."""
    return list(stream_records(root, **kwargs))
