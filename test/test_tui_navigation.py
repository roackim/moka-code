from moka_chat.ui.tui.actions import ActionMap
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.compositor import Compositor
from moka_chat.ui.tui.focus import FocusScope
from moka_chat.ui.tui.navigation import ModalHost, Navigator
from moka_chat.ui.tui.screen import Screen
from moka_chat.ui.tui.chat_screen import ChatScreen
from moka_chat.ui.tui.events import KeyEvent


class RecordingComponent(Component):
    def render(self, buffer: Buffer):
        pass


def test_compositor_shutdown_uses_key_event_metadata():
    compositor = Compositor.__new__(Compositor)
    compositor.shutdown_event = None
    compositor.running = True

    assert compositor._handle_shutdown_key(KeyEvent("\x03"))
    assert compositor.running is False


class FakeCompositor:
    def __init__(self):
        self.root = None
        self.event_router = type("Router", (), {
            "root": None,
            "set_focus_scope": lambda router, scope: setattr(router, "focus_scope", scope),
            "set_action_map": lambda router, actions: setattr(router, "action_map", actions),
        })()
        self.overlays = []
        self.components_by_id = {}

    def set_root(self, root):
        self.root = root
        self.event_router.root = root

    def add_overlay(self, component):
        self.overlays.append(component)

    def remove_overlay(self, component):
        self.overlays.remove(component)


class LifecycleScreen(Screen):
    def __init__(self, name):
        super().__init__(RecordingComponent())
        self.name = name
        self.events = []

    def on_enter(self):
        self.events.append("enter")

    def on_leave(self):
        self.events.append("leave")

    def on_suspend(self):
        self.events.append("suspend")

    def on_resume(self):
        self.events.append("resume")


def test_navigator_manages_stack_and_lifecycle():
    compositor = FakeCompositor()
    first = LifecycleScreen("first")
    second = LifecycleScreen("second")
    navigator = Navigator(compositor, first)

    navigator.push(second)
    assert navigator.current is second
    assert first.events == ["enter", "suspend"]
    assert second.events == ["enter"]

    assert navigator.back()
    assert navigator.current is first
    assert second.events == ["enter", "leave"]
    assert first.events == ["enter", "suspend", "resume"]
    assert not navigator.back()


def test_navigator_installs_screen_routing_state():
    compositor = FakeCompositor()
    scope = FocusScope([])
    actions = ActionMap()
    screen = Screen(RecordingComponent(), focus_scope=scope, action_map=actions)
    navigator = Navigator(compositor, screen)

    assert compositor.root is screen.root
    assert compositor.event_router.focus_scope is scope
    assert compositor.event_router.action_map is actions


def test_navigator_replace_leaves_current_screen_before_entering_new_screen():
    compositor = FakeCompositor()
    first = LifecycleScreen("first")
    replacement = LifecycleScreen("replacement")
    navigator = Navigator(compositor, first)

    navigator.replace(replacement)

    assert navigator.current is replacement
    assert navigator.stack_depth == 1
    assert first.events == ["enter", "leave"]
    assert replacement.events == ["enter"]


def test_modal_host_delegates_overlay_ownership():
    compositor = FakeCompositor()
    host = ModalHost(compositor)
    modal = RecordingComponent()

    host.present(modal)
    assert host.current is modal
    assert compositor.overlays == [modal]
    host.dismiss()
    assert host.current is None
    assert compositor.overlays == []


def test_modal_host_replaces_active_modal_screen_with_lifecycle_hooks():
    compositor = FakeCompositor()
    host = ModalHost(compositor)
    first = LifecycleScreen("first")
    second = LifecycleScreen("second")

    host.present_screen(first)
    host.present_screen(second)

    assert host.current is second.root
    assert compositor.overlays == [second.root]
    assert first.events == ["enter", "leave"]
    assert second.events == ["enter"]

    host.dismiss_screen()
    assert compositor.overlays == []
    assert second.events == ["enter", "leave"]


def test_chat_screen_composes_workspace_and_focus_scope():
    history = RecordingComponent()
    input_box = RecordingComponent()
    focus_scope = FocusScope([])

    screen = ChatScreen(history, input_box, focus_scope)

    assert screen.focus_scope is focus_scope
    assert screen.root is screen.scaffold
    assert screen.scaffold.bottom is screen.status_bar
    assert screen.workspace.children == [history, input_box]
    assert history.parent is screen.workspace
    assert input_box.parent is screen.workspace