"""Clipboard: native helper, mouse-selection extraction, yank keys."""
import asyncio
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from storagemark.python.clipboard import native_copy
from storagemark.python.ui.app import StorageMarkApp
from storagemark.python.ui.filelist import FileList
from textual.geometry import Offset
from textual.selection import Selection

REPO = str(Path(__file__).resolve().parent.parent)


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("pbcopy") is None,
                    reason="needs macOS pbcopy/pbpaste")
def test_native_copy_roundtrip():
    """native_copy actually reaches the system clipboard (pbcopy→pbpaste)."""
    marker = f"storagemark-test-{time.time_ns()}"
    assert native_copy(marker) == "pbcopy"
    back = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    assert back == marker


def test_row_text_matches_selection_extract():
    """get_selection returns exactly the displayed row text, and only
    materializes the selected rows (never the whole list)."""
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(120, 30)) as pilot:
            t0 = time.time()
            while app.dir_tree is None and time.time() - t0 < 15:
                await pilot.pause(0.1)
            fl = app.query_one("#file-list", FileList)
            assert len(fl.rows) > 5

            # whole-row selection of rows 1..3
            sel = Selection(Offset(0, 1), Offset(999, 3))
            text, ending = fl.get_selection(sel)
            lines = text.splitlines()
            assert ending == "\n"
            assert len(lines) == 3
            for i, line in enumerate(lines, start=1):
                assert fl.rows[i].name in line
                assert line == fl._row_text(fl.rows[i])[:len(line)]

            # a select-all (None, None) is bounded, not the whole tree
            from storagemark.python.ui.filelist import MAX_SELECT_ROWS
            text_all, _ = fl.get_selection(Selection(None, None))
            assert len(text_all.splitlines()) <= MAX_SELECT_ROWS
    asyncio.run(main())


def test_yank_keys_copy_paths(monkeypatch):
    """y copies the cursor row's path, Y copies all marked paths."""
    copied: list[str] = []
    monkeypatch.setattr("storagemark.python.ui.app.native_copy",
                        lambda text: copied.append(text) or "fake")

    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(120, 30)) as pilot:
            t0 = time.time()
            while app.dir_tree is None and time.time() - t0 < 15:
                await pilot.pause(0.1)
            fl = app.query_one("#file-list", FileList)
            fl.focus()
            await pilot.pause()

            # y → path of the row under the cursor
            await pilot.press("y")
            await pilot.pause()
            assert copied and copied[-1] == fl.selected().path

            # Y with nothing marked copies nothing new
            before = len(copied)
            await pilot.press("Y")
            await pilot.pause()
            assert len(copied) == before

            # Y → every marked path, newline separated
            want = [n.path for n in fl.rows[:3]]
            for p in want:
                app.marked.add(p)
            await pilot.press("Y")
            await pilot.pause()
            assert sorted(copied[-1].splitlines()) == sorted(want)
    asyncio.run(main())
