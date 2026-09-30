from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.colors import RGB, theme
from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.events import KeyEvent, MouseEvent


@dataclass(frozen=True)
class BarStyle:
    """Shared visual defaults for one-line status and action bars."""

    fg: RGB
    bg: Optional[RGB]
    focused_fg: RGB
    padding: int = 1

    @classmethod
    def default(cls) -> "BarStyle":
        return cls(theme.DEFAULT, theme.get_bg(), theme.FOCUSED)


class StatusBar(Component):
    """One-line status display with configurable field order.

    Passing ``fields`` changes the bar from legacy left/right text mode to a
    value-map mode. This keeps the component reusable while allowing the app's
    ``ui.status_bar_fields`` setting to choose both visibility and order.
    """

    def __init__(self, left: str = "", right: str = "", *, style: Optional[BarStyle] = None,
                 fields: Optional[Sequence[str]] = None, separator: str = "  ",
                 id: Optional[str] = None):
        super().__init__(id)
        self.left = left
        self.right = right
        self.style = style or BarStyle.default()
        self.fields = list(fields) if fields is not None else None
        self.separator = separator
        self.values: dict[str, str] = {}
        self.field_colors: dict[str, Any] = {}

    def set_field_colors(self, colors: dict[str, Any]):
        """Set per-field foreground colors (field name -> RGB/ANSIColor)."""
        self.field_colors = dict(colors)
        self.mark_changed()

    def get_preferred_height(self, width: int) -> int:
        return 1


    def set_fields(self, fields: Sequence[str]):
        """Set visible field names without changing their values."""
        self.fields = list(fields)
        self.left = self.separator.join(
            self.values[field] for field in self.fields if self.values.get(field)
        )
        self.mark_changed()

    def set_values(self, values: dict[str, Any]):
        """Update named values used by the configured field order."""
        self.values = {key: str(value) for key, value in values.items()}
        if self.fields is not None:
            self.left = self.separator.join(
                self.values[field] for field in self.fields if self.values.get(field)
            )
        self.mark_changed()

    def render(self, buffer: Buffer):
        if self.width <= 0 or self.height <= 0:
            return
        buffer.fill(self.x, self.y, self.width, 1, " ", bg=self.style.bg)

        right = self.right[:max(0, self.width - self.style.padding * 2)]
        if right:
            right_x = self.x + max(self.style.padding, self.width - self.style.padding - len(right))
            buffer.write_str(right_x, self.y, right, fg=self.style.fg,
                             bg=self.style.bg, max_width=self.width - (right_x - self.x))

        # Render each configured field with its own color (falling back to the
        # shared style fg). This lets the app colorize e.g. the context field
        # based on how full the context window is.
        if self.fields is not None:
            x = self.x + self.style.padding
            for field in self.fields:
                text = self.values.get(field)
                if not text:
                    continue
                fg = self.field_colors.get(field, self.style.fg)
                buffer.write_str(x, self.y, text, fg=fg, bg=self.style.bg,
                                 max_width=max(0, self.x + self.width - x))
                x += len(text) + len(self.separator)
                if x >= self.x + self.width:
                    break
        else:
            left = self.left[:max(0, self.width - self.style.padding * 2)]
            buffer.write_str(self.x + self.style.padding, self.y, left,
                             fg=self.style.fg, bg=self.style.bg, max_width=self.width)


@dataclass(frozen=True)
class ActionItem:
    key: str
    label: str
    callback: Optional[Callable[[], Any]] = None


class ActionBar(Component):
    """One-line keyboard and mouse action bar."""

    focusable = True

    def __init__(self, actions: Sequence[ActionItem] = (), *, style: Optional[BarStyle] = None,
                 id: Optional[str] = None):
        super().__init__(id)
        self.actions = list(actions)
        self.style = style or BarStyle.default()
        self.enabled = True
        self.focused = False
        self.hint = ""
        self.hint_color: Any = None     # None: muted
        # Leading marker drawn before the actions (e.g. "▌ " for the selected
        # message) and the start of the action hit regions.
        self.prefix = ""
        # Right-align the (prefix + actions + hint) group instead of starting
        # at the left edge.
        self.align_right = False
        # When collapsed the bar occupies zero rows (mounted permanently above
        # the input, shown only while a message is selected or input is idle).
        self.expanded = False
        # When true, a blank line is rendered above the bar for breathing room.
        self.top_pad = False
        self._content_y = 0
        self._hit_regions: list[tuple[int, int, ActionItem]] = []

    def set_top_pad(self, top_pad: bool):
        if self.top_pad != top_pad:
            self.top_pad = top_pad
            self.mark_changed()

    def set_hint(self, hint: str, color: Any = None):
        """Right-aligned hint text (e.g. "↑↓ move · esc back"); muted unless
        ``color`` is given."""
        if (self.hint, self.hint_color) != (hint, color):
            self.hint = hint
            self.hint_color = color
            self.mark_changed()

    def set_prefix(self, prefix: str):
        if self.prefix != prefix:
            self.prefix = prefix
            self.mark_changed()

    def set_align_right(self, align_right: bool):
        if self.align_right != align_right:
            self.align_right = align_right
            self.mark_changed()

    def set_expanded(self, expanded: bool):
        if self.expanded != expanded:
            self.expanded = expanded
            self.mark_changed()

    def set_focused(self, focused: bool):
        if self.focused != focused:
            self.focused = focused
            self.mark_changed()

    def get_preferred_height(self, width: int) -> int:
        if not self.expanded:
            return 0
        return 2 if self.top_pad else 1

    def set_actions(self, actions: Sequence[ActionItem]):
        self.actions = list(actions)
        self._hit_regions = []
        self.mark_changed()

    def _activate(self, item: ActionItem) -> bool:
        if not self.enabled:
            return False
        if item.callback is not None:
            item.callback()
        return True

    def handle_input(self, event: Any) -> bool:
        if not self.enabled:
            return False
        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            for item in self.actions:
                if key.lower() == item.key.lower():
                    return self._activate(item)
        if isinstance(event, MouseEvent) and event.pressed and event.button == 0:
            for start, end, item in self._hit_regions:
                if start <= event.x < end and event.y == self._content_y:
                    return self._activate(item)
        return False

    def render(self, buffer: Buffer):
        if self.width <= 0 or self.height <= 0:
            return
        self._hit_regions = []
        # Fill the whole bar (blank pad line + content line).
        buffer.fill(self.x, self.y, self.width, self.height, " ", bg=self.style.bg)
        self._content_y = self.y + (1 if (self.top_pad and self.height > 1) else 0)
        row = self._content_y
        gap = max(1, self.style.padding)

        # Build the content as ordered segments so it can be left- or
        # right-aligned as a group.
        segments = []
        if self.prefix:
            segments.append(("prefix", self.prefix, None))
        for item in self.actions:
            segments.append(("action", f"[{item.key}] {item.label}", item))
        if self.hint:
            segments.append(("hint", self.hint, None))

        total = sum(len(text) for _, text, _ in segments) + gap * max(0, len(segments) - 1)
        x = self.x + self.style.padding
        if self.align_right:
            x = max(self.x + self.style.padding,
                    self.x + self.width - total - self.style.padding)

        action_fg = self.style.focused_fg if self.focused else self.style.fg
        for index, (kind, text, item) in enumerate(segments):
            if index:
                x += gap
            if x >= self.x + self.width:
                break
            end = min(self.x + self.width, x + len(text))
            color = (self.hint_color or theme.MUTED) if kind == "hint" else action_fg
            if kind == "action" and item is not None:
                self._hit_regions.append((x, end, item))
            buffer.write_str(x, row, text, fg=color, bg=self.style.bg, max_width=end - x)
            x = end


class NoticeBand(Component):
    """Lines on a solid color, one per notice, above everything else.

    Solid color is drawn as the color in the foreground, reversed: the text
    then takes the terminal's own background, whatever the theme.

    ``set_notices`` takes ``(text, color)`` pairs; the band is as tall as the
    notices, up to ``max_lines`` (the last line then says how many are hidden
    and where they are). No notices: zero rows.
    """

    def __init__(self, max_lines: int = 2, more: str = "+{n} more", id: Optional[str] = None):
        super().__init__(id)
        self.notices: list[tuple[str, Any]] = []
        self.max_lines = max_lines
        self.more = more

    def set_notices(self, notices: Sequence[tuple[str, Any]]):
        notices = list(notices)
        if notices != self.notices:
            self.notices = notices
            self.mark_changed()

    def _lines(self) -> list[tuple[str, Any]]:
        limit = max(1, self.max_lines)
        if len(self.notices) <= limit:
            return self.notices
        shown = self.notices[:limit - 1]
        hidden = len(self.notices) - len(shown)
        return shown + [(self.more.format(n=hidden), self.notices[limit - 1][1])]

    def get_preferred_height(self, width: int) -> int:
        return len(self._lines())

    def render(self, buffer: Buffer):
        for row, (text, color) in enumerate(self._lines()[:self.height]):
            line = f" {text}".ljust(self.width)
            buffer.write_str(self.x, self.y + row, line, fg=color, reverse=True,
                             max_width=self.width)
