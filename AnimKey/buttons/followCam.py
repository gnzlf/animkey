"""
    AnimKey Follow Cam
    
    Creates a camera that follows a selected object.
    The camera maintains its offset while following the object's
    translation and/or rotation.
    
    Based on AnimKey's FollowCam functionality.
    
    Structure:
    - AnimKey (dagContainer)
      - animkey_temp (dagContainer)
        - AnimKey_followCam (group with camera and constraints)
    
    Features:
    - Follow translation only
    - Follow rotation only
    - Follow both translation and rotation
    - Easy removal of follow cam
"""

import maya.cmds as cmds
import os


# ═══════════════════════════════════════════════════════════════════════════════
#                           GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════════════

_original_camera = None


# ═══════════════════════════════════════════════════════════════════════════════
#                           HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_icon_path(icon_name=None):
    """
    Get path to AnimKey icon file for outliner display.
    
    Args:
        icon_name: Optional specific icon name (defaults to Outliner_AnimKey.png)
    
    Returns:
        Full path to the icon file, or empty string if not found
    """
    if icon_name is None:
        icon_name = "Outliner_AnimKey.png"
    
    # Try different possible locations
    possible_paths = []
    
    # 1. Relative to this file (development and installed location)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths.append(os.path.join(current_dir, "..", "data", "icons", icon_name))
    
    # Find and return the first existing path
    for path in possible_paths:
        normalized_path = os.path.normpath(path)
        if os.path.exists(normalized_path):
            return normalized_path
    
    return ""


def _create_animkey_container():
    """Create AnimKey dagContainer if it doesn't exist"""
    if not cmds.objExists("AnimKey"):
        # Create dagContainer (like AnimKey's AnimKey)
        container = cmds.container(type='dagContainer', name="AnimKey")
        
        # Set custom icon for outliner
        icon_path = _get_icon_path()
        if icon_path:
            try:
                cmds.setAttr(container + '.iconName', icon_path, type='string')
            except Exception as e:
                print(f"AnimKey: Could not set icon: {e}")
        
        # Lock and hide transform attributes
        attributes = ["translateX", "translateY", "translateZ",
                     "rotateX", "rotateY", "rotateZ",
                     "scaleX", "scaleY", "scaleZ", "visibility"]
        
        for attr in attributes:
            try:
                cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
            except:
                pass
    else:
        # Update icon on existing container if missing
        container = "AnimKey"
        try:
            current_icon = cmds.getAttr(container + '.iconName')
            if not current_icon:
                icon_path = _get_icon_path()
                if icon_path:
                    cmds.setAttr(container + '.iconName', icon_path, type='string')
        except:
            pass


def _create_temp_container():
    """Create animkey_temp dagContainer if it doesn't exist"""
    if not cmds.objExists("animkey_temp"):
        # Create dagContainer
        container = cmds.container(type='dagContainer', name="animkey_temp")
        
        # Set custom icon for outliner (same icon as parent)
        icon_path = _get_icon_path()
        if icon_path:
            try:
                cmds.setAttr(container + '.iconName', icon_path, type='string')
            except Exception as e:
                print(f"AnimKey: Could not set icon: {e}")
        
        # Parent to AnimKey
        if cmds.objExists("AnimKey"):
            cmds.parent(container, "AnimKey")
        
        # Lock and hide transform attributes
        attributes = ["translateX", "translateY", "translateZ",
                     "rotateX", "rotateY", "rotateZ",
                     "scaleX", "scaleY", "scaleZ", "visibility"]
        
        for attr in attributes:
            try:
                cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
            except:
                pass
    else:
        # Update icon on existing container if missing
        container = "animkey_temp"
        try:
            current_icon = cmds.getAttr(container + '.iconName')
            if not current_icon:
                icon_path = _get_icon_path()
                if icon_path:
                    cmds.setAttr(container + '.iconName', icon_path, type='string')
        except:
            pass



def _get_active_panel():
    """Get the active model panel"""
    model_panels = ["modelPanel1", "modelPanel2", "modelPanel3", "modelPanel4"]
    panel = cmds.getPanel(withFocus=True)
    
    if panel not in model_panels:
        return None
    
    return panel


# ═══════════════════════════════════════════════════════════════════════════════
#                           MAIN FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def create_follow_cam(translation=True, rotation=True, *args):
    """
    Create a follow camera that tracks the selected object.
    
    Args:
        translation: Follow the object's translation
        rotation: Follow the object's rotation
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.followCam.create_follow_cam"):
        return None
    global _original_camera
    
    # Get selected object
    selected_objects = cmds.ls(selection=True)
    
    if not selected_objects:
        cmds.warning('AnimKey: No objects selected. Please select an object to follow.')
        return
    
    target_object = selected_objects[0]
    
    # Create AnimKey container structure
    _create_animkey_container()
    _create_temp_container()
    
    # Get active panel and camera
    panel = _get_active_panel()
    if not panel:
        cmds.warning("AnimKey: No active viewport. Please click on a viewport first.")
        return
    
    camera = cmds.modelEditor(panel, query=True, camera=True)
    _original_camera = camera
    
    # 1. Create/Find the Main Follow Cam CONTAINER
    # This must be a dagContainer to support the custom icon properly in the outliner
    fc_container_name = "AnimKey_followCam"
    
    if not cmds.objExists(fc_container_name):
        # Create new container
        cmds.container(type='dagContainer', name=fc_container_name)
        
        # Set custom icon
        icon_path = _get_icon_path("animkey_btn_cam_128.png")
        if icon_path:
            try:
                cmds.setAttr(fc_container_name + '.iconName', icon_path, type='string')
            except:
                pass
                
        # Lock standard attributes to prevent accidental moves of the container itself
        for attr in ["t", "r", "s", "v"]:
            try:
                cmds.setAttr(f"{fc_container_name}.{attr}", lock=True)
            except:
                pass
    
    # Parent (or reparent) container to animkey_temp
    if cmds.objExists('animkey_temp'):
        # Check if already parented using full path to avoid warning
        try:
            full_path = cmds.ls(fc_container_name, long=True)[0]
            # Check if animkey_temp is in the path (e.g., |animkey_temp|AnimKey_followCam)
            if "|animkey_temp|" not in full_path and not full_path.endswith("|animkey_temp"):
                 # Also check direct parent to be sure (listRelatives returns short names usually)
                 parents = cmds.listRelatives(fc_container_name, parent=True) or []
                 if 'animkey_temp' not in parents:
                    cmds.parent(fc_container_name, 'animkey_temp')
        except:
            pass
            
    # 2. Setup Camera and Inner Group
    # Check if we are refreshing or creating new
    fc_group_name = "AnimKey_followCam_grp"
    
    # If group exists, delete it to recreate (clean slate for constraints)
    if cmds.objExists(fc_group_name):
        cmds.delete(fc_group_name)
        
    # Duplicate camera
    # Ensure standard naming if possible, but handle existing
    dup_name = 'followCam'
    if cmds.objExists(dup_name) and not cmds.objExists(fc_group_name):
        # If followCam exists but group does not (weird state), try to delete it
        try:
            cmds.delete(dup_name)
        except:
            pass

    follow_cam = cmds.duplicate(camera, name=dup_name)[0]
    
    # Unlock standard attributes on the new camera
    for attr in ["t", "r", "s", "v"]:
        try:
            cmds.setAttr(f"{follow_cam}.{attr}", lock=False)
        except:
            pass
            
    # Force visibility ON
    try:
        cmds.setAttr(f"{follow_cam}.v", 1)
        cmds.showHidden(follow_cam)
    except:
        pass
    
    # Create the internal group for constraints
    # Valid hierarchy: Container -> Group -> Camera
    # Explicitly create group and parent camera into it
    follow_cam_group = cmds.group(empty=True, name=fc_group_name)
    cmds.parent(follow_cam, follow_cam_group)
    
    # Constraints are applied to the GROUP (in World Space for safety)
    if translation and not rotation:
        # Point constraint for translation only
        cmds.pointConstraint(target_object, follow_cam_group, maintainOffset=True)
    else:
        # Parent constraint with skip options
        skip_trans = [] if translation else ['x', 'y', 'z']
        skip_rot = [] if rotation else ['x', 'y', 'z']
        
        cmds.parentConstraint(
            target_object, 
            follow_cam_group, 
            maintainOffset=True, 
            skipTranslate=skip_trans, 
            skipRotate=skip_rot
        )
        
    # Parent the Group to the Container (AFTER constraining)
    cmds.parent(follow_cam_group, fc_container_name)
    
    # Switch viewport to follow camera
    if camera != 'followCam':
        cmds.lookThru(panel, follow_cam)
    
    # Restore selection
    cmds.select(selected_objects)
    
    # Show message
    mode = []
    if translation:
        mode.append("Translation")
    if rotation:
        mode.append("Rotation")
    mode_str = " & ".join(mode)
    
    cmds.inViewMessage(
        amg=f"<span style='color:#5e81ac'>Follow Cam: ON</span><br>"
            f"<span style='color:#88c0d0'>Following {target_object} ({mode_str})</span>",
        pos='topCenter',
        fade=True,
        fadeStayTime=2000
    )


def remove_follow_cam(*args):
    """Remove the follow camera and restore original view"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.followCam.remove_follow_cam"):
        return None
    global _original_camera
    
    # Get active panel
    panel = _get_active_panel()
    if not panel:
        panel = "modelPanel4"  # Default to persp panel
    
    if cmds.objExists('AnimKey_followCam'):
        cmds.delete('AnimKey_followCam')
        
        # Restore original camera
        if _original_camera:
            cmds.lookThru(panel, _original_camera)
        else:
            cmds.lookThru(panel, 'persp')
        
        cmds.inViewMessage(
            amg="<span style='color:#bf616a'>Follow Cam: Removed</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=1500
        )
    else:
        cmds.warning("AnimKey: No Follow Cam in the scene.")


def has_follow_cam():
    """Check if a follow cam exists"""
    return cmds.objExists('AnimKey_followCam')


# ═══════════════════════════════════════════════════════════════════════════════
#                           EXECUTE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def execute(*args):
    """
    Main execute function for the CAM button.
    Creates a follow cam with both translation and rotation.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.followCam.execute"):
        return None
    create_follow_cam(translation=True, rotation=True)


