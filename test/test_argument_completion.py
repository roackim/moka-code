"""Argument completion shares the selector look and shows descriptions."""

from moka_chat.ui.commands.base import Command, Param
from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.components.input.completion import ArgumentCompletion
from moka_chat.ui.tui.components.menu import SelectionMenu


def _registry():
    return {
        "demo": Command("demo", "d", params=[
            Param("A", completions=["one", "two"], descriptions={"one": "first"}),
        ]),
    }


def test_argument_menu_matches_the_shared_selector_style():
    comp = ArgumentCompletion(SelectionMenu(), _registry())

    assert comp.menu.fill_width is True
    assert comp.menu.frame_color == theme.USER
    assert comp.menu.content_color == theme.DEFAULT


def test_argument_completion_passes_descriptions():
    comp = ArgumentCompletion(SelectionMenu(), _registry())

    comp.update("/demo ", len("/demo "))

    assert comp.is_active
    assert comp.menu.item_descriptions.get("one") == "first"


def test_sandbox_tree_completes_subcommands_then_nested_args(monkeypatch, tmp_path):
    import moka_chat.settings as settings
    from moka_chat.ui.commands.registry import COMMANDS

    monkeypatch.setattr(settings, "get_config_dir", lambda: tmp_path)
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    project = tmp_path / "projects" / "proj.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        '[sandboxes.dev]\ntype = "podman"\ndescription = "toolchain"\n'
        '[sandboxes.tight]\ntype = "bubblewrap"\n',
        encoding="utf-8",
    )

    comp = ArgumentCompletion(SelectionMenu(), COMMANDS)

    comp.update("/sandbox ", len("/sandbox "))
    assert set(comp.menu.items) == {"config", "build", "start", "stop", "terminal", "init"}

    comp.update("/sandbox init ", len("/sandbox init "))
    assert set(comp.menu.items) == {"podman", "docker"}

    comp.update("/sandbox start ", len("/sandbox start "))
    assert set(comp.menu.items) == {"dev", "tight"}
    assert comp.menu.item_descriptions.get("dev") == "toolchain"


def test_config_role_completion_uses_role_descriptions(monkeypatch):
    import moka_chat.ui.commands.core as core

    monkeypatch.setattr(core, "role_name_completions", lambda: ["agent", "chat"])
    monkeypatch.setattr(core, "role_descriptions", lambda: {"agent": "General agent"})

    cmd = core.ConfigCommand()

    assert cmd.get_completions(1, ("role",)) == ["agent", "chat"]
    assert cmd.get_descriptions(1, ("role",)) == {"agent": "General agent"}
    assert cmd.get_descriptions(1, ("ui",)) == {}
