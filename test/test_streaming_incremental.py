"""Regression tests for incremental (append-only) streaming.

Guards the append-only commit path in MarkdownComponent.update(append=True) and
the tail raster in Box/_render_thread_to_subbuffer: the incremental result must
be identical to a streaming-aware full parse/render at every step of a chunked
stream.

Because the still-open final line is rendered *plain* while streaming (approach
A), the reference is built with ``streaming=True`` so it applies the same rule.
"""

import pytest

from moka_chat.ui.chat_history_panel import ChatHistoryPanel
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.components.markdown import MarkdownComponent
from moka_chat.ui.tui.msg_types import AssistantMsg, UserMsg


SAMPLES = [
    "# Title\n\nFirst **bold** para.\n\nSecond `code` para.\n\n- a\n- b\n\n> quote\n\n---\n\nend\n",
    "one long paragraph with no blank lines at all until the very end here",
    "```python\nprint('hi')\n\nprint('blank above')\n```\n\nafter code\n",
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n| c | d |\n|---|---|\n| 3 | 4 |\n\nplain\n",
    "## H\npara one\n\n### H2\n\n- x\n\nfinal **bold** `c` [l](u)\n",
    "intro\n\n```\ncode one\n\ncode two\n```\n\noutro text\n\nmore\n",
    "",
    # streaming edge cases: open/mid fence, table at end, header at end
    "```\nunclosed fence line one\nunclosed fence line two",
    "before\n\n```python\nx = 1\n\n```\nafter **bold** tail",
    "lead\n\n| h1 | h2 |\n|---|---|\n| a | b |",
    "lead\n\n| h1 | h2 |\n",
    "**bold** on the open line",
]


def _flatten(comp):
    """Per-character style view of the wrapped lines (segment-agnostic)."""
    out = []
    for line in comp._wrapped_lines:
        chars = []
        for seg in line:
            for ch in seg.text:
                chars.append((ch, seg.fg, seg.bg, seg.bold, seg.reverse, seg.code_block))
        out.append(chars)
    return out


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("chunk", [1, 2, 5, 13])
def test_incremental_parse_matches_full(sample, chunk):
    inc = MarkdownComponent("", streaming=True)
    inc.set_layout(0, 0, 30, 200)
    cur = ""
    for i in range(0, len(sample), chunk):
        cur += sample[i:i + chunk]
        inc.update(cur, append=True)
        full = MarkdownComponent(cur, streaming=True)
        full.set_layout(0, 0, 30, 200)
        assert _flatten(inc) == _flatten(full)


def _snapshot(buf):
    return [
        [(c.char, c.fg, c.bg, c.bold, c.reverse, c.underline, c.is_wide_char_continuation)
         for c in row]
        for row in buf.cells
    ]


def _fresh(text, width, height):
    panel = ChatHistoryPanel(max_width=width)
    panel.add_message("hello", msg_type=UserMsg()).finalize()
    msg = panel.add_message(text, msg_type=AssistantMsg())
    msg.component.set_streaming(True)
    panel.set_layout(0, 0, width, height)
    buf = Buffer(width, height)
    panel.render(buf)
    return buf


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("size", [(90, 24), (40, 12)])
def test_incremental_render_matches_full(sample, size):
    width, height = size
    full = sample * 3
    panel = ChatHistoryPanel(max_width=width)
    panel.add_message("hello", msg_type=UserMsg()).finalize()
    msg = panel.add_message("", msg_type=AssistantMsg())
    panel.set_layout(0, 0, width, height)
    buf = Buffer(width, height)

    cur = ""
    for i in range(0, len(full), 3):
        cur += full[i:i + 3]
        msg.append(full[i:i + 3])
        panel.render(buf)
        reference = _fresh(cur, width, height)
        assert _snapshot(buf) == _snapshot(reference), f"diverged at {len(cur)} chars"


def test_incremental_render_converges_after_finalize():
    """After finalize the open line is styled, matching a fresh full parse."""
    text = "alpha\n\nbeta **bold**"
    panel = ChatHistoryPanel(max_width=60)
    msg = panel.add_message("", msg_type=AssistantMsg())
    panel.set_layout(0, 0, 60, 20)
    for i in range(0, len(text), 2):
        msg.append(text[i:i + 2])
    msg.finalize()
    buf = Buffer(60, 20)
    panel.render(buf)

    ref = ChatHistoryPanel(max_width=60)
    # Rebuild the same message, finalized, from the full text.
    ref_msg = ref.add_message(text, msg_type=AssistantMsg())
    ref_msg.finalize()
    ref.set_layout(0, 0, 60, 20)
    ref_buf = Buffer(60, 20)
    ref.render(ref_buf)

    assert _snapshot(buf) == _snapshot(ref_buf)


def test_last_line_plain_while_streaming_then_styled_after_finalize():
    comp = MarkdownComponent("hello **bold**", streaming=True)
    comp.set_layout(0, 0, 40, 10)
    assert comp._parsed_lines[0][0].text == "hello **bold**"
    assert not any(seg.fg for seg in comp._parsed_lines[0])

    comp.set_streaming(False)
    joined = "".join(seg.text for seg in comp._parsed_lines[0])
    assert joined == "hello bold"
    # Emphasis is a color (no terminal bold), so the styled span has an fg.
    assert any(seg.fg and seg.text == "bold" for seg in comp._parsed_lines[0])


def test_dirty_from_line_for_paragraph_append():
    comp = MarkdownComponent("", streaming=True)
    comp.set_layout(0, 0, 40, 50)
    comp.update("first line\nsecond line\nthird", append=True)
    committed_wrapped = len(comp._committed_wrapped)
    assert committed_wrapped > 0
    # A subsequent append inside the open line only dirties the open region.
    comp.take_dirty_from_line()  # clear
    comp.update("first line\nsecond line\nthird more", append=True)
    dirty = comp.take_dirty_from_line()
    assert dirty is not None
    assert dirty == committed_wrapped


def test_append_idempotent_on_same_text():
    comp = MarkdownComponent("", streaming=True)
    comp.set_layout(0, 0, 40, 50)
    comp.update("same text here", append=True)
    comp.take_dirty_from_line()
    # Identical text is not an append; the guard short-circuits to no work.
    comp.update("same text here", append=True)
    assert comp._dirty_from_line is None
