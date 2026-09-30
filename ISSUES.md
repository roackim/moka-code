# Known issues

Open issues found but not yet fixed (started 2026-09-30). One entry per
issue, with where it is and how it was found. When an issue is fixed, remove
its entry (git history keeps it). Planned work lives in `PLAN.md`; this file
points to the plan step when one exists.

Status: **confirmed** (verified in code) · **to audit** (a lead: looks like a
known bad pattern, not yet checked).

---

## Docs

**D1. `HANDOFF.md` is stale** · confirmed
Every agent must read it second (`AGENTS.md`). It is titled "Pico-Chat",
gives branch `cleanup`, HEAD `caaa359`, "Last updated 2026-09-24", test
counts 457/540 (actual: 861), commands for a `.pixi` env and a `pico_chat`
package, and cites `SIMPLIFICATION.md` and `plans/cleanup_round2.md`, which
do not exist.

**D2. `principles.md` cites a missing `SIMPLIFICATION.md`** · confirmed
`.wiki/notes/principles.md` line 5.

**D3. Reasoning wiki page describes a past state** · confirmed
`.wiki/notes/reasoning-traces.md`: the verification section still describes
a run "with preserve on and off" (the setting no longer exists).
→ `PLAN.md` step 2.

---

## Reasoning

**R1. `<think>` parsing swallows answer text** · confirmed
`harness.py` stream loop + `thinking_parser.py`: runs on every provider's
content; any answer containing `<think>` loses the rest of its text into
hidden reasoning; an unclosed tag swallows the whole answer (`flush()`).
→ `PLAN.md` step 2 (mode A off).

**R2. Reasoning replayed to a different model** · confirmed
`harness.py` `_api_history`: all stored reasoning, including signed or
encrypted OpenRouter `reasoning_details`, goes to whatever model is active.
→ `PLAN.md` step 2.

**R3. Reasoning replayed for every turn, whatever the server accepts** · confirmed
Always on since `preserve_reasoning` was removed. ⚠ DeepSeek's reasoner is
documented to reject `reasoning_content` in input (unverified here).
→ `PLAN.md` step 2 (current turn by default).

**R4. Inline-tag reasoning stored differently live and on import** · confirmed
Live: tags moved out of `content` into `reasoning`. Import of old exports
(`ui/commands/conversation.py` `_rebuild_ui_from_history`): split for display
only, tags stay in `content` and are replayed as answer text.
→ `PLAN.md` step 2.

**R5. Tag-parsed reasoning sent back in a field the model never used** · confirmed
A model that reasoned inline gets it back as `reasoning_content` / `thinking`
/ `reasoning`. → `PLAN.md` step 2.

**R6. `openai` type docstring contradicts the code** · confirmed
`endpoint_openai.py` `outgoing_messages`: docstring says nothing is sent back
to OpenAI; the code sends `reasoning_content` whenever text exists.
→ `PLAN.md` step 2.

**R7. Dead parser state and misplaced code** · confirmed
`thinking_parser.py`: `full_reasoning` and `detected_open_tag` never read;
module docstring claims it handles the `reasoning_content` path (it does
not); hosts `MetricsState`, unrelated to thinking.
`harness.py` `_stream_llm_response`: `if not chunk.choices: continue` and the
`delta` assignment both appear twice. → `PLAN.md` step 2.

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

**P2. `efforts` model-table key still in code** · confirmed
Rejected by the user; removed from the template only. → `PLAN.md` step 0.

**P3. No model auto-selected** · confirmed
After the first server is added, nothing is selected until `/model`.
Decided: auto-select the last used model if available, else the first
available. → `PLAN.md` step 0.

**P4. Context window guessed as 32768 when unknown** · confirmed
`ui/status_presenter.py`: `endpoint.max_context or 32768`. The status bar
shows a made-up size. The user's todo already records "shows 32k for
deepseek despite being like 1M".

**P5. Bare model id matched by suffix, alias as fallback** · to audit
`harness/endpoint_discovery.py` (`_match`, `openrouter_context_window`):
`deepseek-v4-flash` resolves to any `…/deepseek-v4-flash`; `~`-prefixed alias
entries used as fallback. Could pick the wrong model silently.

**P6. `model` fallback in selection** · to audit
`settings.py` `get_model_for_server`: falls back to the server table's
`model` key. Check whether this is explicit config or a hidden default.

---

## UI

**U1. Menus: sorting and mouse per caller** · confirmed
Sorting done for `/config` only; mouse only for input completion menus.
`/model`, `/session`, `/theme` pickers and the `/config` popup are not
covered. → `PLAN.md` step 0.

**U2. `$` shell prefix still handled** · confirmed
Hint removed; `app.py` `on_user_submit` still runs `$…` as a shell command.
Decided: remove. → `PLAN.md` step 0.

**U3. Flat templates use `#` for help lines** · confirmed
`ui`, `context`, `debug` templates. Decided: `##` for help lines.
→ `PLAN.md` step 0.

**U4. Editor silently chosen** · to audit
`ui/external_editor.py` `resolve_editor`: without `$VISUAL`/`$EDITOR`,
picks `nano`, `vim` or `vi`. Implicit choice; the setup note "no $EDITOR"
only fires when none of them exists.

**U5. Unknown theme silently becomes `terminal`** · to audit
`ui/tui/colors.py` `set_theme`. A typo in `ui.theme` gives no feedback.

**U6. Color typo silently uses the default** · to audit
`ui/status_presenter.py` `_resolve_color` (sandbox colors in `ui.toml`).

**U7. `?` shown for an unknown model** · to audit
`ui/status_presenter.py`: `selected_model or endpoint.model or "?"`. With a
real server whose model is not known yet (llama.cpp before its probe), the
status bar shows `name:?`.

---

## Code health

**H1. 97 dead-code candidates** · to audit
`vulture moka_code --min-confidence 60` (2026-09-30). Most in `harness.py`
(11), `chat_message.py` (8), `app.py` (7), `chat_history_panel.py` (7),
`endpoint.py` (7), `tui/actions.py` (7). Examples in `endpoint.py`:
`set_model`, `_native_base_url`, `_ollama_messages`, `_native_response`,
`_openrouter_context_window`. Some will be false positives.

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
