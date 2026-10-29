"""Reusable prompts: ``!name args`` expands ``<name>.md`` (global or ``.moka/prompts``)."""

import asyncio

import pytest

from moka_code import prompts, settings
from moka_code.harness import events
from moka_code.ui.app import chatTUI
from moka_code.ui.chat_message import unmention

from conftest import StubAgent


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(settings.CONFIG_DIR_ENV, str(tmp_path / "config"))
    work = tmp_path / "work"
    work.mkdir()
    return work


def _write(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(text)


def _glob(workspace):
    return prompts.scope_dir("global", workspace)


def _loc(workspace):
    return prompts.scope_dir("local", workspace)


# -- files ---------------------------------------------------------------------

def test_prompts_live_in_the_config_dir_and_the_workspace(workspace, tmp_path):
    assert _glob(workspace) == tmp_path / "config" / "prompts"
    assert _loc(workspace) == workspace / ".moka" / "prompts"


def test_a_local_prompt_wins_and_hidden_or_misnamed_files_are_ignored(workspace):
    _write(_glob(workspace), "review", "global review")
    _write(_loc(workspace), "review", "local review")
    _write(_glob(workspace), "_draft", "x")
    _write(_glob(workspace), "has space", "x")
    _write(_glob(workspace), "fix", "fix it")

    found = prompts.list_prompts(workspace)

    assert list(found) == ["fix", "review"]
    assert (found["review"].scope, found["review"].text) == ("local", "local review")
    assert found["fix"].scope == "global"


def test_the_description_is_the_first_non_empty_line_without_the_heading_mark(workspace):
    _write(_glob(workspace), "review", "\n\n# Review a file\n\nLook at {moka:file}.")

    assert prompts.list_prompts(workspace)["review"].description == "Review a file"


# -- parameters -------------------------------------------------------------------

def test_params_are_the_distinct_names_in_order_of_first_appearance():
    assert prompts.params_of(
        "Fix {moka:b} then {moka:a}, then {moka:b} again; {moka:1} {moka: c} {moka:}") == ["b", "a"]


def test_only_moka_params_are_replaced_and_every_other_brace_is_text():
    """2026-10-22: ``{name}`` occurred naturally (f-strings, JSON), so a
    parameter is ``{moka:name}`` and there is no brace escaping."""
    template = 'Return {"ok": true}, {{x}} and f"{file}" for {moka:file}'

    assert prompts.params_of(template) == ["file"]
    assert prompts.render(template, {"file": "a.py"}) == 'Return {"ok": true}, {{x}} and f"{file}" for a.py'


def test_the_last_param_takes_the_rest_and_quotes_group_earlier_ones():
    assert prompts.split_args('a.py focus on "error handling"', 2) == ["a.py", 'focus on "error handling"']
    assert prompts.split_args('"my file.py" go', 2) == ["my file.py", "go"]
    assert prompts.split_args('"all of it"', 1) == ["all of it"]
    assert prompts.split_args("one two three", 1) == ["one two three"]


# -- expanding ----------------------------------------------------------------------

def test_expand_fills_the_template(workspace):
    _write(_glob(workspace), "review", "Review {moka:file}, focusing on {moka:focus}.")

    expansion = prompts.expand("!review a.py error handling", workspace)
    assert expansion.text == "Review a.py, focusing on error handling."
    assert (expansion.name, expansion.call, expansion.rest) == (
        "review", "!review a.py error handling", "")


def test_a_hash_description_line_is_not_sent(workspace):
    """2026-10-29: the ``#`` description is the menu's, not the model's; a first
    line without ``#`` is content (it is still the description)."""
    _write(_glob(workspace), "review", "# Review a file {moka:x}\n\nReview {moka:file}.\n# Notes\nbe brief")
    _write(_glob(workspace), "plain", "Review the repo.\nThoroughly.")

    assert prompts.list_prompts(workspace)["review"].params == ["file"]
    assert prompts.expand("!review a.py", workspace).text == "Review a.py.\n# Notes\nbe brief"
    assert prompts.expand("!plain", workspace).text == "Review the repo.\nThoroughly."
    assert prompts.list_prompts(workspace)["plain"].description == "Review the repo."


def test_the_call_is_the_first_line_and_the_rest_follows_the_prompt(workspace):
    """2026-10-29: arguments end with the line; the lines after are the user's
    own text, sent after the prompt (a prompt without parameters takes them too)."""
    _write(_glob(workspace), "audit", "Audit {moka:file} thoroughly.")
    _write(_glob(workspace), "plain", "Review the repo.")

    audit = prompts.expand("!audit file.py\n\nand commit the report", workspace)
    assert audit.text == "Audit file.py thoroughly.\n\nand commit the report"
    assert audit.typed == "!audit file.py\n\nand commit the report"
    assert prompts.expand("!plain\nthen push", workspace).text == "Review the repo.\n\nthen push"

    restored = prompts.from_history(audit.text, audit.meta)
    assert restored == audit
    assert prompts.from_history(audit.text, None) is None


def test_text_that_is_not_a_call_is_left_alone(workspace):
    for text in ("hello", " !review x", "! review", "!!", "!"):
        assert prompts.expand(text, workspace) is None


@pytest.mark.parametrize("text,message", [
    ("!nope", "Unknown prompt: !nope"),
    ("!review a.py", "!review needs <file> <focus>; missing <focus>"),
    ("!review", "missing <file> <focus>"),
    ("!plain extra", "!plain takes no arguments"),
])
def test_expand_reports_what_is_wrong(workspace, text, message):
    _write(_glob(workspace), "review", "Review {moka:file}, focusing on {moka:focus}.")
    _write(_glob(workspace), "plain", "No parameters here.")

    with pytest.raises(prompts.PromptError, match=message.replace("<", r"\<").replace("!", "!")):
        prompts.expand(text, workspace)


def test_ensure_prompt_file_creates_a_starter_once_in_the_scope(workspace):
    path = prompts.ensure_prompt_file("review", "local", workspace)
    path.write_text("mine {x}")

    assert path == _loc(workspace) / "review.md"
    assert prompts.ensure_prompt_file("review", "local", workspace).read_text() == "mine {x}"
    with pytest.raises(ValueError):
        prompts.ensure_prompt_file("bad name", "global", workspace)


# -- in the app -----------------------------------------------------------------------

def _app(workspace, monkeypatch, sent):
    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    agent = StubAgent()
    agent.workspace = str(workspace)

    async def chat(text, attached=None, prompt=None):
        sent.append(text)
        agent.prompts.append(prompt)
        yield events.Done()

    agent.prompts = []

    agent.chat = chat
    return chatTUI(agent)


def test_submitting_a_call_sends_and_shows_the_expansion_and_edit_restores_the_call(workspace, monkeypatch):
    _write(_glob(workspace), "review", "Review {moka:file}, focusing on {moka:focus}.")
    sent = []

    async def scenario():
        ui = _app(workspace, monkeypatch, sent)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("!review @src/app.py tests")
        await asyncio.sleep(0.1)
        ui.worker_task.cancel()
        return ui

    ui = asyncio.run(scenario())

    assert sent == ["Review src/app.py, focusing on tests."]
    assert ui.agent.prompts == [{"call": "!review @src/app.py tests", "chars": 37}]
    message = next(m for m in ui.chat_history_panel.messages if m.base_text)
    assert message.copy_text == "Review src/app.py, focusing on tests."
    assert message.draft[0] == "!review @src/app.py tests"


def test_a_prompt_message_folds_to_its_call_and_o_toggles_it(workspace, monkeypatch):
    _write(_glob(workspace), "audit", "# Audit\n\nAudit {moka:file} thoroughly.")
    sent = []

    async def scenario():
        ui = _app(workspace, monkeypatch, sent)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("!audit file.py\n\nand commit the report")
        await asyncio.sleep(0.1)
        ui.worker_task.cancel()
        return ui

    ui = asyncio.run(scenario())
    message = next(m for m in ui.chat_history_panel.messages if m.prompt is not None)

    assert sent == ["Audit file.py thoroughly.\n\nand commit the report"]
    assert message.base_text == "!audit file.py\n── audit · 1 line ──\nand commit the report"
    assert "o" in [action.key for action in message.get_active_actions()]

    ui.handle_prompt_action(message)
    assert message.base_text == ("!audit file.py\n── audit · 1 line ──\n"
                                 "Audit file.py thoroughly.\n──\nand commit the report")
    ui.handle_prompt_action(message)
    assert message.prompt_folded is True
    assert message.copy_text == sent[0]


def test_a_template_with_an_at_sign_is_not_mangled(workspace, monkeypatch):
    _write(_glob(workspace), "deco", "Explain @property on {moka:topic}")
    sent = []

    async def scenario():
        ui = _app(workspace, monkeypatch, sent)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("!deco cache")
        await asyncio.sleep(0.1)
        ui.worker_task.cancel()

    asyncio.run(scenario())

    assert sent == ["Explain @property on cache"]


def test_an_unknown_call_is_an_error_and_keeps_the_text(workspace, monkeypatch):
    sent = []
    ui = _app(workspace, monkeypatch, sent)

    result = ui.on_user_submit("!reveiw a.py")

    assert result is False
    assert sent == []
    assert any("Unknown prompt: !reveiw" in line for line in ui.activity_panel.lines)


def test_a_leading_space_sends_it_literally(workspace, monkeypatch):
    sent = []

    async def scenario():
        ui = _app(workspace, monkeypatch, sent)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit(" !important: read this")
        await asyncio.sleep(0.1)
        ui.worker_task.cancel()

    asyncio.run(scenario())

    assert sent == [unmention(" !important: read this")]


# -- completion and hints ------------------------------------------------------------

def test_the_bang_menu_lists_prompts_with_descriptions_and_scope(workspace):
    from moka_code.ui.tui.components.input.completion import CommandCompletion
    from moka_code.ui.tui.components.menu import SelectionMenu

    _write(_glob(workspace), "review", "# Review a file\n{moka:file}")
    _write(_loc(workspace), "fix", "# Fix a bug\n{moka:bug}")
    names = lambda: list(prompts.list_prompts(workspace))
    describe = lambda: {n: f"{p.description} [{p.scope}]"
                        for n, p in prompts.list_prompts(workspace).items()}
    comp = CommandCompletion(SelectionMenu(), names, describe, trigger="!")

    comp.update("!", 1)

    assert comp.is_active and set(comp.menu.items) == {"fix", "review"}
    assert comp.menu.item_descriptions["fix"] == "Fix a bug [local]"
    comp.update("/", 1)                       # the slash trigger is not this menu's
    assert not comp.is_active
    comp.update("!review", 7)                 # complete: nothing to complete
    assert not comp.is_active


def test_the_hint_after_the_name_lists_the_parameters_still_to_type(workspace, monkeypatch):
    from moka_code.ui.tui.components.input.input import InputComponent

    _write(_glob(workspace), "review", "{moka:file} {moka:focus}")
    entry = InputComponent("", id="entry")
    entry.setup_prompts(lambda: [], lambda: {},
                        lambda name: prompts.list_prompts(workspace)[name].params
                        if name in prompts.list_prompts(workspace) else [])

    def hint(text):
        entry.buffer.text = text
        return entry._get_parameter_hint()

    assert hint("!review") == ""                     # still typing the name
    assert hint("!review ") == " <file> <focus>"
    assert hint("!review a.py") == " <focus>"        # typing the first one
    assert hint("!review a.py ") == " <focus>"
    assert hint("!review a.py tests") == ""          # the last one takes the rest
    assert hint("!ghost ") == ""


# -- /prompt ----------------------------------------------------------------------------

def _prompt_ui(workspace, monkeypatch, picker=False):
    from types import SimpleNamespace

    class _Panel:
        def __init__(self):
            self.messages = []

        def add_message(self, text, **kw):
            self.messages.append(text)

    opened = []

    async def _open(_ui, path):
        opened.append(path)
        return True

    monkeypatch.setenv("EDITOR", "my-editor")
    monkeypatch.setattr("moka_code.ui.external_editor.open_editor", _open)
    ui = SimpleNamespace(agent=SimpleNamespace(workspace=str(workspace)),
                         chat_history_panel=_Panel())
    if picker:
        ui.picked = {}
        ui.show_search_modal = lambda title, items, **k: ui.picked.update(title=title, items=items, **k)
    return ui, opened


def test_prompt_create_is_global_by_default_local_on_request_and_edit_opens_where_it_lives(workspace, monkeypatch):
    from moka_code.ui.commands.registry import COMMANDS

    ui, opened = _prompt_ui(workspace, monkeypatch)

    asyncio.run(COMMANDS["prompt"].execute(ui, ["create", "review"]))
    asyncio.run(COMMANDS["prompt"].execute(ui, ["create", "fix", "local"]))
    asyncio.run(COMMANDS["prompt"].execute(ui, ["edit", "fix"]))   # exists locally: opens that one

    assert opened == [_glob(workspace) / "review.md", _loc(workspace) / "fix.md", _loc(workspace) / "fix.md"]
    assert (_glob(workspace) / "review.md").exists()


def test_prompt_edit_never_creates_and_create_refuses_one_in_the_same_scope(workspace, monkeypatch):
    """2026-10-22: a typo no longer writes a file; a local prompt may still
    shadow a global one."""
    from moka_code.ui.commands.registry import COMMANDS

    ui, opened = _prompt_ui(workspace, monkeypatch)
    _write(_glob(workspace), "review", "# Review\n")

    asyncio.run(COMMANDS["prompt"].execute(ui, ["edit", "reveiw"]))
    assert not (_glob(workspace) / "reveiw.md").exists()
    assert "/prompt create reveiw" in ui.chat_history_panel.messages[-1]

    asyncio.run(COMMANDS["prompt"].execute(ui, ["create", "review"]))
    assert "already exists (global)" in ui.chat_history_panel.messages[-1]

    asyncio.run(COMMANDS["prompt"].execute(ui, ["create", "review", "local"]))
    assert opened == [_loc(workspace) / "review.md"]


def test_prompt_path_and_remove_act_on_the_file_the_prompt_resolves_to(workspace, monkeypatch):
    from moka_code.ui.commands.registry import COMMANDS

    _write(_glob(workspace), "review", "# Global\n")
    _write(_loc(workspace), "review", "# Local\n")
    ui, _ = _prompt_ui(workspace, monkeypatch, picker=True)
    monkeypatch.setattr("moka_code.ui.clipboard.copy_to_clipboard", lambda text: None)

    asyncio.run(COMMANDS["prompt"].execute(ui, ["path", "review"]))
    assert str(_loc(workspace) / "review.md") in ui.chat_history_panel.messages[-1]

    asyncio.run(COMMANDS["prompt"].execute(ui, ["remove", "review"]))
    assert ui.picked["items"] == ["Remove", "Cancel"]
    ui.picked["on_accept"]("Remove")
    assert not (_loc(workspace) / "review.md").exists()
    assert (_glob(workspace) / "review.md").exists()
    assert "the global one is used now" in ui.chat_history_panel.messages[-1]


def test_bare_prompt_opens_a_picker_of_the_prompts_and_choosing_opens_the_file(workspace, monkeypatch):
    from moka_code.ui.commands.registry import COMMANDS

    _write(_glob(workspace), "review", "# Review a file\n{moka:file}")
    ui, opened = _prompt_ui(workspace, monkeypatch, picker=True)

    async def scenario():
        await COMMANDS["prompt"].execute(ui, [])
        assert ui.picked["title"] == "Prompts" and ui.picked["items"] == ["review"]
        assert ui.picked["descriptions"] == {"review": "Review a file [global]"}
        ui.picked["on_accept"]("review")
        await asyncio.sleep(0.05)

    asyncio.run(scenario())

    assert opened == [_glob(workspace) / "review.md"]


def test_bare_prompt_with_none_says_how_to_make_one_and_bad_arguments_show_the_usage(workspace, monkeypatch):
    from moka_code.ui.commands.registry import COMMANDS

    ui, opened = _prompt_ui(workspace, monkeypatch)

    asyncio.run(COMMANDS["prompt"].execute(ui, []))
    assert "No prompts yet. /prompt create <name> makes one" in ui.chat_history_panel.messages[-1]

    asyncio.run(COMMANDS["prompt"].execute(ui, ["create", "review", "nowhere"]))
    assert ui.chat_history_panel.messages[-1] == "Usage: /prompt create <name> [global|local]"

    asyncio.run(COMMANDS["prompt"].execute(ui, ["review"]))
    assert ui.chat_history_panel.messages[-1].startswith("Unknown subcommand 'review'")
    assert opened == []
