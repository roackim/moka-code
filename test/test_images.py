"""Image attachments: header probing, cache, collection, sending, export/import,
the text-only refusal and Ctrl+V."""

import asyncio
import base64
import json
import struct
import zlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from moka_code import settings
from moka_code.harness import images
from moka_code.harness.endpoint import Chunk, ToolCallPiece, image_input_from_metadata
from moka_code.harness.harness import Harness
from moka_code.ui.app import chatTUI
from moka_code.ui.chat_message import unmention
from moka_code.ui.commands.conversation import conversation_export, conversation_import
from moka_code.ui.tui.msg_types import UserMsg

from conftest import StubAgent

from moka_code.harness.providers import LlamaCpp

MB = 1024 * 1024


def png(width=3, height=2) -> bytes:
    """A real (tiny) PNG."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(width, height) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof = b"\xff\xc0" + struct.pack(">HBHH", 11, 8, height, width) + b"\x00" * 6
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache" / "moka" / "images"


# ---------------------------------------------------------------------------
# probing
# ---------------------------------------------------------------------------

def test_probe_reads_dimensions_from_headers():
    assert images.probe(png(640, 480)) == ("image/png", 640, 480)
    assert images.probe(b"GIF89a" + struct.pack("<HH", 32, 16) + b"\x00" * 8) == ("image/gif", 32, 16)
    assert images.probe(jpeg(1920, 1080)) == ("image/jpeg", 1920, 1080)
    vp8 = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 10 + struct.pack("<HH", 100, 50)
    assert images.probe(vp8) == ("image/webp", 100, 50)
    vp8x = (b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x00" * 8
            + (799).to_bytes(3, "little") + (599).to_bytes(3, "little"))
    assert images.probe(vp8x) == ("image/webp", 800, 600)
    # VP8L packs 14-bit (width-1, height-1) after the 0x2f signature byte.
    bits = (300 - 1) | ((200 - 1) << 14)
    vp8l = b"RIFF\x00\x00\x00\x00WEBPVP8L" + b"\x00" * 4 + b"\x2f" + bits.to_bytes(4, "little") + b"\x00" * 8
    assert images.probe(vp8l) == ("image/webp", 300, 200)


def test_probe_rejects_other_files():
    with pytest.raises(images.ImageError):
        images.probe(b"%PDF-1.7 not an image")


# ---------------------------------------------------------------------------
# cache / collection
# ---------------------------------------------------------------------------

def test_store_saves_once_by_content(cache):
    first = images.store(png(), 5 * MB)
    second = images.store(png(), 5 * MB)
    assert first["path"] == second["path"]
    assert first["path"].startswith(str(cache)) and first["path"].endswith(".png")
    assert (first["name"], first["width"], first["height"]) == ("clipboard", 3, 2)


def test_store_refuses_over_the_limit():
    with pytest.raises(images.ImageError, match="max_image_mb"):
        images.store(png(), 10)


def test_collect_attaches_pasted_markers_only(tmp_path):
    (tmp_path / "shot.png").write_bytes(png(10, 20))
    pasted = {1: images.store(png(), 5 * MB), 2: images.store(png(5, 5), 5 * MB)}

    attached = images.collect("see [image #2] and @shot.png", pasted)

    # [image #1] was deleted from the draft; @shot.png is a path, not an attachment.
    assert [(i["n"], i["name"]) for i in attached] == [(2, "clipboard")]


def test_unmention_makes_mentions_plain_paths():
    text = "fix @src/a.py, see @'my pic.jpg' and @b\\ c.md; mail a@b.c"
    assert unmention(text) == "fix src/a.py, see 'my pic.jpg' and b\\ c.md; mail a@b.c"


def test_describe_line():
    image = {"n": 1, "name": "shot.png", "width": 1920, "height": 1080, "size": 245 * 1024}
    assert images.describe(image) == "▣ image #1 · shot.png · 1920×1080 · 245 KB"


# ---------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------

def _chunk(content=None, finish=None):
    return Chunk(text=content or "", finish=finish)


def test_history_keeps_a_reference_and_the_request_gets_parts(tmp_path, monkeypatch):
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=LlamaCpp(name="test")):
        harness = Harness(workspace_path=str(tmp_path))
    sent = []

    async def fake_completion(messages, tools=None):
        sent.append(list(messages))
        yield _chunk(content="a cat")
        yield _chunk(finish="stop")

    harness.endpoint.stream = fake_completion
    image = {**images.store(png(), 5 * MB), "n": 1}

    async def drain():
        return [event async for event in harness.chat("what is [image #1]?", [image])]

    asyncio.run(drain())

    user = [m for m in harness.history if m["role"] == "user"][0]
    assert user["content"] == "what is [image #1]?"
    assert user["images"] == [image]          # a reference, no bytes
    parts = sent[0][-1]["content"]
    assert parts[0] == {"type": "text", "text": "what is [image #1]?"}
    assert parts[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(png()).decode())
    assert "images" not in sent[0][-1]


def test_missing_image_is_sent_as_placeholder():
    parts = images.api_content("hi", [{"n": 3, "path": "/nonexistent.png", "mime": "image/png"}])
    assert parts[1] == {"type": "text", "text": "[image #3 unavailable]"}


# ---------------------------------------------------------------------------
# capability
# ---------------------------------------------------------------------------

def test_image_input_from_metadata():
    assert image_input_from_metadata({"architecture": {"input_modalities": ["text"]}}) is False
    assert image_input_from_metadata({"architecture": {"input_modalities": ["text", "image"]}}) is True
    assert image_input_from_metadata({}) is None


def _ui_with_endpoint(tmp_path, accepts):
    agent = StubAgent()
    agent.workspace = str(tmp_path)
    agent.endpoint = SimpleNamespace(selected_model="deepseek/deepseek-chat",
                                     accepts_images=lambda: accepts,
                                     _original_base_url="http://localhost:8080/v1")
    return chatTUI(agent)


def test_text_only_model_refuses_and_keeps_the_input(tmp_path):
    ui = _ui_with_endpoint(tmp_path, accepts=False)
    ui._pasted_images = {1: images.store(png(), 5 * MB)}
    ui.input_component.update("describe [image #1]")

    ui.input_component.handle_input("\r")

    assert ui.input_component.text == "describe [image #1]"
    assert ui.message_queue.qsize() == 0
    assert any("deepseek/deepseek-chat can't read images" in line
               for line in ui.activity_panel.lines)


def test_unknown_capability_sends(tmp_path):
    ui = _ui_with_endpoint(tmp_path, accepts=None)
    ui._pasted_images = {1: images.store(png(7, 9), 5 * MB)}
    ui.input_component.update("describe [image #1] like @a.png")

    ui.input_component.handle_input("\r")

    assert ui.input_component.text == ""
    text, msg, attached = ui.message_queue.get_nowait()
    # The model sees a plain path; the transcript keeps the @mention.
    assert text == "describe [image #1] like a.png"
    assert [(i["name"], i["width"], i["height"]) for i in attached] == [("clipboard", 7, 9)]
    assert msg.base_text.splitlines()[-1].startswith("▣ image #1 · clipboard · 7×9")
    assert ui.chat_history_panel.copy_text_for(msg) == "describe [image #1] like @a.png"


# ---------------------------------------------------------------------------
# Ctrl+V
# ---------------------------------------------------------------------------

def test_ctrl_v_pastes_an_image_as_a_marker(tmp_path, monkeypatch):
    monkeypatch.setattr("moka_code.ui.app.read_clipboard", lambda: png())
    ui = _ui_with_endpoint(tmp_path, accepts=True)

    ui.handle_global_input("\x16")
    ui.handle_global_input("\x16")

    assert ui.input_component.text == "[image #1][image #2]"
    ui.input_component.handle_input("\r")
    _, _, attached = ui.message_queue.get_nowait()
    assert [i["n"] for i in attached] == [1, 2]
    assert ui._pasted_images == {}


def test_ctrl_v_pastes_text_like_a_paste(tmp_path, monkeypatch):
    monkeypatch.setattr("moka_code.ui.app.read_clipboard", lambda: "line 1\r\nline 2")
    ui = _ui_with_endpoint(tmp_path, accepts=True)

    ui.handle_global_input("\x16")

    assert ui.input_component.text == "line 1\nline 2"


# ---------------------------------------------------------------------------
# export / import
# ---------------------------------------------------------------------------

class _Panel:
    def __init__(self):
        self.users = []

    def add_user_message(self, text, attached=(), harness_message_ids=None):
        self.users.append((text, list(attached)))

    def add_message(self, *args, **kwargs):
        return SimpleNamespace()

    def clear(self):
        self.users.clear()


def test_export_embeds_images_and_import_restores_them(tmp_path, cache):
    image = {**images.store(png(4, 4), 5 * MB), "n": 1}
    history = [{"role": "user", "content": "[image #1]", "images": [image]}]
    ui = SimpleNamespace(agent=SimpleNamespace(history=history), chat_history_panel=_Panel(),
                         switch_role=lambda role: role)
    exported = tmp_path / "conv.json"

    asyncio.run(conversation_export(ui, [str(exported)]))

    data = json.loads(exported.read_text())
    assert base64.b64decode(data["history"][0]["images"][0]["data"]) == png(4, 4)
    assert "data" not in history[0]["images"][0]        # live history untouched

    for file in cache.iterdir():                         # a fresh machine
        file.unlink()
    ui.agent.history = []
    asyncio.run(conversation_import(ui, [str(exported)]))

    (restored,) = ui.agent.history[0]["images"]
    assert "data" not in restored
    assert open(restored["path"], "rb").read() == png(4, 4)
    assert ui.chat_history_panel.users == [("[image #1]", [restored])]


def test_import_without_bytes_marks_the_image_unavailable(tmp_path):
    from moka_code.ui.chat_history_panel import ChatHistoryPanel

    panel = ChatHistoryPanel()
    image = {"n": 1, "name": "gone.png", "path": "/nonexistent.png",
             "width": 2, "height": 2, "size": 10}
    msg = panel.add_user_message("look", [image])
    assert isinstance(msg.type, UserMsg)
    assert msg.base_text.endswith("· unavailable")


def test_endpoint_learns_image_support_once(monkeypatch):
    calls = []

    async def query(endpoint, model_name):
        calls.append(model_name)
        return False

    monkeypatch.setattr(LlamaCpp, "query_image_input", query)
    endpoint = LlamaCpp(name="local", model="llama3")
    assert endpoint.accepts_images() is None

    asyncio.run(endpoint.probe_image_input())
    asyncio.run(endpoint.probe_image_input())

    assert endpoint.accepts_images() is False
    assert calls == ["llama3"]


def test_references_are_colored_in_input_and_transcript(tmp_path):
    from moka_code.ui.chat_message import reference_spans
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.colors import theme

    line = "see [image #1] and @src/a.png, not a@b.c"
    spans = [(line[s:e], fg) for s, e, fg in reference_spans(line)]
    assert spans == [("[image #1]", theme.FOCUSED), ("@src/a.png", theme.FOCUSED)]

    ui = _ui_with_endpoint(tmp_path, accepts=True)
    ui.input_component.set_layout(0, 0, 60, 1)
    ui.input_component.update("see [image #1] ok")
    buffer = Buffer(60, 1)
    ui.input_component.render(buffer)
    assert buffer.cells[0][4].fg == theme.FOCUSED and buffer.cells[0][0].fg != theme.FOCUSED

    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    msg = ChatHistoryPanel().add_user_message("look @a.png")
    component = msg.component
    component.set_layout(0, 0, 40, 1)
    buffer = Buffer(40, 1)
    component.render(buffer)
    assert buffer.cells[0][5].fg == theme.FOCUSED and buffer.cells[0][0].fg != theme.FOCUSED


# ---------------------------------------------------------------------------
# Phase 2: read returns images
# ---------------------------------------------------------------------------

def test_worker_read_returns_image_only_with_a_limit(tmp_path):
    from moka_code import worker

    (tmp_path / "a.png").write_bytes(png())
    found = worker.read("a.png", cwd=tmp_path, max_image_bytes=5 * MB)
    assert found == {"image": {"path": str(tmp_path / "a.png"),
                               "data": base64.b64encode(png()).decode()}}
    with pytest.raises(worker.ToolError, match="Image too large"):
        worker.read("a.png", cwd=tmp_path, max_image_bytes=10)
    with pytest.raises(worker.ToolError, match="not UTF-8"):
        worker.read("a.png", cwd=tmp_path)
    (tmp_path / "t.txt").write_text("text")
    assert worker.read("t.txt", cwd=tmp_path, max_image_bytes=5 * MB) == "text"


def test_worker_protocol_carries_the_image(tmp_path):
    from moka_code import worker

    (tmp_path / "a.png").write_bytes(png())
    frame = asyncio.run(worker.handle_request(
        {"id": 1, "tool": "read", "args": {"path": "a.png", "max_image_bytes": MB}}, tmp_path))
    assert json.loads(json.dumps(frame))["result"]["image"]["data"] == base64.b64encode(png()).decode()


def _read_harness(tmp_path, monkeypatch, accepts=None):
    from moka_code.harness.roles import Role

    (tmp_path / "shot.png").write_bytes(png(10, 20))
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=LlamaCpp(name="test", model="m")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools={"read": "yes"}))
    if accepts is not None:
        harness.endpoint._probed("m")["image_input"] = accepts
    call = ToolCallPiece(index=0, id="call_1", name="read", arguments='{"path": "shot.png"}')
    requests = []

    async def fake_completion(messages, tools=None):
        requests.append([dict(m) for m in messages])
        if len(requests) == 1:
            yield Chunk(tool_calls=[call])
        else:
            yield _chunk(content="a red square")
        yield _chunk(finish="stop")

    harness.endpoint.stream = fake_completion

    async def drain():
        return [event async for event in harness.chat("look at shot.png")]

    return harness, requests, asyncio.run(drain())


def test_read_attaches_the_image_after_the_tool_results(tmp_path, monkeypatch):
    harness, requests, _ = _read_harness(tmp_path, monkeypatch)

    tool = next(m for m in harness.history if m["role"] == "tool")
    assert tool["content"] == f"[image: {tmp_path / 'shot.png'}, 10×20 — attached]"
    attached = harness.history[harness.history.index(tool) + 1]
    assert attached["role"] == "user" and attached["source"] == "tool"
    assert [(i["n"], i["name"], i["width"]) for i in attached["images"]] == [(1, "shot.png", 10)]

    follow_up = requests[1]
    assert [m["role"] for m in follow_up if m["role"] != "system"] == ["user", "assistant", "tool", "user"]
    last = follow_up[-1]
    assert "source" not in last
    assert last["content"][0]["text"] == "[images returned by read: shot.png]"
    assert last["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_read_on_a_text_only_model_says_why(tmp_path, monkeypatch):
    harness, requests, _ = _read_harness(tmp_path, monkeypatch, accepts=False)

    tool = next(m for m in harness.history if m["role"] == "tool")
    assert tool["content"] == f"[image: {tmp_path / 'shot.png'} — not attached: m can't read images]"
    assert not any(m.get("source") == "tool" for m in harness.history)
    assert [m["role"] for m in requests[1] if m["role"] != "system"] == ["user", "assistant", "tool"]


def test_read_line_shows_the_image():
    from moka_code.ui.chat_message import _tool_target_metric

    def metric(output):
        return _tool_target_metric("read", {"path": "shot.png"}, raw=None,
                                   output=output, drafting=False)[1]

    assert metric("[image: /w/shot.png, 10×20 — attached]") == "image 10×20"
    assert metric("[image: /w/shot.png — not attached: m can't read images]") == "image not attached"


def test_import_skips_the_tool_image_message(tmp_path):
    from moka_code.ui.commands.conversation import _rebuild_ui_from_history

    ui = SimpleNamespace(chat_history_panel=_Panel())
    _rebuild_ui_from_history(ui, [
        {"role": "user", "content": "look"},
        {"role": "user", "content": "[images returned by read: a.png]",
         "images": [], "source": "tool"},
    ])
    assert ui.chat_history_panel.users == [("look", [])]
