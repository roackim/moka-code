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

from moka_code.harness import events
from moka_code.ui.chat_message import Message, thought_worth_showing
from moka_code.ui.tui.msg_types import (
    AskPermissionMsg,
    MsgType,
    AssistantMsg,
    SysMsg,
    SysMsgError,
    ThinkingMsg,
    ToolCallMsg,
)

logger = logging.getLogger("tui")


async def process_generation(app, user_input, user_msg, attached=()) -> None:
    """Process a single generation request, mapping events to messages.

    ``attached``: image references sent with the user message.
    """
    logger.info(f"Starting generation for user input: {user_input[:50]}...")

    chat = app.chat_history_panel
    agent = app.agent
    app.refresh_status_bar()

    if hasattr(app, "reset_stream_revealer"):
        app.reset_stream_revealer()
    smoothing = getattr(app, "stream_revealer", None) is not None

    # The wait-phase message: "processing" while context is ingested, then in
    # flight. It settles into a collapsed "thought for Xs" summary above the
    # answer when the model reasoned, and is removed when it did not.
    current_msg = None
    current_msg_type = None
    # The assistant text message for the current model response. Content may
    # resume after a tool-call delta (providers interleave them), so all of a
    # response's content must land in this one message rather than being sliced
    # around the tool line. Reset at each ``Start(assistant)``.
    response_text_msg = None
    # Every answer message of this generation, split into prose / code /
    # table segments once complete (see ``ChatHistoryPanel.split_answer``).
    answer_msgs = []
    current_harness_ids = []
    natural_done = False
    aborted_status = "cancelled"
    status_is_processing = False

    def ensure_tool_message_type(msg: Message, target_type: MsgType) -> Message:
        return chat.retype_tool_message(msg, target_type)

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

        A wait-phase message whose reasoning is too short to earn a line
        (``ui.thought_min_tokens``) was only a "waiting" indicator: it is
        removed rather than left as a near-empty "thought" line.
        """
        nonlocal current_msg, current_msg_type
        # Drain the revealer first, unconditionally: pending streamed text must
        # be released before the boundary even when the current message was
        # already handed off (otherwise its last chunk surfaces only later).
        flush_text()
        if current_msg is None:
            return
        if isinstance(current_msg.type, ThinkingMsg) and not thought_worth_showing(current_msg.base_text):
            chat.remove_message(current_msg)
        else:
            current_msg.finalize()
            current_msg.update_actions()
        current_msg = None
        current_msg_type = None

    def finalize_active_tools(status: str) -> None:
        """Close any tool message left in flight when generation ends.

        A dropped stream or a cancel can end a generation between a tool draft
        and its result; without this the line would stay live forever.
        """
        for tool_id in list(app.active_tool_messages):
            msg = app.active_tool_messages.get(tool_id)
            if msg is None:
                continue
            msg = ensure_tool_message_type(msg, ToolCallMsg())
            if msg.tool_state() in ("drafting", "running"):
                msg.set_tool_status(status)
            msg.finalize()
            app.active_tool_messages.pop(tool_id, None)

    # Context ingestion happens before the first harness event, so open the
    # placeholder now: otherwise the UI looks frozen during that work.
    current_msg = chat.add_message("", msg_type=ThinkingMsg())
    current_msg.set_collapsed(True)
    current_msg.begin_phase("processing")
    current_msg_type = ThinkingMsg
    status_is_processing = True
    begin_text_message(current_msg)

    # Process streaming events from Harness
    stream = agent.chat(user_input, list(attached))
    try:
        async for event in stream:
            if isinstance(event, events.Start):
                current_harness_ids = [event.message_id]
                logger.debug(f"Start: {event.role} with ID {event.message_id}")

                if event.role == "user":
                    # Link the user message that was passed through the queue
                    if not user_msg.harness_message_ids:
                        user_msg.harness_message_ids = [event.message_id]
                        logger.debug(f"Linked user message to harness ID {event.message_id}")
                else:
                    # A new assistant response begins: its content is a fresh
                    # message even if the previous response was split by tools.
                    response_text_msg = None
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
                # All content of one assistant response belongs to a single
                # message. If content resumes after a tool-call draft opened
                # (providers interleave content and tool-call deltas), append to
                # that response's message rather than slicing a second one out
                # below the tool line.
                if current_msg_type != AssistantMsg:
                    if response_text_msg is None:
                        end_status_message()
                        response_text_msg = chat.add_message(
                            "", msg_type=AssistantMsg(), harness_message_ids=current_harness_ids
                        )
                        answer_msgs.append(response_text_msg)
                    current_msg = response_text_msg
                    current_msg_type = AssistantMsg
                    begin_text_message(current_msg)

                emit_text(event.text)

            elif isinstance(event, events.ToolCallDraft):
                # A tool call's arguments are still streaming: open (or refresh)
                # the tool line itself, which grows live (e.g. ``+12 lines``)
                # instead of hanging on the thinking message. The text above is finalized
                # now (fully revealed and styled) rather than when the whole
                # call has streamed; content resuming after the draft still
                # goes back into ``response_text_msg``.
                tool_id = event.id
                end_status_message()

                msg = app.active_tool_messages.get(tool_id)
                if not msg:
                    msg = chat.add_message(
                        "", msg_type=ToolCallMsg(), harness_message_ids=current_harness_ids
                    )
                    app.active_tool_messages[tool_id] = msg

                msg.tool_name = event.name or msg.tool_name
                msg.tool_args = event.args
                msg.set_tool_status("drafting")
                msg.rebuild_tool_display()
                # Deliberately do not take over current_msg: any content that
                # follows must go back to the message above this line.

            elif isinstance(event, events.ToolCall):
                tool_id = event.id
                msg = app.active_tool_messages.get(tool_id)
                preserve_active_text_stream = current_msg_type in (ThinkingMsg, AssistantMsg)

                # Drain any pending streamed text before showing the tool draft
                # (ordering beats smoothing), even if the text message was
                # already handed off.
                flush_text()

                # Flush any incomplete text message before showing tool draft
                if current_msg_type in (ThinkingMsg, AssistantMsg) and current_msg:
                    end_status_message()

                if not msg:
                    msg = chat.add_message("", msg_type=ToolCallMsg(), harness_message_ids=current_harness_ids)
                    app.active_tool_messages[tool_id] = msg

                msg = ensure_tool_message_type(msg, ToolCallMsg())
                app.active_tool_messages[tool_id] = msg
                msg.tool_name = event.name or msg.tool_name
                msg.tool_args = event.args
                # Complete, awaiting its permission decision (no clock yet).
                msg.tool_status = None
                msg.rebuild_tool_display()

                if not preserve_active_text_stream:
                    current_msg = msg
                    current_msg_type = type(msg.type)

            elif isinstance(event, events.PermissionRequest):
                tool_id = event.id

                # Drain any pending streamed text before showing the request.
                flush_text()

                # Flush any incomplete text message before showing tool request
                if current_msg_type in (ThinkingMsg, AssistantMsg) and current_msg:
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
                    msg.set_tool_status("running")
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

            elif isinstance(event, events.ToolOutput):
                # Interim output from a running tool (bash streaming): the last
                # lines show under the running line; the activity surface keeps
                # the full log.
                msg = app.active_tool_messages.get(event.id)
                if msg is not None:
                    msg.append_live_output(event.data)
                text = event.data.rstrip("\n")
                if text:
                    app.activity(f"[{event.name}:{event.stream}] {text}")

            elif isinstance(event, events.ToolResult):
                tool_id = event.id
                msg = app.active_tool_messages.get(tool_id)
                if msg:
                    msg = ensure_tool_message_type(msg, ToolCallMsg())
                    app.active_tool_messages[tool_id] = msg
                    msg.tool_name = event.name
                    # The outcome (denied / error reason / exit code) is on the
                    # collapsed line; the full output stays behind ``o``.
                    msg.tool_output = event.output
                    msg.set_tool_status(
                        event.outcome if event.outcome in ("completed", "denied") else "error"
                    )
                    msg.finalize()
                    del app.active_tool_messages[tool_id]
                app.pending_permission_prompt = None

            elif isinstance(event, events.Usage):
                # Update message metrics (for live display in footer).
                # Only update for thinking/content messages, not tool messages;
                # while a tool call drafts, the response's text message keeps
                # receiving them.
                metrics_msg = (
                    current_msg if current_msg_type in (ThinkingMsg, AssistantMsg)
                    else response_text_msg
                )
                if metrics_msg is not None:
                    metrics_msg.update_metrics(
                        tokens=event.tokens,
                        tokens_per_second=event.tokens_per_second,
                        ttft_ms=event.ttft_ms,
                        duration_ms=event.duration_ms,
                    )
                app.refresh_status_bar()

            elif isinstance(event, events.Error):
                aborted_status = "error"
                end_status_message()
                if smoothing:
                    app.disengage_stream()
                chat.add_message(event.message, msg_type=SysMsgError())

            elif isinstance(event, events.Done):
                # Let the revealer drain over its remaining window; the frame
                # callback finalizes once it is empty. The wait-phase message
                # stays and reads "thought for Xs".
                if (current_msg_type is ThinkingMsg
                        and current_msg is not None
                        and not thought_worth_showing(current_msg.base_text)):
                    # Nothing worth a line was reasoned: drop it now.
                    end_status_message()
                elif smoothing and current_msg_type in (ThinkingMsg, AssistantMsg):
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
        # Close the harness stream now (not at garbage collection) so it can
        # stop its running tool and repair history before the next turn.
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()
        # Finalize current message and add a plain SysMsg notification.
        # Avoid appending ANSI codes to a MarkdownComponent message (AssistantMsg)
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
        finalize_active_tools(aborted_status)
        # A stopped permission prompt must not keep blocking user input.
        app.pending_permission_prompt = None
        if not natural_done or not smoothing:
            end_status_message()
            if smoothing:
                app.disengage_stream()
        # Complete answers split now; one still revealing splits when the
        # frame callback finalizes it (``chatTUI._finalize_stream``).
        for answer in answer_msgs:
            if answer.finalized:
                chat.split_answer(answer)
