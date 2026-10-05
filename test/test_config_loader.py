"""Tests for the split config loader (per-section files + state.toml)."""

import toml

from moka_code import settings
from moka_code.settings import Config


def _write(path, data):
    path.write_text(toml.dumps(data), encoding="utf-8")
    return path


def test_section_files_apply(tmp_path):
    _write(tmp_path / "ui.toml",
           {"theme": "pastel", "msg_h_padding": 2, "status_bar_fields": ["role"]})
    _write(tmp_path / "context.toml", {"max_files": 50})
    _write(tmp_path / "debug.toml", {"log_enabled": True})
    _write(tmp_path / "servers.toml", {
        "servers": {
            "local": {"type": "llamacpp", "base_url": "http://localhost:8080/v1"},
        },
    })

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.load_errors == []
    assert config.ui_theme == "pastel"
    assert config.ui_msg_h_padding == 2
    assert config.ui_status_bar_fields == ["role"]
    assert config.context_max_files == 50
    assert config.debug_log_enabled is True
    assert config.servers["local"]["base_url"] == "http://localhost:8080/v1"


def test_invalid_values_are_reported_and_do_not_apply(tmp_path):
    _write(tmp_path / "ui.toml", {"theme": "dark", "msg_h_padding": "wide", "bogus": 1})
    _write(tmp_path / "context.toml", {"max_files": "many"})
    _write(tmp_path / "servers.toml", {
        "servers": {"local": {"type": "wat", "base_url": 5}},
    })

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.ui_theme == "dark"
    assert config.ui_msg_h_padding == 1  # default kept
    assert config.context_max_files == 500  # default kept

    joined = "\n".join(config.load_errors)
    assert "ui.toml: unknown key 'bogus'" in joined
    assert "ui.toml: msg_h_padding must be an integer" in joined
    assert "context.toml: max_files must be an integer" in joined
    assert "servers.toml: [servers.local].type unknown server type 'wat'" in joined
    assert "servers.toml: [servers.local].base_url must be a string" in joined


def test_unknown_section_in_dedicated_file_is_reported(tmp_path):
    _write(tmp_path / "styles.toml", {"markdown_styles": {"header1": {"fg": "#fff"}}})
    # A stray top-level table is only valid in servers.toml.
    _write(tmp_path / "ui.toml", {"theme": "terminal", "servers": {}})

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.markdown_styles["header1"]["fg"] == "#fff"
    assert any("ui.toml: unknown key 'servers'" in e for e in config.load_errors)


def test_malformed_toml_reports_error_and_keeps_defaults(tmp_path):
    (tmp_path / "ui.toml").write_text("this is = = not toml", encoding="utf-8")

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.load_errors
    assert config.ui_theme == "terminal"


def test_state_toml_loads_selection_and_ignores_the_retired_catalog(tmp_path):
    state = _write(tmp_path / "state.toml", {
        "last_server": "local",
        "active_model": "retired-key-is-ignored",
        "last_model": {"local": "qwen"},
        "model_catalog": {"local": [{"id": "qwen:stale", "context_window": 32768}]},
    })
    config = Config(config_dir=tmp_path, state_path=state)

    assert config.load_errors == []
    assert config.active_server == "local"
    assert config.model_selection == {"local": "qwen"}
    assert config.models_by_server == {}      # discovered live, never loaded
    assert config.get_model_for_server("local") == "qwen"

    config.save_model_selection("local", "qwen")
    assert "model_catalog" not in toml.load(state)    # the next save drops it


def test_reload_keeps_the_in_memory_catalog(tmp_path):
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")
    config.models_by_server = {"local": [{"id": "qwen"}]}
    config.reload()
    assert config.models_by_server == {"local": [{"id": "qwen"}]}


def test_state_is_written_separately_from_intent(tmp_path):
    servers = _write(tmp_path / "servers.toml", {
        "servers": {"local": {"type": "llamacpp", "base_url": "http://x/v1"}},
    })
    state = tmp_path / "state.toml"
    config = Config(config_dir=tmp_path, state_path=state)

    config.models_by_server = {"local": [{"id": "qwen"}]}
    config.save_model_selection("local", "qwen")

    intent = toml.load(servers)
    assert "last_model" not in intent.get("servers", {}).get("local", {})
    assert "model_catalog" not in intent

    persisted = toml.load(state)
    assert persisted["last_model"] == {"local": "qwen"}
    assert "active_model" not in persisted
    assert "model_catalog" not in persisted


def test_type_defaults_to_the_table_name_only_when_it_is_a_type(tmp_path):
    """A table named after a type needs no ``type``; any other name must say
    it, and the retired ``openai`` type is reported. ``openai-compatible`` is a
    type again (it needs a ``base_url``). Skipped servers are not loaded."""
    _write(tmp_path / "servers.toml", {
        "servers": {
            "openrouter": {"api_key_env": "K"},
            "deepseek": {},
            "local": {"base_url": "http://localhost:8080/v1"},
            "old": {"type": "openai", "base_url": "http://localhost:8000/v1"},
            "gateway": {"type": "openai-compatible", "base_url": "http://localhost:8000/v1"},
            "nourl": {"type": "openai-compatible"},
            "ok": {"type": "llamacpp", "base_url": "http://localhost:8000/v1"},
        },
    })
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    joined = "\n".join(config.load_errors)
    assert "[servers.local].type is required unless the table is named after a type" in joined
    assert "[servers.old].type 'openai' is no longer supported" in joined
    assert "[servers.gateway]" not in joined
    assert "[servers.nourl]" in joined and "base_url" in joined
    assert "[servers.openrouter]" not in joined and "[servers.deepseek]" not in joined
    assert {n: config.servers[n]["type"] for n in config.servers} == {
        "openrouter": "openrouter", "deepseek": "deepseek",
        "gateway": "openai-compatible", "ok": "llamacpp"}


def test_default_templates_are_valid_and_error_free(tmp_path):
    for section, template in settings.DEFAULT_CONFIG_TEMPLATES.items():
        (tmp_path / settings.CONFIG_FILES[section]).write_text(template, encoding="utf-8")

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.load_errors == []
    assert config.servers == {}
    assert config.ui_theme == "terminal"


def test_ensure_files_write_templates(tmp_path):
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    created = [config.ensure_section_file(section) for section in settings.CONFIG_FILES]
    [config.ensure_section_file(section) for section in settings.CONFIG_FILES]  # idempotent

    assert {path.name for path in created} == set(settings.CONFIG_FILES.values())
    assert (tmp_path / "ui.toml").exists()


def test_themes_parse_and_active_theme_is_state(tmp_path):
    _write(tmp_path / "themes.toml", {
        "themes": {"mine": {"USER": "#ABCDEF", "MUTED": {"ansi": 90}}},
    })
    state = _write(tmp_path / "state.toml", {"active_theme": "mine"})

    config = Config(config_dir=tmp_path, state_path=state)

    assert config.load_errors == []
    assert config.themes["mine"]["USER"] == "#ABCDEF"
    assert config.themes["mine"]["MUTED"] == {"ansi": 90}
    assert config.active_theme == "mine"
    assert config.get_active_theme() == "mine"


def test_invalid_theme_values_are_reported(tmp_path):
    _write(tmp_path / "themes.toml", {
        "themes": {"bad": {"USER": "notacolor", "BOGUS": "#000000"}},
    })

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    joined = "\n".join(config.load_errors)
    assert "[themes.bad].USER must be a" in joined
    assert "unknown color 'BOGUS'" in joined
    assert "USER" not in config.themes.get("bad", {})


def test_active_theme_falls_back_to_ui_setting(tmp_path):
    _write(tmp_path / "ui.toml", {"theme": "pastel"})
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.active_theme is None
    assert config.get_active_theme() == "pastel"


def test_reload_picks_up_edits(tmp_path):
    _write(tmp_path / "ui.toml", {"theme": "pastel"})
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")
    assert config.ui_theme == "pastel"

    _write(tmp_path / "ui.toml", {"theme": "terminal"})
    assert config.reload() == []
    assert config.ui_theme == "terminal"


def test_openrouter_routing_keys_load(tmp_path):
    _write(tmp_path / "servers.toml", {"servers": {"or": {
        "type": "openrouter",
        "models": ["deepseek/deepseek-v4.1-flash", "anthropic/claude-sonnet-4"],
        "providers": ["deepseek"],
        "providers_by_model": {"deepseek/deepseek-v4.1-flash": ["deepseek", "fireworks"]},
    }}})
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.load_errors == []
    assert config.servers["or"]["models"] == [
        "deepseek/deepseek-v4.1-flash", "anthropic/claude-sonnet-4"]


def test_old_per_model_tables_are_reported_and_skipped(tmp_path):
    """§2.1 (2026-10-01): the per-model ``[models."<id>"]`` tables are the old
    shape; reported with the replacement, never read."""
    _write(tmp_path / "servers.toml", {"servers": {"or": {
        "type": "openrouter",
        "models": {"deepseek/deepseek-v4.1-flash": {"providers": ["deepseek"]}},
    }}})
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    joined = "\n".join(config.load_errors)
    assert "[servers.or].models is now a list" in joined
    assert "[servers.or.providers_by_model]; server skipped" in joined
    assert "or" not in config.servers


def test_providers_by_model_for_an_unlisted_model_is_reported(tmp_path):
    """Decided 2026-10-01: a typo in ``providers_by_model`` is a load error
    (routing never silently ignored); the server still loads."""
    _write(tmp_path / "servers.toml", {"servers": {"or": {
        "type": "openrouter", "models": ["deepseek/deepseek-v4.1-flash"],
        "providers_by_model": {"deepseek/deepseek-v4-flash": ["deepseek"]},
    }}})
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.load_errors == [
        "servers.toml: [servers.or].providers_by_model: 'deepseek/deepseek-v4-flash' "
        "is not in models (entry not used)"]
    assert "or" in config.servers


def test_openrouter_base_url_is_reported_and_never_used(tmp_path):
    from moka_code.harness.endpoint import make_endpoint

    _write(tmp_path / "servers.toml", {"servers": {"or": {
        "type": "openrouter", "base_url": "http://localhost:8010/openrouter/v1"}}})
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert any("[servers.or].base_url is not used" in e for e in config.load_errors)
    endpoint = make_endpoint("or", config.servers["or"])      # still loaded
    assert endpoint.base_url == "https://openrouter.ai/api/v1"


def test_retired_openrouter_keys_name_their_replacement_and_skip(tmp_path):
    _write(tmp_path / "servers.toml", {"servers": {"or": {
        "type": "openrouter", "base_url": "https://openrouter.ai/api/v1",
        "provider": "deepseek", "enabled_models": ["deepseek/deepseek-v4.1-flash"],
    }}})
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    joined = "\n".join(config.load_errors)
    assert "[servers.or].provider was replaced by 'providers = [...]'" in joined
    assert "[servers.or].enabled_models was replaced by" in joined
    assert "unknown key" not in joined
    assert "or" not in config.servers


def test_openrouter_routing_values_are_validated(tmp_path):
    _write(tmp_path / "servers.toml", {"servers": {
        "or": {"type": "openrouter", "models": ["m"], "providers": "deepseek"},
        "or2": {"type": "openrouter", "models": "m"},
        "or3": {"type": "openrouter", "models": ["m"], "providers_by_model": {"m": [1]}},
        "local": {"type": "llamacpp", "providers": ["a"], "models": ["m"]},
    }})
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    joined = "\n".join(config.load_errors)
    assert "[servers.or].providers must be a list of provider slugs; server skipped" in joined
    assert "[servers.or2].models must be a list of model ids; server skipped" in joined
    assert "[servers.or3].providers_by_model must map model ids" in joined
    assert "[servers.local].providers is only supported for openrouter servers" in joined
    assert "[servers.local].models is only supported for openrouter servers" in joined
    assert set(config.servers) == {"local"}


def test_legacy_config_dir_moves_once(tmp_path, monkeypatch):
    monkeypatch.delenv("MOKA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(settings.Path, "home", staticmethod(lambda: tmp_path))
    old = tmp_path / ".config" / "pico-chat"
    (old / "roles").mkdir(parents=True)
    (old / "servers.toml").write_text("# mine\n", encoding="utf-8")

    notice = settings.migrate_legacy_config_dir()

    new = tmp_path / ".config" / "moka"
    assert "moved your configuration" in notice
    assert (new / "servers.toml").read_text(encoding="utf-8") == "# mine\n"
    assert (new / "roles").is_dir() and not old.exists()
    assert settings.migrate_legacy_config_dir() is None  # only once


def test_legacy_config_dir_left_alone_when_moka_dir_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("MOKA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(settings.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".config" / "pico-chat").mkdir(parents=True)
    (tmp_path / ".config" / "moka").mkdir(parents=True)

    assert settings.migrate_legacy_config_dir() is None
    assert (tmp_path / ".config" / "pico-chat").exists()


def test_servers_template_uncommented_is_a_working_config(tmp_path):
    """§2.1 (2026-10-01): the template comes from the registry, one block per
    type, each writing its ``type``; uncommented, it loads without errors."""
    import re

    setting = re.compile(r'^# (?=\[|[A-Za-z_"])')
    text = "\n".join(setting.sub("", line) for line in settings.DEFAULT_SERVERS_TOML.splitlines())
    (tmp_path / "servers.toml").write_text(text, encoding="utf-8")
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.load_errors == []
    assert sorted(s["type"] for s in config.servers.values()) == sorted(settings.REGISTRY)


def test_a_bad_color_is_reported_and_keeps_the_default(tmp_path):
    """ISSUES U6: a typo in a color setting is not silently ignored."""
    _write(tmp_path / "ui.toml", {"sandbox_active_color": "grene",
                                  "sandbox_inactive_color": "#ff8800"})

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert any("sandbox_active_color must be a palette name" in e for e in config.load_errors)
    assert config.ui_sandbox_active_color == "SUCCESS"
    assert config.ui_sandbox_inactive_color == "#ff8800"
