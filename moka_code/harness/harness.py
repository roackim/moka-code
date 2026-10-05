import asyncio
import base64
import inspect
import json
import logging
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Any, Dict, List, Optional, Tuple

from moka_code.harness.llm_status import AgentState
from moka_code.harness.debug import get_debug_stream
from moka_code.harness.compaction import COMPACTION_MARKER_PREFIX, transcript
from moka_code.harness.elision import elide
from moka_code.harness import events, images
from moka_code.harness.endpoint import Endpoint, get_active_endpoint
from moka_code.harness.permissions import PermissionGate
from moka_code.harness.usage import MetricsState, TokenUsage

# Import the minimal toolset
from moka_code.harness.tools import (
    InProcessTransport,
    MinimalToolset,
    ToolTransport,
    create_toolset,
)

import os

logger = logging.getLogger(__name__)



# Fields of a history entry that are sent to the model (see ``_to_api_message``).
_API_FIELDS = ("role", "content", "tool_calls", "tool_call_id")

# Minimum wall-clock seconds between live ``ToolCallDraft`` updates for one
# call. The first named chunk always emits; later updates are time-throttled so
# a large streamed body (e.g. a file write) neither emits an event per delta
# nor freezes the draft line for seconds between size-based strides.
_TOOL_DRAFT_INTERVAL = 0.1

class Harness:
    # Why the last stream ended ("stop", "tool_calls", "length", ...).
    _last_finish_reason: Optional[str] = None
    # Private conversation: not saved, not logged, images kept in a temp folder.
    private = False

    def __init__(self, workspace_path: str | None = None,
                 transport: Optional[ToolTransport] = None):
        self.debug_stream = get_debug_stream()
        self.state = AgentState.IDLE
        self.history = []
        
        # Tools initialization with minimal toolset
        import os
        self.workspace = workspace_path or os.getcwd()

        # The conversation's role lives in memory. It starts as the user's
        # ``agent`` file, else the first role file that loads; with none, a
        # placeholder without tools (the notice band says so).
        from moka_code.harness import roles
        self.role = roles.default_role()
        self._role_stat = roles.role_file_stat(self.role.name)

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

        # Why the active sandbox is not ready (shown on the UI's notice band).
        self.sandbox_problem: str | None = None
        # The active sandbox's name and its file's stat, to tell when the running
        # one may differ from its file (see ``sandbox_drift``).
        self.sandbox_name: str | None = None
        self._sandbox_stat: tuple[int, int] | None = None
        # When the active container's image was built (see ``sandbox_stale``).
        self._image_built: float | None = None
        # (tool name, task) while a tool runs; see _abort_tool_calls.
        self._running_tool = None
        # USD spent on this conversation, when the provider reports costs
        # (None until one does, so local servers show nothing).
        self.conversation_cost: Optional[float] = None
        # Cache for the @ file picker listing (invalidated on workspace change).
        self._file_list_cache: list[str] = []
        self._file_list_cache_key: tuple = ()

        # The selected server's endpoint, or ``None`` until a model is picked
        # (/model); nothing is guessed. Resolved here, not from a module-level
        # snapshot, so server settings are never stale.
        self.endpoint: Optional[Endpoint] = get_active_endpoint()
        self._last_usage: Optional[TokenUsage] = None

        # Reasoning of the response being streamed: text, and the provider's
        # opaque blocks (``reasoning_native``), stored as produced.
        self._current_reasoning: str = ""
        self._current_reasoning_native: List[Dict[str, Any]] = []

    def set_role(self, role) -> None:
        """Apply a role to this conversation before its next turn."""
        from moka_code.harness.roles import Role

        if not isinstance(role, Role):
            raise TypeError("role must be a Role")
        from moka_code.harness import roles

        previous_name = getattr(self, "role", role).name
        self.role = role
        self._role_stat = roles.role_file_stat(role.name)
        self._permission_gate.set_role(role)
        self._rebuild_tools()
        self.debug_stream.log("ROLE", {"name": role.name, "tools": sorted(role.enabled_tool_names())})
        self._record_role_change(previous_name, role.name)

    def role_problem(self) -> Optional[str]:
        """How the running role (kept in memory) differs from its file: ``"none"``
        (no role file loads), ``"gone"`` (the file was deleted), ``"changed"``
        (the file now says something else; ``/reload`` applies it), else ``None``.
        A ``stat`` is the gate, so a status refresh costs next to nothing; a
        file touched without changing the role is not a difference."""
        from moka_code.harness import roles

        name = self.role.name
        if name == roles.NO_ROLE:
            return "none"
        stat = roles.role_file_stat(name)
        if stat is None:
            return "gone"
        if stat != self._role_stat:
            try:
                fresh = roles.load_role(name)
            except (KeyError, OSError, ValueError):
                return "changed"        # /reload says why it does not load
            if fresh != self.role:
                return "changed"
            self._role_stat = stat
        return None

    def set_sandbox(self, spec, name: str | None = None) -> None:
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
        self.sandbox_problem = _sandbox_problem(spec, name)
        self.track_sandbox(name)
        runtime = getattr(spec, "runtime", "none")
        self.debug_stream.log("SANDBOX", runtime)
        if getattr(self, "_sandbox_runtime", "none") != runtime:
            self._sandbox_runtime = runtime
            self._record_sandbox_change(runtime)

    def track_sandbox(self, name: str | None) -> None:
        """Remember what the running sandbox was built from: its file's stat and,
        for a container, when its image was built. Called on every activation, after
        ``/sandbox build`` and on ``/reload``."""
        from moka_code import projects
        from moka_code.sandbox import image_created

        self.sandbox_name = name
        self._sandbox_stat = projects.sandbox_file_stat(self.workspace, name) if name else None
        spec = getattr(self.transport, "spec", None)
        self._image_built = image_created(spec) if name and spec is not None else None

    def sandbox_stale(self) -> bool:
        """True when the running container's dockerfile is newer than its image:
        ``/reload`` does not help (moka never builds implicitly), ``/sandbox build``
        does. A ``stat`` of the dockerfile is the whole cost."""
        spec = getattr(self.transport, "spec", None)
        if self._image_built is None or spec is None or not spec.dockerfile:
            return False
        try:
            return (Path(self.workspace) / spec.dockerfile).stat().st_mtime > self._image_built
        except OSError:
            return False

    def sandbox_drift(self) -> Optional[str]:
        """How the running sandbox differs from its file: ``"gone"`` (deleted),
        ``"changed"`` (it now says something else; ``/reload`` applies it), else
        ``None``. A ``stat`` is the gate, so a status refresh costs next to nothing;
        a file touched without changing the sandbox is not a difference."""
        name = self.sandbox_name
        if not name:
            return None
        from moka_code import projects

        stat = projects.sandbox_file_stat(self.workspace, name)
        if stat is None:
            return "gone"
        if stat == self._sandbox_stat:
            return None
        entry = projects.load_project(self.workspace).sandboxes.get(name)
        if entry is None or entry.to_spec() != getattr(self.transport, "spec", None):
            return "changed"        # /reload says why it does not load
        return None

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

    def switch_server(self, new_endpoint: Optional[Endpoint]) -> None:
        """Switch to a different LLM endpoint at runtime (``None``: none).

        The old endpoint's connections close once its last request is done.
        """
        old = self.endpoint
        self.endpoint = new_endpoint
        if old is not None and old is not new_endpoint:
            old.retire()
        self._last_usage = None
        if new_endpoint is not None:
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

    def _to_api_message(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Project a stored history entry onto the API message shape.

        Only API fields leave (``_API_FIELDS``; moka's ``id``, ``source``,
        image references and stored reasoning stay home). Image references
        become content parts, encoded only here.
        """
        msg = {key: entry[key] for key in _API_FIELDS if key in entry}
        if entry.get("images"):
            msg["content"] = images.api_content(msg.get("content"), entry["images"])
        return msg

    def _replay_start(self, history: List[Dict[str, Any]]) -> int:
        """Index of the first entry whose reasoning is sent back: the start of
        the last N turns (N = the role's ``replay_reasoning_depth``), capped at
        what exists. A turn starts at a user message (not a tool's images)."""
        depth = getattr(getattr(self, "role", None), "replay_reasoning_depth", 0)
        starts = [i for i, m in enumerate(history)
                  if m.get("role") == "user" and m.get("source") != "tool"]
        if depth <= 0 or not starts:
            return len(history)
        return starts[max(len(starts) - depth, 0)]

    def _api_history(self) -> List[Dict[str, Any]]:
        """The effective history as API messages, with the reasoning the role
        sends back (the provider's own field, ``Endpoint.replay``)."""
        history = self._get_effective_history()
        start = self._replay_start(history)
        endpoint = getattr(self, "endpoint", None)
        replay = endpoint.replay if endpoint is not None else None
        api = []
        for i, entry in enumerate(history):
            msg = self._to_api_message(entry)
            if replay and i >= start and entry.get("role") == "assistant":
                msg.update(replay(entry))
            api.append(msg)
        return api

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

    async def _wait_for_user_input(self, prompt: str) -> str:
        """Wait for the user to provide text via the UI."""
        return await self._permission_gate.wait_for_user_input(prompt)

    def _check_tool_permission(self, tool_name: str, args: dict) -> str:
        """Check tool permission status. Delegates to PermissionGate."""
        return self._permission_gate.check(tool_name, args)

    def _build_permission_prompt(self, tool_name: str, args: dict) -> str:
        """Build a human-readable permission prompt for a tool call."""
        return PermissionGate.build_prompt(tool_name, args)

    def _add_cost(self, usage: Optional[TokenUsage]) -> None:
        """Add a request's reported cost to the conversation total."""
        cost = getattr(usage, "cost", None)
        if cost is not None:
            self.conversation_cost = (getattr(self, "conversation_cost", None) or 0.0) + cost

    def load_history(self, history: List[Dict[str, Any]]) -> None:
        """Replace the conversation (``/import``); its cost starts over."""
        self.history = history
        self._last_usage = None
        self.conversation_cost = None

    def set_private(self, on: bool) -> None:
        """Enter or leave private mode (``/private``; ``/clear`` leaves it)."""
        self.private = on
        self.debug_stream.muted = on
        images.set_private(on)

    def clear_history(self):
        """Clear the conversation history for the agent."""
        self.history = []
        self.conversation_cost = None
        # Drop the provider-reported usage so the status bar no longer shows
        # the previous conversation's accumulated context after /clear.
        self._last_usage = None
        self.debug_stream.log("CLEAR", "Conversation history cleared")
    
    def list_files_and_folders(self) -> List[str]:
        """Returns a bounded list of files/folders for the @ file picker.

        Uses a depth/max-files-bounded walk so it stays responsive on huge
        trees (e.g. ``$HOME``). The result is cached per-workspace so typing
        ``@`` doesn't re-walk the tree on every keystroke.
        """
        from moka_code.harness.context_builder import list_files_bounded
        from moka_code import settings

        cache_key = (self.workspace, settings.config.context_max_files,
                     settings.config.context_max_depth,
                     settings.config.context_ignore_gitignore)
        if getattr(self, "_file_list_cache_key", None) == cache_key:
            return self._file_list_cache
        entries = list_files_bounded(
            self.workspace,
            max_files=settings.config.context_max_files,
            max_depth=settings.config.context_max_depth,
            ignore_gitignore=settings.config.context_ignore_gitignore,
        )
        self._file_list_cache = entries
        self._file_list_cache_key = cache_key
        return entries

    async def compact_history(self) -> Dict[str, Any]:
        """Summarize effective history with one LLM call and insert compaction marker.

        Returns:
            Summary stats describing the compaction operation.
        """
        from moka_code import settings

        if self.endpoint is None:
            raise RuntimeError("no model selected (/model)")
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
                "The summary will replace the conversation history, so the conversation "
                "must be able to continue from it alone. Include:\n"
                "- The user's goal and any instructions or preferences they stated\n"
                "- Key decisions made and their reasoning\n"
                "- Technical constraints and requirements\n"
                "- Current state: what is done, what is in progress\n"
                "- Open tasks and next steps\n"
                "- Exact file paths, names, commands and error messages that still matter\n"
                "- Failed attempts and why they failed\n\n"
                "Do not reproduce raw tool output; state what it showed. Keep code only "
                "where it is needed to continue (an unresolved error, a snippet being "
                "worked on). Be factual; do not invent details.\n\n"
                "Conversation:\n" + transcript(
                    effective_history,
                    filter_thoughts=settings.config.context_compact_filter_thoughts,
                    filter_tool_calls=settings.config.context_compact_filter_tool_calls)
            ),
        }

        summary_text = ""
        usage = None
        async for chunk in self.endpoint.stream(self._system_messages() + [summarize_user]):
            summary_text += chunk.text
            usage = chunk.usage or usage
        self._add_cost(usage)       # one request, one cost: the last usage reported
        summary_text = summary_text.strip()

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
        return self.endpoint is not None and await self.endpoint.check_connection()

    def _system_messages(self) -> List[Dict[str, Any]]:
        """The system prompt comes solely from the active role's ``prompt``."""
        prompt = (getattr(getattr(self, "role", None), "prompt", "") or "").strip()
        return [{"role": "system", "content": prompt}] if prompt else []

    def _request_messages(self) -> List[Dict[str, Any]]:
        """The exact message list the next request sends: the role's system
        prompt, then the effective history as API messages. The one place a
        request is built."""
        return self._system_messages() + self._api_history()

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

        Sets: self._last_full_content, _last_full_reasoning,
              _last_reasoning_native, _last_tool_calls.
        """
        from moka_code import settings

        self.state = AgentState.THINKING
        self.debug_stream.log("REQUEST", messages)

        request_start_time = time.perf_counter()
        first_chunk_received = False
        ttft_ms = None

        metrics = MetricsState()
        # The answer exactly as the server sent it: never parsed (no inline
        # ``<think>`` tags split out; reasoning comes only in its own fields).
        full_content = ""
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
        self._current_reasoning_native = []

        metrics_interval = settings.config.ui_metrics_refresh_interval

        chunk_count = 0
        empty_chunks = 0
        stream_usage: Optional[TokenUsage] = None
        self._last_finish_reason = None
        async for chunk in self.endpoint.stream(messages, tools=self.tool_schemas):
            chunk_count += 1

            if chunk.usage is not None:
                metrics.set_usage(chunk.usage)
                self._last_usage = chunk.usage
                stream_usage = chunk.usage

            if chunk.reasoning_native is not None:
                self._current_reasoning_native = chunk.reasoning_native

            if not (chunk.text or chunk.reasoning or chunk.tool_calls or chunk.finish):
                empty_chunks += 1
                logger.debug(f"Chunk {chunk_count}: empty")
                continue

            if chunk.finish:
                self._last_finish_reason = chunk.finish
                logger.debug(f"Chunk {chunk_count}: finish_reason={chunk.finish}")
                if chunk_count <= 3:
                    logger.warning(f"Got finish_reason={chunk.finish} on chunk {chunk_count} - very early finish! Possible API error or content filter.")
                    logger.debug(f"  Full chunk: {chunk}")

            if not first_chunk_received:
                ttft = time.perf_counter() - request_start_time
                ttft_ms = ttft * 1000
                logger.info(f"Time-to-first-token: {ttft_ms:.0f}ms")
                self.debug_stream.log("TTFT", f"{ttft_ms:.0f}ms")
                first_chunk_received = True
                metrics.ttft_ms = ttft_ms

            # 1. Reasoning, from the server's own fields.
            reasoning = chunk.reasoning
            if reasoning:
                if self.state != AgentState.THINKING:
                    self.state = AgentState.THINKING
                metrics.ensure_started()
                self._current_reasoning += reasoning
                yield events.Reasoning(text=reasoning)
                m = metrics.maybe_metrics(metrics_interval)
                if m:
                    yield m
                # No ``continue``: a chunk may also carry content or tool-call
                # fragments (at the reasoning→answer transition), which would
                # otherwise be dropped and corrupt the call's arguments.

            # 2. Content, verbatim.
            content = chunk.text
            if content:
                metrics.ensure_started()
                if self.state != AgentState.ANSWERING:
                    self.state = AgentState.ANSWERING
                full_content += content
                yield events.Token(text=content)
                m = metrics.maybe_metrics(metrics_interval)
                if m:
                    yield m

            # 3. Handle Tool Calls
            if chunk.tool_calls:
                for tc in chunk.tool_calls:
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

                    if tc.name:
                        tool_calls_buffer[key]["function"]["name"] += tc.name
                    if tc.arguments:
                        tool_calls_buffer[key]["function"]["arguments"] += tc.arguments

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

        # One request, one cost: the last usage the stream reported.
        self._add_cost(stream_usage)

        # Yield final metrics
        m = metrics.final_metrics()
        if m:
            yield m

        # Reconstruct tool calls list
        tool_calls_list = []
        if tool_calls_buffer:
            tool_calls_list = self._assemble_tool_calls(tool_calls_buffer)

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
        self._last_reasoning_native = self._current_reasoning_native
        self._last_tool_calls = tool_calls_list


    async def _execute_tool_calls(
        self, 
        tool_calls_list: List[Dict[str, Any]],
    ) -> AsyncGenerator[events.Event, None]:
        """
        Execute all tool calls following the state machine flow.

        Yields ToolCall, PermissionRequest and ToolResult events strictly in
        order: a tool is announced immediately before its permission decision,
        so a pending ``ask`` blocks every later tool from appearing.
        """
        self.state = AgentState.THINKING
        # Images returned by ``read``; attached after the tool results
        # (providers do not accept images in ``tool`` messages).
        tool_images: List[Dict[str, Any]] = []
        tool_image_paths: List[str] = []

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
                # Invalid JSON - treat as error. When the stream hit the output
                # token limit, the call was cut off: echoing the partial blob
                # back only invites the same oversized retry.
                if self._last_finish_reason == "length":
                    error_msg = (
                        "Your response hit the output token limit and this tool call "
                        f"was cut off after {len(tool_args)} characters of arguments. "
                        "Retry with smaller calls: split large writes or edits into "
                        "several steps, and keep reasoning brief."
                    )
                else:
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

                # ``read`` may return an image; the limit is the harness's,
                # never the model's.
                run_args = ({**args, "max_image_bytes": images.max_bytes()}
                            if tool_name == "read" else args)

                async def _run_tool():
                    result = func.execute(on_output=_on_output, **run_args)
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

                if isinstance(result, dict) and "image" in result:
                    result, image = self._take_tool_image(result["image"])
                    if image is not None:
                        tool_images.append(image)
                        tool_image_paths.append(str(args.get("path", "")))
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

        if tool_images:
            for n, image in enumerate(tool_images, start=1):
                image["n"] = n
            self._add_message_to_history(
                "user", f"[images returned by read: {', '.join(tool_image_paths)}]",
                images=tool_images, source="tool",
            )

    def _take_tool_image(self, found: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
        """Turn an image returned by ``read`` into ``(tool result, reference)``.

        The bytes go to the image cache; the reference is ``None`` when the
        image cannot be attached, and the result then says why.
        """
        path = str(found.get("path", ""))
        if self.endpoint.accepts_images() is False:
            model = self.endpoint.selected_model or "the model"
            return f"[image: {path} — not attached: {model} can't read images]", None
        try:
            data = base64.b64decode(found.get("data") or "", validate=True)
            image = images.store(data, images.max_bytes(), name=Path(path).name)
        except (images.ImageError, ValueError) as exc:
            return f"[image: {path} — not attached: {exc}]", None
        return f"[image: {path}, {image['width']}×{image['height']} — attached]", image

    async def get_status(self) -> Dict[str, Any]:
        """
        Check server status at startup.
        
        Returns:
            Dictionary with status information
        """
        if self.endpoint is None:
            return {"online": False}
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
            status["model"] = self.endpoint.selected_model or "unknown"
            
            ctx = self.endpoint.context_window()
            if ctx:
                status["context_window"] = f"{ctx // 1024}k"
            
            # Context usage is exact only when the provider reports prompt
            # tokens; otherwise there is nothing to show.
            try:
                max_tokens = self.endpoint.context_window() or 0
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

    async def chat(self, user_input: str,
                   attached: Optional[List[Dict[str, Any]]] = None) -> AsyncGenerator[events.Event, None]:
        """
        Main chat loop orchestrator.
        Handles: User Input -> LLM -> [Tool Calls -> Tool Execution -> LLM]* -> Final Answer

        ``attached``: image references (``harness.images``) sent with the
        user message.

        Yields: events from ``moka_code.harness.events``.
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
        if self.endpoint is None:
            yield events.Error(message="No model selected. Pick one with /model.")
            yield events.Done()
            return

        # The user message joins history; every request is built from it.
        extra = {"images": list(attached)} if attached else {}
        self._last_user_message_id = self._add_message_to_history("user", user_input, **extra)
        
        # Emit user message start with its ID
        yield events.Start(message_id=self._last_user_message_id, role="user")

        # Agent Loop (Handle Multi-step Tool Calls)
        while True:
            try:
                # Generate assistant message ID upfront so UI can track it
                assistant_msg_id = str(uuid.uuid4())
                yield events.Start(message_id=assistant_msg_id, role="assistant")
                
                messages = self._request_messages()

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
                
                # Stream LLM response
                async for chunk in self._stream_llm_response(messages):
                    yield chunk
                
                # Collect results from instance variables
                full_content = self._last_full_content
                full_reasoning = self._last_full_reasoning
                tool_calls_list = self._last_tool_calls
                
                logger.debug(f"LLM response complete. Content length: {len(full_content) if full_content else 0}, Reasoning length: {len(full_reasoning) if full_reasoning else 0}, Tool calls: {len(tool_calls_list) if tool_calls_list else 0}")
                
                # Reasoning is stored as produced, with where it came from
                # (``origin``); what is sent back is decided in one place.
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
                if self._last_reasoning_native:
                    msg["reasoning_native"] = self._last_reasoning_native
                msg["origin"] = {"type": self.endpoint.type,
                                 "model": self.endpoint.selected_model}
                self.history.append(msg)

                # If no tools, we're done
                if not tool_calls_list:
                    logger.debug("No tool calls - generation complete")
                    break
                    
                # Execute tools and yield feedback
                logger.debug(f"Executing {len(tool_calls_list)} tool call(s)")
                try:
                    async for feedback in self._execute_tool_calls(tool_calls_list):
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
    from moka_code.sandbox import SandboxTransport

    return SandboxTransport.from_spec(spec, workspace)


def current_workspace() -> str:
    """The running harness's workspace (the working directory before one exists)."""
    return _harness.workspace if _harness is not None else os.getcwd()


def get_harness(config_path: str | None = None) -> Harness:
    global _harness
    if _harness is None:
        workspace = config_path or os.getcwd()
        from moka_code import projects

        project = projects.load_project(workspace)
        spec = projects.active_spec(project)
        transport = _build_transport(spec, workspace)
        _harness = Harness(workspace, transport=transport)
        _harness._sandbox_runtime = getattr(spec, "runtime", "none")
        _harness.track_sandbox(getattr(project, "active", None))
        _harness.sandbox_problem = _sandbox_problem(spec, getattr(project, "active", None))
    return _harness


def _sandbox_problem(spec, sandbox_id: str | None = None) -> str | None:
    """Why the active sandbox is not ready (``problem → fix``), else ``None``."""
    if spec is None or not spec.enabled:
        return None
    from moka_code.sandbox import image_present, runtime_available

    if not runtime_available(spec):
        return f"sandbox runtime '{spec.runtime}' not found on PATH → install it, or /sandbox config"
    if not image_present(spec):
        return (f"sandbox image '{spec.image or spec.runtime}' not found "
                f"→ /sandbox build {sandbox_id or '<id>'}")
    return None
