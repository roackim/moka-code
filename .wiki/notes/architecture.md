# Architecture

Pico is a terminal-based AI agent that connects to local (llama.cpp) or cloud (OpenRouter, OpenAI) LLMs, exposes file and shell tools to the agent, and presents a custom TUI chat interface.

---

## High-Level Layers

```
┌─────────────────────────────┐
│         pico_chat/ui/       │  TUI — user input, chat display, commands
│  app.py  ← commands/        │
│  chat_history_panel.py      │
│  chat_action_handlers.py    │
└────────────┬────────────────┘
             │ async messages / callbacks
┌────────────▼────────────────┐
│      pico_chat/harness/     │  Agent core — LLM loop, tools, approval gate
│  harness.py (main loop)     │
│  endpoint.py (+ endpoint_*) │
│  tools.py (registry)        │
│  permissions.py / roles.py  │
└────────────┬────────────────┘
             │ HTTP / websocket
┌────────────▼────────────────┐
│     LLM Backend             │  llama.cpp server or OpenRouter/OpenAI API
└─────────────────────────────┘
```

## Entry Point

`pico_chat/main.py` — async launcher that:
1. Loads config via `pico_cfg.py`
2. Instantiates `Harness` and `chatTUI`
3. Starts the async TUI event loop

## Agent Loop (`harness.py`)

The core reasoning loop:
1. Build the message list (system prompt from the active role + history)
2. Send to the active `Endpoint` (`endpoint.py`)
3. Stream response events (`events.py`): `Token`/`Reasoning`/`Usage` (tool calls
   are assembled during the stream but emitted in step 4, so all content is
   flushed before the first tool)
4. If tool calls present → `ToolCall` → `PermissionRequest` → check permissions → execute tools → `ToolResult` (one tool at a time: a pending `ask` blocks later tools)
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

`~/.config/pico-chat/` — single-concern files (`ui.toml`, `context.toml`,
`debug.toml`, `styles.toml`, `servers.toml`), one role per
file at `roles/<name>.toml`, and a disposable `state.toml`; loaded by
`pico_cfg.py`. See [notes/config.md](./config.md).

## Key Design Decisions

- **Custom TUI** — no curses or third-party TUI library; full control over rendering pipeline
- **Streaming-first** — LLM output streams token-by-token to the buffer; no waiting for full response
- **Approval gate** — every tool call goes through `PermissionGate` (`permissions.py`), which maps the active role's per-tool setting (`no`/`ask`/`yes`) to a decision before execution; the UI can pause to ask the user
- **Stateless tools** — tools are pure functions; harness owns all state
- **Endpoints** — server config + transport live in one `Endpoint` type (`harness/endpoint.py`, with `endpoint_*` modules for transport/discovery); UI commands are thin adapters
- **Model selection is `(server, model)`** — `/model` refreshes discovery live, resolves a model across servers, then switches the harness. Per-server choices persist in `state.toml`; the catalog is a completion cache. OpenRouter models are disabled unless listed in `enabled_models`.
- **Thinking-tag parsing** — the state machine (`harness/thinking_parser.py`) handles `<think>`/`</think>` and `<thinking>`/`</thinking>` across chunk boundaries

## Module Relationships

```
pico_chat/
  harness/
    harness.py           ← Orchestrator (delegates to modules below)
    permissions.py       ← Single decision point: PermissionGate (role no/ask/yes → deny/ask/allow)
    roles.py             ← Role (prompt + per-tool no/ask/yes; single source of truth)
    thinking_parser.py   ← Thinking-tag state machine + metrics emission
    endpoint.py          ← Endpoint config + transport; endpoint_* modules split the families
    tools.py             ← Tool implementations + @tool registry (read/write/edit/bash)
    ...

  ui/
    commands/            ← Slash commands package (registry.py assembler; domain modules import base only)
    chat_action_handlers.py
    app.py               ← Main TUI class
    tui/
      ...
```
