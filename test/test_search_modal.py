"""SearchModal: type-to-filter picker built on SelectionMenu."""

from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.search_modal import SearchModal


class _Compositor:
    def __init__(self):
        self.overlays = []
        self.renders = 0

    def add_overlay(self, component):
        if component not in self.overlays:
            self.overlays.append(component)

    def remove_overlay(self, component):
        if component in self.overlays:
            self.overlays.remove(component)

    def request_render(self):
        self.renders += 1


def make_modal(items, descriptions=None, initial_index=0):
    compositor = _Compositor()
    modal = SearchModal(compositor=compositor, title="Models")
    accepted = []
    cancelled = []
    modal.open(items, descriptions=descriptions, on_accept=accepted.append,
               on_cancel=lambda: cancelled.append(True), initial_index=initial_index)
    return compositor, modal, accepted, cancelled


def test_open_registers_overlay_and_accepts():
    compositor, modal, accepted, _ = make_modal(["aa", "bb", "cc"], initial_index=1)

    assert modal.is_visible
    assert modal in compositor.overlays

    modal.handle_input("\r")

    assert accepted == ["bb"]
    assert not modal.is_visible
    assert modal not in compositor.overlays


def test_typing_filters_items():
    _, modal, accepted, _ = make_modal(["deepseek/x", "qwen2.5", "llama3"])

    for ch in "deep":
        modal.handle_input(ch)

    assert modal.items == ["deepseek/x"]
    modal.handle_input("\r")
    assert accepted == ["deepseek/x"]


def test_arrow_keys_move_selection():
    _, modal, accepted, _ = make_modal(["a", "b", "c"])

    modal.handle_input("\x1b[B")  # down
    assert modal.get_selected() == "b"

    modal.handle_input("\r")
    assert accepted == ["b"]


def test_escape_cancels():
    _, modal, _, cancelled = make_modal(["a"])

    modal.handle_input("\x1b")

    assert cancelled == [True]
    assert not modal.is_visible


def test_no_matches_keeps_modal_registered_and_recovers():
    compositor, modal, _, _ = make_modal(["alpha", "beta"])

    for ch in "zz":
        modal.handle_input(ch)

    assert modal.items == []
    assert modal.is_visible
    assert modal in compositor.overlays

    # Backspace restores.
    modal.handle_input("\x7f")
    modal.handle_input("\x7f")
    assert modal.items == ["alpha", "beta"]


def test_render_centers_title_and_items():
    _, modal, _, _ = make_modal(["deepseek/x", "qwen2.5"])
    buffer = Buffer(80, 20)

    modal.render(buffer)

    text = "\n".join("".join(c.char for c in row).rstrip() for row in buffer.cells)
    assert "Models" in text
    assert "deepseek/x" in text
    assert "type to filter" not in text


def test_query_is_shown_in_the_title():
    _, modal, _, _ = make_modal(["qwen2.5", "deepseek/x"])

    assert modal.title == "Models"

    for ch in "qwe":
        modal.handle_input(ch)

    assert modal.title == "Models: qwe "

    modal.handle_input("\x7f")
    assert modal.title == "Models: qw "


def test_anchored_modal_sits_directly_above_input():
    from moka_code.ui.tui.components.input.input import InputComponent

    class Comp(_Compositor):
        width, height = 80, 20

    inp = InputComponent("> ")
    inp.set_compositor(Comp())
    inp.set_layout(0, 17, 80, 3)

    compositor = Comp()
    modal = SearchModal(compositor=compositor, title="Models")
    modal.auto_center = False
    modal.fill_width = True
    modal.anchor = lambda: inp.place_menu_above_input(modal)
    modal.open(["a", "b"], initial_index=0)

    buffer = Buffer(80, 20)
    modal.render(buffer)

    # Full-width and bottom-aligned to the row just above the input.
    assert modal.x == 0
    assert modal.width == 80
    assert modal.y + modal.height == inp.y
    # The picker is a normal box (corners), anchored above the input.
    assert buffer.cells[modal.y][0].char == "┌"
    assert buffer.cells[modal.y][modal.width - 1].char == "┐"
    assert buffer.cells[modal.y + 1][0].char == "│"
    assert buffer.cells[modal.y + modal.height - 1][0].char == "└"
    assert buffer.cells[modal.y + modal.height - 1][modal.width - 1].char == "┘"


def test_actions_show_above_the_box_and_take_ctrl_keys_and_clicks():
    """2026-11-02: picker actions are Ctrl+key (typing filters) on the
    highlighted item, drawn on a row above the box."""
    from moka_code.ui.tui.events import MouseEvent

    done = []
    modal = SearchModal(compositor=_Compositor(), title="Sessions")
    modal.open(["a", "b"], ordered=True,
               actions=[("^R", "rename", lambda item: done.append(("rename", item))),
                        ("^D", "delete", lambda item: done.append(("delete", item)))])
    buffer = Buffer(80, 20)
    modal.render(buffer)

    row = "".join(c.char for c in buffer.cells[modal.y])
    assert "[^R] rename  [^D] delete" in row
    assert buffer.cells[modal.y + 1][modal.x].char == "┌"

    modal.handle_input("\x1b[B")
    modal.handle_input("\x12")                       # Ctrl+R
    modal.handle_input("d")                          # a letter still filters
    assert done == [("rename", "b")] and modal._search == "d"
    modal.handle_input("\x7f")
    modal.handle_input("\x1b[B")                     # the empty filter lost the highlight

    x = modal.x + row[modal.x:].index("[^D]")
    modal.handle_input(MouseEvent(x=x, y=modal.y, button=0, pressed=True))
    assert done[-1] == ("delete", "b") and modal.is_visible

    # A click on a row still lands on that row (the box is one row lower).
    modal.handle_input(MouseEvent(x=modal.x + 2, y=modal.y + 2, button=0, pressed=True))
    assert not modal.is_visible


def test_item_footer_is_success_colored():
    from moka_code.ui.tui.colors import theme
    from moka_code.ui.tui.components.menu import SelectionMenu

    menu = SelectionMenu()
    menu.set_layout(0, 0, 60, 4)
    menu.update(["qwen"], "", descriptions={"qwen": "local 32k"},
                footers={"qwen": "active"})
    buffer = Buffer(60, 4)
    menu.render(buffer)

    row = "".join(c.char for c in buffer.cells[1])
    idx = row.find("active")
    assert idx != -1
    assert buffer.cells[1][idx].fg == theme.SUCCESS
    assert buffer.cells[1][row.find("local")].fg == theme.MUTED
