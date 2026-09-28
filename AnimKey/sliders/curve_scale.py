"""
    AnimKey Curve Slider: Scale
    
    Scales keyframe values from a pivot point (average value).
    
    Features:
    - 0% = no change, -100% = relative inversion, positive values intensify
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


_curve_data_cache = {}
_is_dragging = False
_processing_context = {}


def prepare_curve_data(objs=None, attrs=None):
    global _curve_data_cache, _processing_context
    _curve_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    for target in get_slider_target_records(objs, attrs, _processing_context):
        attr_full = target['attr_full']
        curve = target['curve']
        all_keyframes = target['keyframes']
        frames_to_process = target['frames']
        pivot_frames = list(frames_to_process)
        if len(pivot_frames) == 1:
            prev_frame, next_frame = get_previous_next_keyframes(
                all_keyframes, pivot_frames[0]
            )
            pivot_frames.extend(
                frame for frame in (prev_frame, next_frame) if frame is not None
            )
        values = [
            value for value in (
                get_value_at_time(attr_full, frame, curve=curve)
                for frame in pivot_frames
            ) if not isinstance(value, (list, tuple)) and value is not None
        ]
        if not values:
            continue
        pivot_value = sum(values) / len(values)
        for frame in frames_to_process:
            try:
                original_value = get_value_at_time(attr_full, frame, curve=curve)
                if isinstance(original_value, (list, tuple)):
                    continue
                cache_key = '{}@{}'.format(curve or attr_full, frame)
                _curve_data_cache[cache_key] = {
                    "originalValue": original_value,
                    "pivotValue": pivot_value,
                    "attr_full": attr_full,
                    "curve": curve,
                    "frame": frame,
                }
            except Exception:
                continue
    
    return _curve_data_cache


def execute(percentage):
    global _curve_data_cache, _is_dragging
    
    if not _curve_data_cache:
        _curve_data_cache = prepare_curve_data()
    
    if not _curve_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    # Scale From Average:
    # 0 = original, -100 = relative inversion, positive values intensify.
    scale_factor = 1.0 + (percentage / 50.0)
    
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
            pivot_value = cache.get("pivotValue")
            
            if original_value is None or pivot_value is None:
                continue
            
            offset = original_value - pivot_value
            new_value = pivot_value + (offset * scale_factor)
            apply_slider_value(
                attr_full, frame, new_value,
                _processing_context.get('current_time'), curve=curve,
            )
            
        except Exception:
            continue


def reset():
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
