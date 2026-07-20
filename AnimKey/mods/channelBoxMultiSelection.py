"""
Channel Box multi-selection helper for AnimKey.

Lets Maya's Channel Box show/edit all selected objects instead of only the last
selected one.
"""

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods import configMod as config


SETTING_KEY = "channel_box_multi_selection_helper"
CHANNEL_BOX_NAME = "mainChannelBox"
CONNECTION_NAME = "AnimKeyChannelBoxMultiSelectionConnection"

_script_jobs = []
_active = False
_pending = False
_refreshing = False
_original_main_list_connection = None
_original_fixed_attr_list = None
_last_objects = None
_last_attr_list = None
_warned = False


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


def _channel_box_exists():
    try:
        return bool(cmds.channelBox(_channel_box_name(), query=True, exists=True))
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


def _ensure_connection():
    try:
        if not cmds.selectionConnection(CONNECTION_NAME, query=True, exists=True):
            cmds.selectionConnection(CONNECTION_NAME)
        return CONNECTION_NAME
    except Exception:
        try:
            return cmds.selectionConnection()
        except Exception:
            return None


def _fill_connection(connection_name, objects):
    if not connection_name:
        return False
    try:
        cmds.selectionConnection(connection_name, edit=True, clear=True)
        for obj in objects:
            cmds.selectionConnection(connection_name, edit=True, select=obj)
        return True
    except Exception:
        return False


def _dedupe(items):
    result = []
    seen = set()
    for item in items or []:
        if not item or item in seen:
            continue
        result.append(item)
        seen.add(item)
    return result


def _visible_attrs_for_node(node):
    attrs = []
    try:
        attrs.extend(cmds.listAttr(node, keyable=True, scalar=True) or [])
    except Exception:
        pass
    try:
        attrs.extend(cmds.listAttr(node, channelBox=True, scalar=True) or [])
    except Exception:
        pass
    return _dedupe(attrs)


def _aggregate_visible_attrs(objects):
    attrs = []
    seen = set()
    preferred_order = (
        "translateX", "translateY", "translateZ",
        "rotateX", "rotateY", "rotateZ",
        "scaleX", "scaleY", "scaleZ",
        "visibility",
    )

    per_node_attrs = []
    for obj in objects:
        node_attrs = _visible_attrs_for_node(obj)
        per_node_attrs.append(node_attrs)

    for attr in preferred_order:
        if any(attr in node_attrs for node_attrs in per_node_attrs):
            attrs.append(attr)
            seen.add(attr)

    for node_attrs in per_node_attrs:
        for attr in node_attrs:
            if attr not in seen:
                attrs.append(attr)
                seen.add(attr)
    return attrs


def _query_selected_main_attrs(channel_box):
    try:
        return cmds.channelBox(channel_box, query=True, selectedMainAttributes=True) or []
    except Exception:
        return []


def _set_fixed_attr_list(channel_box, attrs):
    try:
        cmds.channelBox(channel_box, edit=True, fixedAttrList=attrs or [])
        return True
    except Exception:
        return False


def _restore_selected_main_attrs(channel_box, selected_attrs, available_attrs):
    if not selected_attrs:
        return
    available = set(available_attrs or [])
    for attr in selected_attrs:
        if available and attr not in available:
            continue
        try:
            cmds.channelBox(channel_box, edit=True, select=attr)
        except Exception:
            pass


def _set_channel_box_objects(objects):
    global _warned, _last_attr_list

    if not _channel_box_exists():
        return False

    channel_box = _channel_box_name()
    selected_attrs = _query_selected_main_attrs(channel_box)
    attr_list = _aggregate_visible_attrs(objects)

    connection_name = _ensure_connection()
    if _fill_connection(connection_name, objects):
        try:
            cmds.channelBox(channel_box, edit=True, mainListConnection=connection_name)
            _set_fixed_attr_list(channel_box, attr_list)
            try:
                cmds.channelBox(channel_box, edit=True, update=True)
            except Exception:
                pass
            _restore_selected_main_attrs(channel_box, selected_attrs, attr_list)
            _last_attr_list = list(attr_list)
            return True
        except Exception:
            pass

    if not _warned:
        _warned = True
        cmds.warning("AnimKey: Could not connect Channel Box Multi Selection Helper.")
    return False


def refresh():
    global _pending, _refreshing, _last_objects, _last_attr_list

    _pending = False
    if not _active or _refreshing:
        return

    _refreshing = True
    try:
        objects = _selected_objects()
        attr_list = _aggregate_visible_attrs(objects)
        if objects == _last_objects and attr_list == _last_attr_list:
            return
        if _set_channel_box_objects(objects):
            _last_objects = list(objects)
            _last_attr_list = list(attr_list)
    finally:
        _refreshing = False


def refresh_deferred():
    global _pending

    if not _active or _pending:
        return
    _pending = True
    try:
        cmds.evalDeferred(
            "from AnimKey.mods import channelBoxMultiSelection; channelBoxMultiSelection.refresh()",
            lowestPriority=True,
        )
    except TypeError:
        cmds.evalDeferred(
            "from AnimKey.mods import channelBoxMultiSelection; channelBoxMultiSelection.refresh()"
        )
    except Exception:
        _pending = False


def _install_script_jobs():
    _kill_script_jobs()
    for event_name in ("SelectionChanged", "Undo", "Redo"):
        try:
            _script_jobs.append(
                cmds.scriptJob(event=(event_name, refresh_deferred), protected=True)
            )
        except Exception:
            pass


def _kill_script_jobs():
    global _script_jobs
    for job in list(_script_jobs):
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
    _script_jobs = []


def _remember_original_channel_box_state():
    global _original_main_list_connection, _original_fixed_attr_list
    if _original_main_list_connection is not None or not _channel_box_exists():
        return
    channel_box = _channel_box_name()
    try:
        connection = cmds.channelBox(
            channel_box,
            query=True,
            mainListConnection=True,
        )
        if isinstance(connection, (list, tuple)):
            connection = connection[0] if connection else ""
        _original_main_list_connection = connection or ""
    except Exception:
        _original_main_list_connection = ""
    try:
        fixed_attrs = cmds.channelBox(channel_box, query=True, fixedAttrList=True)
        _original_fixed_attr_list = list(fixed_attrs or [])
    except Exception:
        _original_fixed_attr_list = []


def _restore_channel_box_state():
    global _original_main_list_connection, _original_fixed_attr_list, _last_objects, _last_attr_list

    if not _channel_box_exists():
        return

    channel_box = _channel_box_name()
    restored = False
    if _original_main_list_connection:
        try:
            cmds.channelBox(
                channel_box,
                edit=True,
                mainListConnection=_original_main_list_connection,
            )
            restored = True
        except Exception:
            restored = False

    if not restored:
        objects = _selected_objects()
        connection_name = _ensure_connection()
        if _fill_connection(connection_name, objects[-1:] if objects else []):
            try:
                cmds.channelBox(channel_box, edit=True, mainListConnection=connection_name)
            except Exception:
                pass

    _set_fixed_attr_list(channel_box, _original_fixed_attr_list or [])
    try:
        cmds.channelBox(channel_box, edit=True, update=True)
    except Exception:
        pass

    _original_main_list_connection = None
    _original_fixed_attr_list = None
    _last_objects = None
    _last_attr_list = None


def apply(enabled=None):
    global _active, _pending

    if enabled is None:
        enabled = is_enabled()

    enabled = bool(enabled)
    if enabled:
        _remember_original_channel_box_state()
        _active = True
        _install_script_jobs()
        refresh_deferred()
    else:
        was_active = _active
        _active = False
        _pending = False
        _kill_script_jobs()
        if was_active or _original_main_list_connection is not None or _last_objects is not None:
            _restore_channel_box_state()


def is_active():
    return _active
