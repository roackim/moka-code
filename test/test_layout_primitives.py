from moka_chat.ui.tui.components.text import TextComponent
from moka_chat.ui.tui.components.layout import EmptyLine, SeparatorLine
from moka_chat.ui.tui.container import (
    Align, Content, Fill, Fixed, Hsplit, Overlay, Padding, Percent,
    ScrollView, Stack, Vsplit,
)
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.events import KeyEvent, MouseEvent


def test_empty_line_renders_one_blank_row():
    component = EmptyLine()
    component.set_layout(1, 1, 5, 2)
    buffer = Buffer(8, 4)

    component.render(buffer)

    assert component.get_preferred_height(20) == 1
    assert "".join(cell.char for cell in buffer.cells[1][1:6]) == "     "


def test_separator_line_fills_one_row_with_rule_character():
    component = SeparatorLine("-")
    component.set_layout(1, 1, 5, 2)
    buffer = Buffer(8, 4)

    component.render(buffer)

    assert component.get_preferred_height(20) == 1
    assert "".join(cell.char for cell in buffer.cells[1][1:6]) == "-----"


def test_vsplit_allocates_children_before_rendering():
    left = TextComponent("left")
    middle = TextComponent("middle")
    right = TextComponent("right")
    root = Vsplit([left, middle, right], [Fixed(10), 0.5, Fill()])

    root.set_layout(2, 3, 40, 8)
    root.layout()

    assert (left.x, left.y, left.width, left.height) == (2, 3, 10, 8)
    assert (middle.x, middle.y, middle.width, middle.height) == (12, 3, 15, 8)
    assert (right.x, right.y, right.width, right.height) == (27, 3, 15, 8)


def test_hsplit_uses_preferred_height_for_auto_children():
    header = TextComponent("header")
    body = TextComponent("one\ntwo\nthree")
    root = Hsplit([header, body], [Content(), Content()])

    root.set_layout(1, 2, 30, 10)
    root.layout()

    assert (header.x, header.y, header.width, header.height) == (1, 2, 30, 1)
    assert (body.x, body.y, body.width, body.height) == (1, 3, 30, 3)


def test_padding_allocates_inner_rectangle():
    child = TextComponent("content")
    root = Padding(child, (1, 2))

    root.set_layout(3, 4, 20, 10)
    root.layout()

    assert (child.x, child.y, child.width, child.height) == (5, 5, 16, 8)


def test_align_centers_preferred_content():
    child = TextComponent("hello")
    root = Align(child, horizontal="center", vertical="center")

    root.set_layout(0, 0, 20, 10)
    root.layout()

    assert (child.x, child.y, child.width, child.height) == (7, 4, 5, 1)


def test_stack_allocates_all_children_to_same_rectangle():
    first = TextComponent("first")
    second = TextComponent("second")
    root = Stack([first, second])

    root.set_layout(2, 1, 12, 4)
    root.layout()

    assert (first.x, first.y, first.width, first.height) == (2, 1, 12, 4)
    assert (second.x, second.y, second.width, second.height) == (2, 1, 12, 4)


def test_overlay_paints_later_children_on_top():
    root = Overlay([TextComponent("base"), TextComponent("top")])
    root.set_layout(0, 0, 8, 1)
    root.layout()
    buffer = Buffer(8, 1)

    root.render(buffer)

    assert "".join(cell.char for cell in buffer.cells[0]) == "tope    "


def test_named_size_policies_allocate_fixed_percent_content_and_fill():
    fixed = TextComponent("fixed")
    percent = TextComponent("percent")
    content = TextComponent("content")
    fill = TextComponent("fill")
    root = Vsplit([fixed, percent, content, fill], [Fixed(4), Percent(0.25), Content(), Fill()])

    root.set_layout(0, 0, 20, 2)
    root.layout()

    assert [child.width for child in root.children] == [4, 2, 7, 7]


def test_component_constraints_clamp_allocated_dimensions():
    child = TextComponent("content")
    child.min_width = 8
    child.max_height = 2

    child.set_layout(1, 2, 4, 6)

    assert (child.width, child.height) == (8, 2)


def test_layout_invalidation_is_tracked_separately_from_content_dirty():
    child = TextComponent("content")
    child.clear_dirty()

    child.update("changed")
    assert child.is_dirty()
    assert not child.is_layout_dirty()

    child.set_layout(0, 0, 10, 2)
    assert child.is_layout_dirty()
    assert child.is_dirty()

    child.clear_dirty()
    assert not child.is_dirty()
    assert not child.is_layout_dirty()


def test_scroll_view_clips_content_and_scrolls_with_keys_and_mouse():
    child = TextComponent("one\ntwo\nthree\nfour")
    view = ScrollView(child)
    view.set_layout(1, 1, 8, 2)
    view.layout()
    buffer = Buffer(12, 5)
    view.render(buffer)

    assert [buffer.cells[row][1].char for row in range(1, 3)] == ["o", "t"]
    assert view.max_scroll == 2
    assert view.handle_input(KeyEvent("\x1b[B"))
    assert view.scroll_offset == 1
    assert view.handle_input(MouseEvent(2, 1, 65, True, scroll_delta=2))
    assert view.scroll_offset == 2
    assert view.handle_input(KeyEvent("\x1b[5~"))
    assert view.scroll_offset == 0


def test_scroll_view_uses_key_event_metadata_for_navigation():
    child = TextComponent("one\ntwo\nthree\nfour")
    view = ScrollView(child)
    view.set_layout(0, 0, 8, 2)
    view.layout()

    assert view.handle_input(KeyEvent("\x1b[B"))
    assert view.scroll_offset == 1