"""Minimal syntax highlighter for code blocks in the TUI.

Single-pass regex scanner.  Highlights: keywords, functions, comments, strings.
Designed to be fast enough for live re-parsing during streaming.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from moka_chat.ui.tui.colors import RGB


def _resolve_hex_color(value: str) -> Optional[Tuple[int, int, int]]:
    """Convert a hex color string like '#FF6464' to an RGB tuple."""
    c = RGB(value)
    return (c.r, c.g, c.b)


def _get_highlight_color(hl_type: str) -> Optional[Tuple[int, int, int]]:
    """Return an RGB color for a highlight type from config, or None for plain text."""
    from moka_chat import settings
    style = settings.config.syntax_highlight_styles.get(hl_type, {})
    fg_hex = style.get("fg")
    if fg_hex:
        return _resolve_hex_color(fg_hex)
    return None


# ---------------------------------------------------------------------------
# Keyword lists (compact)
# ---------------------------------------------------------------------------

_KEYWORDS: dict[str, frozenset[str]] = {
    "python": frozenset({
        "and", "as", "assert", "async", "await", "break", "class", "continue",
        "def", "del", "elif", "else", "except", "finally", "for", "from",
        "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
        "or", "pass", "raise", "return", "try", "while", "with", "yield",
        "True", "False", "None",
    }),
    "javascript": frozenset({
        "async", "await", "break", "case", "catch", "class", "const",
        "continue", "debugger", "default", "delete", "do", "else", "export",
        "extends", "finally", "for", "function", "if", "import", "in",
        "instanceof", "let", "new", "of", "return", "static", "super",
        "switch", "this", "throw", "try", "typeof", "var", "void", "while",
        "with", "yield", "true", "false", "null", "undefined",
    }),
    "typescript": frozenset({
        "abstract", "as", "async", "await", "break", "case", "catch", "class",
        "const", "continue", "debugger", "declare", "default", "delete", "do",
        "else", "enum", "export", "extends", "finally", "for", "from",
        "function", "if", "implements", "import", "in", "infer", "instanceof",
        "interface", "is", "keyof", "let", "module", "namespace", "new",
        "of", "readonly", "return", "static", "super", "switch", "this",
        "throw", "try", "type", "typeof", "var", "void", "while", "with",
        "yield", "true", "false", "null", "undefined",
    }),
    "lua": frozenset({
        "and", "break", "do", "else", "elseif", "end", "false", "for",
        "function", "goto", "if", "in", "local", "nil", "not", "or",
        "repeat", "return", "then", "true", "until", "while",
    }),
    "rust": frozenset({
        "as", "async", "await", "break", "const", "continue", "crate",
        "dyn", "else", "enum", "extern", "false", "fn", "for", "if",
        "impl", "in", "let", "loop", "match", "mod", "move", "mut",
        "pub", "ref", "return", "self", "Self", "static", "struct",
        "super", "trait", "true", "type", "unsafe", "use", "where", "while",
    }),
    "c": frozenset({
        "auto", "break", "case", "const", "continue", "default", "do",
        "else", "enum", "extern", "for", "goto", "if", "register",
        "return", "sizeof", "static", "struct", "switch", "typedef",
        "union", "volatile", "while", "true", "false", "NULL",
    }),
    "cpp": frozenset({
        "alignas", "alignof", "and", "asm", "auto", "bitand", "bitor",
        "break", "case", "catch", "class", "compl", "const", "constexpr",
        "consteval", "continue", "co_await", "co_return", "co_yield",
        "decltype", "default", "delete", "do", "else", "enum", "explicit",
        "export", "extern", "false", "for", "friend", "goto", "if",
        "inline", "mutable", "namespace", "new", "noexcept", "not", "not_eq",
        "nullptr", "operator", "or", "or_eq", "private", "protected", "public",
        "register", "reinterpret_cast", "requires", "return", "sizeof",
        "static", "static_assert", "static_cast", "struct", "switch",
        "template", "this", "thread_local", "throw", "true", "try",
        "typedef", "typeid", "typename", "union", "using", "virtual",
        "void", "volatile", "while", "xor", "xor_eq",
    }),
    "bash": frozenset({
        "alias", "bg", "bind", "break", "builtin", "case", "cd", "command",
        "compgen", "complete", "continue", "declare", "dirs", "disown",
        "echo", "eval", "exec", "exit", "export", "fc", "fg", "getopts",
        "hash", "help", "history", "if", "jobs", "kill", "let", "local",
        "logout", "popd", "printf", "pushd", "read", "readonly", "return",
        "set", "shift", "shopt", "source", "suspend", "test", "times",
        "trap", "type", "typeset", "ulimit", "umask", "unalias", "unset",
        "until", "wait", "while", "do", "done", "then", "else", "elif", "fi", "true", "false", "function",
    }),
    "sql": frozenset({
        "select", "from", "where", "insert", "update", "delete", "create",
        "alter", "drop", "table", "index", "view", "into", "values",
        "set", "and", "or", "not", "in", "like", "between", "is", "null",
        "as", "on", "join", "inner", "left", "right", "outer", "full",
        "cross", "group", "by", "order", "having", "limit", "offset",
        "union", "all", "distinct", "exists", "case", "when", "then",
        "else", "end", "true", "false", "null",
    }),
    "go": frozenset({
        "break", "case", "chan", "const", "continue", "default", "defer",
        "else", "fallthrough", "for", "func", "go", "goto", "if", "import",
        "interface", "map", "package", "range", "return", "select", "struct",
        "switch", "type", "var", "true", "false", "nil", "iota",
    }),
    "java": frozenset({
        "abstract", "assert", "boolean", "break", "byte", "case", "catch",
        "char", "class", "const", "continue", "default", "do", "double",
        "else", "enum", "extends", "final", "finally", "float", "for",
        "goto", "if", "implements", "import", "instanceof", "int",
        "interface", "long", "native", "new", "package", "private",
        "protected", "public", "return", "short", "static", "strictfp",
        "super", "switch", "synchronized", "this", "throw", "throws",
        "transient", "try", "void", "volatile", "while", "true", "false",
        "null",
    }),
    "ruby": frozenset({
        "alias", "and", "begin", "break", "case", "class", "def", "defined?",
        "do", "else", "elsif", "end", "ensure", "false", "for", "if",
        "in", "module", "next", "nil", "not", "or", "redo", "rescue",
        "retry", "return", "self", "super", "then", "true", "undef",
        "unless", "until", "when", "while", "yield",
    }),
    "php": frozenset({
        "abstract", "and", "array", "as", "break", "callable", "case",
        "catch", "class", "clone", "const", "continue", "declare", "default",
        "do", "else", "elseif", "empty", "enddeclare", "endfor", "endforeach",
        "endif", "endswitch", "endwhile", "extends", "false", "finally",
        "for", "foreach", "function", "global", "goto", "if", "implements",
        "include", "include_once", "instanceof", "insteadof", "interface",
        "isset", "list", "namespace", "new", "null", "or", "print",
        "private", "protected", "public", "require", "require_once",
        "return", "static", "switch", "throw", "trait", "true", "try",
        "unset", "use", "var", "while", "xor", "yield",
    }),
    "toml": frozenset({"true", "false", "inf", "nan"}),
    "yaml": frozenset({"true", "false", "null", "yes", "no", "on", "off"}),
    "json": frozenset({"true", "false", "null"}),
}

_FALLBACK_KW = frozenset({
    "true", "false", "null", "none", "nil", "if", "else", "for",
    "while", "return", "import", "from", "def", "class", "function",
    "fn", "const", "let", "var",
})

# Comment prefixes per language (single-line only for speed)
_COMMENT_PREFIXES: dict[str, frozenset[str]] = {
    "python": frozenset({"#"}),
    "javascript": frozenset({"//", "#"}),
    "typescript": frozenset({"//"}),
    "lua": frozenset({"--"}),
    "rust": frozenset({"//"}),
    "c": frozenset({"//"}),
    "cpp": frozenset({"//"}),
    "bash": frozenset({"#"}),
    "sql": frozenset({"--"}),
    "go": frozenset({"//"}),
    "java": frozenset({"//"}),
    "ruby": frozenset({"#"}),
    "php": frozenset({"//", "#"}),
    "toml": frozenset({"#"}),
    "yaml": frozenset({"#"}),
}
_FALLBACK_PREFIXES = frozenset({"#", "//"})

# Pre-compiled regex: word boundary + identifier, optionally followed by (
_IDENT_RE = re.compile(r"[a-zA-Z_]\w*")





# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _resolve_lang(lang: str) -> str:
    """Normalise language name."""
    l = lang.lower().strip()
    if l in _KEYWORDS:
        return l
    for key in _KEYWORDS:
        if l.startswith(key) or key.startswith(l):
            return key
    return ""


def highlight_line(line: str, lang: str = "") -> List[Tuple[str, str]]:
    """Highlight a single code line.

    Returns list of (text, type) tuples covering the entire line.
    Types: "keyword", "function", "comment", "string", "plain".

    Single-pass scanner — no per-character loop.
    """
    resolved = _resolve_lang(lang)
    keywords = _KEYWORDS.get(resolved, _FALLBACK_KW)
    prefixes = _COMMENT_PREFIXES.get(resolved, _FALLBACK_PREFIXES)

    # Build a single combined regex for comment prefixes (longest first)
    sorted_prefixes = sorted(prefixes, key=len, reverse=True)
    escaped = [re.escape(p) for p in sorted_prefixes]
    comment_re = re.compile("|".join(escaped)) if escaped else None

    segments: List[Tuple[str, str]] = []
    i = 0
    n = len(line)

    while i < n:
        # --- comment ---
        if comment_re:
            m = comment_re.match(line, i)
            if m:
                prefix = m.group()
                # For // and -- style, the rest of line is comment
                if len(prefix) >= 2 and prefix[0] == prefix[1]:
                    segments.append((line[i:], "comment"))
                    return segments
                # For # style, check it's at start of line or after whitespace
                if prefix == "#" and (i == 0 or line[i - 1] in " \t"):
                    segments.append((line[i:], "comment"))
                    return segments

        # --- string (double or single quote) ---
        if line[i] in ('"', "'"):
            quote = line[i]
            j = i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == quote:
                    j += 1
                    break
                j += 1
            segments.append((line[i:j], "string"))
            i = j
            continue

        # --- identifier (keyword / function) ---
        m = _IDENT_RE.match(line, i)
        if m:
            word = m.group()
            end = m.end()
            # Keyword check takes priority over function-call detection,
            # so that e.g. "for" in "for (" is still a keyword.
            if word in keywords:
                segments.append((word, "keyword"))
            else:
                # Check for function call: identifier immediately followed by (
                rest = line[end:].lstrip()
                if rest and rest[0] == "(":
                    segments.append((word, "function"))
                else:
                    segments.append((word, "plain"))
            i = end
            continue

        # --- plain character (spaces, operators, etc.) ---
        # Collect runs of plain chars for fewer segments
        j = i + 1
        while j < n:
            # Stop if next char starts an identifier, string, or comment
            if line[j] in ('"', "'") or (line[j].isalpha() or line[j] == "_"):
                break
            if comment_re:
                if comment_re.match(line, j):
                    break
            j += 1
        segments.append((line[i:j], "plain"))
        i = j

    return segments
