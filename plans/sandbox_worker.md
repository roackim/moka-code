# Sandboxed Worker — Plan

**Status:** proposed (agreed direction, no code yet) · **Owner:** Joackim · **Created:** 2026-09-24
**Supersedes:** `plans/containerization.md` (the "pico ships nothing container-related"
decision) for the execution path. The reasoning there is kept as history; the
"the boundary belongs to the environment / explicit over user magic" principles
carry over.
**Companion docs:** `.wiki/notes/principles.md`, `plans/roles_rework.md`,
`notes/` (streaming, tool output).

Goal: let pico run its **tool execution inside a container it starts**, while the
UI, the agent loop, the conversation, the LLM calls, and the config all stay on
the host. The container side is a tiny, stateless **worker** with no config, no
network, no model, and no policy — just the pair of hands.

---

## 1. Why

Today pico is one process: `ui/` + `harness/` running on the host, tools touching
the host filesystem directly. The only isolation options are the per-tool
`no`/`ask`/`yes` approval (`plans/roles_rework.md`) and running the *whole* pico
inside a user-written sandbox (`plans/containerization.md`). Both are all-or-
nothing:

- Bare pico: the model's `write`/`edit`/`bash` hit the host; the only
  guard is `ask`, and there is **no path confinement** (deliberately — path
  logic is not a security boundary).
- Whole-pico-in-a-container: strong, but the user must install/run pico inside
  the box, and the UI, config, and secrets go in with it.

This plan splits the difference: run the **agent** on the host (trusted) and the
**tool execution** in a container (untrusted side of the model). The container is
the boundary for the model's actions; the host keeps everything the user cares
about (conversation, config, keys, model choice).

We already have the seams for it:

- `harness/` must not import `ui/` (R9 guard, `test/test_core_ui_boundary.py`).
- `harness/events.py` is the single harness→UI union.
- Tools are declared in one registry (`harness/tools.py`).

So this is a **transport and lifecycle change**, not a rewrite.

---

## 2. Principles

1. **The container is the boundary.** No path confinement, no allowlists, no
   command parsing in pico. The mount, the namespace, and `--network=none` are
   the wall. (Continues `plans/roles_rework.md`.)
2. **One mechanism, two places.** The tool bodies live once (`worker.py`) and run
   either in-process (bare) or in the container (sandboxed). No second copy.
3. **Host thinks, worker does.** The worker has no config, no roles, no model,
   no history, no permissions. It executes a named tool with arguments and
   returns a result.
4. **Explicit over implicit.** The user selects the backend with a flag; pico
   never detects or guesses a sandbox. (Continues `plans/containerization.md`.)
5. **Config files are the settings UI.** No new interactive surface; the backend
   is config/flag, the image is the user's Containerfile.
6. **Delete before you design.** This removes the subagent machinery and folds
   `patch_parser.py`. Every step ends with fewer files/layers.
7. **Crash-cheap.** The worker holds no state; if the container dies, the host
   respawns and replays. The host is the source of truth.
8. **Least privilege by construction.** No network, read-only rootfs, dropped
   caps, workspace-only mount, host-user ownership.

---

## 3. Architecture

```
                  HOST (trusted)                          CONTAINER (untrusted side)
┌───────────────────────────────────────────┐   ┌──────────────────────────────────┐
│ ui/     TUI, transcript, activity, pickers │   │                                  │
│ harness/ loop, history, events, permissions│   │   python3 /opt/worker.py          │
│ endpoint LLM calls (secrets, network)      │   │   read | write | edit | bash     │
│ config  servers/roles/ui/... (rw)          │   │   no config · no net · no model  │
│ sandbox launcher + transport               │   │   cwd=/workspace                 │
└───────────────┬───────────────────────────┘   └───────────────▲──────────────────┘
                │  JSON-lines over stdio (persistent, session)   │
                └───────────────────────────────────────────────┘
                     podman run -i --rm -w /workspace …
```

- The host **owns**: conversation, history, LLM requests, config, permissions,
  `state.toml`, and the tool *schemas*.
- The container **owns**: the four tool bodies, the workspace view, and the
  shell's process tree. Nothing else.
- The worker is **stateless**; the host is the durable side.

---

## 4. The worker (`pico_chat/worker.py`)

A single, stdlib-only module that is both:

- the container entrypoint (`python3 /opt/worker.py`), and
- the host's source of tool bodies (`from pico_chat.worker import read, …`).

Stdlib-only is what makes the container need no install (see §7). Host import
runs the heavy `pico_chat/__init__.py` (`Harness`, `httpx`), which is fine; the
container runs the file **as a script**, so the package `__init__` never runs.

### 4.1 Functions (mechanism)

Plain functions taking explicit arguments — no `ToolContext`, no TUI, no config.
Signatures mirror the current `FileTools`/`ShellTool` bodies in
`harness/tools.py` (the shell verb is `bash`, the edit verb takes
`path`/`search`/`replace` — there is no model-supplied patch text):

```python
def read(path, *, offset=0, limit=None, max_chars=None,
         include_line_numbers=False, cwd: Path) -> str: ...
def write(path, content, *, cwd: Path) -> str: ...
def edit(path, search, replace, *, cwd: Path) -> str: ...
async def bash(command, *, cwd: Path, timeout: int = 30,
               on_output: Callable[[str, str], None] | None = None) -> str: ...
```

- These are today's bodies from `harness/tools.py`, moved verbatim where possible
  (including the process-group kill and timeout semantics of `ShellTool` /
  `run_async`).
- `edit(path, search, replace)` assembles the replace block internally and
  applies it through the existing parse/apply cascade.
- `patch_parser.py` is **folded into** `worker.py` and deleted; `worker.edit`
  owns `parse_patch`/`apply_patch` (`PatchParseError`) locally, so `tools.py`
  no longer imports the parser.
- `on_output(stream, data)` is the streaming hook (§6.3): the worker emits a
  frame per chunk; in-process callers pass a UI callback.

### 4.2 JSONL entry (`if __name__ == "__main__"`)

- Read newline-delimited JSON requests on **stdin**; write newline-delimited JSON
  responses on **stdout**; **all logs/banners to stderr**.
- Dispatch is a static dict mapping the four verbs → functions
  (`{"read", "write", "edit", "bash"}`); the worker cannot reach the registry
  standalone, and four stable verbs are not worth reflecting. These names match
  the `@tool` registry and the role files' `enabled_tool_names()`.
- Serialize execution (one request at a time) for v1.
- `asyncio.run(...)` around the loop so `bash` can stream.

---

## 5. Host-side tool layer (`harness/tools.py`)

Split **mechanism** from **schema**:

- Bodies move to `worker.py`.
- `harness/tools.py` keeps the `@tool` registry (names, descriptions, JSON
  schemas) and calls into `worker`.
- Execution goes through a **transport** so the same registry serves both modes:

```python
class ToolTransport(Protocol):
    async def execute(self, name: str, args: dict,
                      on_output=None) -> str: ...
```

- `InProcessTransport` — bare mode: resolves `name` → `worker.<name>` and calls
  it with `cwd` from the active `MinimalToolset`. Identical to today's behavior
  (the `@tool` handlers currently bound to a `MinimalToolset` delegate here).
- `SandboxTransport` — sandbox mode: sends `{"id", "tool", "args"}` and awaits
  the matching response (streaming frames go to `on_output`).
- `Harness`/`create_toolset` take a transport; the `RegisteredTool.execute(**args)`
  call in `harness.py` becomes the transport call (same name → args → result
  shape), so the LLM-facing schemas and role `enabled_tool_names()` are
  unchanged.
- Permissions (`ask`) still gate *before* `execute` is called, host-side.

The registry, schemas, role `enabled_tool_names()`, and prompts are unchanged.

---

## 6. Transport & protocol

### 6.1 Shape

- Persistent process for the session (not per call), spawned by the host.
- Newline-delimited JSON; each request carries an integer `id`, echoed by the
  response.
- **`-i`, never `-t`** — a TTY injects CRLF and corrupts framing.
- stdout is protocol only; stderr is pumped to pico's debug log.

```jsonc
{"id":7,"tool":"bash","args":{"command":"pytest -q"}}
{"id":7,"stream":"stdout","data":"collected 42 items\n"}   // interim (optional)
{"id":7,"ok":true,"result":"...final output..."}
{"id":7,"ok":false,"error":"timeout after 120s"}
```

### 6.2 Errors, timeout, restart

- Host enforces a wall-clock timeout; on expiry it kills the process and
  respawns. The worker may also enforce its own timeout.
- Any container exit mid-call is an error to the caller; because the worker is
  stateless, the host respawns and (at most) replays the single failed call.
- Graceful stop: `{"op":"shutdown"}` then close stdin; `--rm` cleans up.

### 6.3 Streaming tool output (observability)

- `bash` streams stdout/stderr as interim frames.
- Add a `ToolOutput` event to `harness/events.py`; the UI routes it to the
  activity surface, so a long test run is visible live.
- In-process mode uses the exact same `on_output` callback, so bare and sandbox
  behave identically.

---

## 7. Container image & worker injection (Model 1)

**The image contains no pico.** Because `worker.py` is stdlib-only, the
container needs only a Python interpreter plus whatever *the project* needs
(compilers, test runners, …).

- The image must provide `python3` (worker.py is stdlib-only, so the interpreter
  is all it needs). `python:*` bases ship it; `/sandbox init` on
  `debian:stable-slim` / `ubuntu` emits an uncommented `apt-get install python3`
  for that reason.
- The host mounts its own installed `worker.py` read-only and runs it as a
  script:
  ```sh
  PYSRC=$(python -c 'import pico_chat,os;print(os.path.dirname(pico_chat.__file__))')
  podman run -i --rm -w /workspace \
    -v "$PWD:/workspace:Z" \
    -v "$PYSRC/worker.py:/opt/worker.py:ro" \
    $PICO_IMAGE python3 /opt/worker.py
  ```
- Version-locking is free: the container runs the host's exact worker (no skew,
  no rebuild on pico updates, no build-time network).
- If pico *is* installed in the image (`pip install`), `pico --worker` is an
  equivalent entrypoint; Model 1 does not require it.
- `pipx` is a host CLI installer and is not used inside images; use `pip`/`uv`.

**Starter Containerfile.** pico can generate a commented, virgin Containerfile
for a chosen base (friendly names `python` / `debian` / `ubuntu`) with the right
workdir/mount/network notes, which the user edits and builds. Generating is the
user's convenience; pico never builds or ships an image (preserves
`containerization.md`'s "the user builds").

---

## 8. Launcher & lifecycle (host)

`pico_chat/sandbox.py` (host-only; no `ui/` import) owns:

- **Selection:** the `--container` flag (see §9) chooses the backend.
- **argv construction:** one place builds the runtime command.
- **Process ownership:** `asyncio.create_subprocess_exec` with pipes; the JSONL
  client; timeout/restart.
- **Lifecycle:** start **lazily on the first tool call**, reuse for the session,
  respawn on crash, stop on exit. Because the worker is stateless, restart needs
  no recovery protocol.

### Backends reduce to "a stdio pipe"

| `--container` | argv (sketch) |
|---|---|
| `none` (default) | in-process (`InProcessTransport`) |
| `podman[:image]` | `podman run -i --rm -w /workspace -v … python3 /opt/worker.py` |
| `docker[:image]` | same, `docker` |
| `bubblewrap` | `bwrap … -- python3 /opt/worker.py` (host process, no image) |

The launcher only has to produce a pipe; the protocol layer is identical.

---

## 9. Configuration

- Per-project scoping comes from **invocation**, not project-local config (P1 is
  preserved): e.g. a shell alias or `pico --container=podman:my-project`.
- A user-level default may live in a new flat `sandbox.toml`
  (`enabled`, `runtime`, `image`, `network`) surfaced by `/config sandbox`.
  Keys added per `AGENTS.md` (spec + `_apply_defaults` + commented template).
- Open: exact flag grammar (`--container=podman:image` vs separate
  `--sandbox`/`--image`). Keep it explicit and small.

---

## 10. Security model

| Concern | Decision |
|---|---|
| File boundary | The workspace bind mount; all `read`/`write`/`edit` run in-container |
| Path confinement | None in pico (the container is the wall) |
| Network | `--network=none`; LLM calls happen on the host |
| Secrets | Stay on the host; never passed into the container |
| Rootfs | `--read-only`, `--tmpfs /tmp` (edit needs scratch) |
| Capabilities | `--cap-drop=all --security-opt no-new-privileges` |
| Sandbox detection | None; the user names the backend |
| Approval | Per-tool `no`/`ask`/`yes`, decided host-side before dispatch |

### 10.1 File ownership (UID)

- **Rootless podman, default userns:** container `uid 0` maps to the host user,
  so files the worker creates — **new and modified** — land owned by the host
  user. Correct by construction; no `sudo`.
- `--userns=keep-id` is the alternative when the image runs as a non-root user.
- Rootful runtimes (`sudo docker`) land **root-owned** files; the launcher must
  then pass `--user $(id -u):$(id -g)`. Prefer rootless.
- The worker runs `umask 022`; `write`/`edit` **preserve an existing file's
  mode** and never chown.
- On SELinux hosts, the workspace mount needs `:Z` (`getenforce`).

---

## 11. Output elision (host-side)

Before a tool result enters history, if it exceeds a threshold (~10k chars) keep
head + tail and insert `… N chars elided; narrow the command (head/tail/grep/sed)`.
Host-side, so the worker stays dumb. This is the bounded-context backstop for
`find`/`cat`/test spam (the mini-swe observation idea).

---

## 12. Subagents — already removed

Already done in the `patch`→`edit` / `run_command`→`bash` rework: the
`subagent` / `wait_for_subagents` tools, the child-`Harness` plumbing,
`scaffolder_role()`, `subagents.toml`, and their tests/docs are gone.
`roles.py` keeps `_RETIRED_TOOLS = {"subagent", "wait_for_subagents"}` to scrub
old role files. Recorded here so the worker verb set stays exactly four
(`read`/`write`/`edit`/`bash`) and no nested loops enter the worker.

---

## 13. Workstreams

Each ends green on §14 gates.

- **W1 — Worker extraction (no behavior change).** Move `read`/`write`/`edit`/
  `bash` bodies into `pico_chat/worker.py`; fold `patch_parser.py`;
  `harness/tools.py` imports from `worker` and keeps schemas. `tools.py` tests
  stay green.
- **W2 — Transport seam.** Introduce `ToolTransport`; `InProcessTransport`;
  `Harness`/`create_toolset` take a transport. Bare mode unchanged.
- **W3 — Protocol + `__main__`.** JSONL loop in `worker.py` (stdlib, `-i`-safe,
  stdout=protocol); tests for framing, each verb, errors, timeout, shutdown.
- **W4 — Launcher.** `pico_chat/sandbox.py`: `--container` selection, argv
  construction, process ownership, lazy start, restart, stop. Unit-test argv;
  integration-test against a trivial fake runtime that echoes protocol.
- **W5 — SandboxTransport.** Client side of the protocol; map frames; wire into
  `Harness`.
- **W6 — Streaming & observability.** `on_output`, `ToolOutput` event, activity
  surface rendering.
- **W7 — Elision.** Head/tail truncation in the harness result path.
- **W8 — Config + Containerfile generation.** `sandbox.toml` (optional),
  `/config sandbox`, starter Containerfile output.
- **W9 — Docs.** `.wiki/` update; mark `plans/containerization.md` superseded.

---

## 14. Gates

```bash
.pixi/envs/default/bin/python -m pytest test/ -q
.pixi/envs/default/bin/python -m compileall -q pico_chat
.pixi/envs/default/bin/python -m vulture pico_chat --min-confidence 80
.pixi/envs/default/bin/python -m pytest test/test_core_ui_boundary.py -q
.pixi/envs/default/bin/python -m pytest test/test_command_import_graph.py -q
```

---

## 15. Tests

- **Worker functions:** each verb against a tmp workspace; edit parse/apply
  round-trips; `bash` timeout + process-group kill; mode preserved on
  overwrite; new files owned per userns assumption (unit-level).
- **Protocol:** request/response correlation; ordering; stderr separated from
  stdout; malformed line handling; shutdown; **no TTY CRLF** (feed `\r\n` input).
- **Transport:** `InProcessTransport` == `SandboxTransport` over a fake worker
  (same results); streaming frames reach `on_output`.
- **Launcher:** argv for podman/docker/bwrap/none; lazy start (no container until
  first tool call); respawn on crash; stop on exit. No real container in CI.
- **Elision:** boundary cases (under/over threshold, binary-ish output).
- **Guards:** R9 and command-import-graph still pass; `harness/` still imports no
  `ui/`.

---

## 16. Risks / fallbacks

- **Framing corruption** (TTY, stray stdout). Mitigation: no `-t`; worker logs to
  stderr; a strict reader that errors on non-JSON.
- **Container start latency.** Mitigation: lazy start once per session, persistent
  worker.
- **Streaming protocol complexity.** Fallback: ship request/response first
  (W1–W5), add streaming in W6; a non-streaming worker is still correct.
- **UID/ownership surprises across runtimes.** Mitigation: document the rootless
  contract; `--user` fallback for rootful.
- **Image unavailable / wrong python.** Mitigation: clear error, generated
  starter Containerfile, `--container=none` always available.
- **Secret leakage.** Mitigation: LLM on host only; never pass keys; `--network=none`.

---

## 17. Open questions

1. Flag grammar: `--container=podman:image` vs `--sandbox=podman --image=…`.
2. `sandbox.toml` now, or flag-only until the flag proves annoying?
3. Where the starter Containerfile is written (`./Containerfile.pico`?) and from
   what surface (flag vs command).
4. `bubblewrap` details: which host paths to bind (`python`, `worker.py`,
   workspace, `/tmp`), no image.
5. Whether `write`/`edit` preserve mtime (probably not) and exact umask rules.
6. Should `read` be allowed outside the workspace? (Container-wise yes; product-
   wise probably workspace-only via the sysprompt, not enforced.)

---

## 18. Deletions (delete before you design)

- `pico_chat/harness/patch_parser.py` (folded into `worker.py`; `worker.edit`
  owns the parse/apply cascade).
- Tool bodies in `harness/tools.py` (moved to `worker.py`).
- Subagent machinery — already deleted (§12).
- The deferred built-in mini-editor (drop from the backlog).

---

## 19. Sequencing

1. **W1–W2** — extract the worker and land the transport seam; bare mode
   unchanged (pure refactor, independently valuable).
2. **W3–W4** — protocol + launcher; prove it talks to a fake runtime.
3. **W5** — wire the sandbox transport into the harness.
4. **W6–W7** — streaming/observability and elision.
5. **W8–W9** — config, Containerfile generation, docs; mark
   `containerization.md` superseded.
