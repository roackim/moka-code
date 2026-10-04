"""Tests for the stdlib-only tool bodies in :mod:`moka_code.worker`."""

import asyncio

import pytest

from moka_code.worker import (
    ToolError,
    bash,
    bash_sync,
    edit,
    read,
    write,
)


def _run(coro):
    return asyncio.run(coro)


# --- read ------------------------------------------------------------------

def test_read_offset_limit_and_line_numbers(tmp_path):
    (tmp_path / "f.txt").write_text("one\ntwo\nthree\nfour\n")

    assert read("f.txt", cwd=tmp_path, offset=1, limit=2) == "two\nthree\n"
    assert read(
        "f.txt", cwd=tmp_path, offset=1, limit=2, include_line_numbers=True
    ) == "     2\ttwo\n     3\tthree\n"


def test_read_rejects_invalid_offset(tmp_path):
    (tmp_path / "f.txt").write_text("content")

    with pytest.raises(ToolError, match="Invalid offset"):
        read("f.txt", cwd=tmp_path, offset=-1)


def test_read_marks_character_truncation(tmp_path):
    (tmp_path / "f.txt").write_text("abcdefgh")

    result = read("f.txt", cwd=tmp_path, max_chars=3)

    assert result.startswith("abc\n[truncated:")


def test_read_supports_absolute_paths(tmp_path):
    (tmp_path / "f.txt").write_text("abs")

    assert read(str(tmp_path / "f.txt"), cwd="/") == "abs"


# --- write -----------------------------------------------------------------

def test_write_creates_parents(tmp_path):
    message = write("nested/f.txt", "data", cwd=tmp_path)

    assert "[OK]" in message
    assert (tmp_path / "nested" / "f.txt").read_text() == "data"


# --- edit ------------------------------------------------------------------

def test_edit_round_trip(tmp_path):
    (tmp_path / "f.py").write_text("def foo():\n    pass\n")

    message = edit("f.py", "    pass", "    return 42", cwd=tmp_path)

    assert message.startswith("[OK]")
    assert "return 42" in (tmp_path / "f.py").read_text()


def test_edit_reports_missing_block(tmp_path):
    (tmp_path / "f.py").write_text("hello\n")

    message = edit("f.py", "nope", "yes", cwd=tmp_path)

    assert message.startswith("[ERROR]")


def test_edit_rejects_oversized_replacement(tmp_path):
    (tmp_path / "f.py").write_text("old\n")

    with pytest.raises(ToolError, match="too large"):
        edit("f.py", "old", "x" * 100_001, cwd=tmp_path)


# --- bash ------------------------------------------------------------------

def test_bash_formats_output(tmp_path):
    out = _run(bash("echo hi", cwd=tmp_path))

    assert "hi" in out
    assert "[exit:0]" in out


def test_bash_sync_formats_output(tmp_path):
    assert "hi" in bash_sync("echo hi", cwd=tmp_path)


def test_bash_timeout_cleans_up(tmp_path):
    with pytest.raises(ToolError, match="timed out"):
        _run(bash("sleep 30", cwd=tmp_path, timeout=1))


def test_bash_keeps_the_output_written_just_before_exit(tmp_path):
    """The output pumps must drain after the process exits: with many commands
    in flight, buffered output used to be dropped (ISSUES H6). Only fails on a
    loaded machine; run it under CPU load to see the old behaviour."""
    async def many():
        return await asyncio.gather(*[
            bash("printf 'a\\nb\\nc\\n'; echo err >&2", cwd=tmp_path) for _ in range(150)
        ])

    for result in _run(many()):
        assert "a\nb\nc" in result and "err" in result

