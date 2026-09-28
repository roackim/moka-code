"""
Tests for context_builder guardrails.

Verifies that:
- is_git_repo() correctly detects git repositories
- build_harness_context() builds the file tree even when not in a git repo
- list_files_bounded() respects max_files / max_depth and .gitignore
"""
import pytest
from pathlib import Path
from moka_code.harness.context_builder import (
    is_git_repo,
    build_harness_context,
    list_files_bounded,
)


class TestIsGitRepo:
    def test_detects_git_repo(self, tmp_path):
        """Directory containing .git should be detected as a git repo."""
        (tmp_path / '.git').mkdir()
        assert is_git_repo(tmp_path) is True

    def test_detects_git_repo_in_parent(self, tmp_path):
        """Subdirectory of a git repo should also be detected."""
        (tmp_path / '.git').mkdir()
        subdir = tmp_path / 'src' / 'module'
        subdir.mkdir(parents=True)
        assert is_git_repo(subdir) is True

    def test_non_git_directory(self, tmp_path):
        """Plain directory with no .git anywhere should return False."""
        # tmp_path is under /tmp which should not be a git repo
        # Use a deeply nested path to avoid any accidental .git hits
        subdir = tmp_path / 'a' / 'b' / 'c'
        subdir.mkdir(parents=True)
        assert is_git_repo(subdir) is False


class TestBuildHarnessContext:
    def test_builds_tree_when_not_git_repo(self, tmp_path):
        """Should build the file list even when not in a git repo."""
        (tmp_path / 'file.py').write_text("# hello")
        result = build_harness_context(str(tmp_path), format="flat")
        assert "[WARNING" not in result
        assert "file.py" in result

    def test_builds_tree_format_when_not_git_repo(self, tmp_path):
        """Should build the tree format even when not in a git repo."""
        (tmp_path / 'file.py').write_text("# hello")
        result = build_harness_context(str(tmp_path), format="tree")
        assert "[WARNING" not in result
        assert "file.py" in result

    def test_builds_tree_in_git_repo(self, tmp_path):
        """Should build file list normally when inside a git repo."""
        (tmp_path / '.git').mkdir()
        (tmp_path / 'hello.py').write_text("# hello")
        result = build_harness_context(str(tmp_path), format="flat")
        assert "[WARNING" not in result
        assert "hello.py" in result

    def test_builds_tree_format_in_git_repo(self, tmp_path):
        """Should build tree output normally when inside a git repo."""
        (tmp_path / '.git').mkdir()
        (tmp_path / 'hello.py').write_text("# hello")
        result = build_harness_context(str(tmp_path), format="tree")
        assert "[WARNING" not in result
        assert "hello.py" in result

    def test_result_includes_project_root(self, tmp_path):
        """Project Root line should always be present."""
        result = build_harness_context(str(tmp_path), format="flat")
        assert f"Project Root: {tmp_path}" in result


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
