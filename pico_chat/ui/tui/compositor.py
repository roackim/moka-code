import sys
import time
import asyncio
from typing import Optional, Dict, Any
from collections import deque
from pico_chat import pico_cfg
from pico_chat.ui.tui.terminal import ANSI, Terminal
from pico_chat.ui.tui.events import KeyEvent, MouseEvent
from pico_chat.ui.tui.buffer import Buffer
from pico_chat.ui.tui.components import Component
from pico_chat.ui.tui.router import EventRouter
from pico_chat.ui.tui.events import ResizeEvent, TickEvent

class Compositor:
    def __init__(self, root: Component, fps: int = 30, shutdown_event: Optional[Any] = None):
        self.root = root
        self.fps = fps
        self.terminal = Terminal()
        self.width, self.height = 0, 0
        self.buffer = Buffer(0, 0)
        self.components_by_id: Dict[str, Component] = {}
        self._collect_ids(self.root)
        self.event_router = EventRouter(self.root)
        self.running = False
        self.shutdown_event = shutdown_event
        self.padding = 0  # Global app padding
        self.overlays = []  # Floating components rendered on top
        self._render_requested = True
        self._full_redraw = True
        self.idle_sleep_seconds = 0.0
        self._wake_event = asyncio.Event()
        self._frame_callbacks: list = []
        # While a child program owns the terminal ($EDITOR, a shell) pico
        # must neither read input nor draw; see ``pause``.
        self.paused = False

        # Scroll coalescing: touchpads emit a high-frequency burst of tiny wheel
        # deltas. We accumulate them and dispatch a single event per frame so we
        # don't rebuild the (expensive) line map once per event.
        self._scroll_accumulator = 0
        self._scroll_x = 0
        self._scroll_y = 0
        self._scroll_alt = False
        self._scroll_event_count = 0  # raw wheel events seen this frame (burst detection)
        
        # Performance tracking - use rolling window for accurate current FPS
        self.render_times = deque(maxlen=10)  # Track last 10 render times for recent FPS

    def _collect_ids(self, component: Component):
        if component.id:
            self.components_by_id[component.id] = component
        # If it's a container, we'll need to recurse. 
        # For now, let's assume components might have children.
        if hasattr(component, 'children'):
            for child in component.children:
                self._collect_ids(child)

    def get_component(self, id: str) -> Optional[Component]:
        return self.components_by_id.get(id)

    def set_root(self, root: Component) -> None:
        """Replace the rendered root while preserving app-level routing policy."""
        self.root = root
        self.components_by_id = {}
        self._collect_ids(root)
        self.event_router.root = root
        self._full_redraw = True
        self.request_render()

    
    def add_overlay(self, component: Component):
        """Register a component as a floating overlay.
        
        Overlays are rendered on top of the main component tree and are not
        clipped by parent bounds. Useful for menus, modals, tooltips, etc.
        """
        if component not in self.overlays:
            self.overlays.append(component)
            self.event_router.add_overlay(component)
            self._full_redraw = True
            self.request_render()
    
    def remove_overlay(self, component: Component):
        """Unregister a component from overlays."""
        if component in self.overlays:
            self.overlays.remove(component)
            self.event_router.remove_overlay(component)
            self._full_redraw = True
            self.request_render()

    def pause(self) -> None:
        """Stay off the terminal (no input reads, no drawing) until ``resume``."""
        self.paused = True

    def resume(self) -> None:
        """Take the terminal back and repaint everything that changed meanwhile."""
        self.paused = False
        self._update_size()
        self._full_redraw = True
        self.request_render()
        self._wake_event.set()

    def request_render(self):
        """Request a repaint on the next loop iteration."""
        self._render_requested = True
        self._wake_event.set()

    def request_full_redraw(self):
        """Discard cached frame content and repaint every component.

        Use after a global visual change (e.g. a theme switch) so components
        that cache their painted cells are re-rendered with the new colors.
        """
        self._full_redraw = True
        self.request_render()

    def add_frame_callback(self, cb):
        """Register a per-frame callback invoked with the current time.

        The callback returns ``True`` when it produced work and a repaint is
        needed. Callbacks may unregister themselves (or others) from within the
        callback; iteration runs over a copy.
        """
        if cb not in self._frame_callbacks:
            self._frame_callbacks.append(cb)

    def remove_frame_callback(self, cb):
        """Unregister a callback previously passed to ``add_frame_callback``."""
        try:
            self._frame_callbacks.remove(cb)
        except ValueError:
            pass

    def _run_frame_callbacks(self, now: float) -> bool:
        """Invoke every registered frame callback; return True if any had work."""
        work = False
        for cb in list(self._frame_callbacks):
            if cb(now):
                work = True
        if work:
            self.request_render()
        return work

    def _handle_shutdown_key(self, event) -> bool:
        key = event.key if isinstance(event, KeyEvent) else event
        if key != '\x03':
            return False
        self.running = False
        if self.shutdown_event:
            self.shutdown_event.set()
        return True

    async def run(self):
        self.running = True
        with self.terminal:
            self._update_size()

            # Track absolute target time for next frame (prevents drift)
            current_fps = self.fps
            frame_time = (1.0 / current_fps) if current_fps > 0 else 0.0
            now = time.perf_counter()
            next_frame_time = now

            while self.running:
                if self.paused:
                    await asyncio.sleep(0.05)
                    next_frame_time = time.perf_counter()
                    continue
                # Pick up runtime FPS changes immediately
                new_fps = self.fps
                if new_fps != current_fps:
                    current_fps = new_fps
                    frame_time = (1.0 / current_fps) if current_fps > 0 else 0.0
                    next_frame_time = time.perf_counter()

                if self.terminal.resized:
                    # Debounce resize events
                    await asyncio.sleep(0.05)
                    self.terminal.resized = False
                    self._update_size()
                    self.event_router.dispatch(ResizeEvent(self.width, self.height))
                    self._full_redraw = True
                    self.request_render()
                    # Reset frame timing after resize
                    next_frame_time = time.perf_counter()

                # Handle input first (non-blocking). Drain ALL pending events in
                # a tight loop so a touchpad burst (many tiny wheel deltas) is
                # accumulated into a single scroll before we flush it once.
                # Without draining, each event would be flushed on its own
                # iteration, defeating the coalescing entirely.
                self._scroll_event_count = 0
                self._scroll_alt = False
                while True:
                    event = self.terminal.get_input()
                    if event is None:
                        break
                    if self._handle_shutdown_key(event):
                        break
                    # Coalesce wheel scroll events: accumulate the delta and
                    # dispatch a single event once per frame. This prevents a
                    # touchpad burst (many tiny deltas) from triggering an
                    # expensive full line-map rebuild for every single event.
                    if isinstance(event, MouseEvent) and event.button in (64, 65):
                        sign = 1 if event.button == 65 else -1
                        self._scroll_accumulator += sign * event.scroll_delta
                        self._scroll_x = event.x
                        self._scroll_y = event.y
                        self._scroll_alt = self._scroll_alt or event.alt
                        self._scroll_event_count += 1
                        self.request_render()
                    else:
                        self.event_router.dispatch(event)
                        self.request_render()

                # Flush any accumulated scroll once per frame, before rendering.
                # Runs every iteration (even with no new event) so the final
                # burst is always dispatched.
                if self._scroll_accumulator:
                    delta = self._scroll_accumulator
                    direction = 1 if delta > 0 else -1
                    self._scroll_accumulator = 0
                    # Apply speed multipliers:
                    #  - Alt held → faster (ui_scroll_alt_multiplier)
                    #  - Touchpad burst (many events in one frame) → slower
                    #    (ui_scroll_touchpad_speed), so touchpad feels gentler
                    #    than a discrete mouse wheel.
                    speed = 1.0
                    if self._scroll_alt:
                        speed *= pico_cfg.config.ui_scroll_alt_multiplier
                    if self._scroll_event_count >= pico_cfg.config.ui_scroll_touchpad_event_threshold:
                        speed *= pico_cfg.config.ui_scroll_touchpad_speed
                    delta = int(round(abs(delta) * speed))
                    if delta == 0:
                        delta = 1
                    scroll_event = MouseEvent(
                        self._scroll_x, self._scroll_y,
                        65 if direction > 0 else 64,
                        True, False, delta,
                    )
                    self.event_router.dispatch(scroll_event)
                    self.request_render()

                if self.event_router.dispatch(TickEvent(time.perf_counter())):
                    self.request_render()

                self._run_frame_callbacks(time.perf_counter())

                has_dirty = self.root.is_dirty() if hasattr(self.root, 'is_dirty') else True
                should_render = self._render_requested or has_dirty

                if not should_render:
                    idle_timeout = (1.0 / current_fps) if current_fps > 0 else (self.idle_sleep_seconds or 0.016)
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=idle_timeout)
                        self._wake_event.clear()
                    except asyncio.TimeoutError:
                        pass
                    continue
                
                # Render once per scheduled frame
                self.render()

                # Uncapped mode (fps <= 0): render as fast as possible while yielding.
                if current_fps <= 0:
                    await asyncio.sleep(0)
                else:
                    # Advance schedule in absolute time (no cumulative drift)
                    next_frame_time += frame_time
                    now = time.perf_counter()
                    sleep_time = next_frame_time - now

                    if sleep_time > 0:
                        await asyncio.sleep(sleep_time)
                    else:
                        # Behind schedule: skip ahead to avoid sustained lag
                        frames_behind = int((-sleep_time) / frame_time) + 1
                        next_frame_time += frames_behind * frame_time
                        # Always yield so other tasks don't starve at very high target FPS
                        await asyncio.sleep(0)

    def _update_size(self):
        w, h = self.terminal.get_size()
        self.width, self.height = w, h
        self.buffer = Buffer(w, h)

    def render(self):
        """Render the compositor."""
        if self.paused or self.width == 0 or self.height == 0:
            return

        now = time.perf_counter()

        # Track this render time for FPS calculation
        self.render_times.append(now)

        # Apply global padding to root component layout
        pad = self.padding
        inner_width = max(0, self.width - 2 * pad)
        inner_height = max(0, self.height - 2 * pad)
        self.root.set_layout(pad, pad, inner_width, inner_height)
        if hasattr(self.root, 'layout'):
            self.root.layout()

        if self._full_redraw:
            self.buffer.clear()
            self.root.render(self.buffer)
            for overlay in self.overlays:
                overlay.render(self.buffer)
        else:
            dirty_rects: list[tuple[int, int, int, int]] = []
            if hasattr(self.root, 'collect_dirty_rects'):
                self.root.collect_dirty_rects(dirty_rects)

            valid_dirty_rects = [
                (x, y, width, height)
                for x, y, width, height in dirty_rects
                if width > 0 and height > 0
            ]
            # Dirty rects propagate up through every ancestor, so the same
            # rectangle is collected many times. Deduplicate to avoid repainting
            # it once per ancestor.
            seen = set()
            deduped = []
            for rect in valid_dirty_rects:
                if rect not in seen:
                    seen.add(rect)
                    deduped.append(rect)
            valid_dirty_rects = deduped

            # If a repaint was explicitly requested but no valid dirty rects exist
            # (e.g. before first stable layout), fall back to a full redraw.
            if self._render_requested and not valid_dirty_rects:
                self.buffer.clear()
                self.root.render(self.buffer)
                for overlay in self.overlays:
                    overlay.render(self.buffer)
            elif not valid_dirty_rects:
                self._render_requested = False
                return
            else:
                for x, y, width, height in valid_dirty_rects:
                    self.buffer.clear_rect(x, y, width, height)
                    self.buffer.set_clip(x, y, width, height)
                    self.root.render(self.buffer)
                    for overlay in self.overlays:
                        overlay.render(self.buffer)
                    self.buffer.clear_clip()
        
        # Use Buffer's built-in render method. Wrap the frame in synchronized
        # output so supporting terminals present it atomically.
        output = ANSI.SYNC_START + self.buffer.render() + ANSI.SYNC_END
        sys.stdout.write(output)
        sys.stdout.flush()

        self._render_requested = False
        self._full_redraw = False
        if hasattr(self.root, 'clear_dirty'):
            self.root.clear_dirty()
    
