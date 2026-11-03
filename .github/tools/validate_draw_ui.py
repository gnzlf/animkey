"""Exercise Draw in a disposable Maya UI process, without saving the scene."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import time
import traceback
import importlib.util
import cProfile
ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "dist" / os.environ.get("ANIMKEY_DRAW_REPORT", "draw-ui-validation.json")
sys.path.insert(0, os.environ.get("ANIMKEY_DRAW_ROOT", str(ROOT)))
import maya.cmds as cmds
from AnimKey.mods.maya_compat import QtCore, QtWidgets

report = {"sceneSaved": False, "stages": [], "passed": False}
def log(message):
    report["stages"].append(message)
    OUTPUT.write_text(json.dumps(report, indent=2))

stack = open(OUTPUT.with_suffix(".stacks.log"), "w")
faulthandler.dump_traceback_later(12, repeat=True, file=stack)
try:
    log("Opening scene")
    scene = r"X:\0066_0926_clash_royale_royal_interest\03_Production\Shots\SQ01\SH20\Scenefiles\3D Animation\3D Layout\SQ01-SH20_3D Layout_v0030.ma"
    cmds.file(scene, open=True, force=True, prompt=False, executeScriptNodes=False)
    baseline_ticks = []
    baseline_timer = QtCore.QTimer()
    baseline_timer.setInterval(50)
    baseline_timer.timeout.connect(lambda: baseline_ticks.append(time.perf_counter()))
    baseline_started = [0.0]
    def start_baseline():
        baseline_started[0] = time.perf_counter()
        baseline_timer.start()
        QtCore.QTimer.singleShot(2500, begin_draw)
    def begin_draw():
        baseline_timer.stop()
        report["baselineHeartbeatTicks"] = len(baseline_ticks)
        gaps = [b-a for a,b in zip(baseline_ticks,baseline_ticks[1:])]
        if baseline_ticks:
            gaps.append(baseline_ticks[0]-baseline_started[0])
        report["baselineMaxHeartbeatGap"] = max(gaps or [0])
        main = QtWidgets.QApplication.activeWindow()
        if main:
            QtWidgets.QApplication.primaryScreen().grabWindow(int(main.winId())).save(str(OUTPUT.with_suffix(".before.png")))
        module_root = Path(os.environ.get("ANIMKEY_DRAW_ROOT", str(ROOT)))
        spec = importlib.util.spec_from_file_location("AnimKey.buttons.brush", module_root / "AnimKey/buttons/brush.py")
        brush = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = brush
        spec.loader.exec_module(brush)
        log("Constructing Draw: " + brush.__file__)
        before = {"selection": cmds.ls(sl=True, long=True), "frame": cmds.currentTime(q=True),
                  "modified": cmds.file(q=True, modified=True), "undo": cmds.undoInfo(q=True, undoName=True)}
        profile = cProfile.Profile()
        profile.enable()
        start = time.perf_counter()
        window = brush.launch()
        report["constructionSeconds"] = time.perf_counter()-start
        log("Draw constructed")
        ticks = []
        timer = QtCore.QTimer(window)
        timer.setInterval(50)
        timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
        timer.start()
        def finish():
            try:
                report["heartbeatTicks"] = len(ticks)
                report["maxHeartbeatGap"] = max([b-a for a,b in zip(ticks,ticks[1:])] or [0])
                report["visible"] = window.isVisible()
                report["overlayIsNativeWindow"] = window.isWindow()
                report["paletteIsNativeWindow"] = window.hud_wrapper.isWindow()
                main = brush.get_maya_main_window()
                QtWidgets.QApplication.primaryScreen().grabWindow(int(main.winId())).save(str(OUTPUT.with_suffix(".window.png")))
                if hasattr(window.hud_wrapper, "container"):
                    window.hud_wrapper.container.backdrop_active = False
                window.hud_wrapper.grab().save(str(OUTPUT.with_suffix(".png")))
                after = {"selection": cmds.ls(sl=True, long=True), "frame": cmds.currentTime(q=True),
                         "modified": cmds.file(q=True, modified=True), "undo": cmds.undoInfo(q=True, undoName=True)}
                report["openPreservedSceneState"] = before == after
                report["before"] = before
                report["after"] = after
                window.set_overlay_enabled(False)
                report["hideWorks"] = not window.isVisible() and not window.hud_wrapper.isVisible()
                window.set_overlay_enabled(True)
                report["reopenWorks"] = window.isVisible()
                window.close()
                report["passed"] = (len(ticks) >= max(2, len(baseline_ticks) // 2)
                                and report["maxHeartbeatGap"] <= max(.5, report["baselineMaxHeartbeatGap"] * 1.2)
                                and report["openPreservedSceneState"] and report["hideWorks"] and report["reopenWorks"])
                profile.disable()
                profile.dump_stats(str(OUTPUT.with_suffix(".prof")))
                log("Finished Draw check")
            except Exception:
                report["error"] = traceback.format_exc()
                log("Failed Draw check")
            faulthandler.cancel_dump_traceback_later()
            cmds.quit(force=True, exitCode=0)
        QtCore.QTimer.singleShot(2500, finish)
    # Scene opening queues renderer/plugin initialization, unrelated to Draw.
    # Let those callbacks settle before measuring the existing UI and overlay.
    QtCore.QTimer.singleShot(12000, start_baseline)

except Exception:
    report["error"] = traceback.format_exc()
    log("Failed startup")
    faulthandler.cancel_dump_traceback_later()
    cmds.quit(force=True, exitCode=1)
