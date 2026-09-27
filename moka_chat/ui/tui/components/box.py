from dataclasses import dataclass
from typing import Optional, Any, List
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.buffer import Buffer, SubBuffer, Cell
from moka_chat.ui.tui.events import MouseEvent
from moka_chat.ui.tui.msg_types import MsgAction

from moka_chat import settings
from moka_chat.ui.tui.colors import theme

# Braille spinner frames for animating in-progress thinking.
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class SubBufferCellsProxy:
    """Proxy for cell access that translates absolute coordinates to SubBuffer-local."""

    def __init__(self, cells, x_offset, y_offset):
        self.cells = cells
        self.x_offset = x_offset
        self.y_offset = y_offset
        self._rows = {}

    def __getitem__(self, y):
        local_y = y - self.y_offset
        if 0 <= local_y < len(self.cells):
            row = self._rows.get(local_y)
            if row is None:
                row = SubBufferRowProxy(self.cells[local_y], self.x_offset)
                self._rows[local_y] = row
            return row
        return []


class SubBufferRowProxy:
    """Proxy for row access that translates x coordinates."""

    def __init__(self, row, x_offset):
        self.row = row
        self.x_offset = x_offset

    def __getitem__(self, x):
        local_x = x - self.x_offset
        if 0 <= local_x < len(self.row):
            return self.row[local_x]
        return Cell()


class SubBufferWrapper:
    """Buffer-compatible wrapper that redirects to a SubBuffer with coordinate translation."""

    def __init__(self, subbuffer, x_offset, y_offset):
        self.subbuffer = subbuffer
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.width = subbuffer.width
        self.height = subbuffer.height
        self.clip_rect = None
        self._cells_proxy = SubBufferCellsProxy(subbuffer.cells, x_offset, y_offset)

    @property
    def cells(self):
        return self._cells_proxy

    def set(self, x, y, char, fg=None, bg=None, bold=False, reverse=False, underline=False, italic=False):
        self.subbuffer.set(x - self.x_offset, y - self.y_offset, char, fg, bg, bold, reverse,
                           underline=underline, italic=italic)

    def write_str(self, x, y, s, fg=None, bg=None, bold=False, reverse=False, max_width=None,
                  underline=False, italic=False):
        self.subbuffer.write_str(x - self.x_offset, y - self.y_offset, s, fg, bg, bold, reverse, max_width,
                                 underline=underline, italic=italic)

    def fill(self, x, y, width, height, char=" ", fg=None, bg=None):
        self.subbuffer.fill(x - self.x_offset, y - self.y_offset, width, height, char, fg, bg)

    def set_clip(self, x, y, w, h):
        # Keep the clip in absolute coordinates (so children can compare against
        # their own x/y) while the subbuffer gets local coordinates.
        self.clip_rect = (x, y, w, h)
        self.subbuffer.set_clip(x - self.x_offset, y - self.y_offset, w, h)

    def clear_clip(self):
        self.clip_rect = None
        self.subbuffer.clear_clip()


class Box(Component):
    def __init__(self, child: Component, title: str = "", id: Optional[str] = None, bg=None, fg=None, focused: bool = False, actions: Optional[List] = None, padding: int = 0, padding_y: Optional[int] = None, focus_in_padding: bool = False, focus_color=None, lines_only: bool = False, title_provider: Optional[callable] = None, color_provider: Optional[callable] = None):
        super().__init__(id)
        self.child = child
        self.child.parent = self
        self.title = title
        self._title_provider = title_provider  # Optional callable -> current title
        self._color_provider = color_provider  # Optional callable -> current fg color
        self.bg = bg
        self.fg = fg
        self.focus_color = focus_color
        self.focused = focused
        self.actions = actions or []
        self.focused_action_key = None
        self.lines_only = lines_only  # If True, render only full-width top/bottom lines
        # Horizontal content padding is the default. Vertical padding is
        # opt-in; otherwise every compact form row would gain blank lines.
        self.padding = max(0, padding)
        self.padding_y = max(0, 0 if padding_y is None else padding_y)
        self.focus_in_padding = focus_in_padding

        if self.bg is None: self.bg = theme.get_bg()
        if self.fg is None: self.fg = theme.DEFAULT
        
        # SubBuffer for efficient rendering
        self.subbuffer: Optional[SubBuffer] = None
        self._sub_valid = False  # True once the SubBuffer holds a full frame
        self._grew_from: Optional[int] = None  # pre-growth height of the SubBuffer

        # Optional in-place editor (replaces child rendering while active)
        self.inline_editor = None

        # Hit regions for clickable actions in the bottom border
        # List of (local_x_start, local_x_end, MsgAction)
        # Populated during render; action click detection is handled by ChatHistoryPanel.
        self._action_hit_regions: List[tuple[int, int, MsgAction]] = []

    @property
    def children(self):
        return [self.child]

    @staticmethod
    def thread_content_width(box_width: int, pad_left: int, pad_right: int) -> int:
        """Wrap width for thread-mode content: box minus gutter and padding.

        Single owner of the ``gutter(1) + pad_left + pad_right`` arithmetic that
        both the :class:`Box` layout and the history panel's wrapping need.
        Callers clamp to a minimum of 1 before wrapping.
        """
        return box_width - 1 - pad_left - pad_right

    @property
    def current_title(self) -> str:
        """Resolve the title, honoring a dynamic title_provider if set."""
        if self._title_provider is not None:
            return self._title_provider() or self.title
        return self.title

    
    def set_focused(self, focused: bool):
        """Set the focused state of this box."""
        if self.focused != focused:
            self.focused = focused
            self.mark_changed()  # Focus changes appearance

    def set_layout(self, x: int, y: int, width: int, height: int):
        size_changed = (self.width, self.height) != (width, height)

        if self.lines_only:
            # Only top/bottom rules; the child is inset by the same 1-space
            # margin on each side, no walls.
            inset_x = 1 + self.padding
            inset_y = 1 + self.padding_y
            cx, cy = x + inset_x, y + inset_y
            cw, ch = width - 2 * inset_x, height - 2 * inset_y
        else:
            # Normal mode with borders.
            inset_x = 1 + self.padding
            inset_y = 1 + self.padding_y
            cx, cy = x + inset_x, y + inset_y
            cw, ch = width - 2 * inset_x, height - 2 * inset_y

        self._layout_self_and_child(x, y, width, height, cx, cy, cw, ch, size_changed)
        self._finalize_layout(x, y, width, height, size_changed)

    def _layout_self_and_child(self, x, y, width, height,
                               cx, cy, cw, ch, size_changed):
        """Position this Box and its child (avoiding redundant sublayout)."""
        if size_changed:
            super().set_layout(x, y, width, height)
            self.child.set_layout(cx, cy, cw, ch)
        else:
            self.x, self.y, self.width, self.height = x, y, width, height
            self.child.x, self.child.y = cx, cy
            self.child.width, self.child.height = cw, ch

    def _finalize_layout(self, x: int, y: int, width: int, height: int,
                         size_changed: bool):
        """Sync the SubBuffer/blit position and any inline editor after layout."""
        # Initialize or resize SubBuffer if size changed
        if size_changed:
            if self.subbuffer is None:
                self.subbuffer = SubBuffer(width, height)
                self._sub_valid = False
            elif width != self.subbuffer.width:
                # Width change rewraps content: full rebuild.
                self.subbuffer = SubBuffer(width, height)
                self._sub_valid = False
            elif height < self.subbuffer.height:
                self.subbuffer.shrink(height)
            elif height > self.subbuffer.height:
                # Append-only growth keeps the already-rasterized prefix, but
                # remember the pre-growth height: if the dirty tail starts below
                # it (e.g. a shrink+grow), the re-added rows lack a gutter and
                # need a full redraw.
                self._grew_from = self.subbuffer.height
                self.subbuffer.grow(height)
            self.mark_changed()

        # Update blit position (free for scrolling!)
        if self.subbuffer:
            self.subbuffer.set_position(x, y)

        # Keep inline editor layout in sync with the box inner area.
        if self.inline_editor is not None:
            inset_x = 1 + self.padding
            inset_y = 1 + self.padding_y
            self.inline_editor.set_layout(
                x + inset_x, y + inset_y,
                max(1, width - 2 * inset_x),
                max(1, height - 2 * inset_y),
            )

    def get_preferred_height(self, width: int) -> int:
        """Height needed: child height plus top/bottom borders (and padding)."""
        if self.inline_editor is not None:
            inner_w = max(1, width - 2 - 2 * self.padding)
            return self.inline_editor.get_preferred_height(inner_w) + 2 + 2 * self.padding_y
        if hasattr(self.child, 'get_preferred_height'):
            # Height of child inside the box plus top/bottom borders.
            # Child's width inside box is box_width - 2.
            inner_height = self.child.get_preferred_height(width - 2 - 2 * self.padding)
            return inner_height + 2 + 2 * self.padding_y
        # Otherwise fall back to a reasonable default or 0
        return 0
    
    def mark_changed(self, rect: Optional[tuple[int, int, int, int]] = None):
        """Mark this box as needing re-rendering."""
        super().mark_changed(rect if rect is not None else (self.x, self.y, self.width, self.height))
        if self.subbuffer:
            self.subbuffer.mark_changed()

    def render(self, buffer: Buffer):
        # A too-small box cannot draw its frame.
        min_size = self._min_size()
        if self.width < min_size or self.height < min_size:
            return
        
        # Ensure SubBuffer exists
        if self.subbuffer is None:
            self.subbuffer = SubBuffer(self.width, self.height)
            self.subbuffer.set_position(self.x, self.y)
            self.mark_changed()
        
        # Phase 1: Render to SubBuffer if changed
        if self.subbuffer.has_changed or not self._sub_valid:
            dirty_from = None
            if self._sub_valid:
                target = self.inline_editor if self.inline_editor is not None else self.child
                taker = getattr(target, "take_dirty_from_line", None)
                if callable(taker):
                    dirty_from = taker()
                # Growth from a lower height means the tail may start above the
                # re-added rows; a full redraw is required then.
                if (dirty_from is not None and self._grew_from is not None
                        and dirty_from > self._grew_from):
                    dirty_from = None
            self._render_to_subbuffer(dirty_from)
            self.subbuffer.has_changed = False
            self._sub_valid = True
            self._grew_from = None
        
        # Phase 2: Blit SubBuffer to main buffer (always happens, position updates are free!)
        self.subbuffer.blit(buffer, clip_rect=getattr(buffer, 'clip_rect', None))

        # Optional focus gutter: containers may keep their content unindented
        # while the focus arrow occupies the Box padding immediately to the
        # left of the focused child.
        if self.focus_in_padding and self.padding > 0:
            focused = getattr(self.child, "get_focused_field", lambda: None)()
            if focused is not None and getattr(focused, "focused", False):
                buffer.write_str(self.child.x - 1, focused.y, "▸",
                                 fg=theme.FOCUSED, max_width=1)
        
        # Phase 3: Render cursor overlay (outside SubBuffer caching)
        cursor_target = self.inline_editor if self.inline_editor is not None else self.child
        if hasattr(cursor_target, 'render_cursor'):
            cursor_target.render_cursor(buffer)
    
    def _render_to_subbuffer(self, dirty_from: Optional[int] = None):
        """Render box content to its SubBuffer using local coordinates (0,0).

        ``dirty_from`` is the first content row that changed (append-only tail
        raster); other modes ignore it and redraw fully.
        """
        # A previous incremental render may have left a clip on the shared
        # SubBuffer; never draw the frame's clear/gutter under it.
        self.subbuffer.clear_clip()
        title = self.current_title
        fg = self.fg
        actions = self.actions

        bg = self.bg

        # Lines-only mode: just full-width horizontal bars on top and bottom,
        # no vertical walls and no corner characters.
        if self.lines_only:
            self._render_lines_only_to_subbuffer()
            return
        
        if self.focused:
            fg = self.focus_color or theme.FOCUSED
        
        @dataclass(frozen=True)
        class BorderStyle:
            tl: str  # top-left
            tr: str  # top-right
            bl: str  # bottom-left
            br: str  # bottom-right
            h: str   # horizontal
            v: str   # vertical
            
        STYLES = {
            "square":  BorderStyle("┌", "┐", "└", "┘", "─", "│"),
            "double":  BorderStyle("╔", "╗", "╚", "╝", "═", "║"),
            "ascii":   BorderStyle("+", "+", "+", "+", "-", "|"),
            "rounded": BorderStyle("╭", "╮", "╰", "╯", "─", "│"),
        }

        # Use focused style if box is focused, otherwise use normal style
        style_name = settings.config.ui_box_style_focused if self.focused else settings.config.ui_box_style
        style = STYLES[style_name]

        # Clear SubBuffer
        self.subbuffer.clear()
        
        # Render using local coordinates (0, 0) within SubBuffer
        # 1. Top + Left borders
        self.subbuffer.set(0, 0, style.tl, fg=fg, bg=bg)

        for i in range(1, self.width - 1):
            self.subbuffer.set(i, 0, style.h, fg=fg, bg=bg)

        for i in range(1, self.height - 1):
            self.subbuffer.set(0, i, style.v, fg=fg, bg=bg)

        # 2. Background
        if self.bg:
            for iy in range(1, self.height - 1):
                for ix in range(1, self.width - 1):
                    self.subbuffer.set(ix, iy, " ", bg=bg)

        # 3. Content - render child to SubBuffer
        # Note: child still uses absolute coordinates from set_layout
        # We need to create a temporary buffer that maps to our SubBuffer
        # For now, create a wrapper buffer that redirects to SubBuffer
        temp_buffer = self._create_subbuffer_wrapper()
        render_target = self.inline_editor if self.inline_editor is not None else self.child
        render_target.render(temp_buffer)

        # 4. Bottom + Right borders
        for i in range(1, self.height - 1):
            self.subbuffer.set(self.width - 1, i, style.v, fg=fg, bg=bg)

        # Bottom border with actions
        # Reset hit regions (only valid when focused with actions)
        self._action_hit_regions = []

        if actions:
            bottom_str = " " + " ".join(action.format() for action in actions) + " "
            bottom_width = len(bottom_str)

            # Space available between the bottom corners.
            available_width = self.width - 3

            if bottom_width <= available_width:
                # Draw left part of bottom border
                left_border_width = available_width - bottom_width
                for i in range(1, left_border_width + 1):
                    self.subbuffer.set(i, self.height - 1, style.h, fg=fg, bg=bg)

                self.subbuffer.write_str(left_border_width + 1, self.height - 1, bottom_str, fg=fg, bg=bg)

                # Record action hit regions (local SubBuffer coordinates)
                if self.focused:
                    # Each action occupies "[key] label " (with trailing space);
                    # the block has one separator space before its first label.
                    x_offset = left_border_width + 2
                    for action in actions:
                        formatted = action.format()
                        region_start = x_offset
                        region_end = x_offset + len(formatted)
                        self._action_hit_regions.append((region_start, region_end, action))
                        # Flash feedback: overwrite this action's cells with reverse
                        if self.focused_action_key and action.key == self.focused_action_key:
                            for fx in range(region_start, region_end):
                                self.subbuffer.set(fx, self.height - 1,
                                                   self.subbuffer.cells[self.height - 1][fx].char,
                                                   fg=fg, bg=bg, reverse=True)
                        x_offset += len(formatted) + 1  # +1 for the space separator

                # Draw one more border char on the right before the corner
                self.subbuffer.set(self.width - 2, self.height - 1, style.h, fg=fg, bg=bg)
            else:
                # Content too long, just draw normal border
                for i in range(1, self.width - 1):
                    self.subbuffer.set(i, self.height - 1, style.h, fg=fg, bg=bg)
        else:
            # No actions, draw normal bottom border
            for i in range(1, self.width - 1):
                self.subbuffer.set(i, self.height - 1, style.h, fg=fg, bg=bg)

        # Corners
        self.subbuffer.set(self.width - 1, 0, style.tr, fg=fg, bg=bg)
        self.subbuffer.set(0, self.height - 1, style.bl, fg=fg, bg=bg)
        self.subbuffer.set(self.width - 1, self.height - 1, style.br, fg=fg, bg=bg)

        # Title
        if title:
            title_str = f" {title[:self.width-4]} "
            self.subbuffer.write_str(2, 0, title_str, fg=fg, bg=bg)
    
    def _render_lines_only_to_subbuffer(self):
        """Render only full-width horizontal bars on the top and bottom edges.

        No corner characters and no vertical walls — just a clean top and
        bottom line spanning the full width, ending in half-lines. The
        current title (mode) is drawn inline in the top bar.
        """
        if self.focused and self.focus_color:
            fg = self.focus_color
        elif self._color_provider:
            fg = self._color_provider()
        else:
            fg = self.fg
        bg = self.bg

        self.subbuffer.clear()

        # Fill background
        if bg:
            for iy in range(self.height):
                for ix in range(self.width):
                    self.subbuffer.set(ix, iy, " ", bg=bg)

        # Top and bottom rules span the full width but start/end with light
        # half-lines so the rule fades at the edges instead of butting into a
        # corner (used by the input field).
        def _draw_rule(row: int) -> None:
            if self.width >= 2:
                self.subbuffer.set(0, row, "╶", fg=fg, bg=bg)
                for ix in range(1, self.width - 1):
                    self.subbuffer.set(ix, row, "─", fg=fg, bg=bg)
                self.subbuffer.set(self.width - 1, row, "╴", fg=fg, bg=bg)
            else:
                for ix in range(self.width):
                    self.subbuffer.set(ix, row, "─", fg=fg, bg=bg)

        _draw_rule(0)

        # Section name (mode) inline in the top bar: "─ message ───...".
        title = self.current_title
        if title:
            title_str = f" {title[:max(0, self.width - 4)]} "
            self.subbuffer.write_str(1, 0, title_str, fg=fg, bg=bg)

        _draw_rule(self.height - 1)

        # Render child content (inset by the layout previously computed).
        temp_buffer = self._create_subbuffer_wrapper()
        self.child.render(temp_buffer)

    def _min_size(self) -> int:
        """Smallest width/height this box can render at (borders need 2)."""
        return 2

    def _create_subbuffer_wrapper(self):
        """Create a Buffer-compatible wrapper redirecting to this Box's SubBuffer."""
        return SubBufferWrapper(self.subbuffer, self.x, self.y)

    def handle_input(self, event: Any) -> bool:
        """Pass input to child, but check mouse bounds for the box area."""
        if isinstance(event, MouseEvent):
            if self.x <= event.x < self.x + self.width and \
               self.y <= event.y < self.y + self.height:
                return self.child.handle_input(event)
            return False
        return self.child.handle_input(event)
