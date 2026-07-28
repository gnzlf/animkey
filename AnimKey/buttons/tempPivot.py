# -*- coding: utf-8 -*-
"""
TEMP CONTROL PRO - MATRIX-BASED SOLUTION
=========================================
Usa pre-bake de world-space + multMatrix + decomposeMatrix
para lograr control aditivo sin loops circulares.

Modern frameless window design matching AnimKey retimer style.
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.OpenMayaUI as omui
import json
import os
from AnimKey.mods.uiMod import ContextPopupWindow

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance

TOOL_TAG = "TEMP_CTRL_MATRIX_V2"
WINDOW_OBJECT = "AnimKey_TempControl"
TEMP_BAKE_LAYER_BASE = "TempControl_Animkey"
TEMP_BAKE_LAYER_SUFFIX = "_Animkey"
TEMP_BAKE_ATTRS = [
    'translateX', 'translateY', 'translateZ',
    'rotateX', 'rotateY', 'rotateZ'
]

# Global window reference
_temp_pivot_window = None

def get_maya_main_window():
    return wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget)


def _selected_temp_pivot_objects(selection=None):
    """Return unique selected transforms/joints while preserving selection order."""
    raw_selection = selection
    if raw_selection is None:
        raw_selection = cmds.ls(selection=True, long=True) or []
    elif isinstance(raw_selection, str):
        raw_selection = [raw_selection]

    objects = []
    seen = set()
    for item in raw_selection or []:
        nodes = cmds.ls(item, objectsOnly=True, long=True) or []
        node = nodes[0] if nodes else str(item).split(".", 1)[0]
        if not cmds.objExists(node):
            continue

        try:
            node_type = cmds.nodeType(node)
        except Exception:
            continue

        if node_type not in ("transform", "joint"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if not parents:
                continue
            node = parents[0]

        long_names = cmds.ls(node, long=True) or [node]
        node = long_names[0]
        if node not in seen:
            seen.add(node)
            objects.append(node)
    return objects


def _temp_pivot_position(objects, pivot_mode="last"):
    if pivot_mode == "center":
        bounds = cmds.exactWorldBoundingBox(objects)
        return [
            (bounds[0] + bounds[3]) * 0.5,
            (bounds[1] + bounds[4]) * 0.5,
            (bounds[2] + bounds[5]) * 0.5,
        ]
    return list(cmds.xform(objects[-1], query=True, worldSpace=True, rotatePivot=True))


def activate_temp_pivot(objects=None, pivot_mode="last", edit_pivot=True):
    """Activate Maya's non-destructive custom manipulator pivot."""
    selected_objects = _selected_temp_pivot_objects(objects)
    if not selected_objects:
        om.MGlobal.displayWarning("Select one or more controls for Temp Pivot.")
        return None

    pivot_position = _temp_pivot_position(selected_objects, pivot_mode=pivot_mode)
    undo_open = False
    try:
        cmds.undoInfo(openChunk=True, chunkName="AnimKey Temp Pivot")
        undo_open = True

        cmds.setToolTo("RotateSuperContext")
        cmds.manipPivot(reset=True)
        cmds.manipRotateContext(
            "Rotate",
            edit=True,
            useManipPivot=True,
            useCenterPivot=False,
            useObjectPivot=False,
        )
        cmds.manipPivot(position=pivot_position)
        cmds.manipPivot(pinPivot=True)
        cmds.manipRotateContext("Rotate", edit=True, pinPivot=True)
        if edit_pivot and not cmds.manipRotateContext(
            "Rotate", query=True, editPivotMode=True
        ):
            cmds.ctxEditMode()
    except Exception as exc:
        om.MGlobal.displayError("Temp Pivot error: {}".format(exc))
        return None
    finally:
        if undo_open:
            cmds.undoInfo(closeChunk=True)

    return {
        "objects": selected_objects,
        "pivot": pivot_position,
        "mode": pivot_mode,
    }


def deactivate_temp_pivot():
    """Reset Maya's custom manipulator pivot to its normal behavior."""
    undo_open = False
    try:
        cmds.undoInfo(openChunk=True, chunkName="AnimKey Temp Pivot Off")
        undo_open = True

        if cmds.manipRotateContext("Rotate", query=True, editPivotMode=True):
            cmds.ctxEditMode()
        cmds.manipPivot(pinPivot=False)
        cmds.manipPivot(reset=True)
        cmds.manipRotateContext(
            "Rotate",
            edit=True,
            pinPivot=False,
            useManipPivot=False,
            useCenterPivot=False,
            useObjectPivot=False,
        )
        return True
    except Exception as exc:
        om.MGlobal.displayError("Temp Pivot reset error: {}".format(exc))
        return False
    finally:
        if undo_open:
            cmds.undoInfo(closeChunk=True)


# ============================================================
# CORE LOGIC (Matrix-Based)
# ============================================================

def tag(node):
    if not cmds.attributeQuery("tempTag", node=node, exists=True):
        cmds.addAttr(node, ln="tempTag", dt="string")
    cmds.setAttr(node + ".tempTag", TOOL_TAG, type="string", lock=True)

def add_string_attr(node, attr, value):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, ln=attr, dt="string")
    cmds.setAttr(node + "." + attr, value, type="string")

def add_follow_attr(ctrl, default=True):
    if not cmds.attributeQuery("follow", node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln="follow", at="bool", keyable=True, dv=1 if default else 0)
    cmds.setAttr(ctrl + ".follow", bool(default))

def _create_temp_controls_container():
    return None

def _parent_under_temp_container(node):
    return

def _cleanup_empty_temp_container():
    legacy_container = "animkey_temp_controls"
    if not cmds.objExists(legacy_container):
        return
    try:
        children = cmds.listRelatives(legacy_container, children=True, fullPath=True) or []
        temp_controls = cmds.ls("TEMP_CTRL*", type="transform") or []
        if not children and not temp_controls:
            cmds.delete(legacy_container)
    except Exception:
        pass

def _read_json_attr(node, attr):
    if not cmds.objExists(node) or not cmds.attributeQuery(attr, node=node, exists=True):
        return {}
    try:
        value = cmds.getAttr(node + "." + attr)
        return json.loads(value) if value else {}
    except Exception:
        return {}

def _shortest_angle_delta(final_value, base_value, previous_delta=None):
    delta = (final_value - base_value + 180.0) % 360.0 - 180.0
    if previous_delta is not None:
        while delta - previous_delta > 180.0:
            delta -= 360.0
        while delta - previous_delta < -180.0:
            delta += 360.0
    return delta

def _temp_anim_layer_name():
    if not cmds.objExists(TEMP_BAKE_LAYER_BASE):
        return TEMP_BAKE_LAYER_BASE
    index = 2
    while True:
        name = "TempControl{:02d}{}".format(index, TEMP_BAKE_LAYER_SUFFIX)
        if not cmds.objExists(name):
            return name
        index += 1

def _sample_transform_values(objects, start_frame, end_frame):
    samples = {}
    current_time = cmds.currentTime(query=True)
    try:
        for frame in range(int(start_frame), int(end_frame) + 1):
            cmds.currentTime(frame, edit=True)
            for obj in objects:
                if not cmds.objExists(obj):
                    continue
                obj_samples = samples.setdefault(obj, {})
                for attr in TEMP_BAKE_ATTRS:
                    plug = obj + "." + attr
                    if not cmds.objExists(plug):
                        continue
                    try:
                        obj_samples.setdefault(attr, []).append((frame, cmds.getAttr(plug)))
                    except Exception:
                        pass
    finally:
        cmds.currentTime(current_time, edit=True)
    return samples

def _disconnect_temp_connections(obj):
    for attr in TEMP_BAKE_ATTRS:
        full_attr = obj + "." + attr
        conns = cmds.listConnections(full_attr, source=True,
                                      plugs=True, destination=False) or []
        for conn in conns:
            conn_node = conn.split(".")[0]
            if "_TEMP_" in conn_node:
                try:
                    cmds.disconnectAttr(conn, full_attr)
                except Exception:
                    pass

def _restore_original_animation(controlled, temp_controls=None):
    control_original_data = _original_data_from_controls(temp_controls)

    for obj in controlled:
        if not cmds.objExists(obj):
            continue

        _disconnect_temp_connections(obj)
        try:
            long_obj = (cmds.ls(obj, long=True) or [obj])[0]
        except Exception:
            long_obj = obj
        ctrl_data = control_original_data.get(long_obj, {})
        orig_connections = _read_json_attr(obj, "tempOrigConn") or ctrl_data.get("connections", {})
        orig_values = _read_json_attr(obj, "tempOrigValues") or ctrl_data.get("values", {})

        for attr in TEMP_BAKE_ATTRS:
            full_attr = obj + "." + attr
            source = orig_connections.get(attr)
            if source and cmds.objExists(source) and cmds.objExists(full_attr):
                try:
                    if not cmds.isConnected(source, full_attr):
                        cmds.connectAttr(source, full_attr, force=True)
                    continue
                except Exception:
                    pass

            if attr in orig_values and cmds.objExists(full_attr):
                try:
                    if not cmds.getAttr(full_attr, lock=True):
                        cmds.setAttr(full_attr, orig_values[attr])
                except Exception:
                    pass

def _create_temp_bake_layer(controlled):
    layer = cmds.animLayer(
        _temp_anim_layer_name(),
        override=False,
        passthrough=True
    )

    for existing_layer in cmds.ls(type="animLayer") or []:
        try:
            cmds.animLayer(existing_layer, edit=True, selected=False, preferred=False)
        except Exception:
            pass

    cmds.animLayer(layer, edit=True, selected=True, preferred=True, mute=False, weight=1.0)

    for obj in controlled:
        if not cmds.objExists(obj):
            continue
        for attr in TEMP_BAKE_ATTRS:
            plug = obj + "." + attr
            if not cmds.objExists(plug):
                continue
            try:
                cmds.animLayer(layer, edit=True, attribute=plug)
            except Exception:
                pass

    return layer

def _key_temp_bake_layer(layer, final_samples, base_samples, start_frame, end_frame):
    keyed_channels = 0
    guard_frames = [int(start_frame) - 1, int(end_frame) + 1]

    for obj, obj_samples in final_samples.items():
        if not cmds.objExists(obj):
            continue

        for attr, values in obj_samples.items():
            base_values = dict(base_samples.get(obj, {}).get(attr, []))
            if not base_values:
                continue

            previous_delta = None
            is_rotate = attr.startswith("rotate")

            for guard_frame in guard_frames:
                try:
                    cmds.setKeyframe(
                        obj,
                        attribute=attr,
                        time=(guard_frame, guard_frame),
                        animLayer=layer,
                        noResolve=True,
                        value=0.0
                    )
                except Exception:
                    pass

            for frame, final_value in values:
                if frame not in base_values:
                    continue
                base_value = base_values[frame]
                if is_rotate:
                    delta = _shortest_angle_delta(final_value, base_value, previous_delta)
                    previous_delta = delta
                else:
                    delta = final_value - base_value

                try:
                    cmds.setKeyframe(
                        obj,
                        attribute=attr,
                        time=(frame, frame),
                        animLayer=layer,
                        noResolve=True,
                        value=delta
                    )
                    keyed_channels += 1
                except Exception:
                    pass

    try:
        cmds.animLayer(forceUIRefresh=True)
    except Exception:
        pass

    return keyed_channels

def get_tagged():
    tagged = cmds.ls("*.tempTag", o=True, long=True)
    return tagged if tagged else []

def _unique_existing(nodes):
    result = []
    seen = set()
    for node in nodes or []:
        if not node or not cmds.objExists(node):
            continue
        try:
            long_name = (cmds.ls(node, long=True) or [node])[0]
        except Exception:
            long_name = node
        if long_name in seen:
            continue
        seen.add(long_name)
        result.append(long_name)
    return result

def _owner_names(temp_controls):
    names = set()
    for ctrl in temp_controls or []:
        names.add(ctrl)
        names.add(ctrl.split("|")[-1])
        try:
            long_name = (cmds.ls(ctrl, long=True) or [ctrl])[0]
            names.add(long_name)
            names.add(long_name.split("|")[-1])
        except Exception:
            pass
    return names

def _owner_matches(node, owner_names):
    if not owner_names:
        return True
    if not cmds.objExists(node) or not cmds.attributeQuery("tempControlOwner", node=node, exists=True):
        return False
    try:
        owner = cmds.getAttr(node + ".tempControlOwner")
        return owner in owner_names or owner.split("|")[-1] in owner_names
    except Exception:
        return False

def _controlled_from_temp_network(temp_controls=None):
    owner_names = _owner_names(temp_controls)
    controlled = []

    for node in get_tagged():
        if not cmds.objExists(node) or not _owner_matches(node, owner_names):
            continue

        try:
            dest_plugs = cmds.listConnections(
                node,
                source=False,
                destination=True,
                plugs=True
            ) or []
        except Exception:
            dest_plugs = []

        for plug in dest_plugs:
            if "." not in plug:
                continue
            obj, attr = plug.rsplit(".", 1)
            if attr in TEMP_BAKE_ATTRS:
                controlled.append(obj)

    return _unique_existing(controlled)

def _remember_controlled_object(ctrl, obj):
    data = []
    if cmds.attributeQuery("tempControlledObjects", node=ctrl, exists=True):
        try:
            data = json.loads(cmds.getAttr(ctrl + ".tempControlledObjects") or "[]")
        except Exception:
            data = []

    try:
        long_obj = (cmds.ls(obj, long=True) or [obj])[0]
    except Exception:
        long_obj = obj

    if long_obj not in data:
        data.append(long_obj)

    if not cmds.attributeQuery("tempControlledObjects", node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln="tempControlledObjects", dt="string")
    cmds.setAttr(ctrl + ".tempControlledObjects", json.dumps(data), type="string")

def _remember_original_data(ctrl, obj, connections, values):
    data = {}
    if cmds.attributeQuery("tempOriginalData", node=ctrl, exists=True):
        try:
            data = json.loads(cmds.getAttr(ctrl + ".tempOriginalData") or "{}")
        except Exception:
            data = {}

    try:
        long_obj = (cmds.ls(obj, long=True) or [obj])[0]
    except Exception:
        long_obj = obj

    data[long_obj] = {
        "connections": connections or {},
        "values": values or {}
    }

    if not cmds.attributeQuery("tempOriginalData", node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln="tempOriginalData", dt="string")
    cmds.setAttr(ctrl + ".tempOriginalData", json.dumps(data), type="string")

def _original_data_from_controls(temp_controls=None):
    data = {}
    controls = temp_controls or [
        node for node in get_tagged()
        if cmds.objExists(node) and node.split("|")[-1].startswith("TEMP_CTRL")
    ]

    for ctrl in controls:
        if not cmds.objExists(ctrl) or not cmds.attributeQuery("tempOriginalData", node=ctrl, exists=True):
            continue
        try:
            ctrl_data = json.loads(cmds.getAttr(ctrl + ".tempOriginalData") or "{}")
            data.update(ctrl_data)
        except Exception:
            pass

    return data

def _controlled_from_control_attrs(temp_controls=None):
    controlled = []
    controls = temp_controls or [
        node for node in get_tagged()
        if cmds.objExists(node) and node.split("|")[-1].startswith("TEMP_CTRL")
    ]

    for ctrl in controls:
        if not cmds.objExists(ctrl) or not cmds.attributeQuery("tempControlledObjects", node=ctrl, exists=True):
            continue
        try:
            controlled.extend(json.loads(cmds.getAttr(ctrl + ".tempControlledObjects") or "[]"))
        except Exception:
            pass

    return _unique_existing(controlled)

def _temp_control_cleanup_nodes(temp_controls=None):
    nodes = []
    transforms = cmds.ls("TEMP_CTRL*", type="transform", long=True) or []

    if temp_controls:
        owner_names = _owner_names(temp_controls)
        for node in transforms:
            short = node.split("|")[-1]
            for owner in owner_names:
                owner_short = owner.split("|")[-1]
                if short == owner_short or short == owner_short + "_OFFSET":
                    nodes.append(node)
                    break
    else:
        nodes.extend(transforms)

    return _unique_existing(nodes)

def get_frame_range():
    """Obtiene el rango del timeline."""
    start = int(cmds.playbackOptions(query=True, minTime=True))
    end = int(cmds.playbackOptions(query=True, maxTime=True))
    return start, end

def get_smart_frame_range(objects):
    """
    Bake inteligente: detecta el rango real de keyframes
    de los objetos controlados y locators temporales.
    Usa el rango mas amplio entre keys existentes y timeline.
    """
    key_times = []
    
    # Buscar keyframes en los locators temporales (tienen la animacion bakeada)
    tagged = get_tagged()
    for node in tagged:
        if not cmds.objExists(node):
            continue
        if "_TEMP_loc" in node:
            keys = cmds.keyframe(node, query=True, timeChange=True)
            if keys:
                key_times.extend(keys)
    
    # Tambien verificar los objetos controlados
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        keys = cmds.keyframe(obj, query=True, timeChange=True)
        if keys:
            key_times.extend(keys)
    
    if key_times:
        key_start = int(min(key_times))
        key_end = int(max(key_times))
    else:
        key_start, key_end = get_frame_range()
    
    # Usar el rango del timeline como referencia
    tl_start, tl_end = get_frame_range()
    
    # Usar el rango mas amplio
    final_start = min(key_start, tl_start)
    final_end = max(key_end, tl_end)
    
    return final_start, final_end

def get_controlled_objects():
    """Buscar todos los objetos marcados como controlados.
    Usa atributos guardados y conexiones temporales como fallback."""
    result = cmds.ls("*.tempControlled", o=True, long=True) or []
    controlled = []
    for obj in result:
        try:
            if cmds.getAttr(obj + ".tempControlled"):
                controlled.append(obj)
        except Exception:
            pass

    controlled.extend(_controlled_from_control_attrs())
    controlled.extend(_controlled_from_temp_network())
    return _unique_existing(controlled)

def get_selected_temp_controls():
    selected = cmds.ls(selection=True, long=True) or []
    result = []
    for node in selected:
        if not cmds.objExists(node):
            continue
        if cmds.objectType(node, isAType="shape"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if parents:
                node = parents[0]
        try:
            if cmds.attributeQuery("tempTag", node=node, exists=True) and cmds.getAttr(node + ".tempTag") == TOOL_TAG:
                if node.split("|")[-1].startswith("TEMP_CTRL"):
                    result.append(node)
        except Exception:
            pass
    return _unique_existing(result)

def filter_controlled_by_temp_controls(controlled, temp_controls):
    if not temp_controls:
        return controlled
    owner_names = set()
    for ctrl in temp_controls:
        owner_names.add(ctrl)
        owner_names.add(ctrl.split("|")[-1])
    filtered = []
    for obj in controlled:
        if not cmds.objExists(obj):
            continue
        try:
            if cmds.attributeQuery("tempControlOwner", node=obj, exists=True):
                owner = cmds.getAttr(obj + ".tempControlOwner")
                if owner in owner_names or owner.split("|")[-1] in owner_names:
                    filtered.append(obj)
        except Exception:
            pass
    filtered.extend(_controlled_from_control_attrs(temp_controls))
    filtered.extend(_controlled_from_temp_network(temp_controls))
    return _unique_existing(filtered)

def create_temp_control_for_object(ctrl, ctrl_grp, obj, start_frame, end_frame):
    base_name = obj.split(":")[-1].split("|")[-1]
    obj_parent = cmds.listRelatives(obj, parent=True, fullPath=True)
    is_joint = cmds.nodeType(obj) == "joint"
    
    # --- Marcar objeto como controlado ---
    _remember_controlled_object(ctrl, obj)
    try:
        if not cmds.attributeQuery("tempControlled", node=obj, exists=True):
            cmds.addAttr(obj, ln="tempControlled", at="bool")
        cmds.setAttr(obj + ".tempControlled", True)
        add_string_attr(obj, "tempControlOwner", ctrl)
    except Exception as e:
        cmds.warning("Temp Control: could not tag {} directly: {}".format(obj, e))
    
    # --- 1. Crear locator world-space ---
    loc = cmds.spaceLocator(name=base_name + "_TEMP_loc")[0]
    tag(loc)
    add_string_attr(loc, "tempControlOwner", ctrl)
    cmds.setAttr(loc + ".visibility", 0)
    
    # --- 2. Constraint LOC -> objeto (LOC sigue al objeto) ---
    temp_con = cmds.parentConstraint(obj, loc, maintainOffset=False)[0]
    
    # --- 3. Bake LOC para capturar animacion world-space ---
    cmds.bakeResults(
        loc,
        time=(start_frame, end_frame),
        sampleBy=1,
        simulation=True,
        minimizeRotation=True,
        disableImplicitControl=True,
        preserveOutsideKeys=False,
        sparseAnimCurveBake=False,
        controlPoints=False,
        shape=False
    )
    
    # --- 4. Borrar constraint temporal ---
    cmds.delete(temp_con)
    _parent_under_temp_container(loc)
    
    # --- 5. Guardar y desconectar animacion del objeto ---
    orig_connections = {}
    attrs_to_check = list(TEMP_BAKE_ATTRS)
    orig_values = {}
    for attr in attrs_to_check:
        try:
            orig_values[attr] = cmds.getAttr(obj + "." + attr)
        except Exception:
            pass
    
    for attr in attrs_to_check:
        full_attr = obj + "." + attr
        conns = cmds.listConnections(full_attr, source=True, 
                                      plugs=True, destination=False)
        if conns:
            orig_connections[attr] = conns[0]
            try:
                cmds.disconnectAttr(conns[0], full_attr)
            except Exception:
                pass
    
    # Guardar conexiones para restauracion
    if orig_connections:
        if not cmds.attributeQuery("tempOrigConn", node=obj, exists=True):
            cmds.addAttr(obj, ln="tempOrigConn", dt="string")
        cmds.setAttr(obj + ".tempOrigConn", 
                     json.dumps(orig_connections), type="string")

    if orig_values:
        if not cmds.attributeQuery("tempOrigValues", node=obj, exists=True):
            cmds.addAttr(obj, ln="tempOrigValues", dt="string")
        cmds.setAttr(obj + ".tempOrigValues",
                     json.dumps(orig_values), type="string")

    _remember_original_data(ctrl, obj, orig_connections, orig_values)
    
    # --- 6. Construir red de matrices ---
    mult = cmds.createNode("multMatrix", name=base_name + "_TEMP_multMat")
    tag(mult)
    add_string_attr(mult, "tempControlOwner", ctrl)
    _parent_under_temp_container(mult)
    
    idx = 0
    cmds.connectAttr(loc + ".worldMatrix[0]", 
                     mult + ".matrixIn[{}]".format(idx))
    idx += 1
    
    cmds.connectAttr(ctrl_grp + ".worldInverseMatrix", 
                     mult + ".matrixIn[{}]".format(idx))
    idx += 1
    
    cmds.connectAttr(ctrl + ".worldMatrix[0]", 
                     mult + ".matrixIn[{}]".format(idx))
    idx += 1
    
    if obj_parent:
        cmds.connectAttr(obj_parent[0] + ".worldInverseMatrix[0]", 
                         mult + ".matrixIn[{}]".format(idx))
        idx += 1
    
    # --- 7. Decompose matrix ---
    decomp = cmds.createNode("decomposeMatrix", name=base_name + "_TEMP_decomp")
    tag(decomp)
    add_string_attr(decomp, "tempControlOwner", ctrl)
    _parent_under_temp_container(decomp)
    
    ro = cmds.getAttr(obj + ".rotateOrder")
    cmds.setAttr(decomp + ".inputRotateOrder", ro)
    cmds.connectAttr(mult + ".matrixSum", decomp + ".inputMatrix")
    
    # --- 8. Conectar resultado al objeto ---
    if is_joint:
        jo_x = cmds.getAttr(obj + ".jointOrientX")
        jo_y = cmds.getAttr(obj + ".jointOrientY")
        jo_z = cmds.getAttr(obj + ".jointOrientZ")
        
        has_joint_orient = (abs(jo_x) > 0.001 or 
                           abs(jo_y) > 0.001 or 
                           abs(jo_z) > 0.001)
        
        if has_joint_orient:
            jo_compose = cmds.createNode("composeMatrix", 
                                         name=base_name + "_TEMP_joCompose")
            tag(jo_compose)
            add_string_attr(jo_compose, "tempControlOwner", ctrl)
            _parent_under_temp_container(jo_compose)
            cmds.setAttr(jo_compose + ".inputRotateX", jo_x)
            cmds.setAttr(jo_compose + ".inputRotateY", jo_y)
            cmds.setAttr(jo_compose + ".inputRotateZ", jo_z)
            cmds.setAttr(jo_compose + ".inputRotateOrder", ro)
            
            jo_inv = cmds.createNode("inverseMatrix", 
                                     name=base_name + "_TEMP_joInv")
            tag(jo_inv)
            add_string_attr(jo_inv, "tempControlOwner", ctrl)
            _parent_under_temp_container(jo_inv)
            cmds.connectAttr(jo_compose + ".outputMatrix", 
                           jo_inv + ".inputMatrix")
            
            mult_jo = cmds.createNode("multMatrix", 
                                      name=base_name + "_TEMP_multJO")
            tag(mult_jo)
            add_string_attr(mult_jo, "tempControlOwner", ctrl)
            _parent_under_temp_container(mult_jo)
            cmds.connectAttr(mult + ".matrixSum", 
                           mult_jo + ".matrixIn[0]")
            cmds.connectAttr(jo_inv + ".outputMatrix", 
                           mult_jo + ".matrixIn[1]")
            
            decomp_jo = cmds.createNode("decomposeMatrix", 
                                        name=base_name + "_TEMP_decompJO")
            tag(decomp_jo)
            add_string_attr(decomp_jo, "tempControlOwner", ctrl)
            _parent_under_temp_container(decomp_jo)
            cmds.setAttr(decomp_jo + ".inputRotateOrder", ro)
            cmds.connectAttr(mult_jo + ".matrixSum", 
                           decomp_jo + ".inputMatrix")
            
            for axis in ['X', 'Y', 'Z']:
                cmds.connectAttr(
                    decomp + ".outputTranslate" + axis, 
                    obj + ".translate" + axis, force=True)
                cmds.connectAttr(
                    decomp_jo + ".outputRotate" + axis, 
                    obj + ".rotate" + axis, force=True)
        else:
            for axis in ['X', 'Y', 'Z']:
                cmds.connectAttr(
                    decomp + ".outputTranslate" + axis, 
                    obj + ".translate" + axis, force=True)
                cmds.connectAttr(
                    decomp + ".outputRotate" + axis, 
                    obj + ".rotate" + axis, force=True)
    else:
        for axis in ['X', 'Y', 'Z']:
            cmds.connectAttr(
                decomp + ".outputTranslate" + axis, 
                obj + ".translate" + axis, force=True)
            cmds.connectAttr(
                decomp + ".outputRotate" + axis, 
                obj + ".rotate" + axis, force=True)
    
    return loc

def create_follow_system(ctrl, grp, locators, enabled=True):
    if not locators:
        return None
    add_follow_attr(ctrl, default=enabled)
    follow_con = cmds.parentConstraint(*(locators + [grp]), maintainOffset=True)[0]
    tag(follow_con)
    add_string_attr(follow_con, "tempControlOwner", ctrl)
    _parent_under_temp_container(follow_con)
    weights = cmds.parentConstraint(follow_con, query=True, weightAliasList=True) or []
    for weight_attr in weights:
        try:
            cmds.connectAttr(ctrl + ".follow", follow_con + "." + weight_attr, force=True)
        except Exception:
            pass
    return follow_con

def create_group_control(objects, pivot_mode="center", follow=True):
    if not objects:
        return None
    
    objects = [obj for obj in objects if cmds.objExists(obj)]
    if not objects:
        return None
    
    _create_temp_controls_container()
    start_frame, end_frame = get_frame_range()
    
    # Determinar posicion y rotacion del control
    ctrl_rot = [0, 0, 0]
    if pivot_mode == "last":
        ctrl_pos = cmds.xform(objects[-1], q=True, ws=True, 
                              rotatePivot=True)
        ctrl_rot = cmds.xform(objects[-1], q=True, ws=True, 
                              rotation=True)
    else:
        # Centro (promedio de pivotes)
        positions = [cmds.xform(obj, q=True, ws=True, rotatePivot=True) 
                     for obj in objects]
        ctrl_pos = [sum(p[i] for p in positions) / len(positions) 
                    for i in range(3)]
    
    # Nombre unico
    ctrl_name = "TEMP_CTRL"
    existing = cmds.ls("TEMP_CTRL*", type="transform")
    if existing:
        ctrl_name = "{}_{}".format(ctrl_name, len(existing) + 1)
    
    # Crear control (NURBS circle)
    ctrl = cmds.circle(name=ctrl_name, radius=4, 
                       normal=(0, 1, 0), sections=16)[0]
    
    shape = cmds.listRelatives(ctrl, shapes=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17)
    
    # Posicionar control en world-space
    cmds.xform(ctrl, ws=True, translation=ctrl_pos)
    cmds.xform(ctrl, ws=True, rotation=ctrl_rot)
    
    # Grupo offset (absorbe la posicion, ctrl queda en cero local)
    grp = cmds.group(ctrl, name=ctrl_name + "_OFFSET")
    tag(ctrl)
    tag(grp)
    add_string_attr(ctrl, "tempControlOwner", ctrl)
    add_string_attr(grp, "tempControlOwner", ctrl)
    add_follow_attr(ctrl, default=follow)
    
    # Freeze control para que sus valores locales sean cero
    cmds.makeIdentity(ctrl, apply=True, translate=True, 
                      rotate=True, scale=True)
    _parent_under_temp_container(grp)
    
    # Procesar objetos
    success = 0
    locators = []
    for obj in objects:
        try:
            loc = create_temp_control_for_object(
                ctrl, grp, obj, start_frame, end_frame)
            if loc:
                locators.append(loc)
            success += 1
        except Exception as e:
            cmds.warning("Temp Control: failed to connect {}: {}".format(obj, e))

    if success:
        create_follow_system(ctrl, grp, locators, enabled=follow)
    else:
        cmds.warning("Temp Control: no objects were connected.")
    
    cmds.select(ctrl, replace=True)
    return ctrl

def create_individual_controls(objects):
    if not objects:
        return []
    
    _create_temp_controls_container()
    start_frame, end_frame = get_frame_range()
    controls = []
    
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        
        base_name = obj.split(":")[-1].split("|")[-1]
        ctrl_name = "TEMP_CTRL_{}".format(base_name)
        
        ctrl = cmds.circle(name=ctrl_name, radius=2.5, 
                          normal=(0, 1, 0), sections=12)[0]
        
        pos = cmds.xform(obj, q=True, ws=True, rotatePivot=True)
        rot = cmds.xform(obj, q=True, ws=True, rotation=True)
        cmds.xform(ctrl, ws=True, translation=pos)
        cmds.xform(ctrl, ws=True, rotation=rot)
        
        shape = cmds.listRelatives(ctrl, shapes=True)[0]
        cmds.setAttr(shape + ".overrideEnabled", 1)
        cmds.setAttr(shape + ".overrideColor", 13)
        
        grp = cmds.group(ctrl, name=ctrl_name + "_OFFSET")
        tag(ctrl)
        tag(grp)
        add_string_attr(ctrl, "tempControlOwner", ctrl)
        add_string_attr(grp, "tempControlOwner", ctrl)
        add_follow_attr(ctrl, default=True)
        
        cmds.makeIdentity(ctrl, apply=True, translate=True, 
                          rotate=True, scale=True)
        _parent_under_temp_container(grp)
        
        try:
            loc = create_temp_control_for_object(
                ctrl, grp, obj, start_frame, end_frame)
            # For individual mode: constrain the group to follow
            # the object's baked locator so the control "sticks"
            # to the animated object.
            if loc:
                create_follow_system(ctrl, grp, [loc], enabled=True)
            controls.append(ctrl)
        except Exception as e:
            cmds.warning("Temp Control: failed to connect {}: {}".format(obj, e))
    
    if controls:
        cmds.select(controls, replace=True)
    
    return controls

def smart_bake_and_delete():
    selected_temp_controls = get_selected_temp_controls()
    controlled = get_controlled_objects()
    controlled = filter_controlled_by_temp_controls(controlled, selected_temp_controls)
    if not controlled:
        orphan_nodes = _temp_control_cleanup_nodes(selected_temp_controls if selected_temp_controls else None)
        if orphan_nodes or get_tagged():
            _cleanup_temp_system([], selected_temp_controls if selected_temp_controls else None)
            try:
                cmds.inViewMessage(
                    amg="<hl>Temp Control cleanup</hl> complete",
                    pos='midCenter',
                    fade=True
                )
            except Exception:
                pass
            return "Temp Control cleanup"
        if selected_temp_controls:
            cmds.warning("Temp Control: selected temp control has no connected objects to bake.")
        else:
            cmds.warning("Temp Control: no active temp controls found.")
        return False
    
    start_frame, end_frame = get_smart_frame_range(controlled)
    
    cmds.undoInfo(openChunk=True)
    try:
        final_samples = _sample_transform_values(controlled, start_frame, end_frame)

        _restore_original_animation(controlled, selected_temp_controls)
        base_samples = _sample_transform_values(controlled, start_frame, end_frame)

        layer = _create_temp_bake_layer(controlled)
        keyed_channels = _key_temp_bake_layer(
            layer,
            final_samples,
            base_samples,
            start_frame,
            end_frame
        )

        if keyed_channels < 1:
            try:
                cmds.delete(layer)
            except Exception:
                pass
            return False

        _cleanup_temp_system(controlled, selected_temp_controls if selected_temp_controls else None)
        existing_controlled = [obj for obj in controlled if cmds.objExists(obj)]
        if existing_controlled:
            cmds.select(existing_controlled, replace=True)

        try:
            cmds.inViewMessage(
                amg="<hl>Temp Control baked</hl> to <hl>{}</hl>".format(layer),
                pos='midCenter',
                fade=True
            )
        except Exception:
            pass

        return layer
    except Exception as e:
        try:
            om.MGlobal.displayError("Temp Control Smart Bake error: {}".format(e))
        except Exception:
            pass
        return False
    finally:
        cmds.undoInfo(closeChunk=True)

def _cleanup_temp_system(controlled=None, temp_controls=None):
    if controlled is None:
        controlled = get_controlled_objects()
    
    for obj in controlled:
        if not cmds.objExists(obj):
            continue
        try:
            # Desconectar conexiones temporales
            for attr in ['translateX', 'translateY', 'translateZ',
                         'rotateX', 'rotateY', 'rotateZ']:
                full_attr = obj + "." + attr
                conns = cmds.listConnections(full_attr, source=True, 
                                              plugs=True, destination=False)
                if conns:
                    for conn in conns:
                        conn_node = conn.split(".")[0]
                        if "_TEMP_" in conn_node:
                            try:
                                cmds.disconnectAttr(conn, full_attr)
                            except:
                                pass
            
            # Limpiar atributos
            for temp_attr in ["tempControlled", "tempOrigConn", "tempOrigValues", "tempControlOwner"]:
                if cmds.attributeQuery(temp_attr, node=obj, exists=True):
                    try:
                        cmds.setAttr(obj + "." + temp_attr, lock=False)
                        cmds.deleteAttr(obj + "." + temp_attr)
                    except:
                        pass
        except:
            pass
    
    tagged = get_tagged()
    if temp_controls:
        owner_names = set()
        for ctrl in temp_controls:
            owner_names.add(ctrl)
            owner_names.add(ctrl.split("|")[-1])
        tagged_filtered = []
        for node in tagged:
            try:
                if cmds.attributeQuery("tempControlOwner", node=node, exists=True):
                    owner = cmds.getAttr(node + ".tempControlOwner")
                    if owner in owner_names or owner.split("|")[-1] in owner_names:
                        tagged_filtered.append(node)
            except Exception:
                pass
        tagged = tagged_filtered
    tagged.extend(_temp_control_cleanup_nodes(temp_controls))
    tagged = _unique_existing(tagged)
    if tagged:
        try:
            cmds.delete(tagged)
        except:
            pass
    _cleanup_empty_temp_container()

def delete_temp_system():
    _cleanup_temp_system()
    return True

# =============================================================================
# UI COMPONENTS (Matching Retimer)
# =============================================================================

class TitleBar(QtWidgets.QWidget):
    def __init__(self, parent=None, title="Temp Control"):
        super(TitleBar, self).__init__(parent)
        self.parent_window = parent
        self._drag_pos = None
        self.setFixedHeight(32)
        self.setup_ui(title)
        
    def setup_ui(self, title):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(8)
        
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setStyleSheet("""
            QLabel {
                color: #AAA;
                font-size: 11px;
                font-weight: 500;
            }
        """)
        layout.addWidget(self.title_label)
        
        layout.addStretch()
        
        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 12px;
                font-weight: bold;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        self.close_btn.clicked.connect(self.close_window)
        layout.addWidget(self.close_btn)
        
        self.setStyleSheet("""
            TitleBar {
                background-color: #363636;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
        """)
        
    def close_window(self):
        if self.parent_window:
            self.parent_window.close()
            
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.parent_window.frameGeometry().topLeft()
            event.accept()
            
    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.LeftButton and self._drag_pos:
            if hasattr(self.parent_window, "detach_from_anchor"):
                self.parent_window.detach_from_anchor()
            self.parent_window.move(event.globalPos() - self._drag_pos)
            event.accept()
            
    def mouseReleaseEvent(self, event):
        if hasattr(self.parent_window, "maybe_attach_to_anchor"):
            self.parent_window.maybe_attach_to_anchor()
        self._drag_pos = None

class TempPivotWindow(ContextPopupWindow):
    def __init__(self, anchor_button=None, parent=None):
        super(TempPivotWindow, self).__init__(anchor_button=anchor_button, parent=parent)
        
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle('Temp Control')
        self.setFixedSize(320, 490)
        
        self.anchor_button = anchor_button
        self._tail_height = 10
        self._tail_width = 16
        self._tail_x = self.width() // 2
        self._tail_on_top = False
        self._magnet_attached = True
        
        self._base_opacity = 0.5
        self._hover_opacity = 1.0
        self._anim = None
        
        self._setup_ui()
        
        # Setup selection job (killWithScene para limpieza automatica)
        self.selection_job = cmds.scriptJob(
            event=["SelectionChanged", self._on_selection_changed],
            killWithScene=True
        )
        
        # Start timer for status updates (3s para no sobrecargar)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update_status)
        self.timer.start(3000)
        
        self._on_selection_changed()
        self._update_status()
        self.position_window()
        
        self.setWindowOpacity(self._base_opacity)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        bg = QtGui.QColor(58, 58, 58)
        border_color = QtGui.QColor(90, 90, 90)

        tail_height = self._tail_height if self._magnet_attached else 0

        if self._tail_on_top and self._magnet_attached:
            body_rect = QtCore.QRectF(
                0.5,
                tail_height + 0.5,
                self.width() - 1,
                self.height() - tail_height - 1
            )
        else:
            body_rect = QtCore.QRectF(
                0.5,
                0.5,
                self.width() - 1,
                self.height() - tail_height - 1
            )

        path = QtGui.QPainterPath()
        path.addRoundedRect(body_rect, 10, 10)

        tail_cx = max(20, min(self._tail_x, self.width() - 20))
        half_width = self._tail_width / 2.0

        if self._magnet_attached and self._tail_on_top:
            tail_base = body_rect.top()
            path.moveTo(tail_cx - half_width, tail_base)
            path.lineTo(tail_cx, tail_base - tail_height)
            path.lineTo(tail_cx + half_width, tail_base)
            path.closeSubpath()
        elif self._magnet_attached:
            tail_base = body_rect.bottom()
            path.moveTo(tail_cx - half_width, tail_base)
            path.lineTo(tail_cx, tail_base + tail_height)
            path.lineTo(tail_cx + half_width, tail_base)
            path.closeSubpath()

        self.setMask(path.toFillPolygon().toPolygon())
        painter.setPen(QtGui.QPen(border_color, 1))
        painter.setBrush(bg)
        painter.drawPath(path)
        painter.end()

    def position_window(self, force=False):
        if not self._magnet_attached and not force:
            return
        if self.anchor_button is None:
            cursor_pos = QtGui.QCursor.pos()
            self.move(cursor_pos.x() - self.width() // 2,
                      cursor_pos.y() - self.height() - 10)
            return

        try:
            btn_rect = self.anchor_button.rect()
            btn_top_left = self.anchor_button.mapToGlobal(btn_rect.topLeft())
            btn_center_x = btn_top_left.x() + btn_rect.width() // 2
            btn_top_y = btn_top_left.y()
            btn_bottom_y = btn_top_y + btn_rect.height()

            screen = QtWidgets.QApplication.screenAt(btn_top_left) if hasattr(QtWidgets.QApplication, 'screenAt') else None
            if screen:
                screen_rect = screen.availableGeometry()
            else:
                desktop = QtWidgets.QApplication.desktop()
                screen_rect = desktop.availableGeometry(self.anchor_button)

            x_pos = btn_center_x - self.width() // 2
            if x_pos < screen_rect.left():
                x_pos = screen_rect.left()
            elif x_pos + self.width() > screen_rect.right():
                x_pos = screen_rect.right() - self.width()

            if btn_top_y - screen_rect.top() >= self.height():
                y_pos = btn_top_y - self.height()
                self._tail_on_top = False
            else:
                y_pos = btn_bottom_y
                self._tail_on_top = True

            self.move(x_pos, y_pos)
            self._tail_x = btn_center_x - x_pos
            if self.layout():
                if self._tail_on_top:
                    self.layout().setContentsMargins(1, self._tail_height + 1, 1, 1)
                else:
                    self.layout().setContentsMargins(1, 1, 1, self._tail_height + 1)
            self.update()
        except Exception:
            cursor_pos = QtGui.QCursor.pos()
            self.move(cursor_pos.x() - self.width() // 2,
                      cursor_pos.y() - self.height() - 10)
            self._tail_x = self.width() // 2
            self._tail_on_top = False
            if self.layout():
                self.layout().setContentsMargins(1, 1, 1, self._tail_height + 1)
            self.update()
    
    def detach_from_anchor(self):
        if not self._magnet_attached:
            return
        self._magnet_attached = False
        if self.layout():
            self.layout().setContentsMargins(1, 1, 1, 1)
        self.clearMask()
        self.update()

    def maybe_attach_to_anchor(self):
        if self.anchor_button is None:
            return
        current_pos = self.pos()
        was_attached = self._magnet_attached
        self._magnet_attached = True
        self.position_window(force=True)
        target_pos = self.pos()
        self.move(current_pos)
        self._magnet_attached = was_attached

        if (current_pos - target_pos).manhattanLength() <= 42:
            self._magnet_attached = True
            self.position_window(force=True)

    def animkey_auto_hide(self):
        if self._magnet_attached:
            self.hide()
            return True
        return False
    
    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, self._tail_height + 1)
        main_layout.setSpacing(0)
        main_layout.setSpacing(0)
        
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet("""
            QFrame#mainContainer {
                background-color: transparent;
                border: none;
            }
        """)
        
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # Header, matching Gimbal Fixer.
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(10, 10, 10, 10)
        self.title_label = QtWidgets.QLabel("Temp Control")
        self.title_label.setStyleSheet("color: #AAA; font-size: 11px; font-weight: 500; border: none;")
        header.addWidget(self.title_label)
        header.addStretch()

        close_btn = QtWidgets.QPushButton("X")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        container_layout.addLayout(header)
        
        content = QtWidgets.QWidget()
        content.setStyleSheet("background-color: #3a3a3a;")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(15, 10, 15, 15)
        content_layout.setSpacing(10)
        
        # --- SECTION: MODE ---
        mode_label = QtWidgets.QLabel("CONTROL MODE")
        mode_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 1px;")
        content_layout.addWidget(mode_label)
        
        self.mode_group = QtWidgets.QButtonGroup(self)
        
        mode_layout = QtWidgets.QHBoxLayout()
        self.radio_indiv = QtWidgets.QRadioButton("Individual")
        self.radio_group = QtWidgets.QRadioButton("Group")
        
        radio_style = """
            QRadioButton {
                color: #AAA;
                font-size: 11px;
            }
            QRadioButton::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #666666;
                border-radius: 7px;
                background-color: #444444;
            }
            QRadioButton::indicator:checked {
                background-color: #3498DB;
                border-color: #3498DB;
            }
        """
        self.radio_indiv.setStyleSheet(radio_style)
        self.radio_group.setStyleSheet(radio_style)
        
        self.mode_group.addButton(self.radio_indiv, 1)
        self.mode_group.addButton(self.radio_group, 2)
        self.radio_indiv.setChecked(True)
        
        mode_layout.addWidget(self.radio_indiv)
        mode_layout.addWidget(self.radio_group)
        content_layout.addLayout(mode_layout)
        
        # --- SECTION: PIVOT OPTIONS (for Group) ---
        self.pivot_options_widget = QtWidgets.QWidget()
        pivot_options_layout = QtWidgets.QVBoxLayout(self.pivot_options_widget)
        pivot_options_layout.setContentsMargins(10, 10, 10, 10)
        pivot_options_layout.setSpacing(8)
        self.pivot_options_widget.setStyleSheet("""
            QWidget {
                background-color: #444444;
                border-radius: 6px;
            }
        """)
        
        pivot_title = QtWidgets.QLabel("GROUP PIVOT POSITION")
        pivot_title.setStyleSheet("color: #888; font-size: 9px; font-weight: bold;")
        pivot_options_layout.addWidget(pivot_title)
        
        self.pivot_group = QtWidgets.QButtonGroup(self)
        self.radio_center = QtWidgets.QRadioButton("Center of All")
        self.radio_last = QtWidgets.QRadioButton("Last Selected")
        self.radio_center.setStyleSheet(radio_style)
        self.radio_last.setStyleSheet(radio_style)
        
        self.pivot_group.addButton(self.radio_center, 1)
        self.pivot_group.addButton(self.radio_last, 2)
        self.radio_center.setChecked(True)
        
        pivot_options_layout.addWidget(self.radio_center)
        pivot_options_layout.addWidget(self.radio_last)
        
        content_layout.addWidget(self.pivot_options_widget)

        self.follow_checkbox = QtWidgets.QCheckBox("Follow")
        self.follow_checkbox.setChecked(True)
        self.follow_checkbox.setToolTip("Group temp control follows the controlled objects without needing keys.")
        self.follow_checkbox.setStyleSheet("""
            QCheckBox {
                color: #AAA;
                font-size: 11px;
                background-color: #444444;
                border-radius: 6px;
                padding: 8px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #666666;
                border-radius: 3px;
                background-color: #333333;
            }
            QCheckBox::indicator:checked {
                background-color: #3498DB;
                border-color: #3498DB;
            }
        """)
        content_layout.addWidget(self.follow_checkbox)
        
        self.radio_indiv.toggled.connect(self._toggle_pivot_options)
        self.pivot_options_widget.setVisible(False)
        self.follow_checkbox.setVisible(False)
        
        # Spacer
        content_layout.addStretch()
        
        # --- SECTION: ACTIONS ---
        self.create_btn = QtWidgets.QPushButton("CREATE TEMP CONTROL")
        self.create_btn.setFixedHeight(46)
        self.create_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.create_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d5d3d;
                color: #a3be8c;
                font-size: 12px;
                font-weight: bold;
                border: 1px solid #5aaa5a;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #3d6d4d; border-color: #a3be8c; }
            QPushButton:pressed { background-color: #254a32; }
        """)
        self.create_btn.clicked.connect(self._on_create)
        content_layout.addWidget(self.create_btn)
        
        self.bake_btn = QtWidgets.QPushButton("SMART BAKE & DELETE")
        self.bake_btn.setFixedHeight(40)
        self.bake_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.bake_btn.setStyleSheet("""
            QPushButton {
                background-color: #4d2d2d;
                color: #ff6b6b;
                font-size: 11px;
                font-weight: bold;
                border: 1px solid #8a4a4a;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #5d3d3d; border-color: #ff6b6b; }
            QPushButton:pressed { background-color: #3d2525; }
        """)
        self.bake_btn.clicked.connect(self._on_bake)
        content_layout.addWidget(self.bake_btn)
        
        # --- SECTION: STATUS ---
        self.status_label = QtWidgets.QLabel("No active temp controls")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #666; font-size: 10px; margin-top: 10px;")
        content_layout.addWidget(self.status_label)
        
        container_layout.addWidget(content)
        main_layout.addWidget(self.container)
    
    def _toggle_pivot_options(self, checked):
        is_group = not checked
        self.pivot_options_widget.setVisible(is_group)
        self.follow_checkbox.setVisible(is_group)
    
    def _on_selection_changed(self):
        # Auto-detect mode (protegido contra errores)
        try:
            sel = cmds.ls(selection=True, transforms=True) or []
            joints = cmds.ls(selection=True, type="joint") or []
            all_sel = list(set(sel + joints))
            
            if len(all_sel) > 1:
                self.radio_group.setChecked(True)
            else:
                self.radio_indiv.setChecked(True)
            self._update_status()
        except Exception:
            pass
            
    def _update_status(self):
        try:
            selected_temp_controls = get_selected_temp_controls()
            controlled = filter_controlled_by_temp_controls(
                get_controlled_objects(),
                selected_temp_controls
            )
            if controlled:
                names = [c.split(":")[-1].split("|")[-1] for c in controlled]
                if len(names) > 3:
                    label = f"{len(names)} objects: {', '.join(names[:3])}..."
                else:
                    label = f"{len(names)} object(s): {', '.join(names)}"
                if selected_temp_controls:
                    ctrl_names = [c.split("|")[-1] for c in selected_temp_controls]
                    label = "{} -> {}".format(", ".join(ctrl_names), label)
                self.status_label.setText(label)
                self.status_label.setStyleSheet("color: #3498DB; font-size: 10px;")
            elif selected_temp_controls:
                names = [c.split("|")[-1] for c in selected_temp_controls]
                self.status_label.setText("{} selected, no controlled objects found".format(", ".join(names)))
                self.status_label.setStyleSheet("color: #ffca28; font-size: 10px;")
            else:
                self.status_label.setText("No active temp controls")
                self.status_label.setStyleSheet("color: #666; font-size: 10px;")
        except Exception:
            pass
            
    def _on_create(self):
        sel = cmds.ls(selection=True, long=True)
        all_sel = []
        for s in sel:
            if cmds.objectType(s) in ("transform", "joint"):
                all_sel.append(s)
            elif cmds.listRelatives(s, parent=True):
                parent = cmds.listRelatives(s, parent=True, fullPath=True)
                if parent and parent[0] not in all_sel:
                    all_sel.append(parent[0])
        
        if not all_sel:
            om.MGlobal.displayWarning("Select animated object(s) first.")
            return
            
        mode = 1 if self.radio_indiv.isChecked() else 2
        try:
            if mode == 1: # Individual
                create_individual_controls(all_sel)
            else: # Group
                pivot_id = 1 if self.radio_center.isChecked() else 2
                pivot_mode = "center" if pivot_id == 1 else "last"
                create_group_control(all_sel, pivot_mode, follow=self.follow_checkbox.isChecked())
            self._update_status()
        except Exception as e:
            om.MGlobal.displayError(f"Error creating temp control: {str(e)}")

    def _on_bake(self):
        baked_layer = smart_bake_and_delete()
        if baked_layer:
            self._update_status()
            om.MGlobal.displayInfo("Smart Bake complete: {}".format(baked_layer))
        else:
            om.MGlobal.displayWarning("Nothing to bake.")

    def enterEvent(self, e):
        self._animate(self._hover_opacity)
        super(TempPivotWindow, self).enterEvent(e)
        
    def leaveEvent(self, e):
        self._animate(self._base_opacity)
        super(TempPivotWindow, self).leaveEvent(e)
        
    def _animate(self, val):
        if self._anim is not None:
            try:
                if self._anim.state() == QtCore.QPropertyAnimation.Running:
                    self._anim.stop()
            except RuntimeError:
                self._anim = None
        self._anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(150)
        self._anim.setEndValue(val)
        self._anim.finished.connect(self._on_anim_finished)
        self._anim.start()

    def _on_anim_finished(self):
        self._anim = None

    def closeEvent(self, event):
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
            self._anim = None
        # Detener timer primero
        if hasattr(self, 'timer'):
            try:
                self.timer.stop()
            except Exception:
                pass
        # Matar scriptJob si existe
        if hasattr(self, 'selection_job'):
            try:
                if cmds.scriptJob(exists=self.selection_job):
                    cmds.scriptJob(kill=self.selection_job, force=True)
            except Exception:
                pass
        global _temp_pivot_window
        _temp_pivot_window = None
        super(TempPivotWindow, self).closeEvent(event)

# =============================================================================
# PUBLIC INTERFACE
# =============================================================================

def _kill_orphan_scriptjobs():
    """Matar scriptJobs huerfanos de instancias previas."""
    try:
        all_jobs = cmds.scriptJob(listJobs=True)
        for job_str in all_jobs:
            if "_on_selection_changed" in job_str and "SelectionChanged" in job_str:
                job_id = int(job_str.split(":")[0])
                try:
                    cmds.scriptJob(kill=job_id, force=True)
                except Exception:
                    pass
    except Exception:
        pass

def _is_valid_qt_widget(widget):
    if widget is None:
        return False
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False
    except Exception:
        return False

def show(anchor_button=None):
    global _temp_pivot_window
    from AnimKey.mods import uiMod

    if _is_valid_qt_widget(_temp_pivot_window):
        try:
            uiMod.close_animkey_tool_windows(except_widget=_temp_pivot_window)
            if anchor_button is not None:
                _temp_pivot_window.anchor_button = anchor_button
            if not _temp_pivot_window.isVisible():
                _temp_pivot_window.show()
            _temp_pivot_window.position_window()
            _temp_pivot_window.raise_()
            _temp_pivot_window.activateWindow()
            return _temp_pivot_window
        except Exception:
            _temp_pivot_window = None

    uiMod.close_animkey_tool_windows()
    
    # Limpiar scriptJobs huerfanos
    _kill_orphan_scriptjobs()
            
    if cmds.window(WINDOW_OBJECT, exists=True):
        cmds.deleteUI(WINDOW_OBJECT)
        
    _temp_pivot_window = TempPivotWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _temp_pivot_window.show()
    _temp_pivot_window.raise_()
    _temp_pivot_window.activateWindow()
    return _temp_pivot_window

def execute(*args, **kwargs):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tempPivot.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))


def execute_temp_pivot(*args, **kwargs):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.tempPivot.execute_temp_pivot"):
        return None
    button = kwargs.get("button")
    if is_active():
        deactivated = deactivate_temp_pivot()
        if deactivated:
            set_button_active(button, False)
        return False if deactivated else None

    result = activate_temp_pivot(
        objects=kwargs.get("objects"),
        pivot_mode=kwargs.get("pivot_mode", "last"),
        edit_pivot=kwargs.get("edit_pivot", True),
    )
    set_button_active(button, bool(result))
    return bool(result)


def is_active():
    try:
        return bool(
            cmds.manipPivot(query=True, valid=True)
            and cmds.manipPivot(query=True, pinPivot=True)
            and cmds.manipRotateContext("Rotate", query=True, useManipPivot=True)
        )
    except Exception:
        return False

def cleanup_orphans():
    delete_temp_system()
    
def set_button_active(button, active):
    if button is None:
        return

    try:
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        color = "#bf616a"

        if active:
            active_bg = "#8f454d"
            button.setStyleSheet(f'''
                QPushButton {{
                    color: #ffffff;
                    background-color: {active_bg};
                    border: 2px solid {color};
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {active_bg};
                    border-color: {color};
                }}
                QPushButton:pressed {{
                    background-color: {theme["button_pressed"]};
                }}
            ''')
        else:
            button.setStyleSheet(f'''
                QPushButton {{
                    background-color: {theme["button_bg"]};
                    border: 1px solid {theme["border_color"]};
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: {theme["button_hover"]};
                    border-color: {color};
                }}
                QPushButton:pressed {{
                    background-color: {theme["button_pressed"]};
                }}
            ''')
    except Exception:
        pass

if __name__ == "__main__":
    show()
