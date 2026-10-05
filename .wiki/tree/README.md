# moka_code/ — Root Package

Entry point, config loading, and public API exports.

See [notes/architecture.md](../notes/architecture.md) for the full system overview.

---

## Files

### `main.py`
Launcher. Seeds role files (`roles.ensure_roles_dir()`), builds the `Harness`
via `get_harness()` (which activates the current project's sandbox, if any),
applies the configured theme, and runs `chatTUI`.

### `settings.py`
`Config` — plain class instantiating the split, user-level config under
`~/.config/moka/` (`ui.toml`, `context.toml`,
`debug.toml`, `styles.toml`, `servers.toml`, `roles/<name>.toml`, disposable
`state.toml`). Loaded at import as the module-level singleton `settings.config`.
`/reload` mutates it in place. See [notes/config.md](../notes/config.md).

### `__init__.py`
Exports `settings`, `Harness`, `get_harness`, and `__version__`.

### `worker.py`
Stdlib-only tool bodies shared with the sandbox worker: `read`, `write`,
`edit`, `bash` (+ `bash_sync`), and the folded replace-block parser
(`parse_patch` / `apply_patch` / `PatchBlock` / `PatchParseError`) formerly in
`harness/patch_parser.py`. Imported by `harness/tools.py` as the in-process
mechanism; also runs as a standalone script (`python worker.py`) inside a
container, which is why it imports nothing from the package. `bash` runs in its
own process group and accepts an `on_spawn` hook so the caller can cancel it.

**JSONL protocol** — when run as a script, `serve()` reads one request per line
on stdin and writes one response per line on stdout (stdout is protocol-only;
logs go to stderr), serialized one at a time. Requests are
`{"id", "tool", "args"}`; bash also emits interim
`{"id", "stream": "stdout"|"stderr", "data": …}` frames; responses are
`{"id", "ok", "result"}` or `{"id", "ok": false, "error"}`.
`{"op": "shutdown"}` stops the loop.
  `handle_request()` is the pure request→response step; `main()` calls
  `asyncio.run(serve())`. See [notes/tools-and-permissions.md](../notes/tools-and-permissions.md).

### `projects.py`
The sandbox registry, stored in the user config (never in the repo): one file per
sandbox, global (`~/.config/moka/sandboxes/<name>.toml`) or local
(`projects/<dirname>_<hash>/sandboxes/<name>.toml`); the project's
`project.toml` holds `path` + `active`. Names are unique across both scopes.
- `load_project()` merges both scopes and reports bad files/collisions;
  `validate_sandboxes()`; `active_spec()` → `ContainerSpec`; `set_active()`;
  `create_sandbox()` / `copy_sandbox()`; `sandbox_file_stat()` (reload notice).
- Entries: `type` (podman/docker/bubblewrap), `description`, `image`,
  `dockerfile` (relative to the sandbox file), `network`, `timeout`, `run_args`.

### `sandbox.py`
Host-side sandbox launcher and JSONL client (no `ui/` imports, no policy).
- `ContainerSpec` — runtime + image + `network`/`timeout`/`run_args`/`dockerfile`
  (built from a project entry).
- `build_argv(spec, workspace, worker)` — build the runtime command line
  (`--network=none`, read-only rootfs, caps dropped, workspace + `worker.py`
  mounts; `bwrap` variant for host runs).
- `SandboxProcess` — owns the worker process and the JSONL client: lazy start,
  request/response with id correlation (interim `stream` frames forwarded to
  `on_output`), wall-clock timeout, respawn after a crash, graceful stop, sync
  `kill()` for the stop button.
- `SandboxTransport` — duck-typed `ToolTransport` over a `SandboxProcess`;
  tool-level failures return as result strings (parity with in-process),
  transport failures propagate. `is_sandbox = True` drives the role lock.
- Preflight/build: `runtime_available()`, `image_present()`,
  `build_command()`, `run_build()` (streams output), `containerfile_name()`
  (`Containerfile` for podman / `Dockerfile` for docker) and
  `containerfile_starter()` (a ready-to-edit file; deps as hints; installs
  `python3` for non-`python:*` bases). moka never builds implicitly —
  `/sandbox start` offers, `/sandbox build` is explicit.
- Containers run the worker as `python3 /opt/worker.py` (portable across
  `python:*` and distro bases that install `python3`; bubblewrap uses
  `sys.executable`).

See [notes/security.md](../notes/security.md).

---

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| [harness/](./harness.md) | LLM agent core — loop, tools, endpoints, context |
| [ui/](./ui.md) | Terminal user interface — chat display, input, commands |
