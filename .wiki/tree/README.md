# pico_chat/ — Root Package

Entry point, config loading, and public API exports.

See [notes/architecture.md](../notes/architecture.md) for the full system overview.

---

## Files

### `main.py`
Launcher. Seeds role files (`roles.ensure_roles_dir()`), builds the `Harness`
via `get_harness()` (which activates the current project's sandbox, if any),
applies the configured theme, and runs `chatTUI`.

### `pico_cfg.py`
`Config` — plain class instantiating the split, user-level config under
`~/.config/pico-chat/` (`ui.toml`, `context.toml`,
`debug.toml`, `styles.toml`, `servers.toml`, `roles/<name>.toml`, disposable
`state.toml`). Loaded at import as the module-level singleton `pico_cfg.config`.
`/reload` mutates it in place. See [notes/config.md](../notes/config.md).

### `__init__.py`
Exports `pico_cfg`, `Harness`, `get_harness`, and `__version__`.

### `worker.py`
Stdlib-only tool bodies shared with the future sandbox worker: `read`, `write`,
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
Per-project settings, stored in the user config (never in the repo) at
`~/.config/pico-chat/projects/<name>.toml` (name = workspace directory name).
- `ensure_project_file()` seeds a commented template; `load_project()` parses
  named `[sandboxes.<id>]` entries + `active`; `set_active()` persists the
  selection without destroying comments; `active_spec()` → `ContainerSpec`.
- Entries: `type` (podman/docker/bubblewrap), `image`, `dockerfile`, `network`,
  `timeout`, `run_args`.

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
  transport failures propagate.

See [notes/security.md](../notes/security.md).

---

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| [harness/](./harness.md) | LLM agent core — loop, tools, endpoints, context |
| [ui/](./ui.md) | Terminal user interface — chat display, input, commands |
