"""Reusable prompts: ``!name arg1 arg2`` in the input expands a markdown file.

One prompt per file, ``<name>.md``: global in ``<config>/prompts/`` and local in
the workspace's ``.moka/prompts/`` (a local one wins over a global one of the
same name). The whole file is the template; ``{moka:name}`` is a parameter
(namespaced, so braces in code or JSON are just text). The parameters are the
distinct names in order of first appearance, an argument is a bare word or a
quoted phrase, and the last parameter takes the rest of the line. The call is
the first line: what follows is the user's own text, sent after the prompt. The menu
description is the first non-empty line; a ``#`` one is only that, never sent. Files are read when used: nothing is
cached or watched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from moka_code import settings

PROMPTS_DIRNAME = "prompts"
LOCAL_DIRNAME = ".moka"
SCOPES = ("global", "local")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
#: ``!name`` at the very start of the input (a leading space sends it literally).
_CALL_RE = re.compile(r"!([A-Za-z0-9][A-Za-z0-9_-]*)(?=\s|$)")
#: ``{moka:param}``; anything else, braces included, is literal text.
_PARAM_RE = re.compile(r"\{moka:([A-Za-z_][A-Za-z0-9_]*)\}")
_HIDDEN_PREFIXES = ("_", ".")

_STARTER = """\
# Short description (shown in the ! menu)

Review {moka:file}, focusing on {moka:focus}.
"""

#: The global prompts seeded on first run (``seed_global_prompts``).
SEEDED = {"moka-config": """\
# Configure moka: sandboxes, prompts, roles, servers, settings

Help me configure moka, the terminal chat app you are running in: {moka:request}

## Where the configuration lives

- The config folder is `$MOKA_CONFIG_DIR` when it is set, else `~/.config/moka`.
- `ui.toml`, `context.toml`, `debug.toml`: settings, one commented line per key
  showing its default. Uncomment and change a line; never invent keys.
- `servers.toml`: one `[servers.<name>]` table per server, with a commented
  example per provider. `api_key = "$NAME"` reads the environment variable NAME;
  anything else is the key itself. Prefer `"$NAME"` to pasting a key.
- `roles/<name>.toml`: a role (description, prompt, color, tools no/ask/yes,
  technical settings), commented in place.
- `themes/<name>.toml`: a color palette, explained in its header.
- `state.toml` and `projects/*/project.toml` are written by moka: never edit them.

These files explain themselves: read the file and follow its comments. When a
settings or servers file is missing, ask me to run `/config <section>` (ui,
context, debug, servers): it writes the commented template.

## Sandboxes

- One file per sandbox. Global: `<config>/sandboxes/<name>.toml`. This project
  only: `<config>/projects/<dir>_<hash>/sandboxes/<name>.toml`, in the folder
  whose `project.toml` has `path` = this workspace. Names are unique across both.
- `type` is `podman`, `docker` or `bubblewrap`:
  - `bubblewrap`: the host's own binaries, read-only, and the workspace; no
    image, no build. Enough when the host already has the tools.
  - `podman` / `docker`: a container from `image`; `dockerfile` (relative to the
    sandbox file) is what `image` is built from.
- Other keys: `description` (picker), `network` (default false), `run_args`
  (runtime flags appended last; bind sources must already exist on the host),
  `instructions` (told to the model while the sandbox is active). No other key.
- For a new sandbox, ask me to run `/sandbox create <name> <type> [global|local]`:
  it writes a commented starter for that type, which you then edit. For a
  container, `/sandbox init <name> [python|debian|ubuntu]` writes a Containerfile
  next to it, which you can edit too.
- moka never builds an image by itself: after a change, I run
  `/sandbox build <name>`, then `/sandbox start <name>` (`/sandbox restart` when
  it is already active).
- Inside a sandbox the config folder is out of reach: if you cannot read it, say
  so and ask me to run `/sandbox stop`.

## Prompts

- One prompt per file, `<name>.md` (letters, digits, `_` and `-`). Global:
  `<config>/prompts/`; this project only: `.moka/prompts/` in the workspace
  (wins over a global one of the same name).
- A first line starting with `#` is the description shown in the `!` menu; it is
  not sent.
- `{moka:<name>}` is a parameter, filled from the call's arguments in order of
  first appearance: a bare word or a "quoted phrase", the last parameter taking
  the rest of the line. Any other brace is plain text.
- The call `!name args` is the first line of a message; the lines after it are
  sent after the prompt, so write the prompt to stand on its own.
- Prompts are read when used: no reload needed.

## How to work

- Read a file before changing it; change only what I asked; keep its comments.
- Ask before overwriting or deleting a file.
- Show what you changed.
- moka's slash commands are mine to run, not yours: end with the one that
  applies the change (`/reload` for settings, servers, roles and themes; the
  `/sandbox` ones above).
"""}


class PromptError(Exception):
    """The invocation cannot be expanded; the message says why."""


@dataclass(frozen=True)
class Expansion:
    """A ``!name args`` call expanded: ``prompt`` is the filled template, ``rest``
    the user's own text from the second line on (sent after the prompt)."""
    call: str
    prompt: str
    rest: str = ""

    @property
    def name(self) -> str:
        return _CALL_RE.match(self.call).group(1)

    @property
    def text(self) -> str:
        """What is sent."""
        return f"{self.prompt}\n\n{self.rest}" if self.rest else self.prompt

    @property
    def typed(self) -> str:
        """What the user typed (``[e]`` edit puts it back)."""
        return f"{self.call}\n\n{self.rest}" if self.rest else self.call

    @property
    def meta(self) -> Dict[str, Any]:
        """The history entry's ``prompt`` field: ``text`` is ``prompt`` then ``rest``."""
        return {"call": self.call, "chars": len(self.prompt)}


def from_history(content: str, meta: Any) -> Optional[Expansion]:
    """The expansion a history entry was sent from (its ``prompt`` field), if any."""
    if not (isinstance(meta, dict) and isinstance(meta.get("call"), str)
            and isinstance(meta.get("chars"), int) and _CALL_RE.match(meta["call"])):
        return None
    chars = meta["chars"]
    return Expansion(meta["call"], content[:chars], content[chars:].strip())


@dataclass(frozen=True)
class Prompt:
    name: str
    scope: str                      # "global" or "local"
    path: Path

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def body(self) -> str:
        """The template sent: the text without its ``#`` description line."""
        return body_of(self.text)

    @property
    def params(self) -> List[str]:
        return params_of(self.body)

    @property
    def description(self) -> str:
        for line in self.text.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                return line
        return ""


# --- locations ----------------------------------------------------------------

def scope_dir(scope: str, workspace: str | Path) -> Path:
    if scope == "global":
        return settings.get_config_dir() / PROMPTS_DIRNAME
    if scope == "local":
        return Path(workspace) / LOCAL_DIRNAME / PROMPTS_DIRNAME
    raise ValueError(f"Scope must be one of {', '.join(SCOPES)}")


def validate_name(name: str) -> str:
    name = name.strip()
    if not _NAME_RE.match(name):
        raise ValueError(
            "Prompt name may only contain letters, digits, '_' and '-' "
            "and must start with a letter or digit")
    return name


# --- reading --------------------------------------------------------------------

def list_prompts(workspace: str | Path) -> Dict[str, Prompt]:
    """Every prompt by name, sorted; a local file replaces a global one."""
    found: Dict[str, Prompt] = {}
    for scope in SCOPES:
        directory = scope_dir(scope, workspace)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.stem.startswith(_HIDDEN_PREFIXES) or not _NAME_RE.match(path.stem):
                continue
            found[path.stem] = Prompt(path.stem, scope, path)
    return dict(sorted(found.items()))


def body_of(text: str) -> str:
    """``text`` without a ``#`` description line (its first non-empty line)."""
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None or not lines[first].lstrip().startswith("#"):
        return text
    return "\n".join(lines[first + 1:])


def params_of(template: str) -> List[str]:
    """The distinct parameter names of ``template``, in order of first appearance."""
    names: List[str] = []
    for match in _PARAM_RE.finditer(template):
        if match.group(1) not in names:
            names.append(match.group(1))
    return names


def render(template: str, values: Dict[str, str]) -> str:
    return _PARAM_RE.sub(lambda match: values[match.group(1)], template)


# --- invoking -------------------------------------------------------------------

def _take(rest: str) -> tuple[str, str]:
    """The next argument of ``rest`` (bare word or quoted phrase) and what follows."""
    rest = rest.lstrip()
    if rest[:1] in ("'", '"'):
        end = rest.find(rest[0], 1)
        if end != -1:
            return rest[1:end], rest[end + 1:]
    word = re.match(r"\S+", rest)
    return word.group(0), rest[word.end():]


def split_args(rest: str, count: int) -> List[str]:
    """``rest`` as at most ``count`` arguments; the last one is the remainder."""
    values: List[str] = []
    while len(values) < count - 1 and rest.strip():
        value, rest = _take(rest)
        values.append(value)
    rest = rest.strip()
    if count and len(values) == count - 1 and rest:
        quoted = rest[0] in ("'", '"') and rest.endswith(rest[0]) and len(rest) > 1
        values.append(rest[1:-1] if quoted and rest.count(rest[0]) == 2 else rest)
    elif not count and rest:
        values.append(rest)             # reported as an error by the caller
    return values


def expand(text: str, workspace: str | Path,
           clean_args: Callable[[str], str] = lambda arguments: arguments) -> Optional[Expansion]:
    """``text``'s ``!name args`` call expanded, or ``None`` when ``text`` is not a
    call (it is sent as typed). The arguments end with the first line; the lines
    after it are the user's own text. Raises ``PromptError`` for an unknown
    prompt or the wrong arguments. ``clean_args`` prepares the arguments (the UI
    turns ``@path`` mentions into plain paths)."""
    line, _, rest = text.partition("\n")
    call = _CALL_RE.match(line)
    if call is None:
        return None
    name = call.group(1)
    prompts = list_prompts(workspace)
    if name not in prompts:
        available = ", ".join(f"!{n}" for n in prompts) or "none (see /prompt)"
        raise PromptError(f"Unknown prompt: !{name}. Available: {available}")
    template = prompts[name].body
    names = params_of(template)
    values = split_args(clean_args(line[call.end():]), len(names))
    if not names and values:
        raise PromptError(f"!{name} takes no arguments")
    if len(values) < len(names):
        missing = " ".join(f"<{n}>" for n in names[len(values):])
        raise PromptError(f"!{name} needs {' '.join(f'<{n}>' for n in names)}; missing {missing}")
    prompt = render(template, dict(zip(names, values))).strip()
    return Expansion(line.rstrip(), prompt, clean_args(rest).strip())


# --- creating -------------------------------------------------------------------

def seed_global_prompts() -> list[Path]:
    """First run: write the ``SEEDED`` prompts into the global folder. Only when
    it does not exist yet (a deleted prompt stays deleted, like a role)."""
    directory = settings.get_config_dir() / PROMPTS_DIRNAME
    if directory.exists():
        return []
    directory.mkdir(parents=True)
    written = []
    for name, text in SEEDED.items():
        path = directory / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def ensure_prompt_file(name: str, scope: str, workspace: str | Path) -> Path:
    """The prompt's file in ``scope``, created from a starter when missing."""
    path = scope_dir(scope, workspace) / f"{validate_name(name)}.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_STARTER, encoding="utf-8")
    return path
