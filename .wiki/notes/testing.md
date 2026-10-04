# Testing

Tests live in `test/`. Run with pytest from the project root.

---

## Running Tests

```bash
.pixi/envs/default/bin/python -m pytest test/ -q          # full suite
.pixi/envs/default/bin/python -m compileall -q moka_code
.pixi/envs/default/bin/python -m vulture moka_code --min-confidence 80
.pixi/envs/default/bin/python -m pytest test/test_core_ui_boundary.py -q      # R9 guard
.pixi/envs/default/bin/python -m pytest test/test_command_import_graph.py -q  # command graph
```

## Shared Fixtures

`test/conftest.py` points `MOKA_CONFIG_DIR` and the XDG cache/state dirs at a
temporary folder *before* moka is imported, so the suite never reads or writes
your own config, sessions or image cache. Tests wait for a subprocess with
`conftest.wait_until(...)`, never a guessed `sleep` (a loaded machine starts
processes late).

`test/conftest.py` provides reusable test infrastructure:
- `NoopDebugStream`, `FakeServer`, `StubReadTool`, `StubAgent` — stub classes
- `harness_stub(tmp_path, stub_read_tool)` — fixture for tool-execution tests (bypasses `Harness.__init__`)
- `harness_stub_compaction()` — fixture with `FakeServer` for compaction tests
- `run_harness_tool_call(harness, tool_call)` — runs a tool call through `_execute_tool_calls`
- `make_chunk_stream(*chunks)` — async generator yielding given chunks

## Test Coverage

| File | What It Tests |
|------|--------------|
| `test_permissions.py` | Gate decisions, prompt text, ask/deny/allow harness flow |
| `test_roles.py` | Role model, files, seeding, validation, `require_sandbox` |
| `test_buffer.py` | Buffer/SubBuffer rendering (cell operations, ANSI clipping, text writing) |
| `test_compaction.py` | Conversation history compaction (summarization via LLM) |
| `test_context_builder.py` | Git repo detection, file tree building guardrails |
| `test_worker.py` | `worker.py` tool bodies (read/write/edit/bash, timeout, truncation) |
| `test_worker_protocol.py` | JSONL framing: dispatch, errors, streaming frames, CRLF, shutdown |
| `test_transport.py` | `InProcessTransport` seam, streaming, harness `ToolOutput`, role lock |
| `test_sandbox.py` | `sandbox.py`: argv (podman/docker/bwrap), JSONL client, timeout/respawn, stderr, preflight/build, `SandboxTransport` |
| `test_projects.py` | `projects.py`: template/parse/validate, `active` persistence |
| `test_sandbox_command.py` | `/sandbox` command tree: dispatch, selectors, build, init, quit |
| `test_elision.py` | Head/tail truncation boundary cases |
| `test_patch_parser.py` | `parse_patch` / `apply_patch` 3-mode cascade (now imported from `worker.py`) |
| `test_ui_permission_submit.py` | Input blocked while awaiting permission prompt |

The sandbox tests need **no real container**: they run the actual `worker.py` as
a local subprocess (the "trivial runtime" that speaks the protocol).

## Notes on `test_compaction.py`

Requires a running LLM server (or the `FakeServer` fixture from `conftest.py`). May be skipped in CI without a backend.

## Adding Tests

- Place new test files in `test/`
- Mirror the module being tested: `harness/permissions.py` → `test/test_permissions.py` (use existing naming convention)
- Use pytest fixtures from `conftest.py` where possible; avoid global state
- Prefer shared stubs (`NoopDebugStream`, `StubReadTool`) over inline duplicates
