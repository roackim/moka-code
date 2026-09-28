"""Logging handlers for the moka TUI."""

import logging
from moka_code.ui.tui.colors import theme


class TuiLogHandler(logging.Handler):
    """Custom log handler that routes logs to the debug panel."""
    
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        # Only show logs from internal code, not external libraries
        self.allowed_prefixes = ('moka_code', 'harness', 'tui', '__main__')
        self.blocked_prefixes = ('httpcore', 'httpx', 'openai', 'urllib3', 'asyncio')
    
    def emit(self, record):
        try:
            # Filter out external library logs
            logger_name = record.name
            
            # Block known noisy libraries
            if any(logger_name.startswith(prefix) for prefix in self.blocked_prefixes):
                return
            
            # Allow internal loggers or warn/error from any source
            is_internal = any(logger_name.startswith(prefix) for prefix in self.allowed_prefixes)
            is_important = record.levelno >= logging.WARNING
            
            if is_internal or is_important:
                msg = self.format(record)
                self.panel.log(msg)
        except Exception:
            self.handleError(record)


class ColoredFormatter(logging.Formatter):
    """Formatter that adds color to timestamp."""
    
    def format(self, record):
        # Format the record normally
        result = super().format(record)
        # Split to separate timestamp from rest
        parts = result.split(' ', 1)
        if len(parts) == 2:
            # Add orange color to timestamp
            timestamp = parts[0]
            rest = parts[1]
            colored_timestamp = f"{theme.WARNING.ansi_fg()}{timestamp}\033[0m"
            return f"{colored_timestamp} {rest}"
        return result


def setup_tui_logging(debug_panel):
    """Configure logging to route to the debug panel.
    
    Args:
        debug_panel: The DebugLogPanel instance to log to
        
    Returns:
        The configured log handler
    """
    handler = TuiLogHandler(debug_panel)
    handler.setLevel(logging.DEBUG)
    formatter = ColoredFormatter('%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
    handler.setFormatter(formatter)
    
    # Configure root logger to accept all levels
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)
    
    # Test log message
    logging.info("[TUI] Debug panel initialized")
    
    return handler
