from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.bars import ActionBar, ActionItem, StatusBar
from moka_code.ui.tui.terminal import MouseEvent


def row_text(buffer):
    return "".join(cell.char for cell in buffer.cells[0])


def test_status_bar_renders_left_and_right_text_with_padding():
    status = StatusBar("ready", "100%")
    status.set_layout(0, 0, 20, 1)
    buffer = Buffer(20, 1)

    status.render(buffer)

    rendered = row_text(buffer)
    assert rendered.startswith(" ready")
    assert rendered.endswith("100% ")


def _render(template, values, colors=None, width=60):
    status = StatusBar(template=template)
    status.set_layout(0, 0, width, 1)
    status.set_values(values)
    status.set_field_colors(colors or {})
    buffer = Buffer(width, 1)
    status.render(buffer)
    return buffer


def test_status_bar_template_fills_values():
    buffer = _render("{endpoint}:{model}  role {role}  ctx {context}",
                     {"endpoint": "local", "model": "qwen3", "role": "default",
                      "context": "12.4k/32k"})
    assert row_text(buffer).strip() == "local:qwen3  role default  ctx 12.4k/32k"


def test_a_group_is_left_out_when_a_value_in_it_is_empty():
    values = {"endpoint": "local", "model": "qwen3", "effort": "", "cost": ""}
    buffer = _render("[{endpoint}:]{model}[:{effort}]  [{cost}]x", values)
    assert row_text(buffer).strip() == "local:qwen3  x"
    # Nested: the inner group goes, the outer one stays.
    buffer = _render("[{endpoint}:{model}[:{effort}]]", values)
    assert row_text(buffer).strip() == "local:qwen3"
    # Outside a group an empty value just leaves nothing.
    buffer = _render("a{effort}b", values)
    assert row_text(buffer).strip() == "ab"


def test_a_group_is_drawn_in_its_value_color():
    from moka_code.ui.tui.colors import RGB

    buffer = _render("{model}  [ctx {context}]", {"model": "m", "context": "10k/32k"},
                     {"context": RGB("#F48771")})
    row = row_text(buffer)
    assert buffer.cells[0][row.index("ctx")].fg == (0xF4, 0x87, 0x71)     # the group text too
    assert buffer.cells[0][row.index("10k")].fg == (0xF4, 0x87, 0x71)
    assert buffer.cells[0][row.index("m")].fg != (0xF4, 0x87, 0x71)


def test_status_bar_right_aligns_after_the_marker():
    buffer = _render("{model}  role {role} | {cost}  ⬢ sandbox:{sandbox}",
                     {"model": "local:model", "role": "agent", "cost": "$0.12",
                      "sandbox": "default"})
    rendered = row_text(buffer)
    assert rendered.startswith(" local:model  role agent")
    assert rendered.rstrip().endswith("$0.12  ⬢ sandbox:default")
    assert "|" not in rendered


def test_a_bracket_is_only_literal_when_unbalanced():
    from moka_code.ui.tui.components.bars import render_template

    left, right = render_template("a]b", {}, {}, None)
    assert "".join(t for t, _, _ in left) == "a]b" and right == []


def test_action_bar_activates_by_key_and_mouse():
    activated = []
    bar = ActionBar([
        ActionItem("q", "quit", lambda: activated.append("quit")),
        ActionItem("s", "save", lambda: activated.append("save")),
    ])
    bar.set_layout(0, 0, 20, 1)
    buffer = Buffer(20, 1)
    bar.render(buffer)

    assert bar.handle_input("s")
    assert bar.handle_input(MouseEvent(3, 0, 0, True))
    assert activated == ["save", "quit"]


def test_action_bar_focus_uses_shared_style_without_inversion():
    from moka_code.ui.tui.colors import theme

    activated = []
    bar = ActionBar([ActionItem("x", "close", lambda: activated.append(True))])
    bar.set_layout(0, 0, 12, 1)
    bar.set_focused(True)
    buffer = Buffer(12, 1)
    bar.render(buffer)

    # Focus switches to the shared focused color but is not reverse-video.
    assert not any(cell.reverse for cell in buffer.cells[0][1:9])
    assert buffer.cells[0][1].fg == theme.FOCUSED
    bar.enabled = False
    assert bar.handle_input("x") is False
    assert activated == []