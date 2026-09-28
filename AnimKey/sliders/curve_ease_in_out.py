"""
    AnimKey Curve Slider: Ease In/Out
    
    Applies staggered ease-in/ease-out blends to animation.
    
    Features:
    - 0% = original values
    - Right side = ease out toward the next guide key
    - Left side = ease in toward the previous guide key
    - Slider strength is capped at 98% so selected keys do not become identical
      to the previous/next guide keys
    - Selected keys move in a staggered cascade based on proximity to the target key
    - When Graph Editor keys selected, ONLY those curves are affected
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
    get_slider_candidate_attributes,
    get_slider_target_records,
    get_value_at_time,
    should_process_attribute,
    slider_attribute_is_editable,
)


_curve_data_cache = {}
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


def _slider_amount(percentage):
    # Slow start and slow finish, so the middle of the slider has the most feel.
    return _smoothstep(min(abs(float(percentage)), EASE_SLIDER_MAX_PERCENT) / 100.0)


def _staggered_amount(amount, proximity_to_target):
    """
    Convert the global slider amount into per-key progress.

    proximity_to_target is 1.0 for the selected key nearest the guide key and
    0.0 for the farthest. Near keys start first; every key stays under the
    guide key value at maximum slider strength.
    """
    delay = (1.0 - _clamp(proximity_to_target)) * STAGGER_DELAY
    if amount <= delay:
        return 0.0
    if delay >= 1.0:
        return EASE_MAX_KEY_PROGRESS
    return min(_smoothstep((amount - delay) / (1.0 - delay)), EASE_MAX_KEY_PROGRESS)


def _bounded_lerp(original_value, target_value, amount):
    value = original_value + (target_value - original_value) * amount
    lower = min(original_value, target_value)
    upper = max(original_value, target_value)
    return max(lower, min(upper, value))


def _capture_tangents(attr_full, frame, curve=None):
    try:
        target = curve or get_curve_target(attr_full, frame)
        return {
            "itt": (cmds.keyTangent(target, q=True, time=(frame, frame), itt=True) or [None])[0],
            "ott": (cmds.keyTangent(target, q=True, time=(frame, frame), ott=True) or [None])[0],
        }
    except Exception:
        return {}


def _restore_tangents(attr_full, frame, tangent_data, curve=None):
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
        cmds.keyTangent(curve or get_curve_target(attr_full, frame), edit=True, time=(frame, frame), **kwargs)
    except Exception:
        pass


def _set_safe_tangent(attr_full, frame, in_tangent=True, out_tangent=True, curve=None):
    kwargs = {}
    if in_tangent:
        kwargs["itt"] = "plateau"
    if out_tangent:
        kwargs["ott"] = "plateau"
    if not kwargs:
        return
    try:
        target = curve or get_curve_target(attr_full, frame)
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
    curve = cache.get("curve")
    if not attr_full or frame is None:
        return

    if direction == "out":
        _set_safe_tangent(attr_full, frame, in_tangent=True, out_tangent=True, curve=curve)
        guide_frame = cache.get("easeOutGuideFrame")
        if guide_frame is not None:
            _set_safe_tangent(attr_full, guide_frame, in_tangent=True, out_tangent=False, curve=curve)
    elif direction == "in":
        _set_safe_tangent(attr_full, frame, in_tangent=True, out_tangent=True, curve=curve)
        guide_frame = cache.get("easeInGuideFrame")
        if guide_frame is not None:
            _set_safe_tangent(attr_full, guide_frame, in_tangent=False, out_tangent=True, curve=curve)


def _restore_cached_tangents(cache):
    attr_full = cache.get("attr_full")
    curve = cache.get("curve")
    if not attr_full:
        return
    for frame, tangent_data in cache.get("tangents", {}).items():
        _restore_tangents(attr_full, frame, tangent_data, curve=curve)


def _proximity_for_frame_index(index, count, target_side):
    if count <= 1:
        return 1.0

    t = float(index) / float(count - 1)
    if target_side == "right":
        return t
    return 1.0 - t


def prepare_curve_data(objs=None, attrs=None):
    """Prepare curve data cache for ease in/out."""
    global _curve_data_cache, _processing_context
    _curve_data_cache = {}
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    for target in get_slider_target_records(objs, attrs, _processing_context):
        attr_full = target['attr_full']
        curve = target['curve']
        frames_to_process = target['frames']
        guide_start_frame, guide_end_frame = get_selection_guide_frames(
            target['keyframes'], frames_to_process
        )
        if guide_start_frame is None:
            guide_start_frame = min(frames_to_process)
        if guide_end_frame is None:
            guide_end_frame = max(frames_to_process)
        start_value = get_value_at_time(attr_full, guide_start_frame, curve=curve)
        end_value = get_value_at_time(attr_full, guide_end_frame, curve=curve)
        if isinstance(start_value, (list, tuple)):
            start_value = start_value[0]
        if isinstance(end_value, (list, tuple)):
            end_value = end_value[0]
        sorted_frames = sorted(frames_to_process)
        for frame_index, frame in enumerate(sorted_frames):
            try:
                original_value = get_value_at_time(attr_full, frame, curve=curve)
                if isinstance(original_value, (list, tuple)):
                    continue
                cache_key = '{}@{}'.format(curve or attr_full, frame)
                _curve_data_cache[cache_key] = {
                    "originalValue": original_value,
                    "easeInTarget": start_value,
                    "easeOutTarget": end_value,
                    "easeInGuideFrame": guide_start_frame,
                    "easeOutGuideFrame": guide_end_frame,
                    "easeInProximity": _proximity_for_frame_index(frame_index, len(sorted_frames), "left"),
                    "easeOutProximity": _proximity_for_frame_index(frame_index, len(sorted_frames), "right"),
                    "attr_full": attr_full,
                    "curve": curve,
                    "frame": frame,
                    "tangents": {
                        frame: _capture_tangents(attr_full, frame, curve=curve),
                        guide_start_frame: _capture_tangents(attr_full, guide_start_frame, curve=curve),
                        guide_end_frame: _capture_tangents(attr_full, guide_end_frame, curve=curve),
                    },
                }
            except Exception:
                continue
    
    return _curve_data_cache


def execute(percentage):
    """Execute ease in/out slider functionality."""
    global _curve_data_cache, _is_dragging
    
    if not _curve_data_cache:
        _curve_data_cache = prepare_curve_data()
    
    if not _curve_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    amount = _slider_amount(percentage)
    
    for cache_key, cache in _curve_data_cache.items():
        attr_full = cache.get("attr_full")
        curve = cache.get("curve")
        frame = cache.get("frame")
        
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
            
            if percentage < 0:
                target_value = cache.get("easeInTarget")
                key_amount = _staggered_amount(amount, cache.get("easeInProximity", 1.0))
                tangent_direction = "in"
            elif percentage > 0:
                target_value = cache.get("easeOutTarget")
                key_amount = _staggered_amount(amount, cache.get("easeOutProximity", 1.0))
                tangent_direction = "out"
            else:
                target_value = original_value
                key_amount = 0.0
                tangent_direction = None

            if target_value is None:
                continue

            new_value = _bounded_lerp(original_value, target_value, key_amount)
            apply_slider_value(
                attr_full, frame, new_value,
                _processing_context.get('current_time'), curve=curve,
            )
            if tangent_direction:
                _apply_anti_overshoot_tangents(cache, tangent_direction)
            else:
                _restore_cached_tangents(cache)
            
        except Exception:
            continue


def reset():
    """Reset ease in/out slider state."""
    global _curve_data_cache, _is_dragging, _processing_context
    
    if _curve_data_cache:
        for cache_key, cache_data in _curve_data_cache.items():
            try:
                attr_full = cache_data.get("attr_full")
                curve = cache_data.get("curve")
                frame = cache_data.get("frame")
                
                if not attr_full:
                    continue
                
                current_value = get_slider_value(
                    attr_full, frame, _processing_context.get('current_time'), curve=curve
                )
                finalize_slider_value(
                    attr_full, frame, current_value,
                    _processing_context.get('current_time'), curve=curve,
                )
            except Exception:
                continue
    
    _curve_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
