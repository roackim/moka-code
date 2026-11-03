"""Transcript settings: thought/tool gaps and thought colors."""

from moka_code import settings
from moka_code.ui.chat_history_panel import _gap_before
from moka_code.ui.chat_message import Message
from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.msg_types import AssistantMsg, ThinkingMsg, ToolCallMsg, UserMsg


def _gaps(messages):
    return [_gap_before(messages, i) for i in range(len(messages))]


def _turn():
    return [Message("q", msg_type=UserMsg()), Message("t", msg_type=ThinkingMsg()),
            Message("a", msg_type=ToolCallMsg()), Message("b", msg_type=ToolCallMsg()),
            Message("done", msg_type=AssistantMsg())]


def _group(monkeypatch, calls, answers):
    monkeypatch.setattr(settings.config, "ui_group_thoughts_and_calls", calls)
    monkeypatch.setattr(settings.config, "ui_group_reasoning_and_answers", answers)


def test_everything_is_spaced_by_default(monkeypatch):
    """2026-11-02: both groupings are off by default."""
    _group(monkeypatch, False, False)
    assert _gaps(_turn()) == [False, True, True, True, True]


def test_group_thoughts_and_calls_joins_all_activity(monkeypatch):
    """2026-11-02: thought↔tool, tool↔tool and thought↔thought; the answer
    and the user turn stay apart."""
    _group(monkeypatch, True, False)
    turn = _turn()
    turn.insert(2, Message("t2", msg_type=ThinkingMsg()))
    # user, thought, thought, tool, tool, answer
    assert _gaps(turn) == [False, True, False, False, False, True]


def test_group_reasoning_and_answers_joins_activity_to_the_answer_after(monkeypatch):
    """2026-11-02: activity -> answer only; an answer before a tool line and
    the user turn stay apart."""
    _group(monkeypatch, False, True)
    assert _gaps(_turn()) == [False, True, True, True, False]
    turn = [Message("q", msg_type=UserMsg()), Message("x", msg_type=AssistantMsg()),
            Message("a", msg_type=ToolCallMsg())]
    assert _gaps(turn) == [False, True, True]


def test_both_groupings_make_one_block(monkeypatch):
    _group(monkeypatch, True, True)
    assert _gaps(_turn()) == [False, True, False, False, False]


def test_thought_colors_follow_the_fold(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_thought_color", "WARNING")
    monkeypatch.setattr(settings.config, "ui_thought_expanded_color", "#112233")
    rgb = lambda color: (color.r, color.g, color.b)
    msg = Message("reasoning", msg_type=ThinkingMsg())
    assert rgb(msg.component.fg) == (0x11, 0x22, 0x33)
    assert rgb(msg.box.gutter_color) == (0x11, 0x22, 0x33)

    msg.set_collapsed(True)
    assert msg.box.gutter_color is theme.WARNING
    assert rgb(msg.component.fg) == (0x11, 0x22, 0x33)


def _first_column(msg):
    from moka_code.ui.tui.buffer import Buffer
    box = msg.get_component()
    height = box.get_preferred_height(12)
    box.set_layout(0, 0, 12, height)
    buf = Buffer(12, height)
    box.render(buf)
    return [row[0].char for row in buf.cells]


def test_bullet_stands_for_the_bar_until_focused(monkeypatch):
    """2026-10-30: unfocused, a message shows ``ui.bullet`` on its first row, or
    nothing with its ``ui.<type>_bullet`` off; the bar is for the focused one."""
    monkeypatch.setattr(settings.config, "ui_bullet", "*")
    monkeypatch.setattr(settings.config, "ui_answer_bullet", True)
    monkeypatch.setattr(settings.config, "ui_user_bullet", False)
    answer = Message("one two three four five six", msg_type=AssistantMsg(), max_width=10)
    user = Message("q", msg_type=UserMsg(), max_width=10)
    monkeypatch.setattr(settings.config, "ui_wall_char", "")
    answer.refresh_theme()
    assert _first_column(answer) == ["*", " ", " "]
    assert _first_column(user) == [" "]

    answer.set_focused(True)
    assert _first_column(answer) == ["▌"] * 3


def test_contiguous_answer_blocks_bullet_only_the_first():
    """2026-10-30: the segments of a split answer touch; only the first shows
    ``ui.bullet``. An answer after a gap gets its own."""
    from moka_code.ui.chat_history_panel import _continues_answer

    split = [Message("a", msg_type=AssistantMsg()), Message("b", msg_type=AssistantMsg())]
    split[0].group = split[1].group = object()
    turn = [Message("q", msg_type=UserMsg()), *split,
            Message("t", msg_type=ToolCallMsg()), Message("c", msg_type=AssistantMsg())]
    assert [_continues_answer(turn, i) for i in range(len(turn))] == [
        False, False, True, False, False]


def test_a_muted_wall_runs_below_the_bullet(monkeypatch):
    """2026-10-30: ``ui.wall_char`` draws a muted line below the bullet, and
    down the whole of a continued answer block; "" or " " turns it off."""
    monkeypatch.setattr(settings.config, "ui_bullet", "*")
    monkeypatch.setattr(settings.config, "ui_answer_bullet", True)
    monkeypatch.setattr(settings.config, "ui_wall_char", "|")
    answer = Message("one two three four five six", msg_type=AssistantMsg(), max_width=10)
    assert _first_column(answer) == ["*", "|", "|"]

    answer.box.bullet_continued = True
    answer.box.mark_changed()
    assert _first_column(answer) == ["|", "|", "|"]

    for off in ("", " "):
        monkeypatch.setattr(settings.config, "ui_wall_char", off)
        answer.refresh_theme()
        answer.box.bullet_continued = False
        answer.box.mark_changed()
        assert _first_column(answer) == ["*", " ", " "]

    answer.set_focused(True)
    assert _first_column(answer) == ["▌"] * 3


def test_focus_draws_the_thick_wall_on_every_row(monkeypatch):
    """2026-10-30: focused, ``ui.focus_wall_char`` replaces the bullet too, so a
    one-line message still reads as selected."""
    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    from moka_code.ui.tui.buffer import Buffer

    monkeypatch.setattr(settings.config, "ui_bullet", "*")
    monkeypatch.setattr(settings.config, "ui_answer_bullet", True)
    monkeypatch.setattr(settings.config, "ui_focus_wall_char", "#")
    panel = ChatHistoryPanel()
    panel.set_layout(0, 0, 12, 8)
    panel.add_message("one line", msg_type=AssistantMsg())
    panel.set_focused_message(0)
    buf = Buffer(12, 8)
    panel.render(buf)
    column = [row[0].char for row in buf.cells]
    assert "*" not in column and column.count("#") == 1
    assert buf.cells[column.index("#")][0].fg is theme.FOCUSED


def test_highlight_paints_the_enabled_types_on_the_theme_highlight(monkeypatch):
    """2026-11-02: ``ui.<type>_highlight`` draws a message on the theme's
    USER_HIGHLIGHT (user) or ASSISTANT_HIGHLIGHT (the rest); user messages
    only by default."""
    from moka_code.ui.tui.colors import RGB

    monkeypatch.setattr(theme, "USER_HIGHLIGHT", RGB("#102030"))
    monkeypatch.setattr(theme, "ASSISTANT_HIGHLIGHT", RGB("#405060"))
    user, answer = Message("q", msg_type=UserMsg()), Message("a", msg_type=AssistantMsg())
    assert user.box.bg is theme.USER_HIGHLIGHT and user.component.bg is theme.USER_HIGHLIGHT
    assert answer.box.bg is theme.get_bg()

    monkeypatch.setattr(settings.config, "ui_answer_highlight", True)
    answer.refresh_theme()
    assert answer.box.bg is theme.ASSISTANT_HIGHLIGHT


def test_user_highlight_pad_lights_a_row_above_and_below(monkeypatch):
    """2026-11-02: ``ui.user_highlight_pad`` adds a lit blank row above and
    below a highlighted user message; the bullet stays on its text row."""
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.colors import RGB

    monkeypatch.setattr(theme, "USER_HIGHLIGHT", RGB("#102030"))
    monkeypatch.setattr(settings.config, "ui_bullet", "*")
    user = Message("q", msg_type=UserMsg())
    box = user.get_component()
    assert box.get_preferred_height(8) == 3
    box.set_layout(0, 0, 8, 3)
    buf = Buffer(8, 3)
    box.render(buf)
    assert all(cell.bg == (0x10, 0x20, 0x30) for row in buf.cells for cell in row)
    assert [row[0].char for row in buf.cells] == [" ", "*", " "]

    monkeypatch.setattr(settings.config, "ui_user_highlight_pad", False)
    user.refresh_theme()
    assert box.get_preferred_height(8) == 1


def test_no_highlight_without_a_theme_highlight(monkeypatch):
    monkeypatch.setattr(theme, "USER_HIGHLIGHT", None)
    user = Message("q", msg_type=UserMsg())
    assert user.box.bg is theme.get_bg()
    assert user.box.content_pad_y == 0                       # no pad around nothing


def test_theme_file_highlight_may_be_empty(tmp_path):
    from moka_code.ui.tui import colors

    (tmp_path / "x.toml").write_text('USER_HIGHLIGHT = ""\nASSISTANT_HIGHLIGHT = ""\n')
    config = settings.Config.__new__(settings.Config)
    config.themes, errors = {}, []
    settings._load_themes_dir(tmp_path, config, errors)

    assert errors == []
    loaded = colors._theme_from_palette("moka", config.themes["x"])
    assert loaded.USER_HIGHLIGHT is None and loaded.ASSISTANT_HIGHLIGHT is None
    assert colors._color_to_toml(None) == '""'


def test_code_highlight_paints_code_rows_but_not_their_indent_or_blank_rows(monkeypatch):
    """2026-11-02: ``ui.code_highlight`` draws a split code block's rows on
    ASSISTANT_HIGHLIGHT after the code indent; the indent, the bullet column
    and the blank rows around it are not."""
    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.colors import RGB
    from moka_code.ui.tui.components.markdown import code_indent_width

    monkeypatch.setattr(theme, "ASSISTANT_HIGHLIGHT", RGB("#102030"))
    monkeypatch.setattr(settings.config, "ui_code_highlight", True)
    monkeypatch.setattr(settings.config, "ui_code_highlight_pad", False)
    panel = ChatHistoryPanel()
    msg = panel.add_message("Intro:\n```python\nx = 1\n```\nDone.", msg_type=AssistantMsg())
    msg.finalize()
    panel.split_answer(msg)
    code = panel.messages[1].get_component()
    width = 20
    height = code.get_preferred_height(width)
    code.set_layout(0, 0, width, height)
    buf = Buffer(width, height)
    code.render(buf)

    lit = (0x10, 0x20, 0x30)
    rows = [[cell.bg == lit for cell in row] for row in buf.cells]
    text = panel.messages[1].component
    pad = text.code_bg_pad                                   # the indent's last cells lead in
    start, end = text.x + code_indent_width() - pad, text.x + text.width
    assert start > 0                                         # the bullet column and indent stay plain
    assert rows[0] == rows[-1] == [False] * width            # blank rows around the block
    assert rows[1] == [False] * start + [True] * (end - start) + [False] * (width - end)


def test_code_highlight_pad_lights_an_empty_code_row_at_each_end(monkeypatch):
    """2026-11-02: ``ui.code_highlight_pad`` adds an empty code row above and
    below a highlighted block, inside the band (after the code indent)."""
    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.colors import RGB

    monkeypatch.setattr(theme, "ASSISTANT_HIGHLIGHT", RGB("#102030"))
    monkeypatch.setattr(settings.config, "ui_code_highlight", True)
    msg = ChatHistoryPanel().add_message("```python\nx = 1\n```", msg_type=AssistantMsg())
    msg.finalize()
    box = msg.get_component()
    width = 20
    height = box.get_preferred_height(width)
    box.set_layout(0, 0, width, height)
    buf = Buffer(width, height)
    box.render(buf)

    lit = (0x10, 0x20, 0x30)
    text = msg.component
    texts = ["".join(c.char for c in row).strip() for row in buf.cells]
    assert len(texts) == 3 and "x = 1" in texts[1]
    assert all(row[text.x + text.width - 1].bg == lit for row in buf.cells)   # all in the band

    monkeypatch.setattr(settings.config, "ui_code_highlight_pad", False)
    msg.refresh_theme()
    assert box.get_preferred_height(width) == 1


def test_indent_thoughts_and_calls_shifts_only_them(monkeypatch):
    """2026-11-03: ``ui.indent_thoughts_and_calls`` lays thoughts and tool
    lines out that many cells right (and narrower); other messages stay."""
    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    from moka_code.ui.tui.buffer import Buffer

    monkeypatch.setattr(settings.config, "ui_indent_thoughts_and_calls", 3)
    panel = ChatHistoryPanel()
    thought = panel.add_message("t", msg_type=ThinkingMsg())
    call = panel.add_message("c", msg_type=ToolCallMsg())
    answer = panel.add_message("a", msg_type=AssistantMsg())
    panel.set_layout(0, 0, 40, 20)
    panel.render(Buffer(40, 20))

    assert [m.get_component().x for m in (thought, call, answer)] == [3, 3, 0]
    assert thought.get_component().width == answer.get_component().width - 3

    monkeypatch.setattr(settings.config, "ui_indent_thoughts_and_calls", 0)
    thought.refresh_theme()
    assert thought.left_margin == 0


def _tree_rows(monkeypatch, grouped, items):
    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    from moka_code.ui.tui.buffer import Buffer

    monkeypatch.setattr(settings.config, "ui_tree_thoughts_and_calls", True)
    monkeypatch.setattr(settings.config, "ui_group_thoughts_and_calls", grouped)
    panel = ChatHistoryPanel()
    panel.add_message("question", msg_type=UserMsg())
    for text, msg_type in items:
        msg = panel.add_message(text, msg_type=msg_type)
        if isinstance(msg_type, ThinkingMsg):
            msg.collapsed = True
            msg.finalize()
    panel.add_message("the answer", msg_type=AssistantMsg())
    panel.set_layout(0, 0, 30, 14)
    buf = Buffer(30, 14)
    panel.render(buf)
    rows = ["".join(c.char for c in row).rstrip() for row in buf.cells]
    return rows[2:rows.index("• the answer") - 1]


def test_tree_draws_thoughts_and_calls_as_branches(monkeypatch):
    """2026-11-03: ``ui.tree_thoughts_and_calls`` replaces their bullets by
    ``┌─ ├─ └─``; with ``group_thoughts_and_calls`` off a ``│`` crosses the
    blank row between them."""
    items = [("t", ThinkingMsg()), ("read README.md", ToolCallMsg()),
             ("read AGENTS.md", ToolCallMsg()), ("t", ThinkingMsg())]
    assert _tree_rows(monkeypatch, True, items) == [
        "┌─ thought", "├─ read README.md", "├─ read AGENTS.md", "└─ thought"]
    assert _tree_rows(monkeypatch, False, items) == [
        "┌─ thought", "│", "├─ read README.md", "│", "├─ read AGENTS.md", "│", "└─ thought"]


def test_a_tree_of_one_is_a_single_branch(monkeypatch):
    assert _tree_rows(monkeypatch, True, [("read a.py", ToolCallMsg())]) == ["── read a.py"]


def test_segments_of_one_tool_call_are_one_branch():
    from moka_code.ui.chat_history_panel import _tree_marks

    group = object()
    msgs = [Message("t", msg_type=ThinkingMsg()), Message("cmd", msg_type=ToolCallMsg()),
            Message("out", msg_type=ToolCallMsg())]
    msgs[1].group = msgs[2].group = group
    assert [_tree_marks(msgs, i)[0] for i in range(3)] == ["┌─", "└─", " "]


def test_an_edit_rule_is_a_red_line_above_the_edited_message():
    """2026-11-03: while a sent message is edited, a red rule above it says
    sending discards it and everything below."""
    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    from moka_code.ui.tui.buffer import Buffer

    panel = ChatHistoryPanel()
    panel.add_message("kept", msg_type=UserMsg())
    edited = panel.add_message("edited", msg_type=UserMsg())
    rule = panel.add_edit_rule(edited)
    assert panel.messages.index(rule) == panel.messages.index(edited) - 1
    panel.set_layout(0, 0, 70, 10)
    buf = Buffer(70, 10)
    panel.render(buf)

    rows = ["".join(c.char for c in r).rstrip() for r in buf.cells]
    line = next(r for r in rows if "editing message" in r)
    assert line.startswith("── editing message, sending will discard everything below ─")
    assert line.endswith("─")
    assert rows.index(line) < next(i for i, r in enumerate(rows) if "edited" in r)
    error = (theme.ERROR.r, theme.ERROR.g, theme.ERROR.b) if hasattr(theme.ERROR, "r") else theme.ERROR
    row = buf.cells[rows.index(line)]
    assert {c.fg for c in row if c.char.strip()} == {error}


def test_the_view_holds_the_edit_rule_on_its_top_row():
    """2026-11-03: focusing the input (as editing does) scrolls to the end,
    past the rule; ``scroll_to_top_of`` holds the rule at the top instead."""
    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    from moka_code.ui.tui.buffer import Buffer

    panel = ChatHistoryPanel()
    edited = None
    for n in range(12):
        msg = panel.add_message(f"message {n}", msg_type=UserMsg())
        edited = msg if n == 3 else edited
    rule = panel.add_edit_rule(edited)
    panel.set_layout(0, 0, 70, 6)
    panel.render(Buffer(70, 6))                      # at the end: the rule is far above

    panel.scroll_to_top_of(rule)
    buf = Buffer(70, 6)
    panel.render(buf)

    top = "".join(c.char for c in buf.cells[0])
    assert "editing message" in top and not panel.auto_scroll
