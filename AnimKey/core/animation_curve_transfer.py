"""Fast, layer-aware animation curve capture and transfer helpers."""

from __future__ import annotations

import copy
import math

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds
import maya.mel as mel

from AnimKey.core.animation_offset_session import (
    get_selected_animation_layer,
    resolve_target_curve_for_layer,
)


CURVE_PAYLOAD_VERSION = 2
FRAME_EPSILON = 1e-6
ANIM_CURVE_TYPES = {
    "animCurveTA",
    "animCurveTL",
    "animCurveTU",
    "animCurveUA",
    "animCurveUL",
    "animCurveUU",
    "animCurveTT",
    "animCurveUT",
}


def selected_time_range():
    """Return the inclusive time-slider range, or None when no range is selected."""
    try:
        slider = mel.eval("$tmp=$gPlayBackSlider")
        if not cmds.timeControl(slider, query=True, rangeVisible=True):
            return None
        values = cmds.timeControl(slider, query=True, rangeArray=True) or []
        if len(values) < 2:
            return None
        start = float(values[0])
        end = float(values[1])
        if end > start:
            end -= 1.0
        if end + FRAME_EPSILON < start:
            return None
        return start, end
    except Exception:
        return None


def active_animation_layer():
    """Return the selected/preferred animation layer, including BaseAnimation."""
    try:
        return get_selected_animation_layer()
    except Exception:
        return None


def is_base_layer(layer_name):
    return not layer_name or layer_name == "BaseAnimation"


def is_additive_layer(layer_name):
    if is_base_layer(layer_name):
        return False
    try:
        return not bool(cmds.animLayer(layer_name, query=True, override=True))
    except Exception:
        return True


def _anim_curve_fn(curve):
    selection = om.MSelectionList()
    selection.add(curve)
    return oma.MFnAnimCurve(selection.getDependNode(0))


def _is_anim_curve(node):
    try:
        return cmds.nodeType(node) in ANIM_CURVE_TYPES
    except Exception:
        return False


def resolve_anim_curve(attr_or_curve, layer_name=None):
    """Resolve an attribute to the curve owned by the requested animation layer."""
    if not attr_or_curve:
        return None
    if _is_anim_curve(attr_or_curve):
        return attr_or_curve

    layer_name = active_animation_layer() if layer_name is None else layer_name
    try:
        curve = resolve_target_curve_for_layer(attr_or_curve, layer_name)
        if curve and cmds.objExists(curve):
            return curve
    except Exception:
        pass
    if layer_name and not is_base_layer(layer_name):
        return None

    try:
        curves = cmds.keyframe(attr_or_curve, query=True, name=True) or []
        for curve in curves:
            if _is_anim_curve(curve):
                return curve
    except Exception:
        pass

    try:
        curves = cmds.listConnections(
            attr_or_curve,
            source=True,
            destination=False,
            skipConversionNodes=True,
        ) or []
        for curve in curves:
            if _is_anim_curve(curve):
                return curve
    except Exception:
        pass
    return None


def _key_indexes_in_range(fn_curve, time_range):
    indexes = []
    for index in range(fn_curve.numKeys):
        frame = float(fn_curve.input(index).asUnits(om.MTime.uiUnit()))
        if time_range:
            start, end = time_range
            if frame < start - FRAME_EPSILON or frame > end + FRAME_EPSILON:
                continue
        indexes.append(index)
    return indexes


def _query_curve_values_ui(curve):
    try:
        return [float(value) for value in (cmds.keyframe(curve, query=True, valueChange=True) or [])]
    except Exception:
        return []


INFINITY_NAMES = {
    int(oma.MFnAnimCurve.kConstant): "constant",
    int(oma.MFnAnimCurve.kLinear): "linear",
    int(oma.MFnAnimCurve.kCycle): "cycle",
    int(oma.MFnAnimCurve.kCycleRelative): "cycleRelative",
    int(oma.MFnAnimCurve.kOscillate): "oscillate",
}


def _infinity_name(infinity_id):
    return INFINITY_NAMES.get(int(infinity_id))


def capture_curve(attr_or_curve, time_range=None, layer_name=None):
    """Capture one timed animation curve without touching Maya's key clipboard."""
    curve = resolve_anim_curve(attr_or_curve, layer_name=layer_name)
    if not curve:
        return None

    try:
        fn_curve = _anim_curve_fn(curve)
    except Exception:
        return None
    if not fn_curve.isTimeInput:
        return None

    indexes = _key_indexes_in_range(fn_curve, time_range)
    if not indexes:
        return None

    all_ui_values = _query_curve_values_ui(curve)
    keyframes = []
    api_values = []
    values = []
    tangent_in_types = []
    tangent_out_types = []
    tangent_in_x = []
    tangent_in_y = []
    tangent_out_x = []
    tangent_out_y = []
    tangents_locked = []
    weights_locked = []
    breakdowns = []

    for index in indexes:
        keyframes.append(float(fn_curve.input(index).asUnits(om.MTime.uiUnit())))
        api_values.append(float(fn_curve.value(index)))
        values.append(
            float(all_ui_values[index])
            if index < len(all_ui_values)
            else float(fn_curve.value(index))
        )
        tangent_in_types.append(int(fn_curve.inTangentType(index)))
        tangent_out_types.append(int(fn_curve.outTangentType(index)))
        in_x, in_y = fn_curve.getTangentXY(index, True)
        out_x, out_y = fn_curve.getTangentXY(index, False)
        tangent_in_x.append(float(in_x))
        tangent_in_y.append(float(in_y))
        tangent_out_x.append(float(out_x))
        tangent_out_y.append(float(out_y))
        tangents_locked.append(bool(fn_curve.tangentsLocked(index)))
        weights_locked.append(bool(fn_curve.weightsLocked(index)))
        breakdowns.append(bool(fn_curve.isBreakdown(index)))

    source_layer = active_animation_layer() if layer_name is None else layer_name
    return {
        "curve_payload_version": CURVE_PAYLOAD_VERSION,
        "curve_node_type": cmds.nodeType(curve),
        "keyframes": keyframes,
        "values": values,
        "api_values": api_values,
        "tangent_in_types": tangent_in_types,
        "tangent_out_types": tangent_out_types,
        "tangent_in_x": tangent_in_x,
        "tangent_in_y": tangent_in_y,
        "tangent_out_x": tangent_out_x,
        "tangent_out_y": tangent_out_y,
        "tangents_locked": tangents_locked,
        "weights_locked": weights_locked,
        "breakdown": breakdowns,
        "weighted_tangents": [bool(fn_curve.isWeighted)],
        "pre_infinity": _infinity_name(fn_curve.preInfinityType),
        "post_infinity": _infinity_name(fn_curve.postInfinityType),
        "pre_infinity_id": int(fn_curve.preInfinityType),
        "post_infinity_id": int(fn_curve.postInfinityType),
        "full_curve": len(indexes) == fn_curve.numKeys,
        "source_layer": source_layer,
    }


def curve_time_range(curve_data, time_offset=0.0):
    frames = curve_data.get("keyframes", []) if isinstance(curve_data, dict) else []
    if not frames:
        return None
    offset = float(time_offset or 0.0)
    return min(frames) + offset, max(frames) + offset


def offset_curve_data(curve_data, time_offset):
    result = copy.deepcopy(curve_data)
    offset = float(time_offset or 0.0)
    result["keyframes"] = [
        float(frame) + offset for frame in curve_data.get("keyframes", [])
    ]
    return result


def trim_curve_data(curve_data, start_frame, end_frame):
    frames = curve_data.get("keyframes", []) if isinstance(curve_data, dict) else []
    keep = [
        index
        for index, frame in enumerate(frames)
        if start_frame - FRAME_EPSILON <= frame <= end_frame + FRAME_EPSILON
    ]
    if not keep:
        return None

    result = {}
    for key, value in curve_data.items():
        if isinstance(value, list) and len(value) == len(frames):
            result[key] = [value[index] for index in keep]
        else:
            result[key] = copy.deepcopy(value)
    result["full_curve"] = bool(curve_data.get("full_curve")) and len(keep) == len(frames)
    return result


def _array(values, array_type):
    try:
        return array_type(values)
    except Exception:
        result = array_type()
        for value in values:
            result.append(value)
        return result


def _populate_api_curve(curve, curve_data):
    fn_curve = _anim_curve_fn(curve)
    keyframes = [float(value) for value in curve_data.get("keyframes", [])]
    values = [float(value) for value in curve_data.get("api_values", [])]
    count = min(len(keyframes), len(values))
    if not count:
        return False

    while fn_curve.numKeys:
        fn_curve.remove(fn_curve.numKeys - 1)

    weighted = curve_data.get("weighted_tangents", [])
    fn_curve.setIsWeighted(bool(weighted[0]) if weighted else False)

    times = _array(
        [om.MTime(frame, om.MTime.uiUnit()) for frame in keyframes[:count]],
        om.MTimeArray,
    )
    api_values = _array(values[:count], om.MDoubleArray)
    in_types = [int(value) for value in curve_data.get("tangent_in_types", [])[:count]]
    out_types = [int(value) for value in curve_data.get("tangent_out_types", [])[:count]]
    in_x = [float(value) for value in curve_data.get("tangent_in_x", [])[:count]]
    in_y = [float(value) for value in curve_data.get("tangent_in_y", [])[:count]]
    out_x = [float(value) for value in curve_data.get("tangent_out_x", [])[:count]]
    out_y = [float(value) for value in curve_data.get("tangent_out_y", [])[:count]]
    tangent_locks = [
        int(bool(value)) for value in curve_data.get("tangents_locked", [])[:count]
    ]
    weight_locks = [
        int(bool(value)) for value in curve_data.get("weights_locked", [])[:count]
    ]

    have_tangents = all(
        len(values_array) == count
        for values_array in (in_types, out_types, in_x, in_y, out_x, out_y)
    )
    if have_tangents:
        fn_curve.addKeysWithTangents(
            times,
            api_values,
            tangentInTypeArray=_array(in_types, om.MIntArray),
            tangentOutTypeArray=_array(out_types, om.MIntArray),
            tangentInXArray=_array(in_x, om.MDoubleArray),
            tangentInYArray=_array(in_y, om.MDoubleArray),
            tangentOutXArray=_array(out_x, om.MDoubleArray),
            tangentOutYArray=_array(out_y, om.MDoubleArray),
            tangentsLockedArray=_array(
                tangent_locks if len(tangent_locks) == count else [0] * count,
                om.MIntArray,
            ),
            weightsLockedArray=_array(
                weight_locks if len(weight_locks) == count else [0] * count,
                om.MIntArray,
            ),
            convertUnits=False,
            keepExistingKeys=False,
        )
    else:
        fn_curve.addKeys(times, api_values, keepExistingKeys=False)

    breakdowns = curve_data.get("breakdown", [])
    for index in range(count):
        if index < len(in_types):
            fn_curve.setInTangentType(index, in_types[index])
        if index < len(out_types):
            fn_curve.setOutTangentType(index, out_types[index])
        if index < len(breakdowns):
            fn_curve.setIsBreakdown(index, bool(breakdowns[index]))
        if index < len(tangent_locks):
            fn_curve.setTangentsLocked(index, bool(tangent_locks[index]))
        if index < len(weight_locks):
            fn_curve.setWeightsLocked(index, bool(weight_locks[index]))
    fn_curve.setIsWeighted(bool(weighted[0]) if weighted else False)

    try:
        fn_curve.setPreInfinityType(int(curve_data.get("pre_infinity_id")))
        fn_curve.setPostInfinityType(int(curve_data.get("post_infinity_id")))
    except Exception:
        pass
    return True


def _populate_legacy_curve(curve, curve_data):
    frames = curve_data.get("keyframes", [])
    values = curve_data.get("values", [])
    if not frames or not values:
        return False

    for frame, value in zip(frames, values):
        cmds.setKeyframe(curve, time=float(frame), value=float(value))

    in_types = curve_data.get("in_tangent", [])
    out_types = curve_data.get("out_tangent", [])
    in_angles = curve_data.get("in_angle", [])
    out_angles = curve_data.get("out_angle", [])
    in_weights = curve_data.get("in_weight", [])
    out_weights = curve_data.get("out_weight", [])
    tangent_locks = curve_data.get("locked", [])
    weight_locks = curve_data.get("weight_locked", [])
    breakdowns = curve_data.get("breakdown", [])
    weighted = curve_data.get("weighted_tangents", [])
    if weighted:
        try:
            cmds.keyTangent(
                curve,
                edit=True,
                weightedTangents=bool(weighted[0] if len(weighted) == 1 else any(weighted)),
            )
        except Exception:
            pass
    for index, frame in enumerate(frames):
        kwargs = {}
        for values_list, key in (
            (in_types, "inTangentType"),
            (out_types, "outTangentType"),
            (in_angles, "inAngle"),
            (out_angles, "outAngle"),
            (in_weights, "inWeight"),
            (out_weights, "outWeight"),
        ):
            if index < len(values_list) and values_list[index] is not None:
                kwargs[key] = values_list[index]
        if kwargs:
            try:
                cmds.keyTangent(curve, edit=True, time=(frame, frame), **kwargs)
            except Exception:
                pass
        try:
            if index < len(tangent_locks):
                cmds.keyTangent(
                    curve,
                    edit=True,
                    time=(frame, frame),
                    lock=bool(tangent_locks[index]),
                )
            if index < len(weight_locks):
                cmds.keyTangent(
                    curve,
                    edit=True,
                    time=(frame, frame),
                    weightLock=bool(weight_locks[index]),
                )
            if index < len(breakdowns) and breakdowns[index]:
                cmds.keyframe(
                    curve,
                    edit=True,
                    time=(frame, frame),
                    breakdown=True,
                )
        except Exception:
            pass
    infinity_kwargs = {}
    if curve_data.get("pre_infinity"):
        infinity_kwargs["preInfinite"] = curve_data["pre_infinity"]
    if curve_data.get("post_infinity"):
        infinity_kwargs["postInfinite"] = curve_data["post_infinity"]
    if infinity_kwargs:
        try:
            cmds.setInfinity(curve, **infinity_kwargs)
        except Exception:
            pass
    return True


def create_temp_curve(curve_data, name="ANIMKEY_TEMP_CURVE"):
    node_type = curve_data.get("curve_node_type") or "animCurveTU"
    if node_type not in ANIM_CURVE_TYPES:
        node_type = "animCurveTU"
    curve = cmds.createNode(node_type, name=name)
    try:
        if curve_data.get("api_values") and _populate_api_curve(curve, curve_data):
            return curve
        if _populate_legacy_curve(curve, curve_data):
            return curve
    except Exception:
        pass
    try:
        cmds.delete(curve)
    except Exception:
        pass
    return None


def _split_attr(attr_path):
    if not attr_path or "." not in attr_path:
        return None, None
    return attr_path.rsplit(".", 1)


def _layer_is_locked(layer_name):
    if is_base_layer(layer_name):
        return False
    try:
        return bool(cmds.getAttr("{}.lock".format(layer_name)))
    except Exception:
        return False


def ensure_target_curve(attr_path, layer_name=None, seed_time=None):
    """Return a destination animCurve, creating it on the requested layer if needed."""
    node, attr = _split_attr(attr_path)
    if not node or not attr or not cmds.objExists(attr_path):
        return None
    try:
        if cmds.getAttr(attr_path, lock=True):
            return None
    except Exception:
        return None

    layer_name = active_animation_layer() if layer_name is None else layer_name
    curve = resolve_anim_curve(attr_path, layer_name=layer_name)
    if curve:
        return curve
    if _layer_is_locked(layer_name):
        return None

    if layer_name and not is_base_layer(layer_name):
        try:
            cmds.animLayer(layer_name, edit=True, attribute=attr_path)
        except Exception:
            pass

    frame = float(seed_time if seed_time is not None else cmds.currentTime(query=True))
    kwargs = {
        "attribute": attr,
        "time": (frame, frame),
    }
    if layer_name:
        kwargs["animLayer"] = layer_name
    try:
        cmds.setKeyframe(node, **kwargs)
    except Exception:
        return None
    return resolve_anim_curve(attr_path, layer_name=layer_name)


def _clear_curve_range(curve, time_range):
    if not curve or not cmds.objExists(curve) or not time_range:
        return
    try:
        cmds.cutKey(
            curve,
            time=(float(time_range[0]), float(time_range[1])),
            option="keys",
            clear=True,
        )
    except TypeError:
        cmds.cutKey(
            curve,
            time=(float(time_range[0]), float(time_range[1])),
            option="keys",
            cl=True,
        )


def clear_attr_range(attr_path, time_range, layer_name=None):
    """Clear keys from one layer curve without creating a new curve."""
    curve = resolve_anim_curve(attr_path, layer_name=layer_name)
    if not curve:
        return False
    _clear_curve_range(curve, time_range)
    return True


def paste_curve(
    attr_path,
    curve_data,
    layer_name=None,
    time_offset=0.0,
    clear_existing=True,
    value_scale=1.0,
    value_offset=0.0,
):
    """Paste serialized keys through Maya's native clipboard into one layer curve."""
    source_range = curve_time_range(curve_data)
    target_range = curve_time_range(curve_data, time_offset=time_offset)
    if not source_range or not target_range:
        return False, 0

    target_curve = ensure_target_curve(
        attr_path,
        layer_name=layer_name,
        seed_time=target_range[0],
    )
    if not target_curve:
        return False, 0

    temp_curve = create_temp_curve(curve_data)
    if not temp_curve:
        return False, 0

    try:
        if not math.isclose(float(value_scale), 1.0, abs_tol=1e-12):
            cmds.scaleKey(
                temp_curve,
                time=source_range,
                valueScale=float(value_scale),
                valuePivot=0.0,
            )
        if not math.isclose(float(value_offset), 0.0, abs_tol=1e-12):
            cmds.keyframe(
                temp_curve,
                edit=True,
                time=source_range,
                relative=True,
                valueChange=float(value_offset),
            )

        if clear_existing:
            _clear_curve_range(target_curve, target_range)
            if not cmds.objExists(target_curve):
                target_curve = ensure_target_curve(
                    attr_path,
                    layer_name=layer_name,
                    seed_time=target_range[0],
                )
                if not target_curve:
                    return False, 0

        weighted = curve_data.get("weighted_tangents", [])
        if weighted:
            try:
                cmds.keyTangent(
                    target_curve,
                    edit=True,
                    weightedTangents=bool(weighted[0]),
                )
            except Exception:
                pass

        copied = cmds.copyKey(temp_curve, time=source_range)
        if not copied:
            return False, 0
        kwargs = {"option": "merge"}
        if time_offset:
            kwargs["timeOffset"] = float(time_offset)
        result = cmds.pasteKey(target_curve, **kwargs)
        if isinstance(result, (int, float)) and result <= 0:
            return False, 0

        if clear_existing and curve_data.get("full_curve"):
            infinity_kwargs = {}
            if curve_data.get("pre_infinity"):
                infinity_kwargs["preInfinite"] = curve_data["pre_infinity"]
            if curve_data.get("post_infinity"):
                infinity_kwargs["postInfinite"] = curve_data["post_infinity"]
            if infinity_kwargs:
                try:
                    cmds.setInfinity(attr_path, **infinity_kwargs)
                except Exception:
                    pass
        return True, len(curve_data.get("keyframes", []))
    finally:
        if cmds.objExists(temp_curve):
            try:
                cmds.delete(temp_curve)
            except Exception:
                pass


def set_key_on_layer(attr_path, frame, value, layer_name=None):
    node, attr = _split_attr(attr_path)
    if not node or not attr or not cmds.objExists(attr_path):
        return False
    try:
        if cmds.getAttr(attr_path, lock=True):
            return False
    except Exception:
        return False

    layer_name = active_animation_layer() if layer_name is None else layer_name
    if _layer_is_locked(layer_name):
        return False
    if layer_name and not is_base_layer(layer_name):
        try:
            cmds.animLayer(layer_name, edit=True, attribute=attr_path)
        except Exception:
            pass
    kwargs = {
        "attribute": attr,
        "time": (float(frame), float(frame)),
        "value": float(value),
    }
    if layer_name:
        kwargs["animLayer"] = layer_name
    try:
        cmds.setKeyframe(node, **kwargs)
        return True
    except Exception:
        return False
