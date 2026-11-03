"""Opening motion stays circular and is a Settings option; Recovery can skip scene copies."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from AnimKey.mods.maya_compat import QtCore, QtWidgets, is_qt_object_valid, PYSIDE_MAJOR
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
if PYSIDE_MAJOR == 6:
    from PySide6 import QtTest
else:
    from PySide2 import QtTest
from AnimKey.buttons import flashbuttons, animCrash
from AnimKey.mods import configMod
from AnimKey.core.window_reveal import (
    apply_window_reveal, opening_animations_enabled, set_opening_animations_enabled,
)


class FlashOpeningPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        for patch in (
            mock.patch.object(configMod, "get_user_folder_path", return_value=self.folder.name),
            mock.patch.object(configMod.ConfigManager, "_instance", None),
            mock.patch.object(flashbuttons.FlashButtonsPanel, "get_config_path",
                              return_value=str(Path(self.folder.name)/"flash.json")),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.widgets = []
        self.panel = self.keep(flashbuttons.FlashButtonsPanel())
        self.panel.add_button_at_position(100, 120, "Second")
        self.positions = {key: QtCore.QPoint(value) for key, value in self.panel.button_positions.items()}

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def tearDown(self):
        for widget in self.widgets:
            if is_qt_object_valid(widget):
                widget.close()
                widget.deleteLater()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        APP.processEvents()

    def assert_settled(self):
        for button in self.panel.buttons:
            self.assertEqual(button.pos(), self.positions[button.button_id])
            self.assertEqual(button.size(), QtCore.QSize(self.panel.button_size, self.panel.button_size))
            self.assertFalse(button._panel_animation_timer.isActive())
            self.assertEqual(button._panel_animation.state(), QtCore.QAbstractAnimation.Stopped)

    def test_hover_during_opening_does_not_resize_or_reposition_buttons(self):
        self.panel.show_panel()
        APP.processEvents()
        for button in self.panel.buttons:
            button._panel_animation_timer.stop()
            button._panel_animation.start()
            for time in (0, 40, 80, 120, 180):
                button._panel_animation.setCurrentTime(time)
                position = button.pos()
                button.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
                button._hover_animation.setCurrentTime(60)
                self.assertEqual(button.pos(), position)
                self.assertEqual(button.size(), QtCore.QSize(55, 55))
                button.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
                button._hover_animation.setCurrentTime(120)
                self.assertEqual(button.pos(), position)
                self.assertEqual(button.size(), QtCore.QSize(55, 55))
            self.assertEqual(button.pos(), self.positions[button.button_id])
        self.assertEqual(self.panel.button_positions, self.positions)

    def test_disabled_opening_is_immediate_with_full_size_buttons_and_opacity(self):
        set_opening_animations_enabled(False)
        self.panel.show_panel()
        APP.processEvents()
        self.assert_settled()
        self.assertEqual(self.panel.windowOpacity(), 1.0)
        self.assertFalse(self.panel._window_reveal.running)
        self.assertFalse(self.panel._window_reveal.pending)
        self.assertEqual(self.panel.fade_animation.state(), QtCore.QAbstractAnimation.Stopped)

    def test_disabling_during_an_entrance_cancels_delayed_motion_and_settles_positions(self):
        self.panel.show_panel()
        APP.processEvents()
        self.assertTrue(self.panel._window_reveal.running)
        set_opening_animations_enabled(False)
        self.assert_settled()
        self.assertFalse(self.panel._window_reveal.running)
        self.assertEqual(self.panel.windowOpacity(), 1.0)
        APP.processEvents()
        self.assert_settled()

    def test_shared_window_reveal_reads_the_option_on_every_open(self):
        window = self.keep(QtWidgets.QWidget(None, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint))
        window.setGeometry(200, 200, 220, 180)
        reveal = apply_window_reveal(window)
        target = window.pos()
        set_opening_animations_enabled(False)
        window.show()
        APP.processEvents()
        self.assertEqual(window.pos(), target)
        self.assertFalse(reveal.running)
        window.hide()
        set_opening_animations_enabled(True)
        window.show()
        APP.processEvents()
        self.assertTrue(reveal.running)
        reveal._animation.setCurrentTime(180)
        self.assertEqual(window.pos(), target)

    def test_settings_switch_saves_the_choice_and_updates_existing_flash_panel(self):
        from AnimKey.core import settings
        with mock.patch.object(settings.ui, "get_maya_main_window", return_value=None):
            window = self.keep(settings.SettingsWindow())
        window.show()
        APP.processEvents()
        switch = window.opening_animations_switch
        self.assertTrue(switch.isChecked())
        self.panel.show_panel()
        QtTest.QTest.mouseClick(switch, QtCore.Qt.LeftButton)
        self.assertFalse(opening_animations_enabled())
        self.assert_settled()
        stored = json.loads(Path(configMod.get_preferences_path()).read_text())
        self.assertIs(stored["ui_opening_animations_enabled"], False)
        configMod.get_config()._load_config()
        self.assertFalse(opening_animations_enabled())
        QtTest.QTest.mouseClick(switch, QtCore.Qt.LeftButton)
        self.assertTrue(opening_animations_enabled())

    def _recovery_window(self):
        with mock.patch.object(animCrash.RecoveryWindow, "_refresh"), \
                mock.patch.object(animCrash, "get_scene_folder", return_value=self.folder.name):
            owner = self.keep(QtWidgets.QWidget())
            recovery = self.keep(animCrash.RecoveryWindow(parent=owner))
            recovery.show()
            APP.processEvents()
        return recovery

    def _drain_recovery_timer(self):
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(120, loop.quit)
        (getattr(loop, 'exec', None) or loop.exec_)()

    def test_recovery_no_longer_owns_opening_animations(self):
        recovery = self._recovery_window()
        self.assertFalse(hasattr(recovery, "opening_animations_switch"))
        self._drain_recovery_timer()

    def test_save_scenes_switch_selects_animation_only_recovery(self):
        recovery = self._recovery_window()
        switch = recovery.save_scenes_switch
        self.assertTrue(switch.isChecked())
        self.assertGreater(animCrash.Config.scene_snapshot_interval(), 0.0)
        QtTest.QTest.mouseClick(switch, QtCore.Qt.LeftButton)
        self.assertFalse(animCrash.Config.scene_snapshots_enabled())
        self.assertEqual(animCrash.Config.scene_snapshot_interval(), 0.0)
        self.assertFalse(recovery.scene_snapshot_combo.isEnabled())
        self.assertEqual(recovery.save_mode_label.text(), "Animation only")
        stored = json.loads(Path(configMod.get_preferences_path()).read_text())
        self.assertIs(stored["crash_recovery_save_scenes"], False)
        # An automatic tick must never write a scene file while off.
        animCrash.RecoverySystem._scene_snapshot_dirty = True
        with mock.patch.object(animCrash, "save_scene_snapshot") as save:
            self.assertIsNone(animCrash.RecoverySystem._save_scene_snapshot_if_due())
        save.assert_not_called()
        QtTest.QTest.mouseClick(switch, QtCore.Qt.LeftButton)
        self.assertTrue(animCrash.Config.scene_snapshots_enabled())
        self.assertTrue(recovery.scene_snapshot_combo.isEnabled())
        self._drain_recovery_timer()

    def test_legacy_scene_off_interval_migrates_to_animation_only(self):
        configMod.get_config().set("crash_recovery_scene_snapshot_minutes", 0)
        self.assertFalse(animCrash.Config.scene_snapshots_enabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
