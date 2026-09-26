# pico_chat/ui/tui/ — Terminal Rendering Engine

Low-level TUI framework. Custom-built; not a wrapper around curses or any
third-party library. Documentation style: one row per file.

See [notes/ui.md](../notes/ui.md) for the component model and layer stack.

---

## Files

| File | Purpose |
|------|---------|
| `terminal.py` | `Terminal` (raw mode, input capture), `ANSI` constants |
| `events.py` | Typed events: `KeyEvent`, `MouseEvent`, `PasteEvent`, `ResizeEvent`, `TickEvent`, `CommandEvent`; `normalize_key()` |
| `focus.py` | `FocusManager`, `FocusScope` (focus boundaries + `focus_at`) |
| `router.py` | `EventRouter` — overlay/component dispatch, child-first hit testing, `ActionMap` routing |
| `actions.py` | `Action`, `Actions`, `ActionMap`, `action()` — semantic operations decoupled from keys |
| `screen.py` | `Screen` — root component + optional focus/action scopes |
| `navigation.py` | `Navigator` (push/pop/replace/back), `ModalHost` (compositor modal ownership) |
| `chat_screen.py` | `ChatScreen(Screen)` — history + input workspace with pinned status bar |
| `scaffold.py` | `AppScaffold` — shared top-level screen chrome |
| `compositor.py` | `Compositor` — async render loop, overlay stack, FPS tracking, per-frame callbacks, Ctrl-C shutdown |
| `buffer.py` | `Cell`, `Buffer` (full-screen grid), `SubBuffer` (clipping viewport) |
| `container.py` | `Container`, `Split`/`Vsplit`/`Hsplit`, `Padding`, `Align`, `Stack`/`Overlay`, `ScrollView`; size policies `Fixed`/`Percent`/`Content`/`Fill` |
| `colors.py` | `RGB`, `ANSIColor`, `theme` palette, `set_theme()` |
| `layout_utils.py` | `wrap_text`, `display_width`, `strip_ansi`, `break_long_word` |
| `graphemes.py` | `split_clusters`, `count_nonws`, `advance_nonws` — grapheme-cluster helpers for stream reveal granularity |
| `fuzzy.py` | `fuzzy_match` (subsequence + indices, used by the file picker), `fuzzy_score`/`fuzzy_search` (word-based, used by menus) |
| `msg_types.py` | `MsgType` base + `UserMsg`, `PicoMsg`, `SysMsg{,Error,Warning}`, `ThinkingMsg`, `ToolCallMsg`, `AskPermissionMsg`; `MsgAction` enum |
| `ascii_table.py` | `TableStyle`, `AsciiTable` — used by the markdown table renderer |
| `syntax_highlight.py` | `highlight_line()`, `_resolve_lang()`, `_get_highlight_color()`, `_resolve_hex_color()` |
| `__init__.py` | Package docstring only |

---

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| [components/](./ui-tui-components.md) | Reusable UI widgets (Box, TextComponent, InputComponent, etc.) |
