import asyncio
import inspect
import json
import logging
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Any, Dict, List, Optional, Tuple

from pico_chat.harness.llm_status import AgentState
from pico_chat.harness.debug import get_debug_stream
from pico_chat.harness.context_builder import build_harness_context
from pico_chat.harness.elision import elide
from pico_chat.harness import events
from pico_chat.harness.endpoint import Endpoint, get_active_endpoint
from pico_chat.harness.permissions import PermissionGate
from pico_chat.harness.thinking_parser import ThinkingTagParser, MetricsState, THINKING_TAGS
from pico_chat.harness.usage import TokenUsage, usage_from_response

# Import the minimal toolset
from pico_chat.harness.tools import (
    InProcessTransport,
    MinimalToolset,
    ToolTransport,
    create_toolset,
)

import os

logger = logging.getLogger(__name__)


COMPACTION_MARKER_PREFIX = "[COMPACTION_SUMMARY]"

# Minimum wall-clock seconds between live ``ToolCallDraft`` updates for one
# call. The first named chunk always emits; later updates are time-throttled so
# a large streamed body (e.g. a file write) neither emits an event per delta
# nor freezes the draft line for seconds between size-based strides.
_TOOL_DRAFT_INTERVAL = 0.1

class Harness:
    def __init__(self, workspace_path: str | None = None,
                 transport: Optional[ToolTransport] = None):
        self.debug_stream = get_debug_stream()
        self.state = AgentState.IDLE
        self.history = []
        
        # Tools initialization with minimal toolset
        import os
        self.workspace = workspace_path or os.getcwd()

        from pico_chat.harness.roles import agent_role
        self.role = agent_role()

        # Permission gate turns the role's per-tool setting into a decision.
        self._permission_gate = PermissionGate(role=self.role)

        # The transport decides where tool bodies actually run. Bare mode
        # (default) uses an in-process transport over the workspace.
        self.transport: ToolTransport = transport or InProcessTransport(
            MinimalToolset(self.workspace)
        )
        self._wire_sandbox_stderr()
        self._rebuild_tools()
        self.debug_stream.log("TOOL_SCHEMAS", self.tool_schemas)

        # Build initial project context
        self.startup_warnings: list[str] = []
        # (tool name, task) while a tool runs; see _abort_tool_calls.
        self._running_tool = None
        self.project_context = build_harness_context(self.workspace)
        self.debug_stream.log("CONTEXT", "Project context built")
        # Cache for the @ file picker listing (invalidated on workspace change).
        self._file_list_cache: list[str] = []
        self._file_list_cache_key: tuple = ()

        # Select LLM endpoint at construction time. Resolving here (rather than
        # from a module-level snapshot) avoids stale server settings.
        self.endpoint: Endpoint = get_active_endpoint()
        self._last_usage: Optional[TokenUsage] = None
        self.debug_stream.log("INIT", f"Server initialized: {self.endpoint.name} ({self.endpoint.type}) at {self.endpoint.base_url}")

        # Steering / pause state
        # Updated live on every Thinking chunk so the UI can snapshot it.
        self._current_reasoning: str = ""
        # Set before a generation starts to prefill the assistant thinking block.
        self._pending_thinking_prefill: Optional[str] = None
        # Tracks which open tag the last generation actually used.
        self._last_detected_thinking_tag: Optional[str] = None

    def set_role(self, role) -> None:
        """Apply a role to this conversation before its next turn."""
        from pico_chat.harness.roles import Role

        if not isinstance(role, Role):
            raise TypeError("role must be a Role")
        previous_name = getattr(self, "role", role).name
        self.role = role
        self._permission_gate.set_role(role)
        self._rebuild_tools()
        self.debug_stream.log("ROLE", {"name": role.name, "tools": sorted(role.enabled_tool_names())})
        self._record_role_change(previous_name, role.name)

    def set_sandbox(self, spec) -> None:
        """Swap the execution transport (None/``none`` → in-process).

        Tears down the old worker (best effort, synchronously) and rebuilds the
        tool map against the new transport.  Role/permissions are unaffected.
        """
        old = getattr(self, "transport", None)
        close = getattr(old, "close", None)
        if callable(close):
            close()
        new = _build_transport(spec, self.workspace)
        self.transport = new if new is not None else InProcessTransport(
            MinimalToolset(self.workspace)
        )
        self._wire_sandbox_stderr()
        self._rebuild_tools()
        runtime = getattr(spec, "runtime", "none")
        self.debug_stream.log("SANDBOX", runtime)
        if getattr(self, "_sandbox_runtime", "none") != runtime:
            self._sandbox_runtime = runtime
            self._record_sandbox_change(runtime)

    def _record_sandbox_change(self, runtime: str) -> None:
        """Record a sandbox change as a system notice (not a user turn)."""
        content = f"[Sandbox: {runtime}]"
        if self.history and self.history[-1].get("content") == content:
            return
        self._add_message_to_history("system", content)

    def sandboxed(self) -> bool:
        """True when tools execute in a sandbox (container/bubblewrap)."""
        return bool(getattr(self.transport, "is_sandbox", False))

    def _wire_sandbox_stderr(self) -> None:
        """Route the sandbox worker's stderr to the debug stream."""
        process = getattr(self.transport, "process", None)
        if process is not None:
            process.on_stderr = lambda line: self.debug_stream.log("SANDBOX_STDERR", line)

    def sandbox_required(self) -> bool:
        """True when the active role needs a sandbox but none is active.

        While this holds the conversation is locked: the UI refuses normal
        submissions and ``chat()`` yields an error instead of running.
        """
        return bool(getattr(self.role, "require_sandbox", False)) and not self.sandboxed()

    def _rebuild_tools(self) -> None:
        """Build the tool map for the active role and cache its schemas."""
        enabled = self.role.enabled_tool_names()
        self.tools_map = {
            name: tool
            for name, tool in create_toolset(
                workspace_path=self.workspace, transport=self.transport
            ).items()
            if name in enabled
        }
        self.tool_schemas = (
            [tool.get_schema() for tool in self.tools_map.values()]
            if self.tools_map else None
        )

    def _record_role_change(self, previous_name: str, role_name: str) -> None:
        """Record a role change without stacking consecutive role notices."""
        if previous_name == role_name:
            return

        content = f"[Role changed from {previous_name} to {role_name}]"
        if (
            self.history
            and self.history[-1].get("role") == "system"
            and str(self.history[-1].get("content", "")).startswith("[Role changed from ")
        ):
            self.history[-1]["content"] = content
            return
        self._add_message_to_history("system", content)

    def switch_workspace(self, new_path: str) -> list[str]:
        """Change the workspace directory and rebuild project context.

        Returns a list of warning strings (may be empty).
        Raises ValueError if the path does not exist or is not a directory.
        """
        resolved = Path(new_path).expanduser().resolve()
        if not resolved.exists():
            raise ValueError(f"Path does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"Not a directory: {resolved}")

        self.workspace = str(resolved)
        os.chdir(self.workspace)

        # Rebuild project context and invalidate the @ file picker cache.
        self.project_context = build_harness_context(self.workspace)
        self._file_list_cache = []
        self._file_list_cache_key = ()
        self.debug_stream.log("WORKSPACE", f"Workspace changed to: {self.workspace}")

        # No git-repo warning: the file tree is built regardless of git status.
        return []

    def switch_server(self, new_endpoint: Endpoint) -> None:
        """Switch to a different LLM endpoint at runtime."""
        self.endpoint = new_endpoint
        self._last_usage = None
        self.debug_stream.log("SWITCH", f"Server switched to: {new_endpoint.name} ({new_endpoint.type}) at {new_endpoint.base_url}")
        logger.info("Switched to server: %s (%s)", new_endpoint.name, new_endpoint.type)

    def _is_compaction_message(self, msg: Dict[str, Any]) -> bool:
        """Return True if message is a compaction marker message."""
        if msg.get("role") != "assistant":
            return False
        content = msg.get("content")
        return isinstance(content, str) and content.startswith(COMPACTION_MARKER_PREFIX)

    def _get_last_compaction_index(self) -> Optional[int]:
        """Get index of the most recent compaction marker, if any."""
        for i in range(len(self.history) - 1, -1, -1):
            if self._is_compaction_message(self.history[i]):
                return i
        return None

    def _get_effective_history(self) -> List[Dict[str, Any]]:
        """Return history slice sent to the LLM, starting from latest compaction marker."""
        last_compaction_idx = self._get_last_compaction_index()
        if last_compaction_idx is None:
            return self.history
        return self.history[last_compaction_idx:]

    @staticmethod
    def _reasoning_tag_pair(tag: Optional[str]) -> Tuple[str, str]:
        """Resolve a stored reasoning tag to its ``(open, close)`` pair."""
        for open_tag, close_tag in THINKING_TAGS:
            if open_tag == tag:
                return open_tag, close_tag
        return THINKING_TAGS[0]

    def _to_api_message(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Project a stored history entry onto the API message shape.

        Stored history keeps ``reasoning``/``reasoning_tag`` for persistence and
        the transcript; they are not valid API fields. When
        ``preserve_reasoning_traces`` is enabled the reasoning is folded back
        into ``content`` using the model's own thinking tag so the model sees
        its prior chain-of-thought.
        """
        from pico_chat import pico_cfg

        msg = {key: value for key, value in entry.items()
               if key not in ("reasoning", "reasoning_tag")}
        reasoning = entry.get("reasoning")
        if reasoning and pico_cfg.config.preserve_reasoning_traces:
            open_tag, close_tag = self._reasoning_tag_pair(entry.get("reasoning_tag"))
            msg["content"] = f"{open_tag}\n{reasoning}\n{close_tag}\n\n{msg.get('content') or ''}"
        return msg

    def _get_tool_output(self, ref: str) -> Optional[str]:
        """
        Get a previous tool output by reference.
        
        Args:
            ref: Reference string (currently only "@" for last bash output)
            
        Returns:
            Tool output or None if not found
        """
        if ref == "@":
            # Get last bash output
            for name, result in reversed(self.tool_output_history):
                if name == "bash":
                    return result
            return None
        
        return None

    def _add_message_to_history(self, role: str, content: Optional[str], **kwargs) -> str:
        """Add a message to history with a unique ID.
        
        Args:
            role: Message role (user, assistant, tool)
            content: Message content
            **kwargs: Additional fields (tool_calls, tool_call_id, name, etc.)
            
        Returns:
            The generated message ID
        """
        msg_id = uuid.uuid4().hex[:8]  # 8-char short ID instead of full UUID
        msg = {
            "id": msg_id,
            "role": role,
            "content": content,
            **kwargs
        }
        self.history.append(msg)
        
        return msg_id

    def set_user_response(self, text: str):
        """Called by the UI when a response to a tool's prompt is ready."""
        self._permission_gate.set_user_response(text)

    def set_thinking_prefill(self, content: str):
        """Queue content to be prepended as the assistant <thinking> prefix on
        the next LLM call.  Subsequent calls overwrite any pending prefill."""
        self._pending_thinking_prefill = content

    def get_current_reasoning(self) -> str:
        """Return the reasoning accumulated so far in the active generation."""
        return self._current_reasoning

    def _abort_tool_calls(self, tool_calls_list: List[Dict[str, Any]]) -> None:
        """Clean up after a turn stopped during tool execution.

        - the running tool is killed (a bash command, or the sandbox worker so
          its orphaned request cannot interleave with the next one);
        - every call of the turn without a result gets a "cancelled" tool
          message, since an assistant ``tool_calls`` entry with no answer makes
          the next request invalid;
        - answers queued for a stopped permission prompt are dropped.
        """
        running = getattr(self, "_running_tool", None)
        if running is not None:
            name, task = running
            try:
                self.transport.cancel_active(name)
            except Exception:  # pragma: no cover - best effort
                logger.warning("Could not stop tool %s", name, exc_info=True)
            task.cancel()
            self._running_tool = None

        answered = {m.get("tool_call_id") for m in self.history if m.get("role") == "tool"}
        for tc in tool_calls_list:
            if tc["id"] not in answered:
                self._add_message_to_history(
                    role="tool",
                    content="[CANCELLED] The user stopped the turn before this tool call finished.",
                    tool_call_id=tc["id"],
                )
        self._permission_gate.clear_pending()
        self.state = AgentState.IDLE

    def stop_tool(self) -> bool:
        """Terminate the currently-running shell command (bash tool), if any.

        Returns True if a running command was stopped.
        """
        bash_tool = self.tools_map.get("bash")
        if bash_tool is not None:
            cancel = getattr(bash_tool, "cancel_active_run", None)
            if callable(cancel):
                return cancel()
        return False

    async def _wait_for_user_input(self, prompt: str) -> str:
        """Wait for the user to provide text via the UI."""
        return await self._permission_gate.wait_for_user_input(prompt)

    def _check_tool_permission(self, tool_name: str, args: dict) -> str:
        """Check tool permission status. Delegates to PermissionGate."""
        return self._permission_gate.check(tool_name, args)

    def _build_permission_prompt(self, tool_name: str, args: dict) -> str:
        """Build a human-readable permission prompt for a tool call."""
        return PermissionGate.build_prompt(tool_name, args)

    def get_state(self) -> AgentState:
        return self.state

    def clear_history(self):
        """Clear the conversation history for the agent."""
        self.history = []
        # Drop the provider-reported usage so the status bar no longer shows
        # the previous conversation's accumulated context after /clear.
        self._last_usage = None
        self.debug_stream.log("CLEAR", "Conversation history cleared")
    
    def delete_messages_after_id(self, message_id: str, inclusive: bool = True) -> bool:
        """Delete all messages after (and optionally including) the message with given ID.
        
        Args:
            message_id: The ID of the message to delete from
            inclusive: If True, delete the message with this ID too. If False, keep it.
            
        Returns:
            True if message was found and deletion occurred, False otherwise
        """
        for i, msg in enumerate(self.history):
            if msg.get("id") == message_id:
                # Delete messages
                if inclusive:
                    self.history = self.history[:i]
                else:
                    self.history = self.history[:i+1]
                
                self.debug_stream.log("DELETE_AFTER_ID", f"Deleted messages after ID {message_id} (inclusive={inclusive})")
                return True
        return False
    
    def get_message_by_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get a message by its ID.
        
        Args:
            message_id: The ID of the message to find
            
        Returns:
            The message dict if found, None otherwise
        """
        for msg in self.history:
            if msg.get("id") == message_id:
                return msg
        return None

    def list_files_and_folders(self) -> List[str]:
        """Returns a bounded list of files/folders for the @ file picker.

        Uses a depth/max-files-bounded walk so it stays responsive on huge
        trees (e.g. ``$HOME``). The result is cached per-workspace so typing
        ``@`` doesn't re-walk the tree on every keystroke.
        """
        from pico_chat.harness.context_builder import list_files_bounded
        from pico_chat import pico_cfg

        cache_key = (self.workspace, pico_cfg.config.context_max_files,
                     pico_cfg.config.context_max_depth,
                     pico_cfg.config.context_ignore_gitignore)
        if getattr(self, "_file_list_cache_key", None) == cache_key:
            return self._file_list_cache
        entries = list_files_bounded(
            self.workspace,
            max_files=pico_cfg.config.context_max_files,
            max_depth=pico_cfg.config.context_max_depth,
            ignore_gitignore=pico_cfg.config.context_ignore_gitignore,
        )
        self._file_list_cache = entries
        self._file_list_cache_key = cache_key
        return entries

    async def compact_history(self) -> Dict[str, Any]:
        """Summarize effective history with one LLM call and insert compaction marker.

        Returns:
            Summary stats describing the compaction operation.
        """
        effective_history = list(self._get_effective_history())
        if not effective_history:
            return {
                "ok": False,
                "reason": "empty",
                "message": "No messages to compact.",
            }

        # Avoid compacting if the effective history is already only one compaction marker.
        if len(effective_history) == 1 and self._is_compaction_message(effective_history[0]):
            return {
                "ok": False,
                "reason": "already_compacted",
                "message": "History is already compacted.",
            }

        summarize_user = {
            "role": "user",
            "content": (
                "Summarize the following conversation for future continuation. "
                "The summary will replace the conversation history, so it must be COMPREHENSIVE enough "
                "for the conversation to continue naturally. Include:\n"
                "- Key decisions made and their reasoning\n"
                "- Technical constraints and requirements\n"
                "- Open tasks and next steps\n"
                "- Important file paths, code snippets, and artifacts\n"
                "- Failed attempts and lessons learned\n"
                "- Any context the assistant needs to continue helping\n\n"
                "Be thorough and factual. Preserve all critical technical details. "
                "Aim for at least 20-30% of the original length to maintain quality.\n\n"
                f"Conversation JSON:\n{json.dumps(effective_history, ensure_ascii=False)}"
            ),
        }

        summary_text = ""
        async for response in self.endpoint.create_completion(
            messages=self._system_messages() + [summarize_user],
            tools=None,
            stream=False,
        ):
            if not response.choices:
                continue
            message = response.choices[0].message
            content = getattr(message, "content", None)
            if content:
                summary_text = content.strip()
                break

        if not summary_text:
            raise RuntimeError("Compaction failed: model returned empty summary")

        compact_message = (
            f"{COMPACTION_MARKER_PREFIX}\n"
            f"compacted_messages={len(effective_history)}\n\n"
            f"{summary_text}"
        )

        compaction_id = self._add_message_to_history("assistant", compact_message)
        self.debug_stream.log("COMPACT", f"Inserted compaction marker {compaction_id} over {len(effective_history)} messages")

        return {
            "ok": True,
            "message_id": compaction_id,
            "compacted_messages": len(effective_history),
            "summary_chars": len(summary_text),
        }

    async def check_connection(self) -> bool:
        """Check if the LLM server is reachable."""
        return await self.endpoint.check_connection()

    async def get_model_name(self) -> str:
        """
        Get the active model name from the server.
        Returns cached value if already queried.
        """
        return await self.endpoint.get_model_name()

    def _system_messages(self) -> List[Dict[str, Any]]:
        """The system prompt comes solely from the active role's ``prompt``."""
        prompt = (getattr(getattr(self, "role", None), "prompt", "") or "").strip()
        return [{"role": "system", "content": prompt}] if prompt else []

    async def _build_messages(self, user_input: str) -> List[Dict[str, Any]]:
        """Build message list with the role's system prompt and history."""
        # Add user message to history and store its ID
        user_msg_id = self._add_message_to_history("user", user_input)
        self._last_user_message_id = user_msg_id

        messages = self._system_messages()
        messages.extend(self._to_api_message(m) for m in self._get_effective_history())
        # Log how much reasoning context is in history
        think_msgs = [m for m in messages if isinstance(m.get('content'), str) and '<think>' in m.get('content', '')]
        if think_msgs:
            logger.info(f"[reasoning] {len(think_msgs)} message(s) in history contain <think> blocks")
        else:
            logger.debug("[reasoning] No <think> blocks in history messages")
        return messages

    async def get_current_context(self) -> List[Dict[str, Any]]:
        """Get the current conversation context (system + history) without modifying state.
        
        Returns the exact message list that would be sent to the LLM.
        Useful for debugging and inspecting what the model sees.
        """
        messages = self._system_messages()
        messages.extend(self._to_api_message(m) for m in self._get_effective_history())
        return messages

    async def get_system_prompt(self) -> str:
        """Return the exact system prompt that would be sent on the next turn.

        The system prompt is the active role's ``prompt`` (empty when unset),
        so switching roles is reflected here.
        """
        return (getattr(getattr(self, "role", None), "prompt", "") or "").strip()

    @staticmethod
    def _assemble_tool_calls(buffer: Dict) -> list:
        """Reconstruct tool_call dicts from the stream buffer.

        Keys may be mixed int (index fallback) and str (id), so ordering sorts
        by the buffered integer index to stay type-safe.
        """
        calls = []
        for tc_data in sorted(
            buffer.values(),
            key=lambda t: (t.get("index", 0), str(t.get("id") or "")),
        ):
            calls.append({
                "id": tc_data["id"] or f"idx_{tc_data.get('index', 0)}",
                "type": "function",
                "function": {
                    "name": tc_data["function"]["name"],
                    "arguments": tc_data["function"]["arguments"]
                }
            })
        return calls

    async def _stream_llm_response(self, messages: List[Dict[str, Any]]) -> AsyncGenerator[events.Event, None]:
        """Stream LLM response and collect content/tool calls.

        Yields: Reasoning, Token, Usage, and ToolCallDraft events. The draft
        events announce a tool call as its arguments stream (so the UI is never
        blank while the model writes them). The complete calls are emitted
        later, in execution order, by ``_execute_tool_calls`` so a pending
        permission prompt blocks every subsequent tool call.

        Sets: self._last_full_content, _last_full_reasoning, _last_tool_calls,
              _last_detected_thinking_tag.
        """
        from pico_chat import pico_cfg

        self.state = AgentState.THINKING
        self.debug_stream.log("REQUEST", messages)

        request_start_time = time.perf_counter()
        first_chunk_received = False
        ttft_ms = None

        metrics = MetricsState()
        parser = ThinkingTagParser()
        tool_calls_buffer: Dict[int, Dict[str, Any]] = {}
        # Maps a tool-call index -> the active buffer key (a tool-call id, or
        # the index itself when no id was ever seen) so id-less argument deltas
        # continue the right slot. Reset each stream.
        self._active_tool_slot_by_index: Dict[int, Any] = {}
        # Buffer key -> (args length, time) of the last ToolCallDraft announced.
        draft_emitted: Dict[Any, Tuple[int, float]] = {}
        # Keep the previous provider-reported usage until a new one arrives.
        # Resetting here made the status bar flicker between the authoritative
        # count and the (lower) heuristic estimate during generation.

        # Reset live reasoning accumulator for this generation
        self._current_reasoning = ""

        metrics_interval = pico_cfg.config.ui_metrics_refresh_interval

        chunk_count = 0
        empty_chunks = 0
        async for chunk in self.endpoint.create_completion(messages, tools=self.tool_schemas, stream=True):
            chunk_count += 1

            usage = usage_from_response(chunk)
            if usage is not None:
                metrics.set_usage(usage)
                self._last_usage = usage

            if not chunk.choices:
                empty_chunks += 1
                logger.debug(f"Chunk {chunk_count}: No choices")
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if finish_reason:
                logger.debug(f"Chunk {chunk_count}: finish_reason={finish_reason}")
                if hasattr(delta, 'content') and delta.content:
                    logger.debug(f"  Final delta content: {delta.content}")
                if hasattr(delta, 'refusal') and delta.refusal:
                    logger.warning(f"  LLM REFUSAL: {delta.refusal}")
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    logger.debug(f"  Final delta has tool calls")
                if chunk_count <= 3:
                    logger.warning(f"Got finish_reason={finish_reason} on chunk {chunk_count} - very early finish! Possible API error or content filter.")
                    logger.debug(f"  Full chunk: {chunk}")

            if not first_chunk_received:
                ttft = time.perf_counter() - request_start_time
                ttft_ms = ttft * 1000
                logger.info(f"Time-to-first-token: {ttft_ms:.0f}ms")
                self.debug_stream.log("TTFT", f"{ttft_ms:.0f}ms")
                first_chunk_received = True
                metrics.ttft_ms = ttft_ms

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # 1. Handle Reasoning (DeepSeek/R1 style — reasoning_content API field)
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                if self.state != AgentState.THINKING:
                    self.state = AgentState.THINKING
                metrics.ensure_started()
                self._current_reasoning += reasoning
                yield events.Reasoning(text=reasoning)
                m = metrics.maybe_metrics(metrics_interval)
                if m:
                    yield m
                # No ``continue``: a delta may also carry content or tool-call
                # fragments (at the reasoning→answer transition), which would
                # otherwise be dropped and corrupt the call's arguments.

            # 2. Handle Content (with thinking tag parsing)
            content = delta.content
            if content:
                metrics.ensure_started()

                for segment in parser.feed(content):
                    if segment.is_thinking:
                        if self.state != AgentState.THINKING:
                            self.state = AgentState.THINKING
                        self._current_reasoning += segment.text
                        yield events.Reasoning(text=segment.text)
                    else:
                        if self.state != AgentState.ANSWERING:
                            self.state = AgentState.ANSWERING
                        yield events.Token(text=segment.text)
                    m = metrics.maybe_metrics(metrics_interval)
                    if m:
                        yield m

            # 3. Handle Tool Calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    # Robust keying for streaming providers. Deltas for ONE call
                    # carry the id only on the first chunk and id-less
                    # argument fragments afterwards; but the model may also
                    # emit SEVERAL distinct calls that share the same index.
                    # So: when an id is present, key by it (start/continue a
                    # dedicated slot); when absent, continue the slot currently
                    # active for this index.
                    if tc.id:
                        key = tc.id
                        if tc.id not in tool_calls_buffer:
                            tool_calls_buffer[tc.id] = {
                                "index": tc.index,
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": "", "arguments": ""}
                            }
                        # Remember this id as the active slot for the index, so
                        # subsequent id-less argument deltas attach correctly.
                        self._active_tool_slot_by_index[tc.index] = tc.id
                    else:
                        # No id: this is a continuation of whatever call is
                        # currently occupying this index.
                        key = self._active_tool_slot_by_index.get(tc.index)
                        if key is None:
                            # Very first delta had no id either — key by index.
                            key = tc.index
                        if key not in tool_calls_buffer:
                            tool_calls_buffer[key] = {
                                "index": tc.index,
                                "id": None,
                                "type": "function",
                                "function": {"name": "", "arguments": ""}
                            }

                    if getattr(tc.function, "name", None):
                        tool_calls_buffer[key]["function"]["name"] += tc.function.name
                    if getattr(tc.function, "arguments", None):
                        tool_calls_buffer[key]["function"]["arguments"] += tc.function.arguments

                    # Announce the in-progress call so the UI can render a live
                    # draft line (spinner + name + args so far) instead of
                    # sitting on the thinking line until the stream ends.
                    buffered = tool_calls_buffer[key]
                    draft_name = buffered["function"]["name"]
                    draft_args = buffered["function"]["arguments"]
                    now = time.perf_counter()
                    announced = draft_emitted.get(key)
                    if draft_name and (
                        announced is None
                        or (len(draft_args) > announced[0]
                            and now - announced[1] >= _TOOL_DRAFT_INTERVAL)
                    ):
                        draft_emitted[key] = (len(draft_args), now)
                        yield events.ToolCallDraft(
                            id=buffered["id"] or f"idx_{buffered.get('index', 0)}",
                            name=draft_name,
                            args=draft_args,
                        )

        # Flush any remaining content buffer at end of stream
        for segment in parser.flush():
            if segment.is_thinking:
                if self.state != AgentState.THINKING:
                    self.state = AgentState.THINKING
                self._current_reasoning += segment.text
                yield events.Reasoning(text=segment.text)
            else:
                if self.state != AgentState.ANSWERING:
                    self.state = AgentState.ANSWERING
                yield events.Token(text=segment.text)

        # Yield final metrics
        m = metrics.final_metrics()
        if m:
            yield m

        # Reconstruct tool calls list
        tool_calls_list = []
        if tool_calls_buffer:
            tool_calls_list = self._assemble_tool_calls(tool_calls_buffer)

        # Log results. Reasoning must include BOTH streaming paths — the
        # ``reasoning_content`` API field and inline thinking tags — so use the
        # live accumulator (``parser.full_reasoning`` only sees tags).
        full_content = parser.full_content
        full_reasoning = self._current_reasoning
        if full_content and not tool_calls_list:
            self.debug_stream.log("RESPONSE", full_content)
        if tool_calls_list:
            self.debug_stream.log("TOOL_CALLS", tool_calls_list)

        if not full_content and not tool_calls_list:
            logger.warning(f"LLM returned empty response - no content and no tool calls! Received {chunk_count} chunks ({empty_chunks} empty)")
            logger.debug(f"Total tokens received: {metrics.total_tokens}")
            if chunk_count <= 3:
                logger.warning("Very few chunks received - likely server error or immediate EOF")
        else:
            logger.debug(f"LLM response: {len(full_content)} chars content, {len(tool_calls_list)} tool calls from {chunk_count} chunks")

        # Store results for caller
        self._last_full_content = full_content
        self._last_full_reasoning = full_reasoning
        self._last_detected_thinking_tag = parser.detected_open_tag  # None = reasoning_content API path
        self._last_tool_calls = tool_calls_list


    async def _execute_tool_calls(
        self, 
        tool_calls_list: List[Dict[str, Any]], 
        messages: List[Dict[str, Any]]
    ) -> AsyncGenerator[events.Event, None]:
        """
        Execute all tool calls following the state machine flow.

        Yields ToolCall, PermissionRequest and ToolResult events strictly in
        order: a tool is announced immediately before its permission decision,
        so a pending ``ask`` blocks every later tool from appearing.
        """
        self.state = AgentState.THINKING
        
        for tc in tool_calls_list:
            tool_name = tc["function"]["name"]
            tool_args = tc["function"]["arguments"]
            tool_call_id = tc["id"]
            
            self.debug_stream.log("TOOL_EXEC", {"name": tool_name, "args": tool_args})
            
            # Announce the tool here (not while streaming) so it lands in the
            # transcript after all content and before its own permission prompt.
            yield events.ToolCall(
                id=tool_call_id,
                name=tool_name,
                args=tool_args,
            )
            
            # STEP 1: Check permissions (without executing)
            try:
                args = json.loads(tool_args)
            except json.JSONDecodeError:
                # Invalid JSON - treat as error
                error_msg = f"Invalid JSON arguments: {tool_args}"
                yield events.ToolResult(
                    id=tool_call_id,
                    name=tool_name,
                    outcome="error",
                    output=error_msg,
                )
                self._add_message_to_history(
                    role="tool",
                    content=f"Error: {error_msg}",
                    tool_call_id=tool_call_id
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"Error: {error_msg}"
                })
                continue
            
            permission_decision = self._check_tool_permission(tool_name, args)
            prompt = self._build_permission_prompt(tool_name, args)
            
            # Emit permission request (auto or user-facing)
            if permission_decision == "ask":
                # Need user input
                yield events.PermissionRequest(
                    id=tool_call_id,
                    name=tool_name,
                    args=tool_args,
                    prompt=prompt,
                    auto=False,
                )
                
                # Wait for user response
                user_response = await self._wait_for_user_input(prompt)
                approved = user_response.lower() in ["approve", "yes", "y", "allow"]
            else:
                # Auto-approve or auto-deny
                approved = (permission_decision == "allow")
                yield events.PermissionRequest(
                    id=tool_call_id,
                    name=tool_name,
                    args=tool_args,
                    prompt=prompt,
                    auto=True,
                )
            
            # STEP 2: Emit denial
            if not approved:
                denial_reason = "User denied" if permission_decision == "ask" else "Auto-denied by security policy"
                yield events.ToolResult(
                    id=tool_call_id,
                    name=tool_name,
                    outcome="denied",
                    output=denial_reason,
                )
                # Add to history and continue to next tool
                # Make the denial message more explicit to help LLM understand what to do
                result = f"[TOOL DENIED] The '{tool_name}' tool call was not executed. Reason: {denial_reason}. You should proceed with answering based on the information from other successful tool calls, or acknowledge the denial and ask if the user would like you to try a different approach."
                logger.debug(f"Tool {tool_name} denied, sending explanation to LLM: {result[:100]}...")
                self._add_message_to_history(
                    role="tool",
                    content=result,
                    tool_call_id=tool_call_id
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result
                })
                continue
            
            # STEP 3: Execute tool
            
            try:
                # Execute the tool
                func = self.tools_map.get(tool_name)
                if func is None:
                    raise Exception(f"Tool '{tool_name}' not found")

                # Run the tool as a task while forwarding interim output
                # (bash streaming) as ToolOutput events, so a long command is
                # visible live. In-process and sandbox use the same callback.
                stream_queue: asyncio.Queue = asyncio.Queue()

                def _on_output(stream: str, data: str) -> None:
                    stream_queue.put_nowait((stream, data))

                async def _run_tool():
                    result = func.execute(on_output=_on_output, **args)
                    if inspect.isawaitable(result):
                        result = await result
                    return result

                task = asyncio.ensure_future(_run_tool())
                # Known to ``_abort_tool_calls`` so a stop can kill it.
                self._running_tool = (tool_name, task)
                while not task.done():
                    try:
                        stream, data = await asyncio.wait_for(
                            stream_queue.get(), timeout=0.05
                        )
                    except asyncio.TimeoutError:
                        continue
                    yield events.ToolOutput(
                        id=tool_call_id, name=tool_name, stream=stream, data=data,
                    )

                while not stream_queue.empty():
                    stream, data = stream_queue.get_nowait()
                    yield events.ToolOutput(
                        id=tool_call_id, name=tool_name, stream=stream, data=data,
                    )

                self._running_tool = None
                result = task.result()

                if not isinstance(result, str):
                    result = str(result)
                
                # STEP 4: Success
                self.debug_stream.log("TOOL_RESULT", {"call_id": tool_call_id, "result": result})

                # Bound what the model sees; the UI event keeps the full output.
                history_result = elide(result)

                yield events.ToolResult(
                    id=tool_call_id,
                    name=tool_name,
                    outcome="completed",
                    output=result,
                )
                self._add_message_to_history(
                    role="tool",
                    content=history_result,
                    tool_call_id=tool_call_id
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": history_result
                })
                
            except Exception as e:
                # STEP 4: Error
                error_msg = str(e)
                self.debug_stream.log("TOOL_ERROR", {"call_id": tool_call_id, "error": error_msg})
                yield events.ToolResult(
                    id=tool_call_id,
                    name=tool_name,
                    outcome="error",
                    output=error_msg,
                )
                self._add_message_to_history(
                    role="tool",
                    content=f"Error: {error_msg}",
                    tool_call_id=tool_call_id
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"Error: {error_msg}"
                })

    async def get_status(self) -> Dict[str, Any]:
        """
        Check server status at startup.
        
        Returns:
            Dictionary with status information
        """
        status = {
            "online": False,
            "server_name": self.endpoint.name,
            "server_type": self.endpoint.type,
            "base_url": self.endpoint.base_url,
            "model": "unknown",
            "context_window": "unknown",
            "context_used": 0,
            "context_max": 0,
            "context_percentage": 0.0,
        }
        
        # Check connection
        status["online"] = await self.endpoint.check_connection()
        
        if status["online"]:
            # Query model info
            try:
                status["model"] = await self.endpoint.get_model_name()
            except Exception as e:
                logger.warning(f"Failed to query model name: {e}")
            
            try:
                ctx = await self.endpoint.get_context_window()
                status["context_window"] = f"{ctx // 1024}k" if isinstance(ctx, int) else str(ctx)
            except Exception as e:
                logger.warning(f"Failed to query context window: {e}")
            
            # Context usage is exact only when the provider reports prompt
            # tokens; otherwise there is nothing to show.
            try:
                max_tokens = self.endpoint._cached_context_window or 0
                status["context_max"] = max_tokens
                if self._last_usage and self._last_usage.prompt_tokens is not None:
                    status["context_used"] = self._last_usage.prompt_tokens
                    status["context_percentage"] = (
                        self._last_usage.prompt_tokens / max_tokens * 100
                        if max_tokens > 0 else 0
                    )
                status["context_exact"] = bool(
                    self._last_usage and self._last_usage.prompt_tokens is not None
                )
                status["usage"] = self._last_usage
            except Exception as e:
                logger.warning(f"Failed to read context usage: {e}")
        
        return status

    async def chat(self, user_input: str) -> AsyncGenerator[events.Event, None]:
        """
        Main chat loop orchestrator.
        Handles: User Input -> LLM -> [Tool Calls -> Tool Execution -> LLM]* -> Final Answer

        Yields: events from ``pico_chat.harness.events``.
        """
        if self.sandbox_required():
            yield events.Error(
                message=(
                    f"Role '{self.role.name}' requires an active sandbox. "
                    "Select one with /sandbox <id>, or switch role."
                )
            )
            yield events.Done()
            return

        messages = await self._build_messages(user_input)
        
        # Emit user message start with its ID
        yield events.Start(message_id=self._last_user_message_id, role="user")

        # Agent Loop (Handle Multi-step Tool Calls)
        while True:
            try:
                # Generate assistant message ID upfront so UI can track it
                assistant_msg_id = str(uuid.uuid4())
                yield events.Start(message_id=assistant_msg_id, role="assistant")
                
                # Log request about to be sent
                logger.debug(f"Calling LLM with {len(messages)} messages in context")
                
                # Log last few messages for debugging
                last_msgs = messages[-3:] if len(messages) > 3 else messages
                for i, msg in enumerate(last_msgs, start=max(1, len(messages)-2)):
                    role = msg.get("role", "?")
                    content_len = len(str(msg.get("content", "")))
                    tool_calls_count = len(msg.get("tool_calls", []))
                    logger.debug(f"  Message {i}: role={role}, content_len={content_len}, tool_calls={tool_calls_count}")
                    
                    # If this is an assistant message with tool calls, log the IDs
                    if role == "assistant" and msg.get("tool_calls"):
                        for tc in msg.get("tool_calls", []):
                            logger.debug(f"    Tool call ID: {tc.get('id', 'MISSING')}, name: {tc.get('function', {}).get('name', '?')}")
                    
                    # If this is a tool result message, log first 200 chars to check format
                    if role == "tool":
                        content = str(msg.get("content", ""))
                        logger.debug(f"    Tool result preview: {content[:200]}")
                        tool_call_id = msg.get('tool_call_id', 'MISSING!')
                        logger.debug(f"    Tool call ID: {tool_call_id}")
                        if tool_call_id == "MISSING!":
                            logger.error("Tool message is missing tool_call_id - this will cause API errors!")
                
                # Inject pending thinking prefill: append a partial assistant message
                # so the model continues thinking from the steered/resumed position.
                prefill_messages = list(messages)
                if self._pending_thinking_prefill:
                    # Use the same tag the model used last turn so the format is consistent.
                    open_tag = self._last_detected_thinking_tag or THINKING_TAGS[0][0]
                    prefill = self._pending_thinking_prefill
                    if not prefill.endswith('\n'):
                        prefill += '\n'
                    prefill_content = f"{open_tag}\n{prefill}"
                    prefill_messages = messages + [{"role": "assistant", "content": prefill_content}]
                    logger.debug(f"Injecting thinking prefill ({len(prefill)} chars)")
                    self._pending_thinking_prefill = None

                # Stream LLM response
                async for chunk in self._stream_llm_response(prefill_messages):
                    yield chunk
                
                # Collect results from instance variables
                full_content = self._last_full_content
                full_reasoning = self._last_full_reasoning
                tool_calls_list = self._last_tool_calls
                # Tag the model actually used (None for reasoning_content field path)
                detected_tag = getattr(self, '_last_detected_thinking_tag', None)
                
                logger.debug(f"LLM response complete. Content length: {len(full_content) if full_content else 0}, Reasoning length: {len(full_reasoning) if full_reasoning else 0}, Tool calls: {len(tool_calls_list) if tool_calls_list else 0}")
                
                # Persist the raw reasoning separately so export/import and the
                # transcript never lose it. ``preserve_reasoning_traces`` now
                # only controls whether it is folded back into the request sent
                # to the model (see ``_to_api_message``), not whether it is kept.
                msg = {
                    "id": assistant_msg_id,
                    "role": "assistant",
                    "content": full_content if full_content else None,
                }
                # The tool results that follow reference these calls by id; the
                # model must see its own calls or it acts on orphaned results.
                if tool_calls_list:
                    msg["tool_calls"] = tool_calls_list
                if full_reasoning:
                    msg["reasoning"] = full_reasoning
                    if detected_tag:
                        msg["reasoning_tag"] = detected_tag
                    logger.info(f"[reasoning] Stored {len(full_reasoning)} chars of reasoning in history (tag={detected_tag!r})")
                self.history.append(msg)
                self._last_assistant_message_id = assistant_msg_id

                # The API message for this turn is projected from the stored
                # entry (reasoning re-sent only when configured).
                messages.append(self._to_api_message(msg))
                
                # If no tools, we're done
                if not tool_calls_list:
                    logger.debug("No tool calls - generation complete")
                    break
                    
                # Execute tools and yield feedback
                logger.debug(f"Executing {len(tool_calls_list)} tool call(s)")
                try:
                    async for feedback in self._execute_tool_calls(tool_calls_list, messages):
                        yield feedback
                except (asyncio.CancelledError, GeneratorExit):
                    # Stopped mid-turn (/stop): kill the running tool and
                    # answer every call so history stays a valid request.
                    self._abort_tool_calls(tool_calls_list)
                    raise
                
                logger.debug("Tool execution complete - continuing loop")

            except Exception as e:
                logger.error("Generation failed: %s", e, exc_info=True)
                self.state = AgentState.IDLE
                yield events.Error(message=str(e))
                return
            
        self.state = AgentState.IDLE

        yield events.Done()

_harness = None


def _build_transport(spec, workspace: str) -> Optional[ToolTransport]:
    """Build a ``SandboxTransport`` for ``spec`` (None/disabled → None).

    The sandbox import is lazy so bare mode never touches podman/docker code.
    """
    if spec is None or not spec.enabled:
        return None
    from pico_chat.sandbox import SandboxTransport

    return SandboxTransport.from_spec(spec, workspace)


def get_harness(config_path: str | None = None) -> Harness:
    global _harness
    if _harness is None:
        workspace = config_path or os.getcwd()
        from pico_chat import projects

        project = projects.load_project(workspace)
        spec = projects.active_spec(project)
        transport = _build_transport(spec, workspace)
        _harness = Harness(workspace, transport=transport)
        _harness._sandbox_runtime = getattr(spec, "runtime", "none")
        _sandbox_warning(_harness, spec, workspace)
    return _harness


def _sandbox_warning(harness: Harness, spec, workspace: str) -> None:
    """Append a startup warning when the active sandbox is not ready."""
    if spec is None or not spec.enabled:
        return
    from pico_chat.sandbox import build_command, image_present, runtime_available

    if not runtime_available(spec):
        harness.startup_warnings.append(
            f"Sandbox runtime '{spec.runtime}' not found on PATH; tools will "
            "fail until it is installed (or run /sandbox none)."
        )
    elif not image_present(spec):
        command = build_command(spec, workspace)
        hint = f" Build it with: {' '.join(command)}" if command else ""
        harness.startup_warnings.append(
            f"Sandbox image '{spec.image or spec.runtime}' not found.{hint}"
        )
