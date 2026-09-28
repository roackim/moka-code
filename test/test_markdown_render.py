"""Markdown presentation: emphasis colors, list pastilles, cluster-aware wrap."""

from moka_code.ui.tui.components.markdown import Markdown, MarkdownComponent
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.layout_utils import display_width


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


def test_emphasis_uses_weight_and_slant_never_reverse():
    lines = Markdown().parse("# Title\nplain *it* **bo** `co` [li](u)")
    segments = [seg for line in lines for seg in line]
    by_text = {seg.text: seg for seg in segments}
    heading = [seg for seg in lines[0] if seg.text.strip()]

    assert all(seg.bold and seg.fg is not None for seg in heading)
    assert by_text["bo"].bold and by_text["bo"].fg is not None
    assert by_text["bo"].fg != heading[0].fg            # headings ≠ bold
    assert by_text["it"].italic and by_text["it"].fg is None
    assert by_text["co"].fg is not None and not by_text["co"].bold
    assert by_text["li"].underline
    assert not any(seg.reverse for seg in segments)


def test_style_colors_accept_theme_names(monkeypatch):
    from moka_code import settings
    from moka_code.ui.tui.colors import theme

    monkeypatch.setitem(settings.config.markdown_styles, "quote", {"fg": "MUTED"})
    (line,) = Markdown().parse("> quoted")
    assert line[0].text == "│ " and line[0].fg is theme.MUTED


def test_italic_and_underline_reach_the_terminal():
    from moka_code.ui.tui.buffer import Buffer

    buffer = Buffer(10, 1)
    buffer.write_str(0, 0, "ab", italic=True)
    buffer.write_str(2, 0, "cd", underline=True)
    out = buffer.render()
    assert "\033[3m" in out and "\033[23m" in out and "\033[4m" in out


def test_hard_break_counts_emoji_clusters():
    """A code block of wide emoji wraps by display width (no clipping)."""
    lines = _rendered_lines("```\n" + "\u2B06\uFE0F" * 40 + "\n```", width=40)
    rendered = [line for line in lines if line]

    assert all(display_width(line) <= 40 for line in rendered)
    assert sum(display_width(line) for line in rendered) == 80


def test_header_marker_matches_the_source_level():
    for level in range(1, 7):
        src = "#" * level + " Title"
        (line,) = Markdown().parse(src)
        assert "".join(seg.text for seg in line) == src
