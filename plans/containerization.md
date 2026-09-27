> **Superseded (2026-09-24).** The "moka ships nothing container-related"
> decision below is reversed for the **execution path**: moka will start a
> container itself and run only the tool **worker** inside it, keeping the UI,
> agent loop, conversation, config, secrets, and LLM calls on the host. See
> [`plans/sandbox_worker.md`](./sandbox_worker.md).
>
> The reasoning here is kept as the historical record. The parts that carry
> over: **the boundary belongs to the environment**, **explicit over implicit**
> (the user names the backend; no detection), the per-tool `no`/`ask`/`yes`
> model, and **the user builds the image** (moka does not ship one).

# Containerization — Decision Record

**Status:** superseded by `plans/sandbox_worker.md` (2026-09-24) · **Owner:** Joackim · **Created:** 2026-09-22
**Companion docs:** `.wiki/notes/principles.md`, `plans/sandbox_worker.md`
(successor), `plans/roles_rework.md`,
`plans/proper_container_integration.md` (older sketch — historical).

This is not an implementation plan. It records the design exploration, the
decisions, and the reasoning, so the work is not re-litigated. The outcome is a
deliberate **non-feature**.

---

## The decision

**moka ships nothing container-related.** No image, no Dockerfile, no wrapper
script, no launcher flag, no detection. moka is a plain program that runs
wherever the user puts it. If the user wants isolation, *they* write a
`podman run …` script, choose the image, and mount the workspace. moka neither
knows nor cares whether it is sandboxed.

The one place this shows up in moka is the **tool approval model**
(`plans/roles_rework.md`): a tool is `no` / `ask` / `yes`. Run inside a sandbox
→ set `yes`. Run bare → set `ask`. The user makes the call explicitly; nothing
is inferred.

---

## Philosophy

- **Delete before you design.** The container subsystem (runtime, lifecycle,
  build, confinement, detection) was fully designed, then deleted once we saw it
  was the wrong layer.
- **Explicit over implicit.** No heuristic sandbox detection, no TOFU, no
  auto-build. The user's script is the explicit statement of how moka runs.
- **Config files are the settings UI / no project-local config.** P1 is
  *preserved*: there is no `.moka.toml`, no project container config. The
  sandbox invocation lives outside moka, in the user's own script.
- **The boundary belongs to the environment.** Containers, VMs, bubblewrap,
  `sandbox-exec` — the user picks the tool. moka should not reimplement any of
  them.
- **The agent runs in a container; that is the expected mode, but it is the
  user's responsibility.**

---

## What moka does and doesn't provide

| moka provides | moka does **not** provide |
|---|---|
| A normal CLI/TUI that runs anywhere | An image or Dockerfile for itself |
| `MOKA_CONFIG_DIR` honored from the environment | A `--container` launcher |
| Per-tool `no`/`ask`/`yes` approval | Runtime detection (podman/docker) |
| The approval prompt | Image building, cache, lifecycle, recreate |
| — | `.moka.toml` or any project-local config |
| — | Workspace path confinement — the container mount is the boundary |

---

## Resulting safety model

Containers are optional, so there is a sandboxed mode and a bare mode, and the
user declares which via tool config:

| Situation | Boundary | Tool settings |
|---|---|---|
| moka run inside the user's container | the container | `yes` (auto-approve; the mount is the wall) |
| moka run bare on the host | none | `ask` (confirm `write` / `edit` / `bash`) |

This is the whole safety story. No profiles, no command allowlists, no
inside/outside repo logic, no chain policy — see `plans/roles_rework.md`.

---

## Design space explored

### 1. Scope of the sandbox — chose (C)

- **(A) Harness on host, sandbox only `bash`.** Simplest to bolt on, but
  the file boundary becomes *our* code: `write`/`edit` run on the host and are
  guarded only by Python path logic (`_validate_path` at `tools.py:67`). A
  symlink/TOCTOU bug is a host escape. Also drags in a runtime, a lifecycle, and
  confinement work.
- **(B) All tool operations executed inside the container.** Closes (A)'s file
  soft spot (absolute paths resolve to the container's filesystem), but needs a
  read/write/edit RPC/helper into the container — more machinery for the same
  files (the workspace is a bind mount either way).
- **(C) The user sandboxes moka itself. (Chosen.)** The container isolates the
  harness, files, and commands at once. moka implements nothing. The
  real boundary is the mount, not our code.

Note the network trade-off: under (C) the LLM traffic originates inside the
sandbox, so the container needs egress to the model endpoint, which also gives
the agent's shell a network. Under (A) an offline shell was possible but the
files were not isolated. (C) was chosen deliberately: full containment of the
agent, with egress being the user's script's concern.

### 2. Who builds the image — chose "the user"

- **moka builds from the project Dockerfile, gated by TOFU/hash-pinning.**
  Rejected: it executes repo-authored `RUN` on the host, unsandboxed, with the
  network and build context; approval prompts decay into rubber stamps;
  hash-pinning is theater if you always approve and doesn't make the build
  reproducible.
- **moka builds from a recipe in user config.** Rejected: the env must be
  maintained in two places and drifts from `requirements.txt`/`package.json`.
- **The user builds, outside moka. (Chosen.)** `podman build -t <image> …`.
  moka never builds, so there is no moka-triggered unsandboxed execution. The
  agent may still *edit* the project Dockerfile — that is normal agent work —
  but nothing moka does runs it; only the user's explicit build does. This is
  what dissolves the cross-session escalation problem (agent writes recipe →
  host compiles it): the human build is the gate.

### 3. How the image is identified — chose "nothing in moka"

- `.moka.toml` in the project with `image`/`containerfile`. **Dropped.**
- `[projects."/path"]` tables in a user-level file. **Dropped.**
- Convention (`moka/<project>`). **Dropped.**

The image is named in the *user's run script*. This keeps the project free of
moka config and preserves P1.

### 4. Where run-time arguments live — chose "nowhere in moka"

`mounts`, `network`, `cpus`, `memory`, GPU, `--shm-size` are all expressed
natively in the user's `podman run` script. moka models none of them, so it never
has to track each runtime's flag differences (`docker --gpus all` vs
`podman --device nvidia.com/gpu=all`, etc.).

### 5. Lifecycle — chose "the user's `podman run --rm`"

No lazy start, no recreate-on-image-change, no orphan cleanup. `--rm` plus the
user restarting the container is the whole lifecycle. This removed an entire
class of state-management code.

### 6. A built-in launcher (`moka --container`, `--image`, `--no-network`, …) — rejected

Considered and designed, then dropped:

- The launcher's only real merit over a script is discoverability and central
  correctness. But moka's container contract is tiny (mount `$PWD`, mount the
  config dir, `-it`, `--rm`, a self-set marker), so a 10-line script is as
  correct and far more transparent.
- Per-project compute needs (CPU/RAM/GPU/`shm`) are *easier* in a script than
  through moka's flags.
- Shipping an image (the actual hard part) would be required either way, and we
  decided moka ships no image.
- Principle 5: no new UI surfaces without need.

Revisit only if the script ergonomics actually bite.

### 7. Heuristic sandbox detection — rejected

An early idea was to detect containers (`/.dockerenv`, `/run/.containerenv`,
cgroups, `/proc/self/uid_map`, `systemd-detect-virt`) and auto-pick the approval
default. Rejected: the heuristics can't see `sandbox-exec`/bubblewrap/chroots,
can't tell a restrictive container from `--privileged`, and turning a heuristic
into a security posture is exactly the implicit magic we avoid. Replaced by the
explicit per-tool `no`/`ask`/`yes`.

### 8. Command allow/deny lists and chain policy — deleted

Earlier we kept `bash` allow/ask/deny patterns (glob/regex). Deleted:
chained-command splitting cannot be done reliably (command substitution,
`bash -c`, `xargs`, interpreters, obfuscation), so allowlists are a security
illusion and deny-lists fail open to obfuscation. The container is the boundary;
`bash` is just `no`/`ask`/`yes`.

---

## Why the trust problem disappears

Every earlier approach needed a trust mechanism because moka *did something*
with repo content (build it, or read project config that could widen the
sandbox). Under the final decision moka does neither: it runs no build, reads no
project config, and executes only the user's own launch script. A cloned repo
cannot authorize itself to moka. There is nothing to trust because moka takes no
action on the repo's behalf.

---

## Recommended posture (guidance for the user's script, not shipped)

```sh
#!/bin/sh
exec "${MOKA_RUNTIME:-podman}" run --rm -it \
  -v "$PWD:/workspace" -w /workspace \
  -v "${MOKA_CONFIG_DIR:-$HOME/.config/moka}:/root/.config/moka:ro" \
  --read-only --tmpfs /tmp \
  --cap-drop=all --security-opt no-new-privileges \
  ${MOKA_NETWORK:---network=none} \
  "$MOKA_IMAGE" moka "$@"
```

Rootless, read-only rootfs, no caps, no new privileges, workspace-only mount,
never the runtime socket, never `--privileged`. Add `--cpus`/`--memory`/`--gpus`
as the project needs. Note the agent's shell shares the container's network, so
`--network=none` is only viable with a locally reachable endpoint.

---

## Research notes: how the incumbents do it

Fetched at decision time, for contrast (see `.wiki/notes/principles.md`).

- **opencode:** safety is **permission rules** (`allow`/`ask`/`deny`, glob
  patterns, last-match-wins, per-agent overrides). **No container/OS sandbox.**
  Config is layered (remote < global < `OPENCODE_CONFIG` < project
  `opencode.json` < `.opencode` < inline < managed); project config is committed
  and merges. `external_directory` guards paths outside cwd; `.env` denied for
  reads by default.
- **Claude Code:** safety is **permission rules plus an OS-level Bash sandbox**
  (macOS Seatbelt, Linux/WSL2 bubblewrap + seccomp), not a container. Config
  precedence: managed > CLI `--settings` > `.claude/settings.local.json` >
  committed `.claude/settings.json` > user; lists merge. Crucially, **a repo
  cannot widen its own box**: `bypassPermissions` and
  `sandbox.filesystem.disabled` are refused from project scope, and project
  `allow` rules wait for per-teammate workspace trust. Dev containers exist as a
  separate opt-in environment.

Takeaways that shaped us: layered config with **project-scope safety
restrictions** is the state of the art, and `deny`/`ask` applying immediately
while `allow` waits for trust is a good split. But **neither incumbent runs the
agent in a container by default** — moka's bet (container-as-user-responsibility,
zero container code) is more hands-off than both, and that is intentional.

---

## History

- `plans/proper_container_integration.md` — July sketch (Podman lifecycle, an
  in-container harness). Superseded; kept for reference.
- An earlier version of this file was an implementation plan for an internal
  container runtime (config, `ContainerRuntime`, `ShellTool` rewiring,
  confinement). Deleted in favor of this decision record.

## Open / future

- Revisit a launcher only if the user-script ergonomics prove painful.
- Nothing else. By construction, there is no container backlog.
