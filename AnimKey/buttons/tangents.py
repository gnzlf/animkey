"""
    AnimKey - Tangent Functions
    
    Functions to set keyframe tangent types.
"""

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.sliders.slider_utils import get_selected_time_range


def _append_unique(items, item):
    if item and item not in items:
        items.append(item)


def _as_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _frame_matches(frame_a, frame_b):
    frame_a = _as_float(frame_a)
    frame_b = _as_float(frame_b)
    if frame_a is None or frame_b is None:
        return False
    return abs(frame_a - frame_b) < 0.0001


def _node_from_plug(item):
    return (item or "").split(".", 1)[0]


def _key_times_in_range(curve, start, end):
    """Return keyed times on a curve inside an inclusive range."""
    try:
        key_times = cmds.keyframe(curve, query=True, timeChange=True) or []
    except Exception:
        key_times = []

    result = []
    start = _as_float(start)
    end = _as_float(end)
    if start is None or end is None:
        return result

    for key_time in key_times:
        numeric_time = _as_float(key_time)
        if numeric_time is None:
            continue
        if start - 0.0001 <= numeric_time <= end + 0.0001:
            result.append(key_time)
    return result


def _timeline_control():
    """Return Maya's main time slider control when available."""
    for expression in (
        "global string $gPlayBackSlider; $gPlayBackSlider",
        "$tmpVar=$gPlayBackSlider",
    ):
        try:
            slider = mel.eval(expression)
        except Exception:
            slider = None
        if slider:
            return slider
    return None


def _selected_timeline_range():
    """
    Return the selected time-slider range, including a single selected frame.

    Maya reports the time-slider range end as exclusive, so a one-frame
    selection commonly arrives as [frame, frame + 1].
    """
    slider = _timeline_control()
    if not slider:
        return None

    try:
        if not cmds.timeControl(slider, query=True, rangeVisible=True):
            return None
        raw_range = cmds.timeControl(slider, query=True, rangeArray=True) or []
    except Exception:
        return None

    if len(raw_range) != 2:
        return None

    start = _as_float(raw_range[0])
    end = _as_float(raw_range[1])
    if start is None or end is None:
        return None

    if end < start:
        start, end = end, start

    if end - start >= 1.0:
        end = end - 1.0

    return (start, max(start, end))


def _upstream_anim_curves(item):
    """Find anim curves upstream from a node or plug, crossing blend/layer nodes."""
    curves = []
    pending = [item]
    visited = set()

    while pending and len(visited) < 120:
        current = pending.pop(0)
        if not current or current in visited:
            continue
        visited.add(current)

        try:
            connections = cmds.listConnections(
                current,
                source=True,
                destination=False,
                plugs=True,
                skipConversionNodes=False
            ) or []
        except Exception:
            connections = []

        for plug in connections:
            node = _node_from_plug(plug)
            if not node:
                continue

            try:
                node_type = cmds.nodeType(node)
            except Exception:
                node_type = ""

            if node_type.startswith("animCurve"):
                _append_unique(curves, node)
                continue

            if plug not in visited:
                pending.append(plug)
            if node not in visited:
                pending.append(node)

    return curves


def _animated_curves_for_object(obj):
    """Return anim curves for a selected object, preserving current behavior."""
    curves = []
    try:
        for curve in cmds.keyframe(obj, query=True, name=True, animation="objects") or []:
            _append_unique(curves, curve)
    except Exception:
        pass

    try:
        for curve in cmds.listConnections(
            obj,
            source=True,
            destination=False,
            type="animCurve",
            skipConversionNodes=True
        ) or []:
            _append_unique(curves, curve)
    except Exception:
        pass

    try:
        attrs = cmds.listAttr(obj, keyable=True) or []
    except Exception:
        attrs = []

    for attr in attrs:
        attr_full = f"{obj}.{attr}"
        try:
            for curve in cmds.keyframe(attr_full, query=True, name=True, animation="objects") or []:
                _append_unique(curves, curve)
        except Exception:
            pass

        try:
            for curve in cmds.listConnections(
                attr_full,
                source=True,
                destination=False,
                type="animCurve",
                skipConversionNodes=True
            ) or []:
                _append_unique(curves, curve)
        except Exception:
            pass

        for curve in _upstream_anim_curves(attr_full):
            _append_unique(curves, curve)

    return curves


def _apply_tangent(curve, tangent_type, in_tangent=True, out_tangent=True,
                   key_index=None, frame=None):
    kwargs = {"edit": True}
    if key_index is not None:
        kwargs["index"] = (int(key_index),)
    if frame is not None:
        kwargs["time"] = (frame, frame)

    try:
        if in_tangent:
            cmds.keyTangent(curve, inTangentType=tangent_type, **kwargs)
        if out_tangent:
            cmds.keyTangent(curve, outTangentType=tangent_type, **kwargs)
        return True
    except Exception:
        return False


def _apply_to_selected_graph_keys(tangent_type, in_tangent, out_tangent):
    anim_curves = cmds.keyframe(query=True, selected=True, name=True) or []
    edited = 0

    for curve in anim_curves:
        selected_indices = cmds.keyframe(curve, query=True, selected=True, indexValue=True) or []
        if selected_indices:
            for key_index in selected_indices:
                if _apply_tangent(
                    curve,
                    tangent_type,
                    in_tangent=in_tangent,
                    out_tangent=out_tangent,
                    key_index=key_index
                ):
                    edited += 1
            continue

        selected_times = cmds.keyframe(curve, query=True, selected=True, timeChange=True) or []
        for frame in selected_times:
            if _apply_tangent(
                curve,
                tangent_type,
                in_tangent=in_tangent,
                out_tangent=out_tangent,
                frame=frame
            ):
                edited += 1

    return edited


def _apply_to_active_selected_keys(tangent_type, in_tangent, out_tangent):
    """Apply to Maya's active key selection when it is not tied to curve names."""
    try:
        selected_times = cmds.keyframe(query=True, selected=True, timeChange=True) or []
    except Exception:
        selected_times = []

    if not selected_times:
        return 0

    kwargs = {"edit": True, "selected": True, "animation": "keys"}
    try:
        if in_tangent:
            cmds.keyTangent(inTangentType=tangent_type, **kwargs)
        if out_tangent:
            cmds.keyTangent(outTangentType=tangent_type, **kwargs)
        return len(selected_times)
    except Exception:
        return 0


def _apply_to_selected_objects(tangent_type, in_tangent, out_tangent):
    selection = cmds.ls(selection=True) or []
    if not selection:
        cmds.warning("Please select keyframes or objects with animation.")
        return 0

    selected_range = _selected_timeline_range() or get_selected_time_range()
    if selected_range:
        start, end = selected_range
    else:
        current_time = cmds.currentTime(query=True)
        start = end = current_time

    edited = 0
    for obj in selection:
        for curve in _animated_curves_for_object(obj):
            for frame in _key_times_in_range(curve, start, end):
                if _apply_tangent(
                    curve,
                    tangent_type,
                    in_tangent=in_tangent,
                    out_tangent=out_tangent,
                    frame=frame
                ):
                    edited += 1

    return edited


def set_tangent_type(tangent_type, in_tangent=True, out_tangent=True):
    """
    Set the tangent type for selected keyframes.
    
    Args:
        tangent_type: The tangent type to set (auto, spline, linear, step, flat, clamped, plateau)
        in_tangent: Whether to set the in-tangent
        out_tangent: Whether to set the out-tangent
    """
    chunk_open = False
    try:
        try:
            cmds.undoInfo(openChunk=True, chunkName=f"AnimKey Tangent {tangent_type}")
        except TypeError:
            cmds.undoInfo(openChunk=True)
        chunk_open = True

        edited = _apply_to_selected_graph_keys(tangent_type, in_tangent, out_tangent)
        if not edited:
            edited = _apply_to_active_selected_keys(tangent_type, in_tangent, out_tangent)
        if not edited:
            edited = _apply_to_selected_objects(tangent_type, in_tangent, out_tangent)
    finally:
        if chunk_open:
            cmds.undoInfo(closeChunk=True)

    if edited:
        cmds.inViewMessage(amg=f"Tangent set to <hl>{tangent_type}</hl>", pos='midCenter', fade=True)
    else:
        cmds.warning("AnimKey: No keyed frames found for tangent operation.")


def set_plateau(*args):
    """Set tangent to plateau"""
    set_tangent_type("plateau")


def set_step(*args):
    """Set tangent to step (stepped)"""
    set_tangent_type("step", in_tangent=False, out_tangent=True)


def set_flat(*args):
    """Set tangent to flat"""
    set_tangent_type("flat")


def set_linear(*args):
    """Set tangent to linear"""
    set_tangent_type("linear")


def set_clamped(*args):
    """Set tangent to clamped"""
    set_tangent_type("clamped")


def set_spline(*args):
    """Set tangent to spline"""
    set_tangent_type("spline")


def set_auto(*args):
    """Set tangent to auto"""
    set_tangent_type("auto")


# Execute functions for toolbar buttons
def execute_plateau(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tangents.execute_plateau"):
        return None
    set_plateau()

def execute_step(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tangents.execute_step"):
        return None
    set_step()

def execute_flat(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tangents.execute_flat"):
        return None
    set_flat()

def execute_linear(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tangents.execute_linear"):
        return None
    set_linear()

def execute_clamped(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tangents.execute_clamped"):
        return None
    set_clamped()

def execute_spline(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tangents.execute_spline"):
        return None
    set_spline()

def execute_auto(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tangents.execute_auto"):
        return None
    set_auto()

