"""Menus are shared components: sorted by default, a source with a
meaningful order declares it, and every picker is clickable.

Decided 2026-09-30 (PLAN.md step 0.3).
"""

import pytest

from moka_code import settings
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.menu import SelectionMenu
from moka_code.ui.tui.components.search_modal import SearchModal
from moka_code.ui.tui.events import MouseEvent


def test_menu_sorts_unless_the_source_is_ordered():
    menu = SelectionMenu()
    menu.update(["beta", "Alpha", "gamma"])
    assert menu.items == ["Alpha", "beta", "gamma"]
    menu.update(["high", "low", "default"], ordered=True)
    assert menu.items == ["high", "low", "default"]


def _modal(items, **kw):
    modal = SearchModal(title="Pick")
    accepted = []
    modal.open(items, on_accept=accepted.append, **kw)
    modal.render(Buffer(60, 20))        # centered: sets x/y/width/height
    return modal, accepted


def test_picker_sorts_and_keeps_the_initial_item():
    modal, _ = _modal(["zeta", "alpha", "mid"], initial_index=0)   # "zeta"
    assert modal.items == ["alpha", "mid", "zeta"]
    assert modal.get_selected() == "zeta"


def test_ordered_picker_keeps_the_source_order():
    modal, _ = _modal(["none", "low", "high"], ordered=True)
    assert modal.items == ["none", "low", "high"]


def test_click_accepts_the_clicked_row():
    modal, accepted = _modal(["a", "b", "c"])
    modal.handle_input(MouseEvent(x=modal.x + 2, y=modal.y + 2, button=0, pressed=True))
    assert accepted == ["b"]
    assert not modal.is_visible


def test_wheel_moves_the_selection_and_other_mouse_events_are_trapped():
    modal, accepted = _modal(["a", "b", "c"])
    assert modal.handle_input(MouseEvent(x=modal.x + 2, y=modal.y + 1, button=65, pressed=True))
    assert modal.get_selected() == "b" and not accepted
    assert modal.handle_input(MouseEvent(x=0, y=0, button=0, pressed=True))   # outside
    assert modal.is_visible and not accepted


@pytest.fixture
def two_servers(monkeypatch):
    monkeypatch.setattr(settings.config, "servers", {
        "zeta": {"type": "openai-compatible", "base_url": "http://z/v1"},
        "alpha": {"type": "openai-compatible", "base_url": "http://a/v1"}})
    monkeypatch.setattr(settings.config, "models_by_server", {
        "zeta": [{"id": "a-model"}], "alpha": [{"id": "z-model"}, {"id": "b-model"}]})
    monkeypatch.setattr(settings.config, "stale_servers", set())
    monkeypatch.setattr(settings.config, "active_server", None)
    monkeypatch.setattr(settings.config, "model_selection", {})
    monkeypatch.setattr(settings.config, "_save_state", lambda: None)


def test_auto_select_picks_the_top_row_of_model(two_servers, monkeypatch):
    from types import SimpleNamespace
    from moka_code.ui.commands import base, models

    monkeypatch.setattr(base, "activate_endpoint", lambda ui, endpoint: None)
    ui = SimpleNamespace(chat_history_panel=SimpleNamespace(add_message=lambda *a, **k: None))
    base.auto_select(ui)
    top = models._by_server(models._cached_pairs())[0]
    assert (settings.config.active_server, settings.config.model_selection) == (
        top[0], {top[0]: top[1].id}) == ("alpha", {"alpha": "b-model"})
