"""The workspace's AGENTS.md joins the system prompt; ``.agents/skills`` are
``!name`` calls and always in the ``@`` list."""

import pytest

from moka_code import prompts, settings
from moka_code.harness.harness import Harness


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(settings.CONFIG_DIR_ENV, str(tmp_path / "config"))
    work = tmp_path / "work"
    work.mkdir()
    return work


def _skill(workspace, folder, text):
    path = workspace / ".agents" / "skills" / folder / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


AUDIT = """---
name: audit
description: >
  Review code for defects,
  not a security audit.
---

# Code Audit

Read `checklist.md` first.
"""


def test_agents_md_follows_the_role_prompt(workspace):
    (workspace / "AGENTS.md").write_text("Run tests with pytest.\n")
    harness = Harness(workspace_path=str(workspace))
    system = harness._system_messages()[0]["content"]
    assert system.endswith("from AGENTS.md in the workspace root:\n\nRun tests with pytest.")
    assert system.startswith(harness.role.prompt.strip()[:20])


def test_agents_md_is_read_at_start_and_on_reload_only(workspace):
    harness = Harness(workspace_path=str(workspace))
    assert "AGENTS.md" not in str(harness._system_messages())
    (workspace / "AGENTS.md").write_text("Be terse.")
    assert "Be terse." not in str(harness._system_messages())     # no per-turn read
    harness.load_agents_md()
    assert "Be terse." in harness._system_messages()[0]["content"]


def test_an_unreadable_agents_md_is_an_error_not_silence(workspace):
    (workspace / "AGENTS.md").write_bytes(b"\xff\xfe\x00bad")
    harness = Harness(workspace_path=str(workspace))
    assert harness.agents_md == "" and "AGENTS.md not read" in harness.agents_md_error


def test_skills_are_bang_calls_described_by_their_frontmatter(workspace):
    _skill(workspace, "audit", AUDIT)
    _skill(workspace, "deep/nested/wiki", "---\ndescription: Update the wiki\n---\nBody")
    found = prompts.list_prompts(workspace)
    assert found["audit"].description == "Review code for defects, not a security audit."
    assert found["audit"].scope == prompts.SKILL
    assert found["wiki"].description == "Update the wiki"       # named after its folder


def test_a_skill_expands_to_its_file_without_frontmatter(workspace):
    _skill(workspace, "audit", AUDIT)
    expansion = prompts.expand("!audit the parser\nand the lexer", workspace)
    assert expansion.prompt.startswith(
        "Use the skill audit (.agents/skills/audit/SKILL.md; paths in it are relative")
    assert "# Code Audit" in expansion.prompt and "description:" not in expansion.prompt
    assert expansion.rest == "the parser\nand the lexer"
    assert expansion.typed == "!audit\n\nthe parser\nand the lexer"


def test_a_prompt_wins_over_a_skill_of_the_same_name(workspace):
    _skill(workspace, "audit", AUDIT)
    local = prompts.scope_dir("local", workspace)
    local.mkdir(parents=True)
    (local / "audit.md").write_text("# mine\nMy audit")
    assert prompts.list_prompts(workspace)["audit"].scope == "local"


def test_the_at_list_always_has_the_whole_skills_tree(workspace, monkeypatch):
    _skill(workspace, "a/b/c/d/e", "deep")
    (workspace / ".gitignore").write_text(".agents/\n")
    monkeypatch.setattr(settings.config, "context_max_depth", 1)
    monkeypatch.setattr(settings.config, "context_ignore_dotfiles", True)
    entries = Harness(workspace_path=str(workspace)).list_files_and_folders()
    assert ".agents/skills/a/b/c/d/e/SKILL.md" in entries
    assert ".agents/" in entries and len(entries) == len(set(entries))
