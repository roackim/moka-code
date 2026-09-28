"""Tests for Compositor frame callbacks (S1)."""

from moka_code.ui.tui.compositor import Compositor


def _bare():
    c = Compositor.__new__(Compositor)
    c._frame_callbacks = []
    c._render_requested = False
    c._wake_event = _NullEvent()
    return c


class _NullEvent:
    def set(self):
        pass


def test_add_registers_once():
    c = _bare()
    cb = lambda now: False
    c.add_frame_callback(cb)
    c.add_frame_callback(cb)
    assert c._frame_callbacks == [cb]


def test_remove_unregisters_and_ignores_unknown():
    c = _bare()
    cb = lambda now: False
    c.add_frame_callback(cb)
    c.remove_frame_callback(cb)
    assert c._frame_callbacks == []
    c.remove_frame_callback(cb)  # no error


def test_run_callbacks_requests_render_when_work():
    c = _bare()
    seen = []
    c.add_frame_callback(lambda now: seen.append(now) or True)
    assert c._run_frame_callbacks(1.5) is True
    assert seen == [1.5]
    assert c._render_requested is True


def test_run_callbacks_no_work_no_render():
    c = _bare()
    c.add_frame_callback(lambda now: False)
    assert c._run_frame_callbacks(1.5) is False
    assert c._render_requested is False


def test_callback_can_unregister_during_iteration():
    c = _bare()
    calls = []

    def self_removing(now):
        calls.append("self")
        c.remove_frame_callback(self_removing)
        return True

    def other(now):
        calls.append("other")
        return False

    c.add_frame_callback(self_removing)
    c.add_frame_callback(other)
    assert c._run_frame_callbacks(0.0) is True
    assert calls == ["self", "other"]
    assert other in c._frame_callbacks
    assert self_removing not in c._frame_callbacks
