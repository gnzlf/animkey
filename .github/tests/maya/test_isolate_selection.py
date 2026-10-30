import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.buttons import isolate
from AnimKey.core.executionGuard import animkey_execution


class IsolateSelectionTests(unittest.TestCase):
    def test_icon_update_uses_the_target_panels_actual_bar_layout(self):
        maya_cmds = mock.Mock()
        maya_cmds.modelPanel.return_value = "customWindow|panel7|icons"
        correct = "customWindow|panel7|icons|flow|IsolateSelectedBtn"
        maya_cmds.lsUI.return_value = [correct, "otherWindow|panel7|icons|IsolateSelectedBtn"]
        with mock.patch.object(isolate, "cmds", maya_cmds):
            isolate._update_maya_isolate_icon("panel7", True)
        maya_cmds.iconTextCheckBox.assert_called_once_with(correct, edit=True, value=True)

    def test_state_falls_back_to_native_command_when_mel_is_unavailable(self):
        maya_cmds = mock.Mock()
        with mock.patch.object(isolate, "cmds", maya_cmds), \
                mock.patch.object(isolate.mel, "eval", side_effect=RuntimeError("no UI proc")):
            isolate._set_isolate_state("modelPanel7", True)
        maya_cmds.isolateSelect.assert_called_once_with("modelPanel7", state=1)

    def test_down_one_level_does_not_drop_a_root_without_parents(self):
        maya_cmds = mock.Mock()
        maya_cmds.ls.return_value = ["|prop"]
        maya_cmds.listRelatives.return_value = []
        with mock.patch.object(isolate, "cmds", maya_cmds):
            self.assertEqual(isolate.get_root_node("|prop", down_one_level=True), "|prop")

    def test_no_viewport_does_not_change_selection_or_isolate_state(self):
        maya_cmds = mock.Mock()
        with mock.patch.object(isolate, "cmds", maya_cmds), \
                mock.patch.object(isolate, "_get_active_model_panel", return_value=None), \
                animkey_execution("isolate regression"):
            self.assertFalse(isolate.execute())
        maya_cmds.select.assert_not_called()
        maya_cmds.isolateSelect.assert_not_called()

    def test_toolbar_focus_uses_the_active_viewport_and_restores_the_selection(self):
        maya_cmds = mock.Mock()
        maya_cmds.ls.return_value = ["|Hero:rig|Hero:ctrl"]
        maya_cmds.getPanel.return_value = "AnimKeyToolbar"
        maya_cmds.isolateSelect.return_value = False
        with mock.patch.object(isolate, "cmds", maya_cmds), \
                mock.patch.object(isolate, "_get_active_model_panel", return_value="modelPanel2", create=True), \
                mock.patch.object(isolate, "get_root_node", return_value="|Hero:rig"), \
                mock.patch.object(isolate, "include_parented_objects_enabled", return_value=False), \
                mock.patch.object(isolate, "_load_isolate_objects", return_value=True) as load, \
                mock.patch.object(isolate, "_update_maya_isolate_icon"), animkey_execution("isolate regression"):
            isolate.execute()
        load.assert_called_once_with("modelPanel2", ["|Hero:rig"])
        maya_cmds.select.assert_called_with(["|Hero:rig|Hero:ctrl"], replace=True)

    def test_active_isolate_can_be_disabled_without_building_roots_or_changing_selection(self):
        maya_cmds = mock.Mock()
        maya_cmds.ls.return_value = ["|Hero:rig|Hero:ctrl"]
        maya_cmds.isolateSelect.return_value = True
        with mock.patch.object(isolate, "cmds", maya_cmds), \
                mock.patch.object(isolate, "_get_active_model_panel", return_value="modelPanel2", create=True), \
                mock.patch.object(isolate, "get_root_node") as root, \
                mock.patch.object(isolate, "_set_isolate_state", create=True) as state, \
                mock.patch.object(isolate, "_update_maya_isolate_icon"), \
                animkey_execution("isolate regression"):
            isolate.execute()
        state.assert_called_once_with("modelPanel2", False)
        root.assert_not_called()

    def test_failed_activation_restores_component_selection_and_does_not_report_success(self):
        maya_cmds = mock.Mock()
        maya_cmds.ls.side_effect = lambda **kwargs: ["|mesh.vtx[2]"] if not kwargs.get("objectsOnly") else ["|mesh"]
        maya_cmds.isolateSelect.return_value = False
        with mock.patch.object(isolate, "cmds", maya_cmds), \
                mock.patch.object(isolate, "_get_active_model_panel", return_value="modelPanel2", create=True), \
                mock.patch.object(isolate, "get_root_node", return_value="|mesh"), \
                mock.patch.object(isolate, "include_parented_objects_enabled", return_value=False), \
                mock.patch.object(isolate, "_load_isolate_objects", return_value=False), \
                mock.patch.object(isolate, "_update_maya_isolate_icon") as icon, \
                animkey_execution("isolate regression"):
            result = isolate.execute()
        self.assertFalse(result)
        icon.assert_not_called()
        maya_cmds.select.assert_called_with(["|mesh.vtx[2]"], replace=True)

    def test_load_replaces_previous_isolate_members(self):
        maya_cmds = mock.Mock()
        maya_cmds.objExists.return_value = True

        with mock.patch.object(isolate, "cmds", maya_cmds), mock.patch.object(isolate.mel, "eval"):
            self.assertTrue(isolate._load_isolate_objects("modelPanel4", ["RigA"]))

        maya_cmds.select.assert_called_once_with(["RigA"], replace=True)
        maya_cmds.isolateSelect.assert_has_calls([
            mock.call("modelPanel4", state=1),
            mock.call("modelPanel4", loadSelected=True),
            mock.call("modelPanel4", update=True),
        ])
        self.assertNotIn(
            mock.call("modelPanel4", addSelected=True),
            maya_cmds.isolateSelect.call_args_list,
        )

    def test_namespaced_control_stops_at_its_own_rig_root(self):
        control = "|scene|characters|HeroA:rig|HeroA:ctrl"
        rig_root = "|scene|characters|HeroA:rig"
        shared_group = "|scene|characters"
        parents = {control: rig_root, rig_root: shared_group}

        maya_cmds = mock.Mock()
        maya_cmds.ls.return_value = [control]
        maya_cmds.listRelatives.side_effect = (
            lambda node, parent=False, **_kwargs:
            [parents[node]] if parent and node in parents else []
        )

        with mock.patch.object(isolate, "cmds", maya_cmds):
            self.assertEqual(isolate.get_root_node(control), rig_root)

    def test_unnamespaced_control_stops_before_shared_rig_group(self):
        control = "|scene|characters|HeroA_Rig|ctrl"
        rig_root = "|scene|characters|HeroA_Rig"
        other_root = "|scene|characters|HeroB_Rig"
        shared_group = "|scene|characters"
        rig_modules_a = rig_root + "|rig_modules"
        rig_modules_b = other_root + "|rig_modules"
        parents = {control: rig_root, rig_root: shared_group}

        def list_relatives(node, parent=False, children=False, **_kwargs):
            if parent:
                return [parents[node]] if node in parents else []
            if children:
                if node == rig_root:
                    return [rig_modules_a]
                if node == other_root:
                    return [rig_modules_b]
                if node == shared_group:
                    return [rig_root, other_root]
            return []

        maya_cmds = mock.Mock()
        maya_cmds.ls.return_value = [control]
        maya_cmds.listRelatives.side_effect = list_relatives

        with mock.patch.object(isolate, "cmds", maya_cmds):
            self.assertEqual(isolate.get_root_node(control), rig_root)


if __name__ == "__main__":
    unittest.main()
