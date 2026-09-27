from typing import Any, Callable, Optional, Sequence

from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.events import KeyEvent, MouseEvent


class Checkbox(Component):
    """Focusable boolean control activated by keyboard or mouse."""

    focusable = True

    def __init__(self, label: str, value: bool = False,
                 on_change: Optional[Callable[[bool], Any]] = None,
                 id: Optional[str] = None):
        super().__init__(id)
        self.label = label
        self.value = bool(value)
        self.enabled = True
        self.focused = False
        self.on_change = on_change

    def set_focused(self, focused: bool):
        if self.focused != focused:
            self.focused = focused
            self.mark_changed()

    def get_preferred_width(self) -> int:
        return len(self.label) + 4

    def get_preferred_height(self, width: int) -> int:
        return 1

    def set_value(self, value: bool):
        value = bool(value)
        if self.value == value:
            return
        self.value = value
        self.mark_changed()
        if self.on_change is not None:
            self.on_change(value)

    def toggle(self) -> bool:
        if not self.enabled:
            return False
        self.set_value(not self.value)
        return True

    def handle_input(self, event: Any) -> bool:
        if not self.enabled:
            return False
        key = event.key if isinstance(event, KeyEvent) else event
        if isinstance(event, (str, KeyEvent)) and key in (" ", "\r", "\n"):
            return self.toggle()
        if isinstance(event, MouseEvent) and event.pressed and event.button == 0:
            if self.x <= event.x < self.x + self.width and self.y <= event.y < self.y + self.height:
                return self.toggle()
        return False

    def render(self, buffer: Buffer):
        if self.width <= 0 or self.height <= 0:
            return
        mark = "[x]" if self.value else "[ ]"
        text = f"{mark} {self.label}"
        fg = theme.MUTED if not self.enabled else (theme.FOCUSED if self.focused else theme.DEFAULT)
        buffer.write_str(self.x, self.y, text, fg=fg, bg=theme.get_bg(),
                         reverse=self.focused and self.enabled, max_width=self.width)


class RadioGroup(Component):
    """Focusable single-selection list with arrow-key and mouse navigation."""

    focusable = True

    def __init__(self, options: Sequence[str], value: Optional[int] = None,
                 on_change: Optional[Callable[[Optional[int]], Any]] = None,
                 id: Optional[str] = None):
        super().__init__(id)
        self.options = list(options)
        self.value = value if value in range(len(self.options)) else None
        self.cursor = self.value if self.value is not None else 0
        self.enabled = True
        self.focused = False
        self.on_change = on_change

    def set_focused(self, focused: bool):
        if self.focused != focused:
            self.focused = focused
            self.mark_changed()

    def get_preferred_width(self) -> int:
        return max((len(option) + 4 for option in self.options), default=0)

    def get_preferred_height(self, width: int) -> int:
        return len(self.options)

    def set_value(self, value: Optional[int]):
        if value is not None and not 0 <= value < len(self.options):
            raise ValueError("radio selection index is out of range")
        if self.value == value:
            return
        self.value = value
        if value is not None:
            self.cursor = value
        self.mark_changed()
        if self.on_change is not None:
            self.on_change(value)

    def _select_cursor(self) -> bool:
        if not self.enabled or not self.options:
            return False
        self.set_value(self.cursor)
        return True

    def handle_input(self, event: Any) -> bool:
        if not self.enabled or not self.options:
            return False
        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            if key == "\x1b[A":
                if self.cursor > 0:
                    self.cursor -= 1
                    self.mark_changed()
                return True
            if key == "\x1b[B":
                if self.cursor < len(self.options) - 1:
                    self.cursor += 1
                    self.mark_changed()
                return True
            if key in (" ", "\r", "\n"):
                return self._select_cursor()
        if isinstance(event, MouseEvent) and event.pressed and event.button == 0:
            if self.x <= event.x < self.x + self.width and self.y <= event.y < self.y + len(self.options):
                self.cursor = event.y - self.y
                return self._select_cursor()
        return False

    def render(self, buffer: Buffer):
        if not self.enabled and not self.options:
            return
        for index, option in enumerate(self.options[:max(0, self.height)]):
            selected = index == self.value
            focused = self.focused and index == self.cursor
            mark = "(x)" if selected else "( )"
            text = f"{mark} {option}"
            fg = theme.MUTED if not self.enabled else (theme.FOCUSED if focused else theme.DEFAULT)
            buffer.write_str(self.x, self.y + index, text, fg=fg, bg=theme.get_bg(),
                             reverse=focused and self.enabled, max_width=self.width)