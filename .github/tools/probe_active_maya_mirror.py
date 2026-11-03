"""Read-only inventory for the existing, initialized Maya/AnimBot session."""
import json
import inspect
from pathlib import Path
import sys

import maya.cmds as cmds
from animBot.mirror import mirror as loaded_mirror_module

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "dist" / "mirror-active-scene-readonly.json"
core = sys.modules.get("animBot._api.core") or next((module for name, module in list(sys.modules.items())
    if name.startswith("animBot._api.__hybrid__.core") and hasattr(module, "CORE")), None)
state = getattr(core, "CORE", None) if core else None
report = {
    "scene": cmds.file(query=True, sceneName=True),
    "modified": cmds.file(query=True, modified=True),
    "mayaVersion": cmds.about(version=True),
    "time": cmds.currentTime(query=True),
    "selection": cmds.ls(selection=True, long=True) or [],
    "layers": cmds.ls(type="animLayer") or [],
    "animBotCoreLoaded": core is not None,
    "animBotMirrorType": type(getattr(state, "mirror", None)).__name__,
    "animBotMirrorModuleLoaded": "animBot.mirror.mirror" in sys.modules,
    "animBotRuntimeModules": [name for name in sys.modules if name.startswith("animBot.")
                             and any(token in name for token in ("core", "mirror", "controller"))],
    "animKeyModule": getattr(sys.modules.get("AnimKey.buttons.mirror"), "__file__", None),
    "rigs": {},
}
mirror_module = sys.modules.get("animBot.mirror.mirror") or next((module for name, module in list(sys.modules.items())
    if name.startswith("animBot.mirror.__hybrid__.mirror") and hasattr(module, "Mirror")), None)
report["animBotResolvedCoreModule"] = getattr(core, "__name__", None)
report["animBotResolvedMirrorModule"] = getattr(mirror_module, "__name__", None)
if mirror_module:
    cls = getattr(mirror_module, "o_____0___O_O__l___O____0_____O_____l_o__0___O_o_O_____O____1", None)
    if cls:
        report["animBotAttributeConstructor"] = "signature unavailable"
        for constructor in (cls, cls.__init__):
            try:
                report["animBotAttributeConstructor"] = str(inspect.signature(constructor))
                break
            except (ValueError, TypeError):
                pass
path = Path(r"C:\Users\ANIM-gonzalo\Documents\maya\scripts\animBot_data\mirror_settings_snapshot.anb")
profiles = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
for namespace in ("King_Rigging", "Goblin_Rigging"):
    nodes = {}
    for name in ("IKArm_L", "IKArm_R", "IKLeg_L", "IKLeg_R"):
        found = cmds.ls(namespace + ":" + name, long=True) or []
        for node in found:
            attrs = {}
            for attr in ("swivel", "translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
                plug = node + "." + attr
                if cmds.objExists(plug):
                    attrs[attr] = {"value": cmds.getAttr(plug), "locked": cmds.getAttr(plug, lock=True),
                                   "settable": cmds.getAttr(plug, settable=True)}
            entry = {"attributes": attrs}
            if cmds.referenceQuery(node, isNodeReferenced=True):
                entry["referenceFile"] = cmds.referenceQuery(node, filename=True, unresolvedName=False)
            nodes[node] = entry
    profile = profiles.get(namespace, {})
    report["rigs"][namespace] = {"nodes": nodes, "savedSwivelCoefficients": {
        key: value for key, value in profile.items() if key.endswith(".swivel")}}
OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
print("ANIMKEY_READONLY_MIRROR_PROBE=" + str(OUTPUT))
