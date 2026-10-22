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

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

from AnimKey.mods import configMod as config
from AnimKey.mods.storage import atomic_write_json, read_json
from AnimKey.buttons import copyWorldspace as worldspace


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
        self.busy = False
        self.button = None


_state = LinkObjectsState()


# ═══════════════════════════════════════════════════════════════════════════════
#                           FILE PATHS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_link_data_folder():
    """Get the folder for link data storage"""
    user_folder = config.get_user_folder_path()
    return os.path.join(user_folder, "tools", "link_objects")


def _get_link_data_file(animation=False):
    """Get the path to the link data file"""
    return os.path.join(_get_link_data_folder(), "link_animation.json" if animation else "link_data.json")


def _load_relative_data(animation=None):
    """Load relative data from file"""
    global _state
    
    if animation is None:
        active = read_json(os.path.join(_get_link_data_folder(), "active.json"), default={})
        animation = bool(active.get("animation", False)) if isinstance(active, dict) else False
        if animation and not os.path.exists(_get_link_data_file(True)):
            animation = False
    file_path = _get_link_data_file(animation)
    
    if not os.path.exists(file_path):
        return False
    
    data = read_json(file_path, default=None, backup_corrupt=True)
    if not isinstance(data, dict):
        return False
    _state.relative_data = data
    return True


# ═══════════════════════════════════════════════════════════════════════════════
#                           COPY/PASTE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _matrix_values(matrix):
    return [float(value) for value in matrix]


def _allowed(action):
    from AnimKey.core.executionGuard import require_animkey_context
    return require_animkey_context("AnimKey.buttons.linkObjects." + action)


def _save_clipboard(data, animation):
    atomic_write_json(_get_link_data_file(animation), data, indent=2)
    atomic_write_json(os.path.join(_get_link_data_folder(), "active.json"),
                      {"animation": animation})
    _state.relative_data = data


def _copy_link_scope(scope):
    if not _allowed("copy_link_" + scope):
        return None
    nodes = worldspace._ordered_selection()
    if len(nodes) < 2:
        cmds.warning("AnimKey: Select followers first, then the parent object last.")
        return None
    current = cmds.currentTime(query=True)
    if scope == "frame":
        bounds = (current, current)
    elif scope == "selected_range":
        bounds = worldspace._get_selected_time_range()
        if bounds is None:
            cmds.warning("AnimKey: Highlight a time range in the timeline first.")
            return None
    else:
        bounds = worldspace._playback_range(animation=scope == "all_animation")
    times = worldspace._sample_times(*bounds, nodes=nodes)
    main = nodes[-1]
    followers = nodes[:-1]
    data = {"version": 2, "kind": "frame" if scope == "frame" else "animation",
            "main_obj": main, "range": list(bounds), "times": times,
            "relative_matrices": {}, "relative_samples": {node: {} for node in followers}}
    suspended = worldspace._suspend_refresh()
    was_busy = _state.busy
    _state.busy = True
    try:
        for frame in times:
            cmds.currentTime(frame)
            inverse = om.MMatrix(worldspace._world_matrix(main)).inverse()
            for node in followers:
                relative = _matrix_values(om.MMatrix(worldspace._world_matrix(node)) * inverse)
                data["relative_samples"][node][worldspace._time_key(frame)] = relative
                if node not in data["relative_matrices"]:
                    data["relative_matrices"][node] = relative
    finally:
        cmds.currentTime(current)
        _state.busy = was_busy
        worldspace._resume_refresh(suspended)
    _save_clipboard(data, animation=scope != "frame")
    worldspace._message("Parent relationship copied", "{} object(s), {} sample(s)".format(
        len(followers), len(times)))
    return data


def copy_link_frame(*args):
    return _copy_link_scope("frame")


def copy_link(*args):
    return copy_link_frame(*args)


def copy_link_all_animation(*args):
    return _copy_link_scope("all_animation")


def copy_link_selected_range(*args):
    return _copy_link_scope("selected_range")


def copy_link_playback_range(*args):
    return _copy_link_scope("playback_range")


def _apply_link_at_frame(frame, set_key=True, data=None):
    data = _state.relative_data if data is None else data
    main = data.get("main_obj")
    if not main or not cmds.objExists(main):
        return False
    cmds.currentTime(frame)
    main_matrix = om.MMatrix(worldspace._world_matrix(main))
    samples = data.get("relative_samples", {})
    static = data.get("relative_matrices", {})
    # Cache the driver before changing followers, and solve ancestors first.
    desired = {}
    for node, relative in static.items():
        if data.get("kind") == "animation":
            relative = samples.get(node, {}).get(worldspace._time_key(frame))
        if relative is not None and cmds.objExists(node):
            desired[node] = _matrix_values(om.MMatrix(relative) * main_matrix)
    for node in sorted(desired, key=lambda item: item.count("|")):
        cmds.xform(node, matrix=desired[node], worldSpace=True)
        if set_key:
            for attr in ("translateX", "translateY", "translateZ",
                         "rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ"):
                if cmds.getAttr(node + "." + attr, settable=True):
                    cmds.setKeyframe(node, attribute=attr, time=frame)
    return bool(desired)


def _paste_link_scope(scope):
    if not _allowed("paste_link_" + scope):
        return None
    bounds = None
    if scope == "selected_range":
        bounds = worldspace._get_selected_time_range()
        if bounds is None:
            cmds.warning("AnimKey: Highlight a time range in the timeline first.")
            return None
    if scope == "frame":
        data = read_json(_get_link_data_file(False), default=None)
        if not isinstance(data, dict):
            cmds.warning("AnimKey: Copy a Parent frame first.")
            return None
    else:
        if not _state.relative_data and not _load_relative_data():
            cmds.warning("AnimKey: No Parent data found. Copy Parent first.")
            return None
        data = _state.relative_data
    main = data.get("main_obj")
    if not main or not cmds.objExists(main):
        cmds.warning("AnimKey: Copied parent object is not in this scene.")
        return None
    current = cmds.currentTime(query=True)
    if scope == "frame":
        frames = [current]
    else:
        if bounds is None:
            bounds = worldspace._playback_range(animation=scope == "all_animation")
        if data.get("kind") == "animation":
            frames = sorted(float(value) for value in data.get("times", [])
                            if scope == "all_animation" or
                            bounds[0] - 1e-7 <= float(value) <= bounds[1] + 1e-7)
        else:
            nodes = [main] + list(data.get("relative_matrices", {}))
            frames = worldspace._sample_times(*bounds, nodes=nodes)
    if not frames:
        cmds.warning("AnimKey: No copied samples inside this range.")
        return 0
    suspended = worldspace._suspend_refresh()
    auto_key = cmds.autoKeyframe(query=True, state=True)
    was_busy = _state.busy
    _state.busy = True
    cmds.undoInfo(openChunk=True, chunkName="AnimKey Parent Paste")
    try:
        cmds.autoKeyframe(state=False)
        count = sum(bool(_apply_link_at_frame(frame, set_key=True, data=data)) for frame in frames)
    finally:
        try:
            cmds.currentTime(current)
            cmds.autoKeyframe(state=auto_key)
        finally:
            _state.busy = was_busy
            worldspace._resume_refresh(suspended)
            cmds.undoInfo(closeChunk=True)
    worldspace._message("Parent relationship pasted", "{} sample(s)".format(count))
    return count


def paste_link_frame(*args):
    return _paste_link_scope("frame")


def paste_link_all_animation(*args):
    return _paste_link_scope("all_animation")


def paste_link_selected_range(*args):
    return _paste_link_scope("selected_range")


def paste_link_playback_range(*args):
    return _paste_link_scope("playback_range")


def paste_link(*args):
    scope = "selected_range" if worldspace._get_selected_time_range() else "frame"
    return _paste_link_scope(scope)


def paste_link_realtime():
    if _state.busy:
        return
    _state.busy = True
    try:
        _apply_link_at_frame(cmds.currentTime(query=True), set_key=False)
    finally:
        _state.busy = False


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

