"""MCP config, roles, client and transport tests."""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest
import toml

from moka_code import mcp, settings
from moka_code.harness import roles
from moka_code.settings import Config


def _write(path, data):
    path.write_text(toml.dumps(data), encoding="utf-8")


# --- config -----------------------------------------------------------------

def test_mcp_config_parses_stdio_and_http(tmp_path):
    _write(tmp_path / "mcp.toml", {"servers": {
        "filesystem": {
            "type": "stdio", "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {"TOKEN": "$MCP_TOKEN"}, "tool_timeout": 30,
        },
        "remote": {
            "type": "http", "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer $TOKEN"},
        },
        "stdio": {"command": "server"},          # table named after a transport
    }})

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")

    assert config.load_errors == []
    assert config.mcp_servers["filesystem"]["command"] == "npx"
    assert config.mcp_servers["remote"]["url"] == "https://mcp.example.com/mcp"
    assert config.mcp_servers["stdio"]["type"] == "stdio"


def test_mcp_config_reports_bad_tables_and_keeps_good_ones(tmp_path):
    _write(tmp_path / "mcp.toml", {"servers": {
        "nocommand": {"type": "stdio"},
        "badtype": {"type": "wat"},
        "nourl": {"type": "http"},
        "badargs": {"type": "stdio", "command": "x", "args": "nope"},
        "badtimeout": {"type": "stdio", "command": "x", "tool_timeout": "soon"},
        "notype": {"command": "x"},
        "extra": {"type": "stdio", "command": "x", "bogus": 1},
        "ok": {"type": "stdio", "command": "x"},
    }})

    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")
    joined = "\n".join(config.load_errors)

    assert "[servers.nocommand].command is required" in joined
    assert "unknown MCP transport 'wat'" in joined
    assert "[servers.nourl].url is required" in joined
    assert "[servers.badargs].args must be a list of strings" in joined
    assert "[servers.badtimeout].tool_timeout must be a number" in joined
    assert "[servers.notype].type is required" in joined
    assert "[servers.extra] unknown key 'bogus'" in joined
    assert set(config.mcp_servers) == {"ok", "extra"}


# --- roles ------------------------------------------------------------------

def test_mcp_server_permission_covers_all_its_tools(monkeypatch):
    monkeypatch.setattr(settings.config, "mcp_servers", {
        "github": {"type": "http", "url": "x"},
    })

    role = roles._role_from_dict("r", {"mcps": {"github": "ask"}})

    assert role.permission_for("github__create_issue") == "ask"
    assert role.permission_for("github__other_tool") == "ask"
    assert role.permission_for("read") == "no"
    assert role.enabled_mcp_servers() == {"github"}


def test_unknown_mcp_server_is_reported(monkeypatch):
    monkeypatch.setattr(settings.config, "mcp_servers", {})

    with pytest.raises(ValueError, match="unknown MCP server 'nope'"):
        roles._role_from_dict("r", {"mcps": {"nope": "yes"}})


# --- fake stdio server ------------------------------------------------------

_FAKE_SERVER = """
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if "id" not in msg:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1"},
        }})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{
            "name": "echo",
            "description": "Echo text",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }]}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": "echo: " + str(args.get("text"))}],
        }})
    else:
        send({"jsonrpc": "2.0", "id": msg["id"],
              "error": {"code": -32601, "message": "no"}})
"""


@pytest.fixture
def fake_mcp_server(tmp_path, monkeypatch):
    script = tmp_path / "fake_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    monkeypatch.setattr(settings.config, "mcp_servers", {
        "fake": {"type": "stdio", "command": sys.executable,
                 "args": [str(script)]},
    })
    return "fake"


def test_fake_stdio_server_discovery_and_call(fake_mcp_server):
    async def main():
        try:
            await mcp.refresh(force=True)
            records = mcp.status()
            assert len(records) == 1
            assert records[0]["connected"] is True
            assert records[0]["error"] is None
            assert [tool.qualified for tool in records[0]["tools"]] == ["fake__echo"]

            tools = mcp.registered_tools({fake_mcp_server})
            assert list(tools) == ["fake__echo"]
            schema = tools["fake__echo"].get_schema()["function"]
            assert schema["name"] == "fake__echo"
            assert schema["parameters"]["type"] == "object"
            assert await tools["fake__echo"].execute(text="hi") == "echo: hi"
        finally:
            await mcp.close_all()

    asyncio.run(main())


def test_harness_only_exposes_mcp_tools_the_role_enables(fake_mcp_server, tmp_path):
    from moka_code.harness.harness import Harness
    from moka_code.harness.roles import Role
    from moka_code.harness.tools import InProcessTransport, MinimalToolset

    async def main():
        try:
            await mcp.refresh(force=True)
            harness = Harness.__new__(Harness)
            harness.workspace = str(tmp_path)
            harness.transport = InProcessTransport(MinimalToolset(tmp_path))

            harness.role = Role(name="r", tools={}, mcps={fake_mcp_server: "yes"})
            harness._rebuild_tools()
            assert "fake__echo" in harness.tools_map
            assert any(schema["function"]["name"] == "fake__echo"
                       for schema in harness.tool_schemas)

            harness.role = Role(name="r", tools={}, mcps={fake_mcp_server: "no"})
            harness._rebuild_tools()
            assert "fake__echo" not in harness.tools_map
        finally:
            await mcp.close_all()

    asyncio.run(main())


# --- protocol helpers -------------------------------------------------------

def test_sse_response_is_parsed():
    body = ('event: message\n'
            'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n')
    message = mcp._HttpConnection._parse_sse(body, 1)
    assert message["result"] == {"ok": True}


def test_tool_result_formats_text_images_and_errors():
    assert mcp._format_result({"content": [{"type": "text", "text": "hi"}]}) == "hi"
    image = mcp._format_result({"content": [{"type": "image", "mimeType": "image/png",
                                             "data": "abcd"}]})
    assert "image/png" in image and "not attached" in image
    error = mcp._format_result({"isError": True, "content": [{"type": "text",
                                                              "text": "boom"}]})
    assert error.startswith("[MCP tool error]") and "boom" in error


def test_tool_name_is_sanitized_and_bounded():
    name = mcp._sanitize_tool_name("my server", "do/thing " + "x" * 100)
    assert len(name) <= 64
    assert all(char.isalnum() or char in "_-" for char in name)
