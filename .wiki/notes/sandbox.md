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
  - `ContainerSpec` (runtime/image/network/timeout/run_args/dockerfile) and
    `build_argv()`;
  - `SandboxProcess` — the process + JSONL client: lazy start, id-correlated
    request/response, interim `stream` frames to `on_output`, wall-clock
    timeout, respawn after a crash, graceful `stop()`, sync `kill()`;
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
workspace at `/workspace`, `--tmpfs /tmp`, `--unshare-net`/`--unshare-pid`,
`--die-with-parent`.

**Interpreter requirements.** The image must provide `python3` (worker.py needs
only the stdlib). Containers call `python3` (present on `python:*` and after the
distro `python3` package); `/sandbox init` emits an uncommented
`apt-get … install python3` for `debian`/`ubuntu`. bubblewrap prefers the
system `/usr/bin/python3` (already inside the bind) over `sys.executable`, which
for a pipx/pixi/venv run lives outside the bound dirs; if it must use an
out-of-tree interpreter it binds that prefix. Worker stderr is surfaced in the
failure message (and to the debug stream), so a bad image or missing interpreter
is diagnosable.

## Project store & selection (`moka_code/projects.py`)

Sandboxes are **per project**, stored in the user config at
`~/.config/moka/projects/<name>.toml` (`<name>` = workspace directory
name). `load_project()` parses named `[sandboxes.<id>]` entries + `active`;
`active_spec()` → `ContainerSpec`; `set_active()` rewrites the `active` line
without destroying comments; `ensure_project_file()` seeds the commented
template. See [config.md](./config.md).

`get_harness()` activates the project's active sandbox at startup.

## Command surface (`/sandbox`)

A verb-first `Command` **tree** (registry), so dispatch and positional
completion come from the framework (see [ui.md](./ui.md)):

| Subcommand | Behavior |
|---|---|
| `/sandbox` | list subcommands |
| `/sandbox config` | open the project file (alias of `/config sandbox`) |
| `/sandbox start [id]` | activate; no id lists sandboxes (id/type/description); missing image offers *Build now* / *Cancel* |
| `/sandbox build <id>` | build the image (streams to activity; does not activate) |
| `/sandbox init <podman\|docker> [base]` | write a starter `Containerfile`/`Dockerfile` (bases: `python`→`python:3.12-slim`, `debian`→`debian:stable-slim`, `ubuntu`) |
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
