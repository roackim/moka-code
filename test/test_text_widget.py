import pytest

from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.text import Label


def row_text(buffer, row):
    return "".join(cell.char for cell in buffer.cells[row])


def test_label_wraps_text_and_reports_wrapped_height():
    label = Label("one two three", wrap=True)
    label.set_layout(0, 0, 7, 3)
    buffer = Buffer(7, 3)

    label.render(buffer)

    assert [row_text(buffer, row) for row in range(3)] == [
        "one two",
        "three  ",
        "       ",
    ]
    assert label.get_preferred_height(7) == 2


def test_label_aligns_text_within_its_layout():
    label = Label("hi", horizontal="center", vertical="center")
    label.set_layout(0, 0, 6, 3)
    buffer = Buffer(6, 3)

    label.render(buffer)

    assert row_text(buffer, 0) == "      "
    assert row_text(buffer, 1) == "  hi  "
    assert row_text(buffer, 2) == "      "


def test_label_rejects_unknown_alignment_policies():
    with pytest.raises(ValueError):
        Label("text", horizontal="diagonal")
    with pytest.raises(ValueError):
        Label("text", vertical="middle")