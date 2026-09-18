"""
    AnimKey Slider: Tweener
    
    Tween between previous and next keyframe.
    AnimKey blend functionality.
    
    Features:
    - Blends between previous and next keyframe values
    - Works on selected objects and channels
    - Respects keyframe tangents
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection

    PERFORMANCE NOTES:
    - prepare_tween_data() is called ONCE when the user starts dragging.
      It pre-fetches ALL Maya data and stores the result in _tween_cache.
    - execute() does ZERO Maya API queries — pure Python math + setKeyframe.
    - Binary search (bisect) replaces all linear scans.
    - cmds.keyframe(valueChange=True) replaces getAttr(time=) for 5-10x speedup.
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import bisect

from AnimKey.sliders.slider_utils import (
    finalize_slider_value,
    get_curve_target,
    get_frames_to_process,
    get_graph_editor_curve_entries,
    get_keyframes_for_attribute,
    get_processing_context,
    get_slider_value,
    normalize_attr_name,
    should_process_attribute
)


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_tween_cache  = []
_is_dragging  = False
_current_time = 0.0


_API_TIME_CURVE_TYPES = {
    "animCurveTA",
    "animCurveTL",
    "animCurveTU",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prev_next_frames(sorted_frames, target):
    """
    Pure bisect lookup for prev/next frame around `target`.
    No loops — O(log n) only.
    """
    idx = bisect.bisect_left(sorted_frames, target)

    prev_frame = sorted_frames[idx - 1] if idx > 0 else None

    # Next: first frame strictly greater than target
    # bisect_right gives us the insertion point AFTER any equal values
    right_idx = bisect.bisect_right(sorted_frames, target)
    next_frame = sorted_frames[right_idx] if right_idx < len(sorted_frames) else None

    return prev_frame, next_frame


def _get_key_value(attr_full, frame):
    """
    Get the value of an attribute at a specific keyed frame.
    Uses cmds.keyframe(valueChange=True) which is 5-10x faster than
    cmds.getAttr(time=) because it reads directly from the anim curve
    without triggering a full DG evaluation.
    
    Falls back to getAttr for the current time (which may not have a key).
    """
    try:
        raw = get_slider_value(attr_full, frame)
        if isinstance(raw, (int, float)):
            return float(raw), True
        if isinstance(raw, (list, tuple)) and len(raw) == 1:
            return float(raw[0]), True
    except Exception:
        pass
    return None, False


def _sample_curve_value(curve, frames, values, frame):
    """Read a cached key value, evaluating the curve only for an unkeyed time."""
    try:
        numeric_frame = float(frame)
        index = bisect.bisect_left(frames, numeric_frame)
        for candidate in (index, index - 1):
            if 0 <= candidate < len(frames):
                if abs(float(frames[candidate]) - numeric_frame) < 0.0001:
                    if candidate < len(values):
                        return float(values[candidate]), True
        evaluated = cmds.keyframe(
            curve,
            query=True,
            time=(numeric_frame, numeric_frame),
            eval=True,
        ) or []
        if evaluated:
            return float(evaluated[0]), True
    except Exception:
        pass
    return None, False


def _direct_curve_for_attribute(attr_full):
    """Fast no-animation-layer curve lookup, including pairBlend fallbacks."""
    try:
        curves = cmds.listConnections(
            attr_full,
            source=True,
            destination=False,
            type="animCurve",
            skipConversionNodes=True,
        ) or []
    except Exception:
        curves = []
    if curves:
        return curves[0]
    try:
        return (cmds.keyframe(attr_full, query=True, name=True) or [None])[0]
    except Exception:
        return None


def _plug_aliases(plug):
    """Return stable short/long-path aliases for a destination plug."""
    if not plug or "." not in plug:
        return {plug} if plug else set()
    node, attribute = plug.rsplit(".", 1)
    aliases = {plug, node.split("|")[-1] + "." + attribute}
    return aliases


def _batch_connection_maps(objects):
    """Collect directly animated and generally connected plugs in two calls."""
    direct_curves = {}
    incoming_plugs = set()
    if not objects:
        return direct_curves, incoming_plugs

    try:
        connections = cmds.listConnections(
            objects,
            source=True,
            destination=False,
            connections=True,
            plugs=True,
            skipConversionNodes=False,
        ) or []
        for index in range(0, len(connections) - 1, 2):
            incoming_plugs.update(_plug_aliases(connections[index]))
    except Exception:
        pass

    try:
        curve_connections = cmds.listConnections(
            objects,
            source=True,
            destination=False,
            connections=True,
            plugs=True,
            type="animCurve",
            skipConversionNodes=True,
        ) or []
        for index in range(0, len(curve_connections) - 1, 2):
            destination = curve_connections[index]
            source = curve_connections[index + 1]
            curve = source.split(".", 1)[0]
            for alias in _plug_aliases(destination):
                direct_curves[alias] = curve
    except Exception:
        pass
    return direct_curves, incoming_plugs


def _fast_curve_for_attribute(attr_full, active_layer,
                              has_animation_layers,
                              animation_curve_transfer=None):
    """Resolve one channel without rediscovering the scene's layers.

    ``slider_utils.get_curve_target`` is deliberately defensive because it is
    shared by every slider.  In a large shot that defense used to call
    ``cmds.ls(type='animLayer')`` several times per channel.  The Tweener has
    already captured the active layer and the layer-existence flag, so it can
    take the much cheaper direct route here.
    """
    if not has_animation_layers or active_layer in (None, "BaseAnimation"):
        return _direct_curve_for_attribute(attr_full)
    if animation_curve_transfer is None:
        return None
    try:
        return animation_curve_transfer.resolve_anim_curve(
            attr_full,
            layer_name=active_layer,
            has_animation_layers=has_animation_layers,
        )
    except Exception:
        return None


def _downstream_plugs(item):
    try:
        return cmds.listConnections(
            item,
            source=False,
            destination=True,
            plugs=True,
            skipConversionNodes=False,
        ) or []
    except Exception:
        return []


def _specific_output_plug(node, input_attr, node_type):
    """Keep XYZ components separate while traversing an anim-layer graph."""
    if node_type.startswith("animBlendNode"):
        if input_attr.startswith("inputA") or input_attr.startswith("inputB"):
            return "{}.output{}".format(node, input_attr[6:])
    if node_type in (
        "unitConversion", "addDoubleLinear", "multDoubleLinear",
        "blendWeighted",
    ):
        return node + ".output"
    if node_type == "pairBlend":
        for group in ("Translate", "Rotate"):
            marker = group.lower()
            if marker in input_attr.lower() and input_attr[-1:] in "XYZxyz":
                return "{}.out{}{}".format(
                    node, group, input_attr[-1].upper()
                )
    return None


def _driven_plugs_for_layer_curve(curve, max_nodes=64):
    """Resolve exact final plugs without expanding one rotation axis to XYZ."""
    pending = list(_downstream_plugs(curve))
    visited = set()
    resolved = []
    while pending and len(visited) < int(max_nodes):
        plug = pending.pop(0)
        if plug in visited or "." not in plug:
            continue
        visited.add(plug)
        node, input_attr = plug.split(".", 1)
        try:
            node_type = cmds.nodeType(node)
        except Exception:
            node_type = ""
        if node_type == "animLayer":
            continue
        output_plug = _specific_output_plug(node, input_attr, node_type)
        if output_plug:
            for downstream in _downstream_plugs(output_plug):
                if downstream not in visited:
                    pending.append(downstream)
            continue
        if plug not in resolved:
            resolved.append(plug)
    return resolved


def _layer_curve_map(layer_name):
    """Build the active layer's plug→curve map once per slider gesture."""
    if not layer_name:
        return {}
    try:
        curves = cmds.animLayer(
            layer_name, query=True, animCurves=True
        ) or []
    except Exception:
        curves = []
    mapping = {}
    for curve in curves:
        for plug in _driven_plugs_for_layer_curve(curve):
            mapping[plug] = curve
            try:
                node, attribute = plug.rsplit(".", 1)
                short_node = (cmds.ls(node, shortNames=True) or [node])[0]
                long_node = (cmds.ls(node, long=True) or [node])[0]
                mapping[short_node + "." + attribute] = curve
                mapping[long_node + "." + attribute] = curve
            except Exception:
                pass
    return mapping


def _to_float(value):
    """Convert a scalar/list Maya value to float, or None."""
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            return None
        value = value[0]
    if isinstance(value, (int, float, bool)):
        return float(value)
    return None


def _api_curve_handle(curve, cache):
    """Return a cached API 2.0 curve handle suitable for live preview."""
    if not curve:
        return None
    if curve in cache:
        return cache[curve]
    try:
        curve_type = cmds.nodeType(curve)
        if curve_type not in _API_TIME_CURVE_TYPES:
            cache[curve] = None
            return None
        selection = om.MSelectionList()
        selection.add(curve)
        handle = (oma.MFnAnimCurve(selection.getDependNode(0)), curve_type)
    except Exception:
        handle = None
    cache[curve] = handle
    return handle


def _api_value_from_ui(value, curve_type):
    """Convert cmds.keyframe display units to MFnAnimCurve internal units."""
    value = float(value)
    if curve_type == "animCurveTA":
        return om.MAngle(value, om.MAngle.uiUnit()).asRadians()
    if curve_type == "animCurveTL":
        return om.MDistance(value, om.MDistance.uiUnit()).asCentimeters()
    return value


def _api_value_to_ui(value, curve_type):
    """Convert MFnAnimCurve internal units to cmds.keyframe display units."""
    value = float(value)
    if curve_type == "animCurveTA":
        return om.MAngle(value, om.MAngle.kRadians).asUnits(
            om.MAngle.uiUnit()
        )
    if curve_type == "animCurveTL":
        return om.MDistance(value, om.MDistance.kCentimeters).asUnits(
            om.MDistance.uiUnit()
        )
    return value


def _api_curve_samples(curve_fn, curve_type):
    """Read every time/value pair without round-tripping through maya.cmds."""
    frames = []
    values = []
    for index in range(curve_fn.numKeys):
        frames.append(float(curve_fn.input(index).asUnits(om.MTime.uiUnit())))
        values.append(_api_value_to_ui(curve_fn.value(index), curve_type))
    return frames, values


def _api_sample_curve_value(curve_fn, curve_type, frames, values, frame):
    """Read a keyed sample or evaluate one unkeyed time through API 2.0."""
    numeric_frame = float(frame)
    index = bisect.bisect_left(frames, numeric_frame)
    for candidate in (index, index - 1):
        if 0 <= candidate < len(frames):
            if abs(float(frames[candidate]) - numeric_frame) < 0.0001:
                return float(values[candidate]), True
    try:
        value = curve_fn.evaluate(
            om.MTime(numeric_frame, om.MTime.uiUnit())
        )
        return _api_value_to_ui(value, curve_type), True
    except Exception:
        return None, False


def _api_tangent_name(curve_fn, key_index):
    """Return the cmds-compatible out-tangent name for one API key."""
    names = (
        ("kTangentGlobal", "global"),
        ("kTangentFixed", "fixed"),
        ("kTangentLinear", "linear"),
        ("kTangentFlat", "flat"),
        ("kTangentSmooth", "smooth"),
        ("kTangentStep", "step"),
        ("kTangentSlow", "slow"),
        ("kTangentFast", "fast"),
        ("kTangentClamped", "clamped"),
        ("kTangentPlateau", "plateau"),
        ("kTangentStepNext", "stepnext"),
        ("kTangentAuto", "auto"),
        ("kTangentAutoMix", "autoMix"),
    )
    try:
        tangent_type = int(curve_fn.outTangentType(int(key_index)))
    except Exception:
        return None
    for enum_name, command_name in names:
        enum_value = getattr(oma.MFnAnimCurve, enum_name, None)
        if enum_value is not None and tangent_type == int(enum_value):
            return command_name
    return None


def _channel_is_requested(attribute, requested_channels):
    """Fast Channel Box/explicit-attribute match without Maya round-trips."""
    if not requested_channels:
        return True
    attribute_names = set(normalize_attr_name(attribute))
    attribute_names.add(attribute)
    for channel in requested_channels:
        channel_names = set(normalize_attr_name(channel))
        channel_names.add(channel)
        if attribute_names.intersection(channel_names):
            return True
    return False


def _selected_node_names(objects):
    """Return exact and leaf names for inexpensive driven-plug filtering."""
    exact = set(objects or [])
    try:
        exact.update(cmds.ls(objects or [], long=True) or [])
    except Exception:
        pass
    leaves = {name.split("|")[-1] for name in exact}
    return exact, leaves


def _plug_belongs_to_selection(plug, exact_nodes, leaf_nodes):
    if not plug or "." not in plug:
        return False
    node = plug.rsplit(".", 1)[0]
    return node in exact_nodes or node.split("|")[-1] in leaf_nodes


def _batched_curve_destinations(curves):
    """Return immediate destination plugs for many curves in one Maya call."""
    result = {}
    if not curves:
        return result
    try:
        connections = cmds.listConnections(
            curves,
            source=False,
            destination=True,
            connections=True,
            plugs=True,
            skipConversionNodes=False,
        ) or []
    except Exception:
        return result
    for index in range(0, len(connections) - 1, 2):
        source = connections[index]
        destination = connections[index + 1]
        curve = source.split(".", 1)[0]
        result.setdefault(curve, []).append(destination)
    return result


def _curve_first_records(objects, attrs, context, active_layer):
    """Resolve only the concrete curves that the gesture can modify.

    This mirrors Animo's fast path: Graph Editor curves are authoritative;
    otherwise Maya is asked for the active-layer curves of the selected
    objects/plugs in one query.  ``None`` requests the defensive legacy path
    when an unusual graph cannot be mapped without risking a wrong channel.
    """
    ge_selection = (
        context.get('ge_selection') or
        context.get('graph_editor_selection') or {}
    )
    exact_nodes, leaf_nodes = _selected_node_names(objects)
    requested_channels = list(attrs or context.get('selected_channels') or [])
    records = []

    if ge_selection:
        entries = get_graph_editor_curve_entries()
        if not entries:
            return None
        for entry in entries:
            curve = entry.get('curve')
            plugs = entry.get('driven_plugs') or []
            chosen = None
            for plug in plugs:
                if not _plug_belongs_to_selection(
                    plug, exact_nodes, leaf_nodes
                ):
                    continue
                attribute = plug.rsplit('.', 1)[-1].split('[', 1)[0]
                if _channel_is_requested(attribute, requested_channels):
                    chosen = plug
                    break
            if not curve or not chosen:
                return None
            records.append({
                'curve': curve,
                'attr_full': chosen,
                'attr': chosen.rsplit('.', 1)[-1].split('[', 1)[0],
                'selected_frames': list(entry.get('frames') or []),
            })
        return records

    if requested_channels:
        plugs = [
            '{}.{}'.format(obj, attribute)
            for obj in objects
            for attribute in requested_channels
        ]
        try:
            targets = cmds.ls(plugs) or []
        except Exception:
            targets = []
    else:
        targets = list(objects)

    if not targets:
        return []
    try:
        curves = list(dict.fromkeys(
            cmds.keyframe(targets, query=True, name=True) or []
        ))
    except Exception:
        return None
    if not curves:
        return []

    immediate_destinations = _batched_curve_destinations(curves)
    for curve in curves:
        chosen = None
        candidates = immediate_destinations.get(curve, [])
        for plug in candidates:
            if _plug_belongs_to_selection(plug, exact_nodes, leaf_nodes):
                attribute = plug.rsplit('.', 1)[-1].split('[', 1)[0]
                if _channel_is_requested(attribute, requested_channels):
                    chosen = plug
                    break

        # Animation Layers, pairBlend and conversion graphs do not connect the
        # curve directly to the control.  Walk only this selection's curves,
        # never every curve on the layer or in the shot.
        if not chosen:
            for plug in _driven_plugs_for_layer_curve(curve):
                if not _plug_belongs_to_selection(
                    plug, exact_nodes, leaf_nodes
                ):
                    continue
                attribute = plug.rsplit('.', 1)[-1].split('[', 1)[0]
                if _channel_is_requested(attribute, requested_channels):
                    chosen = plug
                    break

        if not chosen:
            return None
        records.append({
            'curve': curve,
            'attr_full': chosen,
            'attr': chosen.rsplit('.', 1)[-1].split('[', 1)[0],
            'selected_frames': None,
        })
    return records


def _prepare_curve_first_entries(records, context, current_time,
                                 has_animation_layers, active_layer):
    """Build the live-preview cache directly from selected anim curves."""
    entries = []
    api_curve_cache = {}
    time_range = context.get('time_range')
    ge_selection = (
        context.get('ge_selection') or
        context.get('graph_editor_selection') or {}
    )
    needs_scene_evaluation = bool(
        has_animation_layers and (
            bool(ge_selection) or
            active_layer not in (None, 'BaseAnimation')
        )
    )

    for record in records:
        curve = record['curve']
        attr_full = record['attr_full']
        api_handle = _api_curve_handle(curve, api_curve_cache)
        try:
            if api_handle:
                frames, values = _api_curve_samples(*api_handle)
            else:
                frames = [float(value) for value in (
                    cmds.keyframe(curve, query=True, timeChange=True) or []
                )]
                values = [float(value) for value in (
                    cmds.keyframe(curve, query=True, valueChange=True) or []
                )]
                samples = sorted(zip(frames, values))
                frames = [sample[0] for sample in samples]
                values = [sample[1] for sample in samples]
        except Exception:
            continue
        if not frames:
            continue

        selected_frames = record.get('selected_frames')
        if selected_frames is not None:
            frames_to_process = [float(frame) for frame in selected_frames]
        elif time_range:
            start, end = time_range
            frames_to_process = [
                frame for frame in frames if start <= frame <= end
            ]
        else:
            index = bisect.bisect_left(frames, float(current_time))
            if (
                index < len(frames) and
                abs(frames[index] - float(current_time)) < 0.0001
            ):
                frames_to_process = [frames[index]]
            else:
                frames_to_process = [float(current_time)]

        for frame in frames_to_process:
            numeric_frame = float(frame)
            key_index = bisect.bisect_left(frames, numeric_frame)
            frame_is_keyed = (
                key_index < len(frames) and
                abs(frames[key_index] - numeric_frame) < 0.0001
            )
            prev_f, next_f = _prev_next_frames(frames, numeric_frame)

            if api_handle:
                reader = lambda sample_frame: _api_sample_curve_value(
                    api_handle[0], api_handle[1], frames, values, sample_frame
                )
            else:
                reader = lambda sample_frame: _sample_curve_value(
                    curve, frames, values, sample_frame
                )

            cur_val, ok = reader(numeric_frame)
            if not ok:
                continue
            if prev_f is not None:
                prev_val, prev_ok = reader(prev_f)
            else:
                prev_val, prev_ok = cur_val, True
            if next_f is not None:
                next_val, next_ok = reader(next_f)
            else:
                next_val, next_ok = cur_val, True
            if not (prev_ok and next_ok) or (
                prev_f is None and next_f is None
            ):
                continue

            previous_index = (
                bisect.bisect_left(frames, float(prev_f))
                if prev_f is not None else None
            )
            prev_tan = (
                _api_tangent_name(api_handle[0], previous_index)
                if api_handle and previous_index is not None else None
            )
            entry = {
                'attr_full': attr_full,
                'curve_target': curve,
                'frame': numeric_frame,
                'frame_is_keyed': frame_is_keyed,
                'needs_finalize': False,
                'prev_val': prev_val,
                'next_val': next_val,
                'cur_val': cur_val,
                # Tween interpolation stays between two valid keyed values, so
                # querying attribute limits per channel is unnecessary.
                'min_val': None,
                'max_val': None,
                'is_current': abs(
                    numeric_frame - float(current_time)
                ) < 0.0001,
                'prev_tan': prev_tan,
                'needs_scene_evaluation': needs_scene_evaluation,
            }
            if api_handle:
                curve_fn, curve_type = api_handle
                entry.update({
                    'api_curve_fn': curve_fn,
                    'api_curve_type': curve_type,
                    'api_key_index': int(key_index) if frame_is_keyed else None,
                    'api_created_key': False,
                    'api_preview_used': False,
                })
                if frame_is_keyed:
                    entry['api_original_internal'] = float(
                        curve_fn.value(int(key_index))
                    )
            entries.append(entry)
    return entries


def _restore_entry_api_value(entry):
    """Restore one uncommitted API preview to its pre-drag value."""
    if not entry.get('api_preview_used'):
        return
    curve_fn = entry.get('api_curve_fn')
    key_index = entry.get('api_key_index')
    if curve_fn is None or key_index is None:
        return
    try:
        if entry.get('api_created_key'):
            curve_fn.remove(int(key_index))
            entry['api_key_index'] = None
            entry['api_created_key'] = False
        else:
            original_value = entry.get('api_original_internal')
            if original_value is None:
                return
            curve_fn.setValue(int(key_index), float(original_value))
        entry['api_preview_used'] = False
    except Exception:
        pass


def _restore_api_preview():
    """Cancel raw API previews left by an interrupted slider gesture."""
    for entry in _tween_cache:
        _restore_entry_api_value(entry)


# ---------------------------------------------------------------------------
# prepare_tween_data  — called ONCE per drag gesture
# ---------------------------------------------------------------------------

def prepare_tween_data(objs=None, attrs=None):
    """
    Build the tween cache for the current selection.

    All slow Maya API calls happen here. execute() only does pure math.
    
    Optimizations vs original:
    - cmds.keyframe(valueChange=True) instead of getAttr(time=) — skips DG eval
    - Pure bisect for prev/next frame — no linear scan
    - Batch attribute validation
    """
    global _tween_cache, _current_time, _is_dragging

    # Recover cleanly if a previous drag was interrupted before mouse release.
    if _is_dragging:
        _restore_api_preview()
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        _is_dragging = False

    _tween_cache  = []
    _current_time = cmds.currentTime(query=True)

    # -- Context -------------------------------------------------------
    context = get_processing_context(explicit_attributes=attrs is not None)
    selected_channels = context.get('selected_channels')
    ge_selection = (
        context.get('ge_selection')
        or context.get('graph_editor_selection')
        or {}
    )

    # Whole-rig and Channel Box workflows do not need to rediscover the
    # selected animation layer for every channel.
    fast_rig_path = not bool(ge_selection)
    active_layer = None
    try:
        has_animation_layers = bool(cmds.ls(type="animLayer"))
    except Exception:
        has_animation_layers = False
    active_layer_curves = {}
    direct_curve_map = {}
    incoming_plugs = set()
    animation_curve_transfer = None
    try:
        from AnimKey.core import animation_curve_transfer
        active_layer = animation_curve_transfer.active_animation_layer()
    except Exception:
        animation_curve_transfer = None

    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _tween_cache

    # Animo-style curve-first path.  It avoids listAttr/getAttr/attributeQuery
    # calls for every keyable channel on every selected control.  Graph Editor
    # keys, timeline ranges, Channel Box filters and the active animation layer
    # are already represented by the resolved curve records.
    curve_records = _curve_first_records(
        objects, attrs, context, active_layer
    )
    if curve_records is not None:
        if not curve_records:
            return _tween_cache
        _tween_cache = _prepare_curve_first_entries(
            curve_records,
            context,
            _current_time,
            has_animation_layers,
            active_layer,
        )
        return _tween_cache

    # Defensive fallback for unusual referenced/pairBlend graphs that cannot
    # be mapped to a unique final plug by the curve-first resolver.
    if fast_rig_path and has_animation_layers and active_layer not in (
        None, "BaseAnimation"
    ):
        active_layer_curves = _layer_curve_map(active_layer)

    if fast_rig_path and active_layer in (None, "BaseAnimation"):
        direct_curve_map, incoming_plugs = _batch_connection_maps(objects)

    api_curve_cache = {}

    # -- Build per-object data -----------------------------------------
    for obj in objects:
        if not cmds.objExists(obj):
            continue

        all_attrs = attrs if attrs else (cmds.listAttr(obj, keyable=True) or [])
        if not all_attrs:
            continue

        for attr in all_attrs:
            if not should_process_attribute(obj, attr, selected_channels):
                continue

            attr_full = f'{obj}.{attr}'

            fast_curve_target = None
            fast_curve_frames = None
            fast_curve_values = None
            fast_curve_api_handle = None
            if fast_rig_path:
                try:
                    short_attr_full = obj.split("|")[-1] + "." + attr
                    fast_curve_target = (
                        direct_curve_map.get(attr_full) or
                        direct_curve_map.get(short_attr_full)
                    )
                    if active_layer_curves:
                        fast_curve_target = (
                            fast_curve_target or
                            active_layer_curves.get(attr_full)
                        )
                        if not fast_curve_target:
                            try:
                                long_obj = (cmds.ls(obj, long=True) or [obj])[0]
                                fast_curve_target = active_layer_curves.get(
                                    long_obj + "." + attr
                                )
                            except Exception:
                                pass
                    if (
                        not fast_curve_target and
                        (
                            active_layer not in (None, "BaseAnimation") or
                            attr_full in incoming_plugs or
                            short_attr_full in incoming_plugs
                        )
                    ):
                        fast_curve_target = _fast_curve_for_attribute(
                            attr_full,
                            active_layer,
                            has_animation_layers,
                            animation_curve_transfer,
                        )
                except Exception:
                    fast_curve_target = None
                # The fast layer map is intentionally optional.  Complex
                # nested/referenced layer networks do not always expose the
                # final plug through listConnections, even though Maya can
                # still resolve the layer curve through slider_utils.  Falling
                # back below keeps Tweener working instead of silently
                # discarding every channel in that situation.

            # ----- Fast attribute validation --------------------------
            try:
                if not fast_curve_target and not cmds.getAttr(attr_full, settable=True):
                    continue
                if cmds.getAttr(attr_full, lock=True):
                    continue
                a_type = cmds.getAttr(attr_full, type=True)
                if a_type in ('enum', 'string', 'message', 'bool'):
                    continue
            except Exception:
                continue

            # ----- Pre-query limits -----------------------------------
            min_val = None
            max_val = None
            try:
                if cmds.attributeQuery(attr, node=obj, minExists=True):
                    min_val = float(cmds.attributeQuery(attr, node=obj, minimum=True)[0])
                if cmds.attributeQuery(attr, node=obj, maxExists=True):
                    max_val = float(cmds.attributeQuery(attr, node=obj, maximum=True)[0])
            except Exception:
                pass

            # ----- All keyframe times (one call) ----------------------
            if fast_curve_target:
                try:
                    fast_curve_api_handle = _api_curve_handle(
                        fast_curve_target, api_curve_cache
                    )
                    if fast_curve_api_handle:
                        fast_curve_frames, fast_curve_values = (
                            _api_curve_samples(*fast_curve_api_handle)
                        )
                    else:
                        fast_curve_frames = [
                            float(value) for value in (
                                cmds.keyframe(
                                    fast_curve_target,
                                    query=True,
                                    timeChange=True,
                                ) or []
                            )
                        ]
                        fast_curve_values = [
                            float(value) for value in (
                                cmds.keyframe(
                                    fast_curve_target,
                                    query=True,
                                    valueChange=True,
                                ) or []
                            )
                        ]
                        samples = sorted(zip(
                            fast_curve_frames, fast_curve_values
                        ))
                        fast_curve_frames = [
                            sample[0] for sample in samples
                        ]
                        fast_curve_values = [
                            sample[1] for sample in samples
                        ]
                    all_frames = fast_curve_frames
                except Exception:
                    all_frames = []
            elif fast_rig_path:
                # No curve on the active layer means there is nothing for a
                # tween to interpolate.  Falling into slider_utils here used
                # to rescan every Animation Layer several times for each
                # unanimated keyable channel.
                continue
            else:
                all_frames = get_keyframes_for_attribute(attr_full, attr, context)
            if not all_frames:
                continue
            sorted_frames = sorted(set(all_frames))  # dedupe + sort

            # ----- Which frames to process ----------------------------
            frames_to_process = get_frames_to_process(
                attr_full, sorted_frames, attr, context
            )
            if not frames_to_process:
                continue

            # ----- Build cache entries --------------------------------
            for frame in frames_to_process:
                prev_f, next_f = _prev_next_frames(sorted_frames, frame)
                is_current = abs(float(frame) - float(_current_time)) < 0.0001
                curve_target = fast_curve_target or get_curve_target(
                    attr_full, frame, attr
                )
                keyed_index = bisect.bisect_left(sorted_frames, float(frame))
                frame_is_keyed = (
                    keyed_index < len(sorted_frames) and
                    abs(
                        float(sorted_frames[keyed_index]) - float(frame)
                    ) < 0.0001
                )
                try:
                    if fast_curve_api_handle:
                        value_reader = lambda sample_frame: (
                            _api_sample_curve_value(
                                fast_curve_api_handle[0],
                                fast_curve_api_handle[1],
                                fast_curve_frames,
                                fast_curve_values,
                                sample_frame,
                            )
                        )
                    elif fast_curve_target:
                        value_reader = lambda sample_frame: _sample_curve_value(
                            fast_curve_target,
                            fast_curve_frames,
                            fast_curve_values,
                            sample_frame,
                        )
                    else:
                        value_reader = lambda sample_frame: _get_key_value(
                            attr_full, sample_frame
                        )

                    cur_val, ok = value_reader(frame)
                    if not ok:
                        continue

                    if prev_f is not None and next_f is not None:
                        prev_val, ok1 = value_reader(prev_f)
                        next_val, ok2 = value_reader(next_f)
                        if not (ok1 and ok2):
                            continue
                    elif prev_f is not None:
                        prev_val, ok1 = value_reader(prev_f)
                        if not ok1:
                            continue
                        next_val = cur_val
                    elif next_f is not None:
                        next_val, ok2 = value_reader(next_f)
                        if not ok2:
                            continue
                        prev_val = cur_val
                    else:
                        continue  # isolated key — nothing to tween

                except Exception:
                    continue

                # Prev tangent type (for reset)
                prev_tan = None
                if prev_f is not None:
                    try:
                        previous_index = bisect.bisect_left(
                            sorted_frames, float(prev_f)
                        )
                        if fast_curve_api_handle:
                            prev_tan = _api_tangent_name(
                                fast_curve_api_handle[0], previous_index
                            )
                        else:
                            result = cmds.keyTangent(
                                curve_target,
                                query=True,
                                time=(prev_f,),
                                outTangentType=True,
                            )
                            prev_tan = result[0] if result else None
                    except Exception:
                        pass

                _tween_cache.append({
                    'attr_full':  attr_full,
                    'curve_target': curve_target,
                    'frame':      frame,
                    'frame_is_keyed': frame_is_keyed,
                    'needs_finalize': curve_target == attr_full and not frame_is_keyed,
                    'prev_val':   prev_val,
                    'next_val':   next_val,
                    'cur_val':    cur_val,   # store original for accurate reset
                    'min_val':    min_val,
                    'max_val':    max_val,
                    'is_current': is_current,
                    'prev_tan':   prev_tan,
                    'needs_scene_evaluation': bool(
                        has_animation_layers and
                        active_layer not in (None, "BaseAnimation")
                    ),
                })

                entry = _tween_cache[-1]
                if curve_target != attr_full:
                    api_handle = (
                        fast_curve_api_handle or
                        _api_curve_handle(curve_target, api_curve_cache)
                    )
                    if api_handle:
                        curve_fn, curve_type = api_handle
                        try:
                            api_data = {
                                'api_curve_fn': curve_fn,
                                'api_curve_type': curve_type,
                                'api_key_index': None,
                                'api_created_key': False,
                                'api_preview_used': False,
                            }
                            if frame_is_keyed:
                                key_index = curve_fn.find(om.MTime(
                                    float(frame), om.MTime.uiUnit()
                                ))
                                api_data.update({
                                    'api_key_index': int(key_index),
                                    'api_original_internal': float(
                                        curve_fn.value(int(key_index))
                                    ),
                                })
                            entry.update(api_data)
                        except Exception:
                            pass

    return _tween_cache


# ---------------------------------------------------------------------------
# execute  — called on EVERY slider tick
# ---------------------------------------------------------------------------

def execute(percentage):
    """
    Apply tween blend at `percentage` (0–100) to every cached entry.

    Does ZERO Maya API queries — only math + setKeyframe/setAttr.
    
    WILL NOT auto-prepare if cache is empty (caller must call
    prepare_tween_data() first). This avoids thread-safety issues.
    """
    global _tween_cache, _is_dragging

    if not _tween_cache:
        return  # Caller must prepare first — no auto-trigger

    # -- Resistance snapping at 0 / 50 / 100 --------------------------
    for snap, rng in ((100.0, 4.5), (50.0, 4.5), (0.0, 4.5)):
        if snap - rng <= percentage <= snap + rng:
            percentage = snap
            break

    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True

    ratio = percentage / 100.0
    scene_changed = False
    needs_scene_evaluation = False

    for entry in _tween_cache:
        prev_val  = entry['prev_val']
        next_val  = entry['next_val']
        attr_full = entry['attr_full']
        frame     = entry['frame']
        min_val   = entry['min_val']
        max_val   = entry['max_val']

        # Linear blend — pure Python, no Maya
        new_val = prev_val + (next_val - prev_val) * ratio

        # Clamp to pre-queried limits
        if min_val is not None and new_val < min_val:
            new_val = min_val
        if max_val is not None and new_val > max_val:
            new_val = max_val

        try:
            target = entry.get('curve_target') or attr_full
            curve_fn = entry.get('api_curve_fn')
            api_key_index = entry.get('api_key_index')
            if curve_fn is not None:
                internal_value = _api_value_from_ui(
                    new_val, entry.get('api_curve_type')
                )
                if api_key_index is None:
                    api_key_index = curve_fn.addKey(
                        om.MTime(float(frame), om.MTime.uiUnit()),
                        internal_value,
                    )
                    entry['api_key_index'] = int(api_key_index)
                    entry['api_created_key'] = True
                    entry['frame_is_keyed'] = True
                else:
                    curve_fn.setValue(int(api_key_index), internal_value)
                entry['api_preview_used'] = True
            elif entry.get('frame_is_keyed'):
                cmds.keyframe(
                    target,
                    edit=True,
                    time=(frame, frame),
                    valueChange=new_val,
                )
            elif target != attr_full:
                # Edit the concrete layer curve with cmds rather than an API
                # preview object.  Maya then dirties/evaluates the complete
                # animBlend network immediately, including referenced and
                # nested Animation Layers.
                cmds.setKeyframe(target, time=(frame,), value=new_val)
                entry['frame_is_keyed'] = True
            elif entry.get('is_current'):
                cmds.setAttr(attr_full, new_val)
            else:
                cmds.setKeyframe(attr_full, time=(frame,), value=new_val)
                entry['frame_is_keyed'] = True
            entry['last_value'] = new_val
            scene_changed = True
            needs_scene_evaluation = (
                needs_scene_evaluation or (
                    curve_fn is None and
                    bool(entry.get('needs_scene_evaluation'))
                )
            )
        except Exception:
            continue

    if scene_changed and needs_scene_evaluation:
        # Editing an animCurve directly does not always invalidate an
        # Animation Layer's composed result at the current time.  Reapplying
        # the same time is a single, batched Evaluation Manager invalidation
        # for the whole gesture tick, so the viewport follows the slider
        # without doing an expensive refresh for every channel.
        try:
            cmds.currentTime(_current_time, edit=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# reset  — called when the user releases the slider
# ---------------------------------------------------------------------------

def reset():
    """
    Finalise the tween operation:
    - Bake setAttr changes into keyframes at current time.
    - Restore tangent types where needed.
    - Close the undo chunk.
    
    Optimizations vs original:
    - For 'is_current' entries: read the already-set value via getAttr (no
      redundant setKeyframe → getAttr → setKeyframe cycle).
    - For non-current entries: skip entirely — value was already written
      by cmds.keyframe(edit=True) in execute(). No need to re-read and re-set.
    """
    global _tween_cache, _is_dragging, _current_time

    saved_time = _current_time or cmds.currentTime(query=True)
    offset_changes = []

    for entry in _tween_cache:
        attr_full = entry['attr_full']
        frame     = entry['frame']
        prev_tan  = entry['prev_tan']
        original_val = _to_float(entry.get('cur_val'))
        final_val = None

        try:
            final_val = _to_float(entry.get('last_value'))
            if final_val is None:
                final_val = original_val
            if final_val is None:
                continue

            if entry.get('api_preview_used'):
                # API edits make dense selections responsive, but they do not
                # enter Maya's undo queue. Restore the original value first,
                # then commit one normal Maya command at release so the whole
                # gesture remains a single reliable undo step.
                created_key = bool(entry.get('api_created_key'))
                target = entry.get('curve_target') or attr_full
                _restore_entry_api_value(entry)
                if created_key:
                    cmds.setKeyframe(
                        target, time=(frame,), value=final_val
                    )
                else:
                    cmds.keyframe(
                        target,
                        edit=True,
                        time=(frame, frame),
                        valueChange=final_val,
                    )
            elif entry.get('needs_finalize'):
                # Only the uncommon no-curve fallback changes the live plug
                # through setAttr. Concrete animation curves were keyed while
                # the slider was dragged.
                finalize_slider_value(attr_full, frame, final_val, saved_time)

            if original_val is not None and final_val is not None and abs(final_val - original_val) > 1e-9:
                offset_changes.append({
                    'attr_full': attr_full,
                    'frame': frame,
                    'original_value': original_val,
                    'new_value': final_val,
                })

            # Restore tangent type if needed
            if prev_tan:
                tangent_target = entry.get('curve_target') or attr_full
                try:
                    if prev_tan == 'step':
                        cmds.keyTangent(tangent_target, edit=True, time=(frame,),
                                        inTangentType='auto', outTangentType='step')
                    elif prev_tan == 'stepnext':
                        cmds.keyTangent(tangent_target, edit=True, time=(frame,),
                                        inTangentType='stepnext', outTangentType='auto')
                    else:
                        cmds.keyTangent(tangent_target, edit=True, time=(frame,),
                                        inTangentType=prev_tan, outTangentType=prev_tan)
                except Exception:
                    pass

        except Exception:
            continue

    if offset_changes:
        try:
            from AnimKey.buttons import animation_offset
            animation_offset.apply_slider_offset_changes(offset_changes)
        except Exception:
            pass

    # -- Clean up -------------------------------------------------------
    _tween_cache  = []
    _current_time = 0.0

    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
