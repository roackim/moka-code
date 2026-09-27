"""Scroll management for input component."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .text_buffer import TextBuffer
    from .coordinate_mapper import CoordinateMapper


class ScrollManager:
    """Manages scrolling logic for the input component."""
    
    def __init__(self, buffer, coord_mapper, height_getter):
        self.buffer = buffer  # TextBuffer
        self.coord_mapper = coord_mapper  # CoordinateMapper
        self.get_height = height_getter  # Callable that returns current viewport height
        self.scroll_y = 0
        self._last_cursor_pos = -1
    
    def _constrain_scroll(self):
        """Ensure scroll_y is within valid bounds."""
        lines = self.coord_mapper.get_wrapped_lines(self.buffer.text)
        height = self.get_height()
        max_scroll = max(0, len(lines) - height)
        self.scroll_y = max(0, min(self.scroll_y, max_scroll))
    
    def scroll_up(self):
        """Scroll up by one line."""
        if self.scroll_y > 0:
            self.scroll_y -= 1
        self._constrain_scroll()
    
    def scroll_down(self):
        """Scroll down by one line."""
        self.scroll_y += 1
        self._constrain_scroll()
    
    def scroll_to_cursor(self):
        """Scroll to show cursor after paste or large text insertion."""
        try:
            lines = self.coord_mapper.get_wrapped_lines(self.buffer.text)
            cursor_row, _ = self.coord_mapper.get_cursor_coords(
                self.buffer.text, self.buffer.cursor_pos
            )
            
            height = self.get_height()
            
            # If all content fits, scroll to top
            if len(lines) <= height:
                self.scroll_y = 0
            else:
                # Ensure cursor is visible with minimal scrolling
                # If cursor is above viewport, scroll up to show it
                if cursor_row < self.scroll_y:
                    self.scroll_y = cursor_row
                # If cursor is below viewport, scroll down just enough to show it
                elif cursor_row >= self.scroll_y + height:
                    self.scroll_y = cursor_row - height + 1
                # Otherwise cursor is already visible, don't scroll
        except Exception:
            # On error, just ensure cursor is visible
            pass
        finally:
            self._constrain_scroll()
    
    def ensure_cursor_visible(self, force: bool = False):
        """Ensure cursor is visible in viewport (during navigation)."""
        cursor_pos = self.buffer.cursor_pos
        
        # Only adjust if cursor has moved or if forced
        if force or cursor_pos != self._last_cursor_pos:
            cursor_row, _ = self.coord_mapper.get_cursor_coords(
                self.buffer.text, cursor_pos
            )
            
            height = self.get_height()
            
            if cursor_row < self.scroll_y:
                self.scroll_y = cursor_row
            elif cursor_row >= self.scroll_y + height:
                self.scroll_y = cursor_row - height + 1
            
            self._constrain_scroll()
            self._last_cursor_pos = cursor_pos
    
    def reset(self):
        """Reset scroll to top."""
        self.scroll_y = 0
        self._last_cursor_pos = -1
