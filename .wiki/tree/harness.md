# moka_code/harness/ — LLM Agent Core

The agent backbone. Manages the LLM conversation loop, tool execution, approval gating, context construction, and endpoint management.

Key internal modules: `permissions.py` (the single approval decision point), `roles.py` (role model), `endpoint.py` (one type for endpoint config + transport), `events.py` (the one harness→UI event protocol). The `Harness` class in `harness.py` delegates to these.

See [notes/architecture.md](../notes/architecture.md), [notes/tools-and-permissions.md](../notes/tools-and-permissions.md), and [notes/reasoning-traces.md](../notes/reasoning-traces.md) for conceptual details.

---

## Files

### `harness.py`
`Harness` — main class. Owns the agent state machine and conversation history.
- `chat(user_input)` — async generator; full agent turn (stream → handle tool calls)
- `_stream_llm_response()` — content deltas become `Token`s verbatim (no tag
  parsing), reasoning fields become `Reasoning`; buffers tool-call deltas and emits a live `ToolCallDraft` as arguments arrive
  (first named chunk, then a stride that backs off with argument size), so the UI
  is never blank while the model writes a call
- `_execute_tool_calls()` — emits the complete `ToolCall` then `PermissionRequest`
  per tool in order (a pending `ask` blocks later tools); delegates to `PermissionGate`.
  Runs the tool as a task and forwards its `on_output` chunks as `ToolOutput`
  events as they arrive (bash streaming), then `ToolResult`
- `_build_transport(spec, workspace)` — builds a `SandboxTransport` for a
  `ContainerSpec` (lazy `moka_code.sandbox` import); empty/`none` → in-process
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

### `events.py`
The one harness→UI event protocol. A single union yielded by `Harness.chat()`:
`Start`, `Token`, `Reasoning`, `ToolCallDraft` (live, partial args),
`ToolCall` (complete, announced at execution), `PermissionRequest`, `ToolOutput`
(interim tool stream chunks), `ToolResult` (`outcome` =
completed/denied/error), `Usage`, `Error`, `Done`.
The harness yields events; the UI renders them. There is no separate chunk/status type.
The former unused `SubagentsWaiting`/`SubagentResult`/`SubagentsDone` events were removed.

### `endpoint.py`
`Endpoint` — the **base class** of every provider: connection config *and* the
live transport (httpx client, caches, selected model, connection state). Each
server family is a subclass in `providers/` (below); nothing outside a provider
branches on `type`. Class attributes: `type`, `default_url`, `fixed_url`,
`extra_keys`. Providers implement `list_models` (models with
their facts: `ModelInfo.context_window`, `images`, `efforts`, read from the
server), `_stream` and `effort_payload`. `stream(messages, tools)`
is the only request path (chat and compaction): it wraps the provider's
`_stream` in the retry loop (503 and network errors, backoff) and yields
`Chunk`s (`text`, `reasoning`, `reasoning_native`, `tool_calls` as
`ToolCallPiece`s, `usage`, `finish`); the harness reads no vendor field.
`make_endpoint(name, data)` builds the registry class for a `[servers.<name>]`
table and resolves `api_key_env` (`type` is required unless the table is named
after one; `base_url` is required unless the class has a `default_url` or
`fixed_url`; the loader skips a server otherwise).
Also hosts `ModelInfo`, `ConnectionDiagnosis`, and `.local` hostname resolution
helpers. Factories: `get_active_endpoint()` (= `get_endpoint(active_server)`,
`None` when nothing is selected or the server is gone — there is no fallback
endpoint; `Harness.endpoint` is then `None` and `chat()` refuses with "No model
selected"), `get_endpoint(name)` (applies the server's own
`last_model`, records `source` so `/reload` can tell whether the live endpoint
is stale). Model facts (context window, image support, effort levels) are read
from the live in-memory catalog (`catalog_entry`) when needed, never copied
into the endpoint; `refresh_catalog(names)` rediscovers servers in parallel
(`DISCOVERY_TIMEOUT` per server); a server it cannot list (busy or down) keeps
this session's last successful discovery, flagged in `Config.stale_servers`
(`/model` shows `unreachable`), until a refresh succeeds; only the latest-started
refresh per server applies its result (`_refresh_generation`). The status bar
reads `Endpoint.context_window()` (the catalog's; a selected model whose
listing states none is an error notice). It runs at
startup (active server first), on `/reload` and `/config servers`
(`reapply_endpoint(rediscover=True)`; theme/role/other edits do not), on
`/model`, and on `/effort` (active server). The UI's notice band
flags a selected model that is not among the ids a fresh (non-stale)
listing shows (`unserved_model`). The selected model is one field,
`_selected_model`, sent as selected on every server; with none, `stream`
raises "No model selected" (never a guessed id). `prewarm_connection()`
checks the connection in the background (`_connection_state`). The endpoint
caches no model facts and probes nothing of its own; `OpenAICompatible` is the abstract shared chat-completions code (no `type`);
the registered types are `llamacpp`, `openrouter` and `deepseek`. No built-in context
table and no `max_context`. `type = "openrouter"` always
uses `OpenRouter.fixed_url`; a `base_url` in its table is a load error
(ignored).
`Harness.endpoint` is an `Endpoint`. See [notes/local-hostname-resolution.md](../notes/local-hostname-resolution.md).

This one type replaced the former `LLMServerConfig` + `ServerService` +
`LLMServer` ABC/four-subclass split (`llm_server.py`, `llm_server_config.py`,
`server_service.py` are deleted). The UI `commands/` package calls into
`endpoint.py` and `settings` directly.

### `providers/`
`REGISTRY` (`__init__.py`) maps `type` to class; `settings` validates server
tables from it.
- `openai_compatible.py` — `OpenAICompatible` (abstract, no `type`): `/models`,
  SSE `/chat/completions` → `Chunk`s; `parse_usage`; `_extra_payload` hook;
  `stated_efforts` (`reasoning.supported_efforts`, weakest first by
  `EFFORT_WORDS`) and `stated_images` (`architecture.input_modalities`)
- `openai_server.py` — `OpenAICompatibleServer(OpenAICompatible)`: `type =
  "openai-compatible"`, `base_url` required, nothing else of its own (no `/props`);
  the context window comes only from `/models` `context_length`
- `llamacpp.py` — `LlamaCpp(OpenAICompatible)`: default URL; `list_models` adds context and
  vision from `/props` (unknown if it does not answer),
  cache count from `timings.cache_n` when usage has none
- `deepseek.py` — `DeepSeek(OpenAICompatible)`: fixed URL; own `/models` reader
  (`context_window`, `input_modalities`, `effort.supported_levels`);
  `min_replay_depth` 999 with tools
- `openrouter.py` — `OpenRouter(OpenAICompatible)`: fixed URL; `models`
  (list) is the whitelist; `providers` (server default) and
  `providers_by_model` (per-model replacement) are strict ordered whitelists sent as
  `{"order": [...], "allow_fallbacks": false}` (`_provider_spec`);
  `providers = []` or none → OpenRouter's own routing;
  `reasoning_details` assembled into `Chunk.reasoning_native`
  (`merge_reasoning_details`)

`endpoint_local.py` — `.local` mDNS resolution.

### `images.py`
Image attachments on user messages. History stores a reference (`path`,
`name`, `mime`, `size`, `width`, `height`, `n`) under the entry's `images`;
`_to_api_message` turns it into OpenAI content parts (`api_content`: text,
then `image_url` data URLs, base64 read at request time). `probe` reads
PNG/JPEG/GIF/WebP headers (no Pillow); `store` caches pasted bytes in
`~/.cache/moka/images/<hash>.<ext>`; `collect` resolves a draft's `[image #N]`
markers (files on disk are never attached; the model `read`s them); `embed`/`restore` carry bytes through
`/export`/`/import`.
`read` can return an image (`worker.read` with the harness-set
`max_image_bytes`); `Harness._take_tool_image` caches it and a `source: "tool"`
user entry carries it after the tool results.

Image support per model: `Endpoint.accepts_images()` (True/False/None) reads
the catalog's `images`, filled by `list_models()` (llama.cpp `/props`
`modalities.vision`; OpenRouter and OpenAI-compatible `/models`
`architecture.input_modalities`).

### `usage.py`
`TokenUsage` (provider-neutral prompt/completion/total/cache/cost counts; each
provider fills it from its own fields) and `MetricsState`.

### `llm_status.py`
`AgentState` enum: `IDLE`, `THINKING`, `ANSWERING`.

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

### `compaction.py`
`transcript(history, filter_thoughts, filter_tool_calls)` — the text
`compact_history` gives the summarizer instead of the stored entries: `User:` /
`Assistant:` lines, `Earlier summary:` for a previous marker, `Note:` for system
notices; no ids, image references, `origin` or native reasoning. Filtered tool
calls are one line (`read a.py lines 40–120`, `bash: pytest -q → exit 1`,
`write n.py (3 lines)`, `edit a.py`), plus the first line of the result when it
starts with `Error:` or `[TOOL DENIED]`; unfiltered, the call and its result.
Reasoning is `[thinking] …` only when `filter_thoughts` is off. Owns
`COMPACTION_MARKER_PREFIX`.

### `changes.py`
`changes(workspace)` → `[Change(path, status, added, removed)]` (`None` outside a
git repository): `git status --porcelain -z -uall` plus `git diff --numstat`
against `HEAD`, scoped to the workspace folder; `cached_changes` (3 s) for the `/`
menu; `diff_text(workspace, path=None, context=3)` the unified diff (`context`: N
lines, `"function"` or `"all"`), untracked files via
`git diff --no-index`. Read-only: git only ever runs `status`, `diff`,
`rev-parse`, `hash-object`, with `GIT_OPTIONAL_LOCKS=0`. Used by `/diff`.

### `roles.py`
`Role` — the single source of truth for a conversation's operating mode: a
`description`, a `prompt`, a `tools: dict[str, str]` mapping each registered
tool to exactly one of `no` / `ask` / `yes`, and `require_sandbox` (bool,
default false) and `replay_reasoning_depth` (int ≥ 0, default 999 = all: how many turns
of reasoning are sent back, `notes/reasoning-traces.md`). There is no
permission engine.
- `require_sandbox = true` locks the conversation while no sandbox is active:
  `Harness.sandbox_required()` gates `chat()` (yields an `Error`) and the UI
  refuses normal submissions. Activating a sandbox lifts it automatically.
- Built-in roles `agent` (all tools `yes`) and `chat` (all tools `no`) are
	seeded as files by `ensure_roles_dir()` only into an empty roles folder
	(first run); there is no code fallback: a deleted role stays deleted.
- `ensure_role_file(name)` writes a template listing every registered tool with
	value `no` (all disabled) when the file is missing; `delete_role(name)` unlinks, refusing to remove
	the last role. `load_role` / `list_roles` read one file per role at
	`~/.config/moka/roles/<name>.toml`.
- `validate_roles()` reports unknown tool names and values other than
	`no`/`ask`/`yes` as `roles/<name>.toml: ...`, surfaced by `/reload` and
	`/config role`.
- `default_role()` picks the starting role (`agent`, else the first that loads, else
	the `(no role)` placeholder); `Harness.role_problem()` reports how the running role
	(kept in memory) differs from its file. A role change is
	represented by one system history notice; consecutive notices are collapsed.

### `context_builder.py`
`list_files_bounded()` — breadth-first, depth/max-files-bounded walk used by the `@` file picker so it stays responsive on huge trees (e.g. `$HOME`); respects `.gitignore` unless `ignore_gitignore` is set. It lists the whole directory outside a git repo too, so the picker works everywhere. `get_ignore_spec()` loads `.gitignore`.
- No project tree or date is put in the prompt: the system prompt is the active role's `prompt` only (the earlier `build_harness_context` result was never sent, and was removed with `context.format`).

### System prompt

There is no `system_prompt.py`. The entire system message is the active role's
`prompt` field (empty → no system message). `Harness._system_messages()` builds
it. It is edited as a role file
(`roles/<name>.toml`), never in code.

### `debug.py`
`DebugStream` — structured debug logging to JSON. Used for dev; not active in production builds.
