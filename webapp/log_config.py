"""
Centralised logging configuration for pd_reloaded.

Log levels
──────────
  TRACE (5)  – every library-level message (urllib3 requests, nicegui internals)
  DEBUG (10) – application debug output (webapp.*, releases.*, scraper.*, etc.)
  INFO  (20) – normal operational messages

Settings mapping (stored in DB as "Log level"):
  "info"   → root=INFO
  "debug"  → root=DEBUG, noisy libs forced to WARNING
  "trace"  → root=TRACE (everything)

The module also manages an *in-memory ring buffer* that backs both the
``/debug/logs`` web view and the optional rotating log file.
"""

import logging
import logging.handlers
import os
import sys
from collections import deque
from threading import Lock

# ── Custom TRACE level ──────────────────────────────────────────────
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(self, message, *args, **kwargs):
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, *args, **kwargs)


logging.Logger.trace = _trace

# ── Noisy loggers that should only appear at TRACE ──────────────────
NOISY_LOGGERS = [
    "urllib3",
    "urllib3.connectionpool",
    "nicegui",
    "httpx",
    "httpcore",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "asyncio",
    "watchfiles",
    "legacy",          # old ui_print messages (plex watchlist polling etc.)
]

# ── Unified format ──────────────────────────────────────────────────
LOG_FORMAT = "[%(asctime)s] [%(levelname)-5s] %(name)s: %(message)s"
LOG_DATEFMT = "%d/%m/%y %H:%M:%S"

# ── In-memory ring buffer (used by /debug/logs page) ───────────────
_MAX_BUFFER = 2000
_log_buffer: deque = deque(maxlen=_MAX_BUFFER)
_buffer_lock = Lock()


class _BufferHandler(logging.Handler):
    """Pushes formatted records into the shared ring buffer."""

    def emit(self, record):
        try:
            msg = self.format(record)
            with _buffer_lock:
                _log_buffer.append(msg)
        except Exception:
            self.handleError(record)


def get_log_lines(last_n: int = 500) -> list[str]:
    """Return the most recent *last_n* formatted log lines."""
    with _buffer_lock:
        items = list(_log_buffer)
    return items[-last_n:]


# ── State ───────────────────────────────────────────────────────────
_file_handler: logging.Handler | None = None
_buffer_handler: _BufferHandler | None = None


def setup_logging(log_level: str = "info",
                  log_to_file: bool = False,
                  config_dir: str = "."):
    """
    (Re-)configure the root logger.

    *log_level*  – ``"info"`` | ``"debug"`` | ``"trace"``
    *log_to_file* – whether to write a rotating log file
    *config_dir* – directory where ``pd_reloaded.log`` will be created
    """
    global _file_handler, _buffer_handler

    root = logging.getLogger()
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    # ── Console handler (stdout) — create once ──────────────────────
    # Remove any old basicConfig handlers
    for h in root.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # ── Buffer handler (in-memory ring) — create once ───────────────
    if _buffer_handler is None:
        _buffer_handler = _BufferHandler()
        _buffer_handler.setFormatter(formatter)
    else:
        _buffer_handler.setFormatter(formatter)
    if _buffer_handler not in root.handlers:
        root.addHandler(_buffer_handler)

    # ── File handler ────────────────────────────────────────────────
    if log_to_file:
        log_path = os.path.join(config_dir, "pd_reloaded.log")
        if _file_handler is None or not isinstance(_file_handler, logging.handlers.RotatingFileHandler):
            # Remove old one if type changed
            if _file_handler is not None:
                root.removeHandler(_file_handler)
            _file_handler = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
            )
            _file_handler.setFormatter(formatter)
            root.addHandler(_file_handler)
        else:
            # Already exists; just make sure format is current
            _file_handler.setFormatter(formatter)
            if _file_handler not in root.handlers:
                root.addHandler(_file_handler)
    else:
        # Disable file logging
        if _file_handler is not None:
            root.removeHandler(_file_handler)
            _file_handler.close()
            _file_handler = None

    # ── Level management ────────────────────────────────────────────
    _apply_level(log_level)


def _apply_level(log_level: str):
    """Set root + noisy logger levels according to the chosen preset."""
    root = logging.getLogger()

    if log_level == "trace":
        root.setLevel(TRACE)
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(TRACE)
    elif log_level == "debug":
        root.setLevel(logging.DEBUG)
        # Suppress noisy third-party libs to WARNING so only app code is visible
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
    else:  # "info"
        root.setLevel(logging.INFO)
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)


def reconfigure(log_level: str = "info",
                log_to_file: bool = False,
                config_dir: str = "."):
    """Hot-reload logging settings at runtime (e.g. from settings page)."""
    setup_logging(log_level=log_level, log_to_file=log_to_file, config_dir=config_dir)
