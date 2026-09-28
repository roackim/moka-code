
from dataclasses import dataclass

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
    
pastel = _theme(
    name="pastel",
    BACKGROUND  = RGB("#1E1E1E"),   # Dark gray (VS Code dark background)
    DEFAULT     = RGB("#D4D4D4"),   # Light gray (readable text)
    
    MUTED       = RGB("#808080"),   # Medium gray
    ERROR       = RGB("#F48771"),   # Soft red
    WARNING     = RGB("#CCA700"),   # Amber/gold
    SUCCESS     = RGB("#89D185"),   # Soft green
    PERMISSION  = RGB("#C586C0"),   # Purple/magenta for permission prompts
    TOOL        = RGB("#4FC1FF"),   # Tool-call blue
    
    USER        = RGB("#4EC9B0"),   # Cyan/teal
    ASSISTANT   = RGB("#569CD6"),   # Blue
    FOCUSED     = RGB("#DCDCAA"),   # Yellow-beige
)

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
)

# --- Built-in RGB palettes ------------------------------------------------

nord = _theme(
    name="nord",
    BACKGROUND  = RGB("#2E3440"),
    DEFAULT     = RGB("#D8DEE9"),
    MUTED       = RGB("#4C566A"),
    ERROR       = RGB("#BF616A"),
    WARNING     = RGB("#EBCB8B"),
    SUCCESS     = RGB("#A3BE8C"),
    PERMISSION  = RGB("#B48EAD"),
    TOOL        = RGB("#5E81AC"),
    USER        = RGB("#88C0D0"),
    ASSISTANT   = RGB("#81A1C1"),
    FOCUSED     = RGB("#EBCB8B"),
)

dracula = _theme(
    name="dracula",
    BACKGROUND  = RGB("#282A36"),
    DEFAULT     = RGB("#F8F8F2"),
    MUTED       = RGB("#6272A4"),
    ERROR       = RGB("#FF5555"),
    WARNING     = RGB("#F1FA8C"),
    SUCCESS     = RGB("#50FA7B"),
    PERMISSION  = RGB("#FF79C6"),
    TOOL        = RGB("#8BE9FD"),
    USER        = RGB("#8BE9FD"),
    ASSISTANT   = RGB("#BD93F9"),
    FOCUSED     = RGB("#F1FA8C"),
)

gruvbox = _theme(
    name="gruvbox",
    BACKGROUND  = RGB("#282828"),
    DEFAULT     = RGB("#EBDBB2"),
    MUTED       = RGB("#928374"),
    ERROR       = RGB("#FB4934"),
    WARNING     = RGB("#FABD2F"),
    SUCCESS     = RGB("#B8BB26"),
    PERMISSION  = RGB("#D3869B"),
    TOOL        = RGB("#458588"),
    USER        = RGB("#8EC07C"),
    ASSISTANT   = RGB("#83A598"),
    FOCUSED     = RGB("#FABD2F"),
)

solarized = _theme(
    name="solarized",
    BACKGROUND  = RGB("#002B36"),
    DEFAULT     = RGB("#839496"),
    MUTED       = RGB("#586E75"),
    ERROR       = RGB("#DC322F"),
    WARNING     = RGB("#B58900"),
    SUCCESS     = RGB("#859900"),
    PERMISSION  = RGB("#D33682"),
    TOOL        = RGB("#6C71C4"),
    USER        = RGB("#2AA198"),
    ASSISTANT   = RGB("#268BD2"),
    FOCUSED     = RGB("#B58900"),
)

one_dark = _theme(
    name="one-dark",
    BACKGROUND  = RGB("#282C34"),
    DEFAULT     = RGB("#ABB2BF"),
    MUTED       = RGB("#5C6370"),
    ERROR       = RGB("#E06C75"),
    WARNING     = RGB("#E5C07B"),
    SUCCESS     = RGB("#98C379"),
    PERMISSION  = RGB("#C678DD"),
    TOOL        = RGB("#528BFF"),
    USER        = RGB("#56B6C2"),
    ASSISTANT   = RGB("#61AFEF"),
    FOCUSED     = RGB("#E5C07B"),
)

catppuccin = _theme(
    name="catppuccin",
    BACKGROUND  = RGB("#1E1E2E"),
    DEFAULT     = RGB("#CDD6F4"),
    MUTED       = RGB("#6C7086"),
    ERROR       = RGB("#F38BA8"),
    WARNING     = RGB("#F9E2AF"),
    SUCCESS     = RGB("#A6E3A1"),
    PERMISSION  = RGB("#F5C2E7"),
    TOOL        = RGB("#74C7EC"),
    USER        = RGB("#94E2D5"),
    ASSISTANT   = RGB("#89B4FA"),
    FOCUSED     = RGB("#F9E2AF"),
)

tokyo_night = _theme(
    name="tokyo-night",
    BACKGROUND  = RGB("#1A1B26"),
    DEFAULT     = RGB("#C0CAF5"),
    MUTED       = RGB("#565F89"),
    ERROR       = RGB("#F7768E"),
    WARNING     = RGB("#E0AF68"),
    SUCCESS     = RGB("#9ECE6A"),
    PERMISSION  = RGB("#BB9AF7"),
    TOOL        = RGB("#2AC3DE"),
    USER        = RGB("#7DCFFF"),
    ASSISTANT   = RGB("#7AA2F7"),
    FOCUSED     = RGB("#E0AF68"),
)

rose_pine = _theme(
    name="rose-pine",
    BACKGROUND  = RGB("#191724"),
    DEFAULT     = RGB("#E0DEF4"),
    MUTED       = RGB("#6E6A86"),
    ERROR       = RGB("#EB6F92"),
    WARNING     = RGB("#F6C177"),
    SUCCESS     = RGB("#9CCFD8"),
    PERMISSION  = RGB("#C4A7E7"),
    TOOL        = RGB("#908CAA"),
    USER        = RGB("#EBBCBA"),
    ASSISTANT   = RGB("#31748F"),
    FOCUSED     = RGB("#F6C177"),
)

everforest = _theme(
    name="everforest",
    BACKGROUND  = RGB("#2D353B"),
    DEFAULT     = RGB("#D3C6AA"),
    MUTED       = RGB("#859289"),
    ERROR       = RGB("#E67E80"),
    WARNING     = RGB("#DBBC7F"),
    SUCCESS     = RGB("#A7C080"),
    PERMISSION  = RGB("#D699B6"),
    TOOL        = RGB("#7FBBB3"),
    USER        = RGB("#83C092"),
    ASSISTANT   = RGB("#7FBBB3"),
    FOCUSED     = RGB("#DBBC7F"),
)

monokai = _theme(
    name="monokai",
    BACKGROUND  = RGB("#272822"),
    DEFAULT     = RGB("#F8F8F2"),
    MUTED       = RGB("#75715E"),
    ERROR       = RGB("#F92672"),
    WARNING     = RGB("#E6DB74"),
    SUCCESS     = RGB("#A6E22E"),
    PERMISSION  = RGB("#AE81FF"),
    TOOL        = RGB("#66D9EF"),
    USER        = RGB("#66D9EF"),
    ASSISTANT   = RGB("#FD971F"),
    FOCUSED     = RGB("#E6DB74"),
)

ayu_dark = _theme(
    name="ayu-dark",
    BACKGROUND  = RGB("#0B0E14"),
    DEFAULT     = RGB("#BFBDB6"),
    MUTED       = RGB("#565B66"),
    ERROR       = RGB("#F07178"),
    WARNING     = RGB("#FFB454"),
    SUCCESS     = RGB("#AAD94C"),
    PERMISSION  = RGB("#D2A6FF"),
    TOOL        = RGB("#39BAE6"),
    USER        = RGB("#95E6CB"),
    ASSISTANT   = RGB("#59C2FF"),
    FOCUSED     = RGB("#FFB454"),
)

kanagawa = _theme(
    name="kanagawa",
    BACKGROUND  = RGB("#1F1F28"),
    DEFAULT     = RGB("#DCD7BA"),
    MUTED       = RGB("#727169"),
    ERROR       = RGB("#E82424"),
    WARNING     = RGB("#C0A36E"),
    SUCCESS     = RGB("#76946A"),
    PERMISSION  = RGB("#957FB8"),
    TOOL        = RGB("#7FB4CA"),
    USER        = RGB("#7AA89F"),
    ASSISTANT   = RGB("#7E9CD8"),
    FOCUSED     = RGB("#C0A36E"),
)


#: Built-in themes, always available (and overridable via ``themes.toml``).
BUILTIN_THEMES = {
    "terminal":     terminal,
    "pastel":       pastel,
    "nord":         nord,
    "dracula":      dracula,
    "gruvbox":      gruvbox,
    "solarized":    solarized,
    "one-dark":     one_dark,
    "catppuccin":   catppuccin,
    "tokyo-night":  tokyo_night,
    "rose-pine":    rose_pine,
    "everforest":   everforest,
    "monokai":      monokai,
    "ayu-dark":     ayu_dark,
    "kanagawa":     kanagawa,
}

theme: _theme = terminal.copy()


def _color_from_spec(spec):
    """Build a color from a ``themes.toml`` palette value.

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


def _theme_from_palette(name: str, palette: dict) -> "_theme":
    base = BUILTIN_THEMES.get(name, terminal).copy()
    base.name = name
    for key, spec in palette.items():
        setattr(base, key.upper(), _color_from_spec(spec))
    return base


def available_themes() -> dict:
    """Built-in themes plus user ``[themes.<name>]`` definitions from config."""
    themes = dict(BUILTIN_THEMES)
    for name, palette in getattr(settings.config, "themes", {}).items():
        try:
            themes[name] = _theme_from_palette(name, palette)
        except Exception:
            continue
    return themes


def theme_names() -> list:
    """Selectable theme names (built-ins + user-defined)."""
    return sorted(available_themes())


#: Palette fields in display/file order.
PALETTE_FIELDS = (
    "BACKGROUND", "DEFAULT", "MUTED", "ERROR", "WARNING",
    "SUCCESS", "PERMISSION", "TOOL", "USER", "ASSISTANT", "FOCUSED",
)


def _color_to_toml(color) -> str:
    """Render one palette color as a ``themes.toml`` value."""
    if hasattr(color, "r"):
        return f'"#{color.r:02X}{color.g:02X}{color.b:02X}"'
    return f"{{ ansi = {color.fg}, bg = {color.bg} }}"


def theme_toml_section(name: str) -> "str | None":
    """Render a ``[themes.<name>]`` block for an existing theme, or None.

    Used by ``/config theme <id>`` to materialize a built-in as an override.
    """
    selected = available_themes().get(name)
    if selected is None:
        return None
    lines = [f"[themes.{name}]"]
    lines.extend(
        f"{field} = {_color_to_toml(getattr(selected, field))}"
        for field in PALETTE_FIELDS
    )
    return "\n".join(lines) + "\n"


def set_theme(name: str):
    """Switch the active theme by name. Unknown names fall back to terminal."""
    selected = available_themes().get(name) or terminal
    theme.__dict__.update(selected.__dict__)
