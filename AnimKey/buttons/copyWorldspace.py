"""
    AnimKey Copy Worldspace
    
    Copy and paste animation in world space coordinates.
    This allows you to transfer animation between different hierarchies
    or parent spaces while maintaining the world position and rotation.
    
    Based on AnimKey's Copy Worldspace functionality.
    
    Features:
    - Copy worldspace animation (all keys)
    - Copy worldspace animation (selected range)
    - Copy worldspace current frame
    - Paste worldspace animation
    - Paste worldspace frame
    - Auto worldspace mode (real-time updates)
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

class WorldspaceState:
    """Class to maintain worldspace copy state"""
    
    def __init__(self):
        self.auto_enabled = False
        self.attribute_callback_id = None
        self.time_callback_id = None
        self.source_data = {}  # Stores current frame worldspace values
        self.button = None


_state = WorldspaceState()


# ═══════════════════════════════════════════════════════════════════════════════
#                           FILE PATHS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_worldspace_folder():
    """Get the folder for worldspace data storage"""
    user_folder = config.get_user_folder_path()
    return os.path.join(user_folder, "tools", "worldspace")


def _get_animation_data_file():
    """Get the path to the animation worldspace data file"""
    return os.path.join(_get_worldspace_folder(), "worldspace_animation.json")


def _get_frame_data_file():
    """Get the path to the single frame worldspace data file"""
    return os.path.join(_get_worldspace_folder(), "worldspace_frame.json")


def _get_selected_time_range():
    """Get the selected time range from the timeline"""
    try:
        aTimeSlider = mel.eval('$tmpVar=$gPlayBackSlider')
        time_range = cmds.timeControl(aTimeSlider, q=True, rangeArray=True)
        
        playback_min = cmds.playbackOptions(query=True, minTime=True)
        playback_max = cmds.playbackOptions(query=True, maxTime=True)
        
        # Check if a range is actually selected
        if time_range[0] != playback_min or time_range[1] != playback_max + 1:
            return (time_range[0], time_range[1] - 1)
    except:
        pass
    
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#                           COPY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def copy_worldspace_all_animation(*args):
    """
    Copy worldspace animation for all keyframes of selected objects.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyWorldspace.copy_worldspace_all_animation"):
        return None
    selected_objects = cmds.ls(selection=True)
    
    if not selected_objects:
        cmds.warning("AnimKey: No objects selected.")
        return
    
    # Check if objects have animation
    if not cmds.keyframe(selected_objects, query=True):
        cmds.warning("AnimKey: Selected objects do not have any animation.")
        return
    
    animation_data = {}
    original_time = cmds.currentTime(query=True)
    
    # Suspend viewport updates
    cmds.refresh(suspend=True)
    
    # Progress bar
    gMainProgressBar = mel.eval('$tmp = $gMainProgressBar')
    all_keyframes = sorted(list(set(cmds.keyframe(selected_objects, query=True))))
    total_frames = len(all_keyframes)
    
    cmds.progressBar(
        gMainProgressBar, 
        edit=True, 
        beginProgress=True, 
        isInterruptable=True, 
        status='Copying worldspace animation...', 
        maxValue=total_frames
    )
    
    try:
        for frame in all_keyframes:
            if cmds.progressBar(gMainProgressBar, query=True, isCancelled=True):
                break
            
            cmds.currentTime(frame)
            
            for obj in selected_objects:
                if cmds.keyframe(obj, query=True, time=(frame, frame)):
                    # Get worldspace values
                    translation = cmds.xform(obj, query=True, translation=True, worldSpace=True)
                    rotation = cmds.xform(obj, query=True, rotation=True, worldSpace=True)
                    worldspace_values = translation + rotation
                    
                    if obj not in animation_data:
                        animation_data[obj] = {}
                    
                    animation_data[obj][int(frame)] = worldspace_values
            
            cmds.progressBar(gMainProgressBar, edit=True, step=1)
        
        # Save to file
        folder = _get_worldspace_folder()
        os.makedirs(folder, exist_ok=True)
        
        with open(_get_animation_data_file(), 'w') as f:
            json.dump(animation_data, f, indent=2)
        
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Worldspace Animation Copied</span><br>"
                f"<span style='color:#88c0d0'>{len(selected_objects)} object(s), {total_frames} frame(s)</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=2000
        )
    
    finally:
        cmds.refresh(suspend=False)
        cmds.progressBar(gMainProgressBar, edit=True, endProgress=True)
        cmds.currentTime(original_time)


def copy_worldspace_selected_range(*args):
    """
    Copy worldspace animation for the selected time range only.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyWorldspace.copy_worldspace_selected_range"):
        return None
    selected_objects = cmds.ls(selection=True)
    
    if not selected_objects:
        cmds.warning("AnimKey: No objects selected.")
        return
    
    time_range = _get_selected_time_range()
    if time_range is None:
        cmds.warning("AnimKey: No time range selected on the timeline.")
        return
    
    # Check if objects have animation in range
    keyframes_in_range = cmds.keyframe(
        selected_objects, 
        query=True, 
        time=(time_range[0], time_range[1])
    )
    
    if not keyframes_in_range:
        cmds.warning("AnimKey: No keyframes in selected range.")
        return
    
    animation_data = {}
    original_time = cmds.currentTime(query=True)
    
    cmds.refresh(suspend=True)
    
    gMainProgressBar = mel.eval('$tmp = $gMainProgressBar')
    all_keyframes = sorted(list(set(keyframes_in_range)))
    total_frames = len(all_keyframes)
    
    cmds.progressBar(
        gMainProgressBar, 
        edit=True, 
        beginProgress=True, 
        isInterruptable=True, 
        status='Copying worldspace animation...', 
        maxValue=total_frames
    )
    
    try:
        for frame in all_keyframes:
            if cmds.progressBar(gMainProgressBar, query=True, isCancelled=True):
                break
            
            cmds.currentTime(frame)
            
            for obj in selected_objects:
                if cmds.keyframe(obj, query=True, time=(frame, frame)):
                    translation = cmds.xform(obj, query=True, translation=True, worldSpace=True)
                    rotation = cmds.xform(obj, query=True, rotation=True, worldSpace=True)
                    worldspace_values = translation + rotation
                    
                    if obj not in animation_data:
                        animation_data[obj] = {}
                    
                    animation_data[obj][int(frame)] = worldspace_values
            
            cmds.progressBar(gMainProgressBar, edit=True, step=1)
        
        folder = _get_worldspace_folder()
        os.makedirs(folder, exist_ok=True)
        
        with open(_get_animation_data_file(), 'w') as f:
            json.dump(animation_data, f, indent=2)
        
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Worldspace Animation Copied (Range)</span><br>"
                f"<span style='color:#88c0d0'>{len(selected_objects)} object(s), {total_frames} frame(s)</span><br>"
                f"<span style='color:#ebcb8b'>Range: {int(time_range[0])} - {int(time_range[1])}</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=2000
        )
    
    finally:
        cmds.refresh(suspend=False)
        cmds.progressBar(gMainProgressBar, edit=True, endProgress=True)
        cmds.currentTime(original_time)


def copy_worldspace_current_frame(*args):
    """
    Copy worldspace values for the current frame only.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyWorldspace.copy_worldspace_current_frame"):
        return None
    selected_objects = cmds.ls(selection=True)
    
    if not selected_objects:
        cmds.warning("AnimKey: No objects selected.")
        return
    
    animation_data = {}
    current_time = int(cmds.currentTime(query=True))
    
    for obj in selected_objects:
        translation = cmds.xform(obj, query=True, translation=True, worldSpace=True)
        rotation = cmds.xform(obj, query=True, rotation=True, worldSpace=True)
        worldspace_values = translation + rotation
        
        animation_data[obj] = {current_time: worldspace_values}
    
    folder = _get_worldspace_folder()
    os.makedirs(folder, exist_ok=True)
    
    with open(_get_frame_data_file(), 'w') as f:
        json.dump(animation_data, f, indent=2)
    
    # Also store for auto worldspace
    _state.source_data = animation_data
    
    cmds.inViewMessage(
        amg=f"<span style='color:#a3be8c'>Worldspace Frame Copied</span><br>"
            f"<span style='color:#88c0d0'>{len(selected_objects)} object(s) at frame {current_time}</span>",
        pos='topCenter',
        fade=True,
        fadeStayTime=1500
    )


# ═══════════════════════════════════════════════════════════════════════════════
#                           PASTE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def paste_worldspace_animation(*args):
    """
    Paste worldspace animation to objects.
    If objects are selected, paste to them (1:1 mapping with source order).
    Otherwise, paste to original source objects.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyWorldspace.paste_worldspace_animation"):
        return None
    data_file = _get_animation_data_file()
    
    if not os.path.exists(data_file):
        cmds.warning("AnimKey: No worldspace animation data found. Copy first.")
        return
    
    with open(data_file, 'r') as f:
        animation_data = json.load(f)
    
    if not animation_data:
        cmds.warning("AnimKey: No data in worldspace file.")
        return
    
    # Get source objects and their data (maintaining order)
    source_objects = list(animation_data.keys())
    
    # Check if user has selected objects to paste to
    selected_objects = cmds.ls(selection=True)
    
    if selected_objects:
        # Use selected objects as targets (1:1 mapping)
        if len(selected_objects) != len(source_objects):
            cmds.warning(f"AnimKey: Selection count ({len(selected_objects)}) doesn't match source count ({len(source_objects)}). Using first source for all targets.")
            # Apply first source's data to all selected objects
            first_source = source_objects[0]
            target_mapping = {obj: animation_data[first_source] for obj in selected_objects}
        else:
            # 1:1 mapping
            target_mapping = {selected_objects[i]: animation_data[source_objects[i]] for i in range(len(selected_objects))}
    else:
        # No selection - use original objects (existing behavior)
        target_mapping = {obj: data for obj, data in animation_data.items() if cmds.objExists(obj)}
        
        if not target_mapping:
            cmds.warning("AnimKey: No valid objects found in scene. Select target objects.")
            return
    
    original_time = cmds.currentTime(query=True)
    
    # Remove existing animation from target objects
    for obj in target_mapping.keys():
        try:
            cmds.cutKey(obj, attribute=['tx', 'ty', 'tz', 'rx', 'ry', 'rz'])
        except:
            pass
    
    # Get all unique frames from all source data
    all_frames = sorted(
        set(int(frame) for obj_data in target_mapping.values() for frame in obj_data.keys())
    )
    
    cmds.refresh(suspend=True)
    
    gMainProgressBar = mel.eval('$tmp = $gMainProgressBar')
    cmds.progressBar(
        gMainProgressBar, 
        edit=True, 
        beginProgress=True, 
        isInterruptable=True, 
        status='Pasting worldspace animation...', 
        maxValue=len(all_frames)
    )
    
    cmds.undoInfo(openChunk=True)
    
    try:
        for frame in all_frames:
            if cmds.progressBar(gMainProgressBar, query=True, isCancelled=True):
                break
            
            cmds.currentTime(frame)
            
            for obj, obj_data in target_mapping.items():
                str_frame = str(frame)
                if str_frame in obj_data:
                    values = obj_data[str_frame]
                    try:
                        cmds.xform(obj, translation=values[:3], worldSpace=True)
                        cmds.xform(obj, rotation=values[3:], worldSpace=True)
                        cmds.setKeyframe(obj, attribute=['tx', 'ty', 'tz', 'rx', 'ry', 'rz'])
                    except Exception as e:
                        cmds.warning(f"AnimKey: Could not apply to {obj}: {e}")
            
            cmds.progressBar(gMainProgressBar, edit=True, step=1)
        
        # Filter curves
        try:
            cmds.filterCurve(list(target_mapping.keys()))
        except:
            pass
        
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Worldspace Animation Pasted</span><br>"
                f"<span style='color:#88c0d0'>{len(target_mapping)} object(s), {len(all_frames)} frame(s)</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=2000
        )
    
    finally:
        cmds.undoInfo(closeChunk=True)
        cmds.refresh(suspend=False)
        cmds.progressBar(gMainProgressBar, edit=True, endProgress=True)
        cmds.currentTime(original_time)


def paste_worldspace_current_frame(*args):
    """
    Paste worldspace values at the current frame.
    If objects are selected, paste to them (1:1 mapping with source order).
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyWorldspace.paste_worldspace_current_frame"):
        return None
    data_file = _get_frame_data_file()
    
    if not os.path.exists(data_file):
        cmds.warning("AnimKey: No worldspace frame data found. Copy first.")
        return
    
    with open(data_file, 'r') as f:
        animation_data = json.load(f)
    
    if not animation_data:
        cmds.warning("AnimKey: No data in worldspace file.")
        return
    
    # Get source objects and their data (maintaining order)
    source_objects = list(animation_data.keys())
    
    # Check if user has selected objects to paste to
    selected_objects = cmds.ls(selection=True)
    
    cmds.undoInfo(openChunk=True)
    
    try:
        pasted_count = 0
        
        if selected_objects:
            # Use selected objects as targets
            if len(selected_objects) != len(source_objects):
                # Apply first source's data to all selected objects
                first_source = source_objects[0]
                source_data = animation_data[first_source]
                first_frame = next(iter(source_data))
                values = source_data[first_frame]
                
                for obj in selected_objects:
                    try:
                        cmds.xform(obj, translation=values[:3], worldSpace=True)
                        cmds.xform(obj, rotation=values[3:], worldSpace=True)
                        cmds.setKeyframe(obj, attribute=['tx', 'ty', 'tz', 'rx', 'ry', 'rz'])
                        pasted_count += 1
                    except Exception as e:
                        cmds.warning(f"AnimKey: Could not apply to {obj}: {e}")
            else:
                # 1:1 mapping
                for i, obj in enumerate(selected_objects):
                    source = source_objects[i]
                    source_data = animation_data[source]
                    first_frame = next(iter(source_data))
                    values = source_data[first_frame]
                    
                    try:
                        cmds.xform(obj, translation=values[:3], worldSpace=True)
                        cmds.xform(obj, rotation=values[3:], worldSpace=True)
                        cmds.setKeyframe(obj, attribute=['tx', 'ty', 'tz', 'rx', 'ry', 'rz'])
                        pasted_count += 1
                    except Exception as e:
                        cmds.warning(f"AnimKey: Could not apply to {obj}: {e}")
        else:
            # No selection - use original objects (existing behavior)
            for obj, obj_data in animation_data.items():
                if not cmds.objExists(obj):
                    continue
                
                first_frame = next(iter(obj_data))
                values = obj_data[first_frame]
                
                try:
                    cmds.xform(obj, translation=values[:3], worldSpace=True)
                    cmds.xform(obj, rotation=values[3:], worldSpace=True)
                    cmds.setKeyframe(obj, attribute=['tx', 'ty', 'tz', 'rx', 'ry', 'rz'])
                    pasted_count += 1
                except Exception as e:
                    cmds.warning(f"AnimKey: Could not apply to {obj}: {e}")
        
        if pasted_count == 0:
            cmds.warning("AnimKey: No objects could be pasted. Select target objects.")
            return
        
        current_frame = int(cmds.currentTime(query=True))
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Worldspace Frame Pasted</span><br>"
                f"<span style='color:#88c0d0'>{pasted_count} object(s) at frame {current_frame}</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=1500
        )
    
    finally:
        cmds.undoInfo(closeChunk=True)


# ═══════════════════════════════════════════════════════════════════════════════
#                           PLAYBACK RANGE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def copy_worldspace_playback_range(*args):
    """
    Copy worldspace animation for the playback range.
    """
    selected_objects = cmds.ls(selection=True)
    
    if not selected_objects:
        cmds.warning("AnimKey: No objects selected.")
        return
    
    # Get playback range
    start_time = cmds.playbackOptions(query=True, minTime=True)
    end_time = cmds.playbackOptions(query=True, maxTime=True)
    
    # Check if objects have animation in range
    keyframes_in_range = cmds.keyframe(
        selected_objects, 
        query=True, 
        time=(start_time, end_time)
    )
    
    if not keyframes_in_range:
        cmds.warning("AnimKey: No keyframes in playback range.")
        return
    
    animation_data = {}
    original_time = cmds.currentTime(query=True)
    
    cmds.refresh(suspend=True)
    
    gMainProgressBar = mel.eval('$tmp = $gMainProgressBar')
    all_keyframes = sorted(list(set(keyframes_in_range)))
    total_frames = len(all_keyframes)
    
    cmds.progressBar(
        gMainProgressBar, 
        edit=True, 
        beginProgress=True, 
        isInterruptable=True, 
        status='Copying worldspace animation...', 
        maxValue=total_frames
    )
    
    try:
        for frame in all_keyframes:
            if cmds.progressBar(gMainProgressBar, query=True, isCancelled=True):
                break
            
            cmds.currentTime(frame)
            
            for obj in selected_objects:
                if cmds.keyframe(obj, query=True, time=(frame, frame)):
                    # Get worldspace values
                    translation = cmds.xform(obj, query=True, translation=True, worldSpace=True)
                    rotation = cmds.xform(obj, query=True, rotation=True, worldSpace=True)
                    worldspace_values = translation + rotation
                    
                    if obj not in animation_data:
                        animation_data[obj] = {}
                    
                    animation_data[obj][int(frame)] = worldspace_values
            
            cmds.progressBar(gMainProgressBar, edit=True, step=1)
        
        folder = _get_worldspace_folder()
        os.makedirs(folder, exist_ok=True)
        
        with open(_get_animation_data_file(), 'w') as f:
            json.dump(animation_data, f, indent=2)
        
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Worldspace Animation Copied (Playback)</span><br>"
                f"<span style='color:#88c0d0'>{len(selected_objects)} object(s), {total_frames} frame(s)</span><br>"
                f"<span style='color:#ebcb8b'>Range: {int(start_time)} - {int(end_time)}</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=2000
        )
    
    finally:
        cmds.refresh(suspend=False)
        cmds.progressBar(gMainProgressBar, edit=True, endProgress=True)
        cmds.currentTime(original_time)


def paste_worldspace_playback_range(*args):
    """
    Paste worldspace animation within the playback range.
    Only pastes keys that fall within the current playback range.
    """
    data_file = _get_animation_data_file()
    
    if not os.path.exists(data_file):
        cmds.warning("AnimKey: No worldspace animation data found. Copy first.")
        return
    
    with open(data_file, 'r') as f:
        animation_data = json.load(f)
    
    if not animation_data:
        cmds.warning("AnimKey: No data in worldspace file.")
        return
    
    # Get source objects
    source_objects = list(animation_data.keys())
    
    # Check selection
    selected_objects = cmds.ls(selection=True)
    
    if selected_objects:
        if len(selected_objects) != len(source_objects):
            # Apply first source's data to all selected objects
            first_source = source_objects[0]
            target_mapping = {obj: animation_data[first_source] for obj in selected_objects}
        else:
            # 1:1 mapping
            target_mapping = {selected_objects[i]: animation_data[source_objects[i]] for i in range(len(selected_objects))}
    else:
        # No selection - use original objects (existing behavior)
        target_mapping = {obj: data for obj, data in animation_data.items() if cmds.objExists(obj)}
        
        if not target_mapping:
            cmds.warning("AnimKey: No valid objects found in scene. Select target objects.")
            return

    # Get playback range
    start_time = cmds.playbackOptions(query=True, minTime=True)
    end_time = cmds.playbackOptions(query=True, maxTime=True)

    # Get all unique frames from all source data that are WITHIN range
    all_frames = sorted(
        set(
            int(frame) 
            for obj_data in target_mapping.values() 
            for frame in obj_data.keys() 
            if start_time <= int(frame) <= end_time
        )
    )
    
    if not all_frames:
        cmds.warning(f"AnimKey: No copied keys found within playback range ({int(start_time)}-{int(end_time)}).")
        return

    original_time = cmds.currentTime(query=True)
    
    # Suspend viewport updates
    cmds.refresh(suspend=True)
    
    gMainProgressBar = mel.eval('$tmp = $gMainProgressBar')
    cmds.progressBar(
        gMainProgressBar, 
        edit=True, 
        beginProgress=True, 
        isInterruptable=True, 
        status='Pasting worldspace (Range)...', 
        maxValue=len(all_frames)
    )
    
    cmds.undoInfo(openChunk=True)
    
    try:
        for frame in all_frames:
            if cmds.progressBar(gMainProgressBar, query=True, isCancelled=True):
                break
            
            cmds.currentTime(frame)
            
            for obj, obj_data in target_mapping.items():
                str_frame = str(frame)
                if str_frame in obj_data:
                    values = obj_data[str_frame]
                    try:
                        cmds.xform(obj, translation=values[:3], worldSpace=True)
                        cmds.xform(obj, rotation=values[3:], worldSpace=True)
                        cmds.setKeyframe(obj, attribute=['tx', 'ty', 'tz', 'rx', 'ry', 'rz'])
                    except Exception as e:
                        # cmds.warning(f"AnimKey: Could not apply to {obj}: {e}")
                        pass
            
            cmds.progressBar(gMainProgressBar, edit=True, step=1)
        
        # Filter curves
        try:
            cmds.filterCurve(list(target_mapping.keys()))
        except:
            pass
            
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Worldspace Pasted (Playback)</span><br>"
                f"<span style='color:#88c0d0'>{len(target_mapping)} object(s), {len(all_frames)} frame(s)</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=2000
        )
        
    finally:
        cmds.undoInfo(closeChunk=True)
        cmds.refresh(suspend=False)
        cmds.progressBar(gMainProgressBar, edit=True, endProgress=True)
        cmds.currentTime(original_time)


# ═══════════════════════════════════════════════════════════════════════════════
#                           EXECUTE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def execute(*args):
    """
    Main execute function for the WS button.
    Only triggered explicitly, no modifier shortcuts here.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyWorldspace.execute"):
        return None
    copy_worldspace_current_frame()





