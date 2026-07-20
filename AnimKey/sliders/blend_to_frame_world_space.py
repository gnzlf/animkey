"""
    AnimKey Slider: Blend to Frame World Space
    
    Blend to a specific frame using world space calculations.
    
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
    get_processing_context,
    should_process_attribute,
    slider_amount
)


# Global state
_frame_ws_data_cache = {}
_is_dragging = False
_left_frame = None
_right_frame = None
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


def set_frames(left_frame=None, right_frame=None):
    """Set left and right frames for blending."""
    global _left_frame, _right_frame
    _left_frame = left_frame
    _right_frame = right_frame


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection using world space."""
    global _frame_ws_data_cache, _left_frame, _right_frame, _processing_context
    _frame_ws_data_cache = {}
    
    _processing_context = get_processing_context()
    current_time = _processing_context.get('current_time')
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _frame_ws_data_cache
    
    transform_attrs = ['translateX', 'translateY', 'translateZ',
                       'rotateX', 'rotateY', 'rotateZ',
                       'scaleX', 'scaleY', 'scaleZ']
    
    # Auto-detect frames if not set
    if _left_frame is None or _right_frame is None:
        all_keyframes_global = set()
        for obj in objects:
            for attr in transform_attrs:
                attr_full = f'{obj}.{attr}'
                if cmds.objExists(attr_full):
                    kf = get_keyframes_for_attribute(attr_full, attr, _processing_context)
                    all_keyframes_global.update(kf)
        
        if all_keyframes_global:
            all_keyframes_global = sorted(all_keyframes_global)
            prev_frames = [f for f in all_keyframes_global if f < current_time]
            next_frames = [f for f in all_keyframes_global if f > current_time]
            
            if prev_frames and _left_frame is None:
                _left_frame = max(prev_frames)
            if next_frames and _right_frame is None:
                _right_frame = min(next_frames)
    
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        
        if not cmds.objectType(obj, isAType='transform'):
            continue
        
        # Get all keyframes for this object
        all_keyframes = set()
        for attr in transform_attrs:
            attr_full = f'{obj}.{attr}'
            if cmds.objExists(attr_full):
                kf = get_keyframes_for_attribute(attr_full, attr, _processing_context)
                all_keyframes.update(kf)
        
        all_keyframes = sorted(all_keyframes)
        
        frames_to_process = get_object_frames_to_process(
            obj, all_keyframes, _processing_context
        )
        if not frames_to_process:
            continue
        
        # Get world space transforms at left and right frames
        left_transform = None
        right_transform = None
        
        if _left_frame is not None:
            left_matrix = get_world_matrix(obj, _left_frame)
            left_transform = matrix_to_transform(left_matrix)
        
        if _right_frame is not None:
            right_matrix = get_world_matrix(obj, _right_frame)
            right_transform = matrix_to_transform(right_matrix)
        
        # Process each frame
        for frame in frames_to_process:
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
                    left_value = left_transform.get(attr, 0) if left_transform else None
                    right_value = right_transform.get(attr, 0) if right_transform else None
                    
                    cache_key = f"{attr_full}@{frame}"
                    _frame_ws_data_cache[cache_key] = {
                        "original_value": current_value,
                        "leftValue": left_value,
                        "rightValue": right_value,
                        "needsCalculation": True,
                        "isWorldSpace": True,
                        "attr_full": attr_full,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _frame_ws_data_cache


def execute(percentage, objs=None, selection=True):
    """Execute blend to frame world space slider functionality."""
    global _frame_ws_data_cache, _is_dragging, _processing_context
    
    if not objs and not selection:
        return
    
    if not _frame_ws_data_cache:
        _frame_ws_data_cache = prepare_blend_data(objs)
    
    if not _frame_ws_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    amount = slider_amount(percentage)
    
    for cache_key, cache in _frame_ws_data_cache.items():
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
            
            original_value = cache.get("original_value")
            left_value = cache.get("leftValue")
            right_value = cache.get("rightValue")
            
            if original_value is None:
                continue
            
            if right_value is not None and percentage > 0:
                difference = right_value - original_value
            elif left_value is not None:
                difference = original_value - left_value
            else:
                continue
            
            weighted_difference = difference * amount
            blended_value = original_value + weighted_difference if percentage > 0 else original_value - weighted_difference
            
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
    """Reset blend to frame world space slider state."""
    global _frame_ws_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _frame_ws_data_cache:
        for cache_key, cache_data in _frame_ws_data_cache.items():
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
    
    _frame_ws_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
