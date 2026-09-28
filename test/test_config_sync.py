"""Tests for flat-section config sync (insert new keys, drop retired keys)."""

import pytest

from moka_code import settings
from moka_code.settings import Config


SPEC_TEMPLATES = {
    "ui": (settings._UI_SPEC, settings.DEFAULT_UI_TOML),
    "context": (settings._CONTEXT_SPEC, settings.DEFAULT_CONTEXT_TOML),
    "debug": (settings._DEBUG_SPEC, settings.DEFAULT_DEBUG_TOML),
}


def _config(tmp_path):
    return Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")


def test_sync_inserts_missing_keys_preserving_user_values(tmp_path):
    (tmp_path / "ui.toml").write_text('theme = "pastel"\n', encoding="utf-8")

    changed = _config(tmp_path).sync_section_files()

    assert "ui" in changed
    text = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert 'theme = "pastel"' in text
    assert "# stream_smoothing" in text
    assert "# smooth_target_fps" in text
    assert text.index("# stream_smoothing") < text.index("# smooth_target_fps")


def test_sync_is_idempotent(tmp_path):
    (tmp_path / "ui.toml").write_text('theme = "pastel"\n', encoding="utf-8")
    config = _config(tmp_path)
    config.sync_section_files()
    first = (tmp_path / "ui.toml").read_text(encoding="utf-8")

    changed = config.sync_section_files()

    assert changed == []
    assert (tmp_path / "ui.toml").read_text(encoding="utf-8") == first


def test_sync_does_not_duplicate_active_key(tmp_path):
    (tmp_path / "ui.toml").write_text("stream_smoothing = false\n", encoding="utf-8")

    _config(tmp_path).sync_section_files()

    text = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert text.count("stream_smoothing") == 1
    assert "stream_smoothing = false" in text


def test_sync_removes_retired_key(tmp_path, monkeypatch):
    monkeypatch.setitem(
        settings._FLAT_SECTION_SYNC, "ui",
        (settings.DEFAULT_UI_TOML, {"legacy_knob"}),
    )
    (tmp_path / "ui.toml").write_text(
        'theme = "pastel"\nlegacy_knob = 3\n', encoding="utf-8")

    _config(tmp_path).sync_section_files()

    text = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert "legacy_knob" not in text
    assert 'theme = "pastel"' in text


def test_ensure_section_file_syncs_existing_file(tmp_path):
    (tmp_path / "ui.toml").write_text('theme = "pastel"\n', encoding="utf-8")

    _config(tmp_path).ensure_section_file("ui")

    text = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert "# stream_smoothing" in text


def test_structured_sections_are_not_synced(tmp_path):
    content = '[servers.local]\ntype = "llamacpp"\nbase_url = "http://x/v1"\n'
    (tmp_path / "servers.toml").write_text(content, encoding="utf-8")

    changed = _config(tmp_path).sync_section_files()

    assert "servers" not in changed
    assert (tmp_path / "servers.toml").read_text(encoding="utf-8") == content


@pytest.mark.parametrize("section", sorted(SPEC_TEMPLATES))
def test_every_spec_key_has_a_commented_template_line(section):
    spec, template = SPEC_TEMPLATES[section]
    template_keys = {key for key, _ in settings._template_key_lines(template)}
    assert set(spec) <= template_keys


@pytest.mark.parametrize("section", sorted(SPEC_TEMPLATES))
def test_template_key_lines_are_commented(section):
    _, template = SPEC_TEMPLATES[section]
    for _, line in settings._template_key_lines(template):
        assert line.lstrip().startswith("#")
