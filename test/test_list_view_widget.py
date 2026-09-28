from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.list_view import ListView, Select, SelectionModel
from moka_code.ui.tui.terminal import MouseEvent


def rows(buffer):
    return ["".join(cell.char for cell in row) for row in buffer.cells]


def test_selection_model_tracks_items_and_selection():
    model = SelectionModel(["a", "b", "c"], selected=1)

    assert model.selected == "b"
    assert model.move(1)
    assert model.selected == "c"
    assert not model.move(1)
    assert model.select(0)
    assert model.selected == "a"


def test_list_view_navigates_scrolls_and_activates():
    selected = []
    view = ListView(["one", "two", "three"], on_select=selected.append)
    view.set_layout(0, 0, 8, 2)

    assert view.handle_input("\x1b[B")
    assert view.model.selected == "two"
    assert view.handle_input("\x1b[B")
    assert view.model.selected == "three"
    assert view.scroll_offset == 1
    assert view.handle_input("\r")
    assert selected == ["three"]
    assert view.handle_input(MouseEvent(2, 0, 65, True))


def test_list_view_mouse_selects_visible_row():
    selected = []
    view = ListView(["one", "two", "three"], on_select=selected.append)
    view.set_layout(2, 4, 8, 3)

    assert view.handle_input(MouseEvent(3, 5, 0, True))
    assert view.model.selected == "two"
    assert selected == ["two"]


def test_select_toggles_and_selects_from_inline_list():
    selected = []
    select = Select(["red", "green", "blue"], on_select=selected.append)
    select.set_layout(0, 0, 12, 4)

    assert select.handle_input("\r")
    assert select.open
    assert select.handle_input(MouseEvent(2, 2, 0, True))
    assert select.model.selected == "green"
    assert selected == ["green"]


def test_list_view_renders_selected_row():
    view = ListView(["one", "two"])
    view.set_focused(True)
    view.set_layout(0, 0, 8, 2)
    buffer = Buffer(8, 2)

    view.render(buffer)

    assert rows(buffer)[0].startswith("one")
    assert all(cell.reverse for cell in buffer.cells[0][:3])