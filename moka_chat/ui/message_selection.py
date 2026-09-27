"""Mouse text selection in the chat transcript.

The selection works on rendered cells, like a terminal's: two points
``(row, col)`` where ``row`` is a transcript row (the panel's virtual row, so
scrolling mid-drag keeps it) and ``col`` a column relative to the panel. Each
message keeps its rendered rows in its box's sub-buffer, so the highlighted
cells and the copied characters are the same thing — tables, code blocks and
wide characters come out as displayed. Only a message's text area counts
(not the gutter bar or padding); a gap between messages copies as a blank
line. The selection stays after the drag until ``c`` copies it (``take``)
or focus moves (the panel clears it).
"""
from __future__ import annotations

import bisect
from typing import Optional

from moka_chat.ui.tui.layout_utils import strip_ansi

Point = tuple[int, int]


class MessageSelection:
    """Selection state + operations for one :class:`ChatHistoryPanel`."""

    def __init__(self, panel):
        self.panel = panel
        self.anchor: Optional[Point] = None  # where the drag started
        self.head: Optional[Point] = None    # where the pointer is now
        self.dragging = False

    @property
    def has_selection(self) -> bool:
        """True once the pointer moved: a plain click selects nothing."""
        return self.anchor is not None and self.head != self.anchor

    def clear(self) -> None:
        self.anchor = self.head = None
        self.dragging = False

    # -- drag lifecycle ------------------------------------------------

    def start(self, point: Point) -> None:
        self.anchor = self.head = point
        self.dragging = True
        self.panel._request_repaint()

    def extend(self, point: Point) -> None:
        if self.dragging and point != self.head:
            self.head = point
            self.panel._request_repaint()

    def end(self) -> None:
        """Finish the drag; a plain click leaves no selection."""
        self.dragging = False
        if not self.has_selection:
            self.clear()

    def take(self) -> Optional[str]:
        """The selected text, clearing the selection (``c``)."""
        text = self.get_text()
        self.clear()
        self.panel._request_repaint()
        return text

    # -- geometry ------------------------------------------------------

    def _ordered(self) -> tuple[Point, Point]:
        return min(self.anchor, self.head), max(self.anchor, self.head)

    def _row_cells(self, row: int):
        """``(cells, box_left, first, last)`` for a transcript row: the message's
        rendered row, the panel column of its first cell, and its text columns
        (panel-relative, inclusive). ``None`` for a gap, a row outside the text
        area, or a message never rendered."""
        panel = self.panel
        starts, ends, _ = panel._row_index()
        index = bisect.bisect_right(starts, row) - 1
        if index < 0 or row >= ends[index]:
            return None
        msg = panel.messages[index]
        box = msg.get_component()
        child = getattr(box, "child", box)
        local = row - starts[index]
        if box.subbuffer is None or local >= child.height or local >= len(box.subbuffer.cells):
            return None
        box_left = msg.left_margin
        first = box_left + (child.x - box.x)
        return box.subbuffer.cells[local], box_left, first, first + child.width - 1

    def _spans(self):
        """Yield ``(row, cells, box_left, lo, hi)`` per selected row; ``cells``
        is ``None`` on a row with no text."""
        (r0, c0), (r1, c1) = self._ordered()
        for row in range(r0, r1 + 1):
            found = self._row_cells(row)
            if found is None:
                yield row, None, 0, 0, -1
                continue
            cells, box_left, first, last = found
            lo = max(first, c0) if row == r0 else first
            hi = min(last, c1) if row == r1 else last
            yield row, cells, box_left, lo, hi

    # -- text ----------------------------------------------------------

    def get_text(self) -> Optional[str]:
        """The selected characters, rows joined by newlines."""
        if not self.has_selection:
            return None
        lines = []
        for _row, cells, box_left, lo, hi in self._spans():
            chars = []
            if cells is not None:
                for col in range(lo, hi + 1):
                    x = col - box_left
                    if 0 <= x < len(cells) and not cells[x].is_wide_char_continuation:
                        chars.append(cells[x].char)
            lines.append(strip_ansi("".join(chars)).rstrip())
        text = "\n".join(lines).strip("\n")
        return text or None

    # -- highlight overlay --------------------------------------------

    def render(self, buffer, start_y: int) -> None:
        """Reverse the selected cells; ``start_y`` is the first visible row."""
        if not self.has_selection:
            return
        panel = self.panel
        for row, cells, _box_left, lo, hi in self._spans():
            y = panel.y + row - start_y
            if cells is None or not (panel.y <= y < panel.y + panel.height) or y >= buffer.height:
                continue
            for col in range(lo, hi + 1):
                x = panel.x + col
                if 0 <= x < buffer.width:
                    buffer.cells[y][x].reverse = True
