from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.table_view import TableView
from moka_code.ui.tui.terminal import MouseEvent


def row_text(buffer, row):
    return "".join(cell.char for cell in buffer.cells[row])


def test_table_view_measures_columns_and_renders_header_and_rows():
    table = TableView(["Name", "Age"], [["Ada", 36], ["Grace", 28]])
    widths = table.get_column_widths()
    table.set_layout(0, 0, 14, 3)
    buffer = Buffer(14, 3)

    table.render(buffer)

    assert widths == [5, 3]
    assert row_text(buffer, 0).startswith("Name  | Age")
    assert row_text(buffer, 1).startswith("Ada   | 36")


def test_table_view_supports_explicit_widths_and_horizontal_scroll():
    table = TableView(["Name", "Description"], [["A", "long value"]], column_widths=[3, 12])
    assert table.get_column_widths() == [3, 12]
    table.set_layout(0, 0, 8, 2)
    buffer = Buffer(8, 2)

    table.render(buffer)
    before = row_text(buffer, 1)
    table.handle_input("\x1b[C")
    buffer = Buffer(8, 2)
    table.render(buffer)

    assert before != row_text(buffer, 1)


def test_table_view_scrolls_and_selects_rows():
    selected = []
    table = TableView(["Value"], [["one"], ["two"], ["three"]], on_row_select=lambda index, row: selected.append(index))
    table.set_layout(0, 0, 10, 2)

    assert table.handle_input("\x1b[B")
    assert table.vertical_offset == 1
    assert table.handle_input(MouseEvent(2, 1, 0, True))
    assert table.selected_row == 1
    assert selected == [1]