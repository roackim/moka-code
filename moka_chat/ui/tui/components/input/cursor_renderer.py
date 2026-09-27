"""Cursor rendering with animation for input component."""

import time
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.colors import theme


class CursorRenderer:
    """Handles cursor rendering with pulsating animation."""
    
    def __init__(self, config_getter):
        self.get_config = config_getter  # Callable that returns config
        self.last_input_time = time.time()  # Start with solid cursor
        self.cursor_visible = True
    
    def mark_input(self):
        """Mark that input was received (for pulse delay)."""
        self.last_input_time = time.time()
        self.cursor_visible = True
    
    def blink_interval(self) -> float:
        config = self.get_config()
        if config and hasattr(config, 'ui_cursor_pulse_delay'):
            return float(config.ui_cursor_pulse_delay)
        return 0.5
    
    def pulse_delay(self) -> float:
        config = self.get_config()
        if config and hasattr(config, 'ui_cursor_pulse_delay'):
            return float(config.ui_cursor_pulse_delay)
        return 0.5
    
    @property
    def is_visible(self) -> bool:
        """Current cursor visibility given elapsed time since last input."""
        now = time.time()
        if now - self.last_input_time < self.pulse_delay():
            return True
        # Phase of the blink: half the interval on, half off.
        period = max(0.1, self.blink_interval())
        elapsed = now - self.last_input_time - self.pulse_delay()
        return int(elapsed / period) % 2 == 0
    
    def advance(self, parent_box=None) -> bool:
        """Recompute visibility from wall-clock time; redraw on change.

        Returns True when the visibility state changed so the caller should
        mark the parent dirty. Used on idle ticks so the cursor blinks even
        when no content changed.
        """
        visible = self.is_visible
        if visible != self.cursor_visible:
            self.cursor_visible = visible
            if parent_box is not None and hasattr(parent_box, 'mark_changed'):
                parent_box.mark_changed()
            return True
        return False
    
    def render(self, buffer: Buffer, cursor_row: int, cursor_col: int, 
               x: int, y: int, scroll_y: int, height: int, parent_box=None) -> bool:
        """Render the cursor at the given position.
        
        Returns:
            bool: True if cursor state changed (for marking parent dirty)
        """
        screen_cursor_row = cursor_row - scroll_y
        
        # Only render if cursor is visible in viewport
        if not (0 <= screen_cursor_row < height):
            return False
        
        cx = x + cursor_col
        cy = y + screen_cursor_row
        
        # Bounds check
        if not (0 <= cx < buffer.width and 0 <= cy < buffer.height):
            return False
        
        now = time.time()
        pulse_delay = self.pulse_delay()
        blink_interval = self.blink_interval()
        state_changed = False
        
        # Keep cursor solid for a moment after input, then blink from
        # wall-clock time so an idle tick and a redraw agree on the phase.
        if now - self.last_input_time < pulse_delay:
            visible = True
        else:
            elapsed = now - self.last_input_time - pulse_delay
            visible = int(elapsed / max(0.1, blink_interval)) % 2 == 0
        if visible != self.cursor_visible:
            self.cursor_visible = visible
            state_changed = True
        
        # Render cursor if visible
        if self.cursor_visible:
            curr_cell = buffer.cells[cy][cx]
            char_to_show = curr_cell.char or " "
            buffer.set(cx, cy, char_to_show, fg=theme.DEFAULT, bg=theme.get_bg(), bold=True, reverse=True)
        
        # Mark parent Box as changed if state changed
        if state_changed and parent_box and hasattr(parent_box, 'mark_changed'):
            parent_box.mark_changed()
        
        return state_changed
