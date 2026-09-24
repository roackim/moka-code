"""Maps harness generation events onto transcript messages.

``process_generation`` drives one ``agent.chat()`` stream and translates each
harness event into UI mutations on the app. It lives outside ``app.py`` so the
event→UI mapping can be read on its own.

Streamed text (Token/Reasoning) is routed through the app's revealer when
smoothing is enabled: the message stores the canonical arrived text via
``ingest`` while the revealer paces how much of it is rendered. Hard boundaries
(tool calls, permission requests, errors, reasoning↔content switches) flush the
revealer synchronously before the next block so ordering always wins.
"""
from __future__ import annotations

import asyncio
import logging

from pico_chat.harness import events
from pico_chat.ui.chat_message import Message
from pico_chat.ui.tui.msg_types import (
    AskPermissionMsg,
    MsgType,
    PicoMsg,
    SysMsg,
    SysMsgError,
    ThinkingMsg,
    ToolCallMsg,
    ToolDraftMsg,
)


logger = logging.getLogger("tui")


async def process_generation(app, user_input, user_msg) -> None:
    """Process a single generation request, mapping events to messages."""
    logger.info(f"Starting generation for user input: {user_input[:50]}...")

    chat = app.chat_history_panel
    agent = app.agent
    app.refresh_status_bar()

    if hasattr(app, "reset_stream_revealer"):
        app.reset_stream_revealer()
    smoothing = getattr(app, "stream_revealer", None) is not None

    # The wait-phase message: "processing" while context is ingested, then
    # "thinking" once the request is in flight. It is kept and settles into a
    # collapsed "thought for Xs" summary above the answer.
    current_msg = None
    current_msg_type = None
    current_harness_ids = []
    natural_done = False
    status_is_processing = False

    def ensure_tool_message_type(msg: Message, target_type: MsgType) -> Message:
        if isinstance(msg.type, type(target_type)):
            return msg
        new_msg = chat.new_message(
            "",
            msg_type=target_type,
            harness_message_ids=msg.harness_message_ids or current_harness_ids,
        )
        new_msg.tool_name = msg.tool_name
        new_msg.tool_args = msg.tool_args
        new_msg.tool_output = msg.tool_output
        new_msg.tool_status = msg.tool_status
        new_msg.show_output = msg.show_output
        chat.replace_message(msg, new_msg)
        return new_msg

    def begin_text_message(msg: Message) -> None:
        if smoothing:
            app.start_stream_message(msg)

    def emit_text(text: str) -> None:
        if smoothing:
            current_msg.ingest(text)
            app.stream_ingest(text)
        else:
            current_msg.append(text)

    def flush_text() -> None:
        if smoothing:
            app.flush_stream()

    def end_status_message() -> None:
        """Finalize the current text/status message at a hard boundary.

        The wait-phase message is always kept: it settles into a collapsed
        "thought for Xs" summary above the answer, even when the model exposed
        no reasoning.
        """
        nonlocal current_msg, current_msg_type
        # Drain the revealer first, unconditionally: pending streamed text must
        # be released before the boundary even when the current message was
        # already handed off (otherwise its last chunk surfaces only later).
        flush_text()
        if current_msg is None:
            return
        current_msg.finalize()
        current_msg.update_actions()
        current_msg = None
        current_msg_type = None

    # Context ingestion happens before the first harness event, so open the
    # placeholder now: otherwise the UI looks frozen during that work.
    current_msg = chat.add_message("", msg_type=ThinkingMsg())
    current_msg.set_collapsed(True)
    current_msg.begin_phase("processing")
    current_msg_type = ThinkingMsg
    status_is_processing = True
    begin_text_message(current_msg)

    # Process streaming events from Harness
    try:
        async for event in agent.chat(user_input):
            if isinstance(event, events.Start):
                current_harness_ids = [event.message_id]
                logger.debug(f"Start: {event.role} with ID {event.message_id}")

                if event.role == "user":
                    # Link the user message that was passed through the queue
                    if not user_msg.harness_message_ids:
                        user_msg.harness_message_ids = [event.message_id]
                        logger.debug(f"Linked user message to harness ID {event.message_id}")
                else:
                    if status_is_processing and current_msg_type is ThinkingMsg:
                        # Context is ingested and the request is in flight.
                        status_is_processing = False
                        current_msg.harness_message_ids = current_harness_ids
                        current_msg.begin_phase("thinking")
                    else:
                        # A later turn (after tool calls) opens a fresh one.
                        end_status_message()
                        current_msg = chat.add_message(
                            "", msg_type=ThinkingMsg(), harness_message_ids=current_harness_ids
                        )
                        current_msg.set_collapsed(True)
                        current_msg.begin_phase("thinking")
                        current_msg_type = ThinkingMsg
                        begin_text_message(current_msg)

            elif isinstance(event, events.Reasoning):
                # If not currently in a thinking message, create one
                if current_msg_type != ThinkingMsg:
                    end_status_message()
                    current_msg = chat.add_message("", msg_type=ThinkingMsg(), harness_message_ids=current_harness_ids)
                    # Thinking folds to a single line by default; expand on focus.
                    current_msg.set_collapsed(True)
                    current_msg_type = ThinkingMsg
                    begin_text_message(current_msg)

                current_msg.begin_phase("thinking")
                emit_text(event.text)

            elif isinstance(event, events.Token):
                # If not currently in a content message, create one
                if current_msg_type != PicoMsg:
                    end_status_message()
                    current_msg = chat.add_message("", msg_type=PicoMsg(), harness_message_ids=current_harness_ids)
                    current_msg_type = PicoMsg
                    begin_text_message(current_msg)

                emit_text(event.text)

            elif isinstance(event, events.ToolCall):
                tool_id = event.id
                msg = app.active_tool_messages.get(tool_id)
                preserve_active_text_stream = current_msg_type in (ThinkingMsg, PicoMsg)

                # Drain any pending streamed text before showing the tool draft
                # (ordering beats smoothing), even if the text message was
                # already handed off.
                flush_text()

                # Flush any incomplete text message before showing tool draft
                if current_msg_type in (ThinkingMsg, PicoMsg) and current_msg:
                    end_status_message()

                if not msg:
                    msg = chat.add_message("", msg_type=ToolDraftMsg(), harness_message_ids=current_harness_ids)
                    app.active_tool_messages[tool_id] = msg

                msg = ensure_tool_message_type(msg, ToolDraftMsg())
                app.active_tool_messages[tool_id] = msg
                msg.tool_name = event.name or msg.tool_name
                msg.tool_args = event.args
                msg.tool_status = "drafting"
                msg.rebuild_tool_display()

                if not preserve_active_text_stream:
                    current_msg = msg
                    current_msg_type = type(msg.type)

            elif isinstance(event, events.PermissionRequest):
                tool_id = event.id

                # Drain any pending streamed text before showing the request.
                flush_text()

                # Flush any incomplete text message before showing tool request
                if current_msg_type in (ThinkingMsg, PicoMsg) and current_msg:
                    end_status_message()

                msg = app.active_tool_messages.get(tool_id)

                if event.auto:
                    # Auto-decision: show status marker
                    if not msg:
                        msg = chat.add_message(
                            "",  # Will be built by rebuild_tool_display
                            msg_type=ToolCallMsg(),
                            harness_message_ids=current_harness_ids,
                        )
                        app.active_tool_messages[tool_id] = msg

                    msg = ensure_tool_message_type(msg, ToolCallMsg())
                    app.active_tool_messages[tool_id] = msg
                    msg.tool_name = event.name
                    msg.tool_args = event.args
                    msg.tool_status = "auto-approved"
                    msg.rebuild_tool_display()
                    app.pending_permission_prompt = None
                else:
                    # Need user permission - show request
                    if not msg:
                        msg = chat.add_message(
                            "",
                            msg_type=AskPermissionMsg(),
                            harness_message_ids=current_harness_ids,
                        )
                        app.active_tool_messages[tool_id] = msg

                    msg = ensure_tool_message_type(msg, AskPermissionMsg())
                    app.active_tool_messages[tool_id] = msg
                    msg.tool_name = event.name
                    msg.tool_args = event.args
                    msg.tool_status = None
                    msg.rebuild_tool_display()

                    # Auto-focus for user action
                    try:
                        msg_index = chat.messages.index(msg)
                        chat.set_focused_message(msg_index)
                    except ValueError:
                        pass
                    app._set_app_focus("history")

                    # Force compositor render to show actions immediately
                    if app.compositor:
                        app.compositor.render()

                    # Store prompt for handler
                    app.pending_permission_prompt = event.prompt

                current_msg = msg
                current_msg_type = type(msg.type)

            elif isinstance(event, events.ToolResult):
                tool_id = event.id
                msg = app.active_tool_messages.get(tool_id)
                if msg:
                    msg = ensure_tool_message_type(msg, ToolCallMsg())
                    app.active_tool_messages[tool_id] = msg
                    msg.tool_name = event.name
                    if event.outcome == "completed":
                        msg.tool_status = "completed"
                        msg.tool_output = event.output
                    elif event.outcome == "denied":
                        msg.tool_status = "denied"
                        msg.tool_output = event.output
                        msg.show_output = True  # Always show denial reason
                    else:
                        msg.tool_status = "error"
                        msg.tool_output = event.output
                        msg.show_output = True  # Always show errors
                    msg.rebuild_tool_display()
                    msg.finalize()
                    del app.active_tool_messages[tool_id]
                app.pending_permission_prompt = None

            elif isinstance(event, events.Usage):
                # Update message metrics (for live display in footer).
                # Only update for thinking/content messages, not tool messages.
                if current_msg_type in (ThinkingMsg, PicoMsg):
                    current_msg.update_metrics(
                        tokens=event.tokens,
                        tokens_per_second=event.tokens_per_second,
                        ttft_ms=event.ttft_ms,
                        duration_ms=event.duration_ms,
                    )
                app.refresh_status_bar()

            elif isinstance(event, events.Error):
                end_status_message()
                if smoothing:
                    app.disengage_stream()
                chat.add_message(event.message, msg_type=SysMsgError())

            elif isinstance(event, events.Done):
                # Let the revealer drain over its remaining window; the frame
                # callback finalizes once it is empty. The wait-phase message
                # stays and reads "thought for Xs".
                if smoothing and current_msg_type in (ThinkingMsg, PicoMsg):
                    app.defer_stream_finalize()
                natural_done = True

            # Ensure we scroll to bottom if needed
            if chat.auto_scroll:
                chat.scroll_offset = 0
                # Auto-focus input when new messages arrive (if at bottom)
                # BUT: Don't steal focus if user has explicitly focused a message
                if app._last_focus_id != "input" and chat.focused_message_index is None:
                    app._set_app_focus("input")

            # Yield to let the compositor render the update
            await asyncio.sleep(0)

    except asyncio.CancelledError:
        # Finalize current message and add a plain SysMsg notification.
        # Avoid appending ANSI codes to a MarkdownComponent message (PicoMsg)
        # since the component would render the escape sequences as literal text.
        end_status_message()
        if smoothing:
            app.disengage_stream()
        chat.add_message("[Generation stopped]", msg_type=SysMsg())
        raise

    except Exception as e:
        raise e

    finally:
        # Backstop for torn-down/aborted streams. On a natural Done with
        # smoothing on, the frame callback owns finalization so the reveal can
        # finish animating; otherwise finalize now.
        if not natural_done or not smoothing:
            end_status_message()
            if smoothing:
                app.disengage_stream()
