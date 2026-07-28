"""Helpers for moving neighboring keys to the current timeline frame."""

import maya.cmds as cmds

from AnimKey.core.animation_curve_transfer import (
    ANIM_CURVE_TYPES,
    active_animation_layer,
    is_base_layer,
)
from AnimKey.core.animation_offset_session import resolve_target_curve_for_layer
from AnimKey.sliders.slider_utils import get_selected_channels, should_process_attribute


FRAME_EPSILON = 0.0001


def _is_anim_curve(node):
    try:
        return cmds.nodeType(node) in ANIM_CURVE_TYPES
    except Exception:
        return False


def _as_unique_sorted_frames(frames):
    unique = []
    for frame in frames or []:
        try:
            numeric = float(frame)
        except Exception:
            continue
        if not any(abs(numeric - existing) <= FRAME_EPSILON for existing in unique):
            unique.append(numeric)
    return sorted(unique)


def _nearest_source_frame(frames, current_time, direction):
    if direction < 0:
        candidates = [frame for frame in frames if frame > current_time + FRAME_EPSILON]
        return min(candidates) if candidates else None

    candidates = [frame for frame in frames if frame < current_time - FRAME_EPSILON]
    return max(candidates) if candidates else None


def _selected_anim_curves():
    try:
        curves = cmds.keyframe(query=True, selected=True, name=True) or []
    except Exception:
        curves = []
    return [curve for curve in curves if _is_anim_curve(curve)]


def _keyable_attrs_for_object(obj, attrs=None, selected_channels=None):
    if attrs:
        candidates = list(attrs)
    elif selected_channels:
        candidates = list(selected_channels)
    else:
        candidates = cmds.listAttr(obj, keyable=True, scalar=True) or []

    result = []
    for attr in candidates:
        attr = str(attr).split(".", 1)[-1]
        attr_full = "{}.{}".format(obj, attr)
        if not cmds.objExists(attr_full):
            continue
        if selected_channels and not should_process_attribute(obj, attr, selected_channels):
            continue
        try:
            if cmds.getAttr(attr_full, lock=True) or not cmds.getAttr(attr_full, settable=True):
                continue
        except Exception:
            continue
        result.append(attr)
    return result


def _curve_for_attr(attr_full, layer_name):
    try:
        curve = resolve_target_curve_for_layer(attr_full, layer_name)
        if curve and _is_anim_curve(curve):
            return curve
    except Exception:
        pass

    if layer_name and not is_base_layer(layer_name):
        return None

    try:
        curves = cmds.keyframe(attr_full, query=True, name=True) or []
    except Exception:
        curves = []
    for curve in curves:
        if _is_anim_curve(curve):
            return curve
    return None


def _curves_from_objects(objects, attrs=None, selected_channels=None, layer_name=None):
    curves = []
    seen = set()
    for obj in objects or []:
        if not cmds.objExists(obj):
            continue
        for attr in _keyable_attrs_for_object(obj, attrs=attrs, selected_channels=selected_channels):
            curve = _curve_for_attr("{}.{}".format(obj, attr), layer_name)
            if curve and curve not in seen:
                seen.add(curve)
                curves.append(curve)
    return curves


def _move_curve_key_to_time(curve, source_frame, destination_frame):
    if source_frame is None or abs(float(source_frame) - float(destination_frame)) <= FRAME_EPSILON:
        return False

    try:
        if cmds.keyframe(curve, query=True, time=(destination_frame, destination_frame), timeChange=True):
            cmds.cutKey(curve, time=(destination_frame, destination_frame), option="keys")
        cmds.keyframe(
            curve,
            edit=True,
            time=(source_frame, source_frame),
            absolute=True,
            timeChange=float(destination_frame),
        )
        return True
    except Exception:
        return False


def move_neighbor_key_to_current(direction, objects=None, attrs=None, current_time=None):
    """
    Pull the nearest neighboring key to the current frame.

    direction < 0 pulls the nearest key from the right side.
    direction > 0 pulls the nearest key from the left side.
    """
    if direction == 0:
        return 0

    if current_time is None:
        current_time = float(cmds.currentTime(query=True))
    else:
        current_time = float(current_time)

    selected_curves = _selected_anim_curves()
    if selected_curves:
        curves = selected_curves
    else:
        if objects is None:
            objects = cmds.ls(selection=True) or []
        selected_channels = get_selected_channels()
        curves = _curves_from_objects(
            objects,
            attrs=attrs,
            selected_channels=selected_channels,
            layer_name=active_animation_layer(),
        )

    moved = 0
    cmds.undoInfo(openChunk=True)
    try:
        for curve in curves:
            frames = _as_unique_sorted_frames(cmds.keyframe(curve, query=True, timeChange=True) or [])
            source_frame = _nearest_source_frame(frames, current_time, direction)
            if _move_curve_key_to_time(curve, source_frame, current_time):
                moved += 1
    finally:
        cmds.undoInfo(closeChunk=True)

    return moved
