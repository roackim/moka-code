# moka_chat/ui/ — Chat UI Layer

Async TUI built from scratch. Handles chat display, user input, message
actions, and slash commands. See [notes/ui.md](../notes/ui.md) for the full
architecture overview.

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | `chatTUI` — main application class: single conversation, layout, compositor, session, popup overlay |
| `chat_history_panel.py` | `ChatHistoryPanel` — scrollable transcript: messages, focus, mouse selection, action hit testing |
| `chat_message.py` | `Message` — wraps content with a `MsgType`, colors, padding, and action set |
| `chat_action_handlers.py` | `ChatActionHandlers` mixin for `chatTUI` — copy/delete/edit message actions |
| `message_selection.py` | `MessageSelection` — terminal-style selection over rendered cells (anchor/head in transcript rows), copy text, highlight |
| `generation_presenter.py` | Maps harness generation events onto transcript messages |
| `stream_revealer.py` | `StreamRevealer` — paces streamed-text reveal (no TUI imports; time injected) |
| `status_presenter.py` | Renders agent/endpoint state into the status bar |
| `shell_command.py` | The `$` shell-command escape for the chat input |
| `clipboard.py` | `copy_to_clipboard` (native helpers, then OSC 52); `read_clipboard` for Ctrl+V (image bytes or text) |
| `external_editor.py` | Opens files in `$VISUAL`/`$EDITOR`, suspending the TUI |
| `logging_handlers.py` | `TuiLogHandler` — routes log records to the debug panel |
| `__init__.py` | (empty) |

### Key details

- **`chatTUI`** owns one conversation: `agent`, `chat_history_panel`,
  `message_queue`, `current_generation_task`, `active_tool_messages`,
  `pending_permission_prompt`. `switch_role(role)` applies a role via
  `agent.set_role` and emits a de-duped role-change notice. Ordinary, edited,
  retried, and resumed messages share one enqueue path.
- **Action surface**: a collapsible `ActionBar` above the input shows the
  selected message's actions, or input prefixes (`/`, `@`, `$`) when focused.
- **Activity overlay**: `SysMsg*` is routed to the activity surface (a
  `DebugPopup`) instead of the transcript; `/activity` toggles it.
- **Clipboard**: `ui/clipboard.py` is the single owner (native
  `xclip`/`xsel`/`wl-copy`, then OSC 52). VTE terminals ignore OSC 52.
  Reading (Ctrl+V) uses `wl-paste`, `xclip`, then `xsel`; no OSC 52 read.

---

## `commands/` (package)

Slash command system. Leaf commands are plain `async def` handlers wrapped in
`Command(...)`; only real subcommand trees are classes.

| File | Purpose |
|------|---------|
| `registry.py` | The single `COMMANDS` assembly point; `handle_command`, description/completion helpers |
| `base.py` | `Command`, `Param`, `ChatUIProtocol`, completion helpers (`config_section_completions`, `role_*`) |
| `core.py` | Core commands: help, clear, reload, config, edit, compact, exit, stop, activity |
| `conversation.py` | `/import`, `/export` |
| `models.py` | `/model` leaf (picker or direct selection) |
| `roles.py` | `/role` — list roles or switch the active one |
| `sandbox.py` | `/sandbox` — verb-first: `config`, `build <id>`, `start [id]`, `init <podman\|docker> [base]`, `quit` |
| `themes.py` | `/theme` — picker or direct color-theme selection |

Registered commands: `help`, `clear`, `reload`, `config`, `edit`, `export`,
`import`, `compact`, `exit`, `stop`, `activity`, `model`, `role`, `sandbox`,
`theme`.

- `/config <section>` opens a section file in `$EDITOR` and reloads (sections:
  `ui`, `context`, `debug`, `styles`, `servers`, `theme`).
  `/config sandbox` opens the current project's `projects/<name>.toml` and
  applies its `active` entry;
  `/config role <name>` creates/opens `roles/<name>.toml`;
  `/config role delete <name> confirm` removes it (`_config_role` in `core.py`);
  `/config theme <id>` materializes a `[themes.<id>]` section then opens
  `themes.toml` (`_config_theme`). Both offer id completions via `ConfigCommand`.
- Domain modules import **only** `base`; `registry.py` is the assembler
  (enforced by `test/test_command_import_graph.py`).
- See [notes/tools-and-permissions.md](../notes/tools-and-permissions.md) for the role model and
  [notes/config.md](../notes/config.md) for themes.

---

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| [tui/](./ui-tui.md) | Low-level terminal rendering engine |
