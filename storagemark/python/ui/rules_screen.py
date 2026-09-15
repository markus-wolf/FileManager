"""Rule picker and rule-from-path builder (spec §15).

Both measure before they commit: every rule and every candidate pattern
is shown with the number of items and the space it covers, taken from the
current scan. That is the answer to "the path says it's junk but I'm not
sure" — you choose by consequence, not by reading the path.

Counting is not free — about a second for 7 rules over a 1.4M-item scan.
The screen shows "counting…" first, then counts directly and fills in the
numbers. The pause is accepted: it happens once per keypress on a
deliberate action. (A background thread does not avoid it: the counting
is pure Python and holds the interpreter lock, so the UI froze anyway.)
"""
from __future__ import annotations

import glob
import os

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ..model import FileNode, fmt_size
from ..rules import Rule, RuleResult, evaluate


def count_label(count: int, rule_type: str) -> str:
    """'1 folder', '12 files', '3 items' — or an em dash for no matches."""
    if not count:
        return "—"
    noun = {"dir": "folder", "file": "file"}.get(rule_type, "item")
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


class RulesScreen(ModalScreen[RuleResult | None]):
    """Pick a rule. Returns the chosen RuleResult, or None."""

    BINDINGS = [
        Binding("escape,q,f", "cancel", "Close"),
        Binding("j", "down", "Down", show=False),
        Binding("k", "up", "Up", show=False),
    ]

    def __init__(self, rules: list[Rule], nodes: list[FileNode]) -> None:
        super().__init__()
        self.rules = rules
        self.nodes = nodes
        self.results: list[RuleResult] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[b]Rules[/b] — counting…   "
                   "[dim]Enter apply   Esc close[/dim]", id="rules-head"),
            DataTable(id="rules-table"),
            id="rules-box")

    def on_mount(self) -> None:
        table = self.query_one("#rules-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("RULE", "ITEMS", "SIZE", "WHY")
        for rule in self.rules:                  # visible before counting
            table.add_row(rule.name, "…", "…", rule.why, key=rule.name)
        table.focus()
        # Paint "counting…" before the pause, then count.
        self.call_after_refresh(self._count)

    def _count(self) -> None:
        if not self.is_mounted:
            return
        results = evaluate(self.rules, self.nodes)
        table = self.query_one("#rules-table", DataTable)
        head = self.query_one("#rules-head", Static)
        self.results = sorted(results, key=lambda r: r.total_size,
                              reverse=True)
        table.clear()
        for result in self.results:
            rule = result.rule
            table.add_row(rule.name,
                          count_label(result.count, rule.type),
                          fmt_size(result.total_size) if result.count else "—",
                          rule.why, key=rule.name)
        # No grand total: rules overlap (a __pycache__ inside a Caches
        # folder counts under both), so a sum would overstate the space.
        head.update(f"[b]Rules[/b] — {len(self.results)} rules   "
                    f"[dim]Enter apply   Esc close[/dim]")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_down(self) -> None:
        self.query_one("#rules-table", DataTable).action_cursor_down()

    def action_up(self) -> None:
        self.query_one("#rules-table", DataTable).action_cursor_up()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        name = str(event.row_key.value)
        chosen = next((r for r in self.results if r.rule.name == name), None)
        if chosen is None:
            self.notify("Still counting — try again in a moment.")
            return
        if not chosen.nodes:
            self.notify(f"{name}: nothing matches in this scan.")
            return
        self.dismiss(chosen)


# ------------------------------------------------------------------ #
# Build a rule from the path under the cursor                         #
# ------------------------------------------------------------------ #

def candidates_for(node: FileNode, home: str | None = None) -> list[Rule]:
    """Candidate rules derived from one path, narrowest first.

    Offers the item's extension (for a file) and then each enclosing
    folder by name, deepest first, so the safe choice is at the top.
    Folders are offered as folders rather than as "paths containing
    /name/": that substring matches a folder's contents but not the
    folder itself, and removing one folder beats removing its files.

    No size floors. Every candidate must match its origin node or one of
    its ancestors — a floor would silently break that for small items —
    and the measured count and size already show the reach.
    """
    home = home if home is not None else os.path.expanduser("~")
    out: list[Rule] = []

    if node.type == "d":
        out.append(_folder_rule(node.name))
    elif _plausible_ext(node.ext):
        ext = node.ext.lower()
        out.append(Rule(name=f"{ext} files", why=f"files ending {ext}",
                        name_glob=("*" + glob.escape(ext),)))

    # Generic path segments that would match unrelated folders everywhere.
    skip = {"users", "volumes", "private", "var", "tmp", "home", "mnt",
            "media", os.path.basename(home).lower()}
    ancestor = node.parent
    while ancestor is not None and len(out) < 8:
        name = ancestor.name
        if name and name.lower() not in skip and os.sep not in name:
            if not any(r.name_glob == (glob.escape(name.lower()),)
                       and r.type == "dir" for r in out):
                out.append(_folder_rule(name))
        ancestor = ancestor.parent
    return out


def _plausible_ext(ext: str) -> bool:
    """A real extension, not a version fragment. splitext on
    'openai.chatgpt-26.908.40401-darwin-arm64' yields '.40401-darwin-arm64',
    which would match exactly one file and lead the candidate list."""
    body = ext[1:] if ext.startswith(".") else ext
    return 1 <= len(body) <= 6 and body.isalnum() and not body.isdigit()


def _folder_rule(name: str) -> Rule:
    """Match folders with exactly this name. glob.escape keeps '[', '*'
    and '?' literal: 'Movies [YTS.MX]' is a name, not a character class."""
    return Rule(name=f"Folders named {name}", why=f"folders called {name}",
                type="dir", name_glob=(glob.escape(name.lower()),))


class RuleFromPathScreen(ModalScreen[Rule | None]):
    """Offer patterns derived from the cursor path, each with its reach."""

    BINDINGS = [
        Binding("escape,q,F", "cancel", "Close"),
        Binding("j", "down", "Down", show=False),
        Binding("k", "up", "Up", show=False),
    ]

    def __init__(self, node: FileNode, nodes: list[FileNode]) -> None:
        super().__init__()
        self.node = node
        self.nodes = nodes
        self.candidates = candidates_for(node)
        self.results: list[RuleResult] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("", id="fromparent-head"),
            DataTable(id="fromparent-table"),
            id="fromparent-box")

    def on_mount(self) -> None:
        self.query_one("#fromparent-head", Static).update(
            f"[b]Make a rule matching…[/b]   counting…\n"
            f"[dim]{escape(self.node.path[-100:])}[/dim]")
        table = self.query_one("#fromparent-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("PATTERN", "ITEMS", "SIZE")
        for i, rule in enumerate(self.candidates):
            table.add_row(rule.describe(), "…", "…", key=str(i))
        table.focus()
        self.call_after_refresh(self._count)

    def _count(self) -> None:
        if not self.is_mounted:
            return
        results = evaluate(self.candidates, self.nodes)
        table = self.query_one("#fromparent-table", DataTable)
        head = self.query_one("#fromparent-head", Static)
        self.results = results          # keep candidate order: narrowest first
        table.clear()
        for i, result in enumerate(self.results):
            table.add_row(result.rule.describe(),
                          count_label(result.count, result.rule.type),
                          fmt_size(result.total_size) if result.count else "—",
                          key=str(i))
        head.update(f"[b]Make a rule matching…[/b]   "
                    f"[dim]Enter saves to your rules file   Esc cancel[/dim]\n"
                    f"[dim]{escape(self.node.path[-100:])}[/dim]")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_down(self) -> None:
        self.query_one("#fromparent-table", DataTable).action_cursor_down()

    def action_up(self) -> None:
        self.query_one("#fromparent-table", DataTable).action_cursor_up()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not self.results:
            self.notify("Still counting — try again in a moment.")
            return
        index = int(str(event.row_key.value))
        self.dismiss(self.results[index].rule)
