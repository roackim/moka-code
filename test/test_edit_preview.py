"""The diff an ``edit`` would make: read before the decision, drawn with line numbers."""

import asyncio
from types import SimpleNamespace

from moka_code import settings
from moka_code.harness.changes import edit_diff
from moka_code.harness.harness import Harness
from moka_code.harness.tools import create_toolset
from moka_code.ui.chat_message import Message, _diff_body
from moka_code.ui.tui.msg_types import ToolCallMsg

FILE = "".join(f"line{n}\n" for n in range(1, 21))


def _preview(tmp_path, **args):
    (tmp_path / "f.txt").write_text(FILE)
    agent = SimpleNamespace(tools_map=create_toolset(tmp_path))
    return asyncio.run(Harness._edit_preview(agent, {"path": "f.txt", **args}))


def test_preview_is_a_unified_diff_with_file_context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.config, "context_diff_context", 2)
    diff = _preview(tmp_path, search="line10\n", replace="LINE10\n")
    assert "@@ -8,5 +8,5 @@" in diff
    assert " line8\n" in diff and " line12\n" in diff and " line13\n" not in diff
    assert "-line10\n+LINE10\n" in diff
    assert (tmp_path / "f.txt").read_text() == FILE        # nothing written


def test_no_preview_when_the_edit_would_not_apply(tmp_path):
    assert _preview(tmp_path, search="absent\n", replace="x\n") == ""
    agent = SimpleNamespace(tools_map=create_toolset(tmp_path))
    missing = {"path": "nope.txt", "search": "a", "replace": "b"}
    assert asyncio.run(Harness._edit_preview(agent, missing)) == ""


def test_diff_body_numbers_lines_and_separates_hunks():
    after = FILE.replace("line3\n", "LINE3\n").replace("line18\n", "")
    body = _diff_body(edit_diff(FILE, after, "f.txt", 1))
    lines = [(prefix, text) for _, prefix, text in body]
    assert (" 2   ", "line2") in lines
    assert (" 3 - ", "line3") in lines and (" 3 + ", "LINE3") in lines
    assert ("", "…") in lines
    assert ("18 - ", "line18") in lines and ("18   ", "line19") in lines


def test_focused_edit_line_shows_the_preview(monkeypatch):
    msg = Message("", msg_type=ToolCallMsg())
    msg.tool_name = "edit"
    msg.tool_args = '{"path": "f.txt", "search": "line3\\n", "replace": "LINE3\\n"}'
    msg.tool_status = "completed"
    msg.tool_diff = edit_diff(FILE, FILE.replace("line3\n", "LINE3\n"), "f.txt", 1)
    msg.set_focused(True)
    text = msg.formatted_text if hasattr(msg, "formatted_text") else ""
    assert "3 + " in text and "LINE3" in text and "line4" in text
