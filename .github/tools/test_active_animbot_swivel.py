"""One reversible native AnimBot mirror transaction in the initialized session."""
import hashlib
import json
from pathlib import Path
import traceback
import time

import maya.cmds as cmds
import maya.mel as mel
from animBot._api import core
from AnimKey.buttons import mirror as animkey

if not globals().get("ANIMKEY_RUN_AT_IDLE"):
    raise RuntimeError("Run this test with maya.utils.executeDeferred and ANIMKEY_RUN_AT_IDLE=True; socket MEL execution prevents undo")

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "dist" / "mirror-active-animbot-swivel.json"
BACKUP = ROOT / "dist" / ("mirror-active-animbot-before-" + str(time.time_ns()) + ".json")
DATA = Path(r"C:\Users\ANIM-gonzalo\Documents\maya\scripts\animBot_data")
CHUNK = "AnimKey Reversible AnimBot Swivel Comparison"
report = {"command": "CORE.mirror.mirrorPose_click()", "sceneSaved": False}


def save():
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def animation_state():
    records = {}
    for curve in sorted(cmds.ls(type="animCurve") or []):
        item = {"time": cmds.keyframe(curve, query=True, timeChange=True) or [],
                "value": cmds.keyframe(curve, query=True, valueChange=True) or []}
        for flag in ("inTangentType", "outTangentType", "inAngle", "outAngle",
                     "inWeight", "outWeight", "lock", "weightLock", "weightedTangents"):
            item[flag] = cmds.keyTangent(curve, query=True, **{flag: True})
        records[curve] = item
    return records


def scalar_state(controls):
    values = {}
    for node in controls:
        for attr in cmds.listAttr(node, keyable=True, scalar=True) or []:
            try:
                value = cmds.getAttr(node + "." + attr)
                if isinstance(value, (bool, int, float)):
                    values[node + "." + attr] = value
            except RuntimeError:
                pass
    return values


files = {}
opened = False
completed = False
original_skip_dialogs = core.CORE.skipDialogs
original_modified = bool(cmds.file(query=True, modified=True))
original_scene = cmds.file(query=True, sceneName=True)
original_time = cmds.currentTime(query=True)
original_selection = cmds.ls(selection=True, long=True) or []
original_auto_key = cmds.autoKeyframe(query=True, state=True)
original_undo_name = cmds.undoInfo(query=True, undoName=True)
key_selection = animkey._capture_selected_keyframes()
try:
    if not cmds.undoInfo(query=True, state=True):
        raise RuntimeError("Undo is disabled; no test was performed")
    slider = mel.eval('$tmp=$gPlayBackSlider')
    if cmds.timeControl(slider, query=True, rangeVisible=True):
        raise RuntimeError("A timeline range is selected; no test was performed")
    targets = []
    all_controls = []
    reference_edits = {}
    for namespace in ("King_Rigging", "Goblin_Rigging"):
        all_controls.extend(animkey._control_candidates_in_namespace(namespace))
        for name in ("IKArm_L", "IKArm_R", "IKLeg_L", "IKLeg_R"):
            node = (cmds.ls(namespace + ":" + name, long=True) or [None])[0]
            if not node or not cmds.objExists(node + ".swivel") or not cmds.getAttr(node + ".swivel", settable=True):
                raise RuntimeError("Expected editable swivel is unavailable: " + namespace + ":" + name)
            targets.append(node)
            ref = cmds.referenceQuery(node, referenceNode=True)
            reference_edits[ref] = cmds.referenceQuery(ref, editStrings=True) or []
    for filename in ("mirror_settings_snapshot.anb", "opposite_controls_snapshot.anb"):
        path = DATA / filename
        files[path] = path.read_bytes() if path.exists() else None
    reference = json.loads(files[DATA / "mirror_settings_snapshot.anb"].decode("utf-8"))
    before_curves = animation_state()
    before_scalars = scalar_state(all_controls)
    BACKUP.write_text(json.dumps({"scene": original_scene, "time": original_time,
        "autoKey": original_auto_key, "selectedKeys": key_selection,
        "selection": original_selection, "curves": before_curves,
        "scalars": before_scalars, "referenceEdits": reference_edits}, indent=2), encoding="utf-8")
    report.update(scene=original_scene, time=original_time, phase="backup-written",
                  backupFile=str(BACKUP), beforeUndoName=original_undo_name,
                  beforeCurveDigest=digest(before_curves), beforeScalarDigest=digest(before_scalars))
    save()
    cmds.undoInfo(openChunk=True, chunkName=CHUNK)
    opened = True
    cmds.autoKeyframe(state=False)
    if key_selection:
        cmds.selectKey(list(dict.fromkeys(curve for curve, _ in key_selection)), clear=True)
    for index, node in enumerate(targets):
        cmds.setAttr(node + ".swivel", 17.0 + index if node.endswith("_L") else -23.0 - index)
    cmds.select(targets, replace=True)
    report["input"] = {node: cmds.getAttr(node + ".swivel") for node in targets}
    core.CORE.skipDialogs = True
    report["phase"] = "calling-normal-animbot-command"
    save()
    core.CORE.mirror.mirrorPose_click()
    report["output"] = {node: cmds.getAttr(node + ".swivel") for node in targets}
    expected = {}
    for node in targets:
        namespace = node.rsplit("|", 1)[-1].rsplit(":", 1)[0]
        short = node.rsplit("|", 1)[-1].split(":")[-1]
        keys = [key for key in reference[namespace] if key.endswith("|" + short + ".swivel")]
        if len(keys) != 1:
            raise RuntimeError("Ambiguous reference coefficient: " + node)
        opposite_short = short[:-2] + ("_R" if short.endswith("_L") else "_L")
        opposite = next(item for item in targets if item.rsplit("|", 1)[-1] == namespace + ":" + opposite_short)
        expected[node] = report["input"][opposite] * reference[namespace][keys[0]]
    report["expectedFromSavedSnapshot"] = expected
    report["matchesSavedSnapshot"] = all(abs(report["output"][node] - expected[node]) < 1e-6 for node in targets)
    report["normalCommandChangedValues"] = report["input"] != report["output"]
    completed = True
except Exception:
    report["error"] = traceback.format_exc()
finally:
    core.CORE.skipDialogs = original_skip_dialogs
    if opened:
        if cmds.currentTime(query=True) != original_time:
            cmds.currentTime(original_time, edit=True)
        cmds.select(original_selection, replace=True)
        animkey._restore_selected_keyframes(key_selection)
        cmds.autoKeyframe(state=original_auto_key)
        cmds.undoInfo(closeChunk=True)
        undo_name = cmds.undoInfo(query=True, undoName=True)
        report["undoChunk"] = undo_name
        if undo_name == CHUNK:
            cmds.undo()
        elif undo_name != original_undo_name:
            report["restorationWarning"] = "Unexpected undo head; unrelated user commands were not undone"
        after_curves = animation_state()
        after_scalars = scalar_state(all_controls)
        report["curvesAndTangentsRestored"] = digest(before_curves) == digest(after_curves)
        report["allControlScalarsRestored"] = digest(before_scalars) == digest(after_scalars)
        report["referenceEditsRestored"] = all(
            (cmds.referenceQuery(ref, editStrings=True) or []) == edits
            for ref, edits in reference_edits.items())
        report["timeRestored"] = cmds.currentTime(query=True) == original_time
        report["selectionRestored"] = (cmds.ls(selection=True, long=True) or []) == original_selection
        report["selectedKeysRestored"] = animkey._capture_selected_keyframes() == key_selection
        report["autoKeyRestored"] = cmds.autoKeyframe(query=True, state=True) == original_auto_key
        if all(report[key] for key in ("curvesAndTangentsRestored", "allControlScalarsRestored",
                                       "referenceEditsRestored", "timeRestored", "selectionRestored",
                                       "selectedKeysRestored", "autoKeyRestored")):
            cmds.file(modified=original_modified)
        else:
            report["restorationWarning"] = "State mismatch; inspect backup before doing further tests"
    for path, payload in files.items():
        current = path.read_bytes() if path.exists() else None
        if current != payload:
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(payload)
    report["snapshotFilesRestored"] = all((path.read_bytes() if path.exists() else None) == payload
                                           for path, payload in files.items())
    report["phase"] = "finished" if completed else "stopped"
    save()
    print("ANIMKEY_ANIMBOT_SWIVEL_REPORT=" + str(OUTPUT))
