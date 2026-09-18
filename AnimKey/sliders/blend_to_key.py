"""
    AnimKey Slider: Blend to Key
    
    Blend between previous and next keyframe value.
    AnimKey blend functionality.
    
    Features:
    - Blend to neighboring keyframes
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
"""

import maya.cmds as cmds
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_keyframes_for_attribute,
    get_slider_value,
    get_selection_outer_keyframes,
    get_processing_context,
    get_value_at_time,
    should_process_attribute,
    slider_attribute_is_editable,
    slider_amount
)


# Global state
_frame_data_cache = {}
_is_dragging = False
_processing_context = {}


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection."""
    global _frame_data_cache, _processing_context
    _frame_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    current_time = _processing_context.get('current_time')
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _frame_data_cache
    
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        
        current_attrs = attrs if attrs else cmds.listAttr(obj, keyable=True, scalar=True) or []
        if not current_attrs:
            continue
        
        for attr in current_attrs:
            if not should_process_attribute(obj, attr, selected_channels):
                continue
            
            attr_full = f"{obj}.{attr}"
            
            if not cmds.objExists(attr_full):
                continue
            
            all_keyframes = get_keyframes_for_attribute(attr_full, attr, _processing_context)
            
            if not all_keyframes:
                continue
            
            frames_to_process = get_frames_to_process(
                attr_full, all_keyframes, attr, _processing_context
            )
            if not frames_to_process:
                continue
            
            previous_frame, next_frame = get_selection_outer_keyframes(all_keyframes, frames_to_process)
            previous_value = get_value_at_time(attr_full, previous_frame) if previous_frame is not None else None
            next_value = get_value_at_time(attr_full, next_frame) if next_frame is not None else None

            if isinstance(previous_value, (list, tuple)):
                previous_value = None
            if isinstance(next_value, (list, tuple)):
                next_value = None
            if previous_value is None and next_value is None:
                continue

            for frame in frames_to_process:
                try:
                    original_value = get_value_at_time(attr_full, frame)
                    
                    if isinstance(original_value, (list, tuple)):
                        continue
                    
                    cache_key = f"{attr_full}@{frame}"
                    _frame_data_cache[cache_key] = {
                        "original_value": original_value,
                        "previousValue": previous_value,
                        "nextValue": next_value,
                        "attr_full": attr_full,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _frame_data_cache


def execute(percentage, objs=None, selection=True):
    """Execute blend to key slider functionality."""
    global _frame_data_cache, _is_dragging, _processing_context
    
    if not objs and not selection:
        return
    
    if not _frame_data_cache:
        _frame_data_cache = prepare_blend_data(objs)
    
    if not _frame_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    amount = slider_amount(percentage)
    
    for cache_key, cache in _frame_data_cache.items():
        attr_full = cache.get("attr_full")
        frame = cache.get("frame")
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if not slider_attribute_is_editable(attr_full):
                continue
            
            attr_type = cmds.getAttr(attr_full, type=True)
            if attr_type in ("enum", "string", "message"):
                continue
            
            orig = cache.get("original_value")
            nxt = cache.get("nextValue")
            prev = cache.get("previousValue")
            
            if any(isinstance(v, (list, tuple)) for v in (orig, nxt, prev) if v is not None):
                continue
            
            if not isinstance(orig, (int, float)):
                continue
            
            if nxt is not None and isinstance(nxt, (int, float)) and percentage > 0:
                difference = nxt - orig
            elif prev is not None and isinstance(prev, (int, float)):
                difference = orig - prev
            else:
                continue
            
            weighted_difference = difference * amount
            new_value = orig + weighted_difference if percentage > 0 else orig - weighted_difference
            
            apply_slider_value(attr_full, frame, float(new_value), current_time)
            
        except Exception:
            continue


def reset():
    """Reset blend to key slider state."""
    global _frame_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _frame_data_cache:
        for cache_key, cache_data in _frame_data_cache.items():
            try:
                attr_full = cache_data.get("attr_full")
                frame = cache_data.get("frame")
                
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
    
    _frame_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
