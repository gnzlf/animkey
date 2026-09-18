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
import maya.api.OpenMayaUI as omui2
import maya.OpenMayaUI as omui1
import os


# ═══════════════════════════════════════════════════════════════════════════════
#                           GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════════════

_original_camera = None
_original_panel = None
_last_model_panel = None

FOLLOW_ROOT_TAG = "animKeyFollowCamRoot"
FOLLOW_CAMERA_TAG = "animKeyFollowCamera"
ORIGINAL_CAMERA_ATTR = "animKeyOriginalCamera"
TARGET_ATTR = "animKeyFollowTarget"
PANEL_ATTR = "animKeySourcePanel"


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
    return container


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
    return container



def _is_model_panel(panel):
    if not panel:
        return False
    try:
        return cmds.getPanel(typeOf=panel) == "modelPanel"
    except Exception:
        return False


def _active_3d_view_context():
    """Return the last Maya 3D view's panel and camera, if available."""
    panel = None
    camera = None
    try:
        view = omui2.M3dView.active3dView()
        camera_path = view.getCamera()
        if camera_path and camera_path.isValid():
            camera = camera_path.fullPathName()

        # The active M3dView survives focus moving from the viewport to the
        # AnimKey toolbar.  Resolve its QWidget back to the owning modelPanel
        # when Maya exposes the control path.
        try:
            widget_ptr = view.widget()
            control = omui1.MQtUtil.fullName(int(widget_ptr)) if widget_ptr else None
            if control:
                panel = cmds.getPanel(containing=control)
        except Exception:
            panel = None
    except Exception:
        pass
    return (panel if _is_model_panel(panel) else None), _camera_transform(camera)


def _is_perspective_panel(panel):
    camera = _panel_camera(panel)
    if not camera:
        return False
    try:
        shapes = cmds.listRelatives(
            camera, shapes=True, fullPath=True, type="camera"
        ) or []
        return bool(shapes) and not bool(cmds.getAttr(shapes[0] + ".orthographic"))
    except Exception:
        return False


def _get_active_panel():
    """Return the viewport used immediately before the toolbar was clicked."""
    global _last_model_panel

    direct_candidates = []
    for query in (
        lambda: cmds.getPanel(withFocus=True),
        lambda: cmds.getPanel(underPointer=True),
    ):
        try:
            panel = query()
            if panel and panel not in direct_candidates:
                direct_candidates.append(panel)
        except Exception:
            pass

    for panel in direct_candidates:
        if _is_model_panel(panel):
            _last_model_panel = panel
            return panel

    # Clicking a toolbar usually removes both focus and pointer from the
    # modelPanel.  M3dView still remembers the viewport the animator last used.
    active_view_panel, active_view_camera = _active_3d_view_context()
    if active_view_panel:
        _last_model_panel = active_view_panel
        return active_view_panel

    visible = []
    try:
        visible.extend([
            panel for panel in (cmds.getPanel(visiblePanels=True) or [])
            if _is_model_panel(panel) and panel not in visible
        ])
    except Exception:
        pass

    # Some Maya layouts do not let MQtUtil resolve the panel name.  Match the
    # active M3dView camera instead; this correctly selects the perspective or
    # shot-camera panel rather than modelPanel1 (usually Top view).
    if active_view_camera:
        for panel in visible:
            if _same_node(_panel_camera(panel), active_view_camera):
                _last_model_panel = panel
                return panel

    if _last_model_panel and _is_model_panel(_last_model_panel):
        if not visible or _last_model_panel in visible:
            return _last_model_panel

    # A toolbar click in the standard four-view layout used to fall through to
    # the first visible panel, which is commonly an orthographic view.  Prefer
    # the visible perspective/shot-camera view for the natural FollowCam flow.
    for panel in visible:
        if _is_perspective_panel(panel):
            _last_model_panel = panel
            return panel

    candidates = list(visible)
    try:
        candidates.extend([
            panel for panel in (cmds.getPanel(type="modelPanel") or [])
            if panel not in candidates
        ])
    except Exception:
        pass

    for panel in candidates:
        try:
            if _is_model_panel(panel):
                _last_model_panel = panel
                return panel
        except Exception:
            continue
    return None


def _long_name(node):
    if not node:
        return None
    try:
        matches = cmds.ls(node, long=True) or []
        return matches[0] if matches else node
    except Exception:
        return node


def _camera_transform(node):
    """Resolve either a camera shape or transform to its long transform path."""
    if not node:
        return None
    node = str(node).split(".", 1)[0]
    if not cmds.objExists(node):
        return None
    node = _long_name(node)
    try:
        if cmds.nodeType(node) == "camera":
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            return parents[0] if parents else None
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True, type="camera") or []
        return node if shapes else None
    except Exception:
        return None


def _selected_transform():
    try:
        selected = cmds.ls(selection=True, long=True, objectsOnly=True) or []
    except Exception:
        selected = []
    if not selected:
        try:
            selected = cmds.ls(selection=True, long=True) or []
        except Exception:
            selected = []
    if not selected:
        return None

    node = str(selected[0]).split(".", 1)[0]
    if not cmds.objExists(node):
        return None
    node = _long_name(node)
    try:
        if cmds.objectType(node, isAType="transform"):
            return node
    except Exception:
        pass
    try:
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if parents and cmds.objectType(parents[0], isAType="transform"):
            return parents[0]
    except Exception:
        pass
    return None


def _same_node(left, right):
    if not left or not right:
        return False
    try:
        return _long_name(left) == _long_name(right)
    except Exception:
        return left == right


def _is_below(node, ancestor):
    node_path = _long_name(node)
    ancestor_path = _long_name(ancestor)
    if not node_path or not ancestor_path:
        return False
    return node_path == ancestor_path or node_path.startswith(ancestor_path + "|")


def _find_follow_roots():
    roots = []
    for node in cmds.ls(type="dagContainer", long=True) or []:
        try:
            if (
                cmds.attributeQuery(FOLLOW_ROOT_TAG, node=node, exists=True)
                and cmds.getAttr("{}.{}".format(node, FOLLOW_ROOT_TAG))
            ):
                roots.append(node)
        except Exception:
            pass

    # Backward compatibility with scenes made by the original implementation.
    for node in cmds.ls("AnimKey_followCam", long=True, type="dagContainer") or []:
        if node not in roots:
            roots.append(node)
    return roots


def _follow_cameras(root):
    cameras = []
    try:
        shapes = cmds.listRelatives(
            root, allDescendents=True, fullPath=True, type="camera"
        ) or []
    except Exception:
        shapes = []
    for shape in shapes:
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents and parents[0] not in cameras:
            cameras.append(parents[0])
    return cameras


def _message_source(node, attribute):
    try:
        sources = cmds.listConnections(
            "{}.{}".format(node, attribute), source=True, destination=False
        ) or []
        return _long_name(sources[0]) if sources else None
    except Exception:
        return None


def _stored_panel(root):
    try:
        return cmds.getAttr("{}.{}".format(root, PANEL_ATTR)) or None
    except Exception:
        return None


def _add_follow_metadata(root, camera, target, panel, translation, rotation):
    attrs = (
        (FOLLOW_ROOT_TAG, "bool"),
        (ORIGINAL_CAMERA_ATTR, "message"),
        (TARGET_ATTR, "message"),
        (PANEL_ATTR, "string"),
        ("animKeyFollowTranslation", "bool"),
        ("animKeyFollowRotation", "bool"),
    )
    for attribute, attr_type in attrs:
        if cmds.attributeQuery(attribute, node=root, exists=True):
            continue
        if attr_type == "string":
            cmds.addAttr(root, longName=attribute, dataType="string")
        else:
            cmds.addAttr(root, longName=attribute, attributeType=attr_type)

    cmds.setAttr("{}.{}".format(root, FOLLOW_ROOT_TAG), True)
    cmds.setAttr("{}.{}".format(root, PANEL_ATTR), panel or "", type="string")
    cmds.setAttr("{}.animKeyFollowTranslation".format(root), bool(translation))
    cmds.setAttr("{}.animKeyFollowRotation".format(root), bool(rotation))
    if camera and cmds.objExists(camera):
        cmds.connectAttr(
            camera + ".message",
            "{}.{}".format(root, ORIGINAL_CAMERA_ATTR),
            force=True,
        )
    if target and cmds.objExists(target):
        cmds.connectAttr(
            target + ".message",
            "{}.{}".format(root, TARGET_ATTR),
            force=True,
        )


def _panel_camera(panel):
    try:
        return _camera_transform(cmds.modelEditor(panel, query=True, camera=True))
    except Exception:
        return None


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
    global _original_camera, _original_panel

    if not translation and not rotation:
        cmds.warning("AnimKey: Enable translation, rotation, or both for Follow Cam.")
        return None

    selected_objects = cmds.ls(selection=True, long=True) or []
    target_object = _selected_transform()
    if not target_object:
        cmds.warning('AnimKey: No objects selected. Please select an object to follow.')
        return None

    panel = _get_active_panel()
    if not panel:
        cmds.warning("AnimKey: No active viewport. Please click on a viewport first.")
        return None

    old_roots = _find_follow_roots()
    panel_camera = _panel_camera(panel)
    source_camera = panel_camera

    # Rebuilding while looking through the existing FollowCam must duplicate
    # the original camera, not the temporary camera that is about to be deleted.
    for old_root in old_roots:
        if panel_camera and _is_below(panel_camera, old_root):
            source_camera = _message_source(old_root, ORIGINAL_CAMERA_ATTR)
            break
    source_camera = _camera_transform(source_camera) or _camera_transform(_original_camera)
    source_camera = source_camera or _camera_transform("persp")
    if not source_camera:
        cmds.warning("AnimKey: Could not resolve the viewport camera.")
        return None

    for old_root in old_roots:
        if _is_below(target_object, old_root):
            cmds.warning("AnimKey: Select a rig control, not the existing Follow Cam.")
            return None

    _original_camera = source_camera
    _original_panel = panel
    created_root = None
    follow_cam = None

    cmds.undoInfo(openChunk=True, chunkName="AnimKey_FollowCam")
    try:
        if old_roots:
            cmds.delete(old_roots)

        _create_animkey_container()
        temp_container = _create_temp_container()

        created_root = cmds.container(type="dagContainer", name="AnimKey_followCam")
        if temp_container and cmds.objExists(temp_container):
            created_root = cmds.parent(
                created_root, temp_container, absolute=True
            )[0]
        created_root = _long_name(created_root)

        # Ignore transforms inherited from shared organizational containers.
        # This keeps all constraint math in world space even if an older scene
        # contains a moved/scaled AnimKey root.
        try:
            cmds.setAttr(created_root + ".inheritsTransform", False)
        except Exception:
            pass
        for attr, value in (
            ("translateX", 0.0), ("translateY", 0.0), ("translateZ", 0.0),
            ("rotateX", 0.0), ("rotateY", 0.0), ("rotateZ", 0.0),
            ("scaleX", 1.0), ("scaleY", 1.0), ("scaleZ", 1.0),
        ):
            try:
                cmds.setAttr("{}.{}".format(created_root, attr), value)
            except Exception:
                pass

        icon_path = _get_icon_path("animkey_btn_cam_128.png")
        if icon_path:
            try:
                cmds.setAttr(created_root + ".iconName", icon_path, type="string")
            except Exception:
                pass

        camera_matrix = cmds.xform(
            source_camera, query=True, worldSpace=True, matrix=True
        )
        follow_cam_group = cmds.group(
            empty=True, name="AnimKey_followCam_grp", parent=created_root
        )
        cmds.xform(
            follow_cam_group, worldSpace=True, matrix=camera_matrix
        )

        follow_cam = cmds.duplicate(
            source_camera,
            name="followCam",
            returnRootsOnly=True,
            upstreamNodes=False,
            inputConnections=False,
        )[0]
        # Production cameras are commonly locked. The duplicate is temporary
        # and must be editable so Maya can preserve its world matrix when it is
        # reparented below the follow group.
        for attr in (
            "translateX", "translateY", "translateZ",
            "rotateX", "rotateY", "rotateZ",
            "scaleX", "scaleY", "scaleZ", "visibility",
        ):
            try:
                cmds.setAttr("{}.{}".format(follow_cam, attr), lock=False)
            except Exception:
                pass
        follow_cam = cmds.parent(
            follow_cam, follow_cam_group, absolute=True
        )[0]
        follow_cam = _long_name(follow_cam)

        if not cmds.attributeQuery(FOLLOW_CAMERA_TAG, node=follow_cam, exists=True):
            cmds.addAttr(
                follow_cam, longName=FOLLOW_CAMERA_TAG,
                attributeType="bool", defaultValue=True,
            )
        cmds.setAttr("{}.{}".format(follow_cam, FOLLOW_CAMERA_TAG), True)
        try:
            cmds.setAttr(follow_cam + ".visibility", 1)
            cmds.showHidden(follow_cam)
        except Exception:
            pass

        if translation and rotation:
            constraint = cmds.parentConstraint(
                target_object,
                follow_cam_group,
                maintainOffset=True,
                name="AnimKey_followCam_parentConstraint",
            )
        elif translation:
            constraint = cmds.pointConstraint(
                target_object,
                follow_cam_group,
                maintainOffset=True,
                name="AnimKey_followCam_pointConstraint",
            )
        else:
            constraint = cmds.orientConstraint(
                target_object,
                follow_cam_group,
                maintainOffset=True,
                name="AnimKey_followCam_orientConstraint",
            )
        if not constraint:
            raise RuntimeError("Maya did not create the Follow Cam constraint")

        _add_follow_metadata(
            created_root,
            source_camera,
            target_object,
            panel,
            translation,
            rotation,
        )
        for attr in ("t", "r", "s"):
            try:
                cmds.setAttr("{}.{}".format(created_root, attr), lock=True)
            except Exception:
                pass

        cmds.lookThru(panel, follow_cam)
        try:
            cmds.dgdirty(follow_cam_group)
            cmds.refresh(force=True)
        except Exception:
            pass
    except Exception as exc:
        if created_root and cmds.objExists(created_root):
            try:
                cmds.delete(created_root)
            except Exception:
                pass
        cmds.warning("AnimKey: Could not create Follow Cam: {}".format(exc))
        follow_cam = None
    finally:
        try:
            cmds.select(selected_objects, replace=True)
        except Exception:
            pass
        cmds.undoInfo(closeChunk=True)

    if not follow_cam:
        return None
    
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
    return follow_cam


def remove_follow_cam(*args):
    """Remove the follow camera and restore original view"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.followCam.remove_follow_cam"):
        return None
    global _original_camera, _original_panel

    roots = _find_follow_roots()
    if not roots:
        cmds.warning("AnimKey: No Follow Cam in the scene.")
        return False

    panels = cmds.getPanel(type="modelPanel") or []
    for candidate in (
        _original_panel,
        _get_active_panel(),
    ):
        if candidate and candidate not in panels:
            panels.append(candidate)
    for root in roots:
        candidate = _stored_panel(root)
        if candidate and candidate not in panels:
            panels.append(candidate)
    restored_panels = set()
    for root in roots:
        original = _camera_transform(_message_source(root, ORIGINAL_CAMERA_ATTR))
        original = original or _camera_transform(_original_camera) or _camera_transform("persp")
        follow_cameras = _follow_cameras(root)
        for panel in panels:
            current = _panel_camera(panel)
            if any(_same_node(current, camera) for camera in follow_cameras):
                if original:
                    try:
                        cmds.lookThru(panel, original)
                        restored_panels.add(panel)
                    except Exception:
                        pass

        stored_panel = _stored_panel(root)
        if (
            not restored_panels
            and stored_panel
            and original
        ):
            try:
                cmds.lookThru(stored_panel, original)
                restored_panels.add(stored_panel)
            except Exception:
                pass

    cmds.undoInfo(openChunk=True, chunkName="AnimKey_RemoveFollowCam")
    try:
        cmds.delete(roots)
    finally:
        cmds.undoInfo(closeChunk=True)

    _original_camera = None
    _original_panel = None
    cmds.inViewMessage(
        amg="<span style='color:#bf616a'>Follow Cam: Removed</span>",
        pos='topCenter',
        fade=True,
        fadeStayTime=1500
    )
    return True


def has_follow_cam():
    """Check if a follow cam exists"""
    return bool(_find_follow_roots())


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
