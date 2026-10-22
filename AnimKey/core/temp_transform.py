"""Shared, Maya-native transform utilities for temporary animation tools."""
from contextlib import contextmanager
import math

import maya.cmds as cmds
import maya.api.OpenMaya as om

TR = tuple(prefix + axis for prefix in ("translate", "rotate") for axis in "XYZ")


class NodeRef:
    """Session reference that survives renames and DAG reparenting."""
    def __init__(self, name):
        selection = om.MSelectionList()
        selection.add(name)
        self.handle = om.MObjectHandle(selection.getDependNode(0))

    def name(self):
        if not self.handle.isValid() or not self.handle.isAlive():
            return None
        obj = self.handle.object()
        if obj.hasFn(om.MFn.kDagNode):
            return om.MFnDagNode(obj).getPath().fullPathName()
        return om.MFnDependencyNode(obj).name()


def transforms(nodes=None):
    if nodes is None:
        nodes = cmds.ls(orderedSelection=True, long=True) or []
    if isinstance(nodes, str):
        nodes = [nodes]
    result = []
    for name in nodes:
        for node in cmds.ls(name, objectsOnly=True, long=True) or []:
            if cmds.objectType(node, isAType="transform") and node not in result:
                result.append(node)
    return result


def world(node):
    return om.MMatrix(cmds.xform(node, query=True, worldSpace=True, matrix=True))


def center(nodes):
    positions = [list(world(n))[12:15] for n in nodes]
    return [(min(p[i] for p in positions) + max(p[i] for p in positions)) * .5
            for i in range(3)]


def rigid(matrix):
    transform = om.MTransformationMatrix(matrix)
    result = om.MTransformationMatrix()
    result.setTranslation(transform.translation(om.MSpace.kWorld), om.MSpace.kWorld)
    result.setRotation(transform.rotation(asQuaternion=True))
    return result.asMatrix()


def writable(node):
    """Keyable unlocked TR, including channels fed by layers/animation graphs."""
    return [a for a in TR if not cmds.getAttr(node + "." + a, lock=True)
            and cmds.getAttr(node + "." + a, keyable=True)]


def apply_world(node,matrix):
    """Apply a matrix and correct Maya's pivot-dependent translation residual."""
    values = list(matrix)
    cmds.xform(node,worldSpace=True,matrix=values)
    evaluated = list(world(node))
    delta = [values[i]-evaluated[i] for i in (12,13,14)]
    if any(abs(v)>1e-10 for v in delta):
        cmds.move(*delta,node,relative=True,worldSpace=True)


def set_world(node, matrix, attrs=None):
    """Solve through Maya (pivots, rotateAxis, jointOrient, OPM and rotateOrder).

    A sibling copy solves channels without ever unlocking or disconnecting the
    rig. Only the requested writable channels are applied to the real object.
    """
    attrs = writable(node) if attrs is None else attrs
    if not attrs:
        return
    scratch = cmds.duplicate(node, parentOnly=True, name="AnimKey_PoseSolve#",
                             inputConnections=False)[0]
    try:
        for attr in TR + ("scaleX", "scaleY", "scaleZ", "shearXY", "shearXZ", "shearYZ"):
            cmds.setAttr(scratch + "." + attr, lock=False)
        apply_world(scratch,matrix)
        values = {a: cmds.getAttr(scratch + "." + a) for a in attrs}
        # Choose equivalent Euler values near the current pose, without filtering
        # unrelated keys. This is our continuity rule, not animBot Smart Euler.
        if all(a in attrs for a in ("rotateX", "rotateY", "rotateZ")):
            order = cmds.getAttr(node + ".rotateOrder")
            candidate = om.MEulerRotation(*[math.radians(values["rotate" + a]) for a in "XYZ"], order)
            previous = om.MEulerRotation(*[math.radians(cmds.getAttr(node + ".rotate" + a)) for a in "XYZ"], order)
            closest = candidate.closestSolution(previous)
            for axis, value in zip("XYZ", (closest.x, closest.y, closest.z)):
                values["rotate" + axis] = math.degrees(value)
        for attr, value in values.items():
            cmds.setAttr(node + "." + attr, value)
    finally:
        cmds.delete(scratch)


@contextmanager
def undo_chunk(name):
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


@contextmanager
def sampling():
    frame = cmds.currentTime(query=True)
    auto = cmds.autoKeyframe(query=True, state=True)
    cmds.autoKeyframe(state=False)
    try:
        yield
    finally:
        cmds.currentTime(frame, edit=True)
        cmds.autoKeyframe(state=auto)


def times(nodes, frames=False):
    keys = set()
    for node in nodes:
        keys.update(cmds.keyframe(node, attribute=list(TR), query=True, timeChange=True) or [])
        # Include parent/constraint/layer animation when converting spaces.
        history = cmds.listHistory(node, pruneDagObjects=True) or []
        for dependency in history:
            if cmds.nodeType(dependency).startswith("animCurve"):
                keys.update(cmds.keyframe(dependency, query=True, timeChange=True) or [])
        parents = cmds.listRelatives(node, allParents=True, fullPath=True) or []
        while parents:
            parent = parents[0]
            keys.update(cmds.keyframe(parent, attribute=list(TR), query=True, timeChange=True) or [])
            parents = cmds.listRelatives(parent, parent=True, fullPath=True) or []
    if frames:
        start = min(keys | {cmds.playbackOptions(query=True, minTime=True)})
        end = max(keys | {cmds.playbackOptions(query=True, maxTime=True)})
        if end - start > 10000:
            raise ValueError("Temp Controls: bake range exceeds 10,000 frames.")
        keys.update(range(int(math.ceil(start)), int(math.floor(end)) + 1))
        keys.update((start, end))
    return sorted(keys or {cmds.currentTime(query=True)})


def capture(nodes, frames):
    samples = []
    undo_state = cmds.undoInfo(query=True,state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        with sampling():
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                samples.append((frame, [list(world(n)) for n in nodes]))
    finally:
        cmds.undoInfo(stateWithoutFlush=undo_state)
    return samples


def keyed_world(node, matrix, frame, attrs=TR):
    # This only targets controls, whose TR channels are always writable.
    apply_world(node,matrix)
    cmds.setKeyframe(node, attribute=list(attrs), time=frame)


def message(node, attr, target=None, multi=False):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, attributeType="message", multi=multi)
    if target:
        plug = node + "." + attr
        if multi:
            indices = cmds.getAttr(plug, multiIndices=True) or []
            plug += "[{}]".format(max(indices + [-1]) + 1)
        cmds.connectAttr(target + ".message", plug, force=True)


def linked(node, attr):
    if not cmds.objExists(node + "." + attr):
        return []
    result = []
    plug = node + "." + attr
    indices = cmds.getAttr(plug, multiIndices=True) if cmds.attributeQuery(attr, node=node, multi=True) else None
    for part in ([plug + "[{}]".format(i) for i in indices or []] if indices is not None else [plug]):
        result.extend(cmds.listConnections(part, source=True, destination=False) or [])
    return [(cmds.ls(n, long=True) or [n])[0] for n in result]


def string(node, attr, value=None):
    if not cmds.objExists(node + "." + attr):
        cmds.addAttr(node, longName=attr, dataType="string")
    if value is not None:
        cmds.setAttr(node + "." + attr, value, type="string")
    return cmds.getAttr(node + "." + attr) or ""
