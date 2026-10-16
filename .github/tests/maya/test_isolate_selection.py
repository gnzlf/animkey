import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.buttons import isolate


class IsolateSelectionTests(unittest.TestCase):
    def test_load_replaces_previous_isolate_members(self):
        maya_cmds = mock.Mock()
        maya_cmds.objExists.return_value = True

        with mock.patch.object(isolate, "cmds", maya_cmds):
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
