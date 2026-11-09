# Configuration

moka is configured with TOML files. There is no settings screen: `/config`
opens the relevant file in `$VISUAL` or `$EDITOR`, and the file itself documents
the available keys.

## Where the files live

The default directory is:

```text
~/.config/moka/
```

Set `MOKA_CONFIG_DIR` to use a different directory:

```bash
MOKA_CONFIG_DIR="$HOME/.config/moka-dev" moka
```

This is useful for testing a clean first run without touching your normal
configuration.

## Editing and reloading

Use `/config` to open a picker, or pass a section name directly:

```text
/config servers
/config ui
/config context
/config debug
```

The editor reloads moka when it closes. If you edit a file outside moka, run:

```text
/reload
```

moka never applies config changes in the background.

## Main files

| Section | File | What it controls |
|---|---|---|
| `servers` | `servers.toml` | Model servers, models, API keys, provider routing |
| `ui` | `ui.toml` | Transcript, layout, status bar, streaming, markdown and code styles |
| `context` | `context.toml` | `@` file picker, diffs, image size, session retention, compaction |
| `debug` | `debug.toml` | Debug logging |

Each file starts with commented examples. A line beginning with `#` is a
setting you can uncomment; `##` lines are help text.

Other files:

| Path | What it is |
|---|---|
| `roles/<name>.toml` | One role: prompt, tool permissions, technical settings |
| `themes/<name>.toml` | One color palette |
| `prompts/<name>.md` | Global reusable prompt, expanded with `!name` |
| `sandboxes/<name>.toml` | Global sandbox definition |
| `projects/<name>_<hash>/` | Project-local sandboxes and active selection |
| `state.toml` | Last server/model, theme, role, and other machine-written state |

Roles and themes are global. Prompts can also be project-local under
`.moka/prompts/`, and sandboxes can be project-local under `projects/`. Use
`/role`, `/theme`, `/prompt`, and `/sandbox` to manage them rather than editing
paths by hand.

Do not edit `state.toml` or files under `projects/`. moka writes them.

## Config synchronization

On startup and when `/config <section>` opens a flat section, moka updates the
file with newly added keys and removes keys that have been retired. It preserves
your values, comments, and ordering, and writes a `.bak` backup before changing
the file.

Structured files such as `servers.toml`, themes, and sandboxes are never
rewritten this way.

## Common settings

Some useful starting points:

- `ui.show_banner`: show or hide the startup banner
- `ui.status_bar`: customize the status bar template
- `context.max_files` and `context.max_depth`: limit the `@` file picker
- `context.max_image_mb`: maximum image size
- `context.sessions`: how many conversations to retain per project
- `context.compact_at`: when to auto-compact, as a fraction of the context window
- `debug.log_enabled`: write a debug log

The commented defaults in the files are the authoritative reference. Run
`/reload` after changing a value by hand.

## Related guides

- [Models and providers](models.md)
- [Permissions and roles](permissions.md)
- [Sandboxes](sandboxes.md)
- [Troubleshooting](troubleshooting.md)
