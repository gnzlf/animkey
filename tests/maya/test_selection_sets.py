import unittest

try:
    from PySide2 import QtCore, QtWidgets
except ImportError:
    from PySide6 import QtCore, QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons.selectionSets import SetButton, TabPage


class SelectionSetNamespaceTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.buttons = []

    def tearDown(self):
        cmds.select(clear=True)
        for button in self.buttons:
            button.deleteLater()

    def _namespace(self, name):
        if not cmds.namespace(exists=name):
            cmds.namespace(add=name)

    def _button(self, name, members, dynamic=False, targets=None):
        button = SetButton(
            name,
            members,
            namespace_dynamic_func=lambda: dynamic,
            get_namespaces_func=lambda: list(targets or []),
            get_namespace_func=lambda: (targets or [""])[0],
        )
        self.buttons.append(button)
        return button

    def _selected(self):
        return set(cmds.ls(selection=True, long=True) or [])

    def test_clicking_a_second_rig_preserves_the_first_rig(self):
        self._namespace("rigA")
        self._namespace("propB")
        arm = cmds.createNode("transform", name="rigA:arm_CTRL")
        torso = cmds.createNode("transform", name="rigA:torso_CTRL")
        prop = cmds.createNode("transform", name="propB:prop_CTRL")

        arm_set = self._button("Arm", [arm])
        torso_set = self._button("Torso", [torso])
        prop_set = self._button("Prop", [prop])

        arm_set.do_select()
        prop_set.do_select()
        self.assertEqual(
            self._selected(),
            set(cmds.ls([arm, prop], long=True)),
        )

        torso_set.do_select()
        self.assertEqual(
            self._selected(),
            set(cmds.ls([torso, prop], long=True)),
        )

    def test_unlocked_set_retargets_to_another_namespace(self):
        self._namespace("charA")
        self._namespace("charB")
        source = cmds.createNode("transform", name="charA:hand_CTRL")
        target = cmds.createNode("transform", name="charB:hand_CTRL")

        locked = self._button("Locked", [source], dynamic=False, targets=["charB"])
        unlocked = self._button("Unlocked", [source], dynamic=True, targets=["charB"])

        self.assertEqual(locked.get_resolved_members(), cmds.ls(source, long=True))
        self.assertEqual(unlocked.get_resolved_members(), cmds.ls(target, long=True))

    def test_unlocked_set_can_target_multiple_rig_instances(self):
        self._namespace("charA")
        self._namespace("charB")
        source = cmds.createNode("transform", name="charA:hand_CTRL")
        target = cmds.createNode("transform", name="charB:hand_CTRL")
        button = self._button(
            "Both Hands",
            [source],
            dynamic=True,
            targets=["charA", "charB"],
        )

        self.assertEqual(
            set(button.get_resolved_members()),
            set(cmds.ls([source, target], long=True)),
        )

    def test_dynamic_shift_add_falls_back_to_the_sets_saved_rig(self):
        self._namespace("rigA")
        self._namespace("propB")
        selected_rig = cmds.createNode("transform", name="rigA:body_CTRL")
        prop = cmds.createNode("transform", name="propB:prop_CTRL")
        prop_set = self._button("Prop", [prop], dynamic=True, targets=["rigA"])

        cmds.select(selected_rig, replace=True)
        prop_set.do_add()

        self.assertEqual(
            self._selected(),
            set(cmds.ls([selected_rig, prop], long=True)),
        )

    def test_shift_with_an_additional_modifier_still_adds_the_second_set(self):
        self._namespace("rigA")
        self._namespace("rigB")
        first = cmds.createNode("transform", name="rigA:first_CTRL")
        second = cmds.createNode("transform", name="rigB:second_CTRL")
        second_set = self._button("Second", [second])

        cmds.select(first, replace=True)
        modifiers = QtCore.Qt.ShiftModifier | QtCore.Qt.KeypadModifier
        second_set._dispatch_selection_action(modifiers)

        self.assertEqual(
            self._selected(),
            set(cmds.ls([first, second], long=True)),
        )

    def test_one_locked_set_can_contain_rig_and_prop_namespaces(self):
        self._namespace("character")
        self._namespace("prop")
        control = cmds.createNode("transform", name="character:hand_CTRL")
        prop = cmds.createNode("transform", name="prop:handle_CTRL")
        button = self._button("Character And Prop", [control, prop])

        self.assertEqual(
            set(button.get_resolved_members()),
            set(cmds.ls([control, prop], long=True)),
        )

    def test_legacy_namespace_data_is_migrated_to_a_stable_binding(self):
        self._namespace("character")
        control = cmds.createNode("transform", name="character:hand_CTRL")
        button = SetButton(
            "Legacy",
            ["hand_CTRL"],
            namespaces=["character"],
            namespace_dynamic_func=lambda: False,
        )
        self.buttons.append(button)

        self.assertEqual(button.get_resolved_members(), cmds.ls(control, long=True))
        self.assertEqual(len(button.member_bindings), 1)
        self.assertTrue(button.member_bindings[0]["uuid"])

    def test_saved_bindings_reload_without_duplicate_members(self):
        self._namespace("character")
        control = cmds.createNode("transform", name="character:hand_CTRL")
        original = self._button("Original", [control])
        data = original.get_data()
        restored = SetButton(
            data["name"],
            data["members"],
            namespaces=data["namespaces"],
            member_namespaces=data["member_namespaces"],
            member_bindings=data["member_bindings"],
            namespace_dynamic_func=lambda: False,
        )
        self.buttons.append(restored)

        self.assertEqual(len(restored.member_bindings), 1)
        self.assertEqual(restored.get_resolved_members(), cmds.ls(control, long=True))

    def test_lock_disables_global_namespace_and_unlock_enables_it(self):
        page = TabPage()
        try:
            self.assertFalse(page.is_namespace_dynamic())
            self.assertFalse(page.ns_combo.isEnabled())
            self.assertNotIn("#E67E22", page.namespace_lock_btn.styleSheet())
            page.set_namespace_dynamic(True, save=False)
            self.assertTrue(page.is_namespace_dynamic())
            self.assertTrue(page.ns_combo.isEnabled())
        finally:
            page._auto_ns_timer.stop()
            page.deleteLater()

    def test_namespace_less_set_stays_on_its_original_rig(self):
        root_a = cmds.createNode("transform", name="rigRootA")
        root_b = cmds.createNode("transform", name="rigRootB")
        ctrl_a = cmds.createNode("transform", name="CTRL", parent=root_a)
        ctrl_a = cmds.ls(ctrl_a, long=True)[0]
        cmds.createNode("transform", name="CTRL", parent=root_b)

        button = self._button("Specific", [ctrl_a], dynamic=True, targets=[""])
        cmds.rename(ctrl_a, "RENAMED_CTRL")
        resolved = button.get_resolved_members()

        self.assertEqual(len(resolved), 1)
        self.assertTrue(resolved[0].endswith("|RENAMED_CTRL"))
        self.assertTrue(resolved[0].startswith("|rigRootA|"))

    def test_namespace_less_rig_and_prop_can_be_selected_together(self):
        rig_root = cmds.createNode("transform", name="rigRoot")
        prop_root = cmds.createNode("transform", name="propRoot")
        rig_ctrl = cmds.createNode("transform", name="rig_CTRL", parent=rig_root)
        prop_ctrl = cmds.createNode("transform", name="prop_CTRL", parent=prop_root)
        rig_set = self._button("Rig", [cmds.ls(rig_ctrl, long=True)[0]])
        prop_set = self._button("Prop", [cmds.ls(prop_ctrl, long=True)[0]])

        rig_set.do_select()
        prop_set.do_select()
        self.assertEqual(
            self._selected(),
            set(cmds.ls([rig_ctrl, prop_ctrl], long=True)),
        )

    def test_geometry_and_face_components_are_persisted(self):
        root = cmds.createNode("transform", name="geoRoot")
        geometry = cmds.polyCube(name="bodyGeo")[0]
        geometry = cmds.parent(geometry, root)[0]
        face_range = geometry + ".f[0:2]"

        geometry_set = self._button("Geometry", [geometry])
        face_set = self._button("Arm Faces", [face_range])

        self.assertEqual(geometry_set.get_resolved_members(), cmds.ls(geometry, long=True))
        resolved_faces = face_set.get_resolved_members()
        self.assertEqual(len(resolved_faces), 1)
        self.assertTrue(resolved_faces[0].endswith(".f[0:2]"))

        saved = face_set.get_data()
        self.assertEqual(saved["member_bindings"][0]["kind"], "face")
        self.assertTrue(saved["member_bindings"][0]["uuid"])

    def test_face_set_can_hide_and_restore_faces(self):
        geometry = cmds.polyCube(name="bodyGeo")[0]
        face_range = geometry + ".f[0:2]"
        face_set = self._button("Arm Faces", [face_range])

        face_set.do_toggle_visibility()
        self.assertTrue(face_set.members_hidden)
        hidden_sets = [
            node
            for node in (cmds.ls(type="objectSet") or [])
            if "HiddenFacesSet" in node
        ]
        self.assertTrue(hidden_sets)
        hidden_members = []
        for hidden_set in hidden_sets:
            hidden_members.extend(cmds.sets(hidden_set, query=True) or [])
        self.assertTrue(any("f[0:2]" in member for member in hidden_members))

        face_set.do_toggle_visibility()
        remaining_members = []
        for hidden_set in cmds.ls(type="objectSet") or []:
            if "HiddenFacesSet" in hidden_set:
                remaining_members.extend(cmds.sets(hidden_set, query=True) or [])
        self.assertFalse(any("f[0:2]" in member for member in remaining_members))
        self.assertFalse(face_set.members_hidden)

    def test_set_selection_highlight_supports_shift_and_control(self):
        first_node = cmds.createNode("transform", name="first_CTRL")
        second_node = cmds.createNode("transform", name="second_CTRL")
        page = TabPage()
        page._auto_ns_timer.stop()
        first = SetButton("First", [first_node], namespace_dynamic_func=lambda: False)
        second = SetButton("Second", [second_node], namespace_dynamic_func=lambda: False)
        page.container.add_button(first)
        page.container.add_button(second)
        try:
            first._dispatch_selection_action(QtCore.Qt.NoModifier)
            self.assertTrue(first.ui_selected)
            self.assertFalse(second.ui_selected)
            self.assertIn("#78BFFF", first.styleSheet())

            second._dispatch_selection_action(QtCore.Qt.ShiftModifier)
            self.assertEqual(page.container.selected_buttons(), [first, second])

            first._dispatch_selection_action(QtCore.Qt.ControlModifier)
            self.assertEqual(page.container.selected_buttons(), [second])
        finally:
            page.deleteLater()

    def test_toolbar_eye_toggles_selected_geometry_sets(self):
        first_geo = cmds.polyCube(name="bodyGeo")[0]
        second_geo = cmds.polyCube(name="propGeo")[0]
        page = TabPage()
        page._auto_ns_timer.stop()
        first = SetButton("Body", [first_geo], namespace_dynamic_func=lambda: False)
        second = SetButton("Prop", [second_geo], namespace_dynamic_func=lambda: False)
        page.container.add_button(first)
        page.container.add_button(second)
        try:
            self.assertFalse(hasattr(first, "visibility_btn"))
            self.assertFalse(page.set_visibility_btn.isEnabled())
            self.assertFalse(page.set_visibility_btn.icon().isNull())

            page.container.select_button(first, QtCore.Qt.NoModifier)
            page.container.select_button(second, QtCore.Qt.ShiftModifier)
            self.assertTrue(page.set_visibility_btn.isEnabled())

            page.toggle_selected_set_visibility()
            self.assertFalse(cmds.getAttr(first_geo + ".visibility"))
            self.assertFalse(cmds.getAttr(second_geo + ".visibility"))
            self.assertTrue(first.members_hidden)
            self.assertTrue(second.members_hidden)
            self.assertIn("#555A60", first.styleSheet())
            self.assertEqual(page.set_visibility_btn.toolTip(), "Show selected sets")

            page.toggle_selected_set_visibility()
            self.assertTrue(cmds.getAttr(first_geo + ".visibility"))
            self.assertTrue(cmds.getAttr(second_geo + ".visibility"))
            self.assertFalse(first.members_hidden)
            self.assertFalse(second.members_hidden)
        finally:
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()
