"""
Tumble Around Selection helper for AnimKey.

Keeps the active viewport camera tumble pivot centered on the selected objects.
"""

import math

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods import configMod as config


SETTING_KEY = "tumble_around_selection_enabled"

_script_jobs = []
_active = False
_pending = False
_refreshing = False
_original_tumble_context = None
_original_tumble_settings = None


def is_enabled():
    return bool(config.get_setting(SETTING_KEY, False))


def set_enabled(enabled):
    config.set_setting(SETTING_KEY, bool(enabled))
    apply(bool(enabled))


def _is_model_panel(panel):
    if not panel:
        return False
    try:
        return cmds.getPanel(typeOf=panel) == "modelPanel"
    except Exception:
        return False


def _active_model_panel():
    candidates = []
    for query in ("underPointer", "withFocus"):
        try:
            panel = cmds.getPanel(**{query: True})
            if panel:
                candidates.append(panel)
        except Exception:
            pass
    try:
        candidates.extend(cmds.getPanel(type="modelPanel") or [])
    except Exception:
        pass
    for panel in candidates:
        if _is_model_panel(panel):
            return panel
    return None


def _tumble_context():
    candidates = []
    try:
        ctx = mel.eval('global string $gTumbleCtx; $temp=$gTumbleCtx;')
        if ctx:
            candidates.append(ctx)
    except Exception:
        pass
    candidates.append("tumbleContext")

    seen = set()
    for ctx in candidates:
        if not ctx or ctx in seen:
            continue
        seen.add(ctx)
        try:
            if cmds.tumbleCtx(ctx, exists=True):
                return ctx
        except Exception:
            pass
        try:
            if cmds.tumbleCtx(ctx, query=True, exists=True):
                return ctx
        except Exception:
            pass
    return None


def _query_tumble_setting(ctx, flag, default=None):
    try:
        return cmds.tumbleCtx(ctx, query=True, **{flag: True})
    except Exception:
        return default


def _set_tumble_setting(ctx, flag, value):
    try:
        cmds.tumbleCtx(ctx, edit=True, **{flag: value})
        return True
    except Exception:
        return False


def _remember_tumble_context_settings():
    global _original_tumble_context, _original_tumble_settings
    if _original_tumble_settings is not None:
        return
    ctx = _tumble_context()
    if not ctx:
        return
    _original_tumble_context = ctx
    _original_tumble_settings = {
        "objectTumble": _query_tumble_setting(ctx, "objectTumble", None),
        "autoSetPivot": _query_tumble_setting(ctx, "autoSetPivot", None),
    }


def _apply_tumble_context_settings():
    ctx = _tumble_context()
    if not ctx:
        return False
    changed = False
    changed = _set_tumble_setting(ctx, "objectTumble", True) or changed
    changed = _set_tumble_setting(ctx, "autoSetPivot", True) or changed
    return changed


def _restore_tumble_context_settings():
    global _original_tumble_context, _original_tumble_settings
    ctx = _original_tumble_context or _tumble_context()
    settings = _original_tumble_settings or {}
    if ctx:
        for flag, value in settings.items():
            if value is None:
                continue
            _set_tumble_setting(ctx, flag, value)
    _original_tumble_context = None
    _original_tumble_settings = None


def _camera_nodes(panel):
    try:
        camera = cmds.modelPanel(panel, query=True, camera=True)
    except Exception:
        return None, None

    if not camera or not cmds.objExists(camera):
        return None, None

    try:
        node_type = cmds.nodeType(camera)
    except Exception:
        return None, None

    if node_type == "camera":
        shape = camera
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        transform = parents[0] if parents else None
    else:
        transform = camera
        shapes = cmds.listRelatives(transform, shapes=True, type="camera", fullPath=True) or []
        shape = shapes[0] if shapes else None
    return transform, shape


def _selected_objects():
    try:
        selection = cmds.ls(selection=True, long=True) or []
    except Exception:
        return []

    objects = []
    seen = set()
    for item in selection:
        node = item.split(".", 1)[0]
        if node in seen:
            continue
        try:
            if cmds.objExists(node):
                objects.append(node)
                seen.add(node)
        except Exception:
            pass
    return objects


def _selection_center(objects):
    if not objects:
        return None

    try:
        bbox = cmds.exactWorldBoundingBox(objects, ignoreInvisible=True)
    except TypeError:
        try:
            bbox = cmds.exactWorldBoundingBox(objects)
        except Exception:
            bbox = None
    except Exception:
        bbox = None

    if bbox and len(bbox) == 6:
        return (
            (bbox[0] + bbox[3]) * 0.5,
            (bbox[1] + bbox[4]) * 0.5,
            (bbox[2] + bbox[5]) * 0.5,
        )

    positions = []
    for obj in objects:
        try:
            positions.append(cmds.xform(obj, query=True, worldSpace=True, rotatePivot=True))
        except Exception:
            pass
    if not positions:
        return None

    count = float(len(positions))
    return (
        sum(pos[0] for pos in positions) / count,
        sum(pos[1] for pos in positions) / count,
        sum(pos[2] for pos in positions) / count,
    )


def _set_tumble_pivot_on_node(node, center):
    if not node or not cmds.objExists(node):
        return False

    try:
        if cmds.attributeQuery("tumblePivot", node=node, exists=True):
            cmds.setAttr(node + ".tumblePivot", center[0], center[1], center[2], type="double3")
            return True
    except Exception:
        pass

    changed = False
    for axis, value in zip(("X", "Y", "Z"), center):
        attr = "tumblePivot" + axis
        try:
            if cmds.attributeQuery(attr, node=node, exists=True):
                cmds.setAttr(node + "." + attr, value)
                changed = True
        except Exception:
            pass
    return changed


def _set_center_of_interest(transform, shape, center):
    if not transform or not shape or not cmds.objExists(shape):
        return
    try:
        if not cmds.attributeQuery("centerOfInterest", node=shape, exists=True):
            return
        camera_pos = cmds.xform(transform, query=True, worldSpace=True, translation=True)
        distance = math.sqrt(
            (camera_pos[0] - center[0]) ** 2
            + (camera_pos[1] - center[1]) ** 2
            + (camera_pos[2] - center[2]) ** 2
        )
        cmds.setAttr(shape + ".centerOfInterest", max(0.001, distance))
    except Exception:
        pass


def refresh():
    global _pending, _refreshing

    _pending = False
    if not _active or _refreshing:
        return

    _refreshing = True
    try:
        _apply_tumble_context_settings()
        objects = _selected_objects()
        center = _selection_center(objects)
        if center is None:
            return

        panel = _active_model_panel()
        transform, shape = _camera_nodes(panel)
        if not transform and not shape:
            return

        changed = False
        for node in (shape, transform):
            if _set_tumble_pivot_on_node(node, center):
                changed = True

        if changed:
            _set_center_of_interest(transform, shape, center)
    finally:
        _refreshing = False


def refresh_deferred():
    global _pending

    if not _active or _pending:
        return
    _pending = True
    try:
        cmds.evalDeferred(
            "from AnimKey.mods import tumbleAroundSelection; tumbleAroundSelection.refresh()",
            lowestPriority=True,
        )
    except TypeError:
        cmds.evalDeferred(
            "from AnimKey.mods import tumbleAroundSelection; tumbleAroundSelection.refresh()"
        )
    except Exception:
        _pending = False


def _install_script_jobs():
    _kill_script_jobs()
    for event_name in ("SelectionChanged", "timeChanged"):
        try:
            _script_jobs.append(
                cmds.scriptJob(event=(event_name, refresh_deferred), protected=True)
            )
        except Exception:
            pass


def _kill_script_jobs():
    global _script_jobs
    for job in list(_script_jobs):
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
    _script_jobs = []


def apply(enabled=None):
    global _active, _pending

    if enabled is None:
        enabled = is_enabled()

    enabled = bool(enabled)
    if enabled:
        _remember_tumble_context_settings()
        _active = True
        _apply_tumble_context_settings()
        _install_script_jobs()
        refresh_deferred()
    else:
        _active = False
        _pending = False
        _kill_script_jobs()
        _restore_tumble_context_settings()


def is_active():
    return _active
