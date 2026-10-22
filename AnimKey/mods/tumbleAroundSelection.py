"""Use Maya's selection pivot, with stable Frame Selection navigation when off.

objectTumble is Maya's cursor-hit pivot, despite the flag's name. Keep it off
in both modes so starting a drag cannot replace the pivot established by F.
Only native context preferences are edited; cameras are never repositioned.
"""

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods import configMod as config


SETTING_KEY = "tumble_around_selection_enabled"
# Retain ownership when the module is reloaded in a running Maya session.
if "_active" not in globals():
    _active = False
if "_script_jobs" not in globals():
    _script_jobs = []
if "_running" not in globals():
    _running = False
_pending = False


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
    """Apply the current mode, including repair of settings left by old versions."""
    global _pending
    _pending = False
    context = _tumble_context()
    if not context:
        return False
    # Context commands are undoable. A later animation Undo must never restore
    # the broken navigation preferences or consume a camera-preference step.
    undo_enabled = cmds.undoInfo(query=True, state=True)
    if undo_enabled:
        cmds.undoInfo(stateWithoutFlush=False)
    try:
        results = (
            _set_setting(context, "objectTumble", False),
            _set_setting(context, "localTumble", 0 if _active else 1),
            _set_setting(context, "autoSetPivot", _active),
        )
    finally:
        if undo_enabled:
            cmds.undoInfo(stateWithoutFlush=True)
    return all(results)


def _refresh_deferred():
    global _pending
    if _pending or not _running:
        return
    _pending = True
    try:
        cmds.evalDeferred(
            "from AnimKey.mods import tumbleAroundSelection; "
            "tumbleAroundSelection._run_deferred()",
            lowestPriority=True,
        )
    except TypeError:
        cmds.evalDeferred(
            "from AnimKey.mods import tumbleAroundSelection; "
            "tumbleAroundSelection._run_deferred()"
        )
    except Exception:
        _pending = False


def _run_deferred():
    global _pending
    _pending = False
    if _running:
        refresh()


def _install_script_jobs():
    global _script_jobs
    _script_jobs = [job for job in _script_jobs if cmds.scriptJob(exists=job)]
    if _script_jobs:
        return
    for event_name in ("ToolChanged", "SceneOpened", "NewSceneOpened"):
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
    global _active, _pending, _running
    if enabled is None:
        enabled = is_enabled()
    _active = bool(enabled)
    _running = True
    _pending = False
    # Maya can reload context preferences when tools or scenes change, even
    # while our selection-following mode is off. Keep normal F navigation safe.
    _install_script_jobs()

    # Disabled means normal center-of-interest navigation. Restoring a saved
    # cursor-pivot setting could reproduce the jump even with the box unchecked;
    # after a restart there may be no saved snapshot to restore at all.
    if not refresh():
        _refresh_deferred()


def shutdown():
    global _active, _running, _pending
    _active = False
    _running = False
    _pending = False
    _kill_script_jobs()
    refresh()


def is_active():
    return _active
