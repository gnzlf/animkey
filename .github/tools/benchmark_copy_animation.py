"""Benchmark AnimKey copy/paste against a Maya scene without saving it.

The script opens the source scene in Maya standalone, discovers animated
transforms in the requested rig namespace, runs AnimKey's public copy/paste
entry points, and compares the serialized curves before and after the paste.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import time


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("scene")
    parser.add_argument("rig", help="Rig root or namespace-qualified rig name")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--layer", default=None)
    parser.add_argument("--pose-only", action="store_true")
    return parser.parse_args()


def _numeric_equal(left, right, tolerance=1e-7):
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    return left == right


def _compare_value(left, right, path="", mismatches=None):
    mismatches = [] if mismatches is None else mismatches
    if isinstance(left, dict) and isinstance(right, dict):
        ignored = {"source_layer"}
        keys = (set(left) | set(right)) - ignored
        for key in sorted(keys):
            child_path = "{}.{}".format(path, key) if path else key
            if key not in left or key not in right:
                mismatches.append(child_path + " (missing)")
                continue
            _compare_value(left[key], right[key], child_path, mismatches)
    elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            mismatches.append("{} (length {} != {})".format(path, len(left), len(right)))
        else:
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                _compare_value(left_item, right_item, "{}[{}]".format(path, index), mismatches)
    elif not _numeric_equal(left, right):
        mismatches.append("{} ({} != {})".format(path, left, right))
    return mismatches


def _namespace_from_rig(rig):
    leaf = rig.rsplit("|", 1)[-1]
    return leaf.rsplit(":", 1)[0] if ":" in leaf else ""


def main():
    args = _parse_args()
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import maya.standalone

    maya.standalone.initialize(name="python")
    import maya.cmds as cmds

    started = time.perf_counter()
    cmds.file(args.scene, open=True, force=True, prompt=False, loadReferenceDepth="all")
    scene_load_seconds = time.perf_counter() - started
    if args.layer:
        for layer_name in cmds.ls(type="animLayer") or []:
            try:
                cmds.animLayer(layer_name, edit=True, selected=False, preferred=False)
            except Exception:
                pass
        cmds.animLayer(args.layer, edit=True, selected=True, preferred=True)

    rig_matches = cmds.ls(args.rig, long=True) or []
    namespace = _namespace_from_rig(args.rig)
    namespace_pattern = "{}:*".format(namespace) if namespace else "*"
    namespace_transforms = cmds.ls(namespace_pattern, type="transform", long=True) or []
    curves = cmds.ls(type=("animCurveTA", "animCurveTL", "animCurveTU")) or []

    from AnimKey.core import animation_curve_transfer as curve_transfer

    animated_controls = []
    seen = set()
    for curve in curves:
        for plug in curve_transfer.driven_plugs_for_curve(curve):
            node = plug.rsplit(".", 1)[0]
            long_names = cmds.ls(node, long=True, type="transform") or []
            if not long_names:
                continue
            control = long_names[0]
            if namespace:
                leaf = control.rsplit("|", 1)[-1]
                if not leaf.startswith(namespace + ":"):
                    continue
            if control not in seen:
                seen.add(control)
                animated_controls.append(control)

    active_layer = curve_transfer.active_animation_layer()
    try:
        active_curves = (
            cmds.animLayer(active_layer, query=True, animCurves=True) or []
            if active_layer and not curve_transfer.is_base_layer(active_layer)
            else curves
        )
    except Exception:
        active_curves = curves
    active_controls = []
    active_seen = set()
    for curve in active_curves:
        for plug in curve_transfer.driven_plugs_for_curve(curve):
            node = plug.rsplit(".", 1)[0]
            long_names = cmds.ls(node, long=True, type="transform") or []
            if not long_names:
                continue
            control = long_names[0]
            leaf = control.rsplit("|", 1)[-1]
            if namespace and not leaf.startswith(namespace + ":"):
                continue
            if control not in active_seen:
                active_seen.add(control)
                active_controls.append(control)

    report = {
        "scene": args.scene,
        "scene_load_seconds": scene_load_seconds,
        "rig_matches": rig_matches,
        "namespace": namespace,
        "namespace_transform_count": len(namespace_transforms),
        "scene_anim_curve_count": len(curves),
        "animated_control_count": len(animated_controls),
        "active_layer_control_count": len(active_controls),
    }
    if args.inspect:
        report["active_animation_layer"] = active_layer
        layer_names = cmds.ls(type="animLayer") or []
        report["animation_layers"] = []
        for layer_name in layer_names:
            layer_started = time.perf_counter()
            try:
                layer_curves = cmds.animLayer(layer_name, query=True, animCurves=True) or []
            except Exception:
                layer_curves = []
            report["animation_layers"].append({
                "name": layer_name,
                "curve_count": len(layer_curves),
                "query_seconds": time.perf_counter() - layer_started,
            })
        query_started = time.perf_counter()
        try:
            selected_curves = cmds.keyframe(animated_controls, query=True, name=True) or []
        except Exception as exc:
            selected_curves = []
            report["selected_curve_query_error"] = str(exc)
        report["selected_curve_query_count"] = len(set(selected_curves))
        report["selected_curve_query_seconds"] = time.perf_counter() - query_started
        report["animated_controls_sample"] = animated_controls[:20]
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    benchmark_controls = active_controls or animated_controls
    if not benchmark_controls:
        report["error"] = "No animated controls found"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    from AnimKey.buttons import copyAnimation
    from AnimKey.core.executionGuard import animkey_execution

    if args.pose_only:
        cmds.select(benchmark_controls, replace=True)
        started = time.perf_counter()
        with animkey_execution(source="benchmark", action="copy_pose"):
            copyAnimation.copy_pose()
        report["copy_pose_seconds"] = time.perf_counter() - started
        pose_payload = copy.deepcopy((copyAnimation._pose_buffer or {}).get("animation", {}))
        report["pose_control_count"] = len(pose_payload)
        report["pose_channel_count"] = sum(len(channels) for channels in pose_payload.values())

        cmds.select(benchmark_controls, replace=True)
        started = time.perf_counter()
        with animkey_execution(source="benchmark", action="paste_pose"):
            copyAnimation.paste_pose()
        report["paste_pose_seconds"] = time.perf_counter() - started

        target_map = copyAnimation.resolve_target_controls(pose_payload.keys(), benchmark_controls)
        pose_mismatches = []
        for control_key, channels in pose_payload.items():
            target = target_map.get(control_key)
            if not target:
                pose_mismatches.append(control_key + " (unresolved)")
                continue
            for attr, curve_data in channels.items():
                expected = copyAnimation._extract_pose_value(curve_data)
                try:
                    actual = cmds.getAttr("{}.{}".format(target, attr))
                except Exception:
                    pose_mismatches.append("{}.{} (unreadable)".format(control_key, attr))
                    continue
                if not _numeric_equal(actual, expected):
                    pose_mismatches.append("{}.{} ({} != {})".format(
                        control_key, attr, actual, expected,
                    ))
        report["pose_fidelity_match"] = not pose_mismatches
        report["pose_mismatch_count"] = len(pose_mismatches)
        report["pose_mismatch_sample"] = pose_mismatches[:20]
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not pose_mismatches else 4

    cmds.select(benchmark_controls, replace=True)
    started = time.perf_counter()
    with animkey_execution(source="benchmark", action="copy_animation"):
        copyAnimation.copy_animation()
    report["copy_seconds"] = time.perf_counter() - started
    original_payload = copy.deepcopy(copyAnimation._anim_buffer)
    report["copied_control_count"] = len(original_payload)
    report["copied_channel_count"] = sum(len(channels) for channels in original_payload.values())
    report["copied_key_count"] = sum(
        len(curve.get("keyframes", []))
        for channels in original_payload.values()
        for curve in channels.values()
    )

    cmds.select(benchmark_controls, replace=True)
    started = time.perf_counter()
    with animkey_execution(source="benchmark", action="paste_animation"):
        copyAnimation.paste_animation()
    report["paste_seconds"] = time.perf_counter() - started

    target_map = copyAnimation.resolve_target_controls(original_payload.keys(), benchmark_controls)
    recaptured = {}
    for control_key, channels in original_payload.items():
        target = target_map.get(control_key)
        if not target:
            continue
        recaptured[control_key] = {}
        for channel in channels:
            snapshot = copyAnimation.collect_curve_snapshot("{}.{}".format(target, channel))
            if snapshot:
                recaptured[control_key][channel] = snapshot

    mismatches = _compare_value(original_payload, recaptured)
    report["fidelity_match"] = not mismatches
    report["mismatch_count"] = len(mismatches)
    report["mismatch_sample"] = mismatches[:20]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not mismatches else 3


if __name__ == "__main__":
    raise SystemExit(main())
