"""A centered modal list selector overlay.

Arrow keys / mouse move the selection, Enter selects, Esc cancels. While
visible it traps all input (like :class:`Popup`), so it can be used for
command pickers such as ``/model``.
"""

from typing import Any, Callable, List, Optional

from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.components.box import Box
from moka_code.ui.tui.components.list_view import ListView
from moka_code.ui.tui.components.popup import PopupAction
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.events import KeyEvent, MouseEvent
from moka_code.ui.tui.colors import RGB, theme
from moka_code.ui.tui.screen import Screen


class ListModal(Component):
    """A centered overlay wrapping a focusable :class:`ListView`."""

    def __init__(self,
                 compositor: Optional[Any] = None,
                 id: Optional[str] = None,
                 title: str = "",
                 formatter: Optional[Callable[[Any], str]] = None,
                 frame_color: RGB = None,
                 content_color: RGB = None,
                 max_width_ratio: float = 0.85,
                 max_height_ratio: float = 0.7):
        super().__init__(id)
        self.frame_color = frame_color if frame_color is not None else theme.DEFAULT
        self.content_color = content_color if content_color is not None else self.frame_color
        self.max_width_ratio = max_width_ratio
        self.max_height_ratio = max_height_ratio
        self.is_visible = False
        self.on_accept: Optional[Callable[[Any], None]] = None
        self.on_cancel: Optional[Callable[[], None]] = None
        self._items: List[Any] = []
        self._background_focus_scope = None
        self._background_focus_index = None

        self._list = ListView(formatter=formatter, on_select=self._accept, id=id)
        self._box = Box(
            self._list,
            title=title,
            fg=self.frame_color,
            focused=True,
            actions=[PopupAction("Esc", "cancel"), PopupAction("Enter", "select")],
        )

        self.compositor = compositor
        self._registered_with_compositor = False

    # -- compositor / focus plumbing (mirrors Popup) -------------------------

    def set_compositor(self, compositor):
        self.compositor = compositor

    def _update_compositor_registration(self):
        if not self.compositor:
            return
        if self.is_visible and not self._registered_with_compositor:
            self.compositor.add_overlay(self)
            self._registered_with_compositor = True
        elif not self.is_visible and self._registered_with_compositor:
            self.compositor.remove_overlay(self)
            self._registered_with_compositor = False

    def _suspend_background_focus(self):
        if not self.compositor or not hasattr(self.compositor, "event_router"):
            return
        scope = self.compositor.event_router.focus_scope
        if scope is None or not scope.active:
            return
        self._background_focus_scope = scope
        self._background_focus_index = scope.focused_index
        scope.manager.clear()

    def _restore_background_focus(self):
        scope = self._background_focus_scope
        index = self._background_focus_index
        self._background_focus_scope = None
        self._background_focus_index = None
        if scope is not None and index is not None:
            scope.manager.focus(index)

    # -- show / hide ---------------------------------------------------------

    def show(self, items: List[Any], title: Optional[str] = None,
             on_accept: Optional[Callable[[Any], None]] = None,
             on_cancel: Optional[Callable[[], None]] = None,
             initial_index: Optional[int] = None):
        """Present ``items`` and select one on Enter."""
        if title is not None:
            self._box.title = title
        self._items = list(items)
        self.on_accept = on_accept
        self.on_cancel = on_cancel
        self._list.model.set_items(self._items, initial_index)
        self._suspend_background_focus()
        self.is_visible = True
        self._center()
        self._list.set_focused(True)
        self._update_compositor_registration()
        if self.compositor:
            self.compositor.request_render()

    def hide(self):
        was_visible = self.is_visible
        self.is_visible = False
        self._list.set_focused(False)
        self._restore_background_focus()
        self._update_compositor_registration()
        if was_visible and self.compositor:
            self.compositor.request_render()

    def _accept(self, item: Any):
        callback = self.on_accept
        self.on_accept = None
        self.hide()
        if callback:
            callback(item)

    def _cancel(self):
        callback = self.on_cancel
        self.on_cancel = None
        self.hide()
        if callback:
            callback()

    # -- layout / render -----------------------------------------------------

    def _center(self):
        if not self.compositor:
            return
        term_w = self.compositor.width
        term_h = self.compositor.height

        formatter = self._list.formatter or str
        longest = max((len(formatter(item)) for item in self._items), default=10)
        modal_w = min(int(term_w * self.max_width_ratio), longest + 4)
        modal_w = max(modal_w, 20)
        modal_h = min(int(term_h * self.max_height_ratio), len(self._items) + 2)
        modal_h = max(modal_h, 3)

        self.x = max(0, (term_w - modal_w) // 2)
        self.y = max(0, (term_h - modal_h) // 2)
        self.width = modal_w
        self.height = modal_h
        self._box.set_layout(self.x, self.y, self.width, self.height)

    def handle_input(self, event: Any) -> bool:
        """Trap input while visible; Esc cancels, arrows/Enter drive the list."""
        if not self.is_visible:
            return False

        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            if key == '\x1b':
                self._cancel()
                return True
            return self._list.handle_input(event) or True

        if isinstance(event, MouseEvent):
            inside = (self.x <= event.x < self.x + self.width
                      and self.y <= event.y < self.y + self.height)
            if inside:
                self._list.handle_input(event)
            return True

        return True

    def render(self, buffer: Buffer):
        if not self.is_visible:
            return
        self._box.set_layout(self.x, self.y, self.width, self.height)
        self._box.render(buffer)


class ListModalScreen(Screen):
    """Screen lifecycle wrapper for a :class:`ListModal`."""

    def __init__(self, modal: ListModal, items: List[Any], *,
                 title: str = "",
                 on_accept: Optional[Callable[[Any], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None,
                 initial_index: Optional[int] = None):
        super().__init__(modal)
        self.modal = modal
        self.items = items
        self.title = title
        self.on_accept = on_accept
        self.on_cancel = on_cancel
        self.initial_index = initial_index

    def on_enter(self):
        self.modal.show(
            self.items, title=self.title, on_accept=self.on_accept,
            on_cancel=self.on_cancel, initial_index=self.initial_index,
        )

    def on_leave(self):
        self.modal.hide()
