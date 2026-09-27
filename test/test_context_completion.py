"""Tests for ContextCompletion subdirectory drilling and Tab path building."""

from moka_chat.ui.tui.components.input.completion import ContextCompletion
from moka_chat.ui.tui.components.menu import SelectionMenu


def make_completion(items):
    menu = SelectionMenu()
    return ContextCompletion(menu, lambda: items)


def _update(comp, text):
    comp.update(text, len(text))


def _accept(comp, text):
    return comp.accept_selection(text, len(text))


def test_lists_top_level_by_default():
    comp = make_completion(["src/", "src/a.py", "README.md"])
    _update(comp, "@")
    assert comp.is_active
    # Top-level listing: directories first, then files, alphabetically.
    assert comp.menu.items == ["src/", "README.md", "src/a.py"]


def test_drills_into_directory_with_trailing_slash():
    comp = make_completion(["src/", "src/a.py", "src/sub/", "src/sub/b.py", "README.md"])
    _update(comp, "@src/")
    assert comp.is_active
    # Immediate children of src/ (dirs first), "../" at the END so it is never
    # the default highlight.
    assert comp.menu.items == ["src/sub/", "src/a.py", "../"]
    assert comp.menu.get_selected() == "src/sub/"


def test_trigger_only_at_word_boundary():
    comp = make_completion(["src/", "README.md"])
    # Inside a word (e.g. an email) the picker must not fire.
    _update(comp, "mail me at foo@ba")
    assert comp.is_active is False
    # After whitespace it fires and sees the text typed so far.
    _update(comp, "see @RE")
    assert comp.is_active is True


def test_accept_selection_preserves_directory_prefix():
    comp = make_completion(["src/", "src/a.py", "src/sub/", "src/sub/b.py"])
    _update(comp, "@src/")
    # Select "src/sub/" (index 0: dirs sort first, before "../").
    comp.menu.selected_index = 0
    new_text, new_cursor = _accept(comp, "@src/")
    assert new_text == "@src/sub/"
    assert new_cursor == len("@src/sub/")


def test_accept_selection_top_level():
    comp = make_completion(["src/", "README.md"])
    _update(comp, "@")
    comp.menu.selected_index = 1  # README.md
    new_text, new_cursor = _accept(comp, "@")
    assert new_text == "@README.md"
    assert new_cursor == len("@README.md")


def test_no_children_returns_top_level():
    comp = make_completion(["src/", "README.md"])
    # "nonexistent/" has no children under the listing.
    _update(comp, "@nonexistent/")
    # Falls back to the top-level listing.
    assert comp.menu.items == ["src/", "README.md"]


def test_partial_path_stays_inside_directory():
    comp = make_completion(["src/", "src/a.py", "src/sub/", "src/sub/b.py", "README.md"])
    # Typing "@src/a" (no trailing slash) should still filter within src/.
    _update(comp, "@src/a")
    assert comp.menu.items == ["src/a.py", "../"]


def test_accept_directory_no_double_slash():
    comp = make_completion(["src/", "src/a.py", "src/sub/", "src/sub/b.py"])
    _update(comp, "@src/")
    comp.menu.selected_index = 0  # src/sub/ (dirs first)
    new_text, _ = _accept(comp, "@src/")
    # accept_selection inserts the full path — no double slash.
    assert new_text == "@src/sub/"
    assert "//" not in new_text


def test_accept_nested_file_no_double_prefix():
    # Regression: @notes/ + TAB on notes/doc.md must not become
    # @notes/notes/doc.md.
    comp = make_completion(["notes/", "notes/doc.md", "README.md"])
    _update(comp, "@notes/doc")
    comp.menu.selected_index = 0  # notes/doc.md
    new_text, _ = _accept(comp, "@notes/doc")
    assert new_text == "@notes/doc.md"


def test_accept_with_cursor_midword_replaces_whole_word():
    # Regression: TAB with the cursor mid-word left the trailing letters, e.g.
    # "@src/asomeletters" + TAB -> "@src/a.pysomeletters".
    comp = make_completion(["src/a.py"])
    text = "@src/asomeletters"
    cursor = len("@src/a")
    comp.update(text, cursor)
    comp.menu.selected_index = 0

    new_text, new_cursor = comp.accept_selection(text, cursor)

    assert new_text == "@src/a.py"
    assert new_cursor == len("@src/a.py")


def test_accept_parent_navigates_up_from_single_level():
    comp = make_completion(["src/", "src/a.py", "src/sub/", "src/sub/b.py"])
    _update(comp, "@src/")
    comp.menu.selected_index = 2  # ../ (last)
    new_text, _ = _accept(comp, "@src/")
    assert new_text == "@"


def test_accept_parent_navigates_up_from_nested():
    comp = make_completion(["src/", "src/sub/", "src/sub/b.py"])
    _update(comp, "@src/sub/")
    comp.menu.selected_index = 1  # ../ (last)
    new_text, _ = _accept(comp, "@src/sub/")
    assert new_text == "@src/"
