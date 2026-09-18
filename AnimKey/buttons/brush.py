import math
import json
import os
import copy
import zipfile
import base64
import shutil
import tempfile
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict

import maya.cmds as cmds
import maya.OpenMaya as om
import maya.OpenMayaUI as omui

BRUSH_CONTAINER = "animkey_brush"
BRUSH_SCENE_DATA_ATTR = "sketchboardData"
FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# ======================================================================
#  COMPATIBILIDAD MULTI-VERSIÓN DE PYSIDE (Maya 2022 a Maya 2025)
# ======================================================================
from AnimKey.mods.maya_compat import (
    PYSIDE_MAJOR as PYSIDE_VER,
    QtCore,
    QtGui,
    QtWidgets,
    execute_qt,
    wrap_instance as wrapInstance,
)
from AnimKey.mods.storage import atomic_write_json

Qt = QtCore.Qt
QPoint = QtCore.QPoint
QPointF = QtCore.QPointF
QRectF = QtCore.QRectF
QTimer = QtCore.QTimer
QPropertyAnimation = QtCore.QPropertyAnimation
QEasingCurve = QtCore.QEasingCurve
QPainter = QtGui.QPainter
QPen = QtGui.QPen
QColor = QtGui.QColor
QPixmap = QtGui.QPixmap
QImage = QtGui.QImage
QLinearGradient = QtGui.QLinearGradient
QPainterPath = QtGui.QPainterPath
QBrush = QtGui.QBrush
QTransform = QtGui.QTransform
QCursor = QtGui.QCursor
QTabletEvent = QtGui.QTabletEvent
QConicalGradient = QtGui.QConicalGradient
QAction = QtGui.QAction if hasattr(QtGui, "QAction") else QtWidgets.QAction
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QPushButton = QtWidgets.QPushButton
QSlider = QtWidgets.QSlider
QLabel = QtWidgets.QLabel
QColorDialog = QtWidgets.QColorDialog
QSizePolicy = QtWidgets.QSizePolicy
QFrame = QtWidgets.QFrame
QCheckBox = QtWidgets.QCheckBox
QComboBox = QtWidgets.QComboBox
QSpinBox = QtWidgets.QSpinBox
QDockWidget = QtWidgets.QDockWidget
QMainWindow = QtWidgets.QMainWindow
QToolBar = QtWidgets.QToolBar
QSplitter = QtWidgets.QSplitter
QScrollArea = QtWidgets.QScrollArea
QGroupBox = QtWidgets.QGroupBox

# Helpers para compatibilidad de eventos
def get_event_pos(event) -> QPointF:
    if hasattr(event, 'position'): return event.position()
    if hasattr(event, 'posF'): return event.posF()
    p = event.pos()
    return QPointF(p.x(), p.y())


def get_event_global_pos(event) -> Optional[QPointF]:
    """Return an event's global position across the Qt versions used by Maya."""
    for method_name in ("globalPosition", "globalPosF", "globalPos"):
        method = getattr(event, method_name, None)
        if not callable(method):
            continue
        try:
            point = method()
            return QPointF(point.x(), point.y())
        except Exception:
            continue
    return None


def get_tablet_tilt_x(event):
    return event.tiltX() if hasattr(event, 'tiltX') else event.xTilt()

def get_tablet_tilt_y(event):
    return event.tiltY() if hasattr(event, 'tiltY') else event.yTilt()


def get_tablet_pressure(event, default: float = 1.0) -> float:
    """Return a stable normalized pressure for Qt 5/6 tablet drivers."""
    try:
        pressure = float(event.pressure())
    except Exception:
        pressure = float(default)
    if not math.isfinite(pressure):
        pressure = float(default)
    # Some Windows tablet drivers briefly report zero on TabletPress. Preserve
    # a light stroke instead of dropping the first sample completely.
    return max(0.02, min(1.0, pressure))


def get_tablet_rotation(event) -> float:
    try:
        return float(event.rotation())
    except Exception:
        return 0.0


def _animkey_package_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _animkey_user_tools_dir():
    try:
        from AnimKey.mods import configMod
        return os.path.join(configMod.get_user_folder_path(), "tools")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "AnimKey_user_data", "tools")


def _subprocess_startupinfo():
    if os.name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    return startupinfo


def _is_valid_ffmpeg(path):
    if not path or not os.path.isfile(path):
        return False
    try:
        subprocess.run(
            [path, "-hide_banner", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=6,
            startupinfo=_subprocess_startupinfo(),
        )
        return True
    except Exception:
        return False


def _ffmpeg_candidate_paths():
    package_dir = _animkey_package_dir()
    user_tools_dir = _animkey_user_tools_dir()
    candidates = []

    for env_key in ("ANIMKEY_FFMPEG", "ANIMKEY_FFMPEG_PATH"):
        env_path = os.environ.get(env_key)
        if env_path:
            candidates.append(env_path)

    candidates.extend([
        os.path.join(package_dir, "bin", "ffmpeg.exe"),
        os.path.join(package_dir, "bin", "ffmpeg", "ffmpeg.exe"),
        os.path.join(package_dir, "bin", "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(package_dir, "data", "bin", "ffmpeg.exe"),
        os.path.join(package_dir, "data", "ffmpeg", "ffmpeg.exe"),
        os.path.join(package_dir, "data", "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(package_dir, "vendor", "ffmpeg", "ffmpeg.exe"),
        os.path.join(package_dir, "vendor", "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(user_tools_dir, "ffmpeg", "ffmpeg.exe"),
        os.path.join(user_tools_dir, "ffmpeg", "bin", "ffmpeg.exe"),
    ])

    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        candidates.append(path_ffmpeg)

    if os.name == "nt":
        candidates.extend([
            r"C:\Program Files\Prism2\Tools\FFmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ShareX\ffmpeg.exe",
            r"C:\Program Files\Natron\bin\ffmpeg.exe",
        ])

    clean = []
    seen = set()
    for path in candidates:
        if not path:
            continue
        norm = os.path.normpath(os.path.expandvars(os.path.expanduser(path)))
        key = norm.lower()
        if key in seen:
            continue
        clean.append(norm)
        seen.add(key)
    return clean


def _download_ffmpeg_to_animkey_data(status_callback=None):
    dest_dir = os.path.join(_animkey_user_tools_dir(), "ffmpeg", "bin")
    dest_path = os.path.join(dest_dir, "ffmpeg.exe")
    if _is_valid_ffmpeg(dest_path):
        return dest_path

    tmp_dir = tempfile.mkdtemp(prefix="animkey_ffmpeg_")
    archive_path = os.path.join(tmp_dir, "ffmpeg.zip")
    try:
        if status_callback:
            status_callback("Installing portable FFmpeg for AnimKey...")
        QtWidgets.QApplication.processEvents()

        import urllib.request
        request = urllib.request.Request(
            FFMPEG_DOWNLOAD_URL,
            headers={"User-Agent": "AnimKey"},
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            with open(archive_path, "wb") as handle:
                shutil.copyfileobj(response, handle)

        with zipfile.ZipFile(archive_path, "r") as zf:
            ffmpeg_member = None
            for member in zf.namelist():
                normalized = member.replace("\\", "/").lower()
                if normalized.endswith("/bin/ffmpeg.exe") or normalized.endswith("ffmpeg.exe"):
                    ffmpeg_member = member
                    break
            if not ffmpeg_member:
                return None

            os.makedirs(dest_dir, exist_ok=True)
            with zf.open(ffmpeg_member) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

        return dest_path if _is_valid_ffmpeg(dest_path) else None
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def resolve_ffmpeg_path(status_callback=None, allow_download=False):
    for candidate in _ffmpeg_candidate_paths():
        if _is_valid_ffmpeg(candidate):
            return candidate
    if allow_download:
        return _download_ffmpeg_to_animkey_data(status_callback=status_callback)
    return None


def _maya_playback_fps():
    try:
        unit = cmds.currentUnit(query=True, time=True)
    except Exception:
        unit = "film"
    unit = (unit or "film").lower()
    fps_by_unit = {
        "game": 15.0,
        "film": 24.0,
        "pal": 25.0,
        "ntsc": 30.0,
        "show": 48.0,
        "palf": 50.0,
        "ntscf": 60.0,
    }
    if unit in fps_by_unit:
        return fps_by_unit[unit]
    if unit.endswith("fps"):
        try:
            return float(unit[:-3])
        except Exception:
            pass
    return 24.0


def _normalize_playblast_output_path(path, selected_filter):
    path = os.path.normpath(path)
    selected_filter = selected_filter or ""
    root, ext = os.path.splitext(path)
    ext = ext.lower()
    wants_video_filter = "video" in selected_filter.lower()
    if wants_video_filter and ext not in (".mp4", ".mov"):
        path = path + ".mp4"
        root, ext = os.path.splitext(path)
    want_video = ext in (".mp4", ".mov")
    out_dir = os.path.dirname(path) or os.getcwd()
    base_name = os.path.splitext(os.path.basename(path))[0] or "sketchboard_playblast"
    return path, want_video, out_dir, base_name


def _short_process_error(error):
    details = ""
    try:
        details = (error.stderr or b"").decode("utf-8", "ignore").strip()
    except Exception:
        details = str(error)
    if len(details) > 280:
        details = details[-280:]
    return details or str(error)


def _file_size(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _copy_verified_video_to_destination(source_path, out_path):
    if _file_size(source_path) <= 0:
        raise RuntimeError("FFmpeg generated an empty video file.")

    out_dir = os.path.dirname(out_path) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    temp_final = os.path.join(
        out_dir,
        f".{os.path.basename(out_path)}.animkey_tmp_{int(time.time() * 1000)}"
    )
    try:
        shutil.copy2(source_path, temp_final)
        if _file_size(temp_final) <= 0:
            raise RuntimeError("Video copy failed: destination temp file is empty.")
        os.replace(temp_final, out_path)
    finally:
        if os.path.exists(temp_final):
            try:
                os.remove(temp_final)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════
#  MÓDULO 1: MATEMÁTICAS DEL PINCEL
# ═══════════════════════════════════════════════════════════════════════
class KalmanFilter1D:
    def __init__(self, process_noise: float = 1e-3, measurement_noise: float = 1e-2):
        self.Q = process_noise
        self.R = measurement_noise
        self.P = 1.0
        self.x = 0.5

    def update(self, measurement: float) -> float:
        self.P = self.P + self.Q
        K = self.P / (self.P + self.R)
        self.x = self.x + K * (measurement - self.x)
        self.P = (1.0 - K) * self.P
        return self.x

    def reset(self, value: float = 0.5):
        self.x = value
        self.P = 1.0


class CatmullRomSpline:
    def __init__(self, alpha: float = 0.5):
        self.alpha = alpha

    def _tj(self, ti: float, pi: tuple, pj: tuple) -> float:
        xi, yi = pi
        xj, yj = pj
        dist = math.sqrt((xj - xi)**2 + (yj - yi)**2)
        return ti + (dist ** self.alpha)

    def _v_add(self, v1: tuple, v2: tuple) -> tuple:
        return (v1[0] + v2[0], v1[1] + v2[1])

    def _v_scale(self, v: tuple, s: float) -> tuple:
        return (v[0] * s, v[1] * s)

    def interpolate_segment(self, p0: tuple, p1: tuple, p2: tuple, p3: tuple, num_points: int = 20) -> List[Tuple[float, float]]:
        t0 = 0.0
        t1 = self._tj(t0, p0, p1)
        t2 = self._tj(t1, p1, p2)
        t3 = self._tj(t2, p2, p3)

        if abs(t2 - t1) < 1e-10:
            return [p1]

        result = []
        for i in range(num_points):
            t = t1 + (t2 - t1) * i / max(1, num_points - 1)
            
            w1 = (t1-t)/(t1-t0) if abs(t1-t0) > 1e-10 else 0
            w2 = (t-t0)/(t1-t0) if abs(t1-t0) > 1e-10 else 1
            A1 = self._v_add(self._v_scale(p0, w1), self._v_scale(p1, w2)) if abs(t1-t0) > 1e-10 else p1

            w1 = (t2-t)/(t2-t1) if abs(t2-t1) > 1e-10 else 0
            w2 = (t-t1)/(t2-t1) if abs(t2-t1) > 1e-10 else 1
            A2 = self._v_add(self._v_scale(p1, w1), self._v_scale(p2, w2)) if abs(t2-t1) > 1e-10 else p2

            w1 = (t3-t)/(t3-t2) if abs(t3-t2) > 1e-10 else 0
            w2 = (t-t2)/(t3-t2) if abs(t3-t2) > 1e-10 else 1
            A3 = self._v_add(self._v_scale(p2, w1), self._v_scale(p3, w2)) if abs(t3-t2) > 1e-10 else p3
            
            w1 = (t2-t)/(t2-t0) if abs(t2-t0) > 1e-10 else 0
            w2 = (t-t0)/(t2-t0) if abs(t2-t0) > 1e-10 else 1
            B1 = self._v_add(self._v_scale(A1, w1), self._v_scale(A2, w2)) if abs(t2-t0) > 1e-10 else A2

            w1 = (t3-t)/(t3-t1) if abs(t3-t1) > 1e-10 else 0
            w2 = (t-t1)/(t3-t1) if abs(t3-t1) > 1e-10 else 1
            B2 = self._v_add(self._v_scale(A2, w1), self._v_scale(A3, w2)) if abs(t3-t1) > 1e-10 else A3
            
            w1 = (t2-t)/(t2-t1) if abs(t2-t1) > 1e-10 else 0
            w2 = (t-t1)/(t2-t1) if abs(t2-t1) > 1e-10 else 1
            C = self._v_add(self._v_scale(B1, w1), self._v_scale(B2, w2)) if abs(t2-t1) > 1e-10 else B1
            
            result.append((float(C[0]), float(C[1])))
        return result

    def smooth_path(self, points: list, num_interp: int = 12) -> list:
        if len(points) < 2: return points
        if len(points) == 2: return points

        result = []
        n = len(points)

        for i in range(n - 1):
            i0, i1, i2, i3 = max(0, i-1), i, min(n-1, i+1), min(n-1, i+2)
            p0 = (points[i0][0], points[i0][1])
            p1 = (points[i1][0], points[i1][1])
            p2 = (points[i2][0], points[i2][1])
            p3 = (points[i3][0], points[i3][1])

            pr1 = points[i1][2] if len(points[i1]) > 2 else 1.0
            pr2 = points[i2][2] if len(points[i2]) > 2 else 1.0

            distance = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            samples = max(2, min(num_interp, int(distance / 2.0) + 2))
            seg = self.interpolate_segment(p0, p1, p2, p3, num_points=samples)
            for j, (x, y) in enumerate(seg):
                if result and j == 0:
                    continue
                t = j / max(len(seg) - 1, 1)
                result.append((x, y, pr1 + (pr2 - pr1) * t))
        return result


class BrushPhysics:
    def __init__(self):
        self.pos = QPointF(0, 0)
        self.vel = QPointF(0, 0)
        self.stiffness = 0.35
        self.damping = 0.72
        self.dt = 1.0 / 60.0

    def update(self, target: QPointF) -> QPointF:
        ax = (target.x() - self.pos.x()) * self.stiffness
        ay = (target.y() - self.pos.y()) * self.stiffness
        self.vel = QPointF((self.vel.x() + ax)*self.damping, (self.vel.y() + ay)*self.damping)
        self.pos = QPointF(self.pos.x() + self.vel.x(), self.pos.y() + self.vel.y())
        return QPointF(self.pos)

    def reset(self, pos: QPointF):
        self.pos = QPointF(pos)
        self.vel = QPointF(0, 0)

    def speed(self) -> float:
        return math.sqrt(self.vel.x()**2 + self.vel.y()**2)


def compute_stroke_width(base_size: float, pressure: float, speed: float,
                          tilt: float = 0.0, velocity_sensitivity: float = 0.08) -> float:
    # Stored stroke pressure is already a calibrated 0..1 brush response.
    # Applying a second exponent here made tablet input feel overly thin.
    pressure_factor = max(0.08, min(1.0, float(pressure)))
    velocity_factor = 1.0 / (1.0 + speed * velocity_sensitivity)
    tilt_factor = max(0.3, math.cos(math.radians(tilt * 0.5)))
    return max(0.5, base_size * pressure_factor * velocity_factor * tilt_factor)

# ═══════════════════════════════════════════════════════════════════════
#  MÓDULO 2: DATOS DEL TRAZO Y KEYFRAMES
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class StrokePoint:
    x: float
    y: float
    pressure: float = 1.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0
    rotation: float = 0.0
    wx: Optional[float] = None
    wy: Optional[float] = None
    wz: Optional[float] = None

@dataclass
class Stroke:
    points: List[StrokePoint] = field(default_factory=list)
    color: tuple = (255, 255, 255, 255)
    base_size: float = 8.0
    draw_zoom: float = 1.0
    tool: str = "brush"
    opacity: float = 0.85
    hardness: float = 0.7
    layer: int = 0
    smoothed_path: list = field(default_factory=list)
    _cache_size: tuple = field(default_factory=lambda: (0, 0))

    def compute_smoothed(self, spline: CatmullRomSpline, w: float, h: float, ref_w: float = 0, ref_h: float = 0):
        cache_key = (w, h, ref_w, ref_h)
        if self.smoothed_path and self._cache_size == cache_key:
            return

        if ref_w > 0 and ref_h > 0:
            s = min(w / max(1, ref_w), h / max(1, ref_h))
            ox = (w - ref_w * s) / 2.0
            oy = (h - ref_h * s) / 2.0
            raw = [(p.x * ref_w * s + ox, p.y * ref_h * s + oy, p.pressure) for p in self.points]
        else:
            raw = [(p.x * w, p.y * h, p.pressure) for p in self.points]
            
        self.smoothed_path = spline.smooth_path(raw, num_interp=6)
        self._cache_size = cache_key

@dataclass
class FrameData:
    frame: int
    strokes: List[Stroke] = field(default_factory=list)

class SketchboardData:
    def __init__(self):
        self.frames: Dict[int, FrameData] = {}
        self.canvas_width = 1920
        self.canvas_height = 1080

    def get_or_create_frame(self, frame_num: int) -> FrameData:
        if frame_num not in self.frames:
            self.frames[frame_num] = FrameData(frame=frame_num)
        return self.frames[frame_num]

    def has_frame(self, frame_num: int) -> bool:
        return frame_num in self.frames and len(self.frames[frame_num].strokes) > 0

    def get_frame_numbers(self) -> List[int]:
        return sorted(k for k, v in self.frames.items() if v.strokes)

    def clear_frame(self, frame_num: int):
        if frame_num in self.frames:
            self.frames[frame_num] = FrameData(frame=frame_num)

    def clear_all(self):
        self.frames.clear()

    def to_dict(self) -> dict:
        result = {
            "version": 2,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "frames": {},
        }
        for fn, fd in self.frames.items():
            if not fd.strokes:
                continue
            strokes_data = []
            for s in fd.strokes:
                strokes_data.append({
                    "color": list(s.color),
                    "base_size": s.base_size,
                    "draw_zoom": s.draw_zoom,
                    "tool": s.tool,
                    "opacity": s.opacity,
                    "hardness": s.hardness,
                    "layer": s.layer,
                    "points": [
                        self._point_to_dict(p)
                        for p in s.points
                    ],
                })
            result["frames"][str(fn)] = strokes_data
        return result

    def _point_to_dict(self, point):
        data = {
            "nx": round(point.x, 6),
            "ny": round(point.y, 6),
            "pressure": round(point.pressure, 4),
            "tilt_x": round(point.tilt_x, 4),
            "tilt_y": round(point.tilt_y, 4),
            "rotation": round(point.rotation, 4),
        }
        if point.wx is not None and point.wy is not None and point.wz is not None:
            data["wx"] = round(point.wx, 6)
            data["wy"] = round(point.wy, 6)
            data["wz"] = round(point.wz, 6)
        return data

    def from_dict(self, data: dict, current_w: int, current_h: int):
        self.canvas_width = data.get("canvas_width", 1920)
        self.canvas_height = data.get("canvas_height", 1080)
        spline = CatmullRomSpline()
        for fn_str, frame_entry in data.get("frames", {}).items():
            fn = int(fn_str)
            fd = FrameData(frame=fn)

            # Compatibilidad: v1 guardaba lista, v2 guarda dict con "strokes"
            if isinstance(frame_entry, list):
                strokes_list = frame_entry
            else:
                strokes_list = frame_entry.get("strokes", [])

            for sd in strokes_list:
                # Asegurar compatibilidad para base_size (viejos json exportaban "size" o "brush_size")
                bs = sd.get("base_size", sd.get("size", sd.get("brush_size", 8.0)))
                s = Stroke(
                    color=tuple(sd.get("color", [255,255,255,255])),
                    base_size=bs,
                    draw_zoom=sd.get("draw_zoom", 1.0),
                    tool=sd.get("tool", "brush"),
                    opacity=sd.get("opacity", 0.85),
                    hardness=sd.get("hardness", 0.7),
                    layer=sd.get("layer", 0),
                )
                for pd in sd.get("points", []):
                    nx = pd.get("nx", pd.get("x", 0) / float(max(1, self.canvas_width)))
                    ny = pd.get("ny", pd.get("y", 0) / float(max(1, self.canvas_height)))
                    # Compatibilidad para pressure (antiguos JSON podrían haber usado "p" o "pressure")
                    pr = pd.get("pressure", pd.get("p", 1.0))
                    s.points.append(StrokePoint(
                        x=nx, y=ny,
                        pressure=pr,
                        tilt_x=pd.get("tilt_x", 0.0),
                        tilt_y=pd.get("tilt_y", 0.0),
                        rotation=pd.get("rotation", 0.0),
                        wx=pd.get("wx"),
                        wy=pd.get("wy"),
                        wz=pd.get("wz"),
                    ))
                s.compute_smoothed(spline, current_w, current_h, self.canvas_width, self.canvas_height)
                fd.strokes.append(s)

            self.frames[fn] = fd


# ═══════════════════════════════════════════════════════════════════════
#  MÓDULO 3: CANVAS DE DIBUJO
# ═══════════════════════════════════════════════════════════════════════

def _get_brush_container():
    from AnimKey.mods import nodeMod
    return nodeMod.create_animkey_child_container(BRUSH_CONTAINER)


def save_brush_data_to_scene(data):
    try:
        node = _get_brush_container()
        if not cmds.attributeQuery(BRUSH_SCENE_DATA_ATTR, node=node, exists=True):
            cmds.addAttr(node, ln=BRUSH_SCENE_DATA_ATTR, dt="string")
        payload = json.dumps(data.to_dict(), ensure_ascii=False, separators=(',', ':'))
        cmds.setAttr(f"{node}.{BRUSH_SCENE_DATA_ATTR}", payload, type="string")
        return True
    except Exception as e:
        try:
            cmds.warning(f"AnimKey Brush: could not save drawings to scene: {e}")
        except Exception:
            pass
        return False


def load_brush_data_from_scene(current_w, current_h):
    try:
        if not cmds.objExists(BRUSH_CONTAINER):
            return None
        if not cmds.attributeQuery(BRUSH_SCENE_DATA_ATTR, node=BRUSH_CONTAINER, exists=True):
            return None
        payload = cmds.getAttr(f"{BRUSH_CONTAINER}.{BRUSH_SCENE_DATA_ATTR}")
        if not payload:
            return None
        data = SketchboardData()
        data.from_dict(json.loads(payload), current_w, current_h)
        return data
    except Exception as e:
        try:
            cmds.warning(f"AnimKey Brush: could not load drawings from scene: {e}")
        except Exception:
            pass
        return None


class DrawingCanvas(QWidget):
    stroke_added = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        if hasattr(Qt, "WA_TabletTracking"):
            self.setAttribute(Qt.WA_TabletTracking, True)
        self.setMouseTracking(True)

        self.tool = "brush"
        self.brush_color = QColor(255, 255, 255, 220)
        self.brush_size = 10.0
        self.brush_opacity = 0.85
        self.brush_hardness = 0.72
        self.velocity_sensitivity = 0.0

        self.spline = CatmullRomSpline(alpha=0.5)
        self.kalman = KalmanFilter1D(process_noise=5e-4, measurement_noise=0.02)
        self.physics = BrushPhysics()

        self.drawing = False
        self.current_points: List[StrokePoint] = []
        self.last_draw_pos: Optional[QPointF] = None
        self.last_cursor_pos: Optional[QPointF] = None
        self.last_pressure: float = 1.0
        self.has_tablet = False
        self._tablet_stroke_active = False
        self._last_tablet_pressure = 1.0
        self._last_tablet_event_time = 0.0
        self._last_input_pos: Optional[QPointF] = None
        self._drawing_view_transform = (1.0, 1.0, 0.0, 0.0)
        self._drawing_uniform_transform = None
        self._drawing_pixel_scale = 1.0
        self._stroke_base_pixmap = None

        self.transform_active = False
        self.selected_strokes = []
        self._transform_drag_mode = None
        self._transform_drag_start = None
        self._transform_start_rect = None
        self._transform_start_points = {}
        self._transform_start_stroke_sizes = {}
        self._transform_handles = {}
        self._transform_current_quad = None
        self._last_transform_undo = None
        self._lattice_handle_limit = 90

        self.lasso_active = False
        self._lasso_points = []
        self._lasso_additive = False
        self._lasso_subtractive = False

        self.onion_before, self.onion_after, self.onion_opacity = 2, 1, 0.35
        self.onion_decay = 0.4   # fracción de opacidad que se resta por cada frame de distancia
        self.onion_enabled = True

        # El canvas arranca en un tamaño mínimo, se ajustará en resizeEvent
        self.canvas_pixmap = QPixmap(2, 2)
        self.canvas_pixmap.fill(Qt.transparent)
        self.overlay_pixmap = QPixmap(2, 2)
        self.overlay_pixmap.fill(Qt.transparent)

        self.data = SketchboardData()
        self.current_frame = 1

        self.physics_timer = QTimer(self)
        self.physics_timer.setInterval(16)
        self.physics_timer.timeout.connect(self._physics_tick)

        self._onion_cache: Dict[int, QPixmap] = {}
        self._cache_dirty = True
        self._frame_render_cache = {}
        self._frame_render_cache_order = deque()
        self._frame_render_cache_limit = 12
        self._last_size = (0, 0)
        self._last_view_token = None
        self.model_panel = None
        self._drawing_view_zoom = 1.0
        self._playback_mode = False

        self.setMinimumSize(50, 50)

    def _ensure_pixmap_size(self):
        w, h = self.width(), self.height()
        if w < 2 or h < 2:
            return
        if (w, h) == self._last_size:
            return
        self._last_size = (w, h)
        self._invalidate_frame_render_cache()
        self.canvas_pixmap = QPixmap(w, h)
        self.canvas_pixmap.fill(Qt.transparent)
        self.overlay_pixmap = QPixmap(w, h)
        self.overlay_pixmap.fill(Qt.transparent)
        self._rebuild_current_frame()

    def _rebuild_current_frame(self):
        """Re-renderiza todos los trazos del frame actual desde coordenadas normalizadas."""
        cache_key = (
            int(self.current_frame), int(self.width()), int(self.height()),
            self._last_view_token,
        )
        if self._playback_mode:
            cached = self._frame_render_cache.get(cache_key)
            if cached is not None:
                self.canvas_pixmap = QPixmap(cached)
                self._cache_dirty = True
                return
        self.canvas_pixmap.fill(Qt.transparent)
        fd = self.data.frames.get(self.current_frame)
        if fd and fd.strokes:
            self._render_frame_to_pixmap(fd, self.canvas_pixmap, 1.0)
        if self._playback_mode:
            self._frame_render_cache[cache_key] = QPixmap(self.canvas_pixmap)
            self._frame_render_cache_order.append(cache_key)
            while len(self._frame_render_cache_order) > self._frame_render_cache_limit:
                stale = self._frame_render_cache_order.popleft()
                self._frame_render_cache.pop(stale, None)
        self._cache_dirty = True

    def _invalidate_frame_render_cache(self, frame=None):
        if frame is None:
            self._frame_render_cache.clear()
            self._frame_render_cache_order.clear()
            return
        frame = int(frame)
        stale_keys = [key for key in self._frame_render_cache if key[0] == frame]
        for key in stale_keys:
            self._frame_render_cache.pop(key, None)
        if stale_keys:
            stale_set = set(stale_keys)
            self._frame_render_cache_order = deque(
                key for key in self._frame_render_cache_order if key not in stale_set
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._ensure_pixmap_size()
        self.update()

    def wheelEvent(self, event):
        event.ignore()

    def _is_transform_tool(self):
        return self.tool in ("transform", "free_transform")

    def _is_free_transform_tool(self):
        return self.tool == "free_transform"

    def tabletEvent(self, event: QTabletEvent):
        if self._handle_tablet_event(event, get_event_pos(event)):
            event.accept()
        else:
            event.ignore()

    def handle_external_tablet_event(self, event, global_pos: QPointF) -> bool:
        """Handle a tablet event received by Maya instead of the overlay canvas.

        In Maya's viewport overlay mode, a number of Windows tablet drivers
        target the modelPanel widget even though the canvas is visually above
        it.  Converting the global tablet location here keeps pressure samples
        on the same path as native canvas events.
        """
        if global_pos is None:
            return False
        local_pos = self.mapFromGlobal(QPoint(
            int(round(global_pos.x())), int(round(global_pos.y()))
        ))
        if not self.rect().contains(local_pos):
            return False
        return self._handle_tablet_event(event, QPointF(local_pos))

    def _handle_tablet_event(self, event, epos: QPointF) -> bool:
        self.has_tablet = True
        raw_pressure = get_tablet_pressure(event)
        self._last_tablet_pressure = raw_pressure
        self._last_tablet_event_time = time.monotonic()
        tilt_x, tilt_y, rotation = get_tablet_tilt_x(event), get_tablet_tilt_y(event), get_tablet_rotation(event)

        is_pan_modifier = (event.modifiers() & Qt.AltModifier) != 0

        ev_type = int(event.type())
        if ev_type == int(QtCore.QEvent.TabletPress):
            if self.tool == "lasso":
                self._lasso_mouse_press(epos, event.button(), event.modifiers())
                return True
            if self._is_transform_tool():
                self._transform_mouse_press(epos, event.button(), event.modifiers())
                return True
            if not is_pan_modifier and event.button() != Qt.MiddleButton:
                self._tablet_stroke_active = True
                self._begin_stroke(epos, raw_pressure, tilt_x, tilt_y, rotation)
                return True
            return False
        elif ev_type == int(QtCore.QEvent.TabletMove):
            self.last_cursor_pos = epos
            if self.tool == "lasso":
                if self._transform_drag_mode:
                    self._transform_mouse_move(epos, event.modifiers())
                else:
                    self._lasso_mouse_move(epos)
                return True
            if self._is_transform_tool():
                self._transform_mouse_move(epos, event.modifiers())
                return True
            if self.drawing:
                self._add_point(epos, raw_pressure, tilt_x, tilt_y, rotation)
            else:
                self.last_draw_pos = epos
                self.update()
            return True
        elif ev_type == int(QtCore.QEvent.TabletRelease):
            if self.tool == "lasso":
                if self._transform_drag_mode:
                    self._transform_mouse_release()
                else:
                    self._lasso_mouse_release()
                self._tablet_stroke_active = False
                return True
            if self._is_transform_tool():
                self._transform_mouse_release()
                self._tablet_stroke_active = False
                return True
            if self.drawing and self.current_points:
                # Qt reports the final lift pressure on TabletRelease.  Keep
                # it instead of ending on the last full-pressure move sample.
                # This is what gives the clean outline its natural thin tail.
                release_pressure = self._persistent_pressure(raw_pressure)
                nx, ny = self._to_normalized(
                    epos.x(), epos.y(), self._drawing_view_transform,
                    self._drawing_uniform_transform,
                )
                last = self.current_points[-1]
                last_px, last_py = self._point_to_pixel(
                    last, self._drawing_view_transform,
                    self._drawing_uniform_transform,
                )
                if math.hypot(epos.x() - last_px, epos.y() - last_py) > 0.35:
                    self.current_points.append(StrokePoint(
                        x=nx, y=ny, pressure=release_pressure,
                        tilt_x=tilt_x, tilt_y=tilt_y, rotation=rotation,
                    ))
                else:
                    last.pressure = min(last.pressure, release_pressure)
            self._end_stroke()
            self._tablet_stroke_active = False
            return True
        return False

    def mousePressEvent(self, event):
        epos = get_event_pos(event)
        if self.tool == "lasso" and event.button() == Qt.LeftButton:
            self._lasso_mouse_press(epos, event.button(), event.modifiers())
            event.accept()
            return
        if self._is_transform_tool() and event.button() == Qt.LeftButton:
            self._transform_mouse_press(epos, event.button(), event.modifiers())
            event.accept()
            return
        if not self._tablet_stroke_active and event.button() == Qt.LeftButton:
            self._begin_stroke(epos, 1.0)
            event.accept()

    def mouseMoveEvent(self, event):
        epos = get_event_pos(event)
        self.last_cursor_pos = epos
        if self.tool == "lasso":
            if self._transform_drag_mode:
                self._transform_mouse_move(epos, event.modifiers())
            else:
                self._lasso_mouse_move(epos)
            event.accept()
            return
        if self._is_transform_tool():
            self._transform_mouse_move(epos, event.modifiers())
            event.accept()
            return
        if not self._tablet_stroke_active and self.drawing:
            self._add_point(epos, 1.0)
            event.accept()
        elif self._tablet_stroke_active and self.drawing:
            # Some Windows drivers send tablet pressure to the viewport but
            # report movement to the overlay as mouse events.  Keep those
            # positions while reusing the most recent real tablet pressure.
            if time.monotonic() - self._last_tablet_event_time > 0.02:
                self._add_point(epos, self._last_tablet_pressure)
                event.accept()
                return
            self.last_draw_pos = epos
            self.update()
        else:
            self.last_draw_pos = epos
            self.update()

    def mouseReleaseEvent(self, event):
        if self.tool == "lasso" and event.button() == Qt.LeftButton:
            if self._transform_drag_mode:
                self._transform_mouse_release()
            else:
                self._lasso_mouse_release()
            event.accept()
            return
        if self._is_transform_tool() and event.button() == Qt.LeftButton:
            self._transform_mouse_release()
            event.accept()
            return
        if not self._tablet_stroke_active and event.button() == Qt.LeftButton:
            self._end_stroke()
            event.accept()

    def _uniform_transform(self):
        """Devuelve (scale, offset_x, offset_y, ref_w, ref_h) para escalado uniforme."""
        rw = float(self.data.canvas_width or self.width())
        rh = float(self.data.canvas_height or self.height())
        cw, ch = float(self.width()), float(self.height())
        s = min(cw / max(1, rw), ch / max(1, rh))
        ox = (cw - rw * s) / 2.0
        oy = (ch - rh * s) / 2.0
        return s, ox, oy, rw, rh

    def _viewport_brush_scale(self, target_width=None, target_height=None):
        """Return an isotropic scale while points follow both viewport axes.

        Stroke points are normalized to the complete viewport, not to a
        letterboxed reference rectangle.  Brush width still needs one value,
        so the geometric mean provides a stable proportional response when
        only one viewport dimension changes.
        """
        rw = float(self.data.canvas_width or self.width() or 1.0)
        rh = float(self.data.canvas_height or self.height() or 1.0)
        cw = float(target_width if target_width is not None else self.width())
        ch = float(target_height if target_height is not None else self.height())
        sx = cw / max(1.0, rw)
        sy = ch / max(1.0, rh)
        return max(0.001, math.sqrt(abs(sx * sy)))

    def _camera_shape_from_panel(self):
        panel = getattr(self, "model_panel", None) or get_active_model_panel()
        if not panel:
            return None
        try:
            cam = cmds.modelPanel(panel, query=True, camera=True)
        except Exception:
            return None
        if not cam or not cmds.objExists(cam):
            return None
        try:
            if cmds.nodeType(cam) == "camera":
                return cam
            shapes = cmds.listRelatives(cam, shapes=True, type="camera", fullPath=True) or []
            if shapes:
                return shapes[0]
        except Exception:
            pass
        return None

    def _camera_attr(self, camera_shape, attr, default):
        try:
            plug = f"{camera_shape}.{attr}"
            if cmds.objExists(plug):
                value = cmds.getAttr(plug)
                if isinstance(value, (list, tuple)) and value:
                    value = value[0]
                return value
        except Exception:
            pass
        return default

    def _m3d_view(self):
        panel = getattr(self, "model_panel", None) or get_active_model_panel()
        if not panel:
            return None
        try:
            view = omui.M3dView()
            omui.M3dView.getM3dViewFromModelEditor(panel, view)
            return view
        except Exception:
            return None

    def _camera_fn(self):
        camera_shape = self._camera_shape_from_panel()
        if not camera_shape:
            return None
        try:
            selection = om.MSelectionList()
            selection.add(camera_shape)
            dag_path = om.MDagPath()
            selection.getDagPath(0, dag_path)
            return om.MFnCamera(dag_path)
        except Exception:
            return None

    def _api_view_size(self, view=None):
        try:
            width = float(view.portWidth()) if view is not None else float(self.width())
            height = float(view.portHeight()) if view is not None else float(self.height())
        except Exception:
            width = float(self.width())
            height = float(self.height())
        return max(1.0, width), max(1.0, height)

    def _canvas_to_api_pixel(self, x, y, view=None):
        view_w, view_h = self._api_view_size(view)
        canvas_w = max(1.0, float(self.width()))
        canvas_h = max(1.0, float(self.height()))
        api_x = int(round(float(x) * view_w / canvas_w))
        api_y = int(round((canvas_h - 1.0 - float(y)) * view_h / canvas_h))
        return max(0, api_x), max(0, api_y)

    def _api_to_canvas_pixel(self, x, y, view=None):
        view_w, view_h = self._api_view_size(view)
        canvas_w = max(1.0, float(self.width()))
        canvas_h = max(1.0, float(self.height()))
        canvas_x = float(x) * canvas_w / view_w
        canvas_y = canvas_h - 1.0 - (float(y) * canvas_h / view_h)
        return canvas_x, canvas_y

    def _view_point_to_world(self, px, py):
        view = self._m3d_view()
        if view is None:
            return None
        try:
            near_point = om.MPoint()
            far_point = om.MPoint()
            api_x, api_y = self._canvas_to_api_pixel(px, py, view)
            view.viewToWorld(api_x, api_y, near_point, far_point)
            return float(near_point.x), float(near_point.y), float(near_point.z)
        except Exception:
            return None

    def _world_to_view_pixel(self, wx, wy, wz):
        view = self._m3d_view()
        if view is None:
            return None
        try:
            point = om.MPoint(float(wx), float(wy), float(wz))
            x_util = om.MScriptUtil()
            y_util = om.MScriptUtil()
            x_ptr = x_util.asShortPtr()
            y_ptr = y_util.asShortPtr()
            visible = view.worldToView(point, x_ptr, y_ptr)
            try:
                x = float(om.MScriptUtil.getShort(x_ptr))
                y_api = float(om.MScriptUtil.getShort(y_ptr))
            except Exception:
                x = float(x_util.getShort(x_ptr))
                y_api = float(y_util.getShort(y_ptr))
            canvas_x, canvas_y = self._api_to_canvas_pixel(x, y_api, view)
            return canvas_x, canvas_y, bool(visible)
        except Exception:
            return None

    def _view_frustum(self, apply_pan_zoom):
        camera_fn = self._camera_fn()
        if camera_fn is None:
            return None

        def _double_ptr():
            util = om.MScriptUtil()
            util.createFromDouble(0.0)
            return util, util.asDoublePtr()

        left_util, left_ptr = _double_ptr()
        right_util, right_ptr = _double_ptr()
        bottom_util, bottom_ptr = _double_ptr()
        top_util, top_ptr = _double_ptr()
        _keep_alive = (left_util, right_util, bottom_util, top_util)

        view = self._m3d_view()
        view_w, view_h = self._api_view_size(view)
        aspect = view_w / max(1.0, view_h)

        try:
            camera_fn.getViewingFrustum(
                aspect,
                left_ptr, right_ptr, bottom_ptr, top_ptr,
                True, True, bool(apply_pan_zoom)
            )
        except Exception:
            try:
                camera_fn.getViewingFrustum(
                    aspect,
                    left_ptr, right_ptr, bottom_ptr, top_ptr,
                    False, False, bool(apply_pan_zoom)
                )
            except Exception:
                return None

        try:
            return (
                float(om.MScriptUtil.getDouble(left_ptr)),
                float(om.MScriptUtil.getDouble(right_ptr)),
                float(om.MScriptUtil.getDouble(bottom_ptr)),
                float(om.MScriptUtil.getDouble(top_ptr)),
            )
        except Exception:
            return None

    def _current_view_state(self):
        camera_shape = self._camera_shape_from_panel()
        if not camera_shape:
            return {
                "camera": "",
                "enabled": False,
                "zoom": 1.0,
                "hpan": 0.0,
                "vpan": 0.0,
                "hfa": 1.417,
                "vfa": 0.945,
            }

        enabled = bool(self._camera_attr(camera_shape, "panZoomEnabled", False))
        zoom = float(self._camera_attr(camera_shape, "zoom", 1.0) or 1.0)
        hpan = float(self._camera_attr(camera_shape, "horizontalPan", 0.0) or 0.0)
        vpan = float(self._camera_attr(camera_shape, "verticalPan", 0.0) or 0.0)
        hfa = float(self._camera_attr(camera_shape, "horizontalFilmAperture", 1.417) or 1.417)
        vfa = float(self._camera_attr(camera_shape, "verticalFilmAperture", 0.945) or 0.945)

        if not enabled:
            zoom, hpan, vpan = 1.0, 0.0, 0.0

        return {
            "camera": camera_shape,
            "enabled": enabled,
            "zoom": max(0.001, zoom),
            "hpan": hpan,
            "vpan": vpan,
            "hfa": max(0.001, hfa),
            "vfa": max(0.001, vfa),
        }

    def _current_view_token(self):
        state = self._current_view_state()
        return (
            state["camera"],
            bool(state["enabled"]),
            round(float(state["zoom"]), 6),
            round(float(state["hpan"]), 6),
            round(float(state["vpan"]), 6),
            round(float(state["hfa"]), 6),
            round(float(state["vfa"]), 6),
        )

    def _view_panzoom_transform(self, target_width=None, target_height=None):
        state = self._current_view_state()
        zoom = max(0.001, float(state["zoom"]))
        if not state["enabled"]:
            return 1.0, 1.0, 0.0, 0.0

        base_frustum = self._view_frustum(False)
        panzoom_frustum = self._view_frustum(True)
        if base_frustum and panzoom_frustum:
            left_0, right_0, bottom_0, top_0 = base_frustum
            left_1, right_1, bottom_1, top_1 = panzoom_frustum
            span_x_0 = right_0 - left_0
            span_x_1 = right_1 - left_1
            span_y_0 = top_0 - bottom_0
            span_y_1 = top_1 - bottom_1
            if abs(span_x_1) > 1e-8 and abs(span_y_1) > 1e-8:
                w = float(target_width if target_width is not None else self.width())
                h = float(target_height if target_height is not None else self.height())
                zoom_x = span_x_0 / span_x_1
                zoom_y = span_y_0 / span_y_1
                offset_x = ((left_0 - left_1) / span_x_1) * w
                offset_y = ((top_1 - top_0) / span_y_1) * h
                return zoom_x, zoom_y, offset_x, offset_y

        width = float(target_width if target_width is not None else self.width())
        height = float(target_height if target_height is not None else self.height())
        pan_x = (float(state["hpan"]) / float(state["hfa"])) * width
        pan_y = -(float(state["vpan"]) / float(state["vfa"])) * height
        cx = width * 0.5
        cy = height * 0.5
        return zoom, zoom, cx * (1.0 - zoom) + pan_x, cy * (1.0 - zoom) + pan_y

    def _apply_view_panzoom(self, px, py, view_transform=None):
        zoom_x, zoom_y, offset_x, offset_y = (
            view_transform or self._view_panzoom_transform()
        )
        if (
            abs(zoom_x - 1.0) <= 1e-8 and
            abs(zoom_y - 1.0) <= 1e-8 and
            abs(offset_x) <= 1e-8 and
            abs(offset_y) <= 1e-8
        ):
            return px, py
        return px * zoom_x + offset_x, py * zoom_y + offset_y

    def _remove_view_panzoom(self, px, py, view_transform=None):
        zoom_x, zoom_y, offset_x, offset_y = (
            view_transform or self._view_panzoom_transform()
        )
        if (
            abs(zoom_x - 1.0) <= 1e-8 and
            abs(zoom_y - 1.0) <= 1e-8 and
            abs(offset_x) <= 1e-8 and
            abs(offset_y) <= 1e-8
        ):
            return px, py
        return (
            (px - offset_x) / max(0.001, zoom_x),
            (py - offset_y) / max(0.001, zoom_y),
        )

    def _current_panzoom_zoom(self, target_width=None, target_height=None):
        zoom_x, zoom_y, _offset_x, _offset_y = self._view_panzoom_transform(target_width, target_height)
        return max(0.001, math.sqrt(abs(float(zoom_x) * float(zoom_y))))

    def _effective_pixel_scale(self, stroke=None, draw_zoom=None, base_scale=None, target_width=None, target_height=None):
        origin_zoom = draw_zoom
        if origin_zoom is None and stroke is not None:
            origin_zoom = getattr(stroke, "draw_zoom", 1.0)
        origin_zoom = max(0.001, float(origin_zoom or 1.0))
        scale = (
            self._viewport_brush_scale(target_width, target_height)
            if base_scale is None else float(base_scale)
        )
        return scale * (self._current_panzoom_zoom(target_width, target_height) / origin_zoom)

    def _ensure_view_render_current(self):
        # Camera/pan-zoom state is frozen for the duration of a stroke. Querying
        # Maya here for every tablet sample was the largest source of input lag.
        if self.drawing:
            return
        token = self._current_view_token()
        if token == self._last_view_token:
            return
        self._last_view_token = token
        self._invalidate_frame_render_cache()
        fd = self.data.frames.get(self.current_frame)
        if fd:
            for stroke in fd.strokes:
                self._invalidate_stroke(stroke)
        self._onion_cache.clear()
        self._rebuild_current_frame()

    def _to_normalized(self, px, py, view_transform=None,
                       uniform_transform=None):
        """Pixel del viewport → coordenadas normalizadas de pantalla."""
        px, py = self._remove_view_panzoom(px, py, view_transform)
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        nx = px / width
        ny = py / height
        return nx, ny

    def _to_pixel(self, nx, ny, view_transform=None,
                  uniform_transform=None):
        """Coordenadas normalizadas de pantalla → pixel del viewport."""
        px = nx * max(1.0, float(self.width()))
        py = ny * max(1.0, float(self.height()))
        return self._apply_view_panzoom(px, py, view_transform)

    def _point_has_world(self, point):
        return (
            point is not None and
            point.wx is not None and
            point.wy is not None and
            point.wz is not None
        )

    def _point_to_pixel(self, point, view_transform=None,
                        uniform_transform=None):
        return self._to_pixel(
            point.x, point.y, view_transform, uniform_transform
        )

    def _set_point_from_screen(self, point, px, py):
        point.x, point.y = self._to_normalized(px, py)
        point.wx = point.wy = point.wz = None

    def _start_tuple_to_pixel(self, start):
        if len(start) >= 7:
            return start[5], start[6]
        return self._to_pixel(start[0], start[1])

    def _stroke_smoothed_path(self, stroke):
        if not stroke:
            return []
        _s, _ox, _oy, rw, rh = self._uniform_transform()
        view_token = self._last_view_token
        if view_token is None:
            view_token = self._current_view_token()
        cache_key = (self.width(), self.height(), rw, rh, view_token)
        if stroke.smoothed_path and stroke._cache_size == cache_key:
            return stroke.smoothed_path
        raw = []
        for point in stroke.points:
            px, py = self._point_to_pixel(point)
            raw.append((px, py, point.pressure))
        # Six samples per input span are enough after tablet-point filtering.
        # The old value (14) multiplied the amount of geometry rendered during
        # drawing and playback without producing a visible improvement.
        stroke.smoothed_path = self.spline.smooth_path(raw, num_interp=6)
        stroke._cache_size = cache_key
        return stroke.smoothed_path

    def _persistent_pressure(self, pressure, speed=0.0, tilt=0.0):
        pressure = max(0.0, min(1.0, float(pressure)))
        # Preserve the tablet's useful low-pressure range.  The previous
        # 0.16 floor plus a strong exponent made almost every sample look the
        # same and prevented a natural thin start/end.
        return max(0.025, pressure ** 0.82)

    def _clean_stroke_outline(self, points, base_size):
        """Build one solid variable-width outline for a clean vector stroke.

        Every stored sample contributes its pressure to the final width.  A
        single filled outline keeps that variable line clean without the
        opacity buildup produced by repeated brush stamps.
        """
        if not points:
            return QPainterPath()

        filtered = []
        for point in points:
            item = (float(point[0]), float(point[1]), float(point[2]))
            if not filtered or math.hypot(
                item[0] - filtered[-1][0], item[1] - filtered[-1][1]
            ) >= 0.20:
                filtered.append(item)
            else:
                filtered[-1] = item

        if len(filtered) == 1:
            radius = max(
                0.25,
                float(base_size) * max(0.05, min(1.0, filtered[0][2])) * 0.5,
            )
            path = QPainterPath()
            path.setFillRule(Qt.WindingFill)
            path.addEllipse(QPointF(filtered[0][0], filtered[0][1]), radius, radius)
            return path

        left = []
        right = []
        for index, (x, y, pressure) in enumerate(filtered):
            previous = filtered[max(0, index - 1)]
            following = filtered[min(len(filtered) - 1, index + 1)]
            tangent_x = following[0] - previous[0]
            tangent_y = following[1] - previous[1]
            tangent_length = math.hypot(tangent_x, tangent_y)
            if tangent_length <= 1e-8:
                normal_x, normal_y = 0.0, 1.0
            else:
                normal_x = -tangent_y / tangent_length
                normal_y = tangent_x / tangent_length

            pressure_factor = max(0.04, min(1.0, pressure))
            half_width = max(
                0.25, float(base_size) * pressure_factor * 0.5
            )
            left.append(QPointF(x + normal_x * half_width, y + normal_y * half_width))
            right.append(QPointF(x - normal_x * half_width, y - normal_y * half_width))

        path = QPainterPath(left[0])
        # A pressure outline can cross itself on loops, quick direction
        # changes and ordinary line intersections.  QPainterPath defaults to
        # OddEvenFill, which turns the overlap into a transparent hole.  The
        # winding rule treats the outline as one solid stroke, so opacity is
        # applied once and remains uniform through every intersection.
        path.setFillRule(Qt.WindingFill)
        for point in left[1:]:
            path.lineTo(point)
        for point in reversed(right):
            path.lineTo(point)
        path.closeSubpath()
        return path

    def _paint_stroke_path(self, painter, points, base_size, color, opacity,
                           hardness, is_eraser=False):
        if not points:
            return
        painter.save()
        painter.setCompositionMode(
            QPainter.CompositionMode_DestinationOut
            if is_eraser else QPainter.CompositionMode_SourceOver
        )
        alpha = max(0, min(255, int(round(float(opacity) * color.alpha()))))
        # DestinationOut multiplies the removed alpha by the source alpha.
        # The eraser must therefore be fully opaque; pressure controls only
        # its width, never how completely it removes the stroke.
        paint_color = QColor(0, 0, 0, 255) if is_eraser else QColor(
            color.red(), color.green(), color.blue(), alpha
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(paint_color))
        painter.drawPath(self._clean_stroke_outline(points, base_size))
        painter.restore()

    def _invalidate_stroke(self, stroke):
        stroke.smoothed_path = []
        stroke._cache_size = (0, 0)

    def _current_frame_data(self):
        return self.data.frames.get(self.current_frame)

    def _stroke_bounds(self, stroke):
        if not stroke or not stroke.points:
            return QRectF()
        xs, ys = [], []
        for point in stroke.points:
            px, py = self._point_to_pixel(point)
            xs.append(px)
            ys.append(py)
        pad = max(6.0, stroke.base_size * self._effective_pixel_scale(stroke=stroke))
        return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + pad * 2, max(ys) - min(ys) + pad * 2)

    def _selection_bounds(self):
        fd = self._current_frame_data()
        if not fd or not self.selected_strokes:
            return QRectF()
        rect = QRectF()
        for index in self.selected_strokes:
            if 0 <= index < len(fd.strokes):
                stroke_rect = self._stroke_bounds(fd.strokes[index])
                rect = QRectF(stroke_rect) if rect.isNull() else rect.united(stroke_rect)
        return rect

    def _distance_to_segment(self, px, py, ax, ay, bx, by):
        vx, vy = bx - ax, by - ay
        wx, wy = px - ax, py - ay
        denom = vx * vx + vy * vy
        if denom <= 1e-8:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
        cx, cy = ax + t * vx, ay + t * vy
        return math.hypot(px - cx, py - cy)

    def _hit_test_stroke(self, pos):
        fd = self._current_frame_data()
        if not fd or not fd.strokes:
            return None
        best_index = None
        best_dist = 16.0
        for index in reversed(range(len(fd.strokes))):
            stroke = fd.strokes[index]
            bounds = self._stroke_bounds(stroke)
            if not bounds.adjusted(-8, -8, 8, 8).contains(pos):
                continue
            points = self._stroke_smoothed_path(stroke)
            for i in range(len(points) - 1):
                dist = self._distance_to_segment(pos.x(), pos.y(), points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
                if dist < best_dist:
                    best_dist = dist
                    best_index = index
        return best_index

    def _point_inside_polygon(self, point, polygon):
        if len(polygon) < 3:
            return False
        x, y = point.x(), point.y()
        inside = False
        j = len(polygon) - 1
        for i in range(len(polygon)):
            xi, yi = polygon[i].x(), polygon[i].y()
            xj, yj = polygon[j].x(), polygon[j].y()
            if ((yi > y) != (yj > y)):
                denom = yj - yi
                if abs(denom) < 1e-8:
                    denom = 1e-8 if denom >= 0 else -1e-8
                cross_x = (xj - xi) * (y - yi) / denom + xi
                if x < cross_x:
                    inside = not inside
            j = i
        return inside

    def _point_on_segment_values(self, px, py, ax, ay, bx, by):
        if px < min(ax, bx) - 1e-6 or px > max(ax, bx) + 1e-6:
            return False
        if py < min(ay, by) - 1e-6 or py > max(ay, by) + 1e-6:
            return False
        return abs((px - ax) * (by - ay) - (py - ay) * (bx - ax)) <= 1e-5

    def _segments_intersect_values(self, ax, ay, bx, by, cx, cy, dx, dy):
        def orient(px, py, qx, qy, rx, ry):
            return (qy - py) * (rx - qx) - (qx - px) * (ry - qy)

        o1 = orient(ax, ay, bx, by, cx, cy)
        o2 = orient(ax, ay, bx, by, dx, dy)
        o3 = orient(cx, cy, dx, dy, ax, ay)
        o4 = orient(cx, cy, dx, dy, bx, by)

        if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
            return True
        if abs(o1) <= 1e-6 and self._point_on_segment_values(cx, cy, ax, ay, bx, by):
            return True
        if abs(o2) <= 1e-6 and self._point_on_segment_values(dx, dy, ax, ay, bx, by):
            return True
        if abs(o3) <= 1e-6 and self._point_on_segment_values(ax, ay, cx, cy, dx, dy):
            return True
        if abs(o4) <= 1e-6 and self._point_on_segment_values(bx, by, cx, cy, dx, dy):
            return True
        return False

    def _segment_distance_values(self, ax, ay, bx, by, cx, cy, dx, dy):
        if self._segments_intersect_values(ax, ay, bx, by, cx, cy, dx, dy):
            return 0.0
        return min(
            self._distance_to_segment(ax, ay, cx, cy, dx, dy),
            self._distance_to_segment(bx, by, cx, cy, dx, dy),
            self._distance_to_segment(cx, cy, ax, ay, bx, by),
            self._distance_to_segment(dx, dy, ax, ay, bx, by),
        )

    def _lasso_stroke_touch_radius(self, stroke, pr1=1.0, pr2=1.0):
        pressure = max(0.05, (float(pr1) + float(pr2)) * 0.5)
        scale = self._effective_pixel_scale(stroke=stroke)
        width = compute_stroke_width(stroke.base_size * scale, pressure, 0)
        return max(5.0, width * 0.5 + 3.0)

    def _stroke_hits_lasso(self, stroke, polygon, lasso_rect):
        if stroke.tool == "eraser":
            return False
        if len(polygon) < 3:
            return False
        bounds = self._stroke_bounds(stroke)
        if not bounds.isValid() or not bounds.intersects(lasso_rect):
            return False
        points = self._stroke_smoothed_path(stroke)
        if not points:
            return False

        for x, y, _pressure in points:
            if self._point_inside_polygon(QPointF(x, y), polygon):
                return True

        if len(points) < 2:
            return False

        lasso_loop = list(polygon) + [polygon[0]]
        for i in range(len(points) - 1):
            ax, ay, pr1 = points[i][0], points[i][1], points[i][2]
            bx, by, pr2 = points[i + 1][0], points[i + 1][1], points[i + 1][2]
            touch_radius = self._lasso_stroke_touch_radius(stroke, pr1, pr2)
            for j in range(len(lasso_loop) - 1):
                cx, cy = lasso_loop[j].x(), lasso_loop[j].y()
                dx, dy = lasso_loop[j + 1].x(), lasso_loop[j + 1].y()
                if self._segment_distance_values(ax, ay, bx, by, cx, cy, dx, dy) <= touch_radius:
                    return True
        return False

    def _lasso_mouse_press(self, pos, button, modifiers):
        if button != Qt.LeftButton:
            return
        if self._begin_existing_transform_drag(pos):
            self.lasso_active = False
            self._lasso_points = []
            self.update()
            return
        self.lasso_active = True
        self._lasso_points = [QPointF(pos)]
        self._lasso_additive = bool(modifiers & Qt.ShiftModifier)
        self._lasso_subtractive = bool(modifiers & Qt.ControlModifier)
        self.last_draw_pos = pos
        self.update()

    def _lasso_mouse_move(self, pos):
        self.last_draw_pos = pos
        if not self.lasso_active:
            self.update()
            return
        if not self._lasso_points or (pos - self._lasso_points[-1]).manhattanLength() >= 2:
            self._lasso_points.append(QPointF(pos))
            self.update()

    def _lasso_mouse_release(self):
        if not self.lasso_active:
            return
        points = list(self._lasso_points)
        self.lasso_active = False
        self._lasso_points = []

        fd = self._current_frame_data()
        if not fd or not fd.strokes:
            self.selected_strokes = []
            self.transform_active = False
            self.update()
            return

        hits = []
        if len(points) < 4:
            hit = self._hit_test_stroke(points[0]) if points else None
            if hit is not None and fd.strokes[hit].tool != "eraser":
                hits = [hit]
        else:
            lasso_rect = QRectF(points[0], points[0])
            for point in points[1:]:
                lasso_rect = lasso_rect.united(QRectF(point, point))
            lasso_rect = lasso_rect.adjusted(-6, -6, 6, 6)
            for index, stroke in enumerate(fd.strokes):
                if self._stroke_hits_lasso(stroke, points, lasso_rect):
                    hits.append(index)

        current = set(i for i in self.selected_strokes if 0 <= i < len(fd.strokes))
        if self._lasso_subtractive:
            current.difference_update(hits)
            self.selected_strokes = sorted(current)
        elif self._lasso_additive:
            current.update(hits)
            self.selected_strokes = sorted(current)
        else:
            self.selected_strokes = hits

        self.transform_active = bool(self.selected_strokes)
        self._lasso_additive = False
        self._lasso_subtractive = False
        self.update()

    def activate_transform(self, select_all_if_empty=True):
        fd = self._current_frame_data()
        if not fd or not fd.strokes:
            self.transform_active = False
            self.selected_strokes = []
            self.update()
            return False
        if select_all_if_empty and not self.selected_strokes:
            self.selected_strokes = [i for i, stroke in enumerate(fd.strokes) if stroke.tool != "eraser"]
        self.transform_active = bool(self.selected_strokes)
        if not self._is_free_transform_tool():
            self._transform_current_quad = None
        self.update()
        return self.transform_active

    def deactivate_transform(self):
        self.transform_active = False
        self._transform_drag_mode = None
        self._transform_start_points = {}
        self._transform_start_stroke_sizes = {}
        self._transform_current_quad = None
        self.update()

    def _transform_mouse_press(self, pos, button, modifiers):
        if button != Qt.LeftButton:
            return
        rect = self._selection_bounds()
        mode = self._hit_transform_handle(pos, rect) if self.transform_active else None
        if mode is None and self.transform_active and self._is_free_transform_tool():
            mode = self._hit_lattice_handle(pos)
        if mode is None and rect.isValid() and rect.contains(pos):
            mode = "move"

        if mode is None:
            hit = self._hit_test_stroke(pos)
            if hit is None:
                self.selected_strokes = []
                self.transform_active = False
                self.update()
                return
            if modifiers & Qt.ShiftModifier and hit in self.selected_strokes:
                self.selected_strokes.remove(hit)
            elif modifiers & Qt.ShiftModifier:
                self.selected_strokes.append(hit)
            else:
                self.selected_strokes = [hit]
            self.transform_active = True
            self.update()
            return

        self._transform_drag_mode = mode
        self._invalidate_frame_render_cache(self.current_frame)
        self._transform_drag_start = QPointF(pos)
        self._transform_start_rect = QRectF(rect)
        self._store_transform_undo_snapshot()
        self._capture_transform_start_points()
        if mode in ("tl", "tr", "br", "bl") and self._is_free_transform_tool():
            self._transform_current_quad = self._rect_corners(rect)

    def _existing_transform_mode_at(self, pos):
        if not self.transform_active or not self.selected_strokes:
            return None, QRectF()
        rect = self._selection_bounds()
        if not rect or not rect.isValid():
            return None, QRectF()
        mode = self._hit_transform_handle(pos, rect)
        if mode is None and self._is_free_transform_tool():
            mode = self._hit_lattice_handle(pos)
        if mode is None and rect.contains(pos):
            mode = "move"
        return mode, rect

    def _begin_existing_transform_drag(self, pos):
        mode, rect = self._existing_transform_mode_at(pos)
        if mode is None:
            return False
        self._transform_drag_mode = mode
        self._transform_drag_start = QPointF(pos)
        self._transform_start_rect = QRectF(rect)
        self._store_transform_undo_snapshot()
        self._capture_transform_start_points()
        if mode in ("tl", "tr", "br", "bl") and self._is_free_transform_tool():
            self._transform_current_quad = self._rect_corners(rect)
        return True

    def _transform_mouse_move(self, pos, modifiers):
        self.last_draw_pos = pos
        if not self._transform_drag_mode:
            self.update()
            return
        if self._transform_drag_mode == "move":
            self._apply_transform_move(pos - self._transform_drag_start)
        elif self._transform_drag_mode and self._transform_drag_mode.startswith("vertex:") and self._is_free_transform_tool():
            self._apply_transform_vertex(pos)
        elif self._transform_drag_mode in ("tl", "tr", "br", "bl") and self._is_free_transform_tool():
            self._apply_transform_corner_warp(pos)
        elif self._transform_drag_mode == "rot":
            self._apply_transform_rotate(pos)
        else:
            force_uniform = self._transform_drag_mode in ("tl", "tr", "br", "bl")
            self._apply_transform_scale(pos, modifiers, force_uniform=force_uniform)
        self._rebuild_current_frame()
        self.update()

    def _transform_mouse_release(self):
        self._transform_drag_mode = None
        self._transform_start_points = {}
        self._transform_start_stroke_sizes = {}
        self._transform_current_quad = None
        self._cache_dirty = True
        self.stroke_added.emit(self.current_frame)
        self.update()

    def _store_transform_undo_snapshot(self):
        fd = self._current_frame_data()
        if not fd or not self.selected_strokes:
            self._last_transform_undo = None
            return
        strokes = {}
        selected = []
        for index in self.selected_strokes:
            if 0 <= index < len(fd.strokes):
                strokes[index] = copy.deepcopy(fd.strokes[index])
                selected.append(index)
        if not strokes:
            self._last_transform_undo = None
            return
        self._last_transform_undo = {
            "frame": self.current_frame,
            "selected": selected,
            "strokes": strokes,
        }

    def undo_last_transform(self):
        snapshot = self._last_transform_undo
        if not snapshot or snapshot.get("frame") != self.current_frame:
            return False
        fd = self._current_frame_data()
        if not fd:
            return False

        restored = False
        for index, stroke_snapshot in snapshot.get("strokes", {}).items():
            if 0 <= index < len(fd.strokes):
                fd.strokes[index] = copy.deepcopy(stroke_snapshot)
                self._invalidate_stroke(fd.strokes[index])
                restored = True
        if not restored:
            return False

        self.selected_strokes = [
            index for index in snapshot.get("selected", [])
            if 0 <= index < len(fd.strokes)
        ]
        self.transform_active = bool(self.selected_strokes)
        self._transform_drag_mode = None
        self._transform_start_points = {}
        self._transform_start_stroke_sizes = {}
        self._transform_current_quad = None
        self._last_transform_undo = None
        self._invalidate_frame_render_cache(self.current_frame)
        self._rebuild_current_frame()
        self.update()
        return True

    def _capture_transform_start_points(self):
        fd = self._current_frame_data()
        self._transform_start_points = {}
        self._transform_start_stroke_sizes = {}
        if not fd:
            return
        for index in self.selected_strokes:
            if 0 <= index < len(fd.strokes):
                stroke = fd.strokes[index]
                self._transform_start_stroke_sizes[index] = stroke.base_size
                self._transform_start_points[index] = [
                    (point.x, point.y, point.wx, point.wy, point.wz, *self._point_to_pixel(point))
                    for point in stroke.points
                ]

    def _apply_transform_move(self, delta):
        fd = self._current_frame_data()
        if not fd:
            return
        for index, points in self._transform_start_points.items():
            stroke = fd.strokes[index]
            for point, start in zip(stroke.points, points):
                px, py = self._start_tuple_to_pixel(start)
                self._set_point_from_screen(point, px + delta.x(), py + delta.y())
            self._invalidate_stroke(stroke)

    def _apply_transform_vertex(self, pos):
        fd = self._current_frame_data()
        if not fd:
            return
        try:
            _prefix, stroke_index, point_index = self._transform_drag_mode.split(":")
            stroke_index = int(stroke_index)
            point_index = int(point_index)
        except Exception:
            return
        if stroke_index not in self._transform_start_points:
            return
        if stroke_index < 0 or stroke_index >= len(fd.strokes):
            return
        start_points = self._transform_start_points[stroke_index]
        if point_index < 0 or point_index >= len(start_points):
            return
        stroke = fd.strokes[stroke_index]
        if point_index >= len(stroke.points):
            return
        px, py = self._start_tuple_to_pixel(start_points[point_index])
        self._set_point_from_screen(
            stroke.points[point_index],
            px + (pos.x() - self._transform_drag_start.x()),
            py + (pos.y() - self._transform_drag_start.y())
        )
        self._invalidate_stroke(stroke)

    def _rect_corners(self, rect):
        return {
            "tl": QPointF(rect.left(), rect.top()),
            "tr": QPointF(rect.right(), rect.top()),
            "br": QPointF(rect.right(), rect.bottom()),
            "bl": QPointF(rect.left(), rect.bottom()),
        }

    def _apply_transform_corner_warp(self, pos):
        rect = self._transform_start_rect
        if not rect or rect.width() <= 1 or rect.height() <= 1:
            return
        mode = self._transform_drag_mode
        corners = self._rect_corners(rect)
        delta = pos - self._transform_drag_start
        start_corner = corners[mode]
        corners[mode] = QPointF(start_corner.x() + delta.x(), start_corner.y() + delta.y())
        self._transform_current_quad = corners

        left, top = rect.left(), rect.top()
        width, height = max(1e-5, rect.width()), max(1e-5, rect.height())
        tl, tr, br, bl = corners["tl"], corners["tr"], corners["br"], corners["bl"]

        fd = self._current_frame_data()
        if not fd:
            return
        for index, points in self._transform_start_points.items():
            stroke = fd.strokes[index]
            for point, start in zip(stroke.points, points):
                px, py = self._start_tuple_to_pixel(start)
                u = (px - left) / width
                v = (py - top) / height
                top_x = tl.x() + (tr.x() - tl.x()) * u
                top_y = tl.y() + (tr.y() - tl.y()) * u
                bottom_x = bl.x() + (br.x() - bl.x()) * u
                bottom_y = bl.y() + (br.y() - bl.y()) * u
                npx = top_x + (bottom_x - top_x) * v
                npy = top_y + (bottom_y - top_y) * v
                self._set_point_from_screen(point, npx, npy)
            self._invalidate_stroke(stroke)
        self._apply_transform_stroke_width_factor(self._quad_area_scale(rect, corners))

    def _apply_transform_scale(self, pos, modifiers, force_uniform=False):
        rect = self._transform_start_rect
        if not rect or rect.width() <= 1 or rect.height() <= 1:
            return
        mode = self._transform_drag_mode
        anchor_x = rect.right() if "l" in mode else rect.left() if "r" in mode else rect.center().x()
        anchor_y = rect.bottom() if "t" in mode else rect.top() if "b" in mode else rect.center().y()
        start_x = rect.left() if "l" in mode else rect.right() if "r" in mode else rect.center().x()
        start_y = rect.top() if "t" in mode else rect.bottom() if "b" in mode else rect.center().y()
        sx = 1.0 if abs(start_x - anchor_x) < 1e-5 else (pos.x() - anchor_x) / (start_x - anchor_x)
        sy = 1.0 if abs(start_y - anchor_y) < 1e-5 else (pos.y() - anchor_y) / (start_y - anchor_y)
        if mode in ("l", "r"):
            sy = 1.0
        elif mode in ("t", "b"):
            sx = 1.0
        if force_uniform or (modifiers & Qt.ShiftModifier):
            uniform = max(abs(sx), abs(sy))
            sx = uniform if sx >= 0 else -uniform
            sy = uniform if sy >= 0 else -uniform
        self._apply_pixel_scale(anchor_x, anchor_y, sx, sy)
        self._apply_transform_stroke_width_scale(sx, sy)

    def _apply_transform_rotate(self, pos):
        rect = self._transform_start_rect
        if not rect or not rect.isValid():
            return
        center = rect.center()
        start_angle = math.atan2(self._transform_drag_start.y() - center.y(), self._transform_drag_start.x() - center.x())
        current_angle = math.atan2(pos.y() - center.y(), pos.x() - center.x())
        delta = current_angle - start_angle
        cos_a = math.cos(delta)
        sin_a = math.sin(delta)
        fd = self._current_frame_data()
        if not fd:
            return
        for index, points in self._transform_start_points.items():
            stroke = fd.strokes[index]
            for point, start in zip(stroke.points, points):
                px, py = self._start_tuple_to_pixel(start)
                dx, dy = px - center.x(), py - center.y()
                npx = center.x() + dx * cos_a - dy * sin_a
                npy = center.y() + dx * sin_a + dy * cos_a
                self._set_point_from_screen(point, npx, npy)
            self._invalidate_stroke(stroke)

    def _apply_pixel_scale(self, anchor_x, anchor_y, sx, sy):
        fd = self._current_frame_data()
        if not fd:
            return
        for index, points in self._transform_start_points.items():
            stroke = fd.strokes[index]
            for point, start in zip(stroke.points, points):
                px, py = self._start_tuple_to_pixel(start)
                npx = anchor_x + (px - anchor_x) * sx
                npy = anchor_y + (py - anchor_y) * sy
                self._set_point_from_screen(point, npx, npy)
            self._invalidate_stroke(stroke)

    def _apply_transform_stroke_width_scale(self, sx, sy):
        scale = math.sqrt(max(1e-6, abs(float(sx) * float(sy))))
        self._apply_transform_stroke_width_factor(scale)

    def _quad_area_scale(self, rect, corners):
        base_area = max(1e-6, float(rect.width()) * float(rect.height()))
        points = [corners["tl"], corners["tr"], corners["br"], corners["bl"]]
        area = 0.0
        for i, point in enumerate(points):
            next_point = points[(i + 1) % len(points)]
            area += point.x() * next_point.y() - next_point.x() * point.y()
        return math.sqrt(max(1e-6, abs(area) * 0.5 / base_area))

    def _apply_transform_stroke_width_factor(self, scale):
        fd = self._current_frame_data()
        if not fd:
            return
        try:
            scale = abs(float(scale))
            if not math.isfinite(scale):
                scale = 1.0
        except Exception:
            scale = 1.0
        scale = max(0.05, scale)
        for index in self._transform_start_points:
            if 0 <= index < len(fd.strokes):
                stroke = fd.strokes[index]
                start_size = self._transform_start_stroke_sizes.get(index, stroke.base_size)
                stroke.base_size = max(0.1, start_size * scale)
                self._invalidate_stroke(stroke)

    def _hit_transform_handle(self, pos, rect):
        if not rect or not rect.isValid():
            return None
        for name, handle_rect in self._build_transform_handles(rect).items():
            if handle_rect.contains(pos):
                return name
        return None

    def _iter_lattice_point_indices(self, stroke):
        count = len(stroke.points)
        if count <= 0:
            return []
        if count <= self._lattice_handle_limit:
            return list(range(count))
        step = max(1, int(math.ceil(float(count) / float(self._lattice_handle_limit))))
        indices = list(range(0, count, step))
        if indices[-1] != count - 1:
            indices.append(count - 1)
        return indices

    def _build_lattice_handles(self):
        if not self._is_free_transform_tool():
            return []
        fd = self._current_frame_data()
        handles = []
        if not fd or not self.selected_strokes:
            return handles
        radius = 4.4
        for stroke_index in self.selected_strokes:
            if stroke_index < 0 or stroke_index >= len(fd.strokes):
                continue
            stroke = fd.strokes[stroke_index]
            if stroke.tool == "eraser":
                continue
            for point_index in self._iter_lattice_point_indices(stroke):
                point = stroke.points[point_index]
                px, py = self._point_to_pixel(point)
                rect = QRectF(px - radius, py - radius, radius * 2.0, radius * 2.0)
                handles.append((f"vertex:{stroke_index}:{point_index}", rect))
        return handles

    def _hit_lattice_handle(self, pos):
        for mode, handle_rect in reversed(self._build_lattice_handles()):
            if handle_rect.contains(pos):
                return mode
        return None

    def _build_transform_handles(self, rect):
        size = 10.0
        half = size * 0.5
        points = {
            "tl": rect.topLeft(), "t": QPointF(rect.center().x(), rect.top()),
            "tr": rect.topRight(), "r": QPointF(rect.right(), rect.center().y()),
            "br": rect.bottomRight(), "b": QPointF(rect.center().x(), rect.bottom()),
            "bl": rect.bottomLeft(), "l": QPointF(rect.left(), rect.center().y()),
            "rot": QPointF(rect.center().x(), rect.top() - 26.0),
        }
        return {name: QRectF(pt.x() - half, pt.y() - half, size, size) for name, pt in points.items()}

    def _draw_transform_box(self, painter):
        if not self.transform_active or not self.selected_strokes:
            return
        rect = self._selection_bounds()
        if not rect or not rect.isValid():
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(120, 180, 255, 230), 1.2, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        quad = self._transform_current_quad
        if quad:
            path = QPainterPath(quad["tl"])
            path.lineTo(quad["tr"])
            path.lineTo(quad["br"])
            path.lineTo(quad["bl"])
            path.closeSubpath()
            painter.drawPath(path)
        else:
            painter.drawRect(rect)
            rot_center = QPointF(rect.center().x(), rect.top() - 26.0)
            painter.drawLine(QPointF(rect.center().x(), rect.top()), rot_center)
        painter.setPen(QPen(QColor(20, 30, 45, 240), 1))
        painter.setBrush(QBrush(QColor(140, 205, 255, 235)))
        if quad:
            size = 10.0
            half = size * 0.5
            for point in quad.values():
                painter.drawRect(QRectF(point.x() - half, point.y() - half, size, size))
        else:
            for name, handle_rect in self._build_transform_handles(rect).items():
                if name == "rot":
                    painter.drawEllipse(handle_rect)
                    continue
                painter.drawRect(handle_rect)
        painter.setPen(QPen(QColor(15, 25, 38, 220), 1.0))
        painter.setBrush(QBrush(QColor(190, 230, 255, 230)))
        for _mode, handle_rect in self._build_lattice_handles():
            painter.drawEllipse(handle_rect)
        painter.restore()

    def _draw_lasso_overlay(self, painter):
        if not self.lasso_active or len(self._lasso_points) < 2:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath(self._lasso_points[0])
        for point in self._lasso_points[1:]:
            path.lineTo(point)
        if len(self._lasso_points) > 2:
            closed = QPainterPath(path)
            closed.closeSubpath()
            painter.fillPath(closed, QColor(80, 145, 255, 34))
        pen = QPen(QColor(125, 190, 255, 235), 1.6, Qt.DashLine)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.restore()

    def _begin_stroke(self, pos: QPointF, pressure: float, tilt_x: float = 0, tilt_y: float = 0, rotation: float = 0):
        self._ensure_view_render_current()
        self.drawing = True
        self.kalman.reset(pressure)
        self.current_points = []
        # Freeze all camera conversions once. Previously each accepted point
        # recalculated this about six times through maya.cmds/OpenMaya.
        self._drawing_view_transform = self._view_panzoom_transform()
        zoom_x, zoom_y, _offset_x, _offset_y = self._drawing_view_transform
        self._drawing_view_zoom = max(
            0.001, math.sqrt(abs(float(zoom_x) * float(zoom_y)))
        )
        # Fijar las dimensiones de referencia la primera vez que se dibuja
        if not self.data.get_frame_numbers():
            self.data.canvas_width = self.width()
            self.data.canvas_height = self.height()
        self._drawing_uniform_transform = self._uniform_transform()
        self._drawing_pixel_scale = self._viewport_brush_scale()
        nx, ny = self._to_normalized(
            pos.x(), pos.y(), self._drawing_view_transform,
            self._drawing_uniform_transform,
        )
        persistent_pressure = self._persistent_pressure(pressure, 0.0, tilt_x)
        pt = StrokePoint(x=nx, y=ny, pressure=persistent_pressure, tilt_x=tilt_x, tilt_y=tilt_y, rotation=rotation)
        self.current_points.append(pt)
        self.last_draw_pos = pos
        self._last_input_pos = QPointF(pos)
        self.last_pressure = persistent_pressure

        self.overlay_pixmap.fill(Qt.transparent)
        self._stroke_base_pixmap = (
            self.canvas_pixmap.copy() if self.tool == "eraser" else None
        )

    def _add_point(self, pos: QPointF, pressure: float, tilt_x: float = 0, tilt_y: float = 0, rotation: float = 0):
        if not self.drawing: return
        smooth_pressure = self.kalman.update(pressure)
        if self.last_draw_pos:
            distance = math.hypot(
                pos.x() - self.last_draw_pos.x(), pos.y() - self.last_draw_pos.y()
            )
            min_spacing = max(
                0.85,
                self.brush_size * self._drawing_pixel_scale * 0.075,
            )
            if distance < min_spacing and abs(smooth_pressure - self.last_pressure) < 0.026:
                self.last_cursor_pos = pos
                return

        nx, ny = self._to_normalized(
            pos.x(), pos.y(), self._drawing_view_transform,
            self._drawing_uniform_transform,
        )
        persistent_pressure = self._persistent_pressure(smooth_pressure, 0.0, tilt_x)
        pt = StrokePoint(x=nx, y=ny, pressure=persistent_pressure, tilt_x=tilt_x, tilt_y=tilt_y, rotation=rotation)
        self.current_points.append(pt)

        if self.last_draw_pos and len(self.current_points) >= 2:
            self._render_incremental(0.0, persistent_pressure, tilt_x)

        self.last_draw_pos = QPointF(pos)
        self._last_input_pos = QPointF(pos)
        self.last_pressure = persistent_pressure
        self.update()

    def _end_stroke(self):
        if not self.drawing: return
        self.drawing = False
        self.physics_timer.stop()

        if not self.current_points:
            self.overlay_pixmap.fill(Qt.transparent)
            self._stroke_base_pixmap = None
            return

        color_tuple = (self.brush_color.red(), self.brush_color.green(), self.brush_color.blue(), self.brush_color.alpha())
        stroke = Stroke(
            points=list(self.current_points), color=color_tuple, base_size=self.brush_size,
            draw_zoom=self._drawing_view_zoom,
            tool=self.tool, opacity=self.brush_opacity, hardness=self.brush_hardness
        )
        frame_data = self.data.get_or_create_frame(self.current_frame)
        frame_data.strokes.append(stroke)
        self._invalidate_frame_render_cache(self.current_frame)
        self._last_transform_undo = None

        # Replace the lightweight live preview with a high-quality render of
        # only the new stroke. Rebuilding every previous stroke on every mouse
        # release made a drawing progressively slower as it grew.
        if self.tool == "eraser" and self._stroke_base_pixmap is not None:
            self.canvas_pixmap = QPixmap(self._stroke_base_pixmap)
        self.overlay_pixmap.fill(Qt.transparent)
        self._render_single_stroke(stroke, self.canvas_pixmap)
        self._stroke_base_pixmap = None
        self.current_points = []
        self._cache_dirty = True
        self.stroke_added.emit(self.current_frame)
        self.update()

    def _physics_tick(self):
        self.update()

    def _render_incremental(self, speed: float, pressure: float, tilt: float):
        """Paint a cheap live preview; final stroke quality is rendered on release."""
        is_eraser = (self.tool == "eraser")
        target = self.canvas_pixmap if is_eraser else self.overlay_pixmap
        painter = QPainter(target)
        painter.setRenderHint(QPainter.Antialiasing, True)
        n = len(self.current_points)

        if n < 2:
            painter.end()
            return

        first = self.current_points[-2]
        second = self.current_points[-1]
        x1, y1 = self._point_to_pixel(
            first, self._drawing_view_transform,
            self._drawing_uniform_transform,
        )
        x2, y2 = self._point_to_pixel(
            second, self._drawing_view_transform,
            self._drawing_uniform_transform,
        )
        average_pressure = (first.pressure + second.pressure) * 0.5
        width = compute_stroke_width(
            self.brush_size * self._drawing_pixel_scale,
            average_pressure, speed,
            velocity_sensitivity=self.velocity_sensitivity,
        )
        painter.setCompositionMode(
            QPainter.CompositionMode_DestinationOut
            if is_eraser else QPainter.CompositionMode_SourceOver
        )
        # Opacity belongs to the stroke, not to every tablet stamp.  Varying
        # alpha per sample created dark beads where segments overlapped.
        alpha = int(max(0.0, min(1.0,
            self.brush_opacity * (self.brush_color.alpha() / 255.0)
        )) * 255.0)
        color = QColor(0, 0, 0, 255) if is_eraser else QColor(
            self.brush_color.red(), self.brush_color.green(),
            self.brush_color.blue(), alpha,
        )
        pen = QPen(color)
        pen.setWidthF(max(0.5, width))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        if n >= 3:
            previous = self.current_points[-3]
            x0, y0 = self._point_to_pixel(
                previous, self._drawing_view_transform,
                self._drawing_uniform_transform,
            )
            path = QPainterPath(QPointF((x0 + x1) * 0.5, (y0 + y1) * 0.5))
            path.quadTo(QPointF(x1, y1), QPointF((x1 + x2) * 0.5, (y1 + y2) * 0.5))
            painter.drawPath(path)
        else:
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        painter.end()

    def _render_single_stroke(self, stroke, target):
        """Render one completed vector stroke without rebuilding the frame."""
        if not stroke or not stroke.points:
            return
        raw = []
        for point in stroke.points:
            px, py = self._point_to_pixel(
                point, self._drawing_view_transform,
                self._drawing_uniform_transform,
            )
            raw.append((px, py, point.pressure))
        points = self.spline.smooth_path(raw, num_interp=6)
        painter = QPainter(target)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_saved_stroke(
            painter, stroke, points, self._drawing_pixel_scale,
            global_opacity=1.0,
        )
        painter.end()

    def _commit_overlay(self):
        # Eraser ya pintó directo en canvas_pixmap, nada que commitear
        if self.tool == "eraser":
            self.overlay_pixmap.fill(Qt.transparent)
            return
        painter = QPainter(self.canvas_pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, self.overlay_pixmap)
        painter.end()
        self.overlay_pixmap.fill(Qt.transparent)

    def _paint_saved_stroke(self, painter, stroke, points, scale,
                            global_opacity=1.0):
        color = QColor(*tuple(stroke.color[:4]))
        self._paint_stroke_path(
            painter,
            points,
            stroke.base_size * scale,
            color,
            max(0.0, min(1.0, float(stroke.opacity) * float(global_opacity))),
            stroke.hardness,
            is_eraser=(stroke.tool == "eraser"),
        )

    def _render_frame_to_pixmap(self, frame_data: FrameData, target: QPixmap, opacity: float = 1.0):
        if not frame_data or not frame_data.strokes:
            return

        painter = QPainter(target)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base_scale = self._viewport_brush_scale()
        current_zoom = self._current_panzoom_zoom()

        for stroke in frame_data.strokes:
            s_uniform = base_scale * (
                current_zoom / max(0.001, float(stroke.draw_zoom or 1.0))
            )
            seg = self._stroke_smoothed_path(stroke)
            self._paint_saved_stroke(
                painter, stroke, seg, s_uniform, global_opacity=opacity
            )

        painter.end()

    def _build_onion_skin(self, current_frame: int) -> QPixmap:
        w, h = self.canvas_pixmap.width(), self.canvas_pixmap.height()
        result = QPixmap(w, h)
        result.fill(Qt.transparent)

        frame_nums = self.data.get_frame_numbers()
        decay = getattr(self, 'onion_decay', 0.4)

        # ── Frames ANTERIORES (naranja) ──────────────────────────
        before_frames = [f for f in frame_nums if f < current_frame][-self.onion_before:]
        for fn in reversed(before_frames):
            distance = current_frame - fn          # distancia real en frames
            alpha = self.onion_opacity * max(0.0, 1.0 - decay * (distance - 1))
            if alpha <= 0.01:
                continue
            fd = self.data.frames.get(fn)
            if fd:
                layer = QPixmap(w, h)
                layer.fill(Qt.transparent)
                self._render_frame_to_pixmap(fd, layer, 1.0)
                tint = QPixmap(w, h)
                tint.fill(QColor(255, 140, 0, int(alpha * 255)))
                p = QPainter(layer)
                p.setCompositionMode(QPainter.CompositionMode_Multiply)
                p.drawPixmap(0, 0, tint)
                p.end()
                pp = QPainter(result)
                pp.setOpacity(alpha)
                pp.drawPixmap(0, 0, layer)
                pp.end()

        # ── Frames POSTERIORES (azul) ────────────────────────────
        after_frames = [f for f in frame_nums if f > current_frame][:self.onion_after]
        for fn in after_frames:
            distance = fn - current_frame
            alpha = self.onion_opacity * max(0.0, 1.0 - decay * (distance - 1))
            if alpha <= 0.01:
                continue
            fd = self.data.frames.get(fn)
            if fd:
                layer = QPixmap(w, h)
                layer.fill(Qt.transparent)
                self._render_frame_to_pixmap(fd, layer, 1.0)
                tint = QPixmap(w, h)
                tint.fill(QColor(0, 180, 255, int(alpha * 255)))
                p = QPainter(layer)
                p.setCompositionMode(QPainter.CompositionMode_Multiply)
                p.drawPixmap(0, 0, tint)
                p.end()
                pp = QPainter(result)
                pp.setOpacity(alpha)
                pp.drawPixmap(0, 0, layer)
                pp.end()

        return result

    def paintEvent(self, event):
        self._ensure_pixmap_size()
        self._ensure_view_render_current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        dim = getattr(self, "dim_background", False)
        pt = getattr(self, "pass_through_mode", False)

        if dim:
            painter.fillRect(self.rect(), QColor(15, 15, 20, 100))
        elif not pt:
            painter.fillRect(self.rect(), QColor(1, 1, 1, 1))

        # Onion skins can require three extra full-frame rasterizations.  They
        # are useful while posing, but must not compete with Maya playback.
        if self.onion_enabled and not self._playback_mode and self._cache_dirty:
            self._onion_cache[self.current_frame] = self._build_onion_skin(self.current_frame)
            self._cache_dirty = False

        if (
            self.onion_enabled and not self._playback_mode
            and self.current_frame in self._onion_cache
        ):
            painter.drawPixmap(0, 0, self._onion_cache[self.current_frame])

        # Siempre dibujar el canvas actual
        painter.drawPixmap(0, 0, self.canvas_pixmap)

        # Overlay de dibujo en progreso (solo para brush, eraser ya pinta directo)
        if self.drawing and self.tool != "eraser":
            painter.drawPixmap(0, 0, self.overlay_pixmap)

        self._draw_transform_box(painter)
        self._draw_lasso_overlay(painter)

        # Render Cursor
        if self.last_draw_pos and self.tool in ("brush", "eraser"):
            cx = self.last_draw_pos.x()
            cy = self.last_draw_pos.y()
            if self.drawing:
                r = max(2.0, self.brush_size * 0.5 * self.last_pressure)
            else:
                r = max(2.0, self.brush_size * 0.5)
            
            pen = QPen(QColor(255, 255, 255, 180) if self.tool != "eraser" else QColor(255, 80, 80, 180))
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), r, r)

        painter.end()

    def set_frame(self, frame_num: int, force_repaint: bool = False):
        self.current_frame = frame_num
        self.selected_strokes = []
        self.transform_active = False
        self._last_transform_undo = None
        self._rebuild_current_frame()
        # Let Qt coalesce repaints when Maya advances more quickly than the UI.
        # A synchronous repaint on every frame throttled scene playback.
        self.update()

    def set_playback_mode(self, enabled):
        enabled = bool(enabled)
        if enabled == self._playback_mode:
            return
        self._playback_mode = enabled
        if not enabled:
            self._cache_dirty = True
        self.update()

    def undo_stroke(self):
        fd = self.data.frames.get(self.current_frame)
        if fd and fd.strokes:
            fd.strokes.pop()
            self._invalidate_frame_render_cache(self.current_frame)
            self.selected_strokes = []
            self.transform_active = False
            self._last_transform_undo = None
            self._rebuild_current_frame()
            self.stroke_added.emit(self.current_frame)
            self.update()

    def clear_current_frame(self):
        self.selected_strokes = []
        self.transform_active = False
        self._last_transform_undo = None
        self.data.clear_frame(self.current_frame)
        self._invalidate_frame_render_cache(self.current_frame)
        self._rebuild_current_frame()
        self.stroke_added.emit(self.current_frame)
        self.update()
        
    def clear_all(self):
        self.selected_strokes = []
        self.transform_active = False
        self._last_transform_undo = None
        self.data.clear_all()
        self._invalidate_frame_render_cache()
        self._rebuild_current_frame()
        self.stroke_added.emit(self.current_frame)
        self.update()

    def render_frame_to_image(self, frame_num, target_w, target_h):
        """Renderiza los trazos de un frame a un QImage a resolución arbitraria.
        Pinta directo sobre QImage (NO QPixmap) para preservar canal alpha."""
        img = QImage(target_w, target_h, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))

        fd = self.data.frames.get(frame_num)
        if not fd or not fd.strokes:
            return img

        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        zoom_x, zoom_y, offset_x, offset_y = self._view_panzoom_transform(target_w, target_h)
        current_zoom = max(
            0.001, math.sqrt(abs(float(zoom_x) * float(zoom_y)))
        )

        for stroke in fd.strokes:
            raw = []
            for p in stroke.points:
                px = p.x * target_w
                py = p.y * target_h
                raw.append((px * zoom_x + offset_x, py * zoom_y + offset_y, p.pressure))
            seg = self.spline.smooth_path(raw, num_interp=6)
            stroke_scale = self._viewport_brush_scale(target_w, target_h) * (
                current_zoom / max(0.001, float(stroke.draw_zoom or 1.0))
            )
            self._paint_saved_stroke(painter, stroke, seg, stroke_scale, global_opacity=1.0)

        painter.end()
        return img

# ═══════════════════════════════════════════════════════════════════════
#  MÓDULO 4: MINI-TIMELINE
# ═══════════════════════════════════════════════════════════════════════

class MayaTimelineSync(QtCore.QObject):
    frame_changed = QtCore.Signal(int)
    
    def __init__(self, data_ref):
        super().__init__()
        self.data_ref = data_ref
        self.script_job_id = None
        self._start_tracking()

    def _start_tracking(self):
        try:
            if hasattr(cmds, 'scriptJob'):
                self.script_job_id = cmds.scriptJob(event=["timeChanged", self._on_maya_time_changed], protected=True)
        except Exception:
            pass

    def _on_maya_time_changed(self):
        try:
            curr = int(cmds.currentTime(query=True))
            self.frame_changed.emit(curr)
        except Exception:
            pass

    def _get_brush_key_node(self):
        node = _get_brush_container()
        if not cmds.attributeQuery("drawFrames", node=node, exists=True):
            cmds.addAttr(node, ln="drawFrames", at="double", min=0, max=1, dv=0)
        cmds.setAttr(f"{node}.drawFrames", keyable=True)

        legacy_node = "Sketchboard_Keys"
        if cmds.objExists(legacy_node) and legacy_node != node:
            try:
                if cmds.attributeQuery("drawFrames", node=legacy_node, exists=True):
                    cmds.delete(legacy_node)
            except Exception:
                pass

        return node

    def update_keys(self):
        try:
            if not hasattr(cmds, 'objExists'): return
            
            node = self._get_brush_key_node()

            # Limpiar llaves previas
            cmds.cutKey(node, attribute="drawFrames", clear=True)
            
            # Inyectar ticks en el timeline
            for fn in self.data_ref.get_frame_numbers():
                cmds.setKeyframe(node, attribute="drawFrames", time=(fn, fn), value=1)
            
            # Seleccionar el nodo para que Maya muestre los keyframes en el timeline
            old_sel = cmds.ls(selection=True) or []
            cmds.select(node, replace=True)
            # Deferred: restaurar selección después de que Maya procese el UI
            if old_sel:
                cmds.evalDeferred(lambda: cmds.select(old_sel, replace=True) if cmds.objExists(old_sel[0]) else None)
            else:
                cmds.evalDeferred(lambda: cmds.select(clear=True))
                
        except Exception:
            pass

    def cleanup(self):
        if self.script_job_id and hasattr(cmds, 'scriptJob'):
            try:
                cmds.scriptJob(kill=self.script_job_id, force=True)
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════════════════
#  MÓDULO 5: PANEL DE HERRAMIENTAS
# ═══════════════════════════════════════════════════════════════════════

TOOL_ICONS = {"brush": "B", "eraser": "E", "lasso": "L", "transform": "T", "free_transform": "F"}
BRUSH_ICON_FILES = {
    "brush": "animkey_btn_Brush_128.png",
    "eraser": "animkey_btn_Eraser_128.png",
    "lasso": "animkey_btn_Lasso_128.png",
    "transform": "animkey_btn_Transform_128.png",
    "free_transform": "animkey_btn_FreeTransform_128.png",
    "onion": "animkey_btn_Onion_128.png",
    "undo": "animkey_btn_Undo_128.png",
    "clear": "animkey_btn_Clear_128.png",
    "export": "animkey_btn_Export_128.png",
    "import": "animkey_btn_Import_128.png",
    "toggle": "animkey_btn_Toggle_128.png",
    "lock": "animkey_btn_Lock_128.png",
    "delete": "animkey_btn_Del_128.png",
    "bar": "animkey_btn_Bar_128.png",
}


def get_brush_icon_path(icon_key):
    filename = BRUSH_ICON_FILES.get(icon_key)
    if not filename:
        return ""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, "..", "data", "icons", "Brush_Icons", filename))


def apply_brush_button_icon(button, icon_key, fallback_text="", icon_size=24):
    path = get_brush_icon_path(icon_key)
    if path and os.path.exists(path):
        icon = QtGui.QIcon(path)
        if not icon.isNull():
            button.setText("")
            button.setIcon(icon)
            button.setIconSize(QtCore.QSize(icon_size, icon_size))
            return True
    button.setText(fallback_text)
    return False

BRUSH_SIZE_MIN = 0.5
BRUSH_SIZE_MAX = 150.0
BRUSH_SIZE_SLIDER_STEPS = 1000
BRUSH_SIZE_CURVE = 2.35

class ToolPanel(QWidget):
    tool_changed = QtCore.Signal(str)
    color_changed = QtCore.Signal(QColor)
    size_changed = QtCore.Signal(float)
    onion_toggled = QtCore.Signal(bool)
    undo_requested = QtCore.Signal()
    clear_requested = QtCore.Signal()
    save_requested = QtCore.Signal()
    load_requested = QtCore.Signal()
    playblast_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self._onion_btn = None   # guardamos ref para calcular pos del popup
        self._build_ui()

    def _clamp_brush_size(self, size):
        return max(BRUSH_SIZE_MIN, min(BRUSH_SIZE_MAX, float(size)))

    def _slider_value_to_size(self, value):
        t = max(0.0, min(1.0, float(value) / float(BRUSH_SIZE_SLIDER_STEPS)))
        return BRUSH_SIZE_MIN + (BRUSH_SIZE_MAX - BRUSH_SIZE_MIN) * (t ** BRUSH_SIZE_CURVE)

    def _size_to_slider_value(self, size):
        size = self._clamp_brush_size(size)
        t = (size - BRUSH_SIZE_MIN) / max(1e-8, BRUSH_SIZE_MAX - BRUSH_SIZE_MIN)
        return int(round((t ** (1.0 / BRUSH_SIZE_CURVE)) * BRUSH_SIZE_SLIDER_STEPS))

    def _format_size_slider_value(self, value):
        size = self._slider_value_to_size(value)
        if size < 10.0:
            return f"{size:.2f}"
        if size < 40.0:
            return f"{size:.1f}"
        return f"{size:.0f}"

    def set_size_value(self, size):
        if not hasattr(self, "size_slider"):
            return
        slider_value = self._size_to_slider_value(size)
        previous_value = self.size_slider.value()
        self.size_slider.setValue(slider_value)
        if previous_value == slider_value:
            display = self._format_size_slider_value(slider_value)
            self.size_slider.setToolTip(f"Size: {display}")
            self.size_changed.emit(self._slider_value_to_size(slider_value))

    def _build_ui(self):
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()

        # ── Una sola barra, sin sub-contenedores ────────────────
        layout = QHBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 0, 12, 0)

        TOOL_TOOLTIPS = {
            "brush": "Brush (B)",
            "eraser": "Eraser (E)",
            "lasso": "Lasso select strokes (L)",
            "transform": "Transform drawing (Ctrl+T)",
            "free_transform": "Free Transform: warp corners and edit points (Ctrl+Shift+T)"
        }

        btn_style = self._btn_style()

        # ── Herramientas ─────────────────────────────────────────
        self.tool_buttons = {}
        tool_icons = [
            ("brush", "brush", TOOL_ICONS.get("brush", "B")),
            ("eraser", "eraser", TOOL_ICONS.get("eraser", "E")),
            ("lasso", "lasso", TOOL_ICONS.get("lasso", "L")),
            ("transform", "transform", TOOL_ICONS.get("transform", "T")),
            ("free_transform", "free_transform", TOOL_ICONS.get("free_transform", "F")),
        ]
        for tool, icon_key, fallback in tool_icons:
            btn = QPushButton()
            btn.setFixedSize(30, 30)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(TOOL_TOOLTIPS.get(tool, tool.capitalize()))
            btn.clicked.connect(lambda checked=False, t=tool: self._select_tool(t))
            btn.setStyleSheet(btn_style)
            apply_brush_button_icon(btn, icon_key, fallback, icon_size=24)
            layout.addWidget(btn)
            self.tool_buttons[tool] = btn
        self.tool_buttons["brush"].setChecked(True)

        # ── Divisor vertical ─────────────────────────────────────
        layout.addWidget(self._make_divider())

        # ── Color ────────────────────────────────────────────────
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(22, 22)
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.setStyleSheet(
            "QPushButton { background-color: #FFFFFF; border-radius: 11px;"
            "  border: 1px solid rgba(255,255,255,40); }"
            "QPushButton:hover { border: 2px solid rgba(255,255,255,180); }"
        )
        self.color_btn.clicked.connect(self._pick_color)
        self.color_btn.setToolTip("Change Color")
        layout.addWidget(self.color_btn)

        layout.addSpacing(2)

        # ── Slider tamaño ────────────────────────────────────────
        self.size_slider = self._add_slider(
            layout,
            "S:",
            0,
            BRUSH_SIZE_SLIDER_STEPS,
            self._size_to_slider_value(10.0),
            self._on_size,
            120,
            "Size",
            self._format_size_slider_value
        )

        # ── Divisor vertical ─────────────────────────────────────
        layout.addWidget(self._make_divider())

        # ── Acciones ─────────────────────────────────────────────
        actions = [
            ("🧅", self.onion_toggled, "Onion Skin (Right-click for options)", True, True),
            ("↩",  self.undo_requested,  "Undo (Ctrl+Z)",                       False, False),
            ("🗑",  self.clear_requested, "Clear Board (Current frame)",          False, False),
            ("💾",  self.save_requested,  "Save Drawings",                        False, False),
            ("📂",  self.load_requested,  "Load Drawings",                        False, False),
            ("🎬",  self.playblast_requested, "Playblast with Strokes",           False, False),
        ]
        action_icon_keys = {
            "Onion Skin (Right-click for options)": ("onion", "O"),
            "Undo (Ctrl+Z)": ("undo", "U"),
            "Clear Board (Current frame)": ("clear", "C"),
            "Save Drawings": ("export", "S"),
            "Load Drawings": ("import", "I"),
            "Playblast with Strokes": ("export", "P"),
        }
        for icon, signal, tooltip, checkable, checked in actions:
            btn = QPushButton(icon)
            btn.setFixedSize(30, 30)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.PointingHandCursor)
            icon_key, fallback = action_icon_keys.get(tooltip, ("", icon))
            apply_brush_button_icon(btn, icon_key, fallback, icon_size=24)
            style = btn_style
            if checkable:
                btn.setCheckable(True)
                btn.setChecked(checked)
                btn.toggled.connect(signal.emit)
                if icon == "🧅":
                    self._onion_btn = btn
                    btn.setContextMenuPolicy(Qt.CustomContextMenu)
                    btn.customContextMenuRequested.connect(self._show_onion_options)
                style += (
                    "QPushButton:checked {"
                    "    background-color: rgba(80,140,255,0.24);"
                    "    border: 1px solid rgba(100,160,255,0.78);"
                    "}"
                )
                btn.setStyleSheet(style)
            else:
                btn.clicked.connect(signal.emit)
                btn.setStyleSheet(style)
            layout.addWidget(btn)

        self.setStyleSheet(f"""
            QWidget {{ background-color: transparent; color: {theme["text_primary"]}; font-size: {theme["font_size_normal"]}; font-family: {theme["font_family"]}; }}
            QSlider::groove:horizontal {{
                height: 3px;
                background: rgba(255,255,255,0.10);
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 12px;
                height: 12px;
                margin: -5px 0;
                background: qradialgradient(cx:0.35, cy:0.35, radius:0.7,
                    fx:0.35, fy:0.35,
                    stop:0 rgba(255,255,255,0.95),
                    stop:1 rgba(180,190,210,0.85));
                border-radius: 6px;
                border: 1px solid rgba(255,255,255,0.25);
            }}
            QSlider::handle:horizontal:hover {{
                background: qradialgradient(cx:0.35, cy:0.35, radius:0.7,
                    fx:0.35, fy:0.35,
                    stop:0 #ffffff,
                    stop:1 rgba(160,200,255,0.95));
                border: 1px solid rgba(120,180,255,0.6);
            }}
            QSlider::handle:horizontal:pressed {{
                background: qradialgradient(cx:0.35, cy:0.35, radius:0.7,
                    fx:0.35, fy:0.35,
                    stop:0 rgba(120,180,255,1.0),
                    stop:1 rgba(80,140,220,0.95));
            }}
            QSlider::sub-page:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(80,140,255,0.6),
                    stop:1 rgba(140,200,255,0.8));
                border-radius: 2px;
            }}
            QToolTip {{
                background-color: rgba(18, 20, 28, 0.97);
                color: #ffffff;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px;
                padding: 5px 9px;
                font-size: {theme["font_size_normal"]};
                font-family: {theme["font_family"]};
                font-weight: 500;
            }}
        """)

    def _make_divider(self):
        """Línea vertical separadora dentro de la barra."""
        div = QFrame()
        div.setFrameShape(QFrame.VLine)
        div.setFixedWidth(1)
        div.setFixedHeight(22)
        div.setStyleSheet("background: rgba(255,255,255,0.10); border: none;")
        return div

    def _add_slider(self, layout, prefix, min_v, max_v, default, callback, width, full_name, display_func=None):
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        lbl = QLabel(prefix)
        lbl.setStyleSheet(
            f"font-size: {theme['font_size_normal']};"
            "font-weight: 500;"
            "letter-spacing: 0.5px;"
            f"color: rgba(180,190,210,0.7);"
            "background: transparent;"
        )
        layout.addWidget(lbl)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(default)
        slider.setFixedWidth(width)
        slider.setCursor(Qt.PointingHandCursor)
        display = display_func(default) if display_func else default
        slider.setToolTip(f"{full_name}: {display}")
        def on_value_changed(v):
            display_value = display_func(v) if display_func else v
            slider.setToolTip(f"{full_name}: {display_value}")
            callback(v)
        slider.valueChanged.connect(on_value_changed)
        slider.setStyleSheet("background: transparent;")
        layout.addWidget(slider)
        return slider

    def _btn_style(self):
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        return (
            "QPushButton {"
            "    background-color: rgba(42,45,56,0.88);"
            "    border: 1px solid rgba(255,255,255,0.10);"
            "    border-radius: 6px;"
            "    padding: 0px;"
            "}"
            f"QPushButton:checked {{"
            f"    background-color: rgba(80,140,255,0.24);"
            f"    border: 1px solid rgba(100,160,255,0.78);"
            f"}}"
            "QPushButton:hover:!checked {"
            f"    background-color: rgba(255,255,255,0.10);"
            "    border: 1px solid rgba(255,255,255,0.18);"
            "}"
            "QPushButton:pressed {"
            f"    background-color: rgba(80,140,255,0.25);"
            "}"
        )

    def _select_tool(self, tool: str):
        for t, btn in self.tool_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(t == tool)
            btn.blockSignals(False)
        self.tool_changed.emit(tool)

    def _pick_color(self):
        color = QColorDialog.getColor(Qt.white, self, "Brush Color")
        if color.isValid():
            self.color_btn.setStyleSheet(f"background-color: {color.name()}; border-radius: 10px; border: 1px solid #445566;")
            self.color_changed.emit(color)

    def _on_size(self, v): self.size_changed.emit(self._slider_value_to_size(v))

    def _show_onion_options(self, pos):
        """Abre el panel de configuración del Onion Skin encima del botón."""
        canvas = None
        p = self.parent()
        while p:
            if hasattr(p, 'canvas'):
                canvas = p.canvas
                break
            p = p.parent() if hasattr(p, 'parent') else None

        dialog = OnionSettingsDialog(canvas, self)
        dialog.adjustSize()

        # Calcular posición: encima del botón 🧅
        if self._onion_btn:
            btn_global = self._onion_btn.mapToGlobal(QPoint(0, 0))
            popup_x = btn_global.x() + self._onion_btn.width() // 2 - dialog.sizeHint().width() // 2
            popup_y = btn_global.y() - dialog.sizeHint().height() - 8
        else:
            popup_x = self.mapToGlobal(pos).x()
            popup_y = self.mapToGlobal(pos).y() - dialog.sizeHint().height() - 8

        dialog.move(popup_x, popup_y)
        execute_qt(dialog)

# ═══════════════════════════════════════════════════════════════════════
#  MÓDULO 5b: ONION SKIN SETTINGS DIALOG
# ═══════════════════════════════════════════════════════════════════════

class OnionSettingsDialog(QtWidgets.QDialog):
    """
    Panel flotante para configurar el Onion Skin:
      - Frames hacia atrás  (1–10)
      - Frames hacia adelante (1–10)
      - Opacidad máxima      (5–100 %)
      - Decaimiento por distancia: cada frame más alejado pierde
        opacidad proporcionalmente.
    Los cambios se aplican en tiempo real al canvas.
    """

    _STYLE = """
        QDialog {
            background-color: rgb(36, 38, 50);
            border: 1px solid rgba(0,0,0,0.7);
            border-radius: 12px;
        }
        QLabel {
            color: rgba(200,210,230,0.85);
            font-size: 11px;
            font-weight: 500;
            background: transparent;
        }
        QLabel#section {
            color: rgba(140,170,255,0.95);
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1.2px;
        }
        QSpinBox {
            background-color: rgba(255,255,255,0.07);
            color: #ffffff;
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 6px;
            padding: 2px 6px;
            font-size: 12px;
            min-width: 44px;
        }
        QSpinBox:hover {
            border: 1px solid rgba(100,160,255,0.4);
        }
        QSpinBox::up-button, QSpinBox::down-button {
            width: 16px;
            background: transparent;
            border: none;
        }
        QSlider::groove:horizontal {
            height: 3px;
            background: rgba(255,255,255,0.12);
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            width: 12px; height: 12px;
            margin: -5px 0;
            background: qradialgradient(cx:0.35,cy:0.35,radius:0.7,
                fx:0.35,fy:0.35,
                stop:0 rgba(255,255,255,0.95),
                stop:1 rgba(180,190,210,0.85));
            border-radius: 6px;
            border: 1px solid rgba(255,255,255,0.25);
        }
        QSlider::handle:horizontal:hover {
            background: qradialgradient(cx:0.35,cy:0.35,radius:0.7,
                fx:0.35,fy:0.35,
                stop:0 #ffffff,
                stop:1 rgba(160,200,255,0.95));
            border: 1px solid rgba(120,180,255,0.6);
        }
        QSlider::sub-page:horizontal {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(80,140,255,0.6),
                stop:1 rgba(140,200,255,0.8));
            border-radius: 2px;
        }
        QPushButton#close_btn {
            background-color: transparent;
            color: rgba(200,210,230,0.45);
            border: none;
            font-size: 14px;
            border-radius: 6px;
        }
        QPushButton#close_btn:hover {
            color: #ffffff;
            background-color: rgba(220,60,60,0.18);
        }
    """

    def __init__(self, canvas, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.canvas = canvas
        self.setStyleSheet(self._STYLE)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)

        # ── Header ──────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("ONION SKIN")
        title.setObjectName("section")
        header.addWidget(title)
        header.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("close_btn")
        close_btn.setFixedSize(20, 20)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        root.addLayout(header)

        # ── Separator ───────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background: rgba(255,255,255,0.07); border: none; max-height: 1px;")
        root.addWidget(sep)

        # ── Frames antes / después ──────────────────────────────
        frames_grid = QtWidgets.QGridLayout()
        frames_grid.setHorizontalSpacing(12)
        frames_grid.setVerticalSpacing(6)

        lbl_before = QLabel("Frames antes  ◀")
        lbl_after  = QLabel("▶  Frames después")
        frames_grid.addWidget(lbl_before, 0, 0)
        frames_grid.addWidget(lbl_after,  0, 1)

        canvas_before = getattr(self.canvas, 'onion_before', 2) if self.canvas else 2
        canvas_after  = getattr(self.canvas, 'onion_after',  1) if self.canvas else 1

        self.spin_before = QSpinBox()
        self.spin_before.setRange(0, 10)
        self.spin_before.setValue(canvas_before)
        self.spin_before.setToolTip("Frames hacia atrás visibles")

        self.spin_after = QSpinBox()
        self.spin_after.setRange(0, 10)
        self.spin_after.setValue(canvas_after)
        self.spin_after.setToolTip("Frames hacia adelante visibles")

        frames_grid.addWidget(self.spin_before, 1, 0)
        frames_grid.addWidget(self.spin_after,  1, 1)
        root.addLayout(frames_grid)

        # ── Opacidad máxima ──────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("background: rgba(255,255,255,0.07); border: none; max-height: 1px;")
        root.addWidget(sep2)

        opacity_lbl_row = QHBoxLayout()
        lbl_opacity = QLabel("Opacidad máxima")
        canvas_opacity = getattr(self.canvas, 'onion_opacity', 0.35) if self.canvas else 0.35
        self.opacity_val_lbl = QLabel(f"{int(canvas_opacity * 100)}%")
        self.opacity_val_lbl.setStyleSheet(
            "color: rgba(120,160,255,0.9); font-size: 11px; font-weight: 700; background: transparent;"
        )
        opacity_lbl_row.addWidget(lbl_opacity)
        opacity_lbl_row.addStretch()
        opacity_lbl_row.addWidget(self.opacity_val_lbl)
        root.addLayout(opacity_lbl_row)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.setValue(int(canvas_opacity * 100))
        self.opacity_slider.setFixedWidth(200)
        self.opacity_slider.setCursor(Qt.PointingHandCursor)
        root.addWidget(self.opacity_slider)

        # ── Decaimiento ──────────────────────────────────────────
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet("background: rgba(255,255,255,0.07); border: none; max-height: 1px;")
        root.addWidget(sep3)

        decay_lbl_row = QHBoxLayout()
        lbl_decay = QLabel("Decaimiento por frame")
        canvas_decay = getattr(self.canvas, 'onion_decay', 0.4) if self.canvas else 0.4
        self.decay_val_lbl = QLabel(f"{int(canvas_decay * 100)}%")
        self.decay_val_lbl.setStyleSheet(
            "color: rgba(120,160,255,0.9); font-size: 11px; font-weight: 700; background: transparent;"
        )
        decay_lbl_row.addWidget(lbl_decay)
        decay_lbl_row.addStretch()
        decay_lbl_row.addWidget(self.decay_val_lbl)
        root.addLayout(decay_lbl_row)

        hint = QLabel("Cuánto baja la opacidad por cada frame de distancia")
        hint.setStyleSheet(
            "color: rgba(160,170,190,0.45); font-size: 10px; background: transparent;"
        )
        root.addWidget(hint)

        self.decay_slider = QSlider(Qt.Horizontal)
        self.decay_slider.setRange(0, 90)          # 0% = sin decaimiento, 90% = muy rápido
        self.decay_slider.setValue(int(canvas_decay * 100))
        self.decay_slider.setFixedWidth(200)
        self.decay_slider.setCursor(Qt.PointingHandCursor)
        root.addWidget(self.decay_slider)

        # ── Conectar señales ────────────────────────────────────
        self.spin_before.valueChanged.connect(self._apply)
        self.spin_after.valueChanged.connect(self._apply)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.decay_slider.valueChanged.connect(self._on_decay_changed)

        self.setFixedWidth(240)

    # ── Handlers ────────────────────────────────────────────────
    def _apply(self):
        if not self.canvas:
            return
        self.canvas.onion_before = self.spin_before.value()
        self.canvas.onion_after  = self.spin_after.value()
        self.canvas._cache_dirty = True
        self.canvas.update()

    def _on_opacity_changed(self, v):
        self.opacity_val_lbl.setText(f"{v}%")
        if self.canvas:
            self.canvas.onion_opacity = v / 100.0
            self.canvas._cache_dirty = True
            self.canvas.update()

    def _on_decay_changed(self, v):
        self.decay_val_lbl.setText(f"{v}%")
        if self.canvas:
            self.canvas.onion_decay = v / 100.0
            self.canvas._cache_dirty = True
            self.canvas.update()


# ═══════════════════════════════════════════════════════════════════════
#  MÓDULO 6: VENTANA PRINCIPAL Y OVERLAY
# ═══════════════════════════════════════════════════════════════════════

def get_maya_main_window():
    main_window_ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(main_window_ptr), QtWidgets.QMainWindow)

def get_active_model_panel():
    panel = cmds.getPanel(withFocus=True)
    if panel and "modelPanel" in panel:
        return panel
    panels = cmds.getPanel(type="modelPanel") or []
    for candidate in panels:
        try:
            if cmds.modelPanel(candidate, exists=True):
                return candidate
        except Exception:
            continue
    return panels[0] if panels else None

def get_active_model_panel_widget():
    panel = get_active_model_panel()
    if not panel:
        return None

    try:
        view = omui.M3dView()
        omui.M3dView.getM3dViewFromModelEditor(panel, view)
        view_ptr = view.widget()
        if view_ptr is not None:
            widget = wrapInstance(int(view_ptr), QtWidgets.QWidget)
            if widget:
                return widget
    except Exception:
        pass

    ptr = omui.MQtUtil.findControl(panel)
    if ptr is not None:
        widget = wrapInstance(int(ptr), QtWidgets.QWidget)
        # Using the base modelPanel widget guarantees the layout perfectly
        # matches the entire viewport split region in Maya 2024+
        if widget:
            return widget
    return None

class SketchboardWindow(QWidget):
    _instance = None

    def __init__(self, overlay=True, parent=None):
        vp_widget = None
        self.model_panel = get_active_model_panel() if overlay else None
        if overlay:
            vp_widget = get_active_model_panel_widget()
            
        maya_main = get_maya_main_window()
        super(SketchboardWindow, self).__init__(parent=maya_main, f=Qt.Window)
        self.setObjectName("AnimKey_Sketchboard")
        
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)

        if overlay and vp_widget:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.is_overlay = True
            self.viewport_parent = vp_widget
            self.panel_locked = False
        else:
            self.setWindowFlags(Qt.Window)
            self.setWindowTitle("🎨 Viewport Sketchboard v3.0")
            self.setMinimumSize(1000, 650)
            self.resize(1280, 780)
            self.setStyleSheet("background-color: #0F1218; color: #AABBCC;")
            self.is_overlay = False
            self.panel_locked = False

        self._brush_overlay_enabled = True
        self._sidebar_collapsed = False
        self._sidebar_anim = None
        self._sidebar_opacity_effect = None
        self._sidebar_full_height = 0
        self._hud_width_hint = 0
        self._tablet_event_filter_installed = False
        self._last_polled_frame = None
        self._timeline_keys_dirty = False
        self._scene_commit_timer = QTimer(self)
        self._scene_commit_timer.setSingleShot(True)
        self._scene_commit_timer.setInterval(350)
        self._scene_commit_timer.timeout.connect(self._flush_deferred_scene_commit)

        self._build_ui()
        self._connect_signals()
        self._load_scene_session()
        self._sync_timeline()

        if self.is_overlay:
            self._install_tablet_event_filter()
        
        if self.is_overlay and self.viewport_parent:
            self.sync_timer = QTimer(self)
            self.sync_timer.timeout.connect(self._sync_geometry)
            self.sync_timer.timeout.connect(self._poll_current_frame)
            # 30 Hz is enough for viewport/frame synchronization and leaves
            # more main-thread time for high-frequency tablet events.
            self.sync_timer.start(33)
            self._sync_geometry()

    def _install_tablet_event_filter(self):
        """Capture pen events that Windows routes to Maya's model panel."""
        if self._tablet_event_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._tablet_event_filter_installed = True

    def _remove_tablet_event_filter(self):
        if not self._tablet_event_filter_installed:
            return
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
        except (RuntimeError, AttributeError):
            pass
        self._tablet_event_filter_installed = False

    def _is_canvas_widget(self, widget):
        """Whether *widget* is the canvas or one of its children."""
        while widget is not None:
            if widget is self.canvas:
                return True
            if widget is self:
                return False
            try:
                widget = widget.parentWidget()
            except (AttributeError, RuntimeError):
                return False
        return False

    def eventFilter(self, watched, event):
        """Route off-target tablet events into the visible drawing canvas.

        The event filter deliberately leaves the canvas' own events alone and
        only redirects events landing in an unobstructed canvas area.  Toolbar
        controls and Navigation mode retain their normal Maya/Qt behavior.
        """
        tablet_events = (
            int(QtCore.QEvent.TabletPress),
            int(QtCore.QEvent.TabletMove),
            int(QtCore.QEvent.TabletRelease),
        )
        try:
            is_tablet_event = int(event.type()) in tablet_events
        except Exception:
            is_tablet_event = False

        if (
            is_tablet_event
            and self.is_overlay
            and self.isVisible()
            and getattr(self, "_brush_overlay_enabled", True)
            and not self.canvas.testAttribute(Qt.WA_TransparentForMouseEvents)
            and not self._is_canvas_widget(watched)
        ):
            global_pos = get_event_global_pos(event)
            if global_pos is not None:
                window_pos = self.mapFromGlobal(QPoint(
                    int(round(global_pos.x())), int(round(global_pos.y()))
                ))
                target = self.childAt(window_pos)
                if target is not None and self._is_canvas_widget(target):
                    if self.canvas.handle_external_tablet_event(event, global_pos):
                        event.accept()
                        return True

        return super(SketchboardWindow, self).eventFilter(watched, event)

    def _poll_current_frame(self):
        """
        Sondeo activo del frame actual de Maya en cada tick (~16ms).
        cmds.scriptJob(event=["timeChanged", ...]) no es confiable durante
        cmds.play() en tiempo real (el Evaluation Manager puede saltarse o
        retrasar el disparo del scriptJob), así que el polling garantiza que
        los trazos se actualicen frame a frame mientras se reproduce.
        """
        if not getattr(self, "_brush_overlay_enabled", True) or not self.isVisible():
            return
        if hasattr(self, "canvas") and self.canvas.drawing:
            return
        try:
            curr = int(round(cmds.currentTime(query=True)))
            is_playing = bool(cmds.play(query=True, state=True))
        except Exception:
            return
        self.canvas.set_playback_mode(is_playing)
        if curr == self._last_polled_frame:
            return
        self._last_polled_frame = curr
        self.canvas.set_frame(curr, force_repaint=is_playing)
        fd = self.canvas.data.frames.get(curr)
        self.status_bar.setText(f"Frame: {curr} | Strokes: {len(fd.strokes) if fd else 0}")

    def _sync_geometry(self):
        if not getattr(self, "_brush_overlay_enabled", True):
            return
        # Do not run model-panel discovery or force an extra canvas repaint in
        # the middle of a stroke. Mouse/tablet events already repaint it.
        if hasattr(self, "canvas") and self.canvas.drawing:
            return

        # Actualización dinámica del target en caso de que Maya rearme sus docks
        if not self.panel_locked:
            try:
                new_panel = get_active_model_panel()
                new_vp = get_active_model_panel_widget()
                
                # Limpiar safely si el viejo objeto de C++ fue destruido por Maya
                if self.viewport_parent:
                    try:
                        self.viewport_parent.objectName()
                    except RuntimeError:
                        self.viewport_parent = None

                if new_vp and new_vp != self.viewport_parent:
                    self.viewport_parent = new_vp
                    self.model_panel = new_panel
                    if hasattr(self, "canvas"):
                        self.canvas.model_panel = new_panel
            except Exception:
                pass
        else:
            # Aunque esté lockeado, verificar que el widget siga vivo
            if self.viewport_parent:
                try:
                    self.viewport_parent.objectName()
                except RuntimeError:
                    self.viewport_parent = None

        if not self.viewport_parent:
            if self.isVisible(): self.hide()
            return

        try:
            if not self.viewport_parent.isVisible():
                if self.isVisible(): self.hide()
                return
            else:
                if not self.isVisible(): self.show()
        except RuntimeError:
            self.viewport_parent = None
            return

        try:
            global_pos = self.viewport_parent.mapToGlobal(QPoint(0, 0))
            w, h = self.viewport_parent.width(), self.viewport_parent.height()
            
            if self.x() != global_pos.x() or self.y() != global_pos.y() or self.width() != w or self.height() != h:
                self.setGeometry(global_pos.x(), global_pos.y(), w, h)
            if hasattr(self, "canvas"):
                self.canvas.update()
        except RuntimeError:
            self.viewport_parent = None

    def set_overlay_enabled(self, enabled):
        self._brush_overlay_enabled = bool(enabled)
        if not self._brush_overlay_enabled:
            self.hide()
            return

        if self.is_overlay:
            self._sync_geometry()
            if not self.viewport_parent:
                return
        self.show()
        self.raise_()
        self.canvas.update()

    def toggle_overlay_enabled(self):
        should_show = not (getattr(self, "_brush_overlay_enabled", True) and self.isVisible())
        self.set_overlay_enabled(should_show)

    def toggle_passthrough(self, checked):
        self.canvas.pass_through_mode = checked
        if checked:
            # Modo Navegación — el canvas ignora clicks, la toolbar sigue activa
            self.canvas.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.toggle_mode_btn.setText("👁️ NAV")
        else:
            # Modo Dibujo
            self.canvas.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.toggle_mode_btn.setText("🖌️ DRAW")
        apply_brush_button_icon(self.toggle_mode_btn, "toggle", "D", icon_size=23)
        self.canvas.update()

    def toggle_dim(self, checked):
        self.canvas.dim_background = checked
        self.canvas.update()

    def toggle_lock(self, checked):
        self.panel_locked = checked
        if checked:
            self.lock_btn.setText("🔒")
            self.lock_btn.setToolTip("Panel LOCKED - Click to unlock")
        else:
            self.lock_btn.setText("🔓")
            self.lock_btn.setToolTip("Panel FREE - Click to lock in this viewport")

        apply_brush_button_icon(self.lock_btn, "lock", "L", icon_size=23)

    def toggle_sidebar(self):
        self._set_sidebar_collapsed(not self._sidebar_collapsed, animate=True)

    def _ensure_sidebar_metrics(self):
        if not hasattr(self, "sidebar_widget") or not hasattr(self, "hud_wrapper"):
            return
        try:
            if self.sidebar_widget.layout():
                self.sidebar_widget.layout().activate()
            if self.hud_wrapper.layout():
                self.hud_wrapper.layout().activate()
        except Exception:
            pass

        sidebar_hint = self.sidebar_widget.sizeHint()
        hud_hint = self.hud_wrapper.sizeHint()
        self._sidebar_full_height = max(
            int(getattr(self, "_sidebar_full_height", 0) or 0),
            int(sidebar_hint.height()),
            int(self.sidebar_widget.height()),
            40,
        )
        self._hud_width_hint = max(
            int(getattr(self, "_hud_width_hint", 0) or 0),
            int(hud_hint.width()),
            int(self.hud_wrapper.width()),
        )

    def _position_floating_hud(self):
        if not hasattr(self, "hud_wrapper") or not hasattr(self, "status_bar_wrapper"):
            return

        self._ensure_sidebar_metrics()
        W, H = self.width(), self.height()

        if self.hud_wrapper.layout():
            try:
                self.hud_wrapper.layout().activate()
            except Exception:
                pass

        hud_w = max(self._hud_width_hint, self.hud_wrapper.sizeHint().width())
        hud_h = max(1, self.hud_wrapper.sizeHint().height())
        hud_x = int((W - hud_w) / 2)
        hud_y = H - hud_h - 30 - 5
        self.hud_wrapper.setGeometry(hud_x, hud_y, hud_w, hud_h)

        sb_w = self.status_bar_wrapper.sizeHint().width()
        sb_h = self.status_bar_wrapper.sizeHint().height()
        sb_x = int((W - sb_w) / 2)
        sb_y = H - sb_h - 6
        self.status_bar_wrapper.setGeometry(sb_x, sb_y, sb_w, sb_h)

    def _set_sidebar_collapsed(self, collapsed, animate=True):
        if not hasattr(self, "sidebar_widget") or not hasattr(self, "sidebar_toggle_btn"):
            return

        self._ensure_sidebar_metrics()
        self._sidebar_collapsed = bool(collapsed)
        self.sidebar_toggle_btn.setText("\u25b2" if collapsed else "\u25bc")
        self.sidebar_toggle_btn.setToolTip("Expand toolbar" if collapsed else "Hide toolbar")
        apply_brush_button_icon(self.sidebar_toggle_btn, "bar", "-", icon_size=12)

        old_anim = getattr(self, "_sidebar_anim", None)
        if old_anim is not None:
            try:
                old_anim.stop()
            except Exception:
                pass

        effect = getattr(self, "_sidebar_opacity_effect", None)
        if not isinstance(effect, QtWidgets.QGraphicsOpacityEffect):
            effect = QtWidgets.QGraphicsOpacityEffect(self.sidebar_widget)
            self.sidebar_widget.setGraphicsEffect(effect)
            self._sidebar_opacity_effect = effect

        full_h = max(1, int(self._sidebar_full_height))
        current_h = self.sidebar_widget.maximumHeight()
        if collapsed:
            if current_h <= 0 or current_h > 10000:
                current_h = self.sidebar_widget.height() if self.sidebar_widget.height() > 0 else full_h
        else:
            if current_h <= 0 or current_h > 10000:
                current_h = 0

        self.sidebar_widget.setVisible(True)
        self.sidebar_widget.setMinimumHeight(0)

        if not animate:
            self.sidebar_widget.setMaximumHeight(0 if collapsed else full_h)
            effect.setOpacity(0.0 if collapsed else 1.0)
            self._position_floating_hud()
            return

        group = QtCore.QParallelAnimationGroup(self)
        height_anim = QPropertyAnimation(self.sidebar_widget, b"maximumHeight")
        opacity_anim = QPropertyAnimation(effect, b"opacity")

        height_anim.setDuration(280)
        height_anim.setStartValue(current_h)
        height_anim.setEndValue(0 if collapsed else full_h)
        height_anim.setEasingCurve(QEasingCurve.OutCubic)
        height_anim.valueChanged.connect(lambda *_: self._position_floating_hud())

        opacity_anim.setDuration(180 if collapsed else 220)
        opacity_anim.setStartValue(effect.opacity())
        opacity_anim.setEndValue(0.0 if collapsed else 1.0)
        opacity_anim.setEasingCurve(QEasingCurve.OutCubic)

        def finish():
            if self._sidebar_collapsed:
                self.sidebar_widget.setMaximumHeight(0)
                effect.setOpacity(0.0)
            else:
                self.sidebar_widget.setMaximumHeight(full_h)
                effect.setOpacity(1.0)
            self._position_floating_hud()

        group.addAnimation(height_anim)
        group.addAnimation(opacity_anim)
        group.finished.connect(finish)
        self._sidebar_anim = group
        group.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_floating_hud()

    def _build_ui(self):
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()

        # Layout: el canvas ocupa el 100% de la ventana
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        self.canvas = DrawingCanvas()
        self.canvas.model_panel = self.model_panel
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout.addWidget(self.canvas, 1)

        self.timeline = MayaTimelineSync(self.canvas.data)

        # ── Status bar flotante ──────────────────────────────────
        self.status_bar_wrapper = QWidget(self)
        sb_layout = QHBoxLayout(self.status_bar_wrapper)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        
        self.status_bar = QLabel("Frame: 1 | Strokes: 0")
        self.status_bar.setAlignment(Qt.AlignCenter)
        self.status_bar.setStyleSheet(
            "background-color: rgba(22,24,32,0.82);"
            "color: rgba(200,210,230,0.9);"
            f"font-family: {theme['font_family']};"
            f"font-size: {theme['font_size_normal']};"
            "padding: 3px 14px;"
            "border-radius: 10px;"
            "border: 1px solid rgba(0,0,0,0.6);"
            "font-weight: 500;"
        )
        sb_layout.addWidget(self.status_bar)

        # ── HUD Overlay Flotante ─────────────────────────────────
        self.hud_wrapper = QWidget(self)
        hud_v_layout = QVBoxLayout(self.hud_wrapper)
        hud_v_layout.setContentsMargins(0, 0, 0, 0)
        hud_v_layout.setSpacing(0)

        # ── Una sola barra contenedora ───────────────────────────
        self.sidebar_widget = QFrame()
        self.sidebar_widget.setObjectName("main_toolbar")
        self.sidebar_widget.setStyleSheet(
            "QFrame#main_toolbar {"
            "    background-color: rgba(30,32,42,0.78);"
            "    border-radius: 14px;"
            "    border: 1px solid rgba(0,0,0,0.65);"
            "}"
        )
        bar_layout = QHBoxLayout(self.sidebar_widget)
        bar_layout.setSpacing(6)
        bar_layout.setContentsMargins(10, 6, 10, 6)

        # ── ToolPanel (sin fondo propio, vive dentro de la barra) ─
        self.tool_panel = ToolPanel()
        self.tool_panel.setAutoFillBackground(False)
        self.tool_panel.setStyleSheet("QWidget { background-color: transparent; }")
        bar_layout.addWidget(self.tool_panel)

        # ── Botones exclusivos del modo overlay ──────────────────
        if self.is_overlay:
            # Divisor
            div = QFrame()
            div.setFrameShape(QFrame.VLine)
            div.setFixedWidth(1)
            div.setFixedHeight(22)
            div.setStyleSheet("background: rgba(255,255,255,0.10); border: none;")
            bar_layout.addWidget(div)

            overlay_btn_style = (
                "QPushButton {"
                "    background-color: rgba(42,45,56,0.88);"
                "    color: rgba(200,210,230,0.85);"
                "    border-radius: 7px;"
                "    border: 1px solid rgba(255,255,255,0.10);"
                "    font-size: 11px;"
                "    font-weight: 600;"
                "    padding: 0px;"
                "}"
                "QPushButton:hover {"
                "    background-color: rgba(255,255,255,0.10);"
                "    border: 1px solid rgba(255,255,255,0.18);"
                "}"
                "QPushButton:checked {"
                "    background-color: rgba(80,140,255,0.24);"
                "    border: 1px solid rgba(100,160,255,0.78);"
                "}"
                "QPushButton:pressed {"
                "    background-color: rgba(80,140,255,0.25);"
                "}"
            )

            self.toggle_mode_btn = QPushButton("🖌️ DRAW")
            self.toggle_mode_btn.setCheckable(True)
            self.toggle_mode_btn.setChecked(False)
            self.toggle_mode_btn.setFixedSize(34, 28)
            self.toggle_mode_btn.setCursor(Qt.PointingHandCursor)
            self.toggle_mode_btn.setToolTip("Toggle Draw/Navigation Mode")
            apply_brush_button_icon(self.toggle_mode_btn, "toggle", "D", icon_size=23)
            self.toggle_mode_btn.setStyleSheet(overlay_btn_style)
            self.toggle_mode_btn.toggled.connect(self.toggle_passthrough)
            bar_layout.addWidget(self.toggle_mode_btn)

            self.dim_bg_btn = QCheckBox("Dim")
            self.dim_bg_btn.setCursor(Qt.PointingHandCursor)
            self.dim_bg_btn.setToolTip("Dim viewport for better trace visibility")
            self.dim_bg_btn.setStyleSheet(
                "QCheckBox {"
                "    color: rgba(200,210,230,0.7);"
                f"   font-size: {theme['font_size_normal']};"
                "    font-weight: 600;"
                "    background: transparent;"
                "    spacing: 5px;"
                "}"
                "QCheckBox::indicator {"
                "    width: 13px; height: 13px;"
                "    border-radius: 4px;"
                "    border: 1px solid rgba(255,255,255,0.18);"
                "    background: rgba(255,255,255,0.05);"
                "}"
                "QCheckBox::indicator:checked {"
                "    background: rgba(80,140,255,0.7);"
                "    border: 1px solid rgba(100,160,255,0.6);"
                "}"
            )
            self.dim_bg_btn.toggled.connect(self.toggle_dim)
            bar_layout.addWidget(self.dim_bg_btn)

            self.lock_btn = QPushButton("🔓")
            self.lock_btn.setFixedSize(28, 28)
            self.lock_btn.setCheckable(True)
            self.lock_btn.setChecked(False)
            self.lock_btn.setCursor(Qt.PointingHandCursor)
            self.lock_btn.setToolTip("Panel FREE - Click to lock in this viewport")
            apply_brush_button_icon(self.lock_btn, "lock", "L", icon_size=23)
            self.lock_btn.setStyleSheet(overlay_btn_style + "QPushButton { padding: 0; }")
            self.lock_btn.toggled.connect(self.toggle_lock)
            bar_layout.addWidget(self.lock_btn)

            # Divisor antes de cerrar
            div2 = QFrame()
            div2.setFrameShape(QFrame.VLine)
            div2.setFixedWidth(1)
            div2.setFixedHeight(22)
            div2.setStyleSheet("background: rgba(255,255,255,0.10); border: none;")
            bar_layout.addWidget(div2)

            self.close_overlay_btn = QPushButton("✕")
            self.close_overlay_btn.setFixedSize(28, 28)
            self.close_overlay_btn.setCursor(Qt.PointingHandCursor)
            self.close_overlay_btn.setToolTip("Close Board")
            apply_brush_button_icon(self.close_overlay_btn, "delete", "X", icon_size=23)
            self.close_overlay_btn.setStyleSheet(
                "QPushButton {"
                "    background-color: transparent;"
                "    color: rgba(220,80,80,0.8);"
                "    border-radius: 7px;"
                "    border: 1px solid transparent;"
                "    font-size: 12px; font-weight: 700;"
                "}"
                "QPushButton:hover {"
                "    background-color: rgba(220,60,60,0.18);"
                "    border: 1px solid rgba(220,80,80,0.35);"
                "    color: rgba(255,100,100,1.0);"
                "}"
            )
            self.close_overlay_btn.clicked.connect(self.close)
            bar_layout.addWidget(self.close_overlay_btn)

        hud_v_layout.addWidget(self.sidebar_widget)

        # ── Pestaña colapsar/expandir ────────────────────────────
        self.sidebar_toggle_btn = QPushButton("▼")
        self.sidebar_toggle_btn.setFixedSize(40, 14)
        self.sidebar_toggle_btn.setCursor(Qt.PointingHandCursor)
        apply_brush_button_icon(self.sidebar_toggle_btn, "bar", "-", icon_size=12)
        self.sidebar_toggle_btn.setStyleSheet(
            "QPushButton {"
            "    background-color: rgba(30,32,42,0.78);"
            "    color: rgba(180,190,210,0.6);"
            "    border-bottom-left-radius: 7px;"
            "    border-bottom-right-radius: 7px;"
            "    font-size: 8px; font-weight: bold;"
            "    border: 1px solid rgba(0,0,0,0.65);"
            "    border-top: none;"
            "}"
            "QPushButton:hover {"
            "    color: rgba(200,220,255,0.9);"
            "}"
        )
        self.sidebar_toggle_btn.clicked.connect(self.toggle_sidebar)

        toggle_wrapper = QHBoxLayout()
        toggle_wrapper.setContentsMargins(0, 0, 0, 0)
        toggle_wrapper.addStretch()
        toggle_wrapper.addWidget(self.sidebar_toggle_btn)
        toggle_wrapper.addStretch()

        toggle_container = QWidget()
        toggle_container.setLayout(toggle_wrapper)
        hud_v_layout.addWidget(toggle_container)

    def _connect_signals(self):
        tp = self.tool_panel
        c = self.canvas

        tp.tool_changed.connect(self._on_tool_changed)
        tp.color_changed.connect(lambda col: setattr(c, 'brush_color', col))
        tp.size_changed.connect(lambda v: setattr(c, 'brush_size', v))
        tp.onion_toggled.connect(lambda v: (setattr(c, 'onion_enabled', v), c.update()))
        tp.undo_requested.connect(self._on_undo_requested)
        tp.clear_requested.connect(self._on_clear_requested)
        tp.save_requested.connect(self._save_session)
        tp.load_requested.connect(self._load_session)
        tp.playblast_requested.connect(self._export_playblast_with_strokes)

        c.stroke_added.connect(self._on_stroke_added)
        self.timeline.frame_changed.connect(self._on_frame_changed)

    def _on_tool_changed(self, tool):
        self.canvas.tool = tool
        if tool in ("transform", "free_transform"):
            self.canvas.activate_transform(select_all_if_empty=False)
            if tool == "free_transform":
                self.status_bar.setText("Free Transform: move, warp corners, scale edges, drag stroke points.")
            else:
                self.status_bar.setText("Transform: move, rotate, scale sides, scale corners proportionally.")
        elif tool == "lasso":
            self.canvas.lasso_active = False
            self.canvas._lasso_points = []
            self.canvas.transform_active = bool(self.canvas.selected_strokes)
            self.canvas.update()
            self.status_bar.setText("Lasso: drag around strokes. Shift adds, Ctrl removes.")
        else:
            self.canvas.deactivate_transform()

    def _on_undo_requested(self):
        if self.canvas.undo_last_transform():
            self._schedule_scene_commit(update_timeline=False)
            self.status_bar.setText(f"Frame: {self.canvas.current_frame} | Transform undo")
            return
        self.canvas.undo_stroke()
        self._schedule_scene_commit(update_timeline=True)
        self._on_frame_changed(self.canvas.current_frame)

    def _on_clear_requested(self):
        self.canvas.clear_current_frame()
        self._schedule_scene_commit(update_timeline=True)
        self._on_frame_changed(self.canvas.current_frame)

    def _on_stroke_added(self, frame):
        fd = self.canvas.data.frames.get(frame)
        # Timeline ticks only change when the first stroke is added to a frame.
        # Scene serialization is debounced so several quick strokes do not
        # repeatedly encode the entire board between mouse releases.
        self._schedule_scene_commit(
            update_timeline=bool(fd and len(fd.strokes) == 1)
        )
        self.status_bar.setText(f"Frame: {frame} | Strokes: {len(fd.strokes) if fd else 0}")

    def _on_frame_changed(self, frame):
        is_playing = False
        try:
            is_playing = bool(cmds.play(query=True, state=True))
        except Exception:
            is_playing = False
        self.canvas.set_playback_mode(is_playing)
        frame = int(frame)
        if frame == self.canvas.current_frame:
            return
        # Keep the polling fallback in sync with the scriptJob so the same
        # playback frame is never rasterized twice.
        self._last_polled_frame = frame
        self.canvas.set_frame(frame, force_repaint=is_playing)
        fd = self.canvas.data.frames.get(frame)
        self.status_bar.setText(f"Frame: {frame} | Strokes: {len(fd.strokes) if fd else 0}")

    def _sync_timeline(self):
        try:
            curr = int(cmds.currentTime(query=True))
            self.canvas.set_frame(curr)
        except:
            pass
        self.timeline.update_keys()

    def _save_scene_session(self):
        save_brush_data_to_scene(self.canvas.data)

    def _schedule_scene_commit(self, update_timeline=False):
        self._timeline_keys_dirty = (
            self._timeline_keys_dirty or bool(update_timeline)
        )
        self._scene_commit_timer.start()

    def _flush_deferred_scene_commit(self):
        # Never serialize a growing board while tablet events are arriving.
        if hasattr(self, "canvas") and self.canvas.drawing:
            self._scene_commit_timer.start()
            return
        if self._timeline_keys_dirty:
            self.timeline.update_keys()
            self._timeline_keys_dirty = False
        self._save_scene_session()

    def _load_scene_session(self):
        data = load_brush_data_from_scene(self.canvas.width(), self.canvas.height())
        if data is None:
            return False
        self.canvas.data = data
        self.canvas._invalidate_frame_render_cache()
        self.timeline.data_ref = self.canvas.data
        self.canvas.set_frame(self.canvas.current_frame)
        self.timeline.update_keys()
        frame_count = len(self.canvas.data.get_frame_numbers())
        self.status_bar.setText(f"Loaded scene drawings: {frame_count} frame(s)")
        return True

    def _save_session(self):
        filters = (
            "JSON (*.json);;"
            "Blue Pencil ZIP (*.zip)"
        )
        path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Sketchboard", "", filters
        )
        if not path:
            return

        if path.lower().endswith('.zip') or 'Blue Pencil' in selected_filter:
            self._export_blue_pencil_zip(path)
        else:
            self._export_json(path)

    def _export_json(self, path):
        """Exporta como JSON legible."""
        data = self.canvas.data.to_dict()
        atomic_write_json(path, data, indent=2, ensure_ascii=False)
        self._save_scene_session()
        self.status_bar.setText(f"💾 Saved: {os.path.basename(path)}")

    def _export_blue_pencil_zip(self, path):
        """Exporta en formato Blue Pencil: ZIP con PNGs transparentes + XML."""
        data = self.canvas.data
        frame_nums = data.get_frame_numbers()
        if not frame_nums:
            self.status_bar.setText("❌ No drawings to export.")
            return

        # Obtener nombre de cámara activa
        cam_name = "persp"
        try:
            panel = cmds.getPanel(withFocus=True) or ""
            if "modelPanel" not in panel:
                panels = cmds.getPanel(type="modelPanel") or []
                panel = panels[0] if panels else None
            if panel:
                cam = cmds.modelPanel(panel, q=True, camera=True)
                if cam:
                    cam_name = cam
        except Exception:
            pass

        # Resolución de render
        render_w = data.canvas_width or 1920
        render_h = data.canvas_height or 1080

        self.status_bar.setText("⏳ Exporting Blue Pencil ZIP...")
        QtWidgets.QApplication.processEvents()

        base_name = os.path.splitext(os.path.basename(path))[0]

        # También incluir el JSON completo para poder re-importar con presión
        full_json = json.dumps(data.to_dict(), ensure_ascii=False, separators=(',', ':'))

        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            xml_lines = [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<bluePencil>',
                f'    <camera name="{cam_name}">',
                '        <layer name="Layer" isStatic="false" isActive="true" isLocked="false" isVisible="true">',
            ]

            for frame in frame_nums:
                png_name = f"{cam_name}_Layer_{frame}.png"
                img = self.canvas.render_frame_to_image(frame, render_w, render_h)

                # QImage → bytes PNG en memoria
                buf = QtCore.QBuffer()
                buf.open(QtCore.QIODevice.WriteOnly)
                img.save(buf, "PNG")
                png_bytes = buf.data().data()
                buf.close()

                zf.writestr(f"{base_name}/{png_name}", png_bytes)
                xml_lines.append(f'            <frame file="{png_name}" time="{frame}"/>')

            xml_lines.append('        </layer>')
            xml_lines.append('    </camera>')
            xml_lines.append('</bluePencil>')
            xml_lines.append('')

            zf.writestr(f"{base_name}/{base_name}.xml", '\n'.join(xml_lines))
            # Incluir datos completos para re-importación
            zf.writestr(f"{base_name}/sketchboard.json", full_json)

        total = len(frame_nums)
        self._save_scene_session()
        self.status_bar.setText(f"✅ Blue Pencil Exported: {total} frames → {os.path.basename(path)}")

        # Ask to import to Blue Pencil instantly
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Import to Blue Pencil")
        msg.setText(f"Would you like to import {os.path.basename(path)} into Blue Pencil right now?")
        msg.setStyleSheet(
            "QMessageBox { background-color: #2b2b2b; color: #ffffff; }"
            "QLabel { color: #ffffff; font-size: 13px; }"
            "QPushButton { background-color: #444444; color: #ffffff; padding: 5px 15px; border: 1px solid #555; border-radius: 3px; min-width: 60px; }"
            "QPushButton:hover { background-color: #555555; }"
            "QPushButton:pressed { background-color: #333333; }"
        )
        msg.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        msg.setDefaultButton(QtWidgets.QMessageBox.Yes)
        
        reply = execute_qt(msg)
        if reply == QtWidgets.QMessageBox.Yes:
            try:
                import maya.cmds as cmds
                cmds.bluePencilFrame(importArchive=path)
                self.status_bar.setText(f"✅ Successfully Imported to Blue Pencil.")
            except Exception as e:
                self.status_bar.setText(f"❌ Failed to import: {e}")

    def _export_playblast_with_strokes(self):
        """
        Genera un playblast del viewport y compone encima, frame a frame,
        los trazos del Sketchboard (render_frame_to_image), ya que un
        playblast nativo de Maya captura el framebuffer OpenGL del viewport
        y NO incluye widgets Qt flotantes como este overlay.
        """
        filters = "Video (*.mp4 *.mov);;PNG Sequence Folder (*)"
        out_path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Playblast with Strokes", "", filters
        )
        if not out_path:
            return

        out_path, want_video, out_dir, base_name = _normalize_playblast_output_path(out_path, selected_filter)
        if want_video:
            os.makedirs(out_dir, exist_ok=True)

        try:
            start = int(cmds.playbackOptions(query=True, minTime=True))
            end = int(cmds.playbackOptions(query=True, maxTime=True))
        except Exception:
            self.status_bar.setText("❌ Could not read Maya playback range.")
            return

        viewport_widget = get_active_model_panel_widget()
        if viewport_widget is None:
            self.status_bar.setText("No active Maya viewport found.")
            return

        try:
            original_time = cmds.currentTime(query=True)
        except Exception:
            original_time = None

        tmp_dir = tempfile.mkdtemp(prefix="sketchboard_playblast_")
        if want_video:
            comp_dir = os.path.join(tmp_dir, "composited")
        else:
            comp_dir = out_path if not out_path.lower().endswith((".png", ".mp4", ".mov")) else out_dir
        os.makedirs(comp_dir, exist_ok=True)

        frames_written = 0
        total_frames = max(1, end - start + 1)
        self.status_bar.setText("Capturing viewport and brush strokes...")
        QtWidgets.QApplication.processEvents()

        try:
            for frame_num in range(start, end + 1):
                try:
                    cmds.currentTime(frame_num, edit=True, update=True)
                except TypeError:
                    cmds.currentTime(frame_num, edit=True)

                try:
                    cmds.refresh(force=True, currentView=True)
                except TypeError:
                    cmds.refresh(force=True)
                QtWidgets.QApplication.processEvents()

                try:
                    viewport_pixmap = viewport_widget.grab()
                except RuntimeError:
                    viewport_widget = get_active_model_panel_widget()
                    if viewport_widget is None:
                        raise RuntimeError("The active Maya viewport was closed during export.")
                    viewport_pixmap = viewport_widget.grab()

                if viewport_pixmap.isNull():
                    viewport_widget = get_active_model_panel_widget()
                    if viewport_widget is not None:
                        viewport_pixmap = viewport_widget.grab()
                if viewport_pixmap.isNull():
                    continue

                base_img = viewport_pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
                stroke_img = self.canvas.render_frame_to_image(
                    frame_num,
                    base_img.width(),
                    base_img.height(),
                )

                painter = QPainter(base_img)
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.drawImage(0, 0, stroke_img)
                painter.end()

                frames_written += 1
                output_frame = frames_written if want_video else frame_num
                out_frame_name = f"{base_name}.{output_frame:04d}.png"
                if not base_img.save(os.path.join(comp_dir, out_frame_name), "PNG"):
                    frames_written -= 1
                    continue

                if frames_written == 1 or frames_written % 5 == 0 or frame_num == end:
                    self.status_bar.setText(
                        f"Capturing frame {frame_num}/{end} ({frames_written}/{total_frames})..."
                    )
                    QtWidgets.QApplication.processEvents()
        except Exception as e:
            self.status_bar.setText(f"Viewport playblast failed: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return
        finally:
            if original_time is not None:
                try:
                    cmds.currentTime(original_time, edit=True, update=True)
                except TypeError:
                    cmds.currentTime(original_time, edit=True)
                except Exception:
                    pass

        if frames_written <= 0:
            self.status_bar.setText("Viewport capture produced no frames.")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        if not want_video:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.status_bar.setText(f"Playblast PNG sequence exported to: {comp_dir}")
            return

        ffmpeg_path = resolve_ffmpeg_path(status_callback=self.status_bar.setText)
        if not ffmpeg_path:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.status_bar.setText(
                "FFmpeg not found. Put ffmpeg.exe in AnimKey/data/ffmpeg/bin, "
                "or export as PNG sequence instead."
            )
            return

        fps = _maya_playback_fps()
        seq_pattern = os.path.join(comp_dir, f"{base_name}.%04d.png")
        temp_video_path = os.path.join(
            tmp_dir,
            f"animkey_encoded{os.path.splitext(out_path)[1].lower() or '.mp4'}"
        )
        cmd = [
            ffmpeg_path, "-y",
            "-framerate", str(fps),
            "-start_number", "1",
            "-i", seq_pattern,
        ]
        if out_path.lower().endswith(".mp4"):
            cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
        else:
            cmd.extend(["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"])
        cmd.append(temp_video_path)

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                startupinfo=_subprocess_startupinfo(),
            )
            _copy_verified_video_to_destination(temp_video_path, out_path)
            size_mb = _file_size(out_path) / (1024.0 * 1024.0)
            self.status_bar.setText(
                f"Playblast with strokes exported: {os.path.basename(out_path)} ({size_mb:.1f} MB)"
            )
        except subprocess.CalledProcessError as e:
            self.status_bar.setText(f"FFmpeg failed: {_short_process_error(e)}")
        except Exception as e:
            self.status_bar.setText(f"Video export failed: {e}")
        finally:
            if os.path.exists(out_path) and _file_size(out_path) <= 0:
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return
        raw_template = os.path.join(tmp_dir, "raw")
        self.status_bar.setText("🎬 Rendering base playblast...")
        QtWidgets.QApplication.processEvents()

        try:
            cmds.playblast(
                filename=raw_template,
                format="image",
                compression="png",
                quality=100,
                percent=100,
                width=panel_w,
                height=panel_h,
                startTime=start,
                endTime=end,
                showOrnaments=False,
                viewer=False,
                forceOverwrite=True,
                framePadding=4,
            )
        except Exception as e:
            self.status_bar.setText(f"❌ Playblast failed: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        # Carpeta final de salida (secuencia compuesta)
        if want_video:
            comp_dir = os.path.join(tmp_dir, "composited")
            os.makedirs(comp_dir, exist_ok=True)
        else:
            comp_dir = out_path if not out_path.lower().endswith(("png",)) else out_dir
            os.makedirs(comp_dir, exist_ok=True)

        frame_files = sorted(
            f for f in os.listdir(tmp_dir)
            if f.startswith("raw") and f.lower().endswith((".png", ".iff", ".jpg"))
        )
        if not frame_files:
            self.status_bar.setText("❌ Playblast produced no frames.")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        self.status_bar.setText("🎬 Compositing strokes onto frames...")
        QtWidgets.QApplication.processEvents()

        for i, fname in enumerate(frame_files):
            frame_num = start + i
            base_path = os.path.join(tmp_dir, fname)
            base_img = QImage(base_path)
            if base_img.isNull():
                continue
            w, h = base_img.width(), base_img.height()

            stroke_img = self.canvas.render_frame_to_image(frame_num, w, h)

            painter = QPainter(base_img)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.drawImage(0, 0, stroke_img)
            painter.end()

            out_frame_name = f"{base_name}.{frame_num:04d}.png"
            base_img.save(os.path.join(comp_dir, out_frame_name), "PNG")

            if i % 5 == 0:
                self.status_bar.setText(f"🎬 Compositing frame {frame_num}/{end}...")
                QtWidgets.QApplication.processEvents()

        if not want_video:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.status_bar.setText(f"✅ Playblast PNG sequence exported to: {comp_dir}")
            return

        # Ensamblar video final con ffmpeg si está disponible
        ffmpeg_path = resolve_ffmpeg_path(status_callback=self.status_bar.setText)
        if not ffmpeg_path:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.status_bar.setText(
                "⚠️ ffmpeg not found in PATH. Install ffmpeg to export video, "
                "or export as PNG sequence instead."
            )
            self.status_bar.setText(
                "FFmpeg not found. Put ffmpeg.exe in AnimKey/data/ffmpeg/bin, "
                "or export as PNG sequence instead."
            )
            return

        fps = _maya_playback_fps()

        seq_pattern = os.path.join(comp_dir, f"{base_name}.%04d.png")
        temp_video_path = os.path.join(
            tmp_dir,
            f"animkey_encoded{os.path.splitext(out_path)[1].lower() or '.mp4'}"
        )
        cmd = [
            ffmpeg_path, "-y",
            "-framerate", str(fps),
            "-start_number", str(start),
            "-i", seq_pattern,
        ]
        if out_path.lower().endswith(".mp4"):
            cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
        else:
            cmd.extend(["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"])
        cmd.append(temp_video_path)
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                startupinfo=_subprocess_startupinfo(),
            )
            _copy_verified_video_to_destination(temp_video_path, out_path)
            size_mb = _file_size(out_path) / (1024.0 * 1024.0)
            self.status_bar.setText(f"Playblast with strokes exported: {os.path.basename(out_path)} ({size_mb:.1f} MB)")
            self.status_bar.setText(f"✅ Playblast with strokes exported to: {out_path}")
        except subprocess.CalledProcessError as e:
            self.status_bar.setText(f"❌ ffmpeg failed: {e}")
            self.status_bar.setText(f"FFmpeg failed: {_short_process_error(e)}")
        except Exception as e:
            self.status_bar.setText(f"Video export failed: {e}")
        finally:
            if want_video and os.path.exists(out_path) and _file_size(out_path) <= 0:
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _load_session(self):
        filters = (
            "JSON (*.json);;"
            "Blue Pencil ZIP (*.zip)"
        )
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Sketchboard", "", filters
        )
        if not path:
            return
        try:
            if path.lower().endswith('.skbz'):
                with zipfile.ZipFile(path, 'r') as zf:
                    with zf.open('sketchboard.json') as jf:
                        data = json.loads(jf.read().decode('utf-8'))
            elif path.lower().endswith('.zip'):
                with zipfile.ZipFile(path, 'r') as zf:
                    # Solo se admite el import de datos vectoriales sketchboard.json dentro del ZIP
                    json_files = [n for n in zf.namelist() if n.endswith('sketchboard.json')]
                    if json_files:
                        with zf.open(json_files[0]) as jf:
                            data = json.loads(jf.read().decode('utf-8'))
                    else:
                        self.status_bar.setText("❌ ZIP doesn't contain Sketchboard data (Only native drawings are imported)")
                        return
            else:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            self.canvas.data.from_dict(data, self.canvas.width(), self.canvas.height())
            self.timeline.data_ref = self.canvas.data
            self.canvas.set_frame(self.canvas.current_frame)
            self.timeline.update_keys()
            self._save_scene_session()
            self.status_bar.setText(f"📂 Loaded: {os.path.basename(path)}")
        except Exception as e:
            self.status_bar.setText(f"❌ Error loading file: {e}")

    def closeEvent(self, event):
        global _instance
        self._remove_tablet_event_filter()
        try:
            if self._scene_commit_timer.isActive():
                self._scene_commit_timer.stop()
                self._flush_deferred_scene_commit()
        except Exception:
            pass
        try:
            if hasattr(self, "sync_timer") and self.sync_timer:
                self.sync_timer.stop()
        except Exception:
            pass
        try:
            self.timeline.cleanup()
        except Exception:
            pass
        SketchboardWindow._instance = None
        if _instance is self:
            _instance = None
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_T and event.modifiers() & Qt.ControlModifier:
            free_mode = bool(event.modifiers() & Qt.ShiftModifier)
            self.tool_panel._select_tool("free_transform" if free_mode else "transform")
            self.canvas.activate_transform(select_all_if_empty=True)
            if free_mode:
                self.status_bar.setText("Free Transform: move strokes, warp corners, scale edges, drag stroke points")
            else:
                self.status_bar.setText("Transform: move, rotate, scale sides, scale corners proportionally")
            event.accept()
            return
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.canvas.tool in ("transform", "free_transform"):
            self.canvas.deactivate_transform()
            self.status_bar.setText(f"Frame: {self.canvas.current_frame} | Transform applied")
        elif event.key() == Qt.Key_Escape and self.canvas.tool in ("transform", "free_transform"):
            self.canvas.selected_strokes = []
            self.canvas.deactivate_transform()
            self.status_bar.setText(f"Frame: {self.canvas.current_frame} | Transform cancelled")
        elif event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self._on_undo_requested()
            event.accept()
            return
        elif event.key() == Qt.Key_E:
            self.tool_panel._select_tool("eraser")
        elif event.key() == Qt.Key_L:
            self.tool_panel._select_tool("lasso")
        elif event.key() == Qt.Key_B:
            self.tool_panel._select_tool("brush")
        elif event.key() == Qt.Key_Tab or event.key() == Qt.Key_H:
            if hasattr(self, "sidebar_widget"):
                self.toggle_sidebar()
        elif event.key() == Qt.Key_BracketRight:
            size = self.canvas.brush_size
            step = 0.25 if size < 5.0 else 0.5 if size < 15.0 else 1.0 if size < 50.0 else 2.0
            self.tool_panel.set_size_value(min(BRUSH_SIZE_MAX, size + step))
            event.accept()
            return
        elif event.key() == Qt.Key_BracketLeft:
            size = self.canvas.brush_size
            step = 0.25 if size < 5.0 else 0.5 if size < 15.0 else 1.0 if size < 50.0 else 2.0
            self.tool_panel.set_size_value(max(BRUSH_SIZE_MIN, size - step))
            event.accept()
            return
        elif event.key() == Qt.Key_Right:
            try:
                curr = cmds.currentTime(q=True)
                cmds.currentTime(curr + 1)
            except: pass
        elif event.key() == Qt.Key_Left:
            try:
                curr = cmds.currentTime(q=True)
                cmds.currentTime(curr - 1)
            except: pass
        super().keyPressEvent(event)

# ═══════════════════════════════════════════════════════════════════════
#  MÓDULO 7: PUNTO DE ENTRADA
# ═══════════════════════════════════════════════════════════════════════

def launch():
    global _instance
    if SketchboardWindow._instance:
        try:
            SketchboardWindow._instance.toggle_overlay_enabled()
            _instance = SketchboardWindow._instance
            return SketchboardWindow._instance
        except:
            pass

    win = SketchboardWindow(overlay=True)
    SketchboardWindow._instance = win
    _instance = win
    win.show()
    win.raise_()
    return win

if __name__ == "__main__":
    is_maya = False
    try:
        if cmds.about(version=True):
            is_maya = True
    except:
        pass

    if is_maya:
        launch()
    else:
        import sys
        class _FakeCmds:
            def playbackOptions(self, **kw): return 1 if kw.get('min') else 120
            def currentTime(self, **kw): return 1
            def scriptJob(self, **kw): return 1
        import types
        sys.modules['maya'] = types.ModuleType('maya')
        sys.modules['maya.cmds'] = _FakeCmds()

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        win = SketchboardWindow(overlay=False)
        win.show()
        sys.exit(execute_qt(app))

_instance = None
def close_instance():
    global _instance
    inst = _instance or SketchboardWindow._instance
    if inst is not None:
        try:
            inst.close()
            inst.deleteLater()
        except RuntimeError:
            pass
        except Exception:
            pass
    _instance = None
    SketchboardWindow._instance = None

def execute():
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.brush.execute"):
        return None
    global _instance
    if _instance is None and SketchboardWindow._instance is not None:
        _instance = SketchboardWindow._instance
    if _instance is not None:
        try:
            _instance.toggle_overlay_enabled()
            return
        except RuntimeError:
            # Widget fue destruido, instanciar de nuevo
            _instance = None
            SketchboardWindow._instance = None
            pass
            
    _instance = SketchboardWindow(overlay=True)
    SketchboardWindow._instance = _instance
    _instance.show()
    _instance.raise_()
