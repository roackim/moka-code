"""Tests for the modal list selector (ListModal)."""

from moka_code.ui.tui.components.list_modal import ListModal


class FakeCompositor:
    def __init__(self, width=80, height=24):
        self.width = width
        self.height = height
        self.overlays = []

    def add_overlay(self, component):
        self.overlays.append(component)

    def remove_overlay(self, component):
        self.overlays.remove(component)

    def request_render(self):
        pass


def _make():
    compositor = FakeCompositor()
    modal = ListModal(compositor=compositor, title="pick", formatter=str)
    return modal, compositor


def test_show_registers_overlay_and_selects_first():
    modal, compositor = _make()
    modal.show(["a", "b", "c"], title="pick")

    assert modal.is_visible is True
    assert modal in compositor.overlays
    assert modal._list.model.selected == "a"


def test_arrow_and_enter_accepts_item():
    modal, compositor = _make()
    accepted = []
    modal.show(["a", "b", "c"], on_accept=accepted.append)

    modal.handle_input("\x1b[B")  # down
    handled = modal.handle_input("\r")

    assert handled is True
    assert accepted == ["b"]
    assert modal.is_visible is False
    assert modal not in compositor.overlays


def test_escape_cancels_without_accepting():
    modal, compositor = _make()
    accepted = []
    cancelled = []
    modal.show([1, 2], on_accept=accepted.append, on_cancel=lambda: cancelled.append(True))

    handled = modal.handle_input("\x1b")

    assert handled is True
    assert accepted == []
    assert cancelled == [True]
    assert modal.is_visible is False


def test_modal_traps_unrelated_keys():
    modal, _ = _make()
    modal.show(["a"])
    assert modal.handle_input("x") is True


def test_initial_index_preselects():
    modal, _ = _make()
    modal.show(["a", "b", "c"], initial_index=2)
    assert modal._list.model.selected == "c"


def test_formatter_is_used_for_sizing():
    compositor = FakeCompositor(width=200, height=40)
    modal = ListModal(compositor=compositor, title="pick",
                      formatter=lambda pair: f"{pair[0]}:{pair[1]}")
    modal.show([("srv", "model-x")], title="pick")
    assert modal.width >= len("srv:model-x")
