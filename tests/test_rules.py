"""Rules engine: parsing, matching, the rules file, bulk evaluation.

No UI — these run against small real directory trees built in tmp.
"""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from storagemark.python.model import DirTree, FileNode
from storagemark.python.rules import (BUILTIN_RULES, LINUX_RULES, Rule, RuleError,
                                      append_rule, ensure_rules_file,
                                      evaluate, load_rules, parse_days,
                                      parse_size)
from storagemark.python.scanner import scan


def build_tree(root: Path) -> DirTree:
    return DirTree.build(scan(str(root)))


def node_named(tree: DirTree, name: str):
    return next(n for n in tree.flat if n.name == name)


# ------------------------------------------------------------------ #
# Parsing                                                             #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("text,want", [
    ("100MB", 100 * (1 << 20)), ("1GB", 1 << 30), ("512KB", 512 * 1024),
    ("2.5MB", int(2.5 * (1 << 20))), ("4096", 4096), (1234, 1234),
    ("10 mb", 10 * (1 << 20)),
])
def test_parse_size(text, want):
    assert parse_size(text) == want


@pytest.mark.parametrize("text,want", [
    ("90d", 90.0), ("1y", 365.0), ("6mo", 180.0), ("2w", 14.0), ("30", 30.0),
])
def test_parse_days(text, want):
    assert parse_days(text) == want


def test_parse_rejects_nonsense():
    with pytest.raises(RuleError):
        parse_size("many")
    with pytest.raises(RuleError):
        parse_days("soon")


def test_rule_validation():
    with pytest.raises(RuleError):
        Rule.from_dict({})                                # no name
    with pytest.raises(RuleError):
        Rule.from_dict({"name": "x", "type": "folder"})   # bad type
    with pytest.raises(RuleError):
        Rule.from_dict({"name": "x", "whoops": 1})        # unknown key
    with pytest.raises(RuleError):
        Rule.from_dict({"name": "x", "path_contains": 5})  # wrong shape
    r = Rule.from_dict({"name": " Trim ", "path_contains": "/A/",
                        "min_size": "1MB", "older_than": "1y"})
    assert r.name == "Trim"
    assert r.path_contains == ("/a/",)      # lowered for matching
    assert r.min_size == 1 << 20 and r.older_than == 365.0


# ------------------------------------------------------------------ #
# Matching — the motivating case                                      #
# ------------------------------------------------------------------ #

@pytest.fixture()
def cursor_tree(tmp_path):
    """Reconstruct the case from spec §15: an app's .trash folder holding
    superseded installers, all of them recent."""
    trash = tmp_path / "Application Support" / "Cursor" / "CachedExtensionVSIXs" / ".trash"
    trash.mkdir(parents=True)
    for version in ("26.908.40401", "26.908.31748", "26.901.22334"):
        (trash / f"openai.chatgpt-{version}-darwin-arm64").write_bytes(b"x" * 400_000)
    keep = tmp_path / "Documents"
    keep.mkdir()
    (keep / "taxes.pdf").write_bytes(b"y" * 300_000)
    return build_tree(tmp_path)


def test_dir_rule_catches_trash_folder(cursor_tree):
    """A dir rule matches the folder itself — one item, whole subtree size."""
    rule = Rule(name="t", type="dir", name_glob=(".trash",),
                min_size=parse_size("1MB"))
    hits = [n for n in cursor_tree.flat if rule.matches(n)]
    assert len(hits) == 1
    assert hits[0].name == ".trash"
    assert hits[0].display_size >= 1_200_000      # its contents, not the inode
    assert "taxes.pdf" not in [n.name for n in hits]


def test_min_size_on_dir_uses_subtree(cursor_tree):
    """The folder's own size is tiny; the rule must see what it holds."""
    folder = node_named(cursor_tree, ".trash")
    assert folder.size_disk < 100_000          # the directory entry itself
    assert folder.display_size > 1_000_000     # what min_size compares to


def test_age_rule_misses_the_case(cursor_tree):
    """The reason patterns must stand alone: these files are all new, so an
    age test finds nothing (spec §15)."""
    age_only = Rule(name="old", min_size=parse_size("100KB"), older_than=90.0)
    assert [n for n in cursor_tree.flat if age_only.matches(n)] == []

    pattern = Rule(name="pat", type="dir", name_glob=(".trash",),
                   min_size=parse_size("1MB"))
    assert len([n for n in cursor_tree.flat if pattern.matches(n)]) == 1


def test_path_contains_and_glob_are_case_insensitive(cursor_tree):
    lower = Rule.from_dict({"name": "a", "path_contains": ["/cachedextensionvsixs/"]})
    upper = Rule.from_dict({"name": "b", "path_contains": ["/CachedExtensionVSIXs/"]})
    n_lower = sum(1 for n in cursor_tree.flat if lower.matches(n))
    n_upper = sum(1 for n in cursor_tree.flat if upper.matches(n))
    assert n_lower == n_upper > 0

    glob = Rule.from_dict({"name": "c", "path_glob": ["*/CURSOR/*"]})
    assert sum(1 for n in cursor_tree.flat if glob.matches(n)) > 0


def test_name_glob_and_size_window(tmp_path):
    (tmp_path / "big.dmg").write_bytes(b"x" * 900_000)
    (tmp_path / "small.dmg").write_bytes(b"x" * 1_000)
    (tmp_path / "big.txt").write_bytes(b"x" * 900_000)
    tree = build_tree(tmp_path)
    rule = Rule.from_dict({"name": "installers", "name_glob": ["*.dmg"],
                           "min_size": "100KB"})
    names = sorted(n.name for n in tree.flat if rule.matches(n))
    assert names == ["big.dmg"]


def test_older_than_uses_mtime(tmp_path):
    old = tmp_path / "ancient.bin"
    old.write_bytes(b"x" * 200_000)
    two_years = time.time() - 730 * 86400
    os.utime(old, (two_years, two_years))
    (tmp_path / "fresh.bin").write_bytes(b"x" * 200_000)
    tree = build_tree(tmp_path)
    rule = Rule.from_dict({"name": "old", "older_than": "1y",
                           "min_size": "100KB"})
    assert [n.name for n in tree.flat if rule.matches(n)] == ["ancient.bin"]


# ------------------------------------------------------------------ #
# The rules file                                                      #
# ------------------------------------------------------------------ #

def test_builtins_load_without_a_file(tmp_path):
    rules, problems = load_rules(tmp_path / "absent.toml")
    assert problems == []
    assert [r.name for r in rules] == [r.name for r in BUILTIN_RULES]


def test_user_file_adds_and_overrides(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text('''
[[rule]]
name = "My own"
why = "mine"
path_contains = ["/Downloads/"]

[[rule]]
name = "Caches"
why = "replaced the built-in"
type = "dir"
name_glob = ["weirdcache"]
''')
    rules, problems = load_rules(path)
    assert problems == []
    names = [r.name for r in rules]
    assert "My own" in names
    assert names.count("Caches") == 1           # replaced, not duplicated
    caches = next(r for r in rules if r.name == "Caches")
    assert caches.why == "replaced the built-in"
    assert caches.name_glob == ("weirdcache",)


def test_broken_file_is_reported_not_fatal(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text("this is not toml [[[")
    rules, problems = load_rules(path)
    assert len(problems) == 1 and str(path) in problems[0]
    assert len(rules) == len(BUILTIN_RULES)     # built-ins still usable


def test_one_broken_rule_does_not_sink_the_others(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text('''
[[rule]]
name = "Good"
path_contains = ["/tmp/"]

[[rule]]
name = "Bad"
min_size = "a handful"

[[rule]]
name = "Also good"
name_glob = ["*.iso"]
''')
    rules, problems = load_rules(path)
    assert len(problems) == 1 and "Bad" in problems[0]
    names = [r.name for r in rules]
    assert "Good" in names and "Also good" in names and "Bad" not in names


def test_ensure_file_creates_once_and_never_overwrites(tmp_path):
    path = tmp_path / "sub" / "rules.toml"
    assert ensure_rules_file(path) == path
    assert "StorageMark rules" in path.read_text()
    path.write_text("# my edits\n")
    ensure_rules_file(path)
    assert path.read_text() == "# my edits\n"   # untouched


def test_append_rule_round_trips(tmp_path):
    path = tmp_path / "rules.toml"
    ensure_rules_file(path)
    rule = Rule(name="From the UI", why="built from a path",
                type="dir", path_contains=("/cursor/",),
                name_glob=(".trash",), min_size=parse_size("10MB"))
    append_rule(rule, path)
    rules, problems = load_rules(path)
    assert problems == []
    back = next(r for r in rules if r.name == "From the UI")
    assert back.type == "dir"
    assert back.path_contains == ("/cursor/",)
    assert back.name_glob == (".trash",)
    assert back.min_size == parse_size("10MB")
    assert "StorageMark rules" in path.read_text()   # comments preserved


def test_append_preserves_user_comments(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text("# keep me\n\n[[rule]]\nname = \"Mine\"\n")
    append_rule(Rule(name="Added"), path)
    text = path.read_text()
    assert "# keep me" in text
    rules, problems = load_rules(path)
    assert problems == []
    assert {"Mine", "Added"} <= {r.name for r in rules}


# ------------------------------------------------------------------ #
# Bulk evaluation                                                     #
# ------------------------------------------------------------------ #

def test_evaluate_matches_individual_rules(cursor_tree):
    rules = [Rule(name="trash", type="dir", name_glob=(".trash",),
                  min_size=parse_size("1MB")),
             Rule(name="pdfs", name_glob=("*.pdf",)),
             Rule(name="nothing", name_glob=("*.nope",))]
    results = evaluate(rules, cursor_tree.flat)
    by_name = {r.rule.name: r for r in results}
    assert by_name["trash"].count == 1
    assert by_name["trash"].total_size > 1_000_000
    assert [n.name for n in by_name["pdfs"].nodes] == ["taxes.pdf"]
    assert by_name["nothing"].count == 0
    assert by_name["nothing"].total_size == 0
    # one pass, same answers as matching rule by rule
    for rule in rules:
        direct = [n for n in cursor_tree.flat if rule.matches(n)]
        assert by_name[rule.name].nodes == direct


def test_builtin_rules_are_all_valid():
    """Every built-in must survive a TOML round trip — they double as the
    worked examples users copy."""
    for rule in BUILTIN_RULES + LINUX_RULES:
        assert rule.name and rule.why
        text = rule.to_toml()
        assert text.startswith("[[rule]]")
        import tomllib
        parsed = tomllib.loads(text)["rule"][0]
        again = Rule.from_dict(parsed)
        assert again.name == rule.name
        assert again.type == rule.type
        assert again.name_glob == rule.name_glob
        assert again.path_contains == rule.path_contains


def _synthetic(path: str, type_: str, size: int) -> FileNode:
    epoch = datetime(2020, 1, 1)
    return FileNode(path=path, name=path.rsplit("/", 1)[-1], type=type_,
                    size_bytes=size, size_disk=size, inode=0, dev=0, uid=0,
                    mtime=epoch, atime=epoch, ctime=epoch, depth=0, ext="",
                    hardlink_of="", error="", children=[], parent=None,
                    subtree_bytes=size, subtree_disk=size)


@pytest.mark.parametrize("rule_name,path,type_,size,want", [
    # measured on Ubuntu 24.04: ~/.cache and the journal were missed
    ("Caches", "/home/alex/.cache", "d", 20 << 20, True),
    ("Caches", "/home/alex/.thumbnails", "d", 20 << 20, True),
    ("Caches", "/var/cache", "d", 150 << 20, True),
    ("Desktop Trash", "/home/alex/.local/share/Trash", "d", 5 << 20, True),
    ("Desktop Trash", "/home/alex/.local/share/Trash/files", "d", 5 << 20, False),
    ("Desktop Trash", "/home/alex/Trash", "d", 5 << 20, False),
    ("System journal", "/var/log/journal", "d", 466 << 20, True),
    ("System journal", "/var/log/journal", "d", 10 << 20, False),
    ("System journal", "/var/log/journal/abc/system.journal", "f", 466 << 20, False),
])
def test_linux_locations(rule_name, path, type_, size, want):
    rule = next(r for r in BUILTIN_RULES + LINUX_RULES if r.name == rule_name)
    assert rule.matches(_synthetic(path, type_, size)) is want


def test_linux_rules_offered_only_on_linux():
    names = {r.name for r in BUILTIN_RULES}
    on_linux = sys.platform.startswith("linux")
    assert all((r.name in names) == on_linux for r in LINUX_RULES)


@pytest.mark.parametrize("name", [
    'my "stuff"', "back\\slash", "Movies [YTS.MX]", "tab\there",
    "naïve café", "take*two?",
])
def test_awkward_names_round_trip_through_the_file(tmp_path, name):
    """Names reach to_toml straight off the disk. Quotes and backslashes
    once produced invalid TOML, so the saved rule failed on reload."""
    import glob as globmod
    path = tmp_path / "rules.toml"
    rule = Rule(name=f"Folders named {name}", why=f"folders called {name}",
                type="dir", name_glob=(globmod.escape(name.lower()),))
    append_rule(rule, path)
    rules, problems = load_rules(path)
    assert problems == [], problems
    back = next(r for r in rules if r.name == rule.name)
    assert back.name_glob == rule.name_glob
    assert back.why == rule.why
