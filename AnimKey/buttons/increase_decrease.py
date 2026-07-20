"""
    AnimKey Button: Increase/Decrease Values
    
    Increases or decreases keyframe or attribute values by a specified amount.
    AnimKey value modification functionality.
    
    Features:
    - Modify selected keyframes in Graph Editor
    - Modify selected attributes in Channel Box
    - Modify attributes with keyframes at current time
    - Respects min/max limits of attributes
    - Handles compound attributes (like double3)
"""

import maya.cmds as cmds
import maya.mel as mel


def get_graph_editor_selected_keyframes():
    """
    Get selected keyframes from Graph Editor.
    
    Returns:
        list: List of tuples: [(curve_name, frame_time), ...]
    """
    anim_curves = cmds.keyframe(query=True, selected=True, name=True)
    if not anim_curves:
        return []
    
    keyframes = []
    for curve in anim_curves:
        frames = cmds.keyframe(curve, query=True, selected=True)
        if frames:
            keyframes.extend([(curve, frame) for frame in frames])
    
    return keyframes


def get_selected_channels():
    """
    Get selected channels from the Channel Box.
    
    Returns:
        list: List of selected channel names, or None if no channels are selected
    """
    try:
        # Get the main Channel Box name
        main_channel_box = mel.eval('global string $gChannelBoxName; $temp=$gChannelBoxName;')
        
        # Get selected channels
        selected_channels = cmds.channelBox(main_channel_box, query=True, selectedMainAttributes=True)
        
        return selected_channels if selected_channels else None
    except:
        return None


def modify_keyframe_values(amount):
    """
    Modify keyframe or attribute values by the specified amount.
    Works on selected keyframes in Graph Editor or selected attributes in Channel Box.
    AnimKey increase/decrease functionality.
    
    Args:
        amount (float): Amount to add to the values (positive for increase, negative for decrease)
    """
    cmds.undoInfo(openChunk=True)
    try:
        # Try to get selected keyframes from Graph Editor first
        selected_keyframes = get_graph_editor_selected_keyframes()
        
        if selected_keyframes:
            # Modify keyframe values
            for curve, frame in selected_keyframes:
                try:
                    # Get current value
                    current_value = cmds.keyframe(curve, time=(frame,), query=True, valueChange=True)
                    if current_value:
                        new_value = current_value[0] + amount
                        cmds.keyframe(curve, time=(frame,), edit=True, valueChange=new_value)
                except Exception as e:
                    # Skip if there's an error with this keyframe
                    continue
        else:
            # If no keyframes selected, try to modify selected attributes in Channel Box
            selection = cmds.ls(selection=True)
            if not selection:
                cmds.warning("AnimKey: Please select keyframes in Graph Editor or objects with attributes in Channel Box.")
                return
            
            # Get selected channels from Channel Box
            selected_channels = get_selected_channels()
            
            if not selected_channels:
                # If no channels selected, modify ALL keyable attributes of selected objects
                for obj in selection:
                    attrs = cmds.listAttr(obj, keyable=True, scalar=True) or []
                    for attr in attrs:
                        attr_full = f"{obj}.{attr}"
                        try:
                            # Check if attribute is settable and not locked
                            if cmds.getAttr(attr_full, lock=True) or not cmds.getAttr(attr_full, settable=True):
                                continue
                            
                            # Skip non-numeric types
                            attr_type = cmds.getAttr(attr_full, type=True)
                            if attr_type in ("enum", "string", "message"):
                                continue
                            
                            # Get current value
                            current_value = cmds.getAttr(attr_full)
                            
                            # Handle list/tuple values (like double3)
                            if isinstance(current_value, (list, tuple)):
                                if len(current_value) == 1:
                                    current_value = current_value[0]
                                else:
                                    continue  # Skip compound attributes
                            
                            new_value = current_value + amount
                            
                            # Check for min/max limits
                            min_value, max_value = None, None
                            if cmds.attributeQuery(attr, node=obj, minExists=True):
                                min_value = cmds.attributeQuery(attr, node=obj, min=True)[0]
                            if cmds.attributeQuery(attr, node=obj, maxExists=True):
                                max_value = cmds.attributeQuery(attr, node=obj, max=True)[0]
                            
                            if min_value is not None and new_value < min_value:
                                continue
                            if max_value is not None and new_value > max_value:
                                continue
                            
                            # Set new value
                            cmds.setAttr(attr_full, new_value)
                        except Exception:
                            continue
            else:
                # Modify selected channels
                for obj in selection:
                    for attr in selected_channels:
                        attr_full = f"{obj}.{attr}"
                        try:
                            # Check if attribute exists and is settable
                            if not cmds.objExists(attr_full):
                                continue
                            if cmds.getAttr(attr_full, lock=True) or not cmds.getAttr(attr_full, settable=True):
                                continue
                            
                            # Skip non-numeric types
                            attr_type = cmds.getAttr(attr_full, type=True)
                            if attr_type in ("enum", "string", "message"):
                                continue
                            
                            # Get current value
                            current_value = cmds.getAttr(attr_full)
                            
                            # Handle list/tuple values (like double3)
                            if isinstance(current_value, (list, tuple)):
                                if len(current_value) == 1:
                                    current_value = current_value[0]
                                else:
                                    # For compound attributes, modify each component
                                    for i in range(len(current_value)):
                                        new_comp_value = current_value[i] + amount
                                        try:
                                            cmds.setAttr(f"{attr_full}[{i}]", new_comp_value)
                                        except Exception:
                                            pass
                                    continue
                            
                            # Calculate new value
                            new_value = current_value + amount
                            
                            # Check for min/max limits
                            min_value, max_value = None, None
                            if cmds.attributeQuery(attr, node=obj, minExists=True):
                                min_value = cmds.attributeQuery(attr, node=obj, min=True)[0]
                            if cmds.attributeQuery(attr, node=obj, maxExists=True):
                                max_value = cmds.attributeQuery(attr, node=obj, max=True)[0]
                            
                            if min_value is not None and new_value < min_value:
                                continue
                            if max_value is not None and new_value > max_value:
                                continue
                            
                            # Set new value
                            cmds.setAttr(attr_full, new_value)
                        except Exception:
                            continue
    finally:
        cmds.undoInfo(closeChunk=True)


def increase_values(amount):
    """
    Increase keyframe or attribute values by the specified amount.
    
    Args:
        amount (float): Amount to increase
    """
    modify_keyframe_values(amount)


def decrease_values(amount):
    """
    Decrease keyframe or attribute values by the specified amount.
    
    Args:
        amount (float): Amount to decrease
    """
    modify_keyframe_values(-amount)


def execute(amount, increase=True):
    """
    Main function to execute when button is clicked.
    
    Args:
        amount (float): Amount to modify values
        increase (bool): If True, increase values; if False, decrease values
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.increase_decrease.execute"):
        return None
    if increase:
        increase_values(amount)
    else:
        decrease_values(amount)

