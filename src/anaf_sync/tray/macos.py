"""macOS Dock behaviour for the tray app — a menu-bar-only activation policy.

The PyInstaller bundle declares ``LSUIElement`` in its Info.plist, so the
``.app`` never shows in the Dock. A pip/uv install has no bundle: Qt's Cocoa
plugin transforms the bare interpreter into a *foreground* application, and the
tray ends up with a "Python" Dock icon that invites someone to quit it — taking
the tray with it.

:func:`hide_dock_icon` fixes that at runtime by asking AppKit for the
*accessory* activation policy, the programmatic twin of ``LSUIElement``. There
is no Qt API for it, and pyobjc is not a dependency, so the two calls we need
go through the Objective-C runtime with :mod:`ctypes`. Everything here is a
no-op off macOS and never raises: a missing Dock icon is cosmetic, a crashed
tray is not.

Qt's ``QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM`` looks like a simpler
route and is not one: it only skips the transform, leaving the process
*Prohibited* — no Dock icon, but no focus for Facturi or Setări either.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import sys
from functools import cache

__all__ = ["activate", "hide_dock_icon"]

# NSApplicationActivationPolicyAccessory — no Dock icon, no app-switcher entry,
# but the process may still show windows and become active on request.
_POLICY_ACCESSORY = 1


class _AppKit:
    """The three Objective-C calls we need, bound once."""

    def __init__(self) -> None:
        if (objc_path := ctypes.util.find_library("objc")) is None:
            raise OSError("the Objective-C runtime is unavailable")
        if (appkit_path := ctypes.util.find_library("AppKit")) is None:
            raise OSError("AppKit is unavailable")
        objc = ctypes.cdll.LoadLibrary(objc_path)
        ctypes.cdll.LoadLibrary(appkit_path)  # ensures NSApplication is registered

        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self._sel = objc.sel_registerName

        # objc_msgSend is variadic: on arm64 each signature needs its own
        # prototype over the same address, or arguments land in the wrong
        # registers.
        if (send := ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value) is None:
            raise OSError("objc_msgSend is unavailable")
        self._send_obj = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(send)
        self._send_long = ctypes.CFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long
        )(send)
        self._send_bool = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool
        )(send)

        if not (cls := objc.objc_getClass(b"NSApplication")):
            raise OSError("NSApplication is unavailable")
        if not (app := self._send_obj(cls, self._sel(b"sharedApplication"))):
            raise OSError("NSApplication has no shared instance")
        self._app = app

    def set_activation_policy(self, policy: int) -> bool:
        sel = self._sel(b"setActivationPolicy:")
        return bool(self._send_long(self._app, sel, policy))

    def activate_ignoring_other_apps(self) -> None:
        self._send_bool(self._app, self._sel(b"activateIgnoringOtherApps:"), True)


@cache
def _appkit() -> _AppKit | None:
    """The bound AppKit calls, or ``None`` off macOS / if the runtime says no.

    ``@cache`` is the singleton: the bind happens on first use and the verdict
    — instance or ``None`` — is remembered, so a platform that cannot offer
    AppKit is not probed again on every window open.
    """
    if sys.platform != "darwin":
        return None
    try:
        return _AppKit()
    except Exception:  # pragma: no cover - depends on a broken macOS
        return None


def hide_dock_icon() -> bool:
    """Drop the Dock icon and app-switcher entry; returns whether it took.

    Call this *after* ``QApplication`` exists: Qt's Cocoa plugin runs its own
    foreground transform while the platform plugin loads, and would undo an
    earlier call. Bundled runs are already accessory via ``LSUIElement``, so
    this is simply a no-op there.
    """
    if (appkit := _appkit()) is None:
        return False
    try:
        return appkit.set_activation_policy(_POLICY_ACCESSORY)
    except Exception:  # pragma: no cover - defensive
        return False


def activate() -> None:
    """Bring the app's windows to the front.

    An accessory app is not in the app-switcher, so ``raise_()`` /
    ``activateWindow()`` alone can leave a window behind the app that had
    focus. This is the AppKit nudge that makes it come forward anyway.
    """
    if (appkit := _appkit()) is None:
        return
    with contextlib.suppress(Exception):  # pragma: no cover - defensive
        appkit.activate_ignoring_other_apps()
