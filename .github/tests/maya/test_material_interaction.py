"""Proximity fading and backdrop frames while the tool is still moving."""
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets, shiboken, is_qt_object_valid
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from AnimKey.core.window_style import apply_window_style
from AnimKey.core import window_style


def wait(milliseconds):
    loop = QtCore.QEventLoop()
    timer = QtCore.QTimer(loop)
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(milliseconds)
    (getattr(loop, 'exec', None) or loop.exec_)()


class Gradient(QtWidgets.QWidget):
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        gradient = QtGui.QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0, QtGui.QColor('#ff5030'))
        gradient.setColorAt(1, QtGui.QColor('#3050ff'))
        painter.fillRect(self.rect(), gradient)


class MaterialInteractionTests(unittest.TestCase):
    def setUp(self):
        self.owner = Gradient()
        self.owner.setGeometry(50, 50, 720, 480)
        self.owner.show()
        self.window = QtWidgets.QWidget(self.owner, QtCore.Qt.Tool)
        self.window.setGeometry(100, 100, 220, 180)
        self.cursor = mock.patch.object(QtGui.QCursor, 'pos', return_value=QtCore.QPoint(5000, 5000)).start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        self.owner.deleteLater()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        APP.processEvents()

    def show(self, **kwargs):
        surface = apply_window_style(self.window, **kwargs)
        self.window.show()
        APP.processEvents()
        APP.processEvents()
        return surface

    def finish_fade(self, surface):
        surface._opacity_animation.setCurrentTime(surface._opacity_animation.duration())

    def test_window_fades_away_and_recovers_before_the_cursor_enters(self):
        surface = self.show()
        self.assertAlmostEqual(self.window.windowOpacity(), .5, delta=.01)
        self.cursor.return_value = self.window.frameGeometry().topLeft() - QtCore.QPoint(15, 15)
        surface._update_proximity()
        self.finish_fade(surface)
        self.assertAlmostEqual(self.window.windowOpacity(), 1.0, delta=.01)
        self.cursor.return_value = QtCore.QPoint(5000, 5000)
        surface._update_proximity()
        self.finish_fade(surface)
        self.assertAlmostEqual(self.window.windowOpacity(), .5, delta=.01)

    def test_legacy_enter_leave_handlers_cannot_override_the_shared_fade(self):
        class LegacyWindow(QtWidgets.QWidget):
            def leaveEvent(self, event):
                self.setWindowOpacity(1.0)
        self.window.deleteLater()
        self.window = LegacyWindow(self.owner, QtCore.Qt.Tool)
        surface = self.show()
        APP.sendEvent(self.window, QtCore.QEvent(QtCore.QEvent.Leave))
        self.finish_fade(surface)
        self.assertAlmostEqual(self.window.windowOpacity(), .5, delta=.01)

    def test_owned_dropdown_and_mouse_grab_keep_the_tool_readable(self):
        surface = self.show()
        popup = QtWidgets.QMenu(self.window)
        with mock.patch.object(QtWidgets.QApplication, 'activePopupWidget', return_value=popup):
            surface._update_proximity()
            self.finish_fade(surface)
            self.assertAlmostEqual(self.window.windowOpacity(), 1.0, delta=.01)
        button = QtWidgets.QPushButton('Drag', self.window)
        with mock.patch.object(QtWidgets.QWidget, 'mouseGrabber', return_value=button):
            surface._update_proximity()
            self.assertEqual(surface._opacity_target, 1.0)

    def test_modal_dialogs_and_toolbar_opt_out_stay_fully_visible(self):
        self.window.deleteLater()
        self.window = QtWidgets.QDialog(self.owner)
        self.window.setModal(True)
        surface = self.show()
        self.assertAlmostEqual(self.window.windowOpacity(), 1.0)
        self.assertFalse(surface._proximity_timer.isActive())
        self.window.close()
        self.window = QtWidgets.QWidget(self.owner, QtCore.Qt.Tool)
        surface = self.show(auto_fade=False)
        self.assertAlmostEqual(self.window.windowOpacity(), 1.0)
        self.assertFalse(surface._proximity_timer.isActive())

    def test_backdrop_updates_during_continuous_movement_not_only_after_release(self):
        surface = self.show()
        before = surface._capture_count
        frames = surface._reproject_count
        original = surface._backdrop_image.pixelColor(110, 80)
        real_capture = window_style.capture_application_backdrop
        def slow_capture(window):
            time.sleep(.1)  # Simulate a slow GPU without requiring one in CI.
            return real_capture(window)
        durations = []
        with mock.patch.object(window_style, 'capture_application_backdrop', side_effect=slow_capture) as capture:
            for step in range(50):
                started = time.perf_counter()
                self.window.move(100 + step * 4, 100)
                durations.append((time.perf_counter() - started) * 1000)
                wait(8)
            capture.assert_not_called()
            self.assertEqual(surface._capture_count, before)
            self.assertGreaterEqual(surface._reproject_count - frames, 45)
            self.assertLess(sorted(durations)[int(len(durations) * .95)], 50)
            current = surface._backdrop_image.pixelColor(110, 80)
            self.assertGreater(abs(original.red() - current.red()), 25)
            wait(surface.CAPTURE_SETTLE_MS + 140)
            self.assertEqual(capture.call_count, 1)
        self.assertEqual(surface._capture_count, before + 1)

    def test_paused_drag_waits_for_mouse_release_before_reading_the_gpu(self):
        surface = self.show()
        before = surface._capture_count
        with mock.patch.object(QtWidgets.QApplication, 'mouseButtons', return_value=QtCore.Qt.LeftButton):
            self.window.move(200, 120)
            wait(surface.CAPTURE_SETTLE_MS * 2 + 50)
            self.assertEqual(surface._capture_count, before)
            self.assertTrue(surface._capture_timer.isActive())
        wait(surface.CAPTURE_SETTLE_MS + 50)
        self.assertEqual(surface._capture_count, before + 1)

    def test_resize_and_moving_outside_owner_only_sample_existing_cache(self):
        surface = self.show()
        before = surface._capture_count
        self.window.resize(260, 190)
        self.window.move(0, 0)
        self.assertEqual(surface._backdrop_image.size(), self.window.size())
        self.assertEqual(surface._backdrop_image.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(surface._capture_count, before)
        self.assertGreater(surface._backdrop_image.pixelColor(150, 100).alpha(), 0)

    def test_owner_changes_are_captured_once_after_movement_settles(self):
        surface = self.show()
        before = surface._capture_count
        original = surface._backdrop_image.pixelColor(110, 80)
        viewport = QtGui.QImage(20, 20, QtGui.QImage.Format_ARGB32)
        viewport.fill(QtGui.QColor('#20e030'))
        from AnimKey.core import material_backdrop
        with mock.patch.object(material_backdrop, '_maya_viewport_images',
                               side_effect=lambda *args: iter([(self.owner.rect(), viewport)])):
            self.window.move(120, 100)
            self.assertEqual(surface._capture_count, before)
            wait(surface.CAPTURE_SETTLE_MS + 80)
        current = surface._backdrop_image.pixelColor(110, 80)
        self.assertGreater(current.green() - original.green(), 100)
        self.assertEqual(surface._capture_count, before + 1)

    def test_idle_window_does_not_continuously_read_the_viewport(self):
        surface = self.show()
        wait(60)
        count = surface._capture_count
        wait(150)
        self.assertEqual(surface._capture_count, count)

    def test_hide_stops_work_and_reopen_recalculates_proximity(self):
        surface = self.show()
        self.window.move(105, 105)
        self.window.hide()
        self.assertFalse(surface._capture_timer.isActive())
        self.assertFalse(surface._proximity_timer.isActive())
        self.assertIsNone(surface._backdrop_cache)
        self.cursor.return_value = self.window.frameGeometry().center()
        self.window.show()
        APP.processEvents()
        self.assertAlmostEqual(self.window.windowOpacity(), 1.0)

    def test_deletion_cancels_pending_capture_poll_and_fade(self):
        surface = self.show()
        self.window.move(110, 110)
        objects = (surface._capture_timer, surface._proximity_timer, surface._opacity_animation)
        shiboken.delete(self.window)
        self.assertTrue(all(not is_qt_object_valid(obj) for obj in objects))
        APP.processEvents()


if __name__ == '__main__':
    unittest.main(verbosity=2)
