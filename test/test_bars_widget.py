from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.components.bars import ActionBar, ActionItem, StatusBar
from moka_chat.ui.tui.terminal import MouseEvent


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


def test_status_bar_renders_configured_fields_in_order():
    status = StatusBar(fields=["endpoint_model", "role", "context"])
    status.set_layout(0, 0, 60, 1)
    status.set_values({
        "role": "role default",
        "context": "ctx 12.4k/32k",
        "endpoint_model": "ollama:qwen3:8b",
    })
    buffer = Buffer(60, 1)

    status.render(buffer)

    assert row_text(buffer).strip().startswith(
        "ollama:qwen3:8b  role default  ctx 12.4k/32k"
    )


def test_status_bar_renders_field_colors():
    from moka_chat.ui.tui.colors import RGB

    status = StatusBar(fields=["endpoint_model", "context"])
    status.set_layout(0, 0, 40, 1)
    status.set_values({
        "endpoint_model": "local:model",
        "context": "ctx 10k/32k",
    })
    status.set_field_colors({"context": RGB("#F48771")})
    buffer = Buffer(40, 1)

    status.render(buffer)

    # The context field should be rendered with the error color.
    ctx_start = row_text(buffer).index("ctx")
    ctx_cell = buffer.cells[0][ctx_start]
    assert ctx_cell.fg == (0xF4, 0x87, 0x71)


def test_status_bar_can_change_field_order_without_replacing_values():
    status = StatusBar(fields=["endpoint_model", "role"])
    status.set_layout(0, 0, 40, 1)
    status.set_values({"endpoint_model": "local:model", "role": "role reviewer"})
    status.set_fields(["role", "endpoint_model"])
    buffer = Buffer(40, 1)

    status.render(buffer)

    assert row_text(buffer).strip().startswith("role reviewer  local:model")


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
    from moka_chat.ui.tui.colors import theme

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