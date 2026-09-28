from moka_code.ui.tui.actions import Actions
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.button import Button
from moka_code.ui.tui.terminal import MouseEvent


def test_button_activates_from_keyboard_and_mouse():
    activations = []
    button = Button("ok", lambda: activations.append(True))
    button.set_layout(2, 3, 8, 1)

    assert button.handle_input("\r")
    assert button.handle_input(MouseEvent(3, 3, 0, True))
    assert len(activations) == 2


def test_button_does_not_activate_when_disabled():
    activations = []
    button = Button("ok", activations.append)
    button.enabled = False
    button.set_layout(0, 0, 8, 1)

    assert button.handle_input("\r") is False
    assert button.handle_input(MouseEvent(1, 0, 0, True)) is False
    assert activations == []


def test_button_emits_activate_action_before_callback():
    actions = []
    activations = []

    def sink(action):
        actions.append(action)
        return True

    button = Button("ok", lambda: activations.append(True), action_sink=sink, id="confirm")

    assert button.handle_input(" ")
    assert [(action.name, action.payload) for action in actions] == [(Actions.ACTIVATE, "confirm")]
    assert activations == []


def test_focused_button_renders_reverse_video():
    button = Button("ok")
    button.set_layout(0, 0, 8, 1)
    button.set_focused(True)
    buffer = Buffer(8, 1)

    button.render(buffer)

    assert buffer.cells[0][0].char == "["
    assert all(cell.reverse for cell in buffer.cells[0][:6])