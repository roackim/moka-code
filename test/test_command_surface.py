"""Command surface: top-level /import & /export, leaf /model, descriptions."""

from moka_code.ui.commands import COMMANDS, get_command_descriptions


def test_export_and_import_are_top_level():
    assert "export" in COMMANDS
    assert "import" in COMMANDS
    # The old /conversation tree is gone.
    assert "conversation" not in COMMANDS


def test_export_import_take_a_filename():
    assert COMMANDS["export"].params[0].name == "FILENAME"
    assert COMMANDS["import"].params[0].name == "FILENAME"
    # Import offers .json completion; export does not require it.
    assert COMMANDS["import"].params[0].completions is not None


def test_model_is_a_leaf_handler():
    model = COMMANDS["model"]
    assert not model.has_subcommands()
    assert model.handler is not None
    assert [p.name for p in model.params] == ["MODEL"]


def test_config_command_completes_role_names():
    config = COMMANDS["config"]

    sections = config.get_completions(0)
    assert "role" in sections
    assert "ui" in sections

    names = config.get_completions(1, ("role",))
    assert "agent" in names
    assert "chat" in names

    # A non-role section must not offer role names.
    assert config.get_completions(1, ("ui",)) == []


def test_config_command_completes_theme_names():
    config = COMMANDS["config"]

    names = config.get_completions(1, ("theme",))
    assert "terminal" in names
    assert "nord" in names

    assert config.get_descriptions(1, ("theme",))["terminal"] == "built-in"


def test_every_command_has_a_description():
    descriptions = get_command_descriptions()
    assert set(descriptions) == set(COMMANDS)
    assert all(descriptions.values())


def test_build_rows_marks_active_and_aligns_description():
    from moka_code.harness.endpoint import ModelInfo
    from moka_code.ui.commands.models import _build_rows

    pairs = [
        ("local", ModelInfo(id="qwen", context_window=32768)),
        ("openrouter", ModelInfo(id="deepseek/x", context_window=1310720)),
    ]
    items, descriptions, footers, index = _build_rows(pairs, "local", "qwen")

    assert items[0] == "qwen"
    assert items[1] == "deepseek/x"
    assert index[items[0]] == ("local", "qwen")
    assert "local" in descriptions[items[0]]
    assert "32k" in descriptions[items[0]]
    assert footers.get(items[0]) == "active"
    assert items[1] not in footers
    assert "1.3M" in descriptions[items[1]]
    # Server column starts at the same offset in every description.
    assert descriptions[items[0]].index("local") == descriptions[items[1]].index("openrouter")


def test_build_rows_disambiguates_duplicate_model_ids():
    from moka_code.harness.endpoint import ModelInfo
    from moka_code.ui.commands.models import _build_rows

    pairs = [
        ("a", ModelInfo(id="qwen", context_window=32768)),
        ("b", ModelInfo(id="qwen", context_window=32768)),
    ]
    items, descriptions, _footers, index = _build_rows(pairs, None, None)

    assert len(set(items)) == 2
    assert index[items[0]][0] == "a"
    assert index[items[1]][0] == "b"


def test_open_picker_shows_cached_catalog_without_network(monkeypatch):
    """The picker opens from the cached catalog immediately (no blocking)."""
    import asyncio
    from types import SimpleNamespace

    import moka_code.ui.commands.models as models
    from moka_code.harness.endpoint import ModelInfo

    pairs = [
        ("local", ModelInfo(id="qwen2.5:7b", context_window=32768)),
        ("openrouter", ModelInfo(id="deepseek/x", context_window=1310720)),
    ]
    monkeypatch.setattr(models, "_cached_pairs", lambda: pairs)

    scheduled = []
    monkeypatch.setattr(models.asyncio, "ensure_future", lambda coro: scheduled.append(coro))

    class _Panel:
        def __init__(self):
            self.messages = []

        def add_message(self, *a, **k):
            self.messages.append(a[0] if a else "")

    ui = SimpleNamespace(
        agent=SimpleNamespace(endpoint=SimpleNamespace(name="local", selected_model="qwen2.5:7b")),
        chat_history_panel=_Panel(),
    )
    captured = {}

    def show_search_modal(title, items, descriptions=None, footers=None,
                          on_accept=None, on_cancel=None, initial_index=0):
        captured.update(title=title, items=items, descriptions=descriptions,
                        footers=footers, on_accept=on_accept,
                        initial_index=initial_index)
        return SimpleNamespace(is_visible=True, refresh=lambda *a, **k: None)

    ui.show_search_modal = show_search_modal

    asyncio.run(models._open_picker(ui))

    assert captured["title"] == "Models"
    assert any("qwen2.5:7b" in item for item in captured["items"])
    assert captured["initial_index"] == 0
    # A background refresh was scheduled; close it to avoid an unawaited warning.
    assert scheduled
    for coro in scheduled:
        coro.close()
