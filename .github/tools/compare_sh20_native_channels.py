"""Read AnimBot's native channel evaluator in a disposable Maya process."""
import json
from pathlib import Path
import sys
import traceback

from PySide2 import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import maya.OpenMaya as om

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"C:\Users\ANIM-gonzalo\Documents\maya\scripts")
from AnimKey.buttons import mirror
from animBot.mirror import mirror as bot_module

OUTPUT = ROOT / "dist/mirror-native-channel-comparison.json"
SCENE = r"X:\0066_0926_clash_royale_royal_interest\03_Production\Shots\SQ01\SH20\Scenefiles\3D Animation\3D Layout\SQ01-SH20_3D Layout_v0030.ma"
report = {"scene": SCENE, "sceneSaved": False, "cases": []}


def save():
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")


try:
    cmds.file(SCENE, open=True, force=True, prompt=False, executeScriptNodes=False)
    cls = bot_module.o_____0___O_O__l___O____0_____O_____l_o__0___O_o_O_____O____1
    for namespace in ("King_Rigging", "Goblin_Rigging"):
        for short in ("IKArm_L", "IKArm_R", "IKLeg_L", "IKLeg_R"):
            node = cmds.ls(namespace + ":" + short, long=True)[0]
            plug = node + ".swivel"
            item = {"plug": plug}
            report["cases"].append(item)
            try:
                attr = cls(o__1_0__l_____O_o__O_o___1=plug)
                item["constructed"] = True
                item["readings"] = []
                for frame in (11, 35, 60):
                    cmds.currentTime(frame)
                    reading = {"frame": frame, "value": cmds.getAttr(plug),
                               "nativeMirror": attr.getMirrorValueAt(om.MTime(frame, om.MTime.uiUnit()))}
                    item["readings"].append(reading)
                cmds.setAttr(plug, 17.0)
                item["unkeyedEdit"] = {"currentValue": cmds.getAttr(plug),
                    "evaluatedAtTime": cmds.getAttr(plug, time=60),
                    "nativeMirror": attr.getMirrorValueAt(om.MTime(60, om.MTime.uiUnit()))}
                opposite = mirror.find_opposite_by_name(node)
                cmds.setKeyframe(opposite, attribute="swivel", time=60, value=29.0)
                item["oppositeKeyedEdit"] = {
                    "oppositeValue": cmds.getAttr(opposite + ".swivel", time=60),
                    "nativeMirror": attr.getMirrorValueAt(om.MTime(60, om.MTime.uiUnit()))}
                print("NATIVE_CHANNEL: " + json.dumps(item), flush=True)
            except Exception:
                item["error"] = traceback.format_exc()
                print(item["error"], flush=True)
            save()
except Exception:
    report["error"] = traceback.format_exc()
    print(report["error"], flush=True)
save()
