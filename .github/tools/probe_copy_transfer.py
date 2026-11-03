"""Inspect installed transfer bytecode and exercise Maya clipboard mapping."""
import dis
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
report = {"static": [], "batch": []}
folder = Path.home() / "Documents/maya/scripts/animBot/animPoseTransfer/__hybrid__"
for name in ("animPoseTransfer310.pyc", "animTransfer310.pyc", "poseTransfer310.pyc"):
    path = folder / name
    data = path.read_bytes()
    assert data[:4] == importlib.util.MAGIC_NUMBER
    pending = [marshal.loads(data[16:])]
    functions = []
    while pending:
        code = pending.pop()
        pending.extend(v for v in code.co_consts if isinstance(v, types.CodeType))
        if (any(t in code.co_name.lower() for t in ("copy", "paste", "layer", "insert"))
                or any(t in code.co_names for t in ("currentTime", "pasteKey", "copyKey", "animLayer"))):
            functions.append({"name": code.co_name, "names": code.co_names,
                              "strings": [v for v in code.co_consts if isinstance(v, str)],
                              "disassembly": dis.Bytecode(code).dis()})
    report["static"].append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                             "functions": functions})
from PySide2 import QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from AnimKey.core import animation_curve_transfer as transfer
for offset in (0, 30):
    cmds.file(new=True, force=True)
    jobs = []
    for i, attr in enumerate(("translateX", "rotateY", "scaleZ", "translateY")):
        source = cmds.createNode("transform", name="source%d" % i)
        target = cmds.createNode("transform", name="target%d" % i)
        for frame in (1+i, 8+i):
            cmds.setKeyframe(source, attribute=attr, time=frame, value=100*i+frame)
        jobs.append((target+"."+attr, transfer.capture_curve(source+"."+attr)))
    result = transfer.paste_curves_batch(jobs, time_offset=offset)
    report["batch"].append({"offset": offset, "results": result, "channels": [
        {"plug": plug, "expected": payload["values"],
         "actual": cmds.keyframe(plug, query=True, valueChange=True),
         "times": cmds.keyframe(plug, query=True, timeChange=True)} for plug, payload in jobs]})
(ROOT / "dist/copy-transfer-probe.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report["batch"], indent=2), flush=True)
for override in (False, True):
    cmds.file(new=True, force=True)
    node = cmds.createNode("transform", name="poseTarget")
    for t in (1, 50):
        cmds.setKeyframe(node, attribute="translateX", time=t, value=10)
    layer = cmds.animLayer("Action", override=override)
    cmds.animLayer(layer, edit=True, attribute=node + ".translateX")
    cmds.setAttr(layer + ".weight", .5)
    for other in cmds.ls(type="animLayer"):
        cmds.animLayer(other, edit=True, selected=other == layer, preferred=other == layer)
    cmds.currentTime(24)
    result = transfer.set_keys_on_layer([(node + ".translateX", 30)], 24, layer_name=layer)
    def state():
        return {"curves": [{"name": c, "times": cmds.keyframe(c, q=True, tc=True),
                            "values": cmds.keyframe(c, q=True, vc=True)} for c in cmds.ls(type="animCurve")],
                "value": cmds.getAttr(node + ".translateX"),
                "timedValue": cmds.getAttr(node + ".translateX", time=24)}
    print("POSE_PROBE", override, result, state(), flush=True)
    cmds.currentTime(24, edit=True, update=True)
    print("POSE_SAME_TIME", state(), flush=True)
    cmds.dgdirty(node)
    print("POSE_DIRTY_NODE", state(), flush=True)
    cmds.currentTime(23)
    cmds.currentTime(24)
    print("POSE_AFTER_TIME", state(), flush=True)
    cmds.dgdirty(allPlugs=True)
    print("POSE_AFTER_DIRTY", state(), flush=True)
