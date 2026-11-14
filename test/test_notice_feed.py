"""Posted messages and running jobs on the notice band.

Errors and warnings stay until their ``✕``, a success goes after a few seconds,
``Usage:`` replies still flash on the hint row; a click on a line's text opens
the whole message, a click on its ``✕`` dismisses it, whatever has the focus."""
from moka_code import settings
from moka_code.ui.app import chatTUI
from moka_code.ui.notice_feed import SUCCESS_SECONDS, NoticeFeed, elapsed
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.chat_screen import ChatScreen
from moka_code.ui.tui.events import KeyEvent, MouseEvent
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError, SysMsgSuccess, SysMsgWarning
from moka_code.ui.tui.router import EventRouter

from conftest import StubAgent

W, H = 80, 24


def _app(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    monkeypatch.setattr(settings.config, "ui_notice_lines", 4)
    ui = chatTUI(StubAgent())
    root = ChatScreen(ui.chat_history_panel, ui.input_box, ui._focus_scope, ui.status_bar,
                      ui.action_bar, ui.notice_band).root
    router = EventRouter(root, interceptor=ui.handle_global_input, focus_scope=ui._focus_scope)
    ui._focus_scope.enter()
    ui._set_app_focus("input")
    popups = []
    ui.show_popup = lambda title, text, **kw: popups.append((title, text))

    def layout():
        root.set_layout(0, 0, W, H)
        root.render(Buffer(W, H))

    return ui, router, layout, popups


def _texts(ui):
    return [n.text for n in ui.notice_band.notices]


def test_errors_warnings_and_successes_go_on_the_band_usage_flashes(monkeypatch):
    ui, _router, _layout, _ = _app(monkeypatch)
    panel = ui.chat_history_panel

    panel.add_message("Usage: /sandbox build <id>", msg_type=SysMsgError())
    panel.add_message("Sandbox config reloaded.", msg_type=SysMsg())
    assert _texts(ui) == []
    assert ui._hint_flash[0] == "Sandbox config reloaded."

    panel.add_message("build dev failed (exit 1): boom → fix\n\nlog line", msg_type=SysMsgError())
    panel.add_message("no dockerfile key → add it", msg_type=SysMsgWarning())
    panel.add_message("built dev in 3s", msg_type=SysMsgSuccess())
    assert _texts(ui) == ["built dev in 3s", "no dockerfile key → add it",
                          "build dev failed (exit 1): boom → fix · more"]

    panel.add_message("build dev failed (exit 1): boom → fix\n\nlog line", msg_type=SysMsgError())
    assert _texts(ui)[0].startswith("build dev failed")       # moved up, not twice
    assert len(_texts(ui)) == 3


def test_a_success_goes_by_itself_errors_stay():
    feed = NoticeFeed()
    feed.post("error", "broken", now=0.0)
    feed.post("success", "built", now=0.0)
    assert feed.live()
    assert not feed.expire(now=SUCCESS_SECONDS - 0.1)
    assert feed.expire(now=SUCCESS_SECONDS + 0.1)
    assert [p.text for p in feed.posted] == ["broken"]
    assert not feed.live()


def test_click_opens_the_message_and_the_cross_dismisses_it_while_typing(monkeypatch):
    """2026-11-14: a ✕ must work with text in the input too."""
    ui, router, layout, popups = _app(monkeypatch)
    ui.chat_history_panel.add_message("build dev failed: boom → fix\n\nthe log",
                                      msg_type=SysMsgError())
    for ch in "draft":
        router.dispatch(KeyEvent(ch))
    layout()
    band = ui.notice_band

    router.dispatch(MouseEvent(3, band.y, 0, True))
    assert popups == [("error", "build dev failed: boom → fix\n\nthe log")]

    router.dispatch(MouseEvent(band.x + band.width - 2, band.y, 0, True))
    assert _texts(ui) == [] and ui.notice_feed.posted == []
    assert ui.input_component.buffer.text == "draft"


def test_a_job_line_shows_its_clock_and_latest_output():
    feed = NoticeFeed()
    job = feed.start("building dev")
    job.started = 100.0
    job.last = "STEP 2/5: RUN apt-get install"
    line = job.line(now=163.0)
    assert line[2:] == "building dev · 1m03s · STEP 2/5: RUN apt-get install"
    assert feed.running("building dev") is job
    feed.finish(job)
    assert feed.jobs == [] and elapsed(42) == "42s"
