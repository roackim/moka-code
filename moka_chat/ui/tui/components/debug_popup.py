from typing import Optional, Any
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.components.box import Box
from moka_chat.ui.tui.components.debug_panel import DebugLogPanel
from moka_chat.ui.tui.components.popup import PopupAction
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.events import KeyEvent, MouseEvent
from moka_chat.ui.tui.colors import theme


# Reuse the same close action as the regular popup
_DEBUG_CLOSE = PopupAction("Esc", "close")


class DebugPopup(Component):
    """A floating debug log panel as a compositor overlay.
    
    Wraps a DebugLogPanel in a Box, positioned at the bottom of the terminal.
    Unlike the regular Popup, it doesn't consume all input — it only handles
    Escape to close and mouse scroll, letting other input pass through.
    
    The DebugLogPanel auto-scrolls to bottom on new entries.
    """
    
    def __init__(self,
                 debug_panel: DebugLogPanel,
                 compositor: Optional[Any] = None,
                 title: str = "debug console"):
        super().__init__()
        self.debug_panel = debug_panel
        self.is_visible = False
        self._box = Box(debug_panel, title=title, fg=debug_panel.frame_color,
                        focused=True, actions=[_DEBUG_CLOSE])
        self.compositor = compositor
        self._registered_with_compositor = False
        self._height_ratio = 0.3  # 30% of terminal height
    
    def set_compositor(self, compositor):
        self.compositor = compositor

    def refresh_theme(self) -> None:
        """Re-resolve theme-derived colors after a theme switch."""
        self._box.fg = self.debug_panel.frame_color
        self._box.bg = theme.get_bg()
        self._box.mark_changed()
    
    def _update_compositor_registration(self):
        if not self.compositor:
            return
        if self.is_visible and not self._registered_with_compositor:
            self.compositor.add_overlay(self)
            self._registered_with_compositor = True
        elif not self.is_visible and self._registered_with_compositor:
            self.compositor.remove_overlay(self)
            self._registered_with_compositor = False
    
    def toggle(self):
        """Toggle visibility."""
        if self.is_visible:
            self.hide()
        else:
            self.show()
    
    def show(self):
        self.is_visible = True
        self._layout()
        self._update_compositor_registration()
        if self.compositor:
            self.compositor.request_render()
    
    def hide(self):
        was_visible = self.is_visible
        self.is_visible = False
        self._update_compositor_registration()
        if was_visible and self.compositor:
            self.compositor.request_render()
    
    def _layout(self):
        """Position at the bottom of the terminal."""
        if not self.compositor:
            return
        term_w = self.compositor.width
        term_h = self.compositor.height
        h = max(4, int(term_h * self._height_ratio))
        self.x = 0
        self.y = term_h - h
        self.width = term_w
        self.height = h
        self._box.set_layout(self.x, self.y, self.width, self.height)
    
    def handle_input(self, event: Any) -> bool:
        """Handle Escape and mouse scroll only — don't consume all input."""
        if not self.is_visible:
            return False
        
        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            if key == '\x1b':  # Escape closes
                self.hide()
                return True
        
        if isinstance(event, MouseEvent):
            if event.pressed and not event.drag:
                # Check if click is inside the popup
                if self.y <= event.y < self.y + self.height and self.x <= event.x < self.x + self.width:
                    # Action bar click (bottom border)
                    bottom_y = self.y + self.height - 1
                    if event.y == bottom_y and self._box._action_hit_regions:
                        for start, end, action in self._box._action_hit_regions:
                            abs_start = self.x + start
                            abs_end = self.x + end
                            if abs_start <= event.x < abs_end:
                                self.hide()
                                return True
                    
                    # Mouse scroll inside the popup
                    if event.button == 64:  # Scroll up
                        if hasattr(self.debug_panel, 'auto_scroll_bottom'):
                            self.debug_panel.auto_scroll_bottom = False
                        return True
                    elif event.button == 65:  # Scroll down
                        # Re-enable auto-scroll when scrolling to bottom
                        return True
        
        # Don't consume other input — let it pass through to the rest of the UI
        return False
    
    def render(self, buffer: Buffer):
        if not self.is_visible:
            return
        
        self._layout()
        self._box.render(buffer)
