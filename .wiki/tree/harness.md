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
  buffers tool-call deltas and emits a live `ToolCallDraft` as arguments arrive
  (first named chunk, then a stride that backs off with argument size), so the UI
  is never blank while the model writes a call
- `_execute_tool_calls()` — emits the complete `ToolCall` then `PermissionRequest`
  per tool in order (a pending `ask` blocks later tools); delegates to `PermissionGate`.
  Runs the tool as a task and forwards its `on_output` chunks as `ToolOutput`
  events as they arrive (bash streaming), then `ToolResult`
- `_build_transport(spec, workspace)` — builds a `SandboxTransport` for a
  `ContainerSpec` (lazy `pico_chat.sandbox` import); empty/`none` → in-process
- `set_sandbox(spec)` — swap the transport live (used by `/sandbox`): closes the
  old worker, rebuilds the tool map, records a `[Sandbox: …]` notice
- `get_harness()` — loads the current project's active sandbox (by directory
  name) and builds its transport
Key state: `AgentState` enum, message history list, active endpoint, active role, and thinking steering state (`_current_reasoning`, `_pending_thinking_prefill`, `_last_detected_thinking_tag`).

### `permissions.py`
The single "may I run this?" decision point, and nothing more.
- `PermissionGate` — reads the active `Role`'s per-tool value (`no` / `ask` /
  `yes`) and returns the harness decision (`deny` / `ask` / `allow`). Builds
  permission prompts (`build_prompt()`) and owns the async user-response queue.
- There is no permission engine: no `SecurityChecker`, no command lists, no
  chain policy, no predefined profiles, no path confinement. Isolation is the
  user's choice via a project sandbox (`/sandbox`; see `plans/sandbox_worker.md`).
See [notes/security.md](../notes/security.md) and
[notes/tools-and-permissions.md](../notes/tools-and-permissions.md).

### `thinking_parser.py`
`ThinkingTagParser` — extracted from `Harness._stream_llm_response`. Handles two input paths:
- `reasoning_content` API field (DeepSeek/R1 style) — yielded directly as `events.Reasoning`
- Inline `<thinking>`/`</thinking>` and `<think>`/`</think>` tags — state machine splits content into `events.Reasoning`/`events.Token` segments across chunk boundaries
`MetricsState` — periodic `events.Usage` emission helper.

### `events.py`
The one harness→UI event protocol. A single union yielded by `Harness.chat()`:
`Start`, `Token`, `Reasoning`, `ToolCallDraft` (live, partial args),
`ToolCall` (complete, announced at execution), `PermissionRequest`, `ToolOutput`
(interim tool stream chunks), `ToolResult` (`outcome` =
completed/denied/error), `Usage`, `Error`, `Done`.
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
`api_key_env` (`type` is required; the loader skips a server without one).
Also hosts `ModelInfo`, `ConnectionDiagnosis`, and `.local` hostname resolution
helpers. Factories: `get_active_endpoint()` (= `get_endpoint(active_server)` or
`default_endpoint()`), `get_endpoint(name)` (applies the server's own
`last_model`, seeds context windows from the catalog, records `source` so
`/reload` can tell whether the live endpoint is stale). Model-name and
context-window fallbacks are shown but not memoized.
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
Tool **schemas** and host-side bindings. The tool *bodies* live in
[`worker.py`](./README.md) (stdlib-only, shared with the sandbox worker).
- `MinimalToolset` — binds `FileTools` + `ShellTool` (`read`/`write`/`edit`/`bash`).
- `FileTools` (read/write/edit), `ShellTool` (`bash`, cancellable async path) —
  thin adapters over the `worker` functions.
- `ToolError` — re-exported from `worker`.
- **Transport seam** — `ToolTransport` (Protocol: `execute(name, args)`,
  `cancel_active(name)`) and `InProcessTransport`, which runs the registered
  handler against a `MinimalToolset`. `Harness` accepts an optional transport
  and defaults to in-process (bare mode).

`FileTools.read()` supports optional 0-based offset + line limit, character
limits with an explicit truncation marker, and source line-number prefixes.

**Tool registry** — each tool is declared once with the `@tool` decorator,
which carries its name, LLM-facing schema and handler. There is no separate
`tool_wrappers.py`.
- `ToolDefinition` / `RegisteredTool` — registry record and bound instance.
	`get_schema()` returns the OpenAI function schema; `RegisteredTool.execute()`
	is async and delegates to the bound transport.
- `registered_tool_names()` — the list of registry keys, used to generate role
	files.
- `create_toolset(workspace_path, transport=None)` — factory; builds an
	`InProcessTransport` when no transport is given. Registers: `read`, `write`,
	`edit`, `bash`.

See [notes/tools-and-permissions.md](../notes/tools-and-permissions.md).

### `elision.py`
`elide(text, limit=DEFAULT_LIMIT)` — host-side bound for oversized tool output
(`DEFAULT_LIMIT` = 10k chars). Keeps the head and tail and inserts a
`… N chars elided; narrow the command (head/tail/grep/sed)` marker. Applied
before a completed tool result enters history/LLM messages; the `ToolResult`
event still carries the full output for the UI. `limit <= 0` disables it.

### `roles.py`
`Role` — the single source of truth for a conversation's operating mode: a
`description`, a `prompt`, a `tools: dict[str, str]` mapping each registered
tool to exactly one of `no` / `ask` / `yes`, and `require_sandbox` (bool,
default false). There is no permission engine.
- `require_sandbox = true` locks the conversation while no sandbox is active:
  `Harness.sandbox_required()` gates `chat()` (yields an `Error`) and the UI
  refuses normal submissions. Activating a sandbox lifts it automatically.
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

### `debug.py`
`DebugStream` — structured debug logging to JSON. Used for dev; not active in production builds.
