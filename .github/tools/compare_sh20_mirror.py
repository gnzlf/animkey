"""Compare installed AnimBot and AnimKey in a disposable Maya scene, never save it."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import time
import traceback

from PySide2 import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"C:\Users\ANIM-gonzalo\Documents\maya\scripts")
SCENE = r"X:\0066_0926_clash_royale_royal_interest\03_Production\Shots\SQ01\SH20\Scenefiles\3D Animation\3D Layout\SQ01-SH20_3D Layout_v0027.ma"
OUTPUT = Path(os.environ.get("ANIMKEY_COMPARE_REPORT", str(ROOT / "dist" / "mirror-sh20-comparison.json")))
DATA = Path(r"C:\Users\ANIM-gonzalo\Documents\maya\scripts\animBot_data")
BACKUP = ROOT / "dist" / "animbot-snapshot-backup"
REPORT = {"scene": SCENE, "sceneSaved": False, "steps": [], "cases": []}
STACK_LOG = OUTPUT.with_suffix(".stacks.log").open("w")
faulthandler.enable(file=STACK_LOG)
faulthandler.dump_traceback_later(30, repeat=True, file=STACK_LOG)


def log(message):
    print("MIRROR_COMPARE: " + message, flush=True)
    REPORT["steps"].append(message)
    OUTPUT.write_text(json.dumps(REPORT, indent=2), encoding="utf-8")


def channels(nodes):
    result = {}
    for node in nodes:
        for attr in cmds.listAttr(node, keyable=True, scalar=True) or []:
            try:
                value = cmds.getAttr(node + "." + attr)
                if isinstance(value, (int, float, bool)):
                    result[node + "." + attr] = value
            except RuntimeError:
                pass
    return result


backups = {}
try:
    BACKUP.mkdir(exist_ok=True)
    for filename in ("mirror_settings_snapshot.anb", "opposite_controls_snapshot.anb"):
        path = DATA / filename
        payload = path.read_bytes() if path.exists() else None
        if not os.environ.get("ANIMKEY_COMPARE_EXISTING_SNAPSHOT"):
            backups[path] = payload
        if payload is not None and not os.environ.get("ANIMKEY_COMPARE_EXISTING_SNAPSHOT"):
            (BACKUP / filename).write_bytes(payload)
    log("Original AnimBot mirror snapshots backed up")
    import animBot
    log("AnimBot package imported")
    from animBot._api import core
    log("AnimBot core imported")
    from animBot.mirror import mirror as bot_module
    log("AnimBot mirror imported")
    bot = core.CORE.mirror
    core.CORE.skipDialogs = True
    log("Opening scene with script nodes disabled")
    cmds.file(SCENE, open=True, force=True, prompt=False, executeScriptNodes=False)
    cmds.undoInfo(state=True)
    log("Maya undo enabled: {}".format(cmds.undoInfo(query=True, state=True)))
    cmds.autoKeyframe(state=False)
    REPORT["layers"] = cmds.ls(type="animLayer") or []
    REPORT["sets"] = cmds.ls("King_Rigging:*", type="objectSet") or []
    from AnimKey.buttons import mirror as key_module
    controls = key_module._control_candidates_in_namespace("King_Rigging")
    controls = [node for node in controls if any(part in node.rsplit("|", 1)[-1]
                 for part in ("Finger", "Thumb", "Wrist")) and "FK" in node.rsplit("|", 1)[-1]]
    if os.environ.get("ANIMKEY_COMPARE_NEUTRAL_SNAPSHOT"):
        controls = []
        for namespace in ("King_Rigging", "Goblin_Rigging"):
            controls.extend(key_module._control_candidates_in_namespace(namespace))
        layers = [layer for layer in cmds.ls(type="animLayer") or [] if layer != "BaseAnimation"]
        if layers:
            cmds.delete(layers)
        curves = [curve for curve in cmds.ls(type="animCurve") or []
                  if cmds.nodeType(curve) in ("animCurveTA", "animCurveTL", "animCurveTU", "animCurveTT")]
        if curves:
            cmds.delete(curves)
        key_module._reset_captured_values_to_defaults(key_module._capture_scalar_attr_values(controls))
        log("Animation removed only in this disposable process for neutral-pose snapshot test")
    if not controls:
        raise RuntimeError("No hand controls found")
    log("Hand control comparison: {} controls".format(len(controls)))
    cmds.select(controls, replace=True)
    before = channels(controls)
    start = time.perf_counter()
    log("Calling AnimBot snapshot (normal public command)")
    if not os.environ.get("ANIMKEY_COMPARE_EXISTING_SNAPSHOT"):
        bot.snapshotMirrorSettings_click()
    REPORT["animbotSnapshotSeconds"] = time.perf_counter() - start
    after = channels(controls)
    REPORT["animbotSnapshotChangedChannels"] = {key: [value, after.get(key)] for key, value in before.items()
                                                if after.get(key) != value}
    log("AnimBot snapshot returned in {:.3f}s".format(REPORT["animbotSnapshotSeconds"]))
    if os.environ.get("ANIMKEY_COMPARE_NEUTRAL_SNAPSHOT"):
        REPORT["freshSnapshots"] = json.loads((DATA / "mirror_settings_snapshot.anb").read_text())
        log("Fresh AnimBot neutral-pose snapshots captured")
        sys.exit(0)
    sources = [node for node in controls if node.endswith("_L") and "FK" in node.rsplit("|", 1)[-1]
               and any(part in node.rsplit("|", 1)[-1] for part in ("Finger", "Thumb", "Wrist"))]
    REPORT["sources"] = sources
    cmds.select(sources, replace=True)
    # A deliberate, asymmetric pose makes an early no-op distinguishable from a successful mirror.
    for index, node in enumerate(sources):
        for axis, value in (("X", 4.0 + index), ("Y", -7.0), ("Z", 23.0)):
            plug = node + ".rotate" + axis
            if cmds.getAttr(plug, settable=True):
                cmds.setAttr(plug, value)
    before = channels(controls)
    start = time.perf_counter()
    log("Calling AnimBot mirror pose on {} source controls".format(len(sources)))
    bot.mirrorPose_click()
    after = channels(controls)
    REPORT["animbotMirrorSeconds"] = time.perf_counter() - start
    REPORT["animbotChangedChannels"] = {key: [value, after.get(key)] for key, value in before.items()
                                        if after.get(key) != value}
    log("AnimBot mirror returned; {} changed channels".format(len(REPORT["animbotChangedChannels"])))
except Exception:
    REPORT["error"] = traceback.format_exc()
    log("Comparison stopped: " + REPORT["error"])
finally:
    restored = []
    for path, payload in backups.items():
        current = path.read_bytes() if path.exists() else None
        if current != payload:
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(payload)
        restored.append({"file": path.name, "restored": (path.read_bytes() if path.exists() else None) == payload})
    REPORT["originalSnapshotsRestored"] = restored
    log("Finished without saving the production scene")
    faulthandler.cancel_dump_traceback_later()
    if os.environ.get("ANIMKEY_COMPARE_FULL_UI"):
        cmds.quit(force=True, exitCode=0)
