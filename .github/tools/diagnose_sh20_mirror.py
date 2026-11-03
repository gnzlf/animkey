"""Production-scene mirror checks in an isolated Maya process; never saves scene."""
import json
import os
import time

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from AnimKey.buttons import mirror

SCENE = r"X:\0066_0926_clash_royale_royal_interest\03_Production\Shots\SQ01\SH20\Scenefiles\3D Animation\3D Layout\SQ01-SH20_3D Layout_v0027.ma"
OUTPUT = os.environ["ANIMKEY_MIRROR_REPORT"]


def log(message):
    print("SH20_MIRROR: " + message, flush=True)


log("Opening scene without executing script nodes")
started = time.perf_counter()
cmds.file(SCENE, open=True, force=True, prompt=False, executeScriptNodes=False)
log("Loaded in {:.2f}s".format(time.perf_counter() - started))
report = {"scene": SCENE, "currentTime": cmds.currentTime(query=True),
          "layers": cmds.ls(type="animLayer") or [], "rigs": {}}
for namespace in ("King_Rigging", "Goblin_Rigging"):
    started = time.perf_counter()
    controls = mirror._control_candidates_in_namespace(namespace)
    log("{}: {} controls".format(namespace, len(controls)))
    basic = mirror._build_basic_mirror_snapshot(controls)
    pairs = {}
    for control in controls:
        opposite = mirror._snap_opposite(control, basic)
        if opposite and opposite != control:
            pairs[control] = opposite
    details = []
    for control in controls:
        name = control.rsplit("|", 1)[-1]
        if any(token in name.lower() for token in ("finger", "thumb", "index", "hand", "wrist", "middle", "pinky")):
            details.append({"control": control, "opposite": pairs.get(control),
                            "attributes": mirror._capture_rest_attrs(control),
                            "profile": mirror._snap_ctrl_data(control, basic)})
    report["rigs"][namespace] = {"controls": controls, "pairs": pairs, "handDetails": details,
                                   "basicSeconds": time.perf_counter() - started}
    log("{}: {} mapped controls; {} hand controls".format(namespace, len(pairs), len(details)))
with open(OUTPUT, "w", encoding="utf-8") as stream:
    json.dump(report, stream, indent=2)
log("Inventory written to " + OUTPUT)
