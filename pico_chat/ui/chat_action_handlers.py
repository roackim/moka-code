"""Action handlers for chat messages."""

import logging
from pico_chat.ui.clipboard import copy_to_clipboard
from pico_chat.ui.tui.layout_utils import strip_ansi
from pico_chat.ui.tui.msg_types import SysMsgError


class ChatActionHandlers:
    """Mixin class providing action handlers for chat messages."""
    
    def handle_copy_action(self, message):
        """Handle copy action for a focused message."""
        logger = logging.getLogger("tui")
        
        try:
            # For tool call messages, build comprehensive debug output
            from pico_chat.ui.tui.msg_types import ToolCallMsg, AskPermissionMsg
            if isinstance(message.type, (ToolCallMsg, AskPermissionMsg)) and message.tool_name:
                import json
                
                # Build detailed tool call output
                lines = [
                    "=" * 60,
                    f"TOOL CALL: {message.tool_name}",
                    "=" * 60,
                    "",
                    "ARGUMENTS:",
                    "-" * 60,
                ]
                
                # Pretty-print arguments
                if message.tool_args:
                    try:
                        args_dict = json.loads(message.tool_args)
                        lines.append(json.dumps(args_dict, indent=2))
                    except:
                        lines.append(message.tool_args)
                else:
                    lines.append("(no arguments)")
                
                lines.extend(["", "STATUS:", "-" * 60])
                lines.append(message.tool_state())
                
                # Include output if available
                if message.tool_output:
                    lines.extend(["", "OUTPUT:", "-" * 60, message.tool_output])
                else:
                    lines.extend(["", "OUTPUT:", "-" * 60, "(no output yet)"])
                
                lines.append("")
                lines.append("=" * 60)
                
                text_to_copy = "\n".join(lines)
            else:
                # Strip ANSI escape codes for clean clipboard content
                text_to_copy = strip_ansi(message.base_text)
                # Ensure a trailing newline so pasting keeps the line break.
                if text_to_copy and not text_to_copy.endswith("\n"):
                    text_to_copy += "\n"
            
            # Native helpers first, then OSC 52 (works over SSH).
            method = copy_to_clipboard(text_to_copy)
            if method:
                self._copy_feedback(method)
            else:
                logger.warning("No clipboard method succeeded")
                self.chat_history_panel.add_message(
                    "Could not copy: no clipboard method found\n"
                    "Install xclip, xsel or wl-copy, or use a terminal that "
                    "supports OSC 52",
                    msg_type=SysMsgError()
                )
            
        except Exception as e:
            logger.error(f"Error copying to clipboard: {e}")
            self.chat_history_panel.add_message(f"Copy failed: {e}", msg_type=SysMsgError())
    
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
        
        from pico_chat.ui.tui.msg_types import AskPermissionMsg
        from pico_chat.ui.tui.colors import theme
        
        # Check if this is a permission request message
        if isinstance(message.type, AskPermissionMsg):
            # Send approval to harness
            logger.info("Sending approve to harness")
            self.agent.set_user_response("approve")
            self.pending_permission_prompt = None

            # The ask becomes the running tool line (no "approve?" left while
            # it executes); keep the id mapping so its result lands on it.
            from pico_chat.ui.tui.msg_types import ToolCallMsg

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
        
        from pico_chat.ui.tui.msg_types import AskPermissionMsg
        from pico_chat.ui.tui.colors import theme
        
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
        
        from pico_chat.ui.tui.msg_types import ToolCallMsg, AskPermissionMsg
        
        # Check if this is a tool message
        if isinstance(message.type, (ToolCallMsg, AskPermissionMsg)):
            # Toggle output visibility
            message.show_output = not message.show_output
            message.rebuild_tool_display()
            logger.info(f"Tool output {'shown' if message.show_output else 'hidden'}")
        else:
            logger.warning("Output action called on non-tool message")

