# moka

A terminal coding assistant for local and cloud models.

moka runs in your project folder. The model can read and edit files and run
shell commands, with every tool gated by a role you control and optionally
isolated in a sandbox. It works with llama.cpp, OpenAI-compatible servers,
OpenRouter, and DeepSeek. Configuration is plain TOML.

![moka](img/screenshot.png)

## Requirements

- Linux, Python 3.10+
- A model endpoint: llama.cpp, another OpenAI-compatible server, OpenRouter, or
  DeepSeek
- An editor (`$VISUAL` or `$EDITOR`) for `/config`, `/diff`, and file editing
- Optional: podman, docker, or bubblewrap for sandboxes
- Optional clipboard tools: `wl-paste` or `xclip` for image paste; `xclip`,
  `xsel`, or `wl-copy` for copying

## Installation

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/roackim/moka-code.git
```

or with [pipx](https://pipx.pypa.io):

```bash
pipx install git+https://github.com/roackim/moka-code.git
```

To update later: `uv tool upgrade moka-code` or `pipx upgrade moka-code`.

Then start moka from the project you want to work on:

```bash
cd my-project
moka
```

## First run

moka starts with no model configured. Open the server settings, uncomment one
of the examples, save, and pick a model:

```bash
/config servers
/model
```

For llama.cpp:

```toml
[servers.local]
type = "llamacpp"
base_url = "http://localhost:8080/v1"
```

For OpenRouter, export `OPENROUTER_API_KEY` before starting moka:

```toml
[servers.openrouter]
api_key = "$OPENROUTER_API_KEY"
models = ["deepseek/deepseek-v4.1-flash"]  # the ids /model should offer
```

For DeepSeek, export `DEEPSEEK_API_KEY`:

```toml
[servers.deepseek]
api_key = "$DEEPSEEK_API_KEY"
```

An OpenAI-compatible server uses `type = "openai-compatible"` with a
`base_url`. See the commented examples in `servers.toml` for the details.

## Features

- **Tools with permissions.** The model can read, write and edit files, run
  shell commands, and ask you questions. Each tool is allowed, asks first, or is
  disabled per role. Switch roles with `/role`; edit one with `/role edit`.
- **Sandboxes.** Run tools inside podman, docker, or bubblewrap. Sandbox
  definitions can be global or local to a project; the active sandbox is chosen
  per project with `/sandbox`.
- **Files and prompts.** Type `@` to reference a project file, paste an image
  with Ctrl+V, or use `!name` to expand a reusable prompt from `prompts/`.
- **Conversations.** Sessions are saved automatically per project. Resume one
  with `/session` or `moka --resume` (`-r`). `/export` and `/import` move a
  conversation as a single file, images included. `/private` deletes the current
  session file and saves or logs nothing until `/clear`.
- **Context management.** Long conversations compact automatically, and
  `/compact` summarizes one by hand. The status bar shows context use and cost.
- **Models and reasoning.** Pick a model with `/model` and a reasoning effort
  with `/effort`. Reasoning is shown above answers and replayed to servers that
  support it.
- **Review and shell.** `/diff` shows uncommitted changes. `/terminal` opens a
  shell in the project, and `/shells` manages commands running in the background.
- **Themes.** Pick a built-in or custom theme with `/theme`, or edit the palette
  with `/theme edit`.

## Common commands

| Command | What it does |
|---|---|
| `/config [section]` | Edit a settings file and reload it |
| `/model`, `/effort` | Choose the model and reasoning effort |
| `/role` | Switch roles or edit their permissions |
| `/sandbox` | Create, start, stop, or edit sandboxes |
| `/theme` | Pick or edit a color theme |
| `/prompt` | Manage reusable `!prompt` files |
| `/diff` | Review uncommitted changes |
| `/session` | Resume, rename, or delete saved sessions |
| `/export`, `/import` | Save or load a conversation as one JSON file |
| `/compact` | Summarize the conversation to free context |
| `/private` | Delete the session file and stop saving until `/clear` |
| `/clear` | Clear the conversation and leave private mode |
| `/reload` | Apply hand-edited config and role files |
| `/edit` | Open a file in `$VISUAL` or `$EDITOR` |
| `/terminal`, `/shells` | Open a host shell, or manage running commands |
| `/help` | Full command reference |

## Shell integration

`/terminal` opens a shell in the project while moka keeps running. Shells opened
by moka have `MOKA_TERMINAL` set; moka refuses to start inside one. To mark the
prompt with a colored `(moka)`, add this to your `~/.bashrc`:

```bash
[ -n "$MOKA_TERMINAL" ] && PS1="\[\e[38;5;173m\](moka)\[\e[0m\] $PS1"
```

or to your `~/.zshrc`:

```zsh
[[ -n $MOKA_TERMINAL ]] && PROMPT="%F{173}(moka)%f $PROMPT"
```

## Configuration

Configuration lives in `~/.config/moka/` (or `$MOKA_CONFIG_DIR`) and is created
on first run with commented examples. `/config` opens a picker; hand edits are
applied with `/reload`, while an editor opened by `/config` reloads when it
closes.

| Section | File | Contents |
|---|---|---|
| `servers` | `servers.toml` | Model servers and models |
| `ui` | `ui.toml` | Transcript, layout, status bar, streaming, markdown and code styles |
| `context` | `context.toml` | File picker, diffs, image size, session retention, compaction |
| `debug` | `debug.toml` | Debug logging |

Roles, themes, prompts, and sandboxes each have their own file. Prompts and
sandboxes can be global or project-local. Manage them with `/role`, `/theme`,
`/prompt`, and `/sandbox`.

`state.toml` and `projects/` are written by moka. Leave them alone.
