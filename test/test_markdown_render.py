"""Markdown presentation: emphasis colors, list pastilles, cluster-aware wrap."""

from pico_chat.ui.tui.components.markdown import Markdown, MarkdownComponent
from pico_chat.ui.tui.buffer import Buffer
from pico_chat.ui.tui.layout_utils import display_width


def _rendered_lines(md: str, width: int = 60, height: int = 20):
    comp = MarkdownComponent(md)
    comp.set_layout(0, 0, width, height)
    buf = Buffer(width, height)
    comp.render(buf)
    return [
        "".join(cell.char for cell in row).rstrip()
        for row in buf.cells[: len(comp._wrapped_lines)]
    ]


def test_unordered_list_uses_pastilles():
    lines = _rendered_lines("- a\n- b\n  - c\n")

    assert lines[0].startswith("• ")
    assert any("◦" in line for line in lines)


def test_emphasis_uses_colors_not_reverse_or_bold():
    lines = Markdown().parse("plain *it* **bo**")
    segments = [seg for line in lines for seg in line]

    italic = next(seg for seg in segments if seg.text == "it")
    bold = next(seg for seg in segments if seg.text == "bo")

    assert italic.fg is not None and italic.reverse is False
    assert bold.fg is not None and bold.bold is False


def test_hard_break_counts_emoji_clusters():
    """A code block of wide emoji wraps by display width (no clipping)."""
    lines = _rendered_lines("```\n" + "\u2B06\uFE0F" * 40 + "\n```", width=40)
    rendered = [line for line in lines if line]

    assert all(display_width(line) <= 40 for line in rendered)
    assert sum(display_width(line) for line in rendered) == 80
