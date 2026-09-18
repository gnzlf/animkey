"""
    AnimKey Slider: Blend to Ease
    
    Blend current keyframe values toward guide keys with an ease cascade.
    
    Moving the slider right blends selected keys toward the next guide key
    in a staggered order. Moving left blends toward the previous guide key.
    
    Features:
    - Applies staggered ease-in/ease-out
    - Slider strength is capped at 98% so selected keys do not become identical
      to the previous/next guide keys
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
"""

import maya.cmds as cmds
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_curve_target,
    get_keyframes_for_attribute,
    get_slider_value,
    get_selection_guide_frames,
    get_processing_context,
    get_value_at_time,
    should_process_attribute,
    slider_attribute_is_editable,
    slider_amount
)


# Global cache for blend data
_blend_ease_data_cache = {}
_is_dragging = False
_processing_context = {}

STAGGER_DELAY = 0.45
EASE_SLIDER_MAX_PERCENT = 98.0
EASE_MAX_KEY_PROGRESS = EASE_SLIDER_MAX_PERCENT / 100.0


def _clamp(value, minimum=0.0, maximum=1.0):
    return max(minimum, min(maximum, value))


def _smoothstep(value):
    value = _clamp(value)
    return value * value * (3.0 - 2.0 * value)


def _staggered_amount(amount, proximity_to_target):
    delay = (1.0 - _clamp(proximity_to_target)) * STAGGER_DELAY
    if amount <= delay:
        return 0.0
    return min(_smoothstep((amount - delay) / (1.0 - delay)), EASE_MAX_KEY_PROGRESS)


def _slider_amount_capped(percentage):
    try:
        pct = float(percentage)
    except Exception:
        pct = 0.0
    return slider_amount(min(abs(pct), EASE_SLIDER_MAX_PERCENT))


def _bounded_lerp(original_value, target_value, amount):
    value = original_value + (target_value - original_value) * amount
    lower = min(original_value, target_value)
    upper = max(original_value, target_value)
    return max(lower, min(upper, value))


def _capture_tangents(attr_full, frame):
    try:
        target = get_curve_target(attr_full, frame)
        return {
            "itt": (cmds.keyTangent(target, q=True, time=(frame, frame), itt=True) or [None])[0],
            "ott": (cmds.keyTangent(target, q=True, time=(frame, frame), ott=True) or [None])[0],
        }
    except Exception:
        return {}


def _restore_tangents(attr_full, frame, tangent_data):
    if not tangent_data:
        return
    kwargs = {}
    if tangent_data.get("itt"):
        kwargs["itt"] = tangent_data["itt"]
    if tangent_data.get("ott"):
        kwargs["ott"] = tangent_data["ott"]
    if not kwargs:
        return
    try:
        cmds.keyTangent(get_curve_target(attr_full, frame), edit=True, time=(frame, frame), **kwargs)
    except Exception:
        pass


def _set_safe_tangent(attr_full, frame, in_tangent=True, out_tangent=True):
    kwargs = {}
    if in_tangent:
        kwargs["itt"] = "plateau"
    if out_tangent:
        kwargs["ott"] = "plateau"
    if not kwargs:
        return
    try:
        target = get_curve_target(attr_full, frame)
        cmds.keyTangent(target, edit=True, time=(frame, frame), **kwargs)
    except Exception:
        try:
            kwargs = {}
            if in_tangent:
                kwargs["itt"] = "clamped"
            if out_tangent:
                kwargs["ott"] = "clamped"
            cmds.keyTangent(target, edit=True, time=(frame, frame), **kwargs)
        except Exception:
            pass


def _apply_anti_overshoot_tangents(cache, direction):
    attr_full = cache.get("attr_full")
    frame = cache.get("frame")
    if not attr_full or frame is None:
        return

    if direction == "out":
        _set_safe_tangent(attr_full, frame, in_tangent=True, out_tangent=True)
        guide_frame = cache.get("easeOutGuideFrame")
        if guide_frame is not None:
            _set_safe_tangent(attr_full, guide_frame, in_tangent=True, out_tangent=False)
    elif direction == "in":
        _set_safe_tangent(attr_full, frame, in_tangent=True, out_tangent=True)
        guide_frame = cache.get("easeInGuideFrame")
        if guide_frame is not None:
            _set_safe_tangent(attr_full, guide_frame, in_tangent=False, out_tangent=True)


def _restore_cached_tangents(cache):
    attr_full = cache.get("attr_full")
    if not attr_full:
        return
    for frame, tangent_data in cache.get("tangents", {}).items():
        _restore_tangents(attr_full, frame, tangent_data)


def _proximity_for_frame_index(index, count, target_side):
    if count <= 1:
        return 1.0
    t = float(index) / float(count - 1)
    return t if target_side == "right" else 1.0 - t


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection."""
    global _blend_ease_data_cache, _processing_context
    _blend_ease_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _blend_ease_data_cache
    
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        
        current_attrs = attrs if attrs else cmds.listAttr(obj, keyable=True)
        if not current_attrs:
            continue
        
        for attr in current_attrs:
            if not should_process_attribute(obj, attr, selected_channels):
                continue
            
            attr_full = f'{obj}.{attr}'
            all_keyframes = get_keyframes_for_attribute(attr_full, attr, _processing_context)
            
            if len(all_keyframes) < 2:
                continue
            
            frames_to_process = get_frames_to_process(
                attr_full, all_keyframes, attr, _processing_context,
                use_neighbor_keys_when_unkeyed=True
            )
            if not frames_to_process:
                continue

            guide_start, guide_end = get_selection_guide_frames(all_keyframes, frames_to_process)
            if guide_start is None or guide_end is None or guide_start == guide_end:
                continue

            try:
                start_value = get_value_at_time(attr_full, guide_start)
                end_value = get_value_at_time(attr_full, guide_end)
            except Exception:
                continue

            if isinstance(start_value, (list, tuple)) or isinstance(end_value, (list, tuple)):
                continue
            
            sorted_frames = sorted(frames_to_process)
            selected_count = len(sorted_frames)

            for frame_index, frame in enumerate(sorted_frames):
                try:
                    # Get original value
                    original_value = get_value_at_time(attr_full, frame)
                    if isinstance(original_value, (list, tuple)):
                        continue
                    
                    cache_key = f"{attr_full}@{frame}"
                    _blend_ease_data_cache[cache_key] = {
                        "originalValue": original_value,
                        "easeInTarget": start_value,
                        "easeOutTarget": end_value,
                        "easeInGuideFrame": guide_start,
                        "easeOutGuideFrame": guide_end,
                        "easeInProximity": _proximity_for_frame_index(frame_index, selected_count, "left"),
                        "easeOutProximity": _proximity_for_frame_index(frame_index, selected_count, "right"),
                        "attr_full": attr_full,
                        "frame": frame,
                        "hasKeyframe": frame in all_keyframes,
                        "tangents": {
                            frame: _capture_tangents(attr_full, frame),
                            guide_start: _capture_tangents(attr_full, guide_start),
                            guide_end: _capture_tangents(attr_full, guide_end)
                        }
                    }
                except Exception:
                    continue
    
    return _blend_ease_data_cache


def execute(percentage):
    """
    Execute blend to ease slider functionality.
    
    Args:
        percentage: -98 to 98
            0 = original value
            98 = near the next keyframe value without matching it
            -98 = near the previous keyframe value without matching it
    """
    global _blend_ease_data_cache, _is_dragging, _processing_context
    
    if not _blend_ease_data_cache:
        _blend_ease_data_cache = prepare_blend_data()
    
    if not _blend_ease_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True

    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    amount = _slider_amount_capped(percentage)
    
    for cache_key, cache in _blend_ease_data_cache.items():
        attr_full = cache.get("attr_full")
        frame = cache.get("frame")
        has_keyframe = cache.get("hasKeyframe", False)
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if not slider_attribute_is_editable(attr_full):
                continue
            
            original_value = cache.get("originalValue")
            if original_value is None:
                continue
            
            if percentage == 0:
                blended_value = original_value
                tangent_direction = None
            elif percentage > 0:
                target_value = cache.get("easeOutTarget")
                if target_value is None:
                    continue
                key_amount = _staggered_amount(amount, cache.get("easeOutProximity", 1.0))
                blended_value = _bounded_lerp(original_value, target_value, key_amount)
                tangent_direction = "out"
            else:
                target_value = cache.get("easeInTarget")
                if target_value is None:
                    continue
                key_amount = _staggered_amount(amount, cache.get("easeInProximity", 1.0))
                blended_value = _bounded_lerp(original_value, target_value, key_amount)
                tangent_direction = "in"
            
            # Check min/max limits
            obj, attr = attr_full.split('.', 1)
            try:
                if cmds.attributeQuery(attr, node=obj, minExists=True):
                    min_limit = cmds.attributeQuery(attr, node=obj, minimum=True)[0]
                    blended_value = max(blended_value, min_limit)
                
                if cmds.attributeQuery(attr, node=obj, maxExists=True):
                    max_limit = cmds.attributeQuery(attr, node=obj, maximum=True)[0]
                    blended_value = min(blended_value, max_limit)
            except:
                pass
            
            apply_slider_value(attr_full, frame, blended_value, current_time)
            if tangent_direction:
                _apply_anti_overshoot_tangents(cache, tangent_direction)
            else:
                _restore_cached_tangents(cache)
            
        except Exception:
            continue


def reset():
    """Reset blend to ease slider state."""
    global _blend_ease_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _blend_ease_data_cache:
        for cache_key, cache_data in _blend_ease_data_cache.items():
            try:
                attr_full = cache_data.get("attr_full")
                frame = cache_data.get("frame")
                has_keyframe = cache_data.get("hasKeyframe", False)
                
                if not attr_full:
                    continue
                
                current_value = get_slider_value(attr_full, frame, current_time)
                
                if isinstance(current_value, (list, tuple)):
                    if len(current_value) == 1:
                        current_value = current_value[0]
                    else:
                        continue
                
                finalize_slider_value(attr_full, frame, current_value, current_time)
                
            except Exception:
                continue
    
    _blend_ease_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
