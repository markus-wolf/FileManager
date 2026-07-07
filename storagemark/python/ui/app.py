"""StorageMark Textual application shell.

Replaces the curses TUI (kept behind --classic during the transition).
Scan runs in a thread worker; the ~1M-row Files view is virtualized
(see filelist.py). Small views are ordinary Textual widgets (views.py).
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from textual import work
from textual.worker import get_current_worker
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import (DataTable, Footer, Input, Static, TabbedContent,
                             TabPane)

from ... import __version__
from ..export import export_csv, export_cleanup_script
from ..model import DirTree, fmt_size, short_hostname
from ..scanner import stream_records
from ..trash import TrashError, delete_permanently, send_to_trash
from .filelist import FileList
from .marks import MarkSet
from .remove import ProgressScreen, RemoveScreen, top_level_roots
from .theme import NORTON_THEME
from .views import SubdirsTree, TimeTable, TypesTable, WhatIfPanel, TIME_FIELDS

HOSTNAME = short_hostname()


# ------------------------------------------------------------------ #
# Modals                                                              #
# ------------------------------------------------------------------ #

class ErrorsScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,E", "dismiss", "Close")]

    def __init__(self, errors) -> None:
        super().__init__()
        self.errors = errors

    def compose(self) -> ComposeResult:
        t = DataTable(id="errors-table")
        yield Vertical(
            Static(f"[b]Scan errors ({len(self.errors)})[/b]   Esc to close"),
            t, id="errors-box")

    def on_mount(self) -> None:
        t = self.query_one("#errors-table", DataTable)
        t.cursor_type = "row"
        t.add_columns("ERROR", "PATH")
        for n in self.errors:
            t.add_row(n.error, n.path)


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Close")]

    HELP = """[b]StorageMark — keys[/b]

 1–5        switch view          Space   mark / unmark (row, ext, bucket)
 j/k ↑/↓    move cursor          A / U   mark / unmark all filtered (Files)
 PgUp/PgDn  page                 M       show marked only (toggle)
 g/G        top / bottom         x       clear all marks
 s / S      sort / reverse       D       remove marked (Trash / permanent)
 /          filter (Esc clears)  Enter   expand / drill in
 u          size unit            e       export view to CSV
 t          time field (Time)    E       scan errors
 r          re-scan              p       change root path
 q          quit                 ?       this help
"""

    def compose(self) -> ComposeResult:
        yield Static(self.HELP, id="help-box")


class ConfirmScreen(ModalScreen[bool]):
    """Generic y/N confirmation. Returns True only on y/Y/Enter."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Static(f"{self.message}\n\n[b]y[/b] confirm    "
                     "[b]any other key[/b] cancel", id="confirm-box")

    def on_key(self, event) -> None:
        self.dismiss(event.key in ("y", "Y", "enter"))
        event.stop()


class ScanInterruptScreen(ModalScreen[str]):
    """Shown on Ctrl-C while a scan is running.

    Returns 'quit', 'partial', or 'continue'. The scan keeps running in
    the background while this is open; if it finishes, we auto-dismiss.
    """

    def compose(self) -> ComposeResult:
        # A bare Static, like HelpScreen: a width:auto container holding a
        # default-width Static collapses to an empty box.
        yield Static(id="interrupt-box")

    def on_mount(self) -> None:
        self._refresh_text()
        self.set_interval(0.25, self._tick)

    def _refresh_text(self) -> None:
        count = getattr(self.app, "scan_count", 0)
        self.query_one("#interrupt-box", Static).update(
            f"[b]Scan running — {count:,} objects so far[/b]\n\n"
            "  [b]Ctrl-Q[/b]    quit StorageMark\n"
            "  [b]Ctrl-C[/b]    stop scanning, show PARTIAL results\n"
            "  [b]any key[/b]   keep scanning"
        )

    def _tick(self) -> None:
        if getattr(self.app, "dir_tree", None) is not None:
            self.dismiss("continue")     # scan finished while dialog open
        else:
            self._refresh_text()

    def on_key(self, event) -> None:
        if event.key == "ctrl+q":
            self.dismiss("quit")
        elif event.key == "ctrl+c":
            self.dismiss("partial")
        else:
            self.dismiss("continue")
        event.stop()


class PathScreen(ModalScreen[str]):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Vertical(Static("[b]New root path[/b] (Esc to cancel)"),
                       Input(placeholder="/path/to/scan", id="path-input"),
                       id="path-box")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


# ------------------------------------------------------------------ #
# App                                                                 #
# ------------------------------------------------------------------ #

class StorageMarkApp(App):
    TITLE = "StorageMark"
    AUTO_FOCUS = "#file-list"
    CSS = """
    #hdr { height: 2; background: $panel; color: auto 100%; }
    #filter-input { display: none; dock: bottom; height: 1; }
    #filter-input.visible { display: block; }
    #errors-box, #path-box { width: 90%; height: 80%; margin: 2 4;
        background: $surface; border: solid $accent; padding: 1; }
    #path-box { height: auto; }
    #help-box { width: auto; height: auto; margin: 4 8;
        background: $surface; border: solid $accent; padding: 1 2; }
    #whatif-summary { height: auto; padding: 0 1; }
    #remove-box { width: 90%; height: 80%; margin: 2 4;
        background: $surface; border: solid $warning; padding: 1; }
    #remove-head { height: auto; }
    #remove-actions { height: 3; dock: bottom; }
    #confirm-input { width: 40; }
    #prog-box { width: 70%; height: auto; margin: 8 8;
        background: $surface; border: solid $warning; padding: 1 2; }
    #interrupt-box { width: auto; height: auto; margin: 6 8;
        background: $surface; border: solid $accent; padding: 1 2; }
    #confirm-box { width: auto; height: auto; margin: 6 8;
        background: $surface; border: solid $warning; padding: 1 2; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("1", "tab('subdirs')", "SubDirs"),
        Binding("2", "tab('files')", "Files"),
        Binding("3", "tab('types')", "Types"),
        Binding("4", "tab('time')", "Time"),
        Binding("5", "tab('whatif')", "What-If"),
        Binding("slash", "filter", "Filter", key_display="/"),
        Binding("e", "export", "CSV"),
        Binding("E", "errors", "Errors"),
        Binding("r", "rescan", "Re-scan"),
        Binding("p", "path", "Path"),
        Binding("u", "unit", "Unit"),
        Binding("A", "mark_all", "Mark filtered", show=False),
        Binding("U", "unmark_all", "Unmark filtered", show=False),
        Binding("M", "marked_only", "Marked only", show=False),
        Binding("x", "clear_marks", "Clear marks", show=False),
        Binding("t", "time_field", "Time field", show=False),
        Binding("D", "delete_marked", "Delete marked"),
        Binding("ctrl+c", "interrupt", "Interrupt", priority=True, show=False),
    ]

    def __init__(self, root: str, scanner_kwargs: dict | None = None) -> None:
        super().__init__()
        self.root_path = os.path.abspath(root)
        self.scanner_kwargs = scanner_kwargs or {}
        self.dir_tree: DirTree | None = None
        self.node_map: dict[str, object] = {}     # path -> FileNode, per scan
        self.marked = MarkSet()
        self._marked_version_seen = -1
        self._marked_size = 0
        self.unit_ref = ["auto"]
        self.scan_count = 0
        self.scan_time = 0.0
        self.scan_error: str | None = None
        self.scan_last_path = ""
        self.partial = False
        self.scanning = False
        self._scan_abort = threading.Event()
        self._dirty: set[str] = set()      # views needing reload on activation

    # ------------------------------------------------------------ UI --

    def compose(self) -> ComposeResult:
        yield Static(id="hdr")
        with TabbedContent(initial="files"):
            with TabPane("SubDirs", id="subdirs"):
                yield SubdirsTree(self.marked, id="subdirs-tree")
            with TabPane("Files", id="files"):
                yield FileList(self.marked, self.unit_ref, id="file-list")
            with TabPane("Types", id="types"):
                yield TypesTable(id="types-table")
            with TabPane("Time", id="time"):
                yield TimeTable(id="time-table")
            with TabPane("What-If", id="whatif"):
                yield WhatIfPanel(self.marked, id="whatif-panel")
        yield Input(placeholder="filter: glob, ~regex, ! inverts — Esc clears",
                    id="filter-input", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(NORTON_THEME)
        self.theme = "norton-commander"
        self.update_header()
        self.set_interval(0.25, self.update_header)
        self.query_one(TabbedContent).loading = True
        self.scan_worker()

    def update_header(self) -> None:
        # Line 1: identity + scan status, version right-aligned. Built from
        # plain text (len == display width); if the terminal is narrower
        # than the content, truncate the left side — never wrap the version.
        # NB: no '[' in line 1 — Static parses Rich markup.
        try:
            hdr = self.query_one("#hdr", Static)
        except Exception:
            return   # interval timer can fire once more during app teardown
        status = (f"Scanned: {self.scan_time:.1f}s" if self.dir_tree
                  else f"SCANNING…  {self.scan_count:,} objects")
        left = (f" StorageMark  {'⚠PARTIAL ' if self.partial else ''}"
                f"{HOSTNAME}:{self.root_path}    {status}")
        ver = f"v{__version__}"
        width = hdr.size.width or self.size.width or 80
        avail = max(10, width - len(ver) - 1)      # room reserved for version
        if len(left) > avail:
            left = left[: avail - 1] + "…"
        line1 = f"{left:<{avail}} {ver}"

        if self.dir_tree:
            t = self.dir_tree
            line2 = (f" Total: {fmt_size(t.total_disk)}  "
                     f"Files: {t.file_count:,}  Dirs: {t.dir_count:,}  "
                     f"Errors: {len(t.errors)}")
            if self.marked:
                line2 += (f"  [b]Marked: {len(self.marked):,} items, "
                          f"{fmt_size(self.marked_size())}[/b]")
        else:
            at = self.scan_last_path[-90:] if self.scan_last_path else "…"
            line2 = f" [b]{self.scan_count:,}[/b] objects   [dim]at: {at}[/dim]"

        hdr.update(line1 + "\n" + line2)

    def marked_size(self) -> int:
        """Total disk of marked items; recomputed only when marks change."""
        if self.marked.version != self._marked_version_seen:
            self._marked_version_seen = self.marked.version
            nm = self.node_map
            total = 0
            for p in self.marked:
                n = nm.get(p)
                if n is not None:
                    total += n.display_size
            self._marked_size = total
        return self._marked_size

    # ------------------------------------------------------------ scan --

    @work(thread=True, exclusive=True)
    def scan_worker(self) -> None:
        t0 = time.time()
        records: list[dict] = []
        self.scan_error = None
        self.scanning = True
        self._scan_abort.clear()
        worker = get_current_worker()

        def stop() -> bool:
            return self._scan_abort.is_set() or worker.is_cancelled

        try:
            for rec in stream_records(self.root_path, should_stop=stop,
                                      **self.scanner_kwargs):
                records.append(rec)
                self.scan_count = len(records)
                self.scan_last_path = rec.get("path", "")
        except Exception as e:
            self.partial = True
            self.scan_error = str(e)
        if self._scan_abort.is_set():
            self.partial = True          # user interrupted: partial results
        if worker.is_cancelled:
            return                        # app is shutting down — no UI calls
        self.scan_time = time.time() - t0
        try:
            tree = DirTree.build(records)
        except Exception as e:
            tree = None
            self.scan_error = self.scan_error or str(e)
        records.clear()
        self.call_from_thread(self.on_scan_done, tree)

    def on_scan_done(self, tree: DirTree | None) -> None:
        self.scanning = False
        self.dir_tree = tree
        self.node_map = {n.path: n for n in tree.flat} if tree else {}
        self.query_one(TabbedContent).loading = False
        if tree is None:
            self.notify(self.scan_error or "Scan produced no records.",
                        severity="error", timeout=30)
            return
        if self.partial:
            self.notify(
                f"Scan interrupted — showing PARTIAL results "
                f"({self.scan_count:,} objects). Press r to re-scan.",
                severity="warning", timeout=12)
        # Refresh only the visible view now; others reload lazily on
        # activation. At ~1M nodes each view load costs real time — doing
        # all five eagerly is what froze the UI.
        self._dirty = {"subdirs", "files", "types", "time", "whatif"}
        self._refresh_pane(self.query_one(TabbedContent).active)
        self.update_header()

    # ------------------------------------------------- lazy view loads --

    def _refresh_pane(self, pane: str) -> None:
        """Load `pane` from dir_tree if it is stale."""
        if not self.dir_tree or pane not in self._dirty:
            return
        self._dirty.discard(pane)
        tree = self.dir_tree
        if pane == "files":
            files = [n for n in tree.flat if n.type == "f"]
            self.query_one("#file-list", FileList).set_files(files)
        elif pane == "subdirs":
            self.query_one("#subdirs-tree", SubdirsTree).load(tree)
        elif pane == "types":
            self.query_one("#types-table", TypesTable).load(tree)
            self._refresh_mark_indicators()
        elif pane == "time":
            self.query_one("#time-table", TimeTable).load(tree)
            self._refresh_mark_indicators()
        elif pane == "whatif":
            self.query_one("#whatif-panel", WhatIfPanel).load(
                tree, self.node_map)

    def on_tabbed_content_tab_activated(
            self, event: TabbedContent.TabActivated) -> None:
        """Central refresh point — fires for keys AND mouse tab clicks."""
        pane = str(event.pane.id)
        self._refresh_pane(pane)
        if pane == "whatif":
            self.query_one("#whatif-panel", WhatIfPanel).reload()
        elif pane == "subdirs":
            self.query_one("#subdirs-tree", SubdirsTree).refresh_labels()
        elif pane in ("types", "time"):
            self._refresh_mark_indicators()

    def restart_scan(self) -> None:
        self.dir_tree = None
        self.marked.clear()
        self.scan_count = 0
        self.scan_last_path = ""
        self.partial = False
        self._dirty.clear()
        self.query_one(TabbedContent).loading = True
        self.scan_worker()

    # --------------------------------------------------------- actions --

    def action_tab(self, pane: str) -> None:
        # Per-view refreshes happen in on_tabbed_content_tab_activated,
        # which fires for this assignment and for mouse tab clicks alike.
        self.query_one(TabbedContent).active = pane

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_interrupt(self) -> None:
        """Ctrl-C: interrupt dialog while scanning; plain quit when idle."""
        if isinstance(self.screen, ScanInterruptScreen):
            # Second Ctrl-C: the priority binding consumes the key before
            # the dialog's on_key can — resolve it here as 'partial'.
            self.screen.dismiss("partial")
            return
        if self.scanning:
            def on_choice(choice: str | None) -> None:
                if choice == "quit":
                    self.action_quit_now()
                elif choice == "partial":
                    self._scan_abort.set()
                # 'continue' / None: scan was never paused — nothing to do
            self.push_screen(ScanInterruptScreen(), on_choice)
        elif self.screen is self.screen_stack[0]:
            self.action_quit_now()        # idle, no modal open: quit (as curses did)

    def action_quit_now(self) -> None:
        """Quit without leaving the scan thread or C scanner behind."""
        self._scan_abort.set()
        self.workers.cancel_all()
        self.exit()

    async def action_quit(self) -> None:
        self.action_quit_now()

    def action_errors(self) -> None:
        if self.dir_tree and self.dir_tree.errors:
            self.push_screen(ErrorsScreen(self.dir_tree.errors))
        else:
            self.notify("No scan errors.")

    def action_filter(self) -> None:
        box = self.query_one("#filter-input", Input)
        box.disabled = False
        box.add_class("visible")
        box.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "filter-input":
            return
        fl = self.query_one("#file-list", FileList)
        fl.query = event.value
        fl.resort()
        self.query_one(TabbedContent).active = "files"
        event.input.remove_class("visible")
        event.input.disabled = True
        fl.focus()

    def on_key(self, event) -> None:
        # Esc in the filter box clears the filter
        box = self.query_one("#filter-input", Input)
        if event.key == "escape" and box.has_focus:
            box.value = ""
            fl = self.query_one("#file-list", FileList)
            fl.query = ""
            fl.set_pre_filter(None)
            fl.resort()
            box.remove_class("visible")
            box.disabled = True
            fl.focus()
            event.stop()

    def action_export(self) -> None:
        if not self.dir_tree:
            return
        fname = f"storagemark_export_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        with open(fname, "w") as f:
            f.write(export_csv(self.dir_tree.flat))
        self.notify(f"Exported {fname}")

    def action_rescan(self) -> None:
        self.restart_scan()

    def action_path(self) -> None:
        def set_path(value: str | None) -> None:
            if value and os.path.isdir(os.path.expanduser(value)):
                self.root_path = os.path.abspath(os.path.expanduser(value))
                self.restart_scan()
            elif value:
                self.notify(f"Not a directory: {value}", severity="warning")
        self.push_screen(PathScreen(), set_path)

    def action_unit(self) -> None:
        units = ["auto", "GB", "MB", "KB", "B"]
        self.unit_ref[0] = units[(units.index(self.unit_ref[0]) + 1) % len(units)]
        self.query_one("#file-list", FileList).refresh()
        self.notify(f"Unit: {self.unit_ref[0]}")

    def action_mark_all(self) -> None:
        fl = self.query_one("#file-list", FileList)
        if fl.is_unfiltered():
            # No filter active: 'A' would mark the whole tree — confirm.
            total = sum(n.size_disk for n in fl.rows)
            def on_confirm(yes: bool | None) -> None:
                if yes:
                    n = fl.mark_all_visible()
                    self.notify(f"Marked {n:,} items")
                    self._refresh_mark_indicators()
            self.push_screen(ConfirmScreen(
                f"[b]No filter is active.[/b]\n"
                f"Mark ALL {len(fl.rows):,} files "
                f"({fmt_size(total)})?"), on_confirm)
            return
        n = fl.mark_all_visible()
        self.notify(f"Marked {n:,} items")
        self._refresh_mark_indicators()

    def action_unmark_all(self) -> None:
        fl = self.query_one("#file-list", FileList)
        n = fl.unmark_all_visible()
        self.notify(f"Unmarked {n:,} items")
        self._refresh_mark_indicators()

    def action_marked_only(self) -> None:
        fl = self.query_one("#file-list", FileList)
        fl.show_marked_only = not fl.show_marked_only
        fl.resort()
        self.query_one(TabbedContent).active = "files"
        fl.focus()
        if fl.show_marked_only:
            self.notify(f"Showing marked only — {len(fl.rows):,} rows "
                        f"(M again for all files)")
        else:
            self.notify(f"Showing all files ({len(fl.rows):,} rows)")

    def action_clear_marks(self) -> None:
        self.marked.clear()
        fl = self.query_one("#file-list", FileList)
        if fl.show_marked_only:
            fl.resort()
        else:
            fl.refresh()
        self.query_one("#whatif-panel", WhatIfPanel).reload()
        self._refresh_mark_indicators()
        self.notify("All marks cleared")

    # ---------------------------------------- marked-count indicators --

    def _refresh_mark_indicators(self) -> None:
        """Push ● counts into the Types/Time tables. O(len(marked))."""
        if not self.dir_tree:
            return
        by_ext: dict[str, int] = {}
        marked_files = []
        for p in self.marked:
            n = self.node_map.get(p)
            if n is not None and n.type == "f":
                marked_files.append(n)
                ext = n.ext or "(no ext)"
                by_ext[ext] = by_ext.get(ext, 0) + 1
        self.query_one("#types-table", TypesTable).refresh_marks(by_ext)

        tt = self.query_one("#time-table", TimeTable)
        by_bucket: dict[str, int] = {}
        for label, (field, lo, hi) in tt.bucket_ranges().items():
            by_bucket[label] = sum(
                1 for n in marked_files
                if (lo is None or getattr(n, field) >= lo)
                and (hi is None or getattr(n, field) < hi))
        tt.refresh_marks(by_bucket)

    def action_time_field(self) -> None:
        tt = self.query_one("#time-table", TimeTable)
        tt.field_idx = (tt.field_idx + 1) % len(TIME_FIELDS)
        if self.dir_tree:
            tt.load(self.dir_tree)
        self.notify(f"Time field: {TIME_FIELDS[tt.field_idx]}")

    # --------------------------------------------------- removal (D) --

    def action_delete_marked(self) -> None:
        if not self.dir_tree or not self.marked:
            self.notify("Nothing marked. Use Space / A to mark items first.")
            return
        nodes = [self.node_map[p] for p in sorted(self.marked)
                 if p in self.node_map]
        roots = top_level_roots(nodes, set(self.marked))
        if not roots:
            self.notify("Nothing to remove.")
            return

        def on_choice(mode: str | None) -> None:
            if mode in ("trash", "delete"):
                self._progress = ProgressScreen(len(roots), mode)
                self.push_screen(self._progress)
                # Pass the job via attribute, NOT as @work arguments:
                # the decorator reprs its args into a worker description,
                # which is O(len) and was catastrophic pre-repr=False.
                self._delete_job = (mode, roots)
                self.delete_worker()

        self.push_screen(RemoveScreen(roots, len(self.marked)), on_choice)

    @work(thread=True, exclusive=True, group="delete")
    def delete_worker(self) -> None:
        mode, roots = self._delete_job
        ok: list[str] = []
        failures: list[tuple] = []          # (FileNode, error string)
        act = send_to_trash if mode == "trash" else delete_permanently
        for i, node in enumerate(roots, 1):
            try:
                act(node.path)
                ok.append(node.path)
            except TrashError as e:
                failures.append((node, str(e)))
            self.call_from_thread(self._progress.update_progress, i, node.path)
        self.call_from_thread(self.on_delete_done, mode, ok, failures)

    def on_delete_done(self, mode: str, ok: list[str],
                       failures: list[tuple]) -> None:
        self.pop_screen()                    # progress screen
        tree = self.dir_tree
        freed = sum(self.node_map[p].display_size
                    for p in ok if p in self.node_map)

        if ok and tree:
            tree.prune(ok, self.node_map)   # node_map updated in place
            self.marked.difference_update(set(ok))
            # Drop marks that pointed inside removed subtrees
            self.marked.difference_update(
                {p for p in self.marked if p not in self.node_map})

        for node, err in failures:
            node.error = err
            if tree and node not in tree.errors:
                tree.errors.append(node)

        if tree:
            # Mark all views stale, then update only what's visible.
            # A full SubdirsTree.load() re-materializes the Tree with many
            # seconds of deferred main-thread work — remove the deleted
            # nodes surgically instead (keeps expansion state too).
            self._dirty = {"files", "subdirs", "types", "time", "whatif"}
            st = self.query_one("#subdirs-tree", SubdirsTree)
            st.total = tree.total_disk or 1
            st.remove_paths(set(ok))
            st.refresh_labels()             # ancestor sizes changed
            self._dirty.discard("subdirs")
            active = self.query_one(TabbedContent).active
            self._refresh_pane(active)
            if active == "whatif":
                self.query_one("#whatif-panel", WhatIfPanel).reload()
        self.update_header()

        verb = "Trashed" if mode == "trash" else "Deleted"
        msg = f"{verb} {len(ok):,} item(s), freed {fmt_size(freed)}"
        if failures:
            msg += f" — {len(failures)} failed (see [E]rrors)"
            self.notify(msg, severity="warning", timeout=15)
        else:
            self.notify(msg, timeout=10)

    # ------------------------------------------- view→view messages --

    def on_types_table_drill_ext(self, msg: TypesTable.DrillExt) -> None:
        fl = self.query_one("#file-list", FileList)
        ext = msg.ext
        if ext == "(no ext)":
            fl.set_pre_filter(lambda n: not n.ext, f"ext:{ext}")
        else:
            fl.set_pre_filter(lambda n: n.ext == ext, f"ext:{ext}")
        self.query_one(TabbedContent).active = "files"
        fl.focus()

    def on_types_table_mark_ext(self, msg: TypesTable.MarkExt) -> None:
        if not self.dir_tree:
            return
        paths = {n.path for n in self.dir_tree.ext_map.get(msg.ext, [])}
        if paths and paths <= self.marked:      # all already marked → toggle off
            self.marked.difference_update(paths)
            self.notify(f"Unmarked {len(paths):,} × {msg.ext}")
        else:
            self.marked.update(paths)
            self.notify(f"Marked {len(paths):,} × {msg.ext}")
        self._refresh_mark_indicators()

    def on_time_table_drill_bucket(self, msg: TimeTable.DrillBucket) -> None:
        fl = self.query_one("#file-list", FileList)
        field, lo, hi = msg.field, msg.lo, msg.hi
        fl.set_pre_filter(
            lambda n: (lo is None or getattr(n, field) >= lo)
            and (hi is None or getattr(n, field) < hi),
            f"age:{msg.label}")
        self.query_one(TabbedContent).active = "files"
        fl.focus()

    def on_time_table_mark_bucket(self, msg: TimeTable.MarkBucket) -> None:
        if not self.dir_tree:
            return
        field, lo, hi = msg.field, msg.lo, msg.hi
        paths = set()
        for n in self.dir_tree.flat:
            if n.type != "f":
                continue
            t = getattr(n, field)
            if (lo is None or t >= lo) and (hi is None or t < hi):
                paths.add(n.path)
        if paths and paths <= self.marked:      # all already marked → toggle off
            self.marked.difference_update(paths)
            self.notify(f"Unmarked {len(paths):,} files ({msg.label})")
        else:
            self.marked.update(paths)
            self.notify(f"Marked {len(paths):,} files ({msg.label})")
        self._refresh_mark_indicators()

    # ---------------------------------------------- whatif / export --

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter in the What-If table exports the cleanup script
        if event.data_table.id == "whatif-table":
            self.export_cleanup()

    def export_cleanup(self) -> None:
        if not self.dir_tree or not self.marked:
            self.notify("Nothing marked.")
            return
        node_map = {n.path: n for n in self.dir_tree.flat}
        nodes = [node_map[p] for p in sorted(self.marked) if p in node_map]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"storagemark_cleanup_{ts}.sh"
        with open(fname, "w") as f:
            f.write(export_cleanup_script(nodes, ts))
        os.chmod(fname, 0o755)
        self.notify(f"Cleanup script: {fname}")


def run_ui(root: str, scanner_kwargs: dict | None = None) -> None:
    StorageMarkApp(root, scanner_kwargs).run()
