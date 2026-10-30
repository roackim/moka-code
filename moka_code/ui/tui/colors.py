
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
            # Parse from hex string
            hex_str = r.lstrip('#')
            if len(hex_str) == 6:
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

    # Message text; a theme that leaves them out uses its DEFAULT.
    USER_TEXT: Optional[RGB] = None
    ASSISTANT_TEXT: Optional[RGB] = None

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
)

# --- Built-in RGB palettes ------------------------------------------------
# For dark terminals. Every palette keeps the semantic colors coherent: ERROR
# red, WARNING amber, SUCCESS green, MUTED dimmer than DEFAULT but legible
# (contrast is checked in test/test_themes.py against the theme background and
# black). The accents (USER, TOOL, headings...) are each theme's own.


def _palette(name: str, **colors: str) -> _theme:
    return _theme(name=name, **{key: RGB(value) for key, value in colors.items()})


moka = _palette(
    "moka",  # espresso, cream text, latte answers, sunflower user, cream focus
    BACKGROUND="#1A1412", DEFAULT="#ECDCCF", MUTED="#8E7A6E",
    ERROR="#E5705F", WARNING="#E2B266", SUCCESS="#A3BC7E",
    PERMISSION="#B98BC0", TOOL="#B39AB8", USER="#F0C24A", ASSISTANT="#8E7A6E",
    FOCUSED="#FFF0D0", HEADING="#E39A6E", EMPHASIS="#F0D3A0", CODE="#B4C08F",
    USER_TEXT="#f7ca88", ASSISTANT_TEXT="#CEB9A5",
)

nord = _palette(
    "nord",  # snow text, frost user, aurora orange answers
    BACKGROUND="#2E3440", DEFAULT="#E5E9F0", MUTED="#7B88A1",
    ERROR="#E0848C", WARNING="#EBCB8B", SUCCESS="#A3BE8C",
    PERMISSION="#B48EAD", TOOL="#81A1C1", USER="#88C0D0", ASSISTANT="#D08770",
    FOCUSED="#ECEFF4", HEADING="#88C0D0", EMPHASIS="#F7E09B", CODE="#8FBCBB",
    USER_TEXT="#BCE0EC", ASSISTANT_TEXT="#CBD1DC",
)

dracula = _palette(
    "dracula",  # softened: purple user, cyan answers, orange focus, amber warnings
    BACKGROUND="#282A36", DEFAULT="#EEEEE8", MUTED="#7C86B0",
    ERROR="#EE7B7B", WARNING="#E6C07B", SUCCESS="#7DD39A",
    PERMISSION="#EE90C8", TOOL="#9AA5CE", USER="#B49CF0", ASSISTANT="#8FD8E8",
    FOCUSED="#F0B57E", HEADING="#EE90C8", EMPHASIS="#F0B57E", CODE="#7DD39A",
    USER_TEXT="#E0D6F6", ASSISTANT_TEXT="#DCDAD0",
)

gruvbox = _palette(
    "gruvbox",  # fg1 text, fg2 answers, aqua user, orange answers
    BACKGROUND="#282828", DEFAULT="#EBDBB2", MUTED="#928374",
    ERROR="#FC5D48", WARNING="#FABD2F", SUCCESS="#A9C26A",
    PERMISSION="#D3869B", TOOL="#83A598", USER="#8EC07C", ASSISTANT="#FE8019",
    FOCUSED="#FABD2F", HEADING="#FE8019", EMPHASIS="#FABD2F", CODE="#8EC07C",
    USER_TEXT="#D3E2B4", ASSISTANT_TEXT="#D5C4A1",
)

tokyo_night = _palette(
    "tokyo-night",  # light teal user, fg_dark answers under a blue bar
    BACKGROUND="#1A1B26", DEFAULT="#C0CAF5", MUTED="#737AA2",
    ERROR="#F7768E", WARNING="#E0AF68", SUCCESS="#9ECE6A",
    PERMISSION="#BB9AF7", TOOL="#7DCFFF", USER="#73DACA", ASSISTANT="#7AA2F7",
    FOCUSED="#FF9E64", HEADING="#BB9AF7", EMPHASIS="#FF9E64", CODE="#7DCFFF",
    USER_TEXT="#C8F2EA", ASSISTANT_TEXT="#A9B1D6",
)


#: Built-in themes, always available (and overridable by ``themes/<name>.toml``).
BUILTIN_THEMES = {
    "terminal":     terminal,
    "moka":         moka,
    "nord":         nord,
    "dracula":      dracula,
    "gruvbox":      gruvbox,
    "tokyo-night":  tokyo_night,
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
        setattr(base, key.upper(), _color_from_spec(spec))
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
    "HEADING", "EMPHASIS", "CODE", "USER_TEXT", "ASSISTANT_TEXT",
)


def _color_to_toml(color) -> str:
    """Render one palette color as a theme file value."""
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
