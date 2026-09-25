# Security

Pico runs shell commands and reads/writes files on behalf of an LLM agent.
There is **no security layer inside pico**: no command parsing, no allowlist,
no path confinement. The safety model is deliberately minimal and explicit.

Pico can *optionally* launch an external sandbox (a per-project `/sandbox`
selection, see below) so the tool bodies run in a container or `bubblewrap`
instead of in-process. That is transport, not policy: the isolation is the
runtime's, and the user names the backend explicitly.

---

## Threat model

- The LLM may generate tool calls that read, write, or delete anything the
  process can reach, and shell commands with any operators it likes.
- pico does not try to tell safe commands from dangerous ones. Chained-command
  splitting cannot be done reliably (command substitution, `bash -c`, `xargs`,
  interpreters, obfuscation), so allowlists are a security illusion and
  deny-lists fail open to obfuscation.
- The boundary belongs to the environment: a container, VM, `bubblewrap`, or
  the user watching the prompt.

## Execution modes, declared by the user

The whole safety story is the per-tool approval setting (`roles.py`): each tool
is `no` / `ask` / `yes`, and the user picks it explicitly — nothing is inferred.

| Situation | Boundary | Tool settings |
|---|---|---|
| bare pico, in-process tools | none | `ask` on `write` / `edit` / `bash` |
| `/sandbox <id>` active | the container / `bubblewrap` | `yes` (the mount is the wall) |
| pico run inside the user's own container | the container | `yes` |

See `plans/containerization.md` for the decision record and
`plans/sandbox_worker.md` for the sandboxed-worker design.

## Sandboxes (`projects.py`, `sandbox.py`)

Sandboxes are **per project**, stored outside the repo at
`~/.config/pico-chat/projects/<name>.toml` (name = the workspace directory
name), so the choice travels with the user, not the checkout. Manage with
`/sandbox` (verb-first):

| Subcommand | Behavior |
|---|---|
| `/sandbox config` | Edit the project file (alias of `/config sandbox`) |
| `/sandbox start [id]` | Activate; no id lists the project's sandboxes (id/type/description); missing image offers to build |
| `/sandbox build <id>` | Build the image (explicit; streams to activity; does not activate) |
| `/sandbox init <podman\|docker> [base]` | Write a starter `Containerfile`/`Dockerfile` and print its path |
| `/sandbox quit` | Deactivate (back to in-process) |

An entry is `type` (`podman` / `docker` / `bubblewrap`) plus optional
`description`, `image`, `dockerfile`, `network`, `timeout`, and `run_args`.
The project file ships a thorough commented reference for each type.

**The image must provide `python3`.** pico runs its stdlib-only `worker.py`
inside the container with `python3` (not `python`, which only `python:*` images
guarantee). `/sandbox init <podman|docker> [base]` offers friendly base names — `python`
(`python:3.12-slim`), `debian` (`debian:stable-slim`) and `ubuntu`. `python`
ships `python3`; `debian`/`ubuntu` get an uncommented
`RUN apt-get … install python3` so the starter still works. worker.py needs no
pip packages, only the interpreter.

`build_argv()` builds the runtime command line (network off by default,
read-only rootfs, `--cap-drop=all`, no-new-privileges, workspace-only mount, the
host's `worker.py` mounted read-only). `SandboxProcess` owns the process and the
JSONL protocol (lazy start, timeout, respawn, stop); `SandboxTransport` is the
`ToolTransport` adapter the harness uses. Approval (`ask`) still gates host-side
before dispatch, and secrets/LLM calls never enter the container.

**Preflight / build.** Selecting a sandbox checks the runtime binary and image.
pico never builds implicitly: if `dockerfile` is set it prints the exact
`... build ...` command and offers `/sandbox build <id>`; if not, it reports the
missing image and does not activate. `/sandbox init [base]` writes a commented
`Containerfile.pico` starter. Startup appends a warning (not a prompt) when the
project's active sandbox is not ready.

**bubblewrap interpreter.** `bwrap` binds only standard system dirs. Since
worker.py is stdlib-only, the launcher prefers the system
`/usr/bin/python3` (already inside the bind) over `sys.executable`, which for a
venv/pipx/pixi run lives outside it; if it must use an out-of-tree interpreter
it binds that prefix too. bwrap starts from an **empty root**, so the launcher
creates `/opt` (`--dir`) before binding the worker there. `run_args` bind
sources must already exist on the host (bwrap errors otherwise). Worker stderr
is surfaced in the failure message (and to the debug stream), so a bad image or
missing interpreter is diagnosable.

**Role lock.** A role may set `require_sandbox = true`. While such a role is
active and no sandbox is, the conversation is locked: `on_user_submit` refuses
normal messages and `Harness.chat()` yields an error, but slash commands still
work so `/sandbox <id>` (or `/role`) can unblock. The lock is derived, not
stored, so activating a sandbox lifts it automatically.

## Permission gate (`permissions.py`)

`PermissionGate` is the single decision point. It reads the active role's
per-tool value and returns the harness decision:

- `no` → `deny` — blocked, no prompt. The tool is not exposed to the model.
- `ask` → `ask` — the UI pauses and prompts the user before executing.
- `yes` → `allow` — executes without prompting.

It also builds the prompt text (`build_prompt()`) and owns the async
user-response queue. There is no `SecurityChecker`, no dangerous-pattern list,
and no chain policy.

## Path restrictions

None. File tools resolve relative paths against the workspace and otherwise
operate wherever the process can. Running bare, the user relies on `ask`.
Running in a container, the mount is the boundary.

## Tests

| Test | Coverage |
|------|----------|
| `test_permissions.py` | Gate decisions, prompt text, ask/deny/allow harness flow |
| `test_roles.py` | Role model, files, seeding, validation |
