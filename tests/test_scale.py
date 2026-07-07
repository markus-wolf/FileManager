"""Full-scale tests against the real home directory (~1M objects).

Slow (minutes): excluded by default. Run with:  uv run pytest -m slow
"""
import asyncio
import subprocess
import time
from pathlib import Path

import pytest

from storagemark.python.ui.app import ScanInterruptScreen, StorageMarkApp
from storagemark.python.ui.filelist import FileList
from textual.widgets import Static

pytestmark = pytest.mark.slow

HOME = str(Path.home())


def scanner_procs() -> int:
    r = subprocess.run(["pgrep", "-x", "storagescanner"], capture_output=True)
    return len(r.stdout.split()) if r.stdout else 0


async def drain(app, pilot) -> float:
    """Seconds until the message queue reaches a sentinel (UI idle)."""
    t0 = time.perf_counter()
    done = asyncio.Event()
    app.call_later(done.set)
    while not done.is_set():
        await pilot.pause(0.05)
    return time.perf_counter() - t0


def test_interrupt_dialog_flow():
    """Ctrl-C mid-scan: continue / PARTIAL / quit, no orphan scanner."""
    async def main():
        assert scanner_procs() == 0, "stray storagescanner before test"
        app = StorageMarkApp(HOME)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(1.5)
            assert app.scanning and app.dir_tree is None

            # dialog opens; any key dismisses; scan continues
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert isinstance(app.screen, ScanInterruptScreen)
            await pilot.press("z")
            await pilot.pause()
            assert app.scanning
            n = app.scan_count
            await pilot.pause(1.0)
            assert app.scan_count > n

            # second ctrl+c → PARTIAL results
            await pilot.press("ctrl+c")
            await pilot.pause()
            await pilot.press("ctrl+c")
            for _ in range(200):
                await pilot.pause(0.1)
                if app.dir_tree is not None:
                    break
            assert app.dir_tree is not None and app.partial
            assert app.dir_tree.file_count > 0
            assert "PARTIAL" in str(app.query_one("#hdr", Static).render())
            await pilot.pause(1.0)
            assert scanner_procs() == 0
    asyncio.run(main())


def test_quit_mid_scan_leaves_no_orphan():
    async def main():
        app = StorageMarkApp(HOME)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(1.5)
            assert app.scanning
            await pilot.press("ctrl+c")
            await pilot.pause()
            await pilot.press("ctrl+q")
            await pilot.pause(0.5)
        await asyncio.sleep(1.0)
        assert scanner_procs() == 0
    asyncio.run(main())


def test_delete_one_file_stays_responsive():
    """The 30s-freeze regression: delete 1 file at ~1M scale, from the
    SubDirs tab, and require the UI fully idle again in under 5s."""
    victim = Path.home() / "sm_perf_victim.txt"
    victim.write_bytes(b"x" * 1024)

    async def main():
        app = StorageMarkApp(HOME)
        async with app.run_test(size=(120, 40)) as pilot:
            t0 = time.time()
            while app.dir_tree is None and time.time() - t0 < 300:
                await pilot.pause(0.5)
            assert app.dir_tree
            n_before = app.dir_tree.file_count

            await pilot.press("1")                    # SubDirs (lazy load)
            assert await drain(app, pilot) < 3

            app.marked.add(str(victim))
            await pilot.press("D")
            await pilot.pause(0.3)
            inp = app.screen.query_one("#confirm-input")
            inp.focus()
            await pilot.pause()
            for ch in "yes":
                await pilot.press(ch)

            t0 = time.perf_counter()
            await pilot.press("enter")
            while app.dir_tree.file_count != n_before - 1:
                await pilot.pause(0.05)
            wall = time.perf_counter() - t0 + await drain(app, pilot)
            assert not victim.exists()
            assert wall < 5, f"delete refresh too slow: {wall:.1f}s"

            await pilot.press("2")                    # files pays lazy load
            assert await drain(app, pilot) < 3

    try:
        asyncio.run(main())
    finally:
        victim.unlink(missing_ok=True)
