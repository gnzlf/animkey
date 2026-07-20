"""
    AnimKey Node Module
    
    Utilities for creating and managing AnimKey nodes in Maya's outliner.
    Handles custom icons and dagContainer creation.
"""

import maya.cmds as cmds
import os


DEFAULT_OUTLINER_ICON = "animkey_outliner_minimal_32.png"


def get_icon_path(icon_name=DEFAULT_OUTLINER_ICON):
    """
    Get path to AnimKey icon file for outliner display.
    
    Args:
        icon_name: Name of the icon file (default: Outliner_AnimKey.png)
    
    Returns:
        Full path to the icon file, or empty string if not found
    """
    # Try different possible locations
    possible_paths = []
    
    # Relative to this file (development and installed location)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths.append(os.path.join(current_dir, "..", "data", "icons", icon_name))
    
    # Find and return the first existing path
    for path in possible_paths:
        normalized_path = os.path.normpath(path)
        if os.path.exists(normalized_path):
            return normalized_path
    
    return ""


def create_animkey_container(name="AnimKey", parent=None, icon_name=DEFAULT_OUTLINER_ICON):
    """
    Create an AnimKey dagContainer with custom outliner icon.
    
    Args:
        name: Name of the container node
        parent: Optional parent node to parent the container under
    
    Returns:
        Name of the created or existing container
    """
    if cmds.objExists(name):
        # Update icon on existing container if missing
        _update_icon_if_missing(name, icon_name=icon_name)
        if parent and cmds.objExists(parent):
            try:
                current_parent = cmds.listRelatives(name, parent=True, fullPath=False) or []
                if parent not in current_parent:
                    cmds.parent(name, parent)
            except Exception:
                pass
        return name
    
    # Create dagContainer
    container = cmds.container(type='dagContainer', name=name)
    
    # Set custom icon for outliner
    icon_path = get_icon_path(icon_name)
    if icon_path:
        try:
            cmds.setAttr(container + '.iconName', icon_path, type='string')
        except Exception as e:
            print(f"AnimKey: Could not set icon for {name}: {e}")
    
    # Parent if specified
    if parent and cmds.objExists(parent):
        cmds.parent(container, parent)
    
    # Lock and hide transform attributes
    _lock_transform_attributes(container)
    
    return container


def create_animkey_main():
    """
    Create the main AnimKey container node.
    This is the root node for all AnimKey-created objects.
    
    Returns:
        Name of the container ("AnimKey")
    """
    return create_animkey_container("AnimKey")


def create_animkey_trail():
    """
    Create the animkey_trail container node.
    This is for motion trail objects.
    
    Returns:
        Name of the container ("animkey_trail")
    """
    # Ensure main container exists first
    create_animkey_main()
    
    return create_animkey_container("animkey_trail", parent="AnimKey")


def create_animkey_child_container(name):
    """
    Create an AnimKey child dagContainer under the main AnimKey container.

    Args:
        name: Maya-safe child container node name.

    Returns:
        Name of the created or existing container.
    """
    create_animkey_main()
    return create_animkey_container(name, parent="AnimKey")


def _update_icon_if_missing(node_name, icon_name=DEFAULT_OUTLINER_ICON):
    """Update icon on a node if it's missing or empty."""
    try:
        if cmds.attributeQuery('iconName', node=node_name, exists=True):
            current_icon = cmds.getAttr(node_name + '.iconName')
            if not current_icon or not os.path.exists(current_icon):
                icon_path = get_icon_path(icon_name)
                if icon_path:
                    cmds.setAttr(node_name + '.iconName', icon_path, type='string')
    except:
        pass


def _lock_transform_attributes(node_name):
    """Lock and hide transform attributes on a node"""
    attributes = [
        "translateX", "translateY", "translateZ",
        "rotateX", "rotateY", "rotateZ",
        "scaleX", "scaleY", "scaleZ", 
        "visibility"
    ]
    
    for attr in attributes:
        try:
            cmds.setAttr(node_name + "." + attr, lock=True, keyable=False, channelBox=False)
        except:
            pass


def update_all_animkey_icons():
    """
    Update icons on all existing AnimKey nodes.
    Useful for refreshing icons after installation or update.
    """
    nodes_to_update = [
        "AnimKey",
        "animkey_trail",
        "animkey_brush",
        "animkey_temp_controls",
        "animkey_retimer",
    ]
    
    # Also find any AnimKey child containers
    all_nodes = cmds.ls(type='dagContainer') or []
    for node in all_nodes:
        if node.startswith("animkey_") and node not in nodes_to_update:
            nodes_to_update.append(node)
    
    icon_path = get_icon_path()
    if not icon_path:
        print("AnimKey: Icon file not found")
        return
    
    updated = 0
    for node in nodes_to_update:
        if cmds.objExists(node):
            try:
                cmds.setAttr(node + '.iconName', icon_path, type='string')
                updated += 1
            except Exception as e:
                print(f"AnimKey: Could not update icon for {node}: {e}")
    
    if updated > 0:
        print(f"AnimKey: Updated icons on {updated} node(s)")

