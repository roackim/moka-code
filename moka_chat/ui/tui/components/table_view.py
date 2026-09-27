from typing import Any, Callable, Optional, Sequence

from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.layout_utils import display_width
from moka_chat.ui.tui.events import KeyEvent, MouseEvent


class TableView(Component):
    """Focusable table with measured columns and scrollable viewport."""

    focusable = True

    def __init__(self, headers: Sequence[str], rows: Sequence[Sequence[Any]],
                 column_widths: Optional[Sequence[int]] = None,
                 on_row_select: Optional[Callable[[int, Sequence[Any]], Any]] = None,
                 id: Optional[str] = None):
        super().__init__(id)
        self.headers = [str(header) for header in headers]
        self.rows = [list(row) for row in rows]
        self.column_widths = list(column_widths) if column_widths is not None else None
        self.on_row_select = on_row_select
        self.enabled = True
        self.focused = False
        self.selected_row: Optional[int] = None
        self.vertical_offset = 0
        self.horizontal_offset = 0

    def set_focused(self, focused: bool):
        if self.focused != focused:
            self.focused = focused
            self.mark_changed()

    def set_data(self, headers: Sequence[str], rows: Sequence[Sequence[Any]]):
        self.headers = [str(header) for header in headers]
        self.rows = [list(row) for row in rows]
        self.selected_row = None
        self.vertical_offset = 0
        self.horizontal_offset = 0
        self.mark_changed()

    def _column_count(self) -> int:
        return max(len(self.headers), max((len(row) for row in self.rows), default=0))

    def get_column_widths(self) -> list[int]:
        count = self._column_count()
        if self.column_widths is not None:
            widths = list(self.column_widths[:count])
            widths.extend([1] * (count - len(widths)))
            return [max(1, width) for width in widths]
        widths = []
        for index in range(count):
            values = [self.headers[index] if index < len(self.headers) else ""]
            values.extend(str(row[index]) if index < len(row) else "" for row in self.rows)
            widths.append(max((display_width(value) for value in values), default=1))
        return widths

    def get_preferred_width(self) -> int:
        widths = self.get_column_widths()
        return sum(widths) + max(0, len(widths) - 1) * 3

    def get_preferred_height(self, width: int) -> int:
        return 1 + len(self.rows)

    def _visible_rows(self) -> int:
        return max(0, self.height - 1)

    def _max_vertical_offset(self) -> int:
        return max(0, len(self.rows) - self._visible_rows())

    def _set_vertical_offset(self, value: int):
        self.vertical_offset = max(0, min(self._max_vertical_offset(), value))
        self.mark_changed()

    def _activate_row(self, index: int) -> bool:
        if not 0 <= index < len(self.rows):
            return False
        self.selected_row = index
        self.mark_changed()
        if self.on_row_select is not None:
            self.on_row_select(index, self.rows[index])
        return True

    def handle_input(self, event: Any) -> bool:
        if not self.enabled:
            return False
        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            if key == "\x1b[A":
                self._set_vertical_offset(self.vertical_offset - 1)
                return True
            if key == "\x1b[B":
                self._set_vertical_offset(self.vertical_offset + 1)
                return True
            if key == "\x1b[D":
                self.horizontal_offset = max(0, self.horizontal_offset - 1)
                self.mark_changed()
                return True
            if key == "\x1b[C":
                self.horizontal_offset += 1
                self.mark_changed()
                return True
        if isinstance(event, MouseEvent) and event.pressed:
            if event.button == 64:
                self._set_vertical_offset(self.vertical_offset - max(1, event.scroll_delta))
                return True
            if event.button == 65:
                self._set_vertical_offset(self.vertical_offset + max(1, event.scroll_delta))
                return True
            if event.button == 0 and self.x <= event.x < self.x + self.width and self.y + 1 <= event.y < self.y + self.height:
                row = self.vertical_offset + event.y - self.y - 1
                return self._activate_row(row)
        return False

    def _row_text(self, values: Sequence[Any], widths: Sequence[int]) -> str:
        cells = []
        for index, width in enumerate(widths):
            value = str(values[index]) if index < len(values) else ""
            value = value[:width]
            cells.append(value.ljust(width))
        return " | ".join(cells)

    def render(self, buffer: Buffer):
        if self.width <= 0 or self.height <= 0:
            return
        widths = self.get_column_widths()
        header = self._row_text(self.headers, widths)
        header = header[self.horizontal_offset:]
        buffer.write_str(self.x, self.y, header, fg=theme.FOCUSED if self.focused else theme.DEFAULT,
                         bg=theme.get_bg(), reverse=self.focused, max_width=self.width)
        for visible_row, row_index in enumerate(range(self.vertical_offset, min(len(self.rows), self.vertical_offset + self._visible_rows()))):
            text = self._row_text(self.rows[row_index], widths)[self.horizontal_offset:]
            buffer.write_str(self.x, self.y + visible_row + 1, text,
                             fg=theme.FOCUSED if row_index == self.selected_row else theme.DEFAULT,
                             bg=theme.get_bg(), reverse=row_index == self.selected_row,
                             max_width=self.width)