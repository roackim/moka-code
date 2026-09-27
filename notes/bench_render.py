"""Benchmark the chat render/scroll path on a real conversation (convo.json).

Scenarios (each per raster size, width x height in terminal cells):
  steady    - no content change; boxes reuse cached SubBuffers (blit + serialize)
  rerender  - every box's SubBuffer invalidated each frame (streaming repaint)
  scroll    - one wheel notch into the history, then a full frame
  hittest   - one click hit-test (line-map lookup), then a full frame
  stream    - append one 8-char chunk to a growing message, then a full frame
  stream_smooth - ingest a chunk, reveal one grain via StreamRevealer, full frame

Reported per scenario (median):
  component ms - append (stream) + ChatHistoryPanel.render() into the Buffer
  serialize ms - Buffer.render() -> ANSI string (bytes written to the pty)
  ansi bytes   - size of that string

Also reports `stream_micro`: pure MarkdownComponent.update(append=True) us/append
vs total message length for prose / code / table streams. Cost is flat for
newline-delimited prose; a single never-ending line is O(line) (the open line is
re-wrapped each append), and an open fence/table is held whole by design.

Usage:
  python notes/bench_render.py [--sizes 80x24,120x40,200x50,300x70] [--iters 200]
                               [--save notes/bench_baseline.json]
                               [--load notes/bench_baseline.json]
"""

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moka_chat.ui.chat_history_panel import ChatHistoryPanel
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.events import MouseEvent
from moka_chat.ui.tui.msg_types import AssistantMsg, UserMsg
from moka_chat.ui.tui.components.markdown import MarkdownComponent
from moka_chat.ui.stream_revealer import StreamRevealer

CONVO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "convo.json")

SCENARIOS = ("steady", "rerender", "scroll", "hittest", "stream", "stream_smooth")

STREAM_CHUNK = 8
STREAM_BASE = (
    "Streaming prose sentence that should wrap across the panel width. "
    "More **bold** and `code` and a [link](url).\n\n"
)

MICRO_LENGTHS = (500, 1000, 2000, 4000)


def _stream_source(repeat):
    return STREAM_BASE * (40 * max(1, repeat))


def _micro_text(kind, n):
    if kind == "code":
        body = "x = 1\nprint(x)\n" * (n // 15 + 2)
        return ("```python\n" + body)[:n]
    if kind == "table":
        body = "".join(f"| {i} | value {i} |\n" for i in range(n // 8 + 2))
        return ("| n | v |\n|---|---|\n" + body)[:n]
    unit = "The quick brown fox jumps over the lazy dog. "
    return (unit * (n // len(unit) + 1))[:n]



def build_panel(width, height, repeat=1):
    panel = ChatHistoryPanel(max_width=width)
    history = json.load(open(CONVO, encoding="utf-8"))["history"] * max(1, repeat)
    for entry in history:
        role = entry.get("role")
        content = entry.get("content") or ""
        if role == "user":
            msg = panel.add_message(content, msg_type=UserMsg())
        elif role == "assistant":
            msg = panel.add_message(content, msg_type=AssistantMsg())
        else:
            continue
        msg.finalize()
    panel.set_layout(0, 0, width, height)
    return panel


def _invalidate(panel):
    for msg in panel.messages:
        box = msg.box
        if box.subbuffer is not None:
            box.subbuffer.mark_changed()


def bench(width, height, iterations, scenario, repeat=1):
    panel = build_panel(width, height, repeat)
    buf = Buffer(width, height)
    panel.set_layout(0, 0, width, height)

    stream_msg = None
    stream_source = None
    stream_pos = 0
    revealer = None
    revealed = 0
    clock = 0.0
    if scenario in ("stream", "stream_smooth"):
        stream_msg = panel.add_message("", msg_type=AssistantMsg())
        stream_source = _stream_source(repeat)
        panel.set_layout(0, 0, width, height)
    if scenario == "stream_smooth":
        revealer = StreamRevealer(60)

    def one_frame():
        nonlocal stream_pos, revealed, clock
        t0 = time.perf_counter()
        if scenario == "stream":
            chunk = stream_source[stream_pos:stream_pos + STREAM_CHUNK]
            stream_pos = (stream_pos + STREAM_CHUNK) % len(stream_source)
            stream_msg.append(chunk)
        elif scenario == "stream_smooth":
            chunk = stream_source[stream_pos:stream_pos + STREAM_CHUNK]
            stream_pos = (stream_pos + STREAM_CHUNK) % len(stream_source)
            clock += 1.0 / 60.0
            stream_msg.ingest(chunk)
            revealer.ingest(chunk, clock)
            released = revealer.tick(clock)
            if released:
                revealed += len(released)
                stream_msg.reveal_to(revealed)
        panel.render(buf)
        t1 = time.perf_counter()
        out = buf.render()
        t2 = time.perf_counter()
        return t1 - t0, t2 - t1, len(out)

    for _ in range(5):
        one_frame()

    frames = []
    out_len = 0
    for i in range(iterations):
        if scenario == "rerender":
            _invalidate(panel)
        elif scenario == "scroll":
            button = 64 if i % 2 == 0 else 65
            panel.handle_input(MouseEvent(width // 2, height // 2, button, True, False, 1))
        elif scenario == "hittest":
            panel._cached_hit_test(panel.y + (i % max(1, height)))
        comp, ser, out_len = one_frame()
        frames.append((comp, ser))

    comp = statistics.median(f[0] for f in frames) * 1000
    ser = statistics.median(f[1] for f in frames) * 1000
    return comp, ser, out_len


def bench_update_micro(kinds=("prose", "code", "table"), lengths=MICRO_LENGTHS):
    """us per MarkdownComponent.update(append=True) at increasing total length."""
    results = {}
    max_len = max(lengths) + 32
    for kind in kinds:
        text = _micro_text(kind, max_len)
        rows = []
        for length in lengths:
            comp = MarkdownComponent("", streaming=True)
            comp.set_layout(0, 0, 120, 100000)
            for pos in range(0, length, 16):
                comp.update(text[:min(pos + 16, length)], append=True)
            n = 0
            t0 = time.perf_counter()
            for step in range(16):
                end = length + step + 1
                if end > len(text):
                    break
                comp.update(text[:end], append=True)
                n += 1
            dt = (time.perf_counter() - t0) / max(1, n)
            rows.append({"length": length, "us_per_append": round(dt * 1e6, 2)})
        results[kind] = rows
    return results


def parse_sizes(text):
    sizes = []
    for part in text.split(","):
        w, _, h = part.partition("x")
        sizes.append((int(w), int(h)))
    return sizes


def run(sizes, iterations, repeat=1):
    results = {"repeat": repeat}
    for (w, h) in sizes:
        key = f"{w}x{h}"
        results[key] = {}
        for scenario in SCENARIOS:
            comp, ser, out_len = bench(w, h, iterations, scenario, repeat)
            results[key][scenario] = {
                "component_ms": round(comp, 3),
                "serialize_ms": round(ser, 3),
                "total_ms": round(comp + ser, 3),
                "ansi_bytes": out_len,
            }
    results["stream_micro"] = bench_update_micro()
    return results


def print_micro(micro):
    print("\n== stream_micro (MarkdownComponent.update append=True, us/append) ==")
    kinds = list(micro.keys())
    lengths = [r["length"] for r in micro[kinds[0]]] if kinds else []
    header = f"{'length':>8}" + "".join(f" {k:>10}" for k in kinds)
    print(header)
    for i, length in enumerate(lengths):
        line = f"{length:>8}"
        for k in kinds:
            line += f" {micro[k][i]['us_per_append']:>10.2f}"
        print(line)


def print_table(results, baseline=None):
    print(f"(convo.json repeated x{results.get('repeat', 1)})")
    for key, scenarios in results.items():
        if key in ("repeat", "stream_micro"):
            continue
        print(f"\n== raster {key} ==")
        header = f"{'scenario':<10} {'component ms':>12} {'serialize ms':>13} {'total ms':>9} {'ANSI bytes':>11}"
        if baseline and key in baseline:
            header += f" {'total Δ':>10}"
        print(header)
        for scenario, r in scenarios.items():
            line = f"{scenario:<10} {r['component_ms']:>12.3f} {r['serialize_ms']:>13.3f} {r['total_ms']:>9.3f} {r['ansi_bytes']:>11}"
            if baseline and key in baseline and scenario in baseline[key]:
                base = baseline[key][scenario]["total_ms"]
                pct = (r["total_ms"] - base) / base * 100 if base else 0.0
                line += f" {pct:>+9.1f}%"
            print(line)
    if "stream_micro" in results:
        print_micro(results["stream_micro"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="80x24,120x40,200x50,300x70")
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--save", default=None)
    ap.add_argument("--load", default=None)
    args = ap.parse_args()

    sizes = parse_sizes(args.sizes)
    results = run(sizes, args.iters, args.repeat)
    baseline = json.load(open(args.load, encoding="utf-8")) if args.load else None
    print_table(results, baseline)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nsaved: {args.save}")


if __name__ == "__main__":
    main()
