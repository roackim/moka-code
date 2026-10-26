"""Tests for flat-section config sync (insert new keys, drop retired keys)."""

import os
import re
import stat

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


_SETTING_LINE = re.compile(r'^# (\[|[A-Za-z_][A-Za-z0-9_]*\s*=|"[^"]+"\s*=)')


@pytest.mark.parametrize("section", ["ui", "context", "debug", "servers"])
def test_template_help_lines_use_double_hash(section):
    """Decided 2026-09-30 (PLAN.md step 0.5): a whole-line comment is either
    a setting to uncomment (single ``#``; a bare ``#`` spaces a block of
    settings) or help (``##``)."""
    template = settings.DEFAULT_CONFIG_TEMPLATES[section]
    for line in template.splitlines():
        if line.startswith("#") and not line.startswith("##") and line != "#":
            assert _SETTING_LINE.match(line), f"{section}: help line needs ##: {line!r}"


def test_missing_section_brings_its_heading_and_help(tmp_path):
    (tmp_path / "ui.toml").write_text('theme = "pastel"\n', encoding="utf-8")

    _config(tmp_path).sync_section_files()

    text = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert 'theme = "pastel"' in text
    assert "## Model field" in text
    assert "# show_provider" in text
    assert "# provider_separator" in text
    assert "## Sandbox field" in text


def test_ui_sync_adds_keys_above_the_style_tables_and_never_touches_them(tmp_path):
    """styles.toml merged into ui.toml (2026-10-26): flat keys go above the
    tables (a key below a table header would belong to it)."""
    tables = '[markdown_styles.header1]\nfg = "#FFFFFF"\nbold = false\n'
    (tmp_path / "ui.toml").write_text('theme = "pastel"\n\n' + tables, encoding="utf-8")
    config = _config(tmp_path)

    assert config.sync_section_files() == ["ui"]

    text = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert text.endswith(tables)
    assert text.index("# target_fps") < text.index("[markdown_styles.header1]")
    assert config.reload() == []
    assert config.markdown_styles["header1"] == {"fg": "#FFFFFF", "bold": False}
    assert config.sync_section_files() == []


def test_ui_sync_brings_the_commented_style_tables_to_an_older_file(tmp_path):
    (tmp_path / "ui.toml").write_text('theme = "pastel"\n', encoding="utf-8")
    config = _config(tmp_path)

    config.sync_section_files()

    text = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert settings._TABLES_HEADING in text and "# [markdown_styles.header1]" in text
    assert text.index("# target_fps") < text.index(settings._TABLES_HEADING)
    assert config.sync_section_files() == []


def test_repairs_headings_and_help_left_by_a_previous_sync(tmp_path):
    """Keys inserted by the old per-key sync can sit under the wrong heading."""
    damaged = (
        "## Moka UI settings\n\n"
        "## Status bar and notices\n\n"
        '# status_bar_fields = ["endpoint_model", "role", "context", "cost", "sandbox"]\n'
        '# status_bar_separator = "  "\n'
        "# notice_lines = 2\n\n"
        "## Sandbox field\n\n"
        '# show_provider = true\n'
        '# provider_separator = ":"\n'
        '# sandbox_glyph = "⬢"\n'
        '# sandbox_prefix = "sandbox:"\n'
        '# sandbox_active_color = "SUCCESS"\n'
        '# sandbox_inactive_color = "WARNING"\n'
    )
    (tmp_path / "ui.toml").write_text(damaged, encoding="utf-8")
    config = _config(tmp_path)

    config.sync_section_files()

    text = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert (text.index("# status_bar_separator")
            < text.index('## Put "|" in status_bar_fields')
            < text.index("# notice_lines"))
    assert (text.index("# notice_lines")
            < text.index("## Model field")
            < text.index("# show_provider"))
    assert (text.index("# provider_separator")
            < text.index("## Sandbox field")
            < text.index("# sandbox_glyph"))
    assert config.sync_section_files() == []


def test_backup_is_the_exact_previous_content(tmp_path):
    before = 'theme = "pastel"\n# my own note\n'
    (tmp_path / "ui.toml").write_text(before, encoding="utf-8")

    _config(tmp_path).sync_section_files()

    backup = tmp_path / "ui.toml.bak"
    assert backup.read_bytes() == before.encode("utf-8")


def test_failed_replace_leaves_the_file_untouched(tmp_path, monkeypatch):
    before = 'theme = "pastel"\n'
    (tmp_path / "ui.toml").write_text(before, encoding="utf-8")
    real_replace = os.replace

    def fail_for_target(src, dst):
        if str(dst).endswith("ui.toml"):
            raise OSError("simulated disk failure")
        return real_replace(src, dst)

    monkeypatch.setattr(settings.os, "replace", fail_for_target)
    config = _config(tmp_path)
    config.sync_section_files()

    assert (tmp_path / "ui.toml").read_text(encoding="utf-8") == before
    assert any(section == "ui" for section, _ in config.last_sync_skips)
    assert not list(tmp_path.glob(".ui.toml.moka-*"))


def test_concurrent_change_guard_refuses_to_overwrite(tmp_path):
    target = tmp_path / "x.toml"
    target.write_bytes(b"changed by someone else")

    with pytest.raises(settings._SyncSkipped):
        settings._atomic_write_bytes(target, b"new", guard_path=target,
                                     guard_bytes=b"what we read")

    assert target.read_bytes() == b"changed by someone else"
    assert not list(tmp_path.glob(".x.toml.moka-*"))


def test_invalid_toml_is_never_rewritten(tmp_path):
    broken = 'theme = "pastel"\nbroken = "unterminated\n'
    (tmp_path / "ui.toml").write_text(broken, encoding="utf-8")

    config = _config(tmp_path)
    changed = config.sync_section_files()

    assert "ui" not in changed
    assert (tmp_path / "ui.toml").read_text(encoding="utf-8") == broken
    assert any(section == "ui" for section, _ in config.last_sync_skips)


def test_symlink_is_preserved(tmp_path):
    real = tmp_path / "real-ui.toml"
    real.write_text('theme = "pastel"\n', encoding="utf-8")
    link = tmp_path / "ui.toml"
    link.symlink_to(real)

    _config(tmp_path).sync_section_files()

    assert link.is_symlink()
    assert "# show_provider" in real.read_text(encoding="utf-8")


def test_file_mode_is_preserved(tmp_path):
    path = tmp_path / "ui.toml"
    path.write_text('theme = "pastel"\n', encoding="utf-8")
    os.chmod(path, 0o600)

    _config(tmp_path).sync_section_files()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_keys_that_did_nothing_are_cleaned_from_existing_files(tmp_path):
    """``context.format`` (the project tree was never sent),
    ``ui.debug_console_height`` (the console is never shown) and the
    per-message metrics keys (nothing displayed them) are retired."""
    (tmp_path / "ui.toml").write_text(
        'theme = "terminal"\ndebug_console_height = 12\nshow_metrics = true\n'
        'metrics_show_speed = true\nmetrics_refresh_interval = 0.2\n', encoding="utf-8")
    (tmp_path / "context.toml").write_text(
        'format = "flat"\nmax_files = 50\n', encoding="utf-8")

    config = _config(tmp_path)
    config.sync_section_files()

    ui = (tmp_path / "ui.toml").read_text(encoding="utf-8")
    assert "debug_console_height" not in ui and "show_metrics" not in ui
    assert "metrics_show_speed" not in ui
    assert "metrics_refresh_interval = 0.2" in ui          # still read: kept
    context = (tmp_path / "context.toml").read_text(encoding="utf-8")
    assert "format" not in context and "max_files = 50" in context
    assert config.reload() == []
