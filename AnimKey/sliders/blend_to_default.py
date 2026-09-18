"""
    AnimKey Slider: Blend to Default
    
    Blend between current values and default rig values.
    
    Features:
    - Blends between current attribute values and their default values
    - Works on selected objects and channels
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
    - Useful for returning to T-pose or default state
"""

import maya.cmds as cmds
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_keyframes_for_attribute,
    get_slider_value,
    get_processing_context,
    get_value_at_time,
    should_process_attribute,
    slider_attribute_is_editable,
    slider_amount
)


# Global cache for blend data
_blend_default_data_cache = {}
_is_dragging = False
_processing_context = {}


def get_default_value(obj, attr):
    """Get the default value of an attribute."""
    try:
        if cmds.attributeQuery(attr, node=obj, exists=True):
            if cmds.attributeQuery(attr, node=obj, listDefault=True):
                default = cmds.attributeQuery(attr, node=obj, listDefault=True)
                if default:
                    return default[0]
        
        transform_defaults = {
            'translateX': 0.0, 'translateY': 0.0, 'translateZ': 0.0,
            'rotateX': 0.0, 'rotateY': 0.0, 'rotateZ': 0.0,
            'scaleX': 1.0, 'scaleY': 1.0, 'scaleZ': 1.0,
            'visibility': 1.0
        }
        
        if attr in transform_defaults:
            return transform_defaults[attr]
        
        return 0.0
        
    except Exception:
        return 0.0


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection."""
    global _blend_default_data_cache, _processing_context
    _blend_default_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    current_time = _processing_context.get('current_time')
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _blend_default_data_cache
    
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
            
            default_value = get_default_value(obj, attr)
            
            for frame in frames_to_process:
                try:
                    current_value = get_value_at_time(attr_full, frame)
                    
                    if isinstance(current_value, (list, tuple)):
                        continue
                    
                    cache_key = f"{attr_full}@{frame}"
                    _blend_default_data_cache[cache_key] = {
                        "currentValue": current_value,
                        "defaultValue": default_value,
                        "needsCalculation": current_value != default_value,
                        "attr_full": attr_full,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _blend_default_data_cache


def execute(percentage):
    """Execute blend to default slider functionality."""
    global _blend_default_data_cache, _is_dragging, _processing_context
    
    if not _blend_default_data_cache:
        _blend_default_data_cache = prepare_blend_data()
    
    if not _blend_default_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    for cache_key, cache in _blend_default_data_cache.items():
        if not cache.get("needsCalculation", False):
            continue
        
        attr_full = cache.get("attr_full")
        frame = cache.get("frame")
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if not slider_attribute_is_editable(attr_full):
                continue
            
            current_value = cache.get("currentValue")
            default_value = cache.get("defaultValue")
            
            if current_value is None or default_value is None:
                continue
            
            blend_factor = slider_amount(percentage, signed=True)
            difference = default_value - current_value
            blended_value = current_value + (difference * blend_factor)
            
            obj, attr = attr_full.split('.', 1)
            if cmds.attributeQuery(attr, node=obj, minExists=True):
                min_limit = cmds.attributeQuery(attr, node=obj, minimum=True)[0]
                blended_value = max(blended_value, min_limit)
            
            if cmds.attributeQuery(attr, node=obj, maxExists=True):
                max_limit = cmds.attributeQuery(attr, node=obj, maximum=True)[0]
                blended_value = min(blended_value, max_limit)
            
            apply_slider_value(attr_full, frame, blended_value, current_time)
            
        except Exception:
            continue


def reset():
    """Reset blend to default slider state."""
    global _blend_default_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _blend_default_data_cache:
        for cache_key, cache_data in _blend_default_data_cache.items():
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
    
    _blend_default_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
