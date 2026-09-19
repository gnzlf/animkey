import unittest

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons import tempPivot
from AnimKey.core.animation_curve_transfer import resolve_anim_curve


class TempPivotAnimationPreservationTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        tempPivot._temp_pivot_restore_state = None

    def _create_control(self):
        control = cmds.polyCube(name="tempPivotControl")[0]
        for frame, tx, ry in ((1, 5.0, 0.0), (6, 6.0, 45.0), (12, 4.0, 90.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(control + ".translateX", tx)
            cmds.setAttr(control + ".rotateY", ry)
            cmds.setKeyframe(control, attribute="translateX")
            cmds.setKeyframe(control, attribute="rotateY")
        return control

    @staticmethod
    def _matrices(node):
        result = {}
        for frame in range(1, 13):
            cmds.currentTime(frame, edit=True)
            result[frame] = cmds.xform(node, query=True, worldSpace=True, matrix=True)
        return result

    def _assert_matrices_match(self, expected, node, frames=None):
        actual = self._matrices(node)
        frames = list(expected) if frames is None else list(frames)
        for frame in frames:
            expected_matrix = expected[frame]
            for expected_value, actual_value in zip(expected_matrix, actual[frame]):
                self.assertAlmostEqual(expected_value, actual_value, places=5)

    @staticmethod
    def _pivot_values(node):
        return {
            "rotatePivot": cmds.getAttr(node + ".rotatePivot")[0],
            "scalePivot": cmds.getAttr(node + ".scalePivot")[0],
            "rotatePivotTranslate": cmds.getAttr(node + ".rotatePivotTranslate")[0],
            "scalePivotTranslate": cmds.getAttr(node + ".scalePivotTranslate")[0],
        }

    @staticmethod
    def _set_pivot_values(node, values):
        for attr, value in values.items():
            cmds.setAttr(node + "." + attr, *value, type="double3")

    def _expected_without_temp_pivot(self, node, state):
        edited_pivots = self._pivot_values(node)
        tempPivot._restore_object_pivots(state, preserve_current_pose=False)
        expected = self._matrices(node)
        self._set_pivot_values(node, edited_pivots)
        return expected

    def _edit_pivot(self, control):
        cmds.currentTime(1, edit=True)
        state = tempPivot._capture_temp_pivot_state([control])
        cmds.xform(
            control,
            objectSpace=True,
            preserve=True,
            pivots=(3.0, -2.0, 1.0),
        )
        for frame, ry in ((1, 0.0), (6, 55.0), (12, 110.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(control + ".rotateY", ry)
            cmds.setKeyframe(control, attribute="rotateY")
        return state

    def test_restores_original_pivot_without_changing_animation(self):
        control = self._create_control()
        state = self._edit_pivot(control)
        expected_matrices = self._matrices(control)
        expected_outside_range = self._expected_without_temp_pivot(control, state)
        base_curve = resolve_anim_curve(
            control + ".rotateY", layer_name="BaseAnimation"
        )
        original_values = cmds.keyframe(
            base_curve, query=True, timeChange=True, valueChange=True
        )

        layer, keyed_channels = tempPivot._bake_temp_pivot_compensation(state)

        self.assertTrue(layer)
        self.assertGreater(keyed_channels, 0)
        self.assertEqual(
            cmds.xform(control, query=True, objectSpace=True, rotatePivot=True),
            state["object_pivots"][control]["rotate_pivot"],
        )
        self.assertEqual(
            cmds.keyframe(
                base_curve, query=True, timeChange=True, valueChange=True
            ),
            original_values,
        )
        self._assert_matrices_match(expected_matrices, control, range(6, 13))
        self._assert_matrices_match(expected_outside_range, control, range(1, 6))

        layer_curves = cmds.animLayer(layer, query=True, animCurves=True) or []
        layer_times = []
        for curve in layer_curves:
            layer_times.extend(cmds.keyframe(curve, query=True, timeChange=True) or [])
        self.assertGreaterEqual(min(layer_times), 5.0)
        self.assertLessEqual(max(layer_times), 13.0)

    def test_preserves_animation_when_an_additive_layer_is_active(self):
        control = self._create_control()
        action_layer = cmds.animLayer("animLayerAction", override=False)
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

        state = self._edit_pivot(control)
        expected_matrices = self._matrices(control)
        expected_outside_range = self._expected_without_temp_pivot(control, state)

        layer, keyed_channels = tempPivot._bake_temp_pivot_compensation(state)

        self.assertTrue(layer)
        self.assertGreater(keyed_channels, 0)
        self.assertTrue(cmds.animLayer(action_layer, query=True, selected=True))
        self._assert_matrices_match(expected_matrices, control, range(6, 13))
        self._assert_matrices_match(expected_outside_range, control, range(1, 6))

    def test_does_not_create_a_bake_layer_when_the_pivot_was_not_edited(self):
        control = self._create_control()
        state = tempPivot._capture_temp_pivot_state([control])

        layer, keyed_channels = tempPivot._bake_temp_pivot_compensation(state)

        self.assertIsNone(layer)
        self.assertEqual(keyed_channels, 0)

    def test_range_detection_survives_switching_to_a_new_animation_layer(self):
        control = self._create_control()
        state = tempPivot._capture_temp_pivot_state([control])

        layer = cmds.animLayer("pivotSessionLayer", override=False)
        cmds.animLayer(layer, edit=True, attribute=control + ".rotateY")
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)
        cmds.setKeyframe(
            control,
            attribute="rotateY",
            time=(8, 8),
            value=12.0,
            animLayer=layer,
        )

        self.assertEqual(tempPivot._temp_pivot_changed_key_times(state), [8.0])
        self.assertEqual(tempPivot._temp_pivot_sample_times([control], state), [8.0])


if __name__ == "__main__":
    unittest.main()
