# moka — Agent Entry Point

Read in this order before proposing or making changes:

1. `.wiki/notes/principles.md` — **canonical design principles** (read first).
2. `HANDOFF.md` — current state, architecture, gotchas, working preferences.
3. `.wiki/` — documents what exists. It describes state, **not** direction;
   never infer intent from it.

Do not add features or UI surfaces unless explicitly asked. Config files are the
settings UI. Delete before you design. If direction is unclear, ask first.

Never commit or `git add` unless asked.

## Adding or deprecating a config key

Flat section files (`ui`, `context`, `debug`) are kept in sync with
their templates on startup (and when `/config <section>` opens them):

- `settings.sync_config_files()` runs in `main()`; it inserts the commented
  template line for any key missing from an existing user file and removes lines
  whose key is in that section's `_RETIRED_*` set. Structured files (`styles`,
  `servers`, `theme`) are user-authored tables and are never synced.

To add a key:
1. Add it to the section's `_*_SPEC` (key -> `(attr, kind)`) and to
   `Config._apply_defaults`.
2. Add a commented line to the matching `DEFAULT_*_TOML` template, in the same
   order as the spec. A guard test asserts every spec key has a commented
   template line.
3. Do **not** hand-edit user files; the sync inserts it on next startup.

To deprecate a key:
1. Remove it from the `_*_SPEC` and the `DEFAULT_*_TOML` template.
2. Add its name to that section's `_RETIRED_*` set so existing files are
   cleaned up on next startup.

Only template keys and registered retired keys are ever touched — never unknown
or user-specific keys.
