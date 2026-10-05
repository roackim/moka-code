# Default & shipped sandboxes — deferred

**Status:** deferred (captured, not scheduled). Depends on the sandbox registry
redesign: global + local scopes, **one file per sandbox**, `copy`-only
(no inheritance). Nothing here should be built before that lands.
**See also:** `plans/sandbox_registry.md` (the redesign + full handoff).

## Purpose

Once sandboxes are named, per-file artifacts with a **global** library and
**local** (per-project) scopes, moka can **ship one or more ready-to-use
sandboxes** so a fresh install has a working, pickable `bubblewrap` (and,
possibly, container templates) with **no user config at all** — the same way
roles seed a built-in `agent`.

This file captures the substance we want that default to carry: concrete
improvements to the `bubblewrap` invocation, found while working on the
registry redesign.

## Findings that motivate it

Probed the current `bubblewrap` default (bwrap 0.12.0). It is essentially a
**bare OS**:

- **The host environment is inherited wholesale.** `env` inside the sandbox
  shows `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` in the clear; `HOME` points at
  an unbound path (does not exist); `PATH` carries dangling host entries
  (`~/.pixi/bin`, `/envs/apps/bin`); `PYTHONPATH` / `VIRTUAL_ENV` /
  `LD_LIBRARY_PATH` leak in. (The `bash` transport spawns with no `env=`, so
  nothing is filtered.)
- **`/proc` is absent** — bwrap only synthesises a minimal `/dev`.
- **Only `python3` resolves** — no `git`, `node`, `pytest`, `pixi`. The sandbox
  can't run the project, so users fall back to running bare (no sandbox).

## Target default: "bare mode, minus writes outside the workspace"

The goal is a sandbox that feels like running bare (all tools work) but where
only the workspace can change — so it is actually chosen over no sandbox.

- **Breadth** (_decide_): whole-host read-only (`--ro-bind / /`) vs a curated
  set of toolchain roots. Lean: whole-host ro — maximal flexibility, simplest
  mental model — with the network off and the obvious secrets masked.
- **Writable**: workspace rw, plus a writable scratch `HOME` / `TMPDIR`.
  _Decide_: ephemeral tmpfs vs a persistent per-project scratch (builds warm).
- **`/proc`** mounted; rely on bwrap's own `/dev`.
- **Clean minimal env**: set `HOME`/`PATH`/`TMPDIR`, drop secrets. (Also removes
  the API-key leak for free.)
- **`run_args` applied last** so the user always overrides moka (today they are
  inserted early and get stomped).
- **Network off by default** (already the case), trivially enabled.
- **Mask obvious secrets** (`~/.ssh`, `~/.aws`, `~/.config/gh`) when binding `/`
  read-only.
- **Modest namespace hardening**: add `--unshare-ipc/-uts/-cgroup`; keep
  `--unshare-pid`; skip `--unshare-user` for now (uid-mapping surprises).
- **New structured keys** `read_only = [...]` / `writable = [...]` alongside the
  raw `run_args` escape hatch, so common mounts don't need bwrap flags.

## Open questions (for when this is picked up)

- whole-host-read-only vs curated toolchain roots.
- ephemeral vs persistent scratch home/caches.
- shipped-sandbox namespace/prefix; one `bubblewrap` or a small set
  (`bwrap-full`, `bwrap-system`, …).
- ship container (podman/docker) starter sandboxes too?
- role linkage: `require_sandbox = "global/<name>"` to demand a specific
  confined variant.

## Touch points when picked up

- `moka_code/sandbox.py` — `_bubblewrap_argv`, `_STANDARD_PREFIXES`, and the
  process spawn (env handling).
- `test/test_sandbox.py`, `test/test_foreground_handoff.py` — assert the argv.
- `.wiki/notes/sandbox.md`, `.wiki/notes/security.md`.
- Seed paths follow the registry redesign (`~/.config/moka/sandboxes/<name>.toml`).
