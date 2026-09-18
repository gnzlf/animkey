"""
    AnimKey Slider Utilities
    
    Shared utilities for all sliders to handle:
    - Timeline range selection
    - Graph Editor keyframe selection
    - Channel Box attribute selection
"""

import maya.cmds as cmds
import maya.mel as mel
import math


_GRAPH_EDITOR_CURVE_ENTRIES = []
_CHANNEL_SELECTION_UNSET = object()
_COMPOSITE_LAYER_PREVIEW_TARGETS = set()
_ACTIVE_LAYER_TARGET_CACHE = {}
_SLIDER_KEY_EXISTS_CACHE = {}
_SLIDER_EDITABLE_ATTR_CACHE = {}


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

        # animLayer.blendNodes[] is bookkeeping, not an animator channel.
        # Treating it as a driven plug adds a fake "blendNodes" channel and
        # can make a multi-curve Graph Editor selection collapse incorrectly.
        try:
            if cmds.nodeType(node) == 'animLayer':
                continue
        except Exception:
            pass

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
        all_attrs = list(dict.fromkeys(all_attrs))
        
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


def get_graph_editor_curve_entries():
    """Return the concrete curves captured by the last Graph Editor query.

    Sliders that can work directly on ``MFnAnimCurve`` should consume these
    records instead of rediscovering every selected curve by walking all
    keyable attributes on the selected controls.
    """
    return [
        {
            'curve': entry.get('curve'),
            'frames': list(entry.get('frames') or []),
            'driven_plugs': list(entry.get('driven_plugs') or []),
        }
        for entry in _GRAPH_EDITOR_CURVE_ENTRIES
    ]


def get_graph_editor_selected_channels(ge_selection=None):
    """Return the channel names represented by selected Graph Editor keys.

    Maya can leave a stale Channel Box highlight behind while keys on several
    other curves are selected.  The Graph Editor selection is more specific,
    so sliders must use its driven channels instead of intersecting both.
    """
    if ge_selection is None:
        ge_selection = get_graph_editor_selection()
    if not ge_selection:
        return None

    channels = []
    for entry in _GRAPH_EDITOR_CURVE_ENTRIES:
        for plug in entry.get('driven_plugs') or []:
            if '.' not in plug:
                continue
            channel = plug.rsplit('.', 1)[-1].split('[', 1)[0]
            if channel and channel not in channels:
                channels.append(channel)

    for candidate in ge_selection:
        if '.' not in candidate:
            continue
        channel = candidate.rsplit('.', 1)[-1].split('[', 1)[0]
        if channel and channel not in channels:
            channels.append(channel)

    return channels


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


def should_process_attribute(obj, attr, selected_channels=_CHANNEL_SELECTION_UNSET):
    """
    Check if an attribute should be processed based on Channel Box selection.
    
    Args:
        obj: Object name
        attr: Attribute name
        selected_channels: Pre-fetched selected channels; omit to query Maya
    
    Returns:
        bool: True if attribute should be processed
    """
    if selected_channels is _CHANNEL_SELECTION_UNSET:
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


def should_process_attribute_at_frame(obj, attr, frame, context,
                                      selected_channels=None):
    """Check both the channel filter and an exact Graph Editor key/frame."""
    if not should_process_attribute(obj, attr, selected_channels):
        return False
    ge_selection = (
        context.get('ge_selection') or
        context.get('graph_editor_selection') or {}
    )
    if not ge_selection:
        return True
    attr_full = '{}.{}'.format(obj, attr)
    selected_frames = get_selected_frames_for_attribute(
        attr_full, attr, ge_selection
    )
    return any(frame_matches(frame, selected) for selected in selected_frames)


def get_processing_context(explicit_attributes=False):
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
    global _COMPOSITE_LAYER_PREVIEW_TARGETS
    global _ACTIVE_LAYER_TARGET_CACHE, _SLIDER_KEY_EXISTS_CACHE
    global _SLIDER_EDITABLE_ATTR_CACHE
    _COMPOSITE_LAYER_PREVIEW_TARGETS = set()
    _ACTIVE_LAYER_TARGET_CACHE = {}
    _SLIDER_KEY_EXISTS_CACHE = {}
    _SLIDER_EDITABLE_ATTR_CACHE = {}
    ge_selection = get_graph_editor_selection()
    time_range = get_selected_time_range()

    # When Animation Offset is active, the selected time range belongs to the
    # offset tool. Sliders should work on the current/GE-selected key and let
    # the offset propagate that change to the orange range.
    if _animation_offset_is_active():
        time_range = None

    selected_channels = get_selected_channels()
    if ge_selection:
        # A selected key is authoritative.  This prevents an old Channel Box
        # highlight from silently excluding other selected curves.
        selected_channels = get_graph_editor_selected_channels(ge_selection) or []

    return {
        'time_range': time_range,
        'selected_channels': selected_channels,
        'graph_editor_selection': ge_selection,
        'ge_selection': ge_selection,
        'current_time': cmds.currentTime(query=True),
        # Animo's default path is curve-first: selecting an object processes
        # its animated curves, not every keyable channel.  Explicit attrs
        # (API caller/Channel Box) may still create a key on an unanimated plug.
        'explicit_attributes': bool(explicit_attributes),
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
            # Never use substring ownership here: ctrl_tx must not match
            # ctrlExtra_tx. Connected curves were already resolved above;
            # this fallback is only safe for an exact Maya curve prefix.
            return _node_matches(cand_obj)

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


def _active_layer_target(attr_full):
    """Return ``(layer, curve)`` for the layer currently edited by Maya.

    Attribute-level keyframe queries can resolve a different curve once an
    animLayer blend network exists.  Resolving the selected/preferred layer
    explicitly keeps every slider on the same layer as Maya's native tools.
    """
    if attr_full in _ACTIVE_LAYER_TARGET_CACHE:
        return _ACTIVE_LAYER_TARGET_CACHE[attr_full]
    try:
        from AnimKey.core import animation_curve_transfer
        layer = animation_curve_transfer.active_animation_layer()
        curve = animation_curve_transfer.resolve_anim_curve(
            attr_full,
            layer_name=layer,
        )
        result = (layer, curve)
    except Exception:
        result = (None, None)
    _ACTIVE_LAYER_TARGET_CACHE[attr_full] = result
    return result


def get_curve_target(attr_full, frame=None, attr=None):
    """Return the concrete animCurve a slider must read/edit."""
    curve = None
    if frame is not None:
        curve = get_selected_curve_for_attribute_frame(attr_full, frame, attr)
    if not curve:
        curve = get_selected_curve_for_attribute(attr_full, attr)
    return curve or attr_full


_INFINITY_TYPE_NAMES = {
    0: 'constant',
    1: 'linear',
    3: 'cycle',
    4: 'cycleRelative',
    5: 'oscillate',
}


def get_infinity_type(attr_full, pre=True, attr=None):
    """Query infinity from the concrete active-layer curve."""
    target = get_curve_target(attr_full, attr=attr)
    infinity_attr = 'preInfinity' if pre else 'postInfinity'
    if target != attr_full:
        try:
            value = int(cmds.getAttr('{}.{}'.format(target, infinity_attr)))
            return _INFINITY_TYPE_NAMES.get(value, 'constant')
        except Exception:
            pass

    try:
        flag = {'preInfinite': True} if pre else {'postInfinite': True}
        result = cmds.setInfinity(attr_full, query=True, **flag)
        if isinstance(result, (list, tuple)):
            return result[0] if result else 'constant'
        return result or 'constant'
    except Exception:
        return 'constant'


def get_selected_curve_for_attribute_frame(attr_full, frame, attr=None):
    """Return the selected animCurve that owns attr_full at frame, if any."""
    for entry in _GRAPH_EDITOR_CURVE_ENTRIES:
        if not _curve_entry_matches_attribute(entry, attr_full, attr):
            continue
        for selected_frame in entry.get('frames') or []:
            if frame_matches(selected_frame, frame):
                return entry.get('curve')

    # Channel Box and Time Slider workflows do not select Graph Editor keys.
    # In that case, use the curve owned by the active Animation Layer, but only
    # when it already has a key on this frame.
    _layer, active_curve = _active_layer_target(attr_full)
    if active_curve:
        try:
            values = cmds.keyframe(
                active_curve,
                query=True,
                time=(frame, frame),
                valueChange=True,
            ) or []
            if values:
                return active_curve
        except Exception:
            pass
    return None


def get_selected_curve_for_attribute(attr_full, attr=None):
    """Return the Graph-selected or active-layer curve for an attribute."""
    for entry in _GRAPH_EDITOR_CURVE_ENTRIES:
        if _curve_entry_matches_attribute(entry, attr_full, attr):
            return entry.get('curve')
    return _active_layer_target(attr_full)[1]


def get_keyframes_for_attribute(attr_full, attr=None, context=None):
    """Return keys from the concrete curve on the active/selected layer."""
    keyframes = []
    if context is None:
        ge_selection = get_graph_editor_selection()
    else:
        ge_selection = context.get('ge_selection') or context.get('graph_editor_selection') or {}

    selected_curve = get_selected_curve_for_attribute(attr_full, attr)
    active_layer, active_curve = _active_layer_target(attr_full)
    try:
        if selected_curve:
            keyframes.extend(cmds.keyframe(selected_curve, query=True) or [])
        elif active_layer and active_layer != 'BaseAnimation' and not active_curve:
            # Never fall through to BaseAnimation while a non-base layer is
            # active but does not own this channel yet.
            pass
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

    def _node_matches(node):
        return bool(node) and (node == obj or node.split('|')[-1] == obj_short)

    # Prefer the resolved curve entries.  These include the real driven plug
    # even when a selected anim-layer curve is behind animBlend nodes.
    for entry in _GRAPH_EDITOR_CURVE_ENTRIES:
        if any(
            _node_matches(plug.rsplit('.', 1)[0])
            for plug in entry.get('driven_plugs') or []
            if '.' in plug
        ):
            selected_frames.extend(entry.get('frames') or [])

    # Keep a strict fallback for callers/tests that provide a selection map
    # directly.  Substring matching (ctrl in ctrlExtra_tx) caused unrelated
    # controls and curves to be modified together.
    if not selected_frames:
        for ge_attr, frames in (ge_selection or {}).items():
            plugs = [ge_attr] if '.' in ge_attr else _anim_curve_driven_plugs(ge_attr)
            if any(
                _node_matches(plug.rsplit('.', 1)[0])
                for plug in plugs
                if '.' in plug
            ):
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

    if (
        not keyframes
        and not context.get('explicit_attributes')
        and not context.get('selected_channels')
    ):
        # Match Animo's curve-first behavior.  Without an explicit attribute,
        # Graph Editor selection, range, or existing curve there is no slider
        # target; keying every transform channel here creates accidental keys.
        return []

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


def slider_attribute_is_editable(attr_full):
    """Cached lock/settable validation for high-frequency slider ticks."""
    if attr_full in _SLIDER_EDITABLE_ATTR_CACHE:
        return _SLIDER_EDITABLE_ATTR_CACHE[attr_full]
    try:
        editable = bool(
            cmds.objExists(attr_full)
            and not cmds.getAttr(attr_full, lock=True)
            and cmds.getAttr(attr_full, settable=True)
        )
    except Exception:
        editable = False
    _SLIDER_EDITABLE_ATTR_CACHE[attr_full] = editable
    return editable


def blend_angle_degrees(current, target, amount):
    """Blend Euler display values across the shortest equivalent arc."""
    delta = (float(target) - float(current) + 180.0) % 360.0 - 180.0
    return float(current) + (delta * float(amount))


def average_angles_degrees(values):
    """Circular mean for world Euler components near the +/-180 boundary."""
    values = [float(value) for value in values or []]
    if not values:
        return 0.0
    sine = sum(math.sin(math.radians(value)) for value in values)
    cosine = sum(math.cos(math.radians(value)) for value in values)
    if abs(sine) < 1e-12 and abs(cosine) < 1e-12:
        return values[0]
    return math.degrees(math.atan2(sine, cosine))


def has_key_at_frame(attr_full, frame):
    """Return True if an attribute has a keyed value at frame."""
    try:
        selected_curve = (
            get_selected_curve_for_attribute_frame(attr_full, frame) or
            get_selected_curve_for_attribute(attr_full)
        )
        if selected_curve:
            target = selected_curve
        else:
            active_layer, active_curve = _active_layer_target(attr_full)
            if active_layer and active_layer != 'BaseAnimation':
                if not active_curve:
                    return False
                target = active_curve
            else:
                target = attr_full
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
    global _COMPOSITE_LAYER_PREVIEW_TARGETS

    active_layer, active_curve = _active_layer_target(attr_full)
    if (
        attr_full in _COMPOSITE_LAYER_PREVIEW_TARGETS
        and active_layer
        and active_layer != 'BaseAnimation'
    ):
        from AnimKey.core import animation_curve_transfer
        applied = animation_curve_transfer.set_key_on_layer(
            attr_full,
            frame,
            value,
            layer_name=active_layer,
        )
        if applied:
            try:
                cmds.dgdirty(allPlugs=True)
            except Exception:
                pass
        return applied

    selected_curve = (
        get_selected_curve_for_attribute_frame(attr_full, frame) or
        get_selected_curve_for_attribute(attr_full)
    )
    if selected_curve:
        key_signature = (selected_curve, round(float(frame), 6))
        key_exists = _SLIDER_KEY_EXISTS_CACHE.get(key_signature)
        if key_exists is None:
            key_exists = bool(cmds.keyframe(
                selected_curve,
                query=True,
                time=(frame, frame),
                valueChange=True,
            ) or [])
            _SLIDER_KEY_EXISTS_CACHE[key_signature] = key_exists
        if key_exists:
            cmds.keyframe(
                selected_curve,
                edit=True,
                time=(frame, frame),
                valueChange=value,
            )
        else:
            # A layer curve stores its own local contribution. Creating the
            # key directly avoids feeding that local value to the composite
            # destination plug through setAttr.
            cmds.setKeyframe(selected_curve, time=(frame,), value=value)
            _SLIDER_KEY_EXISTS_CACHE[key_signature] = True
        try:
            cmds.dgdirty(attr_full)
        except Exception:
            pass
        return True

    # A selected non-base layer is authoritative even when it does not own
    # this channel yet.  Falling through to the destination plug here edits a
    # BaseAnimation key and only creates the layer key later on mouse release.
    active_layer, active_curve = _active_layer_target(attr_full)
    if active_layer and active_layer != 'BaseAnimation':
        from AnimKey.core import animation_curve_transfer
        created_layer_curve = not bool(active_curve)
        applied = animation_curve_transfer.set_key_on_layer(
            attr_full,
            frame,
            value,
            layer_name=active_layer,
        )
        if applied:
            if created_layer_curve:
                _COMPOSITE_LAYER_PREVIEW_TARGETS.add(attr_full)
                try:
                    from AnimKey.core import animation_curve_transfer
                    active_curve = animation_curve_transfer.resolve_anim_curve(
                        attr_full, layer_name=active_layer
                    )
                except Exception:
                    active_curve = None
                _ACTIVE_LAYER_TARGET_CACHE[attr_full] = (
                    active_layer, active_curve
                )
                if active_curve:
                    _SLIDER_KEY_EXISTS_CACHE[
                        (active_curve, round(float(frame), 6))
                    ] = True
            try:
                # Adding a channel to an empty layer builds a new animBlend
                # network.  Maya does not always dirty that new branch until
                # the time changes, so the first preview can look unchanged.
                if created_layer_curve:
                    cmds.dgdirty(allPlugs=True)
                else:
                    cmds.dgdirty(attr_full)
            except Exception:
                pass
        return applied

    if has_key_at_frame(attr_full, frame):
        cmds.keyframe(attr_full, edit=True, time=(frame, frame), valueChange=value)
        try:
            cmds.dgdirty(attr_full)
        except Exception:
            pass
        return True

    if current_time is None:
        current_time = cmds.currentTime(query=True)

    # Create the preview key on the first drag tick, like Animo's direct curve
    # editing.  Later ticks edit the same key and reset no longer needs to
    # guess whether an unchanged cache entry was actually touched.
    cmds.setKeyframe(attr_full, time=frame, value=value)
    try:
        cmds.dgdirty(attr_full)
    except Exception:
        pass
    return True


def finalize_slider_value(attr_full, frame, value, current_time=None):
    """Bake/finalize a slider value while respecting selected animLayer curves."""
    selected_curve = (
        get_selected_curve_for_attribute_frame(attr_full, frame) or
        get_selected_curve_for_attribute(attr_full)
    )
    if selected_curve:
        keyed_values = cmds.keyframe(
            selected_curve,
            query=True,
            time=(frame, frame),
            valueChange=True,
        ) or []
        if keyed_values:
            cmds.keyframe(selected_curve, edit=True, time=(frame, frame), valueChange=value)
        else:
            # Slider previews now create their key immediately.  A missing key
            # here means the cache entry was never applied and must stay clean.
            return False
        return True

    active_layer, _curve = _active_layer_target(attr_full)
    if active_layer and active_layer != 'BaseAnimation':
        if not _curve:
            return False
        from AnimKey.core import animation_curve_transfer
        return animation_curve_transfer.set_key_on_layer(
            attr_full,
            frame,
            value,
            layer_name=active_layer,
        )

    if has_key_at_frame(attr_full, frame):
        cmds.keyframe(
            attr_full,
            edit=True,
            time=(frame, frame),
            valueChange=value,
        )
        return True
    return False


_WORLDSPACE_CHANNEL_GROUPS = {
    'translate': ('translateX', 'translateY', 'translateZ'),
    'rotate': ('rotateX', 'rotateY', 'rotateZ'),
    'scale': ('scaleX', 'scaleY', 'scaleZ'),
}


def _transform_group_for_attr(attr):
    for group_name in _WORLDSPACE_CHANNEL_GROUPS:
        if attr.startswith(group_name) and attr[-1:].upper() in 'XYZ':
            return group_name
    return None


def _scalar_attr_value(attr_full):
    value = cmds.getAttr(attr_full)
    while isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if isinstance(value, (list, tuple)):
        raise TypeError('Expected scalar attribute: {}'.format(attr_full))
    return float(value)


def _local_value_changed(group_name, before, after, tolerance=1e-8):
    if group_name == 'rotate':
        difference = (float(after) - float(before) + 180.0) % 360.0 - 180.0
        return abs(difference) > tolerance
    return abs(float(after) - float(before)) > tolerance


def _apply_worldspace_slider_object(obj, attr_values, frame):
    """Solve one object's world update and key only required local channels."""
    from AnimKey.core import animation_curve_transfer

    requested_attrs = {
        attr: value for attr, value in attr_values.items()
        if _transform_group_for_attr(attr)
    }
    if not requested_attrs or not cmds.objExists(obj):
        return 0

    active_groups = {
        _transform_group_for_attr(attr) for attr in requested_attrs
    }
    original_local_values = {}
    for group_name in active_groups:
        for attr in _WORLDSPACE_CHANNEL_GROUPS[group_name]:
            attr_full = '{}.{}'.format(obj, attr)
            if cmds.objExists(attr_full):
                original_local_values[attr] = _scalar_attr_value(attr_full)

    world_values = {}
    if 'translate' in active_groups:
        world_values['translate'] = list(cmds.xform(
            obj, query=True, translation=True, worldSpace=True
        ))
    if 'rotate' in active_groups:
        world_values['rotate'] = list(cmds.xform(
            obj, query=True, rotation=True, worldSpace=True
        ))
    if 'scale' in active_groups:
        world_values['scale'] = list(cmds.xform(
            obj, query=True, scale=True, worldSpace=True
        ))

    for attr, value in requested_attrs.items():
        group_name = _transform_group_for_attr(attr)
        world_values[group_name]['XYZ'.index(attr[-1].upper())] = float(value)

    # Translation is applied last because rotation/scale around a non-zero
    # pivot can move the object in world space.
    if 'scale' in active_groups:
        cmds.xform(obj, scale=world_values['scale'], worldSpace=True)
    if 'rotate' in active_groups:
        cmds.xform(obj, rotation=world_values['rotate'], worldSpace=True)
    if 'translate' in active_groups:
        cmds.xform(obj, translation=world_values['translate'], worldSpace=True)

    # Always preserve the explicitly selected curves. Add a sibling local axis
    # only when the parent transform made it part of the world-space solution.
    # This avoids creating tx/ty/tz keys indiscriminately for one selected key.
    attrs_to_key = set(requested_attrs)
    solved_local_values = {}
    for group_name in active_groups:
        for attr in _WORLDSPACE_CHANNEL_GROUPS[group_name]:
            attr_full = '{}.{}'.format(obj, attr)
            solved_value = _scalar_attr_value(attr_full)
            solved_local_values[attr] = solved_value
            if _local_value_changed(
                group_name, original_local_values.get(attr, solved_value), solved_value
            ):
                attrs_to_key.add(attr)

    requested_layers = {}
    group_layers = {}
    for requested_attr in requested_attrs:
        requested_full = '{}.{}'.format(obj, requested_attr)
        selected_curve = get_selected_curve_for_attribute_frame(
            requested_full, frame
        )
        if not selected_curve:
            continue
        layer_name = animation_curve_transfer.animation_layer_for_curve(
            requested_full, selected_curve
        )
        requested_layers[requested_attr] = layer_name
        group_layers.setdefault(_transform_group_for_attr(requested_attr), layer_name)

    default_layer = animation_curve_transfer.active_animation_layer()
    applied = 0
    for attr in attrs_to_key:
        attr_full = '{}.{}'.format(obj, attr)
        try:
            if (cmds.getAttr(attr_full, lock=True) or
                    not cmds.getAttr(attr_full, settable=True)):
                continue
        except Exception:
            continue

        layer_name = requested_layers.get(
            attr,
            group_layers.get(_transform_group_for_attr(attr), default_layer),
        )
        if animation_curve_transfer.set_key_on_layer(
                attr_full,
                frame,
                solved_local_values[attr],
                layer_name=layer_name):
            applied += 1
    return applied


def apply_worldspace_slider_values(updates):
    """Apply and key world-component updates, evaluating each frame once."""
    grouped = {}
    frame_order = []
    for attr_full, frame, value in updates or []:
        if not attr_full or '.' not in attr_full:
            continue
        try:
            numeric_frame = float(frame)
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if numeric_frame not in grouped:
            grouped[numeric_frame] = {}
            frame_order.append(numeric_frame)
        obj, attr = attr_full.rsplit('.', 1)
        grouped[numeric_frame].setdefault(obj, {})[attr] = numeric_value
    if not frame_order:
        return 0

    original_time = float(cmds.currentTime(query=True))
    applied = 0
    try:
        for frame in frame_order:
            try:
                cmds.currentTime(frame, edit=True, update=True)
            except Exception:
                continue
            for obj, attr_values in grouped[frame].items():
                try:
                    applied += _apply_worldspace_slider_object(
                        obj, attr_values, frame
                    )
                except Exception:
                    # One locked, constrained, or malformed control must not
                    # prevent the remaining selected controls from updating.
                    continue
    finally:
        try:
            cmds.currentTime(original_time, edit=True, update=True)
        except Exception:
            pass
    return applied


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
        return bool(finalize_slider_value(attr_full, time, value))
    except:
        return False
