"""Undo only the completed, specifically identified socket test transaction."""
import json
from pathlib import Path
import maya.cmds as cmds
import maya.utils

root = Path(__file__).resolve().parents[2]
path = root / "dist/mirror-active-animbot-swivel.json"
report = json.loads(path.read_text(encoding="utf-8"))
backup = json.loads(Path(report["backupFile"]).read_text(encoding="utf-8"))
def restore():
    head = cmds.undoInfo(query=True, undoName=True)
    result = {"beforeUndoName": head, "undoPerformed": False}
    try:
        if ("test_active_animbot_swivel.py" in head
                and cmds.file(query=True, sceneName=True) == backup["scene"]
                and cmds.currentTime(query=True) == backup["time"]
                and (cmds.ls(selection=True, long=True) or []) == backup["selection"]):
            cmds.undo()
            result["undoPerformed"] = True
        differences = {}
        for plug, value in backup["scalars"].items():
            current = cmds.getAttr(plug)
            if current != value:
                differences[plug] = {"expected": value, "actual": current}
        result["scalarDifferences"] = differences
        result["referenceEditsRestored"] = all(
            (cmds.referenceQuery(ref, editStrings=True) or []) == edits
            for ref, edits in backup["referenceEdits"].items())
        curves = {}
        for curve in sorted(cmds.ls(type="animCurve") or []):
            item = {"time": cmds.keyframe(curve, query=True, timeChange=True) or [],
                    "value": cmds.keyframe(curve, query=True, valueChange=True) or []}
            for flag in ("inTangentType", "outTangentType", "inAngle", "outAngle",
                         "inWeight", "outWeight", "lock", "weightLock", "weightedTangents"):
                item[flag] = cmds.keyTangent(curve, query=True, **{flag: True})
            curves[curve] = item
        result["curvesAndTangentsRestored"] = curves == backup["curves"]
        result["timeRestored"] = cmds.currentTime(query=True) == backup["time"]
        result["selectionRestored"] = (cmds.ls(selection=True, long=True) or []) == backup["selection"]
        result["autoKeyRestored"] = cmds.autoKeyframe(query=True, state=True) == backup["autoKey"]
    except Exception as exc:
        result["error"] = str(exc)
    (root / "dist/mirror-active-restoration.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("ANIMKEY_RESTORATION=" + str(root / "dist/mirror-active-restoration.json"))


maya.utils.executeDeferred(restore)
