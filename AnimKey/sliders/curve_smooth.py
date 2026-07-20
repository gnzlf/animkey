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
    get_value_at_time,
    should_process_attribute
)


# Global cache for curve data
_curve_data_cache = {}
_is_dragging = False
_processing_context = {}


def prepare_curve_data(objs=None, attrs=None):
    """Prepare curve data cache for current selection."""
    global _curve_data_cache, _processing_context
    _curve_data_cache = {}
    
    _processing_context = get_processing_context()
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _curve_data_cache

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
            
            if not all_keyframes:
                continue
            
            frames_to_process = get_frames_to_process(
                attr_full, all_keyframes, attr, _processing_context,
                use_neighbor_keys_when_unkeyed=True
            )
            if not frames_to_process:
                continue
            
            for frame in frames_to_process:
                try:
                    original_value = get_value_at_time(attr_full, frame)
                    if isinstance(original_value, (list, tuple)):
                        continue
                    
                    prev_frame, next_frame = get_previous_next_keyframes(all_keyframes, frame)
                    
                    prev_value = None
                    next_value = None
                    
                    if prev_frame is not None:
                        prev_value = get_value_at_time(attr_full, prev_frame)
                        if isinstance(prev_value, (list, tuple)):
                            prev_value = None
                    
                    if next_frame is not None:
                        next_value = get_value_at_time(attr_full, next_frame)
                        if isinstance(next_value, (list, tuple)):
                            next_value = None
                    
                    values = [original_value]
                    if prev_value is not None:
                        values.append(prev_value)
                    if next_value is not None:
                        values.append(next_value)
                    
                    smoothed_value = sum(values) / len(values)
                    
                    cache_key = f"{attr_full}@{frame}"
                    _curve_data_cache[cache_key] = {
                        "originalValue": original_value,
                        "smoothedValue": smoothed_value,
                        "attr_full": attr_full,
                        "frame": frame
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
        frame = cache.get("frame")
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if cmds.getAttr(attr_full, lock=True) or not cmds.getAttr(attr_full, settable=True):
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
            apply_slider_value(attr_full, frame, new_value, _processing_context.get('current_time'))
            
        except Exception:
            continue


def reset():
    """Reset smooth slider state."""
    global _curve_data_cache, _is_dragging, _processing_context
    
    if _curve_data_cache:
        for cache_key, cache_data in _curve_data_cache.items():
            try:
                attr_full = cache_data.get("attr_full")
                frame = cache_data.get("frame")
                
                if not attr_full:
                    continue
                
                current_value = get_slider_value(attr_full, frame, _processing_context.get('current_time'))
                finalize_slider_value(attr_full, frame, current_value, _processing_context.get('current_time'))
                
            except Exception:
                continue
    
    _curve_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
