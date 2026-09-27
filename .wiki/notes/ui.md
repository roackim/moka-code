# UI Architecture

Moka's TUI is built from scratch — no curses, no third-party TUI framework. It owns the full rendering pipeline.

---

## Layer Stack

```
chatTUI (app.py)
  └─ Compositor (tui/compositor.py)       ← render loop, FPS throttle
       ├─ Container layout (tui/container.py)
       │    ├─ ChatHistoryPanel           ← scrollable message list
       │    ├─ InputComponent             ← multi-line editor
       │    └─ DebugLogPanel (optional)   ← dev logging
       └─ Overlays (floating, on top)
            ├─ SelectionMenu              ← autocomplete dropdowns
            └─ Popup                      ← centered text popups (/help, /config)
```

Typed event dataclasses are defined in `tui/events.py`. Shared focus ownership
is provided by `tui/focus.py`.
`FocusScope` provides modal focus boundaries. `EventRouter` dispatches keyboard
events to the active focus target after application policy handling.
`EventRouter` provides overlay-priority dispatch and layout-based mouse
hit-testing for compositor input.
Keyboard input is normalized to string-compatible `KeyEvent` objects at the
terminal boundary, and terminal resize notifications are dispatched as
`ResizeEvent` objects.
The application-level input/history focus state is backed by `FocusScope`; its
domain-specific Up/Down and inline-editing rules remain in `chatTUI`.

There is one conversation per process: the app owns its runtime agent, history
panel, queue, worker, and conversation-local tool/permission state. Slash
commands use one application-level worker and remain responsive while
conversation generation is running.

## Startup banner (`ui/banner.py`)

An empty transcript shows the moka art (letters + cup), centered and drawn by
`ChatHistoryPanel._render_banner` — not a message, so it never enters history
or exports; it disappears with the first message and returns after `/clear`.
`banner_lines(width)` degrades: letters + cup (70 cols), letters only (46), the
plain name, nothing. It is drawn in the normal text color (`theme.DEFAULT`).
`show_banner = false` in `ui.toml` turns it off.

## Status Bar

The chat workspace includes a one-line `StatusBar`. Its visible fields and
order come from `settings.config.ui_status_bar_fields`; the default is:

```toml
[ui]
status_bar_fields = ["endpoint_model", "role", "context", "cost", "sandbox"]
```

The default display is `endpoint:model  role agent  ctx 12.4k/32k  $0.12  ⬢ sandbox:none`.
Available values include `endpoint_model`, `endpoint`, `model`, `context`,
`cost`, `role`, `state`, `workspace`, and `sandbox`. Empty values are hidden:
`cost` (`$0.12`, rounded to the cent, from `Harness.conversation_cost`) only appears once the
provider reports a cost (`TokenUsage.cost`, e.g. OpenRouter's `usage.cost`;
`/compact`'s request counts too). It is per conversation: `/clear` and
`/import` (`Harness.load_history`) start it over.

The `sandbox` field is composed as `glyph + " " + prefix + runtime` from
`ui.sandbox_glyph` / `ui.sandbox_prefix` and is colored green
(`ui.sandbox_active_color`) when a sandbox is active and orange
(`ui.sandbox_inactive_color`) when tools run unsandboxed. Both colors accept a
palette name or `#rrggbb`. It is always non-empty, so it stays visible.
Provider-reported prompt usage replaces the context estimate after a response
supplies authoritative usage data.

The `context` field is colorized by how full the context window is:
green below 33%, orange/amber below 66%, and red at or above 66%.

The `role` field reflects the active conversation role and is refreshed
whenever the role changes (via `/role <name>` or a conversation import that
applies a saved role).

## Conversation import/export

`/export <file>` writes `{"role": ..., "history": [...]}`; image references
carry their bytes (`"data"`, base64 — `harness.images.embed`) so the file is
self-contained.
`/import <file>`:
- Fuzzy-autocompletes `.json` files in the current directory.
- Restores the saved role; if the role no longer exists, it falls back to the
  `agent` role and posts a warning message in the chat.
- Writes embedded images back to the image cache and repoints the references
  (`harness.images.restore`); an image with neither bytes nor file is listed
  as `unavailable` and sent as an `[image #N unavailable]` text part.
- Rebuilds visible messages, splitting assistant thinking/content with the
  same `ThinkingTagParser` the harness uses (so imported reasoning renders as
  a `ThinkingMsg`).

## Library Contracts

### Widget Lifecycle and Ownership

Containers assign child geometry through `set_layout()` and `layout()`. The
compositor renders the root tree after layout, then renders registered overlays
above it. Components mark content changes with `mark_changed()` and geometry
changes with `mark_layout_changed()`; the compositor uses those states to
request redraws. Widgets own presentation and local interaction, screens own
workflow and focus/action scope, and the application owns domain state and
services.

### Event and Focus Flow

`EventRouter` checks overlays from newest to oldest first. An unhandled event
then passes through the application interceptor, semantic `ActionMap`, mouse
hit path, focused widget, or root fallback as appropriate. Mouse paths are
built from component rectangles and are tried child-first; a component stops
propagation by returning `True`. The application interceptor handles policy
and focus transitions. `FocusScope` selects the keyboard target and keeps
modal focus bounded.

### Layout and Coordinates

Coordinates are zero-based terminal cells. Component rectangles use absolute
`x`, `y`, `width`, and `height`; the right and bottom edges are exclusive.
Containers allocate child rectangles before rendering. `Padding` insets a
child, `Align` positions it within its allocation, `Stack` paints children in
order, and `ScrollView` clips content to its viewport. Rendering writes to the
allocated `Buffer` or `SubBuffer` and should not perform layout for siblings.

### Screens and Navigation

Create a screen by composing components into a root, then pass optional
`FocusScope`, `ActionMap`, and model values to `Screen`. Install an initial
screen with `Navigator`; use `push`, `pop`, `replace`, or `back` for movement.
Use `ModalHost.present_screen()` for modal screens so enter/leave lifecycle
hooks and overlay ownership are handled together.

### API Stability

The stable library surface is the typed events, `Action`/`ActionMap`, focus
scopes, `Component`, layout primitives, reusable components, `Screen`,
`Navigator`, and `ModalHost`. Modules named
as application adapters, private attributes (leading `_`), compositor internals,
and legacy `chatTUI` callbacks remain internal and may change during migration.

### Library-Only Example

The former `example_screen.py` was removed in the toolkit pruning pass. There is
no library-only demo screen; tests compose library primitives directly.

## Integration Boundary

Behavior-preserving migration: keep compatibility at the application boundary
and remove legacy paths only after production references reach zero.

## Compositor (`tui/compositor.py`)

- Runs an async render loop at ~30 FPS
- Manages overlay stacking (e.g., permission prompts, menus, popups)
- Tracks dirty state; only redraws when something changed
- `Compositor.invalidate()` — marks the frame as needing redraw
- `add_overlay(component)` / `remove_overlay(component)` — register floating components rendered on top of the main tree
- `add_frame_callback(cb)` / `remove_frame_callback(cb)` — per-iteration hooks called with `time.perf_counter()`; a callback returns `True` when it produced work, which requests a repaint. Iteration runs over a copy so callbacks may unregister themselves. The stream revealer uses this as its clock (see below).

## Popup System (`tui/components/popup.py`)

Centered overlay popups for commands that benefit from floating display rather than chat history messages.

- `Popup` extends `Component`, built on `Box` + `TextComponent` component tree
- `show(title, content)` — displays popup, auto-centers, registers with compositor
- `hide()` — dismisses popup, unregisters from compositor
- **Action bar**: `[Esc] close` rendered by Box's native action system — identical positioning and style to message box action bars
- **Clickable action bar**: hit regions computed by Box during render; click detection uses Box's `_action_hit_regions`
- **Scroll**: arrow keys (±1), mouse wheel (±3), clamped to bounds
- `PopupAction(key, label)` dataclass — compatible with Box's `.format()` action protocol, no MsgAction coupling
- Scroll position indicator overlaid on bottom-right when content overflows
- Input interception: when popup is visible, the `EventRouter` overlay-priority
    path routes input to the popup before normal focus handling
- Auto-sizing: `max_width_ratio` / `max_height_ratio` control popup dimensions relative to terminal
- Currently used by: `/help` (command list)

## No In-App Forms

The interactive form stack (`form.py`, `form_popup.py`, `field_models.py`,
`form_schema.py`, `role_editor_model.py`, `config_overlay.py`, `input/basic.py`)
was removed. Configuration is edited as files: `/config <section>` and
`/config role <name>` open the file in `$EDITOR` and reload. Interactive UI is
reserved for permission approval and destructive confirmation; see
[notes/principles.md](./principles.md).

## Foreground programs (`ui/external_editor.py`)

`$EDITOR` (`/config`, `/edit`, `/config role`, `/sandbox config`) and
`/terminal` run through `run_in_foreground(ui, argv, cwd)`, which hands the
terminal to the child **without freezing the conversation** (streams, tools and
permission prompts keep going; the transcript is redrawn on return):

- the child (`preexec_fn`) resets SIGINT/SIGQUIT/SIGTSTP/SIGTTIN/SIGTTOU to
  default, gets its own process group, and makes it the terminal's foreground
  group before exec — so Ctrl+C / Ctrl+Z reach only the child;
- moka ignores SIGINT meanwhile (backup), `Compositor.pause()`s (no input
  reads, no drawing — `render()` is a no-op, so the presenter's direct render
  for a permission prompt is safe), and polls `waitpid(WNOHANG|WUNTRACED)` so
  the event loop stays free; a stopped child's **group** is `SIGCONT`ed
  (continuing only the direct child left `sh` waiting on a stopped `sleep`);
- afterwards it reclaims the terminal (`tcsetpgrp`, SIGTTOU ignored), resumes
  raw mode and `Compositor.resume()`s with a full redraw.

Without job control (moka not the terminal's foreground group) the handover is
skipped and only the SIGINT backup applies. Verified in a pseudo-terminal: at
HEAD before this change, Ctrl+C in a non-job-control child (`sh -c "sleep 5"`)
killed moka; now it survives (also Ctrl+C at a shell prompt / on `sleep`,
Ctrl+Z on a plain child, and an answer streaming throughout).

**`/terminal`** always opens `$SHELL` (else `/bin/sh`) on the host, in the
workspace; **`/sandbox terminal`** opens a shell inside the active sandbox
(`sandbox.shell_argv`: the same mounts/network/limits as the worker, bash if
present, `-it` for podman/docker), or says there is none. Both go through
`commands/base.open_shell`. The screen is cleared first (`clear_screen=True`; editors draw on their own screen and are left as is). `exit` returns.
The shell gets `MOKA_TERMINAL=<outer pid>`; `main()` refuses to start when it
is set ("moka is already running (pid N) … Type 'exit' to return to it.",
exit code 1), so mokas never nest and fight over one terminal
(`MOKA_TERMINAL= moka` overrides deliberately). Editors are not marked.


## Single Conversation

There is one conversation per process. The app (`chatTUI`) owns the agent,
history panel, message queue, generation task, tool state, and pause/steer
state directly; there is no `ConversationRuntime`, `TabView`, or `TabBar`.

**One generation at a time.** A single `agent_worker` serves `message_queue`:
`run()` records it in `worker_task`, so `_ensure_worker()` never starts a
second one (two workers on one queue used to run generations concurrently). A
message sent while the model answers is queued (`is_queued`, "user (queued)")
and **stays last**: `ChatHistoryPanel.new_message(append=True)` inserts new
lines above trailing queued messages, so the running generation's tool lines
and text never land below a queued question. **`/stop`** cancels only the
running generation (`stop_generation` sets `_stop_requested`; the worker keeps
serving the queue). The presenter then `aclose()`s the harness stream so
`Harness._abort_tool_calls` runs at once: it kills the running tool
(`transport.cancel_active` — a bash process group, or the sandbox worker so an
orphaned request cannot interleave with the next), answers every unanswered
call of the turn with a `[CANCELLED]` tool message (keeping history a valid
request), and clears stale permission answers (`PermissionGate.clear_pending`,
so a late "allow" cannot approve the next prompt). The presenter also resets
`pending_permission_prompt` so input is not left blocked.

## Shutdown

Ctrl+C is read as a raw `\x03` byte by the compositor's input loop
(`_handle_shutdown_key`), which sets `running = False` and the app
`shutdown_event`. `agent_worker` races its queue against `shutdown_event`, and a
`shutdown_watcher` task cancels any in-flight generation, so one Ctrl+C exits
promptly and cleanly. `main.py` also swallows a stray `KeyboardInterrupt` so a
mis-timed interrupt never prints a traceback.

## Debug Panel

The debug console is a `DebugLogPanel` (extends `TextComponent`) wrapped in a
`DebugPopup` compositor overlay. `TuiLogHandler` feeds it log entries and
`ChatHistoryPanel.activity_sink` routes `SysMsg*` to the activity overlay
(`/activity`). There is no user-facing debug-panel command; logging is enabled
via `debug.toml` (`log_enabled`).

## Buffer (`tui/buffer.py`)

- Grid of `Cell` objects (character + foreground RGB + background RGB)
- `SubBuffer` — a viewport into a parent buffer, enables clipping
- Components write to their allocated `SubBuffer`; compositor merges and flushes to terminal

## Components (`tui/components/`)

All components extend `Component` (base.py):
- `render(buffer)` — draw self into SubBuffer
- `handle_input(event)` — process keyboard/mouse events
- `dirty` flag — set when state changes, cleared after render

Key components:
- `Box` — bordered wrapper with optional title and action buttons
- `TextComponent` — static/scrollable text display
- `SelectionMenu` — floating dropdown with fuzzy filtering
- `InputComponent` — multi-line editor (see below)
- `DebugLogPanel` — scrolling log display
- `MarkdownComponent` — live markdown renderer (see [Markdown Rendering](#markdown-rendering) below)

## Input Component (`tui/components/input/`)

The most complex component. Responsibilities are split across sub-modules:

| Module | Responsibility |
|--------|---------------|
| `input.py` | Coordinator; cursor animation, menu orchestration, schema-driven parameter hints |
| `text_buffer.py` | Text storage, undo/redo |
| `input_handlers.py` | Keyboard, mouse, paste events |
| `completion.py` | `Completer` base + the four trigger-based providers: `CommandCompletion`, `SubcommandCompletion`, `ArgumentCompletion`, `ContextCompletion` |

Accepting a completion (Tab, or Enter before submit) inserts **no space** and
closes the menu like ESC (`_accept_completion` → `cancel()`, which suppresses
the menu for that word); the next menu opens once the user types the space.
The exception is a folder picked from the `@` menu, which keeps the menu open
to drill into it.

`KeyboardHandler.on_submit` may return `False` to refuse a submission; the text
then stays in the input (used when an image cannot be attached).

### Image attachments (`harness/images.py`)

- **Ctrl+V** (`\x16`, input focused) → `chatTUI.paste_clipboard()`:
  `ui/clipboard.read_clipboard()` asks `wl-paste`, then `xclip`, then `xsel`
  (text only). Text goes through the normal `PasteEvent` path. An image is
  saved by content hash to `~/.cache/moka/images/` (`images.store`) and
  inserted as `[image #N]`; `chatTUI._pasted_images` maps N to it for the draft.
- **On submit** `images.collect` attaches the pasted images whose marker is
  still in the text, then `@path` mentions of existing image files (relative
  to the workspace; numbered after the pasted ones). Too large → refused.
  `endpoint.accepts_images() is False` → refused ("<model> can't read
  images…"); unknown → sent.
- The user message lists them under its text (`ChatHistoryPanel.add_user_message`);
  `c` copies only the text. `[image #N]` markers and `@path` mentions are drawn
  in the `FOCUSED` color, in the focused input and in user messages
  (`chat_message.reference_spans`, set as the component's `highlighter`; the
  toolkit's `TextComponent`/`InputComponent` repaint those spans per line via
  `text.paint_spans`). The references travel through `message_queue`
  as `(text, message, attached)` to `agent.chat(text, attached)`.
| `scroll_manager.py` | Scroll offset for large input |
| `cursor_renderer.py` | Cursor visibility and animation |
| `coordinate_mapper.py` | Screen position → text offset |

### Completion menu styling

All four input completion menus (command `/`, subcommand, argument, `@` file
picker) share **one** look, applied by `Completer._apply_selector_style()` in
`input/completion.py`:

- `set_fill_width(True)` — span the full screen width;
- `frame_color = theme.USER` — accent frame;
- `content_color = theme.DEFAULT` — normal suggestion text;
- descriptions rendered as muted, right-aligned tails
  (`SelectionMenu.item_descriptions`, optionally a `footer` tag).

**Rule:** a new completion provider must call `self._apply_selector_style()` in
its constructor and pass descriptions via `_show(..., descriptions=...)`. Never
style one provider's menu inline — that is how the argument menu drifted from
the `/` and `@` menus. Descriptions come from `Command.get_descriptions(...)`
(`Param.descriptions`, or an override such as `ConfigCommand` for `/config role <name>` / `/config theme <id>`).

### Schema-Driven Parameter Hints

When typing a `/command`, the input component shows grey hints for upcoming parameters. This is driven by the `Param` dataclass on each `Command`:

1. `resolve_command(parts)` walks the command/subcommand tree to find the deepest matching `Command` and the argument offset
2. `_get_parameter_hint()` reads `cmd.params[arg_index:]` and joins them with spaces
3. Hints are rendered at the end of the current text (not at the cursor position)
4. The current argument being typed is skipped from the hints

## Mouse Interaction Model

The TUI supports full mouse interaction via ANSI SGR mode (`?1006h`).

### Text Selection
- **Start**: Click on message content area → `start_selection()` records anchor position
- **Drag**: Mouse move events → `update_selection()` extends highlight (throttled at 50ms for performance)
- **End**: Mouse release → `end_selection()` finalizes selection and auto-copies to clipboard
- **Yank**: Press `y` to copy current selection to clipboard at any time
- Selection is rendered as a reverse-video overlay via `_render_selection()` using segment-level fast-skip optimization
- Hit testing uses a cached line map (`_line_map_cache`) invalidated on scroll/message changes

### Action Button Clicks
- Action buttons (e.g. `[c] copy`) in box bottom borders are clickable
- `_hit_test_action_bar()` computes button hit regions on-demand (replicates Box border layout calculation)
- Clicking triggers the action with a brief **reverse-video flash** feedback (150ms)
- Flash is managed by `_flash_msg` / `_flash_action_key` / `_flash_until` on `ChatHistoryPanel`
- Box renders the flash by checking `parent_msg._flash_action_key` and applying `reverse=True`

## Message Types (`tui/msg_types.py`, `chat_message.py`)

Every message displayed in the chat history has a `MsgType` that controls its title, border color, content color, and available action buttons.

### MsgType Hierarchy

| Class | Title | Frame Color | Actions |
|-------|-------|-------------|---------|
| `MsgType` | *(base)* | DEFAULT | none |
| `UserMsg` | "user" | USER | COPY |
| `AssistantMsg` | "moka" | ASSISTANT | COPY |
| `ThinkingMsg` | "thinking" | MUTED | COPY |
| `SysMsg` | "system" | MUTED | COPY |
| `SysMsgError` | "error" | ERROR | COPY |
| `SysMsgWarning` | "warning" | WARNING | (inherits COPY) |
| `ToolCallMsg` | "tool" | TOOL | OUTPUT, COPY |
| `AskPermissionMsg` | "permission" | PERMISSION | ALLOW, DENY, OUTPUT, COPY |

`clamped` types (`ThinkingMsg`, `ToolCallMsg`, `AskPermissionMsg`) are
*activity*: they stack with no gap; see the spacing rule below.

### Tool-call lines

`ToolCallMsg` / `AskPermissionMsg` render compact (one line when unfocused):
`name target metric trailing`, single-spaced, with **no status glyph**. Only the
name (TOOL, or ERROR when failed/denied) and the colored counts/state stand
out; the target and the `▌` bar are MUTED (PERMISSION for an ask), so activity
reads apart from prose. Long paths are shortened from
the left (`…/commands/models.py`) so the metric stays visible. An expanded bash
line shows just `bash` in the header and the full command wrapped below.

```
read src/main.py:10-59 50 lines
write big.py +300 lines
edit src/main.py +2 lines −1 line
edit README.md +1 line
bash pytest -q running 12s        (elapsed time ticks; last output lines below)
bash pytest -q exit 1
edit a.py Search block not found  (error: name + reason red)
edit a.py +2 lines −1 line denied
edit a.py +2 lines −1 line approve? a/x   (AskPermissionMsg)
```

- **Metric rule:** a sign means the file changed, a plain count means it did not.
  Each side carries its own unit (`+2 lines −1 line`, singular per side) and is
  colored whole — `+N lines` SUCCESS, `−N lines` ERROR; only nonzero sides show.
  (`_line_metric`, `_tool_target_metric`.)
- **Trailing word only when it is news** (`Message._tool_trailing`):
  `approve? a/x`, `running Ns` (after 1s), `denied`, `cancelled`, the first line
  of an error, or a non-zero bash `exit N` parsed from the worker's `[exit:N]`.
- **State** (`Message.tool_state()`): `drafting` → (`asking`) → `running` →
  `completed` / `error` / `denied` / `cancelled`. `set_tool_status("running")`
  starts the elapsed clock.
- **Focused body** (`_tool_body`): edit → `SequenceMatcher` diff (`  `/`- `/`+ `,
  indentation kept, capped; `+`/`-` lines colored whole SUCCESS/ERROR); write → first 20 lines as `+ ` then
  `… +N more lines`; bash → `$ ` full command; other tools → `key: value`. `o`
  toggles the output, capped head 20 / tail 10, bash `[stdout]`/`[exit:N]`
  markers dropped. Focus changes rebuild the line, so an auto-focused permission
  prompt shows the diff it asks about.
- **Running bash** shows its last 5 output lines under the line (even compact),
  fed by `ToolOutput` → `Message.append_live_output`; they fold away once the
  call finishes (the activity surface still logs everything).
- Tool lines are **built for the width** (clipped with `…`, never re-wrapped):
  `_render_revealed` skips `_format_line_wrap` for tool messages, which used to
  strip code indentation. `reformat` rebuilds them for a new width.

**Drafting is the tool line itself.** A `ToolCallDraft` event opens a
`ToolCallMsg` in `drafting` state that grows in the final format (a `write`
counts the escaped newlines streamed so far: `+12 lines` → `+300 lines`); the
complete `ToolCall` updates the same message. Partial JSON args are salvaged by
`_parse_tool_args()`; a draft never full-parses (`full=False`), and
parse/metric/body are cached by args identity. The harness throttles
`ToolCallDraft` to one per 100 ms per call. Approving a permission
(`handle_allow_action`) retypes the ask into a running `ToolCallMsg`
(`ChatHistoryPanel.retype_tool_message`) and repoints `active_tool_messages`.
If generation ends abnormally, `finalize_active_tools()` closes any line still
drafting/running as `cancelled`/`error`.

Live labels are refreshed by the panel's `TickEvent` path every 250 ms
(`_LIVE_TICK_INTERVAL`); `Message.tick()` redraws only when its label changes.

`ThinkingMsg` and `SysMsgError/Warning` extend `AssistantMsg` / `SysMsg` — they inherit defaults and override only what differs.

Actions are deliberately limited to non-destructive operations. State-changing
actions (retry/stop/steer/pause/resume) and removal/edit are **not** message
actions; when needed they belong to explicit commands. `SysMsg*` notices are
routed to the activity surface rather than the transcript (see below).

### MsgAction Enum

| Action | Key | Label |
|--------|-----|-------|
| `COPY` | `c` | copy |
| `OUTPUT` | `o` | output |
| `ALLOW` | `a` | allow |
| `DENY` | `x` | deny |

### Message Selection and the Mode Line
Messages are gutter-threaded and do not render actions inline. Messages use a
full-height `▌` prefix bar (`Box.full_height_gutter`) whose color encodes the
type: user `USER`, assistant/thinking/tool calls `MUTED`, permission asks
`PERMISSION`. User content is normal text color (the accent is only the bar).
`ChatHistoryPanel` keeps a `focused_message_index`
(the selected message); the selected message's prefix bar is replaced with a
brighter `▌` marker (no extra column, nothing shifts, no leading margin).

**Split answers.** Once an answer is complete (end of generation, or the
deferred reveal finalize in `chatTUI._finalize_stream`; also `/import`),
`ChatHistoryPanel.split_answer` replaces it with segment messages from
`ui/answer_split.py` — prose, top-level code blocks (fence at column 0), tables
(header + separator) — sharing an `AnswerGroup` (`Message.group`,
`segment_kind`, `copy_text`). Segments touch (no gap, no blank lines between
them). Navigation has two levels:

- **↑/↓** move between messages; a split answer is one stop, selected whole
  (bright `▌` on all its segments), and `c` copies the whole answer;
- **→** enters it (`inside_group`): the selected segment gets a wide `█`, the
  action line reads `code 2/5` with `↑↓ part · ← back`; ↑/↓ move between
  segments and stop at the edges; `c` copies the segment (`copy_text_for`: code
  without fences). **←**/**Esc** return to the whole answer. **→** on a single
  message only flashes "single block". A click selects a split answer whole;
  a click on the already-selected answer selects the segment under the cursor.

An **action line** sits above the input with a blank pad row above it
(`ActionBar.set_top_pad`): an `ActionBar` mounted permanently in the workspace
body (`ChatScreen(..., action_bar=...)`), collapsed to zero rows and expanded to
two (pad + content) when needed. The app (`_update_action_strip`) drives it in
two modes:

- **Message selected** — shows the message's actions (`[c] copy`, `[o] output`,
  permission `[a]/[x]`) in the muted style, right-aligned as a group with the
  `↑↓ move · esc back` hint (`ActionBar.set_align_right`).
  Mouse clicks are dispatched by the app interceptor to
  `ActionBar.handle_input`; key dispatch goes through
  `ChatHistoryPanel.handle_input` → `on_action`. `ChatHistoryPanel` notifies the
  app via `on_selection_changed`.
- **Input focused** — shows a single muted, right-aligned hint:
  `[/] command [@] file [$] shell    ↑↓ move`. `@` works mid-text, so the line
  stays visible while typing. `InputComponent.on_change` (fired on every text
  change) refreshes it.

The status bar stays visible below in both modes. `Esc`/`Enter`/`i` clear the
message selection and collapse the line.

### Activity Surface and Toasts

Non-conversation output (shell commands/results, command status, errors, role
changes, generation-stopped notices) must not live in the transcript.
`ChatHistoryPanel.add_message` routes `SysMsg`/`SysMsgError`/`SysMsgWarning` to
`activity_sink` when set; the app's sink appends to the **activity overlay**
(a `DebugPopup`-style overlay toggled by `/activity`) and shows a transient
**toast** in the status bar (`StatusBar.set_toast`, auto-expiring). The returned
message is detached (not appended). Explicit `ui.activity(text)` writes to the
overlay only.

### How to Add a New Message Type

1. **Define the class** in `moka_chat/ui/tui/msg_types.py`:
   ```python
   class MyMsg(MsgType):
       name = "my_type"
       title = "my title"           # shown in box border
       frame_color = "WARNING"      # key in theme dict (colors.py)
       content_color = "MUTED"      # optional; None = default text color
       actions = [MsgAction.COPY, MsgAction.DELETE]
   ```
   Use an existing class as a base if it's a variant (e.g. `class MyMsg(SysMsg)`).

2. **Import it** wherever you create messages (usually `app.py` already imports all types).

3. **Use it** when adding to the chat panel:
   ```python
   ui.chat_history_panel.add_message("text", msg_type=MyMsg())
   ```

4. **Handle any new actions** — if you added a new `MsgAction`, wire up a handler callback in `ChatHistoryPanel` (e.g. `on_my_action`) and connect it in `app.py` via `ChatActionHandlers`.

### How Messages Are Displayed

`ChatHistoryPanel.add_message(text, msg_type, title=None, ...)` creates a `Message` object and appends it (or routes it to the activity sink for `SysMsg*`).
`Message` wraps a `TextComponent`/`MarkdownComponent` inside a thread-mode `Box`
(role gutter, no border).

**Padding is owned by the `Box`.** The panel passes the wrap width to `Message`
(content width = panel width − gutter − padding); `Box` lays the child out at
`x + gutter + content_pad_left` with width reduced by the right pad. Content
components render *unpadded* (plain text is no longer pre-padded and
`MarkdownComponent` gets `left_pad=0`), so "where content starts and how wide it
is" has a single owner. `Message.ingest()` appends arrived text without
rendering (canonical `base_text`); `Message.reveal_to(n)` renders
`base_text[:n]` through the append-only markdown fast path. `Message.append()`
is `ingest` + `reveal_to(len(base_text))` for non-streamed callers and drops
leading whitespace on the first chunk, since models often open with a space.

Messages are separated by `ui_msg_v_margin` blank lines (default `1`; set it in
`ui.toml`). Spacing rule (`chat_history_panel._gap_before`): adjacent activity
(`MsgType.clamped`: `ThinkingMsg`, `ToolCallMsg`, `AskPermissionMsg`) stacks
with no gap; prose, user turns and notices keep the gap. A thought belongs to
the message it precedes: it sits directly on top of it, and when that message
needs a gap the gap goes above the thought.
`ChatHistoryPanel` is the owner of the message list — it handles layout,
selection, scrolling, and width-change reformatting.

### Wait-phase feedback

Each generation opens a collapsed `ThinkingMsg` wait line so it never looks
frozen. There is no spinner: the label ticks (`Message._thinking_label`).

- `process_generation` creates it *before* consuming the harness stream (phase
  `processing`); it reads `waiting 0s`, or `preparing Ns` once that phase has
  lasted 0.5 s;
- at `Start(assistant)` the phase becomes `thinking`; with no reasoning yet it
  reads `waiting Ns`, and once reasoning arrives `thinking Ns` followed by a
  muted preview of the tail of the latest reasoning line;
- at the first content/tool boundary (`end_status_message()`): if the
  reasoning earns a line (`thought_worth_showing`: ≥ `ui.thought_min_tokens`,
  default 100, estimated at ~4 chars/token; `0` shows every non-empty thought)
  it is finalized to `thought for Xs` (focus expands the reasoning); otherwise
  the line was only a wait indicator and is **removed**
  (`ChatHistoryPanel.remove_message`). Short reasoning stays in history and
  exports; `/import` applies the same threshold. A tool loop with little
  reasoning therefore shows only tool lines.

`end_status_message()` is used at every hard boundary (Token, ToolCallDraft,
ToolCall, PermissionRequest, Error, cancel, and Done — which also removes an
empty wait line instead of deferring it). It drains the revealer before its
`current_msg` check, so pending streamed text cannot surface only once the tool
finishes. `chatTUI.disengage_stream()` flushes once more before dropping the
stream reference. A later turn after tool calls opens a fresh wait line.

A tool-call delta is **not** a content boundary. Providers may interleave
content and tool-call deltas within one response, so the presenter tracks
`response_text_msg` (reset at each `Start(assistant)`) and appends all of a
response's content to that one `AssistantMsg`. The `ToolCallDraft` handler finalizes
the text above (fully revealed and styled at once); content resuming after the
draft goes back into `response_text_msg`. This prevents
the assistant's sentence from being sliced into a second message below the tool
line (regression test: `test_content_resuming_after_tool_draft_stays_one_message`).

---

## Commands (`commands/` package)

Slash commands typed by the user (e.g. `/model`, `/theme`, `/help`).
The package lives in `moka_chat/ui/commands/`:

- `registry.py` — the single `COMMANDS` assembly point and `handle_command()`.
- `base.py` — `Param`, `Command`, `ChatUIProtocol`, completion helpers.
- Domain modules (`core`, `conversation`, `models`, `roles`, `themes`) import
  **only** `base`; `registry.py` assembles them. This shape is enforced by
  `test/test_command_import_graph.py`.
- Leaf commands are plain `async def` handlers wrapped in
  `Command(name, description, handler=..., params=[...])`.
- **Subcommands are a first-class `Command` tree.** Pass
  `subcommands={"start": Command(..., params=[Param(...)])}` and a fallback
  `handler` for the bare command. `Command.execute()` resolves the tree and
  dispatches to the leaf with the remaining args, so handlers never parse
  `args[0]`. `ArgumentCompletion` already resolves the same tree, so each
  subcommand's `params` complete positionally — subcommands at `/cmd `, then
  the resolved subcommand's params at `/cmd sub `. `/sandbox` is the reference.
- Subclass + override `get_completions` / `get_descriptions` only for purely
  dynamic, cross-argument completion (currently `ConfigCommand` for `/config
  role <name>` and `/config theme <id>`).

### Registered Commands

`help`, `clear`, `reload`, `config`, `edit`, `export`, `import`, `compact`,
`exit`, `stop`, `terminal`, `activity`, `model`, `role`, `sandbox`, `theme`

### Sandbox (`/sandbox`)

Verb-first command tree (`COMMANDS["sandbox"].subcommands`):

- `/sandbox` → subcommand help; `/sandbox config` → edit the project file.
- `/sandbox start [id]` → activate; no id lists sandboxes (id/type/description);
  missing image offers *Build now* / *Cancel*.
- `/sandbox build <id>` → build the image (streams to activity; no activate).
- `/sandbox init podman|docker [base]` → write a starter `Containerfile` /
  `Dockerfile`; `params` complete runtime (`podman|docker`) then base
  (`python|debian|ubuntu`).
- `/sandbox stop` → deactivate.
- `/sandbox terminal` → shell inside the active sandbox (`open_shell`).

The `start`/`build` `ID` param uses `sandbox_id_completions` +
`sandbox_id_descriptions`; `init` uses `sandbox_runtime_completions` then
`sandbox_base_completions`. All positional completion comes from the tree, not
from a hand-rolled `get_completions`.

### Server & model selection

- Servers are configured by editing `servers.toml` (`/config servers`); there is
  no `/server` command.
- `/model` — opens a searchable picker; `/model <model>` selects directly. Refreshes discovery live, resolves a model across servers, switches the harness, and selects it. The picker (`SearchModal`) shows the cached catalog instantly, refreshes in the background, tags the current model with a green `active`, and supports type-to-filter.

### Roles

- `/role` lists roles (marking the active one) or switches with `/role <name>`.
- `/config role <name>` creates/opens `roles/<name>.toml` in `$EDITOR` and reloads;
  `/config role delete <name> confirm` removes it.

### Themes

- `/theme` opens a searchable picker (built-ins plus `themes.toml` definitions);
  `/theme <name>` selects directly and persists it in `state.toml`.
- `/config theme` edits `themes.toml`.

### Structure

- `Param` dataclass — a command argument: `name`, `completions` (static list or callable), `descriptions`, `path` (filesystem scan), `required`.
- `Command` — `name`, `description`, `handler` or `subcommands`, `params`. `resolve_command()`, `get_completions(arg_index, prior_args)`, `get_descriptions(...)`, `execute(ui, args)`.
- `COMMANDS: Dict[str, Command]` — registry.
- `handle_command(ui, text)` — strips the leading `/`, looks up `COMMANDS`, calls `execute`.

### How to Add a New Command

1. Write a handler in the relevant domain module (or `core.py` for a general one):
   ```python
   async def cmd_mycommand(ui: ChatUIProtocol, args: List[str]):
       ui.chat_history_panel.add_message("hello", msg_type=SysMsg())
   ```
2. Register it in `registry.py`:
   ```python
   "mycommand": Command("mycommand", "One-line description", handler=cmd_mycommand,
                        params=[Param("NAME", required=True)]),
   ```
    `Param` definitions drive parameter hints and fuzzy argument autocomplete;
    `path=True` adds filesystem scanning.
3. It is now callable as `/mycommand`, listed by `/help`, and offered by the input autocomplete.

For a command with subcommands, build a tree instead of parsing `args[0]`:

```python
"mycommand": Command(
    "mycommand", "One-line description",
    handler=mycommand_help,                       # bare /mycommand
    subcommands={
        "start": Command("mycommand start", "…", handler=cmd_start,
                         params=[Param("ID", completions=ids, descriptions=desc)]),
        "build": Command("mycommand build", "…", handler=cmd_build,
                         params=[Param("ID", completions=ids)]),
    }),
```

`execute()` dispatches into the tree and `ArgumentCompletion` completes each
subcommand's `params` positionally. Subclass `Command` and override
`get_completions` / `get_descriptions` only when completion genuinely depends on
earlier arguments (currently `ConfigCommand`).

### Hiding a Command from `/help`

Prefix the registry key with `_`; `cmd_help` / `get_command_descriptions` skip such names.

## Terminal I/O (`tui/terminal.py`)

- Sets raw mode, captures mouse/keyboard events
- `ANSI` constants for escape codes
- `Terminal.write()` flushes the buffer to stdout

## Colors and Layout

- `tui/colors.py` — `RGB` class, theme dictionary, hex parsing
- `tui/layout_utils.py` — `wrap_text()`, `display_width()` (wcwidth-aware for Unicode), `strip_ansi()`
- `tui/container.py` — explicit layout pass; `Vsplit`/`Hsplit` support fixed, percentage, content, and fill policies, with `Padding`, `Align`, `Stack`/`Overlay`, and `ScrollView`

### Theme switching

Components resolve theme colors when they are **constructed**, not per render
(the `theme` singleton is mutated in place by `set_theme()`, so re-render alone
does not recolor cached fg/bg). A theme change therefore must:

1. `set_theme(name)` — mutate the palette in place;
2. `chatTUI.refresh_theme()` — re-resolve the chrome (input, bars, debug/activity
   panels), the long-lived overlays (`Popup`, `DebugPopup`), the cached
   completion menus (`InputComponent.refresh_theme()` →
   `Completer.refresh_theme()` → `SelectionMenu.apply_theme()`), and every
   transcript message (`Message.refresh_theme()`), then call
   `Compositor.request_full_redraw()` so cached frame cells are discarded.

Anything that stores a `theme.*` color at construction and lives across a theme
switch must expose a `refresh_theme()` (or `apply_theme()`) and be called from
`chatTUI.refresh_theme()`. `/theme` and `_apply_theme()` (called by `/reload`
and `/config`) do this. `/theme` additionally registers a `ThemePreview` overlay
(a compact top-strip palette overview) while you move through the list and
previews each theme live via `SearchModal.on_highlight`; cancelling restores the
previous theme. The picker is kept short (`max_height = 8`) so the top overview
and the input-anchored picker don't overlap. The default theme is `terminal`.

## Markdown Rendering

Live markdown rendering for chat messages, added to support streaming output with rich formatting.

### Modules

- `tui/components/markdown.py` — parser + `MarkdownComponent` (append-only commit path for streaming; `parse(open_tail=True)` renders the open line plain)
- `tui/ascii_table.py` — `AsciiTable` renders markdown tables with squared-style borders
- `tui/syntax_highlight.py` — `highlight_line(line, lang)` tokenises code blocks for coloring

### Pipeline

1. `BlockParser` splits raw text into blocks (paragraphs, headers, code fences, lists, quotes, HR, tables)
2. `InlineParser` parses inline `**bold**`, `*italic*`, `` `code` ``, `[text](url)`
3. `Markdown.parse()` returns `List[List[StyledSegment]]` (display lines)
4. `MarkdownComponent` wraps lines to the component width (word-wrap for prose, hard-break for code blocks/tables)
5. `render()` writes styled segments to the buffer

### Incremental (streaming) updates

`MarkdownComponent.update(text, append=True)` commits *complete* lines atomically
and re-parses/re-wraps only the open (uncommitted) tail, so an append costs
O(open region) rather than O(message length):

- `BlockParser.find_commit_line(lines, start, last_open)` returns the largest
  line index that can be committed: a line the parser visits outside a
  fence/table, never the still-growing last line, never a run of trailing blank
  lines, and never a potential table header whose separator has not arrived.
- Committed segments/wrapped rows are cached (`_committed_parsed` /
  `_committed_wrapped`) and only ever appended to. The open region is re-parsed
  and re-wrapped each append, then concatenated after the caches.
- `take_dirty_from_line()` returns the open region's first wrapped row
  (reduced across appends); `Box`/`MessageView` reuse that as the tail raster.
- **Open-tail rendering (approach A):** while the final line is still growing it
  is rendered *plain* (no `InlineParser`), so closed spans such as `**bold**` on
  the open line stay literal until its newline arrives. `set_streaming(False)`
  (called by `Message.finalize()`) re-parses in full so the line is styled.
  `Markdown.parse(..., open_tail=True)` carries the same rule; tests use it for
  a streaming-aware reference.
- An open code fence or table is held whole until it closes (bounded by its own
  size); a single never-ending line is still re-wrapped per append (O(line)).

`notes/bench_render.py` has a `stream` scenario (append+render per frame), a
`stream_smooth` scenario (ingest + one revealer tick + render), and a
`stream_micro` table (µs/append vs length for prose/code/table); baseline in
`notes/bench_stream_baseline.json`.

### Stream smoothing (`ui/stream_revealer.py`)

Decouples how fast text *arrives* from how fast it *appears*.

- `StreamRevealer` is a pure controller (no TUI imports, all time passed in):
  `ingest(text, now)` schedules arrived text, `tick(now)` returns the next slice
  to release, `drain()` releases everything. The deadline is anchored to the
  **oldest** buffered character (`oldest + max_window`), so no character is held
  longer than `max_window` (250 ms) no matter how many newer chunks merge in.
  A small chunk reveals one cluster per frame; a large chunk spreads across the
  window. Behind schedule (`now >= deadline`) it releases everything. Grain =
  non-whitespace grapheme clusters, derived from the remaining window and
  `smooth_target_fps`; whitespace is released for free.
- `ui/tui/graphemes.py` provides `split_clusters` / `count_nonws` /
  `advance_nonws` (combining marks, ZWJ, variation selectors, skin tones,
  regional-indicator pairs, Hangul jamo) — vendored rather than adding `regex`.
- `chatTUI` owns `stream_revealer`, the active `stream_message`, and
  `stream_revealed`; `_on_frame` (registered via the compositor frame callback)
  calls `revealer.tick`, applies the slice with `message.reveal_to(...)`, and
  returns whether the rendered output changed. It deliberately returns `False`
  when the revealer is merely waiting for its next step: a render request with
  no dirty rects makes the compositor full-redraw, so signalling every frame
  would full-redraw at `target_fps` during the whole stream. The compositor's
  idle wakeups (`1/target_fps`) still tick the revealer. `generation_presenter`
  routes Token/Reasoning through `message.ingest` + `revealer.ingest` and flushes
  synchronously before tool calls, permission requests, reasoning↔content
  switches, errors, and message replacement/clear. On a natural `Done` the
  revealer drains and the frame callback finalizes (spinner persists ≤250 ms);
  the presenter `finally` force-drains as a backstop.
- Config: `stream_smoothing` (bool, default `true`) and `smooth_target_fps`
  (int, default `60`). `stream_smoothing = false` keeps the direct-append path.

### Styling

Styles are driven by `settings.config.markdown_styles` (see [config.md](./config.md)). Each element (`header1`–`header6`, `bold`, `italic`, `code`, `code_block`, `quote`, `list`, `hr`, `table`, `link`, `paragraph`) maps to `fg`/`bg`/`bold`/`reverse`. Emphasis (`bold`, `italic`, headers) uses an `fg` color rather than terminal bold/reverse, which render as an ugly inversion in many terminals. Unordered list items use pastilles (`•`/`◦`/`▪` by nesting level).

### Tables

Markdown tables (`| ... | ... |` with a `---` separator row) are detected by `BlockParser`, grouped into `TableLine` runs, and emitted by `Markdown.parse` as one `TableSegment` placeholder line (cells stripped of inline markers such as `` ` `` and `**`). `MarkdownComponent._wrap_all` renders it via `AsciiTable(..., total_width=<width>)` with the `squared` style, so tables are laid out for the actual width and again on resize: columns keep their natural width when the table fits; otherwise the widest shrink first (`_fit_widths`, floor 6 cells) and their cells word-wrap onto several lines, and rows are then separated by horizontal lines (`├───┼───┤`). No blank line is added before or after a table. Column alignment (`:--`/`--:`/`:-:`) is read from the separator row. Rendered lines use `code_block=True` so they are never re-wrapped. Cells measure/pad by display width (`layout_utils.display_width`, grapheme-aware). `Buffer`/`SubBuffer.write_str` iterate grapheme clusters (`buffer._text_tokens`) so emoji sequences (variation selectors, ZWJ, keycaps) occupy one cell with the correct width — the previous per-code-point walk split them and broke alignment. `Markdown._hard_break_line`/`_break_segments` are cluster-aware too, so wide emoji count as 2 when wrapping (otherwise a line was left unbroken and clipped at the right edge).
