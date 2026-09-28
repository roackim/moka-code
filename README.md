# moka-code

A terminal coding assistant for local and cloud models.

![moka](docs/screenshot.png)

## Install

```bash
pipx install git+https://github.com/roackim/moka-code.git
moka
```

## Setup

Run **`/config servers`**, uncomment a server (llama.cpp, Ollama, OpenRouter…),
save, then pick a model with **`/model`**.

## Features

- File and shell tools, with per-role approval (`/role`)
- Sandboxes: podman, docker, bubblewrap (`/sandbox`)
- Images: paste with Ctrl+V or mention `@file.png`
- Save and restore conversations (`/export`, `/import`)
- Everything else: `/help`, and `/config` for settings
