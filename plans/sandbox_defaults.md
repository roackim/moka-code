# Default & shipped sandboxes

**Status:** the `bubblewrap` argv is **done (uncommitted)**. What remains —
shipping it as a seeded, pickable sandbox — is deferred and depends on the
sandbox registry redesign (one file per sandbox, global + local scopes,
`copy`-only, names unique across scopes).
**See also:** `plans/sandbox_registry.md`.

## Role of bubblewrap (decided)

- **Linux hosts only.** No Windows/macOS support is planned.
- **Guard against accidents** (stray writes outside the workspace, leaked
  secrets) for **simple agentic usage** — bash, `python3`, coreutils. It is not
  aimed at containing hostile code.
- **It does not grow.** When a project needs tooling that is hard to set up under
  bwrap (`git`/`node`/`pixi` under `$HOME`, specific versions, system packages),
  the answer is **podman/docker**, not more bwrap mounts. That is the moment a
  project "outgrows" bwrap.
- `run_args` is the only escape hatch (e.g. `--ro-bind ~/.pixi ~/.pixi`). No
  `read_only` / `writable` keys, no variant sandboxes.

## What the default does (landed)

`moka_code/sandbox.py::_bubblewrap_argv`:

- **Curated read-only roots:** `/usr /lib /lib64 /bin /etc` (those that exist),
  plus the interpreter prefix if it lives outside them. No `$HOME`, so
  `~/.ssh`, `~/.config/moka`, other repos are unreadable — no secret masking needed.
- **Writable:** `/workspace` (cwd) and `/tmp` (tmpfs). `HOME` and `TMPDIR` are
  `/tmp`, so the scratch home is **ephemeral**.
- **`/proc` and `/dev`** mounted.
- **Clean env:** `--clearenv`; only `HOME`, `TMPDIR`, a fixed `PATH`, and
  `TERM`/`LANG`/`LC_ALL` (if set) pass. Host API keys and venv paths no longer leak.
- **Network off** unless `network = true`.
- **Namespaces:** `--unshare-pid/-ipc/-uts/-cgroup-try` (+ `-net`); user-ns is not
  unshared (uid-mapping surprises).
- **`run_args` apply last** so the user overrides every default.

## Decided and dropped

- Whole-host read-only (`--ro-bind / /`) and secret masking — reads stay
  restricted; costless for simple usage.
- Persistent scratch home — can be added later via `run_args`/`writable` only if
  a real need appears.
- `read_only` / `writable` keys, `bwrap-full` / `bwrap-system` variants.

## Landed: the seeded default

`main()` → `projects.seed_default_sandbox()` writes `~/.config/moka/sandboxes/bubblewrap.toml`
(a commented starter) on first run, i.e. when the global `sandboxes/` folder does not
exist; deleting it later stays deleted. Skipped if the name is taken for the current project.

## Remaining

- ~~Make "outgrown bwrap → use a container" discoverable~~ — done as docs only
  (the starter file and `.wiki/notes/sandbox.md`); no failure-message heuristics.
- Container starter files are **not** shipped: `/sandbox new <scope> podman|docker`
  is one command, and seeding both would clutter the picker where they are not installed.
- Role linkage `require_sandbox = "<name>"` — deferred, no concrete need yet.
- Namespace/prefix for the shipped name — mostly moot (names are unique across scopes).

## Touch points

- `moka_code/sandbox.py`, `test/test_sandbox.py`, `test/test_foreground_handoff.py`
- `.wiki/notes/sandbox.md`, `.wiki/notes/security.md`
