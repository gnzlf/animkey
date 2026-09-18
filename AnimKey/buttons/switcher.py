# -*- coding: utf-8 -*-
"""
AnimKey Switcher

Live FK/IK switching for controls that only expose an FK workflow. The complete
tool lives in this file so scene setups do not depend on external Animo modules.
"""

from __future__ import absolute_import, division, print_function

import json
import math
import re

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui

from AnimKey.mods.maya_compat import (
    QtCore, QtGui, QtWidgets, wrap_instance as wrapInstance,
)

from AnimKey.mods.uiMod import ContextPopupWindow


TOOL_NAME = "Switcher"
WINDOW_OBJECT = "AnimKey_Switcher"
SETUP_MARKER = "animKeySwitcherSetup"
SETUP_PREFIX = "animkey_temp_ik_"
ROOT_GROUP = "ANIMKEY_SWITCHER"
VERSION = 2

OPTION_RANGE_MODE = "AnimKeySwitcherRangeMode"

SOURCE_ATTRS = ("baseParent", "sourceTop", "sourceMid", "sourceEnd")
DRIVER_ATTRS = (
    "rootDriver",
    "ikDriver",
    "poleDriver",
    "tipDriver",
    "systemRoot",
)
KEY_ATTRS = ("tx", "ty", "tz", "rx", "ry", "rz")
SWITCH_ATTR = "fkIk"

_window = None


def get_maya_main_window():
    return wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget)


def _warning(message):
    cmds.warning("AnimKey Switcher: {0}".format(message))


def _short_name(node):
    return (node or "").split("|")[-1]


def _safe_name(node):
    name = _short_name(node).replace(":", "_")
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return name or "chain"


def _unique_scene_name(base_name):
    if not cmds.ls(base_name):
        return base_name
    index = 1
    while cmds.ls("{0}{1}".format(base_name, index)):
        index += 1
    return "{0}{1}".format(base_name, index)


def _long_name(node):
    matches = cmds.ls(node, long=True) or []
    return matches[0] if matches else node


def _existing(nodes):
    result = []
    seen = set()
    for node in nodes or []:
        if not node or not cmds.objExists(node):
            continue
        long_node = _long_name(node)
        if long_node not in seen:
            seen.add(long_node)
            result.append(long_node)
    return result


def _add_attr(node, name, attr_type="message", multi=False):
    if cmds.attributeQuery(name, node=node, exists=True):
        return
    kwargs = {"longName": name}
    if attr_type == "string":
        kwargs["dataType"] = "string"
    else:
        kwargs["attributeType"] = attr_type
    if multi:
        kwargs["multi"] = True
    cmds.addAttr(node, **kwargs)


def _set_string(node, attr, value):
    _add_attr(node, attr, "string")
    cmds.setAttr("{0}.{1}".format(node, attr), value or "", type="string")


def _get_string(node, attr, default=""):
    if not cmds.objExists(node) or not cmds.attributeQuery(attr, node=node, exists=True):
        return default
    try:
        return cmds.getAttr("{0}.{1}".format(node, attr)) or default
    except Exception:
        return default


def _connect_message(source, target, attr, multi=False):
    if not source or not cmds.objExists(source) or not cmds.objExists(target):
        return False
    _add_attr(target, attr, "message", multi=multi)
    destination = "{0}.{1}".format(target, attr)
    if multi:
        indices = cmds.getAttr(destination, multiIndices=True) or []
        destination = "{0}[{1}]".format(destination, max(indices) + 1 if indices else 0)
    try:
        cmds.connectAttr(source + ".message", destination, force=True)
        return True
    except Exception:
        return False


def _connected_node(node, attr):
    if not cmds.objExists(node) or not cmds.attributeQuery(attr, node=node, exists=True):
        return None
    try:
        connected = cmds.listConnections(
            "{0}.{1}".format(node, attr),
            source=True,
            destination=False,
        ) or []
    except Exception:
        connected = []
    return _long_name(connected[0]) if connected and cmds.objExists(connected[0]) else None


def _connected_nodes(node, attr):
    if not cmds.objExists(node) or not cmds.attributeQuery(attr, node=node, exists=True):
        return []
    try:
        connected = cmds.listConnections(
            "{0}.{1}".format(node, attr),
            source=True,
            destination=False,
        ) or []
    except Exception:
        connected = []
    return _existing(connected)


def _is_setup(node):
    if not node or not cmds.objExists(node):
        return False
    try:
        return (
            cmds.nodeType(node) == "network"
            and cmds.attributeQuery(SETUP_MARKER, node=node, exists=True)
            and bool(cmds.getAttr("{0}.{1}".format(node, SETUP_MARKER)))
        )
    except Exception:
        return False


def list_setups():
    return sorted(
        [node for node in (cmds.ls(type="network") or []) if _is_setup(node)],
        key=lambda node: _get_string(node, "displayName", node).lower(),
    )


def _setup_from_node(node):
    if not node or not cmds.objExists(node):
        return None
    if _is_setup(node):
        return node
    try:
        candidates = cmds.listConnections(
            node + ".message",
            source=False,
            destination=True,
            type="network",
        ) or []
    except Exception:
        candidates = []
    for candidate in candidates:
        if _is_setup(candidate):
            return candidate
    return None


def setups_from_selection(selection=None):
    selection = selection or (cmds.ls(selection=True, long=True) or [])
    result = []
    for node in selection:
        setup = _setup_from_node(node)
        if setup and setup not in result:
            result.append(setup)
    return result


def _setup_sources(setup):
    sources = [_connected_node(setup, attr) for attr in SOURCE_ATTRS]
    if all(sources[1:]):
        return sources

    try:
        paths = json.loads(_get_string(setup, "sourcePaths", "[]"))
    except Exception:
        paths = []
    if len(paths) == 4:
        resolved = [path if path and cmds.objExists(path) else None for path in paths]
        for index in range(4):
            sources[index] = sources[index] or resolved[index]
    return sources


def _setup_drivers(setup):
    return [_connected_node(setup, attr) for attr in DRIVER_ATTRS]


def _setup_version(setup):
    if not _is_setup(setup) or not cmds.attributeQuery("version", node=setup, exists=True):
        return 0
    try:
        return int(cmds.getAttr(setup + ".version"))
    except Exception:
        return 0


def _selected_controls():
    ordered = cmds.ls(orderedSelection=True, long=True) or []
    if not ordered:
        ordered = cmds.ls(selection=True, long=True) or []

    controls = []
    seen = set()
    for node in ordered:
        if not cmds.objExists(node):
            continue
        node_type = cmds.nodeType(node)
        if node_type not in ("transform", "joint"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if not parents:
                continue
            node = parents[0]
        node = _long_name(node)
        if node not in seen:
            seen.add(node)
            controls.append(node)
    return controls


def _timeline_range():
    if cmds.about(batch=True):
        return None
    try:
        slider = mel.eval("$tmpVar=$gPlayBackSlider")
        if not slider or not cmds.timeControl(slider, query=True, rangeVisible=True):
            return None
        raw = cmds.timeControl(slider, query=True, rangeArray=True) or []
        if len(raw) < 2:
            return None
        start = float(raw[0])
        end = float(raw[1]) - 1.0
        if end < start:
            return None
        return start, end
    except Exception:
        return None


def resolve_time_range(mode="auto"):
    if mode == "auto":
        selected = _timeline_range()
        if selected:
            return selected
    return (
        float(cmds.playbackOptions(query=True, minTime=True)),
        float(cmds.playbackOptions(query=True, maxTime=True)),
    )


def _world_matrix(node):
    return cmds.xform(node, query=True, worldSpace=True, matrix=True)


def _world_position(node):
    value = cmds.xform(node, query=True, worldSpace=True, rotatePivot=True)
    return om.MVector(float(value[0]), float(value[1]), float(value[2]))


def _vector_length(vector):
    return math.sqrt(vector.x * vector.x + vector.y * vector.y + vector.z * vector.z)


def _fallback_pole_direction(mid, chain_direction):
    matrix = _world_matrix(mid)
    axes = (
        om.MVector(matrix[0], matrix[1], matrix[2]),
        om.MVector(matrix[4], matrix[5], matrix[6]),
        om.MVector(matrix[8], matrix[9], matrix[10]),
    )
    usable = []
    for axis in axes:
        if _vector_length(axis) < 0.000001:
            continue
        axis.normalize()
        usable.append((abs(axis * chain_direction), axis))
    if usable:
        usable.sort(key=lambda item: item[0])
        direction = usable[0][1] - chain_direction * (usable[0][1] * chain_direction)
        if _vector_length(direction) > 0.000001:
            direction.normalize()
            return direction
    return om.MVector(0.0, 0.0, 1.0)


def _pole_position(top, mid, end, previous_direction=None):
    top_pos = _world_position(top)
    mid_pos = _world_position(mid)
    end_pos = _world_position(end)

    chain = end_pos - top_pos
    chain_length = _vector_length(chain)
    upper_length = _vector_length(mid_pos - top_pos)
    lower_length = _vector_length(end_pos - mid_pos)
    total_length = max(upper_length + lower_length, 0.001)

    if chain_length > 0.000001:
        chain_direction = chain / chain_length
        projection = top_pos + chain_direction * ((mid_pos - top_pos) * chain_direction)
        bend = mid_pos - projection
    else:
        chain_direction = om.MVector(1.0, 0.0, 0.0)
        bend = om.MVector()

    if _vector_length(bend) < total_length * 0.0001:
        direction = previous_direction or _fallback_pole_direction(mid, chain_direction)
    else:
        direction = bend.normal()

    if previous_direction is not None and (direction * previous_direction) < 0.0:
        direction *= -1.0

    distance = max(total_length * 0.75, 0.1)
    pole = mid_pos + direction * distance
    return [pole.x, pole.y, pole.z], direction, total_length


def _locator_scale(controls):
    lengths = []
    for first, second in zip(controls[:-1], controls[1:]):
        lengths.append(_vector_length(_world_position(second) - _world_position(first)))
    chain_length = sum(lengths)
    return max(chain_length * 0.06, 0.1)


def _style_locator(locator, scale, color):
    shapes = cmds.listRelatives(locator, shapes=True, fullPath=True) or []
    for shape in shapes:
        for axis in "XYZ":
            try:
                cmds.setAttr("{0}.localScale{1}".format(shape, axis), scale)
            except Exception:
                pass
        try:
            cmds.setAttr(shape + ".overrideEnabled", 1)
            cmds.setAttr(shape + ".overrideRGBColors", 1)
            cmds.setAttr(shape + ".overrideColorRGB", color[0], color[1], color[2])
        except Exception:
            pass


def _style_curve(control, color, line_width=2.0):
    shapes = cmds.listRelatives(control, shapes=True, fullPath=True) or []
    for shape in shapes:
        try:
            cmds.setAttr(shape + ".overrideEnabled", 1)
            cmds.setAttr(shape + ".overrideRGBColors", 1)
            cmds.setAttr(shape + ".overrideColorRGB", color[0], color[1], color[2])
        except Exception:
            pass
        if cmds.attributeQuery("lineWidth", node=shape, exists=True):
            try:
                cmds.setAttr(shape + ".lineWidth", line_width)
            except Exception:
                pass


def _lock_channels(node, attrs):
    for attr in attrs:
        try:
            cmds.setAttr(
                "{0}.{1}".format(node, attr),
                lock=True,
                keyable=False,
                channelBox=False,
            )
        except Exception:
            pass


def _set_world_translation(node, position):
    cmds.xform(
        node,
        worldSpace=True,
        translation=(float(position[0]), float(position[1]), float(position[2])),
    )


def _set_world_rotation(node, rotation):
    cmds.xform(
        node,
        worldSpace=True,
        rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
    )


def _key_channels(node, attrs, time_value):
    valid = []
    for attr in attrs:
        plug = "{0}.{1}".format(node, attr)
        if not cmds.objExists(plug):
            continue
        try:
            if cmds.getAttr(plug, lock=True):
                continue
        except Exception:
            continue
        valid.append(attr)
    if valid:
        cmds.setKeyframe(node, time=time_value, attribute=valid)
    return valid


def _get_or_create_root():
    if cmds.objExists(ROOT_GROUP):
        if cmds.nodeType(ROOT_GROUP) == "transform":
            return ROOT_GROUP
        return cmds.group(empty=True, name=ROOT_GROUP + "_GRP")

    root = cmds.group(empty=True, name=ROOT_GROUP)
    try:
        cmds.setAttr(root + ".useOutlinerColor", True)
        cmds.setAttr(root + ".outlinerColor", 0.25, 0.62, 0.72)
    except Exception:
        pass
    _lock_channels(
        root,
        ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"),
    )
    return root


def _create_setup_node(base_parent, top, mid, end, start, finish):
    base_name = _safe_name(end)
    setup = cmds.createNode("network", name=SETUP_PREFIX + base_name)
    _add_attr(setup, SETUP_MARKER, "bool")
    cmds.setAttr("{0}.{1}".format(setup, SETUP_MARKER), True)
    _add_attr(setup, "version", "long")
    cmds.setAttr(setup + ".version", VERSION)
    _add_attr(setup, "startFrame", "double")
    _add_attr(setup, "endFrame", "double")
    cmds.setAttr(setup + ".startFrame", float(start))
    cmds.setAttr(setup + ".endFrame", float(finish))
    _set_string(setup, "displayName", "{0}  |  {1}".format(_short_name(top), _short_name(end)))
    _set_string(setup, "sourcePaths", json.dumps([base_parent, top, mid, end]))

    for attr, node in zip(SOURCE_ATTRS, (base_parent, top, mid, end)):
        if node:
            _connect_message(node, setup, attr)
    return setup


def _record_constraint(setup, constraint):
    if isinstance(constraint, (list, tuple)):
        constraint = constraint[0] if constraint else None
    if constraint and cmds.objExists(constraint):
        _connect_message(constraint, setup, "constraints", multi=True)
    return constraint


def _record_driver(setup, node, attr):
    _connect_message(node, setup, attr)
    return node


def _locked_axes(node, prefix):
    locked = []
    for axis in "xyz":
        plug = "{0}.{1}{2}".format(node, prefix, axis)
        try:
            if not cmds.objExists(plug) or cmds.getAttr(plug, lock=True):
                locked.append(axis)
        except Exception:
            locked.append(axis)
    return locked


def _blend_weight_attrs(node):
    attrs = cmds.listAttr(node, userDefined=True) or []
    prefixes = ("blendParent", "blendOrient", "blendPoint")
    return [attr for attr in attrs if attr.startswith(prefixes)]


def _ensure_animatable_input(control):
    try:
        if (cmds.keyframe(control, query=True, keyframeCount=True) or 0) > 0:
            return
    except Exception:
        pass
    _key_channels(control, KEY_ATTRS, cmds.currentTime(query=True))


def _new_blend_plug(control, before_attrs, before_pair_blends):
    after_attrs = _blend_weight_attrs(control)
    new_attrs = [attr for attr in after_attrs if attr not in before_attrs]
    if new_attrs:
        return "{0}.{1}".format(control, new_attrs[-1])

    pair_blends = set(cmds.listConnections(control, type="pairBlend") or [])
    new_pair_blends = [node for node in pair_blends if node not in before_pair_blends]
    for pair_blend in new_pair_blends:
        sources = cmds.listConnections(
            pair_blend + ".weight",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        if sources and sources[0].split(".")[-1] in after_attrs:
            return sources[0]

    for attr in reversed(after_attrs):
        plug = "{0}.{1}".format(control, attr)
        destinations = cmds.listConnections(
            plug,
            source=False,
            destination=True,
            type="pairBlend",
        ) or []
        if destinations:
            return plug
    return None


def _drive_control(setup, driver, control):
    _ensure_animatable_input(control)
    before_attrs = set(_blend_weight_attrs(control))
    before_pair_blends = set(cmds.listConnections(control, type="pairBlend") or [])
    skip_translate = _locked_axes(control, "t")
    skip_rotate = _locked_axes(control, "r")
    if len(skip_translate) == 3 and len(skip_rotate) == 3:
        raise RuntimeError("{0} has no editable translate or rotate channels".format(_short_name(control)))

    if len(skip_translate) == 3:
        kwargs = {"maintainOffset": True}
        if skip_rotate:
            kwargs["skip"] = skip_rotate
        constraint = cmds.orientConstraint(driver, control, **kwargs)[0]
    elif len(skip_rotate) == 3:
        kwargs = {"maintainOffset": True}
        if skip_translate:
            kwargs["skip"] = skip_translate
        constraint = cmds.pointConstraint(driver, control, **kwargs)[0]
    else:
        kwargs = {"maintainOffset": True}
        if skip_translate:
            kwargs["skipTranslate"] = skip_translate
        if skip_rotate:
            kwargs["skipRotate"] = skip_rotate
        constraint = cmds.parentConstraint(driver, control, **kwargs)[0]
    _record_constraint(setup, constraint)
    blend_plug = _new_blend_plug(control, before_attrs, before_pair_blends)
    if not blend_plug:
        raise RuntimeError(
            "Maya could not create an FK/IK blend input for {0}".format(_short_name(control))
        )
    return constraint, blend_plug


def _create_proxy_chain(system_root, top, mid, end):
    top_pos = list(_world_position(top))
    mid_pos = list(_world_position(mid))
    end_pos = list(_world_position(end))

    cmds.select(clear=True)
    top_joint = cmds.joint(position=top_pos, name=_unique_scene_name("AKS_top_JNT"))
    mid_joint = cmds.joint(position=mid_pos, name=_unique_scene_name("AKS_mid_JNT"))
    end_joint = cmds.joint(position=end_pos, name=_unique_scene_name("AKS_end_JNT"))
    try:
        cmds.joint(
            top_joint,
            edit=True,
            orientJoint="xyz",
            secondaryAxisOrient="yup",
            children=True,
            zeroScaleOrient=True,
        )
    except Exception:
        pass
    top_joint = _long_name(cmds.parent(top_joint, system_root, absolute=True)[0])
    mid_children = cmds.listRelatives(
        top_joint,
        children=True,
        type="joint",
        fullPath=True,
    ) or []
    if not mid_children:
        raise RuntimeError("Could not resolve the proxy middle joint")
    mid_joint = mid_children[0]
    end_children = cmds.listRelatives(
        mid_joint,
        children=True,
        type="joint",
        fullPath=True,
    ) or []
    if not end_children:
        raise RuntimeError("Could not resolve the proxy end joint")
    end_joint = end_children[0]
    cmds.setAttr(top_joint + ".visibility", 0)

    handle, effector = cmds.ikHandle(
        startJoint=top_joint,
        endEffector=end_joint,
        solver="ikRPsolver",
        name=_unique_scene_name("AKS_tempIK_HDL"),
    )
    handle = cmds.parent(handle, system_root, absolute=True)[0]
    cmds.setAttr(handle + ".visibility", 0)
    return top_joint, mid_joint, end_joint, handle, effector


def _scene_state():
    state = {
        "time": cmds.currentTime(query=True),
        "autoKey": cmds.autoKeyframe(query=True, state=True),
    }
    return state


def _begin_scene_operation(state):
    cmds.autoKeyframe(state=False)
    cmds.refresh(suspend=True)


def _end_scene_operation(state):
    try:
        cmds.currentTime(state["time"], update=True)
    except Exception:
        pass
    try:
        cmds.refresh(suspend=False)
    except Exception:
        pass
    try:
        cmds.autoKeyframe(state=state["autoKey"])
    except Exception:
        pass


def _add_switch_attribute(control):
    if not cmds.attributeQuery(SWITCH_ATTR, node=control, exists=True):
        cmds.addAttr(
            control,
            longName=SWITCH_ATTR,
            attributeType="double",
            minValue=0.0,
            maxValue=1.0,
            defaultValue=0.0,
            keyable=True,
        )
    return "{0}.{1}".format(control, SWITCH_ATTR)


def _switch_plug(setup):
    ik_driver = _connected_node(setup, "ikDriver")
    if not ik_driver or not cmds.attributeQuery(SWITCH_ATTR, node=ik_driver, exists=True):
        return None
    return "{0}.{1}".format(ik_driver, SWITCH_ATTR)


def _blend_plugs(setup):
    try:
        plugs = json.loads(_get_string(setup, "blendPlugs", "[]"))
    except Exception:
        plugs = []
    return [plug for plug in plugs if plug and cmds.objExists(plug)]


def _connect_fk_ik_blend(setup, master_plug, blend_plugs):
    connected = []
    for plug in blend_plugs:
        if not plug or not cmds.objExists(plug):
            continue
        try:
            cmds.connectAttr(master_plug, plug, force=True)
            connected.append(plug)
        except Exception:
            pass
    if len(connected) != len(blend_plugs):
        raise RuntimeError("Could not connect the FK/IK blend to every source control")
    _set_string(setup, "blendPlugs", json.dumps(connected))


def _match_helpers_to_fk(setup, time_value=None, key=True):
    base_parent, top, mid, end = _setup_sources(setup)
    root_driver, ik_driver, pole_driver, tip_driver, system_root = _setup_drivers(setup)
    if not all((top, mid, end, root_driver, ik_driver, pole_driver, tip_driver)):
        raise RuntimeError("The Switcher setup is incomplete")

    time_value = cmds.currentTime(query=True) if time_value is None else time_value
    top_position = cmds.xform(top, query=True, worldSpace=True, rotatePivot=True)
    end_position = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
    end_rotation = cmds.xform(end, query=True, worldSpace=True, rotation=True)
    pole_position, _, _ = _pole_position(top, mid, end)

    _set_world_translation(root_driver, top_position)
    _set_world_translation(ik_driver, end_position)
    _set_world_translation(pole_driver, pole_position)
    _set_world_rotation(tip_driver, end_rotation)

    if key:
        _key_channels(root_driver, ("tx", "ty", "tz"), time_value)
        _key_channels(ik_driver, ("tx", "ty", "tz"), time_value)
        _key_channels(pole_driver, ("tx", "ty", "tz"), time_value)
        _key_channels(tip_driver, ("rx", "ry", "rz"), time_value)


def _key_switch_value(setup, value, time_value=None):
    master_plug = _switch_plug(setup)
    if not master_plug:
        raise RuntimeError("This setup does not have an FK/IK switch attribute")

    time_value = cmds.currentTime(query=True) if time_value is None else float(time_value)
    guard_time = time_value - 1.0
    try:
        guard_value = float(cmds.getAttr(master_plug, time=guard_time))
    except Exception:
        guard_value = float(cmds.getAttr(master_plug))

    cmds.setKeyframe(master_plug, time=guard_time, value=guard_value)
    cmds.setAttr(master_plug, float(value))
    cmds.setKeyframe(master_plug, time=time_value, value=float(value))
    try:
        cmds.keyTangent(
            master_plug,
            edit=True,
            time=(guard_time, time_value),
            outTangentType="step",
        )
    except Exception:
        pass


def current_mode(setup):
    master_plug = _switch_plug(setup)
    if not master_plug:
        return None
    try:
        return "IK" if float(cmds.getAttr(master_plug)) >= 0.5 else "FK"
    except Exception:
        return None


def switch_to_ik(setup=None):
    setup = setup or (setups_from_selection() or [None])[0]
    if not setup or not _is_setup(setup):
        _warning("Select a control from an active Switcher setup.")
        return False
    if _setup_version(setup) < 2:
        _warning("This is a legacy setup. Bake or remove it and create a new Switcher.")
        return False
    if current_mode(setup) == "IK":
        return True

    state = _scene_state()
    cmds.undoInfo(openChunk=True, chunkName="AnimKey Switcher To IK")
    try:
        _begin_scene_operation(state)
        time_value = state["time"]
        _match_helpers_to_fk(setup, time_value=time_value, key=True)
        _key_switch_value(setup, 1.0, time_value=time_value)
        controls = _existing(_setup_drivers(setup)[1:4])
        if controls:
            cmds.select(controls, replace=True)
        return True
    except Exception as error:
        _warning("Could not switch to IK: {0}".format(error))
        return False
    finally:
        _end_scene_operation(state)
        cmds.undoInfo(closeChunk=True)


def switch_to_fk(setup=None):
    setup = setup or (setups_from_selection() or [None])[0]
    if not setup or not _is_setup(setup):
        _warning("Select a control from an active Switcher setup.")
        return False
    if _setup_version(setup) < 2:
        _warning("This is a legacy setup. Bake or remove it and create a new Switcher.")
        return False
    if current_mode(setup) == "FK":
        return True

    base_parent, top, mid, end = _setup_sources(setup)
    sources = _existing((top, mid, end))
    if len(sources) != 3:
        _warning("The source FK controls are no longer available.")
        return False

    state = _scene_state()
    cmds.undoInfo(openChunk=True, chunkName="AnimKey Switcher To FK")
    try:
        _begin_scene_operation(state)
        time_value = state["time"]
        world_matrices = [_world_matrix(control) for control in sources]
        _key_switch_value(setup, 0.0, time_value=time_value)

        for control, matrix_value in zip(sources, world_matrices):
            cmds.xform(control, worldSpace=True, matrix=matrix_value)
            _key_channels(control, KEY_ATTRS, time_value)

        cmds.select(sources, replace=True)
        return True
    except Exception as error:
        _warning("Could not switch to FK: {0}".format(error))
        return False
    finally:
        _end_scene_operation(state)
        cmds.undoInfo(closeChunk=True)


def create_temp_ik(only_keys=False, range_mode="auto"):
    controls = _selected_controls()
    if len(controls) not in (3, 4):
        _warning("Select Top, Mid and End controls in order. An optional parent can be selected first.")
        return None

    if len(controls) == 4:
        base_parent, top, mid, end = controls
    else:
        top, mid, end = controls
        parents = cmds.listRelatives(top, parent=True, fullPath=True) or []
        base_parent = parents[0] if parents else None

    overlaps = []
    for control in (top, mid, end):
        existing_setup = _setup_from_node(control)
        if existing_setup and existing_setup not in overlaps:
            overlaps.append(existing_setup)
    if overlaps:
        _warning("One or more selected controls already belong to an active Switcher setup.")
        return None

    start, finish = resolve_time_range(range_mode)
    if finish < start:
        start, finish = finish, start

    state = _scene_state()
    setup = None
    system_root = None
    undo_open = False
    cmds.undoInfo(openChunk=True, chunkName="AnimKey Switcher Create")
    undo_open = True

    try:
        _begin_scene_operation(state)
        setup = _create_setup_node(base_parent, top, mid, end, start, finish)

        root = _get_or_create_root()
        system_root = cmds.group(
            empty=True,
            name=SETUP_PREFIX + _safe_name(end) + "_GRP",
            parent=root,
        )
        _record_driver(setup, system_root, "systemRoot")

        root_space = cmds.group(
            empty=True,
            name=_unique_scene_name("AKS_rootSpace_GRP"),
            parent=system_root,
        )
        root_driver = cmds.spaceLocator(name=_unique_scene_name("AKS_root_LOC"))[0]
        root_driver = cmds.parent(root_driver, root_space, relative=True)[0]
        ik_driver = cmds.spaceLocator(name=_unique_scene_name("AKS_ik_CTRL"))[0]
        ik_driver = cmds.parent(ik_driver, system_root, absolute=True)[0]
        pole_driver = cmds.spaceLocator(name=_unique_scene_name("AKS_pole_CTRL"))[0]
        pole_driver = cmds.parent(pole_driver, system_root, absolute=True)[0]
        tip_driver = cmds.circle(
            name=_unique_scene_name("AKS_tip_CTRL"),
            normal=(1.0, 0.0, 0.0),
            radius=1.0,
            constructionHistory=False,
        )[0]
        tip_driver = cmds.parent(tip_driver, ik_driver, relative=True)[0]

        _record_driver(setup, root_driver, "rootDriver")
        _record_driver(setup, ik_driver, "ikDriver")
        _record_driver(setup, pole_driver, "poleDriver")
        _record_driver(setup, tip_driver, "tipDriver")

        control_scale = _locator_scale((top, mid, end))
        _style_locator(root_driver, control_scale * 0.6, (0.4, 0.75, 0.85))
        _style_locator(ik_driver, control_scale * 1.35, (0.95, 0.33, 0.28))
        _style_locator(pole_driver, control_scale, (0.25, 0.62, 0.95))
        cmds.scale(
            control_scale * 1.55,
            control_scale * 1.55,
            control_scale * 1.55,
            tip_driver,
            relative=True,
            objectSpace=True,
        )
        cmds.makeIdentity(tip_driver, apply=True, scale=True)
        _style_curve(tip_driver, (0.98, 0.68, 0.22), line_width=2.5)
        cmds.setAttr(root_driver + ".visibility", 0)
        _lock_channels(ik_driver, ("rx", "ry", "rz", "sx", "sy", "sz"))
        _lock_channels(pole_driver, ("rx", "ry", "rz", "sx", "sy", "sz"))
        _lock_channels(tip_driver, ("tx", "ty", "tz", "sx", "sy", "sz"))
        master_plug = _add_switch_attribute(ik_driver)

        if base_parent and cmds.objExists(base_parent):
            _record_constraint(
                setup,
                cmds.parentConstraint(base_parent, root_space, maintainOffset=False)[0],
            )

        _match_helpers_to_fk(setup, time_value=state["time"], key=False)
        top_joint, mid_joint, end_joint, handle, _ = _create_proxy_chain(
            system_root,
            top,
            mid,
            end,
        )

        _record_constraint(
            setup,
            cmds.pointConstraint(root_driver, top_joint, maintainOffset=False)[0],
        )
        _record_constraint(
            setup,
            cmds.pointConstraint(ik_driver, handle, maintainOffset=False)[0],
        )
        _record_constraint(
            setup,
            cmds.orientConstraint(tip_driver, end_joint, maintainOffset=False)[0],
        )
        _record_constraint(
            setup,
            cmds.poleVectorConstraint(pole_driver, handle)[0],
        )

        blend_plugs = []
        for proxy, source in (
            (top_joint, top),
            (mid_joint, mid),
            (end_joint, end),
        ):
            _, blend_plug = _drive_control(setup, proxy, source)
            blend_plugs.append(blend_plug)
        _connect_fk_ik_blend(setup, master_plug, blend_plugs)
        cmds.setAttr(master_plug, 0.0)
        cmds.setKeyframe(master_plug, time=state["time"], value=0.0)
        try:
            cmds.keyTangent(
                master_plug,
                edit=True,
                time=(state["time"], state["time"]),
                outTangentType="step",
            )
        except Exception:
            pass

        cmds.currentTime(state["time"], update=True)
        cmds.select([ik_driver, pole_driver, tip_driver], replace=True)
        cmds.inViewMessage(
            amg="<hl>Switcher</hl> live FK / IK created",
            pos="topCenter",
            fade=True,
        )
        return setup

    except Exception as error:
        for constraint in reversed(_connected_nodes(setup, "constraints") if setup else []):
            try:
                if cmds.objExists(constraint):
                    cmds.delete(constraint)
            except Exception:
                pass
        if system_root and cmds.objExists(system_root):
            try:
                cmds.delete(system_root)
            except Exception:
                pass
        if setup and cmds.objExists(setup):
            try:
                cmds.delete(setup)
            except Exception:
                pass
        _cleanup_empty_root()
        _warning("Could not create Switcher: {0}".format(error))
        return None

    finally:
        _end_scene_operation(state)
        if undo_open:
            cmds.undoInfo(closeChunk=True)


def _bake_attributes(objects):
    attrs = []
    for attr in KEY_ATTRS:
        for obj in objects:
            plug = "{0}.{1}".format(obj, attr)
            if not cmds.objExists(obj) or not cmds.objExists(plug):
                continue
            try:
                if not cmds.getAttr(plug, lock=True):
                    attrs.append(attr)
                    break
            except Exception:
                continue
    return attrs


def _has_animation_layers():
    layers = cmds.ls(type="animLayer") or []
    try:
        root_layer = cmds.animLayer(query=True, root=True)
    except Exception:
        root_layer = None
    return any(layer != root_layer for layer in layers)


def _create_override_bake_layer(source_end):
    layer = cmds.animLayer(
        _unique_scene_name("AnimKey_Switcher_Bake_{0}".format(_safe_name(source_end))),
        override=True,
        passthrough=False,
    )
    try:
        cmds.animLayer(layer, edit=True, mute=False, weight=1.0)
    except Exception:
        pass
    return layer


def _delete_setup_nodes(setup):
    constraints = _connected_nodes(setup, "constraints")
    system_root = _connected_node(setup, "systemRoot")

    for constraint in reversed(constraints):
        if cmds.objExists(constraint):
            try:
                cmds.delete(constraint)
            except Exception:
                pass
    if system_root and cmds.objExists(system_root):
        cmds.delete(system_root)
    if cmds.objExists(setup):
        cmds.delete(setup)
    _cleanup_empty_root()


def _cleanup_empty_root():
    if not cmds.objExists(ROOT_GROUP):
        return
    children = cmds.listRelatives(ROOT_GROUP, children=True, fullPath=True) or []
    if not children:
        try:
            for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
                cmds.setAttr("{0}.{1}".format(ROOT_GROUP, attr), lock=False)
            cmds.delete(ROOT_GROUP)
        except Exception:
            pass


def bake_and_remove(setup=None, time_range=None):
    if not setup:
        selected_setups = setups_from_selection()
        setup = selected_setups[0] if len(selected_setups) == 1 else None
    if not setup or not _is_setup(setup):
        _warning("Select a Switcher control from an active setup.")
        return False

    base_parent, top, mid, end = _setup_sources(setup)
    sources = _existing((top, mid, end))
    if len(sources) != 3:
        _warning("The source FK controls for this setup are no longer available.")
        return False

    if time_range is None:
        start = float(cmds.getAttr(setup + ".startFrame"))
        finish = float(cmds.getAttr(setup + ".endFrame"))
    else:
        start, finish = time_range
    if finish < start:
        start, finish = finish, start

    state = _scene_state()
    bake_layer = None
    cmds.undoInfo(openChunk=True, chunkName="AnimKey Switcher Bake")
    try:
        _begin_scene_operation(state)
        attributes = _bake_attributes(sources)
        bake_kwargs = {
            "time": (start, finish),
            "sampleBy": 1,
            "simulation": True,
            "minimizeRotation": True,
            "disableImplicitControl": True,
            "preserveOutsideKeys": True,
            "sparseAnimCurveBake": False,
            "attribute": attributes,
        }
        if _has_animation_layers():
            bake_layer = _create_override_bake_layer(end)
            bake_kwargs["destinationLayer"] = bake_layer
        cmds.bakeResults(sources, **bake_kwargs)
        _delete_setup_nodes(setup)
        cmds.currentTime(state["time"], update=True)
        cmds.select(sources, replace=True)
        try:
            cmds.filterCurve(sources)
        except Exception:
            pass
        cmds.inViewMessage(
            amg="<hl>Switcher</hl> animation baked to FK",
            pos="topCenter",
            fade=True,
        )
        return True
    except Exception as error:
        if bake_layer and cmds.objExists(bake_layer):
            try:
                cmds.delete(bake_layer)
            except Exception:
                pass
        _warning("Could not bake Switcher: {0}".format(error))
        return False
    finally:
        _end_scene_operation(state)
        cmds.undoInfo(closeChunk=True)


def remove_setup(setup=None, confirm=True):
    if not setup:
        selected_setups = setups_from_selection()
        setup = selected_setups[0] if len(selected_setups) == 1 else None
    if not setup or not _is_setup(setup):
        _warning("No active Switcher setup was found.")
        return False

    if confirm:
        answer = cmds.confirmDialog(
            title="Remove Switcher",
            message="Remove this Switcher and restore the original FK animation?",
            button=("Remove", "Cancel"),
            defaultButton="Cancel",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if answer != "Remove":
            return False

    sources = _existing(_setup_sources(setup)[1:])
    cmds.undoInfo(openChunk=True, chunkName="AnimKey Switcher Remove")
    try:
        _delete_setup_nodes(setup)
        if sources:
            cmds.select(sources, replace=True)
        return True
    except Exception as error:
        _warning("Could not remove setup: {0}".format(error))
        return False
    finally:
        cmds.undoInfo(closeChunk=True)


def select_setup_controls(setup=None):
    if not setup:
        selected_setups = setups_from_selection()
        setup = selected_setups[0] if len(selected_setups) == 1 else None
    if not setup or not _is_setup(setup):
        return False
    drivers = _setup_drivers(setup)
    controls = _existing(drivers[1:4] if _setup_version(setup) >= 2 else drivers[1:3])
    if not controls:
        return False
    cmds.select(controls, replace=True)
    return True


def _option_string(name, default):
    if cmds.optionVar(exists=name):
        value = cmds.optionVar(query=name)
        if value:
            return str(value)
    return default


class SwitcherWindow(ContextPopupWindow):
    def __init__(self, anchor_button=None, parent=None):
        super(SwitcherWindow, self).__init__(anchor_button=anchor_button, parent=parent)
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle(TOOL_NAME)
        self.setFixedSize(350, 390)
        self._base_opacity = 0.94
        self._hover_opacity = 1.0
        self._opacity_anim = None
        self._build_ui()
        self.position_window()
        self.setWindowOpacity(self._base_opacity)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(700)
        self._refresh_timer.timeout.connect(self._refresh_state)
        self._refresh_timer.start()
        self._refresh_state()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, self._tail_height + 1)
        root.setSpacing(0)

        container = QtWidgets.QFrame()
        container.setObjectName("switcherContainer")
        container.setStyleSheet("""
            QFrame#switcherContainer {
                background: transparent;
                border: none;
            }
            QLabel {
                background: transparent;
                border: none;
                color: #d8d8dc;
            }
            QComboBox {
                background-color: #454549;
                color: #eeeeef;
                border: 1px solid #626268;
                border-radius: 6px;
                padding: 6px 9px;
                min-height: 18px;
            }
            QComboBox:hover { border-color: #70aeba; }
            QComboBox QAbstractItemView {
                background-color: #3d3d41;
                color: #f0f0f2;
                border: 1px solid #68686e;
                selection-background-color: #426b73;
            }
            QCheckBox {
                color: #c4c4c9;
                spacing: 7px;
                font-size: 11px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #6a6a70;
                border-radius: 4px;
                background: #414145;
            }
            QCheckBox::indicator:checked {
                background: #5bc0be;
                border-color: #79d4d2;
            }
            QPushButton {
                background-color: #4a4a4f;
                color: #d8d8dc;
                border: 1px solid #64646a;
                border-radius: 7px;
                min-height: 31px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #55555b;
                border-color: #79c8c6;
                color: #ffffff;
            }
            QPushButton:pressed { background-color: #3d3d42; }
            QPushButton:disabled {
                color: #737379;
                background-color: #404044;
                border-color: #4b4b50;
            }
            QPushButton#primaryButton {
                background-color: #315e63;
                color: #d8ffff;
                border-color: #5bc0be;
                min-height: 38px;
                font-weight: 700;
            }
            QPushButton#primaryButton:hover { background-color: #3c7076; }
            QPushButton#bakeButton {
                background-color: #36543f;
                color: #c8e7ce;
                border-color: #709b77;
            }
            QPushButton#modeButton {
                min-height: 34px;
                font-size: 12px;
                font-weight: 700;
                background-color: #414145;
                color: #94949a;
                border-color: #55555b;
            }
            QPushButton#modeButton:hover {
                color: #f1f1f3;
                border-color: #77777e;
            }
            QPushButton#modeButton:checked {
                background-color: #315e63;
                color: #e5ffff;
                border-color: #69c8c5;
            }
            QLabel#roleLabel {
                color: #a8a8ae;
                background-color: #3e3e42;
                border: 1px solid #535359;
                border-radius: 5px;
                padding: 4px 5px;
                font-size: 8px;
                font-weight: 700;
            }
            QPushButton#removeButton {
                color: #c89a96;
                border-color: #755653;
                max-width: 42px;
            }
        """)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(14, 12, 14, 15)
        layout.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("SWITCHER")
        title.setStyleSheet("font-size: 12px; font-weight: 700; color: #eeeeef;")
        header.addWidget(title)
        header.addStretch()

        self.status_label = QtWidgets.QLabel("0 ACTIVE")
        self.status_label.setStyleSheet("""
            color: #91d2cf;
            background-color: #405558;
            border: 1px solid #547275;
            border-radius: 6px;
            padding: 3px 7px;
            font-size: 9px;
            font-weight: 700;
        """)
        header.addWidget(self.status_label)

        close_button = QtWidgets.QPushButton("X")
        close_button.setFixedSize(22, 22)
        close_button.setStyleSheet("""
            QPushButton {
                min-height: 0;
                max-width: 22px;
                background: transparent;
                border: none;
                color: #94949a;
            }
            QPushButton:hover { background: #7a3f42; color: white; }
        """)
        close_button.clicked.connect(self.close)
        header.addWidget(close_button)
        layout.addLayout(header)

        separator = QtWidgets.QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #59595f; border: none;")
        layout.addWidget(separator)

        setup_row = QtWidgets.QHBoxLayout()
        setup_label = QtWidgets.QLabel("SETUP")
        setup_label.setFixedWidth(66)
        setup_label.setStyleSheet("color: #929298; font-size: 9px; font-weight: 700;")
        setup_row.addWidget(setup_label)
        self.setup_combo = QtWidgets.QComboBox()
        setup_row.addWidget(self.setup_combo, 1)
        layout.addLayout(setup_row)

        range_row = QtWidgets.QHBoxLayout()
        range_label = QtWidgets.QLabel("RANGE")
        range_label.setFixedWidth(66)
        range_label.setStyleSheet("color: #929298; font-size: 9px; font-weight: 700;")
        range_row.addWidget(range_label)
        self.range_combo = QtWidgets.QComboBox()
        self.range_combo.addItem("Time Slider / Playback", "auto")
        self.range_combo.addItem("Playback Range", "playback")
        stored_mode = _option_string(OPTION_RANGE_MODE, "auto")
        stored_index = self.range_combo.findData(stored_mode)
        self.range_combo.setCurrentIndex(max(0, stored_index))
        self.range_combo.currentIndexChanged.connect(self._save_range_mode)
        range_row.addWidget(self.range_combo, 1)
        layout.addLayout(range_row)

        mode_row = QtWidgets.QHBoxLayout()
        mode_label = QtWidgets.QLabel("MODE")
        mode_label.setFixedWidth(66)
        mode_label.setStyleSheet("color: #929298; font-size: 9px; font-weight: 700;")
        mode_row.addWidget(mode_label)
        self.fk_button = QtWidgets.QPushButton("FK")
        self.fk_button.setObjectName("modeButton")
        self.fk_button.setCheckable(True)
        self.fk_button.clicked.connect(self._to_fk)
        mode_row.addWidget(self.fk_button, 1)
        self.ik_button = QtWidgets.QPushButton("IK")
        self.ik_button.setObjectName("modeButton")
        self.ik_button.setCheckable(True)
        self.ik_button.clicked.connect(self._to_ik)
        mode_row.addWidget(self.ik_button, 1)
        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.fk_button)
        self.mode_group.addButton(self.ik_button)
        layout.addLayout(mode_row)

        role_row = QtWidgets.QHBoxLayout()
        role_row.setSpacing(5)
        for text in ("IK POSITION", "POLE VECTOR", "TIP ROTATION"):
            label = QtWidgets.QLabel(text)
            label.setObjectName("roleLabel")
            label.setAlignment(QtCore.Qt.AlignCenter)
            role_row.addWidget(label, 1)
        layout.addLayout(role_row)

        self.create_button = QtWidgets.QPushButton("CREATE LIVE SWITCHER")
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self._create)
        layout.addWidget(self.create_button)

        action_row = QtWidgets.QHBoxLayout()
        self.select_button = QtWidgets.QPushButton("SELECT IK")
        self.select_button.clicked.connect(self._select)
        action_row.addWidget(self.select_button)

        self.bake_button = QtWidgets.QPushButton("BAKE RANGE + REMOVE")
        self.bake_button.setObjectName("bakeButton")
        self.bake_button.clicked.connect(self._bake)
        action_row.addWidget(self.bake_button, 1)

        self.remove_button = QtWidgets.QPushButton("X")
        self.remove_button.setObjectName("removeButton")
        self.remove_button.setToolTip("Remove Switcher and restore the original FK animation")
        self.remove_button.clicked.connect(self._remove)
        action_row.addWidget(self.remove_button)
        layout.addLayout(action_row)

        self.selection_label = QtWidgets.QLabel("0 CONTROLS SELECTED")
        self.selection_label.setAlignment(QtCore.Qt.AlignCenter)
        self.selection_label.setStyleSheet(
            "color: #8d8d93; font-size: 9px; padding-top: 2px;"
        )
        layout.addWidget(self.selection_label)
        layout.addStretch()
        root.addWidget(container)

    def _current_setup(self):
        setup = self.setup_combo.currentData()
        return str(setup) if setup and _is_setup(str(setup)) else None

    def _save_range_mode(self):
        mode = self.range_combo.currentData() or "auto"
        cmds.optionVar(stringValue=(OPTION_RANGE_MODE, str(mode)))

    def _refresh_state(self):
        current = self._current_setup()
        selected_setups = setups_from_selection()
        preferred = selected_setups[0] if len(selected_setups) == 1 else current
        setups = list_setups()

        combo_setups = [
            str(self.setup_combo.itemData(index))
            for index in range(self.setup_combo.count())
            if self.setup_combo.itemData(index)
        ]
        if combo_setups != setups or self.setup_combo.count() == 0:
            self.setup_combo.blockSignals(True)
            self.setup_combo.clear()
            if not setups:
                self.setup_combo.addItem("No active Switcher", "")
            else:
                for setup in setups:
                    self.setup_combo.addItem(_get_string(setup, "displayName", setup), setup)
            target_index = self.setup_combo.findData(preferred)
            self.setup_combo.setCurrentIndex(max(0, target_index))
            self.setup_combo.blockSignals(False)
        elif preferred:
            target_index = self.setup_combo.findData(preferred)
            if target_index >= 0 and target_index != self.setup_combo.currentIndex():
                self.setup_combo.setCurrentIndex(target_index)

        active = len(setups)
        self.status_label.setText("{0} ACTIVE".format(active))
        has_setup = bool(self._current_setup())
        setup = self._current_setup()
        live_setup = bool(setup and _setup_version(setup) >= 2)
        self.select_button.setEnabled(has_setup)
        self.bake_button.setEnabled(has_setup)
        self.remove_button.setEnabled(has_setup)
        self.fk_button.setEnabled(live_setup)
        self.ik_button.setEnabled(live_setup)

        mode = current_mode(setup) if live_setup else None
        self.mode_group.setExclusive(False)
        self.fk_button.setChecked(mode == "FK")
        self.ik_button.setChecked(mode == "IK")
        self.mode_group.setExclusive(True)

        selection_count = len(_selected_controls())
        self.selection_label.setText("{0} CONTROLS SELECTED".format(selection_count))
        self.create_button.setEnabled(selection_count in (3, 4))

    def _create(self):
        setup = create_temp_ik(
            only_keys=False,
            range_mode=str(self.range_combo.currentData() or "auto"),
        )
        if setup:
            self._refresh_state()
            index = self.setup_combo.findData(setup)
            if index >= 0:
                self.setup_combo.setCurrentIndex(index)

    def _select(self):
        select_setup_controls(self._current_setup())

    def _to_fk(self):
        if switch_to_fk(self._current_setup()):
            self._refresh_state()

    def _to_ik(self):
        if switch_to_ik(self._current_setup()):
            self._refresh_state()

    def _bake(self):
        setup = self._current_setup()
        if bake_and_remove(setup):
            self._refresh_state()

    def _remove(self):
        setup = self._current_setup()
        if remove_setup(setup, confirm=True):
            self._refresh_state()

    def enterEvent(self, event):
        self._animate_opacity(self._hover_opacity)
        super(SwitcherWindow, self).enterEvent(event)

    def leaveEvent(self, event):
        self._animate_opacity(self._base_opacity)
        super(SwitcherWindow, self).leaveEvent(event)

    def _animate_opacity(self, value):
        if self._opacity_anim is not None:
            try:
                self._opacity_anim.stop()
            except RuntimeError:
                pass
        self._opacity_anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self._opacity_anim.setDuration(130)
        self._opacity_anim.setEndValue(value)
        self._opacity_anim.start()

    def closeEvent(self, event):
        global _window
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        _window = None
        super(SwitcherWindow, self).closeEvent(event)


def show(anchor_button=None):
    global _window
    from AnimKey.mods import uiMod

    uiMod.close_animkey_tool_windows(except_widget=_window)
    if _window is not None:
        existing = uiMod.show_existing_animkey_tool_window(_window, anchor_button)
        if existing is not None:
            _window = existing
            return _window
        _window = None

    if cmds.window(WINDOW_OBJECT, exists=True):
        cmds.deleteUI(WINDOW_OBJECT)
    _window = SwitcherWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _window.show()
    _window.raise_()
    return _window


def execute(*args, **kwargs):
    from AnimKey.core.executionGuard import require_animkey_context

    if not require_animkey_context("AnimKey.buttons.switcher.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))


def btl_main():
    return create_temp_ik(
        range_mode=_option_string(OPTION_RANGE_MODE, "auto"),
    )


if __name__ == "__main__":
    show()
