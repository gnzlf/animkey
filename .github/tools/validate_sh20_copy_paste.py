"""Validate production rig transfer in a disposable Maya session; never save."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PySide2 import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from AnimKey.buttons import copyAnimation as tool
from AnimKey.core import animation_curve_transfer as transfer
from AnimKey.core.executionGuard import animkey_execution

SCENE = r"X:\0066_0926_clash_royale_royal_interest\03_Production\Shots\SQ01\SH20\Scenefiles\3D Animation\3D Layout\SQ01-SH20_3D Layout_v0030.ma"
OUTPUT = ROOT / "dist/copy-paste-sh20-v0030.json"
REPORT = {"scene": SCENE, "sceneSaved": False, "cases": [], "passed": False}


def log(message):
    print("SH20_COPY: " + message, flush=True)
    OUTPUT.write_text(json.dumps(REPORT, indent=2), encoding="utf-8")


def digest():
    data = [(c, cmds.keyframe(c, q=True, tc=True), cmds.keyframe(c, q=True, vc=True))
            for c in sorted(cmds.ls(type="animCurve") or [])]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


def active(layer):
    for name in cmds.ls(type="animLayer") or []:
        cmds.animLayer(name, edit=True, selected=False, preferred=False)
    if layer:
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)


def invoke(operation, selected, frame):
    cmds.select(selected, replace=True)
    cmds.currentTime(frame)
    with animkey_execution("production_copy_validation"):
        operation()


def scalar_keys(plug, layer, pairs):
    node, attr = plug.rsplit(".", 1)
    for frame, value in pairs:
        kwargs = {"animLayer": layer, "noResolve": True} if layer != "BaseAnimation" else {}
        cmds.setKeyframe(node, attribute=attr, time=frame, value=value, **kwargs)


def controls(namespace):
    return [cmds.ls(namespace + ":" + name, long=True)[0]
            for name in ("FKIndexFinger1_L", "FKIndexFinger2_R", "IKArm_L")]


def compare_payload(plug, payload, layer, offset):
    curve = transfer.resolve_anim_curve(plug, layer_name=layer)
    if not curve:
        return [{"plug": plug, "reason": "missing curve", "layer": layer}]
    differences = []
    for frame, value in zip(payload["keyframes"], payload["values"]):
        actual = cmds.keyframe(curve, q=True, time=(frame + offset, frame + offset), vc=True) or []
        if not actual or abs(actual[0] - value) > 1e-5:
            differences.append({"plug": plug, "layer": layer, "time": frame + offset,
                                "expected": value, "actual": actual})
    # Compare shape between keys, including weighted tangent interpolation.
    temp = transfer.create_temp_curve(payload)
    try:
        times = payload["keyframes"]
        for a, b in zip(times, times[1:]):
            for fraction in (.25, .5, .75):
                frame = a + (b-a)*fraction
                expected = cmds.keyframe(temp, q=True, eval=True, time=(frame, frame))[0]
                actual = cmds.keyframe(curve, q=True, eval=True, time=(frame+offset, frame+offset))[0]
                if abs(actual-expected) > 1e-5:
                    differences.append({"plug": plug, "layer": layer, "sample": frame+offset,
                                        "expected": expected, "actual": actual})
    finally:
        cmds.delete(temp)
    return differences


def animation_case(source_ns, target_ns, kind, insert_frame):
    before = digest()
    case = {"source": source_ns, "destination": target_ns, "kind": kind,
            "operation": "insert" if insert_frame is not None else "paste", "differences": []}
    cmds.undoInfo(openChunk=True, chunkName="Copy transfer isolated validation")
    try:
        source, target = controls(source_ns), controls(target_ns)
        layer = "BaseAnimation"
        if kind != "base":
            layer = cmds.animLayer("AnimKeyCopyValidation", override=(kind == "override"))
            cmds.animLayer(layer, edit=True, attribute=[c + "." + attr for c in source + target
                           for attr in ("rotateZ", "translateX", "swivel") if cmds.objExists(c + "." + attr)])
        active(layer)
        for index, (src, dst) in enumerate(zip(source, target)):
            attr = "swivel" if index == 2 else "rotateZ"
            scalar_keys(src+"."+attr, layer, [(11, 13+index*5), (16, 28+index), (21, 41+index*2)])
            scalar_keys(dst+"."+attr, layer, [(1, -9), (15, -11), (70, 7)])
            curve = transfer.resolve_anim_curve(src+"."+attr, layer_name=layer)
            cmds.keyTangent(curve, edit=True, weightedTangents=True)
            cmds.keyTangent(curve, edit=True, time=(11, 21), lock=False,
                            inTangentType="fixed", outTangentType="fixed", inAngle=15, outAngle=-10)
        source_before = digest()
        with mock.patch.object(tool, "get_selected_time_range", return_value=(11, 21)):
            invoke(tool.copy_animation, source, 16)
        payload = copy.deepcopy(tool._anim_buffer)
        case["copiedCurves"] = tool._animation_payload_count(payload)
        if not payload:
            raise AssertionError("No production animation captured")
        offset = float(insert_frame - 11) if insert_frame is not None else 0.
        frame = insert_frame if insert_frame is not None else 33
        # Reverse destination selection to ensure mapping follows identity.
        invoke(tool.paste_insert_animation if insert_frame is not None else tool.paste_animation,
               list(reversed(target)), frame)
        assignments = tool.resolve_animation_target_assignments(payload, list(reversed(target)))
        source_layers = {layer_name for channels in payload.values() for stored in channels.values()
                         for layer_name, _ in tool._iter_curve_payloads(stored)}
        for source_key, destination in assignments:
            for attr, stored in payload[source_key].items():
                for original_layer, curve_data in tool._iter_curve_payloads(stored):
                    destination_layer = layer if len(source_layers) == 1 else original_layer
                    case["differences"].extend(compare_payload(destination+"."+attr, curve_data,
                                                               destination_layer, offset))
        case["currentFrame"] = cmds.currentTime(q=True)
        case["correctFrame"] = case["currentFrame"] == frame
        # Source snapshots must remain unchanged, including layer weights.
        captured_again, _ = tool.collect_animation_for_controls(source, time_range=(11, 21),
                                                                all_layers=(kind == "base"))
        case["sourceUnchanged"] = captured_again == payload
        for index, dst in enumerate(target):
            attr = "swivel" if index == 2 else "rotateZ"
            curve = transfer.resolve_anim_curve(dst+"."+attr, layer_name=layer)
            outside = [cmds.keyframe(curve, q=True, time=(f, f), vc=True)[0] for f in (1, 70)]
            if outside != [-9, 7]:
                case["differences"].append({"plug": dst+"."+attr, "outside": outside})
    finally:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
    case["undoRestored"] = digest() == before
    case["passed"] = (not case["differences"] and case["sourceUnchanged"]
                      and case["correctFrame"] and case["undoRestored"])
    REPORT["cases"].append(case)
    log("{} -> {} {} {}: {}".format(source_ns, target_ns, kind, case["operation"], case["passed"]))


def pose_case(source_ns, target_ns, kind):
    before = digest()
    case = {"source": source_ns, "destination": target_ns, "kind": kind,
            "operation": "pose", "differences": []}
    cmds.undoInfo(openChunk=True, chunkName="Pose transfer isolated validation")
    try:
        source, target = controls(source_ns), controls(target_ns)
        layer = "BaseAnimation"
        if kind != "base":
            layer = cmds.animLayer("AnimKeyPoseValidation", override=(kind == "override"))
            cmds.setAttr(layer+".weight", .5)
        active(layer)
        invoke(tool.copy_pose, source, 16)
        payload = copy.deepcopy(tool._pose_buffer["animation"])
        invoke(tool.paste_pose, list(reversed(target)), 37)
        assignments = tool.resolve_animation_target_assignments(payload, list(reversed(target)))
        compared = 0
        for source_key, dst in assignments:
            for attr, value in payload[source_key].items():
                plug = dst+"."+attr
                if not cmds.objExists(plug) or cmds.getAttr(plug, lock=True):
                    continue
                # Enum ranges/defaults differ between King and Goblin. Compare
                # shared continuous pose channels; custom swivel is included.
                if not (attr.startswith(("translate", "rotate", "scale")) or attr == "swivel"):
                    continue
                expected = value["values"][0]
                actual = cmds.getAttr(plug)
                compared += 1
                if abs(expected-actual) > 1e-4:
                    case["differences"].append({"plug": plug, "expected": expected, "actual": actual})
        case["comparedChannels"] = compared
        case["correctFrame"] = cmds.currentTime(q=True) == 37
    finally:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
    case["undoRestored"] = digest() == before
    case["passed"] = not case["differences"] and compared > 0 and case["correctFrame"] and case["undoRestored"]
    REPORT["cases"].append(case)
    log("{} -> {} {} pose: {}".format(source_ns, target_ns, kind, case["passed"]))


try:
    log("Opening requested scene with script nodes disabled")
    start = time.perf_counter()
    cmds.file(SCENE, open=True, force=True, prompt=False, executeScriptNodes=False)
    cmds.undoInfo(state=True)
    cmds.autoKeyframe(state=False)
    REPORT["loadSeconds"] = time.perf_counter()-start
    REPORT["layers"] = cmds.ls(type="animLayer") or []
    REPORT["mayaVersion"] = cmds.about(version=True)
    with tempfile.TemporaryDirectory(prefix="animkey_production_copy_") as directory:
        with mock.patch.object(tool, "get_user_data_folder", return_value=directory):
            for source, target in (("King_Rigging", "Goblin_Rigging"), ("Goblin_Rigging", "King_Rigging")):
                for kind in ("base", "additive", "override"):
                    for insert_frame in (None, 35, 55):
                        animation_case(source, target, kind, insert_frame)
                    pose_case(source, target, kind)
            with tool._json_write_lock:
                pass
    REPORT["passed"] = all(case["passed"] for case in REPORT["cases"])
except Exception:
    REPORT["error"] = traceback.format_exc()
    log(REPORT["error"])
finally:
    log("Finished without saving scene; passed=" + str(REPORT["passed"]))
sys.exit(0 if REPORT["passed"] else 1)
