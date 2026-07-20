"""
    AnimKey Slider: Blend to Frame
    
    Blend to a specific frame. Uses left and right frame buttons.
    AnimKey blend functionality.
    
    Features:
    - Blend to specific frames
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
    get_previous_next_keyframes,
    get_processing_context,
    get_value_at_time,
    should_process_attribute,
    slider_amount
)


# Global state
_frame_data_cache = {}
_is_dragging = False
_left_frame = None
_right_frame = None
_processing_context = {}


def set_frames(left_frame=None, right_frame=None):
    """Set left and right frames for blending."""
    global _left_frame, _right_frame
    _left_frame = left_frame
    _right_frame = right_frame


def prepare_blend_data(objs=None, attrs=None):
    """Prepare blend data cache for current selection."""
    global _frame_data_cache, _left_frame, _right_frame, _processing_context
    _frame_data_cache = {}
    
    _processing_context = get_processing_context()
    current_time = _processing_context.get('current_time')
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _frame_data_cache
    
    # Auto-detect left and right frames if not set
    if _left_frame is None or _right_frame is None:
        all_keyframes_global = set()
        for obj in objects:
            current_attrs = attrs if attrs else cmds.listAttr(obj, keyable=True, scalar=True) or []
            for attr in current_attrs:
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
        
        current_attrs = attrs if attrs else cmds.listAttr(obj, keyable=True, scalar=True) or []
        if not current_attrs:
            continue
        
        for attr in current_attrs:
            if not should_process_attribute(obj, attr, selected_channels):
                continue
            
            attr_full = f'{obj}.{attr}'
            
            if not cmds.objExists(attr_full):
                continue
            
            all_keyframes = get_keyframes_for_attribute(attr_full, attr, _processing_context)
            
            frames_to_process = get_frames_to_process(
                attr_full, all_keyframes, attr, _processing_context
            )
            if not frames_to_process:
                continue
            
            # Get values at left and right frames
            left_value = get_value_at_time(attr_full, _left_frame) if _left_frame is not None else None
            right_value = get_value_at_time(attr_full, _right_frame) if _right_frame is not None else None
            
            for frame in frames_to_process:
                try:
                    if frame in all_keyframes:
                        original_value = get_value_at_time(attr_full, frame)
                    else:
                        original_value = cmds.getAttr(attr_full)
                    
                    if isinstance(original_value, (list, tuple)):
                        continue
                    
                    # Get tangent type
                    prev_tan_type = None
                    prev_frame, _ = get_previous_next_keyframes(all_keyframes, frame)
                    if prev_frame is not None:
                        try:
                            prev_tan_type = cmds.keyTangent(attr_full, query=True, time=(prev_frame,), outTangentType=True)
                            if prev_tan_type:
                                prev_tan_type = prev_tan_type[0]
                        except:
                            pass
                    
                    cache_key = f"{attr_full}@{frame}"
                    _frame_data_cache[cache_key] = {
                        "leftValue": left_value,
                        "rightValue": right_value,
                        "original_value": original_value,
                        "needsCalculation": True,
                        "prevTanType": prev_tan_type,
                        "attr_full": attr_full,
                        "frame": frame
                    }
                except Exception:
                    continue
    
    return _frame_data_cache


def execute(percentage, objs=None, selection=True):
    """Execute blend to frame slider functionality."""
    global _frame_data_cache, _is_dragging, _processing_context
    
    if not objs and not selection:
        return
    
    if not _frame_data_cache:
        _frame_data_cache = prepare_blend_data(objs)
    
    if not _frame_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    amount = slider_amount(percentage)
    
    for cache_key, cache in _frame_data_cache.items():
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
            new_value = original_value + weighted_difference if percentage > 0 else original_value - weighted_difference
            
            apply_slider_value(attr_full, frame, new_value, current_time)
            
        except Exception:
            continue


def reset():
    """Reset blend to frame slider state."""
    global _frame_data_cache, _is_dragging, _processing_context
    
    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    
    if _frame_data_cache:
        for cache_key, cache_data in _frame_data_cache.items():
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
                
                # Apply tangent type
                if 'prevTanType' in cache_data and cache_data['prevTanType']:
                    prev_tan_type = cache_data['prevTanType']
                    try:
                        if prev_tan_type == 'step':
                            cmds.keyTangent(attr_full, edit=True, time=(frame,), 
                                          inTangentType='auto', outTangentType='step')
                        elif prev_tan_type == 'stepnext':
                            cmds.keyTangent(attr_full, edit=True, time=(frame,), 
                                          inTangentType='stepnext', outTangentType='auto')
                        else:
                            cmds.keyTangent(attr_full, edit=True, time=(frame,), 
                                          inTangentType=prev_tan_type, outTangentType=prev_tan_type)
                    except:
                        pass
                
            except Exception:
                continue
    
    _frame_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
