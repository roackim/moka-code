from typing import Optional
from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.layout_utils import display_width, wrap_text

def paint_spans(buffer: Buffer, x: int, y: int, line: str, highlighter, bg, max_width: int) -> None:
    """Repaint the spans ``highlighter(line)`` returns (``(start, end, fg)``,
    character offsets) over a line already written at ``(x, y)``."""
    for start, end, fg in highlighter(line):
        col = display_width(line[:start])
        if col < max_width:
            buffer.write_str(x + col, y, line[start:end], fg=fg, bg=bg, max_width=max_width - col)


class TextComponent(Component):
    def __init__(self, text: str, id: Optional[str] = None, fg=None, bg=None, auto_scroll_bottom: bool = False):
        super().__init__(id)
        self.text = text
        # Optional ``line -> [(start, end, fg)]``: spans drawn in their own color.
        self.highlighter = None
        self._lines = text.splitlines()
        self.fg = fg
        self.bg = bg
        self.auto_scroll_bottom = auto_scroll_bottom
        
        if self.fg is None: self.fg = theme.DEFAULT
        if self.bg is None: self.bg = theme.get_bg()

    def render(self, buffer: Buffer):
        """
        Renders text lines with ANSI-aware clipping.
        Uses Buffer.write_str which handles absolute positioning and ensures
        ANSI sequences don't corrupt the grid layout.
        """
        lines = self._lines
        
        # If auto_scroll_bottom is enabled and there are more lines than height,
        # show the last lines that fit
        start_line = 0
        if self.auto_scroll_bottom and len(lines) > self.height:
            start_line = len(lines) - self.height

        end_line = min(len(lines), start_line + self.height)
        # Restrict to the buffer's clip band (set by Box for tail rastering).
        clip = getattr(buffer, "clip_rect", None)
        if clip is not None:
            clip_top = clip[1] - self.y
            clip_bottom = clip[1] + clip[3] - self.y
            start_line = max(start_line, clip_top)
            end_line = min(end_line, clip_bottom)

        for i in range(start_line, end_line):
            line_index = i - start_line
            buffer.write_str(self.x, self.y + line_index, lines[i], fg=self.fg, bg=self.bg, max_width=self.width)
            if self.highlighter is not None:
                paint_spans(buffer, self.x, self.y + line_index, lines[i],
                            self.highlighter, self.bg, self.width)

    def get_preferred_height(self, width: int) -> int:
        """Calculate height needed for wrapped text."""
        # Note: If this TextComponent doesn't wrap itself (like ChatHistoryPanel currently does),
        # we still consider splitlines() count.
        return len(self._lines)

    def get_preferred_width(self) -> int:
        return max((len(line) for line in self._lines), default=0)

    def update(self, text: str):
        self.text = text
        self._lines = text.splitlines()
        self.mark_changed((self.x, self.y, self.width, self.height))


class Label(TextComponent):
    """Static text with explicit wrapping and alignment policies."""

    def __init__(self, text: str, id: Optional[str] = None, fg=None, bg=None,
                 wrap: bool = True, horizontal: str = "left",
                 vertical: str = "top"):
        if horizontal not in ("left", "center", "right"):
            raise ValueError("horizontal must be left, center, or right")
        if vertical not in ("top", "center", "bottom"):
            raise ValueError("vertical must be top, center, or bottom")
        super().__init__(text, id=id, fg=fg, bg=bg)
        self.wrap = wrap
        self.horizontal = horizontal
        self.vertical = vertical

    def _display_lines(self, width: Optional[int] = None) -> list[str]:
        if not self.wrap or not width or width <= 0:
            return list(self._lines)
        wrapped = wrap_text(self.text, width, first_line_padding=False)
        return wrapped.split("\n")

    def render(self, buffer: Buffer):
        lines = self._display_lines(self.width)
        if self.vertical == "bottom":
            start_y = self.y + max(0, self.height - len(lines))
        elif self.vertical == "center":
            start_y = self.y + max(0, (self.height - len(lines)) // 2)
        else:
            start_y = self.y

        for offset, line in enumerate(lines[:max(0, self.height)]):
            line_width = display_width(line)
            if self.horizontal == "right":
                start_x = self.x + max(0, self.width - line_width)
            elif self.horizontal == "center":
                start_x = self.x + max(0, (self.width - line_width) // 2)
            else:
                start_x = self.x
            buffer.write_str(start_x, start_y + offset, line,
                             fg=self.fg, bg=self.bg, max_width=self.width)

    def get_preferred_height(self, width: int) -> int:
        return len(self._display_lines(width))

    def get_preferred_width(self) -> int:
        return max((display_width(line) for line in self._lines), default=0)
