# Known issues

Open issues found but not yet fixed (started 2026-09-30). One entry per
issue, with where it is and how it was found. When an issue is fixed, remove
its entry (git history keeps it). Planned work lives in `PLAN.md`; this file
points to the plan step when one exists.

Status: **confirmed** (verified in code) · **to audit** (a lead: looks like a
known bad pattern, not yet checked).

---

## Docs

**D3. Reasoning wiki page describes a past state** · confirmed
`.wiki/notes/reasoning-traces.md`: the verification section still describes
a run "with preserve on and off" (the setting no longer exists).
→ `PLAN.md` step 2.

---

## Reasoning

**R8. No reasoning sent back, even inside a tool loop** · known risk
Replay removed 2026-09-30 until `PLAN.md` step 2. ⚠ Some models may reject
or degrade a tool-loop follow-up without their reasoning (DeepSeek thinking
mode; Gemini/Anthropic signed blocks via OpenRouter). Unverified.
→ `PLAN.md` step 2.

---

## Compaction

**C1. Summarizer receives raw stored entries** · confirmed
`harness.py` `compact_history`: `json.dumps(effective_history)` includes all
reasoning, signed/encrypted blocks, internal ids, `source`, image references,
and full tool output. → `PLAN.md` step 3.

---

## Providers and config

**P1. No provider contract; ~20 `type ==` branches** · in progress
Provider classes and registry in `harness/providers/` (2026-10-01, step 1.2
commits 1a and 1b): no `type ==` left; the §9 behaviour changes are next.
→ `PLAN.md` step 1.

**P4. Context window guessed as 32768 when unknown** · confirmed
`ui/status_presenter.py`: `endpoint.max_context or 32768`. The status bar
shows a made-up size. The user's todo already records "shows 32k for
deepseek despite being like 1M".

**P5. Bare model id matched by suffix, alias as fallback** · to audit
`harness/providers/openrouter.py` (`discover_models._match`, `query_context_window`):
`deepseek-v4-flash` resolves to any `…/deepseek-v4-flash`; `~`-prefixed alias
entries used as fallback. Could pick the wrong model silently.

**P7. Guessed effort levels** · confirmed
`harness/endpoint.py` `efforts_from_metadata`: invents
`none/low/medium/high` when a model lists `reasoning` or `reasoning_effort`
without `reasoning.supported_efforts` (its Ollama guess went with Ollama,
2026-10-01). Checked 2026-10-01 against each provider's docs and OpenRouter's live `/models`:
- OpenRouter: 194 of 333 reasoning models state `supported_efforts` (all also
  list `reasoning_effort`); of the other 139, 135 list only `reasoning`, which
  is not an effort signal. Docs: `reasoning.effort` takes
  `max|xhigh|high|medium|low|minimal|none` for any model; an unsupported level
  is mapped to the nearest supported one.
- OpenAI: `/v1/models` states no reasoning capabilities; levels are per model in
  the docs, and some models reject `none` (HTTP 400). Not discoverable.
- llama.cpp: `reasoning_effort` is accepted; only `none` is interpreted, any
  other value is passed to the model's chat template. No per-model levels
  published (`/models` meta, `/props`).
→ `.wiki/notes/providers.md` §9, decision 4.

**P9. llama.cpp selection overridden by the first listed model** · confirmed
`endpoint.py` `prewarm_model_name` / `providers/llamacpp.py` `query_model_name`:
for `type = "llamacpp"` the selected model is replaced by `models[0]` of
`/models`. llama.cpp router mode serves several models and requires `model`
(its README), so the user's choice is lost there.
→ `PLAN.md` step 1, `providers.md` §9.6.

---

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
`ui/status_presenter.py`: `selected_model or "?"`. With a
real server whose model is not known yet (llama.cpp before its probe), the
status bar shows `name:?`.

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

**H5. Tests written from the implementation** · to audit
Four tests asserted behaviour the user had rejected (fixed 2026-09-30:
type-from-table-name, rediscovery on `/config`, `preserve_reasoning`,
server-level `efforts`). `test_effort.py` also hand-fed an Ollama `capabilities` shape the
server never returns at list time (I17; gone with Ollama, 2026-10-01). Others may do the same; see `PROCESS.md` 5.4
(tests cite the decision they protect).
