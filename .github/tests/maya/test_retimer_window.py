"""Retimer popup lifetime regressions, run in mayapy's standalone Qt loop."""

import unittest
from unittest import mock

import maya.standalone
import maya.cmds as cmds
from AnimKey.mods.maya_compat import QtCore, QtWidgets

_QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.buttons import retimer
from AnimKey.mods import uiMod


class RetimerWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.parent = QtWidgets.QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.main_window = mock.patch.object(
            retimer, "get_maya_main_window", return_value=self.parent
        )
        self.main_window.start()
        self.addCleanup(self.main_window.stop)

    def tearDown(self):
        if retimer._retimer_window is not None:
            retimer._retimer_window.close()
        self.app.processEvents()
        QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()

    def test_repeated_open_reuses_a_single_window(self):
        with mock.patch.object(retimer.Retimer, "find_all", return_value=["main"]), \
                mock.patch.object(retimer, "_get_active_retimer_name", return_value="main"), \
                mock.patch.object(retimer, "_set_active_retimer_name"), \
                mock.patch.object(retimer.Retimer, "create_curve"), \
                mock.patch.object(retimer.Retimer, "is_preview_on", return_value=False), \
                mock.patch.object(retimer.cmds, "deleteUI") as delete_ui:
            for _ in range(20):
                window = retimer.show()
                self.assertIs(window, retimer.show())
                window._refresh_timer.timeout.emit()
                window.enterEvent(None)
                window.leaveEvent(None)
                window.close()
                self.app.processEvents()
                self.assertIsNone(retimer._retimer_window)
            delete_ui.assert_not_called()

    def test_retimer_curve_in_maya_title_does_not_classify_maya_as_animkey(self):
        maya_window = QtWidgets.QWidget()
        maya_window.setObjectName("MayaWindow")
        maya_window.setWindowTitle(
            "untitled - Autodesk MAYA 2024 --- ANIMKEY_RT_main_timeWarpCurve"
        )
        self.addCleanup(maya_window.deleteLater)
        self.assertFalse(uiMod._is_animkey_tool_window(maya_window))

        unnamed_maya_window = QtWidgets.QWidget()
        unnamed_maya_window.setWindowTitle(maya_window.windowTitle())
        self.addCleanup(unnamed_maya_window.deleteLater)
        self.assertFalse(uiMod._is_animkey_tool_window(unnamed_maya_window))

        animkey_window = QtWidgets.QWidget()
        animkey_window.setObjectName("AnimKey_Retimer")
        self.addCleanup(animkey_window.deleteLater)
        self.assertTrue(uiMod._is_animkey_tool_window(animkey_window))

    def test_close_cancels_pending_refresh_and_animation(self):
        with mock.patch.object(retimer.Retimer, "find_all") as find_all:
            window = retimer.show()
            window._animate(1.0)
            self.assertTrue(window._refresh_timer.isActive())
            window.close()
            self.assertFalse(window._refresh_timer.isActive())
            self.assertEqual(window._opacity_animation.state(), QtCore.QAbstractAnimation.Stopped)
            self.app.processEvents()
            find_all.assert_not_called()

    def test_hidden_window_defers_scene_refresh_until_shown_again(self):
        with mock.patch.object(retimer.Retimer, "find_all") as find_all:
            window = retimer.show()
            window.hide()
            self.assertFalse(window._refresh_timer.isActive())
            window.show()
            self.assertTrue(window._refresh_timer.isActive())
            window.close()
            self.app.processEvents()
            find_all.assert_not_called()

    def test_reopening_does_not_rewrite_existing_time_curve(self):
        cmds.file(new=True, force=True)
        self.addCleanup(cmds.file, new=True, force=True)
        rt = retimer.Retimer("main")
        rt.create_curve()
        time_curve = rt._time_curve_node()
        cmds.keyTangent(time_curve, edit=True, inTangentType="spline", outTangentType="spline")

        with mock.patch.object(retimer, "_get_active_retimer_name", return_value="main"), \
                mock.patch.object(retimer, "_set_active_retimer_name"), \
                mock.patch.object(retimer.Retimer, "create_curve") as create_curve:
            window = retimer.show()
            window._refresh_timer.stop()
            window._refresh()
            create_curve.assert_not_called()
            window.close()
        rt.create_curve()
        self.assertEqual(
            cmds.keyTangent(time_curve, query=True, outTangentType=True),
            ["spline", "spline"],
        )


if __name__ == "__main__":
    unittest.main()
