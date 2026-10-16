"""Isolated production-scene checks. Never saves or edits the source file."""
import json
import math
import os
import time
import unittest
from unittest import mock
if not os.environ.get("ANIMKEY_TEST_SCENE"):
    raise unittest.SkipTest("Set ANIMKEY_TEST_SCENE to run the isolated production test")
from AnimKey.mods.maya_compat import QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from AnimKey.core.animation_offset_session import OffsetSession
from AnimKey.core import animation_curve_transfer as transfer
from AnimKey.buttons import copyAnimation
from AnimKey.buttons import tempPivot


def pump():
    for _ in range(4):
        app.processEvents()


def assert_payload_equal(expected, actual, path=""):
    if isinstance(expected, dict):
        assert set(expected) == set(actual), (path, set(expected), set(actual))
        for key in expected:
            assert_payload_equal(expected[key], actual[key], path + "/" + str(key))
    elif isinstance(expected, list):
        assert len(expected) == len(actual), (path, len(expected), len(actual))
        for i, (a, b) in enumerate(zip(expected, actual)):
            assert_payload_equal(a, b, path + "/" + str(i))
    elif isinstance(expected, float):
        assert math.isclose(expected, actual, rel_tol=1e-6, abs_tol=1e-7), (path, expected, actual)
    else:
        assert expected == actual, (path, expected, actual)


def check_offset(control, layer, attr="translateX", requested_layer=None):
    plug = control + "." + attr
    curve = transfer.resolve_anim_curve(plug, layer_name=layer)
    assert curve, (plug, layer)
    times = cmds.keyframe(curve, query=True, timeChange=True)
    values = cmds.keyframe(curve, query=True, valueChange=True)
    inside = [t for t in times if 4 <= t <= 30]
    assert len(inside) > 2, (curve, inside)
    frame = inside[len(inside) // 2]
    cmds.currentTime(frame)
    cmds.select(control)
    session = OffsetSession()
    started = time.perf_counter()
    session.start([control], (4, 30), target_layer=requested_layer or layer)
    try:
        assert session.tracks, session.debug_snapshot()
        cmds.flushUndo()
        for delta in (0.25, -0.5):
            session.begin_pointer_gesture()
            cmds.keyframe(curve, edit=True, time=(frame, frame), relative=True, valueChange=delta)
            pump()
            session.end_pointer_gesture()
            actual = cmds.keyframe(curve, query=True, valueChange=True)
            for t, before, after in zip(times, values, actual):
                expected = before + delta if 4 <= t <= 30 else before
                assert abs(after - expected) < 1e-5, (curve, t, before, after, expected)
            session.begin_undo_guard()
            cmds.undo()
            pump()
            restored = cmds.keyframe(curve, query=True, valueChange=True)
            assert all(abs(a-b) < 1e-6 for a,b in zip(values, restored)), curve
        print("OFFSET_OK", control, layer, len(inside), round(time.perf_counter()-started, 3), flush=True)
    finally:
        session.stop(commit=False)


scene = os.environ["ANIMKEY_TEST_SCENE"]
cmds.file(scene, open=True, force=True, prompt=False, executeScriptNodes=False)
check_offset("Mama_Lucha_Rigging:AimEye_L", "BaseAnimation")
check_offset("Andresito_Rigging:AimEye_L", "BaseAnimation", requested_layer="AnimLayer1")
layer_controls = []
for layer in ("AnimLayer1", "OverlapHelper1", "OverlapHelper2"):
    for curve in cmds.animLayer(layer, query=True, animCurves=True) or []:
        times = cmds.keyframe(curve, query=True, timeChange=True) or []
        if len([t for t in times if 4 <= t <= 30]) < 3:
            continue
        plugs = transfer.driven_plugs_for_curve(curve)
        if not plugs or cmds.getAttr(plugs[0], type=True) not in ("double", "doubleLinear", "doubleAngle"):
            continue
        node, attr = plugs[0].rsplit(".", 1)
        check_offset(node, layer, attr=attr)
        layer_controls.append(node)
        break
controls = [ns + ":AimEye_" + side for ns in (
    "Mama_Lucha_Rigging", "Andrea_Rigging", "Andres_Rigging", "Andresito_Rigging"
) for side in ("L", "M", "R") if cmds.objExists(ns + ":AimEye_" + side)]
controls.extend(layer_controls)
started = time.perf_counter()
payload, sources = copyAnimation.collect_animation_for_controls(controls, all_layers=True)
metadata = json.loads(json.dumps(copyAnimation._anim_layer_metadata))
payload = json.loads(json.dumps(payload))
print("COPY_SECONDS", round(time.perf_counter()-started, 3), "CONTROLS", len(payload), flush=True)
assert len(payload) == len(controls), (len(payload), len(controls))
control = "Mama_Lucha_Rigging:cloak_neck_FK_01_ctrl"
cmds.currentTime(9)
state = tempPivot._capture_temp_pivot_state([control])
state["target_layer"] = "OverlapHelper2"
cmds.xform(control, objectSpace=True, preserve=True, pivots=(1., 0., 0.))
times = [8., 9., 10.]
expected = tempPivot._sample_temp_pivot_world_matrices([control], times)
with mock.patch.object(tempPivot, "_temp_pivot_sample_times", return_value=times):
    compensation, count = tempPivot._bake_temp_pivot_compensation(state)
assert count > 0, "Expected pivot compensation on animated cloak control"
destination = tempPivot._merge_temp_pivot_compensation(compensation, state)
assert destination == "OverlapHelper2"
actual = tempPivot._sample_temp_pivot_world_matrices([control], times)
assert_payload_equal(expected, actual, "TEMP_WORLD_MATRICES")
print("PRODUCTION_TEMP_MERGE_OK", flush=True)
cmds.file(new=True, force=True)
targets = {}
for source, channels in payload.items():
    name = sources[source].split("|")[-1]
    namespace = name.rsplit(":", 1)[0]
    if not cmds.namespace(exists=namespace):
        cmds.namespace(add=namespace)
        cmds.createNode("transform", name=namespace + "_target")
    node = cmds.createNode("transform", name=name, parent=namespace + "_target")
    targets[source] = node
    for attr in channels:
        if not cmds.objExists(node + "." + attr):
            cmds.addAttr(node, longName=attr, attributeType="double", keyable=True)
copyAnimation._anim_buffer = payload
copyAnimation._anim_layer_metadata = metadata
started = time.perf_counter()
copied, skipped = copyAnimation._paste_clip_to_targets(targets)
assert skipped == 0, skipped
print("PASTE_SECONDS", round(time.perf_counter()-started, 3), "CURVES", copied, flush=True)
recaptured, _ = copyAnimation.collect_animation_for_controls(list(targets.values()), all_layers=True)
for key, channels in payload.items():
    target_key = copyAnimation.get_control_storage_key(cmds.ls(targets[key], long=True)[0])
    for attr, original in channels.items():
        actual = recaptured[target_key][attr]
        assert_payload_equal(original, actual, key + "/" + attr)
print("PRODUCTION_ROUNDTRIP_OK", flush=True)
maya.standalone.uninitialize()
