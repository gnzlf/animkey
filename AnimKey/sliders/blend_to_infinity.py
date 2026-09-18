"""
    AnimKey Slider: Blend to Infinity
    
    Blend current keyframe values towards the infinity curve values.
    
    Features:
    - Calculates the projected value based on infinity settings
    - Works with pre-infinity and post-infinity
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
"""

import maya.cmds as cmds
import math
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_infinity_type,
    get_keyframes_for_attribute,
    get_slider_value,
    get_value_at_time,
    get_processing_context,
    get_selected_curve_for_attribute,
    should_process_attribute,
    slider_attribute_is_editable,
    slider_amount
)


# Global cache for blend data
_blend_infinity_data_cache = {}
_is_dragging = False
_processing_context = {}


def get_infinity_value(attr_full, frame):
    """Calculate the infinity value at the given frame."""
    try:
        attr = attr_full.rsplit('.', 1)[-1]
        keyframes = get_keyframes_for_attribute(attr_full, attr, _processing_context)
        
        if not keyframes:
            return None
        
        target_curve = get_selected_curve_for_attribute(attr_full, attr) or attr_full
        first_key = min(keyframes)
        last_key = max(keyframes)
        
        if first_key <= frame <= last_key:
            dist_to_first = frame - first_key
            dist_to_last = last_key - frame
            
            if dist_to_first < dist_to_last:
                infinity_type = get_infinity_type(attr_full, pre=True, attr=attr)
                edge_key = first_key
                edge_value = get_value_at_time(attr_full, first_key)
                direction = -1
            else:
                infinity_type = get_infinity_type(attr_full, pre=False, attr=attr)
                edge_key = last_key
                edge_value = get_value_at_time(attr_full, last_key)
                direction = 1
        elif frame < first_key:
            infinity_type = get_infinity_type(attr_full, pre=True, attr=attr)
            edge_key = first_key
            edge_value = get_value_at_time(attr_full, first_key)
            direction = -1
        else:
            infinity_type = get_infinity_type(attr_full, pre=False, attr=attr)
            edge_key = last_key
            edge_value = get_value_at_time(attr_full, last_key)
            direction = 1
        
        if infinity_type == 'constant':
            return edge_value
        elif infinity_type == 'linear':
            if direction == 1:
                tangent = cmds.keyTangent(target_curve, query=True, time=(edge_key,), outAngle=True)
            else:
                tangent = cmds.keyTangent(target_curve, query=True, time=(edge_key,), inAngle=True)
            
            if tangent:
                slope = math.tan(math.radians(tangent[0]))
                time_diff = frame - edge_key
                return edge_value + (slope * time_diff)
            return edge_value
        elif infinity_type in ('cycle', 'cycleRelative', 'oscillate'):
            # Evaluate the real animCurve infinity at a virtual time outside
            # the chosen edge.  This handles cycle offsets and oscillation at
            # exact cycle boundaries without duplicating Maya's curve math.
            distance = abs(float(frame) - float(edge_key))
            virtual_time = (
                float(edge_key) - distance if direction == -1
                else float(edge_key) + distance
            )
            return get_value_at_time(attr_full, virtual_time)
        
        return edge_value
        
    except Exception:
        return None


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection."""
    global _blend_infinity_data_cache, _processing_context
    _blend_infinity_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _blend_infinity_data_cache
    
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
            
            for frame in frames_to_process:
                try:
                    current_value = get_value_at_time(attr_full, frame)
                    
                    if isinstance(current_value, (list, tuple)):
                        continue
                    
                    infinity_value = get_infinity_value(attr_full, frame)
                    
                    if infinity_value is None:
                        continue
                    
                    cache_key = f"{attr_full}@{frame}"
                    _blend_infinity_data_cache[cache_key] = {
                        "currentValue": current_value,
                        "infinityValue": infinity_value,
                        "needsCalculation": abs(current_value - infinity_value) > 0.0001,
                        "attr_full": attr_full,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _blend_infinity_data_cache


def execute(percentage):
    """Execute blend to infinity slider functionality."""
    global _blend_infinity_data_cache, _is_dragging, _processing_context
    
    if not _blend_infinity_data_cache:
        _blend_infinity_data_cache = prepare_blend_data()
    
    if not _blend_infinity_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    for cache_key, cache in _blend_infinity_data_cache.items():
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
            infinity_value = cache.get("infinityValue")
            
            if current_value is None or infinity_value is None:
                continue
            
            blend_factor = slider_amount(percentage, signed=True)
            difference = infinity_value - current_value
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
    """Reset blend to infinity slider state."""
    global _blend_infinity_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _blend_infinity_data_cache:
        for cache_key, cache_data in _blend_infinity_data_cache.items():
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
    
    _blend_infinity_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
