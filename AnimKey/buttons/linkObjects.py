"""
    AnimKey Link Objects
    
    Link objects allows you to save the spatial relationship between objects
    and apply that relationship back when needed. It's like using parent 
    constraints without actual constraints.
    
    Based on AnimKey's Link Objects functionality.
    
    Features:
    - Copy link position (save relationship)
    - Paste link position (apply relationship)
    - Auto-link mode (real-time updates)
    - Works with timeline range selection
"""

import os
import json

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

from AnimKey.mods import configMod as config


# ═══════════════════════════════════════════════════════════════════════════════
#                           GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════════════

class LinkObjectsState:
    """Class to maintain link objects state"""
    
    def __init__(self):
        self.relative_data = {}
        self.attribute_callback_id = None
        self.time_callback_id = None
        self.auto_link_enabled = False
        self.button = None


_state = LinkObjectsState()


# ═══════════════════════════════════════════════════════════════════════════════
#                           FILE PATHS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_link_data_folder():
    """Get the folder for link data storage"""
    user_folder = config.get_user_folder_path()
    return os.path.join(user_folder, "tools", "link_objects")


def _get_link_data_file():
    """Get the path to the link data file"""
    return os.path.join(_get_link_data_folder(), "link_data.json")


def _load_relative_data():
    """Load relative data from file"""
    global _state
    
    file_path = _get_link_data_file()
    
    if not os.path.exists(file_path):
        return False
    
    try:
        with open(file_path, 'r') as f:
            _state.relative_data = json.load(f)
        return True
    except (json.JSONDecodeError, IOError):
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#                           COPY/PASTE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def copy_link_frame(*args):
    """
    Copy the spatial relationship between selected objects at the current frame.
    
    Select at least 2 objects. The last selected object becomes the "main" object
    that the others will follow.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.linkObjects.copy_link_frame"):
        return None
    global _state
    
    selection = cmds.ls(selection=True)
    
    if len(selection) < 2:
        cmds.warning("AnimKey: Please select at least 2 objects. The last selected will be the main object.")
        return
    
    main_obj = selection[-1]
    follow_objs = selection[:-1]
    
    save_dict = {
        "main_obj": main_obj,
        "relative_matrices": {}
    }
    
    for follow_obj in follow_objs:
        # Get world matrices
        main_matrix = cmds.xform(main_obj, query=True, matrix=True, worldSpace=True)
        follow_matrix = cmds.xform(follow_obj, query=True, matrix=True, worldSpace=True)
        
        # Convert to MMatrix
        main_mmatrix = om.MMatrix(main_matrix)
        follow_mmatrix = om.MMatrix(follow_matrix)
        
        # Calculate relative matrix
        relative_matrix = follow_mmatrix * main_mmatrix.inverse()
        
        # Store as list
        save_dict["relative_matrices"][follow_obj] = [
            relative_matrix.getElement(i, j) 
            for i in range(4) 
            for j in range(4)
        ]
    
    # Save to file
    folder = _get_link_data_folder()
    os.makedirs(folder, exist_ok=True)
    
    file_path = _get_link_data_file()
    with open(file_path, 'w') as f:
        json.dump(save_dict, f, indent=2)
    
    # Load into state
    _state.relative_data = save_dict
    
    current_frame = int(cmds.currentTime(query=True))
    cmds.inViewMessage(
        amg=f"<span style='color:#b48ead'>Link Copied (Frame {current_frame})</span><br>"
            f"<span style='color:#88c0d0'>{len(follow_objs)} object(s) linked to {main_obj}</span>",
        pos='topCenter',
        fade=True,
        fadeStayTime=1500
    )


# Alias for backwards compatibility
def copy_link(*args):
    """Alias for copy_link_frame"""
    copy_link_frame(*args)


def copy_link_playback_range(*args):
    """
    Copy the spatial relationship for the entire playback range.
    This stores the relationship at the current frame but is intended
    to be used with paste_link_all_keys.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.linkObjects.copy_link_playback_range"):
        return None
    global _state
    
    selection = cmds.ls(selection=True)
    
    if len(selection) < 2:
        cmds.warning("AnimKey: Please select at least 2 objects. The last selected will be the main object.")
        return
    
    main_obj = selection[-1]
    follow_objs = selection[:-1]
    
    # Get playback range
    start_frame = int(cmds.playbackOptions(query=True, minTime=True))
    end_frame = int(cmds.playbackOptions(query=True, maxTime=True))
    
    save_dict = {
        "main_obj": main_obj,
        "relative_matrices": {},
        "playback_range": [start_frame, end_frame]
    }
    
    for follow_obj in follow_objs:
        # Get world matrices at current frame
        main_matrix = cmds.xform(main_obj, query=True, matrix=True, worldSpace=True)
        follow_matrix = cmds.xform(follow_obj, query=True, matrix=True, worldSpace=True)
        
        main_mmatrix = om.MMatrix(main_matrix)
        follow_mmatrix = om.MMatrix(follow_matrix)
        
        relative_matrix = follow_mmatrix * main_mmatrix.inverse()
        
        save_dict["relative_matrices"][follow_obj] = [
            relative_matrix.getElement(i, j) 
            for i in range(4) 
            for j in range(4)
        ]
    
    # Save to file
    folder = _get_link_data_folder()
    os.makedirs(folder, exist_ok=True)
    
    file_path = _get_link_data_file()
    with open(file_path, 'w') as f:
        json.dump(save_dict, f, indent=2)
    
    _state.relative_data = save_dict
    
    cmds.inViewMessage(
        amg=f"<span style='color:#b48ead'>Link Copied (Playback Range)</span><br>"
            f"<span style='color:#88c0d0'>{len(follow_objs)} object(s) linked to {main_obj}</span><br>"
            f"<span style='color:#ebcb8b'>Range: {start_frame} - {end_frame}</span>",
        pos='topCenter',
        fade=True,
        fadeStayTime=2000
    )


def _apply_link_at_frame(frame, set_key=True):
    """
    Internal function to apply link at a specific frame.
    
    Args:
        frame: The frame to apply the link at
        set_key: Whether to set keyframes
    """
    global _state
    
    main_obj = _state.relative_data.get("main_obj")
    relative_matrices = _state.relative_data.get("relative_matrices", {})
    
    if not main_obj or not cmds.objExists(main_obj):
        return False
    
    follow_objs = list(relative_matrices.keys())
    
    cmds.currentTime(frame)
    
    for follow_obj in follow_objs:
        if not cmds.objExists(follow_obj):
            continue
        
        if follow_obj in relative_matrices:
            relative_matrix_list = relative_matrices[follow_obj]
            relative_matrix = om.MMatrix()
            
            for i in range(4):
                for j in range(4):
                    relative_matrix.setElement(i, j, relative_matrix_list[i * 4 + j])
            
            main_matrix = cmds.xform(main_obj, query=True, matrix=True, worldSpace=True)
            main_mmatrix = om.MMatrix(main_matrix)
            
            new_follow_matrix = relative_matrix * main_mmatrix
            new_follow_matrix_list = [
                new_follow_matrix.getElement(i, j) 
                for i in range(4) 
                for j in range(4)
            ]
            
            cmds.xform(follow_obj, matrix=new_follow_matrix_list, worldSpace=True)
            
            if set_key:
                cmds.setKeyframe(follow_obj, attribute='translate', t=frame)
                cmds.setKeyframe(follow_obj, attribute='rotate', t=frame)
                cmds.setKeyframe(follow_obj, attribute='scale', t=frame)
    
    return True


def paste_link_frame(*args):
    """
    Apply the saved spatial relationship at the current frame only.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.linkObjects.paste_link_frame"):
        return None
    global _state
    
    if not _state.relative_data:
        if not _load_relative_data():
            cmds.warning("AnimKey: No link data found. Run 'Copy Link' first.")
            return
    
    main_obj = _state.relative_data.get("main_obj")
    if not main_obj or not cmds.objExists(main_obj):
        cmds.warning(f"AnimKey: Main object '{main_obj}' not found in scene.")
        return
    
    current_frame = cmds.currentTime(query=True)
    
    cmds.undoInfo(openChunk=True)
    try:
        _apply_link_at_frame(current_frame, set_key=True)
        
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Link Pasted (Frame {int(current_frame)})</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=1500
        )
    finally:
        cmds.undoInfo(closeChunk=True)


def paste_link_playback_range(*args):
    """
    Apply the saved spatial relationship to all frames in the playback range.
    """
    global _state
    
    if not _state.relative_data:
        if not _load_relative_data():
            cmds.warning("AnimKey: No link data found. Run 'Copy Link' first.")
            return
    
    main_obj = _state.relative_data.get("main_obj")
    if not main_obj or not cmds.objExists(main_obj):
        cmds.warning(f"AnimKey: Main object '{main_obj}' not found in scene.")
        return
    
    # Get playback range
    start_frame = int(cmds.playbackOptions(query=True, minTime=True))
    end_frame = int(cmds.playbackOptions(query=True, maxTime=True))
    
    frames = list(range(start_frame, end_frame + 1))
    
    cmds.undoInfo(openChunk=True)
    try:
        for frame in frames:
            _apply_link_at_frame(frame, set_key=True)
        
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Link Pasted (Playback Range)</span><br>"
                f"<span style='color:#88c0d0'>{len(frames)} frame(s) processed</span><br>"
                f"<span style='color:#ebcb8b'>Range: {start_frame} - {end_frame}</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=2000
        )
    finally:
        cmds.undoInfo(closeChunk=True)


def paste_link(*args):
    """
    Apply the saved spatial relationship to objects.
    
    If a timeline range is selected, keyframes will be created for each frame.
    Otherwise, applies to the current frame only.
    """
    global _state
    
    # Load data if not already loaded
    if not _state.relative_data:
        if not _load_relative_data():
            cmds.warning("AnimKey: No link data found. Run 'Copy Link' first.")
            return
    
    main_obj = _state.relative_data.get("main_obj")
    relative_matrices = _state.relative_data.get("relative_matrices", {})
    
    if not main_obj or not cmds.objExists(main_obj):
        cmds.warning(f"AnimKey: Main object '{main_obj}' not found in scene.")
        return
    
    follow_objs = list(relative_matrices.keys())
    
    # Check for timeline range selection
    playback_range = (
        cmds.playbackOptions(query=True, minTime=True),
        cmds.playbackOptions(query=True, maxTime=True)
    )
    
    try:
        range_start, range_end = cmds.timeControl('timeControl1', q=True, rangeArray=True)
        
        if range_start != playback_range[0] or range_end != playback_range[1]:
            frames = list(range(int(range_start), int(range_end)))
        else:
            frames = [cmds.currentTime(query=True)]
    except:
        frames = [cmds.currentTime(query=True)]
    
    # Apply link for each frame
    cmds.undoInfo(openChunk=True)
    
    try:
        for frame in frames:
            _apply_link_at_frame(frame, set_key=True)
        
        frame_count = len(frames)
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Link Pasted</span><br>"
                f"<span style='color:#88c0d0'>{len(follow_objs)} object(s), {frame_count} frame(s)</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=1500
        )
    
    finally:
        cmds.undoInfo(closeChunk=True)


def paste_link_realtime():
    """
    Apply link in real-time (used by auto-link callback).
    Simplified version without timeline range check for speed.
    """
    global _state
    
    main_obj = _state.relative_data.get("main_obj")
    relative_matrices = _state.relative_data.get("relative_matrices", {})
    
    if not main_obj or not cmds.objExists(main_obj):
        return
    
    follow_objs = list(relative_matrices.keys())
    
    for follow_obj in follow_objs:
        if not cmds.objExists(follow_obj):
            continue
        
        if follow_obj in relative_matrices:
            relative_matrix_list = relative_matrices[follow_obj]
            relative_matrix = om.MMatrix()
            
            for i in range(4):
                for j in range(4):
                    relative_matrix.setElement(i, j, relative_matrix_list[i * 4 + j])
            
            main_matrix = cmds.xform(main_obj, query=True, matrix=True, worldSpace=True)
            main_mmatrix = om.MMatrix(main_matrix)
            
            new_follow_matrix = relative_matrix * main_mmatrix
            new_follow_matrix_list = [
                new_follow_matrix.getElement(i, j) 
                for i in range(4) 
                for j in range(4)
            ]
            
            cmds.xform(follow_obj, matrix=new_follow_matrix_list, worldSpace=True)


# ═══════════════════════════════════════════════════════════════════════════════
#                           AUTO-LINK CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

def _attribute_callback(msg, plug, otherPlug, clientData):
    """Callback for attribute changes on main object"""
    global _state
    
    if not _state.auto_link_enabled:
        return
    
    if msg & om.MNodeMessage.kAttributeSet:
        paste_link_realtime()


def _time_callback(*args):
    """Callback for time changes"""
    global _state
    
    if not _state.auto_link_enabled:
        return
    
    paste_link_realtime()


def enable_auto_link():
    """Enable auto-link mode"""
    global _state
    
    # Load data if needed
    if not _state.relative_data:
        if not _load_relative_data():
            cmds.warning("AnimKey: No link data found. Run 'Copy Link' first.")
            return False
    
    main_obj = _state.relative_data.get("main_obj")
    if not main_obj or not cmds.objExists(main_obj):
        cmds.warning(f"AnimKey: Main object '{main_obj}' not found.")
        return False
    
    # Get MObject for main object
    selection_list = om.MSelectionList()
    selection_list.add(main_obj)
    main_mobject = selection_list.getDependNode(0)
    
    # Add attribute callback
    _state.attribute_callback_id = om.MNodeMessage.addAttributeChangedCallback(
        main_mobject, _attribute_callback
    )
    
    # Add time callback
    _state.time_callback_id = om.MEventMessage.addEventCallback(
        'timeChanged', _time_callback
    )
    
    _state.auto_link_enabled = True
    return True


def disable_auto_link():
    """Disable auto-link mode"""
    global _state
    
    if _state.attribute_callback_id is not None:
        try:
            om.MMessage.removeCallback(_state.attribute_callback_id)
        except:
            pass
        _state.attribute_callback_id = None
    
    if _state.time_callback_id is not None:
        try:
            om.MMessage.removeCallback(_state.time_callback_id)
        except:
            pass
        _state.time_callback_id = None
    
    _state.auto_link_enabled = False


def toggle_auto_link(*args):
    """Toggle auto-link mode"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.linkObjects.toggle_auto_link"):
        return None
    global _state
    
    if _state.auto_link_enabled:
        disable_auto_link()
        cmds.inViewMessage(
            amg="<span style='color:#bf616a'>Auto-Link: OFF</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=1000
        )
    else:
        if enable_auto_link():
            cmds.inViewMessage(
                amg="<span style='color:#a3be8c'>Auto-Link: ON</span><br>"
                    "<span style='color:#88c0d0'>Objects will update in real-time</span>",
                pos='topCenter',
                fade=True,
                fadeStayTime=1500
            )


def is_auto_link_enabled():
    """Check if auto-link is enabled"""
    global _state
    return _state.auto_link_enabled


# ═══════════════════════════════════════════════════════════════════════════════
#                           EXECUTE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def execute(*args):
    """
    Main execute function for the LNK button.
    
    - Normal click: Copy Link
    - Shift+click: Paste Link
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.linkObjects.execute"):
        return None
    # Check for Shift modifier
    mods = mel.eval('getModifiers')
    shift_pressed = bool(mods % 2)
    
    if shift_pressed:
        paste_link()
    else:
        copy_link()


def set_button_active(button, active):
    """Set the button visual state for auto-link"""
    try:
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        
        if active:
            button.setStyleSheet(f'''
                QPushButton {{
                    color: white;
                    background-color: #b48ead;
                    border: 1px solid #b48ead;
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: #a07a9d;
                }}
            ''')
        else:
            button.setStyleSheet(f'''
                QPushButton {{
                    color: #b48ead;
                    background-color: {theme["button_bg"]};
                    border: 1px solid {theme["border_color"]};
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {theme["button_hover"]};
                    border-color: #b48ead;
                }}
            ''')
    except:
        pass


