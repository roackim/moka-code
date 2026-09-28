import sys
import os
import tty
import termios
import shutil
import signal
import fcntl
import time
import select
from typing import Optional, Callable
from moka_code.ui.tui.events import KeyEvent, MouseEvent, PasteEvent, normalize_key

class ANSI:
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"
    ENABLE_MOUSE = "\033[?1000h\033[?1002h\033[?1015h\033[?1006h"
    DISABLE_MOUSE = "\033[?1006l\033[?1015l\033[?1002l\033[?1000l"
    # Bracketed paste, plus focus reporting (``\x1b[I`` / ``\x1b[O``).
    ENABLE_BRACKETED_PASTE = "\033[?2004h\033[?1004h"
    DISABLE_BRACKETED_PASTE = "\033[?1004l\033[?2004l"
    MOVE_HOME = "\033[H"
    # Alternate screen: moka's frames never reach the scrollback, and leaving
    # it restores the shell's screen as it was.
    ENTER_ALT_SCREEN = "\033[?1049h"
    EXIT_ALT_SCREEN = "\033[?1049l"
    RESET = "\033[0m"
    # Synchronized output (DEC private mode 2026): terminals that support it
    # present the frame atomically, removing tearing and perceived lag.
    # Terminals that don't support it ignore the sequence.
    SYNC_START = "\033[?2026h"
    SYNC_END = "\033[?2026l"

    @staticmethod
    def move_to(row: int, col: int) -> str:
        return f"\033[{row};{col}H"



class Terminal:
    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.old_settings = None
        self.resized = False

    def __enter__(self):
        self.old_settings = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        sys.stdout.write(ANSI.ENTER_ALT_SCREEN + ANSI.HIDE_CURSOR + ANSI.ENABLE_MOUSE
                         + ANSI.ENABLE_BRACKETED_PASTE)
        sys.stdout.flush()
        signal.signal(signal.SIGWINCH, self._handle_resize)
        return self

    def cleanup(self, clear_screen: bool = True):
        """Cleanup terminal state. Safe to call multiple times.
        
        Leaves the alternate screen, so the shell's screen comes back as it
        was. ``clear_screen=False`` (a crash) adds a newline so a traceback
        printed next starts on its own line.
        """
        # 1. Reset colors, show cursor, disable mouse/paste, leave the alt screen
        restore = (ANSI.RESET + ANSI.SHOW_CURSOR + ANSI.DISABLE_MOUSE
                   + ANSI.DISABLE_BRACKETED_PASTE + ANSI.EXIT_ALT_SCREEN)
        sys.stdout.write(restore if clear_screen else restore + "\n")
        sys.stdout.flush()
        
        # 2. Restore signal handlers
        try:
            signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        except Exception:
            pass
        
        # 3. Restore serial TTY settings (this effectively exits raw mode)
        if self.old_settings:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)
                self.old_settings = None  # Mark as cleaned up
            except Exception:
                pass
        
        # 4. Final flush
        sys.stdout.write(ANSI.SHOW_CURSOR)
        sys.stdout.flush()

    def __exit__(self, exc_type, _exc_val, _exc_tb):
        """Restore terminal state and ensure cursor visibility."""
        # Only clear screen if exiting normally (no exception)
        self.cleanup(clear_screen=(exc_type is None))

    def suspend(self):
        """Release the terminal to a child process (e.g. ``$EDITOR``).

        Restores cooked mode, the cursor/mouse/paste state and the normal
        screen (the child draws there, or on its own alternate screen), but
        keeps ``old_settings`` so :meth:`resume` (and later :meth:`cleanup`)
        still work.
        """
        if self.old_settings:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)
            except Exception:
                pass
        sys.stdout.write(ANSI.SHOW_CURSOR + ANSI.DISABLE_MOUSE + ANSI.DISABLE_BRACKETED_PASTE
                         + ANSI.RESET + ANSI.EXIT_ALT_SCREEN)
        sys.stdout.flush()

    def resume(self):
        """Re-enter raw mode after a :meth:`suspend`."""
        try:
            tty.setraw(self.fd)
        except Exception:
            pass
        sys.stdout.write(ANSI.ENTER_ALT_SCREEN + ANSI.HIDE_CURSOR + ANSI.ENABLE_MOUSE
                         + ANSI.ENABLE_BRACKETED_PASTE)
        sys.stdout.flush()

    def _handle_resize(self, _signum, _frame):
        self.resized = True

    def get_size(self) -> tuple[int, int]:
        size = shutil.get_terminal_size()
        return size.columns, size.lines

    def _read_char(self) -> Optional[str]:
        """Read one full UTF-8 character directly from the fd (non-blocking).

        Uses os.read() instead of sys.stdin.read() so that select() on the fd
        and the actual read are consistent. sys.stdin is a buffered
        TextIOWrapper: select() on the fd would miss data already sitting in
        sys.stdin's buffer, causing escape sequences to be split and leaked as
        literal text into the input field.
        """
        try:
            data = os.read(self.fd, 1)
        except (EOFError, OSError):
            return None
        if not data:
            return None
        b = data[0]
        if b < 0x80:
            return chr(b)
        # Multi-byte UTF-8: read the continuation bytes.
        if b < 0xE0:
            need = 1
        elif b < 0xF0:
            need = 2
        else:
            need = 3
        for _ in range(need):
            try:
                more = os.read(self.fd, 1)
            except (EOFError, OSError):
                break
            if not more:
                break
            data += more
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return data.decode('utf-8', errors='replace')

    def get_input(self) -> Optional[KeyEvent | MouseEvent | PasteEvent]:
        # Non-blocking read. Note: stdin/stdout usually share the same tty open
        # file description, so O_NONBLOCK must be restored before anything else
        # writes to stdout (a persistent flag breaks cleanup/suspend).
        flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        try:
            char = self._read_char()
            if char is None:
                return None
            
            if char == '\x1b':
                # Potential escape sequence. Read the rest of the sequence using
                # select() with a short timeout instead of blocking time.sleep().
                # This keeps the async event loop responsive during high-rate
                # input bursts (e.g. touchpad scrolling).
                seq = char
                start_time = time.monotonic()
                while True:
                    # If we already have a complete mouse sequence, parse it
                    # immediately without waiting for more bytes.
                    if seq.startswith('\x1b[<') and seq.endswith(('M', 'm')):
                        return self._parse_mouse(seq)
                    # If we have a complete bracketed-paste start, read the paste.
                    if seq == '\x1b[200~':
                        return self._read_bracketed_paste()
                    # If we have a complete known sequence, stop reading.
                    if seq.endswith(('m', 'M', 'A', 'B', 'C', 'D', 'H', 'F', '~')):
                        break
                    # Any other CSI final byte (Shift+Tab ``\x1b[Z``, focus
                    # ``\x1b[I``/``\x1b[O``); ``\x1b[[`` is a Linux-console prefix.
                    if (len(seq) > 2 and seq[1] == '[' and seq != '\x1b[['
                            and '@' <= seq[-1] <= '~'):
                        break
                    # If we've waited too long, treat what we have as a bare ESC.
                    if time.monotonic() - start_time > 0.05:
                        break
                    # Wait for more bytes with a short, non-blocking timeout.
                    r, _, _ = select.select([self.fd], [], [], 0.005)
                    if not r:
                        continue
                    c = self._read_char()
                    if c is None:
                        continue
                    seq += c
                    # Alt+Enter (\x1b\r or \x1b\n) is a complete sequence.
                    if seq in ('\x1b\r', '\x1b\n'):
                        break
                
                if seq.startswith('\x1b[<'):
                    return self._parse_mouse(seq)
                if seq in ('\x1b\r', '\x1b\n', '\x1b[13;3u', '\x1b[27;3;13~'):
                    return normalize_key('\x1b\r')
                return normalize_key(seq)
            return normalize_key(char)
        except:
            return None
        finally:
            fcntl.fcntl(self.fd, fcntl.F_SETFL, flags)

    def _read_bracketed_paste(self) -> PasteEvent:
        """Read paste content until end marker. Temporarily uses blocking mode."""
        # Switch to blocking mode for reliable paste reading
        flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        
        try:
            content = ""
            end_marker = '\x1b[201~'
            
            # Read character by character - simple and works for any size.
            # Use os.read() (blocking here) for consistency with get_input().
            while True:
                try:
                    data = os.read(self.fd, 1)
                except (EOFError, OSError):
                    break
                if not data:
                    break
                char = data.decode('utf-8', errors='replace')
                content += char
                
                # Check for end marker (only check last 6 chars for efficiency)
                if len(content) >= 6 and content[-6:] == end_marker:
                    return PasteEvent(content[:-6])
                
                # Safety limit
                if len(content) > 1_000_000:
                    return PasteEvent(content)
                    
        finally:
            # Restore non-blocking mode
            fcntl.fcntl(self.fd, fcntl.F_SETFL, flags)

    def _parse_mouse(self, seq: str) -> Optional[MouseEvent]:
        # \033[<Cb;Cx;CyM/m
        # Note: ANSI coordinates are 1-indexed, convert to 0-indexed
        try:
            pressed = seq[-1] == 'M'
            parts = seq[3:-1].split(';')
            button = int(parts[0])
            x = int(parts[1]) - 1  # Convert from 1-indexed to 0-indexed
            y = int(parts[2]) - 1  # Convert from 1-indexed to 0-indexed
            drag = (button & 32) != 0
            if drag:
                button -= 32
            # SGR modifier bits: 4=Shift, 8=Alt, 16=Ctrl. Extract Alt.
            alt = (button & 8) != 0
            button &= ~8  # strip Alt bit
            # SGR wheel events encode the notch count in the button code:
            #   64/65 = 1 notch, 66/67 = 2 notches, 68/69 = 3 notches.
            # Touchpads emit many small deltas; decoding the magnitude lets us
            # coalesce them accurately instead of treating each as a single notch.
            scroll_delta = 1
            if button in (66, 67, 68, 69):
                scroll_delta = (button - 64) // 2 + 1
                button = 64 + (button % 2)  # normalize to 64 (up) / 65 (down)
            return MouseEvent(x, y, button, pressed, drag, scroll_delta, alt)
        except:
            return None
