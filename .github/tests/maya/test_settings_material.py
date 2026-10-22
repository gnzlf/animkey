"""Exercise the Settings pilot's custom input and lazy pages without user writes."""
import importlib
import os
import unittest
from unittest import mock

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
QtTest = importlib.import_module(QtCore.__package__ + ".QtTest")
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
APP.setFont(QtGui.QFont("Segoe UI", 9))

import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.core import settings
from AnimKey.core.settings_style import SettingsSwitch, STYLESHEET, WindowControl, apply_native_backdrop


class MaterialSettingsTests(unittest.TestCase):
    def setUp(self):
        self.window = None
        self.config_write = mock.patch.object(settings.config, "set_setting").start()
        self.shortcut_write = mock.patch.object(settings, "save_shortcuts").start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        if self.window:
            self.window.close()
            self.window.deleteLater()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def _window(self):
        with mock.patch.object(settings.config, "get_setting", side_effect=lambda key, default=None: False if key == "check_updates" else default), \
                mock.patch.object(settings.ui, "get_maya_main_window", return_value=None):
            self.window = settings.SettingsWindow()
        self.window.resize(780, 760)
        self.window.show()
        APP.processEvents()
        return self.window

    def test_switch_space_click_disabled_and_programmatic_state(self):
        switch = SettingsSwitch("Enabled")
        switch.resize(280, 38)
        switch.show()
        self.addCleanup(switch.close)
        changes = mock.Mock()
        switch.toggled.connect(changes)
        QtTest.QTest.mouseClick(switch, QtCore.Qt.LeftButton, pos=QtCore.QPoint(10, 19))
        self.assertTrue(switch.isChecked())
        self.assertEqual(changes.call_count, 1)
        QtTest.QTest.keyClick(switch, QtCore.Qt.Key_Space)
        self.assertFalse(switch.isChecked())
        self.assertEqual(changes.call_count, 2)
        switch.setEnabled(False)
        QtTest.QTest.mouseClick(switch, QtCore.Qt.LeftButton)
        self.assertEqual(changes.call_count, 2)
        switch.hide()
        switch.setChecked(True)
        switch.show()
        self.assertEqual(switch._position, 1.0)

    def test_general_switch_calls_existing_handler_once(self):
        window = self._window()
        switch = window.tumble_around_selection_checkbox
        with mock.patch("AnimKey.mods.tumbleAroundSelection.apply") as apply:
            enabled = not switch.isChecked()
            QtTest.QTest.mouseClick(switch, QtCore.Qt.LeftButton)
        self.config_write.assert_called_once_with("tumble_around_selection_enabled", enabled)
        apply.assert_called_once_with(enabled)

    def test_scale_controls_stay_in_sync_without_saving_early(self):
        window = self._window()
        window.scale_slider.setValue(175)
        self.assertAlmostEqual(window.scale_spinbox.value(), 1.75)
        window.scale_spinbox.setValue(2.5)
        self.assertEqual(window.scale_slider.value(), 250)
        self.config_write.assert_not_called()

    def test_window_controls_keep_actions_and_hover_geometry(self):
        host = QtWidgets.QWidget()
        minimize, maximize, close = mock.Mock(), mock.Mock(), mock.Mock()
        host.showMinimized = minimize
        host.showMaximized = maximize
        host.close = close
        title = settings.TitleBar(host)
        title.show()
        self.addCleanup(title.close)
        buttons = (title.minimize_btn, title.maximize_btn, title.close_btn)
        for button, kind in zip(buttons, ("minimize", "maximize", "close")):
            self.assertIsInstance(button, WindowControl)
            self.assertEqual(button.kind, kind)
            self.assertEqual(button.text(), "")
            geometry = button.geometry()
            APP.sendEvent(button, QtCore.QEvent(QtCore.QEvent.Enter))
            button._animation.setCurrentTime(120)
            self.assertEqual(button._hover, 1.0)
            self.assertEqual(button.geometry(), geometry)
            APP.sendEvent(button, QtCore.QEvent(QtCore.QEvent.Leave))
            button._animation.setCurrentTime(120)
            self.assertEqual(button._hover, 0.0)
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        minimize.assert_called_once()
        maximize.assert_called_once()
        close.assert_called_once()
        title.close_btn.setEnabled(False)
        QtTest.QTest.mouseClick(title.close_btn, QtCore.Qt.LeftButton)
        close.assert_called_once()

    def test_offscreen_backdrop_keeps_safe_fallback(self):
        if APP.platformName() != "offscreen":
            self.skipTest("Offscreen fallback only")
        self.assertFalse(apply_native_backdrop(QtWidgets.QWidget()))

    def test_tab_roundtrips_do_not_retain_previous_page_pixels(self):
        window = self._window()
        window.setFocus()
        APP.processEvents()
        before = window.grab().toImage()
        for index in (1, 2, 3, 4, 2, 1, 4, 3, 0):
            with mock.patch.object(settings.config, "get_setting", return_value=False):
                window.tab_widget.setCurrentIndex(index)
            APP.processEvents()
            visible = [i for i in range(window.tab_widget.count())
                       if window.tab_widget.widget(i).isVisibleTo(window)]
            self.assertEqual(visible, [index])
        window.setFocus()
        APP.processEvents()
        self.assertEqual(window.grab().toImage(), before)
        self.assertTrue(window.testAttribute(QtCore.Qt.WA_TranslucentBackground))

    def test_flat_glass_tint_does_not_accumulate_on_repaint(self):
        window = self._window()
        window.container.backdrop_active = True
        window.setFocus()
        APP.processEvents()
        before = window.grab().toImage()
        for _ in range(12):
            window.container.repaint()
            window.repaint()
        after = window.grab().toImage()
        self.assertEqual(before, after)
        sample = after.pixelColor(10, 120)
        self.assertGreater(sample.alpha(), 0)
        self.assertLess(sample.alpha(), 128)
        self.assertLess(max(sample.red(), sample.green(), sample.blue()), 40)

    def test_all_lazy_tabs_render_and_preserve_state(self):
        window = self._window()
        self.assertEqual(len(window._lazy_tabs), 4)
        output = os.environ.get("ANIMKEY_TEST_ARTIFACTS")
        for index in range(window.tab_widget.count()):
            with mock.patch.object(settings.config, "get_setting", return_value=False):
                window.tab_widget.setCurrentIndex(index)
            APP.processEvents()
            page = window.tab_widget.currentWidget()
            self.assertGreater(page.width(), 0)
            self.assertLessEqual(page.geometry().right(), window.width())
            if output:
                os.makedirs(output, exist_ok=True)
                self.assertTrue(window.grab().save(os.path.join(output, "settings-{}.png".format(index))))
        self.assertFalse(window._lazy_tabs)
        self.assertGreater(len(window._shortcut_widgets), 20)
        self.assertGreater(len(window._workspace_checkboxes["buttons"]), 20)
        self.assertEqual(window.styleSheet(), STYLESHEET)
        self.config_write.assert_not_called()
        self.shortcut_write.assert_not_called()
        window.tab_widget.setCurrentIndex(0)
        window.resize(640, 560)
        APP.processEvents()
        self.assertEqual(window.width(), 640)
        for button in window._settings_action_buttons:
            self.assertTrue(button.isVisible())
            self.assertGreaterEqual(button.mapTo(window, QtCore.QPoint()).x(), 0)
        if output:
            window.grab().save(os.path.join(output, "settings-compact.png"))


if __name__ == "__main__":
    unittest.main()
