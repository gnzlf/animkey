"""
    AnimKey Slider: Blend to Neighbors
    
    Blend current keyframe values towards neighboring keyframe values.
    
    This tool blends the current keyframe towards its neighbors:
    - 100% = fully at next keyframe value
    - -100% = fully at previous keyframe value
    - 0% = original value
    
    Features:
    - Blend towards previous or next keyframe
    - Useful for smoothing out spikes or extreme values
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


# Global cache for blend data
_blend_neighbors_data_cache = {}
_is_dragging = False
_processing_context = {}


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection."""
    global _blend_neighbors_data_cache, _processing_context
    _blend_neighbors_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _blend_neighbors_data_cache
    
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
                attr_full, all_keyframes, attr, _processing_context
            )
            if not frames_to_process:
                continue
            
            prev_frame, next_frame = get_selection_outer_keyframes(all_keyframes, frames_to_process)
            prev_value = get_value_at_time(attr_full, prev_frame) if prev_frame is not None else None
            next_value = get_value_at_time(attr_full, next_frame) if next_frame is not None else None

            if isinstance(prev_value, (list, tuple)):
                prev_value = None
            if isinstance(next_value, (list, tuple)):
                next_value = None
            if prev_value is None and next_value is None:
                continue

            for frame in frames_to_process:
                try:
                    # Get original value
                    original_value = get_value_at_time(attr_full, frame)
                    if isinstance(original_value, (list, tuple)):
                        continue
                    
                    cache_key = f"{attr_full}@{frame}"
                    _blend_neighbors_data_cache[cache_key] = {
                        "originalValue": original_value,
                        "prevValue": prev_value,
                        "nextValue": next_value,
                        "attr_full": attr_full,
                        "frame": frame,
                        "hasKeyframe": frame in all_keyframes
                    }
                except Exception:
                    continue
    
    return _blend_neighbors_data_cache


def execute(percentage):
    """
    Execute blend to neighbors slider functionality.
    
    Args:
        percentage: -100 to 100
            0 = original value
            100 = fully at next keyframe value
            -100 = fully at previous keyframe value
    """
    global _blend_neighbors_data_cache, _is_dragging, _processing_context
    
    if not _blend_neighbors_data_cache:
        _blend_neighbors_data_cache = prepare_blend_data()
    
    if not _blend_neighbors_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True

    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    amount = slider_amount(percentage)
    
    for cache_key, cache in _blend_neighbors_data_cache.items():
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
            prev_value = cache.get("prevValue")
            next_value = cache.get("nextValue")
            
            if original_value is None:
                continue
            
            if percentage == 0:
                blended_value = original_value
            elif percentage > 0:
                if next_value is None:
                    blended_value = original_value
                else:
                    blended_value = original_value + (next_value - original_value) * amount
            else:
                if prev_value is None:
                    blended_value = original_value
                else:
                    blended_value = original_value + (prev_value - original_value) * amount
            
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
            
        except Exception:
            continue


def reset():
    """Reset blend to neighbors slider state."""
    global _blend_neighbors_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _blend_neighbors_data_cache:
        for cache_key, cache_data in _blend_neighbors_data_cache.items():
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
    
    _blend_neighbors_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
