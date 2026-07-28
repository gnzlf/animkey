import json
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

from AnimKey.buttons import mirror
from AnimKey.core.animation_offset_session import resolve_target_curve_for_layer


class MirrorTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.data_dir = tempfile.mkdtemp(prefix="animkey_mirror_tests_")
        self.data_patch = mock.patch.object(
            mirror,
            "get_user_data_folder",
            return_value=self.data_dir,
        )
        self.data_patch.start()
        mirror._snapshot_cache.clear()

    def tearDown(self):
        mirror.disable_auto_mirror()
        self.data_patch.stop()
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def _pair(self, left_name="L_ctrl", right_name="R_ctrl"):
        left = cmds.createNode("transform", name=left_name)
        right = cmds.createNode("transform", name=right_name)
        cmds.setAttr(f"{left}.translateX", 1.0)
        cmds.setAttr(f"{right}.translateX", -1.0)
        snapshot = mirror._build_basic_mirror_snapshot(
            [left, right],
            rig_name="test_pair",
        )
        return left, right, snapshot

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

    def test_pose_mirror_is_one_way_for_one_selected_side(self):
        left, right, snapshot = self._pair()
        cmds.setAttr(f"{left}.rotateY", 25.0)
        cmds.setAttr(f"{right}.rotateY", -7.0)
        sym_plane = mirror._snap_sym_plane(snapshot)

        count, touched = mirror._mirror_selected_pose_two_phase(
            [left],
            snapshot,
            sym_plane,
            attrs_filter=["rotateY"],
        )
        self.assertEqual(count, 1)
        self.assertIn(right, touched)
        self.assertEqual(cmds.getAttr(f"{left}.rotateY"), 25.0)
        self.assertAlmostEqual(cmds.getAttr(f"{right}.rotateY"), -25.0)

    def test_pose_mirror_swaps_when_both_sides_are_selected(self):
        left, right, snapshot = self._pair()
        cmds.setAttr(f"{left}.rotateY", 25.0)
        cmds.setAttr(f"{right}.rotateY", -7.0)

        mirror._mirror_selected_pose_two_phase(
            [left, right],
            snapshot,
            mirror._snap_sym_plane(snapshot),
            attrs_filter=["rotateY"],
        )
        self.assertAlmostEqual(cmds.getAttr(f"{left}.rotateY"), 7.0)
        self.assertAlmostEqual(cmds.getAttr(f"{right}.rotateY"), -25.0)

    def test_animation_swap_preserves_tangents_outside_range_time_and_undo(self):
        left, right, snapshot = self._pair()
        for frame, value in ((1, 1.0), (10, 2.0), (20, 3.0), (100, 99.0)):
            cmds.setKeyframe(left, attribute="translateX", time=frame, value=value)
        for frame, value in ((1, -10.0), (10, -20.0), (20, -30.0), (100, -88.0)):
            cmds.setKeyframe(right, attribute="translateX", time=frame, value=value)
        cmds.keyTangent(
            left,
            attribute="translateX",
            edit=True,
            time=(10, 10),
            inTangentType="linear",
            outTangentType="step",
        )
        cmds.keyTangent(
            right,
            attribute="translateX",
            edit=True,
            time=(10, 10),
            inTangentType="flat",
            outTangentType="linear",
        )
        cmds.currentTime(42)

        with mock.patch.object(
            mirror,
            "_load_or_build_basic_snapshot",
            return_value=(snapshot, True),
        ):
            count = mirror.mirror_animation(
                [left],
                attrs_filter=["translateX"],
                time_range=(1, 20),
            )

        self.assertEqual(count, 6)
        self.assertEqual(cmds.currentTime(query=True), 42.0)
        self.assertEqual(
            self._keys(f"{left}.translateX"),
            [(1.0, 10.0), (10.0, 20.0), (20.0, 30.0), (100.0, 99.0)],
        )
        self.assertEqual(
            self._keys(f"{right}.translateX"),
            [(1.0, -1.0), (10.0, -2.0), (20.0, -3.0), (100.0, -88.0)],
        )
        self.assertEqual(
            cmds.keyTangent(left, attribute="translateX", query=True, inTangentType=True)[1],
            "flat",
        )
        self.assertEqual(
            cmds.keyTangent(right, attribute="translateX", query=True, outTangentType=True)[1],
            "step",
        )

        cmds.undo()
        self.assertEqual(
            self._keys(f"{left}.translateX"),
            [(1.0, 1.0), (10.0, 2.0), (20.0, 3.0), (100.0, 99.0)],
        )
        self.assertEqual(
            self._keys(f"{right}.translateX"),
            [(1.0, -10.0), (10.0, -20.0), (20.0, -30.0), (100.0, -88.0)],
        )

    def test_animation_swap_only_changes_selected_additive_layer(self):
        left, right, snapshot = self._pair()
        for node, base in ((left, 10.0), (right, -20.0)):
            for frame, value in ((1, base), (10, base * 2.0)):
                cmds.setKeyframe(node, attribute="translateX", time=frame, value=value)
        left_base_curve = cmds.listConnections(
            f"{left}.translateX",
            source=True,
            destination=False,
            skipConversionNodes=True,
        )[0]
        right_base_curve = cmds.listConnections(
            f"{right}.translateX",
            source=True,
            destination=False,
            skipConversionNodes=True,
        )[0]
        left_base_before = self._keys(left_base_curve)
        right_base_before = self._keys(right_base_curve)

        layer = cmds.animLayer("LayerA")
        cmds.animLayer(layer, edit=True, attribute=f"{left}.translateX")
        cmds.animLayer(layer, edit=True, attribute=f"{right}.translateX")
        self._select_layer(layer)
        for frame, value in ((1, 15.0), (10, 25.0)):
            cmds.setKeyframe(
                left,
                attribute="translateX",
                time=frame,
                value=value,
                animLayer=layer,
            )
        for frame, value in ((1, -30.0), (10, -40.0)):
            cmds.setKeyframe(
                right,
                attribute="translateX",
                time=frame,
                value=value,
                animLayer=layer,
            )
        left_layer_curve = resolve_target_curve_for_layer(f"{left}.translateX", layer)
        right_layer_curve = resolve_target_curve_for_layer(f"{right}.translateX", layer)
        left_before = self._keys(left_layer_curve)
        right_before = self._keys(right_layer_curve)

        with mock.patch.object(
            mirror,
            "_load_or_build_basic_snapshot",
            return_value=(snapshot, True),
        ):
            count = mirror.mirror_animation(
                [left],
                attrs_filter=["translateX"],
                time_range=(1, 10),
            )

        self.assertEqual(count, 4)
        self.assertEqual(
            self._keys(left_layer_curve),
            [(time, -value) for time, value in right_before],
        )
        self.assertEqual(
            self._keys(right_layer_curve),
            [(time, -value) for time, value in left_before],
        )
        self.assertEqual(self._keys(left_base_curve), left_base_before)
        self.assertEqual(self._keys(right_base_curve), right_base_before)

    def test_animation_swap_base_animation_leaves_additive_layer_unchanged(self):
        left, right, snapshot = self._pair()
        for node, values in (
            (left, ((1, 10.0), (10, 20.0))),
            (right, ((1, -30.0), (10, -40.0))),
        ):
            for frame, value in values:
                cmds.setKeyframe(node, attribute="translateX", time=frame, value=value)

        layer = cmds.animLayer("LayerA")
        for node in (left, right):
            cmds.animLayer(layer, edit=True, attribute=f"{node}.translateX")
        self._select_layer(layer)
        for node, values in (
            (left, ((1, 15.0), (10, 25.0))),
            (right, ((1, -35.0), (10, -55.0))),
        ):
            for frame, value in values:
                cmds.setKeyframe(
                    node,
                    attribute="translateX",
                    time=frame,
                    value=value,
                    animLayer=layer,
                )
        left_layer_curve = resolve_target_curve_for_layer(f"{left}.translateX", layer)
        right_layer_curve = resolve_target_curve_for_layer(f"{right}.translateX", layer)
        left_layer_before = self._keys(left_layer_curve)
        right_layer_before = self._keys(right_layer_curve)

        self._select_layer("BaseAnimation")
        with mock.patch.object(
            mirror,
            "_load_or_build_basic_snapshot",
            return_value=(snapshot, True),
        ):
            count = mirror.mirror_animation(
                [left],
                attrs_filter=["translateX"],
                time_range=(1, 10),
            )

        left_base_curve = resolve_target_curve_for_layer(
            f"{left}.translateX",
            "BaseAnimation",
        )
        right_base_curve = resolve_target_curve_for_layer(
            f"{right}.translateX",
            "BaseAnimation",
        )
        self.assertEqual(count, 4)
        self.assertEqual(
            self._keys(left_base_curve),
            [(1.0, 30.0), (10.0, 40.0)],
        )
        self.assertEqual(
            self._keys(right_base_curve),
            [(1.0, -10.0), (10.0, -20.0)],
        )
        self.assertEqual(self._keys(left_layer_curve), left_layer_before)
        self.assertEqual(self._keys(right_layer_curve), right_layer_before)

    def test_animation_swap_clears_destination_when_source_side_is_empty(self):
        left, right, snapshot = self._pair()
        cmds.setKeyframe(right, attribute="translateX", time=1, value=-5.0)
        cmds.setKeyframe(right, attribute="translateX", time=10, value=-10.0)

        with mock.patch.object(
            mirror,
            "_load_or_build_basic_snapshot",
            return_value=(snapshot, True),
        ):
            count = mirror.mirror_animation(
                [left],
                attrs_filter=["translateX"],
                time_range=(1, 10),
            )

        self.assertEqual(count, 2)
        self.assertEqual(self._keys(f"{left}.translateX"), [(1.0, 5.0), (10.0, 10.0)])
        self.assertFalse(cmds.keyframe(right, attribute="translateX", query=True))

    def test_snapshot_loader_reuses_unchanged_file(self):
        left, right, snapshot = self._pair()
        rig_name = mirror.get_rig_identifier([left])
        snapshot["rig_name"] = rig_name
        path = mirror.get_mirror_snapshot_file(rig_name)
        mirror._write_snapshot_file(path, snapshot)

        with mock.patch.object(
            mirror,
            "_read_snapshot_file",
            wraps=mirror._read_snapshot_file,
        ) as reader:
            first = mirror.load_snapshot(rig_name)
            second = mirror.load_snapshot(rig_name)
        self.assertIs(first, second)
        self.assertEqual(reader.call_count, 1)
        self.assertTrue(os.path.exists(path))

    def test_auto_mirror_throttle_skips_unchanged_idle_callbacks(self):
        left, right, snapshot = self._pair()
        cmds.select(left, replace=True)
        mirror._auto_mirror_state.enabled = True
        mirror._auto_mirror_state.last_run = 0.0
        mirror._auto_mirror_state.last_signature = None

        with mock.patch.object(mirror, "load_snapshot", return_value=snapshot), mock.patch.object(
            mirror,
            "_mirror_selected_pose_two_phase",
            wraps=mirror._mirror_selected_pose_two_phase,
        ) as apply_pose:
            mirror._auto_mirror_callback()
            mirror._auto_mirror_state.last_run = 0.0
            mirror._auto_mirror_callback()
        self.assertEqual(apply_pose.call_count, 1)


if __name__ == "__main__":
    unittest.main()
