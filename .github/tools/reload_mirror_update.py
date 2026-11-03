"""Reload validated modules without changing the active Maya scene."""
import importlib
import json
from pathlib import Path
import traceback
import maya.cmds as cmds
from AnimKey.buttons import mirror
from AnimKey.core import animation_curve_transfer

ROOT = Path(__file__).resolve().parents[2]


def scene_state():
    return {"scene": cmds.file(query=True, sceneName=True),
            "modified": cmds.file(query=True, modified=True),
            "time": cmds.currentTime(query=True),
            "selection": cmds.ls(selection=True, long=True) or []}


report = {"before": scene_state(), "reloaded": False}
try:
    if mirror._auto_mirror_state.enabled:
        report["reason"] = "Auto Mirror is active; reload postponed to preserve its callbacks"
    else:
        importlib.reload(animation_curve_transfer)
        importlib.reload(mirror)
        mirror.clear_mirror_cache()
        report["reloaded"] = True
        report["modules"] = [animation_curve_transfer.__file__, mirror.__file__]
        report["profiles"] = {}
        for namespace in ("King_Rigging", "King_Rigging1", "Goblin_Rigging"):
            controls = mirror._control_candidates_in_namespace(namespace)
            if not controls:
                continue
            profile, quick = mirror._load_or_build_basic_snapshot(controls)
            report["profiles"][namespace] = {
                "calibrationVersion": (profile or {}).get("calibration_version"),
                "savedCalibration": (profile or {}).get("saved_calibration"),
                "sharedFrom": (profile or {}).get("shared_from"),
                "quickFallback": quick,
                "controlCount": len(controls),
                "matchesLiveControls": mirror._snapshot_covers_controls(profile, controls)}
except Exception:
    report["error"] = traceback.format_exc()
report["after"] = scene_state()
report["sceneStateUnchanged"] = report["before"] == report["after"]
(ROOT / "dist/mirror-update-reload.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("ANIMKEY_MIRROR_UPDATE_RELOAD=" + str(report["reloaded"]))
