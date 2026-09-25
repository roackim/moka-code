"""
Tool schemas and host-side bindings for LLM harness.

The tool *bodies* live in :mod:`pico_chat.worker` (stdlib-only, shared with the
sandbox worker).  This module keeps the LLM-facing registry — names,
descriptions, JSON schemas — and binds each one to a :class:`MinimalToolset`.

Provides 4 core tools:
- read: Read file content
- write: Write file content
- edit: Replace an exact text block in a file
- bash: Execute shell command in the workspace
"""
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from pico_chat.worker import (
    ToolError,
    bash as _worker_bash,
    bash_sync as _worker_bash_sync,
    edit as _worker_edit,
    kill_process_group,
    read as _worker_read,
    write as _worker_write,
)


class FileTools:
    """File operation tools (read, write, edit) bound to a workspace root."""

    def __init__(self, workspace_path: str | Path):
        """
        Args:
            workspace_path: Root directory for file operations
        """
        self.workspace = Path(workspace_path).resolve()

    def read(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        max_chars: int | None = None,
        include_line_numbers: bool = False,
    ) -> str:
        """Read file content."""
        return _worker_read(
            path,
            cwd=self.workspace,
            offset=offset,
            limit=limit,
            max_chars=max_chars,
            include_line_numbers=include_line_numbers,
        )

    def write(self, path: str, content: str) -> str:
        """Write file content (creates or overwrites)."""
        return _worker_write(path, content, cwd=self.workspace)

    def edit(self, path: str, search: str, replace: str) -> str:
        """Replace one exact text block in a file."""
        return _worker_edit(path, search, replace, cwd=self.workspace)


class ShellTool:
    """Execute shell commands in the workspace."""

    def __init__(self, workspace_path: str | Path):
        """
        Args:
            workspace_path: Working directory for command execution
        """
        self.workspace = Path(workspace_path).resolve()

        # Handle to the currently-running command (for stop/cancellation).
        self._active_proc: Optional["asyncio.subprocess.Process"] = None

    def run(self, command: str, timeout: int = 30) -> str:
        """Execute shell command in workspace (blocking)."""
        return _worker_bash_sync(command, cwd=self.workspace, timeout=timeout)

    async def run_async(self, command: str, timeout: int = 30, on_output=None) -> str:
        """Cancellable async version of :meth:`run`.

        Delegates to :func:`pico_chat.worker.bash` and records the spawned
        process on ``self._active_proc`` so a "stop" request can terminate it.
        ``on_output(stream, chunk)`` forwards interim output.
        """
        def _on_spawn(proc):
            self._active_proc = proc

        return await _worker_bash(
            command,
            cwd=self.workspace,
            timeout=timeout,
            on_spawn=_on_spawn,
            on_output=on_output,
        )

    def cancel_active(self) -> bool:
        """Terminate the currently-running command, if any.

        Kills the whole process group so child processes (e.g. ``sleep``)
        are terminated too. Returns True if a process was terminated.
        """
        if self._active_proc is not None and self._active_proc.returncode is None:
            try:
                kill_process_group(self._active_proc)
                return True
            except Exception:
                return False
        return False


class MinimalToolset:
    """
    Complete minimal toolset for LLM agents.

    Provides read, write, edit, and bash tools.
    """

    def __init__(self, workspace_path: str | Path):
        """
        Args:
            workspace_path: Root directory for all operations
        """
        workspace = Path(workspace_path).resolve()

        self.file_tools = FileTools(workspace)
        self.shell_tool = ShellTool(workspace)

    def read(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        max_chars: int | None = None,
        include_line_numbers: bool = False,
    ) -> str:
        """Read all or part of a file."""
        return self.file_tools.read(
            path,
            offset=offset,
            limit=limit,
            max_chars=max_chars,
            include_line_numbers=include_line_numbers,
        )

    def write(self, path: str, content: str) -> str:
        """Write file content"""
        return self.file_tools.write(path, content)

    def edit(self, path: str, search: str, replace: str) -> str:
        """Replace an exact text block in a file."""
        return self.file_tools.edit(path, search, replace)

    def run(self, command: str, timeout: int = 30) -> str:
        """Execute shell command"""
        return self.shell_tool.run(command, timeout)

    async def run_async(self, command: str, timeout: int = 30, on_output=None) -> str:
        """Execute shell command asynchronously (cancellable)."""
        return await self.shell_tool.run_async(command, timeout, on_output=on_output)

    def cancel_active_run(self) -> bool:
        """Terminate the currently-running shell command, if any."""
        return self.shell_tool.cancel_active()


# ---------------------------------------------------------------------------
# Tool registry
#
# Each tool is declared once with the ``@tool`` decorator, which carries its
# name, LLM-facing schema and handler.  ``create_toolset`` binds those
# definitions to a :class:`MinimalToolset` and returns the harness-facing
# objects.
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """A registered tool: its LLM-facing schema and handler(s)."""

    name: str
    description: str
    parameters: dict
    handler: Callable[["MinimalToolset", Any], Any]
    async_handler: Optional[Callable[["MinimalToolset", Any], Any]] = None


_REGISTRY: dict[str, ToolDefinition] = {}


def tool(*, name: str, description: str, parameters: dict,
         async_handler: Optional[Callable] = None):
    """Register a tool definition.  One decorator per tool — the single
    definition site for its name and schema."""

    def decorator(handler):
        _REGISTRY[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            async_handler=async_handler,
        )
        return handler

    return decorator


class ToolTransport(Protocol):
    """How a registered tool is executed.

    Bare mode runs the body in-process; the sandbox mode (added later) sends
    the request over JSONL to the worker.  The harness only knows this
    interface, so the registry and schemas are identical in both modes.
    """

    async def execute(self, name: str, args: dict, on_output=None) -> str: ...

    def cancel_active(self, name: str) -> bool: ...


class InProcessTransport:
    """Bare-mode transport: runs the registered handler against a toolset."""

    is_sandbox = False

    def __init__(self, toolset: MinimalToolset):
        self.toolset = toolset

    async def execute(self, name: str, args: dict, on_output=None) -> str:
        definition = _REGISTRY[name]
        handler = definition.async_handler or definition.handler
        if on_output is not None and name == "bash":
            args = {**args, "on_output": on_output}
        result = handler(self.toolset, **args)
        if inspect.isawaitable(result):
            result = await result
        return result

    def cancel_active(self, name: str) -> bool:
        if name == "bash":
            return self.toolset.cancel_active_run()
        return False


class RegisteredTool:
    """A registry tool bound to a :class:`ToolTransport`."""

    def __init__(self, definition: ToolDefinition, transport: ToolTransport):
        self._definition = definition
        self.transport = transport
        self.name = definition.name
        self.description = definition.description
        self.parameters = definition.parameters

    def get_schema(self) -> dict:
        """Return the OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def cancel_active_run(self) -> bool:
        """Terminate the tool's active subprocess, if it owns one."""
        return self.transport.cancel_active(self.name)

    async def execute(self, on_output=None, **kwargs):
        """Run the tool through the transport bound at build time.

        ``on_output`` is the interim-output callback (used by ``bash``); it is
        kept out of the tool arguments.
        """
        return await self.transport.execute(self.name, kwargs, on_output=on_output)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RegisteredTool {self.name}>"


def registered_tool_names() -> list[str]:
    """Return every registered tool name (the key the role file uses)."""
    return list(_REGISTRY.keys())


def create_toolset(
    workspace_path: str | Path,
    transport: Optional[ToolTransport] = None,
) -> dict[str, RegisteredTool]:
    """
    Create the registered toolset.

    Args:
        workspace_path: Root directory for all operations (used when building
            the default in-process transport)
        transport: Optional transport; defaults to an
            :class:`InProcessTransport` over ``workspace_path``

    Returns:
        Dict of tool name to registered tool
    """
    if transport is None:
        transport = InProcessTransport(MinimalToolset(workspace_path))
    return {
        name: RegisteredTool(definition, transport)
        for name, definition in _REGISTRY.items()
    }


# --- Tool handlers ---------------------------------------------------------

@tool(
    name="read",
    description=(
        "Read all or part of a UTF-8 text file from the workspace. "
        "Use offset/limit for large files or targeted inspection. Offset "
        "is zero-based and limit is the number of lines. Use "
        "include_line_numbers when you need stable references for an edit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to workspace (e.g., 'config.py' or 'src/main.py')",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Optional zero-based first line to return (defaults to 0)",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional number of lines to return",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional maximum number of characters to return",
            },
            "include_line_numbers": {
                "type": "boolean",
                "description": "Prefix each returned line with its source line number",
            },
        },
        "required": ["path"],
    },
)
def _read_tool(
    toolset: MinimalToolset,
    path: str,
    offset: int = 0,
    limit: int | None = None,
    max_chars: int | None = None,
    include_line_numbers: bool = False,
) -> str:
    try:
        return toolset.read(
            path,
            offset=offset,
            limit=limit,
            max_chars=max_chars,
            include_line_numbers=include_line_numbers,
        )
    except ToolError as e:
        return str(e)


@tool(
    name="write",
    description="Write content to a file in the workspace (creates or overwrites)",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace"},
            "content": {"type": "string", "description": "Content to write to the file"},
        },
        "required": ["path", "content"],
    },
)
def _write_tool(toolset: MinimalToolset, path: str, content: str) -> str:
    try:
        return toolset.write(path, content)
    except ToolError as e:
        return str(e)


@tool(
    name="edit",
    description=(
        "Modify an existing file by replacing one exact text block. "
        "Provide path + search + replace; use write only for creating new "
        "files or full rewrites. Fails if search does not match exactly."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace"},
            "search": {
                "type": "string",
                "description": "Exact existing text block to replace (include enough context to be unique)",
            },
            "replace": {"type": "string", "description": "Replacement text block"},
        },
        "required": ["path", "search", "replace"],
    },
)
def _edit_tool(toolset: MinimalToolset, path: str, search: str, replace: str) -> str:
    try:
        return toolset.edit(path, search, replace)
    except ToolError as e:
        return str(e)


async def _bash_tool_async(toolset: MinimalToolset, command: str, on_output=None) -> str:
    try:
        return await toolset.run_async(command, on_output=on_output)
    except ToolError as e:
        return str(e)


@tool(
    name="bash",
    description=(
        "Execute a shell command in the workspace. "
        "Supports pipes (|), command chaining (&&, ||, ;)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute (e.g., 'ls -la', 'cat file.txt | grep pattern')",
            }
        },
        "required": ["command"],
    },
    async_handler=_bash_tool_async,
)
def _bash_tool(toolset: MinimalToolset, command: str) -> str:
    try:
        return toolset.run(command)
    except ToolError as e:
        return str(e)


__all__ = [
    "ToolError",
    "FileTools",
    "ShellTool",
    "MinimalToolset",
    "ToolDefinition",
    "RegisteredTool",
    "tool",
    "registered_tool_names",
    "create_toolset",
]
