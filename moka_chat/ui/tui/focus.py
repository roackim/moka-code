"""Reusable focus management for interactive TUI widgets."""

from typing import Any, Callable, Iterable, Optional


class FocusManager:
    """Own focus for an ordered collection of interactive widgets.

    Widgets may expose ``set_focused(bool)`` or a ``focused`` attribute.
    They can opt out of focus with ``focusable = False`` or
    ``enabled = False``.
    """

    def __init__(self, widgets: Optional[Iterable[Any]] = None):
        self._widgets: list[Any] = []
        self._focused_index: Optional[int] = None
        if widgets is not None:
            self.set_widgets(widgets)

    @property
    def focused(self) -> Optional[Any]:
        if self._focused_index is None:
            return None
        return self._widgets[self._focused_index]

    @property
    def focused_index(self) -> Optional[int]:
        return self._focused_index

    def set_widgets(self, widgets: Iterable[Any]) -> None:
        self.clear()
        self._widgets = list(widgets)
        first = self._next_focusable(-1, step=1)
        if first is not None:
            self.focus(first)

    def focus(self, index: int) -> bool:
        if not 0 <= index < len(self._widgets) or not self._is_focusable(self._widgets[index]):
            return False
        if self._focused_index == index:
            return True
        if self._focused_index is not None:
            self._set_widget_focused(self._widgets[self._focused_index], False)
        self._focused_index = index
        self._set_widget_focused(self._widgets[index], True)
        return True

    def next(self) -> bool:
        return self._move(step=1)

    def previous(self) -> bool:
        return self._move(step=-1)

    def clear(self) -> None:
        if self._focused_index is not None:
            self._set_widget_focused(self._widgets[self._focused_index], False)
        self._focused_index = None

    def _move(self, step: int) -> bool:
        start = self._focused_index if self._focused_index is not None else (-1 if step > 0 else len(self._widgets))
        index = self._next_focusable(start, step)
        if index is None:
            return False
        return self.focus(index)

    def _next_focusable(self, start: int, step: int) -> Optional[int]:
        index = start + step
        while 0 <= index < len(self._widgets):
            if self._is_focusable(self._widgets[index]):
                return index
            index += step
        return None

    @staticmethod
    def _is_focusable(widget: Any) -> bool:
        return getattr(widget, "focusable", True) and getattr(widget, "enabled", True)

    @staticmethod
    def _set_widget_focused(widget: Any, focused: bool) -> None:
        setter = getattr(widget, "set_focused", None)
        if setter is not None:
            setter(focused)
        else:
            widget.focused = focused


class FocusScope:
    """A focus boundary for a form, dialog, or other interactive region."""

    def __init__(self, widgets: Optional[Iterable[Any]] = None, *, trap: bool = True,
                 on_enter: Optional[Callable[[], None]] = None,
                 on_leave: Optional[Callable[[], None]] = None):
        self.manager = FocusManager(widgets)
        self.trap = trap
        self.active = False
        self._on_enter = on_enter
        self._on_leave = on_leave

    @property
    def focused(self) -> Optional[Any]:
        return self.manager.focused

    @property
    def focused_index(self) -> Optional[int]:
        return self.manager.focused_index

    def enter(self) -> bool:
        if self.active:
            return self.manager.focused is not None
        self.active = True
        if self._on_enter:
            self._on_enter()
        if self.manager.focused is None:
            return self.manager.focus(0)
        return True

    def leave(self) -> None:
        if not self.active:
            return
        self.active = False
        self.manager.clear()
        if self._on_leave:
            self._on_leave()

    def focus_next(self) -> bool:
        if self.manager.next():
            return True
        if self.trap and self.manager._widgets:
            index = self.manager._next_focusable(-1, step=1)
            return index is not None and self.manager.focus(index)
        return False


    def focus_at(self, x: int, y: int) -> bool:
        """Focus the topmost focusable widget containing a coordinate."""
        for index in range(len(self.manager._widgets) - 1, -1, -1):
            widget = self.manager._widgets[index]
            if not self.manager._is_focusable(widget):
                continue
            if (widget.x <= x < widget.x + widget.width
                    and widget.y <= y < widget.y + widget.height):
                return self.manager.focus(index)
        return False