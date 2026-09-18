"""
    AnimKey Slider: Blend to Neighbors World Space
    
    Blend current keyframe values towards the average of neighboring keyframes
    using world space calculations.
    
    Features:
    - Calculates average of previous and next keyframe values in world space
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
from AnimKey.sliders.slider_utils import (
    apply_worldspace_slider_values,
    get_keyframes_for_attribute,
    get_object_frames_to_process,
    get_previous_next_keyframes,
    get_processing_context,
    should_process_attribute_at_frame,
    slider_attribute_is_editable,
    slider_amount,
    blend_angle_degrees,
    average_angles_degrees,
)


# Global cache for blend data
_blend_neighbors_ws_data_cache = {}
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


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection using world space."""
    global _blend_neighbors_ws_data_cache, _processing_context
    _blend_neighbors_ws_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _blend_neighbors_ws_data_cache
    
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
        
        transform_cache = {}

        def transform_at(frame_value):
            numeric_frame = float(frame_value)
            if numeric_frame not in transform_cache:
                transform_cache[numeric_frame] = matrix_to_transform(
                    get_world_matrix(obj, numeric_frame)
                )
            return transform_cache[numeric_frame]

        # Process each selected frame. Neighbors are resolved per curve: the
        # next tx key is not necessarily the next ty/rz key on the control.
        for frame in frames_to_process:
            current_transform = transform_at(frame)
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

                try:
                    attr_keyframes = get_keyframes_for_attribute(
                        attr_full, attr, _processing_context
                    )
                    prev_frame, next_frame = get_previous_next_keyframes(
                        attr_keyframes, frame
                    )
                    neighbor_transforms = []
                    if prev_frame is not None:
                        neighbor_transforms.append(transform_at(prev_frame))
                    if next_frame is not None:
                        neighbor_transforms.append(transform_at(next_frame))
                    if not neighbor_transforms:
                        continue

                    current_value = current_transform.get(attr, 0)
                    neighbor_values = [t.get(attr, 0) for t in neighbor_transforms]
                    neighbor_average = (
                        average_angles_degrees(neighbor_values)
                        if attr.startswith('rotate')
                        else sum(neighbor_values) / len(neighbor_values)
                    )
                    
                    cache_key = f"{attr_full}@{frame}"
                    _blend_neighbors_ws_data_cache[cache_key] = {
                        "currentValue": current_value,
                        "neighborValue": neighbor_average,
                        "needsCalculation": abs(current_value - neighbor_average) > 0.0001,
                        "isWorldSpace": True,
                        "attr_full": attr_full,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _blend_neighbors_ws_data_cache


def execute(percentage):
    """Execute blend to neighbors world space slider functionality."""
    global _blend_neighbors_ws_data_cache, _is_dragging, _processing_context
    
    if not _blend_neighbors_ws_data_cache:
        _blend_neighbors_ws_data_cache = prepare_blend_data()
    
    if not _blend_neighbors_ws_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    pending_updates = []
    
    for cache_key, cache in _blend_neighbors_ws_data_cache.items():
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
            neighbor_value = cache.get("neighborValue")
            
            if current_value is None or neighbor_value is None:
                continue
            
            blend_factor = slider_amount(percentage, signed=True)
            if attr_full.rsplit('.', 1)[-1].startswith('rotate'):
                blended_value = blend_angle_degrees(
                    current_value, neighbor_value, blend_factor
                )
            else:
                difference = neighbor_value - current_value
                blended_value = current_value + (difference * blend_factor)
            
            pending_updates.append((attr_full, frame, blended_value))

        except Exception:
            continue

    apply_worldspace_slider_values(pending_updates)


def reset():
    """Reset blend to neighbors world space slider state."""
    global _blend_neighbors_ws_data_cache, _is_dragging, _processing_context
    
    _blend_neighbors_ws_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
