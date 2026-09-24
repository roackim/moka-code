"""
Minimal tool implementations for LLM harness.

Provides 4 core tools:
- read: Read file content
- write: Write file content
- edit: Replace an exact text block in a file
- bash: Execute shell command in the workspace
"""
import inspect
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from pico_chat.harness.patch_parser import parse_patch, apply_patch, PatchParseError


class ToolError(Exception):
    """Base exception for tool errors"""
    pass


class FileTools:
    """File operation tools (read, write, edit)"""

    MAX_PATCH_REPLACEMENT_CHARS = 100_000
    MAX_PATCH_LINE_DELTA = 500
    
    def __init__(self, workspace_path: str | Path):
        """
        Args:
            workspace_path: Root directory for file operations
        """
        self.workspace = Path(workspace_path).resolve()

    def _validate_path(self, path: str) -> Path:
        """
        Validate and resolve path.

        Args:
            path: File path (relative to workspace or absolute)

        Returns:
            Absolute resolved path

        Raises:
            ToolError: If path is invalid
        """
        try:
            if Path(path).is_absolute():
                return Path(path).resolve()
            return (self.workspace / path).resolve()
        except Exception as e:
            raise ToolError(f"Invalid path '{path}': {e}")
    
    def read(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        max_chars: int | None = None,
        include_line_numbers: bool = False,
    ) -> str:
        """
        Read file content.
        
        Args:
            path: File path relative to workspace or absolute
            offset: Zero-based first line to return
            limit: Optional number of lines to return
            max_chars: Optional maximum size of the returned content
            include_line_numbers: Prefix each returned line with its source line
                number
            
        Returns:
            File content as string
            
        Raises:
            ToolError: If permission denied or file cannot be read
            
        Example:
            >>> tools.read("config.py")
            'import os\\n...'
        """
        for name, value in (("offset", offset), ("limit", limit), ("max_chars", max_chars)):
            minimum = 0 if name == "offset" else 1
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < minimum):
                expectation = "a non-negative integer" if name == "offset" else "a positive integer"
                raise ToolError(f"Invalid {name}: expected {expectation}")

        target = self._validate_path(path)

        if not target.exists():
            raise ToolError(f"File not found: {path}")
        
        if not target.is_file():
            raise ToolError(f"Not a file: {path}")
        
        try:
            content = target.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            raise ToolError(f"File is not UTF-8 text: {path}")
        except Exception as e:
            raise ToolError(f"Error reading file: {e}")

        # Keep line endings while slicing so a selected block can be copied
        # directly into the edit tool.
        lines = content.splitlines(keepends=True)
        first = offset
        last = offset + limit if limit is not None else len(lines)
        selected = lines[first:last]

        if include_line_numbers:
            selected = [f"{number:>6}\t{line}" for number, line in zip(range(first + 1, last + 1), selected)]

        result = "".join(selected)
        if max_chars is not None and len(result) > max_chars:
            result = result[:max_chars] + f"\n[truncated: showing {max_chars} of {len(result)} characters]"
        return result
    
    def write(self, path: str, content: str) -> str:
        """
        Write file content (creates or overwrites).
        
        Args:
            path: File path relative to workspace or absolute
            content: Content to write
            
        Returns:
            Success message
            
        Raises:
            ToolError: If permission denied or file cannot be written
            
        Example:
            >>> tools.write("script.py", "print('hello')")
            '[OK] Wrote 14 bytes to script.py'
        """
        target = self._validate_path(path)

        # Create parent directories if needed
        target.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            target.write_text(content, encoding='utf-8')
            byte_count = len(content.encode('utf-8'))
            return f"[OK] Wrote {byte_count} bytes to {path}"
        except Exception as e:
            raise ToolError(f"Error writing file: {e}")
    
    def edit(self, path: str, search: str, replace: str) -> str:
        """
        Replace one exact text block in a file.

        Args:
            path: File path relative to workspace or absolute
            search: Exact existing text block to replace (include enough
                context to be unique)
            replace: Replacement text block

        Returns:
            Success or error message

        Raises:
            ToolError: If the block cannot be applied

        Example:
            >>> tools.edit("app.py", "old code", "new code")
            '[OK] Applied edit to app.py (1 replacement)'
        """
        if search is None:
            raise ToolError("Invalid edit arguments: missing 'search'")
        if replace is None:
            raise ToolError("Invalid edit arguments: missing 'replace'")

        try:
            patch = parse_patch(
                f"{path}\n"
                "<<<<<<< SEARCH\n"
                f"{search}\n"
                "=======\n"
                f"{replace}\n"
                ">>>>>>> REPLACE"
            )
        except PatchParseError as e:
            raise ToolError(f"Invalid edit: {e}")

        # Guardrails: replacement size and line delta constraints
        replacement_chars = len(patch.replace_text)
        if replacement_chars > self.MAX_PATCH_REPLACEMENT_CHARS:
            raise ToolError(
                f"Edit rejected: replacement too large ({replacement_chars} chars > {self.MAX_PATCH_REPLACEMENT_CHARS})"
            )

        search_line_count = patch.search_text.count('\n') + 1 if patch.search_text else 0
        replace_line_count = patch.replace_text.count('\n') + 1 if patch.replace_text else 0
        line_delta = abs(replace_line_count - search_line_count)
        if line_delta > self.MAX_PATCH_LINE_DELTA:
            raise ToolError(
                f"Edit rejected: line delta too large ({line_delta} lines > {self.MAX_PATCH_LINE_DELTA})"
            )
        
        # Read current file
        try:
            current_content = self.read(patch.filename)
        except ToolError as e:
            raise ToolError(f"Cannot read file for editing: {e}")

        # Apply patch
        new_content, message = apply_patch(current_content, patch)
        
        # If successful, write back
        if message.startswith('[OK]'):
            self.write(patch.filename, new_content)
        
        return message


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
        """
        Execute shell command in workspace.
        
        Args:
            command: Shell command to execute
            timeout: Maximum execution time in seconds
            
        Returns:
            Command output (stdout/stderr combined) with metadata
            
        Raises:
            ToolError: If permission denied or command execution fails
            
        Example:
            >>> tool.run("ls -la")
            '[stdout]\\nfile.txt\\n[exit:0 | 0.1ms]'
        """
        # Execute command
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            # Format output
            output_parts = []
            
            if result.stdout:
                output_parts.append(f"[stdout]\n{result.stdout.rstrip()}")
            
            if result.stderr:
                output_parts.append(f"[stderr]\n{result.stderr.rstrip()}")
            
            # Add exit code and timing
            output_parts.append(f"[exit:{result.returncode}]")
            
            return '\n'.join(output_parts) if output_parts else "[exit:0]"
            
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {timeout}s")
        except Exception as e:
            raise ToolError(f"Command execution failed: {e}")

    async def run_async(self, command: str, timeout: int = 30) -> str:
        """Cancellable async version of :meth:`run`.

        Runs the command as a subprocess whose handle is stored on
        ``self._active_proc`` so a "stop" request can terminate it
        mid-flight. Returns the same formatted output as :meth:`run`.
        """
        import asyncio

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                shell=True,
                cwd=self.workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # own process group so stop kills children
            )
            self._active_proc = proc

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                self._kill_process_group(proc)
                await proc.communicate()
                raise ToolError(f"Command timed out after {timeout}s")
            finally:
                if self._active_proc is proc:
                    self._active_proc = None

            stdout = (stdout or b"").decode("utf-8", errors="replace").rstrip()
            stderr = (stderr or b"").decode("utf-8", errors="replace").rstrip()

            output_parts = []
            if stdout:
                output_parts.append(f"[stdout]\n{stdout}")
            if stderr:
                output_parts.append(f"[stderr]\n{stderr}")
            output_parts.append(f"[exit:{proc.returncode}]")
            return '\n'.join(output_parts) if output_parts else "[exit:0]"
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Command execution failed: {e}")

    def cancel_active(self) -> bool:
        """Terminate the currently-running command, if any.

        Kills the whole process group so child processes (e.g. ``sleep``)
        are terminated too. Returns True if a process was terminated.
        """
        if self._active_proc is not None and self._active_proc.returncode is None:
            try:
                self._kill_process_group(self._active_proc)
                return True
            except Exception:
                return False
        return False

    @staticmethod
    def _kill_process_group(proc) -> None:
        """Kill a subprocess and its entire process group (best effort)."""
        import os
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


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

    async def run_async(self, command: str, timeout: int = 30) -> str:
        """Execute shell command asynchronously (cancellable)."""
        return await self.shell_tool.run_async(command, timeout)

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


class RegisteredTool:
    """A registry tool bound to a :class:`MinimalToolset`."""

    def __init__(self, definition: ToolDefinition, toolset: MinimalToolset):
        self._definition = definition
        self.toolset = toolset
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
        cancel = getattr(self.toolset, "cancel_active_run", None)
        return cancel() if callable(cancel) else False

    async def execute(self, **kwargs):
        """Run the tool. Prefers the async handler so shell commands stay
        cancellable; awaits the result if the chosen handler is a coroutine."""
        handler = self._definition.async_handler or self._definition.handler
        result = handler(self.toolset, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RegisteredTool {self.name}>"


def registered_tool_names() -> list[str]:
    """Return every registered tool name (the key the role file uses)."""
    return list(_REGISTRY.keys())


def create_toolset(workspace_path: str | Path) -> dict[str, RegisteredTool]:
    """
    Create the registered toolset.

    Args:
        workspace_path: Root directory for all operations

    Returns:
        Dict of tool name to registered tool
    """
    toolset = MinimalToolset(workspace_path)
    return {
        name: RegisteredTool(definition, toolset)
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


async def _bash_tool_async(toolset: MinimalToolset, command: str) -> str:
    try:
        return await toolset.run_async(command)
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
