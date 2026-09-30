"""
moka TUI Application.
"""

import sys
import asyncio
import atexit
import time
from typing import Any

from moka_code.ui.tui.compositor import Compositor
from moka_code.ui.tui.events import KeyEvent, MouseEvent, PasteEvent, TickEvent
from moka_code.ui.tui.components import (
    Box, InputComponent,
)
from moka_code.ui.tui.components.bars import ActionBar, ActionItem, BarStyle, NoticeBand
from moka_code.ui.tui.components.debug_panel import DebugLogPanel
from moka_code.ui.tui.components.popup import Popup, PopupScreen
from moka_code.ui.tui.components.debug_popup import DebugPopup
from moka_code.ui.tui.components.bars import StatusBar
from moka_code.ui.chat_history_panel import ChatHistoryPanel
from moka_code.ui.chat_message import Message, reference_spans, unmention
from moka_code.ui.commands import (
    handle_command, get_command_list, get_command_descriptions,
    get_subcommand_list, get_subcommand_descriptions,
    next_role_command, next_sandbox_command,
)
from moka_code.ui.generation_presenter import process_generation
from moka_code.ui.stream_revealer import StreamRevealer
from moka_code.ui.status_presenter import refresh_status_bar
from moka_code.ui.shell_command import handle_shell_command
from moka_code.ui.tui.focus import FocusScope
from moka_code.ui.tui.navigation import ModalHost
from moka_code.ui.tui.chat_screen import ChatScreen

        # Setup logging to debug panel
import logging
from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.msg_types import MsgAction, SysMsg, SysMsgError, SysMsgWarning

from moka_code import settings
from moka_code.ui.logging_handlers import setup_tui_logging
from moka_code.ui.chat_action_handlers import ChatActionHandlers
from moka_code.ui.clipboard import read_clipboard
from moka_code.harness import images


class _AppFocusTarget:
    """Adapter exposing an application focus target to the TUI focus API."""

    focusable = True
    enabled = True

    def __init__(self, component, on_focus, handle_input):
        self._component = component
        self._on_focus = on_focus
        self._handle_input = handle_input
        self.focused = False

    @property
    def x(self):
        return self._component.x

    @property
    def y(self):
        return self._component.y

    @property
    def width(self):
        return self._component.width

    @property
    def height(self):
        return self._component.height

    def set_focused(self, focused: bool):
        self.focused = focused
        self._on_focus(focused)

    def set_component(self, component):
        self._component = component

    def handle_input(self, event):
        return self._handle_input(event)


def _show_role_change(panel: ChatHistoryPanel, previous_name: str, role_name: str) -> None:
    """Show a role-change notice, de-duping consecutive notices."""
    if previous_name == role_name:
        return

    text = f"Role changed: {previous_name} -> {role_name}"
    messages = getattr(panel, "messages", [])
    last = messages[-1] if messages else None
    if getattr(last, "_is_role_change_notice", False):
        replacement = panel.new_message(text, msg_type=SysMsg(), title="role")
        replacement._is_role_change_notice = True
        panel.replace_message(last, replacement)
        return

    notice = panel.add_message(text, msg_type=SysMsg(), title="role")
    if notice is not None:
        notice._is_role_change_notice = True


class chatTUI(ChatActionHandlers):
    """Terminal UI for the agent."""

    def __init__(self, agent, resume: bool = False, migration_notice: str | None = None):
        self.agent = agent
        # One-time note on the banner (the pre-rename config folder was moved).
        self._migration_notice = migration_notice
        # ``moka --resume``: open the /session picker once the UI is up.
        self._resume_on_start = resume
        # Pre-warm .local hostname resolution for the agent so the
        # first message doesn't stall on DNS/mDNS lookup.
        endpoint = getattr(agent, "endpoint", None)
        if endpoint is not None:
            from moka_code.harness.endpoint import prewarm_local_resolution
            prewarm_local_resolution(endpoint._original_base_url)
        self.compositor = None
        self.modal_host = None
        self.popup_screen = None
        self._last_focus_id = "input"
        self.chat_history_panel = ChatHistoryPanel()
        self.input_component = InputComponent("", id="entry", frame_color=theme.USER)
        self.input_component.config = settings.config
        self.input_component.on_submit = self.on_user_submit
        self.input_component.highlighter = reference_spans
        self.input_component.setup_commands(get_command_list(), get_command_descriptions())
        self.input_component.setup_subcommands(get_subcommand_list, get_subcommand_descriptions)
        get_context_items = lambda: agent.list_files_and_folders() if hasattr(agent, "list_files_and_folders") else []
        self.input_component.setup_context(get_context_items)
        from moka_code.ui.commands import COMMANDS
        self.input_component.setup_command_registry(COMMANDS)
        self.input_box = Box(
            self.input_component,
            title="",
            fg=theme.USER,  # Color the bars/prefix with the user color.
            lines_only=True,
            color_provider=self._input_box_fg,
        )
        # Mute the typed text too when the input loses focus; keep default
        # content color (None) while focused.
        self.input_component.set_content_color_provider(lambda: (
            None if self.input_box.focused else theme.MUTED
        ))
        self._focus_targets = [
            _AppFocusTarget(self.input_component, self._set_input_focus, self.input_component.handle_input),
            _AppFocusTarget(self.chat_history_panel, self._set_history_focus, self.chat_history_panel.handle_input),
        ]
        self._focus_scope = FocusScope(self._focus_targets)
        self.debug_panel = DebugLogPanel(max_lines=1000, frame_color=theme.ERROR, content_color=theme.MUTED, left_pad=1, right_pad=0)
        self.debug_popup = DebugPopup(self.debug_panel)
        # Activity surface: non-conversation output (shell results, command
        # status, notices) lives here, not in the transcript.
        self.activity_panel = DebugLogPanel(max_lines=2000, frame_color=theme.WARNING, content_color=theme.MUTED, left_pad=1, right_pad=0)
        self.activity_popup = DebugPopup(self.activity_panel, title="activity")
        self.chat_history_panel.activity_sink = self._on_activity
        self.popup = Popup()
        self.log_handler = setup_tui_logging(self.debug_panel)
        # Left-align the status text (no leading padding).
        self.status_bar = StatusBar(
            fields=settings.config.ui_status_bar_fields,
            id="status",
            style=BarStyle(theme.DEFAULT, theme.get_bg(), theme.FOCUSED, padding=1),
        )
        self._status_spinner_frame = 0
        # Top band: what needs fixing right now (``status_presenter.notices``).
        self.notice_band = NoticeBand(max_lines=settings.config.ui_notice_lines,
                                      more="+{n} more · /activity")
        self._notices = []
        # Bottom mode line: shows the selected message's actions while a message
        # is selected, otherwise the status bar is mounted there.
        self.action_bar = ActionBar(
            id="actions",
            style=BarStyle(theme.MUTED, theme.get_bg(), theme.MUTED, padding=1),
        )
        self._mode_hint_default = "↑↓ move · esc back"
        self.action_bar.set_hint(self._mode_hint_default)
        self.action_bar.set_top_pad(True)
        self.action_bar.set_align_right(True)
        self._hint_flash_until = 0.0
        self._hint_flash = ("", None)
        self.refresh_setup_notes()
        self.chat_history_panel.on_selection_changed = self._update_mode_line
        self.chat_history_panel.on_hint = self.flash_hint
        self.chat_history_panel.on_copy = self.copy_text
        self.input_component.on_change = self._update_action_strip
        self.command_queue = asyncio.Queue()
        self.shutdown_event = asyncio.Event()
        # Single-conversation state (formerly owned by ConversationRuntime).
        self.message_queue = asyncio.Queue()
        self.current_generation_task = None
        self.worker_task = None
        # Set by stop_generation so the worker survives a /stop cancel.
        self._stop_requested = False
        self.active_tool_messages = {}
        self.pending_permission_prompt = None
        self._active_user_input = None
        self._active_user_msg = None
        # Images pasted into the input draft, by their ``[image #N]`` number.
        self._pasted_images = {}
        # Where this conversation is saved (``harness.sessions``); a new
        # session until /session resumes an older one.
        self.session_path = None
        self.new_session()
        # Stream smoothing: the revealer owns timing, the message owns the
        # canonical arrived text plus its rendered prefix.
        self.stream_revealer = None
        self.stream_message = None
        self.stream_revealed = 0
        self._stream_finalize_pending = False
        # Single time source for stream ingestion; tests can replace it.
        self._clock = time.perf_counter
        atexit.register(self._emergency_cleanup)

    def switch_role(self, role):
        """Apply a role and show a role-change notice."""
        if self.is_generating():
            raise RuntimeError("Role changes apply after the current response finishes.")
        previous_name = getattr(getattr(self.agent, "role", None), "name", "agent")
        self.agent.set_role(role)
        _show_role_change(self.chat_history_panel, previous_name, role.name)
        return self.agent.role

    def is_generating(self) -> bool:
        return (
            self.current_generation_task is not None
            and not self.current_generation_task.done()
        )

    # -- stream smoothing ----------------------------------------------------

    def reset_stream_revealer(self):
        """(Re)initialize smoothing state for a new generation."""
        if settings.config.ui_stream_smoothing:
            self.stream_revealer = StreamRevealer(settings.config.ui_smooth_target_fps)
        else:
            self.stream_revealer = None
        self.stream_message = None
        self.stream_revealed = 0
        self._stream_finalize_pending = False

    def start_stream_message(self, msg):
        """Mark ``msg`` as the message the revealer currently feeds."""
        self.stream_message = msg
        self.stream_revealed = msg._reveal_len
        self._stream_finalize_pending = False

    def stream_ingest(self, text: str):
        """Schedule arrived text for smoothed release."""
        if self.stream_revealer is not None:
            self.stream_revealer.ingest(text, self._clock())

    def flush_stream(self):
        """Release all pending text into the active message immediately."""
        if self.stream_revealer is None or self.stream_message is None:
            return
        released = self.stream_revealer.drain()
        if released:
            self.stream_revealed += len(released)
            self.stream_message.reveal_to(self.stream_revealed)

    def defer_stream_finalize(self):
        """Let the revealer drain, then finalize from the frame callback."""
        if self.stream_revealer is not None and self.stream_message is not None:
            self._stream_finalize_pending = True

    def _finalize_stream(self):
        if self.stream_message is not None:
            self.stream_message.finalize()
            # The deferred end of an answer: split it now that it is complete.
            self.chat_history_panel.split_answer(self.stream_message)
        self.stream_message = None
        self.stream_revealed = 0
        self._stream_finalize_pending = False

    def disengage_stream(self):
        """Drop the active-stream reference once the stream has ended.

        Flushes any remaining pending text first so a boundary that missed the
        final flush cannot drop a chunk.
        """
        self.flush_stream()
        self.stream_message = None
        self.stream_revealed = 0
        self._stream_finalize_pending = False

    def _on_frame(self, now: float) -> bool:
        """Compositor frame callback: reveal a grain and manage finalization.

        Returns True only when it changed the rendered output. Returning True
        with no dirty rects makes the compositor fall back to a full-screen
        redraw, so a stream must not request a repaint on every frame while the
        revealer is merely waiting for its next step.
        """
        if self.stream_revealer is None:
            return False
        changed = False
        released = self.stream_revealer.tick(now)
        if released and self.stream_message is not None:
            self.stream_revealed += len(released)
            self.stream_message.reveal_to(self.stream_revealed)
            # Keep the growing text in view while it animates.
            if self.chat_history_panel.auto_scroll:
                self.chat_history_panel.scroll_offset = 0
            changed = True
        if self._stream_finalize_pending and not self.stream_revealer.active():
            self._finalize_stream()
            changed = True
        return changed

    def _input_box_fg(self):
        """Color the input row by focus (muted when unfocused)."""
        return theme.USER if self.input_box.focused else theme.MUTED

    def refresh_status_bar(self) -> None:
        """Refresh local status fields (see ``ui/status_presenter.py``)."""
        refresh_status_bar(self)

    def refresh_setup_notes(self) -> None:
        """Recompute the setup problems shown with the banner: config and role
        file errors, a missing editor, the one-time migration note. Called at
        startup and after every reload."""
        from moka_code.harness import roles
        from moka_code.ui.external_editor import resolve_editor

        sections = {name: section for section, name in settings.CONFIG_FILES.items()}
        notes = []
        for error in settings.config.load_errors:
            section = sections.get(error.split(":", 1)[0])
            notes.append(("error", f"{error} → /config {section}" if section else error))
        notes += [("error", f"{error} → /config role") for error in roles.validate_roles()]
        if not resolve_editor():
            notes.append(("warning", "no $VISUAL or $EDITOR (config files open in it) → export EDITOR=…"))
        if self._migration_notice:
            notes.append(("info", self._migration_notice))
        self.chat_history_panel.setup_notes = notes

    def refresh_theme(self) -> None:
        """Re-apply the active theme everywhere and force a full repaint.

        Colors are resolved when components are built, so a theme switch must
        re-resolve the chrome and every existing message, then ask the
        compositor to repaint every cell (cached frame content is stale).
        """
        self.input_component.frame_color = theme.USER
        self.input_component.bg = theme.get_bg()
        self.input_box.fg = theme.USER
        self.input_box.bg = theme.get_bg()

        self.debug_panel.frame_color = theme.ERROR
        self.debug_panel.content_color = theme.MUTED
        self.debug_panel.fg = theme.MUTED
        self.debug_panel.bg = theme.get_bg()

        self.activity_panel.frame_color = theme.WARNING
        self.activity_panel.content_color = theme.MUTED
        self.activity_panel.fg = theme.MUTED
        self.activity_panel.bg = theme.get_bg()

        self.chat_history_panel.fg = theme.DEFAULT
        self.chat_history_panel.bg = theme.get_bg()

        self.status_bar.style = BarStyle(theme.DEFAULT, theme.get_bg(), theme.FOCUSED, padding=1)
        self.action_bar.style = BarStyle(theme.MUTED, theme.get_bg(), theme.MUTED, padding=1)

        # Modals/overlays cache their colors at construction too.
        self.input_component.refresh_theme()
        self.popup.refresh_theme()
        self.debug_popup.refresh_theme()
        self.activity_popup.refresh_theme()

        self.chat_history_panel.refresh_theme()

        # Field values/colors (e.g. the server:model tint) are computed from
        # ``theme`` by the presenter, so re-run it to recolor them.
        refresh_status_bar(self)

        if self.compositor is not None:
            self.compositor.request_full_redraw()

    def _emergency_cleanup(self):
        """Emergency cleanup handler called by atexit."""
        terminal = getattr(self.compositor, "terminal", None)
        if terminal:
            try:
                terminal.cleanup(clear_screen=False)
            except Exception:
                pass


    async def _process_generation(self, user_input, user_msg, attached=()):
        """Process a single generation request.

        The event→UI mapping lives in ``ui/generation_presenter.py``.
        """
        await process_generation(self, user_input, user_msg, attached)

    async def agent_worker(self):
        """Process queued requests for the conversation."""
        import logging
        logger = logging.getLogger("tui")

        while not self.shutdown_event.is_set():
            try:
                # Race the queue against shutdown so Ctrl+C exits promptly even
                # while idle (otherwise this task blocks the TaskGroup forever).
                get_task = asyncio.create_task(self.message_queue.get())
                shutdown_task = asyncio.create_task(self.shutdown_event.wait())
                done, pending = await asyncio.wait(
                    (get_task, shutdown_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if shutdown_task in done:
                    self.stop_generation()
                    return
                user_input, user_msg, attached = get_task.result()
                if getattr(user_msg, "is_queued", False):
                    user_msg.is_queued = False
                    user_msg.set_title("user")
                    user_msg.set_frame_color(theme.USER)

                self._active_user_input = user_input
                self._active_user_msg = user_msg
                self._stop_requested = False
                self.current_generation_task = asyncio.create_task(
                    self._process_generation(user_input, user_msg, attached)
                )
                try:
                    await self.current_generation_task
                except asyncio.CancelledError:
                    # /stop cancels the generation, which surfaces here too;
                    # keep serving the queue. Anything else (shutdown) cancels
                    # the worker itself.
                    if not self._stop_requested:
                        raise
            except asyncio.CancelledError:
                self.stop_generation()
                return
            except Exception as error:
                logger.error("Conversation generation failed: %s", error, exc_info=True)
                self.chat_history_panel.add_message(str(error), msg_type=SysMsgError())
            finally:
                self.current_generation_task = None
                self._active_user_input = None
                self._active_user_msg = None
                self.save_session()

    async def command_worker(self):
        """Dispatch queued slash commands independently of generation workers."""
        import logging
        logger = logging.getLogger("tui")

        while not self.shutdown_event.is_set():
            command_task = asyncio.create_task(self.command_queue.get())
            shutdown_task = asyncio.create_task(self.shutdown_event.wait())
            try:
                done, pending = await asyncio.wait(
                    (command_task, shutdown_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if shutdown_task in done:
                    return
                command = command_task.result()
                logger.debug("Processing command: %s", command)
                await handle_command(self, command)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("Command failed", exc_info=True)

    def stop_generation(self):
        """Stop the current generation task if active."""
        if self.current_generation_task and not self.current_generation_task.done():
            self._stop_requested = True
            self.current_generation_task.cancel()
            return True
        return False

    def _ensure_worker(self) -> None:
        if self.worker_task is None or self.worker_task.done():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            self.worker_task = asyncio.create_task(self.agent_worker())

    def _enqueue_message(self, text: str, message: Message, attached=()) -> None:
        """Queue a message (with its image references) and apply queued presentation."""
        if self.is_generating() and message is not self._active_user_msg:
            message.is_queued = True
            message.set_title("user (queued)")
            message.set_frame_color(theme.MUTED)

        self._ensure_worker()
        self.message_queue.put_nowait((text, message, list(attached)))

    def on_command_submit(self, text: str):
        """Handle execution of commands."""
        self.command_queue.put_nowait(text)
        
    def toggle_debug_console(self):
        """Toggle the debug console overlay."""
        import logging
        self.debug_popup.set_compositor(self.compositor)
        self.debug_popup.toggle()
        logger = logging.getLogger("tui")
        logger.info("Debug console toggled: visible=%s", self.debug_popup.is_visible)

    def toggle_activity(self):
        """Toggle the activity overlay (non-conversation output)."""
        self.activity_popup.set_compositor(self.compositor)
        self.activity_popup.toggle()

    def activity(self, text: str, level: str = "info"):
        """Append output to the activity surface (persistent, not a message)."""
        for line in str(text).splitlines() or [""]:
            self.activity_panel.log(line)
        if self.compositor:
            self.compositor.request_render()

    def notify(self, text: str, level: str = "info", duration: float = 4.0):
        """Show a transient notice on the hint row, above the input (the status
        bar keeps its fields)."""
        color = {"error": theme.ERROR, "warning": theme.WARNING}.get(level, theme.DEFAULT)
        first_line = str(text).splitlines()[0] if str(text).splitlines() else ""
        self.flash_hint(first_line, duration, color)

    def _on_activity(self, text: str, level: str = "info"):
        """Sink for routed SysMsg-family notices: activity + a hint-row flash."""
        self.activity(text, level)
        self.notify(text, level)

    def flash_hint(self, text: str, duration: float = 1.5, color=None):
        """Temporarily replace the mode-line hint (e.g. "copied ✓"); the row
        shows even when it is otherwise collapsed."""
        self._hint_flash = (text, color)
        self._hint_flash_until = time.monotonic() + duration
        self._update_action_strip()

    def _handle_message_action(self, message, action: MsgAction):
        handlers = {
            MsgAction.COPY: self.handle_copy_action,
            MsgAction.OUTPUT: self.handle_output_action,
            MsgAction.ALLOW: self.handle_allow_action,
            MsgAction.DENY: self.handle_deny_action,
        }
        handler = handlers.get(action)
        if handler:
            handler(message)

    def _selected_message(self):
        """The currently selected message, or None."""
        index = self.chat_history_panel.focused_message_index
        messages = self.chat_history_panel.messages
        if index is None or not (0 <= index < len(messages)):
            return None
        return messages[index]

    def _update_action_strip(self):
        """Show/hide the action line (mounted right above the input).

        It shows the selected message's actions, or, when the input is focused
        and empty, the available input prefixes as hints.
        """
        message = self._selected_message()

        if message is not None:
            actions = list(message.get_active_actions())
            self.action_bar.set_actions([
                ActionItem(action.key, action.label,
                           callback=lambda a=action, m=message: self._handle_message_action(m, a))
                for action in actions
            ])
            panel = self.chat_history_panel
            self.action_bar.set_prefix(panel.segment_label())
            if panel.inside_group:
                hint = "↑↓ part · ← back"
            elif message.group is not None:
                hint = "↑↓ move · → parts · esc back"
            else:
                hint = self._mode_hint_default
            self.action_bar.set_hint(hint)
            self.action_bar.set_focused(False)
            self.action_bar.set_expanded(True)
        elif self._last_focus_id == "input":
            # Subtle right-aligned reminder of the input prefixes and the
            # history-move keys. ``@`` works mid-text; ``/``/``$`` are
            # line-start prefixes.
            self.action_bar.set_actions([])
            self.action_bar.set_prefix("")
            self.action_bar.set_hint("[/] command  [@] file  [↑↓] move")
            self.action_bar.set_focused(False)
            self.action_bar.set_expanded(True)
        else:
            self.action_bar.set_focused(False)
            self.action_bar.set_expanded(False)

        if self._hint_flash_until:
            self.action_bar.set_hint(*self._hint_flash)
            self.action_bar.set_expanded(True)

        if self.compositor:
            self.compositor._full_redraw = True
            self.compositor.request_render()

    # Back-compat alias for the old mode-line hook name.
    _update_mode_line = _update_action_strip

    def show_popup(self, title: str, content: str, content_padding: int = 1):
        """Show a popup overlay with the given title and content."""
        self.popup.set_compositor(self.compositor)
        if self.modal_host is None:
            self.popup.show(title, content, content_padding=content_padding)
            return
        self.popup_screen = PopupScreen(self.popup, title, content,
                                        content_padding=content_padding)
        self.modal_host.present_screen(self.popup_screen)

    def show_search_modal(self, title, items, descriptions=None, footers=None,
                          on_accept=None, on_cancel=None, on_highlight=None,
                          initial_index=0):
        """Present a centered, type-to-filter selection overlay.

        ``on_highlight`` fires with the selected item whenever the highlight
        moves (preview hook). Returns the modal (so callers can ``refresh`` it)
        or None when there is no compositor (headless).
        """
        from moka_code.ui.tui.components.search_modal import SearchModal

        if self.compositor is None:
            return None
        modal = SearchModal(compositor=self.compositor, title=title)
        # Anchor above the input, full-width, like the / and @ menus.
        modal.auto_center = False
        modal.fill_width = True
        modal.anchor = lambda: self.input_component.place_menu_above_input(modal)
        modal.open(items, descriptions=descriptions, footers=footers,
                   on_accept=on_accept, on_cancel=on_cancel,
                   on_highlight=on_highlight, initial_index=initial_index)
        return modal

    def new_session(self) -> None:
        """Start a new saved session (at startup, /clear, /import)."""
        from moka_code.harness import sessions

        self.session_path = sessions.new_path(getattr(self.agent, "workspace", "."))

    def save_session(self) -> None:
        """Save the conversation to its session file (``context.sessions``
        most recent kept per project; 0 = never saved). Best effort: a failed
        save is logged, never raised into the UI."""
        from moka_code.harness import sessions

        keep = settings.config.context_sessions
        history = getattr(self.agent, "history", None)
        if keep <= 0 or not history or self.session_path is None:
            return
        workspace = getattr(self.agent, "workspace", ".")
        try:
            role = getattr(getattr(self.agent, "role", None), "name", "agent")
            model = getattr(getattr(self.agent, "endpoint", None), "selected_model", None)
            sessions.write(self.session_path, role, history, model)
            sessions.prune(workspace, keep)
        except Exception:
            logging.getLogger("tui").warning("Could not save the session", exc_info=True)

    def paste_clipboard(self) -> None:
        """Ctrl+V: paste the clipboard into the input.

        Text is inserted like a terminal paste. An image is saved to the image
        cache and inserted as an ``[image #N]`` marker; it is attached when the
        message is sent (see ``harness.images.collect``).
        """
        content = read_clipboard()
        if content is None:
            self.notify("Clipboard is empty (or no wl-paste/xclip/xsel).", "warning")
            return
        if isinstance(content, bytes):
            try:
                image = images.store(content, images.max_bytes())
            except images.ImageError as exc:
                self.notify(f"Can't paste the image: {exc}", "warning")
                return
            n = max(self._pasted_images, default=0) + 1
            self._pasted_images[n] = image
            content = f"[image #{n}]"
        self.input_component.handle_input(PasteEvent(content))

    def _collect_images(self, text: str):
        """The images *text* attaches, or ``None`` when the message is refused
        (the reason is shown and the text stays in the input)."""
        attached = images.collect(text, self._pasted_images)
        endpoint = getattr(self.agent, "endpoint", None)
        accepts = getattr(endpoint, "accepts_images", lambda: None)()
        if attached and accepts is False:
            model = getattr(endpoint, "selected_model", None) or "This model"
            self.chat_history_panel.add_message(
                f"{model} can't read images. Remove the image, or pick a vision model with /model.",
                msg_type=SysMsgWarning())
            return None
        return attached

    def on_user_submit(self, text: str):
        """Handle user input submission.

        Returns False when the message is refused, which keeps it in the input.
        """
        clean_text = text.strip()
        if not clean_text:
            return  # Ignore empty or whitespace-only input

        if clean_text.startswith('/'):
            self.on_command_submit(unmention(clean_text))
            return

        # $ prefix: execute shell command directly (not visible to LLM)
        if clean_text.startswith('$'):
            self._handle_shell_command(clean_text[1:].strip())
            return

        if self.pending_permission_prompt:
            self.chat_history_panel.add_message(
                "Permission required for pending tool call. Use [a] allow or [x] deny first. Slash commands are still available.",
                msg_type=SysMsg()
            )
            return

        if getattr(self.agent, "sandbox_required", lambda: False)():
            # The notice band says why and how to fix it.
            self.flash_hint("can't send: this role requires a sandbox", 3.0, theme.ERROR)
            return False

        self._ensure_worker()

        if clean_text.lower() in ["exit", "quit", "q"]:
            if self.compositor:
                self.compositor.running = False
        else:
            import logging
            logger = logging.getLogger("tui")
            logger.info(f"User submitted: {text[:50]}...")

            attached = self._collect_images(text)
            if attached is None:
                return False
            self._pasted_images = {}

            # Create user message and queue it
            user_msg = self.chat_history_panel.add_user_message(text, attached)

            self._enqueue_message(unmention(text), user_msg, attached)
            
            # Enable auto-scroll to show the new message
            self.chat_history_panel.auto_scroll = True

    def _handle_shell_command(self, command: str):
        """Execute a shell command (see ``ui/shell_command.py``)."""
        handle_shell_command(self, command)

    def _update_focus_states(self):
        """Update focus states of components based on _last_focus_id."""
        # Default to input focus if nothing is focused
        if self._last_focus_id is None:
            self._last_focus_id = "input"

        target_index = 0 if self._last_focus_id == "input" else 1
        self._focus_scope.manager.focus(target_index)
        
        is_input_focused = (self._last_focus_id == "input")
        is_history_focused = (self._last_focus_id == "history")
        
        # Auto-scroll to bottom when input field is focused
        if is_input_focused:
            self.chat_history_panel.auto_scroll = True
            self.chat_history_panel.scroll_offset = 0

    def _set_input_focus(self, focused: bool):
        self.input_component.set_focused(focused)
        self.input_box.set_focused(focused)

    def _set_history_focus(self, focused: bool):
        self.chat_history_panel.set_keyboard_focus(focused)

    def _set_app_focus(self, focus_id: str):
        if focus_id not in ("input", "history"):
            raise ValueError(f"Unknown application focus target: {focus_id}")
        self._last_focus_id = focus_id
        self._focus_scope.manager.focus(0 if focus_id == "input" else 1)
        self._update_focus_states()
        self._update_action_strip()

    def handle_global_input(self, event: Any) -> bool:
        """Handle focus logging and input dispatch with navigation between input and history."""
        
        # Advance the status-bar spinner while .local resolution or model
        # name discovery is pending. The input cursor blink is driven by the
        # input component's own handle_input(TickEvent) path.
        if isinstance(event, TickEvent):
            from moka_code.harness.endpoint import is_local_resolution_pending
            agent = self.agent
            endpoint = getattr(agent, "endpoint", None)
            if endpoint is not None and (
                is_local_resolution_pending(endpoint._original_base_url)
                or getattr(endpoint, "_model_name_pending", False)
            ):
                self._status_spinner_frame += 1
                self.refresh_status_bar()
            # Restore the mode line after a transient flash (e.g. "copied").
            if self._hint_flash_until and time.monotonic() >= self._hint_flash_until:
                self._hint_flash_until = 0.0
                self._update_action_strip()
            return False

        # Handle keyboard navigation between input and history
        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event

            # Terminal window focus reports: drop the visible focus while the
            # window is in the background, restore it when it comes back.
            if key in ('\x1b[I', '\x1b[O'):
                window_focused = key == '\x1b[I'
                if self._last_focus_id == "input":
                    self._set_input_focus(window_focused)
                else:
                    self._set_history_focus(window_focused)
                if self.compositor:
                    self.compositor.request_render()
                return True

            if key == '\x16' and self._last_focus_id == "input":  # Ctrl+V
                self.paste_clipboard()
                return True

            if self._last_focus_id == "input" and self.input_component.has_active_completion():
                if key in ('\x1b', '\x1b[A', '\x1b[B', '\t', '\r', '\n'):
                    return self.input_component.handle_input(event)

            # On an empty input (or from history), Tab cycles roles and
            # Shift+Tab sandboxes, through the commands so their busy checks
            # and messages apply. With a draft (e.g. ``/model``) Tab does nothing.
            idle = self._last_focus_id != "input" or not self.input_component.buffer.text
            if key == '\t' and idle:
                self.on_command_submit(next_role_command(self))
                return True
            if key == '\x1b[Z' and idle:
                command = next_sandbox_command(self)
                if command:
                    self.on_command_submit(command)
                else:
                    self.flash_hint("no sandbox defined (/sandbox init)")
                return True

            # ESC while the input is focused (and nothing is being completed)
            # unfocuses the input, moving focus to the history panel. The focus
            # manager updates each widget's focused state.
            if key == '\x1b' and self._last_focus_id == "input" and not self.input_component.has_active_completion():
                self._set_app_focus("history")
                return True

            # Shortcuts to focus input: 'i' or Enter (when not already in input)
            if (key == 'i' or key == '\r') and self._last_focus_id != "input":
                self.chat_history_panel.clear_focus()
                self._set_app_focus("input")
                return True
            
            if key == '\x1b[A':  # Up arrow
                # If input has focus and cursor is on first line, move to history
                if self._last_focus_id == "input" and self.input_component.is_cursor_on_first_line():
                    # Focus the last message (or the first message if list is empty)
                    if self.chat_history_panel.messages:
                        self.chat_history_panel.set_focused_message(len(self.chat_history_panel.messages) - 1)
                        self._set_app_focus("history")
                        return True
                # Otherwise, let the event pass through to the active component
            
            elif key == '\x1b[B':  # Down arrow
                # Only handle focus change when in history (not when in input)
                if self._last_focus_id == "history" and self.chat_history_panel.focused_message_index is not None:
                    if self.chat_history_panel.at_last_message():
                        # At the bottom of history, switch to input
                        self.chat_history_panel.clear_focus()
                        self._set_app_focus("input")
                        return True
                # Otherwise: if in input, let DOWN work normally for cursor movement
                # If in history (not at bottom), let it navigate messages normally
        
        # Handle mouse click focus changes
        if isinstance(event, MouseEvent):
            # Clicks on the action line dispatch actions.
            if self.action_bar.expanded and (
                self.action_bar.x <= event.x < self.action_bar.x + self.action_bar.width
                and self.action_bar.y <= event.y < self.action_bar.y + self.action_bar.height
            ):
                return self.action_bar.handle_input(event)

            # Ignore wheel scroll events for focus purposes — they shouldn't
            # change focus, only scroll the panel under the cursor.
            if event.pressed and event.button not in (64, 65):
                clicked_focus = self._focus_scope.focus_at(event.x, event.y)
                # Clicking the input box (including its top/bottom bars) or the
                # status bar should always return focus to the input field.
                in_input_box = (
                    self.input_box.x <= event.x < self.input_box.x + self.input_box.width
                    and self.input_box.y <= event.y < self.input_box.y + self.input_box.height
                )
                if not clicked_focus and in_input_box:
                    # Clicking the input box (bars or field) focuses input.
                    self._set_app_focus("input")
                    self.chat_history_panel.clear_focus()
                    return True
                elif clicked_focus:
                    target_id = "input" if self._focus_scope.focused_index == 0 else "history"
                    if target_id == "input":
                    # Clear focused message when clicking input
                        self.chat_history_panel.clear_focus()
                    
                    if target_id != self._last_focus_id:
                    # Log focus change
                        import logging
                        logger = logging.getLogger("harness")
                        logger.info(f"[UI] Focus changed to: {target_id}")
                        self._set_app_focus(target_id)
            
        # Normal events are dispatched by EventRouter to the active focus target.
        return False


    def render(self, _force_full=False):
        if not self.compositor or self.compositor.width == 0 or self.compositor.height == 0:
            return

        self.compositor.buffer.clear()
        self.root.set_layout(0, 0, self.compositor.width, self.compositor.height)
        self.root.render(self.compositor.buffer)
        
        # Use Buffer's built-in render method
        output = self.compositor.buffer.render()
        sys.stdout.write(output)
        sys.stdout.flush()

    async def run(self):
        """Run the TUI application."""
        import logging
        logger = logging.getLogger("tui")
        logger.info("Starting moka TUI application")
        
        # Start the harness services if available
        if hasattr(self.agent, 'start'):
            self.agent.start()
            logger.info("Agent started")
        
        # Pre-warm resolution and discover the model name in the background so
        # the status bar shows it instead of "?".
        endpoint = getattr(self.agent, "endpoint", None)
        if endpoint is not None:
            from moka_code.harness.endpoint import prewarm_local_resolution
            prewarm_local_resolution(endpoint._original_base_url)

            async def _prewarm_and_refresh():
                from moka_code.harness.endpoint import refresh_catalog

                # Model facts are discovered live: the active server first.
                await refresh_catalog([endpoint.name])
                self.refresh_status_bar()
                await endpoint.prewarm_model_name()
                self.refresh_status_bar()
                from moka_code import settings
                await refresh_catalog([n for n in settings.config.servers if n != endpoint.name])

            asyncio.ensure_future(_prewarm_and_refresh())

        chat_screen = ChatScreen(
            self.chat_history_panel,
            self.input_box,
            self._focus_scope,
            self.status_bar,
            self.action_bar,
            self.notice_band,
        )
        self.root = chat_screen.root  # Store root for global handler
        # Read fps at construction time (not import time) so config changes apply.
        self.compositor = Compositor(self.root, fps=settings.config.target_fps,
                                     shutdown_event=self.shutdown_event)
        self.compositor.padding = settings.config.ui_app_global_padding  # Apply global padding from config
        self.modal_host = ModalHost(self.compositor)
        self._focus_scope.enter()

        self.reset_stream_revealer()
        if self.stream_revealer is not None:
            self.compositor.add_frame_callback(self._on_frame)
        
        self.compositor.event_router.set_interceptor(self.handle_global_input)
        self.compositor.event_router.set_focus_scope(self._focus_scope)

        # Set compositor for all panels
        self.chat_history_panel.set_compositor(self.compositor)
        self.input_component.set_compositor(self.compositor)
        
        self.chat_history_panel.on_action = self._handle_message_action
        
        # Set initial focus states
        self._update_focus_states()
        self._update_action_strip()
        self.refresh_status_bar()
        if self._resume_on_start:
            self.on_command_submit("/session")

        # Start background server status check (non-blocking). No status
        # message is added to the conversation — the status bar reflects it.
        async def background_startup_check():
            status = await self.agent.get_status()
            self.refresh_status_bar()
            logger.info(f"Server status online: {status['online']}")

        async def shutdown_watcher():
            """Cancel an in-flight generation once Ctrl+C requests shutdown."""
            await self.shutdown_event.wait()
            self.stop_generation()

        # Run all tasks
        try:
            async with asyncio.TaskGroup() as tg:
                startup_task = tg.create_task(background_startup_check())

                async def ui_loop():
                    await self.compositor.run()
                    # However the UI stopped (/exit, "exit", Ctrl+C), the rest
                    # stops too: the workers wait for the shutdown event, and a
                    # status probe of a slow server must not hold the exit.
                    self.shutdown_event.set()
                    startup_task.cancel()

                tg.create_task(ui_loop())
                tg.create_task(self.command_worker())
                # Recorded so ``_ensure_worker`` never starts a second worker:
                # two workers on one queue ran generations concurrently.
                self.worker_task = tg.create_task(self.agent_worker())
                tg.create_task(shutdown_watcher())
        except Exception:
            # On exception, cleanup without clearing screen to preserve traceback
            if self.compositor and self.compositor.terminal:
                self.compositor.terminal.cleanup(clear_screen=False)
            
            # Clean shutdown
            if hasattr(self.agent, 'stop'):
                self.agent.stop()
            
            # Re-raise to display traceback
            raise
        finally:
            # Normal exit cleanup (clear screen OK)
            if self.compositor and self.compositor.terminal:
                self.compositor.terminal.cleanup(clear_screen=True)
            self.save_session()
            
            # Clean shutdown
            if hasattr(self.agent, 'stop'):
                self.agent.stop()