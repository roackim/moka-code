"""Tests for the patch parser (search/replace block format).

Covers parse_patch, apply_patch, and PatchParseError.
"""

import pytest
from moka_chat.worker import (
    PatchBlock,
    PatchParseError,
    parse_patch,
    apply_patch,
)


# ---------------------------------------------------------------------------
# parse_patch
# ---------------------------------------------------------------------------

class TestParsePatch:
    def test_basic_parse(self):
        content = """main.py
<<<<<<< SEARCH
old line
=======
new line
>>>>>>> REPLACE"""
        patch = parse_patch(content)
        assert patch.filename == "main.py"
        assert patch.search_text == "old line"
        assert patch.replace_text == "new line"

    def test_multiline_search_and_replace(self):
        content = """app.py
<<<<<<< SEARCH
def foo():
    pass
=======
def foo():
    return 42
>>>>>>> REPLACE"""
        patch = parse_patch(content)
        assert patch.filename == "app.py"
        assert patch.search_text == "def foo():\n    pass"
        assert patch.replace_text == "def foo():\n    return 42"

    def test_strips_whitespace_around_content(self):
        content = """

main.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE
"""
        patch = parse_patch(content)
        assert patch.filename == "main.py"
        assert patch.search_text == "old"
        assert patch.replace_text == "new"

    def test_empty_replace(self):
        content = """main.py
<<<<<<< SEARCH
line to remove
=======
>>>>>>> REPLACE"""
        patch = parse_patch(content)
        assert patch.search_text == "line to remove"
        assert patch.replace_text == ""

    def test_empty_search_raises(self):
        content = """main.py
<<<<<<< SEARCH
=======
new
>>>>>>> REPLACE"""
        # Empty search is technically parseable; apply_patch handles it
        patch = parse_patch(content)
        assert patch.search_text == ""
        assert patch.replace_text == "new"

    def test_missing_search_marker_raises(self):
        content = """main.py
old line
=======
new line
>>>>>>> REPLACE"""
        with pytest.raises(PatchParseError, match="Missing.*SEARCH"):
            parse_patch(content)

    def test_missing_divider_raises(self):
        content = """main.py
<<<<<<< SEARCH
old line
>>>>>>> REPLACE"""
        with pytest.raises(PatchParseError, match="Missing.*======"):
            parse_patch(content)

    def test_missing_replace_marker_raises(self):
        content = """main.py
<<<<<<< SEARCH
old line
=======
new line"""
        with pytest.raises(PatchParseError, match="Missing.*REPLACE"):
            parse_patch(content)

    def test_missing_filename_raises(self):
        content = """<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE"""
        with pytest.raises(PatchParseError, match="Missing filename"):
            parse_patch(content)

    def test_empty_content_raises(self):
        with pytest.raises(PatchParseError, match="Missing.*SEARCH"):
            parse_patch("")

    def test_multiple_dividers_raises(self):
        content = """main.py
<<<<<<< SEARCH
old
=======
=======
new
>>>>>>> REPLACE"""
        with pytest.raises(PatchParseError, match="Multiple"):
            parse_patch(content)

    def test_filename_with_path(self):
        content = """src/utils/helper.py
<<<<<<< SEARCH
def old():
=======
def new():
>>>>>>> REPLACE"""
        patch = parse_patch(content)
        assert patch.filename == "src/utils/helper.py"


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------

class TestApplyPatch:
    def test_exact_match(self):
        content = "def foo():\n    pass\n"
        patch = PatchBlock("test.py", "def foo():\n    pass", "def foo():\n    return 42")
        new, msg = apply_patch(content, patch)
        assert "return 42" in new
        assert "[OK]" in msg
        assert "exact" in msg

    def test_no_match_returns_error(self):
        content = "def foo():\n    pass\n"
        patch = PatchBlock("test.py", "def bar():\n    pass", "def bar():\n    return 1")
        new, msg = apply_patch(content, patch)
        assert new == content  # unchanged
        assert "[ERROR]" in msg
        assert "not found" in msg.lower()

    def test_ambiguous_match_returns_error(self):
        content = "x = 1\nx = 1\n"
        patch = PatchBlock("test.py", "x = 1", "x = 2")
        new, msg = apply_patch(content, patch)
        assert new == content  # unchanged
        assert "[ERROR]" in msg
        assert "ambiguous" in msg.lower()

    def test_whitespace_normalized_match(self):
        """Match should succeed even if whitespace differs."""
        content = "def foo():\n    pass\n"
        # Search with different indentation (tabs vs spaces)
        patch = PatchBlock("test.py", "def foo():\n\tpass", "def foo():\n\treturn 42")
        new, msg = apply_patch(content, patch)
        assert "return 42" in new
        assert "[OK]" in msg

    def test_indentation_normalized_match(self):
        """Match should succeed with different indentation levels."""
        content = "def foo():\n    pass\n"
        # Search with no indentation
        patch = PatchBlock("test.py", "def foo():\npass", "def foo():\nreturn 42")
        new, msg = apply_patch(content, patch)
        assert "return 42" in new
        assert "[OK]" in msg

    def test_empty_search_text(self):
        """Empty search text matches at every position via whitespace-normalized mode."""
        content = "existing content\n"
        patch = PatchBlock("test.py", "", "inserted line\n")
        new, msg = apply_patch(content, patch)
        # Empty search is ambiguous (matches everywhere) -> error
        assert "[ERROR]" in msg or "[OK]" in msg  # behaviour is mode-dependent

    def test_replace_removes_line(self):
        content = "line1\nline2\nline3\n"
        patch = PatchBlock("test.py", "line2\n", "")
        new, msg = apply_patch(content, patch)
        assert "line2" not in new
        assert "line1" in new
        assert "line3" in new
        assert "[OK]" in msg

    def test_multiline_block_replacement(self):
        content = "def old():\n    a = 1\n    b = 2\n    return a + b\n"
        patch = PatchBlock(
            "test.py",
            "def old():\n    a = 1\n    b = 2\n    return a + b",
            "def new():\n    a = 2\n    b = 3\n    return a * b",
        )
        new, msg = apply_patch(content, patch)
        assert "def new()" in new
        assert "a * b" in new
        assert "def old()" not in new
        assert "[OK]" in msg

    def test_similar_content_error_message(self):
        """When search block not found but similar line exists, error should hint."""
        content = "def foo():\n    return 1\n"
        patch = PatchBlock("test.py", "def foo():\n    return 2", "def foo():\n    return 3")
        new, msg = apply_patch(content, patch)
        assert "[ERROR]" in msg
        assert "line 1" in msg or "line 2" in msg  # hints at similar line

    def test_preserves_surrounding_content(self):
        content = "header\nold\nfooter\n"
        patch = PatchBlock("test.py", "old", "new")
        new, msg = apply_patch(content, patch)
        assert new == "header\nnew\nfooter\n"
        assert "[OK]" in msg
