from moka_chat.ui.tui.actions import Action, ActionMap, action
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.router import EventRouter


class RecordingComponent(Component):
    def __init__(self):
        super().__init__()
        self.events = []

    def render(self, buffer: Buffer):
        pass

    def handle_input(self, event):
        self.events.append(event)
        return True


def test_action_is_immutable_and_payload_is_preserved():
    event = action("submit", {"source": "form"})

    assert event == Action("submit", {"source": "form"})
    assert event.payload["source"] == "form"


def test_action_map_dispatches_and_unbinds_handlers():
    received = []
    actions = ActionMap()
    actions.bind("close", lambda event: received.append(event.payload) or True)

    assert actions.dispatch(Action("close", "popup"))
    assert received == ["popup"]
    actions.unbind("close")
    assert not actions.dispatch(Action("close"))


def test_action_map_prefers_local_handler_then_parent():
    received = []
    parent = ActionMap()
    parent.bind("close", lambda event: received.append("parent") or True)
    local = ActionMap(parent=parent)
    local.bind("close", lambda event: received.append("local") or True)

    assert local.dispatch(Action("close"))
    assert received == ["local"]

    local.unbind("close")
    assert local.dispatch(Action("close"))
    assert received == ["local", "parent"]


def test_router_dispatches_actions_before_root_fallback():
    root = RecordingComponent()
    router = EventRouter(root)
    received = []
    router.bind_action("submit", lambda event: received.append(event.name) or True)

    assert router.dispatch(Action("submit"))
    assert received == ["submit"]
    assert root.events == []


def test_unhandled_action_reaches_root():
    root = RecordingComponent()
    router = EventRouter(root)

    assert router.dispatch(Action("scroll", 2))
    assert root.events == [Action("scroll", 2)]


def test_interceptor_prevents_action_dispatch():
    root = RecordingComponent()
    received = []
    router = EventRouter(root, interceptor=lambda event: received.append(event) or True)

    event = Action("cancel")
    assert router.dispatch(event)
    assert received == [event]
    assert root.events == []