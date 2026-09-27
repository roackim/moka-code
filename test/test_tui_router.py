from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.container import Container
from moka_chat.ui.tui.events import KeyEvent, MouseEvent, ResizeEvent
from moka_chat.ui.tui.focus import FocusScope
from moka_chat.ui.tui.router import EventRouter


class RecordingComponent(Component):
    def __init__(self, name):
        super().__init__(id=name)
        self.events = []

    def render(self, buffer: Buffer):
        pass

    def handle_input(self, event):
        self.events.append(event)
        return True


def test_mouse_event_targets_deepest_child_first():
    left = RecordingComponent("left")
    right = RecordingComponent("right")
    root = Container([left, right])
    root.set_layout(0, 0, 20, 5)
    left.set_layout(0, 0, 10, 5)
    right.set_layout(10, 0, 10, 5)
    router = EventRouter(root)

    event = MouseEvent(12, 2, 0, True)
    assert router.dispatch(event) is True
    assert right.events == [event]
    assert left.events == []


def test_overlay_has_priority_over_root():
    root = RecordingComponent("root")
    root.set_layout(0, 0, 20, 5)
    overlay = RecordingComponent("overlay")
    overlay.set_layout(0, 0, 5, 5)
    router = EventRouter(root)
    router.add_overlay(overlay)

    event = MouseEvent(15, 2, 0, True)
    assert router.dispatch(event) is True
    assert overlay.events == [event]
    assert root.events == []


def test_interceptor_runs_after_overlays_and_can_consume_event():
    root = RecordingComponent("root")
    root.set_layout(0, 0, 20, 5)
    overlay = RecordingComponent("overlay")
    overlay.set_layout(0, 0, 5, 5)
    overlay.handle_input = lambda event: False
    intercepted = []
    router = EventRouter(root, interceptor=lambda event: intercepted.append(event) or True)
    router.add_overlay(overlay)

    event = MouseEvent(15, 2, 0, True)
    assert router.dispatch(event) is True
    assert intercepted == [event]
    assert root.events == []


def test_mouse_event_bubbles_to_parent_when_child_does_not_handle():
    child = RecordingComponent("child")
    parent = Container([child])
    parent.set_layout(0, 0, 10, 5)
    child.set_layout(0, 0, 10, 5)
    child.handle_input = lambda event: False
    router = EventRouter(parent)

    assert router.dispatch(MouseEvent(2, 2, 0, True)) is False


def test_keyboard_and_resize_events_use_root_dispatch():
    root = RecordingComponent("root")
    root.set_layout(0, 0, 20, 5)
    router = EventRouter(root)
    key = KeyEvent("a")
    resize = ResizeEvent(80, 24)

    assert router.dispatch(key) is True
    assert router.dispatch(resize) is True
    assert root.events == [key, resize]


def test_keyboard_event_targets_active_focus_scope_widget():
    root = RecordingComponent("root")
    focused = RecordingComponent("focused")
    scope = FocusScope([focused])
    scope.enter()
    router = EventRouter(root, focus_scope=scope)

    event = KeyEvent("a")
    assert router.dispatch(event) is True
    assert focused.events == [event]
    assert root.events == []


def test_focus_scope_skips_disabled_target_in_keyboard_navigation():
    root = RecordingComponent("root")
    first = RecordingComponent("first")
    disabled = RecordingComponent("disabled")
    disabled.enabled = False
    last = RecordingComponent("last")
    scope = FocusScope([first, disabled, last])
    scope.enter()
    router = EventRouter(root, focus_scope=scope)

    assert scope.focus_next() is True
    assert scope.focused is last
    assert router.dispatch(KeyEvent("x")) is True
    assert last.events == [KeyEvent("x")]