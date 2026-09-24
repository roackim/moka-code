"""Tests for host-side tool-output elision."""

from pico_chat.harness.elision import DEFAULT_LIMIT, elide


def test_short_text_is_unchanged():
    assert elide("hello") == "hello"


def test_exact_limit_is_unchanged():
    text = "x" * DEFAULT_LIMIT
    assert elide(text) == text


def test_over_limit_keeps_head_and_tail():
    text = "".join(str(i % 10) for i in range(50_000))

    result = elide(text, limit=1000)
    half = 500

    assert result.startswith(text[:half])
    assert result.endswith(text[-half:])
    assert "chars elided; narrow the command" in result
    assert len(result) < len(text)


def test_elided_count_is_reported():
    text = "a" * 30_000

    result = elide(text, limit=1000)

    assert "29000 chars elided" in result


def test_binary_ish_output_is_truncated_safely():
    text = "\x00\x01\r\n" * 10_000

    result = elide(text, limit=500)

    assert "chars elided" in result
    assert len(result) < len(text)


def test_limit_zero_disables_elision():
    text = "y" * 20_000
    assert elide(text, limit=0) == text


def test_small_limit_does_not_crash():
    result = elide("abcdefgh", limit=1)
    assert "chars elided" in result
