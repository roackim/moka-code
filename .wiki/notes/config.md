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
| `ui.toml` | theme, padding, fps | flat keys |
| `context.toml` | context building | flat keys |
| `debug.toml` | debug logging | flat keys |
| `styles.toml` | `[markdown_styles.*]` / `[syntax_highlight.*]` | tables |
| `servers.toml` | one `[servers.<name>]` table per server | tables |
| `themes.toml` | one `[themes.<name>]` palette per theme | tables |
| `roles/<name>.toml` | one role per file; file name is the role name | role body |
| `sandboxes/<name>.toml` | one global sandbox per file; file name is the sandbox name | sandbox body |
| `projects/<dirname>_<hash>/` | one project: `project.toml` (path + active) and `sandboxes/<name>.toml` (locals) | sandbox body |
| `state.toml` | last server/model, active theme, effort | machine-written |

`state.toml` is disposable: deleting it only loses cached selections.
`roles/` mirrors the same one-thing-per-file idea (see
[tools-and-permissions.md](./tools-and-permissions.md)).

Missing files are created from fully commented templates
(`settings.DEFAULT_CONFIG_TEMPLATES`) by `Config.ensure_section_file()`. In every template a single `#` marks a setting to
uncomment (a bare `#` spaces a block of settings) and `##` marks help
(guarded by `test_template_help_lines_use_double_hash`; role files, from `roles._role_template`: description, prompt, tools, then a `## Technical settings` block, guarded by `test_role_template_layout_and_uncommentable_settings`). The built-in role files (`agent.toml`, `chat.toml`)
are seeded by `roles.ensure_roles_dir()` on startup.

Existing **flat** files (`ui`, `context`, `debug`) are kept in sync
with their templates: on startup (`settings.sync_config_files()` in `main()`) and
when `/config <section>` opens one, `_sync_flat_file()` inserts the commented
line for any spec key missing from the file (at its template-relative position)
and removes lines whose key is in that section's `_RETIRED_*` set. Only keys
named by the template or a registered retirement are touched; user values,
comments, and ordering are preserved, and the file is written only when the text
changes. Structured files (`styles`, `servers`, `theme`, and the sandbox files)
hold user-authored tables and are never synced. See
"Adding or deprecating a config key" in `AGENTS.md`.

### Sandbox files

One file per sandbox: `~/.config/moka/sandboxes/<name>.toml` (global) or
`~/.config/moka/projects/<dirname>_<hash>/sandboxes/<name>.toml` (local to a
project; `<hash>` = 4 hex of a hash of the resolved workspace path). A file holds
`type` (podman/docker/bubblewrap) plus `description`, `image`, `dockerfile`,
`network`, `run_args`; unknown keys are reported, and the retired `timeout` is
too (the limit on a tool call is the role's `tool_timeout`). The project's
`project.toml` holds `path = "<resolved workspace>"` and the `active = "<name>"`
selection (machine-written by `set_active()`). New files come from
`/sandbox new` (a commented starter per type) or `/sandbox copy`; names are unique
across both scopes. The default `bubblewrap` is seeded as a global on first run
(when `sandboxes/` does not exist yet). moka never writes into the repository. See
[sandbox.md](./sandbox.md).

## Loader (`settings.py`)

`Config` is a plain class with a flat attribute surface (`settings.config.<attr>`),
instantiated once at module load as `config`. The split files map onto flat
attributes via per-section specs (`_UI_SPEC`, `_CONTEXT_SPEC`,
`_DEBUG_SPEC`); `styles.toml` and `servers.toml` are merged
into the `markdown_styles` / `syntax_highlight_styles` / `servers` tables, and
`themes.toml` into `config.themes`.

- `reload()` re-reads every section file plus `state.toml` and returns a list of
  validation errors. Invalid entries keep their defaults; valid ones still
  apply. Errors are prefixed with the file name (`ui.toml: ...`).
- Unknown keys/sections/servers/types are reported rather than swallowed.
- Every `/config` edit (any section, `role`, `theme`) and `/reload` go through
  one path, `core.reload_and_apply`: reload the config files, validate the role
  files, apply the theme, **reload the active role from disk** (so an edited
  `replay_reasoning_depth` or tool list applies at once; a file that no longer
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
  While the server is undiscovered, a saved level is sent as-is; the chosen level is saved per server/model in `state.toml`
  (`[effort.<server>]`; nothing without a selected model) and sent verbatim as
  `reasoning_effort` (llamacpp, deepseek) or `reasoning.effort` (openrouter) —
  `Endpoint.effort_payload`. Load errors are shown at startup and on `/reload`/`/config`, and
  every reload rebuilds the live endpoint if its server table or selection
  changed. `type = "openrouter"` has no `base_url` (always
  `https://openrouter.ai/api/v1`); setting one is reported as a load error.
- **State** (machine-written, disposable): `state.toml` holds `last_server`,
  `[last_model]` (per-server selection), `active_theme`, and `[effort]`.
  Written by `set_active_server` / `save_model_selection` / `save_active_theme`
  / `save_effort`. Legacy `active_model` and `[model_catalog]` keys are
  accepted and ignored (dropped on the next write). The discovery catalog is
  in memory only (`Config.models_by_server`, kept across reloads), so a
  changed server can never be shadowed by a stale copy.

## Editing

- `/config <section>` opens the section file in `$VISUAL`/`$EDITOR` and reloads
  on exit; no argument lists the sections. `section` is one of `ui`, `context`,
  `debug`, `styles`, `servers`, `theme`, plus `sandbox [name]` (one sandbox's file;
  a picker without a name) and `role`.
- `/sandbox config` is the equivalent shortcut; `/sandbox` manages the sandbox
  lifecycle (see [sandbox.md](./sandbox.md)).
- `/edit <path>` opens any file.
- `/config role <name>` opens (creating if needed) `roles/<name>.toml`;
  `/config role delete <name> confirm` removes it.
- `/config theme` opens `themes.toml`; `/config theme <id>` first materializes a
  `[themes.<id>]` override section from that theme's current palette (with name
  suggestions as you type the id), then opens the file.
- `/theme` opens a picker; `/theme <name>` selects directly. The choice is
  persisted in `state.toml` (`active_theme`), falling back to `ui.toml`'s
  `theme` when unset.
- The TUI suspends/resumes around the editor (`ui/external_editor.py`,
  `ui/tui/terminal.py`).

## Themes

A theme is a palette (`themes.toml`, `[themes.<name>]`) mapping the eleven
`_theme` fields (`BACKGROUND`, `DEFAULT`, `MUTED`, `ERROR`, `WARNING`,
`SUCCESS`, `PERMISSION`, `TOOL`, `USER`, `ASSISTANT`, `FOCUSED`) to either a
`"#RRGGBB"` hex string or an ANSI table (`{ ansi = 90 }`, `{ ansi = 39, bg = 49 }`).
Missing entries inherit the built-in base of the same name (or `terminal`).
Built-ins are always available and are listed by `/theme` (or
`colors.theme_names()`): `terminal` (default), `pastel`, `nord`, `dracula`,
`gruvbox`, `solarized`, `one-dark`, `catppuccin`, `tokyo-night`, `rose-pine`,
`everforest`, `monokai`, `ayu-dark`, `kanagawa`.
`colors.available_themes()` merges them with the user definitions and
`set_theme()` applies one in place. Markdown/syntax styles remain a separate
global layer in `styles.toml`.

The `/theme` picker previews a theme as the highlight moves (`on_highlight`),
persists only on accept, and on cancel reloads the configured theme
(`reload_config()` + re-apply).

## Roles

A `Role` is a prompt plus a per-tool approval setting (`no` / `ask` / `yes`),
stored one file per role under `roles/<name>.toml`. `PermissionGate`
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
selection, shown red with a notice. Failed discoveries are recorded in
`Config.discovery_errors` and shown for every configured server (an error for
the active one, a warning for the others), as are unset `api_key_env`
variables.

**Context / ui:** `context_max_files`,
`context_max_depth`, `context_ignore_gitignore`,
`context_max_image_mb` (`context.max_image_mb`, largest attachable image),
`context_sessions` (`context.sessions`, conversations saved per project; 0 = off),
`context_compact_filter_thoughts` / `context_compact_filter_tool_calls`
(`/compact` input: reasoning left out; each tool call one line, no output;
both default on), `context_diff_context` (`context.diff_context`: lines around
each change in `/diff`, `"function"` or `"all"`, default 3);
`ui_theme`, `ui_box_style`,
`ui_status_bar_fields`, `ui_max_input_height` (input box caps + scrolls past
this many wrapped lines), `ui_stream_smoothing` / `ui_smooth_target_fps`
(streamed-text reveal smoothing), `target_fps`, and the rest of the `ui_*`
attrs, including `ui_thought_min_tokens` (reasoning shorter than this gets
no transcript line; 0 = show all). `spinner_fps` is retired (`_RETIRED_UI`): tool and thinking lines show
ticking elapsed time instead of a spinner. `context.format` (the project tree was
never sent to the model) and `ui.debug_console_height` (the console is never shown) and the per-message
metrics keys (`show_metrics`, `metrics_show_*`; nothing displayed them) are
retired too. Colour keys (`sandbox_*_color`) are validated at load: a palette
name or `#rrggbb`, else a load error and the default is kept.

The `sandbox` status-bar field is composed from `ui_sandbox_glyph` +
`ui_sandbox_prefix` + the runtime name; it is green (`ui_sandbox_active_color`)
when a sandbox is active and orange (`ui_sandbox_inactive_color`) when tools run
unsandboxed. Both colors accept a palette name or `#rrggbb`.

**Styles / themes:** `config.markdown_styles`, `config.syntax_highlight_styles`;
`config.themes`, `config.active_theme`, `config.get_active_theme()`,
`config.save_active_theme(name)`.
