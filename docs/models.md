# Models and providers

moka can talk to four kinds of server. Each server is one `[servers.<name>]`
table in `servers.toml`.

| Provider | `type` | `base_url` | API key |
|---|---|---|---|
| llama.cpp | `llamacpp` | Optional; defaults to `http://localhost:8080/v1` | Optional |
| OpenAI-compatible server | `openai-compatible` | Required | Optional |
| OpenRouter | `openrouter` | Fixed: `https://openrouter.ai/api/v1` | Required |
| DeepSeek | `deepseek` | Fixed: `https://api.deepseek.com` | Required |

If the table name is the same as the provider type, such as
`[servers.openrouter]`, you can omit `type`. The examples below spell it out to
make the configuration explicit.

## llama.cpp

```toml
[servers.local]
type = "llamacpp"
base_url = "http://localhost:8080/v1"
```

The default is already the usual llama.cpp address, so `base_url` can be left
out. llama.cpp also reports model context size and vision support through its
`/props` endpoint, which lets moka show a more accurate context window.

## OpenAI-compatible server

Use this for vLLM, LM Studio, a proxy, or any server that exposes the
chat-completions API and a `/models` endpoint.

```toml
[servers.custom]
type = "openai-compatible"
base_url = "http://localhost:8000/v1"
api_key = "$CUSTOM_API_KEY"
```

The server should report each model's context length in `/models`. moka needs
that to estimate context use and enable automatic compaction. Omit `api_key` if
the server does not require one.

## OpenRouter

Export the key before starting moka:

```bash
export OPENROUTER_API_KEY="..."
```

Then add the server. The `models` list is the whitelist that `/model` offers:

```toml
[servers.openrouter]
type = "openrouter"
api_key = "$OPENROUTER_API_KEY"
models = ["deepseek/deepseek-v4.1-flash", "qwen/qwen3-coder"]
providers = ["deepseek"]
```

`providers` is optional. When set, it is a strict provider order with fallbacks
disabled. `[]` means OpenRouter's own routing. Per-model routing can be set in
a nested table:

```toml
[servers.openrouter.providers_by_model]
"qwen/qwen3-coder" = ["parasail", "novita"]
```

## DeepSeek

Export the key before starting moka:

```bash
export DEEPSEEK_API_KEY="..."
```

Then add:

```toml
[servers.deepseek]
type = "deepseek"
api_key = "$DEEPSEEK_API_KEY"
```

DeepSeek's API requires earlier reasoning to be sent back when tools are used.
moka handles that through the role's `preserve_thinking` setting, which is on by
default.

## API keys

Use `api_key = "$NAME"` to read an environment variable. Any other value is
used literally, so avoid pasting secrets into a file you might share.

An `api_key` that points at an unset environment variable is reported in the
notice band.

## Selecting a model

Run:

```text
/model
```

moka refreshes the server catalogs and opens a picker. The selected server and
model are saved in `state.toml`.

Use:

```text
/effort
```

to choose a reasoning effort level when the model or provider reports levels.

## Editing and reloading

Edit servers with:

```text
/config servers
```

or edit `servers.toml` directly and run `/reload`. moka does not watch the file.
Invalid entries are reported in the notice band; the corresponding server is
skipped, but the rest keep working.

For connection failures, see [Troubleshooting](troubleshooting.md).
