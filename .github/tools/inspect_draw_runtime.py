"""Read the current Draw state without opening or editing the scene."""
import json
from pathlib import Path
import sys
import maya.cmds as cmds
from AnimKey.mods.maya_compat import QtWidgets

ROOT = Path(__file__).resolve().parents[2]
module = sys.modules.get("AnimKey.buttons.brush")
window = getattr(module, "_instance", None) if module else None
data = {"scene": cmds.file(q=True, sceneName=True), "frame": cmds.currentTime(q=True),
        "modified": cmds.file(q=True, modified=True), "module": getattr(module, "__file__", None),
        "windows": [{"type": type(widget).__name__, "name": widget.objectName(),
                     "visible": widget.isVisible(), "size": [widget.width(), widget.height()]}
                    for widget in QtWidgets.QApplication.topLevelWidgets()
                    if "sketch" in widget.objectName().lower() or "Sketchboard" in type(widget).__name__],
        "brushNode": cmds.objExists("animkey_brush")}
if window:
    data["instance"] = {"visible": window.isVisible(), "overlay": window.is_overlay,
                        "panel": window.model_panel, "frames": len(window.canvas.data.frames),
                        "points": sum(len(s.points) for f in window.canvas.data.frames.values() for s in f.strokes),
                        "timer": window.sync_timer.isActive() if hasattr(window, "sync_timer") else False}
(ROOT / "dist/draw-runtime-readonly.json").write_text(json.dumps(data, indent=2))
