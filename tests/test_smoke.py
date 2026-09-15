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
        # 900 KB, not 900 B: on ext4 both files would round up to one 4 KB
        # block and the sort order would be a tie.
        # '[' in a name is legitimate and would be parsed as Rich markup
        (deep / "The.Gentlemen.1080p.x264-[YTS.MX].mp4").write_bytes(b"x" * 900_000)
        (P(root) / "small.txt").write_bytes(b"y" * 10)

        async def main():
            app = StorageMarkApp(root)
            async with app.run_test(size=(60, 20)) as pilot:
                await wait_scan(app, pilot)
                fl = app.query_one("#file-list", FileList)
                fl.focus()
                await pilot.pause()
                line = app.query_one("#file-path", Static)

                def path_part() -> str:
                    return str(line.render()).split(" │ ", 1)[1]

                # sorted by disk size desc → the .mp4 is first
                node = fl.selected()
                assert node is not None
                assert "YTS.MX" in node.path
                # 60-col terminal, path is longer → left-elided, tail kept
                assert path_part().startswith("…"), line.render()
                assert path_part().endswith("[YTS.MX].mp4"), line.render()

                # moving the cursor updates the line
                await pilot.press("j")
                await pilot.pause()
                assert path_part().endswith("small.txt"), line.render()

                # and it follows the cursor back to the top
                await pilot.press("g")
                await pilot.pause()
                assert fl.selected().path.endswith(path_part().lstrip("…"))
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


def test_help_fits_and_scrolls_on_short_terminals():
    """The overlay is ~28 rows. It must stay on screen at 24 rows and the
    keyboard must reach its last line. A Static silently ignores scroll
    calls (allow_vertical_scroll is False), which an earlier version hit."""
    from storagemark.python.ui.app import HelpScreen

    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(100, 24)) as pilot:
            await wait_scan(app, pilot)
            await pilot.press("question_mark")
            await pilot.pause(0.3)
            assert isinstance(app.screen, HelpScreen)
            from textual.containers import VerticalScroll
            box = app.screen.query_one("#help-box", VerticalScroll)
            text = app.screen.query_one("#help-text", Static)
            assert text.region.width > 60, "help text collapsed to an empty box"
            assert box.region.bottom <= 24, "help box runs off screen"
            assert box.max_scroll_y > 0, "expected overflow at 24 rows"
            for _ in range(box.max_scroll_y + 2):
                await pilot.press("j")
            await pilot.pause(0.2)
            assert box.scroll_y == box.max_scroll_y, "j did not reach the end"
            await pilot.press("k")
            await pilot.pause(0.1)
            assert box.scroll_y == box.max_scroll_y - 1
    asyncio.run(main())



def test_files_status_line_shows_current_sort():
    """The sort was invisible: sort_label() existed with no caller."""
    async def main():
        app = StorageMarkApp(REPO)
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_scan(app, pilot)
            fl = app.query_one("#file-list", FileList)
            line = app.query_one("#file-path", Static)
            fl.focus()
            await pilot.pause()

            def sort_part() -> str:
                return str(line.render()).split(" │ ", 1)[0]

            assert sort_part() == "sort DISK↓", line.render()
            await pilot.press("s")
            await pilot.pause()
            assert sort_part() == "sort LOGICAL↓", line.render()
            await pilot.press("S")
            await pilot.pause()
            assert sort_part() == "sort LOGICAL↑", line.render()
            # the label follows the real sort order, not just the key count
            assert fl.sort_label() == "LOGICAL↑"

            # an empty list still shows the sort
            await pilot.press("slash")
            for ch in "no-such-file-anywhere":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()
            assert not fl.rows
            assert str(line.render()) == "sort LOGICAL↑"
    asyncio.run(main())


def test_subdirs_root_row_keeps_the_path_tail(tmp_path):
    """The root's name is its full path; cutting it at 32 characters from
    the right left an unreadable prefix."""
    from storagemark.python.ui.views import SubdirsTree
    deep = tmp_path / "a-fairly-long-folder-name" / "another-long-one" / "project-root"
    deep.mkdir(parents=True)
    (deep / "f.txt").write_bytes(b"x" * 10)

    async def main():
        app = StorageMarkApp(str(deep))
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_scan(app, pilot)
            tree = app.query_one("#subdirs-tree", SubdirsTree)
            tree.load(app.dir_tree)
            label = tree._label(app.dir_tree.root)
            name_field = label[2:34]
            assert name_field.startswith("…"), label
            assert name_field.rstrip().endswith("project-root"), label
            # non-root rows are unchanged
            child = app.dir_tree.root.children[0]
            assert tree._label(child)[2:34].rstrip() == child.name
    asyncio.run(main())


def test_scanning_filesystem_root_keeps_its_children():
    """Children of "/" were emitted as "//usr"; os.path.dirname("//usr") is
    "//", which matched no node, so every top-level entry was dropped and a
    scan of / reported one directory and nothing else (both platforms,
    v1.3.1). One level deep is enough to exercise the join and stays fast."""
    import os
    tree = DirTree.build(scan("/", max_depth=1))
    assert tree.root.path == "/"
    children = tree.root.children
    assert len(children) >= 3, f"root has {len(children)} children"
    for child in children:
        assert not child.path.startswith("//"), child.path
        assert os.path.dirname(child.path) == "/", child.path
        assert child.parent is tree.root
