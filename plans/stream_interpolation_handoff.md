# Stream Interpolation (Smoothing) — Handoff

**Created:** 2026-09-23 · **Status:** ready to start (no code yet)
**Read first:** `.wiki/notes/principles.md`, `HANDOFF.md`, then
`plans/stream_interpolation.md` (the plan) and this file.
**Predecessor:** `plans/markdown_streaming_refactor.md` — **done**, committed as
`eefa2e2 markdown rendering improved`. Read its §3/§5 and the plan-1 notes below
before touching the message API.

---

## 0. Where things stand

Plan (1) landed the append-only markdown path. Relevant facts for plan (2):

- `MarkdownComponent.update(text, append=True)` commits complete lines and
  re-parses/re-wraps only the open tail. `reveal_to(n)` feeding it growing
  prefixes is exactly its fast path.
- **Approach A is live:** while streaming, the still-open final line is rendered
  *plain* (no inline parse). `MarkdownComponent.set_streaming(False)` re-parses
  in full. `Message.finalize()` already calls `set_streaming(False)`.
- `test/test_streaming_incremental.py` now uses a **streaming-aware reference**
  (`MarkdownComponent(cur, streaming=True)`) and compares per-character buffer
  snapshots. Any change to how the last line is rendered must keep it green.
- Residual: a single never-ending line is still O(line) per append; open
  fence/table held whole. Fine for now — do not re-open this in plan (2).

Baseline suite: **502 passed, 2 pre-existing failures** unrelated to streaming
(`test/test_command_surface.py::test_config_command_completes_theme_names`,
`test/test_themes.py::test_theme_command_opens_picker`). Do not "fix" these here.

---

## 1. Locked decisions (from the plan; do not re-litigate)

- Interpolate **any** visible streamed text — content and reasoning, no type
  sniffing. No cursor in LLM messages.
- Smoothing only; **one-chunk buffer / 100 ms cap**, merge on overlap, snap when
  behind. No cushion knob.
- Grain = **non-whitespace grapheme clusters**; whitespace free; coarse steps
  snap to word ends. `smooth_target_fps` sets cadence; grain is derived.
- Config minimal: `stream_smoothing`, `smooth_target_fps`.
- Clock = `Compositor.add_frame_callback` (not routing `TickEvent` into widgets).
- `base_text` stays canonical full arrived text; `_reveal_len` is the rendered
  prefix; `ingest()` vs `reveal_to(n)`.
- Displace `set_streaming_active`/`streaming_active` and the per-token
  `request_render()` in the presenter.
- Vendor `pico_chat/ui/tui/graphemes.py` rather than adding a `regex` dep.
- Natural `Done`: let the revealer drain, then finalize from the frame callback
  (spinner persists ≤100 ms). Snap only at hard boundaries.

---

## 2. Current anchors (verify line numbers; they move)

- **Compositor** `pico_chat/ui/tui/compositor.py`
  - `self.streaming_active` `:32`; `set_streaming_active` `:102`
  - `should_render = self.streaming_active or ...` `:206-207`
  - per-iteration `TickEvent` dispatch `:203` (keep — spinner still uses it)
  - loop body `run()` `:116-237`; render `:244`
- **App / chatTUI** `pico_chat/ui/app.py`
  - `Compositor(...)` `:733`; `tg.create_task(self.compositor.run())` `:775`
  - `TickEvent` handling / `_status_spinner_frame` `:580-589`
- **Presenter** `pico_chat/ui/generation_presenter.py`
  - `set_streaming_active(True)` `:59-60`; per-event `request_render()` `:65-66`
  - message create/append for Reasoning `:78-89`, Token `:91-100`
  - finalize before ToolCall/PermissionRequest `:108,130`; Error `:224`
  - `finally` clears streaming `:255-257`; auto-scroll `:232-238`
- **Message** `pico_chat/ui/chat_message.py`
  - `base_text` `:66`; `append()` `:454-459`; `reformat()` `:416-439`
  - `finalize()` `:137` (already calls `component.set_streaming(False)`)
- **Panel** `pico_chat/ui/chat_history_panel.py`
  - `auto_scroll` `:37`; `new_message` `:624`; `replace_message` `:739`;
    `add_message` `:771`
- **Config** `pico_chat/pico_cfg.py`
  - `_UI_SPEC` `:256-276`; `DEFAULT_UI_TOML` comments `:120-130`; defaults
    `:413-431`. `target_fps` is a top-level `ui.toml` key (`:276`).
- **Spinner** unaffected: `ChatHistoryPanel.handle_input(TickEvent)` `:449-453`
  → `Message.advance_spinner()` `chat_message.py:244`.

---

## 3. Workstreams (each ends green on §5 gates)

- **S1 — Frame callbacks.** Add
  `add_frame_callback(cb: Callable[[float], bool])` /
  `remove_frame_callback(cb)` to `Compositor`. Invoke per loop iteration near
  `:203` with `time.perf_counter()`; if any returns `True`, `request_render()`.
  Iterate over a **copy** so callbacks can unregister. Unit-test invocation,
  render request, and safe mutation during iteration.
- **S2 — Grapheme helper.** New `pico_chat/ui/tui/graphemes.py`:
  `split_clusters(text) -> list[str]`, `count_nonws(text) -> int`,
  `advance_nonws(text, n) -> int`. Honour combining marks, ZWJ, variation
  selectors, skin-tone modifiers, regional-indicator pairs, Hangul jamo.
  Tests for each; whitespace runs.
- **S3 — `StreamRevealer`.** New `pico_chat/ui/stream_revealer.py`, no TUI
  imports, all time passed in. API and algorithm exactly as plan §2.1/§3:
  `ingest`, `pending`, `tick`, `drain`, `active`. Constants:
  `max_window = 0.100`, `default_window = 0.100`, `min_cluster_grain = 1`.
  Deterministic tests with an injected clock (no sleeps) per plan §10.
- **S4 — Message split.** `base_text` stays canonical arrived text; add
  `ingest(text)` (append only, no reformat) and `_reveal_len` +
  `reveal_to(n)` (re-render `base_text[:_reveal_len]` via
  `component.update(..., append=True)`). Rewrite `append()` as
  `ingest` + `reveal_to(len(base_text))` so non-stream callers are unchanged.
  `finalize()` must drain (`reveal_to(len(base_text))`) **before**
  `component.set_streaming(False)`.
- **S5 — Wiring.** `chatTUI` owns `self.stream_revealer`, the active streamed
  message and `self.stream_revealed`. Presenter routes Token/Reasoning through
  `revealer.ingest` (not `Message.append`) and flushes at boundaries. Add
  `chatTUI._on_frame(now)` (registered via S1) that calls `revealer.tick`,
  applies via `message.reveal_to(...)`, runs the auto-scroll block, and returns
  whether work remains. Deferred finalize on `Done`; presenter `finally` is the
  backstop that force-drains and finalizes.
- **S6 — Config + cleanup.** Add `stream_smoothing` / `smooth_target_fps` to
  `_UI_SPEC` (+ `DEFAULT_UI_TOML` comments) → `ui_stream_smoothing` /
  `ui_smooth_target_fps`; defaults `True` / `60`. Delete
  `Compositor.set_streaming_active`/`streaming_active` and the per-token
  `request_render()`. Run vulture.
- **S7 — Benchmarks & docs.** Extend `notes/bench_render.py` with a smoothed
  stream scenario; keep `notes/bench_stream_baseline.json` usable; update
  `.wiki/notes/ui.md` and `.wiki/log/update.log` (auto-wiki-update skill).

Feature-off (`stream_smoothing = false`) must keep today's direct-append path.

---

## 4. Boundary flush contract (the main correctness risk)

Flush (drain synchronously) and finalize before: `ToolCall` /
`PermissionRequest`, reasoning↔content switch, `Error`/`CancelledError`, and
message replacement / clear / `/import`. Ordering beats smoothness. Tests in
plan §10 integration list are the contract — implement them.

---

## 5. Gates (every step)

```bash
.pixi/envs/default/bin/python -m pytest test/ -q
.pixi/envs/default/bin/python -m compileall -q pico_chat
.pixi/envs/default/bin/python -m vulture pico_chat --min-confidence 80
.pixi/envs/default/bin/python -m pytest test/test_core_ui_boundary.py -q
.pixi/envs/default/bin/python -m pytest test/test_streaming_incremental.py -q
.pixi/envs/default/bin/python notes/bench_render.py --load notes/bench_baseline.json
```

Suite must stay at **502 passed** (plus your new tests) with only the 2
pre-existing theme failures. `harness/` must not import `ui/`.

---

## 6. Do not

- Commit or `git add` (user commits manually).
- Add features/UI beyond the two plans. Config files are the settings UI.
- Touch plan (1)'s commit path or change the streaming-aware test reference
  without re-reading `plans/markdown_streaming_refactor.md`.
