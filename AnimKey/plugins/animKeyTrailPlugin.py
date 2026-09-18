"""Viewport 2.0 nodes for AnimKey Motion Trail.

The trail node deliberately performs no Maya DG sampling. AnimKey's controller
owns scene evaluation and writes a compact JSON cache; the draw override only
parses that cache and turns it into batched viewport primitives. This mirrors
Animo Tracify's stable separation between evaluation and drawing.
"""

import bisect
import colorsys
import json
import math

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.api.OpenMayaRender as omr
import maya.api.OpenMayaUI as omui


PLUGIN_NODE_NAME = "animKeyMotionTrail"
PLUGIN_NODE_ID = om.MTypeId(0x0012A341)
PLUGIN_DRAW_CLASSIFICATION = "drawdb/geometry/animKeyMotionTrail/xray"
PLUGIN_DRAW_REGISTRANT_ID = "animKeyMotionTrailNodePlugin"

TANGENT_HANDLE_NODE_NAME = "animKeyTangentHandle"
TANGENT_HANDLE_NODE_ID = om.MTypeId(0x0012A342)
TANGENT_HANDLE_DRAW_CLASSIFICATION = "drawdb/geometry/animKeyTangentHandle"
TANGENT_HANDLE_DRAW_REGISTRANT_ID = "animKeyTangentHandleNodePlugin"

_PARSED_CACHE_STORE = {}


def maya_useNewAPI():
    pass


def _as_mcolor(value, fallback=None):
    fallback = fallback or om.MColor((1.0, 1.0, 1.0, 1.0))
    if isinstance(value, om.MColor):
        return value
    try:
        values = list(value)
        alpha = values[3] if len(values) > 3 else 1.0
        return om.MColor((float(values[0]), float(values[1]), float(values[2]), float(alpha)))
    except Exception:
        return fallback


def _set_color_default(attribute, color):
    try:
        attribute.default = tuple(float(channel) for channel in color[:3])
    except Exception:
        try:
            attribute.setDefault(*[float(channel) for channel in color[:3]])
        except Exception:
            pass


def _read_color(node, name, fallback):
    try:
        plug = node.findPlug(name, False)
        return om.MColor((
            plug.child(0).asFloat(),
            plug.child(1).asFloat(),
            plug.child(2).asFloat(),
            1.0,
        ))
    except Exception:
        return _as_mcolor(fallback)


def _read_float(node, name, fallback):
    try:
        return float(node.findPlug(name, False).asFloat())
    except Exception:
        return float(fallback)


def _read_int(node, name, fallback):
    try:
        return int(node.findPlug(name, False).asInt())
    except Exception:
        return int(fallback)


def _read_bool(node, name, fallback):
    try:
        return bool(node.findPlug(name, False).asBool())
    except Exception:
        return bool(fallback)


class AnimKeyMotionTrailNode(omui.MPxLocatorNode):
    targetMatrix = om.MObject()
    sourceComponent = om.MObject()
    timeInput = om.MObject()
    cameraWorldMatrix = om.MObject()
    cameraSpace = om.MObject()
    cacheData = om.MObject()
    startTime = om.MObject()
    endTime = om.MObject()
    increment = om.MObject()
    sampleDensity = om.MObject()
    displayFrameRange = om.MObject()
    trailColor = om.MObject()
    pastColor = om.MObject()
    futureColor = om.MObject()
    currentFrameColor = om.MObject()
    keyframeColor = om.MObject()
    previousKeyColor = om.MObject()
    nextKeyColor = om.MObject()
    popColor = om.MObject()
    trailLineWidth = om.MObject()
    markerSize = om.MObject()
    frameMarkerSize = om.MObject()
    colorMode = om.MObject()
    showKeyMarkers = om.MObject()
    showFrameMarkers = om.MObject()
    showPopWarnings = om.MObject()
    popThreshold = om.MObject()
    dirtyStartTime = om.MObject()
    dirtyEndTime = om.MObject()
    cacheVersion = om.MObject()
    outData = om.MObject()

    @classmethod
    def creator(cls):
        return cls()

    @classmethod
    def initialize(cls):
        numeric = om.MFnNumericAttribute()
        matrix = om.MFnMatrixAttribute()
        typed = om.MFnTypedAttribute()
        unit = om.MFnUnitAttribute()

        cls.targetMatrix = matrix.create("targetMatrix", "tm", om.MFnMatrixAttribute.kDouble)
        matrix.storable = False
        matrix.hidden = True
        cls.cameraWorldMatrix = matrix.create("cameraWorldMatrix", "cwm", om.MFnMatrixAttribute.kDouble)
        matrix.storable = False
        matrix.hidden = True

        cls.timeInput = unit.create("timeInput", "ti", om.MFnUnitAttribute.kTime, 0.0)
        unit.storable = False
        unit.hidden = True

        cls.sourceComponent = typed.create("sourceComponent", "scmp", om.MFnData.kString)
        typed.storable = True
        cls.cacheData = typed.create("cacheData", "cd", om.MFnData.kString)
        typed.storable = True
        typed.hidden = True

        cls.startTime = numeric.create("startTime", "st", om.MFnNumericData.kInt, 1)
        cls.endTime = numeric.create("endTime", "et", om.MFnNumericData.kInt, 120)
        cls.increment = numeric.create("increment", "inc", om.MFnNumericData.kInt, 1)
        numeric.setMin(1)
        cls.sampleDensity = numeric.create("sampleDensity", "sdn", om.MFnNumericData.kInt, 1)
        numeric.setMin(1)
        numeric.setMax(8)
        cls.displayFrameRange = numeric.create("displayFrameRange", "dfr", om.MFnNumericData.kInt, 18)
        numeric.setMin(0)
        cls.cameraSpace = numeric.create("cameraSpace", "cs", om.MFnNumericData.kBoolean, False)

        cls.trailColor = numeric.createColor("trailColor", "tc")
        _set_color_default(numeric, (0.12, 0.90, 0.82))
        cls.pastColor = numeric.createColor("pastColor", "pc")
        _set_color_default(numeric, (0.92, 0.14, 0.74))
        cls.futureColor = numeric.createColor("futureColor", "fc")
        _set_color_default(numeric, (0.12, 0.90, 0.82))
        cls.currentFrameColor = numeric.createColor("currentFrameColor", "cfc")
        _set_color_default(numeric, (1.0, 0.48, 0.90))
        cls.keyframeColor = numeric.createColor("keyframeColor", "kfc")
        _set_color_default(numeric, (0.98, 0.94, 1.0))
        cls.previousKeyColor = numeric.createColor("previousKeyColor", "pkc")
        _set_color_default(numeric, (0.92, 0.14, 0.74))
        cls.nextKeyColor = numeric.createColor("nextKeyColor", "nkc")
        _set_color_default(numeric, (0.12, 0.90, 0.82))
        cls.popColor = numeric.createColor("popColor", "poc")
        _set_color_default(numeric, (1.0, 0.12, 0.24))

        cls.trailLineWidth = numeric.create("trailLineWidth", "tlw", om.MFnNumericData.kFloat, 4.0)
        cls.markerSize = numeric.create("markerSize", "ms", om.MFnNumericData.kFloat, 5.0)
        cls.frameMarkerSize = numeric.create("frameMarkerSize", "fms", om.MFnNumericData.kFloat, 3.0)
        cls.colorMode = numeric.create("colorMode", "cm", om.MFnNumericData.kInt, 0)
        numeric.setMin(0)
        numeric.setMax(4)
        cls.showKeyMarkers = numeric.create("showKeyMarkers", "skm", om.MFnNumericData.kBoolean, False)
        cls.showFrameMarkers = numeric.create("showFrameMarkers", "sfm", om.MFnNumericData.kBoolean, True)
        cls.showPopWarnings = numeric.create("showPopWarnings", "spw", om.MFnNumericData.kBoolean, False)
        cls.popThreshold = numeric.create("popThreshold", "pth", om.MFnNumericData.kFloat, 0.4)
        cls.dirtyStartTime = numeric.create("dirtyStartTime", "dst", om.MFnNumericData.kFloat, 1.0e20)
        cls.dirtyEndTime = numeric.create("dirtyEndTime", "det", om.MFnNumericData.kFloat, -1.0e20)
        cls.cacheVersion = numeric.create("cacheVersion", "cv", om.MFnNumericData.kInt, 0)
        cls.outData = numeric.create("outData", "out", om.MFnNumericData.kFloat, 0.0)
        numeric.writable = False
        numeric.storable = False
        numeric.hidden = True

        attributes = (
            cls.targetMatrix, cls.sourceComponent, cls.timeInput, cls.cameraWorldMatrix,
            cls.cameraSpace, cls.cacheData, cls.startTime, cls.endTime, cls.increment,
            cls.sampleDensity, cls.displayFrameRange, cls.trailColor, cls.pastColor,
            cls.futureColor, cls.currentFrameColor, cls.keyframeColor,
            cls.previousKeyColor, cls.nextKeyColor, cls.popColor, cls.trailLineWidth,
            cls.markerSize, cls.frameMarkerSize, cls.colorMode, cls.showKeyMarkers,
            cls.showFrameMarkers, cls.showPopWarnings, cls.popThreshold,
            cls.dirtyStartTime, cls.dirtyEndTime, cls.cacheVersion, cls.outData,
        )
        for attribute in attributes:
            cls.addAttribute(attribute)
        for source in (cls.timeInput, cls.cacheData, cls.cameraSpace, cls.cameraWorldMatrix):
            cls.attributeAffects(source, cls.outData)

    def compute(self, plug, dataBlock):
        # Viewport data is externally cached; this node never evaluates the DG.
        return None

    def isBounded(self):
        return True

    def boundingBox(self):
        return om.MBoundingBox(
            om.MPoint(-1000000.0, -1000000.0, -1000000.0),
            om.MPoint(1000000.0, 1000000.0, 1000000.0),
        )


class TrailUserData(om.MUserData):
    def __init__(self):
        super(TrailUserData, self).__init__(False)
        self.points = om.MPointArray()
        self.frames = []
        self.key_points = om.MPointArray()
        self.key_frames = []
        self.pop_frames = set()
        self.pop_segments = set()
        self.current_frame = 0.0
        self.current_frame_pos = om.MPoint()
        self.start_time = 0.0
        self.end_time = 0.0
        self.display_frame_range = 18
        self.color = om.MColor((0.12, 0.90, 0.82, 1.0))
        self.past_color = om.MColor((0.92, 0.14, 0.74, 1.0))
        self.future_color = om.MColor((0.12, 0.90, 0.82, 1.0))
        self.current_color = om.MColor((1.0, 0.48, 0.90, 1.0))
        self.key_color = om.MColor((0.98, 0.94, 1.0, 1.0))
        self.previous_key_color = om.MColor((0.92, 0.14, 0.74, 1.0))
        self.next_key_color = om.MColor((0.12, 0.90, 0.82, 1.0))
        self.pop_color = om.MColor((1.0, 0.12, 0.24, 1.0))
        self.line_width = 4.0
        self.marker_size = 5.0
        self.frame_marker_size = 3.0
        self.color_mode = 0
        self.show_key_markers = False
        self.show_frame_markers = True
        self.show_pop_warnings = False
        self.pop_threshold = 0.4
        self.is_playing = False
        self.camera_space = False
        self._cached_all_key_frames = []


class AnimKeyMotionTrailDrawOverride(omr.MPxDrawOverride):
    def __init__(self, obj):
        super(AnimKeyMotionTrailDrawOverride, self).__init__(obj, None, True)

    @staticmethod
    def creator(obj):
        return AnimKeyMotionTrailDrawOverride(obj)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kAllDevices

    def hasUIDrawables(self):
        return True

    def isBounded(self, objPath, cameraPath):
        return True

    def boundingBox(self, objPath, cameraPath):
        return om.MBoundingBox(
            om.MPoint(-1000000.0, -1000000.0, -1000000.0),
            om.MPoint(1000000.0, 1000000.0, 1000000.0),
        )

    def disableInternalBoundingBoxDraw(self):
        return True

    @staticmethod
    def _parsed_cache(node_name, payload):
        cached = _PARSED_CACHE_STORE.get(node_name)
        if cached and cached[0] == payload:
            return cached[1]
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {}
        _PARSED_CACHE_STORE[node_name] = (payload, parsed)
        return parsed

    @staticmethod
    def _nearest_camera_depth(camera_positions, current_frame):
        if not camera_positions:
            return None
        exact = camera_positions.get(str(int(round(current_frame))))
        if exact is not None:
            return float(exact[2])
        candidates = []
        for frame_key, position in camera_positions.items():
            try:
                candidates.append((abs(float(frame_key) - current_frame), float(position[2])))
            except Exception:
                pass
        return min(candidates)[1] if candidates else None

    @staticmethod
    def _point_at(frames, points, frame):
        if not frames or len(points) == 0:
            return None
        frame = float(frame)
        if frame < float(frames[0]) - 0.0001 or frame > float(frames[-1]) + 0.0001:
            return None
        index = bisect.bisect_left(frames, frame)
        if index < len(frames) and abs(float(frames[index]) - frame) < 0.0001:
            return points[index]
        if index <= 0:
            return points[0]
        if index >= len(frames):
            return points[len(points) - 1]
        frame_a = float(frames[index - 1])
        frame_b = float(frames[index])
        amount = (frame - frame_a) / max(0.000001, frame_b - frame_a)
        point_a = points[index - 1]
        point_b = points[index]
        return om.MPoint(
            point_a.x + ((point_b.x - point_a.x) * amount),
            point_a.y + ((point_b.y - point_a.y) * amount),
            point_a.z + ((point_b.z - point_a.z) * amount),
        )

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData if isinstance(oldData, TrailUserData) else TrailUserData()
        data.points = om.MPointArray()
        data.frames = []
        data.key_points = om.MPointArray()
        data.key_frames = []
        data.pop_frames = set()
        data.pop_segments = set()

        node = objPath.node()
        if node.isNull():
            return data
        fn_node = om.MFnDependencyNode(node)
        data.current_frame = float(oma.MAnimControl.currentTime().value)
        data.start_time = float(_read_int(fn_node, "startTime", 1))
        data.end_time = float(_read_int(fn_node, "endTime", 120))
        data.display_frame_range = max(0, _read_int(fn_node, "displayFrameRange", 18))
        data.color = _read_color(fn_node, "trailColor", data.color)
        data.past_color = _read_color(fn_node, "pastColor", data.past_color)
        data.future_color = _read_color(fn_node, "futureColor", data.future_color)
        data.current_color = _read_color(fn_node, "currentFrameColor", data.current_color)
        data.key_color = _read_color(fn_node, "keyframeColor", data.key_color)
        data.previous_key_color = _read_color(fn_node, "previousKeyColor", data.previous_key_color)
        data.next_key_color = _read_color(fn_node, "nextKeyColor", data.next_key_color)
        data.pop_color = _read_color(fn_node, "popColor", data.pop_color)
        data.line_width = max(1.0, _read_float(fn_node, "trailLineWidth", 4.0))
        data.marker_size = max(2.0, _read_float(fn_node, "markerSize", 5.0))
        data.frame_marker_size = max(1.0, _read_float(fn_node, "frameMarkerSize", 3.0))
        data.color_mode = max(0, min(4, _read_int(fn_node, "colorMode", 0)))
        data.show_key_markers = _read_bool(fn_node, "showKeyMarkers", False)
        data.show_frame_markers = _read_bool(fn_node, "showFrameMarkers", True)
        data.show_pop_warnings = _read_bool(fn_node, "showPopWarnings", False)
        data.pop_threshold = max(0.05, _read_float(fn_node, "popThreshold", 0.4))
        data.camera_space = _read_bool(fn_node, "cameraSpace", False)
        try:
            data.is_playing = bool(oma.MAnimControl.isPlaying())
        except Exception:
            data.is_playing = False

        try:
            payload = fn_node.findPlug("cacheData", False).asString()
        except Exception:
            payload = ""
        if not payload:
            return data
        cache = self._parsed_cache(fn_node.name(), payload)
        positions = cache.get("positions", {})
        camera_positions = cache.get("cameraPositions", {})
        source_positions = camera_positions if data.camera_space and camera_positions else positions
        use_camera_flatten = bool(data.camera_space and camera_positions)

        camera_matrix = om.MMatrix()
        if use_camera_flatten:
            try:
                matrix_object = fn_node.findPlug("cameraWorldMatrix", False).asMObject()
                camera_matrix = om.MFnMatrixData(matrix_object).matrix()
            except Exception:
                use_camera_flatten = False
        reference_depth = self._nearest_camera_depth(camera_positions, data.current_frame) if use_camera_flatten else None
        if use_camera_flatten and reference_depth is None:
            return data

        ordered = []
        for frame_key, position in source_positions.items():
            try:
                ordered.append((float(frame_key), position))
            except Exception:
                pass
        ordered.sort(key=lambda item: item[0])
        for frame, position in ordered:
            point = om.MPoint(float(position[0]), float(position[1]), float(position[2]))
            if use_camera_flatten:
                depth = float(position[2])
                scale = reference_depth / depth if abs(depth) > 0.000001 else 1.0
                point = om.MPoint(
                    float(position[0]) * scale,
                    float(position[1]) * scale,
                    reference_depth,
                ) * camera_matrix
            data.frames.append(frame)
            data.points.append(point)

        all_key_frames = sorted(float(frame) for frame in cache.get("keyframes", []))
        data._cached_all_key_frames = list(all_key_frames)
        for frame in all_key_frames:
            point = self._point_at(data.frames, data.points, frame)
            if point is not None:
                data.key_frames.append(frame)
                data.key_points.append(point)
        data.pop_frames = set(float(frame) for frame in cache.get("popFrames", []))
        data.pop_segments = set(int(index) for index in cache.get("popSegments", []))
        current_point = self._point_at(data.frames, data.points, data.current_frame)
        data.current_frame_pos = current_point or om.MPoint()
        return data

    @staticmethod
    def _visible_index_range(data, padding=1):
        count = len(data.frames)
        if count == 0 or data.display_frame_range <= 0:
            return 0, count
        start_frame = float(data.current_frame) - float(data.display_frame_range)
        end_frame = float(data.current_frame) + float(data.display_frame_range)
        start_index = max(0, bisect.bisect_left(data.frames, start_frame) - padding)
        end_index = min(count, bisect.bisect_right(data.frames, end_frame) + padding)
        return start_index, end_index

    @staticmethod
    def _same_frame(frame_a, frame_b):
        return abs(float(frame_a) - float(frame_b)) < 0.0001

    @staticmethod
    def _is_pop_frame(data, frame):
        return any(AnimKeyMotionTrailDrawOverride._same_frame(frame, item) for item in data.pop_frames)

    @staticmethod
    def _gradient_color(data, frame):
        color_mode = int(getattr(data, "color_mode", 0) or 0)
        if color_mode <= 0:
            return None
        span = max(0.0001, float(data.end_time) - float(data.start_time))
        amount = max(0.0, min(1.0, (float(frame) - float(data.start_time)) / span))
        if color_mode == 1:
            red, green, blue = colorsys.hsv_to_rgb(amount * 0.85, 0.9, 1.0)
            return om.MColor((red, green, blue, 1.0))
        palettes = {
            2: [(0.90, 0.10, 0.05), (1.0, 0.35, 0.0), (1.0, 0.90, 0.30), (0.95, 0.35, 0.0)],
            3: [(0.0, 0.20, 0.60), (0.0, 0.60, 0.90), (0.20, 0.90, 0.80)],
            4: [(1.0, 0.85, 0.92), (1.0, 0.40, 0.70), (0.75, 0.35, 0.90)],
        }
        if color_mode == 2:
            amount = (amount - ((float(data.current_frame) * 0.04) % 1.0)) % 1.0
        palette = palettes.get(color_mode, palettes[4])
        scaled = amount * (len(palette) - 1)
        index = min(len(palette) - 2, int(scaled))
        blend = scaled - index
        first, second = palette[index], palette[index + 1]
        return om.MColor(tuple(
            first[channel] + ((second[channel] - first[channel]) * blend)
            for channel in range(3)
        ) + (1.0,))

    @staticmethod
    def _gradient_bucket(data, frame, count=8):
        span = max(0.0001, float(data.end_time) - float(data.start_time))
        amount = max(0.0, min(0.999999, (float(frame) - float(data.start_time)) / span))
        return int(amount * count)

    @staticmethod
    def _begin_xray(draw_manager):
        try:
            draw_manager.beginDrawInXray()
            return True
        except Exception:
            return False

    @staticmethod
    def _end_xray(draw_manager, started):
        if started:
            try:
                draw_manager.endDrawInXray()
            except Exception:
                pass

    @staticmethod
    def _draw_segment_batch(draw_manager, points, color, width):
        if len(points) < 2:
            return
        draw_manager.setColor(_as_mcolor(color))
        draw_manager.setLineWidth(max(1.0, min(10.0, float(width))))
        try:
            draw_manager.mesh(omr.MUIDrawManager.kLines, points)
        except Exception:
            pass

    @staticmethod
    def _draw_colored_trail(draw_manager, data, frame_context=None):
        start_index, end_index = AnimKeyMotionTrailDrawOverride._visible_index_range(data)
        if end_index - start_index < 2:
            return
        batches = {
            "past": om.MPointArray(),
            "future": om.MPointArray(),
            "current": om.MPointArray(),
        }
        deltas = [
            float(data.frames[index + 1]) - float(data.frames[index])
            for index in range(start_index, end_index - 1)
            if float(data.frames[index + 1]) > float(data.frames[index])
        ]
        expected_step = min(deltas) if deltas else 1.0
        for index in range(start_index, end_index - 1):
            frame_a = float(data.frames[index])
            frame_b = float(data.frames[index + 1])
            if frame_b - frame_a > max(3.0, expected_step * 3.0):
                continue
            midpoint = (frame_a + frame_b) * 0.5
            if int(getattr(data, "color_mode", 0) or 0) > 0:
                bucket = "gradient_{}".format(
                    AnimKeyMotionTrailDrawOverride._gradient_bucket(data, midpoint)
                )
                batches.setdefault(bucket, om.MPointArray())
            elif frame_a <= data.current_frame <= frame_b:
                bucket = "current"
            elif midpoint < data.current_frame:
                bucket = "past"
            else:
                bucket = "future"
            batches[bucket].append(data.points[index])
            batches[bucket].append(data.points[index + 1])

        for name, color in (
            ("past", data.past_color),
            ("future", data.future_color),
            ("current", data.current_color),
        ):
            AnimKeyMotionTrailDrawOverride._draw_segment_batch(
                draw_manager, batches[name], color, data.line_width
            )
        for bucket in range(8):
            name = "gradient_{}".format(bucket)
            points = batches.get(name)
            if points is None:
                continue
            representative = data.start_time + ((bucket + 0.5) / 8.0) * (
                data.end_time - data.start_time
            )
            AnimKeyMotionTrailDrawOverride._draw_segment_batch(
                draw_manager,
                points,
                AnimKeyMotionTrailDrawOverride._gradient_color(data, representative),
                data.line_width,
            )

    @staticmethod
    def _draw_frame_markers(draw_manager, data, frame_context=None):
        if data.is_playing or not data.show_frame_markers:
            return
        start_index, end_index = AnimKeyMotionTrailDrawOverride._visible_index_range(data)
        batches = {
            "past": om.MPointArray(),
            "future": om.MPointArray(),
            "current": om.MPointArray(),
        }
        for index in range(start_index, end_index):
            frame = float(data.frames[index])
            if AnimKeyMotionTrailDrawOverride._same_frame(frame, data.current_frame):
                name = "current"
            elif frame < data.current_frame:
                name = "past"
            else:
                name = "future"
            batches[name].append(data.points[index])
        for name, color, extra in (
            ("past", data.past_color, 0.0),
            ("future", data.future_color, 0.0),
            ("current", data.current_color, 0.8),
        ):
            points = batches[name]
            if len(points) == 0:
                continue
            draw_manager.setColor(color)
            draw_manager.setPointSize(data.frame_marker_size + extra)
            try:
                draw_manager.mesh(omr.MUIDrawManager.kPoints, points)
            except Exception:
                pass

    @staticmethod
    def _draw_key_markers(draw_manager, data, frame_context=None):
        if data.is_playing or not data.show_key_markers or len(data.key_points) == 0:
            return
        visible_start = data.current_frame - data.display_frame_range
        visible_end = data.current_frame + data.display_frame_range
        batches = {"past": om.MPointArray(), "future": om.MPointArray(), "current": om.MPointArray()}
        for index, frame in enumerate(data.key_frames):
            if index >= len(data.key_points):
                break
            if data.display_frame_range > 0 and not (visible_start <= frame <= visible_end):
                continue
            if AnimKeyMotionTrailDrawOverride._same_frame(frame, data.current_frame):
                name = "current"
            elif frame < data.current_frame:
                name = "past"
            else:
                name = "future"
            batches[name].append(data.key_points[index])
        for name, color, extra in (
            ("past", data.previous_key_color, 0.0),
            ("future", data.next_key_color, 0.0),
            ("current", data.current_color, 2.0),
        ):
            points = batches[name]
            if len(points) == 0:
                continue
            draw_manager.setColor(color)
            draw_manager.setPointSize(data.marker_size + extra)
            try:
                draw_manager.mesh(omr.MUIDrawManager.kPoints, points)
            except Exception:
                pass

    @staticmethod
    def _draw_current_frame_marker(draw_manager, data, frame_context=None):
        if len(data.points) == 0:
            return
        points = om.MPointArray()
        points.append(data.current_frame_pos)
        draw_manager.setColor(data.current_color)
        draw_manager.setPointSize(max(data.marker_size * 1.8, data.line_width + 4.0))
        try:
            draw_manager.mesh(omr.MUIDrawManager.kPoints, points)
        except Exception:
            pass

    @staticmethod
    def draw(context, data):
        pass

    def addUIDrawables(self, objPath, drawManager, frameContext, data):
        if not isinstance(data, TrailUserData) or len(data.points) < 2:
            return
        drawManager.beginDrawable(omr.MUIDrawManager.kNonSelectable)
        xray = self._begin_xray(drawManager)
        try:
            self._draw_colored_trail(drawManager, data, frameContext)
            self._draw_frame_markers(drawManager, data, frameContext)
            self._draw_key_markers(drawManager, data, frameContext)
            self._draw_current_frame_marker(drawManager, data, frameContext)
        finally:
            self._end_xray(drawManager, xray)
            drawManager.endDrawable()


class AnimKeyTangentHandleNode(omui.MPxLocatorNode):
    keyPosition = om.MObject()
    handleColor = om.MObject()
    lineColor = om.MObject()
    screenRadius = om.MObject()
    worldRadius = om.MObject()
    drawLine = om.MObject()

    @classmethod
    def creator(cls):
        return cls()

    @classmethod
    def initialize(cls):
        numeric = om.MFnNumericAttribute()
        cls.keyPosition = numeric.createPoint("keyPosition", "kp")
        numeric.default = (0.0, 0.0, 0.0)
        cls.handleColor = numeric.createColor("handleColor", "hc")
        _set_color_default(numeric, (1.0, 0.82, 0.18))
        cls.lineColor = numeric.createColor("lineColor", "lc")
        _set_color_default(numeric, (1.0, 0.82, 0.18))
        cls.screenRadius = numeric.create("screenRadius", "sr", om.MFnNumericData.kFloat, 9.0)
        cls.worldRadius = numeric.create("worldRadius", "wr", om.MFnNumericData.kFloat, 0.15)
        cls.drawLine = numeric.create("drawLine", "dl", om.MFnNumericData.kBoolean, True)
        for attribute in (
            cls.keyPosition, cls.handleColor, cls.lineColor,
            cls.screenRadius, cls.worldRadius, cls.drawLine,
        ):
            cls.addAttribute(attribute)


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
    def __init__(self, obj):
        super(AnimKeyTangentHandleDrawOverride, self).__init__(obj, None, True)

    @staticmethod
    def creator(obj):
        return AnimKeyTangentHandleDrawOverride(obj)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kAllDevices

    def hasUIDrawables(self):
        return True

    def wantUserSelection(self):
        return True

    def isBounded(self, objPath, cameraPath):
        return True

    def boundingBox(self, objPath, cameraPath):
        try:
            node = om.MFnDependencyNode(objPath.node())
            radius = max(0.05, _read_float(node, "worldRadius", 0.15))
            bounds = om.MBoundingBox(
                om.MPoint(-radius, -radius, -radius),
                om.MPoint(radius, radius, radius),
            )
            plug = node.findPlug("keyPosition", False)
            key_world = om.MPoint(
                plug.child(0).asDouble(), plug.child(1).asDouble(), plug.child(2).asDouble()
            )
            bounds.expand(key_world * objPath.inclusiveMatrix().inverse())
            return bounds
        except Exception:
            return om.MBoundingBox(om.MPoint(-0.25, -0.25, -0.25), om.MPoint(0.25, 0.25, 0.25))

    def disableInternalBoundingBoxDraw(self):
        return True

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData if isinstance(oldData, TangentHandleUserData) else TangentHandleUserData()
        node = om.MFnDependencyNode(objPath.node())
        inverse = objPath.inclusiveMatrix().inverse()
        data.center = om.MPoint()
        try:
            plug = node.findPlug("keyPosition", False)
            key_world = om.MPoint(
                plug.child(0).asDouble(), plug.child(1).asDouble(), plug.child(2).asDouble()
            )
            data.key_point = key_world * inverse
        except Exception:
            data.key_point = data.center
        data.handle_color = _read_color(node, "handleColor", data.handle_color)
        data.line_color = _read_color(node, "lineColor", data.line_color)
        data.screen_radius = max(4.0, _read_float(node, "screenRadius", 9.0))
        data.world_radius = max(0.05, _read_float(node, "worldRadius", 0.15))
        data.draw_line = _read_bool(node, "drawLine", True)
        return data

    @staticmethod
    def draw(context, data):
        pass

    def addUIDrawables(self, objPath, drawManager, frameContext, data):
        if not isinstance(data, TangentHandleUserData):
            return
        drawManager.beginDrawable()
        xray = AnimKeyMotionTrailDrawOverride._begin_xray(drawManager)
        try:
            if data.draw_line:
                line = om.MPointArray()
                line.append(data.key_point)
                line.append(data.center)
                drawManager.setLineWidth(1.8)
                drawManager.setColor(data.line_color)
                drawManager.mesh(omr.MUIDrawManager.kLines, line)
            point = om.MPointArray()
            point.append(data.center)
            drawManager.setPointSize(data.screen_radius)
            drawManager.setColor(data.handle_color)
            drawManager.mesh(omr.MUIDrawManager.kPoints, point)
        finally:
            AnimKeyMotionTrailDrawOverride._end_xray(drawManager, xray)
            drawManager.endDrawable()


def initializePlugin(plugin):
    plugin_fn = om.MFnPlugin(plugin, "AnimKey", "2.0", "Any")
    plugin_fn.registerNode(
        PLUGIN_NODE_NAME,
        PLUGIN_NODE_ID,
        AnimKeyMotionTrailNode.creator,
        AnimKeyMotionTrailNode.initialize,
        om.MPxNode.kLocatorNode,
        PLUGIN_DRAW_CLASSIFICATION,
    )
    omr.MDrawRegistry.registerDrawOverrideCreator(
        PLUGIN_DRAW_CLASSIFICATION,
        PLUGIN_DRAW_REGISTRANT_ID,
        AnimKeyMotionTrailDrawOverride.creator,
    )
    plugin_fn.registerNode(
        TANGENT_HANDLE_NODE_NAME,
        TANGENT_HANDLE_NODE_ID,
        AnimKeyTangentHandleNode.creator,
        AnimKeyTangentHandleNode.initialize,
        om.MPxNode.kLocatorNode,
        TANGENT_HANDLE_DRAW_CLASSIFICATION,
    )
    omr.MDrawRegistry.registerDrawOverrideCreator(
        TANGENT_HANDLE_DRAW_CLASSIFICATION,
        TANGENT_HANDLE_DRAW_REGISTRANT_ID,
        AnimKeyTangentHandleDrawOverride.creator,
    )


def uninitializePlugin(plugin):
    plugin_fn = om.MFnPlugin(plugin)
    omr.MDrawRegistry.deregisterDrawOverrideCreator(
        TANGENT_HANDLE_DRAW_CLASSIFICATION, TANGENT_HANDLE_DRAW_REGISTRANT_ID
    )
    plugin_fn.deregisterNode(TANGENT_HANDLE_NODE_ID)
    omr.MDrawRegistry.deregisterDrawOverrideCreator(
        PLUGIN_DRAW_CLASSIFICATION, PLUGIN_DRAW_REGISTRANT_ID
    )
    plugin_fn.deregisterNode(PLUGIN_NODE_ID)
