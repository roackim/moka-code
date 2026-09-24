"""Tests for the worker JSONL protocol (stdin/stdout framing)."""

import asyncio
import io
import json

from pico_chat.worker import handle_request, serve


def _run(coro):
    return asyncio.run(coro)


def _serve(lines, cwd):
    stdin = io.StringIO("".join(lines))
    stdout = io.StringIO()
    _run(serve(stdin=stdin, stdout=stdout, cwd=cwd))
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def _frame(frame):
    return json.dumps(frame) + "\n"


# --- handle_request --------------------------------------------------------

def test_handle_request_dispatches_each_verb(tmp_path):
    (tmp_path / "f.txt").write_text("hello\n")

    read = _run(handle_request(
        {"id": 1, "tool": "read", "args": {"path": "f.txt"}}, tmp_path
    ))
    write = _run(handle_request(
        {"id": 2, "tool": "write", "args": {"path": "g.txt", "content": "x"}},
        tmp_path,
    ))
    edit = _run(handle_request(
        {"id": 3, "tool": "edit",
         "args": {"path": "f.txt", "search": "hello", "replace": "bye"}},
        tmp_path,
    ))
    bash = _run(handle_request(
        {"id": 4, "tool": "bash", "args": {"command": "echo hi"}}, tmp_path
    ))

    assert read == {"id": 1, "ok": True, "result": "hello\n"}
    assert write["ok"] is True
    assert edit["ok"] is True
    assert "hi" in bash["result"]


def test_handle_request_unknown_tool_is_an_error(tmp_path):
    frame = _run(handle_request({"id": 1, "tool": "nope", "args": {}}, tmp_path))

    assert frame == {"id": 1, "ok": False, "error": "Unknown tool: nope"}


def test_handle_request_tool_error_is_reported(tmp_path):
    frame = _run(handle_request(
        {"id": 1, "tool": "read", "args": {"path": "missing.txt"}}, tmp_path
    ))

    assert frame["id"] == 1
    assert frame["ok"] is False
    assert "File not found" in frame["error"]


def test_handle_request_bash_timeout(tmp_path):
    frame = _run(handle_request(
        {"id": 1, "tool": "bash", "args": {"command": "sleep 30", "timeout": 1}},
        tmp_path,
    ))

    assert frame["ok"] is False
    assert "timed out" in frame["error"]


# --- serve (framing) -------------------------------------------------------

def test_serve_correlates_ids_in_order(tmp_path):
    (tmp_path / "f.txt").write_text("body")

    frames = _serve([
        _frame({"id": 7, "tool": "read", "args": {"path": "f.txt"}}),
        _frame({"id": 8, "tool": "bash", "args": {"command": "echo hi"}}),
    ], tmp_path)

    finals = [f for f in frames if "ok" in f]
    assert [f["id"] for f in finals] == [7, 8]
    assert finals[0]["result"] == "body"


def test_serve_handles_crlf_line_endings(tmp_path):
    (tmp_path / "f.txt").write_text("body")

    frames = _serve([
        _frame({"id": 1, "tool": "read", "args": {"path": "f.txt"}}).replace("\n", "\r\n"),
    ], tmp_path)

    assert frames == [{"id": 1, "ok": True, "result": "body"}]


def test_serve_reports_malformed_json(tmp_path):
    frames = _serve(["{not json}\n"], tmp_path)

    assert frames[0]["id"] is None
    assert frames[0]["ok"] is False
    assert "invalid JSON" in frames[0]["error"]


def test_serve_stops_on_shutdown(tmp_path):
    frames = _serve([
        _frame({"id": 1, "tool": "bash", "args": {"command": "echo hi"}}),
        _frame({"op": "shutdown"}),
        _frame({"id": 2, "tool": "bash", "args": {"command": "echo nope"}}),
    ], tmp_path)

    finals = [f for f in frames if "ok" in f]
    assert [f["id"] for f in finals] == [1, None]
    assert finals[1] == {"id": None, "ok": True, "result": ""}
    assert not any(f.get("id") == 2 for f in frames)


def test_serve_streams_bash_output(tmp_path):
    frames = _serve([
        _frame({"id": 1, "tool": "bash", "args": {"command": "echo streamed"}}),
    ], tmp_path)

    streams = [f for f in frames if "stream" in f]
    finals = [f for f in frames if "ok" in f]
    assert any("streamed" in f["data"] for f in streams)
    assert streams[0]["stream"] == "stdout"
    assert finals == [{"id": 1, "ok": True, "result": "[stdout]\nstreamed\n[exit:0]"}]


def test_serve_stops_at_eof(tmp_path):
    frames = _serve([
        _frame({"id": 1, "tool": "bash", "args": {"command": "echo hi"}}),
    ], tmp_path)

    finals = [f for f in frames if "ok" in f]
    assert len(finals) == 1
