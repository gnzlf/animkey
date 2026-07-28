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
        with mock.patch.object(tempPivot.cmds, "manipPivot") as manip_pivot:
            with mock.patch.object(
                tempPivot.cmds, "manipRotateContext", return_value=True
            ) as rotate_context:
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
