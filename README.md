# moka

**A terminal AI assistant for local and cloud LLMs** — interactive TUI chat with tool use, file access, and sandboxed command execution.

**English** · [Français](README.fr.md) · [Deutsch](README.de.md)

---

## Requirements

- Python ≥ 3.10
- A running LLM endpoint: [llama.cpp](https://github.com/ggerganov/llama.cpp), [Ollama](https://ollama.com) locally, or an [OpenRouter](https://openrouter.ai) API key for cloud models

---

## Installation

```bash
pipx install git+https://github.com/yourusername/moka.git
```

Or from a local clone:

```bash
pipx install .
```

Then run:

```bash
moka
```

---

## Getting Started

On first launch, moka starts with no server configured. Servers live in
`~/.config/moka/servers.toml`; open it with `/config servers` and add a table:

**Local llama.cpp server:**
```toml
[servers.local]
type = "llamacpp"
base_url = "http://localhost:8080/v1"
```

**Ollama (local models):**
```toml
[servers.ollama]
type = "ollama"
base_url = "http://localhost:11434/v1"
```

**OpenRouter (cloud models):**
```bash
export OPENROUTER_API_KEY=sk-or-...
```
```toml
[servers.openrouter]
type = "openrouter"
api_key_env = "OPENROUTER_API_KEY"
providers = ["anthropic"]            # optional: only these hosts, in this order

# One table per enabled model; its own providers replace the default.
[servers.openrouter.models."anthropic/claude-3.5-sonnet"]
```

Save the file, and `/config` reloads it automatically. Then open the model
picker with `/model` (type to filter) or select directly with
`/model <id>` / `/model <server>:<id>`. Models are discovered live from the
configured servers; selecting one switches to the server that serves it.

---

## Commands

| Command | Description |
|---------|-------------|
| `/help` | List all available commands |
| `/config [section]` | Edit a config section (`ui`, `context`, `debug`, `styles`, `servers`, `theme`) and reload |
| `/edit <file>` | Open a file in `$EDITOR` |
| `/terminal [host]` | Open a shell where tools run (inside the active sandbox, or the host with `host`); `exit` returns — the conversation keeps running meanwhile |
| `/reload` | Reload config files and validate `roles/` from disk |
| `/model` | Open the searchable model picker (type to filter), or select with `/model <id>` |
| `/role` | List roles or switch the active one (`/role <name>`) |
| `/theme` | Pick a color theme (opens a picker) |
| `/compact` | Summarize conversation history to free context space |
| `/import <file>` | Import conversation history from a JSON file |
| `/export <file>` | Export conversation history to a JSON file |
| `/clear` | Clear the conversation history |
| `/stop` | Stop the current generation |
| `/activity` | Toggle the activity overlay (shell/status output) |
| `/exit` | Quit the application |

### Server Management

Servers are defined in `servers.toml`; edit them with `/config servers` and the
config reloads when the editor exits. `/model` lists what each server offers.

Examples (`servers.toml`):
```toml
[servers.local]
type = "llamacpp"
base_url = "http://localhost:8080/v1"

[servers.ds]
type = "openrouter"
api_key_env = "OPENROUTER_API_KEY"

[servers.ds.models."deepseek/deepseek-v4-flash"]
providers = ["deepseek", "fireworks"]
```
Then:
```
/model
/model llama3.1:8b
```

---

## Using the Interface

An empty conversation shows the moka banner (hide it with `show_banner = false`
in `ui.toml`).

### Sending messages

- **Enter** — send message
- **Alt+Enter** or **Ctrl+Enter** — insert a newline (multi-line input)
- **Ctrl+W** / **Ctrl+Backspace** — delete word backward
- **Ctrl+Left / Right** — move cursor by word

### Completions

- Type `@` to open a fuzzy file picker — inserts a file path into your message
- Type `/` to autocomplete commands

### Navigating history

- **↑ / ↓** arrow keys — select messages in the history; a selected message
  gets a bright `▌` bar in the left margin
- Answers with code blocks or tables are split into parts (prose, code block,
  table) once complete. **→** enters the selected answer (the part gets a wide
  `█` bar), **↑ / ↓** then move between its parts, **←** / **Esc** go back to
  the whole answer. `c` copies the selected part — a code block without its
  ```` ``` ```` fences — or the whole answer when it is selected as a whole.
- **Mouse click** — select a message; on an answer, the first click selects it
  whole and a click on the selected answer selects the part under the cursor
- **Esc** — clear the selection; **Enter** / **`i`** — jump to the input
- While a message is selected an action line appears just above the input
  (the status bar stays visible), marked with `▌`:
  - **`c`** — copy the message content to clipboard
  - **`o`** — show a tool call's full output
  - **`a`** / **`x`** — allow / deny a pending permission request
- When the input is focused, the same line shows a muted right-aligned hint:
  `[/] command  [@] file  [$] shell  ↑↓ move` (`@` works mid-text)

Non-conversation output (shell commands, command status, notices) appears in the
activity overlay (`/activity`) as a toast, keeping the transcript to the
conversation itself.

---

## Tool Use & Permissions

The agent has access to four tools: `read`, `write`, `edit` (replace an exact
text block), and `bash` (run a shell command). Each tool is configured **per
role** with one of three values:

- **`yes`** — runs automatically without asking
- **`ask`** — prompts you before executing
- **`no`** — never allowed (hidden from the model entirely)

When the agent requests a tool set to `ask`, a prompt appears:

```
> bash
command: pytest test/
[allow] [deny]
```

You can approve or deny with mouse click or keyboard.

The active role (`roles/<name>.toml`) is the whole policy. Built-in roles —
`agent` (every tool `yes`) and `chat` (no tools) — are seeded on first run.
Switch roles with `/role`, and edit one with `/config role <name>` (creates it
if missing). Because the container/OS boundary belongs to the environment
moka runs in, the convention is: run inside a sandbox → `yes` everywhere; run
bare on the host → `ask` on the mutating tools.

---

## Live Metrics

During generation, moka displays:
- **Speed** (tokens/s)
- **Context usage** (tokens used vs. context window size, color-coded by pressure)
- **Cost** of the current conversation in the status bar, when the provider
  reports it (OpenRouter); hidden for local servers. `/clear` resets it.

---

## Configuration Files

Hand-edited configuration lives in `~/.config/moka/`, split into small
single-concern files:

- `ui.toml` — theme, padding, metrics, fps (flat keys).
- `context.toml` — context building (flat keys).
- `debug.toml` — debug logging (flat keys).
- `styles.toml` — `[markdown_styles.*]` / `[syntax_highlight.*]` overrides.
- `servers.toml` — one `[servers.<name>]` table per server.
- `themes.toml` — `[themes.<name>]` palette overrides (built-ins always exist).
- `roles/<name>.toml` — one file per conversation role (prompt + per-tool
  settings). The file name is the role name.
- `state.toml` — disposable runtime state (last server/model, active theme,
  discovery catalog). Safe to delete.

Missing files are created from fully commented templates (the `servers.toml`
template includes example `llamacpp`, `ollama`, `openrouter` and `openai`
blocks). Edit them with `/config <section>` or `/edit <path>`, and roles with
`/config role <name>`. Configuration is read at startup and only re-applied when
you run `/reload` or restart. The loader validates each file and reports unknown
keys, wrong types, and unparsable TOML (`<file>: ...`); invalid entries fall
back to defaults while the rest of the file still applies.

```toml
# ui.toml
theme = "terminal"

# context.toml
format = "tree"
max_files = 500

# servers.toml
[servers.local]
type = "llamacpp"
base_url = "http://localhost:8080/v1"
```

```toml
# roles/reviewer.toml
description = "Read-only code review"
prompt = "Review code carefully. Do not modify files."

read = "yes"
write = "no"
edit = "no"
bash = "no"
```
