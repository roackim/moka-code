"""Tests for the path fuzzy matcher and the selection menu's new behavior."""

from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.components.menu import SelectionMenu
from moka_chat.ui.tui.fuzzy import fuzzy_match


def test_fuzzy_match_returns_indices_in_order():
    result = fuzzy_match("smp", "src/main.py")
    assert result is not None
    score, indices = result
    assert indices == sorted(indices)
    assert [ "src/main.py"[i] for i in indices ] == ["s", "m", "p"]


def test_fuzzy_match_none_when_not_a_subsequence():
    assert fuzzy_match("xyz", "src/main.py") is None


def test_fuzzy_match_prefers_prefix_and_segments():
    prefix, _ = fuzzy_match("main", "main.py")
    nested, _ = fuzzy_match("main", "deep/dir/main.py")
    assert prefix > nested


def _menu(width=40, height=6):
    menu = SelectionMenu()
    menu.set_layout(0, 0, width, height)
    return menu


def test_menu_selection_is_bold_accent_not_reverse():
    menu = _menu(width=30, height=4)
    menu.frame_color = theme.USER
    menu.content_color = theme.DEFAULT
    menu.set_items(["a.py", "b.py"])
    menu.selected_index = 1
    buffer = Buffer(30, 4)

    menu.render(buffer)

    selected = buffer.cells[2][2]  # row 2 = second item, col 2 = content
    unselected = buffer.cells[1][2]
    assert selected.char == "b"
    assert selected.reverse is False
    assert selected.bold is True
    assert selected.fg == theme.USER
    assert unselected.fg == theme.DEFAULT


def test_menu_fill_width_spans_buffer():
    menu = SelectionMenu()
    menu.set_fill_width(True)
    menu.set_layout(0, 0, 40, 4)
    menu.set_items(["x"])
    buffer = Buffer(40, 4)

    menu.render(buffer)

    assert buffer.cells[0][39].char == "┐"  # top-right corner at the last column


def test_action_bar_align_right_flushes_to_right_edge():
    from moka_chat.ui.tui.components.bars import ActionBar, ActionItem

    bar = ActionBar([ActionItem("c", "copy", None)])
    bar.set_align_right(True)
    bar.set_layout(0, 0, 24, 1)
    buffer = Buffer(24, 1)

    bar.render(buffer)

    row = "".join(cell.char for cell in buffer.cells[0])
    assert row.rstrip().endswith("[c] copy")
    assert row.startswith(" ")  # left side is blank


def test_menu_renders_muted_aligned_descriptions():
    menu = _menu(width=70, height=5)
    menu.set_items(["/model", "/import"], descriptions={
        "/model": "pick a model",
        "/import": "load a file",
    })
    buffer = Buffer(70, 5)

    menu.render(buffer)

    row = "".join(cell.char for cell in buffer.cells[1])
    assert "/model" in row
    assert "pick a model" in row
    # Descriptions are aligned into a column and drawn in the muted color.
    desc_col = row.index("pick a model")
    assert buffer.cells[1][desc_col].fg == theme.MUTED
    # The name column is padded so both descriptions start at the same x.
    import_row = "".join(cell.char for cell in buffer.cells[2])
    assert import_row.index("load a file") == desc_col


def test_menu_scrolls_to_keep_selection_visible():
    menu = _menu(height=5)
    items = [f"file{i:02d}.py" for i in range(30)]
    menu.set_items(items)
    menu.selected_index = 20
    buffer = Buffer(40, 5)

    menu.render(buffer)

    rendered = "".join(cell.char for row in buffer.cells for cell in row)
    assert "file20.py" in rendered
    assert menu.items[menu.selected_index] == "file20.py"


def test_menu_clears_its_background():
    """The popup must overwrite whatever is behind it (e.g. the action bar)."""
    menu = SelectionMenu()
    menu.set_layout(0, 0, 40, 4)
    menu.set_items(["a"])

    buffer = Buffer(40, 4)
    for y in range(4):
        buffer.write_str(0, y, "X" * 40)  # sentinel background

    menu.render(buffer)

    # No sentinel survived inside the popup rectangle (15 columns here).
    for y in range(3):
        row = "".join(cell.char for cell in buffer.cells[y])
        assert "X" not in row[:15], row
    assert buffer.cells[1][2].char == "a"


def test_menu_draws_normal_box_corners():
    menu = _menu(width=40, height=4)
    menu.set_items(["a"])

    buffer = Buffer(40, 4)
    menu.render(buffer)
    assert buffer.cells[0][0].char == "┌"
    assert buffer.cells[0][14].char == "┐"
    assert buffer.cells[1][0].char == "│"
    assert buffer.cells[2][0].char == "└"
    assert buffer.cells[2][14].char == "┘"
