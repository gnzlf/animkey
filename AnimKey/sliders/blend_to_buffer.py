"""
    AnimKey Slider: Blend to Buffer
    
    Blend between current animation curves and buffer curves.
    
    Buffer curves are snapshots of animation curves that Maya stores
    when you use the Graph Editor's "Buffer Curve Snapshot" feature.
    
    Features:
    - Blends between current keyframe values and buffer curve values
    - Works on selected objects and channels
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
    - Requires buffer curves to exist (use Graph Editor > View > Buffer Curve Snapshot)
"""

import maya.cmds as cmds
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_keyframes_for_attribute,
    get_selected_curve_for_attribute,
    get_slider_value,
    get_processing_context,
    get_value_at_time,
    should_process_attribute,
    slider_amount
)


# Global cache for blend data
_blend_buffer_data_cache = {}
_is_dragging = False
_processing_context = {}


def prepare_blend_data(objs=None, attrs=None):
    """
    Prepare blend data cache for current selection.
    Captures current values and buffer curve values.
    """
    global _blend_buffer_data_cache, _processing_context
    _blend_buffer_data_cache = {}
    
    _processing_context = get_processing_context()
    current_time = _processing_context.get('current_time')
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _blend_buffer_data_cache
    
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
            
            # Check if attribute has animation
            selected_curve = get_selected_curve_for_attribute(attr_full, attr)
            anim_curves = cmds.listConnections(attr_full, type='animCurve') or []
            if not anim_curves and not selected_curve:
                continue
            
            anim_curve = selected_curve or anim_curves[0]
            all_keyframes = get_keyframes_for_attribute(attr_full, attr, _processing_context)
            
            if not all_keyframes:
                continue
            
            # Check if buffer curve exists
            try:
                has_buffer = cmds.bufferCurve(anim_curve, query=True, exists=True)
                if not has_buffer:
                    continue
            except:
                continue
            
            frames_to_process = get_frames_to_process(
                attr_full, all_keyframes, attr, _processing_context
            )
            if not frames_to_process:
                continue
            
            # Get buffer values by temporarily swapping
            try:
                # Store original values for all frames
                original_values = {}
                for frame in frames_to_process:
                    original_values[frame] = get_value_at_time(attr_full, frame)
                
                # Swap to buffer curve
                cmds.bufferCurve(anim_curve, swap=True)
                
                # Get buffer values for all frames
                buffer_values = {}
                for frame in frames_to_process:
                    buffer_values[frame] = get_value_at_time(attr_full, frame)
                
                # Swap back to original
                cmds.bufferCurve(anim_curve, swap=True)
                
                # Store data for each frame
                for frame in frames_to_process:
                    orig_val = original_values.get(frame)
                    buf_val = buffer_values.get(frame)
                    
                    if isinstance(orig_val, (list, tuple)) or isinstance(buf_val, (list, tuple)):
                        continue
                    
                    cache_key = f"{attr_full}@{frame}"
                    _blend_buffer_data_cache[cache_key] = {
                        "currentValue": orig_val,
                        "bufferValue": buf_val,
                        "animCurve": anim_curve,
                        "needsCalculation": orig_val != buf_val,
                        "attr_full": attr_full,
                        "frame": frame
                    }
            except Exception:
                continue
    
    return _blend_buffer_data_cache


def execute(percentage):
    """
    Execute blend to buffer slider functionality.
    
    Args:
        percentage (float): Percentage value from slider (-100 to 100)
    """
    global _blend_buffer_data_cache, _is_dragging, _processing_context
    
    if not _blend_buffer_data_cache:
        _blend_buffer_data_cache = prepare_blend_data()
    
    if not _blend_buffer_data_cache:
        if not _is_dragging:
            cmds.warning("AnimKey: No buffer curves found. Use Graph Editor > View > Buffer Curve Snapshot first.")
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    for cache_key, cache in _blend_buffer_data_cache.items():
        if not cache.get("needsCalculation", False):
            continue
        
        attr_full = cache.get("attr_full")
        frame = cache.get("frame")
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if cmds.getAttr(attr_full, lock=True) or not cmds.getAttr(attr_full, settable=True):
                continue
            
            current_value = cache.get("currentValue")
            buffer_value = cache.get("bufferValue")
            
            if current_value is None or buffer_value is None:
                continue
            
            # Calculate blended value
            blend_factor = slider_amount(percentage, signed=True)
            difference = buffer_value - current_value
            blended_value = current_value + (difference * blend_factor)
            
            apply_slider_value(attr_full, frame, blended_value, current_time)
            
        except Exception:
            continue


def reset():
    """Reset blend to buffer slider state."""
    global _blend_buffer_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _blend_buffer_data_cache:
        for cache_key, cache_data in _blend_buffer_data_cache.items():
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
    
    _blend_buffer_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
