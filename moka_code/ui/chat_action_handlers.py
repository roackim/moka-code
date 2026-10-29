"""Action handlers for chat messages."""

import logging
from moka_code.ui.clipboard import copy_to_clipboard
from moka_code.ui.tui.layout_utils import strip_ansi
from moka_code.ui.tui.msg_types import SysMsgError


class ChatActionHandlers:
    """Mixin class providing action handlers for chat messages."""
    
    def handle_copy_action(self, message):
        """Handle copy action for a focused message."""
        logger = logging.getLogger("tui")
        
        try:
            from moka_code.ui.chat_message import tool_terminal_text
            from moka_code.ui.tui.msg_types import ToolCallMsg, AskPermissionMsg

            panel = self.chat_history_panel
            # A single (unsplit) tool call copies command + raw output like a
            # terminal. Split groups and their segments go through
            # ``copy_text_for``: whole group = both, inside = one part.
            is_unsplit_tool = (
                getattr(message, "group", None) is None
                and isinstance(message.type, (ToolCallMsg, AskPermissionMsg))
                and getattr(message, "tool_name", None)
            )
            if is_unsplit_tool:
                # Hidden output is not copied: command only. With output shown
                # inline (e.g. while running), copy the terminal transcript.
                if getattr(message, "show_output", False):
                    text = tool_terminal_text(message)
                else:
                    from moka_code.ui.chat_message import tool_command_text
                    text = tool_command_text(message)
            else:
                copy_text_for = getattr(panel, "copy_text_for", None)
                text = copy_text_for(message) if callable(copy_text_for) else message.base_text
            text_to_copy = strip_ansi(text)
            # Ensure a trailing newline so pasting keeps the line break.
            if text_to_copy and not text_to_copy.endswith("\n"):
                text_to_copy += "\n"

            self.copy_text(text_to_copy)
        except Exception as e:
            logger.error(f"Error copying to clipboard: {e}")
            self.chat_history_panel.add_message(f"Copy failed: {e}", msg_type=SysMsgError())

    def copy_text(self, text: str) -> None:
        """Copy *text* (a message, or a mouse selection) and confirm it."""
        # Native helpers first, then OSC 52 (works over SSH).
        method = copy_to_clipboard(text)
        if method:
            self._copy_feedback(method)
        else:
            logging.getLogger("tui").warning("No clipboard method succeeded")
            self.chat_history_panel.add_message(
                "Could not copy: no clipboard method found\n"
                "Install xclip, xsel or wl-copy, or use a terminal that "
                "supports OSC 52",
                msg_type=SysMsgError()
            )

    def _copy_feedback(self, method: str | None = None):
        """Confirm a copy without disturbing the action mode line.

        Native helpers report success reliably; OSC 52 is fire-and-forget —
        the terminal may silently ignore it (e.g. VTE/Ptyxis) — so it gets
        distinct wording instead of a false "copied" checkmark.
        """
        hint = "sent via OSC 52" if method == "OSC 52" else "copied ✓"
        panel = getattr(self, "chat_history_panel", None)
        selected = panel is not None and panel.focused_message_index is not None
        flash = getattr(self, "flash_hint", None)
        if selected and callable(flash):
            flash(hint)
        elif hasattr(self, "notify"):
            self.notify("Copied to clipboard")

    def handle_allow_action(self, message):
        """Handle allow action for tool permission requests."""
        logger = logging.getLogger("tui")
        logger.info("Allow action triggered")
        
        from moka_code.ui.tui.msg_types import AskPermissionMsg
        from moka_code.ui.tui.colors import theme
        
        # Check if this is a permission request message
        if isinstance(message.type, AskPermissionMsg):
            # Send approval to harness
            logger.info("Sending approve to harness")
            self.agent.set_user_response("approve")
            self.pending_permission_prompt = None

            # The ask becomes the running tool line (no "approve?" left while
            # it executes); keep the id mapping so its result lands on it.
            from moka_code.ui.tui.msg_types import ToolCallMsg

            running = self.chat_history_panel.retype_tool_message(message, ToolCallMsg())
            running.set_tool_status("running")
            running.rebuild_tool_display()
            for tool_id, active in list(self.active_tool_messages.items()):
                if active is message:
                    self.active_tool_messages[tool_id] = running

            # Focus input field
            self._last_focus_id = "input"
            self._update_focus_states()
            logger.info("Permission granted, waiting for tool execution")
        else:
            logger.warning("Allow action called on non-permission message")
    
    def handle_deny_action(self, message):
        """Handle deny action for tool permission requests."""
        logger = logging.getLogger("tui")
        logger.info("Deny action triggered")
        
        from moka_code.ui.tui.msg_types import AskPermissionMsg
        from moka_code.ui.tui.colors import theme
        
        # Check if this is a permission request message
        if isinstance(message.type, AskPermissionMsg):
            # Send denial to harness
            logger.info("Sending deny to harness")
            self.agent.set_user_response("deny")
            self.pending_permission_prompt = None
            
            # Focus input field
            self._last_focus_id = "input"
            self._update_focus_states()
            
            # Don't update the message - it will be replaced by ToolStart with DENIED status
            logger.info("Permission denied, waiting for tool result")
        else:
            logger.warning("Deny action called on non-permission message")
    
    def handle_output_action(self, message):
        """Handle output action for tool messages - toggle output visibility."""
        logger = logging.getLogger("tui")
        logger.info("Output toggle action triggered")
        
        from moka_code.ui.tui.msg_types import ToolCallMsg, AskPermissionMsg

        panel = self.chat_history_panel
        # An expanded tool call is a two-segment group: ``o`` collapses it.
        if getattr(message, "group", None) is not None:
            collapse = getattr(panel, "collapse_tool_group", None)
            if callable(collapse) and collapse(message):
                return

        # Check if this is a tool message
        if isinstance(message.type, (ToolCallMsg, AskPermissionMsg)):
            # Finished calls expand into command/output segments; running
            # calls keep the old inline toggle.
            split = getattr(panel, "split_tool_output", None)
            if callable(split) and split(message):
                return
            message.show_output = not message.show_output
            message.rebuild_tool_display()
            logger.info(f"Tool output {'shown' if message.show_output else 'hidden'}")
        else:
            logger.warning("Output action called on non-tool message")


    def handle_prompt_action(self, message):
        """``o`` on a ``!prompt`` message: unfold the prompt, or fold it back."""
        message.set_prompt_folded(not message.prompt_folded)
        panel = self.chat_history_panel
        if message in panel.messages:
            panel._scroll_to_show_message(panel.messages.index(message), prefer_top=True)

    def _unqueue(self, message) -> bool:
        """Take a waiting user message out of the queue (the worker skips it).
        False when it was already picked up."""
        if not getattr(message, "is_queued", False):
            return False
        message.cancelled = True
        message.set_queued(None)
        self.chat_history_panel.remove_message(message)
        self._renumber_queued()
        self._update_action_strip()
        return True

    def handle_cancel_queued_action(self, message):
        """Drop a queued message before it is sent."""
        if self._unqueue(message):
            self.flash_hint("queued message cancelled")

    def handle_edit_action(self, message):
        """Edit a user message: a queued one is unqueued, a sent one is cut back
        (with everything after it) so it can be sent again."""
        if getattr(message, "is_queued", False):
            self.handle_edit_queued_action(message)
        else:
            self.handle_edit_sent_action(message)

    def _fill_input(self, text: str, attached) -> None:
        """Put a message back in the input, replacing the draft."""
        for image in attached:
            self._pasted_images[image["n"]] = image
        self.input_component.buffer.text = text
        self.input_component.buffer.cursor_pos = len(text)
        self.input_component._on_text_changed()
        self._set_app_focus("input")

    def handle_edit_queued_action(self, message):
        """Unqueue a message and put its text back in the input, replacing the
        draft there, until the user sends it again."""
        draft = getattr(message, "draft", None)
        if draft is None or not self._unqueue(message):
            return
        self._fill_input(*draft)

    def handle_edit_sent_action(self, message):
        """Cut the conversation back to just before this user message and put
        it in the input. Sending it again continues from there; the messages
        after it are gone (the session file keeps them until the next save).

        A message that a compaction summarized takes that compaction with it
        (the summary covers what is cut), so the full history up to it is sent
        again: that is asked first."""
        if self.is_generating():
            self.flash_hint("stop the response first (esc)")
            return
        ids = message.harness_message_ids or []
        entry = next((e for e in self.agent.history if e.get("id") in ids), None)
        if entry is None:
            return
        estimate = getattr(self.agent, "uncompacted_estimate", None)
        tokens = estimate(entry["id"]) if callable(estimate) else None
        if tokens is None:
            self._cut_back(message, entry)
            return
        drop, cancel = "Drop compaction and edit", "Cancel"
        show = getattr(self, "show_search_modal", None)
        modal = show(f"This message was compacted: editing it drops the summary "
                     f"(~{tokens // 1000}k tokens sent again)", [drop, cancel],
                     on_accept=lambda choice: choice == drop and self._cut_back(message, entry),
                     ordered=True) if callable(show) else None
        if modal is None:
            self.flash_hint("this message was compacted; editing it needs the confirmation picker")

    def _cut_back(self, message, entry) -> None:
        """``handle_edit_sent_action``'s cut: history and transcript from
        ``entry``/``message`` on go, the text returns to the input. A compaction
        still in effect keeps its line, after what is left."""
        text, attached = getattr(message, "draft", None) or (
            entry.get("content") or "", list(entry.get("images") or ()))
        panel = self.chat_history_panel
        lines = panel.compaction_lines()
        self.agent.truncate_history(entry["id"])
        panel.truncate_from(message)
        kept = {e.get("id") for e in self.agent.history}
        for line in lines:
            marker_id = (line.harness_message_ids or [None])[0]
            if line not in panel.messages and marker_id in kept:
                panel.add_compaction(self.agent.history, marker_id)
        self._fill_input(text, attached)
