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
    apply_worldspace_slider_values,
    get_keyframes_for_attribute,
    get_object_frames_to_process,
    get_previous_next_keyframes,
    get_processing_context,
    should_process_attribute_at_frame,
    slider_attribute_is_editable,
    blend_angle_degrees,
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
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
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
        
        transform_cache = {}

        def transform_at(frame_value):
            numeric_frame = float(frame_value)
            if numeric_frame not in transform_cache:
                transform_cache[numeric_frame] = matrix_to_transform(
                    get_world_matrix(obj, numeric_frame)
                )
            return transform_cache[numeric_frame]

        # Resolve interpolation bounds independently for every selected curve.
        for frame in frames_to_process:
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
                    if prev_frame is None or next_frame is None:
                        continue
                    prev_transform = transform_at(prev_frame)
                    next_transform = transform_at(next_frame)
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
    
    pending_updates = []
    
    for cache_key, cache in _tween_ws_data_cache.items():
        attr_full = cache.get("attr_full")
        frame = cache.get("frame")
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if not slider_attribute_is_editable(attr_full):
                continue
            
            prev_value = cache.get("previousValue")
            next_value = cache.get("nextValue")
            
            if prev_value is None or next_value is None:
                continue
            
            # Calculate interpolated value
            t = percentage / 100.0
            if attr_full.rsplit('.', 1)[-1].startswith('rotate'):
                interpolated_value = blend_angle_degrees(prev_value, next_value, t)
            else:
                interpolated_value = prev_value + (next_value - prev_value) * t
            
            pending_updates.append((attr_full, frame, interpolated_value))

        except Exception:
            continue

    apply_worldspace_slider_values(pending_updates)


def reset():
    """Reset tweener world space slider state."""
    global _tween_ws_data_cache, _is_dragging, _processing_context
    
    _tween_ws_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
