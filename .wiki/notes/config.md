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
| `ui.toml` | theme, padding, metrics, fps | flat keys |
| `context.toml` | context building | flat keys |
| `debug.toml` | debug logging | flat keys |
| `styles.toml` | `[markdown_styles.*]` / `[syntax_highlight.*]` | tables |
| `servers.toml` | one `[servers.<name>]` table per server | tables |
| `themes.toml` | one `[themes.<name>]` palette per theme | tables |
| `roles/<name>.toml` | one role per file; file name is the role name | role body |
| `projects/<name>.toml` | per-project sandboxes; `<name>` = workspace directory name | tables |
| `state.toml` | last server/model, active theme, effort | machine-written |

`state.toml` is disposable: deleting it only loses cached selections.
`roles/` mirrors the same one-thing-per-file idea (see
[tools-and-permissions.md](./tools-and-permissions.md)).

Missing files are created from fully commented templates
(`settings.DEFAULT_CONFIG_TEMPLATES`) by `Config.ensure_section_file()` /
`ensure_config_files()`. In every template a single `#` marks a setting to
uncomment (a bare `#` spaces a block of settings) and `##` marks help
(guarded by `test_template_help_lines_use_double_hash`). The built-in role files (`agent.toml`, `chat.toml`)
are seeded by `roles.ensure_roles_dir()` on startup.

Existing **flat** files (`ui`, `context`, `debug`) are kept in sync
with their templates: on startup (`settings.sync_config_files()` in `main()`) and
when `/config <section>` opens one, `_sync_flat_file()` inserts the commented
line for any spec key missing from the file (at its template-relative position)
and removes lines whose key is in that section's `_RETIRED_*` set. Only keys
named by the template or a registered retirement are touched; user values,
comments, and ordering are preserved, and the file is written only when the text
changes. Structured files (`styles`, `servers`, `theme`, and per-project
`projects/<name>.toml`) hold user-authored tables and are never synced. See
"Adding or deprecating a config key" in `AGENTS.md`.

### Project files (sandboxes)

`~/.config/moka/projects/<name>.toml` holds a project's sandbox
definitions, keyed by the workspace **directory name** (`projects.project_name`)
and loaded/managed by `projects.py`. The file is self-describing
(`path = "<resolved workspace>"`), lists named `[sandboxes.<id>]` tables
(`type` = podman/docker/bubblewrap plus `description`, `image`, `dockerfile`,
`network`, `timeout`, `run_args`) and an `active = "<id>"` selection. Missing
files are seeded from a thorough commented template; `set_active()` updates the
`active` line in place, preserving comments. moka never writes into the
repository. See [sandbox.md](./sandbox.md).

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
- After editing with `/config <section>`, the command reloads; `/reload` also
  reloads explicitly. Nothing is watched. `/reload` additionally runs
  `roles.validate_roles()` and reports role-file errors.

### Intent vs state

- **Intent** (hand-edited): the section files above; moka never writes them.
  Every `[servers.<name>]` has a `type`; a table named after one
  (`[servers.ollama]`) is that type without the key (otherwise missing/unknown
  → load error, server skipped). Any server may have per-model
  `[servers.<name>.models."<id>"]` tables; OpenRouter lists only the models
  that have one. `providers = [...]` (OpenRouter only; server
  default, or per model, replacing the default) is a strict whitelist tried in
  order — `order` alone would let OpenRouter fall back to any host. The retired
  keys `provider`, `enabled_models` and `model_providers` are reported with
  their replacement and the server is skipped (`_RETIRED_SERVER_KEYS`).
  Effort levels are detected only (no config key): effort-variant ids (`X:low` / `X:high` on
  one server) make `/effort` switch models (`models._effort_variants`); else
  catalog metadata (`endpoint_discovery.efforts_from_metadata`: OpenRouter-format
  `reasoning.supported_efforts` (exact; any OpenAI-compatible server's `/models`
  may carry it), else `supported_parameters` (guessed), else Ollama `thinking`
  capability) gives the levels; `/effort` rediscovers the active server first.
  While the server is undiscovered, a saved level is sent as-is; the chosen level is saved per server/model in `state.toml`
  (`[effort.<server>]`, `""` for a single-model server) and sent verbatim as
  `reasoning_effort` (llamacpp/openai), `reasoning.effort` (openrouter) or
  `think` (ollama, `"none"` → `false`) — `Endpoint.effort_payload`. Load errors are shown at startup and on `/reload`/`/config`, and
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
  `debug`, `styles`, `servers`, `theme`, plus `sandbox` (the current project's
  file) and `role`.
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
`commands.base.auto_select` picks the top of `/model` after discovery; a last
used selection is never replaced and is shown red when it cannot be reached.

**Context / ui:** `context_format`, `context_max_files`,
`context_max_depth`, `context_ignore_gitignore`,
`context_max_image_mb` (`context.max_image_mb`, largest attachable image),
`context_sessions` (`context.sessions`, conversations saved per project; 0 = off);
`ui_theme`, `ui_box_style`, `ui_show_metrics`,
`ui_status_bar_fields`, `ui_max_input_height` (input box caps + scrolls past
this many wrapped lines), `ui_stream_smoothing` / `ui_smooth_target_fps`
(streamed-text reveal smoothing), `target_fps`, and the rest of the `ui_*`
attrs, including `ui_thought_min_tokens` (reasoning shorter than this gets
no transcript line; 0 = show all). `spinner_fps` is retired (`_RETIRED_UI`): tool and thinking lines show
ticking elapsed time instead of a spinner.

The `sandbox` status-bar field is composed from `ui_sandbox_glyph` +
`ui_sandbox_prefix` + the runtime name; it is green (`ui_sandbox_active_color`)
when a sandbox is active and orange (`ui_sandbox_inactive_color`) when tools run
unsandboxed. Both colors accept a palette name or `#rrggbb`.

**Styles / themes:** `config.markdown_styles`, `config.syntax_highlight_styles`;
`config.themes`, `config.active_theme`, `config.get_active_theme()`,
`config.save_active_theme(name)`.
