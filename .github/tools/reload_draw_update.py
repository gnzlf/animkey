"""Reload Draw and show its new controls while preserving the active scene."""
import hashlib
import importlib
import json
from pathlib import Path
import time
import traceback
import maya.cmds as cmds
from AnimKey.buttons import brush
from AnimKey.core import toolbar
from AnimKey.core.executionGuard import animkey_execution
from AnimKey.mods.maya_compat import QtCore

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "dist/draw-update-reload.json"


def state():
    payload = ""
    if cmds.objExists("animkey_brush.sketchboardData"):
        payload = cmds.getAttr("animkey_brush.sketchboardData") or ""
    return {"scene": cmds.file(q=True, sceneName=True), "modified": cmds.file(q=True, modified=True),
            "frame": cmds.currentTime(q=True), "selection": cmds.ls(sl=True, long=True) or [],
            "autoKey": cmds.autoKeyframe(q=True, state=True), "undo": cmds.undoInfo(q=True, undoName=True),
            "drawingDataSha256": hashlib.sha256(payload.encode()).hexdigest()}


report = {"before": state(), "reloaded": False}
try:
    old_window = brush._instance or brush.SketchboardWindow._instance
    saved_data = old_window.canvas.data.to_dict() if old_window is not None else None
    brush.close_instance()
    importlib.reload(brush)
    def open_draw(checked=False):
        with animkey_execution("toolbar", "BRUSH"):
            return brush.execute()
    button = toolbar.animkey_toolbar.brush_btn
    button.clicked.disconnect()
    button.clicked.connect(open_draw)
    started = time.perf_counter()
    window = brush.launch()
    if saved_data:
        data = brush.SketchboardData()
        data.from_dict(saved_data, window.canvas.width(), window.canvas.height())
        window.canvas.data = data
        window.timeline.data_ref = data
        window.canvas._invalidate_frame_render_cache()
        window.canvas._rebuild_current_frame()
    report["openSeconds"] = time.perf_counter()-started
    report["reloaded"] = True
    report["module"] = brush.__file__
    report["material"] = bool(window.hud_wrapper.property("materialSurface"))
    report["nativeBlurBackend"] = window.hud_wrapper.property("nativeBlurBackend")
    report["after"] = state()
    report["sceneStateUnchanged"] = report["before"] == report["after"]
    ticks = []
    timer = QtCore.QTimer(window)
    timer.setInterval(50)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()
    def finish():
        timer.stop()
        report["heartbeatTicks"] = len(ticks)
        report["maxHeartbeatGap"] = max([b-a for a,b in zip(ticks,ticks[1:])] or [0])
        report["controlsVisible"] = window.hud_wrapper.isVisible()
        report["canvasVisible"] = window.isVisible()
        report["after"] = state()
        report["sceneStateUnchanged"] = report["before"] == report["after"]
        OUTPUT.write_text(json.dumps(report, indent=2))
    QtCore.QTimer.singleShot(1800, finish)
except Exception:
    report["error"] = traceback.format_exc()
OUTPUT.write_text(json.dumps(report, indent=2))
