"""
    AnimKey Slider: Mirror Blend
    
    Blend between current pose and mirrored pose.
    
    Features:
    - Captures current pose values
    - Calculates mirrored pose values
    - Blends between them based on slider percentage (0% = current, 100% = mirrored)
    - Creates keyframes on release
"""

import maya.cmds as cmds
from AnimKey.buttons.mirror import (
    find_opposite_name,
    load_snapshot,
    is_attribute_modifiable,
    ATTRIBUTES_TO_IGNORE,
    clear_mirror_cache,
    get_opposite_control,
    compute_mirror_values,
    get_rig_identifier
)


# Global cache for mirror blend data
_mirror_blend_data_cache = {}
_is_dragging = False


def _shortest_angle_delta(target, source):
    return ((target - source + 180.0) % 360.0) - 180.0


def prepare_mirror_blend_data(objs=None):
    """
    Prepare mirror blend data cache for current selection.
    
    Captures:
    - Current pose values for all selected controls
    - Mirrored pose values (what they would be after mirror)
    
    Args:
        objs: List of objects to process. If None, uses current selection.
    
    Returns:
        dict: Cache with original and mirrored values
    """
    global _mirror_blend_data_cache
    _mirror_blend_data_cache = {}
    
    # Clear cached mirror values from the previous slider drag.
    clear_mirror_cache()
    
    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _mirror_blend_data_cache
    
    fallback_snapshot = load_snapshot()
    
    # Process each selected control
    for control in objects:
        if not cmds.objExists(control):
            continue
        snapshot = load_snapshot(get_rig_identifier([control])) or fallback_snapshot
        
        opposite_name = get_opposite_control(control, snapshot) or find_opposite_name(control)
        is_central = (opposite_name is None or not cmds.objExists(opposite_name))

        target = control if is_central else opposite_name
        mirrored_values = compute_mirror_values(
            control, target=target, snapshot=snapshot, is_central=is_central
        )

        for attr, mirrored_value in mirrored_values.items():
            if attr in ATTRIBUTES_TO_IGNORE:
                continue
            target_attr = f"{target}.{attr}"
            try:
                original_blend_value = cmds.getAttr(target_attr)
                if isinstance(original_blend_value, (list, tuple)):
                    if len(original_blend_value) == 1:
                        original_blend_value = original_blend_value[0]
                    else:
                        continue
                if not isinstance(original_blend_value, (int, float)):
                    continue

                _mirror_blend_data_cache[target_attr] = {
                    "original": original_blend_value,
                    "mirrored": mirrored_value,
                    "opposite": None,
                    "is_central": is_central,
                    "attr": attr
                }
            except Exception:
                continue
    
    return _mirror_blend_data_cache


def execute(percentage):
    """
    Execute mirror blend slider functionality.
    
    Args:
        percentage (float): Percentage value from slider (0-100)
            - 0% = current pose (original values)
            - 100% = fully mirrored pose
    """
    global _mirror_blend_data_cache, _is_dragging
    
    if not _mirror_blend_data_cache:
        _mirror_blend_data_cache = prepare_mirror_blend_data()
    
    if not _mirror_blend_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    # Clamp percentage
    percentage = max(0.0, min(100.0, percentage))
    
    # Apply blend to each attribute
    for attr_full, cache in _mirror_blend_data_cache.items():
        try:
            original = cache["original"]
            mirrored = cache["mirrored"]
            opposite = cache.get("opposite")
            is_central = cache.get("is_central", False)
            
            # Calculate blended value
            attr = cache.get("attr", attr_full.split('.', 1)[-1])
            if attr in ("rotateX", "rotateY", "rotateZ"):
                difference = _shortest_angle_delta(mirrored, original)
            else:
                difference = mirrored - original
            blended_value = original + (difference * percentage / 100.0)
            
            # The cache key is already the exact target attr produced by the calibrated mirror.
            if is_central or not opposite:
                # Central control: set on itself
                target_attr = attr_full
            else:
                # Paired control: set on opposite
                if opposite and cmds.objExists(opposite):
                    obj, attr = attr_full.split('.', 1)
                    target_attr = f"{opposite}.{attr}"
                    
                    # Check if opposite has this attribute and it's modifiable
                    if not is_attribute_modifiable(opposite, attr):
                        continue
                else:
                    continue
            
            # Check min/max limits
            obj, attr = target_attr.split('.', 1)
            if cmds.attributeQuery(attr, node=obj, minExists=True):
                min_limit = cmds.attributeQuery(attr, node=obj, minimum=True)[0]
                blended_value = max(blended_value, min_limit)
            
            if cmds.attributeQuery(attr, node=obj, maxExists=True):
                max_limit = cmds.attributeQuery(attr, node=obj, maximum=True)[0]
                blended_value = min(blended_value, max_limit)
            
            # Set the blended value
            cmds.setAttr(target_attr, blended_value)
            
        except Exception:
            continue


def reset():
    """
    Reset mirror blend slider state.
    Creates keyframes at current time with current values (already modified by slider).
    """
    global _mirror_blend_data_cache, _is_dragging
    
    current_time = cmds.currentTime(query=True)
    
    # Create keyframes for all modified attributes
    if _mirror_blend_data_cache:
        for attr_full, cache in _mirror_blend_data_cache.items():
            try:
                opposite = cache.get("opposite")
                is_central = cache.get("is_central", False)
                
                # Determine target attribute
                if is_central or not opposite:
                    target_attr = attr_full
                else:
                    if opposite and cmds.objExists(opposite):
                        obj, attr = attr_full.split('.', 1)
                        target_attr = f"{opposite}.{attr}"
                        
                        if not is_attribute_modifiable(opposite, attr):
                            continue
                    else:
                        continue
                
                # Get current value (already modified by slider)
                current_value = cmds.getAttr(target_attr)
                
                # Handle list/tuple values
                if isinstance(current_value, (list, tuple)):
                    if len(current_value) == 1:
                        current_value = current_value[0]
                    else:
                        # For compound attributes, create keyframe for each component
                        for i in range(len(current_value)):
                            try:
                                cmds.setKeyframe(f"{target_attr}[{i}]", time=current_time, value=current_value[i])
                            except:
                                pass
                        continue
                
                # Create keyframe at current time with current value
                cmds.setKeyframe(target_attr, time=current_time, value=current_value)
                
            except Exception:
                continue
    
    # Reset cache
    _mirror_blend_data_cache = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False

