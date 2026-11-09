# Troubleshooting

## moka opens with no model

moka starts without a configured server. Run:

```text
/config servers
```

Uncomment an example, save the editor, then run:

```text
/model
```

See [Getting started](getting-started.md) or [Models and providers](models.md).

## “Unknown section” from /config

`/config` accepts section names, not filenames:

```text
/config servers
/config ui
/config context
/config debug
```

Do not use `/config servers.toml`.

## No editor opens

moka uses `$VISUAL`, then `$EDITOR`. Set one before starting moka:

```bash
export VISUAL=nano
moka
```

## The server is unreachable

Check the address first:

```bash
curl http://localhost:8080/v1/models
```

For llama.cpp, the usual base URL is:

```toml
base_url = "http://localhost:8080/v1"
```

The address must include `/v1` for an OpenAI-compatible server. Check that the
server is running and that its port matches.

For OpenRouter or DeepSeek, make sure the API key variable is exported in the
same shell that started moka.

## No models appear

- Confirm the server reports models through `/models`.
- For OpenRouter, check the `models = [...]` whitelist in `servers.toml`.
- Check the notice band for connection or authentication errors.
- Run `/reload` after editing `servers.toml` by hand.
- Run `/debug` to inspect the debug log.

## A config change did not apply

Files opened through `/config` reload when the editor closes. For edits made
outside moka, run:

```text
/reload
```

Validation errors appear in the notice band and in the transcript. moka keeps
the last valid values for settings that fail to load.

## Context is full

Use:

```text
/compact
```

to summarize the conversation, or `/clear` to start a new one. Auto-compaction
uses `context.compact_at`; a server that does not report a context window cannot
use automatic compaction.

## An image was rejected

The selected model may not accept images. Choose a vision-capable model with
`/model`, or remove the image. The `context.max_image_mb` setting caps image
size.

## A sandbox will not start

- Check that `podman`, `docker`, or `bubblewrap` is installed.
- For container sandboxes, run `/sandbox build <name>` after creating or editing
  the Dockerfile.
- The image must provide `python3`.
- Check the notice band and `/debug` for the runtime error.

## moka refuses to start inside a shell

A shell opened with `/terminal` sets `MOKA_TERMINAL`. moka refuses to start in
that shell to avoid two TUIs fighting over the terminal. Type `exit` to return
to the running moka.

## Where are the logs?

Open the debug view with:

```text
/debug
```

To write a persistent debug log, set `log_enabled = true` in `debug.toml` via
`/config debug`, then reload or restart moka.
