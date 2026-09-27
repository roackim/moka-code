"""A searchable, centered selection overlay.

Reuses :class:`SelectionMenu`'s look (accent frame, aligned muted secondary
text, background-cleared box) but is a standalone modal: it traps input, is
centered in the terminal, and supports type-to-filter fuzzy search.

Used by ``/model`` but generic over ``(items, descriptions)``.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.components.menu import SelectionMenu
from moka_chat.ui.tui.events import KeyEvent, MouseEvent


class SearchModal(SelectionMenu):
    """Centered, filterable list overlay with a menu-style frame."""

    def __init__(self, compositor=None, title: str = "", **kwargs):
        super().__init__(compositor=compositor, id="search_modal", **kwargs)
        self.title = title
        self._base_title = title
        self._base_items: List[str] = []
        self._footers: dict = {}
        self._search = ""
        self._on_accept: Optional[Callable[[str], None]] = None
        self._on_cancel: Optional[Callable[[], None]] = None
        self._on_highlight: Optional[Callable[[str], None]] = None

        # Same look as the /cmd and @ popups.
        self.frame_color = theme.USER
        self.content_color = theme.DEFAULT
        self.fill_width = False
        self.min_width = 40  # enough room for the filter line and columns
        self.max_height = 16
        # When False, the modal is positioned by ``anchor`` (e.g. above the
        # input box) instead of being centered.
        self.auto_center = True
        self.anchor: Optional[Callable[[], None]] = None

    # -- lifecycle -----------------------------------------------------

    def open(self, items: List[str], descriptions: Optional[dict] = None,
             footers: Optional[dict] = None,
             on_accept: Optional[Callable[[str], None]] = None,
             on_cancel: Optional[Callable[[], None]] = None,
             on_highlight: Optional[Callable[[str], None]] = None,
             initial_index: int = 0) -> None:
        self._base_items = list(items)
        self._on_accept = on_accept
        self._on_cancel = on_cancel
        self._on_highlight = on_highlight
        self._search = ""
        if descriptions is not None:
            self.item_descriptions = dict(descriptions)
        self._footers = dict(footers or {})
        self._apply_filter()
        if self.items:
            self.selected_index = max(0, min(initial_index, len(self.items) - 1))
        self.is_visible = True
        self._update_compositor_registration()
        self._notify_highlight()
        self._request_render()

    def refresh(self, items: List[str], descriptions: Optional[dict] = None,
                footers: Optional[dict] = None,
                initial_index: Optional[int] = None) -> None:
        """Replace the candidate list, preserving the current search/selection."""
        current = self.get_selected()
        self._base_items = list(items)
        if descriptions is not None:
            self.item_descriptions = dict(descriptions)
        if footers is not None:
            self._footers = dict(footers)
        self._apply_filter()
        if initial_index is not None and 0 <= initial_index < len(self.items):
            self.selected_index = initial_index
        elif current in self.items:
            self.selected_index = self.items.index(current)
        self.is_visible = True
        self._update_compositor_registration()
        self._request_render()

    def close(self) -> None:
        self._on_accept = None
        self._on_cancel = None
        self._on_highlight = None
        self.hide()

    def _notify_highlight(self) -> None:
        """Fire the highlight callback for the currently selected item."""
        callback = self._on_highlight
        item = self.get_selected()
        if callback and item is not None:
            callback(item)

    def action_up(self):
        super().action_up()
        self._notify_highlight()

    def action_down(self):
        super().action_down()
        self._notify_highlight()

    # -- filtering -----------------------------------------------------

    def _apply_filter(self) -> None:
        self.update(self._base_items, self._search,
                    descriptions=self.item_descriptions,
                    footers=self._footers)
        # The query lives in the title: "Models: qwe " while typing (trailing
        # space so it reads like a caret), "Models" when empty.
        self.title = (f"{self._base_title}: {self._search} "
                      if self._search else self._base_title)
        self.status_text = ""
        # Keep the modal registered even when the filter matches nothing; the
        # empty state still renders (and must keep receiving keys).
        if not self.items:
            self.is_visible = True
            self._update_compositor_registration()

    def _refilter(self) -> None:
        current = self.get_selected()
        self._apply_filter()
        if current in self.items:
            self.selected_index = self.items.index(current)
        self._notify_highlight()
        self._request_render()

    def _request_render(self) -> None:
        if self.compositor and hasattr(self.compositor, "request_render"):
            self.compositor.request_render()

    # -- input ---------------------------------------------------------

    def handle_input(self, event) -> bool:
        if not self.is_visible:
            return False

        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            if key == '\x1b':
                cancel = self._on_cancel
                self.close()
                if cancel:
                    cancel()
                return True
            if key == '\x1b[A':
                self.action_up()
                self._request_render()
                return True
            if key == '\x1b[B':
                self.action_down()
                self._request_render()
                return True
            if key in ('\r', '\n'):
                selected = self.get_selected()
                accept = self._on_accept
                self.close()
                if accept and selected is not None:
                    accept(selected)
                return True
            if key in ('\x7f', '\b'):
                if self._search:
                    self._search = self._search[:-1]
                    self._refilter()
                return True
            if len(key) == 1 and key.isprintable():
                self._search += key
                self._refilter()
                return True
            return True

        if isinstance(event, MouseEvent):
            # Modal traps mouse input; clicks are ignored.
            return True

        # Let ticks/resizes/actions propagate (cursor blink, spinners).
        return False

    # -- rendering -----------------------------------------------------

    def render(self, buffer: Buffer) -> None:
        if not self.is_visible:
            return

        if not self.auto_center and self.anchor is not None:
            # Anchored (e.g. above the input): let the owner position us.
            self.anchor()

        if not self.items:
            self._render_empty(buffer)
            return

        if self.auto_center:
            width = self.measure_width(buffer.width)
            visible = min(len(self.items), max(1, self.max_height - 2))
            self.width = width
            self.height = visible + 2
            self.x = max(0, (buffer.width - width) // 2)
            self.y = max(0, (buffer.height - self.height) // 2)

        super().render(buffer)

    def _render_empty(self, buffer: Buffer) -> None:
        msg = f" {self._search or 'no models'} — no matches "
        if self.auto_center:
            width = min(buffer.width, max(24, len(msg) + 4))
            x = max(0, (buffer.width - width) // 2)
            y = max(0, (buffer.height - 3) // 2)
        else:
            width = min(buffer.width, max(24, len(msg) + 4))
            if self.fill_width:
                width = min(buffer.width, max(15, self.width or buffer.width))
            x = self.x
            # Bottom-align with the anchored position.
            y = max(0, self.y + self.height - 3)
        height = 3
        self.x, self.y, self.width, self.height = x, y, width, height

        for yy in range(height):
            for xx in range(width):
                buffer.set(x + xx, y + yy, " ", bg=self.bg)
        buffer.set(x, y, "┌", fg=self.frame_color, bg=self.bg)
        for i in range(1, width - 1):
            buffer.set(x + i, y, "─", fg=self.frame_color, bg=self.bg)
        buffer.set(x + width - 1, y, "┐", fg=self.frame_color, bg=self.bg)
        buffer.set(x, y + 1, "│", fg=self.frame_color, bg=self.bg)
        buffer.set(x + width - 1, y + 1, "│", fg=self.frame_color, bg=self.bg)
        buffer.write_str(x + 2, y + 1, msg, fg=theme.MUTED, bg=self.bg,
                         max_width=max(0, width - 4))
        buffer.set(x, y + 2, "└", fg=self.frame_color, bg=self.bg)
        for i in range(1, width - 1):
            buffer.set(x + i, y + 2, "─", fg=self.frame_color, bg=self.bg)
        buffer.set(x + width - 1, y + 2, "┘", fg=self.frame_color, bg=self.bg)


__all__ = ["SearchModal"]
