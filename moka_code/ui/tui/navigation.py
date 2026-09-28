"""Navigation and modal hosting for TUI screens."""

from typing import TYPE_CHECKING, Optional

from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.screen import Screen

if TYPE_CHECKING:
    from moka_code.ui.tui.compositor import Compositor


class Navigator:
    """Manage a stack of screens while preserving the current app policy."""

    def __init__(self, compositor: "Compositor", initial: Screen):
        self.compositor = compositor
        self._stack = [initial]
        self._install(initial)
        initial.on_enter()

    @property
    def current(self) -> Screen:
        return self._stack[-1]

    @property
    def stack_depth(self) -> int:
        return len(self._stack)

    def push(self, screen: Screen) -> None:
        self.current.on_suspend()
        self._stack.append(screen)
        self._install(screen)
        screen.on_enter()

    def pop(self) -> bool:
        if len(self._stack) == 1:
            return False
        leaving = self._stack.pop()
        leaving.on_leave()
        self._install(self.current)
        self.current.on_resume()
        return True

    def replace(self, screen: Screen) -> None:
        leaving = self._stack.pop()
        leaving.on_leave()
        self._stack.append(screen)
        self._install(screen)
        screen.on_enter()

    def back(self) -> bool:
        return self.pop()

    def _install(self, screen: Screen) -> None:
        self.compositor.set_root(screen.root)
        self.compositor.event_router.set_focus_scope(screen.focus_scope)
        self.compositor.event_router.set_action_map(screen.action_map)


class ModalHost:
    """Own the active modal component through the compositor overlay API."""

    def __init__(self, compositor: "Compositor"):
        self.compositor = compositor
        self._current: Optional[Component] = None
        self._current_screen: Optional[Screen] = None

    @property
    def current(self) -> Optional[Component]:
        return self._current

    def present(self, component: Component) -> None:
        if self._current is component:
            return
        if self._current is not None:
            self.dismiss(self._current)
        self._current = component
        self.compositor.add_overlay(component)

    def present_screen(self, screen: Screen) -> None:
        """Present a modal screen and run its enter lifecycle hook."""
        if self._current_screen is not None:
            self.dismiss_screen()
        elif self._current is not None:
            self.dismiss()
        self._current_screen = screen
        screen.on_enter()
        self.present(screen.root)

    def dismiss(self, component: Optional[Component] = None) -> None:
        target = component or self._current
        if target is None:
            return
        self.compositor.remove_overlay(target)
        if target is self._current:
            self._current = None

    def dismiss_screen(self, screen: Optional[Screen] = None) -> None:
        """Dismiss a modal screen and run its leave lifecycle hook."""
        target = screen or self._current_screen
        if target is None:
            return
        self.dismiss(target.root)
        if target is self._current_screen:
            self._current_screen = None
            target.on_leave()