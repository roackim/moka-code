# moka-code

A terminal coding assistant for local and cloud models. Chat with a model that
can read and edit your files and run commands — with your approval, optionally
inside a sandbox.

![moka in a terminal](docs/screenshot.png)

---

## Install

You need Python 3.10+ and a model: a local [llama.cpp](https://github.com/ggml-org/llama.cpp)
or [Ollama](https://ollama.com) server, or an [OpenRouter](https://openrouter.ai) API key.

```bash
pipx install git+https://github.com/roackim/moka-code.git
moka
```

Run `moka` from the project folder you want to work on.

---

## First start

moka starts with no model configured. Everything is set up with **`/config`**,
which opens a config file in your `$EDITOR` and applies it when you close it.

1. Type **`/config servers`**. The file lists commented examples; uncomment one:

   ```toml
   # llama.cpp
   [servers.local]
   type = "llamacpp"
   base_url = "http://localhost:8080/v1"

   # Ollama
   [servers.ollama]
   type = "ollama"
   base_url = "http://localhost:11434/v1"

   # OpenRouter — export OPENROUTER_API_KEY=sk-or-... before starting moka
   [servers.openrouter]
   type = "openrouter"
   base_url = "https://openrouter.ai/api/v1"
   api_key_env = "OPENROUTER_API_KEY"

   [servers.openrouter.models."deepseek/deepseek-v4.1-flash"]  # one table per model
   ```

2. Save and close the editor.
3. Type **`/model`** and pick a model.

That's it — type a message and press Enter.

---

## Features

- **Tools with approval** — the model can `read`, `write`, `edit` files and run
  `bash`. Each tool is `yes`, `ask` or `no` per **role**: `agent` (all tools)
  and `chat` (none) are built in; switch with `/role`, create or edit one with
  `/config role <name>`.
- **Sandboxes** — run the tools inside podman, docker or bubblewrap instead of
  on your machine. `/sandbox config` to define one for the project,
  `/sandbox start <id>`, `/sandbox stop`, `/sandbox terminal` for a shell inside.
- **Images** — paste an image with **Ctrl+V** or mention a file with `@shot.png`;
  the model can also `read` image files. Vision support is detected: a
  text-only model gets a clear refusal instead of an error.
- **Conversations** — `/export <file>` saves the whole conversation (images
  included), `/import <file>` restores it. `/compact` summarizes a long
  conversation to free context; `/clear` starts over; `/stop` interrupts.
- **Local and cloud models** — llama.cpp, Ollama, OpenRouter and
  OpenAI-compatible servers; `/model` switches between them. The status bar
  shows the model, context use and, on OpenRouter, the cost.
- **Reasoning models** — the model's thinking is shown collapsed above its
  answer and sent back to it where the server supports it.
- **Your terminal, not a web page** — `/terminal` opens a shell (the
  conversation keeps running), `$ command` runs a quick shell command, `/edit`
  opens a file in your editor.
- **Readable answers** — markdown with colored headings, code highlighting and
  tables that fit the width. Change the look with `/theme` and `/config styles`.

---

## Using it

| Key | Action |
|---|---|
| **Enter** / **Alt+Enter** | send / new line |
| **@** | pick a file to mention |
| **/** | commands (Tab to complete) |
| **↑ ↓** | move through the conversation |
| **→ ←** | step into an answer's parts (code blocks, tables) and back |
| **c** | copy the selected message, part, or mouse selection |
| **o** | show a tool call's full output |
| **a** / **x** | allow / deny a tool call |
| **Esc** / **i** | from the input to the conversation / back to the input |

Drag with the mouse to select text, then press **c**.

Type **`/help`** for every command.

---

## Configuration

Config files live in `~/.config/moka/` and are created with commented examples
on first run. Open any of them with `/config <name>`:

| `/config …` | What it sets |
|---|---|
| `servers` | model servers and models |
| `ui` | look and behavior (theme, banner, status bar) |
| `context` | what moka sends about your project (file tree depth, image size limit) |
| `styles` | markdown and code colors |
| `role <name>` | a role's system prompt and tool permissions |

Sandboxes are per project and stored in your config, never in the repo
(`/sandbox config`).
