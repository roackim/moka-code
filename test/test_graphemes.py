"""Tests for grapheme cluster helpers (S2)."""

from moka_chat.ui.tui.graphemes import advance_nonws, count_nonws, split_clusters


def test_ascii_clusters():
    assert split_clusters("abc") == ["a", "b", "c"]
    assert split_clusters("") == []


def test_combining_mark_attaches():
    assert split_clusters("e\u0301") == ["e\u0301"]


def test_zwj_sequence_is_one_cluster():
    family = "\U0001F468\u200d\U0001F469\u200d\U0001F467"
    assert split_clusters(family) == [family]


def test_variation_selector_attaches():
    assert split_clusters("\u2764\ufe0f") == ["\u2764\ufe0f"]


def test_skin_tone_modifier_attaches():
    assert split_clusters("\U0001F44D\U0001F3FD") == ["\U0001F44D\U0001F3FD"]


def test_regional_indicator_pair():
    assert split_clusters("\U0001F1EB\U0001F1F7") == ["\U0001F1EB\U0001F1F7"]
    # An unpaired indicator is its own cluster.
    assert split_clusters("\U0001F1EB") == ["\U0001F1EB"]


def test_hangul_jamo_cluster():
    jamo = "\u1100\u1161\u11a8"
    assert split_clusters(jamo) == [jamo]


def test_crlf_is_one_cluster():
    assert split_clusters("a\r\nb") == ["a", "\r\n", "b"]


def test_whitespace_runs():
    assert split_clusters("  \n\t a") == [" ", " ", "\n", "\t", " ", "a"]


def test_count_nonws_ignores_whitespace():
    assert count_nonws(" a  b ") == 2
    assert count_nonws("   ") == 0
    assert count_nonws("") == 0
    assert count_nonws("e\u0301 e\u0301") == 2


def test_advance_nonws_includes_leading_whitespace():
    # " a b c": release two non-ws clusters -> " a b"
    assert advance_nonws(" a b c", 2) == 4


def test_advance_nonws_includes_intervening_whitespace():
    assert advance_nonws("ab  cd", 1) == 1
    assert advance_nonws("ab  cd", 2) == 2
    assert advance_nonws("ab  cd", 3) == 5
    assert advance_nonws("ab  cd", 4) == 6


def test_advance_nonws_clamps_and_zero():
    assert advance_nonws("abc", 0) == 0
    assert advance_nonws("abc", -1) == 0
    assert advance_nonws("abc", 99) == 3
    assert advance_nonws("   ", 1) == 3


def test_advance_never_splits_a_cluster():
    text = "e\u0301x"
    assert advance_nonws(text, 1) == len("e\u0301")
