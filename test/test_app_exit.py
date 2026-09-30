"""Leaving the app: however the UI loop stops, the process ends."""

import asyncio

import moka_code.ui.app as app_module
from moka_code.ui.app import chatTUI

from conftest import StubAgent


class _SlowServerAgent(StubAgent):
    """A server that never answers the startup status probe."""


    async def get_status(self):
        await asyncio.sleep(3600)


def test_stopping_the_ui_loop_ends_run(monkeypatch):
    """``/exit`` only stops the compositor; the workers and the startup probe
    must follow, or ``run()`` hangs after clearing the screen."""

    class StoppingCompositor:
        def __init__(self, root, fps=30, shutdown_event=None):
            self.terminal = None
            self.padding = 0
            self.event_router = type("R", (), {"set_interceptor": lambda *a: None,
                                               "set_focus_scope": lambda *a: None})()

        def add_frame_callback(self, callback):
            pass

        def request_render(self):
            pass

        async def run(self):
            await asyncio.sleep(0.05)   # the UI runs, then /exit stops it

    monkeypatch.setattr(app_module, "Compositor", StoppingCompositor)
    monkeypatch.setattr(app_module, "ModalHost", lambda compositor: None)
    ui = chatTUI(_SlowServerAgent())

    asyncio.run(asyncio.wait_for(ui.run(), timeout=5))

    assert ui.shutdown_event.is_set()
