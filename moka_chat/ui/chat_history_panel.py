"""Chat history panel for the moka TUI."""

import bisect
import logging
import time
from typing import Optional, Any
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.components import TextComponent
from moka_chat.ui.tui.components.box import Box
from moka_chat.ui.tui.events import MouseEvent, TickEvent

from moka_chat import settings
from moka_chat.ui.clipboard import copy_to_clipboard
from moka_chat.ui.message_selection import MessageSelection
from moka_chat.ui.tui.colors import theme, RGB
from moka_chat.ui.tui.msg_types import MsgType, MsgAction, ThinkingMsg

from moka_chat.ui.chat_message import Message

# Seconds between refreshes of live labels (elapsed time) on in-progress lines.
_LIVE_TICK_INTERVAL = 0.25


def _clamped(msg: Message) -> bool:
    """True for activity lines (thoughts, tool calls) that stack without gaps."""
    return getattr(msg.type, "clamped", False)


class AnswerGroup:
    """Shared by the segments of one split answer (see ``split_answer``)."""

    def __init__(self, text: str):
        self.text = text  # the whole answer, copied when the group is selected


def _is_thought(msg: Message) -> bool:
    return isinstance(msg.type, ThinkingMsg)


def _needs_gap(prev: Message, msg: Optional[Message]) -> bool:
    """Blank line between two messages: none inside a block of activity."""
    return not (_clamped(prev) and (msg is None or _clamped(msg)))


def _gap_before(messages: list, i: int) -> bool:
    """Whether message ``i`` starts after a blank line.

    A thought belongs to the message it precedes: it sits directly on top of
    it, and when that message needs a gap the gap goes above the thought.
    """
    if i == 0:
        return False
    prev, msg = messages[i - 1], messages[i]
    if prev.group is not None and prev.group is msg.group:
        return False  # segments of one split answer touch
    if _is_thought(prev):
        return False
    if _is_thought(msg):
        following = next((m for m in messages[i + 1:] if not _is_thought(m)), None)
        return _needs_gap(prev, following)
    return _needs_gap(prev, msg)


class ChatHistoryPanel(TextComponent):
    """Manages the chat history display panel with dynamic width support."""

    def __init__(self, max_width: int = 80):
        """Initialize the chat history panel.
        
        Args:
            max_width: Initial maximum width for message line wrapping
        """
        super().__init__("", id="history")
        self.messages = []
        self.max_width = max_width
        self.left_pad = settings.config.ui_msg_h_padding
        self.right_pad = settings.config.ui_msg_h_padding
        self.max_messages = 150  # Maximum number of messages to keep
        self.scroll_offset = 0   # How many rows to scroll up from the bottom
        self.auto_scroll = True
        self.anchored_start_y: Optional[int] = None  # Absolute Y position when scrolled up (for stability)
        self.focused_message_index: Optional[int] = None  # Index of the currently focused message
        # True while one segment of a split answer is selected (``→``);
        # otherwise a split answer is selected as a whole.
        self.inside_group = False
        # Brief hint callback (the app flashes it on the action line).
        self.on_hint: Optional[callable] = None
        self.has_keyboard_focus = False  # Track if this panel should handle keyboard input
        # Notified (no args) whenever the selected message changes, so the app
        # can refresh its contextual mode line.
        self.on_selection_changed = None
        # Optional sink ``(text, level)`` for non-conversation notices
        # (SysMsg/SysMsgError/SysMsgWarning). When set, those messages are
        # routed out of the transcript into the activity surface.
        self.activity_sink = None
        self._message_height_cache: dict[tuple[int, int, int], int] = {}

        # Text selection (drag state, coordinate math, highlight overlay).
        self.selection = MessageSelection(self)

        # Action click feedback: flash an action with inverted colors briefly
        self._flash_msg: Optional[Message] = None
        self._flash_action_key: Optional[str] = None  # e.g. "c" for COPY
        self._flash_until: float = 0.0  # monotonic time when flash expires
        # Live labels (elapsed seconds) are refreshed a few times per second,
        # not on every render frame.
        self._live_tick_next_at: float = 0.0
        
        # Initial component - self is now the component
        self.compositor: Optional[object] = None
        
        self.on_action: Optional[callable] = None

    def set_compositor(self, compositor):
        """Set the compositor for updates."""
        self.compositor = compositor

    def mark_changed(self, rect: Optional[tuple[int, int, int, int]] = None):
        """Mark the whole panel dirty and wake the compositor.

        The panel always repaints its full area (background fill + blit of every
        visible message), so it must report its full bounds as the dirty rect.
        Propagating a smaller child rect would make the compositor clip to the
        child's stale (pre-growth) size and leave newly revealed rows unpainted.
        """
        super().mark_changed((self.x, self.y, self.width, self.height))
        if self.compositor and hasattr(self.compositor, 'request_render'):
            self.compositor.request_render()

    def _request_repaint(self):
        self.mark_changed((self.x, self.y, self.width, self.height))
    
    def set_focused_message(self, index: Optional[int], inside: bool = False):
        """Set the focused message by index.

        The segments of a split answer (``Message.group``) are one message for
        navigation: by default the whole group is selected and ``index`` snaps
        to its first segment. ``inside=True`` selects the single segment at
        ``index`` (``→``, or a click on a segment); it has no effect on a
        message that is not split.
        """
        # Clear previous focus
        if (self.focused_message_index is not None
                and 0 <= self.focused_message_index < len(self.messages)):
            start, end = self._group_range(self.focused_message_index)
            for prev in self.messages[start:end]:
                prev.set_focused(False)
                # Collapsible messages (thinking) fold back when unfocused.
                if hasattr(prev, "set_collapsed") and getattr(prev, "collapsible", False):
                    prev.set_collapsed(True)

        # Normalize an out-of-range index to "no focus".
        if index is not None and not (0 <= index < len(self.messages)):
            index = None
        start = end = 0
        if index is not None:
            start, end = self._group_range(index)
            inside = inside and end - start > 1
            if not inside:
                index = start
        else:
            inside = False

        # Set new focus
        self.focused_message_index = index
        self.inside_group = inside
        if index is not None:
            current = self.messages[index]
            for msg in ([current] if inside else self.messages[start:end]):
                msg.set_focused(True)
            # Collapsible messages (thinking) expand when focused — but only
            # when there is text to reveal: an empty thought stays a summary
            # line instead of collapsing to a bare prefix.
            if (hasattr(current, "set_collapsed")
                    and getattr(current, "collapsible", False)
                    and (current.base_text or "").strip()):
                current.set_collapsed(False)
            # Disable auto-scroll when focusing a message, UNLESS it's the last message
            # (we want to follow the last message's content as it updates)
            if end < len(self.messages):
                self.auto_scroll = False
        self._request_repaint()
        self._notify_selection_changed()

    def _group_range(self, index: int) -> tuple[int, int]:
        """``[start, end)`` of the split answer containing ``index`` (or itself)."""
        group = self.messages[index].group
        if group is None:
            return index, index + 1
        start, end = index, index + 1
        while start > 0 and self.messages[start - 1].group is group:
            start -= 1
        while end < len(self.messages) and self.messages[end].group is group:
            end += 1
        return start, end

    def enter_group(self) -> bool:
        """``→``: select the first segment of the selected split answer."""
        index = self.focused_message_index
        if index is None or self.inside_group:
            return False
        start, end = self._group_range(index)
        if end - start < 2:
            if self.on_hint is not None:
                self.on_hint("single block")
            return True
        self.set_focused_message(start, inside=True)
        self._scroll_to_show_message(start, prefer_top=True)
        return True

    def exit_group(self) -> bool:
        """``←``/Esc: back from a segment to the whole answer."""
        if not self.inside_group or self.focused_message_index is None:
            return False
        self.set_focused_message(self.focused_message_index)
        return True

    def segment_label(self) -> str:
        """``code 2/4`` while a segment is selected, else empty."""
        index = self.focused_message_index
        if not self.inside_group or index is None:
            return ""
        start, end = self._group_range(index)
        kind = self.messages[index].segment_kind or "text"
        return f"{kind} {index - start + 1}/{end - start}"

    def copy_text_for(self, msg: Message) -> str:
        """What ``c`` copies: the whole answer, or the selected segment."""
        if msg.group is not None and not self.inside_group:
            return msg.group.text
        return msg.copy_text if msg.copy_text is not None else msg.base_text

    def split_answer(self, msg: Message) -> None:
        """Replace a finished answer by its segments (prose / code / table).

        Done once the answer is complete, never while it streams. An answer
        with a single segment stays as is.
        """
        from moka_chat.ui.answer_split import split_answer
        from moka_chat.ui.tui.msg_types import AssistantMsg

        if type(msg.type) is not AssistantMsg or msg.group is not None:
            return
        try:
            index = self.messages.index(msg)
        except ValueError:
            return
        segments = split_answer(msg.base_text)
        if len(segments) < 2:
            return
        group = AnswerGroup(msg.base_text)
        parts = []
        for segment in segments:
            part = self.new_message(segment.text, msg_type=AssistantMsg(),
                                    harness_message_ids=list(msg.harness_message_ids))
            part.group, part.segment_kind, part.copy_text = group, segment.kind, segment.copy
            part.finalize()
            part.get_component().parent = self
            parts.append(part)
        for attr in ("metrics_tokens", "metrics_tokens_per_second",
                     "metrics_ttft_ms", "metrics_duration_ms"):
            setattr(parts[-1], attr, getattr(msg, attr))

        focused = self.focused_message_index
        if focused == index:
            msg.set_focused(False)
            self.focused_message_index = None
        self.messages[index:index + 1] = parts
        if focused is not None and focused > index:
            self.focused_message_index = focused + len(parts) - 1
        self._message_height_cache.clear()
        if focused == index:
            self.set_focused_message(index)
        self._request_repaint()

    def _notify_selection_changed(self):
        if self.on_selection_changed is not None:
            self.on_selection_changed()

    def _dispatch_action(self, message, action: MsgAction):
        """Dispatch a MsgAction for a message, mirroring the keyboard handler logic.

        Triggers a brief visual flash on the action button before dispatching.
        """
        # Flash the action button for visual feedback
        self._flash_msg = message
        self._flash_action_key = action.key
        self._flash_until = time.monotonic() + 0.12  # 120ms flash
        message._flash_action_key = action.key
        message.box.mark_changed()
        self._request_repaint()

        if self.on_action:
            self.on_action(message, action)

    def _cached_hit_test(self, screen_y: int) -> tuple[Optional[int], Optional[int]]:
        """Map a screen y coordinate to (msg_index, local_y) using the row index.

        Returns (None, None) if the y is on a gap or outside content.
        """
        starts, ends, total = self._row_index()
        max_scroll = max(0, total - self.height)

        if self.auto_scroll or self.anchored_start_y is None:
            start_y = max_scroll
        else:
            start_y = self.anchored_start_y

        virtual_y = (screen_y - self.y) + start_y
        if virtual_y < 0 or virtual_y >= total:
            return None, None
        index = bisect.bisect_right(starts, virtual_y) - 1
        if index < 0 or virtual_y >= ends[index]:
            return None, None  # inside the gap after a message
        return index, virtual_y - starts[index]

    def _auto_copy_selection(self):
        """Copy the current selection to clipboard (called on mouse release after drag)."""
        text = self.selection.get_text()
        if not text:
            return
        method = copy_to_clipboard(text)
        if method:
            logging.getLogger("tui").info("Selection copied to clipboard (%s)", method)
        else:
            logging.getLogger("tui").warning("No clipboard method for selection copy")

    def move_focus_up(self) -> bool:
        """Move focus to the previous message (or segment, inside an answer).

        Returns:
            True if focus moved, False at the top (or the first segment)
        """
        if not self.messages:
            return False
        index = self.focused_message_index
        if index is None:
            # Focus the last message if nothing is focused
            self.set_focused_message(len(self.messages) - 1)
        elif self.inside_group:
            start, _ = self._group_range(index)
            if index <= start:
                return False
            self.set_focused_message(index - 1, inside=True)
        else:
            start, _ = self._group_range(index)
            if start == 0:
                return False
            self.set_focused_message(start - 1)
        self._scroll_to_show_message(self.focused_message_index, prefer_top=True)
        return True

    def move_focus_down(self) -> bool:
        """Move focus to the next message (or segment, inside an answer).

        Returns:
            True if focus moved, False at the bottom (or the last segment)
        """
        if not self.messages:
            return False
        index = self.focused_message_index
        if index is None:
            # Focus the first message if nothing is focused
            self.set_focused_message(0)
            shown = self.focused_message_index
        elif self.inside_group:
            _, end = self._group_range(index)
            if index + 1 >= end:
                return False
            self.set_focused_message(index + 1, inside=True)
            shown = self.focused_message_index
        else:
            _, end = self._group_range(index)
            if end >= len(self.messages):
                return False
            self.set_focused_message(end)
            # A split answer: bring its end into view, like one tall message.
            shown = self._group_range(self.focused_message_index)[1] - 1
        self._scroll_to_show_message(shown, prefer_top=False)
        return True

    def clear_focus(self):
        """Clear the focused message."""
        self.set_focused_message(None)
    
    def set_keyboard_focus(self, has_focus: bool):
        """Set whether this panel should handle keyboard input."""
        self.has_keyboard_focus = has_focus
        if not has_focus:
            # Clear message focus when losing keyboard focus
            self.clear_focus()
        self._request_repaint()

    def set_layout(self, x: int, y: int, width: int, height: int):
        """Override to detect width changes and trigger reformat."""
        super().set_layout(x, y, width, height)
        
        # If width changed, notify the panels to reformat
        if width != self.max_width and width > 0:
            self.on_width_change(width)

    def on_width_change(self, new_width: int):
        """Called automatically when the component width changes.
        
        Args:
            new_width: The new width of the component
        """
        self.max_width = new_width

        # Content width for a message = panel width - gutter (1) - padding.
        # The Box owns the gutter/padding; this is just the wrap width.
        for message in self.messages:
            msg_inner_width = Box.thread_content_width(
                new_width, message.left_pad, message.right_pad)
            if msg_inner_width < 1:
                msg_inner_width = 1
            message.reformat(msg_inner_width)
        self._message_height_cache.clear()
        self._request_repaint()

    def _get_message_height(self, msg: Message) -> int:
        """Return a cached message height for the current width and content revision."""
        width = self.width - msg.left_margin - msg.right_margin
        revision = getattr(msg, "layout_revision", 0)
        key = (id(msg), width, revision)
        height = self._message_height_cache.get(key)
        if height is None:
            height = msg.get_component().get_preferred_height(width)
            self._message_height_cache[key] = height
        return height

    def _row_index(self) -> tuple[list[int], list[int], int]:
        """Virtual-y layout for all messages: (starts, ends, total).

        ``starts[i]``/``ends[i]`` are the virtual y of message *i*'s top and
        bottom (the inter-message gap is included at the start of each message
        after the first). ``total`` is the full virtual height.

        This is O(number of messages) using the height cache, replacing the
        old O(total rows) line-map that was materialized per scroll event.
        """
        gap = settings.config.ui_msg_v_margin
        starts: list[int] = []
        ends: list[int] = []
        y = 0
        for i, msg in enumerate(self.messages):
            # Activity (thoughts, tool calls) stacks without gaps; prose and
            # turns are separated by one. See ``_gap_before``.
            if _gap_before(self.messages, i):
                y += gap
            starts.append(y)
            y += self._get_message_height(msg)
            ends.append(y)
        return starts, ends, y

    def _get_message_virtual_y_range(self, msg_index: int) -> tuple[int, int]:
        """Get the virtual y-coordinate range for a message (exclusive end)."""
        if msg_index < 0 or msg_index >= len(self.messages):
            return (0, 0)
        starts, ends, _ = self._row_index()
        return (starts[msg_index], ends[msg_index])
    
    def _scroll_to_show_message(self, msg_index: int, prefer_top: bool = True):
        """Scroll to ensure a message is visible.
        
        Args:
            msg_index: Index of the message to show
            prefer_top: If True, prioritize showing the top of the message.
                       If False, prioritize showing the bottom.
        """
        if msg_index < 0 or msg_index >= len(self.messages):
            return
        
        # Get message's virtual position
        msg_start, msg_end = self._get_message_virtual_y_range(msg_index)
        msg_height = msg_end - msg_start
        
        # Calculate current visible range in virtual coordinates.
        # Derive start_y from auto_scroll/anchored_start_y (the source of truth used by
        # render() and the mouse wheel handler) rather than self.scroll_offset, which is
        # only refreshed inside render() and can be stale between renders (e.g. while the
        # last message is streaming and total_height/max_scroll keep growing). Using a
        # stale scroll_offset yields a wrong start_y, which makes the "already visible"
        # check pass incorrectly and the view fail to follow the newly focused message.
        _, _, total_height = self._row_index()
        max_scroll = max(0, total_height - self.height)
        if self.auto_scroll or self.anchored_start_y is None:
            start_y = max_scroll
        else:
            start_y = self.anchored_start_y
        end_y = start_y + self.height
        
        # Check if message is already fully visible
        if msg_start >= start_y and msg_end <= end_y:
            return  # Already visible
        
        if prefer_top:
            # Scrolling up - prioritize showing the top
            if msg_start < start_y:
                # Message top is above visible area, scroll up to show it
                new_start_y = msg_start
                self.anchored_start_y = new_start_y
                self.scroll_offset = max_scroll - new_start_y
                self.scroll_offset = max(0, min(max_scroll, self.scroll_offset))
            elif msg_end > end_y:
                # Message bottom is below visible area
                # Try to show the whole message, but prioritize the top
                if msg_height <= self.height:
                    # Message fits in view, show it all
                    new_start_y = msg_end - self.height
                else:
                    # Message is taller than view, show from the top
                    new_start_y = msg_start
                self.anchored_start_y = new_start_y
                self.scroll_offset = max_scroll - new_start_y
                self.scroll_offset = max(0, min(max_scroll, self.scroll_offset))
        else:
            # Scrolling down - prioritize showing the bottom
            if msg_end > end_y:
                # Message bottom is below visible area, scroll down to show it
                new_end_y = msg_end
                new_start_y = new_end_y - self.height
                self.anchored_start_y = new_start_y
                self.scroll_offset = max_scroll - new_start_y
                self.scroll_offset = max(0, min(max_scroll, self.scroll_offset))
            elif msg_start < start_y:
                # Message top is above visible area
                # Try to show the whole message, but prioritize the bottom
                if msg_height <= self.height:
                    # Message fits in view, show it all
                    new_end_y = msg_start + self.height
                    new_start_y = new_end_y - self.height
                else:
                    # Message is taller than view, show from the bottom
                    new_end_y = msg_end
                    new_start_y = new_end_y - self.height
                self.anchored_start_y = new_start_y
                self.scroll_offset = max_scroll - new_start_y
                self.scroll_offset = max(0, min(max_scroll, self.scroll_offset))
        
        self.auto_scroll = False

    def render(self, buffer: Buffer):
        """Custom render to handle scrolling/clipping of messages."""
        # Clear expired action flash
        if self._flash_until > 0 and time.monotonic() >= self._flash_until:
            if self._flash_msg is not None:
                self._flash_msg._flash_action_key = None
                self._flash_msg.box.mark_changed()
            self._flash_msg = None
            self._flash_action_key = None
            self._flash_until = 0.0
        
        # Set clipping region to this panel's bounds
        if hasattr(buffer, 'set_clip'):
            buffer.set_clip(self.x, self.y, self.width, self.height)

        # Clear background first (to prevent artifacts from previous frames/scrolls)
        buffer.fill(self.x, self.y, self.width, self.height, " ", bg=theme.get_bg())

        if not self.messages and settings.config.ui_show_banner:
            self._render_banner(buffer)

        # Virtual layout: (starts, ends, total). O(messages), no per-row map.
        starts, ends, total_height = self._row_index()

        # Base offset (how much we need to scroll to see the bottom)
        max_scroll = max(0, total_height - self.height)

        # If auto-scroll is on, we always show the bottom
        if self.auto_scroll:
            self.scroll_offset = 0
            self.anchored_start_y = None  # Clear anchor when in auto-scroll mode
            start_y = max_scroll
        else:
            # When manually scrolled, use anchored position to stay stable
            # even when content grows at the bottom
            if self.anchored_start_y is None:
                # First time entering manual scroll mode, anchor current position
                self.anchored_start_y = max_scroll - self.scroll_offset

            # Clamp anchored position to valid range
            self.anchored_start_y = max(0, min(max_scroll, self.anchored_start_y))
            start_y = self.anchored_start_y

            # Keep scroll_offset in sync for compatibility
            self.scroll_offset = max_scroll - start_y

        viewport_top = self.y
        viewport_bottom = self.y + self.height

        # Only touch messages intersecting the viewport. ``ends`` and ``starts``
        # are strictly increasing, so bisect to the visible window instead of
        # walking every message (the bottom-up early-exit, generalized).
        first = bisect.bisect_right(ends, start_y)
        last = min(len(self.messages), bisect.bisect_left(starts, start_y + self.height))
        focus_start = focus_end = -1
        if (self.focused_message_index is not None
                and 0 <= self.focused_message_index < len(self.messages)):
            focus_start, focus_end = self._group_range(self.focused_message_index)
        for i in range(first, last):
            msg = self.messages[i]
            child = msg.get_component()
            child_w = self.width - msg.left_margin - msg.right_margin
            child_h = ends[i] - starts[i]
            child_y = self.y - start_y + starts[i]

            child.set_layout(self.x + msg.left_margin, child_y, child_w, child_h)
            child.render(buffer)

            # Selected message: draw a bright selection bar in the left margin.
            # A whole split answer gets ``▌`` on every segment; a single
            # selected segment gets the wider ``█`` (the rest keep their bar).
            marker = None
            if focus_start <= i < focus_end:
                if not self.inside_group:
                    marker = "▌"
                elif i == self.focused_message_index:
                    marker = "█"
            if marker is not None:
                top = max(child_y, viewport_top)
                bottom = min(child_y + child_h, viewport_bottom)
                for row_y in range(top, bottom):
                    buffer.write_str(self.x, row_y, marker, fg=theme.FOCUSED,
                                     bg=theme.get_bg(), max_width=1)

            # Render selection highlight if this message has an active selection
            sel = self.selection.state
            if sel is not None and sel.msg is msg:
                self.selection.render(buffer, msg, child, child_y, child_w, child_h)

        # Clear clipping region
        if hasattr(buffer, 'clear_clip'):
            buffer.clear_clip()

    def _render_banner(self, buffer: Buffer) -> None:
        """The moka art, centered in the empty transcript (see ``ui/banner.py``)."""
        from moka_chat.ui.banner import banner_lines
        from moka_chat.ui.tui.layout_utils import display_width

        lines = banner_lines(self.width)
        if not lines or len(lines) > self.height:
            return
        art_width = max(display_width(line) for line in lines)
        left = self.x + (self.width - art_width) // 2
        top = self.y + (self.height - len(lines)) // 2
        # Plain text color, like the transcript's own text.
        fg, bg = theme.DEFAULT, theme.get_bg()
        for row, line in enumerate(lines):
            for col, char in enumerate(line):
                if char != " ":
                    buffer.write_str(left + col, top + row, char, fg=fg, bg=bg, max_width=1)

    def handle_input(self, event: Any) -> bool:
        """Handle mouse wheel for scrolling and keyboard navigation."""
        # Refresh the live labels of in-progress messages (thinking/waiting
        # seconds, a running tool's elapsed time). Tick events fire every
        # render frame; messages only redraw when their label changes.
        if isinstance(event, TickEvent):
            if event.timestamp >= self._live_tick_next_at:
                self._live_tick_next_at = event.timestamp + _LIVE_TICK_INTERVAL
                for msg in self.messages:
                    if not getattr(msg, "finalized", True):
                        tick = getattr(msg, "tick", None)
                        if callable(tick):
                            tick()
            return False

        # Handle keyboard input only if this panel has keyboard focus
        if isinstance(event, str) and self.has_keyboard_focus:
            # 'y' yanks (copies) the current mouse selection, if any
            if event == 'y' and self.selection.has_selection:
                self._auto_copy_selection()
                return True

            # ESC clears an active text selection, then the message selection.
            if event == '\x1b':
                if self.selection.has_selection:
                    self.selection.clear()
                    self._request_repaint()
                    return True
                if self.inside_group:
                    return self.exit_group()
                if self.focused_message_index is not None:
                    self.clear_focus()
                    return True
                return False

            if event == '\x1b[A':  # Up arrow
                return self.move_focus_up()
            elif event == '\x1b[B':  # Down arrow
                return self.move_focus_down()
            elif event == '\x1b[C':  # Right arrow: into a split answer
                return self.enter_group()
            elif event == '\x1b[D':  # Left arrow: back to the whole answer
                return self.exit_group()
            
            # Handle action keys when a message is focused
            if self.focused_message_index is not None:
                # Guard against a stale index (e.g. messages cleared or
                # truncated while focus was held) so we never index out of
                # range.
                if not (0 <= self.focused_message_index < len(self.messages)):
                    self.focused_message_index = None
                else:
                    focused_msg = self.messages[self.focused_message_index]

                    action = next(
                        (candidate for candidate in focused_msg.get_active_actions() if candidate.key == event),
                        None,
                    )
                    if action is not None:
                        self._dispatch_action(focused_msg, action)
                        return True
        
        # Handle mouse input
        if isinstance(event, MouseEvent):
            # Check if mouse is over this panel
            if self.x <= event.x < self.x + self.width and \
               self.y <= event.y < self.y + self.height:
                
                # --- Left button: click, drag, release ---
                if event.button == 0:

                    # Mouse press (not drag): start click
                    if event.pressed and not event.drag:
                        # Use cached line map (rebuilt only when needed)
                        msg_index, local_y = self._cached_hit_test(event.y)

                        if msg_index is not None:
                            msg = self.messages[msg_index]
                            box = msg.get_component()

                            # Focus the message and start text selection. On a
                            # split answer the first click selects it whole; a
                            # click on the already-selected answer selects the
                            # segment under the cursor.
                            start, end = self._group_range(msg_index)
                            focused = self.focused_message_index
                            already = focused is not None and start <= focused < end
                            self.set_focused_message(msg_index, inside=already)

                            content_y = local_y - 1  # subtract top border
                            if 0 <= content_y < box.height - 2:
                                display_col = self.selection.screen_to_display_col(msg, box, content_y, event.x)
                                if display_col is not None:
                                    self.selection.start(msg_index, content_y, display_col)
                                else:
                                    self.selection.dragging = False
                            else:
                                self.selection.dragging = False
                        else:
                            # Clicked on a gap - clear focus and selection
                            self.clear_focus()
                            self.selection.clear()
                        self._request_repaint()
                        return True

                    # Mouse drag: extend selection (throttled to ~30ms between repaints)
                    if event.pressed and event.drag and self.selection.dragging:
                        msg_index, local_y = self._cached_hit_test(event.y)
                        if msg_index is not None and self.selection.has_selection and \
                           self.selection.state.msg is self.messages[msg_index]:
                            msg = self.messages[msg_index]
                            box = msg.get_component()
                            content_y = local_y - 1
                            if 0 <= content_y < box.height - 2:
                                display_col = self.selection.screen_to_display_col(msg, box, content_y, event.x)
                                if display_col is not None:
                                    self.selection.extend(msg_index, content_y, display_col)
                        return True

                    # Left button release: finalize selection
                    if not event.pressed and not event.drag:
                        was_dragging = self.selection.dragging
                        self.selection.end()

                        # If we actually dragged a selection (not just a click),
                        # auto-copy the selection to clipboard.
                        if was_dragging and self.selection.has_selection:
                            self._auto_copy_selection()

                        # If we didn't drag (just clicked), clear selection
                        if self.selection.has_selection and not was_dragging:
                            self.selection.clear()
                        return True

                # Handle released drag outside the panel (stop dragging)
                if event.button == 0 and not event.pressed and self.selection.dragging:
                    self.selection.end()
                    if self.selection.has_selection:
                        self._auto_copy_selection()
                    return True
                
                # Button 64 is scroll up, 65 is scroll down
                if event.button == 64: # Scroll Up
                    _, _, total_height = self._row_index()
                    max_scroll = max(0, total_height - self.height)
                    
                    # Get current start_y
                    if self.auto_scroll or self.anchored_start_y is None:
                        current_start_y = max_scroll
                    else:
                        current_start_y = self.anchored_start_y
                    
                    # Scroll up by the coalesced delta (configurable lines per notch)
                    step = settings.config.ui_scroll_lines_per_notch * event.scroll_delta
                    new_start_y = max(0, current_start_y - step)
                    self.anchored_start_y = new_start_y
                    self.scroll_offset = max_scroll - new_start_y
                    self.auto_scroll = False # Scrolling up disables auto-scroll
                    self._request_repaint()
                    return True
                    
                elif event.button == 65: # Scroll Down
                    _, _, total_height = self._row_index()
                    max_scroll = max(0, total_height - self.height)
                    
                    # Get current start_y
                    if self.anchored_start_y is None:
                        current_start_y = max_scroll
                    else:
                        current_start_y = self.anchored_start_y
                    
                    # Scroll down by the coalesced delta (configurable lines per notch)
                    step = settings.config.ui_scroll_lines_per_notch * event.scroll_delta
                    new_start_y = min(max_scroll, current_start_y + step)
                    self.anchored_start_y = new_start_y
                    self.scroll_offset = max_scroll - new_start_y
                    
                    # If we reached the bottom, enable auto-scroll
                    if self.scroll_offset == 0:
                        self.auto_scroll = True
                        self.anchored_start_y = None
                    self._request_repaint()
                    return True
                    
        return False


    @staticmethod
    def _should_render_markdown(msg_type: MsgType) -> bool:
        """Return True if this message type should render markdown."""
        from moka_chat.ui.tui.msg_types import AssistantMsg, ThinkingMsg
        # ThinkingMsg intentionally excluded: thinking content is plain text,
        # markdown would misinterpret it and break trailing-newline stripping.
        return isinstance(msg_type, AssistantMsg) and not isinstance(msg_type, ThinkingMsg)

    def new_message(self, message: str, *, msg_type: MsgType = None, title: str = None, frame_color: RGB = None, content_color: RGB = None, left_margin: int = 0, right_margin: int = 0, harness_message_ids: list = None, append=False) -> Message:
        """Create a new message. If append is True, add it to the chat history. Return the Message object for further manipulation.
        
        Args:
            message: The text to add
            msg_type: The type of message
            title: Optional override for the message box title
            frame_color: Optional override for the box frame color
            content_color: Optional override for the box content color
            left_margin: Optional override for the box left margin
            right_margin: Optional override for the box right margin
            harness_message_ids: List of harness message IDs this UI message references
            
        Returns:
            The created Message object.
        """
        
        # Don't change auto_scroll state here - let it be controlled externally
        # This prevents unwanted scrolling when user has scrolled up
            
        # Create a new message
        # Content width = panel width - gutter (1) - padding. The Box owns the
        # gutter and padding, so ``max_width`` here is the wrap width.
        initial_max_width = Box.thread_content_width(
            self.max_width, self.left_pad, self.right_pad)
        if initial_max_width < 1:
            initial_max_width = 1

        new_message = Message(
            message,
            msg_type=msg_type,
            max_width=initial_max_width,
            left_pad=self.left_pad,
            right_pad=self.right_pad,
            title=title,
            frame_color=frame_color,
            content_color=content_color,
            left_margin=left_margin,
            right_margin=right_margin,
            harness_message_ids=harness_message_ids,
            render_markdown=self._should_render_markdown(msg_type),
        )
        
        if append:
            # Messages the user queued during a generation stay last: the
            # running generation's lines go above them until they are picked
            # up, so a queued question never lands mid-answer.
            index = len(self.messages)
            while index > 0 and getattr(self.messages[index - 1], "is_queued", False):
                index -= 1
            self.messages.insert(index, new_message)
            if self.focused_message_index is not None and self.focused_message_index >= index \
                    and index < len(self.messages) - 1:
                self.focused_message_index += 1
            new_message.get_component().parent = self
            self._message_height_cache.clear()
        
        # Keep only last max_messages
        if len(self.messages) > self.max_messages:
            dropped = len(self.messages) - self.max_messages
            self.messages = self.messages[-self.max_messages:]
            self._message_height_cache.clear()
            # Adjust the focused-message index so it stays valid after the
            # oldest messages are dropped from the front.
            if self.focused_message_index is not None:
                self.focused_message_index = max(0, self.focused_message_index - dropped)
                if self.focused_message_index >= len(self.messages):
                    self.focused_message_index = None

        self._request_repaint()
        
        return new_message
    
    def remove_last_message(self):
        """Remove the last message from the chat history."""
        if self.messages:
            self.messages.pop()
            # Keep focus valid: drop it if the focused message was removed.
            if self.focused_message_index is not None:
                if not (0 <= self.focused_message_index < len(self.messages)):
                    self.focused_message_index = None
                else:
                    self.messages[self.focused_message_index].set_focused(True)
            self._notify_selection_changed()
            self._message_height_cache.clear()
            self._request_repaint()

    
    def remove_message_by_index(self, index: int):
        """Remove a message by its index.
        
        Args:
            index: The index of the message to remove
        """
        if 0 <= index < len(self.messages):
            # Adjust focused message index if needed
            if self.focused_message_index is not None:
                if self.focused_message_index == index:
                    # Deleting the focused message - clear focus or move to adjacent
                    if len(self.messages) > 1:
                        # Move focus to the next message, or previous if at end
                        if index < len(self.messages) - 1:
                            new_focus = index  # Will focus what becomes the new message at this index
                        else:
                            new_focus = index - 1  # Focus previous message
                        self.focused_message_index = new_focus
                    else:
                        # Only one message, clear focus after deletion
                        self.focused_message_index = None
                elif self.focused_message_index > index:
                    # Adjust focus index if it's after the deleted message
                    self.focused_message_index -= 1
            
            # Remove the message
            self.messages.pop(index)
            self._message_height_cache.clear()
            
            # Update focus state after deletion
            if (self.focused_message_index is not None
                    and 0 <= self.focused_message_index < len(self.messages)):
                self.messages[self.focused_message_index].set_focused(True)
            self._notify_selection_changed()
            self._request_repaint()

    def replace_message(self, current: Message, new: Message):
        """Replace a message with a new message.
        
        Args:
            current: The message to replace
            new: The new message to put in its place
        """
        try:
            index = self.messages.index(current)
            
            # Check if the current message is focused
            was_focused = (self.focused_message_index == index)
            
            # Clear focus from the old message if it was focused
            if was_focused:
                current.set_focused(False)
            
            # Replace the message
            self.messages[index] = new
            new.get_component().parent = self
            self._message_height_cache.clear()
            
            # Transfer focus to the new message if the old one was focused
            if was_focused:
                new.set_focused(True)
                # focused_message_index stays the same (same index, different message)
            self._request_repaint()
            
        except ValueError:
            # Message not found, ignore
            pass

    def retype_tool_message(self, msg: Message, msg_type: MsgType) -> Message:
        """Swap a tool line's type in place (e.g. permission ask → tool call).

        The type fixes the gutter color and actions at construction, so the
        line is rebuilt as a new message carrying the tool state over.
        """
        if isinstance(msg.type, type(msg_type)):
            return msg
        new = self.new_message("", msg_type=msg_type,
                               harness_message_ids=msg.harness_message_ids)
        for attr in ("tool_name", "tool_args", "tool_output", "tool_status",
                     "show_output", "live_output", "_run_started_at"):
            setattr(new, attr, getattr(msg, attr))
        self.replace_message(msg, new)
        new.rebuild_tool_display()
        return new

    def remove_message(self, msg: Message) -> None:
        """Remove ``msg`` if it is still in the transcript."""
        try:
            self.remove_message_by_index(self.messages.index(msg))
        except ValueError:
            pass

    def add_message(self, message: str, msg_type: MsgType = None, title: str = None, frame_color: RGB = None, content_color: RGB = None, left_margin: int = 0, right_margin: int = 0, harness_message_ids: list = None) -> Message:
        """Add a message to chat history and update UI.
        
        Args:
            message: The text to add
            msg_type: The type of message
            title: Optional override for the message box title
            frame_color: Optional override for the box frame color
            left_margin: Optional override for the box left margin
            right_margin: Optional override for the box right margin
            harness_message_ids: List of harness message IDs this UI message references
        
        Returns:
            The created Message object. When routed to the activity sink, the
            returned message is detached (not part of the transcript).
        """
        from moka_chat.ui.tui import msg_types

        if self.activity_sink is not None and isinstance(msg_type, msg_types.SysMsg):
            if isinstance(msg_type, msg_types.SysMsgError):
                level = "error"
            elif isinstance(msg_type, msg_types.SysMsgWarning):
                level = "warning"
            else:
                level = "info"
            self.activity_sink(message, level)
            return self.new_message(message, msg_type=msg_type, title=title,
                                    frame_color=frame_color, content_color=content_color,
                                    left_margin=left_margin, right_margin=right_margin,
                                    harness_message_ids=harness_message_ids, append=False)

        return self.new_message(message, msg_type=msg_type, title=title, frame_color=frame_color, content_color=content_color, left_margin=left_margin, right_margin=right_margin, harness_message_ids=harness_message_ids, append=True)


    def clear(self):
        """Clear the chat history UI."""
        self.messages = []
        self._message_height_cache.clear()
        self.scroll_offset = 0
        self.auto_scroll = True
        self.anchored_start_y = None
        self.focused_message_index = None
        self.selection.clear()
        self._request_repaint()

    def refresh_theme(self) -> None:
        """Re-resolve every message's colors after a theme switch."""
        for message in self.messages:
            refresh = getattr(message, "refresh_theme", None)
            if callable(refresh):
                refresh()
        self._request_repaint()

    def restore_messages(self, messages: list) -> None:
        """Restore message objects and rebuild the panel's layout state."""
        self.messages = list(messages)
        for message in self.messages:
            message.get_component().parent = self
        self.focused_message_index = None
        self.selection.clear()
        self._message_height_cache.clear()
        self.scroll_offset = 0
        self.auto_scroll = True
        self.anchored_start_y = None
        self._request_repaint()
        
    # def resize(self, new_width: int):
    #     """Resize the panel and reformat all messages.
        
    #     Args:
    #         new_width: The new maximum width for message wrapping
    #     """
    #     self.max_width = new_width
    #     inner_width = new_width - 2
        
    #     # Reformat all messages with the new width
    #     for message in self.messages:
    #         message.reformat(inner_width)
        
        # No implicit render call here

