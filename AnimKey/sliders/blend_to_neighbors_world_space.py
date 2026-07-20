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
    finalize_slider_value,
    get_keyframes_for_attribute,
    get_object_frames_to_process,
    get_previous_next_keyframes,
    get_processing_context,
    should_process_attribute,
    slider_amount
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
    
    _processing_context = get_processing_context()
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
        
        # Process each frame
        for frame in frames_to_process:
            # Find previous and next keyframes
            prev_frame, next_frame = get_previous_next_keyframes(all_keyframes, frame)
            
            if prev_frame is None and next_frame is None:
                continue
            
            # Get world space transforms at neighbor frames
            neighbor_transforms = []
            
            if prev_frame is not None:
                prev_matrix = get_world_matrix(obj, prev_frame)
                neighbor_transforms.append(matrix_to_transform(prev_matrix))
            
            if next_frame is not None:
                next_matrix = get_world_matrix(obj, next_frame)
                neighbor_transforms.append(matrix_to_transform(next_matrix))
            
            # Get current world space transform
            current_matrix = get_world_matrix(obj, frame)
            current_transform = matrix_to_transform(current_matrix)
            
            current_attrs = attrs if attrs else transform_attrs
            
            for attr in current_attrs:
                if attr not in transform_attrs:
                    continue
                
                if not should_process_attribute(obj, attr, selected_channels):
                    continue
                
                attr_full = f'{obj}.{attr}'
                
                if not cmds.objExists(attr_full):
                    continue
                
                try:
                    current_value = current_transform.get(attr, 0)
                    
                    neighbor_values = [t.get(attr, 0) for t in neighbor_transforms]
                    if not neighbor_values:
                        continue
                    
                    neighbor_average = sum(neighbor_values) / len(neighbor_values)
                    
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
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    for cache_key, cache in _blend_neighbors_ws_data_cache.items():
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
            neighbor_value = cache.get("neighborValue")
            
            if current_value is None or neighbor_value is None:
                continue
            
            blend_factor = slider_amount(percentage, signed=True)
            difference = neighbor_value - current_value
            blended_value = current_value + (difference * blend_factor)
            
            # Apply value in world space
            obj, attr = attr_full.split('.', 1)
            
            if attr.startswith('translate'):
                axis = attr[-1].lower()
                pos = list(cmds.xform(obj, query=True, translation=True, worldSpace=True))
                axis_idx = {'x': 0, 'y': 1, 'z': 2}[axis]
                pos[axis_idx] = blended_value
                cmds.xform(obj, translation=pos, worldSpace=True)
            elif attr.startswith('rotate'):
                axis = attr[-1].lower()
                rot = list(cmds.xform(obj, query=True, rotation=True, worldSpace=True))
                axis_idx = {'x': 0, 'y': 1, 'z': 2}[axis]
                rot[axis_idx] = blended_value
                cmds.xform(obj, rotation=rot, worldSpace=True)
            else:
                cmds.setAttr(attr_full, blended_value)
            
        except Exception:
            continue


def reset():
    """Reset blend to neighbors world space slider state."""
    global _blend_neighbors_ws_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _blend_neighbors_ws_data_cache:
        for cache_key, cache_data in _blend_neighbors_ws_data_cache.items():
            try:
                attr_full = cache_data.get("attr_full")
                frame = cache_data.get("frame")
                
                if not attr_full:
                    continue
                
                current_value = cmds.getAttr(attr_full)
                
                if isinstance(current_value, (list, tuple)):
                    if len(current_value) == 1:
                        current_value = current_value[0]
                    else:
                        continue
                
                finalize_slider_value(attr_full, frame, current_value, current_time)
                
            except Exception:
                continue
    
    _blend_neighbors_ws_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
