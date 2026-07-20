import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.api.OpenMayaRender as omr
import maya.api.OpenMayaUI as omui
import maya.OpenMayaRender as omr_legacy
import maya.cmds as cmds
import math
import bisect
import re

def maya_useNewAPI():
    """ Tells Maya that this plugin uses the Python API 2.0. """
    pass

PLUGIN_NODE_NAME = "animKeyMotionTrail"
PLUGIN_NODE_ID = om.MTypeId(0x0012A341)  # Using a random safe ID block, make sure it's unique
PLUGIN_DRAW_CLASSIFICATION = "drawdb/geometry/animKeyMotionTrail/xray"
PLUGIN_DRAW_REGISTRANT_ID = "animKeyMotionTrailNodePlugin"

TANGENT_HANDLE_NODE_NAME = "animKeyTangentHandle"
TANGENT_HANDLE_NODE_ID = om.MTypeId(0x0012A342)
TANGENT_HANDLE_DRAW_CLASSIFICATION = "drawdb/geometry/animKeyTangentHandle"
TANGENT_HANDLE_DRAW_REGISTRANT_ID = "animKeyTangentHandleNodePlugin"


def _as_mcolor(value, fallback=None):
    fallback = fallback or om.MColor((1.0, 1.0, 1.0, 1.0))
    if isinstance(value, om.MColor):
        return value
    try:
        return om.MColor((value.r, value.g, value.b, getattr(value, "a", 1.0)))
    except Exception:
        pass
    try:
        values = list(value)
        if len(values) >= 3:
            alpha = values[3] if len(values) > 3 else 1.0
            return om.MColor((float(values[0]), float(values[1]), float(values[2]), float(alpha)))
    except Exception:
        pass
    return fallback


def _mcolor_key(value):
    color = _as_mcolor(value)
    return (
        round(float(color.r), 3),
        round(float(color.g), 3),
        round(float(color.b), 3),
        round(float(color.a), 3),
    )


def _set_color_default(n_attr, color):
    try:
        n_attr.default = (float(color[0]), float(color[1]), float(color[2]))
    except Exception:
        pass
    try:
        n_attr.setDefault(float(color[0]), float(color[1]), float(color[2]))
    except Exception:
        pass


def _read_color_plug(fn_node, attr_name, fallback):
    fallback_color = _as_mcolor(fallback)
    try:
        color_plug = fn_node.findPlug(attr_name, False)
        return om.MColor((
            color_plug.child(0).asFloat(),
            color_plug.child(1).asFloat(),
            color_plug.child(2).asFloat(),
            1.0,
        ))
    except Exception:
        return fallback_color


def _read_float_plug(fn_node, attr_name, fallback):
    try:
        return fn_node.findPlug(attr_name, False).asFloat()
    except Exception:
        return fallback


def _read_bool_plug(fn_node, attr_name, fallback):
    try:
        return fn_node.findPlug(attr_name, False).asBool()
    except Exception:
        return fallback


def _read_string_plug(fn_node, attr_name, fallback=""):
    try:
        value = fn_node.findPlug(attr_name, False).asString()
        return value if value is not None else fallback
    except Exception:
        return fallback


def _matrix_position_at_time(matrix_plug, frame):
    context = om.MDGContext(om.MTime(float(frame), om.MTime.uiUnit()))
    mat_obj = matrix_plug.asMObject(context)
    if mat_obj.isNull():
        return None
    mat = om.MFnMatrixData(mat_obj).matrix()
    return om.MPoint(mat[12], mat[13], mat[14])


_VERTEX_COMPONENT_RE = re.compile(r"(.+)\.vtx\[(\d+)\]$")


def _resolve_mesh_vertex_component(component):
    if not component:
        return None
    try:
        flattened = cmds.ls(component, flatten=True, long=True) or []
        component = flattened[0] if flattened else component
    except Exception:
        pass

    match = _VERTEX_COMPONENT_RE.match(component)
    if not match:
        return None

    node_name = match.group(1)
    vertex_index = int(match.group(2))
    shape = node_name
    try:
        node_type = cmds.nodeType(node_name)
    except Exception:
        node_type = None

    if node_type == "transform":
        try:
            shapes = cmds.listRelatives(
                node_name,
                shapes=True,
                noIntermediate=True,
                fullPath=True,
                type="mesh",
            ) or []
        except Exception:
            shapes = []
        if not shapes:
            return None
        shape = shapes[0]
    elif node_type != "mesh":
        return None

    return shape, vertex_index


def _mesh_vertex_position_at_time(component_spec, frame):
    if not component_spec:
        return None
    shape, vertex_index = component_spec
    try:
        selection = om.MSelectionList()
        selection.add(shape)
        dag_path = selection.getDagPath(0)
        fn_node = om.MFnDependencyNode(dag_path.node())
        world_mesh_plug = fn_node.findPlug("worldMesh", False).elementByLogicalIndex(0)
        context = om.MDGContext(om.MTime(float(frame), om.MTime.uiUnit()))
        mesh_obj = world_mesh_plug.asMObject(context)
        if mesh_obj.isNull():
            return None
        mesh_fn = om.MFnMesh(mesh_obj)
        if vertex_index < 0 or vertex_index >= mesh_fn.numVertices:
            return None
        point = mesh_fn.getPoint(vertex_index, om.MSpace.kObject)
        return om.MPoint(point.x, point.y, point.z)
    except Exception:
        return None


def _is_anim_playing():
    try:
        return bool(oma.MAnimControl.isPlaying())
    except Exception:
        pass
    try:
        return bool(cmds.play(query=True, state=True))
    except Exception:
        return False


def _lerp_point(point_a, point_b, amount):
    amount = max(0.0, min(1.0, float(amount)))
    inverse = 1.0 - amount
    return om.MPoint(
        (point_a.x * inverse) + (point_b.x * amount),
        (point_a.y * inverse) + (point_b.y * amount),
        (point_a.z * inverse) + (point_b.z * amount),
    )


def _point_distance(point_a, point_b):
    dx = point_a.x - point_b.x
    dy = point_a.y - point_b.y
    dz = point_a.z - point_b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _point_distance_to_segment(point, segment_start, segment_end):
    sx = segment_end.x - segment_start.x
    sy = segment_end.y - segment_start.y
    sz = segment_end.z - segment_start.z
    px = point.x - segment_start.x
    py = point.y - segment_start.y
    pz = point.z - segment_start.z
    seg_len_sq = sx * sx + sy * sy + sz * sz
    if seg_len_sq <= 0.000001:
        return _point_distance(point, segment_start)
    t = max(0.0, min(1.0, (px * sx + py * sy + pz * sz) / seg_len_sq))
    closest = om.MPoint(segment_start.x + sx * t, segment_start.y + sy * t, segment_start.z + sz * t)
    return _point_distance(point, closest)


def _vector_between(point_a, point_b):
    return om.MVector(point_b.x - point_a.x, point_b.y - point_a.y, point_b.z - point_a.z)


def _vector_length(vector):
    return math.sqrt(vector.x * vector.x + vector.y * vector.y + vector.z * vector.z)


def _motion_break_score(previous_point, point, next_point):
    incoming = _vector_between(previous_point, point)
    outgoing = _vector_between(point, next_point)
    incoming_len = _vector_length(incoming)
    outgoing_len = _vector_length(outgoing)
    longest_len = max(incoming_len, outgoing_len)
    if longest_len <= 0.000001:
        return 0.0

    shortest_len = min(incoming_len, outgoing_len)
    if shortest_len <= longest_len * 0.08:
        speed_score = 1.0
    else:
        speed_ratio = longest_len / max(shortest_len, 0.000001)
        speed_score = max(0.0, min(1.0, (speed_ratio - 2.0) / 3.0))

    if incoming_len <= 0.000001 or outgoing_len <= 0.000001:
        angle_score = 0.0
    else:
        dot = (
            incoming.x * outgoing.x +
            incoming.y * outgoing.y +
            incoming.z * outgoing.z
        ) / (incoming_len * outgoing_len)
        dot = max(-1.0, min(1.0, dot))
        angle_score = (1.0 - dot) * 0.5

    return angle_score + speed_score


def _key_motion_break_score(position_at_time, key_frame, previous_frame, next_frame, start_time, end_time):
    previous_gap = max(0.001, float(key_frame) - float(previous_frame))
    next_gap = max(0.001, float(next_frame) - float(key_frame))
    nearby_span = min(previous_gap, next_gap)
    tangent_offset = max(0.05, min(0.20, nearby_span * 0.05))
    context_offset = max(0.5, min(1.0, nearby_span * 0.25))

    before_frame = max(float(start_time), float(key_frame) - tangent_offset)
    after_frame = min(float(end_time), float(key_frame) + tangent_offset)
    before_pos = position_at_time(before_frame)
    key_pos = position_at_time(key_frame)
    after_pos = position_at_time(after_frame)
    if before_pos is None or key_pos is None or after_pos is None:
        return 0.0

    score = _motion_break_score(before_pos, key_pos, after_pos)

    context_before = position_at_time(max(float(start_time), float(key_frame) - context_offset))
    context_after = position_at_time(min(float(end_time), float(key_frame) + context_offset))
    if context_before is not None and context_after is not None:
        local_len = max(_point_distance(before_pos, key_pos), _point_distance(key_pos, after_pos))
        context_len = max(_point_distance(context_before, key_pos), _point_distance(key_pos, context_after))
        expected_local_len = (context_len / max(context_offset, 0.001)) * tangent_offset
        if expected_local_len > 0.000001 and local_len < expected_local_len * 0.25:
            return 0.0

    return score


def _collect_key_frames_from_source(source_name, start_time, end_time):
    key_frames = set()
    try:
        raw_keys = cmds.keyframe(source_name, query=True, time=(start_time, end_time), timeChange=True) or []
        key_frames.update(float(frame) for frame in raw_keys)
    except Exception:
        pass

    visited = set()

    def _walk_upstream(item, depth=0):
        if not item or depth > 12:
            return
        node = item.split(".", 1)[0]
        if not node or node in visited:
            return
        visited.add(node)
        try:
            node_type = cmds.nodeType(node)
        except Exception:
            return
        if node_type.startswith("animCurve"):
            try:
                raw_keys = cmds.keyframe(node, query=True, time=(start_time, end_time), timeChange=True) or []
                key_frames.update(float(frame) for frame in raw_keys)
            except Exception:
                pass
            return
        try:
            upstream = cmds.listConnections(
                node,
                source=True,
                destination=False,
                plugs=True,
                skipConversionNodes=True,
            ) or []
        except Exception:
            upstream = []
        for upstream_item in upstream:
            _walk_upstream(upstream_item, depth + 1)

    for attr in (
        "translateX", "translateY", "translateZ",
        "rotateX", "rotateY", "rotateZ",
        "scaleX", "scaleY", "scaleZ",
    ):
        try:
            if cmds.objExists(f"{source_name}.{attr}"):
                _walk_upstream(f"{source_name}.{attr}")
        except Exception:
            pass

    return sorted(key_frames)

# ----------------------------------------------------------------------
# Node definition
# ----------------------------------------------------------------------
class AnimKeyMotionTrailNode(omui.MPxLocatorNode if 'omui' in globals() and hasattr(omui, 'MPxLocatorNode') else om.MPxLocatorNode):
    
    # Attributes
    targetMatrix = om.MObject()
    sourceComponent = om.MObject()
    startTime = om.MObject()
    endTime = om.MObject()
    increment = om.MObject()
    sampleDensity = om.MObject()
    
    # Appearance attributes
    trailColor = om.MObject()
    pastColor = om.MObject()
    futureColor = om.MObject()
    currentFrameColor = om.MObject()
    keyframeColor = om.MObject()
    previousKeyColor = om.MObject()
    nextKeyColor = om.MObject()
    popColor = om.MObject()
    trailLineWidth = om.MObject()
    showPopWarnings = om.MObject()
    popThreshold = om.MObject()
    dirtyStartTime = om.MObject()
    dirtyEndTime = om.MObject()
    
    # Cache versioning — bumped externally by trail.py when animation changes
    cacheVersion = om.MObject()
    
    # Output to trigger evaluation
    outData = om.MObject()
    
    def __init__(self):
        super(AnimKeyMotionTrailNode, self).__init__()
        
    def postConstructor(self):
        pass

    def setDependentsDirty(self, plug, plugArray):
        # No longer clears any cache here — cache invalidation is driven
        # exclusively by the cacheVersion attribute, which trail.py bumps
        # when a real animation edit occurs.
        pass

    def compute(self, plug, dataBlock):
        if plug == AnimKeyMotionTrailNode.outData:
            outHandle = dataBlock.outputValue(AnimKeyMotionTrailNode.outData)
            outHandle.setFloat(1.0)
            dataBlock.setClean(plug)
        return None
        
    @classmethod
    def creator(cls):
        return AnimKeyMotionTrailNode()

    @classmethod
    def initialize(cls):
        nAttr = om.MFnNumericAttribute()
        mAttr = om.MFnMatrixAttribute()
        tAttr = om.MFnTypedAttribute()
        
        # Target matrix
        cls.targetMatrix = mAttr.create("targetMatrix", "tm", om.MFnMatrixAttribute.kDouble)
        mAttr.storable = False

        cls.sourceComponent = tAttr.create("sourceComponent", "scmp", om.MFnData.kString)
        tAttr.storable = True
        
        # Time settings
        cls.startTime = nAttr.create("startTime", "st", om.MFnNumericData.kInt, 1)
        cls.endTime = nAttr.create("endTime", "et", om.MFnNumericData.kInt, 120)
        cls.increment = nAttr.create("increment", "inc", om.MFnNumericData.kInt, 1)
        cls.sampleDensity = nAttr.create("sampleDensity", "sdn", om.MFnNumericData.kInt, 1)
        
        # Appearance
        cls.trailColor = nAttr.createColor("trailColor", "tc")
        _set_color_default(nAttr, (1.0, 0.82, 0.18))

        cls.pastColor = nAttr.createColor("pastColor", "pc")
        _set_color_default(nAttr, (1.0, 0.82, 0.18))

        cls.futureColor = nAttr.createColor("futureColor", "fc")
        _set_color_default(nAttr, (1.0, 0.82, 0.18))

        cls.currentFrameColor = nAttr.createColor("currentFrameColor", "cfc")
        _set_color_default(nAttr, (0.18, 0.56, 1.0))

        cls.keyframeColor = nAttr.createColor("keyframeColor", "kfc")
        _set_color_default(nAttr, (1.0, 0.86, 0.36))

        cls.previousKeyColor = nAttr.createColor("previousKeyColor", "pkc")
        _set_color_default(nAttr, (1.0, 0.82, 0.18))

        cls.nextKeyColor = nAttr.createColor("nextKeyColor", "nkc")
        _set_color_default(nAttr, (1.0, 0.82, 0.18))

        cls.popColor = nAttr.createColor("popColor", "poc")
        _set_color_default(nAttr, (1.0, 0.12, 0.24))
        
        cls.trailLineWidth = nAttr.create("trailLineWidth", "tlw", om.MFnNumericData.kFloat, 2.0)

        cls.showPopWarnings = nAttr.create("showPopWarnings", "spw", om.MFnNumericData.kBoolean, True)
        cls.popThreshold = nAttr.create("popThreshold", "pth", om.MFnNumericData.kFloat, 0.4)
        cls.dirtyStartTime = nAttr.create("dirtyStartTime", "dst", om.MFnNumericData.kFloat, 1.0e20)
        cls.dirtyEndTime = nAttr.create("dirtyEndTime", "det", om.MFnNumericData.kFloat, -1.0e20)

        # Cache version — bumped by trail.py on real animation edits
        cls.cacheVersion = nAttr.create("cacheVersion", "cv", om.MFnNumericData.kInt, 0)
        nAttr.storable = True

        # Output
        cls.outData = nAttr.create("outData", "out", om.MFnNumericData.kFloat, 0.0)
        nAttr.writable = False
        nAttr.storable = False
        
        # Add attributes
        cls.addAttribute(cls.targetMatrix)
        cls.addAttribute(cls.sourceComponent)
        cls.addAttribute(cls.startTime)
        cls.addAttribute(cls.endTime)
        cls.addAttribute(cls.increment)
        cls.addAttribute(cls.sampleDensity)
        cls.addAttribute(cls.trailColor)
        cls.addAttribute(cls.pastColor)
        cls.addAttribute(cls.futureColor)
        cls.addAttribute(cls.currentFrameColor)
        cls.addAttribute(cls.keyframeColor)
        cls.addAttribute(cls.previousKeyColor)
        cls.addAttribute(cls.nextKeyColor)
        cls.addAttribute(cls.popColor)
        cls.addAttribute(cls.trailLineWidth)
        cls.addAttribute(cls.showPopWarnings)
        cls.addAttribute(cls.popThreshold)
        cls.addAttribute(cls.dirtyStartTime)
        cls.addAttribute(cls.dirtyEndTime)
        cls.addAttribute(cls.cacheVersion)
        cls.addAttribute(cls.outData)
        
        # Affects
        cls.attributeAffects(cls.targetMatrix, cls.outData)
        cls.attributeAffects(cls.sourceComponent, cls.outData)
        cls.attributeAffects(cls.startTime, cls.outData)
        cls.attributeAffects(cls.endTime, cls.outData)
        cls.attributeAffects(cls.increment, cls.outData)
        cls.attributeAffects(cls.sampleDensity, cls.outData)
        cls.attributeAffects(cls.showPopWarnings, cls.outData)
        cls.attributeAffects(cls.popThreshold, cls.outData)
        cls.attributeAffects(cls.dirtyStartTime, cls.outData)
        cls.attributeAffects(cls.dirtyEndTime, cls.outData)
        cls.attributeAffects(cls.cacheVersion, cls.outData)

# ----------------------------------------------------------------------
# Draw Override
# ----------------------------------------------------------------------
class TrailUserData(om.MUserData):
    def __init__(self):
        super(TrailUserData, self).__init__(False)

        # --- Persistent cache (rebuilt only when cacheVersion changes) ---
        self._cached_points = []          # [(frame, MPoint), ...]
        self._cached_key_data = []        # [(frame, MPoint), ...]
        self._cached_pop_frames = set()
        self._cached_pop_segments = set()
        self._cached_all_key_frames = []  # sorted list of key frame floats
        self._last_cache_version = -1
        self._last_config_key = None
        self._last_draw_arrays_key = None
        self._cached_bbox = None          # om.MBoundingBox or None
        self.source_component = ""

        # --- Per-frame draw data (populated from cache each frame) ---
        self.points = om.MPointArray()
        self.frames = []
        self.color = om.MColor((1.0, 0.82, 0.18, 1))
        self.past_color = om.MColor((1.0, 0.82, 0.18, 1))
        self.future_color = om.MColor((1.0, 0.82, 0.18, 1))
        self.current_color = om.MColor((0.18, 0.56, 1.0, 1))
        self.key_color = om.MColor((1.0, 0.86, 0.36, 1))
        self.previous_key_color = om.MColor((1.0, 0.82, 0.18, 1))
        self.next_key_color = om.MColor((1.0, 0.82, 0.18, 1))
        self.pop_color = om.MColor((1.0, 0.12, 0.24, 1))
        self.show_pop_warnings = True
        self.pop_threshold = 0.4
        self.line_width = 2.0
        self.current_frame_pos = om.MPoint()
        self.current_frame = 1
        self.is_playing = False
        self.start_time = 1
        self.end_time = 120
        self.previous_key_frame = None
        self.next_key_frame = None
        self.pop_frames = set()
        self.pop_segments = set()
        
        # Tangents and keys
        self.key_points = om.MPointArray()
        self.key_frames = []

class AnimKeyMotionTrailDrawOverride(omr.MPxDrawOverride):
    
    @staticmethod
    def creator(obj):
        return AnimKeyMotionTrailDrawOverride(obj)

    def __init__(self, obj):
        super(AnimKeyMotionTrailDrawOverride, self).__init__(obj, AnimKeyMotionTrailDrawOverride.draw)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kOpenGLCoreProfile | omr.MRenderer.kDirectX11 | omr.MRenderer.kOpenGL

    def isBounded(self, objPath, cameraPath):
        return False
        
    def boundingBox(self, objPath, cameraPath):
        try:
            fn_node = om.MFnDependencyNode(objPath.node())
            start_time = fn_node.findPlug("startTime", False).asInt()
            end_time = fn_node.findPlug("endTime", False).asInt()
            target_plug = fn_node.findPlug("targetMatrix", False)
            start_time, end_time = sorted((start_time, end_time))

            bbox = om.MBoundingBox()
            found_point = False
            
            # Fast bounding box: evaluate only a few key frames instead of every frame
            eval_frames = [
                start_time,
                start_time + (end_time - start_time) * 0.25,
                start_time + (end_time - start_time) * 0.5,
                start_time + (end_time - start_time) * 0.75,
                end_time
            ]
            for frame in eval_frames:
                point = _matrix_position_at_time(target_plug, frame)
                if point:
                    bbox.expand(point)
                    found_point = True

            current_point = _matrix_position_at_time(target_plug, oma.MAnimControl.currentTime().value)
            if current_point:
                bbox.expand(current_point)
                found_point = True

            if found_point:
                padding = max(0.5, fn_node.findPlug("trailLineWidth", False).asFloat() * 0.08)
                bbox.expand(om.MPoint(bbox.min.x - padding, bbox.min.y - padding, bbox.min.z - padding))
                bbox.expand(om.MPoint(bbox.max.x + padding, bbox.max.y + padding, bbox.max.z + padding))
                return bbox
        except Exception:
            pass

        return om.MBoundingBox(om.MPoint(-0.5, -0.5, -0.5), om.MPoint(0.5, 0.5, 0.5))

    def disableInternalBoundingBoxDraw(self):
        return True

    def hasUIDrawables(self):
        return True

    def wantUserSelection(self):
        return False

    @staticmethod
    def _mix_color(color_a, color_b, amount):
        amount = max(0.0, min(1.0, amount))
        inverse = 1.0 - amount
        return om.MColor((
            color_a.r * inverse + color_b.r * amount,
            color_a.g * inverse + color_b.g * amount,
            color_a.b * inverse + color_b.b * amount,
            1.0,
        ))

    @staticmethod
    def _with_alpha(color, alpha):
        color = _as_mcolor(color)
        alpha = max(0.0, min(1.0, float(alpha)))
        return om.MColor((color.r, color.g, color.b, alpha))

    @staticmethod
    def _trail_segment_opacity(data, frame):
        spacing = AnimKeyMotionTrailDrawOverride._average_key_spacing(data)
        span = max(1.0, float(data.end_time - data.start_time))
        falloff_range = max(2.0, min(span * 0.5, spacing * 2.4))
        distance = abs(float(frame) - float(data.current_frame))
        falloff = max(0.0, 1.0 - (distance / falloff_range))
        falloff = falloff * falloff
        return 0.4 + (falloff * 0.6)

    @staticmethod
    def _frame_color(data, frame):
        if AnimKeyMotionTrailDrawOverride._is_pop_frame(data, frame):
            return data.pop_color
        base = data.past_color if frame < data.current_frame else data.future_color
        span = max(1.0, float(data.end_time - data.start_time))
        proximity_range = max(2.0, span * 0.07)
        distance = abs(float(frame) - data.current_frame)
        proximity = max(0.0, 1.0 - (distance / proximity_range))
        proximity = round(proximity * 10.0) / 10.0
        return AnimKeyMotionTrailDrawOverride._mix_color(base, data.current_color, proximity)

    @staticmethod
    def _frame_tolerance(data):
        if len(data.frames) >= 2:
            return max(0.51, abs(float(data.frames[1]) - float(data.frames[0])) * 0.75)
        return 0.51

    @staticmethod
    def _same_frame(data, frame_a, frame_b):
        if frame_a is None or frame_b is None:
            return False
        return abs(float(frame_a) - float(frame_b)) <= AnimKeyMotionTrailDrawOverride._frame_tolerance(data)

    @staticmethod
    def _is_pop_frame(data, frame):
        tolerance = AnimKeyMotionTrailDrawOverride._frame_tolerance(data)
        return any(abs(float(frame) - float(pop_frame)) <= tolerance for pop_frame in data.pop_frames)

    @staticmethod
    def _segment_has_pop(data, frame_a, frame_b, segment_index=None):
        if segment_index is not None and segment_index in data.pop_segments:
            return True
        low = min(float(frame_a), float(frame_b))
        high = max(float(frame_a), float(frame_b))
        padding = max(0.001, (high - low) * 0.25)
        return any(low - padding <= float(pop_frame) <= high + padding for pop_frame in data.pop_frames)

    @staticmethod
    def _sync_draw_arrays(data):
        draw_key = (
            data._last_cache_version,
            data._last_config_key,
            len(data._cached_points),
            len(data._cached_key_data),
            len(data._cached_pop_frames),
            len(data._cached_pop_segments),
        )
        if data._last_draw_arrays_key == draw_key:
            return

        data.points.clear()
        data.frames = []
        for frame, point in data._cached_points:
            data.points.append(point)
            data.frames.append(float(frame))

        data.key_points.clear()
        data.key_frames = []
        for frame, point in data._cached_key_data:
            data.key_points.append(point)
            data.key_frames.append(float(frame))

        data._last_draw_arrays_key = draw_key

    @staticmethod
    def _cached_position_at_frame(data, frame):
        if not data._cached_points:
            return None
        frame = float(frame)
        frames = data.frames
        if not frames:
            return None
        if frame <= frames[0]:
            return data.points[0]
        if frame >= frames[-1]:
            return data.points[len(data.points) - 1]

        index = bisect.bisect_left(frames, frame)
        if index <= 0:
            return data.points[0]
        if index >= len(frames):
            return data.points[len(data.points) - 1]

        frame_a = float(frames[index - 1])
        frame_b = float(frames[index])
        if abs(frame_b - frame_a) <= 0.000001:
            return data.points[index]
        amount = (frame - frame_a) / (frame_b - frame_a)
        return _lerp_point(data.points[index - 1], data.points[index], amount)

    @staticmethod
    def _update_neighbor_keys(data):
        key_frames = data._cached_all_key_frames
        if not key_frames:
            data.previous_key_frame = None
            data.next_key_frame = None
            return
        index = bisect.bisect_left(key_frames, data.current_frame)
        data.previous_key_frame = key_frames[index - 1] if index > 0 else None
        data.next_key_frame = key_frames[index] if index < len(key_frames) else None

    @staticmethod
    def _position_sampler(data, target_plug):
        def _position_at_time(frame):
            return _matrix_position_at_time(target_plug, frame)

        return _position_at_time

    @staticmethod
    def _key_marker_color(data, frame):
        if AnimKeyMotionTrailDrawOverride._is_pop_frame(data, frame):
            return data.pop_color
        if AnimKeyMotionTrailDrawOverride._same_frame(data, frame, data.current_frame):
            return data.current_color
        if float(frame) < data.current_frame:
            return data.previous_key_color
        if float(frame) > data.current_frame:
            return data.next_key_color
        return data.key_color

    @staticmethod
    def _average_key_spacing(data):
        if len(data.key_frames) < 2:
            return max(1.0, float(data.end_time - data.start_time) * 0.1)
        spans = []
        for index in range(len(data.key_frames) - 1):
            spans.append(abs(float(data.key_frames[index + 1]) - float(data.key_frames[index])))
        return max(1.0, sum(spans) / float(len(spans)))

    @staticmethod
    def _key_marker_size(data, frame, outline=False):
        spacing = AnimKeyMotionTrailDrawOverride._average_key_spacing(data)
        falloff_range = max(1.0, spacing * 2.4)
        distance = abs(float(frame) - data.current_frame)
        falloff = max(0.0, 1.0 - (distance / falloff_range))
        falloff = falloff * falloff
        size = data.line_width + 1.5 + (falloff * 5.0)
        if AnimKeyMotionTrailDrawOverride._is_pop_frame(data, frame):
            size += 2.5
        if AnimKeyMotionTrailDrawOverride._same_frame(data, frame, data.current_frame):
            size += 1.0
        if outline:
            size += 1.5
        return size

    @staticmethod
    def _draw_single_point(draw_manager, point):
        point_array = om.MPointArray()
        point_array.append(point)
        draw_manager.mesh(omr.MUIDrawManager.kPoints, point_array)

    @staticmethod
    def _screen_projection(frame_context):
        if frame_context is None:
            return None
        try:
            viewport = frame_context.getViewportDimensions()
            if not viewport or len(viewport) < 4:
                return None
        except Exception:
            return None

        for matrix_type in (omr.MFrameContext.kViewProjMtx, omr.MFrameContext.kWorldViewProjMtx):
            try:
                matrix = frame_context.getMatrix(matrix_type)
                if matrix is not None:
                    return matrix, viewport
            except Exception:
                pass
        return None

    @staticmethod
    def _world_to_screen(frame_context, point, projection=None):
        projection = projection or AnimKeyMotionTrailDrawOverride._screen_projection(frame_context)
        if projection is None:
            return None
        try:
            matrix, viewport = projection
            clip = point * matrix
            if clip.w <= 0.000001:
                return None
            ndc_x = clip.x / clip.w
            ndc_y = clip.y / clip.w
            x = viewport[0] + ((ndc_x + 1.0) * 0.5 * viewport[2])
            y = viewport[1] + ((1.0 - ndc_y) * 0.5 * viewport[3])
            return om.MPoint(x, y, 0.0)
        except Exception:
            return None

    @staticmethod
    def _screen_distance(point_a, point_b):
        if point_a is None or point_b is None:
            return 0.0
        dx = point_a.x - point_b.x
        dy = point_a.y - point_b.y
        return math.sqrt(dx * dx + dy * dy)

    @staticmethod
    def _interpolate_point(point_a, point_b, amount):
        inverse = 1.0 - amount
        return om.MPoint(
            point_a.x * inverse + point_b.x * amount,
            point_a.y * inverse + point_b.y * amount,
            point_a.z * inverse + point_b.z * amount,
        )

    @staticmethod
    def _draw_screen_circle(draw_manager, frame_context, point, radius, color):
        color = _as_mcolor(color)
        draw_manager.setColor(color)
        draw_manager.setPointSize(radius * 2.0)
        AnimKeyMotionTrailDrawOverride._draw_single_point(draw_manager, point)

    @staticmethod
    def _begin_non_selectable_drawable(draw_manager):
        try:
            draw_manager.beginDrawable(omr.MUIDrawManager.kNonSelectable)
            return True
        except TypeError:
            try:
                draw_manager.beginDrawable()
                return True
            except Exception:
                return False
        except Exception:
            try:
                draw_manager.beginDrawable()
                return True
            except Exception:
                return False

    @staticmethod
    def _begin_xray(draw_manager):
        try:
            draw_manager.beginDrawInXray()
            return True
        except Exception:
            return False

    @staticmethod
    def _end_xray(draw_manager, started):
        if not started:
            return
        try:
            draw_manager.endDrawInXray()
        except Exception:
            pass

    @staticmethod
    def _gl_function_table():
        try:
            renderer = omr_legacy.MHardwareRenderer.theRenderer()
            if renderer is None:
                return None
            return renderer.glFunctionTable()
        except Exception:
            return None

    @staticmethod
    def _disable_depth_test():
        """Force-disable GL depth test so the trail curve draws as an overlay."""
        try:
            gl_ft = AnimKeyMotionTrailDrawOverride._gl_function_table()
            if gl_ft is None:
                return False
            gl_ft.glDisable(omr_legacy.MGL_DEPTH_TEST)
            return True
        except Exception:
            return False

    @staticmethod
    def _restore_depth_test():
        """Restore GL depth test after drawing the overlay trail curve."""
        try:
            gl_ft = AnimKeyMotionTrailDrawOverride._gl_function_table()
            if gl_ft is not None:
                gl_ft.glEnable(omr_legacy.MGL_DEPTH_TEST)
        except Exception:
            pass

    @staticmethod
    def _draw_trail_halo(draw_manager, data):
        return

    @staticmethod
    def _set_overlay_depth_priority(draw_manager):
        """Ask VP2 to draw the trail above geometry when the API exposes it."""
        try:
            if hasattr(draw_manager, "setDepthPriority") and hasattr(omr, "MRenderItem"):
                draw_manager.setDepthPriority(omr.MRenderItem.sActiveWireDepthPriority)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _draw_clean_trail_segment(draw_manager, point_a, point_b, color, width):
        """Draw one clean world-space segment so it stays locked to key markers."""
        if color is None:
            return

        draw_manager.setColor(_as_mcolor(color))
        draw_manager.setLineWidth(max(1.25, min(6.0, width)))
        AnimKeyMotionTrailDrawOverride._set_overlay_depth_priority(draw_manager)

        try:
            draw_manager.line(point_a, point_b)
            return
        except Exception:
            pass

        try:
            segment = om.MPointArray()
            segment.append(point_a)
            segment.append(point_b)
            draw_manager.mesh(omr.MUIDrawManager.kLines, segment)
        except Exception:
            pass

    @staticmethod
    def _draw_colored_trail(draw_manager, data, frame_context=None):
        count = len(data.points)
        if count < 2:
            return

        trail_width = max(1.25, min(6.0, data.line_width))
        max_segments = 72 if data.is_playing else None
        step = 1
        if max_segments and count - 1 > max_segments:
            step = int(math.ceil((count - 1) / float(max_segments)))
        
        for index in range(0, count - 1, step):
            next_index = min(index + step, count - 1)
            frame_a = data.frames[index]
            frame_b = data.frames[next_index]
            is_pop = AnimKeyMotionTrailDrawOverride._segment_has_pop(data, frame_a, frame_b, index)
            seg_color = data.pop_color if is_pop else AnimKeyMotionTrailDrawOverride._frame_color(data, (frame_a + frame_b) * 0.5)
            seg_color = _as_mcolor(seg_color, data.current_color)
            seg_opacity = AnimKeyMotionTrailDrawOverride._trail_segment_opacity(data, (frame_a + frame_b) * 0.5)
            seg_color = AnimKeyMotionTrailDrawOverride._with_alpha(seg_color, seg_opacity)
            seg_width = (trail_width + 1.5) if is_pop else trail_width

            point_a = data.points[index]
            point_b = data.points[next_index]
            AnimKeyMotionTrailDrawOverride._draw_clean_trail_segment(
                draw_manager,
                point_a,
                point_b,
                seg_color,
                seg_width,
            )

        return

    @staticmethod
    def _draw_key_markers(draw_manager, data, frame_context=None):
        if data.is_playing:
            return
        if len(data.key_points) == 0:
            return
        for index in range(len(data.key_points)):
            frame = data.key_frames[index] if index < len(data.key_frames) else data.current_frame
            point = data.key_points[index]
            radius = AnimKeyMotionTrailDrawOverride._key_marker_size(data, frame, outline=False) * 0.5
            AnimKeyMotionTrailDrawOverride._draw_screen_circle(
                draw_manager,
                frame_context,
                point,
                radius,
                AnimKeyMotionTrailDrawOverride._key_marker_color(data, frame),
            )

    @staticmethod
    def _draw_current_frame_marker(draw_manager, data, frame_context=None):
        radius = (data.line_width + 8.0) * 0.5
        AnimKeyMotionTrailDrawOverride._draw_screen_circle(
            draw_manager,
            frame_context,
            data.current_frame_pos,
            radius,
            data.current_color,
        )

    @staticmethod
    def _rebuild_cache(data, target_plug, start_time, end_time, increment, sample_density=1):
        data._cached_points = []
        data._cached_key_data = []
        data._cached_pop_frames = set()
        data._cached_pop_segments = set()
        data._cached_all_key_frames = []
        
        # Find keyframes from the object if connected
        key_frames = []
        source_name = None
        try:
            target_conns = target_plug.connectedTo(True, False)
            if target_conns:
                source_node = target_conns[0].node()
                fn_source = om.MFnDependencyNode(source_node)
                source_name = fn_source.name()
                key_frames = _collect_key_frames_from_source(source_name, start_time, end_time)
                if not key_frames:
                    # Fallback for simple direct animCurve connections.
                    direct_keys = set()
                    for attr in [
                        "translateX", "translateY", "translateZ",
                        "rotateX", "rotateY", "rotateZ",
                        "scaleX", "scaleY", "scaleZ",
                    ]:
                        try:
                            plug = fn_source.findPlug(attr, False)
                            conns = plug.connectedTo(True, False)
                            if conns and conns[0].node().hasFn(om.MFn.kAnimCurve):
                                anim_fn = oma.MFnAnimCurve(conns[0].node())
                                for i in range(anim_fn.numKeys):
                                    direct_keys.add(float(anim_fn.input(i).value))
                        except Exception:
                            pass
                    key_frames = sorted(direct_keys)
        except Exception:
            pass
            
        try:
            cache = {}
            sampler = AnimKeyMotionTrailDrawOverride._position_sampler(data, target_plug)

            def _position_at_time(frame):
                frame_key = float(frame)
                if frame_key in cache:
                    return cache[frame_key]
                point = sampler(frame_key)
                if not point:
                    return None
                cache[frame_key] = point
                return point

            key_frames = sorted(set(float(frame) for frame in key_frames if start_time <= float(frame) <= end_time))
            data._cached_all_key_frames = key_frames

            effective_pop_threshold = max(0.08, data.pop_threshold * 0.85)
            if data.show_pop_warnings and len(key_frames) >= 3:
                for key_index in range(1, len(key_frames) - 1):
                    previous_frame = key_frames[key_index - 1]
                    key_frame = key_frames[key_index]
                    next_frame = key_frames[key_index + 1]
                    score = _key_motion_break_score(
                        _position_at_time,
                        key_frame,
                        previous_frame,
                        next_frame,
                        start_time,
                        end_time,
                    )
                    if score >= effective_pop_threshold:
                        data._cached_pop_frames.add(key_frame)
            
            for k in key_frames:
                if k >= start_time and k <= end_time:
                    pos = _position_at_time(k)
                    if pos:
                        data._cached_key_data.append((float(k), pos))
            
            sample_density = max(1, min(8, int(sample_density)))
            sample_step = max(0.001, float(max(1, increment)) / float(sample_density))
            end_value = float(end_time)
            frame = float(start_time)
            sampled_frames = set()
            while frame <= end_value + 0.0001:
                frame_key = round(min(frame, end_value), 4)
                if frame_key in sampled_frames:
                    frame += sample_step
                    continue
                sampled_frames.add(frame_key)
                pos = _position_at_time(frame_key)
                if pos:
                    data._cached_points.append((float(frame_key), pos))
                frame += sample_step

            if data._cached_points and abs(data._cached_points[-1][0] - end_value) > 0.0001:
                pos = _position_at_time(end_value)
                if pos:
                    data._cached_points.append((end_value, pos))

            if data.show_pop_warnings and len(data._cached_points) >= 3:
                sampled_threshold = max(0.12, effective_pop_threshold * 1.05)
                sample_lengths = [
                    _point_distance(data._cached_points[index][1], data._cached_points[index + 1][1])
                    for index in range(len(data._cached_points) - 1)
                ]
                average_sample_length = (
                    sum(sample_lengths) / float(len(sample_lengths))
                    if sample_lengths else 0.0
                )
                for point_index in range(1, len(data._cached_points) - 1):
                    previous_pos = data._cached_points[point_index - 1][1]
                    point = data._cached_points[point_index][1]
                    next_pos = data._cached_points[point_index + 1][1]
                    local_length = max(_point_distance(previous_pos, point), _point_distance(point, next_pos))
                    if average_sample_length > 0.000001 and local_length < average_sample_length * 0.35:
                        continue
                    if _motion_break_score(previous_pos, point, next_pos) >= sampled_threshold:
                        frame = data._cached_points[point_index][0]
                        data._cached_pop_frames.add(float(frame))
                        data._cached_pop_segments.add(point_index - 1)
                        data._cached_pop_segments.add(point_index)
                        
        except Exception as e:
            pass

    @staticmethod
    def _refresh_cache_range(data, target_plug, start_time, end_time, increment, sample_density, dirty_start, dirty_end):
        if not data._cached_points:
            AnimKeyMotionTrailDrawOverride._rebuild_cache(
                data, target_plug, start_time, end_time, increment, sample_density
            )
            return

        dirty_start = max(float(start_time), float(dirty_start))
        dirty_end = min(float(end_time), float(dirty_end))
        if dirty_end < dirty_start:
            AnimKeyMotionTrailDrawOverride._rebuild_cache(
                data, target_plug, start_time, end_time, increment, sample_density
            )
            return

        dirty_span = max(0.0, float(dirty_end) - float(dirty_start))
        total_span = max(1.0, float(end_time) - float(start_time))
        small_dirty = dirty_span <= max(2.0, total_span * 0.04)

        if small_dirty and data._cached_all_key_frames:
            key_frames = list(data._cached_all_key_frames)
        else:
            key_frames = []
            try:
                target_conns = target_plug.connectedTo(True, False)
                if target_conns:
                    source_node = target_conns[0].node()
                    fn_source = om.MFnDependencyNode(source_node)
                    source_name = fn_source.name()
                    key_frames = _collect_key_frames_from_source(source_name, start_time, end_time)
                    if not key_frames:
                        direct_keys = set()
                        for attr in [
                            "translateX", "translateY", "translateZ",
                            "rotateX", "rotateY", "rotateZ",
                            "scaleX", "scaleY", "scaleZ",
                        ]:
                            try:
                                plug = fn_source.findPlug(attr, False)
                                conns = plug.connectedTo(True, False)
                                if conns and conns[0].node().hasFn(om.MFn.kAnimCurve):
                                    anim_fn = oma.MFnAnimCurve(conns[0].node())
                                    for i in range(anim_fn.numKeys):
                                        direct_keys.add(float(anim_fn.input(i).value))
                            except Exception:
                                pass
                        key_frames = sorted(direct_keys)
            except Exception:
                pass

        try:
            cache = {}
            sampler = AnimKeyMotionTrailDrawOverride._position_sampler(data, target_plug)

            def _position_at_time(frame):
                frame_key = float(frame)
                if frame_key in cache:
                    return cache[frame_key]
                point = sampler(frame_key)
                if not point:
                    return None
                cache[frame_key] = point
                return point

            key_frames = sorted(set(float(frame) for frame in key_frames if start_time <= float(frame) <= end_time))
            data._cached_all_key_frames = key_frames
            if small_dirty and data._cached_key_data:
                updated_key_data = []
                for key_frame, key_point in data._cached_key_data:
                    if dirty_start - 0.0001 <= float(key_frame) <= dirty_end + 0.0001:
                        pos = _position_at_time(key_frame)
                        updated_key_data.append((float(key_frame), pos or key_point))
                    else:
                        updated_key_data.append((float(key_frame), key_point))
                data._cached_key_data = updated_key_data
            else:
                data._cached_key_data = []
                for key_frame in key_frames:
                    pos = _position_at_time(key_frame)
                    if pos:
                        data._cached_key_data.append((float(key_frame), pos))

            updated_points = []
            touched = False
            for frame, point in data._cached_points:
                if dirty_start - 0.0001 <= float(frame) <= dirty_end + 0.0001:
                    new_point = _position_at_time(frame)
                    updated_points.append((float(frame), new_point or point))
                    touched = True
                else:
                    updated_points.append((float(frame), point))

            if not touched:
                return

            data._cached_points = updated_points

            if small_dirty:
                return

            data._cached_pop_frames = set()
            data._cached_pop_segments = set()

            effective_pop_threshold = max(0.08, data.pop_threshold * 0.85)
            if data.show_pop_warnings and len(key_frames) >= 3:
                for key_index in range(1, len(key_frames) - 1):
                    previous_frame = key_frames[key_index - 1]
                    key_frame = key_frames[key_index]
                    next_frame = key_frames[key_index + 1]
                    score = _key_motion_break_score(
                        _position_at_time,
                        key_frame,
                        previous_frame,
                        next_frame,
                        start_time,
                        end_time,
                    )
                    if score >= effective_pop_threshold:
                        data._cached_pop_frames.add(key_frame)

            if data.show_pop_warnings and len(data._cached_points) >= 3:
                sampled_threshold = max(0.12, effective_pop_threshold * 1.05)
                sample_lengths = [
                    _point_distance(data._cached_points[index][1], data._cached_points[index + 1][1])
                    for index in range(len(data._cached_points) - 1)
                ]
                average_sample_length = (
                    sum(sample_lengths) / float(len(sample_lengths))
                    if sample_lengths else 0.0
                )
                for point_index in range(1, len(data._cached_points) - 1):
                    previous_pos = data._cached_points[point_index - 1][1]
                    point = data._cached_points[point_index][1]
                    next_pos = data._cached_points[point_index + 1][1]
                    local_length = max(_point_distance(previous_pos, point), _point_distance(point, next_pos))
                    if average_sample_length > 0.000001 and local_length < average_sample_length * 0.35:
                        continue
                    if _motion_break_score(previous_pos, point, next_pos) >= sampled_threshold:
                        frame = data._cached_points[point_index][0]
                        data._cached_pop_frames.add(float(frame))
                        data._cached_pop_segments.add(point_index - 1)
                        data._cached_pop_segments.add(point_index)

        except Exception:
            AnimKeyMotionTrailDrawOverride._rebuild_cache(
                data, target_plug, start_time, end_time, increment, sample_density
            )

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData if isinstance(oldData, TrailUserData) else TrailUserData()

        node = objPath.node()
        fnNode = om.MFnDependencyNode(node)
        
        # Read attributes
        start_time = fnNode.findPlug("startTime", False).asInt()
        end_time = fnNode.findPlug("endTime", False).asInt()
        increment = max(1, fnNode.findPlug("increment", False).asInt())
        try:
            sample_density = max(1, min(8, fnNode.findPlug("sampleDensity", False).asInt()))
        except Exception:
            sample_density = 1
        
        data.color = _read_color_plug(fnNode, "trailColor", data.color)
        data.past_color = _read_color_plug(fnNode, "pastColor", data.past_color)
        data.future_color = _read_color_plug(fnNode, "futureColor", data.future_color)
        data.current_color = _read_color_plug(fnNode, "currentFrameColor", data.current_color)
        data.key_color = _read_color_plug(fnNode, "keyframeColor", data.key_color)
        data.previous_key_color = _read_color_plug(fnNode, "previousKeyColor", data.previous_key_color)
        data.next_key_color = _read_color_plug(fnNode, "nextKeyColor", data.next_key_color)
        data.pop_color = _read_color_plug(fnNode, "popColor", data.pop_color)
        data.show_pop_warnings = _read_bool_plug(fnNode, "showPopWarnings", data.show_pop_warnings)
        data.pop_threshold = max(0.05, _read_float_plug(fnNode, "popThreshold", data.pop_threshold))
        data.line_width = fnNode.findPlug("trailLineWidth", False).asFloat()
        # Vertex trails are baked onto a locator in trail.py. Evaluating worldMesh
        # from VP2 draw callbacks can crash Maya on rigged/deformed meshes.
        data.source_component = ""
        data.start_time = start_time
        data.end_time = end_time
        dirty_start = _read_float_plug(fnNode, "dirtyStartTime", 1.0e20)
        dirty_end = _read_float_plug(fnNode, "dirtyEndTime", -1.0e20)
        
        # Evaluate cache needs
        target_plug = fnNode.findPlug("targetMatrix", False)
        cache_version = fnNode.findPlug("cacheVersion", False).asInt()
        config_key = (
            start_time,
            end_time,
            increment,
            sample_density,
            round(data.pop_threshold, 3),
            data.show_pop_warnings,
            data.source_component,
        )
        
        needs_rebuild = (
            data._last_cache_version != cache_version or
            data._last_config_key != config_key
        )
        
        if needs_rebuild:
            can_refresh_range = (
                data._last_config_key == config_key and
                data._cached_points and
                dirty_start <= dirty_end
            )
            if can_refresh_range:
                self._refresh_cache_range(
                    data,
                    target_plug,
                    start_time,
                    end_time,
                    increment,
                    sample_density,
                    dirty_start,
                    dirty_end,
                )
            else:
                self._rebuild_cache(data, target_plug, start_time, end_time, increment, sample_density)
            data._last_cache_version = cache_version
            data._last_config_key = config_key
        
        AnimKeyMotionTrailDrawOverride._sync_draw_arrays(data)

        # Per-frame update
        data.is_playing = _is_anim_playing()
        data.current_frame = float(oma.MAnimControl.currentTime().value)
        current_pos = AnimKeyMotionTrailDrawOverride._cached_position_at_frame(data, data.current_frame)
        if current_pos is None and not data.is_playing:
            current_pos = _matrix_position_at_time(target_plug, data.current_frame)
        data.current_frame_pos = current_pos or om.MPoint()

        AnimKeyMotionTrailDrawOverride._update_neighbor_keys(data)
        
        data.pop_frames = data._cached_pop_frames
        data.pop_segments = data._cached_pop_segments
        
        return data

    @staticmethod
    def draw(context, data):
        # We use MUIDrawManager instead of raw OpenGL
        pass
        
    def addUIDrawables(self, objPath, drawManager, frameContext, data):
        if not isinstance(data, TrailUserData):
            return
            
        if len(data.points) < 2:
            return

        depth_disabled = AnimKeyMotionTrailDrawOverride._disable_depth_test()
        try:
            drawable_started = AnimKeyMotionTrailDrawOverride._begin_non_selectable_drawable(drawManager)
            if drawable_started:
                xray_started = False
                try:
                    xray_started = AnimKeyMotionTrailDrawOverride._begin_xray(drawManager)
                    AnimKeyMotionTrailDrawOverride._draw_colored_trail(drawManager, data, frameContext)
                except Exception:
                    pass
                finally:
                    AnimKeyMotionTrailDrawOverride._end_xray(drawManager, xray_started)
                    try:
                        drawManager.endDrawable()
                    except Exception:
                        pass
        finally:
            if depth_disabled:
                AnimKeyMotionTrailDrawOverride._restore_depth_test()

        drawable_started = AnimKeyMotionTrailDrawOverride._begin_non_selectable_drawable(drawManager)
        if drawable_started:
            xray_started = False
            try:
                xray_started = AnimKeyMotionTrailDrawOverride._begin_xray(drawManager)
                AnimKeyMotionTrailDrawOverride._draw_current_frame_marker(drawManager, data, frameContext)
                AnimKeyMotionTrailDrawOverride._draw_key_markers(drawManager, data, frameContext)
            except Exception:
                pass
            finally:
                AnimKeyMotionTrailDrawOverride._end_xray(drawManager, xray_started)
                try:
                    drawManager.endDrawable()
                except Exception:
                    pass


# ----------------------------------------------------------------------
# Lightweight editable tangent handle
# ----------------------------------------------------------------------
class AnimKeyTangentHandleNode(omui.MPxLocatorNode if 'omui' in globals() and hasattr(omui, 'MPxLocatorNode') else om.MPxLocatorNode):
    keyPosition = om.MObject()
    handleColor = om.MObject()
    lineColor = om.MObject()
    screenRadius = om.MObject()
    worldRadius = om.MObject()
    drawLine = om.MObject()

    def __init__(self):
        super(AnimKeyTangentHandleNode, self).__init__()

    @classmethod
    def creator(cls):
        return AnimKeyTangentHandleNode()

    @classmethod
    def initialize(cls):
        nAttr = om.MFnNumericAttribute()

        cls.keyPosition = nAttr.createPoint("keyPosition", "kp")
        nAttr.default = (0.0, 0.0, 0.0)

        cls.handleColor = nAttr.createColor("handleColor", "hc")
        _set_color_default(nAttr, (1.0, 0.82, 0.18))

        cls.lineColor = nAttr.createColor("lineColor", "lc")
        _set_color_default(nAttr, (1.0, 0.82, 0.18))

        cls.screenRadius = nAttr.create("screenRadius", "sr", om.MFnNumericData.kFloat, 9.0)
        cls.worldRadius = nAttr.create("worldRadius", "wr", om.MFnNumericData.kFloat, 0.15)
        cls.drawLine = nAttr.create("drawLine", "dl", om.MFnNumericData.kBoolean, True)

        cls.addAttribute(cls.keyPosition)
        cls.addAttribute(cls.handleColor)
        cls.addAttribute(cls.lineColor)
        cls.addAttribute(cls.screenRadius)
        cls.addAttribute(cls.worldRadius)
        cls.addAttribute(cls.drawLine)


class TangentHandleUserData(om.MUserData):
    def __init__(self):
        super(TangentHandleUserData, self).__init__(False)
        self.center = om.MPoint()
        self.key_point = om.MPoint()
        self.handle_color = om.MColor((1.0, 0.82, 0.18, 1.0))
        self.line_color = om.MColor((1.0, 0.82, 0.18, 1.0))
        self.screen_radius = 9.0
        self.world_radius = 0.15
        self.draw_line = True


class AnimKeyTangentHandleDrawOverride(omr.MPxDrawOverride):
    @staticmethod
    def creator(obj):
        return AnimKeyTangentHandleDrawOverride(obj)

    def __init__(self, obj):
        super(AnimKeyTangentHandleDrawOverride, self).__init__(obj, AnimKeyTangentHandleDrawOverride.draw)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kOpenGLCoreProfile | omr.MRenderer.kDirectX11 | omr.MRenderer.kOpenGL

    def isBounded(self, objPath, cameraPath):
        return True

    def boundingBox(self, objPath, cameraPath):
        try:
            fn_node = om.MFnDependencyNode(objPath.node())
            world_radius = max(0.05, _read_float_plug(fn_node, "worldRadius", 0.15))
            bbox = om.MBoundingBox(
                om.MPoint(-world_radius, -world_radius, -world_radius),
                om.MPoint(world_radius, world_radius, world_radius),
            )
            if _read_bool_plug(fn_node, "drawLine", True):
                key_plug = fn_node.findPlug("keyPosition", False)
                key_world = om.MPoint(
                    key_plug.child(0).asDouble(),
                    key_plug.child(1).asDouble(),
                    key_plug.child(2).asDouble(),
                )
                key_local = key_world * objPath.inclusiveMatrix().inverse()
                bbox.expand(key_local)
            return bbox
        except Exception:
            return om.MBoundingBox(om.MPoint(-0.25, -0.25, -0.25), om.MPoint(0.25, 0.25, 0.25))

    def disableInternalBoundingBoxDraw(self):
        return True

    def hasUIDrawables(self):
        return True

    def wantUserSelection(self):
        return True

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData
        if not isinstance(data, TangentHandleUserData):
            data = TangentHandleUserData()

        fn_node = om.MFnDependencyNode(objPath.node())
        matrix = objPath.inclusiveMatrix()
        inverse_matrix = matrix.inverse()
        data.center = om.MPoint(0.0, 0.0, 0.0)

        try:
            key_plug = fn_node.findPlug("keyPosition", False)
            key_world = om.MPoint(
                key_plug.child(0).asDouble(),
                key_plug.child(1).asDouble(),
                key_plug.child(2).asDouble(),
            )
            data.key_point = key_world * inverse_matrix
        except Exception:
            data.key_point = data.center

        data.handle_color = _read_color_plug(fn_node, "handleColor", data.handle_color)
        data.line_color = _read_color_plug(fn_node, "lineColor", data.line_color)
        data.screen_radius = max(4.0, _read_float_plug(fn_node, "screenRadius", data.screen_radius))
        data.world_radius = max(0.05, _read_float_plug(fn_node, "worldRadius", data.world_radius))
        data.draw_line = _read_bool_plug(fn_node, "drawLine", True)
        return data

    @staticmethod
    def draw(context, data):
        pass

    def addUIDrawables(self, objPath, drawManager, frameContext, data):
        if not isinstance(data, TangentHandleUserData):
            return

        drawable_started = False
        try:
            drawManager.beginDrawable()
            drawable_started = True
        except Exception:
            drawable_started = False

        if drawable_started:
            xray_started = False
            try:
                xray_started = AnimKeyMotionTrailDrawOverride._begin_xray(drawManager)

                if data.draw_line:
                    line_points = om.MPointArray()
                    line_points.append(data.key_point)
                    line_points.append(data.center)

                    drawManager.setLineWidth(1.8)
                    drawManager.setColor(data.line_color)
                    drawManager.mesh(omr.MUIDrawManager.kLines, line_points)

                handle_point = om.MPointArray()
                handle_point.append(data.center)
                drawManager.setPointSize(data.screen_radius)
                drawManager.setColor(data.handle_color)
                drawManager.mesh(omr.MUIDrawManager.kPoints, handle_point)
            except Exception:
                pass
            finally:
                AnimKeyMotionTrailDrawOverride._end_xray(drawManager, xray_started)
                try:
                    drawManager.endDrawable()
                except Exception:
                    pass


# ----------------------------------------------------------------------
# Interactive Tool Context
# ----------------------------------------------------------------------
class AnimKeyTrailContext(omui.MPxContext if 'omui' in globals() and hasattr(omui, 'MPxContext') else object):
    def __init__(self):
        super(AnimKeyTrailContext, self).__init__()
        self.setTitle("AnimKey Trail Edit")
        self.setImage("animkey_outliner_minimal_32.png", omui.MPxContext.kImage1)
        
        self.is_dragging = False
        self.drag_target = None  # Could be 'key' or 'in_tangent' or 'out_tangent'
        self.drag_index = -1
        
    def toolOnSetup(self, event):
        self.setHelpString("Click and drag keys or tangents to edit them.")
        
    def doPress(self, event):
        # We need to get the viewport and camera to perform raycasting
        view = omui.M3dView.active3dView()
        
        # Determine click position
        x, y = event.position()
        
        # TODO: Raycast against the cached points from the Draw Override
        # Since MPxContext runs independently, it would need to query the Trail node
        # or we could rely on Maya's selection mechanism.
        self.is_dragging = True
        
    def doDrag(self, event):
        if not self.is_dragging:
            return
            
        x, y = event.position()
        view = omui.M3dView.active3dView()
        
        # Unproject to 3D and calculate delta
        # TODO: Apply delta to animation curves
        
    def doRelease(self, event):
        self.is_dragging = False
        self.drag_target = None


class AnimKeyTrailContextCommand(omui.MPxContextCommand if 'omui' in globals() and hasattr(omui, 'MPxContextCommand') else object):
    def __init__(self):
        super(AnimKeyTrailContextCommand, self).__init__()

    @classmethod
    def creator(cls):
        return AnimKeyTrailContextCommand()

    def makeObj(self):
        if hasattr(omui, 'MPxContext'):
            return AnimKeyTrailContext()
        return None

# ----------------------------------------------------------------------
# Plugin initialization
# ----------------------------------------------------------------------
PLUGIN_CONTEXT_NAME = "animKeyTrailContextCmd"

def initializePlugin(plugin):
    pluginFn = om.MFnPlugin(plugin, "AnimKey", "1.0", "Any")

    try:
        pluginFn.registerNode(
            PLUGIN_NODE_NAME,
            PLUGIN_NODE_ID,
            AnimKeyMotionTrailNode.creator,
            AnimKeyMotionTrailNode.initialize,
            om.MPxNode.kLocatorNode,
            PLUGIN_DRAW_CLASSIFICATION
        )
    except Exception as e:
        om.MGlobal.displayError(f"Failed to register node: {PLUGIN_NODE_NAME} - {e}")

    try:
        omr.MDrawRegistry.registerDrawOverrideCreator(
            PLUGIN_DRAW_CLASSIFICATION,
            PLUGIN_DRAW_REGISTRANT_ID,
            AnimKeyMotionTrailDrawOverride.creator
        )
    except Exception as e:
        om.MGlobal.displayError(f"Failed to register draw override: {PLUGIN_DRAW_REGISTRANT_ID} - {e}")

    try:
        pluginFn.registerNode(
            TANGENT_HANDLE_NODE_NAME,
            TANGENT_HANDLE_NODE_ID,
            AnimKeyTangentHandleNode.creator,
            AnimKeyTangentHandleNode.initialize,
            om.MPxNode.kLocatorNode,
            TANGENT_HANDLE_DRAW_CLASSIFICATION
        )
    except Exception as e:
        om.MGlobal.displayError(f"Failed to register node: {TANGENT_HANDLE_NODE_NAME} - {e}")

    try:
        omr.MDrawRegistry.registerDrawOverrideCreator(
            TANGENT_HANDLE_DRAW_CLASSIFICATION,
            TANGENT_HANDLE_DRAW_REGISTRANT_ID,
            AnimKeyTangentHandleDrawOverride.creator
        )
    except Exception as e:
        om.MGlobal.displayError(f"Failed to register draw override: {TANGENT_HANDLE_DRAW_REGISTRANT_ID} - {e}")

    try:
        if hasattr(omui, 'MPxContextCommand'):
            pluginFn.registerContextCommand(PLUGIN_CONTEXT_NAME, AnimKeyTrailContextCommand.creator)
    except Exception as e:
        om.MGlobal.displayError(f"Failed to register context: {PLUGIN_CONTEXT_NAME} - {e}")

def uninitializePlugin(plugin):
    pluginFn = om.MFnPlugin(plugin)
    
    try:
        if hasattr(omui, 'MPxContextCommand'):
            pluginFn.deregisterContextCommand(PLUGIN_CONTEXT_NAME)
    except Exception as e:
        om.MGlobal.displayError(f"Failed to deregister context: {PLUGIN_CONTEXT_NAME} - {e}")

    try:
        omr.MDrawRegistry.deregisterDrawOverrideCreator(
            TANGENT_HANDLE_DRAW_CLASSIFICATION,
            TANGENT_HANDLE_DRAW_REGISTRANT_ID
        )
    except Exception as e:
        om.MGlobal.displayError(f"Failed to deregister draw override: {TANGENT_HANDLE_DRAW_REGISTRANT_ID} - {e}")

    try:
        pluginFn.deregisterNode(TANGENT_HANDLE_NODE_ID)
    except Exception as e:
        om.MGlobal.displayError(f"Failed to deregister node: {TANGENT_HANDLE_NODE_NAME} - {e}")

    try:
        omr.MDrawRegistry.deregisterDrawOverrideCreator(
            PLUGIN_DRAW_CLASSIFICATION,
            PLUGIN_DRAW_REGISTRANT_ID
        )
    except Exception as e:
        om.MGlobal.displayError(f"Failed to deregister draw override: {PLUGIN_DRAW_REGISTRANT_ID} - {e}")

    try:
        pluginFn.deregisterNode(PLUGIN_NODE_ID)
    except Exception as e:
        om.MGlobal.displayError(f"Failed to deregister node: {PLUGIN_NODE_NAME} - {e}")
