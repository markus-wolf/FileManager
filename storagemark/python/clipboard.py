"""System clipboard access, independent of the UI framework.

Textual's App.copy_to_clipboard emits an OSC 52 escape sequence. That
travels over SSH and works in iTerm2, Ghostty, kitty and WezTerm, but
macOS Terminal.app ignores it (Textual documents this). So we also hand
the text to a native helper when one exists:

    macOS   pbcopy
    Wayland wl-copy
    X11     xclip -selection clipboard, xsel --clipboard --input

Callers should attempt both paths: OSC 52 covers remote sessions, the
helper covers terminals without OSC 52 support.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

# (executable, argv) — first available wins.
_CANDIDATES: list[tuple[str, list[str]]] = (
    [("pbcopy", ["pbcopy"])] if sys.platform == "darwin" else
    [("wl-copy", ["wl-copy"]),
     ("xclip", ["xclip", "-selection", "clipboard"]),
     ("xsel", ["xsel", "--clipboard", "--input"])]
)


def native_copy(text: str) -> str | None:
    """Copy via a local helper process.

    Returns the helper's name on success, None when no helper is
    installed or it failed (a remote session, for instance, where only
    OSC 52 can work).
    """
    for name, argv in _CANDIDATES:
        if shutil.which(name) is None:
            continue
        try:
            subprocess.run(argv, input=text.encode("utf-8"),
                           check=True, timeout=5,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError):
            return None
        return name
    return None
