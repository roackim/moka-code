"""Text selection within the chat transcript.

Owns the drag state, coordinate math, and highlight overlay for selecting text
inside a rendered message. Extracted from ``ChatHistoryPanel`` so the panel
only deals with layout/scroll and message focus.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from wcwidth import wcswidth


@dataclass
class SelectionState:
    """Tracks text selection within a message."""
    msg: Any                    # The Message object
    start_line: int = 0         # Display line index (0-based within message component)
    start_col: int = 0          # Display column index (0-based within wrapped line)
    end_line: int = 0
    end_col: int = 0

    def get_normalized(self) -> tuple[int, int, int, int]:
        """Return (start_line, start_col, end_line, end_col) with start <= end."""
        if (self.start_line, self.start_col) <= (self.end_line, self.end_col):
            return self.start_line, self.start_col, self.end_line, self.end_col
        return self.end_line, self.end_col, self.start_line, self.start_col


class MessageSelection:
    """Selection state + operations for one :class:`ChatHistoryPanel`."""

    # Drag repaints are throttled to cap the update rate.
    throttle_interval = 0.050

    def __init__(self, panel):
        self.panel = panel
        self.state: Optional[SelectionState] = None
        self.dragging = False
        self._last_update = 0.0

    # -- state ---------------------------------------------------------

    @property
    def has_selection(self) -> bool:
        return self.state is not None

    def clear(self):
        self.state = None
        self.dragging = False

    # -- drag lifecycle ------------------------------------------------

    def start(self, msg_index: int, display_line: int, col: int):
        msg = self.panel.messages[msg_index]
        self.state = SelectionState(
            msg=msg,
            start_line=display_line, start_col=col,
            end_line=display_line, end_col=col,
        )
        self.dragging = True
        self._last_update = time.monotonic()
        self.panel._request_repaint()

    def extend(self, msg_index: int, display_line: int, col: int) -> bool:
        """Extend the active selection. Returns False when throttled/ignored."""
        if self.state is None:
            return False
        if not self.dragging or self.state.msg is not self.panel.messages[msg_index]:
            return False
        now = time.monotonic()
        if now - self._last_update < self.throttle_interval:
            return False  # Too soon — skip this frame, keep consuming
        self._last_update = now
        self.state.end_line = display_line
        self.state.end_col = col
        self.panel._request_repaint()
        return True

    def end(self):
        self.dragging = False

    # -- text extraction ----------------------------------------------

    def get_text(self) -> Optional[str]:
        """Extract the selected text from the component's rendered lines."""
        sel = self.state
        if sel is None:
            return None

        box = sel.msg.get_component()
        # Unwrap Box → inner component (MarkdownComponent or TextComponent)
        component = getattr(box, 'child', box)
        wrapped_lines = getattr(component, '_wrapped_lines', None)
        if wrapped_lines is None:
            # TextComponent: fall back to _lines
            wrapped_lines = getattr(component, '_lines', None)
            if wrapped_lines is None:
                return None

        sl, sc, el, ec = sel.get_normalized()
        sl = max(0, min(sl, len(wrapped_lines) - 1))
        el = max(0, min(el, len(wrapped_lines) - 1))

        parts = []
        for line_i in range(sl, el + 1):
            if line_i >= len(wrapped_lines):
                break

            line = wrapped_lines[line_i]
            if line_i == sl and line_i == el:
                parts.append(self._extract_line_text(line, sc, ec))
            elif line_i == sl:
                parts.append(self._extract_line_text(line, sc, None))
            elif line_i == el:
                parts.append(self._extract_line_text(line, 0, ec))
            else:
                parts.append(self._extract_line_text(line, 0, None))

        # Each selected line ends with a newline so pasting elsewhere keeps
        # the line breaks (the terminal's rendered rows are display-wrapped,
        # so without trailing newlines the copy collapses into one blob).
        text = "".join(part + "\n" for part in parts)
        return text if text else None

    @staticmethod
    def _extract_line_text(line, start_col: int, end_col: Optional[int]) -> str:
        """Extract plain text from a wrapped line (list of StyledSegments or strings)."""
        if isinstance(line, str):
            return line[start_col:end_col]

        text_parts = []
        col = 0
        for seg in line:
            seg_w = seg.display_width
            seg_end_col = col + seg_w

            if end_col is not None and col >= end_col:
                break
            if seg_end_col > start_col:
                if col >= start_col and (end_col is None or seg_end_col <= end_col):
                    text_parts.append(seg.text)
                else:
                    # Partial overlap — character-level extraction
                    char_col = col
                    for ch in seg.text:
                        cw = wcswidth(ch)
                        if cw < 0:
                            cw = 1
                        ch_end = char_col + cw
                        if char_col >= start_col and (end_col is None or ch_end <= end_col):
                            text_parts.append(ch)
                        char_col = ch_end

            col = seg_end_col

        return "".join(text_parts)

    @staticmethod
    def _resolve_column(line, screen_x: int) -> int:
        """Map a screen x offset to a display column index within a wrapped line.

        Uses segment display_width for fast skipping; only walks characters
        when the target falls inside a segment.
        """
        if isinstance(line, str):
            return min(screen_x, len(line))

        col = 0
        for seg in line:
            seg_w = seg.display_width
            if col + seg_w <= screen_x:
                col += seg_w
                continue
            if col + seg_w > screen_x:
                seg_col = col
                for ch in seg.text:
                    cw = wcswidth(ch)
                    if cw < 0:
                        cw = 1
                    if seg_col + cw > screen_x:
                        return seg_col
                    seg_col += cw
                return seg_col
            col += seg_w
        return col

    def screen_to_display_col(self, msg, box, content_y: int, screen_x: int) -> Optional[int]:
        """Convert a screen x to a display column within a message's content.

        The content origin comes from the laid-out inner component, so ``Box``
        remains the single owner of gutter/padding geometry.
        """
        md_component = getattr(box, 'child', box)
        left_pad = getattr(md_component, 'left_pad', 0)
        wrapped_x = screen_x - (md_component.x + left_pad)

        if wrapped_x < 0:
            return None

        wrapped_lines = getattr(md_component, '_wrapped_lines', None)
        if wrapped_lines is None:
            wrapped_lines = getattr(md_component, '_lines', None)
        if wrapped_lines is None or content_y >= len(wrapped_lines):
            return None

        return self._resolve_column(wrapped_lines[content_y], wrapped_x)

    # -- highlight overlay --------------------------------------------

    def render(self, buffer, msg, box, box_y: int, _box_w: int, _box_h: int):
        """Overlay a highlight on the selected range within a message box."""
        sel = self.state
        if sel is None or sel.msg is not msg:
            return

        panel = self.panel
        sl, sc, el, ec = sel.get_normalized()

        md_component = getattr(box, 'child', box)  # unwrap Box → inner component
        wrapped_lines = getattr(md_component, '_wrapped_lines', None)
        if wrapped_lines is None:
            wrapped_lines = getattr(md_component, '_lines', None)
        if wrapped_lines is None:
            return

        left_pad = getattr(md_component, 'left_pad', 0)
        content_abs_y = box_y + 1  # skip top border

        for line_i in range(sl, el + 1):
            if line_i >= len(wrapped_lines):
                break

            screen_y = content_abs_y + line_i
            if screen_y < panel.y or screen_y >= panel.y + panel.height:
                continue  # clipped off-screen

            line = wrapped_lines[line_i]
            col_start = sc if line_i == sl else 0
            col_end = ec if line_i == el else None

            screen_x_base = md_component.x + left_pad
            current_screen_x = screen_x_base
            current_col = 0

            if isinstance(line, str):
                line_col_end = col_end if col_end is not None else len(line)
                if col_start < line_col_end:
                    start_x = current_screen_x + col_start
                    end_x = current_screen_x + min(line_col_end, len(line))
                    for x in range(max(0, start_x), min(buffer.width, end_x)):
                        if 0 <= screen_y < buffer.height:
                            buffer.cells[screen_y][x].reverse = True
            else:
                for seg in line:
                    seg_w = seg.display_width
                    seg_col_end = current_col + seg_w

                    if col_end is not None and current_col >= col_end:
                        break
                    if seg_col_end <= col_start:
                        current_col = seg_col_end
                        current_screen_x += seg_w
                        continue

                    seg_start_in_sel = current_col >= col_start
                    seg_end_in_sel = col_end is None or seg_col_end <= col_end

                    if seg_start_in_sel and seg_end_in_sel:
                        for dx in range(seg_w):
                            x = current_screen_x + dx
                            if 0 <= x < buffer.width and 0 <= screen_y < buffer.height:
                                buffer.cells[screen_y][x].reverse = True
                    else:
                        char_col = current_col
                        char_x = current_screen_x
                        for ch in seg.text:
                            cw = wcswidth(ch)
                            if cw < 0:
                                cw = 1
                            in_range = char_col >= col_start and (col_end is None or char_col < col_end)
                            if in_range:
                                for dx in range(cw):
                                    x = char_x + dx
                                    if 0 <= x < buffer.width and 0 <= screen_y < buffer.height:
                                        buffer.cells[screen_y][x].reverse = True
                            char_col += cw
                            char_x += cw

                    current_col = seg_col_end
                    current_screen_x += seg_w
