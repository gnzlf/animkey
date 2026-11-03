"""Circular Flash backdrop and cancellation of delayed entrance animations."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from AnimKey.mods.maya_compat import QtCore, QtWidgets, shiboken, is_qt_object_valid
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
from AnimKey.buttons import flashbuttons


class FlashMaterialTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.config = mock.patch.object(flashbuttons.FlashButtonsPanel, 'get_config_path',
                                        return_value=str(Path(self.folder.name)/'config.json'))
        self.config.start()
        self.window = flashbuttons.FlashButtonsPanel()

    def tearDown(self):
        if is_qt_object_valid(self.window):
            self.window.close()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        APP.processEvents()
        self.config.stop()
        self.folder.cleanup()

    def test_background_is_radial_and_has_no_rectangular_corners(self):
        self.window.show()
        self.window.setWindowOpacity(1)
        APP.processEvents()
        image = self.window.grab().toImage()
        for x, y in ((1, 1), (498, 1), (1, 498), (498, 498)):
            self.assertEqual(image.pixelColor(x, y).alpha(), 0)
        center = image.pixelColor(300, 250).alpha()
        middle = image.pixelColor(430, 250).alpha()
        edge = image.pixelColor(490, 250).alpha()
        self.assertGreater(center, middle)
        self.assertGreater(middle, edge)
        self.assertGreater(center, 180)
        self.assertLess(edge, 30)

    def test_hiding_stops_fade_motion_and_staggered_button_timers(self):
        self.window.add_button_at_position(90, 120, 'Second')
        self.window.show_panel()
        APP.processEvents()
        self.assertTrue(self.window._window_reveal.running)
        self.window.hide_panel()
        self.assertFalse(self.window._window_reveal.running)
        self.assertFalse(self.window.hide_timer.isActive())
        self.assertEqual(self.window.fade_animation.state(), QtCore.QAbstractAnimation.Stopped)
        for button in self.window.buttons:
            self.assertFalse(button._panel_animation_timer.isActive())
            self.assertEqual(button._panel_animation.state(), QtCore.QAbstractAnimation.Stopped)
        self.assertEqual(self.window.windowOpacity(), 0)

    def test_reopening_keeps_saved_button_positions(self):
        self.window.add_button_at_position(70, 120, 'Second')
        positions = {bid: QtCore.QPoint(pos) for bid, pos in self.window.button_positions.items()}
        for _ in range(3):
            self.window.show_panel()
            APP.processEvents()
            self.window._window_reveal._animation.setCurrentTime(200)
            for button in self.window.buttons:
                button._panel_animation_timer.stop()
                button._panel_animation.start()
                button._panel_animation.setCurrentTime(300)
                self.assertEqual(button.pos(), positions[button.button_id])
                self.assertEqual(button.width(), button.height())
            self.window.hide_panel()
        self.assertEqual(self.window.button_positions, positions)

    def test_deleting_panel_cancels_all_owned_animation_objects(self):
        self.window.show_panel()
        button = self.window.buttons[0]
        objects = (self.window.fade_animation, self.window._window_reveal,
                   button._panel_animation, button._panel_animation_timer)
        shiboken.delete(self.window)
        self.assertTrue(all(not is_qt_object_valid(obj) for obj in objects))
        APP.processEvents()

    def test_hiding_after_add_cancels_the_pending_button_editor(self):
        with mock.patch.object(self.window, 'save_config'), \
                mock.patch.object(self.window, 'edit_button') as editor:
            self.window.show_panel()
            self.window.add_new_button()
            button = self.window.buttons[-1]
            self.assertTrue(button._edit_timer.isActive())
            self.window.hide_panel()
            self.assertFalse(button._edit_timer.isActive())
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(400, loop.quit)
            (getattr(loop, 'exec', None) or loop.exec_)()
            editor.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
