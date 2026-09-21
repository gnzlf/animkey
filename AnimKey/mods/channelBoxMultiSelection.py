"""Keep multi-object Channel Box editing safe without replacing Maya's selection.

Maya already edits matching attributes on the active selection. The helper
limits the displayed channels to those shared by every selected object, but
never substitutes a private selectionConnection or reselects old channels.
"""

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods import configMod as config


SETTING_KEY = "channel_box_multi_selection_helper"
CHANNEL_BOX_NAME = "mainChannelBox"
LEGACY_CONNECTION_NAME = "AnimKeyChannelBoxMultiSelectionConnection"

_script_jobs = []
_active = False
_pending = False
_refreshing = False
_original_fixed_attr_list = None
_last_objects = None
_last_attr_list = None


def is_enabled():
    return bool(config.get_setting(SETTING_KEY, False))


def set_enabled(enabled):
    config.set_setting(SETTING_KEY, bool(enabled))
    apply(bool(enabled))


def _channel_box_name():
    try:
        name = mel.eval('global string $gChannelBoxName; $temp=$gChannelBoxName;')
        if name:
            return name
    except Exception:
        pass
    return CHANNEL_BOX_NAME


def _channel_box_exists(channel_box):
    try:
        return bool(cmds.channelBox(channel_box, exists=True))
    except Exception:
        return False


def _selected_objects():
    try:
        selection = cmds.ls(selection=True, long=True) or []
    except Exception:
        selection = []
    objects = []
    seen = set()
    for item in selection:
        node = item.split(".", 1)[0]
        if node in seen:
            continue
        try:
            if cmds.objExists(node):
                objects.append(node)
                seen.add(node)
        except Exception:
            pass
    return objects


def _visible_attrs_for_node(node):
    attrs = []
    for flags in ({"keyable": True, "scalar": True},
                  {"channelBox": True, "scalar": True}):
        try:
            attrs.extend(cmds.listAttr(node, **flags) or [])
        except Exception:
            pass
    return list(dict.fromkeys(attrs))


def _common_visible_attrs(objects):
    if not objects:
        return []
    first = _visible_attrs_for_node(objects[0])
    common = set(first)
    for obj in objects[1:]:
        common.intersection_update(_visible_attrs_for_node(obj))
    return [attr for attr in first if attr in common]


def _fixed_attrs(channel_box):
    try:
        return list(cmds.channelBox(
            channel_box, query=True, fixedAttrList=True
        ) or [])
    except Exception:
        return []


def _repair_legacy_connection(channel_box):
    """Release the private connection left by earlier AnimKey versions."""
    try:
        current = cmds.channelBox(
            channel_box, query=True, mainListConnection=True
        )
        if isinstance(current, (tuple, list)):
            current = current[0] if current else ""
        if current and str(current).startswith(LEGACY_CONNECTION_NAME):
            cmds.channelBox(channel_box, edit=True, mainListConnection="")
            return True
    except Exception:
        pass
    return False


def _remember_original_state(channel_box):
    global _original_fixed_attr_list
    repaired_legacy = _repair_legacy_connection(channel_box)
    if _original_fixed_attr_list is None:
        # The old helper's fixedAttrList cannot be treated as a user setting:
        # it is often the stale filter that makes the box appear frozen.
        _original_fixed_attr_list = (
            [] if repaired_legacy else _fixed_attrs(channel_box)
        )
    if repaired_legacy:
        cmds.channelBox(
            channel_box, edit=True,
            fixedAttrList=_original_fixed_attr_list
        )
        cmds.channelBox(channel_box, edit=True, update=True)


def refresh():
    global _pending, _refreshing, _last_objects, _last_attr_list
    _pending = False
    if not _active or _refreshing:
        return
    channel_box = _channel_box_name()
    if not _channel_box_exists(channel_box):
        return

    _refreshing = True
    try:
        _remember_original_state(channel_box)
        objects = _selected_objects()
        original = _original_fixed_attr_list or []
        if len(objects) > 1:
            attrs = _common_visible_attrs(objects)
            if original:
                allowed = set(original)
                attrs = [attr for attr in attrs if attr in allowed]
            desired = attrs or original
        else:
            desired = original

        # Do not reselect old attributes: that traps the Channel Box on a
        # previous channel and also confuses Time Slider key filtering.
        if _last_objects != objects or _fixed_attrs(channel_box) != desired:
            cmds.channelBox(channel_box, edit=True, fixedAttrList=desired)
            cmds.channelBox(channel_box, edit=True, update=True)
        _last_objects = list(objects)
        _last_attr_list = list(desired)
    finally:
        _refreshing = False


def refresh_deferred():
    global _pending
    if not _active or _pending:
        return
    _pending = True
    command = (
        "from AnimKey.mods import channelBoxMultiSelection; "
        "channelBoxMultiSelection.refresh()"
    )
    try:
        cmds.evalDeferred(command, lowestPriority=True)
    except TypeError:
        cmds.evalDeferred(command)
    except Exception:
        _pending = False


def _kill_script_jobs():
    global _script_jobs
    for job in _script_jobs:
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
    _script_jobs = []


def _install_script_jobs():
    _kill_script_jobs()
    for event_name in ("SelectionChanged", "Undo", "Redo"):
        try:
            _script_jobs.append(cmds.scriptJob(
                event=(event_name, refresh_deferred), protected=True
            ))
        except Exception:
            pass


def _restore_channel_box_state():
    global _original_fixed_attr_list, _last_objects, _last_attr_list
    channel_box = _channel_box_name()
    if _channel_box_exists(channel_box):
        repaired_legacy = _repair_legacy_connection(channel_box)
        if _original_fixed_attr_list is not None or repaired_legacy:
            try:
                cmds.channelBox(
                    channel_box, edit=True,
                    fixedAttrList=_original_fixed_attr_list or []
                )
                cmds.channelBox(channel_box, edit=True, update=True)
            except Exception:
                pass
    _original_fixed_attr_list = None
    _last_objects = None
    _last_attr_list = None


def apply(enabled=None):
    global _active, _pending
    if enabled is None:
        enabled = is_enabled()
    enabled = bool(enabled)
    if enabled:
        channel_box = _channel_box_name()
        if _channel_box_exists(channel_box):
            _remember_original_state(channel_box)
        if not _active:
            _active = True
            _install_script_jobs()
        refresh_deferred()
    else:
        _active = False
        _pending = False
        _kill_script_jobs()
        _restore_channel_box_state()


def is_active():
    return _active
