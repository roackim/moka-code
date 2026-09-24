# pico_chat/harness/ — LLM Agent Core

The agent backbone. Manages the LLM conversation loop, tool execution, approval gating, context construction, and endpoint management.

Key internal modules: `permissions.py` (the single approval decision point), `roles.py` (role model), `thinking_parser.py` (thinking-tag state machine), `endpoint.py` (one type for endpoint config + transport), `events.py` (the one harness→UI event protocol). The `Harness` class in `harness.py` delegates to these.

See [notes/architecture.md](../notes/architecture.md), [notes/tools-and-permissions.md](../notes/tools-and-permissions.md), and [notes/reasoning-traces.md](../notes/reasoning-traces.md) for conceptual details.

---

## Files

### `harness.py`
`Harness` — main class. Owns the agent state machine and conversation history.
- `chat(user_input)` — async generator; full agent turn (stream → handle tool calls)
- `_stream_llm_response()` — delegates thinking-tag parsing to `ThinkingTagParser`;
  buffers tool-call deltas without emitting `ToolCall` (content flushes first)
- `_execute_tool_calls()` — emits `ToolCall` then `PermissionRequest` per tool in
  order (a pending `ask` blocks later tools); delegates to `PermissionGate`
Key state: `AgentState` enum, message history list, active endpoint, active role, and thinking steering state (`_current_reasoning`, `_pending_thinking_prefill`, `_last_detected_thinking_tag`).

### `permissions.py`
The single "may I run this?" decision point, and nothing more.
- `PermissionGate` — reads the active `Role`'s per-tool value (`no` / `ask` /
  `yes`) and returns the harness decision (`deny` / `ask` / `allow`). Builds
  permission prompts (`build_prompt()`) and owns the async user-response queue.
- There is no permission engine: no `SecurityChecker`, no command lists, no
  chain policy, no predefined profiles, no path confinement. Isolation is the
  user's responsibility (see `plans/containerization.md`).
See [notes/security.md](../notes/security.md) and
[notes/tools-and-permissions.md](../notes/tools-and-permissions.md).

### `thinking_parser.py`
`ThinkingTagParser` — extracted from `Harness._stream_llm_response`. Handles two input paths:
- `reasoning_content` API field (DeepSeek/R1 style) — yielded directly as `events.Reasoning`
- Inline `<thinking>`/`</thinking>` and `<think>`/`</think>` tags — state machine splits content into `events.Reasoning`/`events.Token` segments across chunk boundaries
`MetricsState` — periodic `events.Usage` emission helper.

### `events.py`
The one harness→UI event protocol. A single union yielded by `Harness.chat()`:
`Start`, `Token`, `Reasoning`, `ToolCall`, `PermissionRequest`, `ToolResult`
(`outcome` = completed/denied/error), `Usage`, `Error`, `Done`.
The harness yields events; the UI renders them. There is no separate chunk/status type.
The former unused `SubagentsWaiting`/`SubagentResult`/`SubagentsDone` events were removed.

### `endpoint.py`
`Endpoint` — **one concrete type** holding connection config *and* the live
transport (httpx client, caches, selected model, connection state). No ABC or
subclasses; server-family differences are internal branches:
- `llamacpp` — single model from `/models[0]`, context via `/props`, ignores per-request model
- `ollama` — native `/api/tags`, `/api/show`, native `/api/chat` (usage counters)
- `openrouter` — enabled-models allowlist, per-model provider routing
- `openai` — configured model + known context-window table
`Endpoint.from_dict(name, data)` reads a `[servers.<name>]` table and resolves
`api_key_env`; `to_dict()` strips secrets. Also hosts `ModelInfo`,
`ConnectionDiagnosis`, and `.local` hostname resolution helpers. Factories:
`get_active_endpoint()`, `get_endpoint(name)`, `default_endpoint()`.
`Harness.endpoint` is an `Endpoint`. See [notes/local-hostname-resolution.md](../notes/local-hostname-resolution.md).

This one type replaced the former `LLMServerConfig` + `ServerService` +
`LLMServer` ABC/four-subclass split (`llm_server.py`, `llm_server_config.py`,
`server_service.py` are deleted). The UI `commands/` package calls into
`endpoint.py` and `pico_cfg` directly.

Server-family code is split out and reached through thin `Endpoint` wrappers:
- `endpoint_openai.py` — SSE transport/adapters
- `endpoint_ollama.py` — native chat + context; outgoing message normalization
- `endpoint_discovery.py` — `list_models` / `discover_models` / `query_*`
- `endpoint_local.py` — `.local` mDNS resolution

### `clipboard.py`
OSC 52 clipboard escape for headless Linux terminals (fallback for
`ui/clipboard.py`).

### `usage.py`
`TokenUsage` and normalization helpers convert OpenAI-compatible and Ollama
usage counters into provider-neutral prompt/completion/total token data.

### `llm_status.py`
`AgentState` enum: `UNCONNECTED`, `IDLE`, `THINKING`, `ANSWERING`.

### `tools.py`
Low-level tool implementations plus the tool registry.
- `MinimalToolset` — binds `FileTools` + `ShellTool` (`read`/`write`/`edit`/`bash`).
- `FileTools` (read/write/edit), `ShellTool` (`bash`, cancellable async path).
- `ToolError` — raised by tool functions on failure.

`FileTools.read()` supports optional 0-based offset + line limit, character
limits with an explicit truncation marker, and source line-number prefixes.

**Tool registry** — each tool is declared once with the `@tool` decorator,
which carries its name, LLM-facing schema and handler. There is no separate
`tool_wrappers.py`.
- `ToolDefinition` / `RegisteredTool` — registry record and bound instance.
	`get_schema()` returns the OpenAI function schema; `RegisteredTool.execute()`
	is async and prefers the tool's async handler when present.
- `registered_tool_names()` — the list of registry keys, used to generate role
	files.
- `create_toolset(workspace_path)` — factory that binds the registered tools to
	one `MinimalToolset`. Registers: `read`, `write`, `edit`, `bash`.

See [notes/tools-and-permissions.md](../notes/tools-and-permissions.md).

### `roles.py`
`Role` — the single source of truth for a conversation's operating mode: a
`description`, a `prompt`, and a `tools: dict[str, str]` mapping each registered
tool to exactly one of `no` / `ask` / `yes`. There is no permission engine.
- Built-in roles `agent` (all tools `yes`) and `chat` (all tools `no`) are
	seeded as files by `ensure_roles_dir()`; a code fallback exists for both.
- `create_role(name)` writes a template listing every registered tool with
	value `no` (all disabled); `delete_role(name)` unlinks, refusing to remove
	the last role. `load_role` / `list_roles` read one file per role at
	`~/.config/pico-chat/roles/<name>.toml`.
- `validate_roles()` reports unknown tool names and values other than
	`no`/`ask`/`yes` as `roles/<name>.toml: ...`, surfaced by `/reload` and
	`/config role`.
- Built-in `agent`/`chat` files override the code fallback. A role change is
	represented by one system history notice; consecutive notices are collapsed.

### `context_builder.py`
`build_harness_context()` — builds the project file-tree context string.
- Builds the file tree regardless of git status (outside a git repo there is no `.gitignore`, so the whole directory is listed — this keeps the `@` file picker working everywhere)
- `list_files_bounded()` — breadth-first, depth/max-files-bounded walk used by the `@` file picker so it stays responsive on huge trees (e.g. `$HOME`); respects `.gitignore` unless `ignore_gitignore` is set
- Injects current date and time
- Note: the result is stored on `Harness.project_context` but is **not** sent to
  the model — the system prompt is the active role's `prompt` only.

### System prompt

There is no `system_prompt.py`. The entire system message is the active role's
`prompt` field (empty → no system message). `Harness._system_messages()` builds
it; `Harness.get_system_prompt()` returns it. It is edited as a role file
(`roles/<name>.toml`), never in code.

### `patch_parser.py`
`PatchBlock` — parsed representation of a search/replace block.
`parse_patch(text)` — extracts filename, search/replace text from aider-style markers.
`apply_patch(content, patch)` — applies a patch with 3-mode cascade: exact → whitespace-normalized → indentation-normalized.
`PatchParseError` — raised on invalid patch format.

### `debug.py`
`DebugStream` — structured debug logging to JSON. Used for dev; not active in production builds.
