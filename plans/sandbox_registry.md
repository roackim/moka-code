# Sandbox registry redesign — plan / handoff

**Purpose:** resume this work cold. Everything decided, everything still open,
and exactly where it lives in the tree.

**Status at handoff:**
- ✅ **Done (uncommitted):** `replay_reasoning_depth` default `1 → 999`.
- ✅ **Implemented (uncommitted, suite green):** the sandbox registry (global /
  local scopes, one file per sandbox, `copy`-only), `new`/`copy`/`config [name]`,
  collision error, drift notice + `/reload`, moved-repo notice, stale-image notice,
  seeded default `bubblewrap`, bare `/sandbox` opens the picker.
- ⏸️ **Deferred:** shipping default sandboxes + improved `bubblewrap`
  invocation → `plans/sandbox_defaults.md`.

**How to run things (on the host, not in the agent sandbox):** `pixi run tests`
(or `pixi run pytest`). The agent runs **inside a bubblewrap sandbox** that has
no `pixi`/`pytest`, so it cannot run the suite itself — see §E.

---

## A. Completed: `replay_reasoning_depth` defaults to 999

**Decision:** keep a plain integer `999` as the "all turns" value. No `"all"`
string, no shared constant — `999` is already the convention (`min_replay_depth`)
and the depth is capped at what exists, so it means "all". The user confirmed
this and that tests pass.

**Edits (all uncommitted):**

`moka_code/harness/roles.py`
- `Role.replay_reasoning_depth: int = 999` (was `1`); comment updated to name it
  the "all turns" convention.
- `_role_from_dict`: `data.get("replay_reasoning_depth", 999)` (was `1`).
- `_role_template`: help now `…, 999 = all (the default)`; commented line
  `# replay_reasoning_depth = 999`.

`test/test_roles.py`, `test/test_config_commands.py`
- Default assertions `1 → 999`.
- **Note:** a few tests *edited* a file to `999`, which now equals the default
  (so they'd pass without proving reload). Those now edit to `3` and assert `3`.

Docs: `.wiki/notes/reasoning-traces.md`, `.wiki/notes/providers.md`,
`.wiki/tree/harness.md`, `PLAN.md` (`default 1` → `default 999 = all`).

**Rationale recap:** 999 stops the DeepSeek "needs all turns" warning from firing
on default roles (the bug logged in `todo.todo`). It's a config default, not a
real limit.

**Verification:** the user ran the full suite on the host — green. The agent
verified `Role()`/`agent_role()`/`chat_role()`/template/`_role_from_dict`
standalone (no deps) — all correct.

---

## B. The sandbox registry redesign (main work)

### B1. The needs (from the user)

1. Create & configure **global** sandboxes.
2. **Use** a global from any project.
3. **Copy** a sandbox into an editable duplicate — into a project **or** as a new
   global.
4. Create & configure a **project-local** sandbox from scratch (no parent).

### B2. Current state (as-is)

- Sandboxes are **per project**, stored in **one file per project**:
  `~/.config/moka/projects/<name>.toml` where `<name>` = the workspace
  directory name. The file holds a `path = "…"` line, an optional
  `active = "…"` line, and inline `[sandboxes.<id>]` tables + a long commented
  template.
- **Code:** `moka_code/projects.py`
  - `PROJECTS_DIRNAME = "projects"`, `SANDBOX_TYPES = ("podman","docker","bubblewrap")`
  - `DEFAULT_PROJECT_TOML` (the commented template, includes bubblewrap hints)
  - `SandboxEntry` (id/type/description/image/dockerfile/network/timeout/run_args)
  - `ProjectConfig` (name/path/active/sandboxes)
  - `get_projects_dir()`, `project_name()` (dir name), `project_path()`,
    `ensure_project_file()`, `_parse_entry()`, `load_project()`, `active_spec()`,
    `set_active()` (rewrites the `active` line preserving comments)
- **Spec type:** `ContainerSpec` in `moka_code/sandbox.py`
  (runtime/image/network/timeout/run_args/dockerfile); `build_argv()`,
  `SandboxProcess`, `SandboxTransport`.
- **Commands:** `moka_code/ui/commands/sandbox.py` — `/sandbox`,
  `/sandbox config`, `start [id]`, `build <id>`, `init <podman|docker> [base]`
  (bubblewrap is rejected by `init`), `stop`, `terminal`.
- **Docs:** `.wiki/notes/sandbox.md`, `security.md`, `config.md`,
  `.wiki/tree/README.md`; plans `sandbox_worker.md`, `containerization.md`.

### B3. The model (all decisions **confirmed** by the user)

- **Copy-only.** No inheritance, no `extends`, no deltas, no merge rules.
  Divergence = `copy` then edit.
- **Verb is `copy`.**
- **One file per sandbox.**
- **Two scopes:** `global` (shared library) and `local` (one project).
- **Names unique across both scopes** (global + local). No shadowing, no
  same-name exception. References are therefore **bare names** (`active = "<name>"`);
  scope is only a display tag (`global` / `local`) in the picker and status line.
- **Collision = error.** If a name exists in both scopes (e.g. a global is added
  that matches a local), show an **error banner** in the notice band naming both
  files. Both entries are **unusable** until one is renamed; if `active` points at
  that name, nothing is active. Unrelated sandboxes are unaffected.
- **Locals live under `~/.config`, not in the workspace — deliberately.** The
  agent can write the workspace, so a workspace-resident sandbox config would let
  it loosen its own sandbox. Cost (accepted): locals can't be versioned/shared
  with the repo.

### B4. File layout

```
~/.config/moka/
├── sandboxes/
│   └── <name>.toml                      # GLOBAL sandboxes
└── projects/
    └── <name>_<hash>/
        ├── project.toml                 # project meta: path + active
        └── sandboxes/
            └── <name>.toml              # LOCAL sandboxes (for this project)
```

- `<name>_<hash>`: `<name>` = workspace directory name, `<hash>` = **first 4 hex
  chars of a hash of the `realpath` of the workspace**. Fixes same-name
  collisions between projects; `<name>` is only for readability.
- `project.toml` holds `path = "<resolved>"` and `active = "<name>"`.
- **Moves/renames:** a moved repo gets a new hash, orphaning its locals and
  `active`. At startup, if a project dir's `path` no longer exists, raise a notice
  (ideally with a hint to re-link). No auto-migration.
- **`active` is per-project** (a single global state file cannot hold it).

### B5. Naming rules

- Allowed: `[A-Za-z0-9_-]`. **No spaces**, no leading `.`, non-empty.
- Reserve `_`- and `.`-prefixed names as hidden/meta (mirrors the roles rule:
  files whose stem starts with `_` or `.` are ignored).
- Reuse/adapt `roles._validate_name` but tighten to the charset above.

### B6. Commands / UX (proposed)

- `/sandbox` — **picker** of all sandboxes (globals + this project's locals),
  each **tagged `global`/`local`**, active one marked.
- `/sandbox config [name]` — open that **one** file; no arg → picker first.
- `/sandbox copy <src> <global|local> <new-name>` — write a new file in the
  target scope; name must be unique across **both** scopes. (Scope is a plain
  argument, not a `--global` flag.)
- `/sandbox new <global|local> <type>` — create from scratch, `type ∈
  {bubblewrap, podman, docker}`; type-specific starter content (bare bwrap file
  vs commented container template, like roles).
- `start` / `stop` / `build` / `init` / `terminal` — resolve by bare name.
- **Display order:** scope **before** description, in the picker **and** the
  status line: `global · <name> — <description>` /
  `local · <name> — <description>`.

### B7. Migration — **remove the old files** (no migration code)

- The old files are the flat per-project files: **`~/.config/moka/projects/<name>.toml`**
  (one per project, named after the workspace directory). They hold `path`,
  `active`, and inline `[sandboxes.<id>]`.
- Decision: **do not migrate.** Ditch the flat format; delete those files on the
  host (`ls ~/.config/moka/projects/`). The new layout is the one in §B4.
- `projects.py`'s inline-sandbox handling (`_parse_entry`, the `[sandboxes.*]`
  loop, `DEFAULT_PROJECT_TOML`'s sandbox section) is replaced by the per-file
  model.

### B8. Reload / rebuild semantics (confirmed)

Two different changes, two different notices:

- **Sandbox file edited** (`run_args`/`image`/`network`/…) → mtime check (mirror
  `roles.role_file_stat`) → notice **"<name> changed on disk → /reload"**.
  `/reload` re-resolves the spec; if a worker is live, defer to idle.
- **Containerfile edited after the image was built** → `/reload` does **not**
  help (moka never builds implicitly) → distinct notice
  **"image stale → /sandbox build"**. So monitor the Containerfile's mtime too,
  as a *rebuild* hint.
- A running container keeps its old config until stop/start regardless.

### B9. Delete / rename

- Deleting or renaming the **active** sandbox → it is simply **no longer
  active** (resolved to none), with a notice. (User: "No longer active?")

### B10. Validation (proposed, user was "unsure")

- Mirror roles: a `validate_sandboxes()` returning
  `sandboxes/<name>.toml: …` (unknown fields, bad name, bad type), surfaced by
  `/reload` and the picker. Low effort; assume we do it.

### B11. Deferred / open

- **Deferred:** shipping default sandboxes + improved `bubblewrap` defaults →
  `plans/sandbox_defaults.md`.
- **Deferred:** role → named sandbox linkage (`require_sandbox = "<name>"`)
  — "maybe later".
- **Decided — Containerfile:** `dockerfile = "x"` resolves **relative to the
  directory of the sandbox file** (`~/.config/moka/sandboxes/` for globals, the
  project dir's `sandboxes/` for locals). Non-`.toml` files there are ignored by
  the registry, so `<name>.Containerfile` can sit beside `<name>.toml`.
- **Decided — build context:** the **directory containing the Containerfile**
  (default `podman/docker build` behaviour), **not the workspace**. The workspace
  is bind-mounted at runtime, not baked in, and a global's image is shared across
  projects so its context must not vary per project. No `context` key unless a
  real use case appears. **Behaviour change:** today `build_argv`
  (`moka_code/sandbox.py` ~l.242-248) resolves `dockerfile` against the workspace
  and passes the workspace as context.
- **Decided — project identity:** `<name>_<4hex hash of realpath>` (§B4); path
  mismatch → notice.
- **Open:** whether shipped/global entries share one namespace with user names
  (now moot-ish: names are unique across scopes anyway — a shipped name just
  reserves it).

---

## C. Bubblewrap default config (deferred — see `plans/sandbox_defaults.md`)

Captured separately. Summary of what it will change in `moka_code/sandbox.py`
`_bubblewrap_argv`:

- Default target: **"bare mode minus writes outside the workspace"**
  (whole-host read-only vs curated roots — decide), writable scratch `HOME`, a
  clean minimal env (kills the API-key leak and the broken `HOME`/`PATH`),
  mount `/proc`, `run_args` applied **last**, network off, mask obvious secrets,
  modest hardening (add `--unshare-ipc/-uts/-cgroup`, keep pid, skip user-ns).
- New structured keys `read_only` / `writable` + raw `run_args` escape hatch.

### Live evidence (the agent's own sandbox — it *is* moka's default bwrap)

While working, the agent ran inside a bubblewrap sandbox (probed read-only):

- Mounts: `/` tmpfs rw; `/usr /lib /lib64 /bin /etc` **ro** (ext4, **no
  `/sbin`**); `/opt/worker.py` ro (nfs4); `/workspace` rw (nfs4); `/tmp` tmpfs;
  `/proc` present; `/dev` minimal. **No `/home`** → `HOME` unbound.
- Namespaces: `mnt/net/pid/user` unshared; `ipc/uts/cgroup` **shared**.
- Env inherited → broken `HOME`, dangling `PATH` (`~/.pixi/bin`,
  `/envs/apps/bin`), and **2 secret vars** present (API keys readable via `env`).
- Only `python3` resolves; **no `git`/`node`/`pytest`/`pixi`** → bare OS.

This confirms every improvement in `plans/sandbox_defaults.md` maps to a real
gap, and shows the current hardcoded argv omits `--proc` (the runner added it via
a `--proc /proc` `run_arg`).

---

## D. Touch points / file map when implementing

| Area | Files |
|---|---|
| Registry read/write | `moka_code/projects.py` (rework), new helpers for name → (scope, path) |
| Spec + argv | `moka_code/sandbox.py` (`ContainerSpec`, `build_argv`, `_bubblewrap_argv`) |
| Commands/UI | `moka_code/ui/commands/sandbox.py`, `moka_code/ui/commands/core.py` (`cmd_config`), `status_presenter.py` |
| Harness wiring | `moka_code/harness/harness.py` (`_build_transport`, `set_sandbox`, `sandbox_required`) |
| Docs | `.wiki/notes/sandbox.md`, `security.md`, `config.md`, `.wiki/tree/README.md` |
| Tests | `test/test_sandbox_command.py`, `test_sandbox.py`, `test_argument_completion.py`, `test_foreground_handoff.py`, `test_status_sandbox.py`, `test_config_commands.py` |
| Tests for this design | new: registry (scopes, uniqueness, copy, active, validation) |

---

## E. Environment caveat (for whoever resumes here)

The agent runs **inside moka's bubblewrap sandbox**: no `pixi`, no `pytest`, no
`toml`/`httpx`, network blocked, `HOME` missing. It can edit files, read the
repo, and reason — but **cannot run the test suite**. Run tests on the host with
`pixi run tests`. (`python3` + `bwrap` 0.12.0 + `docker` are present in the
sandbox; `pixi` is not.)

---

## F. Open questions still to answer

1. ~~Containerfile location~~ — decided (§B11).
2. **Validation surfacing** details (§B10) — assume the roles-style approach.
3. ~~Project identity~~ — decided (§B4); re-link UX for moved repos still open.
4. **Namespace for shipped globals** — reserved prefix vs shared with users.
5. ~~Command spelling~~ — decided (§B6): `new`/`copy`/`config`, scope as an argument.

---

## G. Suggested order of implementation

1. New registry layer in `projects.py`: name → path (scope derived from location), list, load, save,
   copy, new, delete, validate; resolve `active = "<name>"`; detect cross-scope collisions (error banner).
2. Project meta file (`projects/<name>_<hash>/project.toml`) read/write for `path` +
   `active`.
3. Rework `/sandbox` commands: picker (tagged scope), `config`, `copy`, `new`;
   resolve by bare name in `start`/`build`/`init`/`stop`/`terminal`.
4. Rebuild/notice semantics (§B8) + `validate_sandboxes()` surfaced by `/reload`.
5. Update docs + tests; delete old flat-file handling.
6. Only then: `plans/sandbox_defaults.md` (default `bubblewrap`, new keys).

---

## H. Repo state at handoff

- Branch: dirty working tree, **uncommitted**:
  - Modified (the 999 change): `moka_code/harness/roles.py`,
    `test/test_roles.py`, `test/test_config_commands.py`,
    `.wiki/notes/reasoning-traces.md`, `.wiki/notes/providers.md`,
    `.wiki/tree/harness.md`, `PLAN.md`.
  - Added: `plans/sandbox_defaults.md`, this file.
  - Untracked, **not ours**: `image.png` (pre-existing), `sandbox.json`
    (harness session dump of this conversation — leave it).
- Suite: green on the host (`pixi run pytest`).
