"""Persistent temporary animation systems for ANIMKEY.

Independent Maya implementation based on the supplied behavioural specs.
Scene message links own all rig associations; original animation networks stay
connected to holding plugs while a system is active, so Revert restores them
untouched. Bake is a smart bake onto the rig's own curves: original keys keep
their timing and keys are added only where the curve must change to match the
temporary controls exactly. Channels driven by animation layers or other graphs
fall back to an owned override layer so those networks are never rewritten.
"""
import json
import os
from contextlib import contextmanager

import maya.cmds as cmds
import maya.api.OpenMaya as om

from AnimKey.core import temp_transform as tf
from AnimKey.core import temp_shapes as shapes

SYSTEMS = ("Simple", "Group", "Aim", "FK Chain")
SPACES = ("World", "Object", "Relative", "Camera", "Child", "Grab Release")
TAG = "ANIMKEY_TEMP_CONTROLS_1"
ROOT = "AnimKey_Temp_Controls"
_window = None
_pivot_edit = None


def _create_node(*args, **kwargs):
    """Internal nodes never steal the animator's selection.

    A selected GrabOffset/Space node would become the next Grab driver and
    would narrow the systems chosen in the Temp Controls window.
    """
    kwargs.setdefault("skipSelect", True)
    return cmds.createNode(*args, **kwargs)


@contextmanager
def _keep_selection():
    previous = cmds.ls(selection=True, long=True) or []
    try:
        yield
    finally:
        remaining = [n for n in previous if cmds.objExists(n)]
        if remaining:
            cmds.select(remaining, replace=True)
        else:
            cmds.select(clear=True)


def _first(node, attr):
    links = tf.linked(node, attr)
    return links[0] if links else None


def _add(node, attr, kind, value, **kwargs):
    if not cmds.objExists(node + "." + attr):
        cmds.addAttr(node, longName=attr, attributeType=kind, **kwargs)
    cmds.setAttr(node + "." + attr, value)


def all_systems():
    return [n for n in cmds.ls("*.animKeyTempSystem", objectsOnly=True, long=True) or []
            if cmds.getAttr(n + ".animKeyTempSystem") == TAG]


def records(system):
    return [r for r in tf.linked(system, "members") if _first(r,"source")]


def controls(system):
    return tf.linked(system, "controls")


def _sources(system):
    return [_first(r, "source") for r in records(system) if _first(r, "source")]


def selected_systems(nodes=None):
    selected = cmds.ls(selection=True, long=True) if nodes is None else nodes
    found = []
    for system in all_systems():
        members = records(system)
        associated = [system] + controls(system) + _sources(system) + members
        for node in selected or []:
            names = cmds.ls(node, long=True) or []
            if any(n in associated or n.startswith(system + "|") for n in names):
                found.append(system)
                break
    return found


def _pack(system, node):
    tf.message(system, "packNodes", node, multi=True)
    return node


def _create_root():
    for system in all_systems():
        parents = cmds.listRelatives(system, parent=True, fullPath=True) or []
        if parents:
            return parents[0]
    root = _create_node("transform", name=ROOT + "#" if cmds.objExists(ROOT) else ROOT)
    tf.string(root, "animKeyTempRole", "root")
    return root


def _camera(nodes):
    for node in nodes:
        if cmds.listRelatives(node, shapes=True, type="camera"):
            return node
    if not cmds.about(batch=True):
        panel = cmds.getPanel(withFocus=True)
        if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
            camera = cmds.modelPanel(panel, query=True, camera=True)
            if cmds.nodeType(camera) == "camera":
                camera = (cmds.listRelatives(camera, parent=True, fullPath=True) or [camera])[0]
            return camera
    cameras = cmds.ls(type="camera", long=True) or []
    return (cmds.listRelatives(cameras[0], parent=True, fullPath=True) or [None])[0] if cameras else None


def _resolve(nodes, position, orientation, relative):
    references = {}
    sources = list(nodes)
    if 2 in (position, orientation) or 5 in (position, orientation):
        if relative:
            ref = tf.transforms(relative)
            if not ref:
                raise ValueError("Select a valid relative-space transform.")
            references[2] = references[5] = ref[-1]
        elif len(sources) > 1:
            references[2] = references[5] = sources.pop()
        elif 2 in (position, orientation):
            cmds.warning("Temp Controls: no relative reference; using World.")
    if 3 in (position, orientation):
        references[3] = _camera(nodes)
        sources = [n for n in sources if not cmds.listRelatives(n, shapes=True, type="camera")]
    return sources, references


def _validate(sources, references, system_id):
    if not sources:
        raise ValueError("Select at least one rig control.")
    if system_id == 3 and len(sources) < 2:
        raise ValueError("FK Chain requires at least two ordered transforms.")
    occupied = {source for system in all_systems() for source in _sources(system)}
    for source in sources:
        if source in occupied:
            raise ValueError("{} already belongs to Temp Controls.".format(source))
        if cmds.objExists(source + ".animKeyTempRole") or any(source.startswith(s + "|") for s in all_systems()):
            raise ValueError("Internal Temp Controls nodes cannot be rig sources.")
        available = tf.writable(source)
        # Animation/layer/utility inputs are supported; existing constraints on
        # an individual axis are left alone rather than forcibly disconnected.
        for attr in available[:]:
            incoming = cmds.listConnections(source + "." + attr, source=True, destination=False) or []
            if any(cmds.objectType(n, isAType="constraint") for n in incoming):
                available.remove(attr)
        if not available:
            raise ValueError("{} has no available translation/rotation channels.".format(source))
        for ref in references.values():
            if ref and (ref == source or ref.startswith(source + "|")):
                raise ValueError("A relative space cannot be the source or its descendant.")


def _new_control(system, name, parent, shape, size, source=None, color=None):
    node = shapes.create(name, parent, shape=shape, size=size, color=color or _color(system))
    tf.message(system, "controls", node, multi=True)
    if source:
        tf.message(node, "source", source)
        cmds.setAttr(node + ".rotateOrder", cmds.getAttr(source + ".rotateOrder"))
    _add(node, "pivotEditable", "bool", True)
    return node


def _color(system):
    return json.loads(tf.string(system, "color"))


def _make_member(system, source):
    record = _create_node("transform", name="Member#", parent=system)
    tf.message(system, "members", record, multi=True)
    tf.message(record, "source", source)
    tf.string(record, "animKeyTempRole", "member")
    attrs = tf.writable(source)
    for attr in attrs[:]:
        incoming = cmds.listConnections(source + "." + attr, source=True, destination=False) or []
        if any(cmds.objectType(n, isAType="constraint") for n in incoming):
            attrs.remove(attr)
    tf.string(record, "channels", json.dumps(attrs))
    # The reference evaluates the original local animation independently from
    # the now-driven rig source, including parent matrices and joint metadata.
    ghost = tf.unlocked_duplicate(source, "OriginalMotion#")
    ghost = cmds.parent(ghost, record, relative=True)[0]
    _pack(system, ghost)
    tf.message(record, "reference", ghost)
    cmds.setAttr(ghost + ".visibility", 0)
    if cmds.objExists(ghost + ".offsetParentMatrix"):
        cmds.connectAttr(source + ".parentMatrix[0]", ghost + ".offsetParentMatrix", force=True)
    for attr in tf.TR:
        cmds.setAttr(ghost + "." + attr, lock=False)
        _add(record, "hold_" + attr, "doubleAngle" if attr.startswith("rotate") else "doubleLinear", cmds.getAttr(source + "." + attr))
        input_plugs = cmds.listConnections(source + "." + attr, source=True, destination=False, plugs=True) or []
        if input_plugs:
            cmds.connectAttr(input_plugs[0], record + ".hold_" + attr, force=True)
        if input_plugs:
            cmds.connectAttr(input_plugs[0],ghost+"."+attr,force=True)
        else:
            cmds.setAttr(ghost+"."+attr,cmds.getAttr(source+"."+attr))
    return record


def _refresh_reference_motion(system):
    """Rebase spatial references only when transferring rig -> temp (Bake).

    Revert reactivation keeps the previous temporary animation and its original
    frame of reference. Rig recovery holders are separate from this baseline.
    """
    for record in records(system):
        source,ghost = _first(record,"source"),_first(record,"reference")
        for attr in tf.TR:
            plug = ghost+"."+attr
            incoming = cmds.listConnections(plug,source=True,destination=False,plugs=True) or []
            for old in incoming:
                cmds.disconnectAttr(old,plug)
            current = cmds.listConnections(source+"."+attr,source=True,destination=False,plugs=True) or []
            if current:
                cmds.connectAttr(current[0],plug,force=True)
            else:
                cmds.setAttr(plug,cmds.getAttr(source+"."+attr))


def _spatial_target(system, record, mode):
    source = _first(record, "source")
    if mode == 1:
        return (cmds.listRelatives(source, parent=True, fullPath=True) or [None])[0]
    if mode == 4:
        return _first(record, "reference")
    if mode in (2,3):
        return _first(system, "relativeSpace" if mode == 2 else "cameraSpace")
    return None


def _follow(system, target, driven, position=True, orientation=True, maintain=False):
    if not target:
        return
    kwargs = {"maintainOffset": maintain}
    if not position:
        kwargs["skipTranslate"] = ["x","y","z"]
    if not orientation:
        kwargs["skipRotate"] = ["x","y","z"]
    constraint = cmds.parentConstraint(target, driven, **kwargs)[0]
    _pack(system, constraint)
    tf.message(system, "spaceConstraints", constraint, multi=True)
    if position:
        constraint = cmds.scaleConstraint(target, driven, maintainOffset=maintain)[0]
        _pack(system, constraint)
        tf.message(system, "spaceConstraints", constraint, multi=True)


def _space(system, record, parent=None):
    node = _create_node("transform", name="Space#", parent=parent or system)
    _pack(system,node)
    tf.message(record, "space", node)
    position = cmds.getAttr(system + ".positionSpace")
    orientation = cmds.getAttr(system + ".orientationSpace")
    if position == orientation == 5:
        _grab_space(system, node)
    else:
        if position == 5 or orientation == 5:
            gr = _create_node("transform", name="GrabReleaseSpace#", parent=system)
            _grab_space(system, gr)
        else:
            gr = None
        p = gr if position == 5 else _spatial_target(system, record, position)
        o = gr if orientation == 5 else _spatial_target(system, record, orientation)
        _follow(system, p, node, orientation=False)
        _follow(system, o, node, position=False)
    return node


def _grab_events(system):
    return sorted(tf.linked(system, "grabEvents"), key=lambda e: cmds.getAttr(e + ".frame"))


def _grab_space(system, node):
    events = _grab_events(system)
    if not events:
        return
    targets = [_first(e,"target") for e in events]
    pc = cmds.parentConstraint(targets, node, maintainOffset=False)[0]
    _pack(system, pc)
    tf.message(system, "spaceConstraints", pc, multi=True)
    sc = cmds.scaleConstraint(targets, node, maintainOffset=False)[0]
    _pack(system, sc)
    tf.message(system, "spaceConstraints", sc, multi=True)
    for constraint, command in ((pc,cmds.parentConstraint),(sc,cmds.scaleConstraint)):
        aliases = command(constraint, query=True, weightAliasList=True)
        for index, alias in enumerate(aliases):
            for active, event in enumerate(events):
                cmds.setKeyframe(constraint + "." + alias, time=cmds.getAttr(event + ".frame"), value=int(index == active),
                                 inTangentType="auto", outTangentType="step")


def _create_event(system, frame, driver=None):
    event = _create_node("transform", name="GrabEvent#", parent=system)
    _add(event, "frame", "long", int(frame))
    tf.message(system, "grabEvents", event, multi=True)
    target = _create_node("transform", name="GrabOffset#", parent=event)
    tf.message(event, "target", target)
    if driver:
        tf.message(event, "driver", driver)
        _follow(system, driver, event)
    return event


def _normalise_events(system, y_up=False):
    events = _grab_events(system)
    reference_frame = cmds.currentTime(query=True)
    with tf.sampling():
        for previous, event in zip(events, events[1:]):
            cmds.currentTime(cmds.getAttr(event + ".frame"), edit=True)
            target = _first(event, "target")
            matrix = tf.world(_first(previous,"target"))
            cmds.xform(target, worldSpace=True, matrix=list(matrix))
        if y_up and events:
            frame = reference_frame
            cmds.currentTime(frame,edit=True)
            _add(system,"grabReleaseYUp","long",int(frame))
            # Align the active release frame; control compensation below keeps
            # existing keyed poses intact when this operation changes axes.
            active = [e for e in events if cmds.getAttr(e + ".frame") <= frame]
            if active:
                target = _first(active[-1],"target")
                cmds.xform(target, worldSpace=True, rotation=(0,0,0))


def _build(system):
    family = cmds.getAttr(system + ".systemID")
    members = records(system)
    master = None
    previous = None
    if family == 1:
        space = _space(system, members[0])
        motion = _pack(system, _create_node("transform", name="GroupMotion#", parent=space))
        tf.message(system, "groupMotion", motion)
        positions = [list(tf.world(n))[12:15] for n in _sources(system)]
        center = [sum(p[i] for p in positions) / len(positions) for i in range(3)]
        cmds.xform(motion, worldSpace=True, translation=center)
        master = _new_control(system, "Group_Ctrl#", motion, 7, shapes.dimension(_sources(system))*1.4)
        tf.message(system,"master",master)
    for record in members:
        source = _first(record,"source")
        size = shapes.dimension([source]) * (1.3 if family == 3 else 1.4)
        space = master or _space(system, record, previous if family == 3 else None)
        if family == 3 and previous:
            # Child links follow the previous control's position. World/Parent/
            # Local rotation is solved separately on the orientation receiver.
            owned = tf.linked(system,"spaceConstraints")
            for constraint in owned:
                driven = cmds.listConnections(constraint + ".constraintParentInverseMatrix", source=True, destination=False) or []
                if space in (cmds.ls(driven,long=True) or []):
                    cmds.delete(constraint)
            cmds.xform(space, worldSpace=True, matrix=list(tf.rigid(tf.world(source))))
        if family == 3:
            orient = _create_node("transform", name="Sub_Orient#", parent=space)
            _pack(system,orient)
            _fk_orientation(system, orient, _first(record,"reference"))
            space = orient
        control = _new_control(system, "Sub_Ctrl#" if family in (1,3) else "Base_Ctrl#" if family == 2 else "Ctrl#",
                               space, 1 if family == 1 or cmds.getAttr(system+".positionSpace") == 4 else 9 if family == 2 else 4, size, source)
        cmds.xform(control, worldSpace=True, matrix=list(tf.rigid(tf.world(source))))
        tf.message(record,"control",control)
        driver = control
        if family == 2:
            distance = cmds.getAttr(system+".aimDistance") or shapes.dimension([source])
            aim = _new_control(system,"Aim_Ctrl#",space,9,distance/5,source)
            pole = _new_control(system,"Pole_Vector_Ctrl#",space,8,distance/5,source)
            matrix = tf.rigid(tf.world(source))
            for node, point in ((aim,om.MPoint(0,distance,0)),(pole,om.MPoint(distance,0,0))):
                p = point * matrix
                cmds.xform(node,worldSpace=True,translation=(p.x,p.y,p.z))
            joint_a = _create_node("joint",name="joint_a#",parent=control)
            joint_b = _create_node("joint",name="joint_b#",parent=joint_a)
            _pack(system,joint_a)
            _pack(system,joint_b)
            cmds.setAttr(joint_b+".translateY",distance)
            handle, effector = cmds.ikHandle(startJoint=joint_a,endEffector=joint_b,solver="ikRPsolver",name="Aim_IK#")
            handle = cmds.parent(handle,aim)[0]
            _pack(system,handle)
            _pack(system,effector)
            _pack(system,cmds.poleVectorConstraint(pole,handle)[0])
            _add(aim,"twist","double",0,keyable=True)
            cmds.connectAttr(aim+".twist",handle+".twist")
            cmds.setAttr(joint_a+".visibility",0)
            cmds.setAttr(handle+".visibility",0)
            tf.message(record,"aim",aim)
            tf.message(record,"pole",pole)
            driver = joint_a
        tf.message(record,"driver",driver)
        previous = control


def _fk_orientation(system, orient, reference):
    choice = _pack(system,_create_node("choice",name="FKOrientationChoice#"))
    multiply = _pack(system,_create_node("multMatrix",name="FKLocalMatrix#"))
    identity = _pack(system,_create_node("composeMatrix",name="FKParentMatrix#"))
    decompose = _pack(system,_create_node("decomposeMatrix",name="FKOrientDecompose#"))
    cmds.connectAttr(orient+".parentInverseMatrix[0]",choice+".input[0]")
    cmds.connectAttr(identity+".outputMatrix",choice+".input[2]")
    cmds.connectAttr(reference+".worldMatrix[0]",multiply+".matrixIn[0]")
    cmds.connectAttr(orient+".parentInverseMatrix[0]",multiply+".matrixIn[1]")
    cmds.connectAttr(multiply+".matrixSum",choice+".input[1]")
    cmds.connectAttr(system+".chainOrientation",choice+".selector")
    cmds.connectAttr(choice+".output",decompose+".inputMatrix")
    cmds.connectAttr(decompose+".outputRotate",orient+".rotate")


def _bake_group_follow(system, samples):
    """Recover the original averaged follow, with an independent baked driver.

    Only constrain a scratch node while the rig's original inputs are active.
    Remove it before attaching controls, so selected parent/child rigs cannot
    feed their driven animation back into the group. The master stays zeroed.
    """
    motion = _first(system, "groupMotion")
    if not motion:  # Older scene systems retain their existing hierarchy.
        return
    scratch = cmds.duplicate(motion, parentOnly=True, inputConnections=False,
                             name="AnimKey_GroupFollowSolve#")[0]
    constraint = None
    try:
        constraint = cmds.parentConstraint(*(_sources(system) + [scratch]), maintainOffset=True)[0]
        with tf.sampling():
            for frame, _ in samples:
                cmds.currentTime(frame, edit=True)
                tf.keyed_world(motion, tf.world(scratch), frame)
        rotations = cmds.listConnections(motion + '.rotate', source=True, destination=False,
                                         type='animCurve') or []
        if rotations:
            cmds.filterCurve(rotations, filter='euler')
    finally:
        if constraint and cmds.objExists(constraint):
            cmds.delete(constraint)
        cmds.delete(scratch)


def _bake_controls(system, samples):
    members = records(system)
    family = cmds.getAttr(system+".systemID")
    if family == 1:
        _bake_group_follow(system, samples)
    with tf.sampling():
        for frame, matrices in samples:
            cmds.currentTime(frame,edit=True)
            for record, matrix in zip(members, matrices):
                control = _first(record,"control")
                tf.keyed_world(control,tf.rigid(om.MMatrix(matrix)),frame)
                if family == 2:
                    distance = cmds.getAttr(system+".aimDistance")
                    rigid = tf.rigid(om.MMatrix(matrix))
                    for attr, point in (("aim",om.MPoint(0,distance,0)),("pole",om.MPoint(distance,0,0))):
                        node = _first(record,attr)
                        p = point * rigid
                        target = list(rigid)
                        target[12:15] = [p.x,p.y,p.z]
                        tf.keyed_world(node,om.MMatrix(target),frame)
    # Preserve source key tangents for straightforward local-space transfer.
    if family == 0:
        for record in members:
            source, control = _first(record,"source"), _first(record,"control")
            for prefix, space_attr in (("translate","positionSpace"),("rotate","orientationSpace")):
                mode = cmds.getAttr(system+"."+space_attr)
                no_parent = not cmds.listRelatives(source,parent=True)
                compatible = all(abs(v) < 1e-9 for attr in ("rotatePivot","scalePivot","rotateAxis","rotatePivotTranslate","scalePivotTranslate")
                                 for v in cmds.getAttr(source+"."+attr)[0])
                if cmds.nodeType(source) == "joint":
                    compatible = compatible and all(abs(v) < 1e-9 for v in cmds.getAttr(source+".jointOrient")[0])
                if cmds.objExists(source+".offsetParentMatrix"):
                    compatible = compatible and all(abs(a-b) < 1e-9 for a,b in zip(cmds.getAttr(source+".offsetParentMatrix"),list(om.MMatrix())))
                if compatible and (mode == 1 or (mode == 0 and no_parent)):
                    for axis in "XYZ":
                        attr = prefix+axis
                        # Only a direct curve can be copied exactly; other graph
                        # inputs use the evaluated world-space conversion above.
                        inputs = cmds.listConnections(source+"."+attr,source=True,destination=False) or []
                        if inputs and cmds.nodeType(inputs[0]).startswith("animCurve"):
                            if cmds.copyKey(source,attribute=attr):
                                cmds.pasteKey(control,attribute=attr,option="replaceCompletely")


def _attach(system):
    for record in records(system):
        source, driver = _first(record,"source"), _first(record,"driver")
        if not source or not driver:
            continue
        attrs = [a for a in json.loads(tf.string(record,"channels")) if not cmds.getAttr(source+"."+a,lock=True)]
        if not attrs:
            continue
        for attr in attrs:
            plug = source+"."+attr
            value = cmds.getAttr(plug)
            incoming = cmds.listConnections(plug,source=True,destination=False,plugs=True) or []
            hold = record+".hold_"+attr
            for old in cmds.listConnections(hold,source=True,destination=False,plugs=True) or []:
                cmds.disconnectAttr(old,hold)
            if incoming:
                cmds.connectAttr(incoming[0],hold,force=True)
                cmds.disconnectAttr(incoming[0],plug)
            else:
                cmds.setAttr(hold,value)
            # The evaluated original input is now attached to the holder.
        skip_t = [axis.lower() for axis in "XYZ" if "translate"+axis not in attrs]
        skip_r = [axis.lower() for axis in "XYZ" if "rotate"+axis not in attrs]
        kwargs = {"maintainOffset":True}
        if skip_t:
            kwargs["skipTranslate"] = skip_t
        if skip_r:
            kwargs["skipRotate"] = skip_r
        constraint = cmds.parentConstraint(driver,source,**kwargs)[0]
        if cmds.objExists(record+".constraintOffset"):
            offset = json.loads(tf.string(record,"constraintOffset"))
            for name,value in offset.items():
                cmds.setAttr(constraint+".target[0]."+name,*value)
        else:
            tf.string(record,"constraintOffset",json.dumps({name:cmds.getAttr(constraint+".target[0]."+name)[0]
                      for name in ("targetOffsetTranslate","targetOffsetRotate")}))
        tf.message(system,"driveConstraints",constraint,multi=True)
        _pack(system,constraint)
    cmds.setAttr(system+".isEnabled",True)
    for control in controls(system):
        cmds.setAttr(control+".visibility",True)


def _detach(system):
    constraints = tf.linked(system,"driveConstraints")
    if constraints:
        cmds.delete(constraints)
    for record in records(system):
        source = _first(record,"source")
        if not source:
            continue
        for attr in json.loads(tf.string(record,"channels")):
            plug, hold = source+"."+attr, record+".hold_"+attr
            incoming = cmds.listConnections(hold,source=True,destination=False,plugs=True) or []
            if not cmds.getAttr(plug,lock=True):
                if incoming:
                    cmds.connectAttr(incoming[0],plug,force=True)
                else:
                    cmds.setAttr(plug,cmds.getAttr(hold))
    cmds.setAttr(system+".isEnabled",False)
    for control in controls(system):
        cmds.setAttr(control+".visibility",False)


# Smart bake: largest difference (in channel display units) allowed between
# the baked rig curve and the motion produced by the temporary controls.
SMART_BAKE_TOLERANCE = 1e-5
SMART_BAKE_MAX_PASSES = 200


def _solve_rig_values(members, attrs_by_record, samples, parent_samples):
    """Local channel values that reproduce every captured world pose.

    Returns one ``{attr: [(frame, value), ...]}`` per member. Each source is
    solved against its captured parent matrix, so parents baked in the same
    gesture cannot feed back into their children.
    """
    solved = [{attr: [] for attr in attrs} for attrs in attrs_by_record]
    solvers = []
    solver_parents = []
    try:
        for record in members:
            source = _first(record,"source")
            solver = tf.unlocked_duplicate(source,"AnimKey_BakeSolve#")
            parent = _create_node("transform",name="AnimKey_BakeParentSolve#")
            solver_parents.append(parent)
            solvers.append(cmds.parent(solver,parent,relative=True)[0])
        with tf.sampling():
            for (frame, matrices), (_, parents) in zip(samples,parent_samples):
                cmds.currentTime(frame,edit=True)
                for i, attrs in enumerate(attrs_by_record):
                    cmds.xform(solver_parents[i],worldSpace=True,matrix=parents[i])
                    tf.apply_world(solvers[i],matrices[i])
                    for attr in attrs:
                        solved[i][attr].append((frame,cmds.getAttr(solvers[i]+"."+attr)))
    finally:
        if solver_parents:
            cmds.delete(solver_parents)
        leftover = [s for s in solvers if cmds.objExists(s)]
        if leftover:
            cmds.delete(leftover)
    return solved


def _direct_curve(plug, internal=()):
    """The plug's own animCurve, None when unanimated, False for other graphs.

    Layers, pairBlends, expressions and curves shared with other rig plugs
    are left to the layer bake so their networks are never rewritten.
    ``internal`` lists this system's own nodes (holders, reference copies),
    which also read the curve and do not count as sharing it.
    """
    incoming = cmds.listConnections(plug,source=True,destination=False,skipConversionNodes=True) or []
    if not incoming:
        return None
    curve = incoming[0]
    if not cmds.nodeType(curve).startswith("animCurveT"):
        return False
    outputs = cmds.listConnections(curve+".output",source=False,destination=True,skipConversionNodes=True) or []
    owners = {n for n in (cmds.ls(outputs,long=True) or []) if n not in internal}
    if len(owners) > 1:
        return False
    return curve


def _curve_value(curve, frame):
    values = cmds.keyframe(curve,query=True,eval=True,time=(frame,frame)) or []
    return values[0] if values else None


def _continuous_rotations(source, series, reference):
    """Keep solved Euler rotations continuous (no 360 degree flips).

    ``reference`` holds the rig's current values at the first frame, so the
    bake stays on the same rotation branch the animator already uses.
    """
    import math
    axes = [a for a in ("rotateX","rotateY","rotateZ") if a in series]
    if not axes:
        return
    if len(axes) == 3:
        order = cmds.getAttr(source+".rotateOrder")
        previous = om.MEulerRotation(*[math.radians(reference.get(a,0.0) or 0.0) for a in axes],order)
        for index in range(len(series["rotateX"])):
            frame = series["rotateX"][index][0]
            euler = om.MEulerRotation(*[math.radians(series[a][index][1]) for a in axes],order)
            euler = euler.closestSolution(previous)
            for axis, value in zip(axes,(euler.x,euler.y,euler.z)):
                series[axis][index] = (frame,math.degrees(value))
            previous = euler
        return
    for axis in axes:
        previous = reference.get(axis)
        values = series[axis]
        for index, (frame, value) in enumerate(values):
            if previous is not None:
                value += 360.0 * round((previous - value) / 360.0)
            values[index] = (frame,value)
            previous = value


def _set_bake_key(curve, frame, value, key_frames):
    """Edit the key at ``frame`` or add one (auto tangents) on ``curve`` itself.

    Keying the curve node, never the plug, keeps the key out of whatever
    animation layer happens to be selected in Maya's UI.
    """
    if any(abs(frame - k) < 1e-6 for k in key_frames):
        cmds.keyframe(curve,edit=True,time=(frame,frame),valueChange=value)
        return
    cmds.setKeyframe(curve,time=(frame,),value=value)
    try:
        cmds.keyTangent(curve,edit=True,time=(frame,frame),inTangentType="auto",outTangentType="auto")
    except RuntimeError:
        pass
    key_frames.append(frame)


def _curve_evaluator(curve):
    """Fast read-only evaluation of ``curve`` in display units."""
    import maya.api.OpenMayaAnim as oma
    selection = om.MSelectionList()
    selection.add(curve)
    curve_fn = oma.MFnAnimCurve(selection.getDependNode(0))
    curve_type = cmds.nodeType(curve)
    unit = om.MTime.uiUnit()

    def evaluate(frame):
        value = curve_fn.evaluate(om.MTime(float(frame),unit))
        if curve_type == "animCurveTA":
            return om.MAngle(value,om.MAngle.kRadians).asUnits(om.MAngle.uiUnit())
        if curve_type == "animCurveTL":
            return om.MDistance(value,om.MDistance.kCentimeters).asUnits(om.MDistance.uiUnit())
        return value
    return evaluate


def _smart_bake_channel(curve, series, tolerance=SMART_BAKE_TOLERANCE):
    """Write ``series`` onto the rig's own curve with as few keys as possible.

    The original keys inside the baked range are kept (new values); a key is
    added only where the curve still differs from the temporary-control
    motion, until every sampled frame matches within ``tolerance``.
    Returns the number of keys added.
    """
    if not series:
        return 0
    frames = [f for f,_ in series]
    target = dict(series)
    start, end = frames[0], frames[-1]
    existing = [k for k in (cmds.keyframe(curve,query=True,timeChange=True) or []) if start - 1e-6 <= k <= end + 1e-6]
    # Sub-frame keys between samples have no captured pose; the baked result
    # is defined on the sampled frames only.
    stray = [k for k in existing if not any(abs(k - f) < 1e-6 for f in frames)]
    for frame in stray:
        cmds.cutKey(curve,time=(frame,frame),clear=True)
    key_frames = [k for k in existing if k not in stray]
    before = len(key_frames)
    for frame in sorted(set([start,end] + key_frames)):
        _set_bake_key(curve,frame,target[frame],key_frames)
    evaluate = _curve_evaluator(curve)
    for _ in range(SMART_BAKE_MAX_PASSES):
        keys = sorted(key_frames)
        # Worst frame of every gap between neighbouring keys, in one pass.
        worst = {}
        gap = 0
        for frame in frames:
            while gap + 1 < len(keys) and frame >= keys[gap + 1] - 1e-6:
                gap += 1
            if abs(frame - keys[gap]) < 1e-6:
                continue
            error = abs(evaluate(frame) - target[frame])
            if error > tolerance and error > worst.get(gap,(None,0.0))[1]:
                worst[gap] = (frame,error)
        if not worst:
            break
        for frame, _error in worst.values():
            _set_bake_key(curve,frame,target[frame],key_frames)
    return len(key_frames) - before


def _layer_bake_channels(system, channels):
    """Previous behaviour for channels driven by layers or other graphs."""
    layer = cmds.animLayer("TempControls_Bake_AnimKey#",override=True)
    tf.message(system,"bakeLayers",layer,multi=True)
    try:
        for source, attr, _series in channels:
            cmds.animLayer(layer,edit=True,attribute=source+"."+attr)
        for source, attr, series in channels:
            for frame, value in series:
                cmds.setKeyframe(source,attribute=attr,animLayer=layer,time=frame,value=value,noResolve=True)
        curves = cmds.animLayer(layer,query=True,animCurves=True) or []
        rotations = [c for c in curves if cmds.nodeType(c) == "animCurveTA"]
        if rotations:
            cmds.filterCurve(rotations,filter="euler")
    except Exception:
        if cmds.objExists(layer):
            cmds.delete(layer)
        raise
    return layer


def _bake_rig(system, samples, parent_samples):
    """Smart bake onto the rig's own curves, keeping the visible motion.

    No animation layer is created for ordinary keyed (or unanimated)
    channels: their original keys stay where they were, and new keys are
    added only where the curve could not otherwise match the temporary
    controls. Channels driven by layers or other graphs fall back to an
    owned override layer so those networks are preserved.
    """
    members = records(system)
    attrs_by_record = [json.loads(tf.string(r,"channels")) for r in members]
    previous_selection = cmds.ls(selection=True,long=True) or []
    try:
        solved = _solve_rig_values(members,attrs_by_record,samples,parent_samples)
        layer_channels = []
        start_frame = samples[0][0] if samples else None
        scene_has_layers = bool(cmds.ls(type="animLayer"))
        internal = set(cmds.ls([system] + (cmds.listRelatives(system,allDescendents=True,fullPath=True) or [])
                               + list(tf.linked(system,"packNodes")),long=True) or [])
        for record, attrs, series in zip(members,attrs_by_record,solved):
            source = _first(record,"source")
            curves = {attr: _direct_curve(source+"."+attr,internal) for attr in attrs}
            reference = {}
            for attr in attrs:
                if curves[attr] and start_frame is not None:
                    reference[attr] = _curve_value(curves[attr],start_frame)
                else:
                    reference[attr] = cmds.getAttr(source+"."+attr)
            _continuous_rotations(source,series,reference)
            for attr in attrs:
                if cmds.getAttr(source+"."+attr,lock=True):
                    continue
                curve = curves[attr]
                if curve is None and series[attr]:
                    values = [value for _frame, value in series[attr]]
                    if max(values) - min(values) <= SMART_BAKE_TOLERANCE:
                        # Still constant: no keys, only its (possibly new) value.
                        plug = source+"."+attr
                        if abs(cmds.getAttr(plug) - values[0]) > SMART_BAKE_TOLERANCE:
                            cmds.setAttr(plug,values[0])
                        continue
                if curve is None and series[attr] and not scene_has_layers:
                    # Unanimated channel: give it its own base curve first.
                    frame, value = series[attr][0]
                    cmds.setKeyframe(source,attribute=attr,time=frame,value=value)
                    curve = _direct_curve(source+"."+attr)
                if not curve:
                    layer_channels.append((source,attr,series[attr]))
                else:
                    _smart_bake_channel(curve,series[attr])
        layer = _layer_bake_channels(system,layer_channels) if layer_channels else None
    finally:
        if previous_selection:
            cmds.select(previous_selection,replace=True)
    return layer


def create(node=None, name=None, system=0, space=0, positionSpace=None,
           orientationSpace=None, relative=None, color=(.2,.85,.75),
           bake_frames=True, aim_distance=None, restoreDefaultProperties=False):
    """Create systems (0 Simple, 1 Group, 2 Aim, 3 FK); return their DAG paths."""
    if isinstance(system,str):
        system = SYSTEMS.index(system)
    if system not in range(4):
        raise ValueError("Unknown Temp Controls system.")
    position = space if positionSpace is None else positionSpace
    orientation = space if orientationSpace is None else orientationSpace
    if position not in range(6) or orientation not in range(6):
        raise ValueError("Unknown Temp Controls space.")
    if (system == 1 and any(s in (1,4) for s in (position,orientation))) or (system == 3 and 4 in (position,orientation)):
        raise ValueError("This space is unavailable for the selected system.")
    from AnimKey.buttons import tempPivot
    requested = tf.transforms(node)
    if tempPivot.is_active() and tempPivot._session.proxy.name() in requested:
        requested = [r.name() for r in tempPivot._session.targets if r.name()]
    tempPivot.deactivate_temp_pivot()
    sources, refs = _resolve(requested,position,orientation,relative)
    _validate(sources,refs,system)
    chunks = [[s] for s in sources] if system in (0,2) else [sources]
    created = []
    with tf.undo_chunk("AnimKey Create Temp Controls"):
        try:
            root = _create_root()
            for group in chunks:
                label = name or group[0].rsplit("|",1)[-1].replace(":","_")
                mount = _create_node("transform",name=label+"_Temp_Control#",parent=root)
                mount = cmds.ls(mount,long=True)[0]
                created.append(mount)
                tf.string(mount,"animKeyTempSystem",TAG)
                tf.string(mount,"color",json.dumps(color))
                _add(mount,"systemID","long",system)
                cmds.setAttr(mount+".systemID",lock=True)
                _add(mount,"isEnabled","bool",False)
                _add(mount,"positionSpace","long",position)
                _add(mount,"orientationSpace","long",orientation)
                _add(mount,"spaceLocked","bool",position == orientation)
                _add(mount,"bakeFrames","bool",bake_frames)
                _add(mount,"aimDistance","double",aim_distance or shapes.dimension(group))
                _add(mount,"chainOrientation","enum",2,enumName="world:parent:local",keyable=True)
                if refs.get(2):
                    tf.message(mount,"relativeSpace",refs[2])
                if refs.get(3):
                    tf.message(mount,"cameraSpace",refs[3])
                if 5 in (position,orientation):
                    _create_event(mount,-1000000,refs.get(5))
                frames = tf.times(group + list(refs.values()),frames=bake_frames)
                samples = tf.capture(group,frames)
                for source in group:
                    _make_member(mount,source)
                _build(mount)
                _bake_controls(mount,samples)
                if not restoreDefaultProperties:
                    _restore_properties(mount)
                _attach(mount)
            cmds.select([c for s in created for c in controls(s)],replace=True)
        except Exception:
            for mount in created:
                if cmds.objExists(mount):
                    _delete(mount,revert=True)
            raise
    return created


def set_enabled(system, enabled, revert=False):
    if bool(cmds.getAttr(system+".isEnabled")) == bool(enabled):
        return
    with tf.undo_chunk("AnimKey Toggle Temp Controls"), _keep_selection():
        if enabled:
            if not revert:
                nodes = _sources(system)
                samples = tf.capture(nodes,tf.times(nodes,frames=cmds.getAttr(system+".bakeFrames")))
                _refresh_reference_motion(system)
                _bake_controls(system,samples)
            _attach(system)
        else:
            samples = None
            parent_samples = None
            if not revert:
                nodes = _sources(system)
                # Final transfer always captures every displayed frame, even
                # for systems created by older versions in sparse Keys mode.
                frames = tf.times(nodes+controls(system),frames=True)
                parents = [(cmds.listRelatives(n,parent=True,fullPath=True) or [None])[0] for n in nodes]
                # Capture sources and their parent spaces in one evaluation
                # pass, before disconnecting any temporary driver.
                captured = tf.capture(nodes+[p for p in parents if p],frames)
                samples = [(frame,matrices[:len(nodes)]) for frame,matrices in captured]
                parent_samples = []
                for frame,matrices in captured:
                    iterator = iter(matrices[len(nodes):])
                    parent_samples.append((frame,[next(iterator) if p else list(om.MMatrix()) for p in parents]))
            _detach(system)
            if samples:
                try:
                    _bake_rig(system,samples,parent_samples)
                except Exception:
                    _attach(system)
                    raise


def toggle(systems=None, revert=False):
    systems = selected_systems() if systems is None else systems
    if not systems:
        cmds.warning("Temp Controls: select a temporary control or its rig source.")
    for system in systems:
        set_enabled(system,not cmds.getAttr(system+".isEnabled"),revert=revert)


def _delete(system,revert=False):
    _remember_properties(system)
    sources = _sources(system)
    root = (cmds.listRelatives(system,parent=True,fullPath=True) or [None])[0]
    owned = set(tf.linked(system,"packNodes"))
    # Preserve foreign props parented under any control. Their animated world
    # poses are captured before the temporary hierarchy is removed.
    foreign = []
    known = set(controls(system) + records(system) + list(owned))
    for control in controls(system):
        for child in cmds.listRelatives(control,children=True,type="transform",fullPath=True) or []:
            if child not in known and not cmds.objExists(child+".animKeyTempRole"):
                foreign.append(tf.NodeRef(child))
    foreign_samples = tf.capture([r.name() for r in foreign],tf.times(controls(system),frames=True)) if foreign else []
    if cmds.getAttr(system+".isEnabled"):
        set_enabled(system,False,revert=revert)
    for ref in foreign:
        cmds.parent(ref.name(),world=True)
    # Bake layers belong to the rig after clearing and are deliberately kept.
    if owned:
        cmds.delete([n for n in owned if cmds.objExists(n)])
    if cmds.objExists(system):
        cmds.delete(system)
    for frame, matrices in foreign_samples:
        with tf.sampling():
            cmds.currentTime(frame,edit=True)
            for ref,matrix in zip(foreign,matrices):
                tf.keyed_world(ref.name(),om.MMatrix(matrix),frame)
    if root and cmds.objExists(root) and not cmds.listRelatives(root,children=True):
        cmds.delete(root)
    return sources


def clear(systems=None,revert=False):
    systems = selected_systems() if systems is None else systems
    if not systems:
        cmds.warning("Temp Controls: select a system to clear.")
        return
    with tf.undo_chunk("AnimKey Clear Temp Controls"):
        sources = [source for system in systems for source in _delete(system,revert=revert)]
        if sources:
            cmds.select(sources,replace=True)


def selected_controls():
    selected = cmds.ls(selection=True,long=True) or []
    return [n for system in all_systems() for n in controls(system) if n in selected]


def appearance(size_percent=None,shape_step=None,orientation_step=None,color=None):
    with tf.undo_chunk("AnimKey Temp Control Appearance"):
        for control in selected_controls():
            size = cmds.getAttr(control+".shapeSize")[0]
            if size_percent is not None:
                size = [v*(1+size_percent/100.) for v in size]
            shape = (cmds.getAttr(control+".shapeIndex") + (shape_step or 0)) % len(shapes.NAMES)
            orientation = (cmds.getAttr(control+".shapeOrientation") + (orientation_step or 0)) % 24
            shapes.apply(control,size=size,shape=shape,orientation=orientation,color=color)


def _redirect_constraints(old,new):
    pairs = cmds.listConnections(old,source=False,destination=True,plugs=True,connections=True,type="constraint") or []
    for source,destination in zip(pairs[0::2],pairs[1::2]):
        suffix = source.split(".",1)[1]
        if cmds.objExists(new+"."+suffix):
            cmds.connectAttr(new+"."+suffix,destination,force=True)


def add_extra(parent=False):
    result = []
    with tf.undo_chunk("AnimKey Add Temp Control"):
        for control in selected_controls():
            ref = tf.NodeRef(control)
            system = selected_systems([control])[0]
            old_parent = (cmds.listRelatives(control,parent=True,fullPath=True) or [system])[0]
            extra = _new_control(system,"Extra_Parent#" if parent else "Extra_Child#",old_parent if parent else control,
                                 cmds.getAttr(control+".shapeIndex"),
                                 [v*(1.2 if parent else .8) for v in cmds.getAttr(control+".shapeSize")[0]],
                                 source=_first(control,"source"))
            extra_ref = tf.NodeRef(extra)
            tf.message(extra,"baseControl",ref.name())
            _add(extra,"extraIsParent","bool",parent)
            shapes.apply(extra,orientation=cmds.getAttr(control+".shapeOrientation"),
                         color=[float(v) for v in tf.string(control,"shapeColor").split(",")])
            _add(extra,"removableExtra","bool",True)
            if parent:
                cmds.xform(extra,worldSpace=True,matrix=list(tf.world(control)))
                cmds.parent(control,extra)
            else:
                children = cmds.listRelatives(control,children=True,type="transform",fullPath=True) or []
                for child in children:
                    if child != extra_ref.name():
                        cmds.parent(child,extra)
                _redirect_constraints(ref.name(),extra_ref.name())
                for record in records(system):
                    if _first(record,"driver") == ref.name():
                        tf.message(record,"driver",extra_ref.name())
            result.append(extra_ref.name())
        if result:
            cmds.select(result,replace=True)
    return result


def remove_extra():
    with tf.undo_chunk("AnimKey Remove Temp Control"):
        for control in selected_controls():
            if not cmds.objExists(control+".removableExtra"):
                cmds.warning("This control cannot be removed. Use Clear System for primary controls.")
                continue
            system = selected_systems([control])[0]
            parent = cmds.listRelatives(control,parent=True,fullPath=True)[0]
            children = cmds.listRelatives(control,children=True,type="transform",fullPath=True) or []
            child_controls = [n for n in children if n in controls(system)]
            receiver = tf.NodeRef(child_controls[0] if child_controls else parent)
            samples = tf.capture([receiver.name() if child_controls else control],tf.times(controls(system),frames=cmds.getAttr(system+".bakeFrames")))
            for child in children:
                cmds.parent(child,parent)
            _redirect_constraints(control,receiver.name())
            for record in records(system):
                if _first(record,"driver") == control:
                    tf.message(record,"driver",receiver.name())
            cmds.delete(control)
            with tf.sampling():
                for frame,matrices in samples:
                    cmds.currentTime(frame,edit=True)
                    tf.keyed_world(receiver.name(),om.MMatrix(matrices[0]),frame)


def change_space(systems,position,orientation,relative=None):
    with tf.undo_chunk("AnimKey Change Temp Control Space"), _keep_selection():
        for system in systems:
            family = cmds.getAttr(system+".systemID")
            if (family == 1 and any(s in (1,4) for s in (position,orientation))) or (family == 3 and 4 in (position,orientation)):
                raise ValueError("This space is unavailable for the selected system.")
            nodes = controls(system)
            frames = tf.times(nodes+_sources(system),frames=cmds.getAttr(system+".bakeFrames"))
            samples = tf.capture(nodes,frames)
            if relative:
                _validate_references(system,relative)
                tf.message(system,"relativeSpace",relative)
            if 3 in (position,orientation):
                camera = _camera(tf.transforms())
                if camera:
                    tf.message(system,"cameraSpace",camera)
            if 5 in (position,orientation) and not _grab_events(system):
                _create_event(system,-1000000,relative)
            constraints = tf.linked(system,"spaceConstraints")
            # Event-driver constraints remain necessary for Grab Release.
            event_nodes = _grab_events(system)
            for constraint in constraints:
                driven = cmds.listConnections(constraint+".constraintParentInverseMatrix",source=True,destination=False) or []
                if not any(n in (cmds.ls(driven,long=True) or []) for n in event_nodes):
                    cmds.delete(constraint)
            cmds.setAttr(system+".positionSpace",position)
            cmds.setAttr(system+".orientationSpace",orientation)
            unique = set()
            for record in records(system):
                space = _first(record,"space")
                if not space or space in unique:
                    continue
                unique.add(space)
                for attr in tf.TR + ("scaleX","scaleY","scaleZ"):
                    cmds.setAttr(space+"."+attr,1 if attr.startswith("scale") else 0)
                if position == orientation == 5:
                    _grab_space(system,space)
                else:
                    gr = None
                    if 5 in (position,orientation):
                        gr = _create_node("transform",name="GrabReleaseSpace#",parent=system)
                        _grab_space(system,gr)
                    _follow(system,gr if position == 5 else _spatial_target(system,record,position),space,orientation=False)
                    _follow(system,gr if orientation == 5 else _spatial_target(system,record,orientation),space,position=False)
            _apply_control_samples(nodes,samples)


def _apply_control_samples(nodes,samples):
    refs = [tf.NodeRef(n) for n in nodes]
    with tf.sampling():
        for frame,matrices in samples:
            cmds.currentTime(frame,edit=True)
            for ref,matrix in sorted(zip(refs,matrices),key=lambda p:p[0].name().count("|")):
                tf.keyed_world(ref.name(),om.MMatrix(matrix),frame)


def _validate_references(system,driver):
    for source in _sources(system):
        if driver == source or driver.startswith(source+"|"):
            raise ValueError("Grab driver would form a cycle with the source.")
    if driver == system or driver.startswith(system+"|"):
        raise ValueError("Choose an external Grab Release driver.")


def grab(system,driver=None,frame=None,remove=False,normalise=False,y_up=False):
    frame = int(cmds.currentTime(query=True) if frame is None else frame)
    if driver:
        driver = tf.transforms([driver])[0]
        _validate_references(system,driver)
    with tf.undo_chunk("AnimKey Grab Release"), _keep_selection():
        nodes = controls(system)
        samples = tf.capture(nodes,tf.times(nodes+_sources(system),frames=cmds.getAttr(system+".bakeFrames")))
        for event in _grab_events(system):
            if cmds.getAttr(event+".frame") == frame and not normalise:
                owned = [n for n in tf.linked(system,"spaceConstraints") if (cmds.listConnections(n+".constraintParentInverseMatrix",source=True,destination=False) or [None])[0] == event]
                if owned:
                    cmds.delete(owned)
                cmds.delete(event)
        if not remove and not normalise:
            if not _grab_events(system):
                _create_event(system,-1000000)
            _create_event(system,frame,driver)
        _normalise_events(system,y_up=y_up)
        change_space([system],5,5)
        _apply_control_samples(nodes,samples)


def _property_key(system):
    return str(cmds.getAttr(system+".systemID")) + ":" + ";".join(_sources(system))


def _preferences():
    from AnimKey.mods import configMod
    path = os.path.join(configMod.get_user_folder_path(),"tools","temp_controls_properties.json")
    try:
        with open(path,"r",encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError,ValueError):
        data = {}
    return path,data


def _remember_properties(system):
    from AnimKey.mods.storage import atomic_write_json
    path,data = _preferences()
    data[_property_key(system)] = [{"shape":cmds.getAttr(c+".shapeIndex"),"size":cmds.getAttr(c+".shapeSize")[0],
                                   "orientation":cmds.getAttr(c+".shapeOrientation"),
                                   "pivot":tf.string(c,"pivotOffset") if cmds.objExists(c+".pivotOffset") else ""} for c in controls(system)]
    os.makedirs(os.path.dirname(path),exist_ok=True)
    atomic_write_json(path,data)


def _restore_properties(system):
    _,data = _preferences()
    for control,props in zip(controls(system),data.get(_property_key(system),[])):
        shapes.apply(control,shape=props["shape"],size=props["size"],orientation=props["orientation"])
        if props.get("pivot"):
            _apply_pivot_offsets([control],[om.MMatrix(json.loads(props["pivot"]))])


def _apply_pivot_offsets(nodes,offsets,reset=False):
    systems = selected_systems(nodes)
    for system in systems:
        members = records(system)
        all_controls = controls(system)
        drivers = [_first(r,"driver") for r in members]
        frames = tf.times(all_controls+_sources(system),frames=cmds.getAttr(system+".bakeFrames"))
        control_samples = tf.capture(all_controls,frames)
        driver_samples = tf.capture(drivers,frames)
        offsets_by_node = dict(zip(nodes,offsets))
        adjusted = []
        for frame,matrices in control_samples:
            adjusted.append((frame,[list(offsets_by_node.get(n,om.MMatrix()) * om.MMatrix(m)) for n,m in zip(all_controls,matrices)]))
        _apply_control_samples(all_controls,adjusted)
        receivers = []
        for record,old_driver in zip(members,drivers):
            receiver = _first(record,"pivotReceiver")
            if not receiver:
                receiver = _create_node("transform",name="Pivot_Compensation#",parent=old_driver)
                tf.string(receiver,"animKeyTempRole","pivotReceiver")
                _pack(system,receiver)
                tf.message(record,"pivotReceiver",receiver)
                _redirect_constraints(old_driver,receiver)
                tf.message(record,"driver",receiver)
            receivers.append(receiver)
        _apply_control_samples(receivers,driver_samples)
        for node,offset in zip(nodes,offsets):
            if node not in all_controls:
                continue
            previous = om.MMatrix(json.loads(tf.string(node,"pivotOffset"))) if cmds.objExists(node+".pivotOffset") and tf.string(node,"pivotOffset") else om.MMatrix()
            tf.string(node,"pivotOffset",json.dumps(list(om.MMatrix() if reset else offset * previous)))


def edit_pivot():
    global _pivot_edit
    if _pivot_edit:
        return finish_pivot_edit()
    selected = selected_controls()
    if not selected:
        raise ValueError("Select one or more active Temp Controls to edit their pivots.")
    if any(not cmds.getAttr(s+".isEnabled") for s in selected_systems(selected)):
        raise ValueError("Enable the system before editing its pivot.")
    helpers = []
    with tf.undo_chunk("AnimKey Edit Temp Control Pivot"):
        for node in selected:
            helper = shapes.create("AnimKey_EditPivot#",selected_systems([node])[0],shape=1,
                                   size=[v*3 for v in cmds.getAttr(node+".shapeSize")[0]])
            cmds.xform(helper,worldSpace=True,matrix=list(tf.world(node)))
            helpers.append(tf.NodeRef(helper))
        if len(helpers) > 1:
            # The last helper supplies common local pivot edits.
            for helper in helpers[:-1]:
                for attr in ("translate","rotate"):
                    cmds.connectAttr(helpers[-1].name()+"."+attr,helper.name()+"."+attr,force=True)
        cmds.select(helpers[-1].name(),replace=True)
    state = {"controls":[tf.NodeRef(n) for n in selected],"helpers":helpers,"callbacks":[],"busy":False}
    _pivot_edit = state
    def selection(*_):
        if _pivot_edit is state and not state["busy"]:
            active = cmds.ls(selection=True,long=True) or []
            if active != [helpers[-1].name()]:
                finish_pivot_edit()
    state["callbacks"].append(om.MEventMessage.addEventCallback("SelectionChanged",selection))
    for helper in helpers:
        state["callbacks"].append(om.MNodeMessage.addNodePreRemovalCallback(helper.handle.object(),lambda *_: __import__("maya.utils",fromlist=["executeDeferred"]).executeDeferred(finish_pivot_edit)))
    for event in (om.MSceneMessage.kBeforeNew,om.MSceneMessage.kBeforeOpen):
        state["callbacks"].append(om.MSceneMessage.addCallback(event,lambda *_: finish_pivot_edit(apply=False)))
    return [r.name() for r in helpers]


def finish_pivot_edit(apply=True):
    global _pivot_edit
    state = _pivot_edit
    if not state or state["busy"]:
        return
    state["busy"] = True
    _pivot_edit = None
    for callback in state["callbacks"]:
        try:
            om.MMessage.removeCallback(callback)
        except RuntimeError:
            pass
    with tf.undo_chunk("AnimKey Apply Temp Control Pivot"):
        try:
            valid = [(control.name(),helper.name()) for control,helper in zip(state["controls"],state["helpers"]) if control.name() and helper.name()]
            if apply and valid:
                nodes = [n for n,h in valid]
                offsets = [tf.world(helper) * tf.world(node).inverse() for node,helper in valid]
                _apply_pivot_offsets(nodes,offsets)
        finally:
            helpers = [r.name() for r in state["helpers"] if r.name()]
            if helpers:
                cmds.delete(helpers)
            controls = [r.name() for r in state["controls"] if r.name()]
            if controls and apply:
                cmds.select(controls,replace=True)


def reset_pivot():
    nodes = selected_controls()
    offsets = [om.MMatrix(json.loads(tf.string(n,"pivotOffset"))).inverse() if cmds.objExists(n+".pivotOffset") and tf.string(n,"pivotOffset") else om.MMatrix() for n in nodes]
    with tf.undo_chunk("AnimKey Reset Temp Control Pivot"):
        _apply_pivot_offsets(nodes,offsets,reset=True)


def rebuild_members(system,nodes):
    """Membership edits commit current motion to the rig before rebuilding."""
    family = cmds.getAttr(system+".systemID")
    if family not in (1,3):
        raise ValueError("Membership tools apply to Group and FK Chain systems.")
    nodes = tf.transforms(nodes)
    if not nodes or (family == 3 and len(nodes) < 2):
        raise ValueError("The resulting system needs {} sources.".format(2 if family == 3 else 1))
    kwargs = dict(name=system.rsplit("|",1)[-1].split("_Temp_Control")[0],system=family,
                  positionSpace=cmds.getAttr(system+".positionSpace"),orientationSpace=cmds.getAttr(system+".orientationSpace"),
                  relative=_first(system,"relativeSpace"),color=_color(system),bake_frames=bool(cmds.getAttr(system+".bakeFrames")))
    with tf.undo_chunk("AnimKey Edit Temp Control Members"):
        clear([system],revert=False)
        return create(node=nodes,**kwargs)


def edit_members(action):
    systems = selected_systems()
    if len(systems) != 1:
        raise ValueError("Select exactly one Group or FK system, plus members to edit.")
    system = systems[0]
    nodes = _sources(system)
    selected = tf.transforms()
    selected_sources = []
    for n in selected:
        source = _first(n,"source") if cmds.objExists(n+".source") else n
        if source and source not in selected_sources:
            selected_sources.append(source)
    if action == "add":
        nodes += [n for n in selected_sources if n not in nodes and n != system and not n.startswith(system+"|")]
    elif action == "remove":
        nodes = [n for n in nodes if n not in selected_sources]
    elif action == "reverse":
        nodes.reverse()
    elif action in ("up","down"):
        indices = range(1,len(nodes)) if action == "up" else range(len(nodes)-2,-1,-1)
        for i in indices:
            j = i-1 if action == "up" else i+1
            if nodes[i] in selected_sources and nodes[j] not in selected_sources:
                nodes[i],nodes[j] = nodes[j],nodes[i]
    else:
        raise ValueError("Unknown membership edit.")
    return rebuild_members(system,nodes)


def execute(*args,**kwargs):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("Temp Controls"):
        return
    from AnimKey.mods.maya_compat import QtCore,QtWidgets
    mods = QtWidgets.QApplication.keyboardModifiers()
    ctrl,shift,alt = (bool(mods & flag) for flag in (QtCore.Qt.ControlModifier,QtCore.Qt.ShiftModifier,QtCore.Qt.AltModifier))
    if ctrl and shift and alt:
        return clear(revert=False)
    if shift and alt:
        return show(button=kwargs.get("button"),panel=True)
    if alt and not ctrl:
        return toggle(revert=False)
    quick = 0 if ctrl and alt else 3 if ctrl and shift else 2 if ctrl else 1 if shift else None
    if quick is not None:
        return create(space=quick)
    return show(button=kwargs.get("button"))


def show(button=None,panel=False):
    global _window
    from AnimKey.buttons import tempPivot
    tempPivot.deactivate_temp_pivot()
    from AnimKey.mods.maya_compat import is_qt_object_valid
    from AnimKey.buttons.tempControlsUI import TempControlsWindow
    if is_qt_object_valid(_window):
        _window.close()
    _window = TempControlsWindow(anchor_button=button,panel=panel)
    _window.show()
    _window.position_window()
    _window.raise_()
    return _window


def show_menu(button=None,pos=None):
    from AnimKey.buttons.tempControlsUI import show_menu as menu
    return menu(button,pos)


def shutdown():
    global _window
    from AnimKey.mods.maya_compat import is_qt_object_valid
    if is_qt_object_valid(_window):
        _window.close()
    _window = None
    finish_pivot_edit(apply=False)
