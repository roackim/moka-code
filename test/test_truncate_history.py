"""Editing a sent user message cuts the history back to just before it."""

from moka_code.harness.harness import Harness


def _harness():
    harness = Harness.__new__(Harness)
    harness.history = [
        {"id": "u1", "role": "user", "content": "one"},
        {"id": "a1", "role": "assistant", "content": "ans"},
        {"id": "u2", "role": "user", "content": "two"},
        {"id": "a2", "role": "assistant", "content": "ans2"},
    ]
    harness._last_usage = {"tokens": 9}
    return harness


def test_truncate_drops_the_entry_and_everything_after_it():
    harness = _harness()

    assert harness.truncate_history("u2") is True

    assert [e["id"] for e in harness.history] == ["u1", "a1"]
    assert harness._last_usage is None      # the status bar no longer shows the old context


def test_truncate_with_an_unknown_id_changes_nothing():
    harness = _harness()

    assert harness.truncate_history("nope") is False

    assert len(harness.history) == 4
