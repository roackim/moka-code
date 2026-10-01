# Known issues

Open issues found but not yet fixed (started 2026-09-30). One entry per
issue, with where it is and how it was found. When an issue is fixed, remove
its entry (git history keeps it). Planned work lives in `PLAN.md`; this file
points to the plan step when one exists.

Status: **confirmed** (verified in code) · **to audit** (a lead: looks like a
known bad pattern, not yet checked).

---

## Reasoning

**R9. Replay edge cases unverified on a real server** · to audit
(2026-10-01, 2b) Decided without a doc or a key: (a) OpenRouter sends no field
when the model gave no reasoning, but DeepSeek behind OpenRouter requires
`reasoning_content` with tools (its docs): does OpenRouter add it? (b) llama.cpp
gets `reasoning_content: ""` when the model gave none (opencode's habit, not
documented). (c) DeepSeek's 400 rule and the whole DeepSeek provider are from its docs, never run with a key. (d) Signed or encrypted blocks (Claude, Gemini via OpenRouter)
never exercised. Wire tests mark each ⚠. → `reasoning-traces.md`.

---

## Compaction

**C2. Filtered compaction hides what a command printed** · to check
(2026-10-01, step 3) With `compact_filter_tool_calls` on, a failing `bash` is
only `→ exit 1`: the summary cannot say what the test printed unless the
assistant's own text did. Possible improvement: keep the last lines of a
failed command's output. Check on real compactions before changing; the escape
hatch is `compact_filter_tool_calls = false`.

---

## Providers and config

**P5. Bare model id matched by suffix, alias as fallback** · to audit
`harness/providers/openrouter.py` (`list_models._match`):
`deepseek-v4-flash` resolves to any `…/deepseek-v4-flash`; `~`-prefixed alias
entries used as fallback. Could pick the wrong model silently.

**P11. llama.cpp `cache_n` never used** · confirmed (docs)
`providers/llamacpp.py` `_usage` reads `timings.cache_n` only when usage has no
cache count, but llama.cpp's documented usage carries
`prompt_tokens_details.cached_tokens: 0` (tools/server/README.md, read
2026-10-01), so the cache shown is always 0. Pinned in
`test_wire_requests.py::test_llamacpp_cache_counts_as_reported`. Unverified
whether a real server reports a non-zero `cached_tokens` itself.

## UI

**U4. Editor silently chosen** · to audit
`ui/external_editor.py` `resolve_editor`: without `$VISUAL`/`$EDITOR`,
picks `nano`, `vim` or `vi`. Implicit choice; the setup note "no $EDITOR"
only fires when none of them exists.

**U5. Unknown theme silently becomes `terminal`** · to audit
`ui/tui/colors.py` `set_theme`. A typo in `ui.theme` gives no feedback.

**U6. Color typo silently uses the default** · to audit
`ui/status_presenter.py` `_resolve_color` (sandbox colors in `ui.toml`).

**U7. `?` shown for an unknown model** · to audit
`ui/status_presenter.py`: `selected_model or "?"`. Since 2026-10-01 nothing
resolves a model in the background, so this shows only when a server is
active with no model selected.

---

## Code health

**H1. 97 dead-code candidates** · to audit
`vulture moka_code --min-confidence 60` (2026-09-30). Most in `harness.py`
(11), `chat_message.py` (8), `app.py` (7), `chat_history_panel.py` (7),
`endpoint.py` (7), `tui/actions.py` (7). `set_model` and the four private
Ollama/OpenRouter wrappers of `endpoint.py` were deleted 2026-10-01. Also `Harness._get_tool_output` (no callers;
found 2026-09-30). Some will be false positives.

**H2. Compatibility aliases** · to audit
`ui/app.py` `_update_mode_line` ("back-compat alias"),
`ui/tui/chat_screen.py` `workspace` ("compatibility alias"),
`ui/tui/events.py` ("legacy string compatibility"),
`ui/tui/components/bars.py` `StatusBar` legacy left/right mode.

**H3. 48 fallback / legacy / back-compat mentions** · to audit
`grep -rin "fallback\|fall back\|legacy\|back-compat" moka_code`. P4–P6 and
U4–U7 are the ones checked so far; the rest are unreviewed.

**H4. Built-in roles as code fallbacks** · to audit
`harness/roles.py`: built-in roles "used as code fallbacks when files are
absent". Check whether a missing role file silently becomes a default role.

**H6. Flaky tests that run bash** · to audit
Tests that run a bash subprocess and check its (streamed) output fail
intermittently: seen in `test_transport.py`, `test_worker.py`,
`test_worker_protocol.py` (`test_serve_streams_bash_output`,
`test_handle_request_dispatches_each_verb`) and `test_tool_cancel.py`. About 1
run in 10 on 2026-10-01 (on HEAD `7e3b080` too), most runs while the machine
was loaded (load average 12–18). None of these files touch the providers.
Likely a timing assumption on subprocess output.

**H7. The suite reads the real `~/.config/moka`** · confirmed
`settings.config` is loaded at import from the user's config folder (no
`MOKA_CONFIG_DIR` in `test/conftest.py`), so a test that touches the global
config sees the user's servers. Found 2026-10-01: `test_themes.py`
`test_refresh_theme_recolors_status_server_model` passed only because the
user's `servers.toml` had a server (fixed in that test; the suite also passes
with an empty `MOKA_CONFIG_DIR`). Other tests may depend on it the same way.

**H5. Tests written from the implementation** · to audit
Four tests asserted behaviour the user had rejected (fixed 2026-09-30:
type-from-table-name, rediscovery on `/config`, `preserve_reasoning`,
server-level `efforts`). `test_effort.py` also hand-fed an Ollama `capabilities` shape the
server never returns at list time (I17; gone with Ollama, 2026-10-01). Provider tests
were moved onto sourced wire fixtures (`PLAN.md` step 1.5, 2026-10-01; two
unsourced usage fields, `cache_read_input_tokens` and `reasoning_token_count`,
were deleted with their tests). Tests outside the providers are unreviewed;
see `PROCESS.md` 5.4 (tests cite the decision they protect).
