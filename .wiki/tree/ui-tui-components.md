# moka_code/ui/tui/components/ — UI Components

Reusable TUI widgets. Most extend `Component` from `base.py`. Documentation
style: one row per file.

See [notes/ui.md](../notes/ui.md) for the component model overview.

---

## Files

| File | Purpose |
|------|---------|
| `base.py` | `Component` abstract base: `render(buffer)`, `handle_input(event)`, `dirty`, `set_layout()` |
| `text.py` | `TextComponent` (auto-scrolling text; base of `ChatHistoryPanel`/`DebugLogPanel`), `Label` (aligned text) |
| `box.py` | `Box` — border/title/action wrapper; `lines_only` mode; generic popup actions + hit regions; `inline_editor` |
| `message_view.py` | `MessageView(Box)` — borderless thread rendering, lifecycle gutter, collapsed lines |
| `button.py` | `Button` — focusable control (`activate` semantic action) |
| `choice.py` | `Checkbox`, `RadioGroup` — focusable boolean/single-select controls |
| `layout.py` | `EmptyLine`, `SeparatorLine` — spacing primitives |
| `bars.py` | `BarStyle`, `StatusBar` (fields), `ActionBar` (actions, `set_hint(text, color)`, `set_expanded`), `NoticeBand` (colored notice lines, `+N more`) |
| `menu.py` | `SelectionMenu` — floating dropdown with fuzzy filtering, item descriptions/footers, `measure_width()`, `title`/`status_text` |
| `search_modal.py` | `SearchModal(SelectionMenu)` — type-to-filter picker (used by `/model`, `/theme`), anchored above the input; fires `on_highlight` as the selection moves |
| `theme_preview.py` | `ThemePreview` — top-center overlay showing the live palette as swatches while picking a theme |
| `popup.py` | `Popup` + `PopupScreen` — centered overlay popup on `Box` + `TextComponent` |
| `debug_panel.py` | `DebugLogPanel` — capped, auto-scrolling log display |
| `debug_popup.py` | `DebugPopup` — compositor overlay for the debug console and activity surface |
| `table_view.py` | `TableView` — sized/measured columns, scrolling, clipping, row selection |
| `list_view.py` | `SelectionModel`, `ListView`, `Select` — generic list widgets (currently only referenced by tests) |
| `list_modal.py` | `ListModal` + `ListModalScreen` — centered modal list (currently only referenced by tests) |
| `markdown.py` | `Markdown` parser + `MarkdownComponent`; block/inline parsers; `StyledSegment`; table rendering via `AsciiTable`; append-only commit path (`find_commit_line`, `_committed_*`) and open-tail plain rendering for streaming |

---

## Input Subcomponent (`input/`)

The multi-line text editor, split across sub-modules:

| File | Purpose |
|------|---------|
| `input.py` | `InputComponent` — coordinator: cursor animation, completion menu, layout, param hints |
| `text_buffer.py` | `TextBuffer` — text storage, undo/redo, `(line, col)` cursor |
| `input_handlers.py` | `InputHandler` + `KeyboardHandler`/`MouseHandler`/`PasteHandler` |
| `completion.py` | `Completer` base + `CommandCompletion`, `SubcommandCompletion`, `ArgumentCompletion`, `ContextCompletion` — one trigger-based provider module |
| `scroll_manager.py` | Vertical scroll offset when text overflows |
| `cursor_renderer.py` | Cursor glyph + blink animation |
| `coordinate_mapper.py` | Screen coordinates ↔ `(line, col)`; text wrapping |
| `__init__.py` | Exports `InputComponent` |
