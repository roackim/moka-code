"""Reusable prompts: ``!name arg1 arg2`` in the input expands a markdown file.

One prompt per file, ``<name>.md``: global in ``<config>/prompts/`` and local in
the workspace's ``.moka/prompts/`` (a local one wins over a global one of the
same name). The whole file is the template; ``{name}`` is a parameter. The
parameters are the distinct names in order of first appearance, an argument
is a bare word or a quoted phrase, and the last parameter takes the rest of the
line. ``{{`` and ``}}`` are literal braces. The menu description is the first
non-empty line. Files are read when used: nothing is cached or watched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from moka_code import settings

PROMPTS_DIRNAME = "prompts"
LOCAL_DIRNAME = ".moka"
SCOPES = ("global", "local")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
#: ``!name`` at the very start of the input (a leading space sends it literally).
_CALL_RE = re.compile(r"!([A-Za-z0-9][A-Za-z0-9_-]*)(?=\s|$)")
#: ``{{``, ``}}`` or ``{param}``.
_TOKEN_RE = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
_HIDDEN_PREFIXES = ("_", ".")

_STARTER = """\
# Short description (shown in the ! menu)

Review {file}, focusing on {focus}.
"""


class PromptError(Exception):
    """The invocation cannot be expanded; the message says why."""


@dataclass(frozen=True)
class Prompt:
    name: str
    scope: str                      # "global" or "local"
    path: Path

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def params(self) -> List[str]:
        return params_of(self.text)

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


def params_of(template: str) -> List[str]:
    """The distinct parameter names of ``template``, in order of first appearance."""
    names: List[str] = []
    for match in _TOKEN_RE.finditer(template):
        if match.group(1) and match.group(1) not in names:
            names.append(match.group(1))
    return names


def render(template: str, values: Dict[str, str]) -> str:
    def substitute(match: re.Match) -> str:
        if match.group(1):
            return values[match.group(1)]
        return match.group(0)[0]            # "{{" -> "{", "}}" -> "}"

    return _TOKEN_RE.sub(substitute, template)


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
           clean_args: Callable[[str], str] = lambda arguments: arguments) -> Optional[str]:
    """``text`` with its ``!name args`` call expanded, or ``None`` when ``text``
    is not a call (it is sent as typed). Raises ``PromptError`` for an unknown
    prompt or the wrong arguments. ``clean_args`` prepares the arguments (the UI
    turns ``@path`` mentions into plain paths)."""
    call = _CALL_RE.match(text)
    if call is None:
        return None
    name = call.group(1)
    prompts = list_prompts(workspace)
    if name not in prompts:
        available = ", ".join(f"!{n}" for n in prompts) or "none (see /prompt)"
        raise PromptError(f"Unknown prompt: !{name}. Available: {available}")
    template = prompts[name].text
    names = params_of(template)
    values = split_args(clean_args(text[call.end():]), len(names))
    if not names and values:
        raise PromptError(f"!{name} takes no arguments")
    if len(values) < len(names):
        missing = " ".join(f"<{n}>" for n in names[len(values):])
        raise PromptError(f"!{name} needs {' '.join(f'<{n}>' for n in names)}; missing {missing}")
    return render(template, dict(zip(names, values)))


# --- creating -------------------------------------------------------------------

def ensure_prompt_file(name: str, scope: str, workspace: str | Path) -> Path:
    """The prompt's file in ``scope``, created from a starter when missing."""
    path = scope_dir(scope, workspace) / f"{validate_name(name)}.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_STARTER, encoding="utf-8")
    return path
