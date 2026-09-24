"""Chat message representation with formatting and action support."""

import time
from typing import Any, Optional
from pico_chat import pico_cfg
from pico_chat.ui.tui.colors import theme, RGB
from pico_chat.ui.tui.components import TextComponent
from pico_chat.ui.tui.components.markdown import MarkdownComponent
from pico_chat.ui.tui.components.message_view import MessageView
from pico_chat.ui.tui.layout_utils import wrap_text
from pico_chat.ui.tui.msg_types import MsgType, MsgAction
from pico_chat.ui.tui import msg_types
from pico_chat.ui.tui.components.box import SPINNER_FRAMES


class Message:
    """Represents a message in the chat history with formatting support."""
    
    def __init__(self,
                 text: str,
                 msg_type: MsgType = None,
                 max_width: int = 80,
                 left_pad: int = pico_cfg.config.ui_msg_h_padding,
                 right_pad: int = pico_cfg.config.ui_msg_h_padding,
                 title: str = None,
                 frame_color: RGB = None,
                 content_color: RGB = None,
                 left_margin: int = 0,
                 right_margin: int = 0,
                 harness_message_ids: list = None,
                 render_markdown: bool = False,
):
        """Initialize a message.

        Args:
            text: The raw message text
            msg_type: The type of message (determines default formatting)
            max_width: Maximum width for line wrapping
            left_pad: Number of spaces to pad the left side
            right_pad: Number of spaces to pad the right side
            title: Optional override for the message box title
            frame_color: Optional override for the box frame color
            content_color: Optional override for the content text color
            left_margin: Number of spaces to pad the left side of the box
            right_margin: Number of spaces to pad the right side of the box
            harness_message_ids: List of harness message IDs this UI message references
            render_markdown: If True, use MarkdownComponent instead of TextComponent
        """
        
        self.type = msg_type or MsgType()
        
        # Track which harness messages this UI message represents
        self.harness_message_ids = harness_message_ids or []
        
        # Resolve defaults from msg_type if not provided
        if title is None:
            title = self.type.title
        
        if frame_color is None:
            color_name = self.type.frame_color
            frame_color = getattr(theme, color_name, theme.DEFAULT)
            
        if content_color is None and self.type.content_color:
            color_name = self.type.content_color
            content_color = getattr(theme, color_name, None)
        
        self.base_text = text
        # Number of characters of ``base_text`` currently rendered. While
        # streaming, ``base_text`` is the full arrived text and this prefix is
        # what the component shows; non-streamed messages keep them in sync.
        self._reveal_len = len(text)
        self.max_width = max_width
        self.layout_revision = 0
        self.left_pad = left_pad
        self.right_pad = right_pad
        self.title = title
        self.frame_color = frame_color
        self.left_margin = left_margin
        self.right_margin = right_margin
        self.render_markdown = render_markdown

        if render_markdown:
            self.formatted_text = ""  # Not used for markdown messages
            # Padding is owned by the Box (thread mode), not the content.
            self.component = MarkdownComponent(text, fg=content_color, left_pad=0)
        else:
            self.formatted_text = self._format_line_wrap()
            self.component = TextComponent(self.formatted_text, fg=content_color)

        self.finalized = False  # Whether this message is finalized
        # Messages do not render actions inline; the app surfaces the selected
        # message's actions in the bottom mode line instead.
        self.inline_actions = False
        
        # Collapsed state: when True, render a single summary line instead of
        # the full content (used for thinking messages that fold by default).
        self.collapsed = False        # Whether this message type folds by default and expands on focus.
        self.collapsible = isinstance(msg_type, msg_types.ThinkingMsg)        # Animated spinner frame index (advances on TickEvent while streaming).
        self.spinner_frame = 0
        # Live wait-phase label for a collapsible status message (e.g.
        # "processing", "thinking"); rendered collapsed while in flight.
        self.status_phase: Optional[str] = None
        self._phase_started_at: Optional[float] = None
        self.phase_seconds: Optional[float] = None
        
        # Tool-specific metadata
        self.tool_name: Optional[str] = None
        self.tool_args: Optional[str] = None
        self.tool_output: Optional[str] = None
        self.tool_status: Optional[str] = None  # "approved", "denied", "completed", etc.
        self.show_output: bool = False  # Toggle for output visibility (press 'o' to show out: line)

        # Steering / queue state
        self.is_queued: bool = False   # UserMsg waiting while generation is active
        self.is_paused: bool = False   # PicoMsg/ThinkingMsg cancelled via pause action
        
        # Generation metrics
        self.metrics_tokens: int = 0
        self.metrics_tokens_per_second: float = 0.0
        self.metrics_ttft_ms: Optional[float] = None
        self.metrics_duration_ms: Optional[float] = None
        
        # Action click flash feedback (set by ChatHistoryPanel, read by Box)
        self._flash_action_key: Optional[str] = None
        
        # Thread mode: borderless chat-thread rendering with a role gutter.
        # The gutter symbol and color come from the message type.
        thread_gutter = getattr(self.type, "gutter", "▸")
        thread_gutter_color = getattr(self.type, "gutter_color", None)
        if thread_gutter_color is None:
            thread_gutter_color = frame_color
        elif isinstance(thread_gutter_color, str):
            thread_gutter_color = getattr(theme, thread_gutter_color, frame_color)

        self.box = MessageView(
            self.component,
            parent_msg=self,
            compact_when_unfocused=(isinstance(msg_type, (msg_types.ToolCallMsg, msg_types.AskPermissionMsg))),  # Tool calls and permission requests use compact headers
            gutter=thread_gutter,
            gutter_color=thread_gutter_color,
            content_pad_left=left_pad,
            content_pad_right=right_pad,
            # User/pico/tool-call use a `▌` prefix bar that spans every row.
            gutter_full_height=isinstance(
                msg_type,
                (msg_types.UserMsg, msg_types.PicoMsg,
                 msg_types.ToolCallMsg, msg_types.ToolDraftMsg,
                 msg_types.AskPermissionMsg),
            ),
        )
    
    def finalize(self):
        # A finalized message is complete: drain any un-revealed arrived text so
        # the full content is rendered before the inline styling is applied.
        if self._reveal_len < len(self.base_text):
            self.reveal_to(len(self.base_text))
        self.finalized = True
        self.complete_phase()
        # A finalized message is complete: re-render the previously-open last
        # line with its inline styling (streaming renders it plain).
        if self.render_markdown and hasattr(self.component, "set_streaming"):
            self.component.set_streaming(False)
        # Tool messages bake their status symbol into the display text; rebuild
        # so the finalized ✓/✗/⏹ replaces any stale running-spinner glyph.
        if self.is_tool_message():
            self.rebuild_tool_display()
        self.box.mark_changed()  # Finalization affects actions display
    
    def get_active_actions(self):
        """Get the list of active actions based on message state.

        The exposed set is deliberately small (copy/output/permission); actions
        that alter or remove conversation state live behind commands.
        """
        return list(self.type.actions)

    def inline_action_items(self):
        """Actions the Box should render inline, or [] when opted out.

        Messages surface their actions through the app's bottom mode line
        instead of inline, so this is normally empty.
        """
        if not self.inline_actions:
            return []
        return self.get_active_actions()
    
    def update_actions(self):
        """Update the box's actions list based on current state.
        
        Note: This is now a no-op since Box pulls actions dynamically from parent_msg.
        Kept for backward compatibility.
        """
        pass
    
    def set_title(self, title: str):
        """Update the title of the message box."""
        self.title = title
        self.box.title = title
        self.box.mark_changed()  # Title changed

    def set_frame_color(self, color: RGB):
        """Update the frame color of the message box."""
        self.frame_color = color
        self.box.fg = color
        self.box.mark_changed()  # Color changed

    def refresh_theme(self) -> None:
        """Re-resolve this message's colors from the active theme.

        Called on a theme switch so already-rendered messages repaint with the
        new palette (colors are resolved at construction, not render time).
        """
        if self.is_queued:
            frame_color = theme.MUTED
            content_color = theme.MUTED
        else:
            frame_color = getattr(theme, self.type.frame_color, theme.DEFAULT)
            content_color = None
            if self.type.content_color:
                content_color = getattr(theme, self.type.content_color, None)

        self.frame_color = frame_color
        if hasattr(self.component, "fg"):
            self.component.fg = content_color
        if hasattr(self.component, "bg"):
            self.component.bg = theme.get_bg()

        gutter_color = getattr(self.type, "gutter_color", None)
        if gutter_color is None:
            gutter_color = frame_color
        elif isinstance(gutter_color, str):
            gutter_color = getattr(theme, gutter_color, frame_color)

        self.box.fg = frame_color
        self.box.bg = theme.get_bg()
        self.box.gutter_color = gutter_color
        self.box.mark_changed()


    def _is_markdown(self) -> bool:
        """Check if this message uses markdown rendering."""
        return self.render_markdown
    
    def set_focused(self, focused: bool):
        """Set the focused state of this message."""
        # Focus can change the preferred height of compact messages (for
        # example tool/permission messages expand from one line to a box with
        # borders).  Make the history panel discard its height-cache entry so
        # the newly focused single-line message receives a real layout.
        if self.box.focused != focused:
            self.layout_revision += 1
        self.box.set_focused(focused)

    def set_collapsed(self, collapsed: bool):
        """Fold/unfold this message to a single summary line.

        Used for thinking messages: collapsed by default, expanded on focus.
        """
        if self.collapsed != collapsed:
            self.collapsed = collapsed
            self.layout_revision += 1
            self.box.mark_changed()

    def begin_phase(self, phase: str):
        """Label this message with the current wait phase (``processing``/``thinking``).

        The first call starts the phase clock; subsequent calls only relabel, so
        a single message can move processing → thinking without resetting timing.
        """
        self.status_phase = phase
        if self._phase_started_at is None:
            self._phase_started_at = time.perf_counter()
        self.box.mark_changed()

    def complete_phase(self):
        """Stop the phase clock, freezing the elapsed duration for the summary."""
        if self._phase_started_at is not None:
            self.phase_seconds = time.perf_counter() - self._phase_started_at
        self.box.mark_changed()

    def advance_spinner(self):
        """Advance the animated spinner frame (called on TickEvent)."""
        self.spinner_frame = (self.spinner_frame + 1) % len(SPINNER_FRAMES)
        # Live tool messages bake the current spinner glyph into their display
        # text, so rebuild so the braille frame actually animates.
        if self.is_tool_message():
            self.rebuild_tool_display()
        else:
            self.box.mark_changed()

    def is_tool_message(self) -> bool:
        """True for tool-call, tool-draft, and permission-request messages."""
        return isinstance(self.type, (
            msg_types.ToolCallMsg, msg_types.ToolDraftMsg, msg_types.AskPermissionMsg,
        ))

    def spinner_glyph(self) -> str:
        """Current braille spinner frame (for live/in-progress messages)."""
        return SPINNER_FRAMES[self.spinner_frame % len(SPINNER_FRAMES)]

    def tool_terminal_state(self) -> Optional[str]:
        """Resolve the *terminal* tool state, independent of status strings.

        Returns one of ``"completed"``, ``"error"``, ``"denied"``,
        ``"cancelled"``, or ``None`` while still running (or when there is no
        terminal state to report).
        """
        status = self.tool_status or ""
        for terminal, needles in (
            ("completed", ("completed",)),
            ("error", ("error",)),
            ("denied", ("denied",)),
            ("cancelled", ("cancelled",)),
        ):
            if any(n in status for n in needles):
                return terminal
        return None

    def status_glyph(self) -> tuple[str, Any]:
        """Return a single (glyph, color) describing the message lifecycle.

        Lifecycle rule:
        - not finalized            → braille spinner (loading)
        - finalized + terminal state → ✓ / ✗ / ⏹ per terminal state
        - finalized, no terminal   → muted ⋯ fallback
        """
        from pico_chat.ui.tui.colors import theme

        if not self.finalized:
            return self.spinner_glyph(), theme.MUTED

        terminal = self.tool_terminal_state()
        if terminal == "completed":
            return "✓", theme.SUCCESS
        if terminal in ("error", "denied"):
            return "✗", theme.ERROR
        if terminal == "cancelled":
            return "⏹", theme.WARNING
        return "⋯", theme.MUTED

    def _collapsed_text(self) -> str:
        """Return the single-line summary shown when collapsed."""
        if isinstance(self.type, msg_types.ThinkingMsg):
            return self.status_phase or "thinking"
        return self.type.title or self.type.name

    def done_glyph(self) -> tuple[str, Any]:
        """Return a (glyph, color) shown on a collapsed message once done.

        Thinking messages show a muted "✓" once finalized; tool/permission
        messages reuse their terminal status glyph.
        """
        from pico_chat.ui.tui.colors import theme

        if isinstance(self.type, msg_types.ThinkingMsg):
            return "✓", theme.MUTED
        if self.is_tool_message():
            return self.status_glyph()
        return "✓", theme.MUTED

    def done_label(self, collapsed_text: str) -> str:
        """Label shown beside the done marker for a finalized collapsed message."""
        if isinstance(self.type, msg_types.ThinkingMsg):
            if self.phase_seconds is None:
                return "thoughts"
            seconds = self.phase_seconds
            shown = f"{seconds:.1f}" if seconds < 10 else f"{seconds:.0f}"
            return f"thought for {shown}s"
        return collapsed_text

    def render_collapsed_line(self, subbuffer, max_width: int, fg, bg) -> None:
        """Render the single-line summary for a collapsed message.

        Shows an animated spinner while the message is not finalized, then a
        static marker once finalized. Owned by the message (not the Box) so the
        Box stays free of lifecycle decoration.
        """
        text = self._collapsed_text()
        if not self.finalized:
            frame = SPINNER_FRAMES[self.spinner_frame % len(SPINNER_FRAMES)]
            line = f"{frame} {text}"
        elif isinstance(self.type, msg_types.ThinkingMsg):
            # Muted summary only: no done glyph, the message prefix carries the
            # identity (kept in the caller's muted fg).
            line = self.done_label(text)
        else:
            done_glyph, done_color = self.done_glyph()
            line = f"{done_color}{done_glyph}{theme.reset()} {self.done_label(text)}"

        subbuffer.write_str(2, 0, line, fg=fg, bg=bg, max_width=max(0, max_width))
    
    def _format_line_wrap(self, text: Optional[str] = None) -> str:
        """Format the message text with smart word wrapping and padding.

        Uses the provided left and right padding for each line.
        Normalises the text first: strips trailing whitespace on every line
        and collapses runs of blank lines into a single blank line so that
        streaming artefacts (extra \\n from chunk boundaries) don't bloat
        the display.  Existing intentional newlines are preserved.
        """
        if text is None:
            text = self.base_text
        if self.max_width is None or self.max_width <= 0:
            return text

        # ``max_width`` is already the content width (the panel subtracts the
        # gutter and padding); the Box applies the padding at render time.
        content_width = self.max_width
        if content_width < 1:
            content_width = 1
        
        # Convert literal \n escape sequences to real newlines (thinking messages)
        base_text = text.replace('\\n', '\n')
        
        # Strip trailing newlines from the whole block
        base_text = base_text.rstrip('\n')
        
        # Split into raw lines
        raw_lines = base_text.split('\n')
        
        # Normalise: strip trailing whitespace from each line, collapse
        # consecutive blank lines into one, and remove trailing blank lines.
        normalised: list[str] = []
        for line in raw_lines:
            stripped = line.rstrip()
            # Collapse multiple consecutive blank lines into a single one
            if not stripped and normalised and normalised[-1] == "":
                continue
            normalised.append(stripped)
        
        # Strip trailing blank lines
        while normalised and normalised[-1] == "":
            normalised.pop()
        
        # Wrap each normalised line and apply padding
        lines = []
        for line in normalised:
            if not line:
                lines.append("")
                continue
            
            wrapped = wrap_text(line, content_width, padding_width=0, first_line_padding=False)
            for w_line in wrapped.split('\n'):
                lines.append(w_line)
        
        return "\n".join(lines)
    
    def reformat(self, max_width: int, append: bool = False) -> str:
        """Reformat the message with a new maximum width.

        Args:
            max_width: New maximum width for line wrapping
            append: True when the rendered text was only extended (streaming);
                enables the incremental parse/render fast path.

        Returns:
            The newly formatted text (plain-text fallback for markdown)
        """
        self.max_width = max_width
        return self._render_revealed(append=append)

    def _render_revealed(self, append: bool = False) -> str:
        """Render ``base_text[:self._reveal_len]`` into the component."""
        self.layout_revision += 1
        text = self.base_text[:self._reveal_len]

        if self._is_markdown():
            # MarkdownComponent handles wrapping internally via set_layout / width
            self.component.update(text, append=append)
            self.box.mark_changed()
            return text
        else:
            self.formatted_text = self._format_line_wrap(text)
            self.component.update(self.formatted_text)
            self.box.mark_changed()
            return self.formatted_text

    def get_formatted(self) -> str:
        """Get the current formatted text."""
        return self.formatted_text

    def get_component(self):
        """Get the TUI component for this message."""
        return self.box


    def ingest(self, text: str):
        """Append arrived text without rendering it (streaming).

        Leading whitespace on the very first chunk is dropped (models often
        open with a space; user input is stripped on submit). The rendered
        prefix is advanced separately by ``reveal_to``.
        """
        if not self.base_text:
            text = text.lstrip()
        self.base_text += text

    def reveal_to(self, n: int):
        """Reveal up to ``n`` characters of the arrived text.

        Never reveals past ``base_text``. Uses the append-only fast path when
        the prefix grows.
        """
        n = max(0, min(n, len(self.base_text)))
        grew = n >= self._reveal_len
        self._reveal_len = n
        self._render_revealed(append=grew)

    def append(self, text: str):
        """Append text and reveal it all (non-streamed callers).

        Leading whitespace on the very first chunk is dropped (models often
        open with a space; user input is stripped on submit).
        """
        self.ingest(text)
        self.reveal_to(len(self.base_text))
    
    def rebuild_tool_display(self):
        """Rebuild tool message display text based on current metadata and show_output state."""
        if not self.tool_name:
            return
        
        from pico_chat.ui.tui.colors import theme
        
        # Build status line with colors - shown expanded only.
        status_parts = []
        if self.tool_status:
            # Split status by | and color each part
            parts = self.tool_status.split(' | ')
            colored_parts = []
            for part in parts:
                part = part.strip()
                if part in ['approved', 'completed']:
                    colored_parts.append(f"{theme.SUCCESS}{part}{theme.reset()}")
                elif part in ['denied', 'error']:
                    colored_parts.append(f"{theme.ERROR}{part}{theme.reset()}")
                else:
                    colored_parts.append(f"{theme.MUTED}{part}{theme.reset()}")
            status_parts = [' | '.join(colored_parts)]

        # The lifecycle gutter already carries the type/status mark (? for a
        # permission request, spinner/✓/✗ for a tool), so the header is just
        # the tool name — no second "?" / ">" prefix.
        from pico_chat.ui.tui import msg_types
        if isinstance(self.type, msg_types.AskPermissionMsg):
            header = f"{theme.PERMISSION}{self.tool_name}{theme.reset()}"
        else:
            header = f"{theme.TOOL}{self.tool_name}{theme.reset()}"
        
        # Add compact arg summary to header for better single-line view
        args_summary = ""
        if self.tool_args:
            import json
            try:
                args_dict = json.loads(self.tool_args)
                # Extract key info for compact display
                if self.tool_name == "edit" and isinstance(args_dict, dict):
                    path = args_dict.get("path")
                    if path:
                        args_summary = f" {theme.MUTED}{path}{theme.reset()}"
                elif len(args_dict) == 1:
                    # Single arg - show value (truncate if too long)
                    key, value = list(args_dict.items())[0]
                    if isinstance(value, str):
                        # Truncate long values
                        if len(value) > 60:
                            value = value[:57] + "..."
                        args_summary = f" {theme.MUTED}{value}{theme.reset()}"
                    else:
                        args_summary = f" {theme.MUTED}{json.dumps(value)[:60]}{theme.reset()}"
            except:
                pass
        
        # Check if we're in compact mode for status display
        # Compact when unfocused only
        is_compact = self.box.compact_when_unfocused and not self.box.focused
        
        header += args_summary
        # The gutter already carries the lifecycle glyph (spinner/✓/✗/?), so a
        # compact line has no status suffix; only the expanded line spells the
        # status out.
        if not is_compact and status_parts:
            header += f" {status_parts[0]}"
        
        # Build text
        lines = [header]
        
        # Add command/args if available - extract command value directly (skip in compact mode)
        if self.tool_args and not is_compact:
            # Try to parse as JSON to show nicely
            import json
            import re
            try:
                args_dict = json.loads(self.tool_args)
                
                if self.tool_name == "edit" and isinstance(args_dict, dict):
                    edit_lines = 0
                    path = args_dict.get("path")
                    replace_content = args_dict.get("replace")
                    if isinstance(replace_content, str) and replace_content:
                        edit_lines = len(replace_content.splitlines())
                    if path:
                        lines.append(f"{theme.MUTED}cmd:{theme.reset()} {path} ({edit_lines} lines)")
                    else:
                        lines.append(f"{theme.MUTED}cmd:{theme.reset()} {edit_lines} lines")
                elif len(args_dict) == 1:
                    # Single arg - show the value with cmd: prefix
                    key, value = list(args_dict.items())[0]
                    if isinstance(value, str):
                        lines.append(f"{theme.MUTED}cmd:{theme.reset()} {value}")
                    else:
                        lines.append(f"{theme.MUTED}cmd:{theme.reset()} {json.dumps(value)}")
                else:
                    lines.append(f"{theme.MUTED}cmd:{theme.reset()} {json.dumps(args_dict)}")
            except:
                # Not JSON or parse error - show raw
                lines.append(f"{theme.MUTED}cmd:{theme.reset()} {self.tool_args}")
        
        # Add output if show_output is True (toggle with 'o' key)
        if self.show_output and self.tool_output and not is_compact:
            # Split output into lines and format each one
            output_lines = self.tool_output.split('\n')
            for i, line in enumerate(output_lines):
                if i == 0:
                    lines.append(f"{theme.MUTED}out:{theme.reset()} {line}")
                else:
                    lines.append(f"     {line}")  # Indent continuation lines
        
        self.base_text = '\n'.join(lines)
        self._reveal_len = len(self.base_text)
        self.reformat(self.max_width)
    
    def update_metrics(self, tokens: int, tokens_per_second: float, ttft_ms: Optional[float] = None, duration_ms: Optional[float] = None):
        """Update generation metrics for this message."""
        self.metrics_tokens = tokens
        self.metrics_tokens_per_second = tokens_per_second
        if ttft_ms is not None:
            self.metrics_ttft_ms = ttft_ms
        if duration_ms is not None:
            self.metrics_duration_ms = duration_ms
        self.box.mark_changed()  # Metrics changed, affects bottom border
    
    def get_metrics_string(self) -> Optional[str]:
        """Get formatted metrics string based on config."""
        from pico_chat import pico_cfg
        
        if not pico_cfg.config.ui_show_metrics:
            return None
        
        # Only show metrics if we have data
        if self.metrics_tokens == 0 and self.metrics_tokens_per_second == 0:
            return None
        
        parts = []
        
        if pico_cfg.config.ui_metrics_show_tokens and self.metrics_tokens > 0:
            parts.append(f"{self.metrics_tokens} t")
        
        if pico_cfg.config.ui_metrics_show_speed and self.metrics_tokens_per_second > 0:
            parts.append(f"{self.metrics_tokens_per_second:.1f} t/s")
        
        if pico_cfg.config.ui_metrics_show_ttft and self.metrics_ttft_ms is not None:
            parts.append(f"ttft {self.metrics_ttft_ms:.0f}ms")
        
        return " │ ".join(parts) if parts else None
    
    def should_show_metrics(self) -> bool:
        """Check if metrics should be displayed for this message."""
        # Show if focused OR if generating (not finalized)
        return self.box.focused or not self.finalized
