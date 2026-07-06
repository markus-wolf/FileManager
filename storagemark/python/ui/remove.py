"""Removal confirmation modal + deletion progress screen (Phase 2).

Flow: 'D' → RemoveScreen (itemized, warnings, Trash / typed-'yes'
permanent / cancel) → app runs a thread worker → ProgressScreen updates
→ tree pruned in memory → views refresh. Trash is the undo.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, ProgressBar, Static

from ..model import FileNode, fmt_size

MAX_LISTED = 200
YOUNG_DAYS = 30

# Marking these *themselves* (not contents inside them) gets a warning.
_HOME = Path.home()
PROTECTED_ROOTS = {
    str(_HOME), "/", "/Users", "/Applications", "/Library", "/System",
    *(str(_HOME / d) for d in
      ("Library", "Documents", "Desktop", "Downloads",
       "Pictures", "Movies", "Music", "Applications")),
}


def top_level_roots(nodes: list[FileNode], marked: set[str]) -> list[FileNode]:
    """Drop nodes whose ancestor is also marked — deleting the ancestor
    removes them anyway, and double operations would fail."""
    roots = []
    for n in nodes:
        p = n.parent
        nested = False
        while p is not None:
            if p.path in marked:
                nested = True
                break
            p = p.parent
        if not nested:
            roots.append(n)
    return roots


def build_warnings(roots: list[FileNode]) -> list[str]:
    warns = []
    now = datetime.now()
    for n in roots:
        if n.path in PROTECTED_ROOTS:
            warns.append(f"‼ {n.path} is a protected system/user folder")
    young = sum(1 for n in roots if (now - n.mtime).days < YOUNG_DAYS)
    if young:
        warns.append(f"! {young} item(s) modified within {YOUNG_DAYS} days")
    return warns


class RemoveScreen(ModalScreen[str | None]):
    """Returns 'trash', 'delete', or None (cancelled)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("t", "trash", "Move to Trash"),
    ]

    def __init__(self, roots: list[FileNode], total_marked: int) -> None:
        super().__init__()
        self.roots = roots
        self.total_marked = total_marked

    def compose(self) -> ComposeResult:
        total = sum(n.subtree_disk if n.type == "d" else n.size_disk
                    for n in self.roots)
        warns = build_warnings(self.roots)
        head = (f"[b]Remove {len(self.roots):,} item(s)[/b] "
                f"({self.total_marked:,} marked)  —  "
                f"[b]{fmt_size(total).strip()}[/b]\n")
        if warns:
            head += "\n".join(f"[red]{w}[/red]" for w in warns) + "\n"
        head += ("\n[b]t[/b] / button = move to [b]Trash[/b] (recoverable)"
                 "\ntype [b]yes[/b] + Enter = delete permanently  —  "
                 "[b]Esc[/b] = cancel")
        table = DataTable(id="remove-table")
        yield Vertical(
            Static(head, id="remove-head"),
            table,
            Horizontal(
                Button("Move to Trash", variant="warning", id="btn-trash"),
                Input(placeholder="type 'yes' for PERMANENT delete",
                      id="confirm-input"),
                Button("Cancel", id="btn-cancel"),
                id="remove-actions"),
            id="remove-box")

    def on_mount(self) -> None:
        t = self.query_one("#remove-table", DataTable)
        t.cursor_type = "row"
        t.add_columns("SIZE", "TYPE", "PATH")
        by_size = sorted(self.roots, key=lambda n: n.subtree_disk
                         if n.type == "d" else n.size_disk, reverse=True)
        for n in by_size[:MAX_LISTED]:
            size = n.subtree_disk if n.type == "d" else n.size_disk
            t.add_row(fmt_size(size).strip(),
                      "dir" if n.type == "d" else "file", n.path)
        if len(by_size) > MAX_LISTED:
            t.add_row("…", "", f"and {len(by_size) - MAX_LISTED:,} more")
        self.query_one("#btn-trash", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_trash(self) -> None:
        self.dismiss("trash")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("trash" if event.button.id == "btn-trash" else None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip().lower() == "yes":
            self.dismiss("delete")
        else:
            self.notify("Type exactly 'yes' to delete permanently.",
                        severity="warning")


class ProgressScreen(ModalScreen):
    """Indeterminate → determinate progress while the worker deletes."""

    def __init__(self, total: int, mode: str) -> None:
        super().__init__()
        self.total = total
        self.mode = mode

    def compose(self) -> ComposeResult:
        label = "Moving to Trash" if self.mode == "trash" else "Deleting"
        yield Vertical(
            Static(f"[b]{label}…[/b]", id="prog-label"),
            ProgressBar(total=self.total, show_eta=False, id="prog-bar"),
            Static("", id="prog-path"),
            id="prog-box")

    def update_progress(self, done: int, current_path: str) -> None:
        self.query_one("#prog-bar", ProgressBar).update(progress=done)
        self.query_one("#prog-path", Static).update(
            f"[dim]{current_path[-80:]}[/dim]")
