"""Check AnimKey calibration against existing user-created AnimBot snapshots."""
import json
import hashlib
import os
from pathlib import Path
import sys
import time
import traceback

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from AnimKey.buttons import mirror

SCENE = os.environ.get("ANIMKEY_COMPARE_SCENE", r"X:\0066_0926_clash_royale_royal_interest\03_Production\Shots\SQ01\SH20\Scenefiles\3D Animation\3D Layout\SQ01-SH20_3D Layout_v0027.ma")
OUTPUT = ROOT / "dist" / os.environ.get("ANIMKEY_CALIBRATION_REPORT", "mirror-sh20-calibration.json")
CALIBRATION_FILE = ROOT / "dist/animbot-snapshot-backup/mirror_settings_snapshot.anb"
REFERENCE = json.loads(CALIBRATION_FILE.read_text())
REPORT = {"reference": "existing-user-created-animbot-snapshots", "sceneSaved": False, "rigs": {}}


def log(message):
    print("CALIBRATION_COMPARE: " + message, flush=True)
    OUTPUT.write_text(json.dumps(REPORT, indent=2), encoding="utf-8")


def animation_digest():
    records = []
    for curve in sorted(cmds.ls(type="animCurve") or []):
        records.append((curve,
            cmds.keyframe(curve, query=True, timeChange=True) or [],
            cmds.keyframe(curve, query=True, valueChange=True) or []))
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


def pose_cases(namespace, controls, snapshot, reference):
    results = []
    coefficients = {}
    for key, value in reference.items():
        coefficients[key.rsplit("|", 1)[-1]] = value
    for frame, side, moved, synthetic in ((1, "both", False, None), (11, "left", False, None),
                                (35, "right", False, None), (70, "both", False, None),
                                (35, "both", True, None), (11, "left", False, "fist"),
                                (11, "both", False, "swivel")):
        cmds.currentTime(frame, edit=True)
        selected = controls if side == "both" else [node for node in controls
                    if node.endswith("_L" if side == "left" else "_R")]
        expected = {}
        cmds.undoInfo(openChunk=True, chunkName="Mirror comparison temporary pose")
        try:
            if moved:
                root = controls[0].split("|")[1]
                cmds.setAttr(root + ".translate", 125.0, -40.0, 80.0)
                cmds.setAttr(root + ".rotate", 20.0, 65.0, -15.0)
            if synthetic == "fist":
                for node in selected:
                    if "FK" in node.rsplit("|", 1)[-1] and "Finger" in node.rsplit("|", 1)[-1]:
                        for axis, value in zip("XYZ", (9.0, -13.0, 45.0)):
                            if mirror._is_modifiable(node, "rotate" + axis):
                                cmds.setAttr(node + ".rotate" + axis, value)
            if synthetic == "swivel":
                for node in selected:
                    if mirror._attr_exists(node, "swivel") and mirror._is_modifiable(node, "swivel"):
                        cmds.setAttr(node + ".swivel", 17.0 if node.endswith("_L") else -23.0)
            plane = mirror._snap_sym_plane(snapshot)
            for source, target, _ in mirror._selected_mirror_directions(selected, snapshot, plane):
                short = source.rsplit("|", 1)[-1].split(":")[-1]
                for attr in (mirror._snap_ctrl_data(source, snapshot) or {}).get("attr_map", {}):
                    coefficient = coefficients.get(short + "." + attr)
                    if coefficient is None or attr in mirror.ATTRIBUTES_TO_IGNORE:
                        continue
                    if mirror._attr_exists(target, attr) and mirror._is_modifiable(target, attr):
                        expected[target + "." + attr] = cmds.getAttr(source + "." + attr) * coefficient
            start = time.perf_counter()
            changed, _ = mirror._mirror_selected_pose_two_phase(selected, snapshot, plane)
            seconds = time.perf_counter() - start
            differences = [{"attribute": plug, "expected": value, "actual": cmds.getAttr(plug)}
                           for plug, value in expected.items() if abs(cmds.getAttr(plug) - value) > 1e-6]
            results.append({"frame": frame, "side": side, "rigMovedAndRotated": moved,
                            "syntheticPose": synthetic,
                            "seconds": seconds, "changedChannels": changed,
                            "comparedChannels": len(expected), "differences": differences})
            log("{} frame {} {} moved={}: {} pose differences / {} channels ({:.3f}s)".format(
                namespace, frame, side, moved, len(differences), len(expected), seconds))
        finally:
            cmds.undoInfo(closeChunk=True)
            cmds.undo()
    return results


def animation_cases(namespace, controls, snapshot):
    results = []
    for kind in ("base", "additive", "override"):
        for short, attr in (("IKArm_L", "swivel"), ("FKIndexFinger1_L", "rotateZ")):
            source = next((node for node in controls if node.endswith(":" + short)), None)
            if source is None:
                raise RuntimeError("Missing production test control: " + namespace + ":" + short)
            before = animation_digest()
            cmds.undoInfo(openChunk=True, chunkName="Mirror animation production test")
            try:
                target = mirror._snap_opposite(source, snapshot)
                layer = None
                if kind != "base":
                    layer = cmds.animLayer("AnimKeyMirrorValidation", override=(kind == "override"))
                    cmds.select([source, target], replace=True)
                    cmds.animLayer(layer, edit=True, addSelectedObjects=True)
                for other in cmds.ls(type="animLayer") or []:
                    active = other == (layer or "BaseAnimation")
                    cmds.animLayer(other, edit=True, selected=active, preferred=active)
                active_layer = mirror.curve_transfer.active_animation_layer()
                if (active_layer != layer and not (
                        kind == "base" and mirror.curve_transfer.is_base_layer(active_layer))):
                    raise RuntimeError("Wrong active animation layer in test")
                kwargs = {"animLayer": layer} if layer else {}
                for frame, value in ((11, 17.0), (21, 29.0)):
                    cmds.setKeyframe(source, attribute=attr, time=frame, value=value, **kwargs)
                for frame, value in ((1, -8.0), (70, 13.0)):
                    cmds.setKeyframe(target, attribute=attr, time=frame, value=value, **kwargs)
                source_curve = mirror.curve_transfer.resolve_anim_curve(source + "." + attr, layer_name=layer)
                target_curve = mirror.curve_transfer.resolve_anim_curve(target + "." + attr, layer_name=layer)
                outside_before = [cmds.keyframe(target_curve, query=True, time=(frame, frame), valueChange=True)[0]
                                  for frame in (1, 70)]
                cmds.keyTangent(source_curve, edit=True, weightedTangents=True)
                cmds.keyTangent(source_curve, edit=True, time=(11, 21), lock=False,
                                inTangentType="fixed", outTangentType="fixed", inAngle=15, outAngle=25)
                jobs = mirror._animation_mirror_channel_jobs([source], snapshot,
                    mirror._snap_sym_plane(snapshot), [attr])
                job = next(job for job in jobs if job["source_attr"] == attr)
                samples = (11, 12, 15, 20, 21)
                expected = [cmds.keyframe(source_curve, query=True, eval=True, time=(frame, frame))[0]
                            * job["mult"] for frame in samples]
                start = time.perf_counter()
                keys = mirror._transfer_animation_jobs(jobs, (11, 21))
                seconds = time.perf_counter() - start
                actual = [cmds.keyframe(target_curve, query=True, eval=True, time=(frame, frame))[0]
                          for frame in samples]
                outside = [cmds.keyframe(target_curve, query=True, time=(frame, frame), valueChange=True)[0]
                           for frame in (1, 70)]
                result = {"layer": kind, "control": short, "attribute": attr,
                          "keysTransferred": keys, "seconds": seconds,
                          "sampleErrors": [abs(a - b) for a, b in zip(expected, actual)],
                          "outsideRangeBefore": outside_before,
                          "outsideRangeValues": outside,
                          "outsideRangePreserved": all(abs(a - b) < 1e-8
                              for a, b in zip(outside, outside_before))}
                results.append(result)
                log("{} {} {}: max sample error {}, outside range {}".format(namespace,
                    kind, attr, max(result["sampleErrors"]), result["outsideRangePreserved"]))
            finally:
                cmds.undoInfo(closeChunk=True)
                cmds.undo()
            results[-1]["originalKeysRestored"] = animation_digest() == before
    return results


try:
    cmds.file(SCENE, open=True, force=True, prompt=False, executeScriptNodes=False)
    cmds.undoInfo(state=True)
    cmds.autoKeyframe(state=False)
    original_animation = animation_digest()
    for namespace in ("King_Rigging", "Goblin_Rigging"):
        controls = mirror._control_candidates_in_namespace(namespace)
        log("{}: snapshot on {} controls".format(namespace, len(controls)))
        start = time.perf_counter()
        snapshot = mirror._create_default_pose_mirror_snapshot(
            controls, show_message=False, expand_controls=False, persist=False,
            return_snapshot=True, print_report=True,
            calibration_file=str(CALIBRATION_FILE))
        (ROOT / "dist" / (namespace + "-precise-snapshot.json")).write_text(json.dumps(snapshot, indent=2))
        differences = []
        compared = 0
        reference = REFERENCE[namespace]
        for node in controls:
            data = mirror._snap_ctrl_data(node, snapshot) or {}
            short = node.rsplit("|", 1)[-1].split(":")[-1]
            for attr, spec in data.get("attr_map", {}).items():
                keys = [key for key in reference if key.endswith("|" + short + "." + attr)]
                if len(keys) != 1:
                    continue
                compared += 1
                expected = reference[keys[0]]
                if spec.get("target") != attr or spec.get("mult") != expected:
                    differences.append({"control": short, "attribute": attr,
                                        "animbot": expected, "animkey": spec})
        REPORT["rigs"][namespace] = {"seconds": time.perf_counter() - start,
                                    "compared": compared, "differences": differences,
                                    "savedCalibration": snapshot.get("saved_calibration")}
        log("{}: {} differences / {} channels".format(namespace, len(differences), compared))
        REPORT["rigs"][namespace]["poseCases"] = pose_cases(namespace, controls, snapshot, reference)
        REPORT["rigs"][namespace]["animationCases"] = animation_cases(namespace, controls, snapshot)
        REPORT["animationKeysPreserved"] = animation_digest() == original_animation
        log("Animation keys preserved: " + str(REPORT["animationKeysPreserved"]))
except Exception:
    REPORT["error"] = traceback.format_exc()
    log(REPORT["error"])
REPORT["passed"] = bool(not REPORT.get("error") and len(REPORT["rigs"]) == 2
    and REPORT.get("animationKeysPreserved")
    and all(not rig["differences"] and len(rig.get("poseCases", [])) == 7
        and all(not case["differences"] for case in rig["poseCases"])
        and len(rig.get("animationCases", [])) == 6
        and all(case["keysTransferred"] > 0 and max(case["sampleErrors"]) < 1e-6
                and case["outsideRangePreserved"] and case["originalKeysRestored"]
                for case in rig["animationCases"])
        for rig in REPORT["rigs"].values()))
log("Finished without saving production scene or changing AnimBot data")
sys.exit(0 if REPORT["passed"] else 1)
