"""
Small launch gate for AnimKey button entry points.

This is not a security sandbox. In Maya, anyone who can run Python can inspect
or bypass Python code. The goal is to keep normal button entry points from
working when they are launched directly from Script Editor instead of through
AnimKey's toolbar, hotkeys, menus, or Flash Buttons.
"""

import threading
import sys
from contextlib import contextmanager


_state = threading.local()
_TRUSTED_DISPATCH_MODULES = {
    "AnimKey.core.toolbar",
    "AnimKey.mods.hotkeysMod",
    "AnimKey.buttons.flashbuttons",
}


def _depth():
    return int(getattr(_state, "depth", 0) or 0)


def is_animkey_execution():
    return _depth() > 0


def _called_from_trusted_dispatcher():
    frame = None
    try:
        frame = sys._getframe(2)
        for _ in range(16):
            if frame is None:
                break
            module_name = frame.f_globals.get("__name__", "")
            if module_name in _TRUSTED_DISPATCH_MODULES:
                return True
            frame = frame.f_back
    except Exception:
        return False
    finally:
        frame = None
    return False


@contextmanager
def animkey_execution(source="AnimKey", action=None):
    _state.depth = _depth() + 1
    _state.source = source
    _state.action = action
    try:
        yield
    finally:
        next_depth = max(0, _depth() - 1)
        _state.depth = next_depth
        if next_depth == 0:
            _state.source = None
            _state.action = None


def require_animkey_context(action_name="AnimKey action"):
    if is_animkey_execution() or _called_from_trusted_dispatcher():
        return True

    message = (
        "AnimKey: This tool can only be launched from the AnimKey UI, "
        "hotkeys, context menus, or Flash Buttons."
    )
    try:
        import maya.cmds as cmds
        cmds.warning(message)
    except Exception:
        print(message)
    return False
