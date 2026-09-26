"""Tests for the inline thinking-tag streaming parser.

Covers the regression where a fixed trailing buffer withheld the last
characters of a message until end-of-stream (visible when a tool call follows).
"""

from pico_chat.harness.thinking_parser import ThinkingTagParser


def _drain(parser, chunks):
    out = []
    for chunk in chunks:
        for seg in parser.feed(chunk):
            out.append((seg.is_thinking, seg.text))
    for seg in parser.flush():
        out.append((seg.is_thinking, seg.text))
    return "".join(t for k, t in out if not k), "".join(t for k, t in out if k)


def test_trailing_text_emitted_immediately():
    """Plain text is fully emitted by feed(); nothing waits for flush()."""
    parser = ThinkingTagParser()
    segments = parser.feed("I'll ping github.com once for you.")
    text = "".join(seg.text for seg in segments if not seg.is_thinking)

    assert text == "I'll ping github.com once for you."
    assert parser.flush() == []


def test_partial_open_tag_is_held_then_completed():
    parser = ThinkingTagParser()
    segments = parser.feed("hello <thi")
    assert "".join(seg.text for seg in segments) == "hello "

    content, reasoning = _drain(parser, ["nking>why</thinking> done"])
    assert content == " done"
    assert reasoning == "why"


def test_tag_split_across_chunks_roundtrips():
    parser = ThinkingTagParser()
    content, reasoning = _drain(
        parser, ["a<", "think>", "why", "</", "think>", "b"]
    )
    assert content == "ab"
    assert reasoning == "why"


def test_content_ending_with_non_tag_angle_emits_fully():
    parser = ThinkingTagParser()
    segments = parser.feed("use 5 < 10 for this")
    text = "".join(seg.text for seg in segments if not seg.is_thinking)
    assert text == "use 5 < 10 for this"
    assert parser.flush() == []
