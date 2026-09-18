"""
    AnimKey Slider: Blend to Infinity World Space
    
    Blend current keyframe values towards the infinity curve values
    using world space calculations.
    
    Features:
    - Calculates infinity values in world space
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
from AnimKey.sliders.slider_utils import (
    apply_worldspace_slider_values,
    get_infinity_type,
    get_keyframes_for_attribute,
    get_object_frames_to_process,
    get_processing_context,
    should_process_attribute_at_frame,
    slider_attribute_is_editable,
    slider_amount,
    blend_angle_degrees,
)


# Global cache for blend data
_blend_infinity_ws_data_cache = {}
_is_dragging = False
_processing_context = {}


def get_world_matrix(obj, time=None):
    """Get world matrix of object at given time"""
    original_time = cmds.currentTime(query=True)
    
    if time is not None:
        cmds.currentTime(time, edit=True)
    
    matrix_list = cmds.xform(obj, query=True, matrix=True, worldSpace=True)
    
    if time is not None:
        cmds.currentTime(original_time, edit=True)
    
    return om.MMatrix(matrix_list)


def matrix_to_transform(matrix):
    """Extract translation, rotation, scale from matrix"""
    transform_matrix = om.MTransformationMatrix(matrix)
    
    translation = transform_matrix.translation(om.MSpace.kWorld)
    rotation = transform_matrix.rotation(asQuaternion=False)
    scale = transform_matrix.scale(om.MSpace.kWorld)
    
    return {
        'translateX': translation.x,
        'translateY': translation.y,
        'translateZ': translation.z,
        'rotateX': rotation.x * 57.2958,
        'rotateY': rotation.y * 57.2958,
        'rotateZ': rotation.z * 57.2958,
        'scaleX': scale[0],
        'scaleY': scale[1],
        'scaleZ': scale[2]
    }


def get_infinity_time(attr_full, frame):
    """Return the virtual outside time and infinity mode for a channel."""
    try:
        attr = attr_full.rsplit('.', 1)[-1]
        keyframes = get_keyframes_for_attribute(attr_full, attr, _processing_context)
        
        if not keyframes:
            return None, None
        
        first_key = min(keyframes)
        last_key = max(keyframes)
        if last_key == first_key:
            return first_key, 'constant'
        
        if frame < first_key:
            infinity_type = get_infinity_type(attr_full, pre=True, attr=attr)
            edge_key = first_key
            direction = -1
        elif frame > last_key:
            infinity_type = get_infinity_type(attr_full, pre=False, attr=attr)
            edge_key = last_key
            direction = 1
        else:
            if (frame - first_key) < (last_key - frame):
                infinity_type = get_infinity_type(attr_full, pre=True, attr=attr)
                edge_key = first_key
                direction = -1
            else:
                infinity_type = get_infinity_type(attr_full, pre=False, attr=attr)
                edge_key = last_key
                direction = 1
        
        if infinity_type == 'constant':
            return edge_key, infinity_type

        distance = abs(float(frame) - float(edge_key))
        virtual_time = (
            float(edge_key) - distance if direction == -1
            else float(edge_key) + distance
        )
        return virtual_time, infinity_type
        
    except Exception:
        return None, None


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection using world space."""
    global _blend_infinity_ws_data_cache, _processing_context
    _blend_infinity_ws_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _blend_infinity_ws_data_cache
    
    transform_attrs = ['translateX', 'translateY', 'translateZ',
                       'rotateX', 'rotateY', 'rotateZ',
                       'scaleX', 'scaleY', 'scaleZ']
    
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        
        if not cmds.objectType(obj, isAType='transform'):
            continue
        
        # Get keyframes from any animated attribute
        all_keyframes = set()
        for attr in transform_attrs:
            attr_full = f'{obj}.{attr}'
            if cmds.objExists(attr_full):
                kf = get_keyframes_for_attribute(attr_full, attr, _processing_context)
                all_keyframes.update(kf)
        
        if not all_keyframes:
            continue
        
        all_keyframes = sorted(all_keyframes)
        
        frames_to_process = get_object_frames_to_process(
            obj, all_keyframes, _processing_context
        )
        if not frames_to_process:
            continue
        
        # Process each frame
        for frame in frames_to_process:
            # Get current world-space transform once. Infinity is resolved per
            # channel because different channels can have different key ranges
            # and pre/post-infinity modes.
            current_matrix = get_world_matrix(obj, frame)
            current_transform = matrix_to_transform(current_matrix)
            
            current_attrs = attrs if attrs else transform_attrs
            
            for attr in current_attrs:
                if attr not in transform_attrs:
                    continue
                
                if not should_process_attribute_at_frame(
                    obj, attr, frame, _processing_context, selected_channels
                ):
                    continue
                
                attr_full = f'{obj}.{attr}'
                
                if not cmds.objExists(attr_full):
                    continue

                infinity_time, infinity_type = get_infinity_time(attr_full, frame)
                if infinity_time is None:
                    continue
                
                try:
                    current_value = current_transform.get(attr, 0)
                    infinity_transform = matrix_to_transform(
                        get_world_matrix(obj, infinity_time)
                    )
                    infinity_value = infinity_transform.get(attr, 0)

                    if infinity_type == 'linear':
                        # Maya's real pre/post evaluation is on the opposite
                        # side of the edge. Reflect that world delta through
                        # the edge to project the tangent toward this frame.
                        attr_keys = get_keyframes_for_attribute(
                            attr_full, attr, _processing_context
                        )
                        edge_time = (
                            min(attr_keys)
                            if abs(frame - min(attr_keys)) < abs(max(attr_keys) - frame)
                            else max(attr_keys)
                        )
                        edge_value = matrix_to_transform(
                            get_world_matrix(obj, edge_time)
                        ).get(attr, 0)
                        delta = edge_value - infinity_value
                        if attr.startswith('rotate'):
                            delta = (delta + 180.0) % 360.0 - 180.0
                        infinity_value = edge_value + delta
                    
                    cache_key = f"{attr_full}@{frame}"
                    _blend_infinity_ws_data_cache[cache_key] = {
                        "currentValue": current_value,
                        "infinityValue": infinity_value,
                        "needsCalculation": abs(current_value - infinity_value) > 0.0001,
                        "isWorldSpace": True,
                        "attr_full": attr_full,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _blend_infinity_ws_data_cache


def execute(percentage):
    """Execute blend to infinity world space slider functionality."""
    global _blend_infinity_ws_data_cache, _is_dragging, _processing_context
    
    if not _blend_infinity_ws_data_cache:
        _blend_infinity_ws_data_cache = prepare_blend_data()
    
    if not _blend_infinity_ws_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    pending_updates = []
    
    for cache_key, cache in _blend_infinity_ws_data_cache.items():
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
            if attr_full.rsplit('.', 1)[-1].startswith('rotate'):
                blended_value = blend_angle_degrees(
                    current_value, infinity_value, blend_factor
                )
            else:
                difference = infinity_value - current_value
                blended_value = current_value + (difference * blend_factor)
            
            pending_updates.append((attr_full, frame, blended_value))

        except Exception:
            continue

    apply_worldspace_slider_values(pending_updates)


def reset():
    """Reset blend to infinity world space slider state."""
    global _blend_infinity_ws_data_cache, _is_dragging, _processing_context
    
    _blend_infinity_ws_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
