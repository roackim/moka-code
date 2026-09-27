"""AppScaffold — shared top-level screen chrome.

Every workspace screen (chat, settings, debug, …) shares the same frame:
a tab bar across the top, a body filling the middle, and a status bar pinned
to the bottom.  Previously each screen re-implemented this with ad-hoc ``Split``
sizes and workarounds for the "0 means fill" ambiguity, which is how the
settings screen ended up with a collapsed status bar.

``AppScaffold`` owns that frame once:

* ``top`` and ``bottom`` are sized by their ``get_preferred_height()`` (bars
  declare their own height, e.g. 1 line).
* ``body`` receives everything that remains, inset by ``gutter``.
"""

from __future__ import annotations

from typing import Optional, Tuple

from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.components.base import Component

Gutter = Tuple[int, int, int, int]  # top, right, bottom, left


class AppScaffold(Component):
    """Top/bottom bars + a guttered body.  Screens supply only the body."""

    def __init__(self, body: Component,
                 top: Optional[Component] = None,
                 bottom: Optional[Component] = None,
                 gutter: Gutter = (0, 0, 0, 0),
                 id: Optional[str] = None):
        super().__init__(id)
        self.top = top
        self.body = body
        self.bottom = bottom
        self.gutter = gutter
        for part in (self.top, self.body, self.bottom):
            if part is not None:
                part.parent = self

    @property
    def children(self):
        return [part for part in (self.top, self.body, self.bottom)
                if part is not None]

    def _preferred_height(self, component: Optional[Component]) -> int:
        if component is None:
            return 0
        if hasattr(component, "get_preferred_height"):
            return max(0, component.get_preferred_height(self.width))
        return 0

    def body_rect(self) -> tuple[int, int, int, int]:
        """The rectangle allocated to the body (already inset by the gutter)."""
        top_h = self._preferred_height(self.top)
        bottom_h = self._preferred_height(self.bottom)
        gtop, gright, gbottom, gleft = self.gutter
        x = self.x + gleft
        y = self.y + top_h + gtop
        width = max(0, self.width - gleft - gright)
        height = max(0, self.height - top_h - bottom_h - gtop - gbottom)
        return x, y, width, height

    def set_layout(self, x: int, y: int, width: int, height: int):
        super().set_layout(x, y, width, height)
        self.layout()

    def layout(self):
        top_h = self._preferred_height(self.top)
        bottom_h = self._preferred_height(self.bottom)
        if self.top is not None:
            self.top.set_layout(self.x, self.y, self.width, top_h)
            self.top.layout()
        if self.bottom is not None:
            self.bottom.set_layout(self.x, self.y + self.height - bottom_h,
                                   self.width, bottom_h)
            self.bottom.layout()
        bx, by, bw, bh = self.body_rect()
        self.body.set_layout(bx, by, bw, bh)
        self.body.layout()

    def render(self, buffer: Buffer):
        for part in (self.top, self.body, self.bottom):
            if part is not None:
                part.render(buffer)

    def handle_input(self, event) -> bool:
        for part in (self.top, self.body, self.bottom):
            if part is not None and part.handle_input(event):
                return True
        return False

    def collect_dirty_rects(self, rects):
        super().collect_dirty_rects(rects)
        for part in (self.top, self.body, self.bottom):
            if part is not None and hasattr(part, "collect_dirty_rects"):
                part.collect_dirty_rects(rects)

    def clear_dirty(self):
        super().clear_dirty()
        for part in (self.top, self.body, self.bottom):
            if part is not None and hasattr(part, "clear_dirty"):
                part.clear_dirty()
