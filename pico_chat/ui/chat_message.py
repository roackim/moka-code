"""Chat message representation with formatting and action support."""

import difflib
import json
import re
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


# Salvages complete ``"key": "value"`` string fields from a partial JSON args
# string, so a draft line can show e.g. the path before the call finishes.
_PARTIAL_STR_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"((?:[^"\\]|\\.)*)"')

# Cap on how much of a (possibly still-streaming) args string is scanned for
# leading fields. A large streamed ``content`` value must not make every draft
# render re-scan the whole buffer.
_PARTIAL_PARSE_LIMIT = 4096


def _parse_tool_args(raw: Optional[str], *, full: bool = True) -> dict:
    """Best-effort parse of a (possibly partial) JSON tool-args string.

    When ``full`` is False (a still-streaming draft) only the leading fields are
    salvaged, bounded by ``_PARTIAL_PARSE_LIMIT``. This avoids a full
    ``json.loads`` of a partial buffer: a ``"}"`` inside streamed content would
    otherwise trigger an O(n) scan on every delta (O(n²) over the stream).
    """
    if not raw:
        return {}
    if full:
        stripped = raw.rstrip()
        if stripped.startswith("{") and stripped.endswith('"}'):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict):
                    return data
            except (ValueError, TypeError):
                pass
    salvaged: dict = {}
    for key, value in _PARTIAL_STR_RE.findall(raw[:_PARTIAL_PARSE_LIMIT]):
        try:
            salvaged[key] = json.loads(f'"{value}"')
        except ValueError:
            salvaged[key] = value
    return salvaged


def _count_lines(text: str) -> int:
    return len(text.splitlines())


def _edit_counts(search: str, replace: str) -> tuple[int, int]:
    """Return ``(added, removed)`` line counts for a search→replace edit."""
    old = search.splitlines()
    new = replace.splitlines()
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, old, new)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def _short_value(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tool_summary(name: str, args: dict, output: Optional[str]) -> str:
    """Compact, human summary of a tool call for its collapsed line.

    ``output`` is the completed tool output when available, so ``read`` can
    report the number of lines actually returned.
    """
    if name == "read":
        path = args.get("path")
        if not path:
            return ""
        if output is not None:
            return f"{path} {_count_lines(output)} lines"
        return str(path)
    if name == "write":
        path = args.get("path")
        content = args.get("content")
        if path and isinstance(content, str):
            return f"{path} +{_count_lines(content)}"
        return str(path or "")
    if name == "edit":
        path = args.get("path")
        search = args.get("search")
        replace = args.get("replace")
        if path and isinstance(search, str) and isinstance(replace, str):
            added, removed = _edit_counts(search, replace)
            return f"{path} +{added} -{removed}"
        return str(path or "")
    if name == "bash":
        command = args.get("command")
        if isinstance(command, str) and command:
            return command.splitlines()[0]
        return ""
    if len(args) == 1:
        return _short_value(next(iter(args.values())))
    return ""


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
            compact_when_unfocused=(isinstance(msg_type, (msg_types.ToolCallMsg, msg_types.ToolDraftMsg, msg_types.AskPermissionMsg))),  # Tool calls and permission requests use compact headers
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
        """Rebuild the tool message from its metadata.

        The first line is always ``<glyph> <name>  <summary>``: the lifecycle
        glyph (spinner while drafting/running, then ✓/✗/⏹) leads the content,
        the name is colored by message type, and a compact per-tool summary
        carries the path/command and change counts. Expanded (focused) messages
        additionally show the raw command and, when toggled, the output.
        """
        if not self.tool_name:
            return

        if isinstance(self.type, msg_types.AskPermissionMsg):
            name_color = theme.PERMISSION
            glyph, glyph_color = "?", theme.PERMISSION
        else:
            name_color = (
                theme.MUTED if isinstance(self.type, msg_types.ToolDraftMsg)
                else theme.TOOL
            )
            glyph, glyph_color = self.status_glyph()

        # Only report an actual read length for a completed call; error/denial
        # output is not file content.
        completed_output = (
            self.tool_output if self.tool_terminal_state() == "completed" else None
        )

        # Parse/summarize at most once per distinct args/output: the spinner
        # rebuilds this message every tick, and re-parsing a huge completed
        # ``write`` body (or a partial stream) on every tick is O(n²).
        cache_key = (
            id(self.tool_args),
            self.tool_name,
            id(completed_output),
            isinstance(self.type, msg_types.ToolDraftMsg),
        )
        if getattr(self, "_tool_display_cache_key", None) != cache_key:
            # A draft's args are partial: never attempt a full parse, or a
            # ``"}"`` inside the streamed content would trigger a full-buffer
            # scan on every delta.
            args = _parse_tool_args(
                self.tool_args,
                full=not isinstance(self.type, msg_types.ToolDraftMsg),
            )
            summary = _tool_summary(self.tool_name, args, completed_output)
            if (isinstance(self.type, msg_types.ToolDraftMsg)
                    and self.tool_name == "write" and args.get("path")):
                # The content is still streaming (never salvaged): count its
                # escaped newlines so a long write visibly progresses.
                streamed_lines = self.tool_args.count(r"\n")
                summary = f"{args['path']} +{streamed_lines}"
            cmd_text = self._tool_cmd_text(args)
            self._tool_display_cache_key = cache_key
            self._tool_display_args = args
            self._tool_display_summary = summary
            self._tool_display_cmd = cmd_text
        else:
            summary = self._tool_display_summary
            cmd_text = self._tool_display_cmd

        is_compact = self.box.compact_when_unfocused and not self.box.focused

        header = f"{glyph_color}{glyph}{theme.reset()} {name_color}{self.tool_name}{theme.reset()}"
        if summary:
            header += f" {theme.MUTED}{summary}{theme.reset()}"
        if not is_compact and self.tool_status:
            header += f" {self._colored_status()}"

        lines = [header]

        if self.tool_args and not is_compact and cmd_text:
            lines.append(f"{theme.MUTED}cmd:{theme.reset()} {cmd_text}")

        # While a draft is still streaming, show its raw tail when focused so
        # the call is observable (and clearly progressing) rather than a bare
        # spinner.
        if not is_compact and isinstance(self.type, msg_types.ToolDraftMsg) and self.tool_args:
            tail = self.tool_args[-400:]
            ellipsis = "…" if len(self.tool_args) > 400 else ""
            lines.append(f"{theme.MUTED}draft:{theme.reset()} {ellipsis}{tail}")

        if self.show_output and self.tool_output and not is_compact:
            for i, line in enumerate(self.tool_output.split('\n')):
                if i == 0:
                    lines.append(f"{theme.MUTED}out:{theme.reset()} {line}")
                else:
                    lines.append(f"     {line}")  # Indent continuation lines

        self.base_text = '\n'.join(lines)
        self._reveal_len = len(self.base_text)
        self.reformat(self.max_width)

    def _tool_cmd_text(self, args: dict) -> str:
        """The single ``cmd:`` line shown when a tool message is expanded."""
        if not self.tool_args:
            return ""
        if args:
            if len(args) == 1:
                return _short_value(next(iter(args.values())))
            return _short_value(args, limit=200)
        return self.tool_args[:_PARTIAL_PARSE_LIMIT]

    def _colored_status(self) -> str:
        """Render ``tool_status`` parts with success/error/muted colors."""
        colored = []
        for part in self.tool_status.split(' | '):
            part = part.strip()
            if part in ('approved', 'completed'):
                colored.append(f"{theme.SUCCESS}{part}{theme.reset()}")
            elif part in ('denied', 'error'):
                colored.append(f"{theme.ERROR}{part}{theme.reset()}")
            else:
                colored.append(f"{theme.MUTED}{part}{theme.reset()}")
        return ' | '.join(colored)

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
