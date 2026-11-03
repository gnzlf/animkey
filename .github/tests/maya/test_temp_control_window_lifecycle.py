import unittest

from AnimKey.mods.maya_compat import QtCore, QtWidgets
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds
from AnimKey.core.runtime_lifecycle import close_all
from AnimKey.buttons import tempPivot
from AnimKey.buttons.tempControlsUI import Panel as TempControlWindow


class TempControlWindowLifecycleTests(unittest.TestCase):
    def test_slider_widget_destruction_cancels_only_its_gesture(self):
        from AnimKey.mods.uiMod import AnimKeySlider
        from AnimKey.sliders import blend_to_default
        cmds.file(new=True, force=True)
        node = cmds.createNode("transform")
        cmds.setKeyframe(node, attribute="tx", time=1, value=10)
        cmds.currentTime(1)
        slider = AnimKeySlider(parent=self.parent)
        slider.sliderPressed.connect(lambda: blend_to_default.prepare_blend_data([node], ["translateX"]))
        slider.sliderPressed.emit()
        slider._remember_runtime_gesture()
        blend_to_default.execute(100)
        self.assertEqual(cmds.getAttr(node + ".tx"), 0.)
        slider.deleteLater()
        _APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.assertEqual(cmds.getAttr(node + ".tx"), 10.)
        self.assertFalse(cmds.undoInfo(query=True, chunkName=True))

    def test_native_pivot_monitor_is_retired_on_new_scene(self):
        cmds.file(new=True, force=True)
        node = cmds.createNode("transform")
        self.assertTrue(tempPivot.activate_temp_pivot([node], edit_pivot=True))
        scope = tempPivot._temp_pivot_monitor_scope
        self.assertTrue(scope.active)
        cmds.file(new=True, force=True)
        self.assertFalse(scope.active)
        self.assertEqual(scope.pending_cleanup, ())
        self.assertIsNone(tempPivot._temp_pivot_restore_state)

    def setUp(self):
        # Standalone has no Maya main window. Keep all other Qt/API behavior real.
        self.parent = QtWidgets.QWidget()

    def tearDown(self):
        self.assertTrue(close_all())
        self.parent.deleteLater()
        _APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_window_close_reopen_and_scene_change_have_one_resource_owner(self):
        first = TempControlWindow(parent=self.parent)
        first_scope = first.resources
        first.close()
        self.assertFalse(first_scope.active)
        self.assertEqual(first_scope.pending_cleanup, ())
        first.deleteLater()
        second = TempControlWindow(parent=self.parent)
        try:
            cmds.file(new=True, force=True)
            node = cmds.createNode("transform", name="WindowLifecycleProbe")
            cmds.select(node)
            self.assertTrue(second.resources.active)
            self.assertEqual(set(second.resources.pending_cleanup), {"selection", "status.timer"})
        finally:
            second.close()
            second.deleteLater()
            _APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_direct_qobject_destruction_closes_callbacks_without_close_event(self):
        window = TempControlWindow(parent=self.parent)
        scope = window.resources
        window.deleteLater()
        _APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.assertFalse(scope.active)
        self.assertEqual(scope.pending_cleanup, ())


if __name__ == "__main__":
    unittest.main()
