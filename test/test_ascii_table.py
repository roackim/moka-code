"""AsciiTable width handling: wide/emoji cells align; no hidden truncation."""

from pico_chat.ui.tui.ascii_table import AsciiTable, TableStyle
from pico_chat.ui.tui.layout_utils import display_width


def _lines(table: AsciiTable):
    return [line for line in table.to_string().split("\n") if line]


def test_wide_and_emoji_cells_keep_columns_aligned():
    table = AsciiTable(
        headers=["name", "emoji", "description"],
        rows=[
            ["\U0001F600 emoji", "short", "a much longer cell value here"],
            ["plain", "\U0001F44D\U0001F3FD thumbs", "x"],
        ],
        style=TableStyle(),
        max_width=None,
    )

    widths = {display_width(line) for line in _lines(table)}
    assert len(widths) == 1, widths


def test_no_per_column_cap_when_max_width_is_none():
    long_cell = "a fairly long descriptive sentence that should not be cut"
    table = AsciiTable(
        headers=["what", "detail"],
        rows=[["read", long_cell]],
        style=TableStyle(),
        max_width=None,
    )

    rendered = table.to_string()
    assert long_cell in rendered
    assert "\u2025" not in rendered  # no truncation ellipsis


def test_markdown_table_with_emoji_renders_aligned():
    from pico_chat.ui.tui.components.markdown import MarkdownComponent
    from pico_chat.ui.tui.buffer import Buffer

    md = (
        "| 🏆 Rank | 👤 Name | 🎯 Score |\n"
        "|:------:|:-------|:-------:|\n"
        "| 🥇 1 | Alice | 98 |\n"
        "| 4️⃣ 4 | Dave | 82 |\n"
    )
    comp = MarkdownComponent(md)
    comp.set_layout(0, 0, 60, 10)
    buf = Buffer(60, 10)
    comp.render(buf)

    rendered = [
        "".join(cell.char for cell in row).rstrip()
        for row in buf.cells[: len(comp._wrapped_lines)]
    ]
    widths = {display_width(line) for line in rendered if line}
    assert widths and len(widths) == 1, widths
    assert any("4\uFE0F\u20E3" in line for line in rendered)


def test_max_width_truncates_on_display_cells():
    table = AsciiTable(
        headers=["col"],
        rows=[["\U0001F600" * 10]],
        style=TableStyle(),
        max_width=5,
    )

    lines = _lines(table)
    assert len({display_width(line) for line in lines}) == 1
    assert "\u2025" in table.to_string()  # truncation ellipsis
