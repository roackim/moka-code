"""Tests for the Message arrived/revealed split (S4)."""

from moka_chat.ui.chat_message import Message


def test_ingest_grows_base_text_without_rendering():
    msg = Message("", max_width=40)
    msg.ingest("hello")
    assert msg.base_text == "hello"
    assert msg._reveal_len == 0
    assert msg.get_formatted() == ""


def test_reveal_to_renders_only_the_prefix():
    msg = Message("", max_width=40)
    msg.ingest("hello world")
    msg.reveal_to(5)
    assert msg.get_formatted() == "hello"
    msg.reveal_to(11)
    assert msg.get_formatted() == "hello world"


def test_reveal_to_clamps_to_base_text():
    msg = Message("", max_width=40)
    msg.ingest("abc")
    msg.reveal_to(99)
    assert msg._reveal_len == 3
    assert msg.get_formatted() == "abc"


def test_finalize_drains_unrevealed_text():
    msg = Message("", max_width=40, render_markdown=True)
    msg.ingest("hello **world**")
    msg.reveal_to(2)
    msg.finalize()
    assert msg._reveal_len == len(msg.base_text)
    assert msg.finalized is True


def test_append_ingests_and_reveals_fully():
    msg = Message("", max_width=40)
    msg.append("abc")
    assert msg.base_text == "abc"
    assert msg._reveal_len == 3
    assert msg.get_formatted() == "abc"


def test_append_strips_leading_whitespace_but_ingest_keeps_later_text():
    msg = Message("", max_width=40)
    msg.ingest("  first")
    assert msg.base_text == "first"
    msg.ingest("  second")
    assert msg.base_text == "first  second"
