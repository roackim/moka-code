"""One behaviour for every command that takes a choice (2026-10-01, the user's
standing request for consistent command UX).

* ``/cmd `` shows suggestions from the parameter's ``completions`` (with
  descriptions), so every parameter has them unless it is free text.
* A bare ``/cmd`` that selects among values opens a picker over the same values
  (``base.pick``); ``/cmd <value>`` selects directly.
"""

import asyncio
from types import SimpleNamespace

from moka_code.harness.roles import Role
from moka_code.ui.commands.registry import COMMANDS

# Parameters that take free text (a new file or sandbox name): the only ones without
# suggestions. Adding a parameter without completions fails until it is
# listed here on purpose.
FREE_TEXT = {("export", "FILENAME"), ("create", "NAME"), ("copy", "NEW_NAME")}


def _leaves(command, path=()):
    if command.has_subcommands():
        for name, sub in command.subcommands.items():
            yield from _leaves(sub, path + (name,))
    else:
        yield path, command


def test_every_parameter_has_suggestions_unless_it_is_free_text():
    missing = []
    for name, root in COMMANDS.items():
        for path, leaf in _leaves(root, (name,)):
            for param in leaf.params:
                if not (param.completions or param.path) and (path[-1], param.name) not in FREE_TEXT:
                    missing.append(f"/{' '.join(path)} {param.name}")
    assert missing == []


class _UI:
    def __init__(self, with_modal):
        self.agent = SimpleNamespace(role=Role(name="agent"))
        self.messages, self.popups, self.modal, self.switched = [], [], None, []
        self.chat_history_panel = SimpleNamespace(
            add_message=lambda text, **k: self.messages.append(text))
        if with_modal:
            self.show_search_modal = self._modal

    def _modal(self, title, items, descriptions=None, footers=None, on_accept=None, **k):
        self.modal = {"title": title, "items": list(items), "descriptions": descriptions,
                      "footers": footers, "on_accept": on_accept}
        return object()

    def show_popup(self, title, text, **k):
        self.popups.append(text)

    def switch_role(self, role):
        self.switched.append(role.name)


def test_role_select_opens_a_picker_and_a_name_switches_directly(monkeypatch, tmp_path):
    import moka_code.harness.roles as roles_module

    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    roles_module.ensure_roles_dir()
    ui = _UI(with_modal=True)

    asyncio.run(COMMANDS["role"].execute(ui, ["select"]))

    assert ui.modal["title"] == "Roles" and ui.modal["items"] == ["agent", "chat"]
    assert ui.modal["footers"] == {"agent": "active"}
    assert ui.modal["descriptions"]["chat"] == "Pure chat (no tools)"
    assert not ui.popups
    ui.modal["on_accept"]("chat")
    asyncio.run(COMMANDS["role"].execute(ui, ["select", "agent"]))
    assert ui.switched == ["chat", "agent"]


def test_role_select_without_a_compositor_lists_them_as_text(monkeypatch, tmp_path):
    import moka_code.harness.roles as roles_module

    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    roles_module.ensure_roles_dir()
    ui = _UI(with_modal=False)

    asyncio.run(COMMANDS["role"].execute(ui, ["select"]))

    assert "active: agent" in ui.popups[-1] and "chat" in ui.popups[-1]


def test_bare_role_and_theme_list_their_subcommands_and_a_name_is_not_one():
    """2026-10-26: role names beside the verbs got mixed up with them, so
    switching is ``/role select <name>`` and ``/role <name>`` is an error."""
    for name in ("role", "theme"):
        ui = _UI(with_modal=True)
        asyncio.run(COMMANDS[name].execute(ui, []))
        assert "select" in ui.popups[-1] and "remove" in ui.popups[-1]
        assert ui.modal is None

        asyncio.run(COMMANDS[name].execute(ui, ["agent"]))
        assert "Unknown subcommand 'agent'" in ui.messages[-1]
        assert ui.switched == []


def _all_commands(command=None, path=()):
    for name, root in ((COMMANDS.items()) if command is None else command.subcommands.items()):
        yield path + (name,), root
        yield from _all_commands(root, path + (name,))


def test_every_command_is_a_plain_command_described_by_its_schema():
    """Completion, descriptions and hints all read ``subcommands`` and ``params``.
    A subclass that answers some other way (``ConfigCommand`` once did) leaves the
    schema, and so the hint, blind to it."""
    from moka_code.ui.commands.base import Command

    subclasses = [" ".join(path) for path, command in _all_commands() if type(command) is not Command]
    assert subclasses == []


def test_every_config_file_is_a_config_subcommand():
    from moka_code import settings

    assert set(settings.CONFIG_FILES) <= set(COMMANDS["config"].subcommands)


def test_config_arguments_are_hinted_like_sandbox_ones():
    from moka_code.ui.tui.components.input.input import InputComponent

    entry = InputComponent("", id="entry")
    entry.setup_command_registry(COMMANDS)

    def hint(text):
        entry.buffer.text = text
        return entry._get_parameter_hint()

    assert hint("/config ") == " <subcommand>"
    assert hint("/prompt create ") == " <NAME> [SCOPE]"
    assert hint("/prompt create review ") == " [SCOPE]"
    assert hint("/role select ") == " [NAME]"
    assert hint("/role edit ") == " [NAME]"
    assert hint("/role remove ") == " <NAME>"
    assert hint("/sandbox create ") == " <NAME> <TYPE> [SCOPE]"
    assert hint("/theme select ") == " [NAME]"
    assert hint("/theme remove ") == " <NAME>"
    assert hint("/config ui ") == ""
