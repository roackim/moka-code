# Security

Moka runs shell commands and reads/writes files on behalf of an LLM agent.
There is **no security layer inside moka**: no command parsing, no allowlist,
no path confinement. The safety model is deliberately minimal and explicit.

Moka can *optionally* launch an external sandbox (a per-project `/sandbox`
selection, see below) so the tool bodies run in a container or `bubblewrap`
instead of in-process. That is transport, not policy: the isolation is the
runtime's, and the user names the backend explicitly.

---

## Threat model

- The LLM may generate tool calls that read, write, or delete anything the
  process can reach, and shell commands with any operators it likes.
- moka does not try to tell safe commands from dangerous ones. Chained-command
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
| bare moka, in-process tools | none | `ask` on `write` / `edit` / `bash` |
| `/sandbox start <id>` active | the container / `bubblewrap` | `yes` (the mount is the wall) |
| moka run inside the user's own container | the container | `yes` |

See `plans/sandbox_worker.md` for the design (supersedes
`plans/containerization.md`) and [sandbox.md](./sandbox.md) for the subsystem.

## Sandboxes

Sandboxes are per project, declared in the user config and selected with
`/sandbox start <id>`. The security-relevant invariants:

- **The image must provide `python3`** (`worker.py` is stdlib-only). `/sandbox
  init` uses friendly bases (`python`/`debian`/`ubuntu`); non-`python` bases get
  an uncommented `apt-get … install python3`.
- Containers run with no network (unless `network = true`), a read-only rootfs,
  `--cap-drop=all`, no-new-privileges, and only the workspace + `worker.py`
  mounted.
- **bubblewrap** binds only system dirs; it uses the system `python3` and
  creates `/opt` (`--dir`) because bwrap's root is empty. `run_args` bind
  sources must already exist on the host.
- Worker stderr is surfaced in the failure message (and to the debug stream),
  so a bad image or missing interpreter is diagnosable rather than opaque.
- Approval (`ask`) still gates host-side before dispatch; secrets and LLM calls
  never enter the sandbox.
- `require_sandbox = true` on a role locks the conversation until a sandbox is
  active (derived, not stored).

Full details (modules, protocol, command surface, build) are in
[sandbox.md](./sandbox.md).

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
