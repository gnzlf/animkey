"""
    AnimKey Curve Slider: Smooth
    
    Smooths or roughens animation curves based on neighboring keyframes.
    
    Features:
    - Left side smooths with previous and next values
    - Right side exaggerates away from that smoothed value
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
    - When Graph Editor keys selected, ONLY those curves are affected
"""

import maya.cmds as cmds
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_keyframes_for_attribute,
    get_slider_value,
    get_previous_next_keyframes,
    get_processing_context,
    get_slider_candidate_attributes,
    get_slider_target_records,
    get_value_at_time,
    should_process_attribute,
    slider_attribute_is_editable,
)


# Global cache for curve data
_curve_data_cache = {}
_is_dragging = False
_processing_context = {}


def prepare_curve_data(objs=None, attrs=None):
    """Prepare curve data cache for current selection."""
    global _curve_data_cache, _processing_context
    _curve_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    for target in get_slider_target_records(objs, attrs, _processing_context):
        attr_full = target['attr_full']
        curve = target['curve']
        all_keyframes = target['keyframes']
        for frame in target['frames']:
            try:
                original_value = get_value_at_time(attr_full, frame, curve=curve)
                if isinstance(original_value, (list, tuple)):
                    continue

                prev_frame, next_frame = get_previous_next_keyframes(all_keyframes, frame)
                values = [original_value]
                for neighbor in (prev_frame, next_frame):
                    if neighbor is None:
                        continue
                    neighbor_value = get_value_at_time(
                        attr_full, neighbor, curve=curve
                    )
                    if not isinstance(neighbor_value, (list, tuple)):
                        values.append(neighbor_value)

                cache_key = '{}@{}'.format(curve or attr_full, frame)
                _curve_data_cache[cache_key] = {
                    "originalValue": original_value,
                    "smoothedValue": sum(values) / len(values),
                    "attr_full": attr_full,
                    "curve": curve,
                    "frame": frame,
                }
            except Exception:
                continue
    
    return _curve_data_cache


def execute(percentage):
    """
    Execute smooth slider functionality.
    
    Args:
        percentage: -100 to 100
            -100 = fully smoothed
            0 = original values
            100 = rough/exaggerated away from the smoothed value
    """
    global _curve_data_cache, _is_dragging
    
    if not _curve_data_cache:
        _curve_data_cache = prepare_curve_data()
    
    if not _curve_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    amount = abs(percentage) / 100.0
    
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
            smoothed_value = cache.get("smoothedValue")
            
            if original_value is None or smoothed_value is None:
                continue
            
            if percentage < 0:
                new_value = original_value + (smoothed_value - original_value) * amount
            elif percentage > 0:
                new_value = original_value + (original_value - smoothed_value) * amount
            else:
                new_value = original_value
            apply_slider_value(
                attr_full, frame, new_value,
                _processing_context.get('current_time'), curve=curve,
            )
            
        except Exception:
            continue


def reset():
    """Reset smooth slider state."""
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
