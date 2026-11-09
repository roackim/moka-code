# Getting started

This guide takes you from a clean install to your first file edit. It assumes
you already have a model endpoint running; the examples use a local llama.cpp
server.

Use a scratch project for your first session. Once you are comfortable with the
permission model, point moka at a real repository.

## What you need

- Linux and Python 3.11+
- A running model endpoint: llama.cpp, an OpenAI-compatible server, OpenRouter,
  or DeepSeek
- An editor available through `$VISUAL` or `$EDITOR`

## 1. Install moka

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/roackim/moka-code.git
```

Or with [pipx](https://pipx.pypa.io):

```bash
pipx install git+https://github.com/roackim/moka-code.git
```

Check that the command is available:

```bash
moka --help
```

## 2. Have a model endpoint ready

moka does not start or manage model servers. This guide assumes a llama.cpp
server is already running at `http://localhost:8080/v1`.

For the other supported providers and their server settings, see
[Models and providers](models.md).

## 3. Start moka

From a scratch project directory:

```bash
mkdir ~/moka-first-run
cd ~/moka-first-run
moka
```

moka starts without a configured server.

## 4. Configure the server

Run:

```text
/config servers
```

Your editor opens `servers.toml`. Uncomment the llama.cpp example, or add:

```toml
[servers.local]
type = "llamacpp"
base_url = "http://localhost:8080/v1"
```

Save and close the editor. moka reloads the file automatically.

Then choose a model:

```text
/model
```

The picker lists the models reported by the server. Select one.

## 5. Check the permission defaults

A fresh install seeds an `agent` role with every tool set to `yes`. That means
writes and shell commands are auto-approved without asking.

If you want to review actions, run:

```text
/role edit agent
```

Set the tools you care about to `ask`, for example:

```toml
read  = "yes"
write = "ask"
edit  = "ask"
bash  = "ask"
ask   = "yes"
```

Save and close the editor. See [Permissions and roles](permissions.md) for the
full model.

## 6. Ask for a change

In moka, type:

```text
Create hello.txt with a short greeting.
```

Depending on your role, moka may ask before writing the file. Approve the tool
call, then check the result in your shell:

```bash
cat hello.txt
```

If the file exists, the basic loop is working: the model selected a tool,
moka asked for permission if configured, and the tool ran in the workspace.

## Cloud models

If you would rather use OpenRouter or DeepSeek, export the appropriate key
before starting moka and follow the server examples in
[Models and providers](models.md).

## Where to go next

- Learn the permission model: [Permissions and roles](permissions.md)
- Isolate tool execution: [Sandboxes](sandboxes.md)
- Change settings safely: [Configuration](configuration.md)
- Fix connection problems: [Troubleshooting](troubleshooting.md)
