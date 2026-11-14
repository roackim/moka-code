"""``/mcp``: view the configured MCP servers and the tools they expose.

A viewer only — mcp.toml is edited with ``/config mcp``, never in a form.
"""
from __future__ import annotations

from typing import Dict, List

from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import ChatUIProtocol, Command, Param


def mcp_server_completions() -> List[str]:
    from moka_code import mcp

    return mcp.configured_names()


def mcp_server_descriptions() -> Dict[str, str]:
    from moka_code import settings

    return {name: str(table.get("type", "?"))
            for name, table in settings.config.mcp_servers.items()}


def _role_setting(ui: ChatUIProtocol, server: str) -> str:
    role = getattr(getattr(ui, "agent", None), "role", None)
    mcps = getattr(role, "mcps", None) or {}
    return mcps.get(server, "no")


async def cmd_mcp(ui: ChatUIProtocol, args: List[str]):
    from moka_code import mcp

    await mcp.ensure_ready()
    records = mcp.status()
    if not records:
        ui.chat_history_panel.add_message(
            "No MCP servers configured. Add one with /config mcp.",
            msg_type=SysMsg(), title="mcp")
        return

    if args:
        name = args[0]
        record = next((item for item in records if item["name"] == name), None)
        if record is None:
            ui.chat_history_panel.add_message(
                f"Unknown MCP server '{name}'. Configured: {', '.join(r['name'] for r in records)}",
                msg_type=SysMsgError(), title="mcp")
            return
        lines = [
            f"MCP server: {name}",
            f"transport: {record['type']}",
            f"status: {'connected' if record['connected'] else 'unavailable'}",
            f"role: {_role_setting(ui, name)}",
        ]
        if record.get("error"):
            lines.append(f"error: {record['error']}")
        if record["tools"]:
            lines.append("")
            lines.append("tools:")
            lines.extend(f"  {tool.qualified}  — {tool.description}"
                         for tool in record["tools"])
        else:
            lines.append("")
            lines.append("tools: none discovered")
        ui.chat_history_panel.add_message("\n".join(lines), msg_type=SysMsg(), title="mcp")
        return

    lines = []
    for record in records:
        if record["connected"]:
            state = "connected"
        elif record.get("error"):
            state = f"unavailable ({record['error']})"
        else:
            state = "not connected"
        lines.append(
            f"{record['name']} [{record['type']}] · {state} · "
            f"{len(record['tools'])} tools · role:{_role_setting(ui, record['name'])}")
    lines.append("")
    lines.append("/mcp <server> lists a server's tools; /config mcp edits the file.")
    ui.chat_history_panel.add_message("\n".join(lines), msg_type=SysMsg(), title="mcp")


COMMAND = Command(
    "mcp", "List MCP servers and their tools (viewer)",
    handler=cmd_mcp,
    params=[Param("SERVER", required=False, completions=mcp_server_completions,
                  descriptions=mcp_server_descriptions)],
)

__all__ = ["COMMAND", "cmd_mcp", "mcp_server_completions", "mcp_server_descriptions"]
