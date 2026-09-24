# Pico-Chat — Handoff

**Branch:** `cleanup` · **Suite:** 540 passing · **Last updated:** 2026-09-24
**HEAD:** `caaa359 UI tweaks`
**Recent commits (newest first):**
`caaa359 UI tweaks` · `cc9fbf2 pruning` · `43d0ca7 Input completion unification`
· `2e85f3d split god classes` · `db40188 reworked command declaration`

**Tree:** the command-interface / model-picker / menu work is in `caaa359`; a
further polish batch (green `active` footer, picker anchored above the input,
query-in-title, 1-space bar padding, input-field rules, message-prefix color) is
**uncommitted** (partly staged, partly unstaged). The tool-definition rework
(`read`/`write`/`edit`/`bash`; subagents removed — see §3) is also
**uncommitted**. Untracked scratch:
`old_HANDOFF.md`, `plans/cleanup_round2.md`, `test.json`. The user commits
manually; **the assistant never stages or commits.**

Read this to resume. The big simplification (`R1–R9`) and cleanup round 2
(`C1–C6`) are done. Most recently: a command-surface rework (`/import`/`/export`,
leaf `/model` with a new searchable picker) and a UI polish pass.

Canonical plans: `SIMPLIFICATION.md` (R1–R11), `plans/message_ui_rework.md`,
`plans/cleanup_round2.md` (C1–C6, per-item status).

---

## 1. Commands / gates

```bash
.pixi/envs/default/bin/python -m pytest test/ -q            # 457 passing
.pixi/envs/default/bin/python -m compileall -q pico_chat
.pixi/envs/default/bin/python -m vulture pico_chat --min-confidence 80
.pixi/envs/default/bin/python -m pytest test/test_core_ui_boundary.py -q        # R9 guard
.pixi/envs/default/bin/python -m pytest test/test_command_import_graph.py -q    # command graph guard
```

No lint/typecheck beyond these. Keep the suite green and both guards passing.

---

## 2. Architecture (current)

### Harness (no `ui/` imports — R9 guard)
- **Events:** `harness/events.py` is the one harness→UI union (`Start`, `Token`,
  `Reasoning`, `ToolCall`, `PermissionRequest`, `ToolResult`, `Usage`, `Error`,
  `Done`). `chunks.py` is gone.
- **Endpoints:** `harness/endpoint.py` is the one concrete `Endpoint` (config +
  state + dispatch). Server-family code is split out:
  `endpoint_openai.py` (SSE transport/adapters), `endpoint_ollama.py` (native
  chat + context), `endpoint_discovery.py` (`list_models`/`discover_models`/
  `query_*`), and `endpoint_local.py` (`.local` mDNS resolution). `Endpoint`
  keeps thin delegating wrappers so callers/tests are unchanged. `llm_server*.py`
  / `server_service.py` are gone. The Ollama path normalizes outgoing messages
  (`Endpoint._ollama_messages` → `endpoint_ollama.ollama_messages`): `content
  None → ""`, drops `tool_calls: None` (Ollama/proxies reject null content with
  HTTP 422).
- `Harness.endpoint` (not `.server`); fixtures use `harness.endpoint = FakeServer()`.

### Config
User-level only, split per concern under `~/.config/pico-chat/`: `ui.toml`,
`context.toml`, `debug.toml`, `styles.toml`, `servers.toml`,
`roles/<name>.toml`, disposable `state.toml`. `/config <section>` edits a file
and reloads. `pico.toml` is gone. `pico_cfg.config` is a global loaded at import;
`/reload` mutates it in place (no watchers).

### App / conversation
One conversation per process — no tabs/`ConversationRuntime`. `chatTUI` owns the
agent, panel, queue, task, and tool/permission state. Presenter helpers live
beside it and keep `chatTUI` thin (methods delegate):
- `ui/generation_presenter.py` — harness event → transcript messages
- `ui/status_presenter.py` — status-bar values/colors
- `ui/shell_command.py` — the `$` shell escape

### Commands
`ui/commands/registry.py` is the single assembly point. Leaf commands are plain
`async def` handlers wrapped in `Command(name, description, handler=…, params=…)`;
`ConfigCommand` is the only `Command` subclass (contextual role/theme
completion). `help`'s handler is `registry._help` (it needs the whole registry).
Domain modules import **only** `base` (enforced by
`test_command_import_graph.py`).

Registered: `help`, `clear`, `reload`, `config`, `edit`, `export`, `import`,
`compact`, `exit`, `stop`, `activity`, `model`, `role`, `theme`.
The old `/pwd`, `/cd`, `/server`, `/debug`, `/openrouter`, `/status` commands
(and earlier `/roles`/`/tools`) are **removed**; server config is files
(`/config servers`), model selection is `/model`, themes are `/theme`, debug
logging is `debug.toml`.
- `/import <file>` and `/export <file>` are top-level leaves (the old
  `/conversation import|export` tree is gone).
- `/model` is a leaf: no args → searchable picker; `<model>`/`server:model` →
  select directly. `/model list` is gone.
- `/role` lists/switches roles; `/theme` picks a color theme (built-ins +
  `themes.toml`), persisted in `state.toml`.
- Descriptions flow to the UI via `get_command_descriptions()` /
  `get_subcommand_descriptions()`; argument menus also show `Param.descriptions`.

### Input completion
One module, `ui/tui/components/input/completion.py`, holds the `Completer` base
and the four trigger-based providers (`CommandCompletion`,
`SubcommandCompletion`, `ArgumentCompletion`, `ContextCompletion`). Uniform
interface: `accept_selection(text, cursor) -> (text, cursor)` and
`trigger_pos(text, cursor)`. `input.py` drives them with one priority loop
(`_completion_providers()` + `_position_menu()`).

`SelectionMenu` renders items with:
- `item_descriptions` — muted, aligned right of the name (`_tail_len`);
- `item_footers` + `footer_color=theme.SUCCESS` — e.g. the green `active` tag;
- `title` (inline in the top border), `status_text` (bottom-left), `min_width`,
  `fill_width`, `measure_width()`.
- **It must clear its whole rectangle first** (background fill) — overlay cells
  aren't auto-blanked, otherwise the action bar shows through.
- `/` and `@` menus use `fill_width=True`, `frame_color=theme.USER`.

### Search modal (`/model` picker)
`ui/tui/components/search_modal.py` — `SearchModal(SelectionMenu)`, a centered or
anchored type-to-filter picker.
- `app.show_search_modal(...)` anchors it flush above the input field via
  `InputComponent.place_menu_above_input` (reuses the same `_position_menu_at`
  primitive as the `/` and `@` menus), full-width.
- Opens **instantly from the cached catalog** (`_cached_pairs`) and refreshes
  live discovery in the background (`asyncio.ensure_future`, bounded by
  `_DISCOVERY_TIMEOUT = 8.0`s), so an unreachable server can't stall it.
- Rows: model id primary; muted aligned `server  context`; current model gets a
  green `active` footer. Query shows in the title: `Models: qwe `.
- Keys: type to filter, ↑/↓ move, Enter select, Esc cancel, Backspace edits.
  Empty matches keep the modal registered and render a "no matches" box.

### Transcript rendering
- `Box` (`ui/tui/components/box.py`) is message-agnostic: borders, padding,
  clipping, `lines_only`, generic popup actions + hit regions, `inline_editor`.
- `ui/tui/components/message_view.py` — `MessageView(Box)` owns thread rendering,
  the lifecycle gutter, and collapsed lines. `Message.box` is a `MessageView`.
- `ui/message_selection.py` — `SelectionState` + `MessageSelection` own drag
  state, column resolution, text extraction, highlight overlay
  (`panel.selection`). Content geometry comes from `Box.thread_content_width`
  and the laid-out child; the panel must not recompute gutter/padding.
- Inter-message gap: `ui_msg_v_margin`. Padding centralized in `Box`/`MessageView`.
- During a generation, `ui/generation_presenter.py` maps events to messages;
  `SysMsg*` are routed to the activity surface, not the transcript.

### Bottom UI (current styling)
- **Status bar / action bar:** 1-space horizontal padding (`BarStyle(padding=1)`).
- **Input field:** a `lines_only` Box. Content is inset 1 space each side
  (no `▸` prefix; `InputComponent` prompt is `""`). Its top/bottom rules span the
  **full width** and end in light half-lines `╶`/`╴`
  (`Box._render_lines_only_to_subbuffer`).
- **Message prefix:** always heavy `▌` in the message/frame color; when a message
  is focused the history panel paints `▌` in `theme.FOCUSED` over it.
- Menus/popups stay **normal boxes** with `┌ ┐ └ ┘` corners.

### Clipboard / SSH
`ui/clipboard.py` (`copy_to_clipboard`) is the single owner: native
`xclip`/`xsel`/`wl-copy` first, then OSC 52 (`harness/clipboard.py`). OSC 52
payload truncates on a 4-byte base64 boundary so it stays decodable. Feedback
distinguishes verified copies (`copied ✓`) from OSC 52 (`sent via OSC 52`).
**VTE terminals (Ptyxis/GNOME Terminal) ignore OSC 52** — use `ssh -X` (X11
forwarding, `xclip`) or an OSC 52-capable terminal.

---

## 3. Done recently

- **Tool-definition rework (uncommitted):** the tool surface is now exactly
  `read` / `write` / `edit` / `bash`. `patch`→`edit`, `run`+`run_command`→`bash`;
  the registry `key=` aliasing and `permissions._TOOL_ALIASES` are gone (LLM name
  == registry key). The legacy `patch_content` argument was deleted. `tools.py`
  collapsed `_SyncTool`/`_AsyncTool`/`_AsyncCapableTool` into one `RegisteredTool`
  whose async `execute()` prefers the async handler; `ToolContext` and the
  `RunTool`/`SubagentTool`/`WaitForSubagentsTool` factories are gone, and
  `create_toolset(workspace_path)` is the only factory.
- **Subagents removed (uncommitted):** the `subagent` / `wait_for_subagents`
  tools, `scaffolder_role()`, the child-`Harness` plumbing (`depth`/`role`
  params, `_pending_subagents`, `_abort_subagents_event`, `abort_subagents()`,
  `_auto_wait_subagents()`), and the whole `subagents` config section
  (`subagents.toml`, `_SUBAGENT_SPEC`, defaults) are gone. `test_subagents.py`
  deleted.
- **Roles rework (W1–W4 of `plans/roles_rework.md`):** the permission engine is
  gone. A `Role` is now `description` + `prompt` + `tools: dict[str, str]`
  (`no`/`ask`/`yes`); `PermissionGate` maps the value to `deny`/`ask`/`allow`
  and owns only the prompt + user-response queue. `permissions.py` shrank from
  ~760 to ~100 lines (no `SecurityChecker`, no command lists, no profiles, no
  path confinement). `tools.py`/`harness.py` lost all policy plumbing.
  Built-ins `agent` (all `yes`) and `chat` (all `no`) are seeded as files by
  `roles.ensure_roles_dir()`; `create_role` writes all tools `no`. Commands:
  `/role` (list/switch),
  `/config role <id>` and `/config role delete <id> confirm`; `/tools` deleted.
  Role files are validated by `roles.validate_roles()` (surfaced by `/reload`).
- **System prompt is role-owned:** `harness/system_prompt.py` is deleted. The
  system message is exactly the active role's `prompt` (empty → no system
  message), built by `Harness._system_messages()`; `get_system_prompt()` returns
  it. Edit it in `roles/<name>.toml`. `/config role` now offers role-name
  completions (`ConfigCommand.get_completions` + `Command.get_completions`
  gained a `prior_args` parameter).
- **Themes:** `themes.toml` (`[themes.<name>]` palettes) + `/theme` picker
  (`terminal` default, plus 13 RGB built-ins: `pastel`, `nord`, `dracula`,
  `gruvbox`, `solarized`, `one-dark`, `catppuccin`, `tokyo-night`, `rose-pine`,
  `everforest`, `monokai`, `ayu-dark`, `kanagawa`; user definitions merge in;
  ANSI or hex colors). No `default` alias.
  Selection persists in `state.toml` (`active_theme`); `/config theme` edits the
  file and `/config theme <id>` materializes a `[themes.<id>]` override section
  (with theme-name suggestions) then opens it. Palette-only; markdown/syntax styles stay in `styles.toml`. The picker
  **previews on highlight** (`SearchModal.on_highlight`), shows a
  `ThemePreview` swatch overlay while browsing, and on cancel reloads the
  configured theme. `/theme` has **no inline argument completion** so Enter
  always opens the picker.
  A theme switch calls `chatTUI.refresh_theme()` (re-resolves chrome + every
  message + overlays/menus + status-bar field colors) and
  `Compositor.request_full_redraw()`.
- **Command surface trimmed:** removed `/pwd`, `/cd`, `/server`, `/debug`,
  `/openrouter`, `/status` (keeping `/activity`). Server config is files, model
  selection `/model`, themes `/theme`, debug logging `debug.toml`. Remaining:
  `help, clear, reload, config, edit, export, import, compact, exit, stop,
  activity, model, role, theme`.
- **Completion menus unified:** all four providers use
  `Completer._apply_selector_style()` (full width, `USER` frame, `DEFAULT`
  content) and show `Param.descriptions`; see the wiki rule.
- **Clipboard / SSH:** native helpers then OSC 52 (see §2). `handle_copy_action`,
  `_auto_copy_selection`, `/debug get_context` all use `ui/clipboard.py`.
- **Ollama 422 fix:** `content=None` on tool-call-only assistant turns; fixed via
  `_ollama_messages` normalization.
- **R1–R9 committed** (earlier): event union, mock server removal, config split,
  command rework, etc.
- **C1 dead-code sweep:** removed orphaned message-action handlers
  (`handle_retry/stop/steer/pause/resume`), `/resume`, `_paused_*` /
  `_requeue_after_cancel` / `is_steered`, plus orphan methods.
- **C6.1 `ui/tui` dead code:** `input_result.py`, `VerticalDivider`, ~14
  zero-reference methods/attributes.
- **C2 commands as data:** 30 leaf classes → functions; classes 38 → 8; commands
  LOC 1716 → 1581. Tests call handler functions now.
- **C3 god-file split** (`2e85f3d split god classes`): `endpoint.py`
  1,255 → 619 LOC via the `endpoint_*` modules; `app.py` 1,192 → 745 LOC via the
  presenters. `harness.py` intentionally not split (murky seams).
- **C5 completion unification** (`43d0ca7`): the 4 provider modules merged into
  `completion.py`; `input.py` 700 → 602 LOC. `@` picker behaviour unchanged.
- **C4 message UI** (`43d0ca7` + `cc9fbf2 pruning`): `ui/message_selection.py`;
  `chat_history_panel.py` 1,126 → 843 LOC; `ui/tui/components/message_view.py`;
  `Box` 719 → 479 LOC, message-agnostic (unreachable compact mode removed).
- **C6.2 toolkit pruning (conservative):** deleted `input/basic.py`
  (`LineInput`/`BoxInput`) and `example_screen.py` (+ tests); kept the generic
  `choice`/`table_view`/layout/list/container primitives. Pruned barrels:
  `components/__init__` exports `Component, TextComponent, Box, MessageView,
  InputComponent`; `tui/__init__` is a docstring; `input/__init__` exports
  `InputComponent`.
- **Command interface rework** (`caaa359 UI tweaks`): top-level `/import` /
  `/export`; leaf `/model`; new searchable picker; per-command/subcommand
  descriptions in the `/` popup; popup background fill.
- **Uncommitted polish batch:** green `active` footer; picker anchored above the
  input with query-in-title and no placeholder; 1-space status/action bar
  padding; input field 1-space content inset, `▸` removed, full-width half-line
  rules; message prefix color follows focus.
- Earlier UI polish: `▌` prefix bar, `ui_msg_v_margin`, centralized `Box`
  padding, right-aligned action line, fuzzy-menu styling, Ctrl+C quits on first
  press, `ui_max_input_height`.

---

## 4. What needs change / next

- **Optional picker polish:** breadcrumb/header for the drilled directory;
  tail-truncation for deep paths; dir/file styling. (Match highlighting was
  deliberately removed.)

Deferred: replace the bespoke TUI toolkit; derive tool schemas from signatures;
built-in mini editor.

Open improvements not yet requested but worth considering:
- `ListModal`/`ListView` are now only used by tests (the app's modal picker path
  moved to `SearchModal`); decide whether to keep or prune under the C6.2 policy.

---

## 5. Gotchas

- Tests isolate config by monkeypatching **module functions**
  (`pico_cfg.get_config_dir`, `get_state_path`, `get_roles_dir`,
  `roles._ROLES_DIR`). Follow that pattern for new tests.
- Roles: `Role.tools` is a `dict[str, str]` of `no`/`ask`/`yes`;
  `enabled_tool_names()` = values != `no` (the harness filters `tools_map` /
  `tool_schemas` by it). `PermissionGate(role=…)` → `allow`/`ask`/`deny`.
  Role files are top-level `<tool> = "…"` keys beside `description`/`prompt`.
  Built-in files are seeded on startup; `roles.validate_roles()` reports
  unknown tools / bad values. `/reload` runs it.
- **Tool names are the registry keys** (`read`/`write`/`edit`/`bash`); the LLM
  name always equals the key (no alias). Old role keys are handled: on startup
  `ensure_roles_dir()` rewrites `patch → edit` and `run_command → bash` and drops
  `subagent` / `wait_for_subagents` (comments preserved), and `_role_from_dict`
  applies the same aliases on load, so stale files no longer error.
  `create_toolset(workspace)` builds the map; `RegisteredTool.execute()` is
  async.
- **Themes resolve at construction, not render.** After `set_theme()` you must
  call `chatTUI.refresh_theme()` (chrome + long-lived overlays `Popup`/
  `DebugPopup` + cached completion menus + `ChatHistoryPanel.refresh_theme()` /
  `Message.refresh_theme()`) and `Compositor.request_full_redraw()`, otherwise
  cached fg/bg cells stay stale. `/theme` and `_apply_theme()` do this; do the
  same from any new theme surface. Components that keep a `theme.*` color must
  expose `refresh_theme()`/`apply_theme()` and be wired in.
- `harness/` must not import `ui/` (R9 guard); only `main.py` may import both.
- **Command import graph** (`test/test_command_import_graph.py`): domain modules
  under `ui/commands/` may import **only** `base`; `registry.py`/`__init__.py` are
  the assemblers. Don't add a lazy `core → registry` import — inject via the
  registry (see `_help`).
- Command tests call handler **functions** (`cmd_config`, `cmd_edit`,
  `cmd_reload`, `conversation_export`, `conversation_import`, `model_command`),
  not classes.
- Ollama native API requires string `content`; keep `ollama_messages`
  normalization when touching the chat payload. `Endpoint._ollama_messages` /
  `_native_response` / `_create_ollama_completion` are thin wrappers over
  `harness/endpoint_ollama.py` (tests pin the wrappers).
- `.local` resolution lives in `harness/endpoint_local.py`; tests patch
  `endpoint_local._getent_host`, not `endpoint._getent_host`.
- Input completion is one module (`input/completion.py`); tests import
  `ContextCompletion` from `.completion`. **All four providers must style their
  menu via `Completer._apply_selector_style()`** (full width, `theme.USER`
  frame, `theme.DEFAULT` content) and pass descriptions to
  `_show(..., descriptions=...)`; never restyle one menu inline. Descriptions
  flow through `Command.get_descriptions(arg_index, prior_args)` /
  `Param.descriptions`. See `.wiki/notes/ui.md` → "Completion menu styling".
- `SelectionMenu.render` must bg-fill its whole rectangle before drawing (overlay
  cells aren't auto-blanked). The `/` and `@` menus set `fill_width` +
  `frame_color=theme.USER`; item descriptions/footers use `_tail_len` for width.
- The input field is a `lines_only` Box: content inset 1 space each side, no
  `▸`, and its rules span full width ending in `╶`/`╴`
  (`Box._render_lines_only_to_subbuffer`). Popups stay normal boxes with corners.
- Command surface: `/import` + `/export` (top-level), leaf `/model` (no args →
  picker). `get_command_descriptions()` feeds `InputComponent.setup_commands`;
  `SelectionMenu.item_descriptions` renders them muted/aligned.
- Picker: `SearchModal` is anchored above the input via
  `InputComponent.place_menu_above_input`; it opens from `_cached_pairs` and
  refreshes via `_discover_all` with `asyncio.wait_for(..., _DISCOVERY_TIMEOUT)`.
- Transcript text selection lives in `ui/message_selection.py`
  (`panel.selection`), not on `ChatHistoryPanel`. Geometry comes from
  `Box.thread_content_width` / the laid-out child.
- `Message.box` is a `MessageView` (thread-mode `Box` subclass). `Box` itself is
  message-agnostic — don't add message fields to it.
- Message prefix bars are `▌` in the frame color; focus recolors them via the
  panel's `▌` (FOCUSED) selection bar.
- Clipboard/X11 code must stay import-safe in headless test runs. VTE terminals
  do not implement OSC 52.
- Form stack is gone (`TextField`, `FormPopup`, `ConfigOverlay`, `RoleEditorForm`
  etc.) — use `Button`/`Label` as test stand-ins.
- Untracked scratch: `old_HANDOFF.md`, `plans/cleanup_round2.md`, `test.json`
  (origin unknown — inspect before committing).

---

## 6. Working preferences

Canonical principles: [`.wiki/notes/principles.md`](./.wiki/notes/principles.md).
Quick reminders:

- Aggressive simplification toward the essence; explicit over implicit
  (`/reload`, no watchers).
- Config files over UI; edit with `$EDITOR`.
- Keeps the custom TUI aesthetic; do not delete the TUI toolkit.
- Delete before you design; each step ends with fewer files/layers/lines.
- Terse answers; corrections welcome.
- **Do not commit unless asked** (and never `git add`).
