"""Tool calls never stall or drop output: long output lines, fast output,
big files."""

import asyncio
import sys

from moka_code import worker
from moka_code.harness.harness import _drain_output
from moka_code.ui.tui.components.debug_panel import DebugLogPanel


def test_a_bash_output_line_over_64_kib_is_kept():
    """Lines were read with asyncio's 64 KiB cap: past it the pump died and
    the whole output was lost (only ``[exit:0]`` came back)."""
    command = f"{sys.executable} -c \"print('x' * 200000); print('after')\""
    out = asyncio.run(worker.bash(command, cwd=".", timeout=20))
    assert "x" * 200000 in out and "after" in out


def test_bash_output_keeps_a_character_split_across_chunks():
    command = f"{sys.executable} -c \"import sys; sys.stdout.write('a' * 65535 + 'é' * 3)\""
    out = asyncio.run(worker.bash(command, cwd=".", timeout=20))
    assert "a" * 65535 + "ééé" in out and "�" not in out


def test_queued_output_is_merged_per_stream_in_order():
    queue = asyncio.Queue()
    for item in [("stdout", "b\n"), ("stdout", "c\n"), ("stderr", "e\n"), ("stdout", "d\n")]:
        queue.put_nowait(item)
    assert _drain_output(("stdout", "a\n"), queue) == [
        ("stdout", "a\nb\nc\n"), ("stderr", "e\n"), ("stdout", "d\n")]
    assert queue.empty()


def test_the_activity_log_takes_a_burst_in_one_update():
    panel = DebugLogPanel(max_lines=3, left_pad=0)
    updates = []
    panel.update = lambda text: updates.append(text)
    panel.log_many([f"line {i}" for i in range(10)])
    assert updates == ["line 7\nline 8\nline 9"]


def test_an_exact_edit_does_not_scan_the_file_line_by_line():
    """The normalized fallbacks only run when the exact search finds nothing."""
    content = "".join(f"line {i}\n" for i in range(50000))
    patch = worker.PatchBlock("f", "line 42\n", "LINE 42\n")
    calls = []
    original = str.split

    class Spy(str):
        def split(self, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)

    new, message = worker.apply_patch(Spy(content), patch)
    assert message.startswith("[OK]") and "mode=exact" in message
    assert "LINE 42\n" in new and calls == []
