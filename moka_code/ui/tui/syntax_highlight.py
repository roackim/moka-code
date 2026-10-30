"""Minimal syntax highlighter for code blocks in the TUI.

A small regex scanner per line, no grammar: keywords, functions, strings,
comments, numbers and constants, types, decorators, variables, data keys,
plus line-based diffs and a little markup. Strings and comments that span
lines carry a state from one line to the next (``scan``). Fast enough for
live re-parsing during streaming.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from moka_code.ui.tui.colors import RGB, theme


def _get_highlight_color(hl_type: str):
    """The color of a highlight type from config, or None for plain text: a
    theme color name (``"KEYWORD"``, follows the active theme) or a hex
    string (an RGB tuple)."""
    from moka_code import settings
    style = settings.config.syntax_highlight_styles.get(hl_type, {})
    value = style.get("fg")
    if not value:
        return None
    if not value.startswith("#") and hasattr(theme, value):
        return getattr(theme, value)
    c = RGB(value)
    return (c.r, c.g, c.b)


# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------

def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


@dataclass(frozen=True)
class _Lang:
    keywords: frozenset[str]
    constants: frozenset[str] = frozenset()      # drawn as numbers
    types: frozenset[str] = frozenset()          # builtin type names
    line_comments: Tuple[str, ...] = ()
    block_comment: Optional[Tuple[str, str]] = None
    quotes: str = "\"'"
    multiline_quotes: str = ""                   # may span lines (JS `...`)
    triple: bool = False                         # Python / TOML """...""" and '''...'''
    capitalized_types: bool = True               # Name -> type, NAME -> constant
    decorators: str = ""                         # "@" or "#[" (Rust attributes)
    variables: bool = False                      # $name, ${name}
    keys: str = ""                               # "json" | "yaml" | "toml"
    lifetimes: bool = False                      # Rust 'a
    macros: bool = False                         # Rust name!(...)
    preprocessor: bool = False                   # C #include
    ignore_case: bool = False                    # SQL
    operators: str = "code"                      # "code" | "shell" | "" (none)


_C_LIKE = dict(line_comments=("//",), block_comment=("/*", "*/"))

_LANGS: dict[str, _Lang] = {
    "python": _Lang(
        _words("and as assert async await break class continue def del elif else except"
               " finally for from global if import in is lambda nonlocal not or pass raise"
               " return try while with yield"),
        constants=_words("True False None"),
        types=_words("int float str bool bytes list dict set tuple object type complex"
                     " frozenset bytearray"),
        line_comments=("#",), triple=True, decorators="@"),
    "javascript": _Lang(
        _words("async await break case catch class const continue debugger default delete"
               " do else export extends finally for function if import in instanceof let new"
               " of return static super switch this throw try typeof var void while with"
               " yield from"),
        constants=_words("true false null undefined NaN Infinity"),
        multiline_quotes="`", quotes="\"'`", decorators="@", **_C_LIKE),
    "typescript": _Lang(
        _words("abstract as async await break case catch class const continue debugger"
               " declare default delete do else enum export extends finally for from"
               " function if implements import in infer instanceof interface is keyof let"
               " module namespace new of private protected public readonly return satisfies"
               " static super switch this throw try type typeof var void while with yield"),
        constants=_words("true false null undefined NaN Infinity"),
        types=_words("string number boolean any unknown never object bigint symbol"),
        multiline_quotes="`", quotes="\"'`", decorators="@", **_C_LIKE),
    "rust": _Lang(
        _words("as async await break const continue crate dyn else enum extern fn for if"
               " impl in let loop match mod move mut pub ref return self Self static struct"
               " super trait type unsafe use where while"),
        constants=_words("true false"),
        types=_words("i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool"
                     " char str"),
        quotes="\"'", decorators="#[", lifetimes=True, macros=True, **_C_LIKE),
    "go": _Lang(
        _words("break case chan const continue default defer else fallthrough for func go"
               " goto if import interface map package range return select struct switch"
               " type var"),
        constants=_words("true false nil iota"),
        types=_words("int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 uintptr"
                     " float32 float64 complex64 complex128 string bool byte rune error any"),
        quotes="\"'`", multiline_quotes="`", **_C_LIKE),
    "c": _Lang(
        _words("auto break case const continue default do else enum extern for goto if"
               " inline register restrict return sizeof static struct switch typedef union"
               " volatile while"),
        constants=_words("true false NULL"),
        types=_words("void char short int long float double signed unsigned bool size_t"
                     " ssize_t int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t"
                     " uint64_t FILE"),
        preprocessor=True, **_C_LIKE),
    "cpp": _Lang(
        _words("alignas alignof and asm auto break case catch class co_await co_return"
               " co_yield concept const consteval constexpr continue decltype default delete"
               " do dynamic_cast else enum explicit export extern for friend goto if inline"
               " mutable namespace new noexcept not operator or private protected public"
               " register reinterpret_cast requires return sizeof static static_assert"
               " static_cast struct switch template this thread_local throw try typedef"
               " typeid typename union using virtual volatile while"),
        constants=_words("true false nullptr NULL"),
        types=_words("void char short int long float double signed unsigned bool size_t"
                     " wchar_t auto string vector map"),
        preprocessor=True, **_C_LIKE),
    "java": _Lang(
        _words("abstract assert break case catch class const continue default do else enum"
               " extends final finally for goto if implements import instanceof interface"
               " native new package private protected public record return static strictfp"
               " super switch synchronized this throw throws transient try var volatile while"
               " yield"),
        constants=_words("true false null"),
        types=_words("boolean byte char double float int long short void"),
        decorators="@", **_C_LIKE),
    "bash": _Lang(
        _words("if then else elif fi case esac for select while until do done in function"
               " return break continue local export readonly declare unset set shift source"
               " alias eval exec exit trap cd echo printf read test"),
        constants=_words("true false"),
        line_comments=("#",), quotes="\"'`", capitalized_types=False, variables=True,
        operators="shell"),
    "sql": _Lang(
        _words("select from where insert update delete create alter drop table index view"
               " into values set and or not in like between is as on join inner left right"
               " outer full cross group by order having limit offset union all distinct"
               " exists case when then else end primary key foreign references default"
               " with returning begin commit rollback"),
        constants=_words("true false null"),
        types=_words("int integer bigint smallint serial real float double numeric decimal"
                     " varchar char text boolean date time timestamp json jsonb uuid blob"),
        line_comments=("--",), block_comment=("/*", "*/"), capitalized_types=False,
        ignore_case=True),
    "lua": _Lang(
        _words("and break do else elseif end for function goto if in local not or repeat"
               " return then until while"),
        constants=_words("nil true false"),
        line_comments=("--",), block_comment=("--[[", "]]"), capitalized_types=False),
    "ruby": _Lang(
        _words("alias and begin break case class def defined? do else elsif end ensure for"
               " if in module next not or redo rescue retry return self super then undef"
               " unless until when while yield require attr_accessor"),
        constants=_words("nil true false"),
        line_comments=("#",)),
    "php": _Lang(
        _words("abstract and array as break callable case catch class clone const continue"
               " declare default do echo else elseif empty extends final finally fn for"
               " foreach function global goto if implements include include_once instanceof"
               " interface isset list match namespace new or print private protected public"
               " readonly require require_once return static switch throw trait try unset"
               " use var while xor yield"),
        constants=_words("true false null TRUE FALSE NULL"),
        line_comments=("//", "#"), block_comment=("/*", "*/"), variables=True),
    "json": _Lang(frozenset(), constants=_words("true false null"), line_comments=("//",),
                  quotes='"', capitalized_types=False, keys="json", operators=""),
    "yaml": _Lang(frozenset(), constants=_words("true false null yes no on off ~"),
                  line_comments=("#",), capitalized_types=False, keys="yaml", operators=""),
    "toml": _Lang(frozenset(), constants=_words("true false inf nan"), line_comments=("#",),
                  triple=True, capitalized_types=False, keys="toml", operators=""),
}

# A named language this scanner does not know: generic C-ish rules.
_FALLBACK = _Lang(
    _words("if else for while do return import from def class function fn func const let"
           " var new public private static struct enum interface package use"),
    constants=_words("true false null none nil True False None"),
    line_comments=("//", "#"), block_comment=("/*", "*/"))

_ALIASES = {
    "py": "python", "python3": "python", "py3": "python",
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "node": "javascript", "ts": "typescript", "tsx": "typescript", "mts": "typescript",
    "rs": "rust", "golang": "go", "h": "c", "c++": "cpp", "cc": "cpp", "cxx": "cpp",
    "hpp": "cpp", "hh": "cpp", "sh": "bash", "shell": "bash", "zsh": "bash",
    "console": "bash", "shell-session": "bash", "rb": "ruby", "yml": "yaml",
    "jsonc": "json", "json5": "json", "psql": "sql", "postgres": "sql",
    "htm": "html", "xml": "html", "svg": "html", "vue": "html",
    "scss": "css", "less": "css", "patch": "diff",
}
# Prose, or no language at all: left alone (an apostrophe is not a string).
_PLAIN = frozenset({"", "text", "txt", "plain", "plaintext", "md", "markdown", "output",
                    "log"})


def _resolve_lang(lang: str) -> str:
    """Normalise a fence's language: a known name, ``"plain"`` or ``""`` (unknown)."""
    name = (lang or "").strip().lower().split()[0] if (lang or "").strip() else ""
    name = _ALIASES.get(name, name)
    if name in _PLAIN:
        return "plain"
    if name in _LANGS or name in ("diff", "html", "css"):
        return name
    return ""


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

Segments = List[Tuple[str, str]]

_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
# A lone ":" is left out (``def f():``, ``x: int``); punctuation stays plain.
_OPERATOR_RE = {
    "code": re.compile(r"::|[-+*/%=<>!&|^~?]+"),
    "shell": re.compile(r"&&|\|\||>>|[|<>;]"),
}
_NUMBER_RE = re.compile(
    r"(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+"
    r"|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?|\.\d[\d_]*(?:[eE][+-]?\d+)?)"
    r"(?:[iuf](?:8|16|32|64|128|size)|[uUlLfFnm]{1,3})?(?!\w)")
_VARIABLE_RE = re.compile(r"\$(?:\{[^}]*\}?|[A-Za-z_]\w*|[0-9@#?$!*-])")
_DECORATOR_RE = re.compile(r"@[A-Za-z_][\w.]*")
_LIFETIME_RE = re.compile(r"'[A-Za-z_]\w*(?!')")
_YAML_KEY_RE = re.compile(r"(\s*(?:-\s+)?)([^\s#:'\"][^:#]*?|\"[^\"]*\"|'[^']*')(\s*:)(?=\s|$)")
_TOML_KEY_RE = re.compile(r"(\s*)([A-Za-z0-9_\-\"'.]+(?:\s*\.\s*[A-Za-z0-9_\-\"']+)*)(\s*=)")


def _close_quote(line: str, i: int, quote: str) -> int:
    """Index just past the quote closing the one at ``i``, or -1 if the line
    ends first (a backslash escapes the next character)."""
    return _find_close(line, i + len(quote), quote)


def _find_close(line: str, j: int, quote: str) -> int:
    """Index just past the next unescaped ``quote`` from ``j``, or -1."""
    n = len(line)
    while j < n:
        if line[j] == "\\" and len(quote) == 1:
            j += 2
            continue
        if line.startswith(quote, j):
            return j + len(quote)
        j += 1
    return -1


def highlight_line(line: str, lang: str = "", state: str = "") -> Segments:
    """Highlight one code line: ``(text, type)`` pairs covering it.

    Types: keyword, function, string, comment, number (numbers and constants),
    type, decorator, variable, key, operator, added, removed, hunk, plain. ``state`` is
    what the previous line left open (``scan``).
    """
    return scan(line, lang, state)[0]


def scan(line: str, lang: str = "", state: str = "") -> Tuple[Segments, str]:
    """Highlight one line and say what it leaves open for the next one: a
    multi-line string (``"s" + closing quote``), a block comment (``"c" +
    closing marker``) or nothing (``""``)."""
    name = _resolve_lang(lang)
    if name == "plain":
        return [(line, "plain")], ""
    if name == "diff":
        return _diff(line), ""
    if name == "html":
        return _markup(line, state)
    if name == "css":
        return _css(line, state)
    spec = _LANGS.get(name, _FALLBACK)
    segments: Segments = []
    i = 0
    if state:
        i, state = _resume(line, state, segments)
        if state:
            return segments, state
    if spec.keys == "yaml":
        i = _yaml_key(line, i, segments)
    elif spec.keys == "toml":
        if line.lstrip().startswith("["):
            return [(line, "keyword")], ""
        i = _toml_key(line, i, segments)
    if spec.preprocessor:
        m = re.match(r"\s*#\s*[A-Za-z_]\w*", line[i:])
        if m:
            segments.append((m.group(), "keyword"))
            i += m.end()
    state = _scan_code(line, i, spec, segments)
    return _merge(segments), state


def _resume(line: str, state: str, segments: Segments) -> Tuple[int, str]:
    """Continue a string or comment left open by the previous line."""
    kind = "string" if state[0] == "s" else "comment"
    close = state[1:]
    if kind == "string":
        end = _find_close(line, 0, close)
    else:
        end = line.find(close)
        end = end + len(close) if end >= 0 else -1
    if end < 0:
        segments.append((line, kind))
        return len(line), state
    segments.append((line[:end], kind))
    return end, ""


def _scan_code(line: str, i: int, spec: _Lang, segments: Segments) -> str:
    n = len(line)
    keywords, constants, types = spec.keywords, spec.constants, spec.types
    while i < n:
        c = line[i]
        # Comments.
        if spec.block_comment and line.startswith(spec.block_comment[0], i):
            close = spec.block_comment[1]
            end = line.find(close, i + len(spec.block_comment[0]))
            if end < 0:
                segments.append((line[i:], "comment"))
                return "c" + close
            end += len(close)
            segments.append((line[i:end], "comment"))
            i = end
            continue
        comment = next((p for p in spec.line_comments if line.startswith(p, i)), None)
        if comment and (comment != "#" or i == 0 or line[i - 1] in " \t"):
            segments.append((line[i:], "comment"))
            return ""
        # Decorators and attributes.
        if spec.decorators == "@" and c == "@":
            m = _DECORATOR_RE.match(line, i)
            if m:
                segments.append((m.group(), "decorator"))
                i = m.end()
                continue
        if spec.decorators == "#[" and (line.startswith("#[", i) or line.startswith("#![", i)):
            end = line.find("]", i)
            end = n if end < 0 else end + 1
            segments.append((line[i:end], "decorator"))
            i = end
            continue
        # Variables.
        if spec.variables and c == "$":
            m = _VARIABLE_RE.match(line, i)
            if m:
                segments.append((m.group(), "variable"))
                i = m.end()
                continue
        # Strings.
        if spec.triple and line.startswith(('"""', "'''"), i):
            quote = line[i:i + 3]
            end = _close_quote(line, i, quote)
            if end < 0:
                segments.append((line[i:], "string"))
                return "s" + quote
            segments.append((line[i:end], "string"))
            i = end
            continue
        if c in spec.quotes:
            if spec.lifetimes and c == "'":
                m = _LIFETIME_RE.match(line, i)
                if m:
                    segments.append((m.group(), "type"))
                    i = m.end()
                    continue
            end = _close_quote(line, i, c)
            if end < 0:
                if c in spec.multiline_quotes:
                    segments.append((line[i:], "string"))
                    return "s" + c
                segments.append((c, "plain"))  # a lone quote (it's, don't)
                i += 1
                continue
            kind = "string"
            if spec.keys == "json" and line[end:].lstrip().startswith(":"):
                kind = "key"
            segments.append((line[i:end], kind))
            i = end
            continue
        # Numbers.
        if (c.isdigit() or (c == "." and i + 1 < n and line[i + 1].isdigit())) and (
                i == 0 or not (line[i - 1].isalnum() or line[i - 1] == "_")):
            m = _NUMBER_RE.match(line, i)
            if m:
                segments.append((m.group(), "number"))
                i = m.end()
                continue
        # Words.
        m = _IDENT_RE.match(line, i)
        if m:
            word = m.group()
            end = m.end()
            key = word.lower() if spec.ignore_case else word
            if key in keywords:
                kind = "keyword"
            elif key in constants:
                kind = "number"
            elif key in types:
                kind = "type"
            elif spec.macros and line.startswith("!", end) and line[end + 1:end + 2] in ("(", "[", "{"):
                word, end, kind = word + "!", end + 1, "function"
            elif spec.capitalized_types and word[0].isupper():
                kind = "number" if word.isupper() and len(word) > 1 else "type"
            elif line[end:].lstrip().startswith("("):
                kind = "function"
            else:
                kind = "plain"
            segments.append((word, kind))
            i = end
            continue
        operator = _OPERATOR_RE.get(spec.operators)
        m = operator.match(line, i) if operator else None
        if m:
            segments.append((m.group(), "operator"))
            i = m.end()
            continue
        segments.append((c, "plain"))
        i += 1
    return ""


def _yaml_key(line: str, i: int, segments: Segments) -> int:
    if i:
        return i
    m = _YAML_KEY_RE.match(line)
    if not m:
        return 0
    if m.group(1):
        segments.append((m.group(1), "plain"))
    segments.append((m.group(2), "key"))
    segments.append((m.group(3), "plain"))
    return m.end()


def _toml_key(line: str, i: int, segments: Segments) -> int:
    if i:
        return i
    m = _TOML_KEY_RE.match(line)
    if not m:
        return 0
    if m.group(1):
        segments.append((m.group(1), "plain"))
    segments.append((m.group(2), "key"))
    segments.append((m.group(3), "plain"))
    return m.end()


def _diff(line: str) -> Segments:
    if line.startswith(("+++", "---", "diff ", "index ")):
        return [(line, "comment")]
    if line.startswith("@@"):
        return [(line, "hunk")]
    if line.startswith("+"):
        return [(line, "added")]
    if line.startswith("-"):
        return [(line, "removed")]
    return [(line, "plain")]


_TAG_RE = re.compile(r"</?[A-Za-z][\w:.-]*|/?>|<!DOCTYPE[^>]*>|<\?[\w-]*|\?>")
_ATTR_RE = re.compile(r"[A-Za-z_:][\w:.-]*(?=\s*=)")


def _markup(line: str, state: str) -> Tuple[Segments, str]:
    """HTML / XML: tags, attribute names, quoted values, ``<!-- -->``."""
    segments: Segments = []
    i = 0
    if state:
        i, state = _resume(line, state, segments)
        if state:
            return segments, state
    n = len(line)
    in_tag = False
    while i < n:
        if line.startswith("<!--", i):
            end = line.find("-->", i + 4)
            if end < 0:
                segments.append((line[i:], "comment"))
                return _merge(segments), "c-->"
            segments.append((line[i:end + 3], "comment"))
            i = end + 3
            continue
        m = _TAG_RE.match(line, i)
        if m:
            segments.append((m.group(), "keyword"))
            in_tag = not m.group().endswith(">")
            i = m.end()
            continue
        if in_tag:
            m = _ATTR_RE.match(line, i)
            if m:
                segments.append((m.group(), "type"))
                i = m.end()
                continue
            if line[i] in "\"'":
                end = _close_quote(line, i, line[i])
                end = n if end < 0 else end
                segments.append((line[i:end], "string"))
                i = end
                continue
        segments.append((line[i], "plain"))
        i += 1
    return _merge(segments), ""


_CSS_RE = re.compile(
    r"(?P<comment>/\*.*?(?:\*/|$))"
    r"|(?P<string>\"[^\"]*\"?|'[^']*'?)"
    r"|(?P<at>@[\w-]+)"
    r"|(?P<hex>#[0-9a-fA-F]{3,8}\b)"
    r"|(?P<number>-?\d*\.?\d+(?:[a-zA-Z%]+)?)"
    r"|(?P<key>[\w-]+(?=\s*:[^:]))"
    r"|(?P<selector>[.#][\w-]+)"
    r"|(?P<function>[\w-]+(?=\())"
)


def _css(line: str, state: str) -> Tuple[Segments, str]:
    """CSS: properties, selectors, at-rules, numbers with units, colors."""
    segments: Segments = []
    i = 0
    if state:
        i, state = _resume(line, state, segments)
        if state:
            return segments, state
    kinds = {"comment": "comment", "string": "string", "at": "keyword", "hex": "number",
             "number": "number", "key": "key", "selector": "type", "function": "function"}
    for m in _CSS_RE.finditer(line, i):
        if m.start() > i:
            segments.append((line[i:m.start()], "plain"))
        segments.append((m.group(), kinds[m.lastgroup]))
        i = m.end()
        if m.lastgroup == "comment" and not m.group().endswith("*/"):
            return _merge(segments), "c*/"
    if i < len(line):
        segments.append((line[i:], "plain"))
    return _merge(segments), ""


def _merge(segments: Segments) -> Segments:
    """Join neighbours of the same type (fewer, longer segments)."""
    merged: Segments = []
    for text, kind in segments:
        if not text:
            continue
        if merged and merged[-1][1] == kind:
            merged[-1] = (merged[-1][0] + text, kind)
        else:
            merged.append((text, kind))
    return merged
