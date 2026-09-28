"""
    AnimKey Curve Slider: Linear
    
    Linearizes animation curves between first and last keyframe.
    
    Features:
    - 0% = original, 100% = fully linear
    - When Graph Editor keys selected, ONLY those curves are affected
"""

import maya.cmds as cmds
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
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


def prepare_curve_data(objs=None, attrs=None):
    global _curve_data_cache, _processing_context
    _curve_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    for target in get_slider_target_records(objs, attrs, _processing_context):
        attr_full = target['attr_full']
        curve = target['curve']
        all_keyframes = target['keyframes']
        frames_to_process = target['frames']
        first_frame, last_frame = get_selection_guide_frames(
            all_keyframes, frames_to_process
        )
        if first_frame is None or last_frame is None:
            first_frame, last_frame = min(frames_to_process), max(frames_to_process)
        first_value = last_value = None
        if first_frame != last_frame:
            first_value = get_value_at_time(attr_full, first_frame, curve=curve)
            last_value = get_value_at_time(attr_full, last_frame, curve=curve)
            if isinstance(first_value, (list, tuple)) or isinstance(last_value, (list, tuple)):
                continue
        for frame in frames_to_process:
            try:
                original_value = get_value_at_time(attr_full, frame, curve=curve)
                if isinstance(original_value, (list, tuple)):
                    continue
                if first_value is not None and last_value is not None:
                    t = (frame - first_frame) / float(last_frame - first_frame)
                    linear_value = first_value + (last_value - first_value) * t
                else:
                    linear_value = original_value
                cache_key = '{}@{}'.format(curve or attr_full, frame)
                _curve_data_cache[cache_key] = {
                    "originalValue": original_value,
                    "linearValue": linear_value,
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
    
    blend_factor = max(0.0, min(1.0, percentage / 100.0))
    
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
            linear_value = cache.get("linearValue")
            
            if original_value is None or linear_value is None:
                continue
            
            new_value = original_value + (linear_value - original_value) * blend_factor
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
