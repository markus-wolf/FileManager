"""Rules: reusable "what should I look at?" queries over a scanned tree.

A rule combines path patterns with size and age limits. Patterns matter on
their own: the case that motivated this (an editor's own `.trash` folder
holding 1.3 GB of superseded installers) is entirely newer than 90 days,
so an age test misses all of it.

Built-in rules live in BUILTIN_RULES and ship with the app, so upgrades
deliver new ones. `~/.config/storagemark/rules.toml` is additive: rules
there are appended, and one sharing a built-in's `name` replaces it.

Framework-agnostic — no Textual import — so it is testable on its own.
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from .model import FileNode

# ------------------------------------------------------------------ #
# Value parsing                                                       #
# ------------------------------------------------------------------ #

_SIZE_UNITS = {"B": 1, "KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30,
               "TB": 1 << 40}
# Ages are coarse by nature; 'm' is ambiguous (minutes/months) so months
# are spelled 'mo'.
_DAY_UNITS = {"D": 1.0, "W": 7.0, "MO": 30.0, "Y": 365.0}


class RuleError(ValueError):
    """A rule could not be understood. Reported, never fatal."""


def parse_size(text: str | int) -> int:
    """'100MB' -> 104857600. A bare number is bytes."""
    if isinstance(text, int):
        return text
    s = str(text).strip().upper().replace(" ", "")
    for unit in ("TB", "GB", "MB", "KB", "B"):
        if s.endswith(unit):
            number = s[: -len(unit)]
            try:
                return int(float(number) * _SIZE_UNITS[unit])
            except ValueError:
                raise RuleError(f"bad size {text!r}") from None
    try:
        return int(float(s))
    except ValueError:
        raise RuleError(f"bad size {text!r}") from None


def parse_days(text: str | int | float) -> float:
    """'90d' -> 90.0, '1y' -> 365.0, '6mo' -> 180.0. Bare number is days."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().upper().replace(" ", "")
    for unit in ("MO", "D", "W", "Y"):
        if s.endswith(unit):
            number = s[: -len(unit)]
            try:
                return float(number) * _DAY_UNITS[unit]
            except ValueError:
                raise RuleError(f"bad duration {text!r}") from None
    try:
        return float(s)
    except ValueError:
        raise RuleError(f"bad duration {text!r}") from None


def _as_list(value, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise RuleError(f"{key} must be a string or a list of strings")


# ------------------------------------------------------------------ #
# Rule                                                                #
# ------------------------------------------------------------------ #

KNOWN_KEYS = {"name", "why", "type", "path_contains", "path_glob",
              "name_glob", "min_size", "max_size", "older_than",
              "newer_than", "time_field"}


@dataclass(frozen=True)
class Rule:
    """One saved query. Conditions are ANDed; a list within a condition is
    ORed. Everything except `name` is optional.

    Sizes compare against FileNode.display_size — a directory's whole
    subtree — because a rule targeting a folder means the space it holds,
    not the few bytes of the directory entry itself.
    """
    name: str
    why: str = ""
    type: str = "file"                    # "file" | "dir" | "any"
    path_contains: tuple[str, ...] = ()
    path_glob: tuple[str, ...] = ()
    name_glob: tuple[str, ...] = ()
    min_size: int = 0
    max_size: int | None = None
    older_than: float | None = None       # days
    newer_than: float | None = None       # days
    time_field: str = "mtime"             # mtime | atime | ctime

    # ---- construction ----

    @staticmethod
    def from_dict(data: dict) -> Rule:
        unknown = set(data) - KNOWN_KEYS
        if unknown:
            raise RuleError(f"unknown key(s): {', '.join(sorted(unknown))}")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RuleError("every rule needs a non-empty name")
        rtype = str(data.get("type", "file")).lower()
        if rtype not in ("file", "dir", "any"):
            raise RuleError(f"type must be file, dir or any (got {rtype!r})")
        tfield = str(data.get("time_field", "mtime")).lower()
        if tfield not in ("mtime", "atime", "ctime"):
            raise RuleError(f"time_field must be mtime, atime or ctime")
        return Rule(
            name=name.strip(),
            why=str(data.get("why", "")),
            type=rtype,
            # Patterns are matched case-insensitively; lower them once here.
            path_contains=tuple(s.lower() for s in
                                _as_list(data.get("path_contains"), "path_contains")),
            path_glob=tuple(s.lower() for s in
                            _as_list(data.get("path_glob"), "path_glob")),
            name_glob=tuple(s.lower() for s in
                            _as_list(data.get("name_glob"), "name_glob")),
            min_size=parse_size(data["min_size"]) if "min_size" in data else 0,
            max_size=parse_size(data["max_size"]) if "max_size" in data else None,
            older_than=parse_days(data["older_than"]) if "older_than" in data else None,
            newer_than=parse_days(data["newer_than"]) if "newer_than" in data else None,
            time_field=tfield,
        )

    # ---- matching ----

    def predicate(self, now: datetime | None = None) -> Callable[[FileNode], bool]:
        """Build a matcher. Age cutoffs are resolved once here rather than
        per node — this runs over every node in the tree."""
        now = now or datetime.now()
        want_type = self.type
        contains = self.path_contains
        pglobs = self.path_glob
        nglobs = self.name_glob
        min_size, max_size = self.min_size, self.max_size
        field_name = self.time_field
        older_cutoff = (now - timedelta(days=self.older_than)
                        if self.older_than is not None else None)
        newer_cutoff = (now - timedelta(days=self.newer_than)
                        if self.newer_than is not None else None)

        def match(node: FileNode) -> bool:
            if want_type == "file":
                if node.type != "f":
                    return False
            elif want_type == "dir":
                if node.type != "d":
                    return False
            # cheap numeric tests before any string work
            size = node.display_size
            if size < min_size:
                return False
            if max_size is not None and size > max_size:
                return False
            if older_cutoff is not None or newer_cutoff is not None:
                stamp = getattr(node, field_name)
                if older_cutoff is not None and stamp >= older_cutoff:
                    return False
                if newer_cutoff is not None and stamp < newer_cutoff:
                    return False
            if contains or pglobs:
                path = node.path.lower()
                if contains and not any(c in path for c in contains):
                    return False
                # fnmatchcase on pre-lowered text: predictable regardless of
                # the filesystem's own case sensitivity. '*' crosses '/'.
                if pglobs and not any(fnmatch.fnmatchcase(path, g)
                                      for g in pglobs):
                    return False
            if nglobs:
                base = node.name.lower()
                if not any(fnmatch.fnmatchcase(base, g) for g in nglobs):
                    return False
            return True

        return match

    def matches(self, node: FileNode, now: datetime | None = None) -> bool:
        """Convenience for single checks; use predicate() for bulk work."""
        return self.predicate(now)(node)

    def describe(self) -> str:
        """Human summary, e.g. 'folders named .trash, >10MB'."""
        noun = {"file": "files", "dir": "folders",
                "any": "files and folders"}[self.type]
        conditions: list[str] = []
        if self.name_glob:
            conditions.append("named " + " or ".join(self.name_glob))
        if self.path_contains:
            conditions.append("path has " + " or ".join(self.path_contains))
        if self.path_glob:
            conditions.append("path like " + " or ".join(self.path_glob))
        if self.min_size:
            conditions.append(f">{_short_size(self.min_size)}")
        if self.max_size is not None:
            conditions.append(f"<{_short_size(self.max_size)}")
        if self.older_than is not None:
            conditions.append(f"older than {_short_days(self.older_than)}")
        if self.newer_than is not None:
            conditions.append(f"newer than {_short_days(self.newer_than)}")
        if not conditions:
            return f"all {noun}"
        # the first condition attaches to the noun; the rest follow commas
        return f"{noun} {conditions[0]}" + "".join(f", {c}" for c in conditions[1:])

    def to_toml(self) -> str:
        """A [[rule]] block. Used when appending a rule built in the UI.

        Strings go through _toml_str: names come off the disk verbatim and
        may contain '"' or '\\', which a bare f-string would turn into
        invalid TOML.
        """
        q = _toml_str
        lines = ["[[rule]]", f"name = {q(self.name)}"]
        if self.why:
            lines.append(f"why  = {q(self.why)}")
        if self.type != "file":
            lines.append(f"type = {q(self.type)}")
        for key, values in (("path_contains", self.path_contains),
                            ("path_glob", self.path_glob),
                            ("name_glob", self.name_glob)):
            if values:
                lines.append(f"{key} = [{', '.join(q(v) for v in values)}]")
        if self.min_size:
            lines.append(f"min_size = {q(_short_size(self.min_size))}")
        if self.max_size is not None:
            lines.append(f"max_size = {q(_short_size(self.max_size))}")
        if self.older_than is not None:
            lines.append(f"older_than = {q(_short_days(self.older_than))}")
        if self.newer_than is not None:
            lines.append(f"newer_than = {q(_short_days(self.newer_than))}")
        if self.time_field != "mtime":
            lines.append(f"time_field = {q(self.time_field)}")
        return "\n".join(lines) + "\n"


def _toml_str(value: str) -> str:
    """A TOML basic string. JSON's escapes (\\", \\\\, \\n, \\uXXXX) are a
    subset of TOML's, so json.dumps produces valid TOML for any text."""
    return json.dumps(value, ensure_ascii=False)


def _short_size(n: int) -> str:
    for unit, div in (("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20),
                      ("KB", 1 << 10)):
        if n >= div and n % div == 0:
            return f"{n // div}{unit}"
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.0f}{unit}"
    return f"{n}B"


def _short_days(days: float) -> str:
    if days >= 365 and days % 365 == 0:
        return f"{int(days // 365)}y"
    if days >= 30 and days % 30 == 0:
        return f"{int(days // 30)}mo"
    if days % 7 == 0:
        return f"{int(days // 7)}w"
    return f"{days:g}d"


# ------------------------------------------------------------------ #
# Built-in rules — shipped with the app, always available              #
# ------------------------------------------------------------------ #

BUILTIN_RULES: list[Rule] = [
    Rule(name="App discard folders",
         why="an app's own throw-away pile; recreated on demand",
         type="dir", name_glob=(".trash", ".trashes"), min_size=parse_size("1MB")),
    Rule(name="Caches",
         why="regenerable, but some entries cost a re-download",
         type="dir", name_glob=("caches", "cache", "cacheddata",
                                "code cache", "cachedextensionvsixs"),
         min_size=parse_size("10MB")),
    Rule(name="Build artifacts",
         why="rebuilt by the toolchain from source",
         type="dir", name_glob=("node_modules", "__pycache__", ".venv",
                                "venv", "build", "dist", "target",
                                ".pytest_cache", ".mypy_cache"),
         min_size=parse_size("10MB")),
    Rule(name="Big and old",
         why=">100 MB and untouched for over a year",
         min_size=parse_size("100MB"), older_than=365.0),
    Rule(name="Installers and archives",
         why="you already installed it; the installer is dead weight",
         name_glob=("*.dmg", "*.pkg", "*.iso", "*.zip", "*.tar.gz",
                    "*.tgz", "*.xz"),
         min_size=parse_size("50MB")),
    Rule(name="Big media",
         why="not junk by itself — but this is where the space is",
         name_glob=("*.mov", "*.mp4", "*.mkv", "*.avi", "*.m4v", "*.wav",
                    "*.raw", "*.tiff"),
         min_size=parse_size("100MB")),
    Rule(name="Logs",
         why="diagnostics; apps rotate or recreate them",
         name_glob=("*.log", "*.log.*"), min_size=parse_size("1MB")),
]

# ------------------------------------------------------------------ #
# The user's rules file                                               #
# ------------------------------------------------------------------ #

FILE_HEADER = '''\
# StorageMark rules — your own "what should I look at?" queries.
#
# Rules here are ADDED to the ones built into StorageMark. Give a rule the
# same `name` as a built-in to replace it.
#
# Conditions (all optional) combine with AND; a list within one condition
# means "any of these":
#
#   path_contains = ["/.trash/"]     plain text anywhere in the full path
#   path_glob     = ["*/Cursor/*"]   glob on the full path ('*' crosses '/')
#   name_glob     = ["*.dmg"]        glob on the file or folder name
#   min_size      = "100MB"          size; for a folder, its whole contents
#   max_size      = "2GB"
#   older_than    = "1y"             untouched for longer than this
#   newer_than    = "30d"
#   type          = "file"           "file" (default), "dir" or "any"
#   time_field    = "mtime"          which timestamp older/newer_than uses
#
# Matching ignores case. Example — the folders an editor keeps its
# superseded downloads in:
#
# [[rule]]
# name = "Cursor superseded extensions"
# why  = "old versions of extensions the editor already replaced"
# type = "dir"
# path_contains = ["/cursor/cachedextensionvsixs/"]
# name_glob = [".trash"]
# min_size = "10MB"

'''


def rules_file_path() -> Path:
    """~/.config/storagemark/rules.toml on both platforms — easier to find
    and edit than ~/Library/Application Support. (Deliberately different
    from scanner.py's cache dir, which must be platform-split.)"""
    base = os.environ.get("STORAGEMARK_CONFIG_DIR")
    if base:
        return Path(base) / "rules.toml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "storagemark" / "rules.toml"


def ensure_rules_file(path: Path | None = None) -> Path | None:
    """Create the file with explanatory comments if it does not exist.

    Never overwrites. Returns the path, or None if it could not be
    written (a read-only home, say) — built-ins still work in that case.
    """
    path = path or rules_file_path()
    try:
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FILE_HEADER)
        return path
    except OSError:
        return None


def load_rules(path: Path | None = None) -> tuple[list[Rule], list[str]]:
    """Built-ins plus the user's file.

    Returns (rules, problems). A broken file or a broken rule never
    raises: the problems are reported and the remaining rules still work.
    """
    path = path or rules_file_path()
    rules = list(BUILTIN_RULES)
    problems: list[str] = []

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return rules, problems
    except OSError as e:
        return rules, [f"{path}: {e}"]

    try:
        data = tomllib.loads(raw.decode("utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return rules, [f"{path}: {e}"]

    entries = data.get("rule", [])
    if isinstance(entries, dict):          # a single [rule] table
        entries = [entries]
    if not isinstance(entries, list):
        return rules, [f"{path}: 'rule' must be a list of [[rule]] blocks"]

    by_name = {r.name.lower(): i for i, r in enumerate(rules)}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            problems.append(f"{path}: rule #{index} is not a table")
            continue
        try:
            rule = Rule.from_dict(entry)
        except RuleError as e:
            label = entry.get("name", f"#{index}") if isinstance(entry, dict) else f"#{index}"
            problems.append(f"{path}: rule {label}: {e}")
            continue
        existing = by_name.get(rule.name.lower())
        if existing is None:               # new rule
            by_name[rule.name.lower()] = len(rules)
            rules.append(rule)
        else:                              # same name replaces the built-in
            rules[existing] = rule
    return rules, problems


def append_rule(rule: Rule, path: Path | None = None) -> Path:
    """Add a rule to the user's file, preserving what is already there.

    Appends rather than rewriting so comments and formatting survive.
    """
    path = path or rules_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else FILE_HEADER
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + "\n" + rule.to_toml())
    return path


# ------------------------------------------------------------------ #
# Bulk evaluation                                                     #
# ------------------------------------------------------------------ #

@dataclass
class RuleResult:
    rule: Rule
    nodes: list[FileNode] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.nodes)

    @property
    def total_size(self) -> int:
        return sum(n.display_size for n in self.nodes)


def evaluate(rules: Iterable[Rule], nodes: Iterable[FileNode],
             now: datetime | None = None) -> list[RuleResult]:
    """Match every rule in a single pass over the nodes.

    One pass, N predicates — not N passes. Nested matches are kept as
    they are; the removal path already collapses them (top_level_roots).
    """
    now = now or datetime.now()
    results = [RuleResult(rule) for rule in rules]
    pairs = [(r.rule.predicate(now), r) for r in results]
    for node in nodes:
        for predicate, result in pairs:
            if predicate(node):
                result.nodes.append(node)
    return results
