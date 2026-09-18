# -*- coding: utf-8 -*-
"""
TEMP CONTROL PRO - MATRIX-BASED SOLUTION
=========================================
Usa pre-bake de world-space + multMatrix + decomposeMatrix
para lograr control aditivo sin loops circulares.

Modern frameless window design matching AnimKey retimer style.
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.OpenMayaUI as omui
import json
import math
import os
from AnimKey.mods.uiMod import ContextPopupWindow
from AnimKey.mods.storage import atomic_write_json, backup_corrupt_file

from AnimKey.mods.maya_compat import (
    QtCore, QtGui, QtWidgets, screen_available_geometry,
    wrap_instance as wrapInstance,
)
from AnimKey.core.animation_curve_transfer import resolve_anim_curve, selected_time_range

TOOL_TAG = "TEMP_CTRL_MATRIX_V2"
WINDOW_OBJECT = "AnimKey_TempControl"
TEMP_BAKE_LAYER_BASE = "TempControl_Animkey"
TEMP_BAKE_LAYER_SUFFIX = "_Animkey"
TEMP_BAKE_ATTRS = [
    'translateX', 'translateY', 'translateZ',
    'rotateX', 'rotateY', 'rotateZ'
]
TEMP_PIVOT_PREFS_FILE = "temp_pivot_offsets.json"
TEMP_PIVOT_MATRIX_EPSILON = 1e-5
TEMP_PIVOT_MAX_BAKE_SAMPLES = 10000
TEMP_PIVOT_MONITOR_INTERVAL_MS = 50

# Global window reference
_temp_pivot_window = None
_temp_pivot_restore_state = None
_temp_pivot_monitor = None
_temp_pivot_edit_filter = None


class _TempPivotEditFilter(QtCore.QObject):
    """Wake the low-frequency monitor immediately after an edit gesture."""

    def eventFilter(self, watched, event):
        event_type = event.type()
        event_types = getattr(QtCore.QEvent, "Type", QtCore.QEvent)
        if event_type in (
            event_types.MouseButtonRelease,
            event_types.KeyRelease,
        ):
            QtCore.QTimer.singleShot(0, _monitor_temp_pivot_edit_mode)
        return False

def get_maya_main_window():
    return wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget)


def _temp_pivot_prefs_path():
    """Prefs path for reusable TEMP pivot offsets."""
    try:
        from AnimKey.mods import configMod
        folder = os.path.join(configMod.get_user_folder_path(), "tools", "temp_pivot")
    except Exception:
        folder = os.path.join(
            cmds.internalVar(userAppDir=True),
            "AnimKey_user_data",
            "tools",
            "temp_pivot",
        )
    if not os.path.exists(folder):
        os.makedirs(folder)
    return os.path.join(folder, TEMP_PIVOT_PREFS_FILE)


def _empty_temp_pivot_offsets():
    return {"single_offsets": {}, "multi_offsets": []}


def _load_temp_pivot_offsets():
    path = _temp_pivot_prefs_path()
    if not os.path.exists(path):
        return _empty_temp_pivot_offsets()
    try:
        with open(path, "r") as stream:
            data = json.load(stream) or {}
    except Exception:
        backup_corrupt_file(path)
        return _empty_temp_pivot_offsets()

    if not isinstance(data.get("single_offsets"), dict):
        data["single_offsets"] = {}
    if not isinstance(data.get("multi_offsets"), list):
        data["multi_offsets"] = []
    return data


def _save_temp_pivot_offsets(data):
    path = _temp_pivot_prefs_path()
    atomic_write_json(path, data or _empty_temp_pivot_offsets())


def _world_matrix(node):
    return om.MMatrix(cmds.xform(node, query=True, worldSpace=True, matrix=True))


def _matrix_translation(matrix_value):
    values = list(matrix_value)
    return [float(values[12]), float(values[13]), float(values[14])]


def _pivot_matrix_from_position(position, orientation_source=None):
    if orientation_source and cmds.objExists(orientation_source):
        values = list(_world_matrix(orientation_source))
    else:
        values = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    values[12] = float(position[0])
    values[13] = float(position[1])
    values[14] = float(position[2])
    return om.MMatrix(values)


def _offset_to_world_position(offset_values, obj):
    try:
        if not offset_values or len(offset_values) != 16 or not cmds.objExists(obj):
            return None
        if _is_identity_matrix_values(offset_values):
            return list(cmds.xform(obj, query=True, worldSpace=True, rotatePivot=True))
        pivot_matrix = om.MMatrix(offset_values) * _world_matrix(obj)
        return _matrix_translation(pivot_matrix)
    except Exception:
        return None


def _is_identity_matrix_values(values):
    if not values or len(values) != 16:
        return False
    identity = [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]
    try:
        return all(abs(float(a) - float(b)) < 0.0001 for a, b in zip(values, identity))
    except Exception:
        return False


def _find_multi_offset_group(data, objects):
    sorted_objects = sorted(objects or [])
    for group in data.get("multi_offsets", []):
        if sorted(group.get("objects", [])) == sorted_objects:
            return group
    return None


def _stored_temp_pivot_position(objects, pivot_mode="last"):
    objects = _selected_temp_pivot_objects(objects)
    if not objects:
        return None

    data = _load_temp_pivot_offsets()
    if len(objects) == 1:
        return _offset_to_world_position(
            data.get("single_offsets", {}).get(objects[0]),
            objects[0],
        )

    if pivot_mode != "last":
        return None

    group = _find_multi_offset_group(data, objects)
    if not group:
        return None

    reference = group.get("reference")
    reference_offset = group.get("offset_matrix")
    if reference not in objects or not cmds.objExists(reference):
        reference = objects[-1]
    if reference_offset:
        position = _offset_to_world_position(reference_offset, reference)
        if position:
            return position

    # Backward compatibility with offsets saved by older AnimKey versions.
    positions = []
    offsets = group.get("offsets", {})
    for obj in objects:
        position = _offset_to_world_position(offsets.get(obj), obj)
        if position:
            positions.append(position)
    if not positions:
        return None

    return [
        sum(position[i] for position in positions) / float(len(positions))
        for i in range(3)
    ]


def _save_temp_pivot_offsets_for_objects(objects, pivot_position):
    objects = _selected_temp_pivot_objects(objects)
    if not objects or not pivot_position:
        return False

    data = _load_temp_pivot_offsets()
    pivot_matrix = _pivot_matrix_from_position(pivot_position, objects[-1])

    if len(objects) == 1:
        obj = objects[0]
        try:
            data["single_offsets"][obj] = list(pivot_matrix * _world_matrix(obj).inverse())
        except Exception:
            return False
    else:
        offsets = {}
        for obj in objects:
            if not cmds.objExists(obj):
                continue
            try:
                offsets[obj] = list(pivot_matrix * _world_matrix(obj).inverse())
            except Exception:
                pass
        if not offsets:
            return False

        group = _find_multi_offset_group(data, objects)
        if group is None:
            data["multi_offsets"].append({
                "objects": list(objects),
                "offsets": offsets,
                "reference": objects[-1],
                "offset_matrix": list(
                    pivot_matrix * _world_matrix(objects[-1]).inverse()
                ),
            })
        else:
            group["objects"] = list(objects)
            group["offsets"] = offsets
            group["reference"] = objects[-1]
            group["offset_matrix"] = list(
                pivot_matrix * _world_matrix(objects[-1]).inverse()
            )

    _save_temp_pivot_offsets(data)
    return True


def reset_temp_pivot_offsets(objects=None):
    """Reset TEMP pivot offsets to object center or last selected, like Animo."""
    selected_objects = _selected_temp_pivot_objects(objects)
    if not selected_objects:
        cmds.warning("AnimKey Temp Pivot: nothing selected.")
        return False

    data = _load_temp_pivot_offsets()
    if len(selected_objects) == 1:
        obj = selected_objects[0]
        data["single_offsets"][obj] = [
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
        ]
        pivot_position = list(cmds.xform(obj, query=True, worldSpace=True, rotatePivot=True))
    else:
        last_obj = selected_objects[-1]
        last_matrix = _world_matrix(last_obj)
        offsets = {}
        for obj in selected_objects:
            try:
                offsets[obj] = list(last_matrix * _world_matrix(obj).inverse())
            except Exception:
                pass

        group = _find_multi_offset_group(data, selected_objects)
        if group is None:
            data["multi_offsets"].append({
                "objects": list(selected_objects),
                "offsets": offsets,
                "reference": last_obj,
                "offset_matrix": [
                    1, 0, 0, 0,
                    0, 1, 0, 0,
                    0, 0, 1, 0,
                    0, 0, 0, 1,
                ],
            })
        else:
            group["objects"] = list(selected_objects)
            group["offsets"] = offsets
            group["reference"] = last_obj
            group["offset_matrix"] = [
                1, 0, 0, 0,
                0, 1, 0, 0,
                0, 0, 1, 0,
                0, 0, 0, 1,
            ]

        pivot_position = list(cmds.xform(last_obj, query=True, worldSpace=True, rotatePivot=True))

    _save_temp_pivot_offsets(data)
    if is_active():
        try:
            cmds.manipPivot(position=pivot_position)
            cmds.manipPivot(pinPivot=True)
        except Exception:
            pass
    return True


def _selected_temp_pivot_objects(selection=None):
    """Return unique selected transforms/joints while preserving selection order."""
    raw_selection = selection
    if raw_selection is None:
        raw_selection = cmds.ls(selection=True, long=True) or []
    elif isinstance(raw_selection, str):
        raw_selection = [raw_selection]

    objects = []
    seen = set()
    for item in raw_selection or []:
        nodes = cmds.ls(item, objectsOnly=True, long=True) or []
        node = nodes[0] if nodes else str(item).split(".", 1)[0]
        if not cmds.objExists(node):
            continue

        try:
            node_type = cmds.nodeType(node)
        except Exception:
            continue

        if node_type not in ("transform", "joint"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if not parents:
                continue
            node = parents[0]

        long_names = cmds.ls(node, long=True) or [node]
        node = long_names[0]
        if node not in seen:
            seen.add(node)
            objects.append(node)
    return objects


def _temp_pivot_position(objects, pivot_mode="last"):
    stored_position = _stored_temp_pivot_position(objects, pivot_mode=pivot_mode)
    if stored_position:
        return stored_position

    if pivot_mode == "center":
        bounds = cmds.exactWorldBoundingBox(objects)
        return [
            (bounds[0] + bounds[3]) * 0.5,
            (bounds[1] + bounds[4]) * 0.5,
            (bounds[2] + bounds[5]) * 0.5,
        ]
    return list(cmds.xform(objects[-1], query=True, worldSpace=True, rotatePivot=True))


def _flat_vector(value):
    if value and isinstance(value[0], (list, tuple)):
        value = value[0]
    return list(value) if value else None


def _query_rotate_context_flag(flag, default=False):
    try:
        return cmds.manipRotateContext("Rotate", query=True, **{flag: True})
    except Exception:
        return default


def _capture_temp_pivot_curve_state(objects):
    """Capture only transform keys needed to detect edits made this session."""
    result = {}
    for obj in objects or []:
        if not cmds.objExists(obj):
            continue
        for attr in TEMP_BAKE_ATTRS:
            plug = obj + "." + attr
            if not cmds.objExists(plug):
                continue
            curves = []
            layers = cmds.ls(type="animLayer") or []
            for layer in layers:
                try:
                    curve = resolve_anim_curve(plug, layer_name=layer)
                except Exception:
                    curve = None
                if curve and curve not in curves:
                    curves.append(curve)
            if not curves:
                try:
                    curves = cmds.keyframe(plug, query=True, name=True) or []
                except Exception:
                    curves = []
            for curve in curves:
                try:
                    curve_id = (cmds.ls(curve, uuid=True) or [curve])[0]
                    times = [
                        round(float(value), 10)
                        for value in (cmds.keyframe(curve, query=True, timeChange=True) or [])
                    ]
                    values = [
                        float(value)
                        for value in (cmds.keyframe(curve, query=True, valueChange=True) or [])
                    ]
                except Exception:
                    continue
                result[curve_id] = {
                    "object": obj,
                    "attribute": attr,
                    "times": times,
                    "values": values,
                }
    return result


def _temp_pivot_changed_key_times(state):
    """Return key times added, removed, or value-edited since activation."""
    before = (state or {}).get("transform_curve_state", {})
    after = _capture_temp_pivot_curve_state((state or {}).get("objects", []))
    changed = set()

    for curve_id in set(before).union(after):
        old = before.get(curve_id, {})
        new = after.get(curve_id, {})
        old_values = dict(zip(old.get("times", []), old.get("values", [])))
        new_values = dict(zip(new.get("times", []), new.get("values", [])))
        for frame in set(old_values).union(new_values):
            if frame not in old_values or frame not in new_values:
                changed.add(float(frame))
                continue
            if abs(float(old_values[frame]) - float(new_values[frame])) > 1e-8:
                changed.add(float(frame))
    return sorted(changed)


def _capture_temp_pivot_state(objects):
    object_pivots = {}
    for obj in objects:
        try:
            object_pivots[obj] = {
                "rotate_pivot": list(
                    cmds.xform(obj, query=True, objectSpace=True, rotatePivot=True)
                ),
                "scale_pivot": list(
                    cmds.xform(obj, query=True, objectSpace=True, scalePivot=True)
                ),
                "rotate_pivot_translate": list(
                    cmds.getAttr(obj + ".rotatePivotTranslate")[0]
                ),
                "scale_pivot_translate": list(
                    cmds.getAttr(obj + ".scalePivotTranslate")[0]
                ),
            }
        except Exception:
            pass

    manip_valid = bool(cmds.manipPivot(query=True, valid=True))
    return {
        "objects": list(objects),
        "object_pivots": object_pivots,
        "activation_time": float(cmds.currentTime(query=True)),
        "transform_curve_state": _capture_temp_pivot_curve_state(objects),
        "tool_context": cmds.currentCtx(),
        "manip_valid": manip_valid,
        "manip_position": (
            _flat_vector(cmds.manipPivot(query=True, position=True))
            if manip_valid
            else None
        ),
        "manip_orientation": (
            _flat_vector(cmds.manipPivot(query=True, orientation=True))
            if manip_valid
            else None
        ),
        "manip_pinned": bool(cmds.manipPivot(query=True, pinPivot=True)),
        "rotate_context": {
            "useManipPivot": bool(_query_rotate_context_flag("useManipPivot")),
            "useCenterPivot": bool(_query_rotate_context_flag("useCenterPivot")),
            "useObjectPivot": bool(_query_rotate_context_flag("useObjectPivot")),
            "pinPivot": bool(_query_rotate_context_flag("pinPivot")),
            "editPivotMode": bool(_query_rotate_context_flag("editPivotMode")),
        },
    }


def _restore_object_pivots(state, preserve_current_pose=True):
    """Restore object pivot attributes.

    ``preserve_current_pose`` is useful for a simple pivot reset.  Temp Pivot
    deactivation deliberately disables it, because it must first restore the
    original pivot at every sampled time and then bake the compensation.
    """
    for obj, pivots in (state or {}).get("object_pivots", {}).items():
        if not cmds.objExists(obj):
            continue
        try:
            world_matrix = None
            if preserve_current_pose:
                world_matrix = cmds.xform(
                    obj, query=True, worldSpace=True, matrix=True
                )
            for attr, value_key in (
                ("rotatePivot", "rotate_pivot"),
                ("scalePivot", "scale_pivot"),
                ("rotatePivotTranslate", "rotate_pivot_translate"),
                ("scalePivotTranslate", "scale_pivot_translate"),
            ):
                value = pivots.get(value_key)
                if value is not None:
                    cmds.setAttr(
                        "{}.{}".format(obj, attr),
                        *value,
                        type="double3"
                    )
            if world_matrix is not None:
                cmds.xform(obj, worldSpace=True, matrix=world_matrix)
        except Exception as exc:
            cmds.warning(
                "Temp Pivot: could not restore pivot for {}: {}".format(obj, exc)
            )


def _values_match(first, second, epsilon=TEMP_PIVOT_MATRIX_EPSILON):
    if first is None or second is None or len(first) != len(second):
        return False
    try:
        return all(
            abs(float(left) - float(right)) <= epsilon
            for left, right in zip(first, second)
        )
    except (TypeError, ValueError):
        return False


def _temp_pivot_objects_changed(state):
    """Return whether Maya's edit-pivot mode changed any real object pivot."""
    for obj, pivots in (state or {}).get("object_pivots", {}).items():
        if not cmds.objExists(obj):
            continue
        try:
            current_values = {
                "rotate_pivot": list(
                    cmds.xform(obj, query=True, objectSpace=True, rotatePivot=True)
                ),
                "scale_pivot": list(
                    cmds.xform(obj, query=True, objectSpace=True, scalePivot=True)
                ),
                "rotate_pivot_translate": list(
                    cmds.getAttr(obj + ".rotatePivotTranslate")[0]
                ),
                "scale_pivot_translate": list(
                    cmds.getAttr(obj + ".scalePivotTranslate")[0]
                ),
            }
        except Exception:
            continue
        for key, current_value in current_values.items():
            if not _values_match(pivots.get(key), current_value):
                return True
    return False


def _temp_pivot_dependency_nodes(objects):
    """Include parents whose animation changes the selected controls' world pose."""
    nodes = []
    seen = set()
    for obj in objects:
        current = obj
        while current and current not in seen:
            seen.add(current)
            nodes.append(current)
            parents = cmds.listRelatives(
                current, parent=True, fullPath=True
            ) or []
            current = parents[0] if parents else None
    return nodes


def _temp_pivot_sample_times(objects, state=None):
    """Sample only the interval actually edited while Temp Pivot was active."""
    selected_range = None
    if not cmds.about(batch=True):
        selected_range = selected_time_range()
    current_time = float(cmds.currentTime(query=True))

    if selected_range:
        start, end = selected_range
    else:
        changed_times = _temp_pivot_changed_key_times(state)
        if changed_times:
            start = min(changed_times)
            end = max(changed_times)
        else:
            start = current_time
            end = current_time

    range_start, range_end = float(start), float(end)
    start = min(range_start, range_end)
    end = max(range_start, range_end)
    integer_start = int(math.ceil(start))
    integer_end = int(math.floor(end))
    values = {round(start, 10), round(end, 10)}
    if integer_end >= integer_start:
        values.update(float(frame) for frame in range(integer_start, integer_end + 1))

    try:
        key_times = cmds.keyframe(
            objects, query=True, time=(start, end), timeChange=True
        ) or []
    except Exception:
        key_times = []
    for value in key_times:
        value = float(value)
        if start <= value <= end:
            values.add(round(value, 10))

    times = sorted(values)
    if len(times) > TEMP_PIVOT_MAX_BAKE_SAMPLES:
        raise RuntimeError(
            "Temp Pivot range has {} samples. Select a shorter Time Slider range "
            "(maximum {}).".format(len(times), TEMP_PIVOT_MAX_BAKE_SAMPLES)
        )
    return times


def _world_matrix_values(obj):
    return [
        float(value)
        for value in cmds.xform(obj, query=True, worldSpace=True, matrix=True)
    ]


def _sample_temp_pivot_world_matrices(objects, times):
    samples = {}
    current_time = cmds.currentTime(query=True)
    try:
        for frame in times:
            cmds.currentTime(frame, edit=True)
            for obj in objects:
                if cmds.objExists(obj):
                    samples.setdefault(obj, {})[frame] = _world_matrix_values(obj)
    finally:
        cmds.currentTime(current_time, edit=True)
    return samples


def _sample_temp_pivot_transform_values(objects, times):
    samples = {}
    current_time = cmds.currentTime(query=True)
    try:
        for frame in times:
            cmds.currentTime(frame, edit=True)
            for obj in objects:
                if not cmds.objExists(obj):
                    continue
                object_samples = samples.setdefault(obj, {})
                for attr in TEMP_BAKE_ATTRS:
                    plug = obj + "." + attr
                    if not cmds.objExists(plug):
                        continue
                    try:
                        object_samples.setdefault(attr, []).append(
                            (frame, float(cmds.getAttr(plug)))
                        )
                    except Exception:
                        pass
    finally:
        cmds.currentTime(current_time, edit=True)
    return samples


def _restore_transform_sample(obj, values):
    """Undo a temporary xform solve without touching connected anim curves."""
    for attr, value in values.items():
        plug = obj + "." + attr
        try:
            if not cmds.getAttr(plug, lock=True):
                cmds.setAttr(plug, value)
        except Exception:
            pass


def _solve_temp_pivot_transform_values(objects, times, world_samples, base_samples):
    """Solve channel values that reproduce the pre-reset world matrices."""
    final_samples = {}
    current_time = cmds.currentTime(query=True)
    try:
        for frame in times:
            cmds.currentTime(frame, edit=True)
            for obj in objects:
                desired_matrix = world_samples.get(obj, {}).get(frame)
                if not desired_matrix or not cmds.objExists(obj):
                    continue
                base_values = {
                    attr: dict(values).get(frame)
                    for attr, values in base_samples.get(obj, {}).items()
                }
                try:
                    cmds.xform(obj, worldSpace=True, matrix=desired_matrix)
                except Exception:
                    continue
                for attr in TEMP_BAKE_ATTRS:
                    plug = obj + "." + attr
                    if attr not in base_values or not cmds.objExists(plug):
                        continue
                    try:
                        final_samples.setdefault(obj, {}).setdefault(attr, []).append(
                            (frame, float(cmds.getAttr(plug)))
                        )
                    except Exception:
                        pass
                _restore_transform_sample(obj, {
                    attr: value for attr, value in base_values.items()
                    if value is not None
                })
    finally:
        cmds.currentTime(current_time, edit=True)
    return final_samples


def _capture_anim_layer_selection():
    selected = []
    for layer in cmds.ls(type="animLayer") or []:
        try:
            if cmds.animLayer(layer, query=True, selected=True):
                selected.append(layer)
        except Exception:
            pass
    return selected


def _restore_anim_layer_selection(selected_layers):
    for layer in cmds.ls(type="animLayer") or []:
        try:
            cmds.animLayer(layer, edit=True, selected=layer in selected_layers)
        except Exception:
            pass


def _bake_temp_pivot_compensation(state):
    """Restore edited pivots and preserve the resulting animation on a layer.

    Editing a Maya pivot changes the transform evaluation at every frame.  A
    one-frame restore therefore makes the visible animation jump when TEMP is
    turned off.  This records the evaluated motion before restoring the real
    pivots, then writes only the required delta to a dedicated additive layer.
    """
    objects = [
        obj for obj in (state or {}).get("objects", []) if cmds.objExists(obj)
    ]
    if not objects or not _temp_pivot_objects_changed(state):
        return None, 0

    times = _temp_pivot_sample_times(objects, state=state)
    world_samples = _sample_temp_pivot_world_matrices(objects, times)
    selected_layers = _capture_anim_layer_selection()
    current_time = cmds.currentTime(query=True)

    try:
        _restore_object_pivots(state, preserve_current_pose=False)
        base_samples = _sample_temp_pivot_transform_values(objects, times)
        final_samples = _solve_temp_pivot_transform_values(
            objects, times, world_samples, base_samples
        )
        if not final_samples:
            return None, 0

        layer = _create_temp_bake_layer(objects)
        key_count = _key_temp_bake_layer(
            layer, final_samples, base_samples, min(times), max(times)
        )
        return layer, key_count
    finally:
        cmds.currentTime(current_time, edit=True)
        _restore_anim_layer_selection(selected_layers)


def _stop_temp_pivot_monitor():
    global _temp_pivot_monitor, _temp_pivot_edit_filter

    timer = _temp_pivot_monitor
    _temp_pivot_monitor = None
    application = QtWidgets.QApplication.instance()
    event_filter = _temp_pivot_edit_filter
    _temp_pivot_edit_filter = None
    if application is not None and event_filter is not None:
        try:
            application.removeEventFilter(event_filter)
            event_filter.deleteLater()
        except RuntimeError:
            pass
    if application is not None:
        for existing in application.findChildren(
            QtCore.QTimer, "AnimKeyTempPivotMonitor"
        ):
            if existing is not timer:
                try:
                    existing.stop()
                    existing.deleteLater()
                except RuntimeError:
                    pass
    if timer is None:
        return
    try:
        timer.stop()
        timer.deleteLater()
    except (RuntimeError, AttributeError):
        pass


def _current_temp_pivot_position(state=None):
    try:
        if cmds.manipPivot(query=True, valid=True):
            position = _flat_vector(cmds.manipPivot(query=True, position=True))
            if position:
                return position
    except Exception:
        pass

    state = state or {}
    position = state.get("custom_pivot_position")
    if position:
        return list(position)

    for obj in reversed(state.get("objects", [])):
        if not cmds.objExists(obj):
            continue
        try:
            return list(
                cmds.xform(obj, query=True, worldSpace=True, rotatePivot=True)
            )
        except Exception:
            continue
    return None


def _finalize_temp_pivot_edit(pivot_position=None):
    """Leave pivot-edit mode while keeping the temporary pivot active.

    Maya can write real rotate/scale pivot attributes while the user positions
    the handle.  Do not restore those attributes here: restoring them before
    the animation has been sampled is what caused TEMP to change the pose.
    They are restored and compensated atomically when TEMP is switched off.
    """
    global _temp_pivot_restore_state

    state = _temp_pivot_restore_state
    if not state or state.get("pivot_edit_finished"):
        return False

    pivot_position = pivot_position or _current_temp_pivot_position(state)
    _save_temp_pivot_offsets_for_objects(state.get("objects", []), pivot_position)

    cmds.manipRotateContext(
        "Rotate",
        edit=True,
        useManipPivot=True,
        useCenterPivot=False,
        useObjectPivot=False,
        pinPivot=True,
    )
    if pivot_position:
        cmds.manipPivot(position=pivot_position)
    cmds.manipPivot(pinPivot=True)

    state["custom_pivot_position"] = (
        list(pivot_position) if pivot_position else None
    )
    state["edit_mode_seen"] = False
    state["pivot_edit_finished"] = True
    return True


def _monitor_temp_pivot_edit_mode():
    state = _temp_pivot_restore_state
    if not state:
        _stop_temp_pivot_monitor()
        return
    if state.get("pivot_edit_finished"):
        _stop_temp_pivot_monitor()
        return

    try:
        editing = bool(
            cmds.manipRotateContext(
                "Rotate", query=True, editPivotMode=True
            )
        )
    except Exception:
        return

    if editing:
        state["edit_mode_seen"] = True
        position = _current_temp_pivot_position(state)
        if position:
            state["custom_pivot_position"] = position
        return

    if state.get("edit_mode_seen") and not state.get("pivots_finalized"):
        try:
            _finalize_temp_pivot_edit()
            _stop_temp_pivot_monitor()
        except Exception as exc:
            cmds.warning(
                "Temp Pivot: could not finalize pivot edit: {}".format(exc)
            )


def _start_temp_pivot_monitor():
    global _temp_pivot_monitor, _temp_pivot_edit_filter

    _stop_temp_pivot_monitor()
    application = QtWidgets.QApplication.instance()
    if application is None:
        return

    timer = QtCore.QTimer(application)
    timer.setObjectName("AnimKeyTempPivotMonitor")
    timer.setInterval(TEMP_PIVOT_MONITOR_INTERVAL_MS)
    timer.timeout.connect(_monitor_temp_pivot_edit_mode)
    timer.start()
    _temp_pivot_monitor = timer

    event_filter = _TempPivotEditFilter(application)
    application.installEventFilter(event_filter)
    _temp_pivot_edit_filter = event_filter


def activate_temp_pivot(objects=None, pivot_mode="last", edit_pivot=True):
    """Activate Maya's non-destructive custom manipulator pivot."""
    global _temp_pivot_restore_state

    selected_objects = _selected_temp_pivot_objects(objects)
    if not selected_objects:
        om.MGlobal.displayWarning("Select one or more controls for Temp Pivot.")
        return None

    pivot_position = _temp_pivot_position(selected_objects, pivot_mode=pivot_mode)
    _temp_pivot_restore_state = _capture_temp_pivot_state(selected_objects)
    _temp_pivot_restore_state.update({
        "custom_pivot_position": list(pivot_position),
        "pivots_finalized": False,
        "edit_mode_seen": bool(edit_pivot),
        "pivot_edit_finished": not edit_pivot,
    })
    undo_open = False
    try:
        cmds.undoInfo(openChunk=True, chunkName="AnimKey Temp Pivot")
        undo_open = True

        cmds.setToolTo("RotateSuperContext")
        cmds.manipPivot(reset=True)
        cmds.manipRotateContext(
            "Rotate",
            edit=True,
            useManipPivot=True,
            useCenterPivot=False,
            useObjectPivot=False,
        )
        cmds.manipPivot(position=pivot_position)
        cmds.manipPivot(pinPivot=True)
        cmds.manipRotateContext("Rotate", edit=True, pinPivot=True)
        if edit_pivot and not cmds.manipRotateContext(
            "Rotate", query=True, editPivotMode=True
        ):
            cmds.ctxEditMode()
    except Exception as exc:
        _stop_temp_pivot_monitor()
        _temp_pivot_restore_state = None
        om.MGlobal.displayError("Temp Pivot error: {}".format(exc))
        return None
    finally:
        if undo_open:
            cmds.undoInfo(closeChunk=True)

    if edit_pivot:
        _start_temp_pivot_monitor()

    return {
        "objects": selected_objects,
        "pivot": pivot_position,
        "mode": pivot_mode,
    }


def deactivate_temp_pivot():
    """Clear TEMP while preserving animation made around the temporary pivot."""
    global _temp_pivot_restore_state

    _stop_temp_pivot_monitor()
    restore_state = _temp_pivot_restore_state or {}
    rotate_state = restore_state.get("rotate_context", {})
    undo_open = False
    try:
        cmds.undoInfo(openChunk=True, chunkName="AnimKey Temp Pivot Off")
        undo_open = True

        if cmds.manipRotateContext("Rotate", query=True, editPivotMode=True):
            if cmds.currentCtx() != "RotateSuperContext":
                cmds.setToolTo("RotateSuperContext")
            cmds.ctxEditMode()

        pivot_position = _current_temp_pivot_position(restore_state)
        _save_temp_pivot_offsets_for_objects(
            restore_state.get("objects", []), pivot_position
        )
        layer, keyed_channels = _bake_temp_pivot_compensation(restore_state)
        restore_state["pivots_finalized"] = True

        cmds.manipPivot(pinPivot=False)
        cmds.manipPivot(reset=True)
        cmds.manipRotateContext(
            "Rotate",
            edit=True,
            pinPivot=bool(rotate_state.get("pinPivot", False)),
            useManipPivot=bool(rotate_state.get("useManipPivot", False)),
            useCenterPivot=bool(rotate_state.get("useCenterPivot", False)),
            useObjectPivot=bool(rotate_state.get("useObjectPivot", False)),
        )

        if restore_state.get("manip_valid"):
            position = restore_state.get("manip_position")
            orientation = restore_state.get("manip_orientation")
            if position:
                cmds.manipPivot(position=position)
            if orientation:
                cmds.manipPivot(orientation=orientation)
            cmds.manipPivot(
                pinPivot=bool(restore_state.get("manip_pinned", False))
            )

        original_context = restore_state.get("tool_context")
        if original_context:
            cmds.setToolTo(original_context)

        if (
            rotate_state.get("editPivotMode")
            and original_context == "RotateSuperContext"
            and not cmds.manipRotateContext(
                "Rotate", query=True, editPivotMode=True
            )
        ):
            cmds.ctxEditMode()

        if layer and keyed_channels:
            try:
                cmds.inViewMessage(
                    amg=(
                        "<hl>Temp Pivot</hl> animation preserved on "
                        "<hl>{}</hl>".format(layer)
                    ),
                    pos="midCenterTop",
                    fade=True,
                )
            except Exception:
                pass

        _temp_pivot_restore_state = None
        return True
    except Exception as exc:
        om.MGlobal.displayError("Temp Pivot reset error: {}".format(exc))
        return False
    finally:
        if undo_open:
            cmds.undoInfo(closeChunk=True)


# ============================================================
# CORE LOGIC (Matrix-Based)
# ============================================================

def tag(node):
    if not cmds.attributeQuery("tempTag", node=node, exists=True):
        cmds.addAttr(node, ln="tempTag", dt="string")
    cmds.setAttr(node + ".tempTag", TOOL_TAG, type="string", lock=True)

def add_string_attr(node, attr, value):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, ln=attr, dt="string")
    cmds.setAttr(node + "." + attr, value, type="string")

def add_follow_attr(ctrl, default=True):
    if not cmds.attributeQuery("follow", node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln="follow", at="bool", keyable=True, dv=1 if default else 0)
    cmds.setAttr(ctrl + ".follow", bool(default))

def _create_temp_controls_container():
    return None

def _parent_under_temp_container(node):
    return

def _cleanup_empty_temp_container():
    legacy_container = "animkey_temp_controls"
    if not cmds.objExists(legacy_container):
        return
    try:
        children = cmds.listRelatives(legacy_container, children=True, fullPath=True) or []
        temp_controls = cmds.ls("TEMP_CTRL*", type="transform") or []
        if not children and not temp_controls:
            cmds.delete(legacy_container)
    except Exception:
        pass

def _read_json_attr(node, attr):
    if not cmds.objExists(node) or not cmds.attributeQuery(attr, node=node, exists=True):
        return {}
    try:
        value = cmds.getAttr(node + "." + attr)
        return json.loads(value) if value else {}
    except Exception:
        return {}

def _shortest_angle_delta(final_value, base_value, previous_delta=None):
    delta = (final_value - base_value + 180.0) % 360.0 - 180.0
    if previous_delta is not None:
        while delta - previous_delta > 180.0:
            delta -= 360.0
        while delta - previous_delta < -180.0:
            delta += 360.0
    return delta

def _temp_anim_layer_name():
    if not cmds.objExists(TEMP_BAKE_LAYER_BASE):
        return TEMP_BAKE_LAYER_BASE
    index = 2
    while True:
        name = "TempControl{:02d}{}".format(index, TEMP_BAKE_LAYER_SUFFIX)
        if not cmds.objExists(name):
            return name
        index += 1

def _interval_sample_times(start_frame, end_frame):
    """Return dense UI-frame samples while preserving fractional endpoints."""
    start = min(float(start_frame), float(end_frame))
    end = max(float(start_frame), float(end_frame))
    values = {round(start, 10), round(end, 10)}
    first_integer = int(math.ceil(start))
    last_integer = int(math.floor(end))
    if last_integer >= first_integer:
        values.update(float(frame) for frame in range(first_integer, last_integer + 1))
    return sorted(values)


def _sample_transform_values(objects, start_frame, end_frame):
    samples = {}
    current_time = cmds.currentTime(query=True)
    try:
        for frame in _interval_sample_times(start_frame, end_frame):
            cmds.currentTime(frame, edit=True)
            for obj in objects:
                if not cmds.objExists(obj):
                    continue
                obj_samples = samples.setdefault(obj, {})
                for attr in TEMP_BAKE_ATTRS:
                    plug = obj + "." + attr
                    if not cmds.objExists(plug):
                        continue
                    try:
                        obj_samples.setdefault(attr, []).append((frame, cmds.getAttr(plug)))
                    except Exception:
                        pass
    finally:
        cmds.currentTime(current_time, edit=True)
    return samples

def _disconnect_temp_connections(obj):
    for attr in TEMP_BAKE_ATTRS:
        full_attr = obj + "." + attr
        conns = cmds.listConnections(full_attr, source=True,
                                      plugs=True, destination=False) or []
        for conn in conns:
            conn_node = conn.split(".")[0]
            if "_TEMP_" in conn_node:
                try:
                    cmds.disconnectAttr(conn, full_attr)
                except Exception:
                    pass

def _restore_original_animation(controlled, temp_controls=None):
    control_original_data = _original_data_from_controls(temp_controls)

    for obj in controlled:
        if not cmds.objExists(obj):
            continue

        _disconnect_temp_connections(obj)
        try:
            long_obj = (cmds.ls(obj, long=True) or [obj])[0]
        except Exception:
            long_obj = obj
        ctrl_data = control_original_data.get(long_obj, {})
        orig_connections = _read_json_attr(obj, "tempOrigConn") or ctrl_data.get("connections", {})
        orig_values = _read_json_attr(obj, "tempOrigValues") or ctrl_data.get("values", {})

        for attr in TEMP_BAKE_ATTRS:
            full_attr = obj + "." + attr
            source = orig_connections.get(attr)
            if source and cmds.objExists(source) and cmds.objExists(full_attr):
                try:
                    if not cmds.isConnected(source, full_attr):
                        cmds.connectAttr(source, full_attr, force=True)
                    continue
                except Exception:
                    pass

            if attr in orig_values and cmds.objExists(full_attr):
                try:
                    if not cmds.getAttr(full_attr, lock=True):
                        cmds.setAttr(full_attr, orig_values[attr])
                except Exception:
                    pass

def _create_temp_bake_layer(controlled):
    layer = cmds.animLayer(
        _temp_anim_layer_name(),
        override=False,
        passthrough=True
    )

    for existing_layer in cmds.ls(type="animLayer") or []:
        try:
            cmds.animLayer(existing_layer, edit=True, selected=False, preferred=False)
        except Exception:
            pass

    cmds.animLayer(layer, edit=True, selected=True, preferred=True, mute=False, weight=1.0)

    for obj in controlled:
        if not cmds.objExists(obj):
            continue
        for attr in TEMP_BAKE_ATTRS:
            plug = obj + "." + attr
            if not cmds.objExists(plug):
                continue
            try:
                cmds.animLayer(layer, edit=True, attribute=plug)
            except Exception:
                pass

    return layer

def _key_temp_bake_layer(layer, final_samples, base_samples, start_frame, end_frame):
    keyed_channels = 0
    guard_frames = [float(start_frame) - 1.0, float(end_frame) + 1.0]

    for obj, obj_samples in final_samples.items():
        if not cmds.objExists(obj):
            continue

        for attr, values in obj_samples.items():
            base_values = dict(base_samples.get(obj, {}).get(attr, []))
            if not base_values:
                continue

            previous_delta = None
            is_rotate = attr.startswith("rotate")

            for guard_frame in guard_frames:
                try:
                    cmds.setKeyframe(
                        obj,
                        attribute=attr,
                        time=(guard_frame, guard_frame),
                        animLayer=layer,
                        noResolve=True,
                        value=0.0
                    )
                except Exception:
                    pass

            for frame, final_value in values:
                if frame not in base_values:
                    continue
                base_value = base_values[frame]
                if is_rotate:
                    delta = _shortest_angle_delta(final_value, base_value, previous_delta)
                    previous_delta = delta
                else:
                    delta = final_value - base_value

                try:
                    cmds.setKeyframe(
                        obj,
                        attribute=attr,
                        time=(frame, frame),
                        animLayer=layer,
                        noResolve=True,
                        value=delta
                    )
                    keyed_channels += 1
                except Exception:
                    pass

    try:
        cmds.animLayer(forceUIRefresh=True)
    except Exception:
        pass

    return keyed_channels

def get_tagged():
    tagged = cmds.ls("*.tempTag", o=True, long=True)
    return tagged if tagged else []

def _unique_existing(nodes):
    result = []
    seen = set()
    for node in nodes or []:
        if not node or not cmds.objExists(node):
            continue
        try:
            long_name = (cmds.ls(node, long=True) or [node])[0]
        except Exception:
            long_name = node
        if long_name in seen:
            continue
        seen.add(long_name)
        result.append(long_name)
    return result

def _owner_names(temp_controls):
    names = set()
    for ctrl in temp_controls or []:
        names.add(ctrl)
        names.add(ctrl.split("|")[-1])
        try:
            long_name = (cmds.ls(ctrl, long=True) or [ctrl])[0]
            names.add(long_name)
            names.add(long_name.split("|")[-1])
        except Exception:
            pass
    return names

def _owner_matches(node, owner_names):
    if not owner_names:
        return True
    if not cmds.objExists(node) or not cmds.attributeQuery("tempControlOwner", node=node, exists=True):
        return False
    try:
        owner = cmds.getAttr(node + ".tempControlOwner")
        return owner in owner_names or owner.split("|")[-1] in owner_names
    except Exception:
        return False

def _controlled_from_temp_network(temp_controls=None):
    owner_names = _owner_names(temp_controls)
    controlled = []

    for node in get_tagged():
        if not cmds.objExists(node) or not _owner_matches(node, owner_names):
            continue

        try:
            dest_plugs = cmds.listConnections(
                node,
                source=False,
                destination=True,
                plugs=True
            ) or []
        except Exception:
            dest_plugs = []

        for plug in dest_plugs:
            if "." not in plug:
                continue
            obj, attr = plug.rsplit(".", 1)
            if attr not in TEMP_BAKE_ATTRS or not cmds.objExists(obj):
                continue
            # TEMP nodes are legitimate destinations in the matrix and
            # follow networks, but they are implementation details rather
            # than controls that Smart Bake is allowed to touch.
            try:
                is_temp_node = (
                    obj.split("|")[-1].startswith("TEMP_CTRL")
                    or (
                        cmds.attributeQuery("tempTag", node=obj, exists=True)
                        and cmds.getAttr(obj + ".tempTag") == TOOL_TAG
                    )
                )
            except Exception:
                is_temp_node = obj.split("|")[-1].startswith("TEMP_CTRL")
            if not is_temp_node:
                controlled.append(obj)

    return _unique_existing(controlled)

def _remember_controlled_object(ctrl, obj):
    data = []
    if cmds.attributeQuery("tempControlledObjects", node=ctrl, exists=True):
        try:
            data = json.loads(cmds.getAttr(ctrl + ".tempControlledObjects") or "[]")
        except Exception:
            data = []

    try:
        long_obj = (cmds.ls(obj, long=True) or [obj])[0]
    except Exception:
        long_obj = obj

    if long_obj not in data:
        data.append(long_obj)

    if not cmds.attributeQuery("tempControlledObjects", node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln="tempControlledObjects", dt="string")
    cmds.setAttr(ctrl + ".tempControlledObjects", json.dumps(data), type="string")

def _remember_original_data(ctrl, obj, connections, values):
    data = {}
    if cmds.attributeQuery("tempOriginalData", node=ctrl, exists=True):
        try:
            data = json.loads(cmds.getAttr(ctrl + ".tempOriginalData") or "{}")
        except Exception:
            data = {}

    try:
        long_obj = (cmds.ls(obj, long=True) or [obj])[0]
    except Exception:
        long_obj = obj

    data[long_obj] = {
        "connections": connections or {},
        "values": values or {}
    }

    if not cmds.attributeQuery("tempOriginalData", node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln="tempOriginalData", dt="string")
    cmds.setAttr(ctrl + ".tempOriginalData", json.dumps(data), type="string")

def _original_data_from_controls(temp_controls=None):
    data = {}
    controls = temp_controls or [
        node for node in get_tagged()
        if cmds.objExists(node) and node.split("|")[-1].startswith("TEMP_CTRL")
    ]

    for ctrl in controls:
        if not cmds.objExists(ctrl) or not cmds.attributeQuery("tempOriginalData", node=ctrl, exists=True):
            continue
        try:
            ctrl_data = json.loads(cmds.getAttr(ctrl + ".tempOriginalData") or "{}")
            data.update(ctrl_data)
        except Exception:
            pass

    return data

def _controlled_from_control_attrs(temp_controls=None):
    controlled = []
    controls = temp_controls or [
        node for node in get_tagged()
        if cmds.objExists(node) and node.split("|")[-1].startswith("TEMP_CTRL")
    ]

    for ctrl in controls:
        if not cmds.objExists(ctrl) or not cmds.attributeQuery("tempControlledObjects", node=ctrl, exists=True):
            continue
        try:
            controlled.extend(json.loads(cmds.getAttr(ctrl + ".tempControlledObjects") or "[]"))
        except Exception:
            pass

    return _unique_existing(controlled)

def _temp_control_cleanup_nodes(temp_controls=None):
    nodes = []
    transforms = cmds.ls("TEMP_CTRL*", type="transform", long=True) or []

    if temp_controls:
        owner_names = _owner_names(temp_controls)
        for node in transforms:
            short = node.split("|")[-1]
            for owner in owner_names:
                owner_short = owner.split("|")[-1]
                if short == owner_short or short == owner_short + "_OFFSET":
                    nodes.append(node)
                    break
    else:
        nodes.extend(transforms)

    return _unique_existing(nodes)

def get_frame_range():
    """Obtiene el rango del timeline."""
    start = int(cmds.playbackOptions(query=True, minTime=True))
    end = int(cmds.playbackOptions(query=True, maxTime=True))
    return start, end

def _all_temp_controls():
    return _unique_existing([
        node for node in get_tagged()
        if cmds.objExists(node)
        and node.split("|")[-1].startswith("TEMP_CTRL")
        and not node.split("|")[-1].endswith("_OFFSET")
    ])


def _transform_key_times(nodes):
    key_times = set()
    curve_state = _capture_temp_pivot_curve_state(_unique_existing(nodes))
    for curve_data in curve_state.values():
        key_times.update(
            round(float(value), 10)
            for value in curve_data.get("times", [])
        )
    return sorted(key_times)


def _explicit_temp_range():
    if cmds.about(batch=True):
        return None
    try:
        value = selected_time_range()
    except Exception:
        value = None
    if not value:
        return None
    return tuple(sorted((float(value[0]), float(value[1]))))


def _has_transform_driver(nodes):
    """Detect non-curve upstream motion that requires the playback range."""
    for node in _unique_existing(nodes):
        for attr in TEMP_BAKE_ATTRS:
            try:
                sources = cmds.listConnections(
                    node + "." + attr,
                    source=True,
                    destination=False,
                    skipConversionNodes=True,
                ) or []
            except Exception:
                sources = []
            for source in sources:
                try:
                    if not cmds.nodeType(source).startswith("animCurve"):
                        return True
                except Exception:
                    return True
    return False


def _temp_control_creation_range(objects):
    """Smallest safe source-motion range used to build temporary locators."""
    explicit_range = _explicit_temp_range()
    if explicit_range:
        return explicit_range

    dependency_nodes = _temp_pivot_dependency_nodes(objects)
    key_times = _transform_key_times(dependency_nodes)
    if key_times:
        return float(min(key_times)), float(max(key_times))

    if _has_transform_driver(dependency_nodes):
        start, end = get_frame_range()
        return float(start), float(end)

    current = float(cmds.currentTime(query=True))
    return current, current


def get_smart_frame_range(objects, temp_controls=None):
    """Return only the interval explicitly edited with the temp control.

    Locator and controlled-object curves describe the original shot, not the
    animator's Temp Pivot edit.  Including them was the reason Smart Bake
    wrote compensation across the whole playback range.
    """
    explicit_range = _explicit_temp_range()
    if explicit_range:
        return explicit_range

    controls = temp_controls or get_selected_temp_controls() or _all_temp_controls()
    key_times = _transform_key_times(controls)
    if key_times:
        return float(min(key_times)), float(max(key_times))

    current = float(cmds.currentTime(query=True))
    return current, current

def get_controlled_objects():
    """Buscar todos los objetos marcados como controlados.
    Usa atributos guardados y conexiones temporales como fallback."""
    result = cmds.ls("*.tempControlled", o=True, long=True) or []
    controlled = []
    for obj in result:
        try:
            if cmds.getAttr(obj + ".tempControlled"):
                controlled.append(obj)
        except Exception:
            pass

    controlled.extend(_controlled_from_control_attrs())
    controlled.extend(_controlled_from_temp_network())
    return _unique_existing(controlled)

def get_selected_temp_controls():
    selected = cmds.ls(selection=True, long=True) or []
    result = []
    for node in selected:
        if not cmds.objExists(node):
            continue
        if cmds.objectType(node, isAType="shape"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if parents:
                node = parents[0]
        try:
            if cmds.attributeQuery("tempTag", node=node, exists=True) and cmds.getAttr(node + ".tempTag") == TOOL_TAG:
                if node.split("|")[-1].startswith("TEMP_CTRL"):
                    result.append(node)
        except Exception:
            pass
    return _unique_existing(result)

def filter_controlled_by_temp_controls(controlled, temp_controls):
    if not temp_controls:
        return controlled
    owner_names = set()
    for ctrl in temp_controls:
        owner_names.add(ctrl)
        owner_names.add(ctrl.split("|")[-1])
    filtered = []
    for obj in controlled:
        if not cmds.objExists(obj):
            continue
        try:
            if cmds.attributeQuery("tempControlOwner", node=obj, exists=True):
                owner = cmds.getAttr(obj + ".tempControlOwner")
                if owner in owner_names or owner.split("|")[-1] in owner_names:
                    filtered.append(obj)
        except Exception:
            pass
    filtered.extend(_controlled_from_control_attrs(temp_controls))
    filtered.extend(_controlled_from_temp_network(temp_controls))
    return _unique_existing(filtered)


def _can_drive_temp_attr(obj, attr):
    plug = obj + "." + attr
    try:
        return cmds.objExists(plug) and not cmds.getAttr(plug, lock=True)
    except Exception:
        return False

def create_temp_control_for_object(ctrl, ctrl_grp, obj, start_frame, end_frame):
    base_name = obj.split(":")[-1].split("|")[-1]
    obj_parent = cmds.listRelatives(obj, parent=True, fullPath=True)
    is_joint = cmds.nodeType(obj) == "joint"
    
    # --- Marcar objeto como controlado ---
    _remember_controlled_object(ctrl, obj)
    try:
        if not cmds.attributeQuery("tempControlled", node=obj, exists=True):
            cmds.addAttr(obj, ln="tempControlled", at="bool")
        cmds.setAttr(obj + ".tempControlled", True)
        add_string_attr(obj, "tempControlOwner", ctrl)
    except Exception as e:
        cmds.warning("Temp Control: could not tag {} directly: {}".format(obj, e))
    
    # --- 1. Crear locator world-space ---
    loc = cmds.spaceLocator(name=base_name + "_TEMP_loc")[0]
    tag(loc)
    add_string_attr(loc, "tempControlOwner", ctrl)
    cmds.setAttr(loc + ".visibility", 0)
    
    # --- 2. Constraint LOC -> objeto (LOC sigue al objeto) ---
    temp_con = cmds.parentConstraint(obj, loc, maintainOffset=False)[0]
    
    # --- 3. Bake LOC para capturar animacion world-space ---
    cmds.bakeResults(
        loc,
        time=(start_frame, end_frame),
        sampleBy=1,
        simulation=True,
        minimizeRotation=True,
        disableImplicitControl=True,
        preserveOutsideKeys=False,
        sparseAnimCurveBake=False,
        controlPoints=False,
        shape=False
    )
    
    # --- 4. Borrar constraint temporal ---
    cmds.delete(temp_con)
    _parent_under_temp_container(loc)
    
    # --- 5. Guardar y desconectar animacion del objeto ---
    orig_connections = {}
    attrs_to_check = list(TEMP_BAKE_ATTRS)
    orig_values = {}
    for attr in attrs_to_check:
        try:
            orig_values[attr] = cmds.getAttr(obj + "." + attr)
        except Exception:
            pass
    
    for attr in attrs_to_check:
        full_attr = obj + "." + attr
        if not _can_drive_temp_attr(obj, attr):
            continue
        conns = cmds.listConnections(full_attr, source=True, 
                                      plugs=True, destination=False)
        if conns:
            orig_connections[attr] = conns[0]
            try:
                cmds.disconnectAttr(conns[0], full_attr)
            except Exception:
                pass
    
    # Guardar conexiones para restauracion
    if orig_connections:
        if not cmds.attributeQuery("tempOrigConn", node=obj, exists=True):
            cmds.addAttr(obj, ln="tempOrigConn", dt="string")
        cmds.setAttr(obj + ".tempOrigConn", 
                     json.dumps(orig_connections), type="string")

    if orig_values:
        if not cmds.attributeQuery("tempOrigValues", node=obj, exists=True):
            cmds.addAttr(obj, ln="tempOrigValues", dt="string")
        cmds.setAttr(obj + ".tempOrigValues",
                     json.dumps(orig_values), type="string")

    _remember_original_data(ctrl, obj, orig_connections, orig_values)
    
    # --- 6. Construir red de matrices ---
    mult = cmds.createNode("multMatrix", name=base_name + "_TEMP_multMat")
    tag(mult)
    add_string_attr(mult, "tempControlOwner", ctrl)
    _parent_under_temp_container(mult)
    
    idx = 0
    cmds.connectAttr(loc + ".worldMatrix[0]", 
                     mult + ".matrixIn[{}]".format(idx))
    idx += 1
    
    cmds.connectAttr(ctrl_grp + ".worldInverseMatrix", 
                     mult + ".matrixIn[{}]".format(idx))
    idx += 1
    
    cmds.connectAttr(ctrl + ".worldMatrix[0]", 
                     mult + ".matrixIn[{}]".format(idx))
    idx += 1
    
    if obj_parent:
        cmds.connectAttr(obj_parent[0] + ".worldInverseMatrix[0]", 
                         mult + ".matrixIn[{}]".format(idx))
        idx += 1
    
    # --- 7. Decompose matrix ---
    decomp = cmds.createNode("decomposeMatrix", name=base_name + "_TEMP_decomp")
    tag(decomp)
    add_string_attr(decomp, "tempControlOwner", ctrl)
    _parent_under_temp_container(decomp)
    
    ro = cmds.getAttr(obj + ".rotateOrder")
    cmds.setAttr(decomp + ".inputRotateOrder", ro)
    cmds.connectAttr(mult + ".matrixSum", decomp + ".inputMatrix")
    
    # --- 8. Conectar resultado al objeto ---
    if is_joint:
        jo_x = cmds.getAttr(obj + ".jointOrientX")
        jo_y = cmds.getAttr(obj + ".jointOrientY")
        jo_z = cmds.getAttr(obj + ".jointOrientZ")
        
        has_joint_orient = (abs(jo_x) > 0.001 or 
                           abs(jo_y) > 0.001 or 
                           abs(jo_z) > 0.001)
        
        if has_joint_orient:
            jo_compose = cmds.createNode("composeMatrix", 
                                         name=base_name + "_TEMP_joCompose")
            tag(jo_compose)
            add_string_attr(jo_compose, "tempControlOwner", ctrl)
            _parent_under_temp_container(jo_compose)
            cmds.setAttr(jo_compose + ".inputRotateX", jo_x)
            cmds.setAttr(jo_compose + ".inputRotateY", jo_y)
            cmds.setAttr(jo_compose + ".inputRotateZ", jo_z)
            cmds.setAttr(jo_compose + ".inputRotateOrder", ro)

            # Maya has no built-in ``inverseMatrix`` dependency node.  Build
            # the joint-orient inverse once from composeMatrix instead, then
            # feed it directly into multMatrix.  This keeps the decompose
            # result in the joint's rotate space on every supported Maya.
            jo_matrix = _flat_vector(
                cmds.getAttr(jo_compose + ".outputMatrix")
            )
            jo_inverse = list(om.MMatrix(jo_matrix).inverse())
            
            mult_jo = cmds.createNode("multMatrix", 
                                      name=base_name + "_TEMP_multJO")
            tag(mult_jo)
            add_string_attr(mult_jo, "tempControlOwner", ctrl)
            _parent_under_temp_container(mult_jo)
            cmds.connectAttr(mult + ".matrixSum", 
                           mult_jo + ".matrixIn[0]")
            cmds.setAttr(
                mult_jo + ".matrixIn[1]", *jo_inverse, type="matrix"
            )
            
            decomp_jo = cmds.createNode("decomposeMatrix", 
                                        name=base_name + "_TEMP_decompJO")
            tag(decomp_jo)
            add_string_attr(decomp_jo, "tempControlOwner", ctrl)
            _parent_under_temp_container(decomp_jo)
            cmds.setAttr(decomp_jo + ".inputRotateOrder", ro)
            cmds.connectAttr(mult_jo + ".matrixSum", 
                           decomp_jo + ".inputMatrix")
            
            for axis in ['X', 'Y', 'Z']:
                if _can_drive_temp_attr(obj, "translate" + axis):
                    cmds.connectAttr(
                        decomp + ".outputTranslate" + axis,
                        obj + ".translate" + axis, force=True)
                if _can_drive_temp_attr(obj, "rotate" + axis):
                    cmds.connectAttr(
                        decomp_jo + ".outputRotate" + axis,
                        obj + ".rotate" + axis, force=True)
        else:
            for axis in ['X', 'Y', 'Z']:
                if _can_drive_temp_attr(obj, "translate" + axis):
                    cmds.connectAttr(
                        decomp + ".outputTranslate" + axis,
                        obj + ".translate" + axis, force=True)
                if _can_drive_temp_attr(obj, "rotate" + axis):
                    cmds.connectAttr(
                        decomp + ".outputRotate" + axis,
                        obj + ".rotate" + axis, force=True)
    else:
        for axis in ['X', 'Y', 'Z']:
            if _can_drive_temp_attr(obj, "translate" + axis):
                cmds.connectAttr(
                    decomp + ".outputTranslate" + axis,
                    obj + ".translate" + axis, force=True)
            if _can_drive_temp_attr(obj, "rotate" + axis):
                cmds.connectAttr(
                    decomp + ".outputRotate" + axis,
                    obj + ".rotate" + axis, force=True)
    
    return loc

def create_follow_system(ctrl, grp, locators, enabled=True):
    if not locators:
        return None
    add_follow_attr(ctrl, default=enabled)
    follow_con = cmds.parentConstraint(*(locators + [grp]), maintainOffset=True)[0]
    tag(follow_con)
    add_string_attr(follow_con, "tempControlOwner", ctrl)
    _parent_under_temp_container(follow_con)
    weights = cmds.parentConstraint(follow_con, query=True, weightAliasList=True) or []
    for weight_attr in weights:
        try:
            cmds.connectAttr(ctrl + ".follow", follow_con + "." + weight_attr, force=True)
        except Exception:
            pass
    return follow_con

def create_group_control(objects, pivot_mode="center", follow=True):
    if not objects:
        return None
    
    objects = [obj for obj in objects if cmds.objExists(obj)]
    if not objects:
        return None
    
    _create_temp_controls_container()
    start_frame, end_frame = _temp_control_creation_range(objects)
    
    # Determinar posicion y rotacion del control
    ctrl_rot = [0, 0, 0]
    if pivot_mode == "last":
        ctrl_pos = cmds.xform(objects[-1], q=True, ws=True, 
                              rotatePivot=True)
        ctrl_rot = cmds.xform(objects[-1], q=True, ws=True, 
                              rotation=True)
    else:
        # Centro (promedio de pivotes)
        positions = [cmds.xform(obj, q=True, ws=True, rotatePivot=True) 
                     for obj in objects]
        ctrl_pos = [sum(p[i] for p in positions) / len(positions) 
                    for i in range(3)]
    
    # Nombre unico
    ctrl_name = "TEMP_CTRL"
    existing = cmds.ls("TEMP_CTRL*", type="transform")
    if existing:
        ctrl_name = "{}_{}".format(ctrl_name, len(existing) + 1)
    
    # Crear control (NURBS circle)
    ctrl = cmds.circle(name=ctrl_name, radius=4, 
                       normal=(0, 1, 0), sections=16)[0]
    
    shape = cmds.listRelatives(ctrl, shapes=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17)
    
    # Posicionar control en world-space
    cmds.xform(ctrl, ws=True, translation=ctrl_pos)
    cmds.xform(ctrl, ws=True, rotation=ctrl_rot)
    
    # Grupo offset (absorbe la posicion, ctrl queda en cero local)
    grp = cmds.group(ctrl, name=ctrl_name + "_OFFSET")
    tag(ctrl)
    tag(grp)
    add_string_attr(ctrl, "tempControlOwner", ctrl)
    add_string_attr(grp, "tempControlOwner", ctrl)
    add_follow_attr(ctrl, default=follow)
    
    # Freeze control para que sus valores locales sean cero
    cmds.makeIdentity(ctrl, apply=True, translate=True, 
                      rotate=True, scale=True)
    _parent_under_temp_container(grp)
    
    # Procesar objetos
    success = 0
    locators = []
    for obj in objects:
        try:
            loc = create_temp_control_for_object(
                ctrl, grp, obj, start_frame, end_frame)
            if loc:
                locators.append(loc)
            success += 1
        except Exception as e:
            cmds.warning("Temp Control: failed to connect {}: {}".format(obj, e))

    if success:
        create_follow_system(ctrl, grp, locators, enabled=follow)
    else:
        cmds.warning("Temp Control: no objects were connected.")
    
    cmds.select(ctrl, replace=True)
    return ctrl

def create_individual_controls(objects):
    if not objects:
        return []
    
    _create_temp_controls_container()
    start_frame, end_frame = _temp_control_creation_range(objects)
    controls = []
    
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        
        base_name = obj.split(":")[-1].split("|")[-1]
        ctrl_name = "TEMP_CTRL_{}".format(base_name)
        
        ctrl = cmds.circle(name=ctrl_name, radius=2.5, 
                          normal=(0, 1, 0), sections=12)[0]
        
        pos = cmds.xform(obj, q=True, ws=True, rotatePivot=True)
        rot = cmds.xform(obj, q=True, ws=True, rotation=True)
        cmds.xform(ctrl, ws=True, translation=pos)
        cmds.xform(ctrl, ws=True, rotation=rot)
        
        shape = cmds.listRelatives(ctrl, shapes=True)[0]
        cmds.setAttr(shape + ".overrideEnabled", 1)
        cmds.setAttr(shape + ".overrideColor", 13)
        
        grp = cmds.group(ctrl, name=ctrl_name + "_OFFSET")
        tag(ctrl)
        tag(grp)
        add_string_attr(ctrl, "tempControlOwner", ctrl)
        add_string_attr(grp, "tempControlOwner", ctrl)
        add_follow_attr(ctrl, default=True)
        
        cmds.makeIdentity(ctrl, apply=True, translate=True, 
                          rotate=True, scale=True)
        _parent_under_temp_container(grp)
        
        try:
            loc = create_temp_control_for_object(
                ctrl, grp, obj, start_frame, end_frame)
            # For individual mode: constrain the group to follow
            # the object's baked locator so the control "sticks"
            # to the animated object.
            if loc:
                create_follow_system(ctrl, grp, [loc], enabled=True)
            controls.append(ctrl)
        except Exception as e:
            cmds.warning("Temp Control: failed to connect {}: {}".format(obj, e))
    
    if controls:
        cmds.select(controls, replace=True)
    
    return controls

def smart_bake_and_delete():
    selected_temp_controls = get_selected_temp_controls()
    controlled = get_controlled_objects()
    controlled = filter_controlled_by_temp_controls(controlled, selected_temp_controls)
    if not controlled:
        orphan_nodes = _temp_control_cleanup_nodes(selected_temp_controls if selected_temp_controls else None)
        if orphan_nodes or get_tagged():
            _cleanup_temp_system([], selected_temp_controls if selected_temp_controls else None)
            try:
                cmds.inViewMessage(
                    amg="<hl>Temp Control cleanup</hl> complete",
                    pos='midCenter',
                    fade=True
                )
            except Exception:
                pass
            return "Temp Control cleanup"
        if selected_temp_controls:
            cmds.warning("Temp Control: selected temp control has no connected objects to bake.")
        else:
            cmds.warning("Temp Control: no active temp controls found.")
        return False
    
    active_temp_controls = (
        selected_temp_controls if selected_temp_controls else _all_temp_controls()
    )
    start_frame, end_frame = get_smart_frame_range(
        controlled, active_temp_controls
    )
    selected_layers = _capture_anim_layer_selection()

    cmds.undoInfo(openChunk=True)
    try:
        final_samples = _sample_transform_values(controlled, start_frame, end_frame)

        _restore_original_animation(controlled, selected_temp_controls)
        base_samples = _sample_transform_values(controlled, start_frame, end_frame)

        layer = _create_temp_bake_layer(controlled)
        keyed_channels = _key_temp_bake_layer(
            layer,
            final_samples,
            base_samples,
            start_frame,
            end_frame
        )

        if keyed_channels < 1:
            try:
                cmds.delete(layer)
            except Exception:
                pass
            return False

        _cleanup_temp_system(controlled, selected_temp_controls if selected_temp_controls else None)
        existing_controlled = [obj for obj in controlled if cmds.objExists(obj)]
        if existing_controlled:
            cmds.select(existing_controlled, replace=True)

        try:
            cmds.inViewMessage(
                amg="<hl>Temp Control baked</hl> to <hl>{}</hl>".format(layer),
                pos='midCenter',
                fade=True
            )
        except Exception:
            pass

        return layer
    except Exception as e:
        try:
            om.MGlobal.displayError("Temp Control Smart Bake error: {}".format(e))
        except Exception:
            pass
        return False
    finally:
        _restore_anim_layer_selection(selected_layers)
        cmds.undoInfo(closeChunk=True)

def _cleanup_temp_system(controlled=None, temp_controls=None):
    if controlled is None:
        controlled = get_controlled_objects()
    
    for obj in controlled:
        if not cmds.objExists(obj):
            continue
        try:
            # Desconectar conexiones temporales
            for attr in ['translateX', 'translateY', 'translateZ',
                         'rotateX', 'rotateY', 'rotateZ']:
                full_attr = obj + "." + attr
                conns = cmds.listConnections(full_attr, source=True, 
                                              plugs=True, destination=False)
                if conns:
                    for conn in conns:
                        conn_node = conn.split(".")[0]
                        if "_TEMP_" in conn_node:
                            try:
                                cmds.disconnectAttr(conn, full_attr)
                            except:
                                pass
            
            # Limpiar atributos
            for temp_attr in ["tempControlled", "tempOrigConn", "tempOrigValues", "tempControlOwner"]:
                if cmds.attributeQuery(temp_attr, node=obj, exists=True):
                    try:
                        cmds.setAttr(obj + "." + temp_attr, lock=False)
                        cmds.deleteAttr(obj + "." + temp_attr)
                    except:
                        pass
        except:
            pass
    
    tagged = get_tagged()
    if temp_controls:
        owner_names = set()
        for ctrl in temp_controls:
            owner_names.add(ctrl)
            owner_names.add(ctrl.split("|")[-1])
        tagged_filtered = []
        for node in tagged:
            try:
                if cmds.attributeQuery("tempControlOwner", node=node, exists=True):
                    owner = cmds.getAttr(node + ".tempControlOwner")
                    if owner in owner_names or owner.split("|")[-1] in owner_names:
                        tagged_filtered.append(node)
            except Exception:
                pass
        tagged = tagged_filtered
    tagged.extend(_temp_control_cleanup_nodes(temp_controls))
    tagged = _unique_existing(tagged)
    if tagged:
        try:
            cmds.delete(tagged)
        except:
            pass
    _cleanup_empty_temp_container()

def delete_temp_system():
    controlled = get_controlled_objects()
    # Deleting a temporary control is a cancel operation.  Its original
    # animation must be reconnected before the matrix nodes are removed;
    # otherwise the control is left at whichever value happened to be
    # evaluated in the current frame.
    _restore_original_animation(controlled)
    _cleanup_temp_system(controlled)
    return True

# =============================================================================
# UI COMPONENTS (Matching Retimer)
# =============================================================================

class TitleBar(QtWidgets.QWidget):
    def __init__(self, parent=None, title="Temp Control"):
        super(TitleBar, self).__init__(parent)
        self.parent_window = parent
        self._drag_pos = None
        self.setFixedHeight(32)
        self.setup_ui(title)
        
    def setup_ui(self, title):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(8)
        
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setStyleSheet("""
            QLabel {
                color: #AAA;
                font-size: 11px;
                font-weight: 500;
            }
        """)
        layout.addWidget(self.title_label)
        
        layout.addStretch()
        
        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 12px;
                font-weight: bold;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        self.close_btn.clicked.connect(self.close_window)
        layout.addWidget(self.close_btn)
        
        self.setStyleSheet("""
            TitleBar {
                background-color: #363636;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
        """)
        
    def close_window(self):
        if self.parent_window:
            self.parent_window.close()
            
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.parent_window.frameGeometry().topLeft()
            event.accept()
            
    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.LeftButton and self._drag_pos:
            if hasattr(self.parent_window, "detach_from_anchor"):
                self.parent_window.detach_from_anchor()
            self.parent_window.move(event.globalPos() - self._drag_pos)
            event.accept()
            
    def mouseReleaseEvent(self, event):
        if hasattr(self.parent_window, "maybe_attach_to_anchor"):
            self.parent_window.maybe_attach_to_anchor()
        self._drag_pos = None

class TempPivotWindow(ContextPopupWindow):
    def __init__(self, anchor_button=None, parent=None):
        super(TempPivotWindow, self).__init__(anchor_button=anchor_button, parent=parent)
        
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle('Temp Control')
        self.setFixedSize(320, 490)
        
        self.anchor_button = anchor_button
        self._tail_height = 10
        self._tail_width = 16
        self._tail_x = self.width() // 2
        self._tail_on_top = False
        self._magnet_attached = True
        
        self._base_opacity = 0.5
        self._hover_opacity = 1.0
        self._anim = None
        
        self._setup_ui()
        
        # Setup selection job (killWithScene para limpieza automatica)
        self.selection_job = cmds.scriptJob(
            event=["SelectionChanged", self._on_selection_changed],
            killWithScene=True
        )
        
        # Start timer for status updates (3s para no sobrecargar)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update_status)
        self.timer.start(3000)
        
        self._on_selection_changed()
        self._update_status()
        self.position_window()
        
        self.setWindowOpacity(self._base_opacity)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        bg = QtGui.QColor(58, 58, 58)
        border_color = QtGui.QColor(90, 90, 90)

        tail_height = self._tail_height if self._magnet_attached else 0

        if self._tail_on_top and self._magnet_attached:
            body_rect = QtCore.QRectF(
                0.5,
                tail_height + 0.5,
                self.width() - 1,
                self.height() - tail_height - 1
            )
        else:
            body_rect = QtCore.QRectF(
                0.5,
                0.5,
                self.width() - 1,
                self.height() - tail_height - 1
            )

        path = QtGui.QPainterPath()
        path.addRoundedRect(body_rect, 10, 10)

        tail_cx = max(20, min(self._tail_x, self.width() - 20))
        half_width = self._tail_width / 2.0

        if self._magnet_attached and self._tail_on_top:
            tail_base = body_rect.top()
            path.moveTo(tail_cx - half_width, tail_base)
            path.lineTo(tail_cx, tail_base - tail_height)
            path.lineTo(tail_cx + half_width, tail_base)
            path.closeSubpath()
        elif self._magnet_attached:
            tail_base = body_rect.bottom()
            path.moveTo(tail_cx - half_width, tail_base)
            path.lineTo(tail_cx, tail_base + tail_height)
            path.lineTo(tail_cx + half_width, tail_base)
            path.closeSubpath()

        self.setMask(path.toFillPolygon().toPolygon())
        painter.setPen(QtGui.QPen(border_color, 1))
        painter.setBrush(bg)
        painter.drawPath(path)
        painter.end()

    def position_window(self, force=False):
        if not self._magnet_attached and not force:
            return
        if self.anchor_button is None:
            cursor_pos = QtGui.QCursor.pos()
            self.move(cursor_pos.x() - self.width() // 2,
                      cursor_pos.y() - self.height() - 10)
            return

        try:
            btn_rect = self.anchor_button.rect()
            btn_top_left = self.anchor_button.mapToGlobal(btn_rect.topLeft())
            btn_center_x = btn_top_left.x() + btn_rect.width() // 2
            btn_top_y = btn_top_left.y()
            btn_bottom_y = btn_top_y + btn_rect.height()

            screen_rect = screen_available_geometry(self.anchor_button, btn_top_left)

            x_pos = btn_center_x - self.width() // 2
            if x_pos < screen_rect.left():
                x_pos = screen_rect.left()
            elif x_pos + self.width() > screen_rect.right():
                x_pos = screen_rect.right() - self.width()

            if btn_top_y - screen_rect.top() >= self.height():
                y_pos = btn_top_y - self.height()
                self._tail_on_top = False
            else:
                y_pos = btn_bottom_y
                self._tail_on_top = True

            self.move(x_pos, y_pos)
            self._tail_x = btn_center_x - x_pos
            if self.layout():
                if self._tail_on_top:
                    self.layout().setContentsMargins(1, self._tail_height + 1, 1, 1)
                else:
                    self.layout().setContentsMargins(1, 1, 1, self._tail_height + 1)
            self.update()
        except Exception:
            cursor_pos = QtGui.QCursor.pos()
            self.move(cursor_pos.x() - self.width() // 2,
                      cursor_pos.y() - self.height() - 10)
            self._tail_x = self.width() // 2
            self._tail_on_top = False
            if self.layout():
                self.layout().setContentsMargins(1, 1, 1, self._tail_height + 1)
            self.update()
    
    def detach_from_anchor(self):
        if not self._magnet_attached:
            return
        self._magnet_attached = False
        if self.layout():
            self.layout().setContentsMargins(1, 1, 1, 1)
        self.clearMask()
        self.update()

    def maybe_attach_to_anchor(self):
        if self.anchor_button is None:
            return
        current_pos = self.pos()
        was_attached = self._magnet_attached
        self._magnet_attached = True
        self.position_window(force=True)
        target_pos = self.pos()
        self.move(current_pos)
        self._magnet_attached = was_attached

        if (current_pos - target_pos).manhattanLength() <= 42:
            self._magnet_attached = True
            self.position_window(force=True)

    def animkey_auto_hide(self):
        if self._magnet_attached:
            self.hide()
            return True
        return False
    
    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, self._tail_height + 1)
        main_layout.setSpacing(0)
        main_layout.setSpacing(0)
        
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet("""
            QFrame#mainContainer {
                background-color: transparent;
                border: none;
            }
        """)
        
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # Header, matching Gimbal Fixer.
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(10, 10, 10, 10)
        self.title_label = QtWidgets.QLabel("Temp Control")
        self.title_label.setStyleSheet("color: #AAA; font-size: 11px; font-weight: 500; border: none;")
        header.addWidget(self.title_label)
        header.addStretch()

        close_btn = QtWidgets.QPushButton("X")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        container_layout.addLayout(header)
        
        content = QtWidgets.QWidget()
        content.setStyleSheet("background-color: #3a3a3a;")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(15, 10, 15, 15)
        content_layout.setSpacing(10)
        
        # --- SECTION: MODE ---
        mode_label = QtWidgets.QLabel("CONTROL MODE")
        mode_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 1px;")
        content_layout.addWidget(mode_label)
        
        self.mode_group = QtWidgets.QButtonGroup(self)
        
        mode_layout = QtWidgets.QHBoxLayout()
        self.radio_indiv = QtWidgets.QRadioButton("Individual")
        self.radio_group = QtWidgets.QRadioButton("Group")
        
        radio_style = """
            QRadioButton {
                color: #AAA;
                font-size: 11px;
            }
            QRadioButton::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #666666;
                border-radius: 7px;
                background-color: #444444;
            }
            QRadioButton::indicator:checked {
                background-color: #3498DB;
                border-color: #3498DB;
            }
        """
        self.radio_indiv.setStyleSheet(radio_style)
        self.radio_group.setStyleSheet(radio_style)
        
        self.mode_group.addButton(self.radio_indiv, 1)
        self.mode_group.addButton(self.radio_group, 2)
        self.radio_indiv.setChecked(True)
        
        mode_layout.addWidget(self.radio_indiv)
        mode_layout.addWidget(self.radio_group)
        content_layout.addLayout(mode_layout)
        
        # --- SECTION: PIVOT OPTIONS (for Group) ---
        self.pivot_options_widget = QtWidgets.QWidget()
        pivot_options_layout = QtWidgets.QVBoxLayout(self.pivot_options_widget)
        pivot_options_layout.setContentsMargins(10, 10, 10, 10)
        pivot_options_layout.setSpacing(8)
        self.pivot_options_widget.setStyleSheet("""
            QWidget {
                background-color: #444444;
                border-radius: 6px;
            }
        """)
        
        pivot_title = QtWidgets.QLabel("GROUP PIVOT POSITION")
        pivot_title.setStyleSheet("color: #888; font-size: 9px; font-weight: bold;")
        pivot_options_layout.addWidget(pivot_title)
        
        self.pivot_group = QtWidgets.QButtonGroup(self)
        self.radio_center = QtWidgets.QRadioButton("Center of All")
        self.radio_last = QtWidgets.QRadioButton("Last Selected")
        self.radio_center.setStyleSheet(radio_style)
        self.radio_last.setStyleSheet(radio_style)
        
        self.pivot_group.addButton(self.radio_center, 1)
        self.pivot_group.addButton(self.radio_last, 2)
        self.radio_center.setChecked(True)
        
        pivot_options_layout.addWidget(self.radio_center)
        pivot_options_layout.addWidget(self.radio_last)
        
        content_layout.addWidget(self.pivot_options_widget)

        self.follow_checkbox = QtWidgets.QCheckBox("Follow")
        self.follow_checkbox.setChecked(True)
        self.follow_checkbox.setToolTip("Group temp control follows the controlled objects without needing keys.")
        self.follow_checkbox.setStyleSheet("""
            QCheckBox {
                color: #AAA;
                font-size: 11px;
                background-color: #444444;
                border-radius: 6px;
                padding: 8px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #666666;
                border-radius: 3px;
                background-color: #333333;
            }
            QCheckBox::indicator:checked {
                background-color: #3498DB;
                border-color: #3498DB;
            }
        """)
        content_layout.addWidget(self.follow_checkbox)
        
        self.radio_indiv.toggled.connect(self._toggle_pivot_options)
        self.pivot_options_widget.setVisible(False)
        self.follow_checkbox.setVisible(False)
        
        # Spacer
        content_layout.addStretch()
        
        # --- SECTION: ACTIONS ---
        self.create_btn = QtWidgets.QPushButton("CREATE TEMP CONTROL")
        self.create_btn.setFixedHeight(46)
        self.create_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.create_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d5d3d;
                color: #a3be8c;
                font-size: 12px;
                font-weight: bold;
                border: 1px solid #5aaa5a;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #3d6d4d; border-color: #a3be8c; }
            QPushButton:pressed { background-color: #254a32; }
        """)
        self.create_btn.clicked.connect(self._on_create)
        content_layout.addWidget(self.create_btn)
        
        self.bake_btn = QtWidgets.QPushButton("SMART BAKE & DELETE")
        self.bake_btn.setFixedHeight(40)
        self.bake_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.bake_btn.setStyleSheet("""
            QPushButton {
                background-color: #4d2d2d;
                color: #ff6b6b;
                font-size: 11px;
                font-weight: bold;
                border: 1px solid #8a4a4a;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #5d3d3d; border-color: #ff6b6b; }
            QPushButton:pressed { background-color: #3d2525; }
        """)
        self.bake_btn.clicked.connect(self._on_bake)
        content_layout.addWidget(self.bake_btn)
        
        # --- SECTION: STATUS ---
        self.status_label = QtWidgets.QLabel("No active temp controls")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #666; font-size: 10px; margin-top: 10px;")
        content_layout.addWidget(self.status_label)
        
        container_layout.addWidget(content)
        main_layout.addWidget(self.container)
    
    def _toggle_pivot_options(self, checked):
        is_group = not checked
        self.pivot_options_widget.setVisible(is_group)
        self.follow_checkbox.setVisible(is_group)
    
    def _on_selection_changed(self):
        # Auto-detect mode (protegido contra errores)
        try:
            sel = cmds.ls(selection=True, transforms=True) or []
            joints = cmds.ls(selection=True, type="joint") or []
            all_sel = list(set(sel + joints))
            
            if len(all_sel) > 1:
                self.radio_group.setChecked(True)
            else:
                self.radio_indiv.setChecked(True)
            self._update_status()
        except Exception:
            pass
            
    def _update_status(self):
        try:
            selected_temp_controls = get_selected_temp_controls()
            controlled = filter_controlled_by_temp_controls(
                get_controlled_objects(),
                selected_temp_controls
            )
            if controlled:
                names = [c.split(":")[-1].split("|")[-1] for c in controlled]
                if len(names) > 3:
                    label = f"{len(names)} objects: {', '.join(names[:3])}..."
                else:
                    label = f"{len(names)} object(s): {', '.join(names)}"
                if selected_temp_controls:
                    ctrl_names = [c.split("|")[-1] for c in selected_temp_controls]
                    label = "{} -> {}".format(", ".join(ctrl_names), label)
                self.status_label.setText(label)
                self.status_label.setStyleSheet("color: #3498DB; font-size: 10px;")
            elif selected_temp_controls:
                names = [c.split("|")[-1] for c in selected_temp_controls]
                self.status_label.setText("{} selected, no controlled objects found".format(", ".join(names)))
                self.status_label.setStyleSheet("color: #ffca28; font-size: 10px;")
            else:
                self.status_label.setText("No active temp controls")
                self.status_label.setStyleSheet("color: #666; font-size: 10px;")
        except Exception:
            pass
            
    def _on_create(self):
        sel = cmds.ls(selection=True, long=True)
        all_sel = []
        for s in sel:
            if cmds.objectType(s) in ("transform", "joint"):
                all_sel.append(s)
            elif cmds.listRelatives(s, parent=True):
                parent = cmds.listRelatives(s, parent=True, fullPath=True)
                if parent and parent[0] not in all_sel:
                    all_sel.append(parent[0])
        
        if not all_sel:
            om.MGlobal.displayWarning("Select animated object(s) first.")
            return
            
        mode = 1 if self.radio_indiv.isChecked() else 2
        try:
            if mode == 1: # Individual
                create_individual_controls(all_sel)
            else: # Group
                pivot_id = 1 if self.radio_center.isChecked() else 2
                pivot_mode = "center" if pivot_id == 1 else "last"
                create_group_control(all_sel, pivot_mode, follow=self.follow_checkbox.isChecked())
            self._update_status()
        except Exception as e:
            om.MGlobal.displayError(f"Error creating temp control: {str(e)}")

    def _on_bake(self):
        baked_layer = smart_bake_and_delete()
        if baked_layer:
            self._update_status()
            om.MGlobal.displayInfo("Smart Bake complete: {}".format(baked_layer))
        else:
            om.MGlobal.displayWarning("Nothing to bake.")

    def enterEvent(self, e):
        self._animate(self._hover_opacity)
        super(TempPivotWindow, self).enterEvent(e)
        
    def leaveEvent(self, e):
        self._animate(self._base_opacity)
        super(TempPivotWindow, self).leaveEvent(e)
        
    def _animate(self, val):
        if self._anim is not None:
            try:
                if self._anim.state() == QtCore.QPropertyAnimation.Running:
                    self._anim.stop()
            except RuntimeError:
                self._anim = None
        self._anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(150)
        self._anim.setEndValue(val)
        self._anim.finished.connect(self._on_anim_finished)
        self._anim.start()

    def _on_anim_finished(self):
        self._anim = None

    def closeEvent(self, event):
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
            self._anim = None
        # Detener timer primero
        if hasattr(self, 'timer'):
            try:
                self.timer.stop()
            except Exception:
                pass
        # Matar scriptJob si existe
        if hasattr(self, 'selection_job'):
            try:
                if cmds.scriptJob(exists=self.selection_job):
                    cmds.scriptJob(kill=self.selection_job, force=True)
            except Exception:
                pass
        global _temp_pivot_window
        _temp_pivot_window = None
        super(TempPivotWindow, self).closeEvent(event)

# =============================================================================
# PUBLIC INTERFACE
# =============================================================================

def _kill_orphan_scriptjobs():
    """Matar scriptJobs huerfanos de instancias previas."""
    try:
        all_jobs = cmds.scriptJob(listJobs=True)
        for job_str in all_jobs:
            if "_on_selection_changed" in job_str and "SelectionChanged" in job_str:
                job_id = int(job_str.split(":")[0])
                try:
                    cmds.scriptJob(kill=job_id, force=True)
                except Exception:
                    pass
    except Exception:
        pass

def _is_valid_qt_widget(widget):
    if widget is None:
        return False
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False
    except Exception:
        return False

def show(anchor_button=None):
    global _temp_pivot_window
    from AnimKey.mods import uiMod

    if _is_valid_qt_widget(_temp_pivot_window):
        try:
            uiMod.close_animkey_tool_windows(except_widget=_temp_pivot_window)
            if anchor_button is not None:
                _temp_pivot_window.anchor_button = anchor_button
            if not _temp_pivot_window.isVisible():
                _temp_pivot_window.show()
            _temp_pivot_window.position_window()
            _temp_pivot_window.raise_()
            _temp_pivot_window.activateWindow()
            return _temp_pivot_window
        except Exception:
            _temp_pivot_window = None

    uiMod.close_animkey_tool_windows()
    
    # Limpiar scriptJobs huerfanos
    _kill_orphan_scriptjobs()
            
    if cmds.window(WINDOW_OBJECT, exists=True):
        cmds.deleteUI(WINDOW_OBJECT)
        
    _temp_pivot_window = TempPivotWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _temp_pivot_window.show()
    _temp_pivot_window.raise_()
    _temp_pivot_window.activateWindow()
    return _temp_pivot_window

def execute(*args, **kwargs):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tempPivot.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))


def execute_temp_pivot(*args, **kwargs):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tempPivot.execute_temp_pivot"):
        return None
    button = kwargs.get("button")
    if is_active():
        deactivated = deactivate_temp_pivot()
        if deactivated:
            set_button_active(button, False)
        return False if deactivated else None

    result = activate_temp_pivot(
        objects=kwargs.get("objects"),
        pivot_mode=kwargs.get("pivot_mode", "last"),
        edit_pivot=kwargs.get("edit_pivot", True),
    )
    set_button_active(button, bool(result))
    return bool(result)


def is_active():
    try:
        return bool(
            cmds.manipPivot(query=True, valid=True)
            and cmds.manipPivot(query=True, pinPivot=True)
            and cmds.manipRotateContext("Rotate", query=True, useManipPivot=True)
        )
    except Exception:
        return False

def cleanup_orphans():
    delete_temp_system()
    
def set_button_active(button, active):
    if button is None:
        return

    try:
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        color = "#bf616a"

        if active:
            active_bg = "#8f454d"
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

if __name__ == "__main__":
    show()
