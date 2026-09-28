"""Text buffer state management for input component."""

class TextBuffer:
    """Manages text content and cursor position."""
    
    def __init__(self, text: str = ""):
        self._text = text
        self._cursor_pos = 0
    
    @property
    def text(self) -> str:
        return self._text
    
    @text.setter
    def text(self, value: str):
        self._text = value
        # Ensure cursor stays in bounds
        self._cursor_pos = min(self._cursor_pos, len(self._text))
    
    @property
    def cursor_pos(self) -> int:
        return self._cursor_pos
    
    @cursor_pos.setter
    def cursor_pos(self, value: int):
        self._cursor_pos = max(0, min(value, len(self._text)))
    
    def insert(self, text: str):
        """Insert text at cursor position."""
        self._text = self._text[:self._cursor_pos] + text + self._text[self._cursor_pos:]
        self._cursor_pos += len(text)
    
    def delete_backward(self, count: int = 1):
        """Delete characters before cursor."""
        if self._cursor_pos > 0:
            count = min(count, self._cursor_pos)
            self._text = self._text[:self._cursor_pos - count] + self._text[self._cursor_pos:]
            self._cursor_pos -= count
    
    def delete_word_backward(self):
        """Delete word before cursor."""
        if self._cursor_pos > 0:
            # Find start of current word
            i = self._cursor_pos
            while i > 0 and self._text[i-1].isspace():
                i -= 1
            while i > 0 and not self._text[i-1].isspace():
                i -= 1
            
            self._text = self._text[:i] + self._text[self._cursor_pos:]
            self._cursor_pos = i
    
    def move_cursor_left(self):
        """Move cursor one position left."""
        if self._cursor_pos > 0:
            self._cursor_pos -= 1
    
    def move_cursor_right(self):
        """Move cursor one position right."""
        if self._cursor_pos < len(self._text):
            self._cursor_pos += 1
    
    def move_cursor_word_left(self):
        """Move cursor to start of previous word."""
        if self._cursor_pos > 0:
            i = self._cursor_pos - 1
            while i > 0 and self._text[i-1].isspace():
                i -= 1
            while i > 0 and not self._text[i-1].isspace():
                i -= 1
            self._cursor_pos = i
    
    def move_cursor_word_right(self):
        """Move cursor to end of next word."""
        if self._cursor_pos < len(self._text):
            i = self._cursor_pos
            while i < len(self._text) and self._text[i].isspace():
                i += 1
            while i < len(self._text) and not self._text[i].isspace():
                i += 1
            self._cursor_pos = i
    
    def clear(self):
        """Clear all text and reset cursor."""
        self._text = ""
        self._cursor_pos = 0
    
    def is_empty(self) -> bool:
        """Check if buffer is empty."""
        return len(self._text.strip()) == 0
