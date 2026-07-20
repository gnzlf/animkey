"""
    AnimKey Slider: Blend to Undo
    
    Blend between current values and the previous undo state.
    
    This slider captures the current state, performs an undo to get
    the previous values, then allows you to blend between them.
    
    Features:
    - Captures current state and undo state
    - Allows blending back to previous state
    - 0% = current value, 100% = undo value, -100% = exaggerate away from undo
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection
"""

import maya.cmds as cmds
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_keyframes_for_attribute,
    get_processing_context,
    get_slider_value,
    get_value_at_time,
    should_process_attribute,
    slider_amount
)


# Global cache for blend data
_blend_undo_data_cache = {}
_is_dragging = False
_processing_context = {}


def prepare_blend_data(objs=None, attrs=None):
    """
    Prepare blend data cache for current selection.
    
    This function:
    1. Captures current values
    2. Performs undo to get previous values
    3. Redoes to restore current state
    4. Stores both for blending
    """
    global _blend_undo_data_cache, _processing_context
    _blend_undo_data_cache = {}
    
    _processing_context = get_processing_context()
    selected_channels = _processing_context.get('selected_channels')
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        print("[Blend to Undo] No objects selected")
        return _blend_undo_data_cache
    
    # Step 1: Capture current values
    current_values = {}
    
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
                    value = get_value_at_time(attr_full, frame)
                    if isinstance(value, (list, tuple)):
                        continue
                    
                    cache_key = f"{attr_full}@{frame}"
                    current_values[cache_key] = {
                        "currentValue": value,
                        "attr_full": attr_full,
                        "frame": frame,
                        "hasKeyframe": frame in all_keyframes
                    }
                except Exception:
                    continue
    
    if not current_values:
        print("[Blend to Undo] No values captured")
        return _blend_undo_data_cache
    
    # Step 2: Perform undo to get previous values
    cmds.undo()
    
    # Step 3: Capture undo values
    for cache_key, data in current_values.items():
        try:
            attr_full = data["attr_full"]
            frame = data["frame"]
            
            undo_value = get_value_at_time(attr_full, frame)
            if isinstance(undo_value, (list, tuple)):
                continue
            
            data["undoValue"] = undo_value
            _blend_undo_data_cache[cache_key] = data
            
        except Exception:
            continue
    
    # Step 4: Redo to restore current state
    cmds.redo()
    
    print(f"[Blend to Undo] Captured {len(_blend_undo_data_cache)} values for blending")
    
    return _blend_undo_data_cache


def execute(percentage):
    """
    Execute blend to undo slider functionality.
    
    Args:
        percentage: -100 to 100
            0 = current value (no change)
            100 = fully at undo value
            -100 = exaggerate away from undo value
    """
    global _blend_undo_data_cache, _is_dragging, _processing_context
    
    if not _blend_undo_data_cache:
        _blend_undo_data_cache = prepare_blend_data()
    
    if not _blend_undo_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True

    current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
    amount = slider_amount(percentage)
    
    for cache_key, cache in _blend_undo_data_cache.items():
        attr_full = cache.get("attr_full")
        frame = cache.get("frame")
        has_keyframe = cache.get("hasKeyframe", False)
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if cmds.getAttr(attr_full, lock=True) or not cmds.getAttr(attr_full, settable=True):
                continue
            
            current_value = cache.get("currentValue")
            undo_value = cache.get("undoValue")
            
            if current_value is None or undo_value is None:
                continue
            
            # Calculate blended value
            # 0% = current, 100% = undo, -100% = exaggerate away from undo
            if percentage == 0:
                blended_value = current_value
            elif percentage > 0:
                # Blend towards undo value
                blended_value = current_value + (undo_value - current_value) * amount
            else:
                # Exaggerate away from undo value
                difference = current_value - undo_value
                blended_value = current_value + difference * amount
            
            # Check min/max limits
            obj, attr = attr_full.split('.', 1)
            try:
                if cmds.attributeQuery(attr, node=obj, minExists=True):
                    min_limit = cmds.attributeQuery(attr, node=obj, minimum=True)[0]
                    blended_value = max(blended_value, min_limit)
                
                if cmds.attributeQuery(attr, node=obj, maxExists=True):
                    max_limit = cmds.attributeQuery(attr, node=obj, maximum=True)[0]
                    blended_value = min(blended_value, max_limit)
            except:
                pass
            
            # Apply value
            if has_keyframe:
                apply_slider_value(attr_full, frame, blended_value, current_time)
            else:
                cmds.setAttr(attr_full, blended_value)
            
        except Exception:
            continue


def reset():
    """Reset blend to undo slider state."""
    global _blend_undo_data_cache, _is_dragging, _processing_context
    
    if _blend_undo_data_cache:
        current_time = _processing_context.get('current_time', cmds.currentTime(query=True))
        for cache_key, cache_data in _blend_undo_data_cache.items():
            try:
                attr_full = cache_data.get("attr_full")
                frame = cache_data.get("frame")
                has_keyframe = cache_data.get("hasKeyframe", False)
                
                if not attr_full:
                    continue
                
                # Get the current value after slider manipulation
                if has_keyframe:
                    current_value = get_slider_value(attr_full, frame, current_time)
                else:
                    current_value = cmds.getAttr(attr_full)
                
                if isinstance(current_value, (list, tuple)):
                    if len(current_value) == 1:
                        current_value = current_value[0]
                    else:
                        continue
                
                # Set keyframe with the final value
                finalize_slider_value(attr_full, frame, current_value, current_time)
                
            except Exception:
                continue
    
    _blend_undo_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
