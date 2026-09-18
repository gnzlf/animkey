"""
    AnimKey Slider: Push/Pull
    
    Push values away from a linear interpolation or pull them towards it.
    AnimKey blend functionality.
    
    Features:
    - Left pulls selected keys into a perfect linear interpolation
    - Right pushes selected keys away from that linear interpolation
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
    get_selection_guide_frames,
    get_processing_context,
    get_value_at_time,
    should_process_attribute,
    slider_attribute_is_editable,
    slider_amount
)


# Global state
_push_pull_data_cache = {}
_is_dragging = False
_processing_context = {}


def prepare_push_pull_data(objs=None, attrs=None):
    """Prepare push/pull data cache for current selection."""
    global _push_pull_data_cache, _processing_context
    _push_pull_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _push_pull_data_cache
    
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        
        current_attrs = attrs if attrs else (selected_channels if selected_channels else cmds.listAttr(obj, keyable=True, scalar=True) or [])
        if not current_attrs:
            continue
        
        for attr in current_attrs:
            if not should_process_attribute(obj, attr, selected_channels):
                continue
            
            attr_full = f"{obj}.{attr}"
            
            if not cmds.objExists(attr_full):
                continue
            
            all_keyframes = get_keyframes_for_attribute(attr_full, attr, _processing_context)
            
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
            
            for frame in frames_to_process:
                try:
                    original_value = get_value_at_time(attr_full, frame)
                    
                    if isinstance(original_value, (list, tuple)):
                        continue

                    t = (frame - guide_start) / float(guide_end - guide_start)
                    linear_value = start_value + (end_value - start_value) * t
                    
                    cache_key = f"{attr_full}@{frame}"
                    _push_pull_data_cache[cache_key] = {
                        "originalValue": original_value,
                        "linearValue": linear_value,
                        "attr_full": attr_full,
                        "attr": attr,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _push_pull_data_cache


def execute(value, objs=None, selection=True):
    """Execute push/pull slider functionality."""
    global _push_pull_data_cache, _is_dragging, _processing_context
    
    if not _push_pull_data_cache:
        _push_pull_data_cache = prepare_push_pull_data(objs)
    
    if not _push_pull_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    amount = slider_amount(value)
    
    for cache_key, cache in _push_pull_data_cache.items():
        attr_full = cache.get("attr_full")
        attr = cache.get("attr")
        frame = cache.get("frame")
        original_value = cache.get("originalValue")
        
        if not attr_full or original_value is None:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if not slider_attribute_is_editable(attr_full):
                continue
            
            linear_value = cache.get("linearValue")
            if linear_value is None:
                continue

            if value < 0:
                # Pull: move selected keys toward the straight interpolation line.
                new_value = original_value + (linear_value - original_value) * amount
            elif value > 0:
                # Push: exaggerate away from that interpolation line.
                new_value = original_value + (original_value - linear_value) * amount
            else:
                new_value = original_value
            
            # Check min/max limits
            obj = attr_full.split('.')[0]
            if cmds.attributeQuery(attr, node=obj, minExists=True):
                min_limit = cmds.attributeQuery(attr, node=obj, minimum=True)[0]
                new_value = max(new_value, min_limit)
            
            if cmds.attributeQuery(attr, node=obj, maxExists=True):
                max_limit = cmds.attributeQuery(attr, node=obj, maximum=True)[0]
                new_value = min(new_value, max_limit)
            
            apply_slider_value(attr_full, frame, new_value, current_time)
            
        except Exception:
            continue


def reset():
    """Reset push/pull slider state."""
    global _push_pull_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _push_pull_data_cache:
        for cache_key, cache_data in _push_pull_data_cache.items():
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
                
                finalize_slider_value(attr_full, frame, current_value)
                
            except Exception:
                continue
    
    _push_pull_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
