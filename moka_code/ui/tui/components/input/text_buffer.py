"""Text buffer state management for input component."""

import re
from dataclasses import dataclass

# Complete image markers restored from a sent message (or typed as one paste)
# behave as one unit: an arrow key steps over them, Backspace removes them.
_IMAGE_TOKEN_RE = re.compile(r"\[image #\d+\]")


@dataclass
class Token:
    """A visible run of text that edits as one unit.

    ``value`` is what submission turns the token back into. For image markers
    it is the marker itself; for a collapsed paste it is the original text.
    """

    start: int
    end: int
    kind: str
    value: str


class TextBuffer:
    """Manages text content, atomic tokens and cursor position."""

    def __init__(self, text: str = ""):
        self._text = text
        self._cursor_pos = 0
        self._tokens: list[Token] = []
        self._index_image_tokens()

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str):
        self._text = value
        self._tokens = []
        # Ensure cursor stays in bounds
        self._cursor_pos = min(self._cursor_pos, len(self._text))
        self._index_image_tokens()

    @property
    def cursor_pos(self) -> int:
        return self._cursor_pos

    @cursor_pos.setter
    def cursor_pos(self, value: int):
        value = max(0, min(value, len(self._text)))
        self._cursor_pos = self._snap_out_of_token(value)

    # -- tokens ---------------------------------------------------------

    def _index_image_tokens(self) -> None:
        for match in _IMAGE_TOKEN_RE.finditer(self._text):
            marker = match.group(0)
            self._tokens.append(Token(match.start(), match.end(), "image", marker))

    def _token_containing(self, pos: int):
        for token in self._tokens:
            if token.start <= pos < token.end:
                return token
        return None

    def _token_at_boundary(self, pos: int):
        """Token ending at *pos*, or containing it when the cursor is inside."""
        for token in self._tokens:
            if token.end == pos or token.start < pos < token.end:
                return token
        return None

    def _token_starting_at(self, pos: int):
        for token in self._tokens:
            if token.start == pos or token.start < pos < token.end:
                return token
        return None

    def _snap_out_of_token(self, pos: int) -> int:
        token = self._token_containing(pos)
        if token is None:
            return pos
        # Keep the cursor outside a token. Pick the nearer boundary.
        return token.start if pos - token.start < token.end - pos else token.end

    def insert_token(self, label: str, value: str, kind: str) -> None:
        """Insert *label* at the cursor and remember *value* for submission."""
        start = self._cursor_pos
        self.insert(label)
        self._tokens.append(Token(start, self._cursor_pos, kind, value))
        self._tokens.sort(key=lambda token: token.start)

    def expanded_text(self) -> str:
        """The submitted text: each token replaced by its original value."""
        if not self._tokens:
            return self._text
        pieces: list[str] = []
        pos = 0
        for token in sorted(self._tokens, key=lambda item: item.start):
            pieces.append(self._text[pos:token.start])
            pieces.append(token.value)
            pos = token.end
        pieces.append(self._text[pos:])
        return "".join(pieces)

    # -- editing --------------------------------------------------------

    def insert(self, text: str):
        """Insert text at cursor position."""
        if not text:
            return
        start = self._cursor_pos
        self._text = self._text[:start] + text + self._text[start:]
        self._cursor_pos += len(text)
        growth = len(text)
        for token in self._tokens:
            if token.start >= start:
                token.start += growth
                token.end += growth
            elif token.end > start:
                # Insertion inside a token: keep the token covering the text.
                token.end += growth

    def _delete_span(self, start: int, end: int):
        """Delete ``[start, end)`` and any token it touches, atomically."""
        if start >= end:
            return
        start = max(0, start)
        end = min(len(self._text), end)
        touched = [t for t in self._tokens if t.start < end and t.end > start]
        if touched:
            start = min([start] + [t.start for t in touched])
            end = max([end] + [t.end for t in touched])
        self._tokens = [t for t in self._tokens if not (t.start < end and t.end > start)]
        removed = end - start
        self._text = self._text[:start] + self._text[end:]
        for token in self._tokens:
            if token.start >= end:
                token.start -= removed
                token.end -= removed
        self._cursor_pos = start

    def delete_backward(self, count: int = 1):
        """Delete characters before cursor; a token deletes as one unit."""
        if self._cursor_pos <= 0:
            return
        token = self._token_at_boundary(self._cursor_pos)
        if token is not None:
            self._delete_span(token.start, token.end)
            return
        self._delete_span(self._cursor_pos - min(count, self._cursor_pos), self._cursor_pos)

    def delete_forward(self):
        """Delete the character or token after the cursor."""
        if self._cursor_pos >= len(self._text):
            return
        token = self._token_starting_at(self._cursor_pos)
        if token is not None:
            self._delete_span(token.start, token.end)
            return
        self._delete_span(self._cursor_pos, self._cursor_pos + 1)

    def delete_word_backward(self):
        """Delete word before cursor."""
        if self._cursor_pos <= 0:
            return
        i = self._cursor_pos
        while i > 0 and self._text[i - 1].isspace():
            i -= 1
        while i > 0 and not self._text[i - 1].isspace():
            i -= 1
        self._delete_span(i, self._cursor_pos)

    def move_cursor_left(self):
        """Move cursor one position left, stepping over tokens."""
        if self._cursor_pos <= 0:
            return
        token = self._token_containing(self._cursor_pos - 1)
        self._cursor_pos = token.start if token is not None else self._cursor_pos - 1

    def move_cursor_right(self):
        """Move cursor one position right, stepping over tokens."""
        if self._cursor_pos >= len(self._text):
            return
        token = self._token_containing(self._cursor_pos)
        self._cursor_pos = token.end if token is not None else self._cursor_pos + 1

    def move_cursor_word_left(self):
        """Move cursor to start of previous word, stepping over tokens."""
        if self._cursor_pos <= 0:
            return
        i = self._cursor_pos - 1
        while i > 0 and self._text[i - 1].isspace():
            i -= 1
        while i > 0 and not self._text[i - 1].isspace():
            i -= 1
        token = self._token_containing(i)
        self._cursor_pos = token.start if token is not None else i

    def move_cursor_word_right(self):
        """Move cursor to end of next word, stepping over tokens."""
        if self._cursor_pos >= len(self._text):
            return
        i = self._cursor_pos
        while i < len(self._text) and self._text[i].isspace():
            i += 1
        while i < len(self._text) and not self._text[i].isspace():
            i += 1
        token = self._token_containing(i)
        self._cursor_pos = token.end if token is not None else i

    def clear(self):
        """Clear all text and reset cursor."""
        self._text = ""
        self._cursor_pos = 0
        self._tokens = []

    def is_empty(self) -> bool:
        """Check if buffer is empty."""
        return len(self._text.strip()) == 0
