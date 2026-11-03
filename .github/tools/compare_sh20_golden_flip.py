"""Compare the user's original/flipped rigs without changing the saved scene."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
import time

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from AnimKey.buttons import mirror
from AnimKey.core.executionGuard import animkey_execution

SCENE = r"X:\0066_0926_clash_royale_royal_interest\03_Production\Shots\SQ01\SH20\Scenefiles\3D Animation\3D Layout\SQ01-SH20_3D Layout_v0031.ma"
OUTPUT = ROOT / "dist" / os.environ.get("ANIMKEY_GOLDEN_REPORT", "mirror-v0031-golden-flip.json")
SOURCE = "King_Rigging"
TARGET = "King_Rigging1"
report = {"scene": SCENE, "sceneSaved": False, "source": SOURCE, "target": TARGET}


def save(message):
    print("GOLDEN_FLIP: " + message, flush=True)
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")


def by_path(controls):
    return {tuple(mirror._namespace_free_parts(node)): node for node in controls}


def animation_digest():
    values = [(curve, cmds.keyframe(curve, query=True, timeChange=True) or [],
               cmds.keyframe(curve, query=True, valueChange=True) or [])
              for curve in sorted(cmds.ls(type="animCurve") or [])]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def engine_cases(snapshot, source_controls, target_controls, times):
    controls_by_namespace = {SOURCE: source_controls, TARGET: target_controls}
    mirror._write_snapshot_file(mirror.get_mirror_snapshot_file(
        mirror.get_rig_identifier(source_controls)), snapshot)
    mirror.clear_mirror_cache()
    report["preparedRoots"] = {}
    for namespace in (SOURCE, TARGET):
        prepared = mirror._prepared_snapshot_groups([namespace + ":King_rig"])
        report["preparedRoots"][namespace] = [{
            "controlCount": len(item["info"]["controls"]),
            "quickFallback": item["is_basic_snapshot"],
            "sharedFrom": item["snapshot"].get("shared_from")}
            for item in prepared]
    save("Prepared actual root selections: " + json.dumps(report["preparedRoots"]))
    paths = {namespace: by_path(controls) for namespace, controls in controls_by_namespace.items()}
    pairs = []
    for path in paths[SOURCE].keys() & paths[TARGET].keys():
        a, b = paths[SOURCE][path], paths[TARGET][path]
        for attr in set(mirror._scalar_keyable_attrs(a)) & set(mirror._scalar_keyable_attrs(b)):
            pairs.append((a + "." + attr, b + "." + attr))
    original_keys = animation_digest()
    cmds.autoKeyframe(state=False)
    expected = {}
    pose_cases = []
    report["enginePoseCases"] = pose_cases
    animation_only = os.environ.get("ANIMKEY_GOLDEN_ANIMATION_ONLY") == "1"
    report["animationOnly"] = animation_only
    for frame in times:
        cmds.currentTime(frame, edit=True)
        expected[frame] = {SOURCE: [cmds.getAttr(a) for a, _ in pairs],
                           TARGET: [cmds.getAttr(b) for _, b in pairs]}
        if animation_only:
            continue
        for index, namespace in enumerate((SOURCE, TARGET)):
            other = TARGET if namespace == SOURCE else SOURCE
            cmds.select(namespace + ":King_rig", replace=True)
            cmds.undoInfo(openChunk=True, chunkName="Golden flip pose")
            try:
                started = time.perf_counter()
                with animkey_execution("Regression", "Golden flip pose"):
                    mirror.mirror()
                seconds = time.perf_counter() - started
                actual = [cmds.getAttr(pair[index]) for pair in pairs]
                differences = [{"plug": pairs[i][index], "actual": value,
                                "expected": expected[frame][other][i]}
                               for i, value in enumerate(actual)
                               if abs(value - expected[frame][other][i]) > 1e-5]
                pose_cases.append({"frame": frame, "namespace": namespace,
                    "seconds": seconds, "comparedChannels": len(pairs),
                    "differenceCount": len(differences), "differences": differences[:30]})
                save("{} frame {}: {} actual pose differences".format(namespace, frame, len(differences)))
            finally:
                cmds.undoInfo(closeChunk=True)
                cmds.undo()
    report["poseUndoPreservedKeys"] = original_keys == animation_digest()
    layer_names = cmds.ls(type="animLayer") or []
    animation_cases = []
    report["engineAnimationCases"] = animation_cases
    for index, namespace in enumerate((SOURCE, TARGET)):
        other = TARGET if namespace == SOURCE else SOURCE
        cmds.select(namespace + ":King_rig", replace=True)
        cmds.undoInfo(openChunk=True, chunkName="Golden flip all layers")
        try:
            started = time.perf_counter()
            for layer in layer_names or [None]:
                for name in layer_names:
                    cmds.animLayer(name, edit=True, selected=(name == layer), preferred=(name == layer))
                with animkey_execution("Regression", "Golden flip animation"):
                    mirror.all_mirror()
            elapsed = time.perf_counter() - started
            differences = []
            for frame in times:
                cmds.currentTime(frame, edit=True)
                for i, pair in enumerate(pairs):
                    value = cmds.getAttr(pair[index])
                    reference = expected[frame][other][i]
                    if abs(value - reference) > 1e-5:
                        differences.append({"frame": frame, "plug": pair[index],
                                            "actual": value, "expected": reference})
            animation_cases.append({"namespace": namespace, "seconds": elapsed,
                "layers": layer_names, "differenceCount": len(differences),
                "differences": differences[:60]})
            save("{}: {} actual animation differences".format(namespace, len(differences)))
        finally:
            cmds.undoInfo(closeChunk=True)
            cmds.undo()
    report["animationUndoPreservedKeys"] = original_keys == animation_digest()
    report["passed"] = bool(report["poseUndoPreservedKeys"] and report["animationUndoPreservedKeys"]
        and not any(case["differenceCount"] for case in pose_cases + animation_cases)
        and not any(item["quickFallback"] for groups in report["preparedRoots"].values() for item in groups))


try:
    cmds.file(SCENE, open=True, force=True, prompt=False, executeScriptNodes=False)
    source_controls = mirror._control_candidates_in_namespace(SOURCE)
    target_controls = mirror._control_candidates_in_namespace(TARGET)
    source_paths, target_paths = by_path(source_controls), by_path(target_controls)
    report["controlCounts"] = {SOURCE: len(source_controls), TARGET: len(target_controls)}
    report["layers"] = [{"name": name, "mute": cmds.getAttr(name + ".mute"),
                         "weight": cmds.getAttr(name + ".weight")}
                        for name in cmds.ls(type="animLayer") or []]
    snapshot = json.loads((ROOT / "dist/King_Rigging-precise-snapshot.json").read_text())
    report["profileKind"] = snapshot.get("profile_kind")
    report["profileCalibration"] = snapshot.get("saved_calibration")
    report["roots"] = {}
    for namespace in (SOURCE, TARGET):
        root = namespace + ":King_rig"
        report["roots"][namespace] = {"matrix": cmds.xform(root, query=True, worldSpace=True, matrix=True),
                                     "reference": cmds.referenceQuery(root, filename=True)}
    times = sorted(set(cmds.keyframe(source_controls + target_controls,
                                    query=True, timeChange=True) or []))
    first, last = cmds.playbackOptions(query=True, min=True), cmds.playbackOptions(query=True, max=True)
    times = sorted(set([first, last, cmds.currentTime(query=True)] +
                       [frame for frame in times if first <= frame <= last]))
    if len(times) > 240:
        times = sorted(set(times[::max(1, len(times) // 200)] + [times[-1]]))
    report["frames"] = times
    save("{} / {} controls; {} frames".format(len(source_controls), len(target_controls), len(times)))
    channels = []
    for source in source_controls:
        data = mirror._snap_ctrl_data(source, snapshot) or {}
        opposite = mirror._snap_opposite(source, snapshot)
        if opposite:
            destination = target_paths.get(tuple(mirror._namespace_free_parts(opposite)))
        elif data.get("type") == "central":
            destination = target_paths.get(tuple(mirror._namespace_free_parts(source)))
        else:
            continue
        if not destination:
            continue
        for attr, spec in data.get("attr_map", {}).items():
            target_attr = spec.get("target", attr)
            if attr in mirror.ATTRIBUTES_TO_IGNORE or not cmds.objExists(destination + "." + target_attr):
                continue
            channels.append({"source": source + "." + attr,
                             "target": destination + "." + target_attr,
                             "multiplier": spec["mult"], "values": []})
    report["comparedChannels"] = len(channels)
    for frame in times:
        cmds.currentTime(frame, edit=True)
        for channel in channels:
            a = cmds.getAttr(channel["source"])
            b = cmds.getAttr(channel["target"])
            channel["values"].append((frame, a, b))
    differences = []
    for channel in channels:
        values = channel["values"]
        sign = channel["multiplier"]
        max_error = max(abs(a * sign - b) for _, a, b in values)
        if max_error <= 1e-5:
            continue
        positive_error = max(abs(a - b) for _, a, b in values)
        negative_error = max(abs(-a - b) for _, a, b in values)
        samples = [(frame, a, b, a * sign) for frame, a, b in values if abs(a * sign - b) > 1e-5]
        differences.append({"source": channel["source"], "target": channel["target"],
                            "profileMultiplier": sign, "maxError": max_error,
                            "positiveError": positive_error, "negativeError": negative_error,
                            "samples": samples[:6]})
    report["differences"] = sorted(differences, key=lambda item: item["maxError"], reverse=True)
    report["differenceCounts"] = dict(Counter(item["target"].rsplit(".", 1)[-1] for item in differences))
    report["channels"] = channels
    save("{} channel differences / {}".format(len(differences), len(channels)))
    if os.environ.get("ANIMKEY_GOLDEN_EXECUTE") == "1":
        engine_cases(snapshot, source_controls, target_controls, times)
except Exception:
    report["error"] = traceback.format_exc()
    save(report["error"])
save("Finished without saving the scene")
sys.exit(1 if report.get("error") or report.get("passed") is False else 0)
