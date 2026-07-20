"""The platform-native log sinks and the console/system mode switch."""

from __future__ import annotations

import logging
import logging.handlers
import socket
import struct
import sys
import tempfile
from pathlib import Path

import pytest

from anaf_sync.logsink import (
    IDENTIFIER,
    JournalSocketHandler,
    LogMode,
    resolve_mode,
    system_log_handler,
)

# -- mode resolution ---------------------------------------------------------------


def test_env_override_wins_over_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANAF_SYNC_LOG", "system")
    assert resolve_mode(interactive=True) is LogMode.SYSTEM
    monkeypatch.setenv("ANAF_SYNC_LOG", "Console")
    assert resolve_mode(interactive=False) is LogMode.CONSOLE


def test_unknown_env_value_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANAF_SYNC_LOG", "syslog")
    with pytest.raises(ValueError, match="ANAF_SYNC_LOG"):
        resolve_mode()


def test_tty_decides_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANAF_SYNC_LOG", raising=False)
    assert resolve_mode(interactive=True) is LogMode.CONSOLE
    assert resolve_mode(interactive=False) is LogMode.SYSTEM


# -- platform dispatch -------------------------------------------------------------


def test_system_handler_matches_platform() -> None:
    handler = system_log_handler()
    if sys.platform == "win32":
        # Either variant reports to the Application event log; which one we
        # get depends on whether source registration needed elevation.
        assert type(handler).__name__ in {"NTEventLogHandler", "_RawEventLogHandler"}
    elif sys.platform == "darwin":
        import pyoslog

        assert isinstance(handler, pyoslog.Handler)
    else:
        assert isinstance(handler, JournalSocketHandler)


# -- journald native protocol ------------------------------------------------------

pytestmark_posix = pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX sockets")


def _record(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="anaf_sync.engine",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


@pytest.fixture
def journal_socket() -> tuple[socket.socket, str]:
    # tempfile.mkdtemp over tmp_path: sun_path is capped at ~104 bytes and
    # pytest's tmp dirs can exceed it on macOS.
    directory = tempfile.mkdtemp()
    address = str(Path(directory) / "journal.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(address)
    return server, address


@pytestmark_posix
def test_journal_datagram_fields(journal_socket: tuple[socket.socket, str]) -> None:
    server, address = journal_socket
    handler = JournalSocketHandler(address=address)
    handler.emit(_record(logging.WARNING, "no_signature_xml message_id=42"))
    data = server.recv(65536)
    assert b"MESSAGE=no_signature_xml message_id=42\n" in data
    assert b"PRIORITY=4\n" in data
    assert f"SYSLOG_IDENTIFIER={IDENTIFIER}\n".encode() in data
    assert b"LOGGER=anaf_sync.engine\n" in data
    handler.close()


@pytestmark_posix
@pytest.mark.parametrize(
    ("level", "priority"),
    [
        (logging.DEBUG, b"7"),
        (logging.INFO, b"6"),
        (logging.WARNING, b"4"),
        (logging.ERROR, b"3"),
        (logging.CRITICAL, b"2"),
    ],
)
def test_journal_priority_mapping(
    journal_socket: tuple[socket.socket, str], level: int, priority: bytes
) -> None:
    server, address = journal_socket
    handler = JournalSocketHandler(address=address)
    handler.emit(_record(level, "x"))
    assert b"PRIORITY=" + priority + b"\n" in server.recv(65536)
    handler.close()


@pytestmark_posix
def test_journal_multiline_value_is_length_prefixed(
    journal_socket: tuple[socket.socket, str],
) -> None:
    server, address = journal_socket
    handler = JournalSocketHandler(address=address)
    handler.emit(_record(logging.ERROR, "line one\nline two"))
    data = server.recv(65536)
    body = b"line one\nline two"
    assert b"MESSAGE\n" + struct.pack("<Q", len(body)) + body + b"\n" in data
    assert b"MESSAGE=" not in data
    handler.close()
