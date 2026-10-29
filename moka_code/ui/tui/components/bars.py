import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.colors import RGB, theme
from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.events import KeyEvent, MouseEvent
from moka_code.ui.tui.layout_utils import display_width


#: In a status bar template, everything after it is right-aligned.
RIGHT_ALIGN_MARKER = "|"

_PLACEHOLDER = re.compile(r"\{([A-Za-z_]+)\}")


def _parse_template(template: str) -> tuple[list, list]:
    """``(left, right)`` node trees of a status bar template.

    A node is ``("text", s)``, ``("value", name)`` or ``("group", nodes)``
    (``[ … ]``). A top-level ``|`` splits left from right. Unbalanced brackets
    are read as text (``settings`` reports them at load).
    """
    sides: list[list] = [[]]
    stack: list[list] = [sides[0]]
    position = 0
    while position < len(template):
        char = template[position]
        match = _PLACEHOLDER.match(template, position)
        if match:
            stack[-1].append(("value", match.group(1)))
            position = match.end()
            continue
        if char == "[":
            group: list = []
            stack[-1].append(("group", group))
            stack.append(group)
        elif char == "]" and len(stack) > 1:
            stack.pop()
        elif char == RIGHT_ALIGN_MARKER and len(stack) == 1 and len(sides) == 1:
            sides.append([])
            stack = [sides[1]]
        elif stack[-1] and stack[-1][-1][0] == "text":
            stack[-1][-1] = ("text", stack[-1][-1][1] + char)
        else:
            stack[-1].append(("text", char))
        position += 1
    return sides[0], sides[1] if len(sides) > 1 else []


def _first_color(nodes: list, colors: dict) -> Any:
    for kind, payload in nodes:
        if kind == "value" and payload in colors:
            return colors[payload]
        if kind == "group":
            found = _first_color(payload, colors)
            if found is not None:
                return found
    return None


def _layout(nodes: list, values: dict, colors: dict, color: Any,
            in_group: bool = False) -> Optional[list[tuple[str, Any, Optional[str]]]]:
    """``[(text, color, value name or None)]`` for ``nodes``; ``None`` when a
    value directly in a group is empty (the group is dropped). A group is drawn
    whole in the color of its first colored value; outside groups a value has
    its own color."""
    out: list[tuple[str, Any, Optional[str]]] = []
    for kind, payload in nodes:
        if kind == "text":
            out.append((payload, color, None))
        elif kind == "value":
            value = values.get(payload, "")
            if not value:
                if in_group:
                    return None
                continue
            out.append((value, color if in_group else colors.get(payload, color), payload))
        else:
            group_color = _first_color(payload, colors)
            inner = _layout(payload, values, colors,
                            group_color if group_color is not None else color, True)
            if inner:
                out += inner
    return out


def render_template(template: str, values: dict, colors: dict, color: Any) -> tuple[list, list]:
    """The left and right ``[(text, color, value name or None)]`` runs of a
    status bar template."""
    left, right = _parse_template(template)
    return (_layout(left, values, colors, color) or [],
            _layout(right, values, colors, color) or [])


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
    """One-line status display: plain left/right text, or a ``template``
    (``ui.status_bar``) filled from named values (see ``render_template``)."""

    def __init__(self, left: str = "", right: str = "", *, style: Optional[BarStyle] = None,
                 template: Optional[str] = None, id: Optional[str] = None):
        super().__init__(id)
        self.left = left
        self.right = right
        self.style = style or BarStyle.default()
        self.template = template
        self.values: dict[str, str] = {}
        self.field_colors: dict[str, Any] = {}
        # The value drawn inverted (keyboard focus on the bar), and where each
        # value was last drawn (``value_at``).
        self.focused_value: Optional[str] = None
        self._value_spans: dict[str, tuple[int, int]] = {}

    def set_focused_value(self, name: Optional[str]):
        self.focused_value = name
        self.mark_changed()

    def shown_values(self) -> list[str]:
        """The names of the values the template shows, left to right."""
        left, right = self.runs()
        return list(dict.fromkeys(name for _, _, name in left + right if name))

    def value_at(self, x: int) -> Optional[str]:
        """The value drawn at column ``x``, if any."""
        return next((name for name, (start, end) in self._value_spans.items()
                     if start <= x < end), None)

    def set_field_colors(self, colors: dict[str, Any]):
        """Set per-value foreground colors (value name -> RGB/ANSIColor)."""
        self.field_colors = dict(colors)
        self.mark_changed()

    def get_preferred_height(self, width: int) -> int:
        return 1

    def set_template(self, template: str):
        self.template = template
        self.mark_changed()

    def set_values(self, values: dict[str, Any]):
        """Update the named values the template refers to."""
        self.values = {key: str(value) for key, value in values.items()}
        self.mark_changed()

    def runs(self) -> tuple[list, list]:
        """The left and right ``[(text, color, value name)]`` runs the template draws."""
        return render_template(self.template or "", self.values, self.field_colors,
                               self.style.fg)

    def text(self) -> str:
        """The bar as plain text (left part, then right part after a space)."""
        left, right = self.runs()
        return " ".join(part for part in ("".join(t for t, _, _ in left).strip(),
                                          "".join(t for t, _, _ in right).strip()) if part)

    def render(self, buffer: Buffer):
        if self.width <= 0 or self.height <= 0:
            return
        buffer.fill(self.x, self.y, self.width, 1, " ", bg=self.style.bg)

        right = self.right[:max(0, self.width - self.style.padding * 2)]
        if right:
            right_x = self.x + max(self.style.padding, self.width - self.style.padding - len(right))
            buffer.write_str(right_x, self.y, right, fg=self.style.fg,
                             bg=self.style.bg, max_width=self.width - (right_x - self.x))

        # A template draws each value in its own color (falling back to the
        # shared style fg), e.g. the context by how full the window is.
        if self.template is not None:
            x = self.x + self.style.padding
            edge = self.x + self.width - self.style.padding
            left, right = self.runs()
            self._value_spans = {}

            def draw(at: int, text: str, fg, name: Optional[str], limit: int) -> None:
                buffer.write_str(at, self.y, text, fg=fg, bg=self.style.bg,
                                 reverse=name is not None and name == self.focused_value,
                                 max_width=max(0, limit - at))
                if name:
                    self._value_spans[name] = (at, min(limit, at + display_width(text)))

            right_width = sum(display_width(text) for text, _, _ in right)
            if right and edge - right_width > x:
                tail_x = edge - right_width
                for text, fg, name in right:
                    draw(tail_x, text, fg, name, edge)
                    tail_x += display_width(text)
                edge -= right_width + 1         # keep a cell between the halves
            for text, fg, name in left:         # too narrow: the right half went first
                if x >= edge:
                    break
                draw(x, text, fg, name, edge)
                x += display_width(text)
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
