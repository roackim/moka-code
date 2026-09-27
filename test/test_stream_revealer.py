"""Deterministic tests for StreamRevealer. No sleeps; time is injected."""

from collections import deque

import pytest

from moka_chat.ui.stream_revealer import StreamRevealer

STEP = 1.0 / 60


def test_inactive_by_default():
    r = StreamRevealer(60)
    assert r.active() is False
    assert r.pending() == 0
    assert r.tick(0.0) == ""
    assert r.drain() == ""


def test_steady_arrival_reveals_one_cluster_per_step():
    r = StreamRevealer(60)
    t = 0.0
    released = ""
    for _ in range(60):
        r.ingest("a", t)
        released += r.tick(t)
        t += 2 * STEP
    assert released == "a" * 60
    assert r.active() is False


def test_large_burst_drains_within_window_and_not_all_at_once():
    r = StreamRevealer(60)
    r.ingest("x" * 600, 0.0)
    first = r.tick(0.0)
    assert first != ""
    assert 0 < len(first) < 600
    # Once past the window, everything is released.
    r.tick(0.25)
    assert r.active() is False


def test_deadline_is_anchored_to_oldest_text():
    r = StreamRevealer(60)
    r.ingest("a" * 100, 0.0)
    assert r._oldest == 0.0
    r.ingest("b" * 100, 0.20)
    # The newer chunk does not move the deadline.
    assert r._oldest == 0.0
    # At the original deadline everything (old and new) is released.
    released = r.tick(0.25)
    assert released == "a" * 100 + "b" * 100
    assert r.active() is False


def test_no_character_held_longer_than_max_window():
    """A continuous producer cannot keep a character buffered past the window."""
    r = StreamRevealer(60)
    arrivals: deque[float] = deque()
    max_lag = 0.0
    t = 0.0
    next_arrival = 0.0
    end = 2.0
    while t < end + 1.0:
        while next_arrival < end and next_arrival <= t:
            r.ingest("x" * 5, next_arrival)
            arrivals.extend([next_arrival] * 5)
            next_arrival += 0.01
        released = r.tick(t)
        for _ in range(len(released)):
            max_lag = max(max_lag, t - arrivals.popleft())
        t += STEP

    assert not arrivals
    assert max_lag <= r.max_window + STEP + 1e-9


def test_anchor_resets_after_drain():
    r = StreamRevealer(60)
    r.ingest("abc", 0.0)
    r.drain()
    r.ingest("de", 0.5)
    assert r._oldest == 0.5


def test_behind_schedule_releases_all():
    r = StreamRevealer(60)
    r.ingest("a" * 100, 0.0)
    r.tick(0.0)
    assert r.active() is True
    r.tick(1.0)
    assert r.active() is False


def test_whitespace_only_pending_releases_fully():
    r = StreamRevealer(60)
    r.ingest("  \n  ", 0.0)
    out = r.tick(0.0)
    assert out == "  \n  "
    assert r.active() is False


def test_reveal_never_splits_a_cluster():
    r = StreamRevealer(60)
    r.ingest("e\u0301e\u0301", 0.0)
    out = r.tick(0.0)
    assert out == "e\u0301"


def test_drain_releases_everything():
    r = StreamRevealer(60)
    r.ingest("hello world", 0.0)
    assert r.pending() == 10
    assert r.drain() == "hello world"
    assert r.active() is False
    assert r.drain() == ""


def test_step_interval_gates_ticks():
    r = StreamRevealer(60)
    r.ingest("abc", 0.0)
    assert r.tick(0.0) == "a"
    # A second tick within the step interval yields nothing.
    assert r.tick(0.001) == ""


def test_whitespace_included_with_released_clusters():
    r = StreamRevealer(60)
    r.ingest(" a b", 0.0)
    # grain is 1 (3 clusters spread over the window) -> leading space + cluster.
    assert r.tick(0.0) == " a"
