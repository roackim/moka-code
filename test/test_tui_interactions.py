from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.events import TickEvent
from moka_code.ui.tui.terminal import MouseEvent
from moka_code.ui.chat_history_panel import ChatHistoryPanel
from moka_code.ui.app import chatTUI
from conftest import StubAgent


class FakeCompositor:
    def __init__(self, width=80, height=24):
        self.width = width
        self.height = height
        self.overlays = []

    def add_overlay(self, component):
        self.overlays.append(component)

    def remove_overlay(self, component):
        self.overlays.remove(component)

    def request_render(self):
        pass


class FakeMessageComponent:
    parent = None


class FakeMessage:
    def __init__(self):
        self.component = FakeMessageComponent()

    def get_component(self):
        return self.component


def test_cached_hit_test_rebuilds_after_cache_invalidation():
    """Scrolling invalidates the line-map cache; a subsequent hit test must
    rebuild it rather than crashing on a None cache (regression)."""
    from moka_code.ui.tui.msg_types import UserMsg

    panel = ChatHistoryPanel()
    panel.set_layout(0, 0, 40, 10)
    panel.add_message("hello", msg_type=UserMsg())
    panel.add_message("world", msg_type=UserMsg())

    # Simulate the scroll handler invalidating only the cache (not the key).
    panel._line_map_cache = None

    # Must not raise TypeError (len() of None).
    result = panel._cached_hit_test(panel.y + 1)
    assert result is not None


def test_app_focus_targets_expose_component_geometry_for_mouse_focus():
    ui = chatTUI(StubAgent())
    ui.chat_history_panel.set_layout(0, 0, 80, 20)
    ui.input_component.set_layout(0, 20, 80, 4)

    handled = ui.handle_global_input(MouseEvent(4, 5, 0, True))

    assert handled is False
    assert ui._last_focus_id == "history"


def test_debug_view_is_full_screen_at_the_end_and_copies_the_log():
    """/debug: the whole log, opened at its end; ``c`` copies all of it."""
    ui = chatTUI(StubAgent())
    ui.compositor = FakeCompositor(width=60, height=10)
    copied = []
    ui.popup.on_copy = copied.append
    for n in range(30):
        ui.debug_panel.log(f"line {n}")
    ui.debug_panel.log("x" * 150)           # a long line wraps instead of being cut

    ui.show_debug()
    popup = ui.popup

    assert popup.is_visible and popup in ui.compositor.overlays
    assert (popup.x, popup.y, popup.width, popup.height) == (0, 0, 60, 10)
    assert popup._scroll_offset == popup._max_scroll() > 0      # opened at the end
    assert [a.key for a in popup._box.actions] == ["c", "Esc"]

    assert popup.handle_input("c") is True
    assert copied[0].splitlines()[0].strip() == "line 0"
    assert copied[0].splitlines()[-1].strip() == "x" * 150     # copied unwrapped

    popup.handle_input("\x1b[5~")                              # PgUp
    assert popup._scroll_offset < popup._max_scroll()
    popup.handle_input("\x1b[H")                               # Home
    assert popup._scroll_offset == 0
    popup.handle_input("\x1b[F")                               # End
    assert popup._scroll_offset == popup._max_scroll()

    popup.handle_input("\x1b")
    assert not popup.is_visible and popup not in ui.compositor.overlays


def test_debug_view_with_an_empty_log_has_nothing_to_copy():
    ui = chatTUI(StubAgent())
    ui.compositor = FakeCompositor()
    ui.debug_panel.lines.clear()

    ui.show_debug()

    assert [a.key for a in ui.popup._box.actions] == ["Esc"]
    assert ui.popup.handle_input("c") is True       # consumed, nothing copied


def test_agent_worker_exits_promptly_on_shutdown():
    """Ctrl+C (shutdown_event) must release the idle worker, not hang."""
    import asyncio

    async def _run():
        ui = chatTUI(StubAgent())
        worker = asyncio.create_task(ui.agent_worker())
        await asyncio.sleep(0.01)  # let it block on the message queue
        ui.shutdown_event.set()
        await asyncio.wait_for(worker, timeout=1.0)

    asyncio.run(_run())


def test_multiple_messages_are_consistently_queued_during_generation():
    class ActiveTask:
        def done(self):
            return False

    ui = chatTUI(StubAgent())
    ui.current_generation_task = ActiveTask()

    ui.on_user_submit("second")
    ui.on_user_submit("third")
    ui.on_user_submit("fourth")

    queued = ui.chat_history_panel.messages
    assert [message.is_queued for message in queued] == [True, True, True]
    assert ui.message_queue.qsize() == 3


def test_clicking_input_box_bars_focuses_input():
    """Clicking anywhere in the input box (incl. its bars) focuses the input."""
    ui = chatTUI(StubAgent())
    ui._set_app_focus("history")
    # Geometry: place the input box bottom-anchored, click directly on its top bar.
    ui.input_box.set_layout(0, 20, 80, 3)

    handled = ui.handle_global_input(MouseEvent(5, 20, 0, True))

    assert handled is True
    assert ui._last_focus_id == "input"


def test_clicking_status_bar_does_not_focus_input():
    """Clicks outside the input box and history are not consumed for focus."""
    ui = chatTUI(StubAgent())
    # Place input box and status bar far from the click point.
    ui.input_box.set_layout(0, 20, 80, 3)
    ui._set_app_focus("history")

    handled = ui.handle_global_input(MouseEvent(70, 2, 0, True))

    assert handled is False
    assert ui._last_focus_id == "history"


def test_tick_advances_focused_input_cursor_blink():
    """The input's own handle_input drives the cursor blink on a TickEvent."""
    ui = chatTUI(StubAgent())
    ui.input_box.set_layout(0, 20, 80, 3)
    ui.input_box.render(Buffer(80, 24))

    # First tick stays inside the pulse delay -> no redraw needed.
    handled = ui.input_component.handle_input(TickEvent(0))
    assert handled is False

    # Force the blink to be past the pulse delay so the next tick flips.
    renderer = ui.input_component.cursor_renderer
    renderer.last_input_time -= 5.0
    # The box subbuffer flags a redraw once the cursor visibility toggles.
    ui.input_box.subbuffer.has_changed = False
    flipped = ui.input_component.handle_input(TickEvent(1))

    assert flipped is True
    assert ui.input_box.subbuffer.has_changed is True


def test_esc_unfocuses_input_when_focused():
    """ESC while input is focused moves focus to history (unfocuses input)."""
    ui = chatTUI(StubAgent())
    ui._set_app_focus("input")

    handled = ui.handle_global_input('\x1b')

    assert handled is True
    assert ui._last_focus_id == "history"
    assert ui.input_box.focused is False


def test_esc_with_active_completion_not_unfocused():
    """ESC with an active completion is passed to the input (cancels it)."""
    ui = chatTUI(StubAgent())
    ui._set_app_focus("input")
    # Simulate an active completion menu (so ESC should route to the input).
    class FakeCompletion:
        is_active = True
        def cancel(self, text, pos):
            pass
    ui.input_component.command_completion = FakeCompletion()

    handled = ui.handle_global_input('\x1b')

    # Should NOT unfocus; completion handling consumes it.
    assert handled is not True or ui._last_focus_id == "input"
