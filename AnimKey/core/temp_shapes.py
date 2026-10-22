"""Original ANIMKEY control geometry, with the documented shape catalogue.

The REA documents supplied no NURBS payload files. These are independently
constructed shapes; they do not claim byte-for-byte geometry compatibility.
"""
import math
import maya.cmds as cmds
import maya.api.OpenMaya as om
from AnimKey.core import temp_transform as tf

NAMES = ("No Shape", "Locator", "Circle", "Square", "Rounded Square", "Diamond",
         "Cross", "Box", "Sphere", "Rhombus", "Arrow Cross", "Arrow Circle", "Cog")
ORIENTATIONS = ((0,0,0),(0,-90,0),(0,180,0),(0,90,0),
                (180,0,0),(180,-90,0),(180,180,0),(180,90,0),
                (0,0,90),(0,-90,90),(0,180,90),(0,90,90),
                (0,0,-90),(0,90,-90),(0,180,-90),(0,-90,-90),
                (90,0,0),(90,0,-90),(90,0,180),(90,0,90),
                (-90,0,0),(-90,0,-90),(-90,0,180),(-90,0,90))


def dimension(nodes):
    try:
        bounds = cmds.exactWorldBoundingBox(nodes)
        return max(1., *[bounds[i + 3] - bounds[i] for i in range(3)])
    except RuntimeError:
        return 1.


def _circle(plane=0, count=48):
    points = []
    for i in range(count + 1):
        a = i * math.tau / count
        c, s = math.cos(a), math.sin(a)
        points.append((c,0,s) if plane == 0 else (c,s,0) if plane == 1 else (0,c,s))
    return points


def _paths(index):
    if index == 0:
        return []
    if index == 1:
        return [[(-1,0,0),(1,0,0)],[(0,-1,0),(0,1,0)],[(0,0,-1),(0,0,1)]]
    if index == 2:
        return [_circle()]
    if index in (3,4,5):
        if index == 3:
            return [[(-1,0,-1),(-1,0,1),(1,0,1),(1,0,-1),(-1,0,-1)]]
        if index == 5:
            return [[(0,0,-1),(-1,0,0),(0,0,1),(1,0,0),(0,0,-1)]]
        points = []
        for i in range(65):
            angle = i * math.tau / 64
            c, s = math.cos(angle), math.sin(angle)
            points.append((math.copysign(abs(c) ** .5, c),0,math.copysign(abs(s) ** .5,s)))
        return [points]
    if index == 6:
        points = [(-.3,-1),(.3,-1),(.3,-.3),(1,-.3),(1,.3),(.3,.3),(.3,1),(-.3,1),(-.3,.3),(-1,.3),(-1,-.3),(-.3,-.3),(-.3,-1)]
        return [[(x,0,z) for x,z in points]]
    if index == 7:
        a = [(-1,-1,-1),(-1,-1,1),(1,-1,1),(1,-1,-1),(-1,-1,-1)]
        b = [(x,1,z) for x,y,z in a]
        return [a,b] + [[a[i], b[i]] for i in range(4)]
    if index == 8:
        return [_circle(i) for i in range(3)]
    if index == 9:
        return [[(0,1,0),(1,0,0),(0,-1,0),(-1,0,0),(0,1,0),(0,0,1),(0,-1,0),(0,0,-1),(0,1,0)],[(1,0,0),(0,0,1),(-1,0,0),(0,0,-1),(1,0,0)]]
    if index == 10:
        points = []
        for quarter in range(4):
            for x,z in ((-.18,.18),(-.18,.7),(-.4,.7),(0,1.15),(.4,.7),(.18,.7),(.18,.18)):
                a = quarter * math.pi / 2
                points.append((x*math.cos(a)-z*math.sin(a),0,x*math.sin(a)+z*math.cos(a)))
        return [points + [points[0]]]
    if index == 11:
        return [_circle()[:-5] + [(.45,0,-.8),(.9,0,-.8),(.75,0,-1.2)]]
    return [[((1. if i % 4 in (1,2) else .78)*math.cos(i*math.tau/120),0,
              (1. if i % 4 in (1,2) else .78)*math.sin(i*math.tau/120)) for i in range(121)]]


def apply(node, shape=None, size=None, orientation=None, color=None):
    for attr, default in (("shapeIndex",4),("shapeOrientation",0)):
        if not cmds.objExists(node + "." + attr):
            cmds.addAttr(node, longName=attr, attributeType="long", defaultValue=default)
    if not cmds.objExists(node + ".shapeSize"):
        cmds.addAttr(node, longName="shapeSize", attributeType="double3")
        for axis in "XYZ":
            cmds.addAttr(node, longName="shapeSize" + axis, parent="shapeSize", attributeType="double", defaultValue=1)
    if not cmds.objExists(node + ".shapeColor"):
        cmds.addAttr(node, longName="shapeColor", dataType="string")
        cmds.setAttr(node + ".shapeColor", "0.2,0.85,0.75", type="string")
    if shape is not None:
        cmds.setAttr(node + ".shapeIndex", int(shape) % len(NAMES))
    if size is not None:
        values = [float(size)] * 3 if isinstance(size, (int,float)) else size
        cmds.setAttr(node + ".shapeSize", *values)
    if orientation is not None:
        cmds.setAttr(node + ".shapeOrientation", int(orientation) % 24)
    if color is not None:
        cmds.setAttr(node + ".shapeColor", ",".join(str(v) for v in color), type="string")
    index = cmds.getAttr(node + ".shapeIndex")
    size = cmds.getAttr(node + ".shapeSize")[0]
    orient = ORIENTATIONS[cmds.getAttr(node + ".shapeOrientation")]
    color = [float(v) for v in cmds.getAttr(node + ".shapeColor").split(",")]
    old_shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    if old_shapes:
        cmds.delete(old_shapes)
    matrix = om.MEulerRotation(*[math.radians(v) for v in orient]).asMatrix()
    for path in _paths(index):
        points = []
        for point in path:
            p = om.MPoint(*[point[i] * size[i] * .5 for i in range(3)]) * matrix
            points.append((p.x,p.y,p.z))
        curve = cmds.curve(degree=1, point=points)
        for s in cmds.listRelatives(curve, shapes=True, fullPath=True) or []:
            new_shape = cmds.parent(s, node, shape=True, relative=True)[0]
            cmds.setAttr(new_shape + ".overrideEnabled", 1)
            cmds.setAttr(new_shape + ".overrideRGBColors", 1)
            cmds.setAttr(new_shape + ".overrideColorRGB", *color)
        cmds.delete(curve)
    cmds.setAttr(node + ".useOutlinerColor", 1)
    cmds.setAttr(node + ".outlinerColor", *color)


def create(name, parent, shape=4, size=1., color=(.2,.85,.75)):
    node = cmds.createNode("transform", name=name, parent=parent)
    tf.string(node, "animKeyTempRole", "control")
    apply(node, shape=shape, size=size, color=color)
    return node
