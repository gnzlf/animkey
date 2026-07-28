import os
import shutil
import tempfile
import unittest
from unittest import mock

from PySide2 import QtWidgets

import maya.standalone


_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons import copyAnimation
from AnimKey.buttons import mirror
from AnimKey.core.animation_offset_session import resolve_target_curve_for_layer
from AnimKey.core.executionGuard import animkey_execution


class CopyAnimationTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.data_dir = tempfile.mkdtemp(prefix="animkey_copy_tests_")
        self.patches = [
            mock.patch.object(
                copyAnimation,
                "get_user_data_folder",
                return_value=self.data_dir,
            ),
            mock.patch.object(
                copyAnimation,
                "get_animation_backup_folder",
                return_value=self.data_dir,
            ),
            mock.patch.object(
                mirror,
                "get_user_data_folder",
                return_value=self.data_dir,
            ),
        ]
        for patcher in self.patches:
            patcher.start()
        copyAnimation._cleanup_buffer()
        copyAnimation._last_disk_mtime = 0

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def _run(self, callback):
        with animkey_execution("maya_test"):
            return callback()

    def _keys(self, target):
        times = cmds.keyframe(target, query=True, timeChange=True) or []
        values = cmds.keyframe(target, query=True, valueChange=True) or []
        return list(zip(times, values))

    def _select_layer(self, selected_layer):
        for layer in cmds.ls(type="animLayer") or []:
            cmds.animLayer(layer, edit=True, selected=False, preferred=False)
        cmds.animLayer(
            selected_layer,
            edit=True,
            selected=True,
            preferred=True,
        )

    def test_replace_preserves_outside_keys_tangents_and_undo(self):
        source = cmds.createNode("transform", name="source_ctrl")
        target = cmds.createNode("transform", name="target_ctrl")
        for frame, value in ((1, 1.0), (10, 5.0), (20, -2.0)):
            cmds.setKeyframe(source, attribute="translateX", time=frame, value=value)
        cmds.keyTangent(
            source,
            attribute="translateX",
            edit=True,
            weightedTangents=True,
        )
        cmds.keyTangent(
            source,
            attribute="translateX",
            edit=True,
            time=(10, 10),
            inTangentType="linear",
            outTangentType="step",
        )
        cmds.keyframe(
            source,
            attribute="translateX",
            edit=True,
            time=(20, 20),
            breakdown=True,
        )
        cmds.setInfinity(source, attribute="translateX", preInfinite="cycle", postInfinite="linear")
        for frame, value in ((-10, 7.0), (10, 999.0), (100, 8.0)):
            cmds.setKeyframe(target, attribute="translateX", time=frame, value=value)

        with mock.patch.object(copyAnimation, "get_selected_time_range", return_value=None):
            cmds.select(source, replace=True)
            self._run(copyAnimation.copy_animation)
        self.assertFalse(cmds.ls("ANIMKEY_BUF_*"))

        cmds.select(target, replace=True)
        self._run(copyAnimation.paste_animation)
        self.assertEqual(
            self._keys(f"{target}.translateX"),
            [(-10.0, 7.0), (1.0, 1.0), (10.0, 5.0), (20.0, -2.0), (100.0, 8.0)],
        )
        self.assertEqual(
            cmds.keyTangent(target, attribute="translateX", query=True, inTangentType=True)[2],
            "linear",
        )
        self.assertEqual(
            cmds.keyTangent(target, attribute="translateX", query=True, outTangentType=True)[2],
            "step",
        )
        self.assertEqual(
            cmds.keyTangent(
                target,
                attribute="translateX",
                query=True,
                weightedTangents=True,
            ),
            [True],
        )
        self.assertTrue(
            cmds.keyframe(
                target,
                attribute="translateX",
                query=True,
                time=(20, 20),
                breakdown=True,
            )
        )
        self.assertEqual(
            cmds.setInfinity(target, attribute="translateX", query=True, preInfinite=True),
            ["cycle"],
        )
        self.assertEqual(
            cmds.setInfinity(target, attribute="translateX", query=True, postInfinite=True),
            ["linear"],
        )

        cmds.undo()
        self.assertEqual(
            self._keys(f"{target}.translateX"),
            [(-10.0, 7.0), (10.0, 999.0), (100.0, 8.0)],
        )

    def test_copy_selected_range_is_inclusive_and_does_not_clear_selection(self):
        source = cmds.createNode("transform", name="source_ctrl")
        for frame in (1, 5, 10, 20):
            cmds.setKeyframe(
                source,
                attribute="translateY",
                time=frame,
                value=float(frame),
            )

        with mock.patch.object(
            copyAnimation,
            "get_selected_time_range",
            return_value=(5.0, 10.0),
        ):
            cmds.select(source, replace=True)
            self._run(copyAnimation.copy_animation)

        payload = copyAnimation._anim_buffer["source_ctrl"]["translateY"]
        self.assertEqual(payload["keyframes"], [5.0, 10.0])
        self.assertEqual(cmds.ls(selection=True), [source])

    def test_insert_uses_one_global_clip_offset(self):
        source = cmds.createNode("transform", name="source_ctrl")
        target = cmds.createNode("transform", name="target_ctrl")
        for frame, value in ((1, 1.0), (10, 2.0)):
            cmds.setKeyframe(source, attribute="translateX", time=frame, value=value)
        for frame, value in ((5, 30.0), (10, 40.0)):
            cmds.setKeyframe(source, attribute="rotateY", time=frame, value=value)

        with mock.patch.object(copyAnimation, "get_selected_time_range", return_value=None):
            cmds.select(source, replace=True)
            self._run(copyAnimation.copy_animation)
        cmds.currentTime(100)
        cmds.select(target, replace=True)
        self._run(copyAnimation.paste_insert_animation)

        self.assertEqual(
            self._keys(f"{target}.translateX"),
            [(100.0, 1.0), (109.0, 2.0)],
        )
        self.assertEqual(
            self._keys(f"{target}.rotateY"),
            [(104.0, 29.999999999999996), (109.0, 40.0)],
        )

    def test_copy_and_paste_use_selected_animation_layer_only(self):
        source = cmds.createNode("transform", name="source_ctrl")
        target = cmds.createNode("transform", name="target_ctrl")
        for node, base in ((source, 10.0), (target, 100.0)):
            for frame, value in ((1, base), (10, base + 10.0)):
                cmds.setKeyframe(node, attribute="translateX", time=frame, value=value)
        layer = cmds.animLayer("LayerA")
        cmds.animLayer(layer, edit=True, attribute=f"{source}.translateX")
        self._select_layer(layer)
        for frame, value in ((1, 15.0), (10, 35.0)):
            cmds.setKeyframe(
                source,
                attribute="translateX",
                time=frame,
                value=value,
                animLayer=layer,
            )
        source_curve = resolve_target_curve_for_layer(f"{source}.translateX", layer)
        source_layer_keys = self._keys(source_curve)

        with mock.patch.object(copyAnimation, "get_selected_time_range", return_value=None):
            cmds.select(source, replace=True)
            self._run(copyAnimation.copy_animation)
        cmds.select(target, replace=True)
        self._run(copyAnimation.paste_animation)

        target_curve = resolve_target_curve_for_layer(f"{target}.translateX", layer)
        self.assertTrue(target_curve)
        self.assertEqual(self._keys(target_curve), source_layer_keys)
        cmds.animLayer(layer, edit=True, mute=True)
        cmds.currentTime(1)
        self.assertAlmostEqual(cmds.getAttr(f"{target}.translateX"), 100.0)
        cmds.currentTime(10)
        self.assertAlmostEqual(cmds.getAttr(f"{target}.translateX"), 110.0)

    def test_copy_and_paste_base_animation_leave_other_layer_unchanged(self):
        source = cmds.createNode("transform", name="source_ctrl")
        target = cmds.createNode("transform", name="target_ctrl")
        for node, values in (
            (source, ((1, 10.0), (10, 20.0))),
            (target, ((1, 100.0), (10, 200.0))),
        ):
            for frame, value in values:
                cmds.setKeyframe(node, attribute="translateX", time=frame, value=value)

        layer = cmds.animLayer("LayerA")
        for node in (source, target):
            cmds.animLayer(layer, edit=True, attribute=f"{node}.translateX")
        self._select_layer(layer)
        for node, values in (
            (source, ((1, 15.0), (10, 30.0))),
            (target, ((1, 125.0), (10, 250.0))),
        ):
            for frame, value in values:
                cmds.setKeyframe(
                    node,
                    attribute="translateX",
                    time=frame,
                    value=value,
                    animLayer=layer,
                )
        target_layer_curve = resolve_target_curve_for_layer(f"{target}.translateX", layer)
        target_layer_before = self._keys(target_layer_curve)

        self._select_layer("BaseAnimation")
        with mock.patch.object(copyAnimation, "get_selected_time_range", return_value=None):
            cmds.select(source, replace=True)
            self._run(copyAnimation.copy_animation)
        cmds.select(target, replace=True)
        self._run(copyAnimation.paste_animation)

        target_base_curve = resolve_target_curve_for_layer(
            f"{target}.translateX",
            "BaseAnimation",
        )
        self.assertTrue(target_base_curve)
        self.assertEqual(
            self._keys(target_base_curve),
            [(1.0, 10.0), (10.0, 20.0)],
        )
        self.assertEqual(self._keys(target_layer_curve), target_layer_before)

    def test_paste_opposite_only_transforms_copied_range(self):
        left = cmds.createNode("transform", name="L_ctrl")
        right = cmds.createNode("transform", name="R_ctrl")
        cmds.setAttr(f"{left}.translateX", 1.0)
        cmds.setAttr(f"{right}.translateX", -1.0)
        for frame, value in ((1, 2.0), (10, 4.0), (20, -3.0)):
            cmds.setKeyframe(left, attribute="translateX", time=frame, value=value)
        for frame, value in ((-10, 7.0), (10, 999.0), (100, 8.0)):
            cmds.setKeyframe(right, attribute="translateX", time=frame, value=value)

        with mock.patch.object(copyAnimation, "get_selected_time_range", return_value=None):
            cmds.select(left, replace=True)
            self._run(copyAnimation.copy_animation)
        cmds.select(left, replace=True)
        self._run(copyAnimation.paste_opposite_animation)

        self.assertEqual(
            self._keys(f"{right}.translateX"),
            [(-10.0, 7.0), (1.0, -2.0), (10.0, -4.0), (20.0, 3.0), (100.0, 8.0)],
        )

    def test_pose_paste_keys_the_selected_layer(self):
        source = cmds.createNode("transform", name="source_ctrl")
        target = cmds.createNode("transform", name="target_ctrl")
        cmds.setAttr(f"{source}.translateX", 12.5)
        cmds.setKeyframe(target, attribute="translateX", time=1, value=100.0)

        cmds.select(source, replace=True)
        self._run(copyAnimation.copy_pose)

        layer = cmds.animLayer("PoseLayer")
        self._select_layer(layer)
        cmds.currentTime(8)
        cmds.select(target, replace=True)
        self._run(copyAnimation.paste_pose)

        layer_curve = resolve_target_curve_for_layer(f"{target}.translateX", layer)
        self.assertTrue(layer_curve)
        self.assertEqual(cmds.keyframe(layer_curve, query=True, timeChange=True), [8.0])

    def test_disk_reload_does_not_create_scene_buffer_nodes(self):
        source = cmds.createNode("transform", name="source_ctrl")
        cmds.setKeyframe(source, attribute="translateZ", time=1, value=3.0)
        with mock.patch.object(copyAnimation, "get_selected_time_range", return_value=None):
            cmds.select(source, replace=True)
            self._run(copyAnimation.copy_animation)

        copyAnimation._anim_buffer = {}
        copyAnimation._last_disk_mtime = 0
        copyAnimation._sync_buffer_from_disk()
        self.assertIn("source_ctrl", copyAnimation._anim_buffer)
        self.assertFalse(cmds.ls("ANIMKEY_BUF_*"))
        self.assertTrue(os.path.exists(copyAnimation.get_copy_paste_animation_file()))


if __name__ == "__main__":
    unittest.main()
