# Writing a command

How slash commands (`/model`, `/config`, `/sandbox`…) are built. Read this before
adding or changing one. For the files and the other surfaces (`!prompt`, `@file`)
see [ui.md](./ui.md).

## The one rule

**A command is a declaration.** A `Command` carries its `description`, its
`params` and its `subcommands`, and *everything* that needs to know about its
arguments reads that declaration: dispatch (`execute`), the `/` menu, the
argument menu (`ArgumentCompletion`), descriptions, the grey hint after the
text, and `/help`. Nothing else may answer for it.

So if something works in one of those and not in another (a menu that completes
but no hint, a hint that lists options the menu does not), **the declaration is
incomplete, not the surface.** `/config` once did this: a `Command` subclass
answered `get_completions` from Python `if section == "role"` branches, so the menu
worked and the hint, which reads `params`, was blind. The subclass is gone and a
test forbids its return (below). Do not subclass `Command`.

## Where the code goes

```
moka_code/ui/commands/
  base.py      Param, Command, ChatUIProtocol, pick(), shared completion helpers
  <domain>.py  handlers and the Commands of one area (core, models, roles, sandbox, diff…)
  registry.py  COMMANDS: the one assembly point; handle_command()
```

- A domain module imports **only** `base` (`test_command_import_graph.py`).
  Anything two domain modules need goes in `base.py`; wiring two of them together
  happens in `registry.py` (e.g. `COMMANDS["config"].subcommands["sandbox"]` *is*
  `COMMANDS["sandbox"].subcommands["config"]`: one object, one set of params).
- `core.py` holds the general commands. Put a new area in its own module.

## A leaf command

A handler is `async def (ui, args: list[str])`; `args` are the words after the
command, already split.

```python
async def cmd_fix(ui: ChatUIProtocol, args: List[str]):
    ui.chat_history_panel.add_message("done", msg_type=SysMsg(), title="fix")

"fix": Command("fix", "One line, shown in the / menu and /help", handler=cmd_fix,
               params=[Param("FILE", required=True, completions=files, descriptions=files_desc),
                       Param("LEVEL", completions=["low", "high"], ordered=True)]),
```

`Param` fields and what they drive:

| Field | Effect |
|---|---|
| `name` | the hint: `<NAME>` when `required`, `[NAME]` otherwise |
| `completions` | the values the argument menu offers: a list, or a function called each time the menu updates (so it can be live) |
| `descriptions` | `value -> one-line text` shown beside each value (a dict or a function) |
| `ordered` | keep the completions' own order instead of sorting (levels, scopes) |
| `path` | complete from the workspace files (the `@` list); a typed `@` is dropped by `unmention` |

- Every param needs `completions` unless it is free text (a new name, a filename to
  write). A free-text param is listed in `FREE_TEXT` in `test_command_contract.py`
  *on purpose*; adding one without completions fails that test until you do.
- The grey hint names only the slots still to type (`<subcommand>`, `<NAME>`,
  `[LEVEL]`), never the options: the menu lists those. A slot is hinted until it is
  begun. Nothing to do for this; it falls out of `params`.
- `live_description=` takes a function for a description computed when the menu
  opens (`/diff` shows the number of changed files).
- A command whose `name` starts with `_` is left out of `/help` (only there).

## A command with subcommands

Declare a tree. Never read `args[0]` to pick a branch.

```python
"thing": Command("thing", "Things: start, stop", handler=thing_help,   # bare /thing, or an unknown word
                 subcommands={
    "start": Command("thing start", "…", handler=thing_start, params=[Param("ID", …)]),
    "stop":  Command("thing stop",  "…", handler=thing_stop),
}),
```

`execute()` resolves the deepest subcommand and calls its handler with the words
after it. `/thing ` completes and hints `<subcommand>`; `/thing start ` completes
and hints that subcommand's own params. `/sandbox` and `/config` are the
references. The root `handler` runs for the bare command and for a word that is
not a subcommand: use it for the picker or the "unknown …" error.

Build the tree in a function when its keys come from data (`config_command()`
makes one subcommand per file in `settings.CONFIG_FILES`; a test fails if a file
has none). Two parents may share one `Command` object.

## Choices: pick, or take the value

A command that selects among values follows one behaviour (guarded by
`test_command_contract.py`):

- `/cmd ` suggests the values (`Param.completions` + `descriptions`).
- bare `/cmd` opens a picker over the same values through `base.pick(ui, title,
  items, on_accept, descriptions=…, headless=…)`; `headless` shows them as text when
  there is no compositor.
- `/cmd <value>` selects directly.
- Keep one source for values and descriptions (`theme_name_completions` /
  `theme_descriptions`), used by both the `Param` and the picker.

A command that also has verbs puts the choice under a `select` subcommand instead
(`/role select [name]`, `/theme select [name]`), never as values beside its verbs:
names mixed into the `/cmd ` menu got confused with the subcommands. Its bare
`/cmd` lists the subcommands (`base.show_subcommands`).

## Things handlers must get right

- **Say what happened, in the right place.** `add_message(text, msg_type=SysMsg() |
  SysMsgWarning() | SysMsgError(), title="cmd")`. In the running app any
  `SysMsg`-family message is routed to the activity surface and a toast
  (`ChatHistoryPanel.activity_sink`), not the transcript; only `UserMsg`/answers and
  the like are transcript lines. Never swallow a failure.
- **Config files open in `$EDITOR`.** Use `open_editor`, and check `resolve_editor()`
  first (`_no_editor` in `core.py`). If the file feeds loaded state, call
  `reload_and_apply(ui, "config")` afterwards (roles, sections, sandboxes); a file
  read when used (prompts) needs no reload. No forms, no in-app editors.
- **A command that replaces or clears the history** (`/clear`, `/import`) starts with
  `if not await stop_before(ui, "/clear"): return`, so a running turn is cancelled
  and has finished saving before the history changes.
- **A command that needs the agent idle** refuses while `ui.is_generating()` says so,
  with a message that names what to do.
- Destructive commands ask first with `base.confirm_remove` (a *Remove* /
  *Cancel* picker; without one nothing is removed), as `/role remove` does.
- Handlers are plain functions of `(ui, args)`; reach the agent through `ui.agent`
  and the screen through `ui.chat_history_panel`. They must work without a
  compositor (tests, headless).

## Tests

Drive the real tree, not a copy of it:

```python
await COMMANDS["thing"].execute(ui, ["start", "x"])        # dispatch + handler
COMMANDS["thing"].subcommands["start"].get_completions(0)   # what the menu offers
COMMANDS["thing"].subcommands["start"].get_descriptions(0)
```

A fake `ui` is a `SimpleNamespace(agent=…, chat_history_panel=…)` with an
`add_message` that records text (see `_UI` in `test_config_commands.py`). For the
hint, set `InputComponent.buffer.text` and read `_get_parameter_hint()`
(`test_parameter_hints.py`).

Guards that run for every command, with no per-command work:

| Test | Fails when |
|---|---|
| `test_command_contract.py` `test_every_parameter_has_suggestions_unless_it_is_free_text` | a param has no `completions`/`path` and is not in `FREE_TEXT` |
| `test_command_contract.py` `test_every_command_is_a_plain_command_described_by_its_schema` | any command is a `Command` subclass |
| `test_command_contract.py` `test_every_config_file_is_a_config_subcommand` | a `settings.CONFIG_FILES` section has no `/config` subcommand |
| `test_command_import_graph.py` | a domain module imports another domain module, or an import cycle |
| `test_command_surface.py` | a command lacks a description, or is missing from the registry views |

## Checklist for a new command

1. Handler in the domain module; imports only `base`.
2. `Command(...)` with every argument declared as a `Param` (or a subcommand tree).
3. Registered in `registry.py`.
4. Values and descriptions from one function each, shared with any picker.
5. Errors and results through `add_message` with the right `SysMsg*` type.
6. Tests: dispatch, completions, hint; the guards above must stay green.
7. Wiki: the command in `ui.md`'s registered list and, if it is an area of its own, a
   short section.
