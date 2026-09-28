"""A tool call cut off by the output token limit gets a targeted error."""

from moka_code.harness import events

from conftest import run_harness_tool_call

_PARTIAL = '{"path": "big.py", "content": "' + "x" * 500


def _call():
    return {"id": "t1", "type": "function",
            "function": {"name": "read", "arguments": _PARTIAL}}


def _error(harness):
    evts, messages = run_harness_tool_call(harness, _call())
    result = next(e for e in evts if isinstance(e, events.ToolResult))
    assert result.outcome == "error"
    return messages[-1]["content"]


def test_cut_off_call_says_so_without_echoing_the_blob(harness_stub):
    harness_stub._last_finish_reason = "length"
    content = _error(harness_stub)
    assert "output token limit" in content
    assert "x" * 50 not in content


def test_other_invalid_json_keeps_the_generic_error(harness_stub):
    harness_stub._last_finish_reason = "tool_calls"
    content = _error(harness_stub)
    assert content.startswith("Error: Invalid JSON arguments:")
