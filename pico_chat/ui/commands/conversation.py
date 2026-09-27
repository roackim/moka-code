"""Conversation import and export commands."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .base import ChatUIProtocol
from pico_chat.ui.chat_message import thought_worth_showing
from pico_chat.ui.tui.msg_types import (
    PicoMsg,
    SysMsg,
    SysMsgError,
    SysMsgWarning,
    ThinkingMsg,
    ToolCallMsg,
    UserMsg,
)


def json_file_completions() -> List[str]:
    """List ``.json`` files in the current directory for fuzzy autocomplete."""
    try:
        return sorted(
            entry.name for entry in os.scandir(".")
            if entry.is_file() and entry.name.endswith(".json")
        )
    except OSError:
        return []


async def conversation_export(ui: ChatUIProtocol, args: List[str]):
    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /export <filename>", msg_type=SysMsgError())
        return

    filename = args[0]
    if not filename.endswith(".json"):
        filename += ".json"

    try:
        history = ui.agent.history
        if not history:
            ui.chat_history_panel.add_message(
                "No conversation history to export.", msg_type=SysMsgError())
            return

        active_role = getattr(getattr(ui.agent, "role", None), "name", "agent")
        with open(filename, "w", encoding="utf-8") as stream:
            json.dump({"role": active_role, "history": history}, stream,
                      indent=2, ensure_ascii=False)
        ui.chat_history_panel.add_message(
            f"Conversation exported to {filename}\n({len(history)} messages)",
            msg_type=SysMsg(), title="conversation")
    except Exception as exc:
        ui.chat_history_panel.add_message(
            f"Export failed: {exc}", msg_type=SysMsgError(), title="conversation")


async def conversation_import(ui: ChatUIProtocol, args: List[str]):
    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /import <filename>", msg_type=SysMsgError())
        return

    filename = args[0]
    if not filename.endswith(".json"):
        filename += ".json"

    try:
        with open(filename, "r", encoding="utf-8") as stream:
            data = json.load(stream)

        role_name = data.get("role") if isinstance(data, dict) else None
        history = data.get("history") if isinstance(data, dict) else data
        if not isinstance(history, list):
            ui.chat_history_panel.add_message(
                "Invalid conversation file: expected a history array or conversation object",
                msg_type=SysMsgError(), title="conversation")
            return
        if role_name is not None and not isinstance(role_name, str):
            ui.chat_history_panel.add_message(
                "Invalid conversation file: role must be a string",
                msg_type=SysMsgError(), title="conversation")
            return
        for index, message in enumerate(history):
            if not isinstance(message, dict):
                ui.chat_history_panel.add_message(
                    f"Invalid message at index {index}: expected an object",
                    msg_type=SysMsgError(), title="conversation")
                return
            if "role" not in message:
                ui.chat_history_panel.add_message(
                    f"Invalid message at index {index}: missing 'role' field",
                    msg_type=SysMsgError(), title="conversation")
                return

        # Apply the saved role, warning if it no longer exists.
        role_warning = None
        if role_name:
            from pico_chat.harness import roles
            try:
                role = roles.load_role(role_name)
            except KeyError:
                role = roles.agent_role()
                role_warning = (
                    f"Role '{role_name}' no longer exists — defaulted to 'agent'."
                )
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
        ui.chat_history_panel.add_message(
            f"Conversation imported from {filename}\n({len(history)} messages)",
            msg_type=SysMsg(), title="conversation")
    except FileNotFoundError:
        ui.chat_history_panel.add_message(
            f"File not found: {filename}", msg_type=SysMsgError(), title="conversation")
    except json.JSONDecodeError as exc:
        ui.chat_history_panel.add_message(
            f"Invalid JSON file: {exc}", msg_type=SysMsgError(), title="conversation")
    except Exception as exc:
        ui.chat_history_panel.add_message(
            f"Import failed: {exc}", msg_type=SysMsgError(), title="conversation")


def _add_answer(ui: ChatUIProtocol, text: str, ids) -> None:
    """Add a restored answer, split into segments like a live one."""
    answer = ui.chat_history_panel.add_message(text, msg_type=PicoMsg(), harness_message_ids=ids)
    answer.finalize()
    split = getattr(ui.chat_history_panel, "split_answer", None)
    if callable(split):
        split(answer)


def _rebuild_ui_from_history(ui: ChatUIProtocol, history: List[Dict[str, Any]]):
    """Reconstruct visible messages from imported harness history."""
    from pico_chat.harness.thinking_parser import ThinkingTagParser

    for message in history:
        role = message.get("role", "")
        content = message.get("content", "")
        message_id = message.get("id", "")
        ids = [message_id] if message_id else None

        if role == "user":
            ui.chat_history_panel.add_message(content or "", msg_type=UserMsg(),
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
                # Older exports (or preserve_reasoning_traces): reasoning is
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
    "conversation_export", "conversation_import", "json_file_completions",
]
