"""Use Maya's native tumble-around-selection context without moving cameras.

Earlier versions also wrote camera tumblePivot and centerOfInterest on every
selection/time change. That fought Maya's own auto-pivot and could make the
viewport jump or tumble around an old point, especially with several panels.
"""

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods import configMod as config


SETTING_KEY = "tumble_around_selection_enabled"
_active = False
_context_name = None
_original_settings = None
_pending = False
_script_jobs = []


def is_enabled():
    return bool(config.get_setting(SETTING_KEY, False))


def set_enabled(enabled):
    config.set_setting(SETTING_KEY, bool(enabled))
    apply(bool(enabled))


def _tumble_context():
    candidates = []
    try:
        candidates.append(mel.eval(
            'global string $gTumbleCtx; $temp=$gTumbleCtx;'
        ))
    except Exception:
        pass
    candidates.append("tumbleContext")
    for name in candidates:
        if not name:
            continue
        try:
            if cmds.tumbleCtx(name, exists=True):
                return name
        except Exception:
            pass
    return None


def _query_setting(context, flag):
    try:
        return cmds.tumbleCtx(context, query=True, **{flag: True})
    except Exception:
        return None


def _set_setting(context, flag, value):
    if _query_setting(context, flag) == value:
        return True
    try:
        cmds.tumbleCtx(context, edit=True, **{flag: value})
        return True
    except Exception:
        return False


def refresh():
    """Reapply preferences if Maya recreated its tumble context."""
    global _pending, _context_name, _original_settings
    _pending = False
    if not _active:
        return False
    context = _tumble_context()
    if not context:
        return False
    if context != _context_name:
        _context_name = context
        _original_settings = {
            flag: _query_setting(context, flag)
            for flag in ("objectTumble", "autoSetPivot", "localTumble")
        }
    results = (
        _set_setting(context, "localTumble", 0),
        _set_setting(context, "objectTumble", True),
        _set_setting(context, "autoSetPivot", True),
    )
    return all(results)


def _refresh_deferred():
    global _pending
    if _pending or not _active:
        return
    _pending = True
    try:
        cmds.evalDeferred(
            "from AnimKey.mods import tumbleAroundSelection; "
            "tumbleAroundSelection.refresh()",
            lowestPriority=True,
        )
    except TypeError:
        cmds.evalDeferred(
            "from AnimKey.mods import tumbleAroundSelection; "
            "tumbleAroundSelection.refresh()"
        )
    except Exception:
        _pending = False


def _install_script_jobs():
    global _script_jobs
    if _script_jobs:
        return
    for event_name in ("ToolChanged", "SceneOpened"):
        try:
            _script_jobs.append(cmds.scriptJob(
                event=(event_name, _refresh_deferred), protected=True
            ))
        except Exception:
            pass


def _kill_script_jobs():
    global _script_jobs
    for job in _script_jobs:
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
    _script_jobs = []


def apply(enabled=None):
    global _active, _pending, _context_name, _original_settings
    if enabled is None:
        enabled = is_enabled()
    if enabled:
        _active = True
        _install_script_jobs()
        if not refresh():
            _refresh_deferred()
    else:
        _active = False
        _pending = False
        _kill_script_jobs()
        if _context_name and _original_settings:
            for flag, value in _original_settings.items():
                if value is not None:
                    _set_setting(_context_name, flag, value)
        _context_name = None
        _original_settings = None


def is_active():
    return _active
