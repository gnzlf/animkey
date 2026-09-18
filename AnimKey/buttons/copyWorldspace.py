# -*- coding: utf-8 -*-
"""Reliable world-space transform recorder for AnimKey.

The original implementation stored world translation and Euler rotation as six
independent numbers. That loses the real transform when a control has pivots,
rotate axes, joint orientation, negative scale, an animated parent, constraints,
or animation layers. This module records evaluated world matrices instead and
bakes them back in hierarchy order.

Public function names remain compatible with existing AnimKey shelves, toolbar
actions, and hotkeys in Maya 2022 and newer.
"""

from __future__ import absolute_import, division, print_function

import json
import math
import os

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods import configMod as config


DATA_VERSION = 2
MATRIX_TOLERANCE = 1.0e-5
POSE_TOLERANCE = 1.0e-4
ROTATION_TOLERANCE_DEGREES = 1.0e-3
TIME_TOLERANCE = 1.0e-7

TRANSFORM_CHANNELS = (
    "translateX", "translateY", "translateZ",
    "rotateX", "rotateY", "rotateZ",
    "scaleX", "scaleY", "scaleZ",
    "shearXY", "shearXZ", "shearYZ",
)
ROTATE_CHANNELS = ("rotateX", "rotateY", "rotateZ")


class WorldspaceState(object):
    """Runtime state for the optional live world-space pin."""

    def __init__(self):
        self.auto_enabled = False
        self.attribute_callback_id = None  # Compatibility with older callers.
        self.time_callback_id = None
        self.callback_ids = []
        self.source_data = {}
        self.auto_matrices = {}
        self.busy = False
        self.auto_pending = False
        self.button = None


_state = WorldspaceState()


# ---------------------------------------------------------------------------
# Paths and serialization
# ---------------------------------------------------------------------------

def _get_worldspace_folder():
    return os.path.join(config.get_user_folder_path(), "tools", "worldspace")


def _get_animation_data_file():
    return os.path.join(_get_worldspace_folder(), "worldspace_animation.json")


def _get_frame_data_file():
    return os.path.join(_get_worldspace_folder(), "worldspace_frame.json")


def _time_key(value):
    value = float(value)
    if abs(value) < TIME_TOLERANCE:
        value = 0.0
    return format(value, ".15g")


def _write_payload(path, payload):
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def _normalise_payload(raw, kind=None):
    """Validate version 2 data and upgrade the legacy six-value format."""
    if not isinstance(raw, dict):
        return None

    if raw.get("version") == DATA_VERSION and isinstance(raw.get("sources"), list):
        sources = []
        for source in raw["sources"]:
            if not isinstance(source, dict) or not isinstance(source.get("samples"), dict):
                continue
            samples = {}
            for time_value, sample in source["samples"].items():
                if isinstance(sample, dict):
                    matrix = sample.get("matrix") or sample.get("values")
                    rotation = sample.get("rotation")
                    if not isinstance(matrix, (list, tuple)) or len(matrix) != 16:
                        continue
                    try:
                        clean_sample = {
                            "matrix": [float(value) for value in matrix],
                        }
                        if isinstance(rotation, (list, tuple)) and len(rotation) == 3:
                            clean_sample["rotation"] = [float(value) for value in rotation]
                        samples[_time_key(float(time_value))] = clean_sample
                    except (TypeError, ValueError):
                        continue
                elif isinstance(sample, (list, tuple)) and len(sample) in (6, 16):
                    try:
                        samples[_time_key(float(time_value))] = [
                            float(value) for value in sample
                        ]
                    except (TypeError, ValueError):
                        continue
            if not samples:
                continue
            clean = dict(source)
            clean["samples"] = samples
            sources.append(clean)
        if not sources:
            return None
        result = dict(raw)
        result["sources"] = sources
        result["kind"] = result.get("kind") or kind or "animation"
        return result

    # Legacy files used {object: {frame: [tx, ty, tz, rx, ry, rz]}}.
    sources = []
    for index, (node, node_data) in enumerate(raw.items()):
        if not isinstance(node_data, dict):
            continue
        samples = {}
        for time_value, values in node_data.items():
            if not isinstance(values, (list, tuple)) or len(values) != 6:
                continue
            try:
                samples[_time_key(float(time_value))] = [float(value) for value in values]
            except (TypeError, ValueError):
                continue
        if samples:
            sources.append({
                "path": node,
                "name": node.split("|")[-1],
                "uuid": "",
                "order": index,
                "samples": samples,
            })
    if not sources:
        return None
    all_times = sorted(
        float(time_value)
        for source in sources
        for time_value in source["samples"]
    )
    return {
        "version": 1,
        "kind": kind or "animation",
        "range": [all_times[0], all_times[-1]],
        "sources": sources,
    }


def _read_payload(path, kind=None):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return _normalise_payload(json.load(stream), kind=kind)
    except (OSError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Maya selection and time helpers
# ---------------------------------------------------------------------------

def _long_transform(node):
    if not node or not cmds.objExists(node):
        return None
    matches = cmds.ls(node, long=True) or []
    if not matches:
        return None
    node = matches[0]
    try:
        node_type = cmds.nodeType(node)
    except Exception:
        return None
    if node_type not in ("transform", "joint"):
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if not parents:
            return None
        node = parents[0]
    return node


def _ordered_selection():
    selection = cmds.ls(orderedSelection=True, long=True) or []
    if not selection:
        selection = cmds.ls(selection=True, long=True) or []
    result = []
    seen = set()
    for item in selection:
        node = _long_transform(item)
        if node and node not in seen:
            seen.add(node)
            result.append(node)
    return result


def _get_selected_time_range():
    if cmds.about(batch=True):
        return None
    try:
        slider = mel.eval("$tmpVar=$gPlayBackSlider")
        if not slider or not cmds.timeControl(slider, query=True, rangeVisible=True):
            return None
        raw_range = cmds.timeControl(slider, query=True, rangeArray=True) or []
        if len(raw_range) < 2:
            return None
        start = float(raw_range[0])
        end = float(raw_range[1]) - 1.0
        return (start, end) if end >= start else None
    except Exception:
        return None


def _playback_range(animation=False):
    if animation:
        start = cmds.playbackOptions(query=True, animationStartTime=True)
        end = cmds.playbackOptions(query=True, animationEndTime=True)
    else:
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
    return float(start), float(end)


def _world_dependency_nodes(nodes):
    """Include DAG and DG inputs whose animation contributes to world motion."""
    dag_nodes = []
    seen = set()
    for node in nodes:
        current = node
        while current and current not in seen:
            seen.add(current)
            dag_nodes.append(current)
            parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
            current = parents[0] if parents else None

    # listHistory reaches animCurves through constraints, pairBlends,
    # offsetParentMatrix networks, and animation layers. That lets us retain
    # fractional upstream key times in addition to the regular per-frame bake.
    result = list(dag_nodes)
    for node in dag_nodes:
        try:
            history = cmds.listHistory(node, future=False, pruneDagObjects=False) or []
        except Exception:
            history = []
        for dependency in history:
            if dependency not in seen:
                seen.add(dependency)
                result.append(dependency)
    return result


def _sample_times(start, end, nodes=None):
    """Return every frame plus exact fractional keys in the requested range."""
    start = float(start)
    end = float(end)
    if end < start:
        start, end = end, start

    values = {round(start, 10), round(end, 10)}
    current = start
    guard = 0
    while current <= end + TIME_TOLERANCE:
        values.add(round(current, 10))
        current += 1.0
        guard += 1
        if guard > 1000000:
            raise RuntimeError("Worldspace range is too large to sample safely.")

    dependencies = _world_dependency_nodes(nodes or [])
    if dependencies:
        try:
            key_times = cmds.keyframe(
                dependencies,
                query=True,
                time=(start, end),
                timeChange=True,
            ) or []
        except Exception:
            key_times = []
        for value in key_times:
            value = float(value)
            if start - TIME_TOLERANCE <= value <= end + TIME_TOLERANCE:
                values.add(round(value, 10))
    return sorted(values)


def _dag_path(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _world_matrix(node, dag_path=None):
    if dag_path is not None:
        try:
            return [float(value) for value in dag_path.inclusiveMatrix()]
        except Exception:
            pass
    return [
        float(value)
        for value in cmds.xform(node, query=True, worldSpace=True, matrix=True)
    ]


def _node_uuid(node):
    try:
        values = cmds.ls(node, uuid=True) or []
        return values[0] if values else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Progress, messages, and scene-state protection
# ---------------------------------------------------------------------------

def _begin_progress(status, maximum):
    if cmds.about(batch=True):
        return None
    try:
        progress = mel.eval("$tmp=$gMainProgressBar")
        cmds.progressBar(
            progress,
            edit=True,
            beginProgress=True,
            isInterruptable=True,
            status=status,
            maxValue=max(1, int(maximum)),
        )
        return progress
    except Exception:
        return None


def _progress_cancelled(progress):
    if not progress:
        return False
    try:
        return bool(cmds.progressBar(progress, query=True, isCancelled=True))
    except Exception:
        return False


def _progress_step(progress):
    if progress:
        try:
            cmds.progressBar(progress, edit=True, step=1)
        except Exception:
            pass


def _end_progress(progress):
    if progress:
        try:
            cmds.progressBar(progress, edit=True, endProgress=True)
        except Exception:
            pass


def _suspend_refresh():
    if cmds.about(batch=True):
        return False
    try:
        was_suspended = bool(cmds.refresh(query=True, suspend=True))
    except Exception:
        was_suspended = False
    if not was_suspended:
        try:
            cmds.refresh(suspend=True)
        except Exception:
            pass
    return was_suspended


def _resume_refresh(was_suspended):
    if not was_suspended and not cmds.about(batch=True):
        try:
            cmds.refresh(suspend=False)
        except Exception:
            pass


def _warning(message):
    cmds.warning("AnimKey Worldspace: {0}".format(message))


def _message(title, detail="", success=True):
    colour = "#a3be8c" if success else "#bf616a"
    body = "<span style='color:{0}'>{1}</span>".format(colour, title)
    if detail:
        body += "<br><span style='color:#88c0d0'>{0}</span>".format(detail)
    try:
        cmds.inViewMessage(
            amg=body,
            pos="topCenter",
            fade=True,
            fadeStayTime=1800,
        )
    except Exception:
        pass


def _require_context(name):
    from AnimKey.core.executionGuard import require_animkey_context
    return bool(require_animkey_context(name))


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _capture_payload(nodes, times, kind="animation", range_mode="custom"):
    nodes = [_long_transform(node) for node in nodes]
    nodes = [node for node in nodes if node]
    if not nodes or not times:
        return None

    paths = {}
    sources = []
    for index, node in enumerate(nodes):
        try:
            paths[node] = _dag_path(node)
        except Exception:
            paths[node] = None
        sources.append({
            "path": node,
            "name": node.split("|")[-1],
            "uuid": _node_uuid(node),
            "order": index,
            "samples": {},
        })

    original_time = float(cmds.currentTime(query=True))
    refresh_state = _suspend_refresh()
    progress = _begin_progress("Recording real world-space matrices...", len(times))
    previous_busy = _state.busy
    _state.busy = True
    cancelled = False
    try:
        for time_value in times:
            if _progress_cancelled(progress):
                cancelled = True
                break
            cmds.currentTime(float(time_value), edit=True, update=True)
            sample_key = _time_key(time_value)
            for source, node in zip(sources, nodes):
                try:
                    source["samples"][sample_key] = {
                        "matrix": _world_matrix(node, paths[node]),
                        # Maya's evaluated world Euler is retained as a safe
                        # orientation fallback for reflected/non-uniform spaces.
                        "rotation": [
                            float(value)
                            for value in cmds.xform(
                                node, query=True, worldSpace=True, rotation=True
                            )
                        ],
                    }
                except Exception as error:
                    _warning("Could not evaluate {0}: {1}".format(node, error))
            _progress_step(progress)
    finally:
        try:
            cmds.currentTime(original_time, edit=True, update=True)
        finally:
            _state.busy = previous_busy
            _end_progress(progress)
            _resume_refresh(refresh_state)

    if cancelled:
        _warning("Capture cancelled; no partial data was saved.")
        return None

    sources = [source for source in sources if source["samples"]]
    if not sources:
        return None
    return {
        "version": DATA_VERSION,
        "kind": kind,
        "rangeMode": range_mode,
        "range": [float(times[0]), float(times[-1])],
        "sampleStep": 1.0,
        "mayaVersion": str(cmds.about(version=True)),
        "linearUnit": str(cmds.currentUnit(query=True, linear=True)),
        "timeUnit": str(cmds.currentUnit(query=True, time=True)),
        "sources": sources,
    }


def _copy_range(path, nodes, start, end, label, range_mode):
    times = _sample_times(start, end, nodes=nodes)
    payload = _capture_payload(nodes, times, kind="animation", range_mode=range_mode)
    if not payload:
        return None
    _write_payload(path, payload)
    _message(
        label,
        "{0} object(s), {1} sampled frame(s), range {2} - {3}".format(
            len(payload["sources"]), len(times), _time_key(start), _time_key(end)
        ),
    )
    return payload


def copy_worldspace_all_animation(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.copy_worldspace_all_animation"):
        return None
    nodes = _ordered_selection()
    if not nodes:
        _warning("Select at least one source object.")
        return None
    start, end = _playback_range(animation=True)
    return _copy_range(
        _get_animation_data_file(), nodes, start, end,
        "Worldspace Animation Copied", "animation",
    )


def copy_worldspace_selected_range(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.copy_worldspace_selected_range"):
        return None
    nodes = _ordered_selection()
    if not nodes:
        _warning("Select at least one source object.")
        return None
    time_range = _get_selected_time_range()
    if time_range is None:
        _warning("Highlight a time range in the timeline first.")
        return None
    return _copy_range(
        _get_animation_data_file(), nodes, time_range[0], time_range[1],
        "Worldspace Selected Range Copied", "selected",
    )


def copy_worldspace_playback_range(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.copy_worldspace_playback_range"):
        return None
    nodes = _ordered_selection()
    if not nodes:
        _warning("Select at least one source object.")
        return None
    start, end = _playback_range(animation=False)
    return _copy_range(
        _get_animation_data_file(), nodes, start, end,
        "Worldspace Playback Range Copied", "playback",
    )


def copy_worldspace_current_frame(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.copy_worldspace_current_frame"):
        return None
    nodes = _ordered_selection()
    if not nodes:
        _warning("Select at least one source object.")
        return None
    current_time = float(cmds.currentTime(query=True))
    payload = _capture_payload(
        nodes, [current_time], kind="frame", range_mode="currentFrame"
    )
    if not payload:
        return None
    _write_payload(_get_frame_data_file(), payload)
    _state.source_data = payload
    _message(
        "Worldspace Frame Copied",
        "{0} object(s) at frame {1}".format(
            len(payload["sources"]), _time_key(current_time)
        ),
    )
    return payload


# ---------------------------------------------------------------------------
# Target mapping and exact matrix application
# ---------------------------------------------------------------------------

def _resolve_original_source(source):
    path = source.get("path") or ""
    if path and cmds.objExists(path):
        return _long_transform(path)

    uuid_value = source.get("uuid") or ""
    if uuid_value:
        try:
            matches = cmds.ls(uuid_value, long=True) or []
        except Exception:
            matches = []
        if len(matches) == 1:
            return _long_transform(matches[0])

    name = source.get("name") or (path.split("|")[-1] if path else "")
    if name:
        matches = cmds.ls(name, long=True) or []
        if len(matches) == 1:
            return _long_transform(matches[0])
    return None


def _target_mapping(payload, selection=None, warn=True):
    sources = list(payload.get("sources") or [])
    targets = _ordered_selection() if selection is None else [
        _long_transform(node) for node in selection
    ]
    targets = [node for node in targets if node]

    if targets:
        if len(sources) == 1:
            return [(target, sources[0]) for target in targets]
        if len(targets) == len(sources):
            return list(zip(targets, sources))
        if warn:
            _warning(
                "Selection has {0} object(s), but the copy contains {1}. "
                "Select the same count, or copy one source for many targets.".format(
                    len(targets), len(sources)
                )
            )
        return []

    mapping = []
    for source in sources:
        target = _resolve_original_source(source)
        if target:
            mapping.append((target, source))
    if not mapping and warn:
        _warning("The original objects are unavailable. Select destination objects.")
    return mapping


def _hierarchy_order(mapping):
    return sorted(mapping, key=lambda item: (item[0].count("|"), item[0]))


def _available_times(payload):
    values = set()
    for source in payload.get("sources") or []:
        for time_value in source.get("samples") or {}:
            try:
                values.add(float(time_value))
            except (TypeError, ValueError):
                pass
    return sorted(values)


def _sample_for_time(source, time_value):
    return (source.get("samples") or {}).get(_time_key(time_value))


def _settable_channels(node):
    result = []
    for attribute in TRANSFORM_CHANNELS:
        plug = "{0}.{1}".format(node, attribute)
        if not cmds.objExists(plug):
            continue
        try:
            if cmds.getAttr(plug, lock=True):
                continue
            if not cmds.getAttr(plug, settable=True):
                continue
        except Exception:
            continue
        result.append(attribute)
    return result


def _matrix_difference(first, second):
    if len(first) != 16 or len(second) != 16:
        return float("inf")
    return max(abs(float(a) - float(b)) for a, b in zip(first, second))


def _normalised_axis(matrix, start):
    axis = om.MVector(matrix[start], matrix[start + 1], matrix[start + 2])
    length = axis.length()
    return axis / length if length > 1.0e-12 else om.MVector()


def _pose_difference(desired, actual):
    translation_error = math.sqrt(sum(
        (float(desired[index]) - float(actual[index])) ** 2
        for index in (12, 13, 14)
    ))
    axis_error = 0.0
    for start in (0, 4, 8):
        desired_axis = _normalised_axis(desired, start)
        actual_axis = _normalised_axis(actual, start)
        if desired_axis.length() < 1.0e-12 or actual_axis.length() < 1.0e-12:
            continue
        alignment = max(-1.0, min(1.0, desired_axis * actual_axis))
        axis_error = max(axis_error, 1.0 - alignment)
    return translation_error, axis_error


def _matrix_rotation_degrees(matrix):
    transform = om.MTransformationMatrix(om.MMatrix(matrix))
    rotation = transform.rotation(asQuaternion=True).asEulerRotation()
    return [
        math.degrees(rotation.x),
        math.degrees(rotation.y),
        math.degrees(rotation.z),
    ]


def _rotation_difference_degrees(first, second):
    first_rotation = om.MEulerRotation(*[
        math.radians(float(value)) for value in first
    ]).asQuaternion()
    second_rotation = om.MEulerRotation(*[
        math.radians(float(value)) for value in second
    ]).asQuaternion()
    dot = abs(
        first_rotation.x * second_rotation.x
        + first_rotation.y * second_rotation.y
        + first_rotation.z * second_rotation.z
        + first_rotation.w * second_rotation.w
    )
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(2.0 * math.acos(dot))


def _apply_world_sample(node, values):
    """Apply a matrix exactly, falling back to world rotation then position."""
    saved_world_rotation = None
    if isinstance(values, dict):
        saved_world_rotation = values.get("rotation")
        values = values.get("matrix") or values.get("values") or []
    if len(values) == 6:
        # Rotation first: changing it can move a non-zero pivot.
        cmds.xform(node, worldSpace=True, rotation=values[3:6])
        cmds.xform(node, worldSpace=True, translation=values[0:3])
        return True, 0.0, True

    desired = [float(value) for value in values]
    if len(desired) != 16:
        return False, float("inf"), False

    try:
        cmds.xform(node, worldSpace=True, matrix=desired)
        actual = _world_matrix(node)
        matrix_error = _matrix_difference(desired, actual)
        if matrix_error <= MATRIX_TOLERANCE:
            return True, matrix_error, True
    except Exception:
        pass

    # Locked scale/shear can prevent a full matrix match even when the world
    # position and orientation are achievable. Preserve those rig channels.
    try:
        cmds.xform(
            node,
            worldSpace=True,
            rotation=(saved_world_rotation or _matrix_rotation_degrees(desired)),
        )
        cmds.xform(
            node,
            worldSpace=True,
            translation=desired[12:15],
        )
        actual = _world_matrix(node)
        translation_error, axis_error = _pose_difference(desired, actual)
        full_match = _matrix_difference(desired, actual) <= MATRIX_TOLERANCE
        if saved_world_rotation:
            actual_world_rotation = cmds.xform(
                node, query=True, worldSpace=True, rotation=True
            )
            rotation_error = _rotation_difference_degrees(
                saved_world_rotation, actual_world_rotation
            )
            pose_error = max(translation_error, rotation_error)
            return (
                translation_error <= POSE_TOLERANCE
                and rotation_error <= ROTATION_TOLERANCE_DEGREES,
                pose_error,
                full_match,
            )
        pose_error = max(translation_error, axis_error)
        return (
            translation_error <= POSE_TOLERANCE and axis_error <= POSE_TOLERANCE,
            pose_error,
            full_match,
        )
    except Exception:
        return False, float("inf"), False


def _verify_world_sample(node, values):
    """Verify the evaluated result after key insertion, not just after xform."""
    saved_world_rotation = None
    if isinstance(values, dict):
        saved_world_rotation = values.get("rotation")
        values = values.get("matrix") or values.get("values") or []
    if len(values) == 6:
        actual_translation = cmds.xform(
            node, query=True, worldSpace=True, translation=True
        )
        translation_error = math.sqrt(sum(
            (float(values[index]) - float(actual_translation[index])) ** 2
            for index in range(3)
        ))
        rotation_error = _rotation_difference_degrees(
            values[3:6], cmds.xform(node, query=True, worldSpace=True, rotation=True)
        )
        return (
            translation_error <= POSE_TOLERANCE
            and rotation_error <= ROTATION_TOLERANCE_DEGREES,
            max(translation_error, rotation_error),
            False,
        )
    if len(values) != 16:
        return False, float("inf"), False

    desired = [float(value) for value in values]
    actual = _world_matrix(node)
    matrix_error = _matrix_difference(desired, actual)
    if matrix_error <= MATRIX_TOLERANCE:
        return True, matrix_error, True
    translation_error, axis_error = _pose_difference(desired, actual)
    if saved_world_rotation:
        rotation_error = _rotation_difference_degrees(
            saved_world_rotation,
            cmds.xform(node, query=True, worldSpace=True, rotation=True),
        )
        return (
            translation_error <= POSE_TOLERANCE
            and rotation_error <= ROTATION_TOLERANCE_DEGREES,
            max(translation_error, rotation_error),
            False,
        )
    return (
        translation_error <= POSE_TOLERANCE and axis_error <= POSE_TOLERANCE,
        max(translation_error, axis_error),
        False,
    )


def _cut_transform_keys(nodes, start, end):
    for node in nodes:
        channels = _settable_channels(node)
        if not channels:
            continue
        try:
            cmds.cutKey(
                node,
                attribute=channels,
                time=(float(start), float(end)),
                clear=True,
            )
        except Exception:
            pass


def _key_transform(node, time_value):
    channels = _settable_channels(node)
    if not channels:
        return []
    cmds.setKeyframe(node, time=float(time_value), attribute=channels)
    return channels


def _filter_rotations(nodes):
    curves = []
    for node in nodes:
        for attribute in ROTATE_CHANNELS:
            curves.extend(cmds.listConnections(
                "{0}.{1}".format(node, attribute),
                source=True,
                destination=False,
                type="animCurve",
            ) or [])
    if curves:
        try:
            cmds.filterCurve(sorted(set(curves)))
        except Exception:
            pass


def _apply_payload(payload, selection=None, time_range=None,
                   destination_time=None, replace=True, label="Worldspace Pasted"):
    payload = _normalise_payload(payload)
    if not payload:
        _warning("The copied data is empty or invalid.")
        return None
    mapping = _target_mapping(payload, selection=selection)
    if not mapping:
        return None
    mapping = _hierarchy_order(mapping)

    source_times = _available_times(payload)
    if destination_time is not None:
        if not source_times:
            return None
        schedule = [(float(destination_time), source_times[0])]
    else:
        if time_range is not None:
            start, end = [float(value) for value in time_range]
            source_times = [
                value for value in source_times
                if start - TIME_TOLERANCE <= value <= end + TIME_TOLERANCE
            ]
        schedule = [(value, value) for value in source_times]

    if not schedule:
        _warning("No copied samples exist in the requested range.")
        return None

    targets = []
    for target, _source in mapping:
        if target not in targets:
            targets.append(target)

    original_time = float(cmds.currentTime(query=True))
    refresh_state = _suspend_refresh()
    progress = _begin_progress("Applying real world-space matrices...", len(schedule))
    previous_busy = _state.busy
    _state.busy = True
    failures = []
    maximum_error = 0.0
    full_matrix_matches = 0
    applied_samples = 0
    cancelled = False
    undo_open = False
    try:
        cmds.undoInfo(openChunk=True, chunkName="AnimKey Worldspace")
        undo_open = True
        if replace and destination_time is None:
            _cut_transform_keys(targets, schedule[0][0], schedule[-1][0])

        for target_time, source_time in schedule:
            if _progress_cancelled(progress):
                cancelled = True
                break
            cmds.currentTime(target_time, edit=True, update=True)
            for target, source in mapping:
                values = _sample_for_time(source, source_time)
                if values is None:
                    continue
                before = None
                sample_failure = None
                full_match = False
                try:
                    before = _world_matrix(target)
                    success, _error, _full_match = _apply_world_sample(target, values)
                    if not success:
                        sample_failure = _error
                    else:
                        keyed_channels = _key_transform(target, target_time)
                        if not keyed_channels:
                            sample_failure = "no writable channels"
                        else:
                            success, error, full_match = _verify_world_sample(
                                target, values
                            )
                            if not success:
                                # Animation layers and existing animCurves
                                # sometimes need one extra solve after the
                                # destination key exists.
                                _apply_world_sample(target, values)
                                _key_transform(target, target_time)
                                success, error, full_match = _verify_world_sample(
                                    target, values
                                )
                            maximum_error = max(maximum_error, error)
                            if not success:
                                sample_failure = error
                except Exception as exception:
                    sample_failure = str(exception)

                if sample_failure is None:
                    applied_samples += 1
                    if full_match:
                        full_matrix_matches += 1
                else:
                    failures.append((target, target_time, sample_failure))
                    # Do not leave a partially decomposed transform behind.
                    if before is not None:
                        try:
                            cmds.xform(target, worldSpace=True, matrix=before)
                            _key_transform(target, target_time)
                        except Exception:
                            pass
            _progress_step(progress)

        _filter_rotations(targets)
    finally:
        if undo_open:
            cmds.undoInfo(closeChunk=True)
        try:
            cmds.currentTime(original_time, edit=True, update=True)
        finally:
            _state.busy = previous_busy
            _end_progress(progress)
            _resume_refresh(refresh_state)

    if failures:
        first = failures[0]
        _warning(
            "{0} sample(s) could not reach the requested world pose. "
            "First: {1} at {2} (error {3}). Check locked or constrained channels.".format(
                len(failures), first[0], _time_key(first[1]), first[2]
            )
        )
    if cancelled:
        _message("Worldspace Paste Cancelled", "Undo restores the partial paste.", False)
    else:
        _message(
            label,
            "{0} object(s), {1} frame(s), {2} exact matrix sample(s)".format(
                len(targets), len(schedule), full_matrix_matches
            ),
            success=not failures,
        )
    return {
        "objects": len(targets),
        "frames": len(schedule),
        "appliedSamples": applied_samples,
        "exactMatrixSamples": full_matrix_matches,
        "failedSamples": len(failures),
        "maximumError": maximum_error,
        "cancelled": cancelled,
    }


# ---------------------------------------------------------------------------
# Paste commands
# ---------------------------------------------------------------------------

def _load_animation_or_warn():
    payload = _read_payload(_get_animation_data_file(), kind="animation")
    if payload is None:
        _warning("No valid worldspace animation was found. Copy it first.")
    return payload


def _load_frame_or_warn():
    payload = _read_payload(_get_frame_data_file(), kind="frame")
    if payload is None:
        _warning("No valid worldspace frame was found. Copy it first.")
    return payload


def paste_worldspace_animation(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.paste_worldspace_animation"):
        return None
    payload = _load_animation_or_warn()
    return _apply_payload(payload, label="Worldspace Animation Pasted") if payload else None


def paste_worldspace_selected_range(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.paste_worldspace_selected_range"):
        return None
    time_range = _get_selected_time_range()
    if time_range is None:
        _warning("Highlight a time range in the timeline first.")
        return None
    payload = _load_animation_or_warn()
    if not payload:
        return None
    return _apply_payload(
        payload,
        time_range=time_range,
        label="Worldspace Selected Range Pasted",
    )


def paste_worldspace_playback_range(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.paste_worldspace_playback_range"):
        return None
    payload = _load_animation_or_warn()
    if not payload:
        return None
    return _apply_payload(
        payload,
        time_range=_playback_range(animation=False),
        label="Worldspace Playback Range Pasted",
    )


def paste_worldspace_current_frame(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.paste_worldspace_current_frame"):
        return None
    payload = _load_frame_or_warn()
    if not payload:
        return None
    return _apply_payload(
        payload,
        destination_time=float(cmds.currentTime(query=True)),
        replace=False,
        label="Worldspace Frame Pasted",
    )


# ---------------------------------------------------------------------------
# Auto Worldspace: keep copied targets pinned while a parent/space changes
# ---------------------------------------------------------------------------

def _auto_apply_now(*args):
    _state.auto_pending = False
    if not _state.auto_enabled or _state.busy:
        return
    _state.busy = True
    try:
        ordered = sorted(
            _state.auto_matrices.items(),
            key=lambda item: (item[0].count("|"), item[0]),
        )
        for target, values in ordered:
            if cmds.objExists(target):
                try:
                    _apply_world_sample(target, values)
                except Exception:
                    pass
    finally:
        _state.busy = False


def _schedule_auto_apply(*args):
    if not _state.auto_enabled or _state.busy or _state.auto_pending:
        return
    _state.auto_pending = True
    if cmds.about(batch=True):
        _auto_apply_now()
    else:
        try:
            cmds.evalDeferred(_auto_apply_now, lowestPriority=True)
        except Exception:
            _auto_apply_now()


def _auto_attribute_callback(message, plug, other_plug, client_data):
    if message & om.MNodeMessage.kAttributeSet:
        _schedule_auto_apply()


def _auto_time_callback(*args):
    _schedule_auto_apply()


def _auto_world_matrix_callback(*args):
    _schedule_auto_apply()


def _auto_scene_callback(*args):
    disable_auto_worldspace()


def _ancestor_nodes(nodes):
    ancestors = []
    seen = set()
    for node in nodes:
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        current = parents[0] if parents else None
        while current:
            if current not in seen:
                seen.add(current)
                ancestors.append(current)
            parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
            current = parents[0] if parents else None
    return ancestors


def enable_auto_worldspace():
    if _state.auto_enabled:
        return True
    payload = _read_payload(_get_frame_data_file(), kind="frame")
    if not payload:
        _warning("Copy a worldspace frame before enabling Auto Worldspace.")
        return False
    mapping = _target_mapping(payload)
    if not mapping:
        return False

    matrices = {}
    for target, source in mapping:
        times = sorted(float(value) for value in source.get("samples", {}))
        if not times:
            continue
        matrices[target] = _sample_for_time(source, times[0])
    if not matrices:
        return False

    disable_auto_worldspace()
    _state.auto_matrices = matrices
    _state.auto_enabled = True
    callback_ids = []
    targets = list(matrices)
    for target in targets:
        try:
            callback_ids.append(om.MDagMessage.addWorldMatrixModifiedCallback(
                _dag_path(target), _auto_world_matrix_callback
            ))
        except Exception:
            pass
    for node in targets + _ancestor_nodes(targets):
        try:
            selection = om.MSelectionList()
            selection.add(node)
            callback_ids.append(om.MNodeMessage.addAttributeChangedCallback(
                selection.getDependNode(0), _auto_attribute_callback
            ))
        except Exception:
            pass
    try:
        _state.time_callback_id = om.MEventMessage.addEventCallback(
            "timeChanged", _auto_time_callback
        )
        callback_ids.append(_state.time_callback_id)
    except Exception:
        _state.time_callback_id = None
    for event in (om.MSceneMessage.kBeforeNew, om.MSceneMessage.kBeforeOpen):
        try:
            callback_ids.append(om.MSceneMessage.addCallback(event, _auto_scene_callback))
        except Exception:
            pass
    _state.callback_ids = callback_ids
    _state.attribute_callback_id = callback_ids[0] if callback_ids else None
    _auto_apply_now()
    return True


def disable_auto_worldspace(*args):
    for callback_id in list(_state.callback_ids):
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass
    _state.callback_ids = []
    _state.attribute_callback_id = None
    _state.time_callback_id = None
    _state.auto_enabled = False
    _state.auto_pending = False
    _state.auto_matrices = {}
    return True


def toggle_auto_worldspace(*args):
    if not _require_context("AnimKey.buttons.copyWorldspace.toggle_auto_worldspace"):
        return None
    if _state.auto_enabled:
        disable_auto_worldspace()
        _message("Auto Worldspace: OFF", "World-space pin released.", False)
        return False
    if enable_auto_worldspace():
        _message(
            "Auto Worldspace: ON",
            "Copied targets stay pinned while their parent or space changes.",
        )
        return True
    return False


def is_auto_worldspace_enabled():
    return bool(_state.auto_enabled)


# ---------------------------------------------------------------------------
# Toolbar entry point
# ---------------------------------------------------------------------------

def execute(*args):
    """Normal click copies a frame; Shift pastes it; Ctrl toggles Auto."""
    if not _require_context("AnimKey.buttons.copyWorldspace.execute"):
        return None
    try:
        modifiers = int(mel.eval("getModifiers"))
    except Exception:
        modifiers = 0
    if modifiers & 4:
        return toggle_auto_worldspace()
    if modifiers & 1:
        return paste_worldspace_current_frame()
    return copy_worldspace_current_frame()
