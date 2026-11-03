
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from moka_code import settings

class RGB:
    def __init__(self, r, g=None, b=None):
        self.r = r
        self.g = g
        self.b = b
        
        if type(r) == str:
            # Parse from hex string; an alpha byte (#rrggbbaa, as editors'
            # color pickers write) is ignored.
            hex_str = r.lstrip('#')
            if len(hex_str) in (6, 8):
                self.r = int(hex_str[0:2], 16)
                self.g = int(hex_str[2:4], 16)
                self.b = int(hex_str[4:6], 16)
            else:
                raise ValueError(f"Invalid hex color string: {r}")
    
    def __str__(self):
        return self.ansi_fg()
    
    # concat support for ergonomics
    def __add__(self, other):
        if isinstance(other, str):
            return self.ansi_fg() + other
        else:
            return self.ansi_fg() + str(other)
    
    def ansi_fg(self): 
        """ coinvert to ANSI foreground color code """
        return f"\033[38;2;{self.r};{self.g};{self.b}m"

    def ansi_bg(self): 
        """ coinvert to ANSI background color code """
        return f"\033[48;2;{self.r};{self.g};{self.b}m"        


class ANSIColor:
    """Terminal-native color using standard 8/16 ANSI color codes.
    
    Uses the terminal's own palette instead of hardcoded RGB values,
    so the theme respects the user's terminal color scheme.
    
    fg_code: ANSI foreground code (30-37 standard, 90-97 bright, 39 = default)
    bg_code: ANSI background code (40-47 standard, 100-107 bright, 49 = default)
    """
    def __init__(self, fg: int = 39, bg: int = 49):
        self.fg = fg
        self.bg = bg

    def __str__(self):
        return self.ansi_fg()

    def __add__(self, other):
        if isinstance(other, str):
            return self.ansi_fg() + other
        else:
            return self.ansi_fg() + str(other)

    def ansi_fg(self) -> str:
        return f"\033[{self.fg}m"

    def ansi_bg(self) -> str:
        return f"\033[{self.bg}m"


#: Text colors that default to the theme's own DEFAULT.
_TEXT_FIELDS = ("USER_TEXT", "ASSISTANT_TEXT")


@dataclass
class _theme:    
    name: str
    BACKGROUND: RGB
    DEFAULT: RGB
    
    MUTED: RGB
    ERROR: RGB
    WARNING: RGB
    SUCCESS: RGB
    PERMISSION: RGB  # Purple/magenta for permission prompts
    TOOL: RGB        # Tool-call / action accent (asked as blue; any hue is fine)
    
    USER: RGB
    ASSISTANT: RGB
    FOCUSED: RGB

    # Answer markdown (``markdown_styles`` refer to these by name).
    HEADING: RGB
    EMPHASIS: RGB    # **bold**
    CODE: RGB        # `inline code`

    # Code blocks (``syntax_highlight`` refers to these; comments are MUTED).
    KEYWORD: RGB
    FUNCTION: RGB
    STRING: RGB
    NUMBER: RGB      # numbers and constants
    TYPE: RGB        # types, keys, variables

    # Message text; a theme that leaves them out uses its DEFAULT.
    USER_TEXT: Optional[RGB] = None
    ASSISTANT_TEXT: Optional[RGB] = None
    # Backgrounds of the message types whose ``ui.<type>_highlight`` is on:
    # the user's messages, and the rest (answers, thoughts, tools, code).
    # None (``""`` in a theme file) highlights nothing.
    USER_HIGHLIGHT: Optional[RGB] = None
    ASSISTANT_HIGHLIGHT: Optional[RGB] = None

    def __post_init__(self):
        for field in _TEXT_FIELDS:
            if getattr(self, field) is None:
                setattr(self, field, self.DEFAULT)

    def copy(self) -> "_theme":
        return _theme(**self.__dict__)
    
    def reset(self) -> str:
        """reset to theme bg + fg colors"""
        
        if settings.config.ui_use_bg_color:
            return self.BACKGROUND.ansi_bg() + self.DEFAULT.ansi_fg()
        else:
            return "\033[0m" + self.DEFAULT.ansi_fg()
        
        # return self.BACKGROUND.ansi_bg() + self.DEFAULT.ansi_fg()
    
    def get_bg(self):
        """Get background color respecting ui_use_bg_color config setting."""
        from moka_code import settings
        if settings.config.ui_use_bg_color:
            return self.BACKGROUND
        return None
    
# Terminal-native theme — reuses the user's own terminal color palette.
# No hardcoded RGB: colors are the standard 8/16 ANSI slots so they
# automatically match whatever the user has configured in their terminal.
terminal = _theme(
    name="terminal",
    BACKGROUND  = ANSIColor(fg=39, bg=49),  # terminal default fg/bg
    DEFAULT     = ANSIColor(fg=39),         # default fg
    MUTED       = ANSIColor(fg=90),         # bright black (dark gray)
    ERROR       = ANSIColor(fg=91),         # bright red
    WARNING     = ANSIColor(fg=33),         # yellow  (maps to user's yellow)
    SUCCESS     = ANSIColor(fg=32),         # green   (maps to user's green)
    PERMISSION  = ANSIColor(fg=95),         # bright magenta (maps to user's magenta)
    TOOL        = ANSIColor(fg=34),         # blue (maps to user's blue)
    USER        = ANSIColor(fg=32),         # green
    ASSISTANT   = ANSIColor(fg=36),         # cyan    (maps to user's cyan)
    FOCUSED     = ANSIColor(fg=33),         # bright yellow
    HEADING     = RGB("#FF79C6"),           # pink
    EMPHASIS    = RGB("#FFD700"),           # gold
    CODE        = RGB("#9CDCFE"),           # light blue
    KEYWORD     = ANSIColor(fg=31),         # red     (maps to user's red)
    FUNCTION    = ANSIColor(fg=32),         # green
    STRING      = ANSIColor(fg=33),         # yellow
    NUMBER      = ANSIColor(fg=35),         # magenta
    TYPE        = ANSIColor(fg=36),         # cyan
)

# --- Built-in RGB palettes ------------------------------------------------
# For dark terminals. Every palette keeps the semantic colors coherent: ERROR
# red, WARNING amber, SUCCESS green, MUTED dimmer than DEFAULT but legible
# (contrast is checked in test/test_themes.py against the theme background and
# black). The accents (USER, TOOL, headings...) are each theme's own.


def _palette(name: str, **colors: str) -> _theme:
    return _theme(name=name, **{key: RGB(value) for key, value in colors.items()})


moka = _palette(
    "moka",  # espresso, cream text, light-brown user, sage answers, gold focus and warning
    BACKGROUND="#1C130F", DEFAULT="#F4E4D2", MUTED="#9E8371",
    ERROR="#F2604A", WARNING="#F3C158", SUCCESS="#A8C964",
    PERMISSION="#C49AE6", TOOL="#8FB8DE", USER="#C9A07A", ASSISTANT="#86C3B8",
    FOCUSED="#F3C158", HEADING="#F0875A", EMPHASIS="#F8D59A", CODE="#C3D17C",
    KEYWORD="#F27C62", FUNCTION="#F3C56E", STRING="#B9D07A", NUMBER="#DBA0D5", TYPE="#86CDB4",
    USER_TEXT="#F3DABD", ASSISTANT_TEXT="#DDC3A6",
    USER_HIGHLIGHT="#2C1D18", ASSISTANT_HIGHLIGHT="#170F0C",
)

nord = _palette(
    "nord",  # snow text, frost user, aurora orange answers, teal focus
    BACKGROUND="#2E3440", DEFAULT="#ECEFF4", MUTED="#818EA6",
    ERROR="#E58891", WARNING="#EBCB8B", SUCCESS="#A3BE8C",
    PERMISSION="#C29BC0", TOOL="#81A1C1", USER="#88C0D0", ASSISTANT="#D08770",
    FOCUSED="#8FBCBB", HEADING="#88C0D0", EMPHASIS="#F2DAA6", CODE="#8FBCBB",
    KEYWORD="#81A1C1", FUNCTION="#88C0D0", STRING="#A3BE8C", NUMBER="#C29BC0", TYPE="#8FBCBB",
    USER_TEXT="#BCE3EF", ASSISTANT_TEXT="#D8DEE9",
    USER_HIGHLIGHT="#3B4252", ASSISTANT_HIGHLIGHT="#272C36",
)

dracula = _palette(
    "dracula",  # stock, softer greens and an orange warning: purple user, cyan answers
    BACKGROUND="#282A36", DEFAULT="#F8F8F2", MUTED="#7F89B5",
    ERROR="#FF6E6E", WARNING="#F2B37A", SUCCESS="#7FD69A",
    PERMISSION="#FF92DF", TOOL="#9AA5CE", USER="#BD93F9", ASSISTANT="#8BE9FD",
    FOCUSED="#FF79C6", HEADING="#FF79C6", EMPHASIS="#FFB86C", CODE="#7FD69A",
    KEYWORD="#FF79C6", FUNCTION="#7FD69A", STRING="#F1FA8C", NUMBER="#BD93F9", TYPE="#8BE9FD",
    USER_TEXT="#E6DBFF", ASSISTANT_TEXT="#E2E2DC",
    USER_HIGHLIGHT="#343746", ASSISTANT_HIGHLIGHT="#21222C",
)

gruvbox = _palette(
    "gruvbox",  # fg1 text, purple user and focus, orange answers, warm highlights
    BACKGROUND="#282828", DEFAULT="#EBDBB2", MUTED="#A89984",
    ERROR="#FC5D48", WARNING="#FABD2F", SUCCESS="#A6C44A",
    PERMISSION="#83A598", TOOL="#8EC07C", USER="#D3869B", ASSISTANT="#FE8019",
    FOCUSED="#D3869B", HEADING="#FE8019", EMPHASIS="#FABD2F", CODE="#8EC07C",
    KEYWORD="#FC5D48", FUNCTION="#B8BB26", STRING="#B8BB26", NUMBER="#D3869B", TYPE="#FABD2F",
    USER_TEXT="#F0D6D8", ASSISTANT_TEXT="#D5C4A1",
    USER_HIGHLIGHT="#37302A", ASSISTANT_HIGHLIGHT="#1E1C1A",
)

github_dark = _palette(
    "github-dark",  # GitHub's dark default (softer green and amber): blue user and focus
    BACKGROUND="#0D1117", DEFAULT="#E6EDF3", MUTED="#8B949E",
    ERROR="#FF7B72", WARNING="#E8B062", SUCCESS="#7CC99A",
    PERMISSION="#D2A8FF", TOOL="#79C0FF", USER="#58A6FF", ASSISTANT="#FFA657",
    FOCUSED="#58A6FF", HEADING="#79C0FF", EMPHASIS="#FFA657", CODE="#A5D6FF",
    KEYWORD="#FF7B72", FUNCTION="#D2A8FF", STRING="#A5D6FF", NUMBER="#79C0FF", TYPE="#FFA657",
    USER_TEXT="#D4DDE7", ASSISTANT_TEXT="#C9D1D9",
    USER_HIGHLIGHT="#161B22", ASSISTANT_HIGHLIGHT="#010409",
)

rose_pine = _palette(
    "rose-pine",  # main: iris user, foam answers, rose focus (green added for SUCCESS)
    BACKGROUND="#191724", DEFAULT="#E0DEF4", MUTED="#6E6A86",
    ERROR="#EB6F92", WARNING="#F6C177", SUCCESS="#95C9A0",
    PERMISSION="#EBBCBA", TOOL="#8FB8D8", USER="#C4A7E7", ASSISTANT="#9CCFD8",
    FOCUSED="#EBBCBA", HEADING="#C4A7E7", EMPHASIS="#F6C177", CODE="#9CCFD8",
    KEYWORD="#6CA6C1", FUNCTION="#EBBCBA", STRING="#F6C177", NUMBER="#C4A7E7", TYPE="#9CCFD8",
    USER_TEXT="#E7D6FB", ASSISTANT_TEXT="#C4C8E0",
    USER_HIGHLIGHT="#221F33", ASSISTANT_HIGHLIGHT="#12101B",
)

everforest = _palette(
    "everforest",  # dark medium: blue user and focus, orange answers
    BACKGROUND="#2D353B", DEFAULT="#DDD0B4", MUTED="#859289",
    ERROR="#E67E80", WARNING="#DBBC7F", SUCCESS="#A7C080",
    PERMISSION="#D699B6", TOOL="#83C092", USER="#7FBBB3", ASSISTANT="#E69875",
    FOCUSED="#7FBBB3", HEADING="#E69875", EMPHASIS="#DBBC7F", CODE="#83C092",
    KEYWORD="#E67E80", FUNCTION="#A7C080", STRING="#83C092", NUMBER="#D699B6", TYPE="#DBBC7F",
    USER_TEXT="#CFE0D9", ASSISTANT_TEXT="#CDC2A6",
    USER_HIGHLIGHT="#343F44", ASSISTANT_HIGHLIGHT="#232A2E",
)


#: Built-in themes, always available (and overridable by ``themes/<name>.toml``).
BUILTIN_THEMES = {
    "terminal":     terminal,
    "moka":         moka,
    "nord":         nord,
    "dracula":      dracula,
    "gruvbox":      gruvbox,
    "github-dark":  github_dark,
    "rose-pine":    rose_pine,
    "everforest":   everforest,
}

theme: _theme = terminal.copy()


def _color_from_spec(spec):
    """Build a color from a theme file palette value.

    Accepted: ``"#RRGGBB"``, an integer ANSI fg code, or a table
    ``{ ansi = <fg>, bg = <bg> }``.
    """
    if isinstance(spec, str):
        if spec.startswith("#"):
            return RGB(spec)
        return ANSIColor(fg=int(spec))
    if isinstance(spec, bool):
        raise ValueError(f"Invalid color: {spec!r}")
    if isinstance(spec, int):
        return ANSIColor(fg=spec)
    if isinstance(spec, dict):
        fg = spec.get("ansi", 39)
        bg = spec.get("bg", 49)
        return ANSIColor(fg=fg, bg=bg)
    raise ValueError(f"Invalid color: {spec!r}")


def resolve_color(value: str, fallback):
    """Resolve a theme palette name or ``#rrggbb`` string to a color.

    Falls back to ``fallback`` for an unknown name or invalid hex so a typo in
    the config never blanks what it colors.
    """
    if not value:
        return fallback
    named = getattr(theme, value, None)
    if isinstance(named, (RGB, ANSIColor)):
        return named
    if value.startswith("#"):
        try:
            return RGB(value)
        except ValueError:
            return fallback
    return fallback


def _theme_from_palette(name: str, palette: dict) -> "_theme":
    base = BUILTIN_THEMES.get(name, terminal).copy()
    base.name = name
    for key, spec in palette.items():
        setattr(base, key.upper(), None if spec == "" else _color_from_spec(spec))
    given = {key.upper() for key in palette}
    for field in _TEXT_FIELDS:
        if field not in given:
            setattr(base, field, base.DEFAULT)
    return base


def available_themes() -> dict:
    """Built-in themes plus the user's ``themes/<name>.toml`` files."""
    themes = dict(BUILTIN_THEMES)
    for name, palette in getattr(settings.config, "themes", {}).items():
        try:
            themes[name] = _theme_from_palette(name, palette)
        except Exception:
            continue
    return themes


def theme_problems() -> list:
    """Chosen themes that do not exist (they fall back to ``terminal``)."""
    known = available_themes()
    problems = []
    name = settings.config.active_theme
    if name and name not in known:
        problems.append(f"state.toml: active_theme '{name}' is not a theme (using terminal)")
    return problems


def theme_names() -> list:
    """Selectable theme names (built-ins + user-defined)."""
    return sorted(available_themes())


#: Palette fields in display/file order.
PALETTE_FIELDS = (
    "BACKGROUND", "DEFAULT", "MUTED", "ERROR", "WARNING",
    "SUCCESS", "PERMISSION", "TOOL", "USER", "ASSISTANT", "FOCUSED",
    "HEADING", "EMPHASIS", "CODE", "KEYWORD", "FUNCTION", "STRING",
    "NUMBER", "TYPE", "USER_TEXT", "ASSISTANT_TEXT", "USER_HIGHLIGHT", "ASSISTANT_HIGHLIGHT",
)


def _color_to_toml(color) -> str:
    """Render one palette color as a theme file value (``""``: none)."""
    if color is None:
        return '""'
    if hasattr(color, "r"):
        return f'"#{color.r:02X}{color.g:02X}{color.b:02X}"'
    return f"{{ ansi = {color.fg}, bg = {color.bg} }}"


def write_theme_file(name: str, source: str) -> "Path":
    """Write ``themes/<name>.toml`` with the full palette of theme ``source``
    (``/theme edit`` copies a built-in, ``/theme create`` the active theme)."""
    name = name.strip()
    if not name or name.startswith(".") or any(c in name for c in "[]\\/"):
        raise ValueError("Theme name must be non-empty and cannot contain '[', '\\', '/' or start with '.'")
    selected = available_themes().get(source)
    if selected is None:
        raise KeyError(f"Unknown theme '{source}'")
    path = settings.config.theme_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{field.ljust(10)} = {_color_to_toml(getattr(selected, field))}"
             for field in PALETTE_FIELDS]
    path.write_text(settings.THEME_FILE_HELP + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def set_theme(name: str):
    """Switch the active theme by name. Unknown names fall back to terminal."""
    selected = available_themes().get(name) or terminal
    theme.__dict__.update(selected.__dict__)
