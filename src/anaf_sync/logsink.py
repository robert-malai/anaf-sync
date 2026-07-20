"""Platform-native log sinks for scheduled runs.

Interactive runs keep the pretty console renderer; scheduled runs (no TTY)
log straight into the OS facility instead, so the native tools — Event
Viewer / ``Get-WinEvent``, ``log show``/``log stream``, ``journalctl`` — can
introspect them. Each sink is the platform's own API, no piping or capture
files in between:

- Windows: the Application event log via ``ReportEvent`` (pywin32).
- macOS: the unified logging system via ``os_log`` (pyoslog).
- Linux: journald via its native datagram socket (no dependency).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import struct
import sys
from enum import Enum

__all__ = [
    "IDENTIFIER",
    "JournalSocketHandler",
    "LogMode",
    "resolve_mode",
    "system_log_handler",
]

_MODE_ENV = "ANAF_SYNC_LOG"

IDENTIFIER = "anaf-sync"
"""Source name under which the OS log facilities record our messages."""

_MACOS_SUBSYSTEM = "ro.anaf-sync"  # matches the launchd label stem


class LogMode(Enum):
    """Where log events are rendered."""

    CONSOLE = "console"
    SYSTEM = "system"


def resolve_mode(*, interactive: bool | None = None) -> LogMode:
    """Pick the log sink: ``$ANAF_SYNC_LOG`` wins, else a TTY means console.

    Raises:
        ValueError: ``$ANAF_SYNC_LOG`` is set to something unrecognised.
    """
    if value := os.environ.get(_MODE_ENV):
        try:
            return LogMode(value.strip().lower())
        except ValueError:
            raise ValueError(
                f"{_MODE_ENV} must be 'console' or 'system', not {value!r}"
            ) from None
    if interactive is None:
        interactive = sys.stderr.isatty()
    return LogMode.CONSOLE if interactive else LogMode.SYSTEM


def system_log_handler() -> logging.Handler:
    """The native log handler for this platform."""
    if sys.platform == "win32":
        return _event_log_handler()
    if sys.platform == "darwin":
        import pyoslog  # type: ignore[import-untyped]

        handler: logging.Handler = pyoslog.Handler()
        handler.setSubsystem(_MACOS_SUBSYSTEM, "sync")  # type: ignore[attr-defined]
        return handler
    return JournalSocketHandler()


# -- Windows (Application event log) ----------------------------------------------


def _event_log_handler() -> logging.Handler:
    # NTEventLogHandler registers the message source in HKLM on construction,
    # which needs elevation the task will usually not have. Registration only
    # improves Event Viewer's rendering, so fall back to the raw handler that
    # reports events under the still-unregistered source name.
    try:
        return logging.handlers.NTEventLogHandler(IDENTIFIER)
    except Exception:
        return _RawEventLogHandler()


class _RawEventLogHandler(logging.Handler):
    """``ReportEvent`` without source registration.

    Event Viewer prefixes such entries with a "description not found" note,
    but the message text is intact and fully queryable.
    """

    def __init__(self) -> None:
        super().__init__()
        import win32evtlog
        import win32evtlogutil

        self._report = win32evtlogutil.ReportEvent
        self._error_type = win32evtlog.EVENTLOG_ERROR_TYPE
        self._warning_type = win32evtlog.EVENTLOG_WARNING_TYPE
        self._info_type = win32evtlog.EVENTLOG_INFORMATION_TYPE

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno >= logging.ERROR:
                event_type = self._error_type
            elif record.levelno >= logging.WARNING:
                event_type = self._warning_type
            else:
                event_type = self._info_type
            self._report(
                IDENTIFIER, 1, eventType=event_type, strings=[self.format(record)]
            )
        except Exception:
            self.handleError(record)


# -- Linux (journald) --------------------------------------------------------------

_JOURNAL_SOCKET = "/run/systemd/journal/socket"


class JournalSocketHandler(logging.Handler):
    """Log to journald over its native datagram protocol.

    journald attaches the trusted fields (``_PID``, ``_UID``, the systemd
    unit) from the socket peer credentials itself, so the payload carries
    only the message fields. See systemd.io/JOURNAL_NATIVE_PROTOCOL.
    """

    def __init__(self, address: str = _JOURNAL_SOCKET) -> None:
        super().__init__()
        self._address = address
        self._socket: socket.socket | None = None  # created on first emit

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self._socket is None:
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self._socket.sendto(self._payload(record), self._address)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        super().close()

    def _payload(self, record: logging.LogRecord) -> bytes:
        fields = {
            "MESSAGE": self.format(record),
            "PRIORITY": str(_syslog_priority(record.levelno)),
            "SYSLOG_IDENTIFIER": IDENTIFIER,
            "LOGGER": record.name,
        }
        return b"".join(_serialize_field(key, value) for key, value in fields.items())


def _syslog_priority(levelno: int) -> int:
    """Map a stdlib level to a syslog priority (``journalctl -p`` filters)."""
    if levelno >= logging.CRITICAL:
        return 2
    if levelno >= logging.ERROR:
        return 3
    if levelno >= logging.WARNING:
        return 4
    if levelno >= logging.INFO:
        return 6
    return 7


def _serialize_field(key: str, value: str) -> bytes:
    data = value.encode()
    if b"\n" in data:  # multi-line values are length-prefixed in the protocol
        return key.encode() + b"\n" + struct.pack("<Q", len(data)) + data + b"\n"
    return key.encode() + b"=" + data + b"\n"
