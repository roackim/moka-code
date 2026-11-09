# Permissions and roles

moka gives the model tools, not unrestricted access. A role decides which tools
are available and whether each one runs automatically, asks first, or is
disabled.

## Read this first

The seeded `agent` role sets every tool to `yes`, so writes and shell commands
run without confirmation. That is convenient and risky.

Before using moka on an important repository, either:

- run `/role edit agent` and set `write`, `edit`, and `bash` to `ask`, or
- use the built-in `chat` role with `/role` if you want no tools at all.

## Tools

| Tool | What it does |
|---|---|
| `read` | Read a text file, or attach an image file |
| `write` | Create or overwrite a file |
| `edit` | Replace one exact block in a file |
| `bash` | Run a shell command in the workspace |
| `ask` | Ask you a question and wait for an answer |

`read`, `write`, `edit`, and `bash` follow the role's permission setting. The
`ask` tool is a special case: `ask` and `yes` both enable it, and it never asks
for approval because asking you is its purpose.

## Role files

Roles live in:

```text
~/.config/moka/roles/<name>.toml
```

A role file contains:

- `description` and `prompt`
- one line per tool, set to `no`, `ask`, or `yes`
- optional `require_sandbox`, `preserve_thinking`, `tool_timeout`, and `color`

The meaning of each tool value is:

| Value | Effect |
|---|---|
| `no` | Disabled, and hidden from the model |
| `ask` | Enabled, but moka asks you before each call |
| `yes` | Enabled and auto-approved |

For example:

```toml
read  = "yes"
write = "ask"
edit  = "ask"
bash  = "ask"
ask   = "yes"
```

## Managing roles

```text
/role
```

opens the role picker. The useful subcommands are:

- `/role select [name]`: switch the active role
- `/role edit [name]`: edit a role file
- `/role create <name>`: create a new role
- `/role path [name]`: show the file path
- `/role remove <name>`: delete a role

Changes made through `/role edit` reload when the editor closes. If you edit a
role file by hand, run `/reload`.

## Locking a role to a sandbox

Set this in a role file:

```toml
require_sandbox = true
```

The role then refuses to run until a sandbox is active. This is useful when you
want a strong rule that tools never execute directly on the host.

Start one with `/sandbox start <name>`. See [Sandboxes](sandboxes.md).

## What permissions do not do

moka does not parse shell commands, enforce path restrictions, or decide which
commands are safe. The permission gate decides only whether a tool call is
allowed, asked, or denied.

The isolation boundary is the environment:

- the permission prompt, when a tool is set to `ask`
- a container or bubblewrap sandbox, when one is active
- a VM or disposable machine around the whole session

For anything untrusted, use a sandbox. For stronger isolation, use both a
sandbox and a role with `require_sandbox = true`.
