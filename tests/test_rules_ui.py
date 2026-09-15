"""Rule picker (f) and rule-from-path builder (F) through the UI.

STORAGEMARK_CONFIG_DIR is redirected per test, so the developer's own
~/.config/storagemark/rules.toml is never read or written.
"""
import asyncio
import time
from pathlib import Path

import pytest

from storagemark.python.rules import load_rules, rules_file_path
from storagemark.python.ui.app import StorageMarkApp
from storagemark.python.ui.filelist import FileList
from storagemark.python.ui.rules_screen import (RuleFromPathScreen,
                                                RulesScreen, candidates_for)
from textual.widgets import DataTable, TabbedContent


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGEMARK_CONFIG_DIR", str(tmp_path / "cfg"))
    assert rules_file_path().parent == tmp_path / "cfg"
    return tmp_path / "cfg" / "rules.toml"


@pytest.fixture()
def tree_root(tmp_path):
    """A .trash folder of superseded installers, plus files to keep."""
    trash = tmp_path / "Application Support" / "Cursor" / "CachedExtensionVSIXs" / ".trash"
    trash.mkdir(parents=True)
    for v in ("26.908.40401", "26.908.31748"):
        (trash / f"openai.chatgpt-{v}-darwin-arm64").write_bytes(b"x" * 600_000)
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "taxes.pdf").write_bytes(b"y" * 200_000)
    (docs / "movie.mp4").write_bytes(b"z" * 800_000)
    return tmp_path


async def wait_scan(app, pilot, timeout=15.0):
    t0 = time.time()
    while app.dir_tree is None and time.time() - t0 < timeout:
        await pilot.pause(0.1)
    assert app.dir_tree is not None


async def wait_measured(screen, pilot, timeout=15.0):
    t0 = time.time()
    while not screen.results and time.time() - t0 < timeout:
        await pilot.pause(0.1)
    assert screen.results, "counting never finished"


def test_rules_file_is_created_with_the_template(isolated_config, tree_root):
    async def main():
        app = StorageMarkApp(str(tree_root))
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_scan(app, pilot)
            assert isolated_config.exists()
            assert "StorageMark rules" in isolated_config.read_text()
            assert app.rules and app.rule_problems == []
    asyncio.run(main())


def test_picker_measures_and_applies(isolated_config, tree_root):
    """f lists rules with measured reach; choosing one filters the Files
    view to its matches — including directories."""
    async def main():
        app = StorageMarkApp(str(tree_root))
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_scan(app, pilot)
            await pilot.press("f")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RulesScreen)
            await wait_measured(screen, pilot)

            # the built-in dir rule found the .trash folder
            trash = next(r for r in screen.results
                         if r.rule.name == "App discard folders")
            assert trash.count == 1
            assert trash.total_size > 1_000_000

            # pick it: the Files view now shows that one directory
            table = screen.query_one("#rules-table", DataTable)
            for i in range(table.row_count):
                key, _ = table.coordinate_to_cell_key((i, 0))
                if str(key.value) == "App discard folders":
                    table.move_cursor(row=i)
                    break
            await pilot.press("enter")
            await pilot.pause(0.3)

            fl = app.query_one("#file-list", FileList)
            assert app.query_one(TabbedContent).active == "files"
            assert fl.rule_label == "App discard folders"
            assert [n.name for n in fl.rows] == [".trash"]
            assert fl.rows[0].type == "d"          # a directory, not its files

            # marking it is one item; Esc restores the full list
            await pilot.press("space")
            assert len(app.marked) == 1
            await pilot.press("escape")
            await pilot.pause()
            assert fl.rule_nodes is None
            assert len(fl.rows) > 1
    asyncio.run(main())


def test_picker_sorts_by_size_and_survives_dismissal(isolated_config, tree_root):
    async def main():
        app = StorageMarkApp(str(tree_root))
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_scan(app, pilot)
            await pilot.press("f")
            await pilot.pause()
            screen = app.screen
            await wait_measured(screen, pilot)
            sizes = [r.total_size for r in screen.results]
            assert sizes == sorted(sizes, reverse=True)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, RulesScreen)

            # opening and closing straight away must not raise
            await pilot.press("f")
            await pilot.press("escape")
            await pilot.pause(0.5)
    asyncio.run(main())


def test_candidates_are_narrowest_first(tree_root):
    """The safe choice sits at the top: the item itself, then each
    enclosing folder outward."""
    from storagemark.python.model import DirTree
    from storagemark.python.scanner import scan
    tree = DirTree.build(scan(str(tree_root)))
    node = next(n for n in tree.flat if "openai.chatgpt" in n.name)
    cands = candidates_for(node, home=str(tree_root))
    # no pseudo-extension offer for a version-numbered name
    assert all(c.type == "dir" for c in cands), [c.describe() for c in cands]
    names = [c.name_glob[0] for c in cands]
    # the item's own folder first, then outward
    assert names[0] == ".trash"
    assert names.index(".trash") < names.index("cachedextensionvsixs") \
        < names.index("cursor")


def test_rule_from_path_saves_and_reloads(isolated_config, tree_root):
    """F offers measured patterns; choosing one appends it to the file and
    it is immediately available to f."""
    async def main():
        app = StorageMarkApp(str(tree_root))
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_scan(app, pilot)
            fl = app.query_one("#file-list", FileList)
            fl.focus()
            await pilot.pause()
            # put the cursor on one of the superseded installers
            for i, n in enumerate(fl.rows):
                if "openai.chatgpt" in n.name:
                    fl.cursor = i
                    break
            assert "openai.chatgpt" in fl.selected().name

            await pilot.press("F")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RuleFromPathScreen)
            await wait_measured(screen, pilot)

            # the first offer is the folder the installer sits in, and it
            # is measured as one folder holding both installers
            first = screen.results[0]
            assert first.rule.type == "dir"
            assert first.rule.name_glob == (".trash",)
            assert first.count == 1
            assert first.total_size > 1_000_000

            table = screen.query_one("#fromparent-table", DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause(0.5)

            text = isolated_config.read_text()
            assert 'name_glob = [".trash"]' in text
            assert 'type = "dir"' in text
            assert "StorageMark rules" in text        # template preserved
            rules, problems = load_rules()
            assert problems == []
            saved = [r for r in rules if r.name == "Folders named .trash"]
            assert saved and saved[0].type == "dir"
            assert any(r.name == "Folders named .trash" for r in app.rules)
    asyncio.run(main())


def test_broken_user_file_warns_but_app_works(isolated_config, tree_root):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("[[rule]]\nname = 'Bad'\nmin_size = 'lots'\n")

    async def main():
        app = StorageMarkApp(str(tree_root))
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_scan(app, pilot)
            assert app.rule_problems, "a bad rule should be reported"
            assert app.rules, "built-ins must still load"
            await pilot.press("f")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RulesScreen)
            await wait_measured(screen, pilot)
    asyncio.run(main())


def _ancestors(node):
    while node is not None:
        yield node
        node = node.parent


def test_every_candidate_matches_its_origin(tree_root):
    """Invariant: a candidate built from a path must match the item it was
    built from, or one of its folders. Guards two broken versions: a size
    floor that excluded small files, and literal names read as globs."""
    from storagemark.python.model import DirTree
    from storagemark.python.scanner import scan

    # names containing glob metacharacters, the case that broke
    odd = tree_root / "Movies [YTS.MX]" / "take*two?"
    odd.mkdir(parents=True)
    (odd / "clip-[YTS.MX].mp4").write_bytes(b"x" * 300)       # tiny file
    tree = DirTree.build(scan(str(tree_root)))

    for node in tree.flat:
        if node.depth == 0:
            continue
        chain = list(_ancestors(node))
        for cand in candidates_for(node, home=str(tree_root)):
            assert any(cand.matches(a) for a in chain), (
                f"{cand.describe()!r} matches nothing on {node.path}")


def test_bracket_names_match_literally(tree_root):
    from storagemark.python.model import DirTree
    from storagemark.python.scanner import scan
    folder = tree_root / "Movies [YTS.MX]"
    folder.mkdir()
    (folder / "a.mp4").write_bytes(b"x" * 10)
    decoy = tree_root / "Movies y"            # what [yts.mx] as a glob hits
    decoy.mkdir()
    tree = DirTree.build(scan(str(tree_root)))
    node = next(n for n in tree.flat if n.name == "a.mp4")
    rule = next(c for c in candidates_for(node, home=str(tree_root))
                if c.type == "dir")
    hits = [n.name for n in tree.flat if rule.matches(n)]
    assert hits == ["Movies [YTS.MX]"], hits


def test_rules_screens_render_bracket_paths(isolated_config, tmp_path):
    """Paths reach Rich markup in the F header; a '[b]' in a folder name
    must show as text, not turn the line bold and vanish."""
    root = tmp_path / "scan"
    folder = root / "[b]not bold[/b]"
    folder.mkdir(parents=True)
    (folder / "x.bin").write_bytes(b"x" * 10)

    async def main():
        app = StorageMarkApp(str(root))
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_scan(app, pilot)
            fl = app.query_one("#file-list", FileList)
            fl.focus()
            await pilot.pause()
            await pilot.press("F")
            await pilot.pause(0.5)
            from textual.widgets import Static
            head = str(app.screen.query_one("#fromparent-head", Static).render())
            assert "[b]not bold[/b]" in head, head
    asyncio.run(main())
