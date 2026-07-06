"""Move files/directories to the platform trash — the recoverable path.

macOS:  rename into ~/.Trash with a collision-safe name. Recoverable by
        dragging back out of the Trash (no Finder 'Put Back' metadata,
        which would require Finder automation or PyObjC).
Linux:  freedesktop.org Trash spec — ~/.local/share/Trash/files plus a
        .trashinfo record (so 'Restore' works in file managers).

Cross-volume moves raise TrashError: a rename can't cross filesystems and
silently copying gigabytes to another volume's trash is worse than telling
the user. Callers surface the error and can fall back to permanent delete.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


class TrashError(Exception):
    pass


def _unique_dest(trash_dir: Path, name: str) -> Path:
    dest = trash_dir / name
    if not dest.exists():
        return dest
    stem, ext = os.path.splitext(name)
    ts = datetime.now().strftime("%H.%M.%S")
    dest = trash_dir / f"{stem} {ts}{ext}"
    counter = 1
    while dest.exists():
        dest = trash_dir / f"{stem} {ts}-{counter}{ext}"
        counter += 1
    return dest


def _trash_dir_macos() -> Path:
    d = Path.home() / ".Trash"
    if not d.is_dir():
        raise TrashError(f"Trash directory not found: {d}")
    return d


def _trash_linux(path: Path) -> None:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    files_dir = base / "Trash" / "files"
    info_dir = base / "Trash" / "info"
    files_dir.mkdir(parents=True, exist_ok=True)
    info_dir.mkdir(parents=True, exist_ok=True)

    dest = _unique_dest(files_dir, path.name)
    info = info_dir / (dest.name + ".trashinfo")
    info.write_text(
        "[Trash Info]\n"
        f"Path={quote(str(path))}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
    )
    try:
        os.rename(path, dest)
    except OSError:
        info.unlink(missing_ok=True)
        raise


def send_to_trash(path: str) -> None:
    """Move one path (file or directory) to the trash. Raises TrashError."""
    p = Path(path)
    if not p.exists() and not p.is_symlink():
        raise TrashError(f"No such file: {path}")
    try:
        if sys.platform == "darwin":
            os.rename(p, _unique_dest(_trash_dir_macos(), p.name))
        else:
            _trash_linux(p)
    except OSError as e:
        if getattr(e, "errno", None) == 18:   # EXDEV — cross-device
            raise TrashError(
                f"{path} is on a different volume than the Trash; "
                "use permanent delete or remove it in Finder."
            ) from e
        raise TrashError(f"{path}: {e}") from e


def delete_permanently(path: str) -> None:
    """Irreversibly remove one path. Raises TrashError on failure."""
    p = Path(path)
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=False)
    except OSError as e:
        raise TrashError(f"{path}: {e}") from e
