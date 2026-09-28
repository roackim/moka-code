from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.choice import Checkbox, RadioGroup
from moka_code.ui.tui.terminal import MouseEvent


def row_text(buffer, row):
    return "".join(cell.char for cell in buffer.cells[row])


def test_checkbox_toggles_and_notifies_changes():
    changes = []
    checkbox = Checkbox("Verbose", on_change=changes.append)
    checkbox.set_layout(0, 0, 12, 1)

    assert checkbox.handle_input(" ")
    assert checkbox.value is True
    assert changes == [True]
    assert checkbox.handle_input(MouseEvent(2, 0, 0, True))
    assert checkbox.value is False
    assert changes == [True, False]


def test_checkbox_disabled_state_ignores_input():
    checkbox = Checkbox("Verbose")
    checkbox.enabled = False
    checkbox.set_layout(0, 0, 12, 1)

    assert checkbox.handle_input(" ") is False
    assert checkbox.value is False


def test_radio_group_navigates_and_selects_one_option():
    changes = []
    group = RadioGroup(["one", "two", "three"], on_change=changes.append)
    group.set_layout(0, 0, 12, 3)

    assert group.handle_input("\x1b[B")
    assert group.cursor == 1
    assert group.value is None
    assert group.handle_input("\r")
    assert group.value == 1
    assert changes == [1]
    assert group.handle_input(MouseEvent(2, 2, 0, True))
    assert group.value == 2
    assert changes == [1, 2]


def test_radio_group_renders_selected_and_focused_rows():
    group = RadioGroup(["one", "two"], value=1)
    group.set_focused(True)
    group.set_layout(0, 0, 10, 2)
    buffer = Buffer(10, 2)

    group.render(buffer)

    assert row_text(buffer, 0).startswith("( ) one")
    assert row_text(buffer, 1).startswith("(x) two")
    assert all(cell.reverse for cell in buffer.cells[1][:7])