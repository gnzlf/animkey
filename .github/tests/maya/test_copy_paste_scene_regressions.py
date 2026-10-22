"""Copy/paste regression coverage using Maya's real layer evaluator."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.environ.get("ANIMKEY_TEST_ROOT", str(ROOT)))
from PySide2 import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import copyAnimation as tool
from AnimKey.core import animation_curve_transfer as transfer
from AnimKey.core.executionGuard import animkey_execution


def active(layer):
    for name in cmds.ls(type="animLayer") or []:
        cmds.animLayer(name, edit=True, selected=False, preferred=False)
    if layer:
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)


def control(namespace, name="ctrl"):
    if not cmds.namespace(exists=namespace):
        cmds.namespace(add=namespace)
    return cmds.createNode("transform", name=namespace + ":" + name)


def keys(node, attr, values, layer=None):
    for frame, value in values:
        kwargs = {"animLayer": layer, "noResolve": True} if layer else {}
        cmds.setKeyframe(node, attribute=attr, time=frame, value=value, **kwargs)


def curve_values(node, attr, layer="BaseAnimation"):
    curve = transfer.resolve_anim_curve(node + "." + attr, layer_name=layer)
    return (cmds.keyframe(curve, query=True, timeChange=True) or [],
            cmds.keyframe(curve, query=True, valueChange=True) or []) if curve else ([], [])


class CopyPasteSceneRegressionTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        cmds.autoKeyframe(state=False)
        self.directory = tempfile.TemporaryDirectory(prefix="animkey_copy_test_")
        self.patch = mock.patch.object(tool, "get_user_data_folder", return_value=self.directory.name)
        self.patch.start()
        tool._cleanup_buffer()
        tool._pose_buffer = None
        tool._pose_buffer_is_current = False
        tool._last_disk_mtime = 0

    def tearDown(self):
        # Finish cache workers before their temporary destination disappears.
        with tool._json_write_lock:
            pass
        self.patch.stop()
        self.directory.cleanup()

    def invoke(self, operation, selection, frame=None):
        cmds.select(selection, replace=True)
        if frame is not None:
            cmds.currentTime(frame)
        with animkey_execution("copy_paste_regression"):
            operation()

    def test_unrelated_graph_key_selection_cannot_replace_selected_rig(self):
        old = control("old")
        source = control("source")
        keys(old, "translateX", [(1, 99), (5, 199)])
        keys(source, "translateX", [(1, 3), (5, 7)])
        cmds.selectKey(old, replace=True)
        self.invoke(tool.copy_animation, source)
        self.assertEqual(list(tool._anim_buffer), [tool.get_control_storage_key(source)])
        self.assertEqual(next(iter(tool._anim_buffer.values()))["translateX"]["values"], [3, 7])

    def test_flash_buttons_resolve_copy_paste_and_file_actions(self):
        from AnimKey.buttons import flashbuttons
        import importlib
        actions = flashbuttons.get_available_actions()
        for label in ("Copy Animation", "Paste Animation", "Paste Insert", "Paste Flipped",
                      "Copy Pose", "Paste Pose", "Save Animation", "Load Animation",
                      "Save Pose", "Load Pose"):
            module_name, function_name = actions[label]
            self.assertTrue(callable(getattr(importlib.import_module(module_name), function_name)))

    def test_copy_layer_pastes_to_selected_destination_layer_only(self):
        for override in (False, True):
            with self.subTest(override=override):
                cmds.file(new=True, force=True)
                source, target = control("source"), control("target")
                keys(source, "translateX", [(1, 10), (10, 20)])
                keys(target, "translateX", [(1, 50), (10, 60)])
                source_layer = cmds.animLayer("SourceAction")
                destination = cmds.animLayer("DestinationAction", override=override)
                cmds.animLayer(source_layer, edit=True, attribute=source + ".translateX")
                cmds.animLayer(destination, edit=True, attribute=target + ".translateX")
                keys(source, "translateX", [(1, 3), (10, 9)], source_layer)
                keys(target, "translateX", [(1, -2), (10, -4)], destination)
                before_base = curve_values(target, "translateX")
                before_source = curve_values(source, "translateX", source_layer)
                active(source_layer)
                self.invoke(tool.copy_animation, source)
                active(destination)
                self.invoke(tool.paste_animation, target)
                self.assertEqual(curve_values(target, "translateX", destination), before_source)
                self.assertEqual(curve_values(target, "translateX"), before_base)
                self.assertEqual(curve_values(source, "translateX", source_layer), before_source)
                self.assertEqual(transfer.active_animation_layer(), destination)

    def test_pose_one_control_repeats_on_multiple_targets_at_playhead(self):
        source = control("source")
        targets = [control("targetA"), control("targetB")]
        cmds.setAttr(source + ".translateX", 17)
        cmds.setAttr(source + ".rotateY", 210)
        self.invoke(tool.copy_pose, source, 3)
        self.invoke(tool.paste_pose, targets, 24)
        for target in targets:
            self.assertEqual(curve_values(target, "translateX"), ([24.], [17.]))
            self.assertEqual(curve_values(target, "rotateY")[0], [24.])
            self.assertAlmostEqual(curve_values(target, "rotateY")[1][0], 210.)

    def test_pose_uses_evaluated_values_on_weighted_layers(self):
        for override in (False, True):
            with self.subTest(override=override):
                cmds.file(new=True, force=True)
                source, target = control("source"), control("target")
                cmds.setAttr(source + ".translateX", 30)
                cmds.setAttr(source + ".rotateY", 110)
                keys(target, "translateX", [(1, 10), (50, 10)])
                keys(target, "rotateY", [(1, 20), (50, 20)])
                layer = cmds.animLayer("TargetAction", override=override)
                cmds.animLayer(layer, edit=True, attribute=[target + ".translateX", target + ".rotateY"])
                cmds.setAttr(layer + ".weight", .5)
                active(layer)
                self.invoke(tool.copy_pose, source, 1)
                self.invoke(tool.paste_pose, target, 24)
                self.assertAlmostEqual(cmds.getAttr(target + ".translateX"), 30, places=5)
                self.assertAlmostEqual(cmds.getAttr(target + ".rotateY"), 110, places=5)
                self.assertEqual(curve_values(target, "translateX")[1], [10, 10])
                self.assertEqual(curve_values(target, "translateX", layer)[0], [24])

    def test_insert_does_not_shift_shared_layer_weight_or_source(self):
        source, target = control("source"), control("target")
        keys(source, "translateX", [(5, 2), (10, 8)])
        layer = cmds.animLayer("SharedLayer")
        cmds.animLayer(layer, edit=True, attribute=source + ".translateX")
        keys(source, "translateX", [(5, 3), (10, 9)], layer)
        keys(layer, "weight", [(1, .2), (20, .8)])
        active("BaseAnimation")
        weights_before = curve_values(layer, "weight")
        source_before = curve_values(source, "translateX", layer)
        self.invoke(tool.copy_animation, source)
        self.invoke(tool.paste_insert_animation, target, 40)
        self.assertEqual(curve_values(layer, "weight"), weights_before)
        self.assertEqual(curve_values(source, "translateX", layer), source_before)
        self.assertEqual(curve_values(target, "translateX")[0], [40, 45])
        self.assertEqual(curve_values(target, "translateX", layer)[0], [40, 45])

    def test_locked_destination_layer_is_not_unlocked_or_changed(self):
        source, target = control("source"), control("target")
        keys(source, "translateX", [(1, 2), (10, 8)])
        layer = cmds.animLayer("LockedTarget")
        cmds.animLayer(layer, edit=True, attribute=target + ".translateX")
        keys(target, "translateX", [(1, 44), (10, 55)], layer)
        active("BaseAnimation")
        self.invoke(tool.copy_animation, source)
        active(layer)
        cmds.setAttr(layer + ".lock", True)
        before = curve_values(target, "translateX", layer)
        self.invoke(tool.paste_animation, target)
        self.assertTrue(cmds.getAttr(layer + ".lock"))
        self.assertEqual(curve_values(target, "translateX", layer), before)

    def test_insert_respects_selected_range_start_with_leading_empty_frames(self):
        source, target = control("source"), control("target")
        keys(source, "translateX", [(5, 2), (10, 8)])
        with mock.patch.object(tool, "get_selected_time_range", return_value=(1, 12)):
            self.invoke(tool.copy_animation, source)
        self.invoke(tool.paste_insert_animation, target, 40)
        self.assertEqual(curve_values(target, "translateX"), ([40, 44, 49, 51], [2, 2, 8, 8]))

    def test_insert_replaces_old_keys_inside_clip_without_changing_other_times(self):
        source, target = control("source"), control("target")
        keys(source, "translateX", [(1, 2), (11, 8)])
        keys(target, "translateX", [(1, -5), (44, 100), (70, 33)])
        self.invoke(tool.copy_animation, source)
        self.invoke(tool.paste_insert_animation, target, 40)
        self.assertEqual(curve_values(target, "translateX"), ([1, 40, 50, 70], [-5, 2, 8, 33]))
        self.assertAlmostEqual(cmds.keyframe(target+".translateX", q=True, eval=True,
                                            time=(45, 45))[0], 5.)

    def test_missing_base_curve_does_not_resolve_to_another_layer(self):
        source = control("source")
        layer = cmds.animLayer("Action")
        cmds.animLayer(layer, edit=True, attribute=source + ".translateX")
        keys(source, "translateX", [(1, 3), (10, 7)], layer)
        base = transfer.resolve_anim_curve(source + ".translateX", layer_name="BaseAnimation")
        if base:
            cmds.delete(base)
        self.assertIsNone(transfer.resolve_anim_curve(source + ".translateX", layer_name="BaseAnimation"))

    def test_base_curve_behind_constraint_resolves_with_unrelated_layers(self):
        source = control("source")
        driver = control("driver")
        keys(source, "translateX", [(1, 3), (10, 7)])
        original = transfer.resolve_anim_curve(source + ".translateX", layer_name="BaseAnimation")
        cmds.pointConstraint(driver, source, maintainOffset=True)
        cmds.animLayer("Unrelated")
        self.assertEqual(transfer.resolve_anim_curve(source + ".translateX", layer_name="BaseAnimation"), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
