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
name), so the choice travels with the user, not the checkout. `/config sandbox`
edits the file; `/sandbox <id>` selects the `active` entry for the session
(live-swaps the transport). An entry is `type` (`podman` / `docker` /
`bubblewrap`) plus `image`, optional `dockerfile`, `network`, `timeout`, and
`run_args`.

`build_argv()` builds the runtime command line (network off by default,
read-only rootfs, `--cap-drop=all`, no-new-privileges, workspace-only mount, the
host's `worker.py` mounted read-only). `SandboxProcess` owns the process and the
JSONL protocol (lazy start, timeout, respawn, stop); `SandboxTransport` is the
`ToolTransport` adapter the harness uses. Approval (`ask`) still gates host-side
before dispatch, and secrets/LLM calls never enter the container.

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
