# Incremental Rendering & Message Artifacts — Findings

Status: **partially resolved.** The streaming fast path is correct under a full
repaint; a residual divergence remains between the compositor's *partial*
dirty-rect repaint and a full repaint. Details, repros and open hypotheses below.

Files in scope:
- `moka_chat/ui/tui/components/markdown.py` (incremental parse, dirty line)
- `moka_chat/ui/tui/components/box.py` (tail raster, SubBuffer grow/shrink, clip)
- `moka_chat/ui/tui/components/text.py` (clip-aware line range)
- `moka_chat/ui/tui/buffer.py` (`SubBuffer.clear_region`/`shrink`)
- `moka_chat/ui/chat_message.py` (`reformat(append=True)`)
- `moka_chat/ui/chat_history_panel.py` (`_row_index`, window render, `mark_changed`)
- `moka_chat/ui/tui/compositor.py` (partial vs full repaint, dirty-rect dedupe)

---

## 1. Symptom reported

In a long assistant message the transcript showed:
- a word split mid-word (`...Two names, fa` / `st to vet.`),
- a blank row **without** the `▌` gutter between the split halves,
- lots of trailing spaces (normal: rows are filled to panel width).

No markdown fence/table was present in that region, so the split was not
code-block hard-wrapping — it was a raster/positioning artifact.

## 2. What was ruled out

- **Import path** (`/conversation import` → `add_message` full text): renders
  correctly at 80/120/160/200 columns. No artifact.
- **Streaming with a full panel repaint**: streaming the real `convo.json`
  message (char-by-char and in chunks 1/3/5/7) into a `ChatHistoryPanel` and
  comparing **every step** to a freshly built panel is pixel-identical. Guarded
  by `test/test_streaming_incremental.py`.
- **Leading-space mismatch**: the first streamed chunk is `lstrip()`-ed by
  `Message.append`; reference panels must apply the same strip or they differ by
  one leading space (expected, not a bug).

So both the parse layer and the component/raster layer are correct in
isolation. The remaining suspect is the **compositor's partial repaint**.

## 3. Bugs found and fixed during the investigation

### 3.1 Stale SubBuffer clip (fixed)
The child render uses a `SubBufferWrapper` whose `set_clip` writes a clip onto
the shared `SubBuffer`. That clip was never cleared, so the *next* frame's
`clear()`/gutter/bg fill ran under the old clip and silently skipped cells.
Fix: `Box._render_to_subbuffer` starts with `self.subbuffer.clear_clip()`.

### 3.2 Shrink → grow loses gutter rows (fixed)
When a code fence is half-typed, the message can shrink (fence swallows lines)
and later grow again. `SubBuffer.grow` appends default (gutter-less) rows, while
the tail raster starts at `dirty_from`; if `dirty_from > old_height`, the
re-added rows are never gutted. Fix: `Box._grew_from` records the pre-growth
height, and if the dirty tail starts below it, force a full redraw.

### 3.3 Gap accounting in the panel (fixed, from earlier scroll work)
`render()` used `_get_all_rows()` (sum of heights, **no** inter-message gaps)
while hit-testing used the gap-inclusive line map, so auto-scroll's `start_y`
was short by `gap × (messages − 1)`. Now both use one `_row_index()`.

### 3.4 Panel reported a child rect but repaints its whole area (fixed)
`ChatHistoryPanel.mark_changed` propagated the box's rect (captured **before**
the box grew). The panel, however, always fills its full background and re-blits
every visible message. The compositor therefore clipped/cleared only the stale,
too-small rect — leaving newly revealed rows unpainted for one frame (or
persistently under continuous streaming). Fix:
`ChatHistoryPanel.mark_changed` now always marks the full panel bounds.

### 3.5 Duplicate dirty rects (fixed)
`BaseComponent.mark_changed` propagates the same rect up through every ancestor,
so `collect_dirty_rects` returns the same rectangle once per ancestor (observed:
5 copies) and the compositor repainted it 5×. Fix: dedupe `valid_dirty_rects` in
`Compositor.render`.

## 4. Reproduced-but-unresolved: partial render ≠ full render

### Repro harness (headless)
Build the app, add the `convo.json` history, stream the last assistant message,
and for each frame compare the compositor-style partial repaint with a full
repaint of the same tree:

```python
rects = []
root.collect_dirty_rects(rects)
valid = [r for r in rects if r[2] > 0 and r[3] > 0]
if valid:
    for x, y, w, h in valid:
        buf.clear_rect(x, y, w, h)
        buf.set_clip(x, y, w, h)
        root.render(buf)
        buf.clear_clip()
else:
    buf.clear()
    root.render(buf)
root.clear_dirty()

ref.clear()
root.render(ref)
assert snapshot(buf) == snapshot(ref)   # currently fails on some frames
```

Prime `buf` with one full frame first, otherwise the bars/input are simply
unpainted and everything "mismatches".

### Current numbers (after fixes 3.4 + 3.5)
`mismatched frames` for the real message:
- 160×40 chunk 3, unfocused: **2**
- 160×40 chunk 3, focused: **6**
- 200×50 chunk 1, unfocused: **13**
- 120×40 chunk 7, unfocused: **1**
- 80×24 chunk 5, focused: **1**

Earlier (before 3.4) the pattern was a clean one-frame lag: a mismatch appeared
on the frame where the message grew and recovered on the next frame. Some
residual mismatches persist after 3.4/3.5 and need the dumps below to classify.

### Leading hypothesis for the remainder
The compositor only clears/redraws the **dirty rects**. Any component that
changed without contributing a dirty rect — or whose own bounds differ from the
rect it reported — stays stale in `buf` and diverges from a full repaint.
Candidates to inspect next:
- Components outside the panel (status-bar toast, action bar, input cursor
  blink) changing on the same frame without a matching dirty rect.
- The panel overriding the compositor clip (`buffer.set_clip(panel_bounds)`)
  while the compositor cleared a different rect.
- The dirty rect captured before `set_layout` moved/grew a box.

### How to finish diagnosing
On the first mismatching frame, print the differing rows and `valid` rects, then
check whether the stale region lies inside or outside the panel.
The script from §4 prints rows and rects at the first mismatch; extend it to
also emit `panel.y/height`, `box.y/height`, and whether the row is inside the
reported dirty rect.

## 5. Perf context (why the fast path exists)

Streaming per-frame cost at 200×50, before → after Phases A/B/C:

| chars | append | render | serialize | frame |
|---|---|---|---|---|
| 500  | 0.33→0.09 | 3.24→2.70 | 1.90→1.89 | 5.46→4.67 |
| 1000 | 0.59→0.10 | 4.25→3.10 | 1.86→1.82 | 6.71→5.03 |
| 2000 | 1.08→0.11 | 5.08→3.26 | 1.88→1.81 | 8.04→5.17 |
| 4000 | 2.64→0.08 | 6.56→3.20 | 1.90→1.82 | 11.09→5.10 |

Cost is now flat with message length. Any fix for §4 must keep the **full
repaint** path correct and fast; prefer correcting the dirty-rect bookkeeping
over disabling the fast path.

## 6. Guards / commands

```bash
.pixi/envs/default/bin/python -m pytest test/test_streaming_incremental.py -q   # parse + full-render equivalence
.pixi/envs/default/bin/python -m pytest test/ -q                                # 500 passing
.pixi/envs/default/bin/python -m compileall -q moka_chat
.pixi/envs/default/bin/python -m vulture moka_chat --min-confidence 80
```

`test/test_streaming_incremental.py` covers:
- incremental parse vs full parse (7 samples × 4 chunk sizes),
- incremental render vs fresh build (samples × 2 sizes).

It does **not yet** cover the compositor partial-repaint path — that is the gap
to close next (add the §4 harness as a regression test once the residual
mismatch is fixed).
