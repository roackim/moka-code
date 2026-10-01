# test/ — Test Suite

All tests use pytest. Run with the pixi env:

```bash
.pixi/envs/default/bin/python -m pytest test/ -q
```

---

## Common Fixtures (`conftest.py`)

- `NoopDebugStream`, `FakeServer`, `StubReadTool`, `StubAgent` — reusable stubs
- `harness_stub` / `harness_stub_compaction` — pre-built harnesses (skip `__init__`)
- `run_harness_tool_call()`, `make_chunk_stream()` — async test helpers

## Test Files

| File | Module Under Test | What It Covers |
|------|-------------------|----------------|
| `test_permissions.py` | `permissions.py`, `tools.py`, `harness.py` | Gate decisions, prompt text, ask/deny/allow flow; file-tool layer |
| `test_roles.py` | `roles.py` | Role model, files, seeding, validation |
| `test_themes.py` | `commands/themes.py`, `colors.py` | Theme selection, picker, persistence |
| `test_argument_completion.py` | `input/completion.py` | Selector style + descriptions for argument menus |
| `test_tool_cancel.py` | `tools.py` | `ShellTool`/`MinimalToolset` run/cancel/timeout |
| `test_tool_message_lifecycle.py` | `tools.py`, `ui/chat_message.py` | Tool message states, `Harness.stop_tool` |
| `test_tool_call_assembly.py` | `harness.py` | Streaming tool-call buffer assembly |
| `test_ui_permission_submit.py` | `ui/app.py` | Input blocked/cleared during permission prompts |
| `test_command_surface.py` | `ui/commands/` | Command registry shape, descriptions, model rows |
| `test_command_import_graph.py` | `ui/commands/` | Domain modules import only `base`; no cycles |
| `test_core_ui_boundary.py` | `harness/` | R9 guard: harness imports no UI |
| `test_config_loader.py` | `settings.py` | Split config files, validation, state |
| `test_images.py` | `harness/images.py`, `ui/app.py`, `commands/conversation.py` | Header probing, image cache, attachment collection, content parts, text-only refusal, Ctrl+V, export/import round trip |
| `test_config_commands.py` | `ui/commands/core.py` | `/config`, `/edit`, `/reload`, external editor |
| `test_conversation_commands.py` | `ui/commands/conversation.py` | `/import`, `/export`, history rebuild |
| `test_compaction.py` | `harness.py` | Conversation history summarization (FakeServer) |
| `test_context_builder.py` | `context_builder.py` | File tree building, gitignore, bounded walk |
| `test_context_completion.py` | `input/completion.py` | `@` file picker completion |
| `test_context_window_discovery.py` | `harness/endpoint.py` | OpenRouter context-window lookup |
| `test_local_hostname_resolution.py` | `endpoint_local.py` | `.local` mDNS resolution |
| `test_local_proxy_diagnostics.py` | `harness/endpoint.py` | Connection diagnosis |
| `test_wire_requests.py` | providers (`endpoint*.py`) | Wire-level golden tests: request bodies, headers and what each provider learns, through `wire_fake.py`'s recording fake server (samples copied from the real APIs, source and date noted). Pins today's behaviour for the provider restructure (`notes/providers.md` §8) |
| `test_model_selection.py` | `ui/commands/models.py` | Model picker/selection |
| `test_worker.py` | `worker.py` | Four tool bodies: read/write/edit/bash, timeout, truncation |
| `test_worker_protocol.py` | `worker.py` | JSONL framing, dispatch, errors, CRLF, shutdown |
| `test_transport.py` | `tools.py` | `InProcessTransport` seam, toolset factory |
| `test_sandbox.py` | `sandbox.py` | argv (network/run_args), JSONL client, timeout/respawn, transport |
| `test_projects.py` | `projects.py` | project file template/parse/validation, active persistence |
| `test_sandbox_command.py` | `commands/sandbox.py` | `/sandbox` list/select/none/unknown, completions |
| `test_elision.py` | `harness/elision.py` | head/tail truncation boundary cases |
| `test_patch_parser.py` | `worker.py` | `parse_patch` + `apply_patch` cascade |
| `test_usage.py` | `usage.py` | Token usage normalization |
| `test_streaming_incremental.py` | UI rendering | Incremental streaming render artifacts |
| `test_no_shadowed_modules.py` | package layout | No shadowed/duplicate module names |
| TUI widget tests | `ui/tui/` | `buffer`, `bars`, `button`, `choice`, `table_view`, `popup`, `text`, `layout`, `fuzzy`/`menu`, `search_modal`, `colors`, `tui_*` (navigation/router/actions/foundations/interactions/integration), `message_focus`, `chat_message`, `input_height`, `debug_popup`, `clipboard` |

## Notes

- `test_compaction.py` uses the `FakeServer` fixture; no real backend needed.
- Tests isolate config by monkeypatching module functions
  (`settings.get_config_dir`, `get_state_path`, `roles._ROLES_DIR`).

## Adding Tests

- Place new test files in `test/`, mirroring the module (`harness/roles.py` →
  `test/test_roles.py`).
- Use `conftest.py` fixtures/stubs; avoid global state.
