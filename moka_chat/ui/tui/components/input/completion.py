"""Trigger-based input completion.

One module owns every completion provider and their shared lifecycle:

    update(text, cursor_pos)     -> refresh the menu
    accept_selection(...)        -> commit the highlighted candidate
    cancel(text, cursor_pos)     -> ESC: suppress the current word
    hide() / navigate_up() / navigate_down()
    trigger_pos(text, cursor_pos)-> where to anchor the menu

Providers differ only in trigger and candidates:

- :class:`CommandCompletion`  — ``/`` at line start
- :class:`SubcommandCompletion` — ``/cmd `` sub-command
- :class:`ArgumentCompletion` — generic ``Param.completions`` arguments
- :class:`ContextCompletion` — ``@`` file/folder paths

The shared base centralises the "suppressed word" rule: after ESC the menu
stays hidden while the user keeps typing the same prefix, and reappears once
the word changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.components.menu import SelectionMenu
from moka_chat.ui.tui.fuzzy import fuzzy_match

if TYPE_CHECKING:
    from moka_chat.ui.commands import Command


class Completer:
    """Base class for a menu-backed completion provider."""

    def __init__(self, menu: SelectionMenu):
        self.menu = menu
        self.is_active = False
        # The word the user dismissed with ESC; the menu stays hidden while
        # the current word still starts with it.
        self.suppressed_word: Optional[str] = None

    # -- suppression ---------------------------------------------------

    def _refresh_suppression(self, current_word: Optional[str]) -> None:
        """Forget the suppressed word once the user has moved on."""
        if not self.suppressed_word:
            return
        if not current_word or not current_word.startswith(self.suppressed_word):
            self.suppressed_word = None

    def _is_suppressed(self, current_word: Optional[str]) -> bool:
        """True when the menu must stay hidden for the current word."""
        return bool(
            current_word
            and self.suppressed_word
            and current_word.startswith(self.suppressed_word)
        )

    def _suppress(self, current_word: Optional[str]) -> None:
        """Record the current word as dismissed and hide the menu."""
        if current_word:
            self.suppressed_word = current_word
        self.hide()

    # -- menu lifecycle ------------------------------------------------

    def hide(self) -> None:
        """Deactivate and hide the menu."""
        self.menu.hide()
        self.is_active = False

    def navigate_up(self) -> None:
        if self.is_active:
            self.menu.action_up()

    def navigate_down(self) -> None:
        if self.is_active:
            self.menu.action_down()

    def _show(self, items: List[str], search_term: str,
              display_prefix: str = "", descriptions: Optional[dict] = None) -> None:
        """Push candidates to the menu and sync the active flag."""
        self.menu.update(items, search_term, display_prefix=display_prefix,
                         descriptions=descriptions)
        self.is_active = self.menu.is_visible

    # -- interface -----------------------------------------------------

    def update(self, text: str, cursor_pos: int) -> None:
        raise NotImplementedError

    def accept_selection(self, text: str, cursor_pos: int) -> Optional[tuple[str, int]]:
        raise NotImplementedError

    def cancel(self, text: str, cursor_pos: int) -> None:
        raise NotImplementedError

    def trigger_pos(self, text: str, cursor_pos: int) -> Optional[int]:
        """Anchor position for the menu (index into ``text``), or None."""
        return None

    def _leading_ws(self, text: str) -> int:
        clean = text.lstrip()
        return len(text) - len(clean)

    def _apply_selector_style(self) -> None:
        """One shared look for every completion menu.

        Full width, ``USER`` accent frame, normal content color, and muted
        aligned descriptions. All providers must call this instead of styling
        their menu in isolation — see ``.wiki/notes/ui.md``
        ("Completion menu styling").
        """
        apply_theme = getattr(self.menu, "apply_theme", None)
        if callable(apply_theme):
            apply_theme()
        self.menu.set_fill_width(True)
        self.menu.frame_color = theme.USER
        self.menu.content_color = theme.DEFAULT

    def refresh_theme(self) -> None:
        """Re-apply the shared selector style after a theme switch."""
        self._apply_selector_style()


# ---------------------------------------------------------------------------
# /command
# ---------------------------------------------------------------------------


class CommandCompletion(Completer):
    """Manages /command completion with auto-show menu."""

    def __init__(self, menu, commands: List[str],
                 descriptions: Optional[dict] = None):
        super().__init__(menu)
        self.commands = commands
        self.descriptions = dict(descriptions or {})
        self._apply_selector_style()

    def should_trigger(self, text: str) -> bool:
        """Check if command completion should be active."""
        clean = text.lstrip()
        # Trigger: starts with '/', no space (not a complete command)
        return clean.startswith('/') and ' ' not in clean

    def get_current_command_word(self, text: str, cursor_pos: int) -> Optional[str]:
        """Extract the /command word at cursor position (without /)."""
        clean = text.lstrip()

        # Must start with /
        if not clean.startswith('/'):
            return None

        # Find word boundaries (no space allowed in commands)
        if ' ' in clean:
            word_part = clean.split(' ', 1)[0]
        else:
            word_part = clean

        # Return without the leading /
        return word_part[1:] if len(word_part) > 1 else ""

    def update(self, text: str, cursor_pos: int):
        """Auto-update menu based on current text and cursor position."""
        current_word = self.get_current_command_word(text, cursor_pos)
        self._refresh_suppression(current_word)

        if self._is_suppressed(current_word):
            self.hide()
            return

        if not self.should_trigger(text):
            self.hide()
            return

        search_term = text.lstrip()[1:]  # Remove leading '/'

        # Filter out exact matches (command is already complete)
        if search_term.lower() in [cmd.lower() for cmd in self.commands]:
            self.hide()
            return

        self._show(self.commands, search_term, display_prefix="/",
                   descriptions=self.descriptions)

    def accept_selection(self, text: str, cursor_pos: int) -> Optional[tuple[str, int]]:
        """Accept current selection, return (completed_text, cursor_pos)."""
        selected = self.menu.get_selected()
        if not selected:
            return None
        completed = f"/{selected}"
        return completed, len(completed)

    def cancel(self, text: str, cursor_pos: int):
        """User pressed ESC - suppress menu for current word."""
        self._suppress(self.get_current_command_word(text, cursor_pos))

    def trigger_pos(self, text: str, cursor_pos: int) -> Optional[int]:
        return self._leading_ws(text)


# ---------------------------------------------------------------------------
# /command sub-command
# ---------------------------------------------------------------------------


class SubcommandCompletion(Completer):
    """Manages /command subcommand completion with auto-show menu."""

    def __init__(self, menu, get_subcommands_callback: Callable[[str], List[str]],
                 get_descriptions_callback: Optional[Callable[[str], dict]] = None):
        super().__init__(menu)
        self.get_subcommands = get_subcommands_callback
        self.get_descriptions = get_descriptions_callback
        self._apply_selector_style()
        # Track which command we're completing for, so suppression memory is
        # cleared when the parent command changes.
        self.current_parent_command: Optional[str] = None

    def parse_command_line(self, text: str) -> Optional[tuple[str, str]]:
        """Parse /command subcommand format. Returns (command, subcommand_text) or None."""
        clean = text.lstrip()

        # Must start with / and have a space
        if not clean.startswith('/') or ' ' not in clean:
            return None

        parts = clean.split(' ', 1)
        command = parts[0][1:]  # Remove leading /
        subcommand_text = parts[1] if len(parts) > 1 else ""

        return (command, subcommand_text)

    def should_trigger(self, text: str) -> bool:
        """Check if subcommand completion should be active."""
        parsed = self.parse_command_line(text)
        if not parsed:
            return False

        command, _ = parsed
        return len(self.get_subcommands(command)) > 0

    def update(self, text: str, cursor_pos: int):
        """Auto-update menu based on current text and cursor position."""
        parsed = self.parse_command_line(text)

        if not parsed:
            self.hide()
            self.current_parent_command = None
            return

        command, subcommand_text = parsed
        subcommands = self.get_subcommands(command)
        if not subcommands:
            self.hide()
            self.current_parent_command = None
            return

        # If the parent command changed, forget the suppressed word.
        if self.current_parent_command != command:
            self.suppressed_word = None
            self.current_parent_command = command

        self._refresh_suppression(subcommand_text)
        if self._is_suppressed(subcommand_text):
            self.hide()
            return

        subcommand_trimmed = subcommand_text.rstrip()

        # Hide when the subcommand is already complete and valid.
        if subcommand_trimmed in subcommands:
            self.hide()
            return

        # Hide once the user is typing arguments after a valid subcommand
        # (e.g. "fps 5" where "fps" is a valid subcommand).
        first_word = subcommand_text.split()[0] if subcommand_text.split() else ""
        if first_word in subcommands and ' ' in subcommand_text:
            self.hide()
            return

        descriptions = self.get_descriptions(command) if self.get_descriptions else None
        self._show(subcommands, subcommand_trimmed, descriptions=descriptions)

    def accept_selection(self, text: str, cursor_pos: int) -> Optional[tuple[str, int]]:
        """Accept current selection, return (completed_text, cursor_pos)."""
        selected = self.menu.get_selected()
        if not selected:
            return None

        parsed = self.parse_command_line(text)
        if not parsed:
            return None

        command, _ = parsed
        completed = f"/{command} {selected}"
        return completed, len(completed)

    def cancel(self, text: str, cursor_pos: int):
        """User pressed ESC - suppress menu for current subcommand prefix."""
        parsed = self.parse_command_line(text)
        if parsed:
            _, subcommand_text = parsed
            self._suppress(subcommand_text)
        else:
            self.hide()

    def trigger_pos(self, text: str, cursor_pos: int) -> Optional[int]:
        clean = text.lstrip()
        leading = self._leading_ws(text)
        if ' ' in clean:
            return leading + clean.find(' ') + 1
        return leading


# ---------------------------------------------------------------------------
# generic argument completion (Param-driven)
# ---------------------------------------------------------------------------


class ArgumentCompletion(Completer):
    """Provides fuzzy autocomplete for command arguments based on Param definitions.

    Works for both top-level commands and nested subcommands. The caller
    (InputComponent) is responsible for only calling update() when a deeper
    completer (CommandCompletion, SubcommandCompletion) is not active.
    """

    def __init__(self, menu, commands: Dict[str, "Command"]):
        super().__init__(menu)
        self.commands = commands  # The COMMANDS registry
        self._apply_selector_style()

    def _resolve(self, text: str) -> Optional[tuple["Command", int, str, tuple[str, ...]]]:
        """Parse the input text and resolve to (command, arg_index, current_arg_text, prior_args).

        Returns None if no argument completion is applicable.
        """
        clean = text.lstrip()
        if not clean.startswith('/'):
            return None

        parts = clean.split()
        if not parts:
            return None

        cmd_name = parts[0][1:]  # strip leading '/'
        if cmd_name not in self.commands:
            return None

        root_cmd = self.commands[cmd_name]

        # Walk subcommands to find the deepest resolved command
        cmd, offset = root_cmd.resolve_command(parts[1:])

        # The arg_index is relative to the resolved command's own params
        # parts = ["/cmd", "sub1", "sub2", "arg0", "arg1", ...]
        # offset counts how many subcommands were consumed from parts[1:]
        # So the args start at parts[1 + offset]
        args_start = 1 + offset  # index in `parts` where cmd's own args begin
        rest = clean.split(' ', args_start)
        # rest has args_start+1 elements; the last element is everything after the consumed parts
        if len(rest) <= args_start:
            # Nothing typed for args yet — but check if there's a trailing space
            if clean.endswith(' '):
                arg_index = 0
                current_text = ''
                prior_args: tuple[str, ...] = ()
            else:
                return None  # still typing subcommand
        else:
            after = rest[args_start]
            # Count how many complete args precede the current one
            after_parts = after.split()
            # Determine if user is between args (trailing space) or on one
            if after.endswith(' ') or not after_parts:
                # Between args or empty — completing the NEXT arg
                arg_index = len(after_parts)
                current_text = ''
            else:
                # On an arg — completing the CURRENT arg
                arg_index = len(after_parts) - 1
                current_text = after_parts[-1]
            prior_args = tuple(after_parts[:arg_index])

        return cmd, arg_index, current_text, prior_args

    def update(self, text: str, cursor_pos: int):
        """Auto-update menu based on current text and cursor position."""
        result = self._resolve(text)
        if not result:
            self.hide()
            return

        cmd, arg_index, current_text, prior_args = result

        # Get completions from the resolved command
        items = cmd.get_completions(arg_index, prior_args)
        if not items:
            self.hide()
            return

        descriptions = cmd.get_descriptions(arg_index, prior_args)

        # Suppression
        self._refresh_suppression(current_text)
        if self._is_suppressed(current_text):
            self.hide()
            return

        # Hide if exact match already typed
        if current_text in items:
            self.hide()
            return

        # Fuzzy filter and show
        self._show(items, current_text, descriptions=descriptions or None)

    def accept_selection(self, text: str, cursor_pos: int) -> Optional[tuple[str, int]]:
        """Accept current selection, return (completed_text, cursor_pos)."""
        selected = self.menu.get_selected()
        if not selected:
            return None

        result = self._resolve(text)
        if not result:
            return None

        cmd, arg_index, current_text, _prior_args = result
        clean = text.lstrip()
        parts = clean.split()

        # Rebuild the command prefix (everything up to and including subcommands)
        cmd_name = parts[0][1:]
        root_cmd = self.commands[cmd_name]
        _, offset = root_cmd.resolve_command(parts[1:])

        # Prefix = "/cmd sub1 sub2 ..."
        prefix_parts = parts[:1 + offset]
        prefix = ' '.join(prefix_parts)

        # Rebuild the args portion, replacing the current arg with the selection
        args_start = 1 + offset
        after = clean.split(' ', args_start)
        if len(after) <= args_start:
            existing_args: List[str] = []
        else:
            # Split existing args but drop the last (incomplete) one
            existing_args = after[args_start].split()
            if not clean.endswith(' '):
                existing_args = existing_args[:-1]  # drop the partial arg

        # Build final text (no trailing space: the user types one to go on)
        all_args = existing_args + [selected]
        completed = f"{prefix} {' '.join(all_args)}"
        return completed, len(completed)

    def cancel(self, text: str, cursor_pos: int):
        """User pressed ESC — suppress menu for current word."""
        result = self._resolve(text)
        if result:
            current_text = result[2]
            self._suppress(current_text)
        else:
            self.hide()

    def trigger_pos(self, text: str, cursor_pos: int) -> Optional[int]:
        clean = text.lstrip()
        leading = self._leading_ws(text)
        # Start of the current argument (after the last space), else the command.
        last_space = clean.rfind(' ')
        return leading + (last_space + 1 if last_space != -1 else 0)


# ---------------------------------------------------------------------------
# @ context (file/folder paths)
# ---------------------------------------------------------------------------


class ContextCompletion(Completer):
    """Manages file/folder completion with auto-show menu.

    Args:
        menu: SelectionMenu instance for displaying completions
        get_items_callback: Callable returning list of available items
        trigger: Trigger prefix string (default: "@")
    """

    def __init__(self, menu, get_items_callback: Callable[[], List[str]], trigger: str = "@"):
        super().__init__(menu)
        self.get_items = get_items_callback
        self.trigger = trigger
        self.trigger_len = len(trigger)
        # File paths are long; let the menu use the available width.
        self._apply_selector_style()

    def find_trigger_position(self, text: str, cursor_pos: int) -> Optional[int]:
        """Find the last trigger before cursor position."""
        # Only look up to cursor position
        text_before_cursor = text[:cursor_pos]

        # Look for trigger pattern
        last_trigger = text_before_cursor.rfind(self.trigger)

        if last_trigger == -1:
            return None

        # Only trigger at a word boundary (start or after whitespace), so the
        # file picker does not fire inside e.g. an email address ("a@b").
        if last_trigger > 0 and not text[last_trigger - 1].isspace():
            return None

        # Check if there's a space after the trigger (means context is complete)
        text_after_trigger = text[last_trigger + self.trigger_len:cursor_pos]
        if ' ' in text_after_trigger:
            return None

        return last_trigger

    def get_current_context_word(self, text: str, cursor_pos: int) -> Optional[str]:
        """Extract the word after trigger at cursor position (without trigger)."""
        trigger_pos = self.find_trigger_position(text, cursor_pos)
        if trigger_pos is None:
            return None

        # Extract text from trigger to cursor
        text_after_trigger = text[trigger_pos + self.trigger_len:cursor_pos]

        # Word ends at space or cursor
        if ' ' in text_after_trigger:
            return None

        return text_after_trigger

    def _resolve_items(self, current_word: str) -> List[str]:
        """Resolve the file list for the current word.

        If ``current_word`` points into a directory (e.g. ``src/`` or a partial
        ``src/fo``), list that directory's immediate children as full relative
        paths (e.g. ``src/a.py``, ``src/sub/``) so the user sees the aggregated
        path. Otherwise return the top-level listing.
        """
        items = self.get_items()
        if not current_word:
            return items
        # Determine the directory prefix the user is drilling into. For
        # "src/" it's "src/"; for a partial "src/fo" it's "src/".
        if current_word.endswith('/'):
            prefix = current_word
        elif '/' in current_word:
            prefix = current_word.rsplit('/', 1)[0] + '/'
        else:
            # No slash: still at the top level.
            return items
        # Match items that live under this directory prefix.
        children = [it for it in items if it.startswith(prefix) and it != prefix]
        if children:
            # Keep only immediate children (no further "/" beyond a trailing
            # dir slash), so we show one level at a time — but keep the full
            # relative path so the user sees the aggregated path.
            immediate = [it for it in children if '/' not in it[len(prefix):].rstrip('/')]
            # Offer a ".." entry to navigate back up a level. It goes LAST so
            # it is never the default highlight — the user must explicitly
            # select it.
            return immediate + ["../"]
        return items

    def update(self, text: str, cursor_pos: int):
        """Auto-update menu based on current text and cursor position."""
        current_word = self.get_current_context_word(text, cursor_pos)
        self._refresh_suppression(current_word)

        if self._is_suppressed(current_word):
            self.hide()
            return

        # No trigger found
        if current_word is None:
            self.hide()
            return

        # Get available items (resolved for subdirectory drilling)
        items = self._resolve_items(current_word)
        if not items:
            self.hide()
            return

        # Filter out exact matches (path is already complete and valid)
        if current_word in items:
            self.hide()
            return

        # When drilling into a directory, the fuzzy search term is the text after
        # the last slash. For "src/" that's empty (show all children); for a
        # partial "src/fo" it's "fo" (filter within the directory).
        if '/' in current_word:
            search_term = current_word.rsplit('/', 1)[-1]
        else:
            search_term = current_word

        # Keep the "../" navigation entry always visible when drilling, even
        # while the user types a partial name that would fuzzy-filter it out.
        # It stays at the END so it is never the default highlight.
        has_parent = "../" in items
        if has_parent:
            rest = [it for it in items if it != "../"]
        else:
            rest = items

        # Rank the candidates. With a search term, use the subsequence matcher
        # (score + matched indices for highlighting); without one, list
        # directories before files, alphabetically.
        if search_term:
            scored = []
            for it in rest:
                match = fuzzy_match(search_term, it)
                if match is not None:
                    scored.append((it, match[0]))
            scored.sort(key=lambda t: (-t[1], t[0].count('/'), len(t[0]), t[0].lower()))
            ranked = [t[0] for t in scored]
        else:
            dirs = sorted((it for it in rest if it.endswith('/')), key=str.lower)
            files = sorted((it for it in rest if not it.endswith('/')), key=str.lower)
            ranked = dirs + files

        if has_parent:
            ranked.append("../")

        if not ranked:
            self.hide()
            return

        # No display prefix: items are relative paths, so showing "@" would be
        # redundant.
        self.menu.set_items(ranked)
        self.is_active = self.menu.is_visible

    def accept_selection(self, text: str, cursor_pos: int) -> Optional[tuple[str, int]]:
        """Accept current selection, return (new_text, new_cursor_pos)."""
        selected = self.menu.get_selected()
        if not selected:
            return None

        trigger_pos = self.find_trigger_position(text, cursor_pos)
        if trigger_pos is None:
            return None

        # Replace the whole current word (trigger to the next whitespace), not
        # just up to the cursor, so accepting with the cursor mid-word doesn't
        # leave trailing letters behind.
        word_end = cursor_pos
        while word_end < len(text) and not text[word_end].isspace():
            word_end += 1
        tail = text[word_end:]

        # Preserve any directory prefix already typed (e.g. "@src/").
        current_word = self.get_current_context_word(text, cursor_pos) or ""
        prefix = current_word if current_word.endswith('/') else ""

        # The ".." entry navigates up one level: drop the last path segment.
        if selected == "../":
            if prefix:
                stripped = prefix.rstrip('/')
                if '/' in stripped:
                    parent = stripped.rsplit('/', 1)[0] + '/'
                else:
                    # Single-level dir (e.g. "src/") — going up returns to root.
                    parent = ""
                new_text = text[:trigger_pos] + self.trigger + parent + tail
                new_cursor_pos = trigger_pos + self.trigger_len + len(parent)
            else:
                new_text = text[:trigger_pos] + self.trigger + tail
                new_cursor_pos = trigger_pos + self.trigger_len
            return (new_text, new_cursor_pos)

        # _resolve_items returns full relative paths (e.g. "notes/doc.md"), so
        # we must NOT re-prepend the directory prefix — that would double it
        # ("@notes/notes/doc.md"). Just insert the selected path as-is.
        new_text = text[:trigger_pos] + self.trigger + selected + tail
        new_cursor_pos = trigger_pos + self.trigger_len + len(selected)

        return (new_text, new_cursor_pos)

    def cancel(self, text: str, cursor_pos: int):
        """User pressed ESC - suppress menu for current word prefix."""
        self._suppress(self.get_current_context_word(text, cursor_pos))

    def trigger_pos(self, text: str, cursor_pos: int) -> Optional[int]:
        return self.find_trigger_position(text, cursor_pos)


__all__ = [
    "Completer",
    "CommandCompletion",
    "SubcommandCompletion",
    "ArgumentCompletion",
    "ContextCompletion",
]
