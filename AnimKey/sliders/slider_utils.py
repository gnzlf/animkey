"""
    AnimKey Slider Utilities
    
    Shared utilities for all sliders to handle:
    - Timeline range selection
    - Graph Editor keyframe selection
    - Channel Box attribute selection
"""

import maya.cmds as cmds
import maya.mel as mel


_GRAPH_EDITOR_CURVE_ENTRIES = []


def _animation_offset_is_active():
    """Return True when the orange Animation Offset range owns the time slider."""
    try:
        from AnimKey.buttons import animation_offset
        return animation_offset.is_active()
    except Exception:
        return False


def _node_from_plug(plug):
    return (plug or '').split('.', 1)[0]


def _is_anim_layer_blend_node(node):
    try:
        node_type = cmds.nodeType(node)
    except Exception:
        return False

    if not node_type:
        return False

    if node_type.startswith('animBlendNode'):
        return True

    return node_type in (
        'blendWeighted',
        'pairBlend',
        'unitConversion',
        'addDoubleLinear',
        'multDoubleLinear',
    )


def _downstream_plugs(item):
    try:
        return cmds.listConnections(
            item,
            source=False,
            destination=True,
            plugs=True,
            skipConversionNodes=False
        ) or []
    except Exception:
        return []


def _anim_curve_driven_plugs(curve):
    """
    Resolve the final driven plugs for an animCurve.

    Anim layers usually connect selected animCurves into animBlend nodes before
    the real control attribute. Walking downstream lets Graph Editor selected
    layer keys map back to the visible rig channel.
    """
    resolved = []
    pending = list(_downstream_plugs(curve))
    visited = set()

    while pending and len(visited) < 80:
        plug = pending.pop(0)
        if plug in visited:
            continue
        visited.add(plug)

        node = _node_from_plug(plug)
        if not node:
            continue

        if _is_anim_layer_blend_node(node):
            next_plugs = _downstream_plugs(node)
            if not next_plugs:
                next_plugs = _downstream_plugs(plug)
            for next_plug in next_plugs:
                if next_plug not in visited:
                    pending.append(next_plug)
            continue

        if plug not in resolved:
            resolved.append(plug)

    return resolved


def _remember_graph_curve(curve, frames, driven_plugs):
    global _GRAPH_EDITOR_CURVE_ENTRIES
    _GRAPH_EDITOR_CURVE_ENTRIES.append({
        'curve': curve,
        'frames': _as_unique_sorted_frames(frames),
        'driven_plugs': driven_plugs or []
    })


def get_selected_time_range():
    """
    Get the selected time range from Maya's time slider.
    
    Returns:
        tuple: (start_time, end_time) or None if no range selected
    """
    try:
        aTimeSlider = mel.eval('global string $gPlayBackSlider; $tmpVar=$gPlayBackSlider')
        if not aTimeSlider:
            return None
        time_range = cmds.timeControl(aTimeSlider, query=True, rangeArray=True)
        
        if time_range and len(time_range) == 2:
            start, end = time_range[0], time_range[1]
            # Check if it's actually a range (more than 1 frame)
            if (end - start) > 1:
                return (start, end - 1)  # end is exclusive in Maya
    except:
        pass
    
    return None


def get_selected_channels():
    """
    Get selected channels from the Channel Box.
    
    Returns:
        list: List of selected attribute names or None
    """
    try:
        main_channel_box = mel.eval('global string $gChannelBoxName; $temp=$gChannelBoxName;')
        
        # Get selected main attributes
        selected_attrs = cmds.channelBox(main_channel_box, query=True, selectedMainAttributes=True) or []
        
        # Also check shape attributes
        shape_attrs = cmds.channelBox(main_channel_box, query=True, selectedShapeAttributes=True) or []
        
        # Also check history attributes
        history_attrs = cmds.channelBox(main_channel_box, query=True, selectedHistoryAttributes=True) or []
        
        # Also check output attributes
        output_attrs = cmds.channelBox(main_channel_box, query=True, selectedOutputAttributes=True) or []
        
        all_attrs = selected_attrs + shape_attrs + history_attrs + output_attrs
        
        return all_attrs if all_attrs else None
    except:
        return None


# Mapping between short and long attribute names
ATTR_NAME_MAP = {
    # Short to long
    'tx': 'translateX', 'ty': 'translateY', 'tz': 'translateZ',
    'rx': 'rotateX', 'ry': 'rotateY', 'rz': 'rotateZ',
    'sx': 'scaleX', 'sy': 'scaleY', 'sz': 'scaleZ',
    'v': 'visibility',
    # Long to short (reverse mapping)
    'translateX': 'tx', 'translateY': 'ty', 'translateZ': 'tz',
    'rotateX': 'rx', 'rotateY': 'ry', 'rotateZ': 'rz',
    'scaleX': 'sx', 'scaleY': 'sy', 'scaleZ': 'sz',
    'visibility': 'v'
}


def normalize_attr_name(attr):
    """
    Get both short and long versions of an attribute name.
    
    Args:
        attr: Attribute name (short or long)
    
    Returns:
        tuple: (short_name, long_name)
    """
    # Check if it's in our mapping
    if attr in ATTR_NAME_MAP:
        other = ATTR_NAME_MAP[attr]
        # Determine which is short and which is long
        if len(attr) <= 2:
            return (attr, other)
        else:
            return (other, attr)
    
    # For custom attributes, return the same for both
    return (attr, attr)


def get_graph_editor_selection():
    """
    Get selected keyframes from the Graph Editor.
    
    Returns:
        dict: {attr_full: [frame1, frame2, ...]} or empty dict
    """
    global _GRAPH_EDITOR_CURVE_ENTRIES
    result = {}
    _GRAPH_EDITOR_CURVE_ENTRIES = []
    
    try:
        # Get selected animation curves
        anim_curves = cmds.keyframe(query=True, selected=True, name=True)
        
        if not anim_curves:
            return result
        
        for curve in anim_curves:
            # Get selected keyframe times for this curve
            selected_times = cmds.keyframe(curve, query=True, selected=True, timeChange=True)
            
            if selected_times:
                driven_plugs = _anim_curve_driven_plugs(curve)
                _remember_graph_curve(curve, selected_times, driven_plugs)

                for attr_full in driven_plugs:
                    result.setdefault(attr_full, [])
                    result[attr_full].extend(selected_times)

                # Keep the curve entry as a fallback for direct curve-name
                # matching and for sliders that operate on selected curves.
                result.setdefault(curve, [])
                result[curve].extend(selected_times)
    except:
        pass
    
    return result


def get_keyframes_to_modify(obj, attr, current_time):
    """
    Determine which keyframes should be modified based on selection context.
    
    Args:
        obj: Object name
        attr: Attribute name
        current_time: Current timeline time
    
    Returns:
        list: List of frame times to modify, or None to modify current time only
    """
    attr_full = f"{obj}.{attr}"
    context = get_processing_context()
    all_keyframes = get_keyframes_for_attribute(attr_full, attr, context)
    frames = get_frames_to_process(
        attr_full,
        all_keyframes,
        attr,
        context,
        current_time=current_time
    )
    return frames or None


def should_process_attribute(obj, attr, selected_channels=None):
    """
    Check if an attribute should be processed based on Channel Box selection.
    
    Args:
        obj: Object name
        attr: Attribute name
        selected_channels: Pre-fetched selected channels or None to fetch
    
    Returns:
        bool: True if attribute should be processed
    """
    if selected_channels is None:
        selected_channels = get_selected_channels()
    
    # If no channels selected, process all
    if not selected_channels:
        return True
    
    # Get both short and long versions of the attribute we're checking
    attr_short, attr_long = normalize_attr_name(attr)
    
    # Also try to get the actual long name from Maya
    try:
        maya_long_name = cmds.attributeQuery(attr, node=obj, longName=True)
    except:
        maya_long_name = attr
    
    try:
        maya_short_name = cmds.attributeQuery(attr, node=obj, shortName=True)
    except:
        maya_short_name = attr
    
    # Check each selected channel
    for sel_channel in selected_channels:
        # Get both versions of the selected channel
        sel_short, sel_long = normalize_attr_name(sel_channel)
        
        # Compare all combinations
        if sel_channel in (attr, attr_short, attr_long, maya_long_name, maya_short_name):
            return True
        if sel_short in (attr, attr_short, attr_long, maya_long_name, maya_short_name):
            return True
        if sel_long in (attr, attr_short, attr_long, maya_long_name, maya_short_name):
            return True
    
    return False


def get_processing_context():
    """
    Get the full context for slider processing.
    
    Returns:
        dict: {
            'time_range': (start, end) or None,
            'selected_channels': list or None,
            'graph_editor_selection': dict or {},
            'current_time': float
        }
    """
    ge_selection = get_graph_editor_selection()
    time_range = get_selected_time_range()

    # When Animation Offset is active, the selected time range belongs to the
    # offset tool. Sliders should work on the current/GE-selected key and let
    # the offset propagate that change to the orange range.
    if _animation_offset_is_active():
        time_range = None

    return {
        'time_range': time_range,
        'selected_channels': get_selected_channels(),
        'graph_editor_selection': ge_selection,
        'ge_selection': ge_selection,
        'current_time': cmds.currentTime(query=True)
    }


def _as_unique_sorted_frames(frames):
    """Return sorted unique frame numbers while keeping Maya's float times."""
    unique = []
    for frame in frames or []:
        try:
            numeric_frame = float(frame)
        except Exception:
            continue
        if not any(abs(numeric_frame - existing) < 0.0001 for existing in unique):
            unique.append(numeric_frame)
    return sorted(unique)


def _find_matching_frame(frame, frames):
    """Find the exact keyed frame that matches a queried time."""
    try:
        numeric_frame = float(frame)
    except Exception:
        return None
    for keyed_frame in frames or []:
        try:
            if abs(float(keyed_frame) - numeric_frame) < 0.0001:
                return keyed_frame
        except Exception:
            continue
    return None


def attr_matches_attribute(candidate, attr_full, attr=None):
    """
    Check whether a Graph Editor selection entry belongs to an attribute.

    Maya can return either a plug name (node.attr) or an anim curve name
    (node_attr). This keeps matching strict enough to avoid touching the
    wrong channel while still supporting both forms.
    """
    if not candidate or not attr_full:
        return False

    if candidate == attr_full:
        return True

    if candidate and '.' not in candidate:
        for driven_plug in _anim_curve_driven_plugs(candidate):
            if attr_matches_attribute(driven_plug, attr_full, attr):
                return True

    if '.' not in attr_full:
        return candidate == attr_full

    obj_name, attr_name = attr_full.rsplit('.', 1)
    attr_to_check = attr or attr_name
    attr_names = set(normalize_attr_name(attr_name) + normalize_attr_name(attr_to_check))
    attr_names.add(attr_name)
    attr_names.add(attr_to_check)

    def _node_matches(node_name):
        if not node_name:
            return False
        if node_name == obj_name:
            return True
        return node_name.split('|')[-1] == obj_name.split('|')[-1]

    if '.' in candidate:
        cand_obj, cand_attr = candidate.rsplit('.', 1)
        return cand_attr in attr_names and _node_matches(cand_obj)

    for attr_variant in attr_names:
        suffix = '_' + attr_variant
        if candidate.endswith(suffix):
            cand_obj = candidate[:-len(suffix)]
            return _node_matches(cand_obj) or obj_name.split('|')[-1] in cand_obj

    return False


def get_selected_frames_for_attribute(attr_full, attr=None, ge_selection=None):
    """Return Graph Editor selected frames for one attribute."""
    if ge_selection is None:
        ge_selection = get_graph_editor_selection()

    selected_frames = []
    for ge_attr, frames in (ge_selection or {}).items():
        if attr_matches_attribute(ge_attr, attr_full, attr):
            selected_frames.extend(frames or [])

    return _as_unique_sorted_frames(selected_frames)


def _curve_entry_matches_attribute(entry, attr_full, attr=None):
    for driven_plug in entry.get('driven_plugs') or []:
        if attr_matches_attribute(driven_plug, attr_full, attr):
            return True
    return attr_matches_attribute(entry.get('curve'), attr_full, attr)


def get_selected_curve_for_attribute_frame(attr_full, frame, attr=None):
    """Return the selected animCurve that owns attr_full at frame, if any."""
    for entry in _GRAPH_EDITOR_CURVE_ENTRIES:
        if not _curve_entry_matches_attribute(entry, attr_full, attr):
            continue
        for selected_frame in entry.get('frames') or []:
            if frame_matches(selected_frame, frame):
                return entry.get('curve')
    return None


def get_selected_curve_for_attribute(attr_full, attr=None):
    """Return the selected animCurve that drives attr_full, if any."""
    for entry in _GRAPH_EDITOR_CURVE_ENTRIES:
        if _curve_entry_matches_attribute(entry, attr_full, attr):
            return entry.get('curve')
    return None


def get_keyframes_for_attribute(attr_full, attr=None, context=None):
    """Return attr keyframes plus selected animLayer curve keys for this attr."""
    keyframes = []
    if context is None:
        ge_selection = get_graph_editor_selection()
    else:
        ge_selection = context.get('ge_selection') or context.get('graph_editor_selection') or {}

    selected_curve = get_selected_curve_for_attribute(attr_full, attr)
    try:
        if selected_curve:
            keyframes.extend(cmds.keyframe(selected_curve, query=True) or [])
        else:
            keyframes.extend(cmds.keyframe(attr_full, query=True) or [])
    except Exception:
        pass

    keyframes.extend(get_selected_frames_for_attribute(attr_full, attr, ge_selection))
    return _as_unique_sorted_frames(keyframes)


def get_selected_frames_for_object(obj, ge_selection=None):
    """Return selected Graph Editor frames that belong to an object."""
    if ge_selection is None:
        ge_selection = get_graph_editor_selection()

    obj_short = (obj or '').split('|')[-1]
    selected_frames = []
    for ge_attr, frames in (ge_selection or {}).items():
        ge_node = ge_attr.rsplit('.', 1)[0] if '.' in ge_attr else ge_attr
        if ge_node == obj or ge_node.split('|')[-1] == obj_short or obj_short in ge_attr:
            selected_frames.extend(frames or [])

    return _as_unique_sorted_frames(selected_frames)


def get_frames_to_process(attr_full, all_keyframes=None, attr=None, context=None,
                          current_time=None, allow_current_frame=True,
                          use_neighbor_keys_when_unkeyed=False):
    """
    Resolve the frames a slider is allowed to modify.

    Shared slider rules:
    1. Selected Graph Editor keys take priority.
    2. Otherwise, a selected time-slider range touches only keys in that range.
    3. Otherwise, the current frame is touched, even if it is not keyed yet.
       Curve-shaping sliders can opt into using surrounding real keys instead.
    """
    keyframes = _as_unique_sorted_frames(all_keyframes)
    if context is None:
        context = get_processing_context()

    if current_time is None:
        current_time = context.get('current_time')

    time_range = context.get('time_range')
    ge_selection = context.get('ge_selection') or context.get('graph_editor_selection') or {}
    raw_selected_frames = get_selected_frames_for_attribute(attr_full, attr, ge_selection)
    selected_frames = [
        frame for frame in raw_selected_frames
        if _find_matching_frame(frame, keyframes) is not None
    ]

    if selected_frames:
        return selected_frames

    if raw_selected_frames:
        return raw_selected_frames

    if ge_selection:
        return []

    if time_range:
        start, end = time_range
        range_frames = [frame for frame in keyframes if start <= frame <= end]
        return range_frames

    if allow_current_frame and current_time is not None:
        current_key = _find_matching_frame(current_time, keyframes)
        if current_key is not None:
            return [current_key]
        if use_neighbor_keys_when_unkeyed:
            neighbor_frames = get_neighbor_keyframes_for_unkeyed_time(keyframes, current_time)
            if neighbor_frames:
                return neighbor_frames
        return [float(current_time)]

    return []


def get_object_frames_to_process(obj, all_keyframes=None, context=None,
                                 current_time=None, allow_current_frame=True,
                                 use_neighbor_keys_when_unkeyed=False):
    """Resolve modifiable frames for object-level/world-space sliders."""
    keyframes = _as_unique_sorted_frames(all_keyframes)
    if context is None:
        context = get_processing_context()

    if current_time is None:
        current_time = context.get('current_time')

    time_range = context.get('time_range')
    ge_selection = context.get('ge_selection') or context.get('graph_editor_selection') or {}
    raw_selected_frames = get_selected_frames_for_object(obj, ge_selection)
    selected_frames = [
        frame for frame in raw_selected_frames
        if _find_matching_frame(frame, keyframes) is not None
    ]

    if selected_frames:
        return selected_frames

    if raw_selected_frames:
        return raw_selected_frames

    if ge_selection:
        return []

    if time_range:
        start, end = time_range
        range_frames = [frame for frame in keyframes if start <= frame <= end]
        return range_frames

    if allow_current_frame and current_time is not None:
        current_key = _find_matching_frame(current_time, keyframes)
        if current_key is not None:
            return [current_key]
        if use_neighbor_keys_when_unkeyed:
            neighbor_frames = get_neighbor_keyframes_for_unkeyed_time(keyframes, current_time)
            if neighbor_frames:
                return neighbor_frames
        return [float(current_time)]

    return []


def get_previous_next_keyframes(all_keyframes, frame):
    """Return the nearest previous and next keyed frames around a frame."""
    keyframes = _as_unique_sorted_frames(all_keyframes)
    if not keyframes:
        return None, None

    try:
        frame = float(frame)
    except Exception:
        return None, None

    previous = [key for key in keyframes if key < frame]
    next_keys = [key for key in keyframes if key > frame]
    return (max(previous) if previous else None,
            min(next_keys) if next_keys else None)


def get_neighbor_keyframes_for_unkeyed_time(all_keyframes, frame):
    """
    Return real keyed frames around an unkeyed current time.

    This lets curve-shaping sliders behave like the animator selected the
    neighboring keys manually, instead of creating a temporary key at the
    current frame.
    """
    keyframes = _as_unique_sorted_frames(all_keyframes)
    if not keyframes:
        return []

    current_key = _find_matching_frame(frame, keyframes)
    if current_key is not None:
        return [current_key]

    previous, next_key = get_previous_next_keyframes(keyframes, frame)
    frames = []
    if previous is not None:
        frames.append(previous)
    if next_key is not None and next_key not in frames:
        frames.append(next_key)
    return frames


def get_selection_guide_frames(all_keyframes, frames_to_process):
    """
    Return guide frames around a selected/ranged frame group.

    For a single selected key, this gives the previous and next keys so tools
    like Linear and Ease can still produce a meaningful value.
    """
    keyframes = _as_unique_sorted_frames(all_keyframes)
    frames = _as_unique_sorted_frames(frames_to_process)
    if not keyframes or not frames:
        return None, None

    first = min(frames)
    last = max(frames)
    previous = [key for key in keyframes if key < first]
    next_keys = [key for key in keyframes if key > last]

    start_guide = max(previous) if previous else first
    end_guide = min(next_keys) if next_keys else last
    return start_guide, end_guide


def get_selection_outer_keyframes(all_keyframes, frames_to_process):
    """Return the keyed frames just outside a selected/ranged frame group."""
    keyframes = _as_unique_sorted_frames(all_keyframes)
    frames = _as_unique_sorted_frames(frames_to_process)
    if not keyframes or not frames:
        return None, None

    first = min(frames)
    last = max(frames)
    previous = [key for key in keyframes if key < first]
    next_keys = [key for key in keyframes if key > last]
    return (max(previous) if previous else None,
            min(next_keys) if next_keys else None)


def get_value_at_time(attr_full, time):
    """
    Get the value of an attribute at a specific time.
    
    Args:
        attr_full: Full attribute path (obj.attr)
        time: Time to query
    
    Returns:
        Value at that time or None
    """
    try:
        selected_curve = (
            get_selected_curve_for_attribute_frame(attr_full, time) or
            get_selected_curve_for_attribute(attr_full)
        )
        if selected_curve:
            values = cmds.keyframe(selected_curve, query=True, time=(time, time), valueChange=True)
            if values:
                return values[0]
            values = cmds.keyframe(selected_curve, query=True, time=(time, time), eval=True)
            if values:
                return values[0]
        return cmds.getAttr(attr_full, time=time)
    except:
        return None


def frame_matches(frame_a, frame_b):
    """Compare Maya frame values with a small float tolerance."""
    try:
        return abs(float(frame_a) - float(frame_b)) < 0.0001
    except Exception:
        return False


def clamp(value, minimum=0.0, maximum=1.0):
    """Clamp a numeric value to a range."""
    return max(minimum, min(maximum, value))


def smoothstep(value):
    """Ease a normalized value with a slow start and slow finish."""
    value = clamp(value)
    return value * value * (3.0 - 2.0 * value)


def slider_amount(percentage, signed=False):
    """
    Convert a slider percentage into an eased blend amount.

    signed=False returns 0..1 from either side of the slider.
    signed=True returns -1..1 while preserving the side of the slider.
    """
    try:
        pct = float(percentage)
    except Exception:
        pct = 0.0

    amount = smoothstep(abs(pct) / 100.0)
    if signed and pct < 0:
        return -amount
    return amount


def has_key_at_frame(attr_full, frame):
    """Return True if an attribute has a keyed value at frame."""
    try:
        selected_curve = get_selected_curve_for_attribute_frame(attr_full, frame)
        target = selected_curve or attr_full
        values = cmds.keyframe(target, query=True, time=(frame, frame), valueChange=True)
        return bool(values)
    except Exception:
        return False


def apply_slider_value(attr_full, frame, value, current_time=None):
    """
    Apply a slider value to an existing key, or preview it on the current frame.

    Existing keyed frames are edited directly. An unkeyed current frame is set
    with setAttr during dragging and baked by the slider reset step.
    """
    selected_curve = get_selected_curve_for_attribute_frame(attr_full, frame)
    if selected_curve:
        cmds.keyframe(selected_curve, edit=True, time=(frame, frame), valueChange=value)
        return True

    if has_key_at_frame(attr_full, frame):
        cmds.keyframe(attr_full, edit=True, time=(frame, frame), valueChange=value)
        return True

    if current_time is None:
        current_time = cmds.currentTime(query=True)

    if frame_matches(frame, current_time):
        cmds.setAttr(attr_full, value)
        return True

    cmds.setKeyframe(attr_full, time=frame, value=value)
    return True


def finalize_slider_value(attr_full, frame, value, current_time=None):
    """Bake/finalize a slider value while respecting selected animLayer curves."""
    selected_curve = get_selected_curve_for_attribute_frame(attr_full, frame)
    if selected_curve:
        cmds.keyframe(selected_curve, edit=True, time=(frame, frame), valueChange=value)
        return True

    cmds.setKeyframe(attr_full, time=frame, value=value)
    return True


def get_slider_value(attr_full, frame, current_time=None):
    """Read a value from an existing key or from the current unkeyed frame."""
    values = None
    try:
        selected_curve = (
            get_selected_curve_for_attribute_frame(attr_full, frame) or
            get_selected_curve_for_attribute(attr_full)
        )
        target = selected_curve or attr_full
        values = cmds.keyframe(target, query=True, time=(frame, frame), valueChange=True)
        if not values and selected_curve:
            values = cmds.keyframe(target, query=True, time=(frame, frame), eval=True)
    except Exception:
        pass
    if values:
        return values[0]

    if current_time is None:
        current_time = cmds.currentTime(query=True)

    if frame_matches(frame, current_time):
        return cmds.getAttr(attr_full)

    return cmds.getAttr(attr_full, time=frame)


def set_value_at_time(attr_full, time, value):
    """
    Set a keyframe with a specific value at a specific time.
    
    Args:
        attr_full: Full attribute path (obj.attr)
        time: Time to set keyframe
        value: Value to set
    
    Returns:
        bool: Success
    """
    try:
        cmds.setKeyframe(attr_full, time=time, value=value)
        return True
    except:
        return False
