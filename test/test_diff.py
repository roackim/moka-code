"""``/diff``: git-only, read-only review of uncommitted changes in $EDITOR
(2026-10-01). Real temporary repositories: git is the thing being driven."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from moka_code.harness import changes
from moka_code.ui.commands import diff as diff_module
from moka_code.ui.commands.registry import COMMANDS, get_command_descriptions
from moka_code.ui.tui.components.menu import cell_text

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path, commit=True):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    if commit:
        (root / "a.py").write_text("one\ntwo\n")
        (root / "gone.txt").write_text("x\n")
        (root / "sp ace.txt").write_text("s\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init")
    return root


def _dirty(root):
    (root / "a.py").write_text("one\nTWO\nthree\n")
    (root / "gone.txt").unlink()
    (root / "new.md").write_text("n1\nn2\n")
    (root / "sp ace.txt").write_text("s\nt\n")


@pytest.fixture(autouse=True)
def _fresh_cache():
    changes._cache.clear()


def test_changes_lists_modified_deleted_untracked_with_stats(tmp_path):
    root = _repo(tmp_path)
    _dirty(root)

    found = {c.path: (c.status, c.stats()) for c in changes.changes(str(root))}

    assert found == {
        "a.py": ("M", "+2 −1"), "gone.txt": ("D", "+0 −1"),
        "new.md": ("??", "+2 −0"), "sp ace.txt": ("M", "+1 −0")}


def test_a_clean_repo_has_no_changes_and_a_plain_folder_is_not_a_repo(tmp_path):
    assert changes.changes(str(_repo(tmp_path))) == []
    plain = tmp_path / "plain"
    plain.mkdir()
    assert changes.changes(str(plain)) is None


def test_a_repository_without_commits_lists_everything(tmp_path):
    root = _repo(tmp_path, commit=False)
    (root / "x.txt").write_text("x\n")
    (root / "y.txt").write_text("y\n")
    _git(root, "add", "y.txt")

    found = {c.path: c.status for c in changes.changes(str(root))}

    assert found == {"x.txt": "??", "y.txt": "A"}
    assert "+y" in changes.diff_text(str(root), "y.txt")


def test_binary_files_are_listed_without_line_counts(tmp_path):
    root = _repo(tmp_path)
    (root / "blob.bin").write_bytes(b"\0\1\2")
    (root / "a.py").write_bytes(b"\0binary now")

    stats = {c.path: c.stats() for c in changes.changes(str(root))}

    assert stats["blob.bin"] == "binary" and stats["a.py"] == "binary"


def test_a_workspace_inside_the_repo_sees_only_its_own_changes(tmp_path):
    root = _repo(tmp_path)
    (root / "pkg").mkdir()
    (root / "pkg" / "in.py").write_text("a\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "pkg")
    (root / "pkg" / "in.py").write_text("a\nb\n")
    (root / "pkg" / "new.py").write_text("n\n")
    (root / "a.py").write_text("outside\n")

    found = {c.path: c.status for c in changes.changes(str(root / "pkg"))}
    text = changes.diff_text(str(root / "pkg"))

    assert found == {"in.py": "M", "new.py": "??"}
    assert {c.path: c.stats() for c in changes.changes(str(root / "pkg"))}["in.py"] == "+1 −0"
    assert "in.py" in text and "new.py" in text and "a.py" not in text


def test_diff_text_for_one_file_for_an_untracked_one_and_for_all(tmp_path):
    root = _repo(tmp_path)
    _dirty(root)

    assert "+TWO" in changes.diff_text(str(root), "a.py")
    assert "-two" in changes.diff_text(str(root), "a.py") and "n1" not in changes.diff_text(str(root), "a.py")
    assert "+n1" in changes.diff_text(str(root), "new.md")
    everything = changes.diff_text(str(root))
    assert all(token in everything for token in ("+TWO", "-x", "+n1", "+t"))
    assert "\x1b[" not in everything                      # no colour codes


def test_cached_changes_are_reused_for_a_few_seconds(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(changes.time, "monotonic", lambda: clock[0])
    assert changes.cached_changes(str(root)) == []
    (root / "late.txt").write_text("x\n")

    assert changes.cached_changes(str(root)) == []        # still the cached answer
    clock[0] += changes._CACHE_SECONDS + 1
    assert [c.path for c in changes.cached_changes(str(root))] == ["late.txt"]


# -- the command ------------------------------------------------------------------------

class _UI:
    def __init__(self, workspace):
        self.agent = SimpleNamespace(workspace=str(workspace))
        self.messages, self.picker = [], None
        self.chat_history_panel = SimpleNamespace(
            add_message=lambda text, **k: self.messages.append(text))

    def show_search_modal(self, title, items, descriptions=None, footers=None,
                          on_accept=None, ordered=False, **k):
        self.picker = {"title": title, "items": list(items), "descriptions": descriptions,
                       "on_accept": on_accept, "ordered": ordered}
        return object()


@pytest.fixture
def editor(monkeypatch, tmp_path):
    """A stand-in editor that records what it was opened on."""
    monkeypatch.setenv("EDITOR", "my-editor")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    opened = []

    async def _open(ui, path):
        path = Path(path)
        opened.append({"name": path.name, "text": path.read_text(),
                       "writable": os.access(path, os.W_OK), "path": path})
        return True

    monkeypatch.setattr("moka_code.ui.external_editor.open_editor", _open)
    return opened


def test_bare_diff_opens_a_picker_and_a_choice_opens_that_diff_read_only(tmp_path, editor):
    root = _repo(tmp_path)
    _dirty(root)
    ui = _UI(root)

    async def scenario():
        await COMMANDS["diff"].execute(ui, [])
        assert ui.picker["title"] == "Changes" and ui.picker["ordered"]
        assert ui.picker["items"] == ["(all changes)", "a.py", "gone.txt", "new.md", "sp ace.txt"]
        assert [cell_text(ui.picker["descriptions"][n]) for n in ("a.py", "gone.txt", "new.md")] == [
            "modified  +2 −1", "deleted   +0 −1", "new       +2 −0"]
        assert ui.picker["descriptions"]["(all changes)"] == "4 files"
        ui.picker["on_accept"]("a.py")
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        ui.picker["on_accept"]("(all changes)")
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

    asyncio.run(scenario())

    one, everything = editor
    assert one["name"] == "a.py.diff" and "+TWO" in one["text"] and "n1" not in one["text"]
    assert everything["name"] == "all.diff" and "+n1" in everything["text"]
    assert not one["writable"] and not everything["writable"]      # a view, not a file to edit
    assert not one["path"].exists() and not everything["path"].exists()   # removed afterwards


def test_diff_with_a_file_opens_it_directly_and_rejects_an_unchanged_one(tmp_path, editor):
    root = _repo(tmp_path)
    _dirty(root)
    ui = _UI(root)

    asyncio.run(COMMANDS["diff"].execute(ui, ["sp ace.txt"]))
    asyncio.run(COMMANDS["diff"].execute(ui, ["nothing.py"]))

    assert [e["name"] for e in editor] == ["sp ace.txt.diff"]
    assert ui.picker is None and ui.messages == ["No changes in nothing.py."]


def test_diff_says_why_when_there_is_nothing_to_show(tmp_path, editor, monkeypatch):
    ui = _UI(_repo(tmp_path))
    asyncio.run(COMMANDS["diff"].execute(ui, []))
    plain = tmp_path / "plain"
    plain.mkdir()
    plain_ui = _UI(plain)
    asyncio.run(COMMANDS["diff"].execute(plain_ui, []))
    (tmp_path / "other").mkdir()
    root = _repo(tmp_path / "other")
    (root / "a.py").write_text("changed\n")
    monkeypatch.delenv("EDITOR")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    no_editor = _UI(root)
    asyncio.run(COMMANDS["diff"].execute(no_editor, []))

    assert ui.messages == ["No uncommitted changes."]
    assert plain_ui.messages == ["Not a git repository (or git is not installed)."]
    assert no_editor.messages == ["No editor found. Set $VISUAL or $EDITOR."]
    assert editor == []


# -- discoverability: the / menu says how many files changed ---------------------------

def test_the_command_menu_shows_a_live_count_and_the_file_suggestions(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setattr(diff_module, "current_workspace", lambda: str(root))

    assert get_command_descriptions()["diff"] == "Review uncommitted changes (opens a picker)"
    (root / "a.py").write_text("changed\n")
    (root / "new.md").write_text("n\n")
    changes._cache.clear()

    assert get_command_descriptions()["diff"] == "Review changes · 2 files"
    assert COMMANDS["diff"].get_completions(0) == ["a.py", "new.md"]
    assert cell_text(COMMANDS["diff"].get_descriptions(0)["new.md"]) == "new       +1 −0"


def test_the_slash_menu_asks_for_descriptions_each_time_it_opens(tmp_path, monkeypatch):
    from moka_code.ui.tui.components.input.input import InputComponent

    root = _repo(tmp_path)
    monkeypatch.setattr(diff_module, "current_workspace", lambda: str(root))
    inp = InputComponent("> ")
    inp.set_layout(0, 0, 80, 3)
    inp.setup_commands(sorted(COMMANDS), get_command_descriptions)
    inp.setup_command_registry(COMMANDS)

    for char in "/dif":
        inp.handle_input(char)
    assert inp.command_completion.menu.item_descriptions["diff"].startswith("Review uncommitted")

    (root / "a.py").write_text("changed\n")
    changes._cache.clear()
    inp.clear()
    for char in "/dif":
        inp.handle_input(char)
    assert inp.command_completion.menu.item_descriptions["diff"] == "Review changes · 1 file"


def test_the_picker_aligns_its_columns_and_colours_status_and_numbers(tmp_path):
    """Rendered: a status word (never ``??``), then +added −removed in columns,
    green / red / amber by meaning, a zero count muted."""
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.colors import theme
    from moka_code.ui.tui.components.menu import SelectionMenu

    root = _repo(tmp_path)
    _dirty(root)
    (root / "a.py").write_text("\n".join(str(n) for n in range(120)) + "\n")     # +120 −2
    cells = diff_module.rows(changes.changes(str(root)))
    menu = SelectionMenu()
    menu.set_fill_width(True)
    menu.set_layout(0, 0, 70, 8)
    menu.set_items(list(cells), descriptions=cells)
    buffer = Buffer(70, 8)

    menu.render(buffer)

    lines = ["".join(cell.char for cell in row) for row in buffer.cells[1:5]]
    assert "??" not in "".join(lines)
    assert len({line.index("−") for line in lines}) == 1               # the columns line up
    assert cell_text(cells["a.py"]).endswith("+120 −2")
    assert cell_text(cells["gone.txt"]).endswith("  +0 −1")             # right-aligned
    row = {name: buffer.cells[1 + i] for i, name in enumerate(cells)}

    def colour_at(name, text):
        line = "".join(cell.char for cell in row[name])
        return row[name][line.index(text, len(name))].fg

    assert colour_at("a.py", "modified") == theme.WARNING
    assert colour_at("gone.txt", "deleted") == theme.ERROR
    assert colour_at("new.md", "new") == theme.SUCCESS
    assert colour_at("a.py", "+120") == theme.SUCCESS and colour_at("a.py", "−2") == theme.ERROR
    assert colour_at("new.md", "−0") == theme.MUTED           # nothing removed: quiet


def test_diff_context_lines_function_or_whole_file(tmp_path):
    """``context.toml`` ``diff_context``: N lines around a change, the enclosing
    function, or the whole file with the change marked in place."""
    root = _repo(tmp_path, commit=False)
    # git finds the "function" by the last unindented line, so the filler is indented.
    body = "class Filler:\n" + "".join(f"    x{n} = {n}\n" for n in range(40))
    (root / "m.py").write_text(
        "def first():\n    return 1\n\n" + body + "\ndef target():\n" + "".join(f"    a{n} = {n}\n" for n in range(8)) + "    b = 2\n"
        + "".join(f"    c{n} = {n}\n" for n in range(8)) + "    return a0\n\n"
        + body + "\ndef last():\n    return 3\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "m")
    text = (root / "m.py").read_text().replace("    b = 2\n", "    b = 22\n")
    (root / "m.py").write_text(text)

    narrow = changes.diff_text(str(root), "m.py", 1)
    default = changes.diff_text(str(root), "m.py")
    function = changes.diff_text(str(root), "m.py", "function")
    whole = changes.diff_text(str(root), "m.py", "all")

    assert "-    b = 2" in narrow and "+    b = 22" in narrow
    assert "\n def target():" not in narrow           # (the @@ header names it, but no line)
    assert "\n def target():" in function and "\n     return a0" in function
    assert "\n def first():" not in function and "x39 = 39" not in function.split("def target")[0]
    assert "\n def first():" in whole and "\n def last():" in whole and "    x39 = 39" in whole
    assert len(narrow) < len(default) < len(function) < len(whole)


def test_diff_context_setting_is_validated_and_reaches_the_command(tmp_path, editor, monkeypatch):
    from moka_code import settings as settings_module
    from moka_code.settings import _coerce

    assert [_coerce("diff_context", v)[1] for v in (0, 7, "function", "all")] == [None] * 4
    assert all(_coerce("diff_context", v)[1] for v in (-1, True, "some", 1.5))

    root = _repo(tmp_path)
    (root / "a.py").write_text("one\nTWO\n")
    ui = _UI(root)
    asyncio.run(COMMANDS["diff"].execute(ui, ["a.py"]))
    monkeypatch.setattr(settings_module.config, "context_diff_context", "all")
    asyncio.run(COMMANDS["diff"].execute(ui, ["a.py"]))

    assert [e["text"].count("\n") for e in editor][0] <= [e["text"].count("\n") for e in editor][1]
    assert "one" in editor[1]["text"]
