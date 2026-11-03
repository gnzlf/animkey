"""Actual Qt surfaces, menu routes and saved toolbar layout migration."""
import ast
from pathlib import Path
import unittest
from unittest import mock
from AnimKey.mods.maya_compat import QtCore,QtWidgets
APP=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import tempControlsUI as ui,tempPivot,tempControls
from AnimKey.core import toolbar,settings
from AnimKey.mods import mediaMod,hotkeysMod,configMod


class TempToolsUITests(unittest.TestCase):
    def tearDown(self):
        ui.close_windows()
        tempPivot.deactivate_temp_pivot(restore_selection=False)
        APP.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)

    def test_creation_dialog_has_four_systems_independent_spaces_and_family_limits(self):
        window=ui.CreateWindow()
        try:
            self.assertEqual(window.family.count(),4)
            self.assertEqual(window.position.count(),6)
            self.assertFalse(window.orientation.isEnabled())
            window.lock.setChecked(False)
            self.assertTrue(window.orientation.isEnabled())
            window.family.setCurrentIndex(1)
            self.assertFalse(window.position.model().item(1).isEnabled())
            self.assertFalse(window.position.model().item(4).isEnabled())
            window.family.setCurrentIndex(3)
            self.assertTrue(window.position.model().item(1).isEnabled())
            self.assertFalse(window.position.model().item(4).isEnabled())
            window.family.setCurrentIndex(2)
            self.assertEqual(window.create_button.text(),"Create Aim Setup")
        finally: window.close()

    def test_new_button_uses_same_icon_and_has_visibility_and_shortcut_entries(self):
        self.assertEqual(mediaMod.get_button_icon("PIV"),mediaMod.get_button_icon("TCT"))
        self.assertIn("TCT",settings.DEFAULT_SHORTCUTS)
        self.assertTrue(configMod.DEFAULT_WORKSPACE["buttons"]["TCT"])
        self.assertIn("tempControls",hotkeysMod.get_action_function("TCT"))
        self.assertIn("execute_temp_pivot",hotkeysMod.get_action_function("PIV"))

    def test_saved_order_places_copy_between_hierarchy_and_mirror_and_controls_next_to_pivot(self):
        root=QtWidgets.QWidget();layout=QtWidgets.QHBoxLayout(root)
        names=("HIR","MIR","C","WS","PIV","RUL","TCT")
        for name in names:
            button=QtWidgets.QPushButton(name);button.setObjectName(name);layout.addWidget(button)
        bar=object.__new__(toolbar.AnimKeyToolbar)
        bar._apply_layout_order(layout,["HIR","MIR","C","WS","PIV","RUL"])
        actual=[layout.itemAt(i).widget().objectName() for i in range(layout.count()) if layout.itemAt(i).widget()]
        self.assertEqual(actual[:3],["HIR","C","MIR"])
        self.assertEqual(actual[actual.index("PIV")+1],"TCT")
        root.deleteLater()

    def test_default_button_lists_have_copy_and_temp_controls_in_the_requested_positions(self):
        module=ast.parse(Path(toolbar.__file__).read_text(encoding="utf-8-sig"))
        definitions={}
        for node in ast.walk(module):
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.List):
                for target in node.targets:
                    if isinstance(target,ast.Name) and target.id in ("tools_group2","tools_after_slider"):
                        definitions[target.id]=[item.elts[0].value for item in node.value.elts]
        self.assertEqual(definitions["tools_group2"][-3:],["HIR","C","MIR"])
        after=definitions["tools_after_slider"]
        self.assertEqual(after[after.index("PIV")+1],"TCT")
        self.assertNotIn("C",after)

    def test_right_click_on_pivot_only_offers_the_five_pivot_actions(self):
        button=QtWidgets.QPushButton()
        labels=[]
        with mock.patch("AnimKey.mods.maya_compat.execute_qt",side_effect=lambda menu,pos:labels.extend(action.text() for action in menu.actions() if not action.isSeparator())):
            tempPivot.show_context_menu(button,QtCore.QPoint())
        self.assertEqual(labels,["Temp Pivot To Last Object","Temp Pivot Centered","Temp Pivot World Space","Toggle Edit Temp Pivot","Reset Temp Pivot"])
        button.deleteLater()

    def test_right_click_on_control_offers_control_operations_and_separate_panel(self):
        button=QtWidgets.QPushButton();labels=[]
        with mock.patch.object(ui,"execute_qt",side_effect=lambda menu,pos:labels.extend(action.text() for action in menu.actions() if not action.isSeparator())):
            ui.show_context_menu(button,QtCore.QPoint())
        for label in ("Baking Mode","World Space","Child Space","Toggle and Revert","Clear and Bake","Temp Controls Panel"):
            self.assertIn(label,labels)
        self.assertNotIn("Temp Pivot Centered",labels)
        button.deleteLater()

    def test_panel_closure_does_not_clear_persistent_systems(self):
        with mock.patch.object(tempControls,"clear") as clear:
            panel=ui.Panel()
            self.assertGreater(panel.size().width(),0)
            self.assertIsInstance(panel.layout(),QtWidgets.QVBoxLayout)
            panel.close()
            clear.assert_not_called()


if __name__=="__main__": unittest.main()
