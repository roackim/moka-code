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

**P1. No provider contract; ~20 `type ==` branches** · confirmed
`endpoint.py`, `endpoint_discovery.py`, `endpoint_openai.py`, `settings.py`.
→ `PLAN.md` step 1.

**P4. Context window guessed as 32768 when unknown** · confirmed
`ui/status_presenter.py`: `endpoint.max_context or 32768`. The status bar
shows a made-up size. The user's todo already records "shows 32k for
deepseek despite being like 1M".

**P5. Bare model id matched by suffix, alias as fallback** · to audit
`harness/endpoint_discovery.py` (`_match`, `openrouter_context_window`):
`deepseek-v4-flash` resolves to any `…/deepseek-v4-flash`; `~`-prefixed alias
entries used as fallback. Could pick the wrong model silently.

**P7. Guessed effort levels** · to audit
`harness/endpoint_discovery.py` `efforts_from_metadata`: without
`reasoning.supported_efforts`, `supported_parameters` containing `reasoning`
yields a guessed `none/low/medium/high`; Ollama `thinking` gives levels only
when the model name contains `gpt-oss`. Levels shown may not be what the
model accepts.

---

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
`endpoint.py` (7), `tui/actions.py` (7). Examples in `endpoint.py`:
`set_model`, `_native_base_url`, `_ollama_messages`, `_native_response`,
`_openrouter_context_window`. Also `Harness._get_tool_output` (no callers;
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

**H5. Tests written from the implementation** · to audit
Four tests asserted behaviour the user had rejected (fixed 2026-09-30:
type-from-table-name, rediscovery on `/config`, `preserve_reasoning`,
server-level `efforts`). Others may do the same; see `PROCESS.md` 5.4
(tests cite the decision they protect).
