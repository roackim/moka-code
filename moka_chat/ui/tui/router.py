"""Event routing for the TUI component tree."""

from typing import Any, Callable, Optional

from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.actions import Action, ActionMap
from moka_chat.ui.tui.events import MouseEvent
from moka_chat.ui.tui.focus import FocusScope


class EventRouter:
    """Route input through overlays and the component tree.

    Overlays are checked from newest to oldest. Mouse events use the layout
    rectangles to build a child-to-parent path, allowing handled events to
    bubble without requiring every container to repeat hit-testing logic.
    """

    def __init__(self, root: Component, interceptor: Optional[Callable[[Any], bool]] = None,
                 focus_scope: Optional[FocusScope] = None,
                 action_map: Optional[ActionMap] = None):
        self.root = root
        self.interceptor = interceptor
        self.focus_scope = focus_scope
        self.action_map = action_map
        self.overlays: list[Component] = []

    def set_interceptor(self, interceptor: Optional[Callable[[Any], bool]]) -> None:
        """Set an optional application policy layer before widget routing."""
        self.interceptor = interceptor

    def set_focus_scope(self, focus_scope: Optional[FocusScope]) -> None:
        """Set the focus scope used for keyboard target dispatch."""
        self.focus_scope = focus_scope

    def set_action_map(self, action_map: Optional[ActionMap]) -> None:
        self.action_map = action_map

    def bind_action(self, name: str, handler: Callable[[Action], bool]) -> None:
        if self.action_map is None:
            self.action_map = ActionMap()
        self.action_map.bind(name, handler)

    def add_overlay(self, component: Component) -> None:
        if component not in self.overlays:
            self.overlays.append(component)

    def remove_overlay(self, component: Component) -> None:
        if component in self.overlays:
            self.overlays.remove(component)

    def dispatch(self, event: Any) -> bool:
        for overlay in reversed(self.overlays):
            if overlay.handle_input(event):
                return True

        if self.interceptor is not None and self.interceptor(event):
            return True

        if isinstance(event, Action):
            if self.action_map is not None and self.action_map.dispatch(event):
                return True
            return self.root.handle_input(event)

        if isinstance(event, MouseEvent):
            path = self._hit_path(self.root, event.x, event.y)
            for component in reversed(path):
                if component.handle_input(event):
                    return True
            return False

        if self.focus_scope is not None and self.focus_scope.active:
            focused = self.focus_scope.focused
            if focused is not None and focused.handle_input(event):
                return True

        return self.root.handle_input(event)

    def _hit_path(self, component: Component, x: int, y: int) -> list[Component]:
        if not self._contains(component, x, y):
            return []

        path = [component]
        children = getattr(component, "children", ())
        for child in reversed(children):
            child_path = self._hit_path(child, x, y)
            if child_path:
                path.extend(child_path)
                break
        return path

    @staticmethod
    def _contains(component: Component, x: int, y: int) -> bool:
        return (
            component.x <= x < component.x + component.width
            and component.y <= y < component.y + component.height
        )