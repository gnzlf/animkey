"""Bounded Draw startup probe with a real Qt event loop and a fake viewport."""
import faulthandler
import json
from pathlib import Path
import sys
import time
from unittest import mock
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from AnimKey.mods.maya_compat import QtCore, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from AnimKey.buttons import brush

stream = open(ROOT / "dist/draw-startup-stack.log", "w")
faulthandler.dump_traceback_later(8, repeat=True, file=stream)
main = QtWidgets.QMainWindow()
main.resize(1280, 800)
main.show()
viewport = QtWidgets.QWidget(main)
viewport.setGeometry(0, 0, 1280, 700)
viewport.show()
started = time.perf_counter()
patches = [mock.patch.object(brush, "get_maya_main_window", return_value=main),
           mock.patch.object(brush, "get_active_model_panel", return_value="modelPanel4"),
           mock.patch.object(brush, "get_active_model_panel_widget", return_value=viewport),
           mock.patch.object(brush.SketchboardWindow, "_install_native_pen_event_filter")]
for patch in patches:
    patch.start()
print("DRAW_PROBE: constructing", flush=True)
window = brush.SketchboardWindow(overlay=True)
print("DRAW_PROBE: constructed %.3fs" % (time.perf_counter()-started), flush=True)
window.show()
ticks = []
timer = QtCore.QTimer()
timer.setInterval(50)
timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
timer.start()
def finish():
    window.hud_wrapper.container.backdrop_active = False
    window.hud_wrapper.container.update()
    window.hud_wrapper.grab().save(str(ROOT / "dist/draw-controls-preview.png"))
    report = {"startupSeconds": time.perf_counter()-started, "heartbeatTicks": len(ticks),
              "maxHeartbeatGap": max([b-a for a,b in zip(ticks,ticks[1:])] or [0]),
              "hud": [window.hud_wrapper.width(),window.hud_wrapper.height()]}
    (ROOT / "dist/draw-startup-probe.json").write_text(json.dumps(report, indent=2))
    print("DRAW_PROBE: " + json.dumps(report), flush=True)
    window.close()
    APP.quit()
QtCore.QTimer.singleShot(1800, finish)
APP.exec_()
faulthandler.cancel_dump_traceback_later()
