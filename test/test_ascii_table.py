"""AsciiTable width handling: wide/emoji cells align; no hidden truncation."""

from moka_chat.ui.tui.ascii_table import AsciiTable, TableStyle
from moka_chat.ui.tui.layout_utils import display_width


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
    from moka_chat.ui.tui.components.markdown import MarkdownComponent
    from moka_chat.ui.tui.buffer import Buffer

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


def _fitted(headers, rows, width):
    return AsciiTable(headers=headers, rows=rows, style=TableStyle(),
                      max_width=None, total_width=width).to_string().split("\n")


def test_fits_the_width_by_shrinking_the_widest_columns_and_wrapping():
    lines = _fitted(["key", "description"],
                    [["a", "a long description that cannot fit on one line here"]], 30)
    assert {display_width(line) for line in lines} == {30}
    assert lines[1].startswith("│ key │")          # the narrow column is kept
    assert sum(1 for line in lines if "│ a " in line or "│   " in line) >= 2


def test_row_separators_only_when_a_row_spans_several_lines():
    single = _fitted(["a", "b"], [["1", "2"], ["3", "4"]], 40)
    assert sum(line.startswith("├") for line in single) == 1   # header only
    multi = _fitted(["a", "b"], [["1", "word " * 12], ["3", "4"]], 30)
    assert sum(line.startswith("├") for line in multi) == 2    # + between rows


def test_no_blank_lines_around_the_table():
    lines = _fitted(["a", "b"], [["1", "2"]], None)
    assert lines[0].startswith("┌") and lines[-1].startswith("└")


def test_markdown_table_fits_the_component_and_relays_on_resize():
    from moka_chat.ui.tui.components.markdown import MarkdownComponent

    md = ("| name | notes |\n|---|---|\n"
          "| `x` | **a** long cell that will need to wrap at narrow widths |\n")
    comp = MarkdownComponent(md)

    def widest(width):
        comp.set_layout(0, 0, width, 30)
        comp.get_preferred_height(width)
        lines = ["".join(seg.text for seg in line) for line in comp._wrapped_lines]
        assert not any("`" in line or "**" in line for line in lines)  # markers gone
        return max(display_width(line) for line in lines), len(lines)

    wide_width, wide_rows = widest(90)
    narrow_width, narrow_rows = widest(30)
    assert wide_width <= 90 and narrow_width == 30
    assert narrow_rows > wide_rows
