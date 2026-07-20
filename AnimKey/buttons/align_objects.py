"""
    AnimKey Button: Align Objects

    Aligns selected objects to the last selected object. The toolbar-facing
    function names stay stable for menus, hotkeys, and flash buttons.
"""

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods import configMod as config


_CHANNEL_GROUPS = (
    ("translate", ("translateX", "translateY", "translateZ")),
    ("rotate", ("rotateX", "rotateY", "rotateZ")),
    ("scale", ("scaleX", "scaleY", "scaleZ")),
)
_ALIGN_CHANNELS_KEY = "align_objects_channels"
_ALIGN_CHANNEL_NAMES = ("translate", "rotate", "scale")


def _default_align_channels():
    return {name: False for name in _ALIGN_CHANNEL_NAMES}


def get_align_channels():
    """Return persisted Align Objects channel check states."""
    stored = config.get_setting(_ALIGN_CHANNELS_KEY, _default_align_channels())
    if not isinstance(stored, dict):
        stored = {}
    return {
        name: bool(stored.get(name, False))
        for name in _ALIGN_CHANNEL_NAMES
    }


def set_align_channel(channel, enabled):
    """Persist one Align Objects channel checkbox."""
    if channel not in _ALIGN_CHANNEL_NAMES:
        return
    channels = get_align_channels()
    channels[channel] = bool(enabled)
    config.set_setting(_ALIGN_CHANNELS_KEY, channels)


def set_align_channels(translate=False, rotate=False, scale=False):
    channels = {
        "translate": bool(translate),
        "rotate": bool(rotate),
        "scale": bool(scale),
    }
    config.set_setting(_ALIGN_CHANNELS_KEY, channels)


def resolve_align_flags(channels=None):
    """Map checkbox state to matchTransform flags."""
    channels = channels or get_align_channels()
    selected_count = sum(1 for name in _ALIGN_CHANNEL_NAMES if channels.get(name))
    if selected_count == 0 or selected_count == len(_ALIGN_CHANNEL_NAMES):
        return True, True, True
    return (
        bool(channels.get("translate")),
        bool(channels.get("rotate")),
        bool(channels.get("scale")),
    )


def _safe_warning(message):
    try:
        cmds.warning(message)
    except Exception:
        print(message)


def _timeline_control():
    slider_global = "gPlayBackSlider"
    expressions = (
        "global string ${0}; ${0}".format(slider_global),
        "$animKeyTimelineControl=${}".format(slider_global),
    )
    for expression in expressions:
        try:
            slider = mel.eval(expression)
        except Exception:
            continue
        if slider:
            return slider
    return None


def get_time_range_selected():
    """Return the highlighted time range as [start, end], or None."""
    slider = _timeline_control()
    if not slider:
        return None

    try:
        if not cmds.timeControl(slider, query=True, rangeVisible=True):
            return None
    except Exception:
        pass

    try:
        raw_range = cmds.timeControl(slider, query=True, rangeArray=True) or []
    except Exception:
        return None

    if len(raw_range) < 2:
        return None

    start = int(round(float(raw_range[0])))
    end = int(round(float(raw_range[1])))
    if start == end:
        return None

    if start > end:
        start, end = end, start

    return [start, end]


def _selected_objects():
    try:
        selection = cmds.ls(selection=True, long=True) or []
    except Exception:
        selection = []

    if not selection:
        selection = cmds.ls(selection=True) or []

    return selection


def _split_selection(selection):
    if len(selection) < 2:
        return [], None
    return selection[:-1], selection[-1]


def _frame_list(range_pair):
    if not range_pair:
        return []
    start, end = range_pair
    return list(range(int(start), int(end) + 1))


def _match_transform(source, target, pos=True, rot=True, scl=False):
    flags = {
        "pos": bool(pos),
        "rot": bool(rot),
        "scl": bool(scl),
    }
    cmds.matchTransform(source, target, **flags)


def _keyable_channel_groups(node, pos=True, rot=True, scl=False):
    enabled = {
        "translate": bool(pos),
        "rotate": bool(rot),
        "scale": bool(scl),
    }

    groups = []
    for group_name, attrs in _CHANNEL_GROUPS:
        if not enabled.get(group_name):
            continue

        keyable_attrs = []
        for attr in attrs:
            plug = "{}.{}".format(node, attr)
            try:
                if cmds.getAttr(plug, lock=True):
                    continue
                if not cmds.getAttr(plug, settable=True):
                    continue
            except Exception:
                continue
            keyable_attrs.append(attr)

        if keyable_attrs:
            groups.append((group_name, keyable_attrs))

    return groups


def _set_alignment_keys(node, frame, pos=True, rot=True, scl=False):
    for group_name, attrs in _keyable_channel_groups(node, pos=pos, rot=rot, scl=scl):
        try:
            cmds.setKeyframe(node, attribute=group_name, time=frame)
            continue
        except Exception:
            pass

        for attr in attrs:
            try:
                cmds.setKeyframe(node, attribute=attr, time=frame)
            except Exception:
                pass


def _align_sources_once(sources, target, pos=True, rot=True, scl=False, key=False, frame=None):
    for source in sources:
        if source == target:
            continue
        try:
            _match_transform(source, target, pos=pos, rot=rot, scl=scl)
            if key and frame is not None:
                _set_alignment_keys(source, frame, pos=pos, rot=rot, scl=scl)
        except Exception as exc:
            print("AnimKey: Could not align {} to {}: {}".format(source, target, exc))


def _run_over_frames(sources, target, frames, pos=True, rot=True, scl=False):
    original_time = cmds.currentTime(query=True)
    try:
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            _align_sources_once(
                sources,
                target,
                pos=pos,
                rot=rot,
                scl=scl,
                key=True,
                frame=frame,
            )
    finally:
        cmds.currentTime(original_time, edit=True)


def _show_result(source_count, target, frame_count=None):
    if frame_count:
        text = (
            "<span style='color:#88c0d0'>Aligned {}</span><br>"
            "<span style='color:#d8dee9'>Target: {}</span><br>"
            "<span style='color:#a3be8c'>{} frame(s) keyed</span>"
        ).format(source_count, target, frame_count)
    else:
        text = (
            "<span style='color:#88c0d0'>Aligned {}</span><br>"
            "<span style='color:#d8dee9'>Target: {}</span>"
        ).format(source_count, target)

    try:
        cmds.inViewMessage(amg=text, pos="topCenter", fade=True, fadeStayTime=1200)
    except Exception:
        pass


def _execute_alignment(pos=True, rot=True, scl=False):
    selection = _selected_objects()
    sources, target = _split_selection(selection)

    if not sources or not target:
        _safe_warning("AnimKey: Please select at least two objects. The last selected object is the target.")
        return

    frames = _frame_list(get_time_range_selected())

    cmds.undoInfo(openChunk=True)
    try:
        if frames:
            cmds.refresh(suspend=True)
            try:
                _run_over_frames(sources, target, frames, pos=pos, rot=rot, scl=scl)
            finally:
                cmds.refresh(suspend=False)
            _show_result(len(sources), target, frame_count=len(frames))
        else:
            _align_sources_once(sources, target, pos=pos, rot=rot, scl=scl)
            _show_result(len(sources), target)
    finally:
        cmds.undoInfo(closeChunk=True)


def align_position(*args):
    """Align only translation to the last selected object."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.align_objects.align_position"):
        return None
    _execute_alignment(pos=True, rot=False, scl=False)


def align_translate(*args):
    """Align only translation to the last selected object."""
    align_position(*args)


def align_orientation(*args):
    """Align only rotation to the last selected object."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.align_objects.align_orientation"):
        return None
    _execute_alignment(pos=False, rot=True, scl=False)


def align_rotate(*args):
    """Align only rotation to the last selected object."""
    align_orientation(*args)


def align_scale(*args):
    """Align only scale to the last selected object."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.align_objects.align_scale"):
        return None
    _execute_alignment(pos=False, rot=False, scl=True)


def execute(*args, pos=None, rot=None, scl=None):
    """Align selected objects to the last selected object."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.align_objects.execute"):
        return None
    if pos is None and rot is None and scl is None:
        pos, rot, scl = resolve_align_flags()
    else:
        pos, rot, scl = bool(pos), bool(rot), bool(scl)
        if not (pos or rot or scl):
            pos = rot = scl = True
    _execute_alignment(pos=pos, rot=rot, scl=scl)


def get_info():
    """Return button information for the toolbar."""
    return {
        "name": "Align Objects",
        "tooltip": "Align selected objects to the last selected target.",
        "icon": "align.svg",
        "shortcut": None,
    }
