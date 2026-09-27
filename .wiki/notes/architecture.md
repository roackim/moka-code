# Architecture

Moka is a terminal-based AI agent that connects to local (llama.cpp) or cloud (OpenRouter, OpenAI) LLMs, exposes file and shell tools to the agent, and presents a custom TUI chat interface.

---

## High-Level Layers

```
┌─────────────────────────────┐
│         moka_chat/ui/       │  TUI — user input, chat display, commands
│  app.py  ← commands/        │
│  chat_history_panel.py      │
│  chat_action_handlers.py    │
└────────────┬────────────────┘
             │ async events / callbacks
┌────────────▼────────────────┐
│      moka_chat/harness/     │  Agent core — LLM loop, tools, approval gate
│  harness.py (main loop)     │
│  endpoint.py (+ endpoint_*) │
│  tools.py (registry +       │
│    ToolTransport seam)      │
│  permissions.py / roles.py  │
│  elision.py                 │
└──────┬───────────────┬──────┘
       │ HTTP          │ tool call (transport)
┌──────▼──────┐  ┌─────▼───────────────────────────┐
│ LLM Backend │  │ InProcessTransport (bare)       │
│ llama.cpp / │  │  or SandboxTransport → worker   │
│ OpenRouter  │  │  (moka_chat/worker.py) in a     │
│             │  │  container / bubblewrap         │
└─────────────┘  └─────────────────────────────────┘
```

`moka_chat/worker.py` (stdlib-only) holds the tool bodies + JSONL protocol;
`moka_chat/sandbox.py` launches and talks to it; `moka_chat/projects.py` stores
per-project sandbox definitions. See [sandbox.md](./sandbox.md).

## Entry Point

`moka_chat/main.py` — async launcher that:
1. Loads config via `settings.py` (and syncs flat section templates)
2. Builds the `Harness` via `get_harness()`, which activates the current
   project's sandbox (if any) as its tool transport
3. Instantiates `chatTUI` and starts the async event loop

## Agent Loop (`harness.py`)

The core reasoning loop:
1. Build the message list (system prompt from the active role + history)
2. Send to the active `Endpoint` (`endpoint.py`)
3. Stream response events (`events.py`): `Token`/`Reasoning`/`Usage`, plus a live
   `ToolCallDraft` per tool call while its arguments stream. The complete
   `ToolCall` is emitted in step 4, so all content is flushed before the first tool
4. If tool calls present → `ToolCall` → `PermissionRequest` → check permissions → execute the tool through the active `ToolTransport` (in-process or sandbox; interim `ToolOutput` may stream from `bash`) → `ToolResult` (one tool at a time: a pending `ask` blocks later tools). Completed results are head/tail-elided before entering history (`elision.py`); the event keeps the full output
5. Append tool results to history → repeat from step 2 until no more tool calls → `Done`

## Data Flow: User Message → Response

```
User types → InputComponent
           → chatTUI.handle_submit()
           → Harness.chat()
           → Endpoint.create_completion()
           → events yielded → UI renders streaming tokens
           → tool call detected → PermissionGate checks the active role's per-tool setting
           → tool executed → result appended to history
           → next iteration until IDLE
```

## Config

`~/.config/moka/` — single-concern files (`ui.toml`, `context.toml`,
`debug.toml`, `styles.toml`, `servers.toml`), one role per
file at `roles/<name>.toml`, per-project sandboxes at `projects/<name>.toml`,
and a disposable `state.toml`; global config loaded by `settings.py`, project
files by `projects.py`. See [notes/config.md](./config.md).

## Key Design Decisions

- **Custom TUI** — no curses or third-party TUI library; full control over rendering pipeline
- **Streaming-first** — LLM output streams token-by-token to the buffer; no waiting for full response
- **Approval gate** — every tool call goes through `PermissionGate` (`permissions.py`), which maps the active role's per-tool setting (`no`/`ask`/`yes`) to a decision before execution; the UI can pause to ask the user
- **Stateless tools, swappable transport** — tool bodies are pure functions in `worker.py`; the harness executes them through a `ToolTransport`, either `InProcessTransport` (bare) or `SandboxTransport` (container/bubblewrap). Same registry/schemas in both
- **Sandbox is transport, not policy** — moka parses no commands and confines no paths; a container/bwrap mount is the wall, and the user names the backend
- **Endpoints** — server config + transport live in one `Endpoint` type (`harness/endpoint.py`, with `endpoint_*` modules for transport/discovery); UI commands are thin adapters
- **Model selection is `(server, model)`** — `/model` refreshes discovery live, resolves a model across servers, then switches the harness. Per-server choices persist in `state.toml`; the catalog is a completion cache. OpenRouter models are disabled unless they have a `[servers.<name>.models."<id>"]` table.
- **Thinking-tag parsing** — the state machine (`harness/thinking_parser.py`) handles `<think>`/`</think>` and `<thinking>`/`</thinking>` across chunk boundaries

## Module Relationships

```
moka_chat/
  worker.py              ← Stdlib-only tool bodies (read/write/edit/bash) + patch parser + JSONL worker protocol
  sandbox.py             ← Launcher + SandboxProcess/SandboxTransport (container/bwrap argv, preflight, build)
  projects.py            ← Per-project sandbox store (~/.config/moka/projects/<name>.toml)
  main.py                ← Async launcher
  harness/
    harness.py           ← Orchestrator (delegates to modules below); builds/swaps the transport
    permissions.py       ← Single decision point: PermissionGate (role no/ask/yes → deny/ask/allow)
    roles.py             ← Role (prompt + per-tool no/ask/yes + require_sandbox; single source of truth)
    thinking_parser.py   ← Thinking-tag state machine + metrics emission
    endpoint.py          ← Endpoint config + transport; endpoint_* modules split the families
    tools.py             ← @tool registry + schemas; InProcessTransport; delegates bodies to worker.py
    elision.py           ← Bound oversized tool results before they enter history
    ...

  ui/
    commands/            ← Slash commands package (registry.py assembler; domain modules import base only)
    chat_action_handlers.py
    app.py               ← Main TUI class
    tui/
      ...
```
