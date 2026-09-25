# Tools and Permissions

The tool system exposes file and shell operations to the LLM agent. Every tool
call goes through the approval gate before execution.

pico implements **no sandbox policy** — there is no permission engine, no
command allowlist, no path confinement, no policy engine. A tool is either
disabled, asks, or auto-approves. Isolation is the user's choice via a
per-project sandbox (`/sandbox`, containers/bubblewrap); see
[sandbox.md](./sandbox.md), [security.md](./security.md), and
`plans/sandbox_worker.md`.

---

## Tools

Four tools, declared in `tools.py` (schemas) with bodies in `worker.py`:

| Tool | Operation |
|------|-----------|
| `read` | Read all or part of a UTF-8 text file |
| `write` | Create or overwrite a file |
| `edit` | Replace one exact text block in a file |
| `bash` | Run a shell command in the workspace |

`read` accepts optional `offset` (zero-based first line) and `limit` (number of
lines) values for targeted reads, `max_chars` for bounded output, and
`include_line_numbers` for stable source references when preparing an edit.
The default call remains a complete, unnumbered file read for compatibility.

`bash` runs through `ShellTool`, whose `run_async` path is cancellable: `/stop`
(or the stop action) terminates the process group. It also streams: `on_output
(stream, chunk)` forwards interim stdout/stderr, the harness emits a
`ToolOutput` event per chunk, and the UI routes them to the activity surface.
The same callback path serves in-process and sandboxed `bash`.

The tool *bodies* are stateless functions in `worker.py` (stdlib-only). The
`Harness` owns all state and passes a `cwd` in. The only stateful adapter is
`ShellTool`, which remembers the live process so it can be cancelled; the tool
layer never checks permissions itself — the gate decides before a tool runs.

`worker.py` also runs as a standalone script inside a container/bubblewrap:
`serve()` speaks a JSONL request/response protocol on stdin/stdout (stdout is
protocol-only). The host-side `ToolTransport` seam keeps the registry and
schemas identical whether execution is in-process or sandboxed. See
[sandbox.md](./sandbox.md).

## Tool Registry (`tools.py`)

Each tool is declared once with the `@tool` decorator, which carries its name,
OpenAI function schema, and handler:

- `ToolDefinition` / `RegisteredTool` — registry record and the bound instance
  returned to the harness.
- `get_schema()` — returns the function schema for the LLM.
- `RegisteredTool.execute()` — an async dispatcher that delegates to the bound
  `ToolTransport`.

`registered_tool_names()` is the canonical registry view: the list of tool names
a role file lists.

`ToolTransport` is the execution seam: `execute(name, args, on_output=None)`
(bash streams chunks through `on_output`) and `cancel_active(name)`;
`is_sandbox` marks a real sandbox transport (drives the role lock). `InProcessTransport` (bare mode) runs the registered
handler against one `MinimalToolset` — preferring the async handler so shell
commands stay cancellable. `create_toolset(workspace_path, transport=None)` —
factory that builds an `InProcessTransport` when none is given. Registers
`read`, `write`, `edit`, `bash`.

## Approval Flow

```
LLM generates tool call
        ↓
Harness._execute_tool_calls()
        ↓
PermissionGate.check(tool, args)          ← the single decision point
        ↓
  role's per-tool value: no → deny · ask → prompt · yes → allow
        ↓
  deny → blocked, error returned to the LLM
  ask  → UI shows permission prompt, awaits user response
  allow→ RegisteredTool.execute(args) → transport
        ↓
result appended to conversation history
```

## Roles (`roles.py`, `permissions.py`)

`Role` is the single source of truth for a conversation's enabled tools and
per-tool approval setting:

- Each tool is exactly one of `no` / `ask` / `yes`:
  - `no` — disabled; **not put in `tool_schemas`**, so the model never sees it.
  - `ask` — enabled; the harness prompts before the call.
  - `yes` — enabled; runs without prompting.
- There is no `deny`: a tool you would always deny is simply `no`.
- `enabled_tool_names()` returns every value except `no`; `permission_for(name)`
  returns the raw value.

`PermissionGate` (`permissions.py`) maps the value to the harness decision
(`allow` / `ask` / `deny`), builds the prompt (`build_prompt()`), and owns the
async queue the UI uses to deliver the user's answer on the `ask` path.

### Role files

One file per role at `<config>/roles/<name>.toml`. The file name is the role
name; the body is `description` / `prompt` plus one `<tool> = "no" | "ask" |
"yes"` entry per registered tool. The file lists every available tool so the
user can comment/uncomment or edit values — the "all options visible" style of
`servers.toml`.

Built-in roles (`agent`, `chat`) are seeded as files on first run by
`ensure_roles_dir()`; a code fallback exists for both. `create_role(name)` writes
a template from the registry (all tools `no`); `delete_role(name)` unlinks the
file, refusing to remove the last role.

`ensure_roles_dir()` also migrates retired tool keys in existing role files,
preserving comments and everything else: `patch → edit`, `run_command → bash`,
and the removed `subagent` / `wait_for_subagents` keys are dropped. The same
aliases are applied on load, so an unmigrated file still round-trips.

Unknown tool names and values other than `no`/`ask`/`yes` are reported as
`roles/<name>.toml: ...` by `validate_roles()`, surfaced by `/reload` and
`/config role`.

### Command surface

| Command | Behavior |
|---------|----------|
| `/role` | list roles, mark the active one |
| `/role <name>` | switch the active role |
| `/config role <id>` | ensure the file exists, open in `$EDITOR`, reload |
| `/config role delete <id> [confirm]` | confirm, then unlink |

## Edit Tool (`worker.py`)

The `edit` tool takes `path` + `search` + `replace`; it assembles an
aider-style search/replace block internally:
```
<<<<<<< SEARCH
old content
=======
new content
>>>>>>> REPLACE
```

`parse_patch()` extracts the block and `apply_patch()` applies it with a
3-mode cascade: exact → whitespace-normalized → indentation-normalized. It fails
if the `SEARCH` block is not found or is ambiguous. Both functions (and
`PatchBlock` / `PatchParseError`) now live in `worker.py`, folded from the
deleted `harness/patch_parser.py`.
