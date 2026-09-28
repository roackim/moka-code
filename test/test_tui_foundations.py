from dataclasses import dataclass

from moka_code.ui.tui.events import CommandEvent, KeyEvent, MouseEvent, normalize_key
from moka_code.ui.tui.focus import FocusManager, FocusScope


@dataclass
class Widget:
    focusable: bool = True
    enabled: bool = True
    focused: bool = False
    x: int = 0
    y: int = 0
    width: int = 10
    height: int = 10


def test_focus_manager_skips_non_focusable_widgets():
    first = Widget()
    disabled = Widget(enabled=False)
    last = Widget()
    manager = FocusManager([first, disabled, last])

    assert manager.focused is first
    assert first.focused is True
    assert manager.next() is True
    assert manager.focused is last
    assert disabled.focused is False


def test_focus_manager_updates_focus_state_and_can_clear():
    first = Widget()
    second = Widget()
    manager = FocusManager([first, second])

    assert manager.focus(1) is True
    assert first.focused is False
    assert second.focused is True
    manager.clear()
    assert manager.focused is None
    assert second.focused is False


def test_events_are_typed_values():
    event = normalize_key("a")
    assert isinstance(event, KeyEvent)
    assert isinstance(event, str)
    assert event == "a"
    assert event.key == "a"
    assert event.text == "a"
    assert MouseEvent(2, 3, 0, True).x == 2
    assert CommandEvent("submit").name == "submit"


def test_non_printable_key_has_no_text_payload():
    event = normalize_key("\x1b[A")
    assert event.key == "\x1b[A"
    assert event.text is None


def test_focus_scope_traps_focus_and_clears_on_leave():
    first = Widget()
    second = Widget()
    scope = FocusScope([first, second])

    assert scope.enter() is True
    assert scope.focused is first
    assert scope.focus_next() is True
    assert scope.focused is second
    assert scope.focus_next() is True
    assert scope.focused is first
    scope.leave()
    assert scope.active is False
    assert scope.focused is None


def test_focus_scope_lifecycle_callbacks_fire_once():
    calls = []
    scope = FocusScope(
        [Widget()],
        on_enter=lambda: calls.append("enter"),
        on_leave=lambda: calls.append("leave"),
    )

    assert scope.enter() is True
    assert scope.enter() is True
    scope.leave()
    scope.leave()
    assert calls == ["enter", "leave"]


def test_focus_scope_focuses_widget_at_coordinate():
    first = Widget(x=0, y=0, width=5, height=5)
    second = Widget(x=5, y=0, width=5, height=5)
    scope = FocusScope([first, second])

    assert scope.focus_at(7, 2) is True
    assert scope.focused is second
    assert scope.focus_at(20, 2) is False
    assert scope.focused is second