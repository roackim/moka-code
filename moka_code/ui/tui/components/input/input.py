from typing import Optional, Any, List, Callable, Dict
from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.components.menu import SelectionMenu
from moka_code.ui.tui.fuzzy import fuzzy_search
from .text_buffer import TextBuffer
from .coordinate_mapper import CoordinateMapper
from .input_handlers import (
    InputHandler, InputContext, KeyboardHandler, MouseHandler, PasteHandler
)
from .scroll_manager import ScrollManager
from .cursor_renderer import CursorRenderer
from .completion import (
    CommandCompletion,
    SubcommandCompletion,
    ArgumentCompletion,
    ContextCompletion,
)
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.events import KeyEvent, MouseEvent, TickEvent
from moka_code.ui.tui.layout_utils import display_width
from moka_code.ui.tui.components.text import paint_spans

from moka_code.ui.tui.colors import theme

class InputComponent(Component):
    """Multi-line text input component with menus, scrolling, and cursor animation."""
    
    def __init__(self, prompt: str = "> ", id: Optional[str] = None, frame_color=None, content_color=None):
        super().__init__(id)
        self.prompt = prompt
        self.frame_color = frame_color if frame_color is not None else theme.DEFAULT
        self.content_color = content_color if content_color is not None else theme.DEFAULT
        self._content_color_provider: Optional[callable] = None
        self.bg = theme.get_bg()  # Use global background
        self.config = None  # Config object passed during initialization
        self.focused = True  # Track focus state for input handling and cursor rendering
        # Optional ``line -> [(start, end, fg)]``: spans drawn in their own color.
        self.highlighter = None
        
        # Core components
        self.buffer = TextBuffer()
        self.coord_mapper = CoordinateMapper(prompt, self.width)
        self.scroll_manager = ScrollManager(
            self.buffer,
            self.coord_mapper,
            lambda: self.height
        )
        self.cursor_renderer = CursorRenderer(lambda: self.config)
        
        # Input handlers
        self.keyboard_handler = KeyboardHandler()
        self.input_handlers: List[InputHandler] = [
            PasteHandler(),
            MouseHandler(),
            self.keyboard_handler
        ]
        
        # Completion system (lazy-initialized)
        self.command_completion: Optional[CommandCompletion] = None
        self.command_list: List[str] = []
        self.command_descriptions: Dict[str, str] = {}
        self.subcommand_completion: Optional[SubcommandCompletion] = None
        self.subcommand_callback: Optional[Callable[[str], List[str]]] = None
        self.subcommand_descriptions_callback: Optional[Callable[[str], Dict[str, str]]] = None
        self.context_completion: Optional[ContextCompletion] = None
        self.context_items_callback: Optional[Callable[[], List[str]]] = None
        self.argument_completion: Optional[ArgumentCompletion] = None
        self._command_registry: Optional[Dict[str, Any]] = None  # COMMANDS dict for generic hints/args
        # Notified (no args) whenever the text changes, so hosts can refresh
        # context-sensitive chrome (e.g. the action line).
        self.on_change = None
    
    @property
    def on_submit(self):
        """Get submit callback."""
        return self.keyboard_handler.on_submit
    
    @on_submit.setter
    def on_submit(self, callback):
        """Set submit callback."""
        self.keyboard_handler.on_submit = callback
    
    @property
    def text(self) -> str:
        """Get current text (for backward compatibility)."""
        return self.buffer.text
    
    @text.setter
    def text(self, value: str):
        """Set text (for backward compatibility)."""
        self.buffer.text = value
        # Mark parent for re-render
        if hasattr(self, 'parent') and self.parent and hasattr(self.parent, 'mark_changed'):
            self.parent.mark_changed()
    
    @property
    def cursor_pos(self) -> int:
        """Get cursor position (for backward compatibility)."""
        return self.buffer.cursor_pos
    
    @cursor_pos.setter
    def cursor_pos(self, value: int):
        """Set cursor position (for backward compatibility)."""
        self.buffer.cursor_pos = value
    
    def setup_commands(self, commands: List[str], descriptions: Optional[Dict[str, str]] = None):
        """Initialize command completion (optionally with one-line descriptions)."""
        self.command_list = commands
        self.command_descriptions = dict(descriptions or {})
    
    def setup_subcommands(self, get_subcommands_callback: Callable[[str], List[str]],
                          get_descriptions_callback: Optional[Callable[[str], Dict[str, str]]] = None):
        """Initialize subcommand completion system."""
        self.subcommand_callback = get_subcommands_callback
        self.subcommand_descriptions_callback = get_descriptions_callback
    
    def setup_context(self, get_items_callback: Callable[[], List[str]]):
        """Initialize context (./file) completion system."""
        self.context_items_callback = get_items_callback

    def setup_command_registry(self, registry: Dict[str, Any]):
        """Store the COMMANDS registry for generic argument hints and completion."""
        self._command_registry = registry
    
    def _ensure_command_menu(self):
        """Lazy-create command menu and completion system on first use."""
        if self.command_completion is None and self.command_list:
            compositor = self.compositor_ref if hasattr(self, 'compositor_ref') else None
            menu = SelectionMenu(
                compositor=compositor,
                frame_color=self.content_color,
                content_color=self.content_color
            )
            self.command_completion = CommandCompletion(
                menu, self.command_list, self.command_descriptions)
    
    def _ensure_subcommand_menu(self):
        """Lazy-create subcommand menu and completion system on first use."""
        if self.subcommand_completion is None and self.subcommand_callback:
            compositor = self.compositor_ref if hasattr(self, 'compositor_ref') else None
            menu = SelectionMenu(
                compositor=compositor,
                frame_color=self.content_color,
                content_color=self.content_color
            )
            self.subcommand_completion = SubcommandCompletion(
                menu, self.subcommand_callback,
                self.subcommand_descriptions_callback)
    
    def _ensure_context_menu(self):
        """Lazy-create context menu and completion system on first use."""
        if self.context_completion is None and self.context_items_callback:
            compositor = self.compositor_ref if hasattr(self, 'compositor_ref') else None
            menu = SelectionMenu(
                compositor=compositor,
                frame_color=self.content_color,
                content_color=self.content_color
            )
            self.context_completion = ContextCompletion(
                menu, self.context_items_callback, trigger="@")
    
    def _ensure_argument_menu(self):
        """Lazy-create argument completion menu for generic Param-driven completion."""
        if self.argument_completion is None and self._command_registry:
            compositor = self.compositor_ref if hasattr(self, 'compositor_ref') else None
            menu = SelectionMenu(
                compositor=compositor,
                frame_color=self.content_color,
                content_color=self.content_color
            )
            self.argument_completion = ArgumentCompletion(menu, self._command_registry)

    # def setup_menus(self, commands: List[str], get_context_items: Optional[Callable[[], List[str]]] = None,
                    # get_subcommands: Optional[Callable[[str], List[str]]] = None):
        # """Initialize menu data sources."""
        # self.command_list = commands
        # self.get_context_items_callback = get_context_items
        # self.get_subcommands_callback = get_subcommands
    
    def set_compositor(self, compositor):
        """Set compositor reference for overlay rendering."""
        self.compositor_ref = compositor

    def refresh_theme(self) -> None:
        """Re-apply the active theme to the (cached) completion menus."""
        self.bg = theme.get_bg()
        for completion in (
            self.command_completion,
            self.subcommand_completion,
            self.argument_completion,
            self.context_completion,
        ):
            if completion is not None:
                completion.refresh_theme()
    
    def _on_text_changed(self):
        """Called whenever text changes - updates completion menus and marks parent for re-render."""
        # Mark parent Box as changed for visual update
        if hasattr(self, 'parent') and self.parent and hasattr(self.parent, 'mark_changed'):
            self.parent.mark_changed()
        self._notify_changed()
        if self._run_picker_command():
            return

        providers = self._completion_providers()
        for provider in providers:
            provider.update(self.buffer.text, self.buffer.cursor_pos)
            if provider.is_active:
                # Higher-priority menu owns the screen; dismiss the rest.
                for other in providers:
                    if other is not provider:
                        other.hide()
                self._position_menu(provider)
                return

    def _run_picker_command(self) -> bool:
        """``/model `` (a picker command, then a space) submits ``/model``,
        opening its picker instead of an inline argument menu."""
        text = self.buffer.text
        if not (text.startswith('/') and text.endswith(' ') and self.on_submit):
            return False
        command = (self._command_registry or {}).get(text[1:-1])
        if not getattr(command, "picker", False):
            return False
        for provider in self._completion_providers():
            provider.hide()
        self.buffer.clear()
        self.scroll_manager.reset()
        self.on_submit(text.rstrip())
        return True

    def _completion_providers(self):
        """Return the configured completion providers in priority order."""
        providers = []
        if self.command_list:
            self._ensure_command_menu()
            providers.append(self.command_completion)
        if self.subcommand_callback:
            self._ensure_subcommand_menu()
            providers.append(self.subcommand_completion)
        if self._command_registry:
            self._ensure_argument_menu()
            providers.append(self.argument_completion)
        if self.context_items_callback:
            self._ensure_context_menu()
            providers.append(self.context_completion)
        return providers

    def _position_menu(self, completion):
        """Anchor the provider's menu at its trigger position."""
        if not completion or not completion.is_active:
            return
        trigger_pos = completion.trigger_pos(self.buffer.text, self.buffer.cursor_pos)
        if trigger_pos is not None:
            self._position_menu_at(completion.menu, trigger_pos)

    def _notify_changed(self):
        if self.on_change is not None:
            self.on_change()

    def _position_menu_at(self, menu, trigger_pos: int):
        """Position menu at specific text position (shared logic)."""
        # Calculate screen position
        _, trigger_col = self.coord_mapper.get_cursor_coords(
            self.buffer.text, trigger_pos
        )

        visible_count = min(len(menu.items), menu.max_height - 2)
        menu_height = visible_count + 2

        # Horizontally at the trigger; vertically above the whole input box
        # (not the trigger's line), so a multiline input is never covered.
        trigger_x = self.x + trigger_col - 2
        trigger_y = self.y
        compositor = getattr(self, "compositor_ref", None)
        screen_width = getattr(compositor, "width", self.x + self.width)
        screen_height = getattr(compositor, "height", self.y + self.height)

        if getattr(menu, "fill_width", False):
            # File picker: span the full screen width (simpler, shows long paths).
            menu_x = 0
            menu_width = screen_width
        else:
            menu_width = min(max(self.width, 20), screen_width)
            menu_x = max(0, min(trigger_x, screen_width - menu_width))

        space_above = max(0, trigger_y)
        space_below = max(0, screen_height - trigger_y - 1)
        if menu_height <= space_above or space_above >= space_below:
            menu_y = trigger_y - menu_height
            menu_height = min(menu_height, space_above)
        else:
            menu_y = trigger_y + 1
            menu_height = min(menu_height, space_below)
        
        menu.set_layout(menu_x, menu_y, menu_width, menu_height)

    def place_menu_above_input(self, menu) -> None:
        """Anchor a floating menu directly above the input box.

        Reuses the completion-menu positioning so standalone pickers (e.g.
        ``/model``) sit exactly where the ``/`` and ``@`` menus do.
        """
        self._position_menu_at(menu, 0)

    def set_layout(self, x: int, y: int, width: int, height: int):
        """Update layout and notify coordinate mapper of width change."""
        super().set_layout(x, y, width, height)
        self.coord_mapper.update_dimensions(width)

    def get_preferred_height(self, width: int) -> int:
        """Calculate height needed for wrapped text."""
        # Temporarily update dimensions for calculation
        old_width = self.coord_mapper.width
        self.coord_mapper.update_dimensions(width)
        lines = self.coord_mapper.get_wrapped_lines(self.buffer.text)
        self.coord_mapper.update_dimensions(old_width)
        
        # Apply maximum height constraint from config
        height = len(lines) if lines else 1
        if self.config and hasattr(self.config, 'ui_max_input_height'):
            height = min(height, self.config.ui_max_input_height)
        return height
    
    def is_cursor_on_first_line(self) -> bool:
        """Check if the cursor is on the first line of text (regardless of scroll position).
        
        Returns True only if the cursor is on row 0 of the actual text content,
        not just the first visible line if the input is scrolled.
        """
        cursor_row, _ = self.coord_mapper.get_cursor_coords(
            self.buffer.text, self.buffer.cursor_pos
        )
        return cursor_row == 0

    def set_content_color_provider(self, provider: Optional[callable]):
        """Set a dynamic content-color provider.

        The callable is invoked on every render (no args) and its return value
        overrides ``content_color`` — used to tint the typed text by input mode
        (e.g. message vs command) without re-theming the surrounding bars.
        """
        self._content_color_provider = provider

    def set_focused(self, focused: bool):
        """Set the focused state of this input component."""
        self.focused = focused
        # When gaining focus, make cursor immediately visible
        if focused:
            self.cursor_renderer.mark_input()
            # Mark parent for re-render to show cursor
            if hasattr(self, 'parent') and self.parent and hasattr(self.parent, 'mark_changed'):
                self.parent.mark_changed()

    def render(self, buffer: Buffer):
        """Render the input field with prompt, text, and scrolling (cursor rendered separately)."""
        # Clear background
        buffer.fill(self.x, self.y, self.width, self.height, " ", bg=self.bg)

        # Resolve dynamic content color (mode-aware text tint) if provided.
        content_color = self.content_color
        if self._content_color_provider is not None:
            provider_color = self._content_color_provider()
            if provider_color is not None:
                content_color = provider_color
        
        # Get wrapped lines
        lines = self.coord_mapper.get_wrapped_lines(self.buffer.text)
        prompt_width = display_width(self.prompt)
        
        # Ensure cursor is visible and scroll is constrained
        self.scroll_manager.ensure_cursor_visible()
        
        # Render visible lines
        scroll_y = self.scroll_manager.scroll_y
        
        # Safety check: ensure scroll_y is in bounds
        if scroll_y >= len(lines):
            scroll_y = max(0, len(lines) - 1)
            self.scroll_manager.scroll_y = scroll_y
        
        for i in range(scroll_y, min(len(lines), scroll_y + self.height)):
            line_idx = i - scroll_y
            line = lines[i]
            
            # Only add prompt to the very first line of content (when not scrolled past it)
            if i == 0:
                # First line of text - add prompt
                display_line = self.prompt + line
            else:
                # Continuation lines already have proper padding
                display_line = line
            
            buffer.write_str(self.x, self.y + line_idx, display_line, fg=content_color, bg=self.bg, max_width=self.width)
            if self.highlighter is not None and self.focused:
                paint_spans(buffer, self.x, self.y + line_idx, display_line,
                            self.highlighter, self.bg, self.width)
        
        # Note: Cursor is rendered separately via render_cursor() called after SubBuffer blit
        
        # Render parameter hints in grey after the end of typed text (not cursor)
        if self.focused and not self.has_active_completion():
            hint = self._get_parameter_hint()
            if hint:
                # Position hint at the END of the text, not at the cursor
                text_end_row, text_end_col = self.coord_mapper.get_cursor_coords(
                    self.buffer.text, len(self.buffer.text)
                )
                screen_row = text_end_row - scroll_y
                
                # Only show hint if the end of text is visible
                if 0 <= screen_row < self.height:
                    hint_x = self.x + text_end_col
                    hint_y = self.y + screen_row
                    
                    # Show hint in muted color (limited to available width)
                    hint_max_width = max(0, self.width - text_end_col)
                    buffer.write_str(hint_x, hint_y, hint, fg=theme.MUTED, bg=self.bg, max_width=hint_max_width)
        
        # Menu is rendered by compositor as overlay (not here)
    
    def _get_parameter_hint(self) -> str:
        """Get parameter hint text to show in grey after cursor.
        
        Uses the Command.params schema for generic, data-driven hints.
        """
        text = self.buffer.text.lstrip()
        
        # Only show hints for commands
        if not text.startswith('/'):
            return ""
        
        # Parse the command structure
        parts = text.split()
        if not parts:
            return ""
        
        cmd_name = parts[0][1:]  # strip '/'
        if cmd_name not in self._command_registry:
            return ""
        
        root_cmd = self._command_registry[cmd_name]
        
        # Walk subcommands to find the deepest resolved command
        cmd, offset = root_cmd.resolve_command(parts[1:])
        
        # If the resolved command is a non-leaf (has subcommands), hint the subcommand list
        if cmd.has_subcommands():
            # We're still on the subcommand position — no param hint
            return ""
        
        if not cmd.params:
            return ""
        
        # Calculate which arg the cursor is on
        args_start = 1 + offset
        if len(parts) <= args_start:
            # No args typed yet — hint the first param
            if text.endswith(' '):
                arg_index = 0
            else:
                # Still typing the subcommand name
                return ""
        else:
            # Count args after the subcommand path
            after_parts = parts[args_start:]
            if text.endswith(' '):
                # Trailing space: cursor is between args, hint from the next arg
                arg_index = len(after_parts)
            else:
                # No trailing space: user is mid-arg — skip the arg being typed
                arg_index = len(after_parts)
        
        # Collect hint for arg_index and beyond
        hints = []
        for i in range(arg_index, len(cmd.params)):
            p = cmd.params[i]
            name = p.name if p.required else f"[{p.name}]"
            hints.append(name)
        
        if hints:
            return " " + " ".join(hints)
        return ""
    
    def tick_cursor(self):
        """Advance the cursor blink phase and request a redraw when focused.

        Called on idle ticks so the cursor keeps blinking even when the
        content hasn't changed. Returns True if a redraw is needed.
        """
        if not self.focused:
            return False
        return self.cursor_renderer.advance(parent_box=self.parent)

    def render_cursor(self, buffer: Buffer):
        """Render cursor as an overlay (called outside SubBuffer caching).
        
        This is called by Box after blitting SubBuffer to main buffer,
        ensuring cursor blinks even when content hasn't changed.
        """
        if not self.focused:
            return
        
        # Get cursor position
        cursor_row, cursor_col = self.coord_mapper.get_cursor_coords(
            self.buffer.text, self.buffer.cursor_pos
        )
        
        scroll_y = self.scroll_manager.scroll_y
        
        # Render cursor and let it mark parent if it blinked
        self.cursor_renderer.render(
            buffer, cursor_row, cursor_col,
            self.x, self.y, scroll_y, self.height,
            parent_box=self.parent
        )

    def _accept_completion(self, completion) -> bool:
        """Commit the highlighted candidate, then close the menu like ESC.

        No space is inserted: the cursor sits right after the completed word
        and the menu stays closed for it (the next word, after a space the
        user types, opens menus as usual). A directory picked from the ``@``
        menu is the exception: the menu stays open to keep drilling into it.
        """
        result = completion.accept_selection(self.buffer.text, self.buffer.cursor_pos)
        if not result:
            return False

        new_text, new_cursor_pos = result
        selected = completion.menu.get_selected() or ""
        is_dir = selected.endswith('/')

        self.buffer.text = new_text
        self.buffer.cursor_pos = new_cursor_pos
        if not is_dir:
            completion.cancel(self.buffer.text, self.buffer.cursor_pos)
        self._on_text_changed()
        return True

    def has_active_completion(self) -> bool:
        """Return True when any completion menu is currently active."""
        return any(
            completion and completion.is_active
            for completion in (
                self.command_completion,
                self.subcommand_completion,
                self.argument_completion,
                self.context_completion,
            )
        )


    def handle_input(self, event: Any) -> bool:
        """Handle input events by delegating to appropriate handlers."""
        # Idle ticks drive the cursor blink. Handle BEFORE the generic path so
        # we don't reset the cursor pulse (mark_input) on every tick, which
        # would keep the cursor permanently solid. Return True on a blink flip
        # so the compositor requests a redraw exactly when needed.
        if isinstance(event, TickEvent):
            return self.tick_cursor()

        key = event.key if isinstance(event, KeyEvent) else event
        # Ignore keyboard input if not focused (but allow mouse events for potential focus change)
        if not self.focused and not isinstance(event, MouseEvent):
            return False
        
        # Mark input for cursor animation
        self.cursor_renderer.mark_input()
        
        # Determine which completion is active (generic iteration)
        active_completion = None
        for completion in [
            self.command_completion,
            self.subcommand_completion,
            self.argument_completion,
            self.context_completion,
        ]:
            if completion and completion.is_active:
                active_completion = completion
                break
        
        # Handle ESC - cancel menu and remember the word
        if key == '\x1b':  # ESC key
            if active_completion:
                active_completion.cancel(self.buffer.text, self.buffer.cursor_pos)
                return True
            return False  # Let other handlers try if menu not active
        
        # Handle arrow keys for menu navigation (priority)
        if active_completion:
            if key == '\x1b[A':  # Up arrow
                active_completion.navigate_up()
                return True
            elif key == '\x1b[B':  # Down arrow
                active_completion.navigate_down()
                return True
        
        # Handle Tab - accept the selection (no space, menu closed)
        if key == '\t':
            if active_completion and self._accept_completion(active_completion):
                return True
            return False  # Let other handlers try
        
        # Handle Enter - accept selection + submit
        if key in ('\r', '\n'):
            # Try completion first if menu is visible
            if active_completion:
                self._accept_completion(active_completion)
            

        
        # Handle mouse events (check bounds)
        if isinstance(event, MouseEvent):
            # Only handle clicks within this component's bounds
            if not (self.x <= event.x < self.x + self.width and
                    self.y <= event.y < self.y + self.height):
                return False
        
        # Create context for handlers
        context = InputContext(
            self.buffer,
            self.coord_mapper,
            self.scroll_manager,
        )
        
        # Try each handler
        for handler in self.input_handlers:
            if handler.can_handle(event) and handler.handle(event, context):
                # Update menu visibility after text changes
                self._on_text_changed()
                return True
        
        return False

    def update(self, text: str):
        """Update the input field text programmatically."""
        self.buffer.text = text
        self.buffer.cursor_pos = len(text)
        self._on_text_changed()

    def clear(self):
        """Clear the input field."""
        self.buffer.clear()
        self.scroll_manager.reset()
        self._on_text_changed()
