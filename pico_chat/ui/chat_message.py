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
from pico_chat.ui.tui.layout_utils import display_width, strip_ansi, wrap_text
from pico_chat.ui.tui.msg_types import MsgType, MsgAction
from pico_chat.ui.tui import msg_types


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


# Focused view: lines of a write head / edit diff before "… N more lines".
_BODY_LINES = 20
# Toggled output (``o``): head and tail kept around the elided middle.
_OUTPUT_HEAD, _OUTPUT_TAIL = 20, 10
# Live output lines shown under a running command.
_LIVE_TAIL = 5
# A running tool shows its elapsed time only once it is not instant.
_RUNNING_AFTER = 1.0
# The pre-request phase is labelled "preparing" only when it is slow.
_PREPARING_AFTER = 0.5

_EXIT_RE = re.compile(r"^\[exit:(-?\d+)\]$")


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


def _lines_word(count: int) -> str:
    return "line" if count == 1 else "lines"


def _line_metric(*, count: Optional[int] = None, added: int = 0, removed: int = 0) -> str:
    """Colored line count; a sign means the file changed, a plain count not.

    ``count`` renders ``240 lines`` (read); ``added``/``removed`` render
    ``+2 lines −1 line`` with only the nonzero sides, each side fully colored
    and with its own unit.
    """
    if count is not None:
        return f"{theme.MUTED}{count} {_lines_word(count)}{theme.reset()}"
    parts = []
    if added:
        parts.append(f"{theme.SUCCESS}+{added} {_lines_word(added)}{theme.reset()}")
    if removed:
        parts.append(f"{theme.ERROR}−{removed} {_lines_word(removed)}{theme.reset()}")
    return " ".join(parts)


def _bash_exit_code(output: Optional[str]) -> Optional[int]:
    """Exit code from the worker's trailing ``[exit:N]`` line, if any."""
    if not output:
        return None
    lines = output.rstrip().splitlines()
    match = _EXIT_RE.match(lines[-1].strip()) if lines else None
    return int(match.group(1)) if match else None


def _tool_target_metric(name: str, args: dict, *, raw: Optional[str],
                        output: Optional[str], drafting: bool) -> tuple[str, str]:
    """``(target, metric)`` for a tool line header.

    ``output`` is the completed output (``read`` reports the lines returned).
    While drafting, a ``write`` body is still streaming and never salvaged, so
    its escaped newlines are counted so the line grows live.
    """
    if name == "read":
        target = str(args.get("path") or "")
        offset, limit = args.get("offset"), args.get("limit")
        if target and isinstance(limit, int):
            first = (offset if isinstance(offset, int) else 0) + 1
            target += f":{first}-{first + limit - 1}"
        elif target and isinstance(offset, int) and offset:
            target += f":{offset + 1}-"
        metric = _line_metric(count=_count_lines(output)) if output is not None else ""
        return target, metric
    if name == "write":
        target = str(args.get("path") or "")
        content = args.get("content")
        if isinstance(content, str):
            added = _count_lines(content)
        elif drafting and target and raw:
            added = raw.count(r"\n")
        else:
            added = 0
        return target, _line_metric(added=added)
    if name == "edit":
        target = str(args.get("path") or "")
        search, replace = args.get("search"), args.get("replace")
        if isinstance(search, str) and isinstance(replace, str):
            added, removed = _edit_counts(search, replace)
            return target, _line_metric(added=added, removed=removed)
        return target, ""
    if name == "bash":
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            return command.strip().splitlines()[0], ""
        return "", ""
    if len(args) == 1:
        return _short_value(next(iter(args.values()))), ""
    return "", ""


def _tool_summary(name: str, args: dict, output: Optional[str]) -> str:
    """Plain ``target metric`` summary of a completed tool call."""
    target, metric = _tool_target_metric(name, args, raw=None, output=output, drafting=False)
    return " ".join(part for part in (target, strip_ansi(metric)) if part)


def thought_worth_showing(reasoning: str) -> bool:
    """Whether reasoning earns its own transcript line (``ui.thought_min_tokens``).

    Tokens are estimated at ~4 characters each. Shorter reasoning is still kept
    in history and exports; it just gets no line.
    """
    text = reasoning.strip()
    if not text:
        return False
    return len(text) / 4 >= pico_cfg.config.ui_thought_min_tokens


def _clip(text: str, width: int, *, keep_end: bool = False) -> str:
    """Fit plain ``text`` in ``width`` columns with an ellipsis.

    ``keep_end`` keeps the tail (paths: the file name matters most).
    """
    text = text.replace("\t", "    ")
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    if width == 1:
        return "…"
    if keep_end:
        tail = text
        while tail and display_width(tail) > width - 1:
            tail = tail[1:]
        return "…" + tail
    head = text
    while head and display_width(head) > width - 1:
        head = head[:-1]
    return head + "…"


def _hard_wrap(text: str, width: int) -> list[str]:
    """Split plain ``text`` into chunks of at most ``width`` columns."""
    text = text.replace("\t", "    ")
    if width <= 0 or not text:
        return [text]
    chunks, current, used = [], "", 0
    for char in text:
        char_width = display_width(char)
        if current and used + char_width > width:
            chunks.append(current)
            current, used = "", 0
        current += char
        used += char_width
    chunks.append(current)
    return chunks


def _tool_body(name: str, args: dict) -> list[tuple]:
    """Readable focused-view body: edit diff, write head, full bash command.

    Lines are ``(color, prefix, text)`` triples so the caller clips the plain
    text to the width before coloring.
    """
    body: list = []
    if name == "edit":
        search, replace = args.get("search"), args.get("replace")
        if isinstance(search, str) and isinstance(replace, str):
            old, new = search.splitlines(), replace.splitlines()
            matcher = difflib.SequenceMatcher(None, old, new)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    body += [(theme.MUTED, "  ", line) for line in old[i1:i2]]
                    continue
                if tag in ("replace", "delete"):
                    body += [(theme.ERROR, "- ", line) for line in old[i1:i2]]
                if tag in ("replace", "insert"):
                    body += [(theme.SUCCESS, "+ ", line) for line in new[j1:j2]]
            limit = _BODY_LINES * 2
            if len(body) > limit:
                more = len(body) - limit
                body = body[:limit] + [(theme.MUTED, "", f"… {more} more {_lines_word(more)}")]
        return body
    if name == "write":
        content = args.get("content")
        if isinstance(content, str):
            lines = content.splitlines()
            body = [(theme.SUCCESS, "+ ", line) for line in lines[:_BODY_LINES]]
            more = len(lines) - _BODY_LINES
            if more > 0:
                body.append((theme.MUTED, "", f"… +{more} more {_lines_word(more)}"))
        return body
    if name == "bash":
        command = args.get("command")
        if isinstance(command, str):
            for i, line in enumerate(command.strip().splitlines()):
                body.append((theme.MUTED, "$ " if i == 0 else "  ", line))
        return body
    if name == "read":
        return body
    for key, value in args.items():
        body.append((theme.MUTED, f"{key}: ", _short_value(value, limit=200)))
    return body


def _output_lines(name: str, output: str) -> list[str]:
    """Toggled output, capped head…tail (bash stream markers dropped)."""
    lines = output.splitlines()
    if name == "bash":
        lines = [line for line in lines
                 if line.strip() != "[stdout]" and not _EXIT_RE.match(line.strip())]
    if len(lines) > _OUTPUT_HEAD + _OUTPUT_TAIL + 1:
        hidden = len(lines) - _OUTPUT_HEAD - _OUTPUT_TAIL
        lines = (lines[:_OUTPUT_HEAD]
                 + [f"… {hidden} more {_lines_word(hidden)} …"]
                 + lines[-_OUTPUT_TAIL:])
    return lines


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
        self.collapsed = False
        # Whether this message type folds by default and expands on focus.
        self.collapsible = isinstance(msg_type, msg_types.ThinkingMsg)
        # Live wait-phase label for a collapsible status message (e.g.
        # "processing", "thinking"); rendered collapsed while in flight.
        self.status_phase: Optional[str] = None
        self._phase_started_at: Optional[float] = None
        self.phase_seconds: Optional[float] = None
        
        # Tool-specific metadata
        self.tool_name: Optional[str] = None
        self.tool_args: Optional[str] = None
        self.tool_output: Optional[str] = None
        # "drafting" | "running" | "completed" | "error" | "denied" | "cancelled"
        self.tool_status: Optional[str] = None
        self.show_output: bool = False  # Toggle for output visibility ('o')
        # Last few lines of live output while a command runs.
        self.live_output: list[str] = []
        self._run_started_at: Optional[float] = None
        # Last rendered live label (elapsed seconds); ``tick`` redraws on change.
        self._live_label: Optional[str] = None

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
                 msg_types.ToolCallMsg, msg_types.AskPermissionMsg),
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
        # Tool messages bake their state into the display text; rebuild so the
        # finalized line drops its live elapsed time and output tail.
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
        if self.is_tool_message():
            # The cached header/body carry colors of the previous theme.
            self._tool_display_cache_key = None
            self.rebuild_tool_display()
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
        changed = self.box.focused != focused
        if changed:
            self.layout_revision += 1
        self.box.set_focused(focused)
        # A tool line's body (diff, command, output) is built only when
        # focused, so focus changes must rebuild it; otherwise an auto-focused
        # permission prompt would show just its header.
        if changed and self.is_tool_message():
            self.rebuild_tool_display()

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

    def tick(self) -> None:
        """Refresh live elapsed-time text (called a few times per second).

        Only redraws when the visible label changes, so an idle tick is free.
        """
        if self.is_tool_message():
            label = self._tool_trailing()
        elif self.collapsible:
            label = self._thinking_label()
        else:
            return
        if label != self._live_label:
            self._live_label = label
            if self.is_tool_message():
                self.rebuild_tool_display()
            else:
                self.box.mark_changed()

    def is_tool_message(self) -> bool:
        """True for tool-call and permission-request messages."""
        return isinstance(self.type, (msg_types.ToolCallMsg, msg_types.AskPermissionMsg))

    def set_tool_status(self, status: str) -> None:
        """Move the tool lifecycle forward; ``running`` starts the clock."""
        if status == "running" and self.tool_status != "running":
            self._run_started_at = time.perf_counter()
        self.tool_status = status

    def append_live_output(self, data: str) -> None:
        """Keep the last few lines of a running command's output.

        The final element is the still-open line, completed by the next chunk.
        """
        pending = self.live_output.pop() if self.live_output else ""
        lines = (pending + data).split("\n")
        self.live_output = (self.live_output + lines)[-(_LIVE_TAIL + 1):]
        self.rebuild_tool_display()

    def tool_state(self) -> str:
        """``drafting``/``asking``/``running`` or a terminal state."""
        if isinstance(self.type, msg_types.AskPermissionMsg) and not self.finalized:
            return "asking"
        status = self.tool_status or ""
        if status in ("drafting", "completed", "error", "denied", "cancelled"):
            return status
        return "running"

    def _thinking_label(self) -> str:
        """Collapsed thinking label: live ``thinking 3s``, then ``thought for 4.2s``."""
        if self.finalized:
            if self.phase_seconds is None:
                return "thought"
            seconds = self.phase_seconds
            shown = f"{seconds:.1f}" if seconds < 10 else f"{seconds:.0f}"
            return f"thought for {shown}s"
        elapsed = 0.0
        if self._phase_started_at is not None:
            elapsed = time.perf_counter() - self._phase_started_at
        if self.base_text.strip():
            label = "thinking"
        elif self.status_phase == "processing" and elapsed >= _PREPARING_AFTER:
            label = "preparing"
        else:
            label = "waiting"
        return f"{label} {int(elapsed)}s"

    def render_collapsed_line(self, subbuffer, max_width: int, fg, bg) -> None:
        """Render the single-line summary for a collapsed message.

        A live thinking line shows its ticking label plus the tail of the
        latest reasoning line; a finalized one reads ``thought for Xs``.
        """
        if not isinstance(self.type, msg_types.ThinkingMsg):
            line = self.type.title or self.type.name
        else:
            line = self._thinking_label()
            revealed = self.base_text[:self._reveal_len]
            if not self.finalized and revealed.strip():
                last = next(
                    (l for l in reversed(revealed.splitlines()) if l.strip()), ""
                )
                room = max_width - 2 - display_width(line) - 2
                preview = _clip(" ".join(last.split()), room, keep_end=True)
                if preview:
                    line += f"  {preview}"
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
        if self.is_tool_message() and self.tool_name:
            # Tool lines are built for the width (clipped, not wrapped).
            self.rebuild_tool_display()
            return self.formatted_text
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
        elif self.is_tool_message():
            # Built line by line for the width; wrapping would strip the
            # indentation of code in diffs and commands.
            self.formatted_text = text
            self.component.update(text)
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

        The header is ``name target metric trailing``: no status glyph; the
        name color and a trailing word carry the state (``approve? a/x``,
        ``running 12s``, an error reason). While the model is still writing
        the call the same line grows live. Focused messages add a readable
        body (edit diff, write head, full command) and, when toggled, the
        output; a running command shows its last output lines.
        """
        if not self.tool_name:
            return
        state = self.tool_state()
        drafting = state == "drafting"
        completed_output = self.tool_output if state == "completed" else None
        width = max(10, self.max_width or 80)

        # Parse/summarize at most once per distinct args/output: ticks rebuild
        # a running message, and re-parsing a huge ``write`` body is O(n).
        cache_key = (id(self.tool_args), self.tool_name, id(completed_output), drafting)
        if getattr(self, "_tool_display_cache_key", None) != cache_key:
            # A draft's args are partial: never full-parse them, or a ``"}"``
            # inside the streamed content would scan the whole buffer.
            args = _parse_tool_args(self.tool_args, full=not drafting)
            self._tool_display_cache_key = cache_key
            self._tool_display_parts = _tool_target_metric(
                self.tool_name, args, raw=self.tool_args,
                output=completed_output, drafting=drafting)
            self._tool_display_body = _tool_body(self.tool_name, args) if not drafting else []
        target, metric = self._tool_display_parts

        failed = state in ("error", "denied")
        name_color = theme.ERROR if failed else theme.TOOL
        name = self.tool_name
        is_compact = self.box.compact_when_unfocused and not self.box.focused
        # Expanded bash shows the whole command wrapped below, so the header
        # does not repeat a cut-off copy of it.
        wrap_body = self.tool_name == "bash" and not is_compact
        if wrap_body:
            target = ""
        trailing = self._tool_trailing()
        trailing_color = {
            "asking": theme.PERMISSION, "running": theme.MUTED,
            "cancelled": theme.WARNING,
        }.get(state, theme.ERROR)
        trailing = _clip(trailing, width // 2)

        # Single spaces; the parts read apart by color. Only the name (and
        # colored counts/states) stand out: the rest is muted activity.
        fixed = display_width(name) + 1
        if metric:
            fixed += display_width(strip_ansi(metric)) + 1
        if trailing:
            fixed += display_width(trailing) + 1
        target = _clip(target, max(8, width - fixed), keep_end=self.tool_name != "bash")

        header = f"{name_color}{name}{theme.reset()}"
        if target:
            header += f" {theme.MUTED}{target}{theme.reset()}"
        if metric:
            header += f" {metric}"
        if trailing:
            header += f" {trailing_color}{trailing}{theme.reset()}"
        lines = [header]

        if not is_compact:
            for color, prefix, text in self._tool_display_body:
                room = width - display_width(prefix)
                if wrap_body:
                    chunks = _hard_wrap(text, room)
                    lines += [
                        f"{color}{prefix if k == 0 else ' ' * len(prefix)}{theme.reset()}"
                        f"{theme.MUTED}{chunk}{theme.reset()}"
                        for k, chunk in enumerate(chunks)
                    ]
                else:
                    clipped = _clip(text, room)
                    if prefix.strip() in ("+", "-"):
                        # Added/removed lines are colored whole, not just the sign.
                        lines.append(f"{color}{prefix}{clipped}{theme.reset()}")
                    else:
                        lines.append(f"{color}{prefix}{theme.reset()}{clipped}")

        if state == "running" and self.live_output:
            tail = [line for line in self.live_output if line.strip()][-_LIVE_TAIL:]
            lines += [f"{theme.MUTED}{_clip(line, width)}{theme.reset()}" for line in tail]

        if self.show_output and self.tool_output and not is_compact:
            for line in _output_lines(self.tool_name, self.tool_output):
                lines.append(f"{theme.MUTED}{_clip(line, width)}{theme.reset()}")

        self.base_text = '\n'.join(lines)
        self._reveal_len = len(self.base_text)
        self._render_revealed()

    def _tool_trailing(self) -> str:
        """Plain trailing word: only when it is news (asking, running, failed)."""
        state = self.tool_state()
        if state == "asking":
            return "approve? a/x"
        if state == "running":
            if self._run_started_at is None:
                return ""
            elapsed = time.perf_counter() - self._run_started_at
            return f"running {int(elapsed)}s" if elapsed >= _RUNNING_AFTER else ""
        if state == "denied":
            return "denied"
        if state == "cancelled":
            return "cancelled"
        if state == "error":
            reason = next(
                (line.strip() for line in (self.tool_output or "").splitlines() if line.strip()),
                "error",
            )
            return reason
        if state == "completed" and self.tool_name == "bash":
            code = _bash_exit_code(self.tool_output)
            if code:
                return f"exit {code}"
        return ""

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
