"""The sandbox notice: what the model is told about where tools run, and when.

The notice is a ``system`` history entry (never the role system prompt, so the
sandbox can change without invalidating the provider's prompt cache). It carries
the sandbox's ``instructions`` and collapses consecutive changes.
"""

from moka_code.harness.harness import Harness


def _bare():
    harness = Harness.__new__(Harness)
    harness.history = []
    return harness


def test_notice_carries_the_sandbox_instructions():
    harness = _bare()

    harness._record_sandbox_change("bubblewrap", "No network. /workspace is the repo.")

    assert len(harness.history) == 1
    assert harness.history[0]["role"] == "system"
    assert harness.history[0]["content"] == (
        "[Sandbox: bubblewrap]\nNo network. /workspace is the repo.")


def test_bare_notice_has_no_trailing_blank():
    harness = _bare()

    harness._record_sandbox_change("podman", "")

    assert harness.history[0]["content"] == "[Sandbox: podman]"


def test_consecutive_changes_keep_only_the_last_notice():
    harness = _bare()

    harness._record_sandbox_change("bubblewrap", "first")
    harness._record_sandbox_change("none")

    assert len(harness.history) == 1
    assert harness.history[0]["content"] == "[Sandbox: none]"


def test_a_notice_after_other_messages_is_appended_not_collapsed():
    harness = _bare()

    harness._record_sandbox_change("bubblewrap", "first")
    harness._add_message_to_history("user", "hello")
    harness._record_sandbox_change("none")

    assert [m["role"] for m in harness.history] == ["system", "user", "system"]
    assert harness.history[-1]["content"] == "[Sandbox: none]"


def test_an_unchanged_sandbox_is_not_reannounced():
    harness = _bare()

    harness._record_sandbox_change("bubblewrap", "same")
    harness._add_message_to_history("user", "hello")

    harness._record_sandbox_change("bubblewrap", "same")     # e.g. /reload

    assert len(harness.history) == 2                         # no new notice


def test_changed_instructions_are_reannounced():
    harness = _bare()

    harness._record_sandbox_change("bubblewrap", "old")
    harness._add_message_to_history("user", "hello")

    harness._record_sandbox_change("bubblewrap", "new")

    assert harness.history[-1]["content"] == "[Sandbox: bubblewrap]\nnew"


def test_turning_a_sandbox_off_is_announced_once():
    harness = _bare()

    harness._record_sandbox_change("bubblewrap", "")
    harness._record_sandbox_change("none")
    harness._record_sandbox_change("none")                   # repeated stop

    assert len(harness.history) == 1
    assert harness.history[0]["content"] == "[Sandbox: none]"


def test_get_harness_announces_a_sandbox_active_at_startup(tmp_path, monkeypatch):
    """The notice also fires when the conversation *starts* in a sandbox, not
    only when the sandbox changes."""
    import moka_code.harness.harness as harness_module
    import moka_code.harness.roles as roles_module
    from moka_code import projects, settings

    monkeypatch.setattr(settings, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    workspace = tmp_path / "proj"
    workspace.mkdir()
    projects.create_sandbox(workspace, "local", "bubblewrap", "box")
    projects.set_active(workspace, "box")
    monkeypatch.setattr(harness_module, "_harness", None)

    harness = harness_module.get_harness(str(workspace))

    assert harness.history[0]["content"].startswith("[Sandbox: bubblewrap]")
