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

**P11. llama.cpp `cache_n` never used** · confirmed (docs)
`providers/llamacpp.py` `_usage` reads `timings.cache_n` only when usage has no
cache count, but llama.cpp's documented usage carries
`prompt_tokens_details.cached_tokens: 0` (tools/server/README.md, read
2026-10-01), so the cache shown is always 0. Pinned in
`test_wire_requests.py::test_llamacpp_cache_counts_as_reported`. Unverified
whether a real server reports a non-zero `cached_tokens` itself.

---

## Code health

**H1. Dead-code leftovers** · to decide
`vulture moka_code --min-confidence 60` went from 87 hits (2026-09-30) to 15
(2026-10-04); the unambiguous ones are deleted. What is left, on purpose:
- *Metrics display*: `Message.update_metrics` is fed on every `Usage` event but
  nothing shows it (`get_metrics_string`, `should_show_metrics` have no caller),
  so `ui.show_metrics`, `metrics_show_tokens`, `metrics_show_speed` and
  `metrics_show_ttft` do nothing. Delete the whole chain (and retire the keys),
  or show it again (status bar?). Needs a decision.
- *Debug console*: `DebugLogPanel` / `DebugPopup` / `TuiLogHandler` are built and
  fed, but no command or key opens them (`chatTUI.toggle_debug_console` is
  reachable only from a test). Delete, or give it a way in.
- *Test seams*: `chat_message._tool_summary` and `Message.get_formatted` (the
  tool-line tests read the lines through them), `SandboxProcess.start_count`.
- *Toolkit* (`ui/tui/`: `Button`, `Checkbox`, `Navigator`, `Vsplit`, …): kept on
  purpose (principles: do not delete the TUI toolkit).
- *False positives*: the `@tool` functions in `tools.py`, theme palette keys read
  through `getattr`, `_flash_action_key` (read by `Box`).

**H2. Compatibility aliases** · to audit
`ChatScreen.workspace` ("compatibility alias", only `test_tui_navigation.py`
reads it) and the toolkit's own legacy modes (`KeyEvent` string compatibility,
`InputComponent.text`/`cursor` properties, `StatusBar` left/right mode). The
`_update_mode_line` alias and the no-op `Message.update_actions` are gone.

**H3. fallback / legacy / back-compat mentions** · to audit
`grep -rin "fallback\|fall back\|legacy\|back-compat" moka_code`. P4–P6, U4–U7
and H2 are checked; the rest are unreviewed.

**H4. Built-in roles as code fallbacks** · checked (2026-10-04), kept
`harness/roles.py` `load_role`: an unknown name raises `KeyError`; only
`agent` / `chat` come from code when their file is missing, and
`ensure_roles_dir()` re-seeds both at startup, so it is reached only when the
file is deleted during a session. `agent` is the permissive role (every tool
`yes`). Removing the fallback means seeding or failing instead; a design
decision, not a cleanup.

**H5. Tests written from the implementation** · to audit
Four tests asserted behaviour the user had rejected (fixed 2026-09-30:
type-from-table-name, rediscovery on `/config`, `preserve_reasoning`,
server-level `efforts`). `test_effort.py` also hand-fed an Ollama `capabilities` shape the
server never returns at list time (I17; gone with Ollama, 2026-10-01). Provider tests
were moved onto sourced wire fixtures (`PLAN.md` step 1.5, 2026-10-01; two
unsourced usage fields, `cache_read_input_tokens` and `reasoning_token_count`,
were deleted with their tests). Tests outside the providers are unreviewed;
see `PROCESS.md` 5.4 (tests cite the decision they protect).
