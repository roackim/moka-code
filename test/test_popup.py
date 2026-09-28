"""Tests for the Popup overlay component."""
import pytest
from moka_code.ui.tui.components.popup import Popup, PopupScreen
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.terminal import MouseEvent
from moka_code.ui.tui.navigation import ModalHost
from moka_code.ui.tui.focus import FocusScope
from moka_code.ui.tui.components.button import Button


class FakeCompositor:
    """Minimal compositor stub for testing."""
    def __init__(self, w=80, h=24):
        self.width = w
        self.height = h
        self.overlays = []
        self._render_requested = False

    def add_overlay(self, comp):
        self.overlays.append(comp)

    def remove_overlay(self, comp):
        self.overlays.remove(comp)

    def request_render(self):
        self._render_requested = True


class FocusCompositor(FakeCompositor):
    def __init__(self, focus_scope):
        super().__init__()
        self.event_router = type("Router", (), {"focus_scope": focus_scope})()


class TestPopup:
    def test_popup_screen_uses_modal_host_lifecycle(self):
        comp = FakeCompositor()
        popup = Popup()
        host = ModalHost(comp)
        screen = PopupScreen(popup, "title", "content")
        popup.set_compositor(comp)

        host.present_screen(screen)

        assert host.current is popup
        assert popup.is_visible
        assert popup in comp.overlays

        host.dismiss_screen(screen)

        assert host.current is None
        assert not popup.is_visible
        assert popup not in comp.overlays

    def test_show_hide(self):
        comp = FakeCompositor()
        popup = Popup(compositor=comp)
        
        assert not popup.is_visible
        assert len(comp.overlays) == 0
        
        popup.show("test", "line1\nline2")
        assert popup.is_visible
        assert popup._box.title == "test"
        assert popup._lines == ["line1", "line2"]
        assert popup in comp.overlays
        
        popup.hide()
        assert not popup.is_visible
        assert popup not in comp.overlays

    def test_popup_screen_forwards_content_padding(self):
        comp = FakeCompositor()
        popup = Popup(compositor=comp)
        screen = PopupScreen(popup, "test", "content", content_padding=1)

        screen.on_enter()

        assert popup._content_pad == 1
        assert popup._text.text == " content"

        screen.on_leave()

    def test_popup_suspends_and_restores_background_focus(self):
        background = Button("Background")
        scope = FocusScope([background])
        scope.enter()
        popup = Popup(FocusCompositor(scope))

        popup.show("Help", "content")
        assert scope.focused is None
        assert not background.focused

        popup.hide()
        assert scope.focused is background
        assert background.focused

    def test_center_positioning(self):
        comp = FakeCompositor(80, 24)
        popup = Popup(compositor=comp, max_width_ratio=0.5, max_height_ratio=0.5)
        popup.show("title", "short")
        
        # Should be centered
        assert popup.x == (80 - popup.width) // 2
        assert popup.y == (24 - popup.height) // 2

    def test_escape_hides(self):
        comp = FakeCompositor()
        popup = Popup(compositor=comp)
        popup.show("test", "content")
        
        consumed = popup.handle_input('\x1b')
        assert consumed is True
        assert not popup.is_visible

    def test_all_input_consumed_when_visible(self):
        comp = FakeCompositor()
        popup = Popup(compositor=comp)
        popup.show("test", "content")
        
        # Every key is consumed when popup is open
        assert popup.handle_input('a') is True
        assert popup.handle_input('\r') is True
        assert popup.handle_input('\x1b[A') is True

    def test_input_ignored_when_hidden(self):
        popup = Popup()
        assert popup.handle_input('\x1b') is False

    def test_arrow_scroll(self):
        comp = FakeCompositor(80, 10)
        popup = Popup(compositor=comp, max_height_ratio=0.5)
        lines = "\n".join(f"line {i}" for i in range(20))
        popup.show("scroll test", lines)
        
        assert popup._scroll_offset == 0
        
        # Down arrow scrolls
        popup.handle_input('\x1b[B')
        assert popup._scroll_offset == 1
        
        # Up arrow scrolls back
        popup.handle_input('\x1b[A')
        assert popup._scroll_offset == 0
        
        # Can't scroll above 0
        popup.handle_input('\x1b[A')
        assert popup._scroll_offset == 0

    def test_mouse_scroll(self):
        from moka_code.ui.tui.terminal import MouseEvent
        comp = FakeCompositor(80, 24)
        popup = Popup(compositor=comp)
        lines = "\n".join(f"line {i}" for i in range(30))
        popup.show("scroll test", lines)
        
        assert popup._scroll_offset == 0
        
        # Mouse scroll up (button 64) scrolls content up (offset decreases... but at 0, no change)
        # Actually scroll down first so we have room to scroll up
        popup._scroll_offset = 10
        
        # Mouse scroll up: scrolls by 3
        evt_up = MouseEvent(x=40, y=12, button=64, pressed=True)
        popup.handle_input(evt_up)
        assert popup._scroll_offset == 7
        
        # Mouse scroll down: scrolls by 3
        evt_down = MouseEvent(x=40, y=12, button=65, pressed=True)
        popup.handle_input(evt_down)
        assert popup._scroll_offset == 10

    def test_mouse_scroll_clamps(self):
        comp = FakeCompositor(80, 10)
        popup = Popup(compositor=comp, max_height_ratio=0.5)
        lines = "\n".join(f"line {i}" for i in range(30))
        popup.show("scroll test", lines)
        
        max_scroll = max(0, len(popup._lines) - popup._visible_content_height())
        
        # Scroll to near end
        popup._scroll_offset = max_scroll - 1
        
        # Scroll down by 3 should clamp to max
        evt_down = MouseEvent(x=40, y=5, button=65, pressed=True)
        popup.handle_input(evt_down)
        assert popup._scroll_offset == max_scroll
        
        # Scroll up from 0 should clamp to 0
        popup._scroll_offset = 1
        evt_up = MouseEvent(x=40, y=5, button=64, pressed=True)
        popup.handle_input(evt_up)
        assert popup._scroll_offset == 0

    def test_mouse_drag_ignored(self):
        """Mouse drags (not wheel) should be consumed but not scroll."""
        comp = FakeCompositor(80, 24)
        popup = Popup(compositor=comp)
        lines = "\n".join(f"line {i}" for i in range(30))
        popup.show("test", lines)
        
        popup._scroll_offset = 5
        # A drag event with button 0 (left click drag) should not change scroll
        evt_drag = MouseEvent(x=40, y=12, button=0, pressed=True, drag=True)
        result = popup.handle_input(evt_drag)
        assert result is True  # consumed
        assert popup._scroll_offset == 5  # unchanged

    def test_action_bar_click_closes(self):
        """Clicking the action bar should close the popup."""
        comp = FakeCompositor(80, 24)
        popup = Popup(compositor=comp)
        popup.show("test", "content\nmore content")
        
        # Render to populate hit regions
        buf = Buffer(80, 24)
        popup.render(buf)
        
        # Find the action bar hit region
        bottom_y = popup.y + popup.height - 1
        assert len(popup._box._action_hit_regions) > 0
        
        # Click in the middle of the first hit region
        start, end, action = popup._box._action_hit_regions[0]
        click_x = popup.x + (start + end) // 2
        
        evt_click = MouseEvent(x=click_x, y=bottom_y, button=0, pressed=True)
        result = popup.handle_input(evt_click)
        assert result is True
        assert not popup.is_visible

    def test_content_rendered_inside_borders(self):
        """Content should be rendered inside the Box borders."""
        comp = FakeCompositor(80, 24)
        popup = Popup(compositor=comp)
        popup.show("test", "hello world")
        
        buf = Buffer(80, 24)
        popup.render(buf)
        
        # Content starts immediately inside the border.
        content_y = popup.y + 1
        content_start = popup.x + 1
        
        # Should find 'h' from "hello world"
        assert buf.cells[content_y][content_start].char == 'h'

    def test_action_bar_rendered(self):
        """Bottom border should contain [Esc] close action."""
        comp = FakeCompositor(80, 24)
        popup = Popup(compositor=comp)
        popup.show("test", "content")
        
        buf = Buffer(80, 24)
        popup.render(buf)
        
        bottom_y = popup.y + popup.height - 1
        # Scan bottom row for action text
        bottom_chars = ""
        for col in range(popup.x, popup.x + popup.width):
            bottom_chars += buf.cells[bottom_y][col].char
        
        assert "[Esc] close" in bottom_chars

    def test_render_outputs_borders(self):
        comp = FakeCompositor(80, 24)
        popup = Popup(compositor=comp)
        popup.show("title", "hello world")
        
        buf = Buffer(80, 24)
        popup.render(buf)
        
        # Check top-left corner is a box corner
        x, y = popup.x, popup.y
        assert buf.cells[y][x].char == "┌"
        assert buf.cells[y][popup.x + popup.width - 1].char == "┐"
        # Bottom is now action bar, check corners
        assert buf.cells[popup.y + popup.height - 1][x].char == "└"
        assert buf.cells[popup.y + popup.height - 1][popup.x + popup.width - 1].char == "┘"

    def test_compositor_registration_auto(self):
        """Test that show/hide auto-registers with compositor."""
        comp = FakeCompositor()
        popup = Popup(compositor=comp)
        
        # Initially not registered
        assert popup not in comp.overlays
        
        popup.show("t", "c")
        assert popup in comp.overlays
        
        popup.hide()
        assert popup not in comp.overlays

    def test_no_compositor_still_works(self):
        """Popup should work even without a compositor (e.g. in tests)."""
        popup = Popup()
        popup.show("test", "content\nmore")
        assert popup.is_visible
        assert popup._lines == ["content", "more"]
        
        popup.handle_input('\x1b')
        assert not popup.is_visible

    def test_visible_content_height(self):
        """Visible content height excludes borders and Box content padding."""
        comp = FakeCompositor(80, 24)
        popup = Popup(compositor=comp)
        popup.show("test", "\n".join(f"line {i}" for i in range(30)))
        
        assert popup._visible_content_height() == popup.height - 2 - 2 * popup._box.padding_y
