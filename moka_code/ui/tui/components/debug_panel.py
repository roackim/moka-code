from collections import deque
from typing import Optional
from moka_code.ui.tui.components.text import TextComponent
from moka_code.ui.tui.colors import theme, RGB

class DebugLogPanel(TextComponent):
    """
    A scrolling debug log panel with configurable colors and padding.
    """
    def __init__(self, 
                 max_lines: int = 1000, 
                 max_line_length: int = 300,
                 frame_color: RGB = None,
                 content_color: RGB = None,
                 left_pad: int = 1,
                 right_pad: int = 0,
                 id: Optional[str] = None):
        # Set content color, defaulting to MUTED
        self.content_color = content_color if content_color is not None else theme.MUTED
        self.frame_color = frame_color if frame_color is not None else theme.DEFAULT
        
        super().__init__("", id=id, auto_scroll_bottom=True, fg=self.content_color)
        self.max_lines = max_lines
        self.max_line_length = max_line_length
        self.left_pad = left_pad
        self.right_pad = right_pad
        self.lines = deque(maxlen=max_lines)
        
    def log(self, message: str):
        """Add a message to the log (single line only, newlines escaped)."""
        # Escape newlines to show as single line
        single_line = message.replace('\n', '\\n').replace('\r', '')
        
        # Apply padding
        padded_line = (" " * self.left_pad) + single_line + (" " * self.right_pad)
        
        # Truncate if too long (accounting for padding)
        total_max = self.max_line_length + self.left_pad + self.right_pad
        if len(padded_line) > total_max:
            # Truncate and add ellipsis before right padding
            truncate_at = self.max_line_length - 3  # Leave room for "..."
            padded_line = (" " * self.left_pad) + single_line[:truncate_at] + "..." + (" " * self.right_pad)
        
        self.lines.append(padded_line)
        self.update("\n".join(self.lines))
        
    def clear(self):
        """Clear the log."""
        self.lines.clear()
        self.update("")
