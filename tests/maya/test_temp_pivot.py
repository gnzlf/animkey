import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons import tempPivot


class TempPivotTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        tempPivot._stop_temp_pivot_monitor()
        tempPivot._temp_pivot_restore_state = None
        self.first = cmds.polyCube(name="pivot_first")[0]
        self.last = cmds.polyCube(name="pivot_last")[0]
        cmds.xform(self.first, worldSpace=True, translation=(1.0, 2.0, 3.0))
        cmds.xform(self.last, worldSpace=True, translation=(8.0, 5.0, -2.0))

    def test_activate_uses_last_selected_object_without_creating_nodes(self):
        cmds.select(self.first, self.last, replace=True)
        nodes_before = set(cmds.ls(long=True))

        with mock.patch.object(tempPivot.cmds, "setToolTo") as set_tool:
            with mock.patch.object(tempPivot.cmds, "manipPivot") as manip_pivot:
                with mock.patch.object(
                    tempPivot.cmds, "manipRotateContext", return_value=False
                ) as rotate_context:
                    with mock.patch.object(tempPivot.cmds, "ctxEditMode") as edit_mode:
                        result = tempPivot.activate_temp_pivot()

        self.assertEqual(result["objects"], ["|pivot_first", "|pivot_last"])
        self.assertEqual(result["pivot"], [8.0, 5.0, -2.0])
        self.assertEqual(set(cmds.ls(long=True)), nodes_before)
        set_tool.assert_called_once_with("RotateSuperContext")
        manip_pivot.assert_has_calls(
            [
                mock.call(reset=True),
                mock.call(position=[8.0, 5.0, -2.0]),
                mock.call(pinPivot=True),
            ]
        )
        rotate_context.assert_has_calls(
            [
                mock.call(
                    "Rotate",
                    edit=True,
                    useManipPivot=True,
                    useCenterPivot=False,
                    useObjectPivot=False,
                ),
                mock.call("Rotate", edit=True, pinPivot=True),
                mock.call("Rotate", query=True, editPivotMode=True),
            ]
        )
        edit_mode.assert_called_once_with()
        self.assertTrue(
            tempPivot._temp_pivot_restore_state["edit_mode_seen"]
        )
        self.assertFalse(
            tempPivot._temp_pivot_restore_state["pivots_finalized"]
        )

    def test_center_mode_uses_selection_bounding_box(self):
        cmds.select(self.first, self.last, replace=True)

        with mock.patch.object(tempPivot.cmds, "setToolTo"):
            with mock.patch.object(tempPivot.cmds, "manipPivot"):
                with mock.patch.object(tempPivot.cmds, "manipRotateContext"):
                    result = tempPivot.activate_temp_pivot(pivot_mode="center")

        self.assertEqual(result["pivot"], [4.5, 3.5, 0.5])

    def test_shape_selection_resolves_to_parent_transform(self):
        shape = (cmds.listRelatives(self.last, shapes=True, fullPath=True) or [None])[0]

        self.assertEqual(
            tempPivot._selected_temp_pivot_objects([shape]),
            ["|pivot_last"],
        )

    def test_empty_selection_warns_and_does_not_touch_context(self):
        cmds.select(clear=True)

        with mock.patch.object(tempPivot, "om") as open_maya:
            with mock.patch.object(tempPivot.cmds, "setToolTo") as set_tool:
                result = tempPivot.activate_temp_pivot()

        self.assertIsNone(result)
        open_maya.MGlobal.displayWarning.assert_called_once()
        set_tool.assert_not_called()

    def test_deactivate_resets_custom_pivot_and_exits_edit_mode(self):
        tempPivot._temp_pivot_restore_state = {
            "object_pivots": {},
            "tool_context": "moveSuperContext",
            "manip_valid": False,
            "pivots_finalized": False,
            "rotate_context": {
                "pinPivot": False,
                "useManipPivot": False,
                "useCenterPivot": False,
                "useObjectPivot": False,
                "editPivotMode": False,
            },
        }
        with mock.patch.object(tempPivot.cmds, "manipPivot") as manip_pivot:
            with mock.patch.object(
                tempPivot.cmds, "manipRotateContext", return_value=True
            ) as rotate_context:
                with mock.patch.object(tempPivot.cmds, "currentCtx", return_value="RotateSuperContext"):
                    with mock.patch.object(tempPivot.cmds, "setToolTo") as set_tool:
                        with mock.patch.object(tempPivot.cmds, "ctxEditMode") as edit_mode:
                            result = tempPivot.deactivate_temp_pivot()

        self.assertTrue(result)
        edit_mode.assert_called_once_with()
        manip_pivot.assert_has_calls(
            [
                mock.call(pinPivot=False),
                mock.call(reset=True),
            ]
        )
        rotate_context.assert_has_calls(
            [
                mock.call("Rotate", query=True, editPivotMode=True),
                mock.call(
                    "Rotate",
                    edit=True,
                    pinPivot=False,
                    useManipPivot=False,
                    useCenterPivot=False,
                    useObjectPivot=False,
                ),
            ]
        )
        set_tool.assert_called_once_with("moveSuperContext")
        self.assertIsNone(tempPivot._temp_pivot_restore_state)

    def test_deactivate_does_not_restore_objects_after_edit_was_finalized(self):
        tempPivot._temp_pivot_restore_state = {
            "object_pivots": {},
            "tool_context": "RotateSuperContext",
            "manip_valid": False,
            "pivots_finalized": True,
            "rotate_context": {
                "pinPivot": False,
                "useManipPivot": False,
                "useCenterPivot": False,
                "useObjectPivot": False,
                "editPivotMode": False,
            },
        }

        with mock.patch.object(
            tempPivot, "_restore_object_pivots"
        ) as restore_pivots:
            with mock.patch.object(tempPivot.cmds, "manipPivot"):
                with mock.patch.object(
                    tempPivot.cmds,
                    "manipRotateContext",
                    return_value=False,
                ):
                    with mock.patch.object(
                        tempPivot.cmds,
                        "currentCtx",
                        return_value="RotateSuperContext",
                    ):
                        with mock.patch.object(tempPivot.cmds, "setToolTo"):
                            result = tempPivot.deactivate_temp_pivot()

        self.assertTrue(result)
        restore_pivots.assert_not_called()

    def test_finalize_restores_real_pivots_before_animation(self):
        state = tempPivot._capture_temp_pivot_state([self.last])
        state.update({
            "custom_pivot_position": [3.0, -2.0, 5.0],
            "pivots_finalized": False,
            "edit_mode_seen": True,
        })
        tempPivot._temp_pivot_restore_state = state
        original_rotate_pivot = cmds.xform(
            self.last, query=True, objectSpace=True, rotatePivot=True
        )
        cmds.xform(
            self.last,
            objectSpace=True,
            preserve=True,
            pivots=(3.0, -2.0, 5.0),
        )

        with mock.patch.object(tempPivot.cmds, "manipPivot") as manip_pivot:
            with mock.patch.object(
                tempPivot.cmds, "manipRotateContext"
            ) as rotate_context:
                result = tempPivot._finalize_temp_pivot_edit(
                    pivot_position=[3.0, -2.0, 5.0]
                )

        self.assertTrue(result)
        self.assertTrue(state["pivots_finalized"])
        self.assertFalse(state["edit_mode_seen"])
        self.assertEqual(
            cmds.xform(self.last, query=True, objectSpace=True, rotatePivot=True),
            original_rotate_pivot,
        )
        rotate_context.assert_called_once_with(
            "Rotate",
            edit=True,
            useManipPivot=True,
            useCenterPivot=False,
            useObjectPivot=False,
            pinPivot=True,
        )
        manip_pivot.assert_has_calls([
            mock.call(position=[3.0, -2.0, 5.0]),
            mock.call(pinPivot=True),
        ])

    def test_deactivate_preserves_animation_made_after_finalize(self):
        state = tempPivot._capture_temp_pivot_state([self.last])
        state.update({
            "custom_pivot_position": [3.0, -2.0, 5.0],
            "pivots_finalized": False,
            "edit_mode_seen": True,
        })
        tempPivot._temp_pivot_restore_state = state
        cmds.xform(
            self.last,
            objectSpace=True,
            preserve=True,
            pivots=(3.0, -2.0, 5.0),
        )

        with mock.patch.object(tempPivot.cmds, "manipPivot"):
            with mock.patch.object(tempPivot.cmds, "manipRotateContext"):
                tempPivot._finalize_temp_pivot_edit(
                    pivot_position=[3.0, -2.0, 5.0]
                )

        for frame, rotation in ((1, 0.0), (6, 35.0), (12, -20.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(self.last + ".rotateY", rotation)
            cmds.setKeyframe(self.last, attribute="rotateY")

        matrices_before = {}
        for frame in (1, 4, 6, 9, 12):
            cmds.currentTime(frame, edit=True)
            matrices_before[frame] = cmds.xform(
                self.last, query=True, worldSpace=True, matrix=True
            )
        key_values_before = cmds.keyframe(
            self.last + ".rotateY", query=True, valueChange=True
        )

        with mock.patch.object(tempPivot.cmds, "manipPivot"):
            with mock.patch.object(
                tempPivot.cmds, "manipRotateContext", return_value=False
            ):
                with mock.patch.object(
                    tempPivot.cmds,
                    "currentCtx",
                    return_value="RotateSuperContext",
                ):
                    with mock.patch.object(tempPivot.cmds, "setToolTo"):
                        self.assertTrue(tempPivot.deactivate_temp_pivot())

        self.assertEqual(
            cmds.keyframe(
                self.last + ".rotateY", query=True, valueChange=True
            ),
            key_values_before,
        )
        for frame, expected_matrix in matrices_before.items():
            cmds.currentTime(frame, edit=True)
            actual_matrix = cmds.xform(
                self.last, query=True, worldSpace=True, matrix=True
            )
            for actual, expected in zip(actual_matrix, expected_matrix):
                self.assertAlmostEqual(actual, expected, places=6)

    def test_finalize_does_not_change_preexisting_animation(self):
        for frame, rotation in ((1, 5.0), (6, 42.0), (12, -18.0)):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(self.last + ".rotateY", rotation)
            cmds.setKeyframe(self.last, attribute="rotateY")
        key_times_before = cmds.keyframe(
            self.last + ".rotateY", query=True, timeChange=True
        )
        key_values_before = cmds.keyframe(
            self.last + ".rotateY", query=True, valueChange=True
        )
        matrices_before = {}
        for frame in (1, 4, 6, 9, 12):
            cmds.currentTime(frame, edit=True)
            matrices_before[frame] = cmds.xform(
                self.last, query=True, worldSpace=True, matrix=True
            )

        cmds.currentTime(4, edit=True)
        state = tempPivot._capture_temp_pivot_state([self.last])
        state.update({
            "custom_pivot_position": [3.0, -2.0, 5.0],
            "pivots_finalized": False,
            "edit_mode_seen": True,
        })
        tempPivot._temp_pivot_restore_state = state
        cmds.xform(
            self.last,
            objectSpace=True,
            preserve=True,
            pivots=(3.0, -2.0, 5.0),
        )

        with mock.patch.object(tempPivot.cmds, "manipPivot"):
            with mock.patch.object(tempPivot.cmds, "manipRotateContext"):
                tempPivot._finalize_temp_pivot_edit(
                    pivot_position=[3.0, -2.0, 5.0]
                )

        self.assertEqual(
            cmds.keyframe(
                self.last + ".rotateY", query=True, timeChange=True
            ),
            key_times_before,
        )
        self.assertEqual(
            cmds.keyframe(
                self.last + ".rotateY", query=True, valueChange=True
            ),
            key_values_before,
        )
        for frame, expected_matrix in matrices_before.items():
            cmds.currentTime(frame, edit=True)
            actual_matrix = cmds.xform(
                self.last, query=True, worldSpace=True, matrix=True
            )
            for actual, expected in zip(actual_matrix, expected_matrix):
                self.assertAlmostEqual(actual, expected, places=6)

    def test_monitor_finalizes_when_pivot_edit_mode_ends(self):
        tempPivot._temp_pivot_restore_state = {
            "pivots_finalized": False,
            "edit_mode_seen": True,
        }
        with mock.patch.object(
            tempPivot.cmds, "manipRotateContext", return_value=False
        ):
            with mock.patch.object(
                tempPivot, "_finalize_temp_pivot_edit"
            ) as finalize:
                tempPivot._monitor_temp_pivot_edit_mode()

        finalize.assert_called_once_with()

    def test_restore_returns_object_pivot_without_changing_current_pose(self):
        original_rotate_pivot = cmds.xform(
            self.last, query=True, objectSpace=True, rotatePivot=True
        )
        original_scale_pivot = cmds.xform(
            self.last, query=True, objectSpace=True, scalePivot=True
        )
        original_rotate_pivot_translate = cmds.getAttr(
            self.last + ".rotatePivotTranslate"
        )[0]
        original_scale_pivot_translate = cmds.getAttr(
            self.last + ".scalePivotTranslate"
        )[0]
        state = tempPivot._capture_temp_pivot_state([self.last])

        cmds.xform(
            self.last,
            objectSpace=True,
            preserve=True,
            pivots=(3.0, -2.0, 5.0),
        )
        cmds.rotate(17.0, -8.0, 11.0, self.last, relative=True, objectSpace=True)
        world_matrix_before_restore = cmds.xform(
            self.last, query=True, worldSpace=True, matrix=True
        )

        tempPivot._restore_object_pivots(state)

        self.assertEqual(
            cmds.xform(self.last, query=True, objectSpace=True, rotatePivot=True),
            original_rotate_pivot,
        )
        self.assertEqual(
            cmds.xform(self.last, query=True, objectSpace=True, scalePivot=True),
            original_scale_pivot,
        )
        self.assertEqual(
            cmds.getAttr(self.last + ".rotatePivotTranslate")[0],
            original_rotate_pivot_translate,
        )
        self.assertEqual(
            cmds.getAttr(self.last + ".scalePivotTranslate")[0],
            original_scale_pivot_translate,
        )
        world_matrix_after_restore = cmds.xform(
            self.last, query=True, worldSpace=True, matrix=True
        )
        for actual, expected in zip(
            world_matrix_after_restore, world_matrix_before_restore
        ):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_execute_temp_pivot_toggles_button_state(self):
        button = mock.Mock()

        with mock.patch.object(tempPivot, "is_active", return_value=False):
            with mock.patch.object(
                tempPivot, "activate_temp_pivot", return_value={"pivot": [0, 0, 0]}
            ) as activate:
                with mock.patch.object(tempPivot, "set_button_active") as set_active:
                    with mock.patch(
                        "AnimKey.core.executionGuard.require_animkey_context",
                        return_value=True,
                    ):
                        result = tempPivot.execute_temp_pivot(button=button)

        self.assertTrue(result)
        activate.assert_called_once()
        set_active.assert_called_once_with(button, True)

        with mock.patch.object(tempPivot, "is_active", return_value=True):
            with mock.patch.object(
                tempPivot, "deactivate_temp_pivot", return_value=True
            ) as deactivate:
                with mock.patch.object(tempPivot, "set_button_active") as set_active:
                    with mock.patch(
                        "AnimKey.core.executionGuard.require_animkey_context",
                        return_value=True,
                    ):
                        result = tempPivot.execute_temp_pivot(button=button)

        self.assertFalse(result)
        deactivate.assert_called_once_with()
        set_active.assert_called_once_with(button, False)


if __name__ == "__main__":
    unittest.main()
