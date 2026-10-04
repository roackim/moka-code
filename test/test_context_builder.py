"""
Tests for context_builder guardrails.

Verifies that:
- list_files_bounded() respects max_files / max_depth and .gitignore
"""
import pytest
from pathlib import Path
from moka_code.harness.context_builder import (
    list_files_bounded,
)


class TestListFilesBounded:
    def test_lists_files_and_dirs(self, tmp_path):
        """Should list files and directories with trailing slash on dirs."""
        (tmp_path / 'a.py').write_text("")
        (tmp_path / 'src').mkdir()
        (tmp_path / 'src' / 'b.py').write_text("")
        entries = list_files_bounded(str(tmp_path))
        assert 'a.py' in entries
        assert 'src/' in entries
        assert 'src/b.py' in entries

    def test_respects_max_files(self, tmp_path):
        """Should stop listing once max_files is reached."""
        for i in range(20):
            (tmp_path / f'f{i}.py').write_text("")
        entries = list_files_bounded(str(tmp_path), max_files=5)
        assert len(entries) == 5

    def test_respects_max_depth(self, tmp_path):
        """Should not descend past max_depth."""
        deep = tmp_path / 'a' / 'b' / 'c' / 'd'
        deep.mkdir(parents=True)
        (deep / 'x.py').write_text("")
        entries = list_files_bounded(str(tmp_path), max_depth=2)
        # a/, a/b/, a/b/c/ are listed, but we never descend into a/b/c/.
        assert 'a/' in entries
        assert 'a/b/' in entries
        assert 'a/b/c/' in entries
        assert 'a/b/c/d/' not in entries
        assert 'a/b/c/d/x.py' not in entries

    def test_prunes_dot_folders(self, tmp_path):
        """Should skip dot-folders like .git and .venv."""
        (tmp_path / '.git').mkdir()
        (tmp_path / '.venv').mkdir()
        (tmp_path / 'ok.py').write_text("")
        entries = list_files_bounded(str(tmp_path))
        assert 'ok.py' in entries
        assert not any(e.startswith('.') for e in entries)

    def test_respects_gitignore(self, tmp_path):
        """Should skip gitignored files by default."""
        (tmp_path / '.gitignore').write_text("node_modules/\nbuild/\n")
        (tmp_path / 'node_modules').mkdir()
        (tmp_path / 'node_modules' / 'x.js').write_text("")
        (tmp_path / 'build').mkdir()
        (tmp_path / 'build' / 'out.bin').write_text("")
        (tmp_path / 'main.py').write_text("")
        entries = list_files_bounded(str(tmp_path))
        assert 'main.py' in entries
        assert 'node_modules/' not in entries
        assert 'build/' not in entries

    def test_ignore_gitignore_flag(self, tmp_path):
        """ignore_gitignore=True should list gitignored files too."""
        (tmp_path / '.gitignore').write_text("build/\n")
        (tmp_path / 'build').mkdir()
        (tmp_path / 'build' / 'out.bin').write_text("")
        (tmp_path / 'main.py').write_text("")
        entries = list_files_bounded(str(tmp_path), ignore_gitignore=True)
        assert 'main.py' in entries
        assert 'build/' in entries
