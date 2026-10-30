"""Helpers for moving timeline-neighboring keys."""

import maya.cmds as cmds

from AnimKey.core.animation_curve_transfer import (
    ANIM_CURVE_TYPES,
    active_animation_layer,
    is_base_layer,
    selected_time_range,
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


def _curves_for_context(objects=None, attrs=None, use_selected_keys=True):
    selected_curves = _selected_anim_curves() if use_selected_keys else []
    if selected_curves:
        return selected_curves

    if objects is None:
        objects = cmds.ls(selection=True) or []
    selected_channels = get_selected_channels()
    return _curves_from_objects(
        objects,
        attrs=attrs,
        selected_channels=selected_channels,
        layer_name=active_animation_layer(),
    )


def _move_current_keys_to_offset(curves, current_time, frame_offset):
    destination_frame = float(current_time) + float(frame_offset)
    moved = 0
    for curve in curves:
        frames = _as_unique_sorted_frames(cmds.keyframe(curve, query=True, timeChange=True) or [])
        if not any(abs(frame - current_time) <= FRAME_EPSILON for frame in frames):
            continue
        if _move_curve_key_to_time(curve, current_time, destination_frame):
            moved += 1
    return moved


def _move_key_block(curves, frame_offset, time_range=None):
    """Move a complete range/Graph selection without implicit active-key scope."""
    edits = []
    scope = {"time": tuple(time_range)} if time_range is not None else {"selected": True}
    for curve in curves:
        frames = _as_unique_sorted_frames(cmds.keyframe(curve, query=True, timeChange=True, **scope) or [])
        if not frames:
            continue
        all_frames = _as_unique_sorted_frames(cmds.keyframe(curve, query=True, timeChange=True) or [])
        stationary = [t for t in all_frames if not any(abs(t-f) <= FRAME_EPSILON for f in frames)]
        if any(abs(frame+frame_offset-other) <= FRAME_EPSILON for frame in frames for other in stationary):
            cmds.warning("AnimKey: A destination key is occupied; the selected key block was left unchanged.")
            return 0
        edits.append((curve, frames))
    for curve, _frames in edits:
        cmds.keyframe(curve, edit=True, relative=True, timeChange=float(frame_offset), option="over",
                      time=[(frame, frame) for frame in _frames])
    return sum(len(frames) for _curve, frames in edits)


def _move_selected_keys_by_offset(frame_offset):
    return _move_key_block(_selected_anim_curves(), frame_offset)


def move_key_with_arrow(direction, frame_amount=1, objects=None, attrs=None, current_time=None,
                        allow_neighbor=True):
    """
    Apply the toolbar arrow behavior.

    When the current frame is keyed, the current key moves by frame_amount.
    Otherwise the nearest key from the arrow side is pulled to the current frame.
    """
    if direction == 0:
        return {"mode": "none", "moved": 0}

    if current_time is None:
        current_time = float(cmds.currentTime(query=True))
    else:
        current_time = float(current_time)

    frame_offset = abs(float(frame_amount)) * (1 if direction > 0 else -1)
    time_range = selected_time_range()
    curves = _curves_for_context(objects=objects, attrs=attrs, use_selected_keys=time_range is None)

    moved = 0
    mode = "none"
    cmds.undoInfo(openChunk=True)
    try:
        if time_range is not None:
            moved = _move_key_block(curves, frame_offset, time_range=time_range)
            return {"mode": "range", "moved": moved}
        if _selected_anim_curves():
            moved = _move_selected_keys_by_offset(frame_offset)
            return {"mode": "selected", "moved": moved}
        else:
            moved = _move_current_keys_to_offset(curves, current_time, frame_offset)
            if moved:
                mode = "current"
            elif allow_neighbor:
                for curve in curves:
                    frames = _as_unique_sorted_frames(cmds.keyframe(curve, query=True, timeChange=True) or [])
                    source_frame = _nearest_source_frame(frames, current_time, direction)
                    if _move_curve_key_to_time(curve, source_frame, current_time):
                        moved += 1
                if moved:
                    mode = "neighbor"
    finally:
        cmds.undoInfo(closeChunk=True)

    return {"mode": mode, "moved": moved}


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

    curves = _curves_for_context(objects=objects, attrs=attrs)

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
