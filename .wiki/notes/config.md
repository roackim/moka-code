# Configuration

Configuration is files. Everything hand-edited lives under
`~/.config/moka/` (override the directory with `MOKA_CONFIG_DIR`), split
into small single-concern files so each stays easy to edit. There is **no
project-local config**: per-project settings are stored *here* (keyed by the
workspace name), never in the repository, and there is no trust model.


The project was renamed from pico-chat to moka. On the first start after the
rename, `settings.migrate_legacy_config_dir()` (called first in `main()`) moves
`~/.config/pico-chat` to `~/.config/moka` when `MOKA_CONFIG_DIR` is unset, the
new directory is missing and the old one exists, and reports it once in the
activity panel. Nothing else reads the old location.
## Files

| File | Contents | Shape |
|------|----------|-------|
| `ui.toml` | theme, padding, fps; then `[markdown_styles.*]` / `[syntax_highlight.*]` | flat keys, then tables |
| `context.toml` | context building | flat keys |
| `debug.toml` | debug logging | flat keys |
| `servers.toml` | one `[servers.<name>]` table per server | tables |
| `themes/<name>.toml` | one palette per file; file name is the theme name | palette keys |
| `roles/<name>.toml` | one role per file; file name is the role name | role body |
| `sandboxes/<name>.toml` | one global sandbox per file; file name is the sandbox name | sandbox body |
| `projects/<dirname>_<hash>/` | one project: `project.toml` (path + active) and `sandboxes/<name>.toml` (locals) | sandbox body |
| `prompts/<name>.md` | one reusable prompt per file (global); local ones live in the workspace's `.moka/prompts/` | markdown template |
| `state.toml` | last server/model, active theme, last role, effort | machine-written |

`state.toml` is disposable: deleting it only loses cached selections.
The old `styles.toml` and `themes.toml` are no longer read (merged into
`ui.toml` and split into `themes/` on 2026-10-26; nothing migrates them).
`roles/` and `themes/` mirror the same one-thing-per-file idea (see
[tools-and-permissions.md](./tools-and-permissions.md)).

Missing files are created from fully commented templates
(`settings.DEFAULT_CONFIG_TEMPLATES`) by `Config.ensure_section_file()`.
Sections are headed by one-line rules (`settings._rule`: `## ── Look ───…`, 60
columns) and run from the most used to the most technical. Every config file uses them: `servers.toml` has one section per provider (`Endpoint.label`) then Technical settings, theme files a Palette section, role files Role, Tools and Technical settings; the specs list the
keys in the same order (`test_template_and_spec_list_keys_in_the_same_order`). In every template a single `#` marks a setting to
uncomment (a bare `#` spaces a block of settings) and `##` marks help
(guarded by `test_template_help_lines_use_double_hash`; role files, from `roles._role_template`: Role (description, prompt, color), Tools, then Technical settings, guarded by `test_role_template_layout_and_uncommentable_settings`). The built-in role files (`agent.toml`, `chat.toml`)
are seeded by `roles.ensure_roles_dir()` on startup.

Existing **flat** files (`ui`, `context`, `debug`) are kept in sync
with their templates: on startup (`settings.sync_config_files()` in `main()`) and
when `/config <section>` opens one, `_sync_flat_file()` merges missing template
keys and removes lines whose key is in that section's `_RETIRED_*` set. A
commented template key (`# key = ...`, which only shows the default) is
rewritten to the current template line, so a changed default shows up; a set
value is never touched. Missing `##` help lines go after the template line
that precedes them. The
template is parsed into blocks (a `##` heading plus its help and settings); a
key missing from an existing block is inserted next to its siblings, and a
completely missing block brings its heading and help with it at the
template-relative position. Only keys named by the template or a registered
retirement are touched; user values, comments, and ordering are preserved.
`ui.toml` ends with a tables part (the style tables), which starts at
`_TABLES_HEADING` or the first table header, commented or not
(`_split_tables`). Only the part above it is merged, so a new key never lands
inside a table. The tables part is kept byte for byte, and a file without one
gets the template's commented tables appended.

Migrations are fail-closed. The merged text is verified before it can reach
disk (valid TOML, identical parsed user values, every user line preserved), the
previous bytes are written to `<file>.bak`, and the new file is installed with a
same-directory temp file + `fsync` + atomic `os.replace`. A concurrent-change
guard re-reads the original immediately before the rename; invalid TOML, a
changed file, or any I/O failure leaves the file byte-identical and records the
reason in `Config.last_sync_skips` (surfaced on the startup banner). An advisory
lock in the config directory serialises moka instances; editors are not locked,
so the backup is the safety net for that window. Structured files (`servers`,
the theme files, and the sandbox files) hold user-authored tables and are
never synced. See "Adding or deprecating a config key" in `AGENTS.md`.

### Sandbox files

One file per sandbox: `~/.config/moka/sandboxes/<name>.toml` (global) or
`~/.config/moka/projects/<dirname>_<hash>/sandboxes/<name>.toml` (local to a
project; `<hash>` = 4 hex of a hash of the resolved workspace path). A file holds
`type` (podman/docker/bubblewrap) plus `description`, `image`, `dockerfile`,
`network`, `run_args`, `instructions`; unknown keys are reported, and the retired `timeout` is
too (the limit on a tool call is the role's `tool_timeout`). The project's
`project.toml` holds `path = "<resolved workspace>"` and the `active = "<name>"`
selection (machine-written by `set_active()`). New files come from
`/sandbox create` (a commented starter per type) or `/sandbox copy`; names are unique
across both scopes. The default `bubblewrap` is seeded as a global on first run
(when `sandboxes/` does not exist yet). moka never writes into the repository. See
[sandbox.md](./sandbox.md).

## Loader (`settings.py`)

`Config` is a plain class with a flat attribute surface (`settings.config.<attr>`),
instantiated once at module load as `config`. The split files map onto flat
attributes via per-section specs (`_UI_SPEC`, `_CONTEXT_SPEC`,
`_DEBUG_SPEC`); the style tables of `ui.toml` and `servers.toml` are merged
into the `markdown_styles` / `syntax_highlight_styles` / `servers` tables, and
`themes/*.toml` into `config.themes` (errors prefixed `themes/<name>.toml:`).

- `reload()` re-reads every section file plus `state.toml` and returns a list of
  validation errors. Invalid entries keep their defaults; valid ones still
  apply. Errors are prefixed with the file name (`ui.toml: ...`).
- Unknown keys/sections/servers/types are reported rather than swallowed.
- Every `/config` edit, every `/role` and `/theme` file edit, and `/reload` go through
  one path, `core.reload_and_apply`: reload the config files, validate the role
  files, apply the theme, **reload the active role from disk** (so an edited
  `preserve_thinking` or tool list applies at once; a file that no longer
  loads keeps the running role and is reported; a running response defers it,
  with a message to `/reload`), rebuild the endpoint if needed, then refresh the
  notices. Nothing is watched.

### Intent vs state

- **Intent** (hand-edited): the section files above; moka never writes them.
  A `[servers.<name>]` writes its `type` (`llamacpp`,
  `openrouter`, `deepseek`; from `REGISTRY`) unless the table is named after one
  (`[servers.openrouter]`); an otherwise missing or unknown type is a
  load error and the server is skipped. Allowed keys: the common ones
  (`_SERVER_KEYS`) plus the class's `extra_keys`. OpenRouter: `models = [...]`
  is the whitelist `/model` lists; `providers = [...]` (server default) and
  `[servers.<name>.providers_by_model]` (`"<id>" = [...]`, replacing the
  default; `[]` = OpenRouter's own routing) are strict whitelists tried in
  order — `order` alone would let OpenRouter fall back to any host; a
  `providers_by_model` id not in `models` is reported (entry not used). Old
  shapes are reported with their replacement and the server is skipped:
  `type = "openai"` (`_RETIRED_SERVER_TYPES`), per-model `models."<id>"`
  tables, and the keys `provider`, `enabled_models`, `model_providers`
  (`_RETIRED_SERVER_KEYS`). The `servers.toml` template is generated: a
  legend plus each class's `template` block.
  Effort levels are detected only (no config key): the catalog's `efforts`,
  which a provider fills only from a stated list (OpenRouter-format
  `reasoning.supported_efforts`; any OpenAI-compatible server's `/models` may
  carry it; never guessed), gives the levels; `/effort` rediscovers the active server first.
  `/effort <level>` is sent as typed even when the server states no levels or not that one (the server's error is the check); the picker needs stated levels.
  While the server is undiscovered, a saved level is sent as-is; the chosen level is saved per server/model in `state.toml`
  (`[effort.<server>]`; nothing without a selected model) and sent verbatim as
  `reasoning_effort` (llamacpp, deepseek) or `reasoning.effort` (openrouter) —
  `Endpoint.effort_payload`. Load errors are shown at startup and on `/reload`/`/config`, and
  every reload rebuilds the live endpoint if its server table or selection
  changed. `type = "openrouter"` has no `base_url` (always
  `https://openrouter.ai/api/v1`); setting one is reported as a load error.
- **State** (machine-written, disposable): `state.toml` holds `last_server`,
  `[last_model]` (per-server selection), `active_theme`, `last_role` (the role
  the next start opens on; ignored when its file is gone), and `[effort]`.
  Written by `set_active_server` / `save_model_selection` / `save_active_theme`
  / `save_last_role` / `save_effort`. Legacy `active_model` and `[model_catalog]` keys are
  accepted and ignored (dropped on the next write). The discovery catalog is
  in memory only (`Config.models_by_server`, kept across reloads), so a
  changed server can never be shadowed by a stale copy.

## Editing

- `/config <section>` opens the section file in `$VISUAL`/`$EDITOR` and reloads
  on exit; no argument lists the sections. `section` is one of `ui`, `context`,
  `debug`, `servers`.
- `/role`, `/theme`, `/prompt` and `/sandbox` each have `edit [name]`, `create`,
  `path` and `remove` for their own files; `/sandbox` also manages the sandbox
  lifecycle (see [sandbox.md](./sandbox.md)).
- `/edit <path>` opens any file.
- `/theme edit <name>` opens `themes/<name>.toml`; for a built-in without a file
  it first writes one with that theme's full palette
  (`colors.write_theme_file`). `/theme create <name>` copies the active palette.
- `/theme select` opens a picker; `/theme select <name>` selects directly. The
  choice is persisted in `state.toml` (`active_theme`), else `terminal`. The
  theme is state only: `ui.toml`'s old `theme` key is retired, and a value left
  there moves to `state.toml` once (when no theme was chosen yet).
- The TUI suspends/resumes around the editor (`ui/external_editor.py`,
  `ui/tui/terminal.py`).

## Themes

A theme is a palette file (`themes/<name>.toml`, top-level keys) mapping the
sixteen `colors.PALETTE_FIELDS` (`BACKGROUND`, `DEFAULT`, `MUTED`, `ERROR`,
`WARNING`, `SUCCESS`, `PERMISSION`, `TOOL`, `USER`, `ASSISTANT`, `FOCUSED`,
`HEADING`, `EMPHASIS`, `CODE`, `USER_TEXT`, `ASSISTANT_TEXT`) to either a
`"#RRGGBB"` hex string or an ANSI table (`{ ansi = 90 }`, `{ ansi = 39, bg = 49 }`).
Missing entries inherit the built-in base of the same name (or `terminal`),
except `USER_TEXT` / `ASSISTANT_TEXT` (user and answer message text), which
default to the theme's own `DEFAULT` (built-ins too).
Built-ins are always available and are listed by `/theme select` (or
`colors.theme_names()`): `terminal` (default), `moka`, `nord`, `dracula`,
`gruvbox`, `tokyo-night`.
`colors.available_themes()` merges them with the user definitions and
`set_theme()` applies one in place. Markdown/syntax styles remain a separate
global layer in the tables of `ui.toml`.

The `/theme select` picker previews a theme as the highlight moves (`on_highlight`),
persists only on accept, and on cancel reloads the configured theme
(`reload_config()` + re-apply).

## Roles

A `Role` is a prompt plus a per-tool approval setting (`no` / `ask` / `yes`),
stored one file per role under `roles/<name>.toml`, plus an optional `color`
(palette name or `#rrggbb`, validated like the `ui.toml` colour keys) for the
input bars and the status bar's role field. `PermissionGate`
(`harness/permissions.py`) is the single decision point. See
[security.md](./security.md) and [tools-and-permissions.md](./tools-and-permissions.md).

## Key settings

**Servers:** `config.servers`, `config.active_server`,
`config.model_selection` (`server -> model`), `config.models_by_server`
(catalog), `config.get_model_for_server(server)` (the last selection only;
servers.toml has no `model` key). On a fresh state (`active_server` is None)
`commands.base.auto_select` runs after discovery: the previous model is kept
while available; if its server left `servers.toml`, or the server's fresh
listing no longer has it, or nothing was selected, it selects the first
available model (that server's first, else the top of `/model`; stale servers
are never picked) and says why. A server that cannot be listed keeps its
selection, shown red with a notice. `Config.server_errors` holds, per server,
the latest failed probe (a model listing, the connection check, or a chat
request failing on the network, auth or a server error), cleared by the next one
that succeeds; it colors the active server red and is shown for every configured
server (an error for the active one, a warning for the others), as are unset
`api_key = "$NAME"` variables.

**Context / ui:** `context_max_files`,
`context_max_depth`, `context_ignore_gitignore`,
`context_ignore_dotfiles` (`context.ignore_dotfiles`: the `@` list leaves out
names starting with `.`; `.git/` always; default off, so they are listed),
`context_max_image_mb` (`context.max_image_mb`, largest attachable image),
`context_sessions` (`context.sessions`, conversations saved per project; 0 = off),
`context_compact_filter_thoughts` / `context_compact_filter_tool_calls`
(`/compact` input: reasoning left out; each tool call one line, no output;
both default on), `context_compact_at` / `context_compact_stop_at` /
`context_compact_keep` (auto-compaction: fractions of the context window,
defaults 0.8 / 0.85 / 0.1; see `tree/harness.md` `compaction.py`; 0 turns a
trigger off), `context_diff_context` (`context.diff_context`: lines around
each change in `/diff` and in a focused edit line, a number, default 3),
`context_diff_full_file` (`context.diff_full_file`: `/diff` shows the whole
file; default off);
`ui_status_bar` (the status bar template, see [ui.md](./ui.md#status-bar)),
`ui_max_input_height` (input box caps + scrolls past
this many wrapped lines), `ui_stream_smoothing` / `ui_smooth_target_fps`
(streamed-text reveal smoothing), `ui_target_fps` (`ui.target_fps`, render
loop), and the rest of the `ui_*` attrs, including `ui_thought_min_tokens`
(reasoning shorter than this gets no transcript line; 0 = show all).
Each spec entry is `key -> (attr, kind, default)`: the default lives only there
(`_apply_defaults` loops over the specs; `test_template_values_are_the_spec_defaults`
checks the template shows it). The loader ignores retired keys (the sync removes
them). `msg_h_padding`, `app_global_padding` and `target_fps` are held by the
layout and re-applied by `chatTUI.apply_layout_settings` (startup and every
reload). `spinner_fps` is retired (`_RETIRED_UI`): tool and thinking lines show
ticking elapsed time instead of a spinner. `context.format` (the project tree was
never sent to the model) and `ui.debug_console_height` (the console is never shown) and the per-message
metrics keys (`show_metrics`, `metrics_show_*`; nothing displayed them) are
retired too, and so are `box_style` / `box_style_focused` (only the popups
read them; every frame is square) and `theme` (state only). Colour keys (`sandbox_*_color`, `thought_color`,
`thought_expanded_color`) are validated at load: a palette name or `#rrggbb`,
else a load error and the default is kept. `thought_gap` (default on) / `tool_gap`
(default off) space thoughts / tool lines like other messages; `stack_activity`
(default on) keeps a thought on an adjacent tool line (see [ui.md](./ui.md)).

The status bar is one template, `ui.status_bar` (kind `status_bar`: every
`{name}` must be one of `settings.STATUS_BAR_VALUES` and `[ ]` must balance,
else a load error and the default is kept). It replaced `status_bar_fields`,
`status_bar_separator`, `show_provider`, `provider_separator`, `show_effort`,
`effort_separator`, `sandbox_glyph` and `sandbox_prefix` (all in `_RETIRED_UI`;
`model_glyph` / `model_prefix` before them). `{sandbox}` is green
(`ui_sandbox_active_color`) when a sandbox is active and orange
(`ui_sandbox_inactive_color`) when tools run unsandboxed; both accept a palette
name or `#rrggbb`.

**Styles / themes:** `config.markdown_styles`, `config.syntax_highlight_styles`;
`config.themes`, `config.active_theme`, `config.get_active_theme()`,
`config.save_active_theme(name)`.
