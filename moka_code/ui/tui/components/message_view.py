"""Message-specific Box: borderless thread rendering with a role gutter.

``Box`` stays generic (borders, padding, clipping, generic actions). This
subclass owns the chat-transcript presentation: the gutter glyph, collapsed
thinking lines, and the message lifecycle-aware gutter. It is always
thread-mode, so it removes the mode branching from ``Box``.
"""
from __future__ import annotations

from typing import Optional

from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.components.box import Box


class MessageView(Box):
    """A thread-mode Box backed by a :class:`~moka_code.ui.chat_message.Message`."""

    def __init__(
        self,
        child,
        parent_msg,
        *,
        gutter: str = "▸",
        gutter_color=None,
        gutter_full_height: bool = False,
        compact_when_unfocused: bool = False,
        content_pad_left: int = 0,
        content_pad_right: int = 0,
    ):
        super().__init__(child)
        # Message-backing state (Box stays message-agnostic).
        self.parent_msg = parent_msg
        self.thread_mode = True
        # Drives compact tool/permission headers; text-side only (see Message).
        self.compact_when_unfocused = compact_when_unfocused
        self.gutter = gutter
        self.gutter_color = gutter_color
        self.full_height_gutter = gutter_full_height
        self.content_pad_left = max(0, content_pad_left)
        self.content_pad_right = max(0, content_pad_right)

    # -- layout --------------------------------------------------------

    def _min_size(self) -> int:
        # Borderless thread content can render in a single row (collapsed).
        return 1

    def set_layout(self, x: int, y: int, width: int, height: int):
        size_changed = (self.width, self.height) != (width, height)
        pad_l = self.content_pad_left
        pad_r = self.content_pad_right
        # A focused message with actions reserves the last row for the action
        # line, so the content is one row shorter.
        has_actions = bool(self._visible_actions())
        child_h = max(0, height - (1 if has_actions else 0))
        child_x = x + 1 + pad_l
        child_w = max(0, self.thread_content_width(width, pad_l, pad_r))
        self._layout_self_and_child(
            x, y, width, height, child_x, y, child_w, child_h, size_changed)
        self._finalize_layout(x, y, width, height, size_changed)

    def get_preferred_height(self, width: int) -> int:
        # Collapsed messages render a single summary line.
        if self.parent_msg is not None and getattr(self.parent_msg, "collapsed", False):
            return 1
        # No borders; the child width is reduced by the gutter + padding.
        inner_w = max(1, self.thread_content_width(
            width, self.content_pad_left, self.content_pad_right))
        base = self.child.get_preferred_height(inner_w)
        if self._visible_actions():
            base += 1
        # A message always occupies at least its gutter row, even when its
        # content is empty (a thought with no exposed reasoning, an empty
        # notice, ...). Without this the box collapses to zero rows and the
        # message — prefix included — vanishes from the transcript.
        return max(1, base)

    def _visible_actions(self):
        if self.parent_msg is not None:
            return self.parent_msg.inline_action_items() if self.focused else []
        return self.actions if self.focused else []

    # -- rendering -----------------------------------------------------

    def _render_to_subbuffer(self, dirty_from: Optional[int] = None):
        # A previous incremental render may have left a clip on the shared
        # SubBuffer; never draw the frame's clear/gutter under it.
        self.subbuffer.clear_clip()
        self._render_thread_to_subbuffer(dirty_from)

    def _render_thread_to_subbuffer(self, dirty_from: Optional[int] = None):
        """Render borderless content with a role gutter.

        The gutter (e.g. ▸ for assistant, ▌ for user/assistant) is drawn in the first
        column, colored by the message's frame color. Content flows to the
        right. When focused, actions render on the last row.

        When ``dirty_from`` is set, only rows from that content line down are
        cleared and re-rasterized (append-only streaming); the unchanged prefix
        is kept from the previous frame.
        """
        bg = self.bg
        # Gutter glyph/color come from the message type (▌ everywhere, colored
        # per type: USER, MUTED, TOOL, PERMISSION).
        fg = self.gutter_color or self.fg
        gutter = self.gutter

        collapsed = self.parent_msg is not None and getattr(self.parent_msg, "collapsed", False)
        incremental = (
            dirty_from is not None
            and dirty_from > 0
            and not collapsed
            and dirty_from < self.height
        )

        if incremental:
            start = dirty_from
            self.subbuffer.clear_region(0, start, self.width, self.height - start)
        else:
            start = 0
            self.subbuffer.clear()

        # Fill background
        if bg:
            for iy in range(start, self.height):
                for ix in range(self.width):
                    self.subbuffer.set(ix, iy, " ", bg=bg)

        # Draw the role gutter in the first column. User/assistant messages use a
        # full-height prefix bar (every row); status gutters stay on row 0.
        if gutter and self.width > 0:
            if self.full_height_gutter:
                rows = range(start, self.height)
            else:
                rows = (0,) if start == 0 else ()
            for row in rows:
                self.subbuffer.set(0, row, gutter, fg=fg, bg=bg)

        # Collapsed messages (e.g. thinking folded by default) render a single
        # summary line instead of the full content.
        if collapsed:
            self.parent_msg.render_collapsed_line(
                self.subbuffer,
                max_width=max(0, self.width - 2),
                fg=self.gutter_color or self.fg,
                bg=bg,
            )
            return

        # Render child content (no border offset). For incremental raster, clip
        # to the dirty band so the child skips unchanged lines.
        temp_buffer = self._create_subbuffer_wrapper()
        if incremental:
            temp_buffer.set_clip(self.x, self.y + start, self.width, self.height - start)
        self.child.render(temp_buffer)

        # Actions on a dedicated row below the content when focused. This row
        # is part of the box height (see get_preferred_height), so it pushes
        # subsequent messages down rather than overlaying content.
        actions = self._visible_actions()
        if actions:
            actions_str = " ".join(action.format() for action in actions)
            self.subbuffer.write_str(2, self.height - 1, actions_str,
                                     fg=theme.FOCUSED, bg=bg, max_width=max(0, self.width - 2))
            # Record hit regions for mouse clicks.
            self._action_hit_regions = []
            x_offset = 2
            for action in actions:
                formatted = action.format()
                self._action_hit_regions.append((x_offset, x_offset + len(formatted), action))
                x_offset += len(formatted) + 1


__all__ = ["MessageView"]
