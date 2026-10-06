# Sandbox

Moka can run **tool execution** inside a container (`podman`/`docker`) or under
`bubblewrap`, while the UI, agent loop, conversation, LLM calls, and config all
stay on the host. The container side is a tiny, stateless **worker** with no
config, no network, no model, and no policy.

This is transport, not policy: moka adds no path confinement or command parsing.
The isolation is the runtime's, and the user names the backend explicitly. See
[security.md](./security.md) for the trust boundary.

---

## Execution modes

| Mode | How tools run | Selected by |
|---|---|---|
| bare (default) | in-process, on the host | no active sandbox |
| podman / docker | host's `worker.py` injected into a container | project `active` sandbox |
| bubblewrap | host process under `bwrap`, no image | project `active` sandbox |

The harness only knows the `ToolTransport` seam, so the tool registry and
schemas are identical in all modes.

## The worker (`moka_code/worker.py`)

A **stdlib-only** module that is both the host's source of tool bodies and the
container entrypoint (`python3 /opt/worker.py`, run as a script so the package
`__init__` never loads). It owns:

- the four verbs `read` / `write` / `edit` / `bash` (plain functions taking
  `cwd`), plus `bash_sync`;
- the replace-block parser folded from `harness/patch_parser.py`
  (`parse_patch` / `apply_patch` / `PatchParseError`);
- the JSONL protocol: `_TOOL_TABLE` dispatch, `handle_request()`, `serve()`,
  `main()`.

Protocol: one request per line on **stdin**, one response per line on **stdout**
(stdout is protocol-only; logs go to stderr). Requests are
`{"id", "tool", "args"}`; `bash` may emit interim
`{"id", "stream": "stdout"|"stderr", "data": …}` frames before
`{"id", "ok": true, "result"}` or `{"id", "ok": false, "error"}`. A bare
`{"op": "shutdown"}` stops the loop. Execution is serialized (one request at a
time).

## Host-side layers

- `harness/tools.py` keeps the `@tool` registry (names, descriptions, JSON
  schemas) and binds each to a `ToolTransport`. `InProcessTransport` runs the
  handler against a `MinimalToolset`; `SandboxTransport` (in `sandbox.py`) sends
  the request over JSONL. `RegisteredTool.execute(on_output=..., **args)`
  delegates to the transport.
- `moka_code/sandbox.py` owns the launcher and client:
  - `ContainerSpec` (runtime/image/network/run_args/dockerfile) and
    `build_argv()`;
  - `SandboxProcess` — the process + JSONL client: lazy start, id-correlated
    request/response, interim `stream` frames to `on_output`, a request limit
    (see below), respawn after a crash, graceful `stop()`, sync `kill()`;
  - `SandboxTransport` — the `ToolTransport` adapter (`is_sandbox = True`);
    tool-level failures return as result strings (parity with in-process),
    transport failures propagate;
  - preflight/build: `runtime_available()`, `image_present()`,
    `build_command()`, `run_build()`, `containerfile_name()`,
    `containerfile_starter()`, `resolve_base()`.
- `harness/harness.py` builds the transport (`_build_transport`), swaps it live
  (`set_sandbox`), exposes `sandboxed()` / `sandbox_required()`, gates `chat()`
  when a role requires a sandbox, and yields interim `ToolOutput` events.

### Runtime command line

Containers (`podman`/`docker`): `run -i --rm -w /workspace --read-only
--tmpfs /tmp --cap-drop=all --security-opt no-new-privileges` (plus
`--network=none` unless `network = true`), `run_args`, then the workspace mount
(`<workspace>:/workspace:Z`), the worker mount
(`<installed worker.py>:/opt/worker.py:ro`), the image, and
`python3 /opt/worker.py`.

bubblewrap: binds `/usr`, `/lib`, `/lib64`, `/bin`, `/etc` read-only, creates
`/opt` (`--dir` — bwrap's root is **empty**), binds the worker there and the
workspace at `/workspace`, `--tmpfs /tmp`, `--proc /proc`, `--dev /dev`,
`--unshare-net`/`--unshare-pid`/`-ipc`/`-uts`/`-cgroup-try`, `--die-with-parent`.
The environment is cleared (`--clearenv`): only `HOME`/`TMPDIR` (= the `/tmp`
tmpfs), a fixed `PATH`, and `TERM`/`LANG`/`LC_ALL` (if set on the host) are
passed, so host API keys and venv paths do not leak in. `run_args` are appended
**last** (just before the command), so they override moka's defaults.

**Interpreter requirements.** The image must provide `python3` (worker.py needs
only the stdlib). Containers call `python3` (present on `python:*` and after the
distro `python3` package); `/sandbox init` emits an uncommented
`apt-get … install python3` for `debian`/`ubuntu`. bubblewrap prefers the
system `/usr/bin/python3` (already inside the bind) over `sys.executable`, which
for a pipx/pixi/venv run lives outside the bound dirs; if it must use an
out-of-tree interpreter it binds that prefix. Worker stderr is surfaced in the
failure message (and to the debug stream), so a bad image or missing interpreter
is diagnosable.

## Tool-call timeout

One explicit setting: the role's **`tool_timeout`** (seconds, default 300; a role
file's `# tool_timeout = 300` line). **0 or less (`0`, `-1`) means no limit**: the
command runs until it ends or `/stop`, and the sandbox's request limit is lifted too. The harness sends it with every `bash` call
(`Harness._execute_tool_calls`, like `max_image_bytes` for `read`), so it applies in
bare and sandbox mode alike and the model cannot change it (it is not in the tool
schema; a `timeout` the model sends anyway is overwritten). The worker's `bash`
enforces it and ends the command with "Command timed out after Ns (the role's
tool_timeout)". The sandbox file has **no** `timeout` key (retired: a file that still
has one is reported and skipped). The sandbox's request limit only catches a hung
worker: a bash call's `tool_timeout` + 10 s, `DEFAULT_REQUEST_TIMEOUT` (120 s) for
other tools. The server's own `timeout` (`servers.toml`, default 30 s) is a different
thing: waiting on the model's HTTP response.

## Role of bubblewrap

Linux hosts only. A zero-setup guard against accidents (stray writes outside the
workspace, leaked secrets) for **simple agentic usage** — bash, `python3`,
coreutils; not a boundary against hostile code. It does not grow: when a project
needs tooling that is hard to set up under bwrap (`git`/`node`/`pixi` under `$HOME`,
specific versions, system packages), use a **podman/docker** sandbox
(`/sandbox new <scope> podman`). The bubblewrap starter file says so, and
`run_args` is the only escape hatch (e.g. `--ro-bind ~/.pixi ~/.pixi`).

## Registry & selection (`moka_code/projects.py`)

**One TOML file per sandbox**, in the user config (never in the repo), in one of
two scopes:

```
~/.config/moka/
├── sandboxes/<name>.toml                    # global: usable from any project
└── projects/<dirname>_<hash>/
    ├── project.toml                         # path = "<resolved>", active = "<name>"
    └── sandboxes/<name>.toml                # local: this project's own
```

- A project's folder is `<workspace dir name>_<4 hex of a hash of the resolved
  path>`, so same-named workspaces never share locals. A moved repo gets a new
  folder (its locals and `active` stay behind).
- **Names are unique across both scopes** (`[A-Za-z0-9_-]`, starting with a letter
  or digit; files starting with `_` or `.` are ignored). A name present in both
  scopes is an error (banner + `/reload`) and **neither entry is usable** until one
  is renamed. Scope is only a display tag (`global · name — description`).
- `copy`-only: no inheritance. Divergence = copy, then edit.
- Locals live in the user config, not the workspace, on purpose: the agent can write
  the workspace and must not be able to loosen its own sandbox.
- `load_project()` merges both scopes (+ `active`) and reports bad files, unknown
  keys and collisions; `validate_sandboxes()` returns just those errors;
  `active_spec()` → `ContainerSpec`; `set_active()` writes `project.toml`;
  `create_sandbox()` / `copy_sandbox()` write new files (a copy is a plain file copy,
  so comments survive, and a relative Containerfile beside the source comes along).
- A relative `dockerfile` resolves **next to the sandbox file**, and the build
  context is the Containerfile's folder (not the workspace).

`get_harness()` activates the project's active sandbox at startup. A deleted or
renamed active sandbox is simply no longer active (with an error notice).

**First run.** `main()` calls `seed_default_sandbox()`: when the global
`sandboxes/` folder does not exist yet, it writes the default `bubblewrap` there (a
commented starter), unless that name is already taken for the current project.
Deleting the file later stays deleted; edits are kept.

**Moved repo.** A moved or renamed workspace hashes to a new project folder, leaving
its locals and `active` behind. While the new project has no local sandbox, the
banner warns about a same-named project folder whose recorded `path` is gone and
that holds sandboxes, with the `mkdir -p … && mv …/sandboxes …/` command that moves
them (`moved_project_notes()`).

**Stale image.** For a container with a `dockerfile`, the harness reads the image's
build time (`sandbox.image_created()`, from `image inspect`) when the sandbox is
activated, after `/sandbox build` and on `/reload`. The band warns **"sandbox <name>:
its image is older than its dockerfile → /sandbox build <name>"** while the
Containerfile's mtime is newer (`sandbox_stale()`, a `stat` per refresh).
`/reload` does not rebuild — moka never builds implicitly.

**Reload.** The harness remembers the active sandbox's name and file `stat`
(`sandbox_drift()`): the notice band says **"sandbox <name> changed on disk →
/reload"** (or that its file is gone). `/reload` re-resolves the active sandbox and
applies it (deferred while a response is being written). A running container keeps
its old config until stop/start. See [config.md](./config.md).

## Command surface (`/sandbox`)

A verb-first `Command` **tree** (registry), so dispatch and positional
completion come from the framework (see [ui.md](./ui.md)):

| Subcommand | Behavior |
|---|---|
| `/sandbox` | opens the `start` picker when a sandbox exists (like `/model`, `/role`); otherwise, and for an unknown subcommand, lists the subcommands |
| `/sandbox config [name]` | open that sandbox's file; no name opens a picker (alias of `/config sandbox [name]`) |
| `/sandbox new <global\|local> <type> [name]` | create a file from a type-specific starter (name defaults to the type), then open it |
| `/sandbox copy <name> <global\|local> <new-name>` | copy a sandbox into the other (or same) scope |
| `/sandbox start [name]` | activate; no name opens a picker (tagged `global`/`local`); missing image offers *Build now* / *Cancel* |
| `/sandbox build <name>` | build the image (streams to activity; does not activate) |
| `/sandbox init <name> [base]` | write `<name>.Containerfile`/`.Dockerfile` next to the sandbox's file (bases: `python`→`python:3.12-slim`, `debian`→`debian:stable-slim`, `ubuntu`) |
| `/sandbox stop` | deactivate (back to in-process) |
| `/sandbox terminal` | shell inside the active sandbox (same mounts/network/limits as the tools); `exit` returns |

moka **never builds implicitly**: `start` offers, `build` is explicit. A
missing image prints the exact command; `run_args` bind sources must already
exist on the host.

## Role lock (`require_sandbox`)

A role may set `require_sandbox = true`. While it is active and no sandbox is,
the conversation is locked: `on_user_submit` refuses normal messages and
`Harness.chat()` yields an `Error`; slash commands still work so `/sandbox`
(or `/role`) can unblock. The lock is derived (`sandbox_required()`), not
stored, so activating a sandbox lifts it automatically.

## Output elision

Independent of sandboxing, `harness/elision.py` bounds oversized tool results
before they enter history (head + tail + `… N chars elided …`); the `ToolResult`
event keeps the full output for the UI. See [tools-and-permissions.md](./tools-and-permissions.md).

## Key files

`moka_code/worker.py` · `moka_code/sandbox.py` · `moka_code/projects.py` ·
`moka_code/harness/tools.py` · `moka_code/harness/harness.py` ·
`moka_code/harness/elision.py` · `moka_code/ui/commands/sandbox.py`.
