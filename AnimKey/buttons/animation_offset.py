"""
    AnimKey Button: Animation Offset
    
    Allows moving animated objects without affecting existing animation.
    The position change propagates throughout the entire existing animation.
    Based on AnimKey's animation offset functionality.
"""

import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui
import threading
import time
import sys
import math

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance

from AnimKey.core.animation_offset_session import (
    ANIMATION_OFFSET_ENGINE_REVISION,
    OffsetSession,
    get_active_session,
    get_selected_animation_layer as _session_selected_animation_layer,
    has_active_session,
)
from AnimKey.core.animation_offset_math import time_slider_range_to_inclusive


# Global state for animation offset
_anim_offset_active = False
_anim_offset_thread = None
_anim_offset_run_timer = False
_animation_offset_original_values = {}
_anim_offset_time_range = None
_timeline_overlay = None  # Qt overlay widget

# FIX 1: Global button reference so deactivation via hotkey can always
# reset the button highlight, even when button=None was passed to execute().
_anim_offset_button_ref = None

# FIX: Store the original selection at activation time so we always
# process exactly the objects the user had selected when they pressed OFF.
_anim_offset_original_selection = []

# FIX 4: Immutable baseline – snapshot taken ONCE at activation time and
# NEVER overwritten.  The running diff is always computed against this
# snapshot so floating-point re-baselines cannot accumulate drift.
# Structure: { obj: { attr: { frame: original_value } } }
_anim_offset_frozen_baseline = {}

# Value of every tracked attribute at the frame where the offset starts.
# Structure: { obj: { attr: scalar_value } }
_anim_offset_activation_values = {}

# Evaluated baseline samples across the active range, used when the current
# time has no key but the user still offsets the pose there.
# Structure: { obj: { attr: { frame: original_value } } }
_anim_offset_evaluated_baseline = {}

# FIX: Per-object per-attribute offset cache so we never recalculate an offset
# from scratch once it has been established. Structure:
#   { obj: { attr: float } }
_anim_offset_diff_cache = {}

# FIX 2: Snapshot of interpolated curve values at current_time from the PREVIOUS
# timer tick.  Key change: stored as { obj: { attr: value } } (no frame number)
# so it works even while the user is scrubbing the timeline.
_prev_tick_snapshot = {}
_prev_tick_time = None
_anim_offset_key_nav_filter = None
_anim_offset_time_job = None
_anim_offset_syncing_time = False
_anim_offset_undo_jobs = []
_anim_offset_undo_guard = False
_anim_offset_flush_scheduled = False
_anim_offset_attr_jobs = []
_anim_offset_live_values = {}
_anim_offset_applying_offset = False
_anim_offset_generation = 0

# Undo chunk tracking: open a chunk when movement is detected,
# close it when the user stops moving (diff returns to zero).
# This makes each continuous drag = one single Ctrl+Z step.
_chunk_is_open = False

# ── ANIMATION LAYERS SUPPORT ──────────────────────────────────────────────
# Name of the animation layer that was the *selected* layer at activation
# time. None means "no animation layers in the scene" or "BaseAnimation".
# This is frozen for the whole offset session, exactly like the rest of the
# baseline, so switching the selected layer mid-drag never changes which
# curves are being edited.
_anim_offset_target_layer = None

# Per (obj, attr) cache of the resolved animCurve that belongs to
# _anim_offset_target_layer. Structure: { "obj.attr": "curveNodeName" }
# Populated once per object/attr in _track_object_for_offset and reused
# everywhere else so we never re-walk the blend graph every tick.
_anim_offset_target_curve_cache = {}


def _close_offset_chunk():
    global _chunk_is_open
    if not _chunk_is_open:
        return
    try:
        cmds.undoInfo(closeChunk=True)
    except Exception:
        pass
    _chunk_is_open = False


def _new_offset_generation():
    global _anim_offset_generation
    _anim_offset_generation += 1
    return _anim_offset_generation


def _is_current_offset_generation(generation):
    return generation == _anim_offset_generation


def _has_any_animation_layers():
    """True when the scene has at least one animLayer node."""
    try:
        return bool(cmds.ls(type="animLayer"))
    except Exception:
        return False


def _get_selected_animation_layer():
    """
    Return the name of the animation layer the user has selected in the
    Animation Layer editor, or None if there are no layers (or only the
    implicit BaseAnimation with nothing else in the scene).

    Maya keeps "selected" state on each animLayer node (the layer the user
    clicked in the editor). BaseAnimation itself can also be selected, in
    which case we still return it explicitly so callers can tell "no layers
    at all" (None) apart from "base layer is the active target" ("BaseAnimation").
    """
    try:
        all_layers = cmds.ls(type="animLayer") or []
    except Exception:
        return None

    if not all_layers:
        return None

    selected = []
    for layer in all_layers:
        try:
            if cmds.animLayer(layer, query=True, selected=True):
                selected.append(layer)
        except Exception:
            continue

    if selected:
        # If more than one layer is "selected" (multi-select in the editor),
        # Maya's own tools use the last one returned as the edit target.
        real_layers = [layer for layer in selected if not _is_base_layer(layer)]
        return (real_layers or selected)[-1]

    # No layer explicitly selected: fall back to whatever Maya considers the
    # preferred edit target (mute/solo aware), defaulting to BaseAnimation.
    try:
        preferred = cmds.animLayer(query=True, root=True)
        if preferred:
            return preferred
    except Exception:
        pass

    return "BaseAnimation" if cmds.animLayer("BaseAnimation", query=True, exists=True) else None


def _is_base_layer(layer_name):
    return not layer_name or layer_name == "BaseAnimation"


_ATTR_LONG_NAMES = {
    "tx": "translateX", "ty": "translateY", "tz": "translateZ",
    "rx": "rotateX", "ry": "rotateY", "rz": "rotateZ",
    "sx": "scaleX", "sy": "scaleY", "sz": "scaleZ",
    "v": "visibility",
}


def _split_plug(plug):
    if not plug or "." not in plug:
        return None, None
    node, attr = plug.rsplit(".", 1)
    attr = attr.split("[", 1)[0]
    return node, _ATTR_LONG_NAMES.get(attr, attr)


def _node_name_candidates(node):
    names = set()
    if not node:
        return names

    names.add(node)
    names.add(node.split("|")[-1])
    try:
        names.update(cmds.ls(node, long=True) or [])
    except Exception:
        pass
    try:
        names.update(cmds.ls(node, long=False) or [])
    except Exception:
        pass
    return {name for name in names if name}


def _plug_candidates(attr_full_name):
    node, attr = _split_plug(attr_full_name)
    if not node or not attr:
        return []

    candidates = []
    seen = set()
    for node_name in _node_name_candidates(node):
        plug = node_name + "." + attr
        if plug not in seen:
            seen.add(plug)
            candidates.append(plug)
    return candidates or [attr_full_name]


def _plugs_match(left_plug, right_plug):
    left_node, left_attr = _split_plug(left_plug)
    right_node, right_attr = _split_plug(right_plug)
    if not left_node or not right_node or left_attr != right_attr:
        return False
    return bool(_node_name_candidates(left_node).intersection(_node_name_candidates(right_node)))


def _mel_string(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _layer_anim_curves(layer_name):
    try:
        return cmds.animLayer(layer_name, query=True, animCurves=True) or []
    except Exception:
        return []


def _curve_reaches_attr(curve, attr_full_name, max_depth=8):
    """True if an animCurve eventually contributes to attr_full_name."""
    if not curve or not cmds.objExists(curve):
        return False

    seen = set()
    stack = [curve]
    for _depth in range(max_depth):
        next_stack = []
        for node in stack:
            if node in seen:
                continue
            seen.add(node)
            try:
                outputs = cmds.listConnections(
                    node,
                    source=False,
                    destination=True,
                    plugs=True,
                    skipConversionNodes=True,
                ) or []
            except Exception:
                outputs = []

            for out_plug in outputs:
                if _plugs_match(out_plug, attr_full_name):
                    return True
                out_node = out_plug.split(".", 1)[0]
                if out_node not in seen:
                    next_stack.append(out_node)

        if not next_stack:
            break
        stack = next_stack
    return False


def _fallback_layer_curve_for_attr(attr_full_name, layer_name):
    """Find a layer-owned animCurve when layeredPlug cannot resolve the plug."""
    for curve in _layer_anim_curves(layer_name):
        try:
            if cmds.objExists(curve) and cmds.nodeType(curve).startswith("animCurve"):
                if _curve_reaches_attr(curve, attr_full_name):
                    return curve
        except Exception:
            continue
    return None


def _layered_plug_for_attr(attr_full_name, layer_name):
    """
    Resolve the animBlendNode input plug that represents layer_name's own
    contribution to attr_full_name, using Maya's supported "layeredPlug"
    mechanism. Works even when the layer has no animCurve yet (a flat,
    unanimated layer value), which a curve-only search would miss.

    Returns None if the attribute is not present on layer_name at all.
    """
    try:
        if not cmds.objExists(attr_full_name):
            return None
    except Exception:
        return None

    if _is_base_layer(layer_name):
        # BaseAnimation: walk the animBlendNodeBase chain all the way down,
        # since "layeredPlug" only resolves non-base layers reliably.
        try:
            conns = cmds.listConnections(attr_full_name, type="animBlendNodeBase",
                                          source=True, destination=False) or []
        except Exception:
            conns = []
        if not conns:
            # No layers touch this attribute at all - the base IS the plug.
            return attr_full_name

        blend_node = conns[0]
        while True:
            try:
                upstream = cmds.listConnections(blend_node, type="animBlendNodeBase",
                                                  source=True, destination=False) or []
            except Exception:
                upstream = []
            if not upstream:
                break
            blend_node = upstream[0]

        short_attr = attr_full_name.rsplit(".", 1)[-1]
        try:
            node_type = cmds.objectType(blend_node)
        except Exception:
            node_type = ""
        if node_type == "animBlendNodeAdditiveRotation" and short_attr and short_attr[-1] in "XYZxyz":
            return "{0}.inputA{1}".format(blend_node, short_attr[-1].upper())
        return "{0}.inputA".format(blend_node)

    # Any layer other than BaseAnimation: ask Maya directly which plug holds
    # this layer's contribution. This is the same mechanism the Graph Editor
    # and the Animation Layer Editor use internally, and it correctly
    # returns a plug even before any curve exists on the layer.
    try:
        if not cmds.animLayer(layer_name, query=True, exists=True):
            return None
        owned_attrs = cmds.animLayer(layer_name, query=True, attribute=True) or []
    except Exception:
        return None

    candidates = _plug_candidates(attr_full_name)
    owns_attr = (
        not owned_attrs or
        any(_plugs_match(candidate, owned_attr) for candidate in candidates for owned_attr in owned_attrs)
    )

    for candidate in candidates:
        try:
            plug = mel.eval(
                'animLayer -q -layeredPlug "{0}" "{1}"'.format(
                    _mel_string(candidate),
                    _mel_string(layer_name),
                )
            )
            if plug:
                return plug
        except Exception:
            continue

    if not owns_attr:
        return None

    return None


def _resolve_target_curve_for_layer(attr_full_name, layer_name):
    """
    Find the animCurve that actually belongs to layer_name for attr_full_name.

    With no animation layers in the scene, the curve feeding the attribute
    is the regular direct connection. With layers present, we first resolve
    the layer's own blend-node plug (_layered_plug_for_attr) and then look
    for an animCurve feeding *that specific plug* - never the composited
    attribute itself. Returns None when the layer has no curve yet (a flat
    value on that layer) so callers can tell "create the first key here"
    apart from "nothing to do".
    """
    if not _has_any_animation_layers():
        try:
            curves = cmds.listConnections(attr_full_name, source=True, destination=False,
                                           type="animCurve") or []
        except Exception:
            curves = []
        return curves[0] if curves else None

    plug = _layered_plug_for_attr(attr_full_name, layer_name)
    if not plug:
        return _fallback_layer_curve_for_attr(attr_full_name, layer_name)

    if plug == attr_full_name:
        try:
            curves = cmds.listConnections(plug, source=True, destination=False,
                                           type="animCurve") or []
        except Exception:
            curves = []
        return curves[0] if curves else None

    try:
        curves = cmds.listConnections(plug, source=True, destination=False, type="animCurve") or []
    except Exception:
        curves = []
    return curves[0] if curves else None


def _target_layer_for_session():
    """Return the frozen layer target for the active offset session."""
    global _anim_offset_target_layer
    return _anim_offset_target_layer


def _get_target_curve(obj, attr, use_cache=True):
    """
    Resolve (and cache) the animCurve that the active offset session should
    read/write for obj.attr, based on the frozen target layer.

    Returns None when the target layer has no curve for this attribute yet
    (flat/unanimated on that layer, or simply not present there at all) -
    callers should consult _get_target_read_plug for the right plug to
    sample in that case, instead of falling back to the composited attr.
    """
    attr_full_name = obj + "." + attr
    cache_key = attr_full_name

    if use_cache and cache_key in _anim_offset_target_curve_cache:
        return _anim_offset_target_curve_cache[cache_key]

    layer_name = _target_layer_for_session()
    curve = _resolve_target_curve_for_layer(attr_full_name, layer_name)
    _anim_offset_target_curve_cache[cache_key] = curve
    return curve


def _get_target_read_plug(obj, attr):
    """
    Resolve the best plug to *read* obj.attr's target-layer value from when
    there is no animCurve yet: the layer's own blend-node input plug if one
    exists, otherwise the attribute plug itself (no-layers case).
    """
    attr_full_name = obj + "." + attr
    if not _has_any_animation_layers():
        return attr_full_name

    layer_name = _target_layer_for_session()
    plug = _layered_plug_for_attr(attr_full_name, layer_name)
    return plug or attr_full_name


def _layer_aware_get_value(obj, attr, frame=None):
    """
    Read the value contributed by the target layer's own curve, NOT the
    layer-composited result that a plain getAttr on the attribute would
    return.

    Three cases, in order:
      1. Target layer has its own animCurve for this attr -> sample/evaluate
         that curve directly.
      2. No animation layers at all in the scene -> behave exactly like the
         original getAttr-based implementation.
      3. Animation layers exist but the target layer has no curve yet for
         this attr (flat layer value) -> read the layer's own blend-node
         input plug instead of the composited attribute, so we never read
         a value "contaminated" by layers above/below it.
    """
    curve = _get_target_curve(obj, attr)

    if curve is None:
        read_plug = _get_target_read_plug(obj, attr)
        try:
            if frame is None:
                value, ok = _as_scalar(cmds.getAttr(read_plug))
            else:
                value, ok = _as_scalar(cmds.getAttr(read_plug, time=frame))
            return (float(value), True) if ok and isinstance(value, (int, float, bool)) else (None, False)
        except Exception:
            return None, False

    try:
        if frame is None:
            frame = cmds.currentTime(query=True)
        # animCurve nodes can be evaluated directly at an arbitrary time
        # without touching the attribute itself, which is exactly the
        # per-layer value we want (no composite from other layers).
        values = cmds.keyframe(curve, query=True, time=(frame, frame), valueChange=True)
        if values:
            return float(values[0]), True
        # No key exactly at this frame: evaluate the curve's interpolated
        # value instead of just sampling existing keys.
        evaluated = cmds.keyframe(curve, query=True, eval=True, time=(frame, frame))
        if evaluated:
            return float(evaluated[0]), True
    except Exception:
        pass

    return None, False


def _layer_aware_has_key(obj, attr, frame, epsilon=1e-4):
    """Per-layer key existence check, using the resolved target curve."""
    curve = _get_target_curve(obj, attr)
    if curve is None:
        if _has_any_animation_layers():
            # Target layer has no curve for this attr at all yet, so it
            # cannot have a key on any frame.
            return False
        return _has_key_at_time(obj, attr, frame, epsilon=epsilon)
    try:
        keys = cmds.keyframe(curve, query=True, time=(frame, frame)) or []
    except Exception:
        return False
    return any(abs(float(key) - float(frame)) <= epsilon for key in keys)


def _layer_aware_query_keyframes(obj, attr, time_range):
    """Per-layer keyframe listing, using the resolved target curve."""
    curve = _get_target_curve(obj, attr)
    if curve is None:
        if _has_any_animation_layers():
            return []
        target = obj + "." + attr
    else:
        target = curve
    try:
        keyframes = cmds.keyframe(
            target,
            query=True,
            time=(time_range[0], time_range[1]),
        ) or []
    except Exception:
        return []
    return sorted({float(frame) for frame in keyframes if time_range[0] <= frame <= time_range[1]})


def _layer_aware_apply_delta(obj, attr, keyframes, delta, protected_times=None):
    """
    Apply a relative delta to keys on the curve belonging to the target
    animation layer, instead of the attribute plug. With no layers, curve
    is None and we transparently use the plug exactly like the original
    (pre-layer-aware) implementation did.
    """
    global _anim_offset_applying_offset
    curve = _get_target_curve(obj, attr)
    if curve is None and _has_any_animation_layers():
        # Nothing keyed on the target layer for this attr - there is
        # nothing to offset by relative delta (use _layer_aware_set_key to
        # create the first key instead).
        return False
    target = curve if curve is not None else (obj + "." + attr)
    current_epsilon = 1e-4
    protected_times = protected_times or []
    applied = False

    previous_state = _anim_offset_applying_offset
    _anim_offset_applying_offset = True
    try:
        for frame in keyframes:
            if any(abs(float(frame) - float(protected_time)) <= current_epsilon for protected_time in protected_times):
                continue
            try:
                cmds.keyframe(
                    target,
                    edit=True,
                    time=(frame, frame),
                    relative=True,
                    valueChange=delta,
                )
                applied = True
            except Exception:
                continue
    finally:
        _anim_offset_applying_offset = previous_state

    return applied or any(
        any(abs(float(frame) - float(protected_time)) <= current_epsilon for protected_time in protected_times)
        for frame in keyframes
    )


def _layer_aware_set_key(obj, attr, frame, value):
    """
    Set/move a key on the target layer's curve at an exact frame.

    cmds.setKeyframe on the plug is layer-aware automatically *when the
    target layer is the one Maya currently treats as the preferred edit
    layer* - which is not guaranteed to match our frozen session target if
    the user changed the selected layer in the editor mid-session. We force
    correctness by temporarily selecting the session's target layer (and
    restoring whatever was selected before) only for the duration of the
    key edit.
    """
    global _anim_offset_applying_offset
    layer_name = _target_layer_for_session()
    attr_full_name = obj + "." + attr

    if not layer_name or not _has_any_animation_layers():
        return _set_key_value_at_time(obj, attr, frame, value)

    previous_state = _anim_offset_applying_offset
    _anim_offset_applying_offset = True

    previously_selected = []
    try:
        for layer in cmds.ls(type="animLayer") or []:
            try:
                if cmds.animLayer(layer, query=True, selected=True):
                    previously_selected.append(layer)
            except Exception:
                continue
    except Exception:
        pass

    changed_selection = False
    try:
        try:
            if cmds.animLayer(layer_name, query=True, exists=True):
                cmds.animLayer(layer_name, edit=True, selected=True, preferred=True)
                changed_selection = True
        except Exception:
            pass

        cmds.setKeyframe(obj, attribute=attr, time=frame, value=value)
        # The curve cache may now be stale for this attr (a brand-new curve
        # may have just been created on the target layer for the first time).
        _anim_offset_target_curve_cache.pop(attr_full_name, None)
        return True
    except Exception:
        return False
    finally:
        if changed_selection:
            try:
                for layer in cmds.ls(type="animLayer") or []:
                    try:
                        is_selected = layer in previously_selected
                        cmds.animLayer(layer, edit=True, selected=is_selected)
                    except Exception:
                        continue
            except Exception:
                pass
        _anim_offset_applying_offset = previous_state


def _resnapshot_tracked_objects_as_baseline():
    """Make the current scene state the new stable offset baseline."""
    global _prev_tick_snapshot, _prev_tick_time

    if not _anim_offset_time_range:
        return

    tracked_objects = [obj for obj in _anim_offset_original_selection if cmds.objExists(obj)]
    if not tracked_objects:
        return

    _animation_offset_original_values.clear()
    _anim_offset_frozen_baseline.clear()
    _anim_offset_activation_values.clear()
    _anim_offset_evaluated_baseline.clear()
    _anim_offset_diff_cache.clear()
    # The target layer itself stays frozen (it's a session-level setting),
    # but cached curve resolutions can go stale after undo/redo recreates
    # or destroys animCurve nodes, so they're cleared and re-resolved.
    _anim_offset_target_curve_cache.clear()

    current_time = cmds.currentTime(query=True)
    kept_objects = []
    for obj in tracked_objects:
        if _track_object_for_offset(obj, _anim_offset_time_range, current_time):
            kept_objects.append(obj)

    _anim_offset_original_selection[:] = kept_objects
    _prev_tick_snapshot = _snapshot_tracked_values(_anim_offset_original_selection, current_time)
    _prev_tick_time = current_time


def _sync_offset_state_from_scene_after_undo():
    """Rebuild runtime diffs after Maya undo/redo without changing baseline."""
    global _prev_tick_snapshot, _prev_tick_time

    if not _anim_offset_time_range:
        return

    current_time = cmds.currentTime(query=True)
    _anim_offset_diff_cache.clear()
    _anim_offset_live_values.clear()
    # Undo/redo can recreate or remove animCurve nodes, so cached curve paths
    # need to be resolved again while the frozen values remain untouched.
    _anim_offset_target_curve_cache.clear()

    kept_objects = []
    for obj in list(_anim_offset_original_selection):
        if not cmds.objExists(obj):
            continue
        kept_objects.append(obj)
        frozen_obj_data = _anim_offset_frozen_baseline.get(obj, {})
        for attr in frozen_obj_data.keys():
            attr_full_name = obj + "." + attr
            if not _is_offsettable_numeric_attr(attr_full_name):
                continue

            keyframes = _layer_aware_query_keyframes(obj, attr, _anim_offset_time_range)
            candidate_diffs = []
            for frame in keyframes:
                if abs(float(frame) - float(current_time)) <= 1e-4:
                    continue
                baseline_value, has_baseline = _baseline_value_for_time(obj, attr, frame)
                if not has_baseline:
                    continue
                current_value, ok = _layer_aware_get_value(obj, attr, frame=frame)
                if ok:
                    candidate_diffs.append(float(current_value) - float(baseline_value))

            if candidate_diffs:
                diff = max(candidate_diffs, key=lambda value: abs(float(value)))
            else:
                baseline_value, has_baseline = _baseline_value_for_time(obj, attr, current_time)
                if not has_baseline:
                    continue
                current_value, ok = _layer_aware_get_value(obj, attr, frame=current_time)
                if not ok:
                    continue
                diff = float(current_value) - float(baseline_value)

            if abs(diff) > 1e-9:
                _anim_offset_diff_cache.setdefault(obj, {})[attr] = diff

    _anim_offset_original_selection[:] = kept_objects
    _prev_tick_snapshot = _snapshot_tracked_values(_anim_offset_original_selection, current_time)
    _prev_tick_time = current_time


def _begin_offset_undo_guard():
    global _anim_offset_undo_guard
    if not _anim_offset_active:
        return
    _anim_offset_undo_guard = True
    _close_offset_chunk()


def _finish_offset_undo_guard():
    global _anim_offset_undo_guard
    if not _anim_offset_active:
        _anim_offset_undo_guard = False
        return
    try:
        _close_offset_chunk()
        _sync_offset_state_from_scene_after_undo()
    finally:
        _anim_offset_undo_guard = False


def _defer_finish_offset_undo_guard():
    try:
        import maya.utils as utils
        utils.executeDeferred(_finish_offset_undo_guard)
    except Exception:
        try:
            QtCore.QTimer.singleShot(0, _finish_offset_undo_guard)
        except Exception:
            _finish_offset_undo_guard()


def _on_offset_undo_redo(*args):
    if not _anim_offset_active:
        return
    _begin_offset_undo_guard()
    _defer_finish_offset_undo_guard()


def _start_offset_undo_jobs():
    global _anim_offset_undo_jobs
    _stop_offset_undo_jobs()
    for event_name in ("Undo", "Redo"):
        try:
            _anim_offset_undo_jobs.append(cmds.scriptJob(
                event=[event_name, _on_offset_undo_redo],
                killWithScene=True
            ))
        except Exception:
            pass


def _stop_offset_undo_jobs():
    global _anim_offset_undo_jobs, _anim_offset_undo_guard
    for job in _anim_offset_undo_jobs:
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
    _anim_offset_undo_jobs = []
    _anim_offset_undo_guard = False


def _record_live_offset_value(obj, attr):
    """Remember the live control value before keyboard key navigation changes time."""
    if not _anim_offset_active or not _anim_offset_run_timer:
        return
    if _anim_offset_undo_guard or _anim_offset_syncing_time or _anim_offset_applying_offset:
        return
    if not _anim_offset_time_range or not cmds.objExists(obj):
        return

    try:
        current_time = float(cmds.currentTime(query=True))
    except Exception:
        return
    if _prev_tick_time is not None and abs(current_time - float(_prev_tick_time)) > 1e-4:
        return
    if current_time < _anim_offset_time_range[0] - 1e-4 or current_time > _anim_offset_time_range[1] + 1e-4:
        return

    attr_full_name = obj + "." + attr
    if not _is_offsettable_numeric_attr(attr_full_name):
        return

    value, ok = _layer_aware_get_value(obj, attr, frame=current_time)
    if not ok:
        return

    _anim_offset_live_values.setdefault(obj, {}).setdefault(attr, {})[current_time] = float(value)


def _live_value_for_time(obj, attr, frame, epsilon=1e-4):
    frame_values = _anim_offset_live_values.get(obj, {}).get(attr, {})
    if not frame_values:
        return None
    for live_time, value in frame_values.items():
        try:
            if abs(float(live_time) - float(frame)) <= epsilon:
                return float(value)
        except Exception:
            continue
    return None


def _snapshot_live_offset_values_at_current_time():
    """Capture current viewport/manipulator values before Maya jumps time."""
    if not _anim_offset_active or not _anim_offset_run_timer:
        return
    if _anim_offset_undo_guard or _anim_offset_syncing_time or _anim_offset_applying_offset:
        return
    if not _anim_offset_time_range:
        return

    try:
        current_time = float(cmds.currentTime(query=True))
    except Exception:
        return
    if _prev_tick_time is not None and abs(current_time - float(_prev_tick_time)) > 1e-4:
        return
    if current_time < _anim_offset_time_range[0] - 1e-4 or current_time > _anim_offset_time_range[1] + 1e-4:
        return

    for obj in list(_anim_offset_original_selection):
        if not cmds.objExists(obj):
            continue
        for attr in _anim_offset_frozen_baseline.get(obj, {}).keys():
            attr_full_name = obj + "." + attr
            if not _is_offsettable_numeric_attr(attr_full_name):
                continue
            value, ok = _layer_aware_get_value(obj, attr, frame=current_time)
            if ok:
                _anim_offset_live_values.setdefault(obj, {}).setdefault(attr, {})[current_time] = float(value)


def _stop_offset_attr_jobs(clear_values=True):
    global _anim_offset_attr_jobs
    for job in _anim_offset_attr_jobs:
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
    _anim_offset_attr_jobs = []
    if clear_values:
        _anim_offset_live_values.clear()


def _start_offset_attr_jobs():
    global _anim_offset_attr_jobs
    _stop_offset_attr_jobs(clear_values=False)
    _anim_offset_live_values.clear()

    for obj in list(_anim_offset_original_selection):
        if not cmds.objExists(obj):
            continue
        for attr in _anim_offset_frozen_baseline.get(obj, {}).keys():
            attr_full_name = obj + "." + attr
            try:
                job = cmds.scriptJob(
                    attributeChange=[attr_full_name, lambda o=obj, a=attr: _record_live_offset_value(o, a)],
                    killWithScene=True,
                )
                _anim_offset_attr_jobs.append(job)
            except Exception:
                continue


def _clear_time_slider_range_to_current():
    """Clear Maya's blue time-slider range while keeping AnimKey's overlay."""
    try:
        aTimeSlider = mel.eval('global string $gPlayBackSlider; $tmpVar=$gPlayBackSlider')
        if not aTimeSlider:
            return
        current_time = cmds.currentTime(query=True)
        cmds.timeControl(aTimeSlider, e=True, rangeArray=[current_time, current_time + 1])
    except Exception:
        pass


def _stop_offset_time_job():
    global _anim_offset_time_job
    if _anim_offset_time_job:
        try:
            if cmds.scriptJob(exists=_anim_offset_time_job):
                cmds.scriptJob(kill=_anim_offset_time_job, force=True)
        except Exception:
            pass
    _anim_offset_time_job = None


def _start_offset_time_job():
    global _anim_offset_time_job
    _stop_offset_time_job()
    try:
        _anim_offset_time_job = cmds.scriptJob(
            event=["timeChanged", _on_offset_time_changed],
            killWithScene=True
        )
    except Exception:
        _anim_offset_time_job = None


def _qt_event_type(name):
    event_enum = getattr(QtCore.QEvent, "Type", None)
    if event_enum is not None and hasattr(event_enum, name):
        return getattr(event_enum, name)
    return getattr(QtCore.QEvent, name)


def _qt_key(name):
    key_enum = getattr(QtCore.Qt, "Key", None)
    if key_enum is not None and hasattr(key_enum, name):
        return getattr(key_enum, name)
    return getattr(QtCore.Qt, name, None)


def _qt_keyboard_modifier(name):
    modifier_enum = getattr(QtCore.Qt, "KeyboardModifier", None)
    if modifier_enum is not None and hasattr(modifier_enum, name):
        return getattr(modifier_enum, name)
    return getattr(QtCore.Qt, name, None)


def _flush_offset_before_time_navigation():
    """Commit the current offset edit before comma/period jumps time."""
    if not _anim_offset_active or not _anim_offset_run_timer:
        return
    if _anim_offset_undo_guard:
        return
    try:
        _snapshot_live_offset_values_at_current_time()
        adjust_keyframes(scan_changed_keys=True)
        _flush_previous_frame_change()
    except Exception:
        pass


def _run_scheduled_offset_flush(generation=None):
    global _anim_offset_flush_scheduled
    _anim_offset_flush_scheduled = False
    if generation is not None and not _is_current_offset_generation(generation):
        return
    _flush_offset_before_time_navigation()


def _schedule_offset_interaction_flush():
    """Flush shortly after viewport interactions finish, before key navigation."""
    global _anim_offset_flush_scheduled
    if not _anim_offset_active or not _anim_offset_run_timer:
        return
    if _anim_offset_undo_guard or _anim_offset_flush_scheduled:
        return
    _snapshot_live_offset_values_at_current_time()
    _anim_offset_flush_scheduled = True
    generation = _anim_offset_generation
    try:
        import maya.utils as utils
        utils.executeDeferred(lambda: _run_scheduled_offset_flush(generation))
    except Exception:
        try:
            QtCore.QTimer.singleShot(0, lambda: _run_scheduled_offset_flush(generation))
        except Exception:
            _run_scheduled_offset_flush(generation)


class _OffsetKeyNavigationFilter(QtCore.QObject):
    """Catch Maya key navigation before the current frame changes."""

    def __init__(self, parent=None):
        super(_OffsetKeyNavigationFilter, self).__init__(parent)
        self._nav_event_types = {
            _qt_event_type("ShortcutOverride"),
            _qt_event_type("KeyPress"),
            _qt_event_type("KeyRelease"),
        }
        self._mouse_release_type = _qt_event_type("MouseButtonRelease")
        self._nav_keys = {
            _qt_key("Key_Comma"),
            _qt_key("Key_Period"),
            _qt_key("Key_Semicolon"),
            _qt_key("Key_Less"),
            _qt_key("Key_Greater"),
        }
        self._nav_keys.discard(None)
        self._ctrl_modifier = _qt_keyboard_modifier("ControlModifier")
        self._undo_keys = {_qt_key("Key_Z"), _qt_key("Key_Y")}
        self._undo_keys.discard(None)

    def eventFilter(self, obj, event):
        try:
            event_type = event.type()
            if event_type in self._nav_event_types:
                key = event.key()
                if key in self._nav_keys:
                    _flush_offset_before_time_navigation()
                elif self._ctrl_modifier is not None and event.modifiers() & self._ctrl_modifier:
                    if key in self._undo_keys:
                        _begin_offset_undo_guard()
                        try:
                            QtCore.QTimer.singleShot(350, _finish_offset_undo_guard)
                        except Exception:
                            pass
            elif event_type == self._mouse_release_type:
                _schedule_offset_interaction_flush()
        except Exception:
            pass
        return False


def _start_offset_key_nav_filter():
    global _anim_offset_key_nav_filter
    _stop_offset_key_nav_filter()
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    _anim_offset_key_nav_filter = _OffsetKeyNavigationFilter(app)
    app.installEventFilter(_anim_offset_key_nav_filter)


def _stop_offset_key_nav_filter():
    global _anim_offset_key_nav_filter
    if _anim_offset_key_nav_filter is None:
        return
    app = QtWidgets.QApplication.instance()
    if app is not None:
        try:
            app.removeEventFilter(_anim_offset_key_nav_filter)
        except Exception:
            pass
    _anim_offset_key_nav_filter = None


class TimelineOverlay(QtWidgets.QWidget):
    """
    Transparent overlay widget that sits on top of Maya's timeline
    and draws a yellow highlight over the selected time range.
    """
    
    def __init__(self, parent=None):
        super(TimelineOverlay, self).__init__(parent)
        self._time_range = None
        self._is_full_timeline = False
        self._opacity = 0.40
        self._fade_step_amount = 0.04

        self._fade_delay_timer = QtCore.QTimer(self)
        self._fade_delay_timer.setSingleShot(True)
        self._fade_delay_timer.timeout.connect(self._start_fade)

        self._fade_timer = QtCore.QTimer(self)
        self._fade_timer.setInterval(15)
        self._fade_timer.timeout.connect(self._fade_step)
        
        # Make widget transparent and click-through
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        if parent:
            parent.installEventFilter(self)
            self.setGeometry(0, 0, parent.width(), parent.height())
        
        self._highlight_color = QtGui.QColor("#d08770")

    def eventFilter(self, obj, event):
        try:
            resize_event = QtCore.QEvent.Type.Resize
        except AttributeError:
            resize_event = QtCore.QEvent.Resize

        if obj == self.parent() and event.type() == resize_event:
            self.setGeometry(0, 0, obj.width(), obj.height())
        return False
        
    def set_time_range(self, start_time, end_time, is_full_timeline=False):
        """Set the time range to highlight."""
        self._fade_delay_timer.stop()
        self._fade_timer.stop()

        start, end = _normalize_inclusive_time_range([start_time, end_time])
        self._time_range = (start, end)
        self._is_full_timeline = is_full_timeline
        self._opacity = 0.40
        self.show()
        self.raise_()
        self.update()

    def trigger_fade(self, delay=0, fast=True):
        """Fade the overlay out instead of disappearing abruptly."""
        self._fade_delay_timer.stop()
        self._fade_timer.stop()

        if fast:
            self._fade_timer.setInterval(15)
            self._fade_step_amount = 0.04
        else:
            self._fade_timer.setInterval(30)
            self._fade_step_amount = 0.015

        if delay > 0:
            self._fade_delay_timer.start(delay)
        else:
            self._start_fade()

    def _start_fade(self):
        self._fade_timer.start()

    def _fade_step(self):
        self._opacity -= self._fade_step_amount
        if self._opacity <= 0:
            self._opacity = 0
            self._fade_timer.stop()
            self._time_range = None
            self.hide()
        self.update()
        
    def paintEvent(self, event):
        """Draw the yellow highlight on the timeline range."""
        if not self._time_range:
            return
            
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        
        color = QtGui.QColor(self._highlight_color)
        color.setAlphaF(max(0.0, min(1.0, self._opacity)))

        # Get timeline visible range
        min_visible = cmds.playbackOptions(query=True, minTime=True)
        max_visible = cmds.playbackOptions(query=True, maxTime=True)
        
        min_frame = float(min_visible)
        max_frame = float(max_visible)
        total_frame_cells = max_frame - min_frame + 1.0
        if total_frame_cells <= 0:
            return
            
        # Calculate pixel positions
        widget_width = self.width()
        widget_height = self.height()
        
        if self._is_full_timeline:
            # Highlight entire widget
            x_start = 0
            x_end = widget_width
        else:
            # Match Animo's marker math: Maya's frame strip has a small
            # proportional gutter, so use 0.5% left/right padding instead of
            # a fixed pixel nudge.
            range_start = max(float(_round_frame(self._time_range[0])), min_frame)
            range_end = min(float(_round_frame(self._time_range[1])), max_frame)
            if range_end < range_start:
                painter.end()
                return

            step = (widget_width - (widget_width * 0.01)) / total_frame_cells
            x_start = (range_start - min_frame) * step + (widget_width * 0.005)
            x_end = (range_end - min_frame + 1.0) * step + (widget_width * 0.005)
        
        highlight_width = max(1.0, x_end - x_start)

        # Draw the yellow highlight rectangle
        painter.fillRect(QtCore.QRectF(x_start, 0, highlight_width, widget_height), color)
        
        painter.end()


def get_timeline_widget():
    """Find and return Maya's timeline widget."""
    try:
        # Get the time slider control name
        time_slider_name = mel.eval('$tmpVar=$gPlayBackSlider')
        
        # Get the widget pointer
        ptr = omui.MQtUtil.findControl(time_slider_name)
        if ptr:
            return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception as e:
        print(f"Could not find timeline widget: {e}")
    return None


def get_timeline_paint_widget():
    """Return the child widget that Maya uses for the visible frame strip."""
    timeline_widget = get_timeline_widget()
    if not timeline_widget:
        return None

    try:
        for child in timeline_widget.children():
            if isinstance(child, QtWidgets.QWidget) and not isinstance(child, TimelineOverlay):
                return child
    except Exception:
        pass

    return timeline_widget



def _as_scalar(value):
    """Convert a value to scalar, handling single-element lists/tuples."""
    v = value
    while isinstance(v, (list, tuple)) and len(v) == 1:
        v = v[0]
    if isinstance(v, (list, tuple)):
        return None, False
    return v, True


def _selected_objects_long():
    """Return the current Maya selection using long DAG paths."""
    return cmds.ls(selection=True, long=True) or []


def _round_frame(value):
    """Round Maya time values to the nearest whole frame."""
    value = float(value)
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _normalize_inclusive_time_range(time_range):
    """Return an ordered inclusive [start, end] range."""
    start = float(time_range[0])
    end = float(time_range[1])
    if end < start:
        start, end = end, start
    return [start, end]


def _normalize_time_slider_range(time_range):
    """
    Convert Maya's timeControl range to an inclusive whole-frame range.

    The time slider stores the right edge of a dragged range, so selecting
    frames 10-20 usually comes back as [10, 21]. Internally we use [10, 20].
    """
    start, end = time_slider_range_to_inclusive(time_range)
    return [start, end]


def _time_slider_range_from_inclusive(time_range):
    """Convert an inclusive internal range back to Maya timeControl edges."""
    start, end = _normalize_inclusive_time_range(time_range)
    return [start, end + 1.0]


def _has_key_at_time(obj, attr, frame, epsilon=1e-4):
    """True when an attribute has a key on frame, using Maya float tolerance."""
    try:
        keys = cmds.keyframe(
            obj,
            attribute=attr,
            query=True,
            time=(frame, frame),
        ) or []
    except Exception:
        return False

    return any(abs(float(key) - float(frame)) <= epsilon for key in keys)


def _key_value_at_time(attr_full_name, frame):
    """
    Read a keyed value directly from an animCurve, with getAttr fallback.

    LEGACY/UNUSED in the active offset flow: kept for backward-compat in
    case other parts of the toolbar import it directly. All internal
    call-sites now use _layer_aware_get_value(obj, attr, frame), which
    correctly targets the active session's animation layer curve instead
    of whatever curve happens to be directly connected to the plug.
    """
    try:
        values = cmds.keyframe(
            attr_full_name,
            query=True,
            time=(frame, frame),
            valueChange=True,
        ) or []
        if values:
            return float(values[0]), True
    except Exception:
        pass

    try:
        value, ok = _as_scalar(cmds.getAttr(attr_full_name, time=frame))
        if ok and isinstance(value, (int, float, bool)):
            return float(value), True
    except Exception:
        pass

    return None, False


def _set_key_value_at_time(obj, attr, frame, value):
    """Set one key while suppressing live-value tracking caused by our own edits."""
    global _anim_offset_applying_offset
    previous_state = _anim_offset_applying_offset
    _anim_offset_applying_offset = True
    try:
        cmds.setKeyframe(obj, attribute=attr, time=frame, value=value)
        return True
    except Exception:
        return False
    finally:
        _anim_offset_applying_offset = previous_state


def _has_selected_anim_keys():
    """Return True when Graph Editor/key selections may be driving a slider."""
    try:
        return bool(cmds.keyframe(query=True, selected=True, name=True))
    except Exception:
        return False


def _find_changed_key_diffs(obj, attr, keyframes, applied_diff):
    """
    Find keys inside the orange range that were edited outside transform tools.

    Slider tools such as Tweener edit animCurve keys directly, so the current
    transform value is not always enough to detect the offset driver.
    """
    changed = []

    for frame in keyframes:
        original_value, found = _baseline_value_for_time(obj, attr, frame)
        if not found:
            continue

        current_value, ok = _layer_aware_get_value(obj, attr, frame=frame)
        if not ok:
            continue

        _anim_offset_frozen_baseline.setdefault(obj, {}).setdefault(attr, {})[float(frame)] = float(original_value)
        _animation_offset_original_values.setdefault(obj, {}).setdefault(attr, {})[float(frame)] = float(original_value)

        observed_diff = current_value - float(original_value)
        if abs(observed_diff - float(applied_diff)) > 1e-7:
            changed.append((float(frame), observed_diff))

    if not changed:
        return None, []

    changed.sort(key=lambda item: abs(item[1] - float(applied_diff)), reverse=True)
    desired_diff = changed[0][1]
    protected_frames = [frame for frame, _diff in changed]
    return desired_diff, protected_frames


def _flush_previous_frame_change():
    """
    Commit an edited key at the previous frame after keyboard navigation.

    Comma/period can change Maya's current time before the timer tick runs.
    The previous key is still queryable from the animCurve, so we compare it
    against the frozen baseline and propagate that delta if needed.
    """
    global _chunk_is_open

    if _anim_offset_undo_guard:
        return False
    if _prev_tick_time is None or not _anim_offset_time_range:
        return False

    previous_time = float(_prev_tick_time)
    time_range = _anim_offset_time_range
    if previous_time < time_range[0] - 1e-4 or previous_time > time_range[1] + 1e-4:
        return False

    pending_changes = []

    for obj in _anim_offset_original_selection:
        if not cmds.objExists(obj):
            continue

        frozen_obj_data = _anim_offset_frozen_baseline.get(obj, {})
        if not frozen_obj_data:
            continue

        for attr in frozen_obj_data.keys():
            attr_full_name = obj + "." + attr
            if not _is_offsettable_numeric_attr(attr_full_name):
                continue

            keyframes = _layer_aware_query_keyframes(obj, attr, time_range)
            if not keyframes:
                continue

            original_value, has_baseline = _baseline_value_for_time(obj, attr, previous_time)
            if not has_baseline:
                previous_snapshot_value = _prev_tick_snapshot.get(obj, {}).get(attr)
                if previous_snapshot_value is None:
                    continue
                applied_diff = _anim_offset_diff_cache.get(obj, {}).get(attr, 0.0)
                original_value = float(previous_snapshot_value) - float(applied_diff)
                _anim_offset_frozen_baseline.setdefault(obj, {}).setdefault(attr, {})[previous_time] = original_value
                _animation_offset_original_values.setdefault(obj, {}).setdefault(attr, {})[previous_time] = original_value

            live_value = _live_value_for_time(obj, attr, previous_time)
            current_value, ok = _layer_aware_get_value(obj, attr, frame=previous_time)
            if live_value is not None:
                current_value = live_value
                ok = True
            if not ok:
                continue

            applied_diff = _anim_offset_diff_cache.get(obj, {}).get(attr, 0.0)
            desired_diff = float(current_value) - float(original_value)
            delta_to_apply = desired_diff - float(applied_diff)
            if abs(delta_to_apply) <= 1e-9:
                continue

            driver_value = float(current_value) if live_value is not None else None
            pending_changes.append((obj, attr, keyframes, delta_to_apply, desired_diff, [previous_time], driver_value))

    if not pending_changes:
        return False

    if not _chunk_is_open:
        try:
            cmds.undoInfo(openChunk=True, chunkName="AnimKey_AnimOffset")
            _chunk_is_open = True
        except Exception:
            pass

    applied_any = False
    for obj, attr, keyframes, delta_to_apply, desired_diff, protected_times, driver_value in pending_changes:
        if driver_value is not None:
            _layer_aware_set_key(obj, attr, protected_times[0], driver_value)
        if _apply_key_offset_delta(obj, attr, keyframes, delta_to_apply, protected_times):
            _anim_offset_diff_cache.setdefault(obj, {})[attr] = desired_diff
            applied_any = True

    return applied_any


def _on_offset_time_changed(*args):
    global _anim_offset_syncing_time
    if _anim_offset_syncing_time:
        return
    if _anim_offset_undo_guard:
        return
    if not _anim_offset_active or not _anim_offset_run_timer:
        return

    _anim_offset_syncing_time = True
    try:
        _flush_previous_frame_change()
        adjust_keyframes(scan_changed_keys=False)
        _clear_time_slider_range_to_current()
        if _timeline_overlay is not None:
            _timeline_overlay.raise_()
            _timeline_overlay.update()
    finally:
        _anim_offset_syncing_time = False


def _is_offsettable_numeric_attr(attr_full_name):
    """True for unlocked scalar numeric attrs that can safely be offset."""
    try:
        if cmds.getAttr(attr_full_name, lock=True):
            return False

        attr_type = cmds.getAttr(attr_full_name, type=True)
        if attr_type in ("enum", "string", "message", "matrix", "TdataCompound"):
            return False

        value, ok = _as_scalar(cmds.getAttr(attr_full_name))
        if not ok:
            return False

        return isinstance(value, (int, float, bool))
    except Exception:
        return False


def _query_keyframes(obj, attr, time_range):
    """
    Return sorted unique keyframes for one attr inside time_range.

    LEGACY/UNUSED in the active offset flow: kept for backward-compat in
    case other parts of the toolbar import it directly. All internal
    call-sites now use _layer_aware_query_keyframes(obj, attr, time_range),
    which lists keys from the active session's animation layer curve
    instead of every curve touching the attribute regardless of layer.
    """
    try:
        keyframes = cmds.keyframe(
            obj,
            attribute=attr,
            query=True,
            time=(time_range[0], time_range[1]),
        ) or []
    except Exception:
        return []

    return sorted({float(frame) for frame in keyframes if time_range[0] <= frame <= time_range[1]})


def _value_at_key_time(frame_values, frame, epsilon=1e-4):
    """Return the stored value for a key at frame, using a float tolerance."""
    for key_time, value in frame_values.items():
        if abs(float(key_time) - float(frame)) <= epsilon:
            return value, True
    return None, False


def _sample_evaluated_baseline(attr_full_name, time_range):
    """
    Sample original evaluated values on whole frames inside time_range.

    LEGACY/UNUSED in the active offset flow: kept for backward-compat.
    _track_object_for_offset now calls _sample_evaluated_baseline_layer_aware
    instead, which samples the target animation layer's own curve rather
    than the layer-composited attribute value.
    """
    sample_values = {}
    start = int(math.floor(time_range[0]))
    end = int(math.ceil(time_range[1]))

    # Large ranges are still supported, but avoid making activation feel frozen
    # on very long shots by sampling a reasonable ceiling.
    if end - start > 5000:
        return sample_values

    for frame in range(start, end + 1):
        if frame < time_range[0] or frame > time_range[1]:
            continue
        try:
            value, ok = _as_scalar(cmds.getAttr(attr_full_name, time=frame))
        except Exception:
            continue
        if ok and isinstance(value, (int, float, bool)):
            sample_values[float(frame)] = float(value)
    return sample_values


def _sample_evaluated_baseline_layer_aware(obj, attr, time_range):
    """
    Layer-aware twin of _sample_evaluated_baseline: samples the TARGET
    layer's own curve (interpolated where there's no key), instead of the
    final composited attribute value. With no animation layers in the
    scene this produces identical results to the original function.
    """
    sample_values = {}
    start = int(math.floor(time_range[0]))
    end = int(math.ceil(time_range[1]))

    if end - start > 5000:
        return sample_values

    for frame in range(start, end + 1):
        if frame < time_range[0] or frame > time_range[1]:
            continue
        value, ok = _layer_aware_get_value(obj, attr, frame=float(frame))
        if ok:
            sample_values[float(frame)] = float(value)
    return sample_values


def _baseline_value_for_time(obj, attr, frame):
    """Get the original baseline value at frame from key or sampled data."""
    frozen_values = _anim_offset_frozen_baseline.get(obj, {}).get(attr, {})
    value, found = _value_at_key_time(frozen_values, frame)
    if found:
        return value, True

    sampled_values = _anim_offset_evaluated_baseline.get(obj, {}).get(attr, {})
    return _value_at_key_time(sampled_values, frame)


def _snapshot_tracked_values(objects_to_process, current_time):
    """Snapshot current scalar values for all tracked attrs."""
    snapshot = {}
    for obj in objects_to_process:
        if not cmds.objExists(obj):
            continue
        tracked_attrs = _anim_offset_frozen_baseline.get(obj, {})
        for attr in tracked_attrs.keys():
            value, ok = _layer_aware_get_value(obj, attr, frame=current_time)
            if ok:
                snapshot.setdefault(obj, {})[attr] = float(value)
    return snapshot


def _track_object_for_offset(obj, time_range, activation_time):
    """Snapshot one object so Animation Offset can process it robustly."""
    if not cmds.objExists(obj):
        return False

    attrs = cmds.listAttr(obj, keyable=True, scalar=True) or []
    if not attrs:
        return False

    obj_values = {}
    obj_activation_values = {}
    obj_evaluated_values = {}

    for attr in attrs:
        attr_full_name = obj + "." + attr
        if not _is_offsettable_numeric_attr(attr_full_name):
            continue

        # Layer-aware: only track this attr if the TARGET layer actually
        # owns a curve for it. An attribute animated only on a different
        # layer (or only on the base layer while we're targeting another
        # layer) is correctly skipped here, instead of being read from the
        # composited plug value like the old code did.
        curve = _get_target_curve(obj, attr, use_cache=False)
        if curve is None and _has_any_animation_layers():
            continue

        keyframes = _layer_aware_query_keyframes(obj, attr, time_range)
        if not keyframes:
            continue

        frame_values = {}
        for frame in keyframes:
            value, ok = _layer_aware_get_value(obj, attr, frame=frame)
            if ok:
                frame_values[frame] = float(value)

        if not frame_values:
            continue

        activation_value, ok = _layer_aware_get_value(obj, attr, frame=activation_time)

        if not ok:
            continue

        obj_values[attr] = frame_values
        obj_activation_values[attr] = float(activation_value)
        obj_evaluated_values[attr] = _sample_evaluated_baseline_layer_aware(obj, attr, time_range)

    if not obj_values:
        return False

    _animation_offset_original_values[obj] = {
        attr: dict(frame_values)
        for attr, frame_values in obj_values.items()
    }
    _anim_offset_frozen_baseline[obj] = {
        attr: dict(frame_values)
        for attr, frame_values in obj_values.items()
    }
    _anim_offset_activation_values[obj] = dict(obj_activation_values)
    _anim_offset_evaluated_baseline[obj] = {
        attr: dict(frame_values)
        for attr, frame_values in obj_evaluated_values.items()
    }
    _anim_offset_diff_cache.setdefault(obj, {})
    return True



def _resolve_tracked_object(obj_name):
    """Resolve a slider plug object name to one of the tracked long DAG paths."""
    if not obj_name:
        return None

    candidates = set()
    try:
        candidates.update(cmds.ls(obj_name, long=True) or [])
    except Exception:
        pass
    candidates.add(obj_name)

    obj_short = obj_name.split("|")[-1]
    for tracked_obj in _anim_offset_original_selection:
        if tracked_obj in candidates:
            return tracked_obj
        if tracked_obj.split("|")[-1] == obj_short:
            return tracked_obj

    return None


def _coerce_float(value):
    """Return a float scalar or None."""
    value, ok = _as_scalar(value)
    if ok and isinstance(value, (int, float, bool)):
        return float(value)
    return None


def apply_slider_offset_changes(changes):
    """
    Propagate slider-authored keys through the active offset range.

    Tweener can create a key on an unkeyed frame after using setAttr as a
    preview. Passing the slider's original/final values here lets the offset
    use that new key as the protected anchor immediately.
    """
    session = get_active_session()
    if session is None:
        return False
    return session.apply_slider_changes(changes)

    global _chunk_is_open, _prev_tick_snapshot, _prev_tick_time

    if not _anim_offset_active or not _anim_offset_run_timer:
        return False
    if _anim_offset_undo_guard:
        return False
    if not _anim_offset_time_range or not changes:
        return False

    time_range = _anim_offset_time_range
    pending_changes = []

    for change in changes:
        try:
            attr_full = change.get("attr_full")
            frame = float(change.get("frame"))
            original_value = _coerce_float(change.get("original_value"))
            new_value = _coerce_float(change.get("new_value"))
        except Exception:
            continue

        if original_value is None or new_value is None:
            continue
        if abs(new_value - original_value) <= 1e-9:
            continue
        if frame < time_range[0] - 1e-4 or frame > time_range[1] + 1e-4:
            continue
        if not attr_full or "." not in attr_full:
            continue

        obj_name, attr = attr_full.rsplit(".", 1)
        obj = _resolve_tracked_object(obj_name)
        if not obj or not cmds.objExists(obj):
            continue

        frozen_obj_data = _anim_offset_frozen_baseline.get(obj, {})
        if attr not in frozen_obj_data:
            continue

        attr_full_name = obj + "." + attr
        if not _is_offsettable_numeric_attr(attr_full_name):
            continue

        if not _layer_aware_has_key(obj, attr, frame):
            if not _layer_aware_set_key(obj, attr, frame, new_value):
                continue

        keyframes = _layer_aware_query_keyframes(obj, attr, time_range)
        if not keyframes:
            continue

        _anim_offset_frozen_baseline.setdefault(obj, {}).setdefault(attr, {})[float(frame)] = original_value
        _animation_offset_original_values.setdefault(obj, {}).setdefault(attr, {})[float(frame)] = original_value

        desired_diff = new_value - original_value
        applied_diff = _anim_offset_diff_cache.get(obj, {}).get(attr, 0.0)
        delta_to_apply = desired_diff - applied_diff
        if abs(delta_to_apply) <= 1e-9:
            continue

        pending_changes.append((obj, attr, keyframes, delta_to_apply, desired_diff, [frame]))

    if not pending_changes:
        return False

    opened_chunk_here = False
    if not _chunk_is_open:
        try:
            cmds.undoInfo(openChunk=True, chunkName="AnimKey_AnimOffset")
            _chunk_is_open = True
            opened_chunk_here = True
        except Exception:
            pass

    applied_any = False
    for obj, attr, keyframes, delta_to_apply, desired_diff, protected_times in pending_changes:
        if _apply_key_offset_delta(obj, attr, keyframes, delta_to_apply, protected_times):
            _anim_offset_diff_cache.setdefault(obj, {})[attr] = desired_diff
            applied_any = True

    current_time = cmds.currentTime(query=True)
    _prev_tick_snapshot = _snapshot_tracked_values(_anim_offset_original_selection, current_time)
    _prev_tick_time = current_time

    if opened_chunk_here and _chunk_is_open:
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        _chunk_is_open = False

    return applied_any


def _apply_key_offset_delta(obj, attr, keyframes, delta, protected_times=None):
    """
    Apply only the new delta to the animCurve values.

    Protected keys have already been moved by the user/slider. They are skipped
    to avoid doubling the offset, while all other keys receive the delta.

    Layer-aware: delegates to _layer_aware_apply_delta, which targets the
    animCurve belonging to the frozen session's target animation layer
    instead of the attribute plug. With no animation layers in the scene
    this resolves to the plug anyway, so behavior is unchanged for scenes
    without layers.
    """
    return _layer_aware_apply_delta(obj, attr, keyframes, delta, protected_times)


def store_keyframes():
    """
    Store original keyframe values in the selected time range.
    This is used as reference to calculate offset when user moves the object.
    Also records the original selection with long DAG paths so duplicate
    referenced rigs are handled without name ambiguity.

    Animation-layer aware: the animation layer selected in the Animation
    Layer editor at this moment (BaseAnimation if none, or whatever Maya
    considers the preferred layer) becomes the FROZEN target for the whole
    offset session. All reads/writes for this session go through that
    layer's own curves, never the layer-composited attribute value, so
    using Animation Offset on a non-base layer no longer corrupts the
    composited result.
    """
    global _animation_offset_original_values, _anim_offset_time_range
    global _anim_offset_original_selection, _anim_offset_diff_cache
    global _anim_offset_frozen_baseline, _anim_offset_activation_values
    global _anim_offset_evaluated_baseline
    global _prev_tick_snapshot, _prev_tick_time
    global _anim_offset_target_layer, _anim_offset_target_curve_cache
    
    # Clear previous values
    _animation_offset_original_values.clear()
    _anim_offset_diff_cache.clear()
    _anim_offset_frozen_baseline.clear()
    _anim_offset_activation_values.clear()
    _anim_offset_evaluated_baseline.clear()
    _prev_tick_snapshot.clear()
    _anim_offset_live_values.clear()
    _prev_tick_time = None

    # Freeze the target animation layer for this whole session. Switching
    # the selected layer in the editor mid-drag must NOT retarget an
    # already-active offset; the user has to stop, reselect, and start
    # again - exactly like changing object selection works today.
    _anim_offset_target_layer = _get_selected_animation_layer()
    _anim_offset_target_curve_cache.clear()
    
    # Get selected time range from Range Slider
    aTimeSlider = mel.eval('$tmpVar=$gPlayBackSlider')
    timeRange = cmds.timeControl(aTimeSlider, q=True, rangeArray=True)
    
    # If no range is selected, use entire timeline range
    if abs(float(timeRange[1]) - float(timeRange[0])) <= 1.0001:
        timeRange = _normalize_inclusive_time_range([
            cmds.playbackOptions(q=True, minTime=True),
            cmds.playbackOptions(q=True, maxTime=True),
        ])
    else:
        timeRange = _normalize_time_slider_range(timeRange)
    
    # Save time range
    _anim_offset_time_range = timeRange.copy()
    
    selected_objects = _selected_objects_long()
    
    if not selected_objects:
        return
    
    _anim_offset_original_selection = []
    activation_time = cmds.currentTime(query=True)

    for obj in selected_objects:
        if _track_object_for_offset(obj, timeRange, activation_time):
            _anim_offset_original_selection.append(obj)

    _prev_tick_snapshot = _snapshot_tracked_values(_anim_offset_original_selection, activation_time)
    _prev_tick_time = activation_time
    
    # Restore original selection
    if selected_objects:
        cmds.select(selected_objects, replace=True)


def adjust_keyframes(scan_changed_keys=False):
    """
    Adjust keyframes by applying only the newly changed offset delta.

    Long DAG paths avoid duplicate-reference ambiguity, and relative key edits
    preserve animCurve tangents better than rebuilding keys with setKeyframe.
    """
    session = get_active_session()
    if session is None:
        return False
    return session.commit_dirty(
        reason="legacy_adjust",
        scan_changed_keys=bool(scan_changed_keys),
    )

    global _animation_offset_original_values, _anim_offset_time_range
    global _anim_offset_original_selection, _anim_offset_diff_cache
    global _anim_offset_frozen_baseline, _anim_offset_activation_values
    global _prev_tick_snapshot, _prev_tick_time, _chunk_is_open

    if not _anim_offset_run_timer:
        return
    if _anim_offset_undo_guard:
        return

    if not _anim_offset_time_range:
        return

    timeRange = _anim_offset_time_range
    current_time = cmds.currentTime(query=True)

    # Robust incremental path:
    # - long DAG paths keep duplicate referenced rigs unambiguous
    # - newly selected controls can join while the mode stays active
    # - relative key edits preserve tangent data better than setKeyframe
    # - only the newly changed diff is applied, so offsets cannot compound
    newly_tracked_objects = []
    current_selection = _selected_objects_long()
    for obj in current_selection:
        if obj not in _anim_offset_original_selection and _track_object_for_offset(obj, timeRange, current_time):
            _anim_offset_original_selection.append(obj)
            newly_tracked_objects.append(obj)

    if newly_tracked_objects:
        new_snapshot = _snapshot_tracked_values(newly_tracked_objects, current_time)
        for obj, attrs_dict in new_snapshot.items():
            _prev_tick_snapshot.setdefault(obj, {}).update(attrs_dict)

    objects_to_process = _anim_offset_original_selection
    if not objects_to_process:
        return

    same_time_as_previous = (
        _prev_tick_time is not None and
        abs(float(current_time) - float(_prev_tick_time)) <= 1e-4
    )

    current_tick_snapshot = _snapshot_tracked_values(objects_to_process, current_time)
    allow_changed_key_scan = bool(scan_changed_keys)
    pending_changes = []

    for obj in objects_to_process:
        if not cmds.objExists(obj):
            continue

        frozen_obj_data = _anim_offset_frozen_baseline.get(obj, {})
        if not frozen_obj_data:
            continue

        for attr, frozen_values in frozen_obj_data.items():
            if not frozen_values:
                continue

            attr_full_name = obj + "." + attr
            if not _is_offsettable_numeric_attr(attr_full_name):
                continue

            current_value = current_tick_snapshot.get(obj, {}).get(attr)
            if current_value is None:
                continue

            applied_diff = _anim_offset_diff_cache.get(obj, {}).get(attr, 0.0)
            original_value, has_baseline_at_current = _baseline_value_for_time(obj, attr, current_time)
            has_key_at_current = _layer_aware_has_key(obj, attr, current_time)

            if not has_baseline_at_current:
                previous_value = _prev_tick_snapshot.get(obj, {}).get(attr)

                if has_key_at_current:
                    if same_time_as_previous and previous_value is not None:
                        original_value = float(previous_value) - applied_diff
                    else:
                        original_value = current_value - applied_diff

                    _anim_offset_frozen_baseline.setdefault(obj, {}).setdefault(attr, {})[float(current_time)] = float(original_value)
                    _animation_offset_original_values.setdefault(obj, {}).setdefault(attr, {})[float(current_time)] = float(original_value)
                    has_baseline_at_current = True
                elif same_time_as_previous and previous_value is not None:
                    movement_delta = current_value - float(previous_value)
                    if abs(movement_delta) > 1e-9:
                        original_value = float(previous_value) - applied_diff
                        _anim_offset_evaluated_baseline.setdefault(obj, {}).setdefault(attr, {})[float(current_time)] = float(original_value)
                        has_baseline_at_current = True

            protected_times = [current_time]

            if has_baseline_at_current:
                desired_diff = current_value - float(original_value)
            else:
                desired_diff = applied_diff

            keyframes = _layer_aware_query_keyframes(obj, attr, timeRange)
            if not keyframes:
                continue

            delta_to_apply = desired_diff - applied_diff
            if abs(delta_to_apply) <= 1e-9:
                if not allow_changed_key_scan:
                    continue

                slider_diff, changed_frames = _find_changed_key_diffs(obj, attr, keyframes, applied_diff)
                if not changed_frames:
                    continue
                desired_diff = slider_diff
                protected_times = changed_frames
                delta_to_apply = desired_diff - applied_diff
                if abs(delta_to_apply) <= 1e-9:
                    continue

            pending_changes.append((obj, attr, keyframes, delta_to_apply, desired_diff, protected_times))

    if pending_changes:
        if not _chunk_is_open:
            try:
                cmds.undoInfo(openChunk=True, chunkName="AnimKey_AnimOffset")
                _chunk_is_open = True
            except Exception:
                pass

        for obj, attr, keyframes, delta_to_apply, desired_diff, protected_times in pending_changes:
            if not _anim_offset_run_timer:
                break
            if _apply_key_offset_delta(obj, attr, keyframes, delta_to_apply, protected_times):
                _anim_offset_diff_cache.setdefault(obj, {})[attr] = desired_diff
    else:
        if _chunk_is_open:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass
            _chunk_is_open = False

    _prev_tick_snapshot = _snapshot_tracked_values(objects_to_process, current_time)
    _prev_tick_time = current_time


def show_anim_offset_timeline_bar():
    """
    Shows visual indication of the active Animation Offset range.
    Creates a yellow overlay on top of the timeline for the selected range only.
    """
    global _anim_offset_time_range, _timeline_overlay
    
    if not _anim_offset_time_range:
        return
    
    # Get the visible frame strip widget, following the same approach as Animo.
    timeline_widget = get_timeline_paint_widget()
    if not timeline_widget:
        print("AnimKey: Could not find timeline widget for overlay")
        return
    
    # Check if using full timeline or specific range
    min_time = cmds.playbackOptions(query=True, minTime=True)
    max_time = cmds.playbackOptions(query=True, maxTime=True)
    
    is_full_timeline = (
        abs(_anim_offset_time_range[0] - min_time) < 0.01 and 
        abs(_anim_offset_time_range[1] - max_time) < 0.01
    )
    
    # Create or update overlay
    if _timeline_overlay is None:
        _timeline_overlay = TimelineOverlay(timeline_widget)
    
    # Position and size the overlay to match the visible frame strip widget.
    _timeline_overlay.setGeometry(0, 0, timeline_widget.width(), timeline_widget.height())
    _timeline_overlay.set_time_range(
        _anim_offset_time_range[0], 
        _anim_offset_time_range[1],
        is_full_timeline=is_full_timeline
    )
    _timeline_overlay.show()
    _timeline_overlay.raise_()
    
    # Keep Maya's real blue range clear. The orange overlay carries the
    # offset range without changing comma/period key navigation behavior.
    _clear_time_slider_range_to_current()


def hide_anim_offset_timeline_bar():
    """
    Hides the timeline overlay and clears the range selection.
    """
    global _timeline_overlay
    
    # Fade and delete the overlay widget after the opacity animation.
    if _timeline_overlay is not None:
        overlay = _timeline_overlay
        _timeline_overlay = None
        try:
            overlay.trigger_fade(delay=0, fast=True)
            QtCore.QTimer.singleShot(350, overlay.deleteLater)
        except:
            try:
                overlay.hide()
                overlay.deleteLater()
            except:
                pass
    
    # Clear the timeline range selection
    _clear_time_slider_range_to_current()


def offset_animation_deferred(interval, generation):
    """
    Execute keyframe adjustment in a separate thread with specific interval.
    This allows offset to update continuously while user moves the object.
    """
    # Legacy entry point. Engine revision 2 is event-driven and has no polling
    # thread, so direct external calls should do nothing.
    return

    global _anim_offset_run_timer
    
    def adjust_offset_animation():
        if not _is_current_offset_generation(generation):
            return
        try:
            adjust_keyframes(scan_changed_keys=False)
        except Exception:
            # Silence errors to avoid spam in script editor
            pass
    
    while _anim_offset_run_timer and _is_current_offset_generation(generation):
        time.sleep(interval)
        if _anim_offset_run_timer and _is_current_offset_generation(generation):  # Check again before executing
            try:
                # Use Maya's deferred execution
                import maya.utils as utils
                utils.executeDeferred(adjust_offset_animation)
            except:
                adjust_offset_animation()


def _selected_offset_time_range():
    """Return the inclusive Animation Offset range from Maya's time slider."""
    try:
        aTimeSlider = mel.eval('$tmpVar=$gPlayBackSlider')
        time_range = cmds.timeControl(aTimeSlider, q=True, rangeArray=True)
        range_visible = cmds.timeControl(
            aTimeSlider,
            q=True,
            rangeVisible=True,
        )
    except Exception:
        time_range = None
        range_visible = None

    if not time_range:
        return _normalize_inclusive_time_range([
            cmds.playbackOptions(q=True, minTime=True),
            cmds.playbackOptions(q=True, maxTime=True),
        ])

    if range_visible is False:
        return _normalize_inclusive_time_range([
            cmds.playbackOptions(q=True, minTime=True),
            cmds.playbackOptions(q=True, maxTime=True),
        ])
    if range_visible is None and abs(float(time_range[1]) - float(time_range[0])) <= 1.0001:
        return _normalize_inclusive_time_range([
            cmds.playbackOptions(q=True, minTime=True),
            cmds.playbackOptions(q=True, maxTime=True),
        ])
    return _normalize_time_slider_range(time_range)


def _clear_legacy_offset_state():
    """Clear globals kept for compatibility with older AnimKey call-sites."""
    global _anim_offset_active, _anim_offset_thread, _anim_offset_run_timer
    global _animation_offset_original_values, _anim_offset_time_range
    global _anim_offset_original_selection, _anim_offset_diff_cache
    global _anim_offset_frozen_baseline, _anim_offset_activation_values
    global _anim_offset_evaluated_baseline
    global _prev_tick_time, _anim_offset_syncing_time
    global _anim_offset_flush_scheduled
    global _anim_offset_applying_offset
    global _anim_offset_target_layer, _anim_offset_target_curve_cache
    global _chunk_is_open

    _anim_offset_active = False
    _anim_offset_run_timer = False
    _anim_offset_thread = None
    _animation_offset_original_values.clear()
    _anim_offset_frozen_baseline.clear()
    _anim_offset_activation_values.clear()
    _anim_offset_evaluated_baseline.clear()
    _anim_offset_time_range = None
    _anim_offset_original_selection = []
    _anim_offset_diff_cache.clear()
    _prev_tick_snapshot.clear()
    _anim_offset_live_values.clear()
    _prev_tick_time = None
    _anim_offset_syncing_time = False
    _anim_offset_flush_scheduled = False
    _anim_offset_applying_offset = False
    _anim_offset_target_layer = None
    _anim_offset_target_curve_cache.clear()
    _chunk_is_open = False


def _tracked_objects_from_session(session):
    seen = set()
    objects = []
    for track in session.tracks.values():
        if track.node_path in seen:
            continue
        seen.add(track.node_path)
        objects.append(track.node_path)
    return objects


def _execute_session_v2(button=None):
    """Toggle the event-driven Animation Offset engine."""
    global _anim_offset_active, _anim_offset_run_timer
    global _anim_offset_button_ref, _anim_offset_time_range
    global _anim_offset_original_selection, _anim_offset_target_layer

    if button is not None:
        _anim_offset_button_ref = button
    active_button = button if button is not None else _anim_offset_button_ref

    session = get_active_session()
    if session is not None:
        session.stop(commit=True)
        _stop_offset_key_nav_filter()
        _stop_offset_time_job()
        _stop_offset_undo_jobs()
        _stop_offset_attr_jobs()
        hide_anim_offset_timeline_bar()
        _clear_legacy_offset_state()
        set_button_active(active_button, False)
        _anim_offset_button_ref = None
        return

    selection = _selected_objects_long()
    if not selection:
        cmds.warning("AnimKey: Please select at least one object.")
        return

    has_keyframes = False
    for obj in selection:
        try:
            if cmds.objExists(obj) and cmds.keyframe(obj, query=True):
                has_keyframes = True
                break
        except Exception:
            continue

    if not has_keyframes:
        cmds.warning("AnimKey: Selected objects do not have any keyframes.")
        return

    _stop_offset_key_nav_filter()
    _stop_offset_time_job()
    _stop_offset_undo_jobs()
    _stop_offset_attr_jobs()
    _clear_legacy_offset_state()

    time_range = _selected_offset_time_range()
    target_layer = _session_selected_animation_layer()
    new_session = OffsetSession()

    try:
        new_session.start(selection, time_range, target_layer=target_layer)
    except RuntimeError:
        if _has_any_animation_layers():
            cmds.warning(
                "AnimKey: Selected objects have no keyframes on the active "
                "animation layer ({0}) within the selected time range."
                .format(target_layer or "BaseAnimation")
            )
        else:
            cmds.warning("AnimKey: No keyframes found in the selected time range.")
        set_button_active(active_button, False)
        return
    except Exception as exc:
        cmds.warning("AnimKey: Animation Offset could not start: {0}".format(exc))
        set_button_active(active_button, False)
        return

    _anim_offset_active = True
    _anim_offset_run_timer = True
    _anim_offset_time_range = list(new_session.time_range)
    _anim_offset_target_layer = target_layer
    _anim_offset_original_selection = _tracked_objects_from_session(new_session)

    show_anim_offset_timeline_bar()
    set_button_active(active_button, True)


def execute(*args, button=None):
    """
    Main function to execute when button is clicked.
    Toggles Animation Offset mode on/off.

    FIX 1: The button reference is stored globally so that deactivation
    triggered by a hotkey (button=None) can still reset the highlight.
    FIX 3: Thread is no longer joined on the main thread; we just signal
    it to stop and let it die on its own, preventing UI freezes.
    
    Args:
        button: Optional Qt button widget to update visual state
    """
    global _anim_offset_active, _anim_offset_thread, _anim_offset_run_timer
    global _animation_offset_original_values, _anim_offset_time_range
    global _anim_offset_original_selection, _anim_offset_diff_cache
    global _anim_offset_frozen_baseline, _anim_offset_activation_values, _chunk_is_open
    global _anim_offset_evaluated_baseline
    global _prev_tick_time, _anim_offset_syncing_time
    global _anim_offset_button_ref
    global _anim_offset_flush_scheduled
    global _anim_offset_applying_offset
    global _anim_offset_generation
    global _anim_offset_target_layer, _anim_offset_target_curve_cache

    return _execute_session_v2(button=button)

    # FIX 1: Update the stored button reference whenever a real widget is given.
    # This keeps the reference current even if the toolbar is rebuilt.
    if button is not None:
        _anim_offset_button_ref = button

    # Resolve which button widget to operate on (prefer the live arg, fall back
    # to the globally stored reference so hotkey calls can still toggle the UI).
    active_button = button if button is not None else _anim_offset_button_ref
    
    # ── DEACTIVATE: If already active, always allow turning off ──────────
    if _anim_offset_active:
        # Invalidate the current generation FIRST so any pending deferred
        # callbacks (from executeDeferred / QTimer.singleShot) are discarded
        # before they can apply stale diffs to the scene.
        _anim_offset_run_timer = False
        _new_offset_generation()
        _anim_offset_active = False

        # Now flush is safe – the generation guard prevents re-entry from
        # old deferred calls that might still be queued.
        _flush_offset_before_time_navigation()

        _anim_offset_syncing_time = False
        _stop_offset_key_nav_filter()
        _stop_offset_time_job()
        _stop_offset_undo_jobs()
        _stop_offset_attr_jobs()
        
        # FIX 3: Do NOT join() the thread here – that would block Maya's main
        # thread for up to 1 second.  The daemon flag means it will exit on
        # its own once _anim_offset_run_timer is False.
        # (thread reference is intentionally left; it self-terminates.)
        
        # Hide yellow timeline bar
        hide_anim_offset_timeline_bar()
        
        # Close the drag chunk if one is still open
        if _chunk_is_open:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass
            _chunk_is_open = False
        
        # Clear stored values and all caches
        _animation_offset_original_values.clear()
        _anim_offset_frozen_baseline.clear()
        _anim_offset_activation_values.clear()
        _anim_offset_evaluated_baseline.clear()
        _anim_offset_time_range = None
        _anim_offset_original_selection = []
        _anim_offset_diff_cache.clear()
        _prev_tick_snapshot.clear()
        _anim_offset_live_values.clear()
        _prev_tick_time = None
        _anim_offset_syncing_time = False
        _anim_offset_flush_scheduled = False
        _anim_offset_applying_offset = False
        # Release the frozen animation-layer target and any cached curve
        # resolutions, so the NEXT activation re-reads whatever layer is
        # selected in the editor at that time (base, a different layer, etc).
        _anim_offset_target_layer = None
        _anim_offset_target_curve_cache.clear()
        
        # FIX 1: Update button using resolved reference (works for hotkeys too)
        set_button_active(active_button, False)
        # Clear the stored ref so a future activation stores the new one fresh
        _anim_offset_button_ref = None
        return
    
    # ── ACTIVATE: Validate selection first ───────────────────────────────
    selection = _selected_objects_long()
    
    if not selection:
        cmds.warning("AnimKey: Please select at least one object.")
        return
    
    # Check that selected objects have keyframes
    has_keyframes = False
    for obj in selection:
        if cmds.objExists(obj):
            keyframes = cmds.keyframe(obj, query=True)
            if keyframes:
                has_keyframes = True
                break
    
    if not has_keyframes:
        cmds.warning("AnimKey: Selected objects do not have any keyframes.")
        return
    
    # FIX 3: Signal any previous thread to stop but do not wait for it.
    # It is a daemon thread so it will not prevent Maya from closing.
    if _anim_offset_thread and _anim_offset_thread.is_alive():
        _anim_offset_run_timer = False
        _new_offset_generation()
        # (no join – non-blocking)
    
    _stop_offset_key_nav_filter()
    _stop_offset_time_job()
    _stop_offset_undo_jobs()
    _stop_offset_attr_jobs()

    # Reset caches on activation
    _anim_offset_diff_cache.clear()
    _prev_tick_snapshot.clear()
    _anim_offset_frozen_baseline.clear()
    _anim_offset_activation_values.clear()
    _anim_offset_evaluated_baseline.clear()
    _anim_offset_target_curve_cache.clear()
    _prev_tick_time = None
    _anim_offset_syncing_time = False
    _anim_offset_flush_scheduled = False
    _anim_offset_applying_offset = False
    
    # Store original values AND original selection (also builds frozen baseline)
    store_keyframes()
    
    # Verify that values were stored
    if not _animation_offset_original_values:
        if _has_any_animation_layers():
            cmds.warning(
                "AnimKey: Selected objects have no keyframes on the active "
                "animation layer ({0}) within the selected time range."
                .format(_anim_offset_target_layer or "BaseAnimation")
            )
        else:
            cmds.warning("AnimKey: No keyframes found in the selected time range.")
        set_button_active(active_button, False)
        return
    
    # Activate
    _anim_offset_active = True
    
    # Show yellow timeline bar
    show_anim_offset_timeline_bar()
    
    # Start thread for continuous adjustment
    _anim_offset_run_timer = True
    generation = _new_offset_generation()
    _start_offset_key_nav_filter()
    _start_offset_time_job()
    _start_offset_undo_jobs()
    _anim_offset_thread = threading.Thread(target=offset_animation_deferred, args=(0.05, generation))
    _anim_offset_thread.daemon = True  # Thread closes when Maya closes
    _anim_offset_thread.start()
    
    # FIX 1: Update button using resolved reference
    set_button_active(active_button, True)


def is_active():
    """Check if animation offset is currently active"""
    return has_active_session()


def has_active_offset():
    """
    Check if animation offset is currently active.
    
    Returns:
        bool: True if animation offset is active, False otherwise
    """
    return has_active_session()


def set_button_active(button, is_active):
    """
    Set the button's active state (highlighted when animation offset is active).
    
    Args:
        button: The Qt button widget (can be None)
        is_active: True to highlight, False to restore normal state
    """
    if button is None:
        return
    
    try:
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        
        # The button's accent color (used for hover border)
        color = "#d08770"  # Orange for OFF button
        
        if is_active:
            # Active state: highlighted background with brighter border
            active_bg = "#b8705a"  # Darker orange for active state
            button.setStyleSheet(f'''
                QPushButton {{
                    color: #ffffff;
                    background-color: {active_bg};
                    border: 2px solid {color};
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {active_bg};
                    border-color: {color};
                }}
                QPushButton:pressed {{
                    background-color: {theme["button_pressed"]};
                }}
            ''')
        else:
            # Normal state: restore the EXACT same style the toolbar applies
            # in create_button (icon-based style, no text color/font overrides)
            button.setStyleSheet(f'''
                QPushButton {{
                    background-color: {theme["button_bg"]};
                    border: 1px solid {theme["border_color"]};
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: {theme["button_hover"]};
                    border-color: {color};
                }}
                QPushButton:pressed {{
                    background-color: {theme["button_pressed"]};
                }}
            ''')
    except Exception:
        pass


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Animation Offset",
        "tooltip": "Move animated objects without affecting existing animation",
        "icon": "animation_offset.svg",
        "shortcut": None,
    }
