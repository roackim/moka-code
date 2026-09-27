from moka_chat.ui.tui.actions import Action, ActionMap
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.components.text import Label
from moka_chat.ui.tui.events import KeyEvent, MouseEvent, ResizeEvent
from moka_chat.ui.tui.focus import FocusScope
from moka_chat.ui.tui.router import EventRouter


class RecordingComponent(Component):
    def __init__(self, handled=True):
        super().__init__()
        self.events = []
        self.handled = handled

    def render(self, buffer):
        buffer.write_str(self.x, self.y, "ok", max_width=self.width)

    def handle_input(self, event):
        self.events.append(event)
        return self.handled


def test_router_integration_covers_focus_overlay_resize_and_rendering():
    root = RecordingComponent()
    root.set_layout(0, 0, 20, 5)
    focused = RecordingComponent()
    focused.set_layout(0, 0, 20, 5)
    overlay = RecordingComponent()
    overlay.set_layout(0, 0, 5, 5)
    scope = FocusScope([focused])
    scope.enter()
    router = EventRouter(root, focus_scope=scope)

    assert router.dispatch(KeyEvent("x"))
    assert focused.events == [KeyEvent("x")]
    router.add_overlay(overlay)
    assert router.dispatch(MouseEvent(15, 2, 0, True))
    assert overlay.events[-1].x == 15
    assert router.dispatch(ResizeEvent(80, 24))
    assert overlay.events[-1] == ResizeEvent(80, 24)

    label = Label("library")
    label.set_layout(0, 0, 20, 1)
    buffer = Buffer(20, 1)
    label.render(buffer)
    assert buffer.cells[0][0].char == "l"


def test_action_map_is_a_router_integration_boundary():
    root = RecordingComponent(handled=False)
    action_map = ActionMap()
    received = []
    action_map.bind("activate", lambda action: received.append(action) or True)
    router = EventRouter(root, action_map=action_map)

    action = Action("activate", "tab-1")
    assert router.dispatch(action)
    assert received == [action]
    assert root.events == []
