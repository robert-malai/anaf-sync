"""The Dock-icon suppression edge — importable and safe on every platform."""

import ctypes
import ctypes.util
import sys
from collections.abc import Iterator

import pytest

from anaf_sync.tray import macos


def _activation_policy() -> int:
    """Read NSApp's current activation policy through the ObjC runtime."""
    objc = ctypes.cdll.LoadLibrary(str(ctypes.util.find_library("objc")))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    send_obj = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(send)
    send_long = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(send)
    cls = objc.objc_getClass(b"NSApplication")
    app = send_obj(cls, objc.sel_registerName(b"sharedApplication"))
    return int(send_long(app, objc.sel_registerName(b"activationPolicy")))


@pytest.fixture(autouse=True)
def _unbound() -> Iterator[None]:
    """Each test starts (and leaves) the AppKit singleton unbound."""
    macos._appkit.cache_clear()
    yield
    macos._appkit.cache_clear()


def test_no_ops_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos.sys, "platform", "win32")

    assert macos.hide_dock_icon() is False
    macos.activate()  # must not raise


def test_survives_an_unavailable_objc_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    monkeypatch.setattr(macos.ctypes.util, "find_library", lambda _name: None)

    assert macos.hide_dock_icon() is False
    macos.activate()


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit is macOS-only")
def test_sets_the_accessory_policy_on_macos() -> None:
    assert macos.hide_dock_icon() is True
    assert _activation_policy() == macos._POLICY_ACCESSORY
