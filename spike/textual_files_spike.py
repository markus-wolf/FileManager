"""Textual migration spike — Files view over DirTree.flat via the Line API.

Purpose: answer ONE question before committing to a Textual rewrite —
does a virtualized (render_line) list over ~1M FileNodes stay fast and
memory-flat in Textual, including sort and filter?

Interactive:
    uv run python spike/textual_files_spike.py ~          # scan + browse
    keys: j/k/arrows scroll, PgUp/PgDn, g/G, s sort, S reverse,
          / filter (type, Enter apply, Esc clear), q quit

Headless benchmark (no TTY needed):
    uv run python spike/textual_files_spike.py ~ --measure

This file is deliberately throwaway-quality: no styling, one view,
no integration with the real app.
"""
from __future__ import annotations

import resource
import sys
import time

from rich.segment import Segment
from rich.style import Style
from textual.app import App, ComposeResult
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Footer, Static

# Reuse the real core — this is the whole point of the spike.
sys.path.insert(0, ".")
from storagemark.python.model import DirTree, FileNode, fmt_size  # noqa: E402
from storagemark.python.scanner import scan  # noqa: E402

SORT_KEYS = ["size_disk", "size_bytes", "mtime", "name"]


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20)


class FileList(ScrollView):
    """Virtualized file list: only visible lines are ever rendered."""

    def __init__(self, files: list[FileNode]) -> None:
        super().__init__()
        self.all_files = files          # full backing list (~900k)
        self.rows = files               # current sorted/filtered view
        self.cursor = 0
        self.sort_idx = 0
        self.sort_rev = True
        self.query = ""
        self.resort()

    # ---- data ops (plain Python on the backing list) ----

    def resort(self) -> None:
        key = SORT_KEYS[self.sort_idx]
        if key == "name":
            fn = lambda n: n.name.lower()
        else:
            fn = lambda n: getattr(n, key)
        src = self.all_files
        if self.query:
            q = self.query.lower()
            src = [n for n in src if q in n.name.lower()]
        self.rows = sorted(src, key=fn, reverse=self.sort_rev)
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))
        self.virtual_size = Size(self.size.width, len(self.rows))
        self.refresh()

    # ---- Line API ----

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        idx = y + scroll_y
        if idx >= len(self.rows):
            return Strip.blank(self.size.width)
        n = self.rows[idx]
        text = (f" {fmt_size(n.size_disk)}  {fmt_size(n.size_bytes)}  "
                f"{n.mtime:%Y-%m-%d %H:%M}  {n.name}")
        style = Style(reverse=(idx == self.cursor))
        seg = Segment(text[: self.size.width].ljust(self.size.width), style)
        return Strip([seg], self.size.width)

    # ---- cursor / viewport ----

    def move(self, delta: int) -> None:
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))
        self.scroll_to_region_visible()

    def scroll_to_region_visible(self) -> None:
        top = self.scroll_offset.y
        height = self.size.height
        if self.cursor < top:
            self.scroll_to(y=self.cursor, animate=False)
        elif self.cursor >= top + height:
            self.scroll_to(y=self.cursor - height + 1, animate=False)
        self.refresh()


class SpikeApp(App):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "sort", "Sort"),
        ("S", "reverse", "Reverse"),
    ]

    def __init__(self, files: list[FileNode], meta: str) -> None:
        super().__init__()
        self.files = files
        self.meta = meta

    def compose(self) -> ComposeResult:
        yield Static(self.meta, id="hdr")
        yield FileList(self.files)
        yield Footer()

    # key handling kept minimal for the spike
    def on_key(self, event) -> None:
        fl = self.query_one(FileList)
        k = event.key
        if k in ("j", "down"):
            fl.move(1)
        elif k in ("k", "up"):
            fl.move(-1)
        elif k == "pagedown":
            fl.move(fl.size.height)
        elif k == "pageup":
            fl.move(-fl.size.height)
        elif k == "g":
            fl.cursor = 0
            fl.scroll_to(y=0, animate=False)
        elif k == "G":
            fl.cursor = len(fl.rows) - 1
            fl.scroll_to(y=len(fl.rows), animate=False)

    def action_sort(self) -> None:
        fl = self.query_one(FileList)
        fl.sort_idx = (fl.sort_idx + 1) % len(SORT_KEYS)
        t0 = time.perf_counter()
        fl.resort()
        self.query_one("#hdr", Static).update(
            f"{self.meta}  sort={SORT_KEYS[fl.sort_idx]} "
            f"({time.perf_counter() - t0:.2f}s)")

    def action_reverse(self) -> None:
        fl = self.query_one(FileList)
        fl.sort_rev = not fl.sort_rev
        fl.resort()


# --------------------------------------------------------------------- #
# Headless benchmark
# --------------------------------------------------------------------- #

async def measure(files: list[FileNode], meta: str) -> None:
    app = SpikeApp(files, meta)
    results: list[str] = []
    async with app.run_test(size=(120, 40)) as pilot:
        fl = app.query_one(FileList)
        results.append(f"rows loaded into widget:  {len(fl.rows):,}")
        results.append(f"RSS after mount:          {rss_mb():.0f} MB")

        # Scroll: 200 single-line moves + forced repaints
        t0 = time.perf_counter()
        for _ in range(200):
            fl.move(1)
            await pilot.pause()
        dt = (time.perf_counter() - t0) / 200 * 1000
        results.append(f"scroll frame time:        {dt:.1f} ms/line")

        # Page-down through 100 pages
        t0 = time.perf_counter()
        for _ in range(100):
            fl.move(40)
            await pilot.pause()
        dt = (time.perf_counter() - t0) / 100 * 1000
        results.append(f"page-down frame time:     {dt:.1f} ms/page")

        # Sort by each key
        for i in range(len(SORT_KEYS)):
            fl.sort_idx = i
            t0 = time.perf_counter()
            fl.resort()
            await pilot.pause()
            results.append(
                f"sort by {SORT_KEYS[i]:<11}       "
                f"{time.perf_counter() - t0:.2f} s")

        # Filter
        t0 = time.perf_counter()
        fl.query = ".py"
        fl.resort()
        await pilot.pause()
        results.append(
            f"filter '.py' -> {len(fl.rows):,} rows   "
            f"{time.perf_counter() - t0:.2f} s")
        fl.query = ""
        fl.resort()

        results.append(f"RSS after all ops:        {rss_mb():.0f} MB")
    print("\n=== SPIKE RESULTS ===")
    for r in results:
        print(" ", r)


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    headless = "--measure" in sys.argv

    print(f"scanning {root} …", file=sys.stderr)
    t0 = time.perf_counter()
    tree = DirTree.build(scan(root))
    files = [n for n in tree.flat if n.type == "f"]
    scan_s = time.perf_counter() - t0
    meta = (f"SPIKE  {root}  files={len(files):,}  "
            f"scan+build={scan_s:.1f}s  RSS={rss_mb():.0f}MB")
    print(meta, file=sys.stderr)

    if headless:
        import asyncio
        asyncio.run(measure(files, meta))
    else:
        SpikeApp(files, meta).run()


if __name__ == "__main__":
    main()
