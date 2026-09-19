import os
import tempfile
import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons import tempPivot


class TempPivotControlsAuditTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.playbackOptions(minTime=1, maxTime=12)
        tempPivot._temp_pivot_restore_state = None
        tempPivot._temp_pivot_window = None

    def tearDown(self):
        tempPivot._stop_temp_pivot_monitor()
        tempPivot.delete_temp_system()

    @staticmethod
    def _world_matrices(node):
        values = {}
        for frame in range(1, 13):
            cmds.currentTime(frame, edit=True)
            values[frame] = cmds.xform(node, query=True, worldSpace=True, matrix=True)
        return values

    def _assert_world_matrices(self, node, expected):
        actual = self._world_matrices(node)
        for frame, expected_matrix in expected.items():
            for expected_value, actual_value in zip(expected_matrix, actual[frame]):
                self.assertAlmostEqual(expected_value, actual_value, places=5)

    @staticmethod
    def _animated_cube(name, base_x, delta_x=0.0):
        node = cmds.polyCube(name=name)[0]
        for frame, rotate_y in ((1, 0.0), (6, 45.0), (12, 90.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(node + ".translateX", base_x + delta_x * (frame - 1) / 11.0)
            cmds.setAttr(node + ".rotateY", rotate_y)
            cmds.setKeyframe(node, attribute="translateX")
            cmds.setKeyframe(node, attribute="rotateY")
        return node

    def test_individual_controls_preserve_motion_and_smart_bake(self):
        first = self._animated_cube("tempFirst", 1.0, 3.0)
        second = self._animated_cube("tempSecond", 8.0, -2.0)
        original_first = self._world_matrices(first)
        original_second = self._world_matrices(second)

        controls = tempPivot.create_individual_controls([first, second])

        self.assertEqual(len(controls), 2)
        self.assertEqual(
            set(tempPivot.get_controlled_objects()),
            set(cmds.ls([first, second], long=True)),
        )
        self._assert_world_matrices(first, original_first)
        self._assert_world_matrices(second, original_second)

        control = controls[0]
        for frame, value in ((1, 0.0), (6, 2.0), (12, -1.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(control + ".translateZ", value)
            cmds.setKeyframe(control, attribute="translateZ")
        expected_first = self._world_matrices(first)

        cmds.select(control, replace=True)
        baked_layer = tempPivot.smart_bake_and_delete()

        self.assertTrue(baked_layer)
        self.assertEqual(
            tempPivot.get_controlled_objects(),
            cmds.ls(second, long=True),
        )
        self._assert_world_matrices(first, expected_first)

    def test_group_pivot_modes_and_follow_toggle(self):
        first = self._animated_cube("groupFirst", 0.0, 3.0)
        second = self._animated_cube("groupSecond", 10.0, 3.0)

        center_control = tempPivot.create_group_control(
            [first, second], pivot_mode="center", follow=True
        )
        cmds.currentTime(1, edit=True)
        self.assertAlmostEqual(
            cmds.xform(center_control, query=True, worldSpace=True, rotatePivot=True)[0],
            5.0,
            places=5,
        )
        cmds.currentTime(6, edit=True)
        self.assertAlmostEqual(
            cmds.xform(center_control, query=True, worldSpace=True, rotatePivot=True)[0],
            5.0 + 3.0 * 5.0 / 11.0,
            places=5,
        )
        self.assertTrue(cmds.getAttr(center_control + ".follow"))

        tempPivot.delete_temp_system()
        cmds.currentTime(1, edit=True)
        last_control = tempPivot.create_group_control(
            [first, second], pivot_mode="last", follow=False
        )
        cmds.currentTime(1, edit=True)
        start_position = cmds.xform(
            last_control, query=True, worldSpace=True, rotatePivot=True
        )
        self.assertAlmostEqual(start_position[0], 10.0, places=5)
        cmds.currentTime(6, edit=True)
        later_position = cmds.xform(
            last_control, query=True, worldSpace=True, rotatePivot=True
        )
        self.assertAlmostEqual(later_position[0], start_position[0], places=5)
        self.assertFalse(cmds.getAttr(last_control + ".follow"))

    def test_temp_controls_bake_with_an_animation_layer(self):
        control = self._animated_cube("layeredControl", 4.0, 2.0)
        action_layer = cmds.animLayer("tempActionLayer", override=False)
        cmds.animLayer(action_layer, edit=True, attribute=control + ".rotateY")
        cmds.animLayer(action_layer, edit=True, selected=True, preferred=True)
        for frame, value in ((1, 0.0), (6, 15.0), (12, -10.0)):
            cmds.setKeyframe(
                control,
                attribute="rotateY",
                time=(frame, frame),
                value=value,
                animLayer=action_layer,
            )

        temp_control = tempPivot.create_individual_controls([control])[0]
        for frame, value in ((1, 0.0), (6, 1.5), (12, -0.5)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(temp_control + ".translateZ", value)
            cmds.setKeyframe(temp_control, attribute="translateZ")
        expected = self._world_matrices(control)

        cmds.select(temp_control, replace=True)
        baked_layer = tempPivot.smart_bake_and_delete()

        self.assertTrue(baked_layer)
        self.assertTrue(cmds.animLayer(action_layer, query=True, selected=True))
        self._assert_world_matrices(control, expected)

    def test_smart_bake_only_changes_the_temp_control_key_range(self):
        control = self._animated_cube("rangeControl", 2.0, 6.0)
        original = self._world_matrices(control)
        temp_control = tempPivot.create_individual_controls([control])[0]

        for frame, value in ((6, 2.0), (8, -1.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(temp_control + ".translateZ", value)
            cmds.setKeyframe(temp_control, attribute="translateZ")
        expected = self._world_matrices(control)

        cmds.currentTime(8, edit=True)
        cmds.select(temp_control, replace=True)
        baked_layer = tempPivot.smart_bake_and_delete()

        self.assertTrue(baked_layer)
        self._assert_world_matrices(
            control,
            {frame: expected[frame] for frame in range(6, 9)},
        )
        self._assert_world_matrices(
            control,
            {frame: original[frame] for frame in (1, 2, 3, 4, 5, 9, 10, 11, 12)},
        )

        layer_times = []
        for curve in cmds.animLayer(baked_layer, query=True, animCurves=True) or []:
            layer_times.extend(
                cmds.keyframe(curve, query=True, timeChange=True) or []
            )
        self.assertGreaterEqual(min(layer_times), 5.0)
        self.assertLessEqual(max(layer_times), 9.0)

    def test_keyless_temp_control_bakes_only_the_current_frame(self):
        control = self._animated_cube("singleFrameControl", 3.0, 4.0)
        original = self._world_matrices(control)
        temp_control = tempPivot.create_individual_controls([control])[0]

        cmds.currentTime(6, edit=True)
        cmds.setAttr(temp_control + ".translateZ", 2.5)
        expected_at_six = cmds.xform(
            control, query=True, worldSpace=True, matrix=True
        )
        cmds.select(temp_control, replace=True)
        baked_layer = tempPivot.smart_bake_and_delete()

        self.assertTrue(baked_layer)
        cmds.currentTime(6, edit=True)
        actual_at_six = cmds.xform(
            control, query=True, worldSpace=True, matrix=True
        )
        for expected_value, actual_value in zip(expected_at_six, actual_at_six):
            self.assertAlmostEqual(expected_value, actual_value, places=5)
        self._assert_world_matrices(
            control,
            {frame: original[frame] for frame in (1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12)},
        )

    def test_creation_range_uses_animation_keys_instead_of_the_whole_shot(self):
        cmds.playbackOptions(minTime=1, maxTime=100)
        control = cmds.polyCube(name="creationRangeControl")[0]
        for frame, value in ((20, 1.0), (30, 5.0)):
            cmds.setKeyframe(
                control,
                attribute="translateX",
                time=(frame, frame),
                value=value,
            )

        self.assertEqual(
            tempPivot._temp_control_creation_range([control]),
            (20.0, 30.0),
        )

    def test_delete_temp_system_restores_animation_without_baking(self):
        control = self._animated_cube("cancelControl", 2.0, 4.0)
        expected = self._world_matrices(control)

        temp_control = tempPivot.create_individual_controls([control])[0]
        cmds.currentTime(6, edit=True)
        cmds.setAttr(temp_control + ".translateZ", 3.0)
        cmds.setKeyframe(temp_control, attribute="translateZ")

        self.assertTrue(tempPivot.delete_temp_system())

        self.assertFalse(tempPivot.get_controlled_objects())
        self.assertFalse(tempPivot.get_tagged())
        self._assert_world_matrices(control, expected)

    def test_joint_and_parented_control_preserve_world_motion_on_bake(self):
        parent = cmds.group(empty=True, name="tempParent")
        cmds.select(clear=True)
        joint = cmds.joint(name="tempJoint")
        cmds.parent(joint, parent)
        cmds.setAttr(joint + ".jointOrientX", 15.0)
        for frame, parent_y, child_y in ((1, 0.0, 0.0), (6, 20.0, 30.0), (12, 40.0, -15.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(parent + ".rotateY", parent_y)
            cmds.setAttr(joint + ".rotateY", child_y)
            cmds.setKeyframe(parent, attribute="rotateY")
            cmds.setKeyframe(joint, attribute="rotateY")

        temp_control = tempPivot.create_individual_controls([joint])[0]
        for frame, value in ((1, 0.0), (6, 1.0), (12, -2.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(temp_control + ".translateX", value)
            cmds.setKeyframe(temp_control, attribute="translateX")
        expected = self._world_matrices(joint)

        cmds.select(temp_control, replace=True)
        baked_layer = tempPivot.smart_bake_and_delete()

        self.assertTrue(baked_layer)
        self._assert_world_matrices(joint, expected)

    def test_locked_transform_axis_does_not_break_temp_control(self):
        control = self._animated_cube("lockedAxisControl", 4.0, 0.0)
        cmds.setAttr(control + ".translateX", lock=True)

        temp_controls = tempPivot.create_individual_controls([control])

        self.assertEqual(len(temp_controls), 1)
        self.assertTrue(cmds.getAttr(control + ".translateX", lock=True))
        self.assertFalse(
            cmds.isConnected(
                control.split("|")[-1] + "_TEMP_decomp.outputTranslateX",
                control + ".translateX",
            )
        )
        self.assertTrue(
            cmds.listConnections(
                control + ".rotateY", source=True, destination=False
            )
        )

    def test_temp_pivot_offsets_reset_to_the_default_pivot(self):
        control = self._animated_cube("storedPivot", 3.0)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "temp_pivot_offsets.json")
            with mock.patch.object(tempPivot, "_temp_pivot_prefs_path", return_value=path):
                self.assertTrue(
                    tempPivot._save_temp_pivot_offsets_for_objects(
                        [control], [8.0, -2.0, 4.0]
                    )
                )
                self.assertEqual(
                    tempPivot._stored_temp_pivot_position([control]),
                    [8.0, -2.0, 4.0],
                )
                self.assertTrue(tempPivot.reset_temp_pivot_offsets([control]))
                self.assertEqual(
                    tempPivot._stored_temp_pivot_position([control]),
                    cmds.xform(
                        control, query=True, worldSpace=True, rotatePivot=True
                    ),
                )

    def test_group_pivot_offset_follows_the_last_selected_reference(self):
        first = cmds.polyCube(name="offsetFirst")[0]
        reference = cmds.polyCube(name="offsetReference")[0]
        cmds.xform(first, worldSpace=True, translation=(1.0, 0.0, 0.0))
        cmds.xform(reference, worldSpace=True, translation=(10.0, 0.0, 0.0))

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "temp_pivot_offsets.json")
            with mock.patch.object(tempPivot, "_temp_pivot_prefs_path", return_value=path):
                self.assertTrue(
                    tempPivot._save_temp_pivot_offsets_for_objects(
                        [first, reference], [12.0, 3.0, -2.0]
                    )
                )
                cmds.xform(first, worldSpace=True, translation=(50.0, 0.0, 0.0))
                cmds.xform(reference, worldSpace=True, translation=(15.0, 0.0, 0.0))

                stored = tempPivot._stored_temp_pivot_position(
                    [first, reference], pivot_mode="last"
                )

        for expected, actual in zip((17.0, 3.0, -2.0), stored):
            self.assertAlmostEqual(expected, actual, places=5)

    def test_group_ui_toggle_updates_both_group_only_subcontrols(self):
        window = mock.Mock()
        window.pivot_options_widget = mock.Mock()
        window.follow_checkbox = mock.Mock()

        tempPivot.TempPivotWindow._toggle_pivot_options(window, checked=True)
        window.pivot_options_widget.setVisible.assert_called_once_with(False)
        window.follow_checkbox.setVisible.assert_called_once_with(False)

        window.pivot_options_widget.reset_mock()
        window.follow_checkbox.reset_mock()
        tempPivot.TempPivotWindow._toggle_pivot_options(window, checked=False)
        window.pivot_options_widget.setVisible.assert_called_once_with(True)
        window.follow_checkbox.setVisible.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
