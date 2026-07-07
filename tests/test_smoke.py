"""Fast UI smoke tests: scan, views, marking, filtering, theme, repr guard.

All tests scan the repo itself (small tree) — seconds, not minutes.
"""
import asyncio
import time
from pathlib import Path

from storagemark.python.model import DirTree
from storagemark.python.scanner import scan
from storagemark.python.ui.app import ConfirmScreen, StorageMarkApp
from storagemark.python.ui.filelist import FileList
from storagemark.python.ui.views import TypesTable
from textual.widgets import TabbedContent

REPO = str(Path(__file__).resolve().parent.parent)


async def wait_scan(app, pilot, timeout=15.0):
    t0 = time.time()
    while app.dir_tree is None and time.time() - t0 < timeout:
        await pilot.pause(0.1)
    assert app.dir_tree is not None, "scan never finished"


def test_repr_is_bounded():
    """parent/children must stay repr=False — repr of one node used to
    stringify the entire tree (~14s at 1M nodes) inside Textual's @work."""
    tree = DirTree.build(scan(REPO))
    node = next(n for n in tree.flat if n.type == "f" and n.depth > 2)
    t0 = time.perf_counter()
    text = repr(node)
    assert len(text) < 2000
    assert time.perf_counter() - t0 < 0.05


def test_ui_smoke():
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_scan(app, pilot)
            fl = app.query_one("#file-list", FileList)
            assert len(fl.rows) > 0

            # tab switching 1..5
            for key, pane in [("1", "subdirs"), ("3", "types"),
                              ("4", "time"), ("5", "whatif"), ("2", "files")]:
                await pilot.press(key)
                assert app.query_one(TabbedContent).active == pane

            # cursor + mark + sort
            fl.focus()
            await pilot.pause()
            await pilot.press("j", "space")
            assert len(app.marked) == 1
            before = fl.sort_idx
            await pilot.press("s")
            assert fl.sort_idx == (before + 1) % 6

            # filter + clear
            await pilot.press("slash")
            for ch in ".py":
                await pilot.press(ch)
            await pilot.press("enter")
            assert all(".py" in n.name.lower() for n in fl.rows[:20])
            await pilot.press("slash", "escape")

            # help modal open/close; clear marks
            await pilot.press("question_mark", "escape")
            await pilot.press("x")
            assert len(app.marked) == 0
    asyncio.run(main())


def test_marking_workflow():
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_scan(app, pilot)
            fl = app.query_one("#file-list", FileList)
            tt = app.query_one("#types-table", TypesTable)
            py = len(app.dir_tree.ext_map.get(".py", []))
            assert py > 0

            # group toggle on .py row: mark, indicator, unmark
            await pilot.press("3")
            await pilot.pause()
            tt.focus()
            for i in range(tt.row_count):
                rk, _ = tt.coordinate_to_cell_key((i, 0))
                if str(rk.value) == ".py":
                    tt.move_cursor(row=i)
                    break
            await pilot.press("space")
            await pilot.pause()
            assert len(app.marked) == py
            assert "all" in str(tt.get_cell(".py", tt._marked_col))
            await pilot.press("space")
            await pilot.pause()
            assert len(app.marked) == 0

            # A with filter, then U
            await pilot.press("2")
            fl.focus()
            await pilot.press("slash")
            for ch in ".py":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.press("A")
            await pilot.pause()
            assert len(app.marked) > 0
            await pilot.press("U")
            await pilot.pause()
            assert len(app.marked) == 0
            await pilot.press("slash", "escape")
            await pilot.pause()

            # unfiltered A → guard modal; y confirms
            await pilot.press("A")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await pilot.pause()
            assert len(app.marked) == len(fl.rows) > 0

            # M: marked-only view; unmark removes row live
            app.marked.clear()
            for n in app.dir_tree.ext_map[".py"][:5]:
                app.marked.add(n.path)
            await pilot.press("M")
            await pilot.pause()
            assert fl.show_marked_only and len(fl.rows) == 5
            await pilot.press("space")
            await pilot.pause()
            assert len(fl.rows) == 4
            await pilot.press("M")
            await pilot.pause()
            assert not fl.show_marked_only
    asyncio.run(main())


def test_norton_theme_renders():
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(100, 32)) as pilot:
            await wait_scan(app, pilot)
            assert app.theme == "norton-commander"
            fl = app.query_one("#file-list", FileList)
            fl.focus()
            await pilot.pause()
            await pilot.press("space")     # a mark, so yellow renders
            await pilot.pause(0.3)
            svg = app.export_screenshot().lower()
            for color in ("#0000aa", "#00aaaa", "#ffff55"):
                assert color in svg, f"{color} missing from render"
            assert "#ff5555" not in svg and "#aa0000" not in svg
    asyncio.run(main())
