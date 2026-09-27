"""Tests for the DebugPopup overlay component."""
import pytest
from moka_chat.ui.tui.components.debug_popup import DebugPopup
from moka_chat.ui.tui.components.debug_panel import DebugLogPanel
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.terminal import MouseEvent


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


class TestDebugPopup:
    def test_toggle(self):
        comp = FakeCompositor()
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        
        assert not popup.is_visible
        assert popup not in comp.overlays
        
        popup.toggle()
        assert popup.is_visible
        assert popup in comp.overlays
        
        popup.toggle()
        assert not popup.is_visible
        assert popup not in comp.overlays

    def test_show_hide(self):
        comp = FakeCompositor()
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        
        popup.show()
        assert popup.is_visible
        assert popup in comp.overlays
        
        popup.hide()
        assert not popup.is_visible
        assert popup not in comp.overlays

    def test_escape_closes(self):
        comp = FakeCompositor()
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        popup.show()
        
        result = popup.handle_input('\x1b')
        assert result is True
        assert not popup.is_visible

    def test_non_escape_not_consumed(self):
        """Regular keys should pass through — don't trap all input."""
        comp = FakeCompositor()
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        popup.show()
        
        # Regular characters should NOT be consumed
        assert popup.handle_input('a') is False
        assert popup.handle_input('\r') is False
        assert popup.handle_input('\x1b[A') is False  # arrow up

    def test_positioned_at_bottom(self):
        comp = FakeCompositor(80, 24)
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        popup.show()
        
        # Should be at the bottom of the terminal
        assert popup.y == 24 - popup.height
        assert popup.x == 0
        assert popup.width == 80

    def test_action_bar_click_closes(self):
        comp = FakeCompositor(80, 24)
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        popup.show()
        
        # Render to populate hit regions
        buf = Buffer(80, 24)
        popup.render(buf)
        
        bottom_y = popup.y + popup.height - 1
        assert len(popup._box._action_hit_regions) > 0
        
        start, end, action = popup._box._action_hit_regions[0]
        click_x = popup.x + (start + end) // 2
        
        evt = MouseEvent(x=click_x, y=bottom_y, button=0, pressed=True)
        result = popup.handle_input(evt)
        assert result is True
        assert not popup.is_visible

    def test_log_entries_appear_in_render(self):
        comp = FakeCompositor(80, 24)
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        popup.show()
        
        panel.log("test message")
        
        buf = Buffer(80, 24)
        popup.render(buf)
        
        # The content area should contain "test message"
        content_y = popup.y + 1  # inside top border
        found = False
        for col in range(popup.x + 1, popup.x + popup.width - 1):
            if buf.cells[content_y][col].char == 't':
                # Check if "test" starts here
                chars = ""
                for c in range(col, min(col + 12, popup.x + popup.width - 1)):
                    chars += buf.cells[content_y][c].char
                if 'test' in chars:
                    found = True
                    break
        assert found

    def test_input_outside_popup_not_consumed(self):
        """Mouse clicks outside the popup should not be consumed."""
        comp = FakeCompositor(80, 24)
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        popup.show()
        
        # Click above the popup (in the main UI area)
        evt = MouseEvent(x=40, y=5, button=0, pressed=True)
        result = popup.handle_input(evt)
        assert result is False  # not consumed

    def test_height_ratio(self):
        comp = FakeCompositor(80, 24)
        panel = DebugLogPanel()
        popup = DebugPopup(panel, compositor=comp)
        popup.show()
        
        # Default 30% ratio: 24 * 0.3 = 7.2 -> 7
        assert popup.height == 7

    def test_no_compositor_still_works(self):
        panel = DebugLogPanel()
        popup = DebugPopup(panel)
        popup.show()
        assert popup.is_visible
        
        popup.handle_input('\x1b')
        assert not popup.is_visible
