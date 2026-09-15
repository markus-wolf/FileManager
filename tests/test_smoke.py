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
from textual.widgets import Static, TabbedContent

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


def test_vi_keys_all_views():
    """hjkl work everywhere — arrow keys can be flaky on some setups."""
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_scan(app, pilot)

            # SubDirs tree: j/k move, l expands, h collapses
            from storagemark.python.ui.views import SubdirsTree, TimeTable
            await pilot.press("1")
            tree = app.query_one("#subdirs-tree", SubdirsTree)
            tree.focus()
            await pilot.pause()
            line0 = tree.cursor_line
            await pilot.press("j", "j")
            assert tree.cursor_line == line0 + 2, "j did not move tree cursor"
            await pilot.press("k")
            assert tree.cursor_line == line0 + 1
            # find a collapsible dir under the cursor path
            for _ in range(30):
                node = tree.cursor_node
                if node and node.allow_expand:
                    break
                await pilot.press("j")
            node = tree.cursor_node
            if node and node.allow_expand:
                was = node.is_expanded
                await pilot.press("l")
                assert node.is_expanded or was, "l did not expand"
                await pilot.press("h")
                assert not node.is_expanded, "h did not collapse"

            # Types table: j/k move the row cursor
            await pilot.press("3")
            tt = app.query_one("#types-table")
            tt.focus()
            await pilot.pause()
            r0 = tt.cursor_row
            await pilot.press("j")
            assert tt.cursor_row == r0 + 1, "j did not move types cursor"
            await pilot.press("k")
            assert tt.cursor_row == r0

            # Time table: j/k
            await pilot.press("4")
            tmt = app.query_one("#time-table", TimeTable)
            tmt.focus()
            await pilot.pause()
            r0 = tmt.cursor_row
            await pilot.press("j")
            assert tmt.cursor_row == r0 + 1, "j did not move time cursor"

            # Tab strip: h/l switch tabs when the strip has focus
            from textual.widgets import TabbedContent, Tabs
            tabs = app.query_one(Tabs)
            tabs.focus()
            await pilot.pause()
            tc = app.query_one(TabbedContent)
            before = tc.active
            await pilot.press("l")
            await pilot.pause()
            assert tc.active != before, "l did not switch tab"
            await pilot.press("h")
            await pilot.pause()
            assert tc.active == before, "h did not switch back"
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


def test_header_never_scrolls_away():
    """Focusing a tall listing must not scroll the Screen: auto-height
    widgets once grew the layout past the terminal, and focus scrolled
    the 2-line header out of view until a short tab (Time) was shown."""
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(100, 24)) as pilot:
            await wait_scan(app, pilot)
            assert app.screen.virtual_size.height <= 24, "layout overflows terminal"
            for key, wid in [("1", "#subdirs-tree"), ("2", "#file-list"),
                             ("3", "#types-table"), ("5", "#whatif-panel")]:
                await pilot.press(key)
                app.query_one(wid).focus()
                await pilot.pause(0.2)
                assert app.screen.scroll_offset.y == 0, f"{wid} scrolled screen"
    asyncio.run(main())


def test_files_view_shows_full_path():
    """The Files tab status line shows the cursor row's full path."""
    import shutil
    import tempfile
    from pathlib import Path as P

    root = tempfile.mkdtemp(prefix="sm_path_test_")
    try:
        deep = P(root) / "a" / "very" / "deeply" / "nested" / "set" / "of" / "dirs"
        deep.mkdir(parents=True)
        # '[' in a name is legitimate and would be parsed as Rich markup
        (deep / "The.Gentlemen.1080p.x264-[YTS.MX].mp4").write_bytes(b"x" * 900)
        (P(root) / "small.txt").write_bytes(b"y" * 10)

        async def main():
            app = StorageMarkApp(root)
            async with app.run_test(size=(60, 20)) as pilot:
                await wait_scan(app, pilot)
                fl = app.query_one("#file-list", FileList)
                fl.focus()
                await pilot.pause()
                line = app.query_one("#file-path", Static)

                # sorted by disk size desc → the .mp4 is first
                shown = str(line.render())
                node = fl.selected()
                assert node is not None
                assert "YTS.MX" in node.path
                # 60-col terminal, path is longer → left-elided, tail kept
                assert shown.startswith("…"), shown
                assert shown.endswith("[YTS.MX].mp4"), shown

                # moving the cursor updates the line
                await pilot.press("j")
                await pilot.pause()
                assert str(line.render()).endswith("small.txt"), line.render()

                # and it follows the cursor back to the top
                await pilot.press("g")
                await pilot.pause()
                shown = str(line.render())
                assert fl.selected().path.endswith(shown.lstrip("…")), shown
        asyncio.run(main())
    finally:
        shutil.rmtree(root, ignore_errors=True)


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


def test_no_attribute_shadows_textual_api():
    """Instance attributes must not shadow Textual methods/properties.

    Hit three times: `.tree` on the App, and `.query` on FileList — a
    double-click called Widget.text_select_all(), which calls
    widget.query("*"), which was our filter string ('str' object is not
    callable). Compare every instance attribute against the framework
    base classes and fail on any collision.
    """
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(100, 24)) as pilot:
            await wait_scan(app, pilot)
            offenders = []
            for obj in [app, app.screen, *app.screen.query("*")]:
                # only our own classes; skip framework-provided widgets
                mro = type(obj).__mro__
                ours = [c for c in mro
                        if c.__module__.startswith("storagemark")]
                if not ours:
                    continue
                base = mro[len(ours)]          # first framework class
                for name in vars(obj):
                    if name.startswith("_"):
                        continue
                    inherited = getattr(base, name, None)
                    if inherited is None:
                        continue
                    if callable(inherited) or isinstance(inherited, property):
                        offenders.append(
                            f"{type(obj).__name__}.{name} shadows "
                            f"{base.__name__}.{name}")
            assert not offenders, "; ".join(offenders)
    asyncio.run(main())


def test_double_and_triple_click_do_not_crash():
    """Double-click selects the whole widget, triple-click its container.
    Both route through Widget.query(), so a shadowed name crashes here."""
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(100, 24)) as pilot:
            await wait_scan(app, pilot)
            fl = app.query_one("#file-list", FileList)
            fl.focus()
            await pilot.pause()

            await pilot.double_click(fl, offset=(10, 3))
            await pilot.pause()
            assert app.screen.selections, "double-click selected nothing"
            text = app.screen.get_selected_text() or ""
            assert text, "double-click produced no text"

            app.screen.clear_selection()
            await pilot.triple_click(fl, offset=(10, 3))
            await pilot.pause()
            assert app.screen.selections, "triple-click selected nothing"
    asyncio.run(main())
