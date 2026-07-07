"""End-to-end removal: mark → D → confirm (trash / typed-yes) → prune.

Trash is redirected to a temp dir so ~/.Trash is never touched.
"""
import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from storagemark.python import trash as trash_mod
from storagemark.python.ui.app import StorageMarkApp
from storagemark.python.ui.filelist import FileList
from storagemark.python.ui.remove import top_level_roots


def build_tree() -> str:
    root = tempfile.mkdtemp(prefix="sm_rm_test_")
    (Path(root) / "keep").mkdir()
    (Path(root) / "junk").mkdir()
    (Path(root) / "junk" / "sub").mkdir()
    (Path(root) / "keep" / "a.txt").write_bytes(b"x" * 10_000)
    (Path(root) / "junk" / "big.bin").write_bytes(b"y" * 500_000)
    (Path(root) / "junk" / "sub" / "c.log").write_bytes(b"z" * 200_000)
    (Path(root) / "top.dat").write_bytes(b"w" * 300_000)
    return root


async def run_scenario(mode: str) -> None:
    root = build_tree()
    try:
        app = StorageMarkApp(root)
        async with app.run_test(size=(120, 40)) as pilot:
            t0 = time.time()
            while app.dir_tree is None and time.time() - t0 < 15:
                await pilot.pause(0.1)
            assert app.dir_tree
            t = app.dir_tree
            before_files, before_disk = t.file_count, t.total_disk

            # nested marks must dedupe to 2 top-level roots
            app.marked.add(os.path.join(root, "junk"))
            app.marked.add(os.path.join(root, "junk", "big.bin"))
            app.marked.add(os.path.join(root, "top.dat"))
            nodes = [app.node_map[p] for p in sorted(app.marked)]
            assert len(top_level_roots(nodes, set(app.marked))) == 2

            await pilot.press("D")
            await pilot.pause()
            if mode == "trash":
                await pilot.press("t")
            else:
                inp = app.screen.query_one("#confirm-input")
                inp.focus()
                await pilot.pause()
                for ch in "yes":
                    await pilot.press(ch)
                await pilot.press("enter")

            for _ in range(100):
                await pilot.pause(0.1)
                if not os.path.exists(os.path.join(root, "junk")):
                    break
            await pilot.pause(0.5)

            assert not os.path.exists(os.path.join(root, "junk"))
            assert not os.path.exists(os.path.join(root, "top.dat"))
            assert os.path.exists(os.path.join(root, "keep", "a.txt"))
            assert t.file_count == before_files - 3
            assert t.total_disk < before_disk
            assert len(app.marked) == 0
            fl = app.query_one("#file-list", FileList)
            assert all("junk" not in n.path and n.name != "top.dat"
                       for n in fl.rows)
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def fake_trash(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="sm_fake_trash_"))
    monkeypatch.setattr(trash_mod, "_trash_dir_macos", lambda: d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_remove_via_trash(fake_trash):
    asyncio.run(run_scenario("trash"))
    names = sorted(p.name for p in fake_trash.iterdir())
    assert "junk" in names and "top.dat" in names


def test_remove_permanent():
    asyncio.run(run_scenario("delete"))
