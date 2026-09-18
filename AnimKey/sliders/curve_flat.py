"""
    AnimKey Curve Slider: Flat
    
    Flattens animation curves to the average value.
    
    Features:
    - 0% = original, 100% = all values at average
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
            
            frames_to_process = get_frames_to_process(
                attr_full, all_keyframes, attr, _processing_context
            )
            if not frames_to_process:
                continue
            
            average_frames = list(frames_to_process)
            if len(average_frames) == 1:
                prev_frame, next_frame = get_previous_next_keyframes(all_keyframes, average_frames[0])
                if prev_frame is not None:
                    average_frames.append(prev_frame)
                if next_frame is not None:
                    average_frames.append(next_frame)

            values = []
            for frame in average_frames:
                try:
                    val = get_value_at_time(attr_full, frame)
                    if not isinstance(val, (list, tuple)):
                        values.append(val)
                except:
                    pass
            
            if not values:
                continue
            
            average_value = sum(values) / len(values)
            
            for frame in frames_to_process:
                try:
                    original_value = get_value_at_time(attr_full, frame)
                    if isinstance(original_value, (list, tuple)):
                        continue
                    
                    cache_key = f"{attr_full}@{frame}"
                    _curve_data_cache[cache_key] = {
                        "originalValue": original_value,
                        "averageValue": average_value,
                        "attr_full": attr_full,
                        "frame": frame
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
        frame = cache.get("frame")
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if not slider_attribute_is_editable(attr_full):
                continue
            
            original_value = cache.get("originalValue")
            average_value = cache.get("averageValue")
            
            if original_value is None or average_value is None:
                continue
            
            new_value = original_value + (average_value - original_value) * blend_factor
            apply_slider_value(attr_full, frame, new_value, _processing_context.get('current_time'))
            
        except Exception:
            continue


def reset():
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
