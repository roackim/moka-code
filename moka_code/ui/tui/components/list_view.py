from typing import Any, Callable, Generic, Optional, Sequence, TypeVar

from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.events import KeyEvent, MouseEvent


T = TypeVar("T")


class SelectionModel(Generic[T]):
    """Reusable ordered selection state independent from rendering."""

    def __init__(self, items: Optional[Sequence[T]] = None, selected: Optional[int] = None):
        self.items = list(items or [])
        self.selected_index: Optional[int] = None
        self.set_items(self.items, selected)

    @property
    def selected(self) -> Optional[T]:
        if self.selected_index is None:
            return None
        return self.items[self.selected_index]

    def set_items(self, items: Sequence[T], selected: Optional[int] = None):
        self.items = list(items)
        if not self.items:
            self.selected_index = None
        elif selected is None:
            self.selected_index = 0
        elif 0 <= selected < len(self.items):
            self.selected_index = selected
        else:
            raise IndexError("selection index is out of range")

    def select(self, index: int) -> bool:
        if not 0 <= index < len(self.items):
            return False
        changed = self.selected_index != index
        self.selected_index = index
        return changed

    def move(self, delta: int, wrap: bool = False) -> bool:
        if not self.items or self.selected_index is None:
            return False
        target = self.selected_index + delta
        if wrap:
            target %= len(self.items)
        else:
            target = max(0, min(len(self.items) - 1, target))
        return self.select(target)


class ListView(Component, Generic[T]):
    """Focusable, scrollable list backed by a SelectionModel."""

    focusable = True

    def __init__(self, items: Optional[Sequence[T]] = None,
                 model: Optional[SelectionModel[T]] = None,
                 formatter: Optional[Callable[[T], str]] = None,
                 on_select: Optional[Callable[[T], Any]] = None,
                 id: Optional[str] = None):
        super().__init__(id)
        self.model = model or SelectionModel(items)
        self.formatter = formatter or str
        self.on_select = on_select
        self.enabled = True
        self.focused = False
        self.scroll_offset = 0

    @property
    def items(self) -> list[T]:
        return self.model.items

    def set_focused(self, focused: bool):
        if self.focused != focused:
            self.focused = focused
            self.mark_changed()

    def get_preferred_width(self) -> int:
        return max((len(self.formatter(item)) for item in self.items), default=0)

    def get_preferred_height(self, width: int) -> int:
        return len(self.items)

    def _visible_count(self) -> int:
        return max(0, self.height)

    def _keep_selected_visible(self):
        index = self.model.selected_index
        if index is None:
            self.scroll_offset = 0
            return
        visible = self._visible_count()
        if visible <= 0:
            return
        if index < self.scroll_offset:
            self.scroll_offset = index
        elif index >= self.scroll_offset + visible:
            self.scroll_offset = index - visible + 1

    def _activate(self) -> bool:
        selected = self.model.selected
        if selected is None:
            return False
        if self.on_select is not None:
            self.on_select(selected)
        return True

    def handle_input(self, event: Any) -> bool:
        if not self.enabled or not self.items:
            return False
        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            if key == "\x1b[A":
                self.model.move(-1)
                self._keep_selected_visible()
                return True
            if key == "\x1b[B":
                self.model.move(1)
                self._keep_selected_visible()
                return True
            if key in ("\r", "\n", " "):
                return self._activate()
        if isinstance(event, MouseEvent) and event.pressed:
            if event.button == 0 and self.x <= event.x < self.x + self.width:
                row = event.y - self.y + self.scroll_offset
                if 0 <= row < len(self.items):
                    self.model.select(row)
                    self._keep_selected_visible()
                    return self._activate()
            if event.button == 64:
                self.model.move(-max(1, event.scroll_delta))
                self._keep_selected_visible()
                return True
            if event.button == 65:
                self.model.move(max(1, event.scroll_delta))
                self._keep_selected_visible()
                return True
        return False

    def render(self, buffer: Buffer):
        if self.width <= 0 or self.height <= 0:
            return
        end = min(len(self.items), self.scroll_offset + self.height)
        for row, index in enumerate(range(self.scroll_offset, end)):
            selected = index == self.model.selected_index
            text = self.formatter(self.items[index])
            focused = selected and self.focused
            fg = theme.FOCUSED if focused else theme.DEFAULT
            buffer.write_str(self.x, self.y + row, text, fg=fg, bg=theme.get_bg(),
                             reverse=focused, max_width=self.width)


class Select(ListView[T]):
    """Compact select field that toggles an inline list with Enter or Space."""

    def __init__(self, items: Optional[Sequence[T]] = None,
                 model: Optional[SelectionModel[T]] = None,
                 formatter: Optional[Callable[[T], str]] = None,
                 on_select: Optional[Callable[[T], Any]] = None,
                 id: Optional[str] = None):
        super().__init__(items, model, formatter, on_select, id)
        self.open = False
        self._field_height = 1

    def get_preferred_height(self, width: int) -> int:
        return 1 if not self.open else 1 + len(self.items)

    def handle_input(self, event: Any) -> bool:
        key = event.key if isinstance(event, KeyEvent) else event
        if isinstance(event, (str, KeyEvent)) and key in ("\r", "\n", " "):
            self.open = not self.open
            self.mark_changed()
            return True
        if isinstance(event, MouseEvent) and event.pressed and event.button == 0:
            if self.x <= event.x < self.x + self.width and self.y <= event.y < self.y + self._field_height:
                self.open = not self.open
                self.mark_changed()
                return True
        if not self.open:
            return False
        original_y = self.y
        original_height = self.height
        self.y += 1
        self.height -= 1
        handled = super().handle_input(event)
        self.y = original_y
        self.height = original_height
        if handled and isinstance(event, (str, KeyEvent)) and key in ("\r", "\n"):
            self.open = False
        return handled

    def render(self, buffer: Buffer):
        if self.width <= 0 or self.height <= 0:
            return
        value = "" if self.model.selected is None else self.formatter(self.model.selected)
        field = f"[ {value} ]"
        buffer.write_str(self.x, self.y, field, fg=theme.FOCUSED if self.focused else theme.DEFAULT,
                         bg=theme.get_bg(), reverse=self.focused, max_width=self.width)
        if self.open and self.height > 1:
            original_y = self.y
            original_height = self.height
            self.y += 1
            self.height -= 1
            super().render(buffer)
            self.y = original_y
            self.height = original_height