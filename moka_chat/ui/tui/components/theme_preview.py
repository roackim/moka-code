"""A compact palette overview overlay shown while picking a theme.

Renders the live ``theme`` singleton, so it always matches the theme currently
being previewed by the ``/theme`` picker. Kept deliberately short (a swatch
strip plus a colored sample line) and pinned to the top of the screen so the
bottom-anchored picker never covers it.
"""

from __future__ import annotations

from typing import Optional, Tuple

from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.components.base import Component


# (palette field, sample word) in display order.
_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("BACKGROUND", "bg"),
    ("DEFAULT", "text"),
    ("MUTED", "muted"),
    ("ERROR", "err"),
    ("WARNING", "warn"),
    ("SUCCESS", "ok"),
    ("PERMISSION", "perm"),
    ("TOOL", "tool"),
    ("USER", "user"),
    ("ASSISTANT", "moka"),
    ("FOCUSED", "focus"),
)

_HEIGHT = 4  # top border + swatch row + sample row + bottom border
_SWATCH = "██"


class ThemePreview(Component):
    """Top-center overlay: a swatch strip and a colored sample line."""

    def __init__(self, id: Optional[str] = None):
        super().__init__(id)
        self.is_visible = False
        self.compositor = None
        self._registered = False

    def set_compositor(self, compositor):
        self.compositor = compositor

    def show(self) -> None:
        self.is_visible = True
        self._update_registration()
        self._request_render()

    def hide(self) -> None:
        self.is_visible = False
        self._update_registration()
        self._request_render()

    def _update_registration(self) -> None:
        if self.compositor is None:
            return
        add = getattr(self.compositor, "add_overlay", None)
        remove = getattr(self.compositor, "remove_overlay", None)
        if self.is_visible and not self._registered and callable(add):
            add(self)
            self._registered = True
        elif not self.is_visible and self._registered and callable(remove):
            remove(self)
            self._registered = False

    def _request_render(self) -> None:
        request = getattr(self.compositor, "request_render", None)
        if callable(request):
            request()

    def _sample_width(self) -> int:
        return sum(len(label) + 1 for _field, label in _FIELDS) + 1

    def render(self, buffer: Buffer) -> None:
        if not self.is_visible:
            return

        content = max(self._sample_width(), len(_FIELDS) * 3)
        width = min(buffer.width - 2, max(24, content + 4))
        if width <= 0:
            return
        x = max(0, (buffer.width - width) // 2)
        y = 1
        bg = theme.get_bg()

        for yy in range(_HEIGHT):
            for xx in range(width):
                buffer.set(x + xx, y + yy, " ", bg=bg)

        buffer.set(x, y, "┌", fg=theme.USER, bg=bg)
        buffer.set(x + width - 1, y, "┐", fg=theme.USER, bg=bg)
        buffer.set(x, y + _HEIGHT - 1, "└", fg=theme.USER, bg=bg)
        buffer.set(x + width - 1, y + _HEIGHT - 1, "┘", fg=theme.USER, bg=bg)
        for i in range(1, width - 1):
            buffer.set(x + i, y, "─", fg=theme.USER, bg=bg)
            buffer.set(x + i, y + _HEIGHT - 1, "─", fg=theme.USER, bg=bg)
        buffer.write_str(x + 2, y, f" theme: {theme.name} ", fg=theme.USER, bg=bg,
                         max_width=max(0, width - 4))

        # Row 1: one swatch block per palette field.
        swatch_x = x + 2
        for field, _label in _FIELDS:
            if swatch_x + 2 > x + width - 1:
                break
            buffer.write_str(swatch_x, y + 1, _SWATCH, fg=getattr(theme, field), bg=bg)
            swatch_x += 3

        # Row 2: sample words, each in its palette color.
        sample_x = x + 2
        for field, label in _FIELDS:
            if sample_x + len(label) > x + width - 1:
                break
            buffer.write_str(sample_x, y + 2, label, fg=getattr(theme, field), bg=bg,
                             max_width=max(0, x + width - 1 - sample_x))
            sample_x += len(label) + 1


__all__ = ["ThemePreview"]
