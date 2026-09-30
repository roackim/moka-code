"""Conversation commands: /export, /import and /session (saved sessions)."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from .base import ChatUIProtocol
from moka_code.ui.chat_message import thought_worth_showing
from moka_code.ui.tui.msg_types import (
    AssistantMsg,
    SysMsg,
    SysMsgError,
    SysMsgWarning,
    ThinkingMsg,
    ToolCallMsg,
)


def json_file_completions() -> List[str]:
    """List ``.json`` files in the current directory for fuzzy autocomplete."""
    try:
        return [entry.name for entry in os.scandir(".")
                if entry.is_file() and entry.name.endswith(".json")]
    except OSError:
        return []


def _role_name(ui: ChatUIProtocol) -> str:
    return getattr(getattr(ui.agent, "role", None), "name", "agent")


def _model_name(ui: ChatUIProtocol):
    return getattr(getattr(ui.agent, "endpoint", None), "selected_model", None)


async def conversation_export(ui: ChatUIProtocol, args: List[str]):
    from moka_code.harness import images, sessions

    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /export <filename>", msg_type=SysMsgError())
        return

    filename = args[0]
    if not filename.endswith(".json"):
        filename += ".json"

    history = ui.agent.history
    if not history:
        ui.chat_history_panel.add_message(
            "No conversation history to export.", msg_type=SysMsgError())
        return
    try:
        # Images are embedded (base64) so the file is self-contained.
        sessions.write(filename, _role_name(ui), images.embed(history), _model_name(ui))
    except Exception as exc:
        ui.chat_history_panel.add_message(
            f"Export failed: {exc}", msg_type=SysMsgError(), title="conversation")
        return
    ui.chat_history_panel.add_message(
        f"Conversation exported to {filename}\n({len(history)} messages)",
        msg_type=SysMsg(), title="conversation")


async def conversation_import(ui: ChatUIProtocol, args: List[str]):
    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /import <filename>", msg_type=SysMsgError())
        return

    filename = args[0]
    if not filename.endswith(".json"):
        filename += ".json"
    if load_conversation(ui, filename):
        # An imported conversation continues as a new session.
        new_session = getattr(ui, "new_session", None)
        if callable(new_session):
            new_session()
        ui.chat_history_panel.add_message(
            f"Conversation imported from {filename}\n({len(ui.agent.history)} messages)",
            msg_type=SysMsg(), title="conversation")


def load_conversation(ui: ChatUIProtocol, path) -> bool:
    """Replace the conversation with the one saved at ``path`` (``/import``,
    ``/session``). Reports a failure in the transcript and returns False."""
    from moka_code.harness import images, roles, sessions

    try:
        role_name, history = sessions.read(path)
    except FileNotFoundError:
        ui.chat_history_panel.add_message(
            f"File not found: {path}", msg_type=SysMsgError(), title="conversation")
        return False
    except json.JSONDecodeError as exc:
        ui.chat_history_panel.add_message(
            f"Invalid JSON file: {exc}", msg_type=SysMsgError(), title="conversation")
        return False
    except (ValueError, OSError) as exc:
        ui.chat_history_panel.add_message(
            f"Invalid conversation file: {exc}", msg_type=SysMsgError(), title="conversation")
        return False

    # Embedded images go back to the image cache.
    history = images.restore(history)

    # Apply the saved role, warning if it no longer exists.
    role_warning = None
    if role_name:
        try:
            role = roles.load_role(role_name)
        except KeyError:
            role = roles.agent_role()
            role_warning = f"Role '{role_name}' no longer exists — defaulted to 'agent'."
        ui.switch_role(role)

    load = getattr(ui.agent, "load_history", None)
    if callable(load):
        load(history)  # also starts the conversation's cost over
    else:
        ui.agent.history = history
    ui.chat_history_panel.clear()
    _rebuild_ui_from_history(ui, history)
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()
    if role_warning:
        ui.chat_history_panel.add_message(
            role_warning, msg_type=SysMsgWarning(), title="conversation")
    return True


def _ago(timestamp: float) -> str:
    seconds = max(0, time.time() - timestamp)
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


async def conversation_session(ui: ChatUIProtocol, args: List[str]):
    """Pick a saved session of this project; Enter resumes it."""
    from moka_code.harness import sessions

    if getattr(ui, "is_generating", lambda: False)():
        ui.chat_history_panel.add_message(
            "A response is in progress — stop it first (/stop).", msg_type=SysMsgWarning())
        return
    workspace = getattr(ui.agent, "workspace", ".")
    current = getattr(ui, "session_path", None)
    found = [s for s in sessions.list_sessions(workspace) if s.path != current]
    if not found:
        ui.chat_history_panel.add_message(
            f"No saved sessions for this project yet ({sessions.sessions_dir(workspace)}).",
            msg_type=SysMsg(), title="session")
        return

    by_item, descriptions = {}, {}
    for session in found:
        item = session.title[:70]
        while item in by_item:  # the picker needs distinct rows
            item += " "
        by_item[item] = session
        details = [_ago(session.modified), f"{session.messages} messages"]
        if session.model:
            details.append(session.model)
        descriptions[item] = " · ".join(details)

    def _accept(item):
        session = by_item.get(item)
        if session is not None and load_conversation(ui, session.path):
            # Resuming continues that session's file (no duplicate).
            ui.session_path = session.path
            ui.chat_history_panel.add_message(
                f"Resumed session: {session.title[:70]}", msg_type=SysMsg(), title="session")

    show = getattr(ui, "show_search_modal", None)
    if show is not None:
        # Newest first (list_sessions' order).
        show("Sessions", list(by_item), descriptions=descriptions, on_accept=_accept,
             ordered=True)


def _add_answer(ui: ChatUIProtocol, text: str, ids) -> None:
    """Add a restored answer, split into segments like a live one."""
    answer = ui.chat_history_panel.add_message(text, msg_type=AssistantMsg(), harness_message_ids=ids)
    answer.finalize()
    split = getattr(ui.chat_history_panel, "split_answer", None)
    if callable(split):
        split(answer)


def _rebuild_ui_from_history(ui: ChatUIProtocol, history: List[Dict[str, Any]]):
    """Reconstruct visible messages from imported harness history."""
    from moka_code.harness.thinking_parser import ThinkingTagParser

    for message in history:
        role = message.get("role", "")
        content = message.get("content", "")
        message_id = message.get("id", "")
        ids = [message_id] if message_id else None

        if role == "user" and message.get("source") == "tool":
            continue  # images returned by read: the read line already shows them
        if role == "user":
            ui.chat_history_panel.add_user_message(content or "", message.get("images") or (),
                                                   harness_message_ids=ids)
        elif role == "assistant":
            # content may be None for tool-call-only assistant messages.
            content = content or ""
            reasoning = message.get("reasoning")
            if reasoning:
                # Current format: reasoning is stored verbatim in its own
                # field, so restore it exactly and keep the answer separate.
                # Short reasoning gets no line, as during generation.
                if thought_worth_showing(reasoning):
                    think = ui.chat_history_panel.add_message(
                        reasoning, msg_type=ThinkingMsg(), harness_message_ids=ids)
                    think.set_collapsed(True)
                    think.finalize()
                if content:
                    _add_answer(ui, content, ids)
            else:
                # Older exports: reasoning is
                # inline in content as thinking tags. Split it with the same
                # parser the harness uses so it renders as a ThinkingMsg.
                parser = ThinkingTagParser()
                raw_segments = parser.feed(content) + parser.flush()
                # ``feed`` may hold back a partial thinking tag (and ``flush``
                # emits it as a separate segment once the stream ends). Coalesce
                # adjacent same-kind segments so import does not split one
                # assistant reply into multiple messages (which showed up as a
                # mid-word split separated by the inter-message gap, e.g.
                # "narro" / "w it down.").
                segments = []
                for segment in raw_segments:
                    if not segment.text:
                        continue
                    if segments and segments[-1].is_thinking == segment.is_thinking:
                        segments[-1].text += segment.text
                    else:
                        segments.append(segment)
                for segment in segments:
                    if segment.is_thinking:
                        think = ui.chat_history_panel.add_message(
                            segment.text, msg_type=ThinkingMsg(), harness_message_ids=ids)
                        think.set_collapsed(True)
                        think.finalize()
                    else:
                        _add_answer(ui, segment.text, ids)
                if not segments and content:
                    _add_answer(ui, content, ids)
            for tool_call in message.get("tool_calls", []):
                if not isinstance(tool_call, dict) or "function" not in tool_call:
                    continue
                function = tool_call["function"]
                tool_call_id = tool_call.get("id", "")
                tool_message = ui.chat_history_panel.add_message(
                    "", msg_type=ToolCallMsg(),
                    harness_message_ids=[tool_call_id] if tool_call_id else None)
                tool_message.tool_name = function.get("name", "unknown")
                tool_message.tool_args = function.get("arguments", "{}")
                tool_message.tool_status = "completed"
                tool_message.show_output = False
                tool_message.rebuild_tool_display()
                tool_message.finalize()
        elif role == "tool" and content:
            tool_call_id = message.get("tool_call_id", "")
            found = False
            for existing in reversed(ui.chat_history_panel.messages):
                if (isinstance(existing.type, ToolCallMsg)
                        and tool_call_id
                        and tool_call_id in existing.harness_message_ids):
                    existing.tool_output = content
                    existing.tool_status = "completed"
                    existing.show_output = False
                    existing.rebuild_tool_display()
                    found = True
                    break
            if not found:
                ui.chat_history_panel.add_message(
                    f"Tool result: {content[:200]}{'...' if len(content) > 200 else ''}",
                    msg_type=SysMsg())


__all__ = [
    "conversation_export", "conversation_import", "conversation_session",
    "json_file_completions", "load_conversation",
]
