"""Exercise native ownership and deletion while Qt callbacks are pending."""
import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from AnimKey.mods.maya_compat import QtCore, QtWidgets, shiboken, is_qt_object_valid
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
from AnimKey.core import toolbar
from AnimKey.core.toolbar_glass import ToolbarGlass, install_toolbar_glass
from AnimKey.mods import uiMod


class UiLifecycleStabilityTests(unittest.TestCase):
    def tearDown(self):
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        APP.processEvents()

    def test_dock_owns_glass_and_cancels_pending_geometry_work(self):
        owner = QtWidgets.QWidget()
        anchor = QtWidgets.QWidget(owner)
        strip = QtWidgets.QFrame(anchor)
        glass = ToolbarGlass(strip, anchor)
        timer = glass._sync_timer
        glass.schedule_sync()
        shiboken.delete(anchor)
        self.assertFalse(is_qt_object_valid(glass))
        self.assertFalse(is_qt_object_valid(strip))
        self.assertFalse(is_qt_object_valid(timer))
        APP.processEvents()
        owner.deleteLater()

    def test_repeated_refresh_requests_wait_for_idle_and_rebuild_once(self):
        bar = object.__new__(toolbar.AnimKeyToolbar)
        bar._rebuild_toolbar = mock.Mock()
        pending = []
        with mock.patch('maya.utils.executeDeferred', side_effect=pending.append):
            bar.reload()
            bar.reload()
        bar._rebuild_toolbar.assert_not_called()
        self.assertEqual(len(pending), 1)
        pending.pop()()
        bar._rebuild_toolbar.assert_called_once_with()
        self.assertFalse(bar._reload_pending)

    def test_failed_rebuild_allows_a_later_refresh(self):
        bar = object.__new__(toolbar.AnimKeyToolbar)
        bar._rebuild_toolbar = mock.Mock(side_effect=RuntimeError('test rebuild'))
        pending = []
        with mock.patch('maya.utils.executeDeferred', side_effect=pending.append):
            bar.reload()
            with self.assertRaises(RuntimeError):
                pending.pop()()
            bar.reload()
        self.assertEqual(len(pending), 1)

    def test_tool_cleanup_does_not_destroy_the_active_button_callback(self):
        window = QtWidgets.QWidget()
        window.setObjectName('AnimKey_LifecycleTest')
        button = QtWidgets.QPushButton('Close', window)
        state = []
        def close_from_button():
            uiMod.close_animkey_tool_windows(include_hidden=True, force_close=True)
            state.append(is_qt_object_valid(button))
        button.clicked.connect(close_from_button)
        window.show()
        button.click()
        self.assertEqual(state, [True])
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.assertFalse(is_qt_object_valid(window))

    def test_pending_gif_preview_tolerates_an_externally_deleted_button(self):
        controller = toolbar.ToolbarGifPreviewController()
        button = QtWidgets.QPushButton('Preview')
        controller._hover_button = button
        shiboken.delete(button)
        controller._show_current_preview()
        controller.dispose()

    def test_maya_controls_stay_inside_the_native_dock(self):
        from types import SimpleNamespace
        dock = QtWidgets.QWidget()
        strip = QtWidgets.QFrame(dock)
        bar = SimpleNamespace(toolbar_scroll_area=strip)
        with mock.patch('AnimKey.core.toolbar_glass.get_maya_main_window', return_value=dock):
            self.assertIsNone(install_toolbar_glass(bar))
        self.assertIs(strip.parentWidget(), dock)
        dock.deleteLater()


if __name__ == '__main__':
    unittest.main(verbosity=2)
