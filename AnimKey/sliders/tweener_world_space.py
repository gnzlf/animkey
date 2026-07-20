"""
    AnimKey Slider: Tweener World Space
    
    Tween between previous and next keyframe in world space.
    Uses world space transformations for more accurate results
    when objects have parent constraints or complex hierarchies.
    
    Features:
    - Uses world space transformations
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
    should_process_attribute
)


# Global cache for tween data
_tween_ws_data_cache = {}
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


def prepare_tween_data(objs=None, attrs=None):
    """Prepare tween data cache for current selection using world space."""
    global _tween_ws_data_cache, _processing_context
    _tween_ws_data_cache = {}
    
    _processing_context = get_processing_context()
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _tween_ws_data_cache
    
    transform_attrs = ['translateX', 'translateY', 'translateZ',
                       'rotateX', 'rotateY', 'rotateZ',
                       'scaleX', 'scaleY', 'scaleZ']
    
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        
        if not cmds.objectType(obj, isAType='transform'):
            continue
        
        # Get keyframes from any animated attribute to find time range
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
            
            if prev_frame is None or next_frame is None:
                continue
            
            # Get world space transforms at those frames
            prev_matrix = get_world_matrix(obj, prev_frame)
            next_matrix = get_world_matrix(obj, next_frame)
            
            prev_transform = matrix_to_transform(prev_matrix)
            next_transform = matrix_to_transform(next_matrix)
            
            # Store data for each attribute
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
                    prev_value = prev_transform.get(attr, 0)
                    next_value = next_transform.get(attr, 0)
                    
                    cache_key = f"{attr_full}@{frame}"
                    _tween_ws_data_cache[cache_key] = {
                        "previousValue": prev_value,
                        "nextValue": next_value,
                        "needsCalculation": prev_value != next_value,
                        "isWorldSpace": True,
                        "attr_full": attr_full,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _tween_ws_data_cache


def execute(percentage):
    """Execute tweener world space slider functionality."""
    global _tween_ws_data_cache, _is_dragging, _processing_context
    
    if not _tween_ws_data_cache:
        _tween_ws_data_cache = prepare_tween_data()
    
    if not _tween_ws_data_cache:
        return
    
    # Resistance points
    resistance_points = [(100.0, 4.5), (50.0, 4.5), (0.0, 4.5)]
    for resistance_point, resistance_range in resistance_points:
        if resistance_point - resistance_range <= percentage <= resistance_point + resistance_range:
            percentage = resistance_point
            break
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    for cache_key, cache in _tween_ws_data_cache.items():
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
            
            prev_value = cache.get("previousValue")
            next_value = cache.get("nextValue")
            
            if prev_value is None or next_value is None:
                continue
            
            # Calculate interpolated value
            t = percentage / 100.0
            interpolated_value = prev_value + (next_value - prev_value) * t
            
            # Apply value in world space
            obj, attr = attr_full.split('.', 1)
            
            if attr.startswith('translate'):
                axis = attr[-1].lower()
                pos = list(cmds.xform(obj, query=True, translation=True, worldSpace=True))
                axis_idx = {'x': 0, 'y': 1, 'z': 2}[axis]
                pos[axis_idx] = interpolated_value
                cmds.xform(obj, translation=pos, worldSpace=True)
            elif attr.startswith('rotate'):
                axis = attr[-1].lower()
                rot = list(cmds.xform(obj, query=True, rotation=True, worldSpace=True))
                axis_idx = {'x': 0, 'y': 1, 'z': 2}[axis]
                rot[axis_idx] = interpolated_value
                cmds.xform(obj, rotation=rot, worldSpace=True)
            else:
                cmds.setAttr(attr_full, interpolated_value)
            
        except Exception:
            continue


def reset():
    """Reset tweener world space slider state."""
    global _tween_ws_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _tween_ws_data_cache:
        for cache_key, cache_data in _tween_ws_data_cache.items():
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
    
    _tween_ws_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
