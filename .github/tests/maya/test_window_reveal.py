"""Opening motion preserves popup anchors and never performs per-frame captures."""
import unittest
import time
from unittest import mock

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets, shiboken, is_qt_object_valid
from AnimKey.core.window_style import apply_window_style
from AnimKey.core import window_style
from AnimKey.core.window_reveal import apply_window_reveal

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class WindowRevealTests(unittest.TestCase):
    def setUp(self):
        self.owner = QtWidgets.QWidget()
        self.owner.setGeometry(20, 20, 800, 600)
        self.owner.show()
        self.window = QtWidgets.QWidget(self.owner, QtCore.Qt.Tool)
        self.window.setGeometry(180, 160, 220, 180)

    def tearDown(self):
        self.owner.deleteLater()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        APP.processEvents()

    def show(self, **options):
        surface = apply_window_style(self.window, **options)
        self.window.show()
        APP.processEvents()
        APP.processEvents()
        return surface, surface.reveal

    def test_entrance_moves_up_and_finishes_at_the_requested_anchor(self):
        surface = apply_window_style(self.window)
        self.window.show()
        target = QtCore.QPoint(220, 200)
        self.window.move(target)  # Real tools often place themselves after show().
        APP.processEvents()
        reveal = surface.reveal
        self.assertTrue(reveal.running)
        self.assertEqual(self.window.pos(), target + QtCore.QPoint(0, reveal.DISTANCE))
        reveal._animation.setCurrentTime(100)
        self.assertGreater(self.window.y(), target.y())
        self.assertLess(self.window.y(), target.y() + reveal.DISTANCE)
        reveal._animation.setCurrentTime(reveal.DURATION_MS)
        self.assertEqual(self.window.pos(), target)
        self.assertFalse(reveal.running)

    def test_slow_capture_waits_until_entrance_finishes_and_preserves_opacity(self):
        real_capture = window_style.capture_application_backdrop
        def slow_capture(window):
            self.assertFalse(window._window_reveal.running)
            self.assertFalse(window._window_reveal.pending)
            time.sleep(.08)
            return real_capture(window)
        with mock.patch.object(window_style, 'capture_application_backdrop', side_effect=slow_capture) as capture:
            surface, reveal = self.show()
            opacity = self.window.windowOpacity()
            self.assertEqual(surface._capture_count, 0)
            for elapsed in (30, 60, 90, 120, 150, reveal.DURATION_MS):
                reveal._animation.setCurrentTime(elapsed)
                APP.processEvents()
            capture.assert_not_called()
            self.assertEqual(self.window.windowOpacity(), opacity)
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(surface.CAPTURE_SETTLE_MS + 160, loop.quit)
            (getattr(loop, 'exec', None) or loop.exec_)()
            self.assertEqual(capture.call_count, 1)
            self.assertIsNotNone(surface._backdrop_image)

    def test_first_paint_is_offset_and_every_visible_step_moves_up_without_rebound(self):
        target = self.window.pos()
        positions = []
        class PaintRecorder(QtCore.QObject):
            def eventFilter(self, watched, event):
                if event.type() == QtCore.QEvent.Paint:
                    positions.append(watched.y())
                return False
        surface = apply_window_style(self.window)
        recorder = PaintRecorder(self.window)
        self.window.installEventFilter(recorder)
        self.window.show()
        APP.processEvents()
        self.assertTrue(positions)
        self.assertEqual(positions[0], target.y() + surface.reveal.DISTANCE)
        for elapsed in range(15, surface.reveal.DURATION_MS + 1, 15):
            surface.reveal._animation.setCurrentTime(elapsed)
            self.window.repaint()
        self.assertEqual(positions[-1], target.y())
        self.assertEqual(positions, sorted(positions, reverse=True))

    def test_native_move_echo_does_not_cancel_the_entrance(self):
        _, reveal = self.show()
        reveal._animation.setCurrentTime(60)
        current = self.window.pos()
        APP.sendEvent(self.window, QtGui.QMoveEvent(current, current + QtCore.QPoint(0, 1)))
        self.assertTrue(reveal.running)
        reveal._animation.setCurrentTime(reveal.DURATION_MS)
        self.assertEqual(self.window.pos(), reveal._target)

    def test_drag_interrupts_entrance_without_snapping_back(self):
        _, reveal = self.show()
        reveal._animation.setCurrentTime(70)
        dragged = QtCore.QPoint(300, 280)
        self.window.move(dragged)
        self.assertFalse(reveal.running)
        APP.processEvents()
        self.assertEqual(self.window.pos(), dragged)

    def test_hide_mid_animation_restores_anchor_and_reopen_does_not_drift(self):
        target = self.window.pos()
        _, reveal = self.show()
        reveal._animation.setCurrentTime(90)
        self.window.hide()
        self.assertFalse(reveal.running)
        self.assertEqual(self.window.pos(), target)
        self.window.show()
        APP.processEvents()
        reveal._animation.setCurrentTime(reveal.DURATION_MS)
        self.assertEqual(self.window.pos(), target)

    def test_deletion_cancels_owned_reveal_timer_and_animation(self):
        reveal = apply_window_reveal(self.window)
        self.window.show()
        owned = reveal, reveal._timer, reveal._animation
        shiboken.delete(self.window)
        self.assertTrue(all(not is_qt_object_valid(obj) for obj in owned))
        APP.processEvents()

    def test_toolbar_can_opt_out_and_maximized_windows_do_not_slide(self):
        target = self.window.pos()
        _, reveal = self.show(animate_open=False, auto_fade=False)
        self.assertFalse(reveal.running)
        self.assertEqual(self.window.pos(), target)
        self.window.hide()
        reveal.enabled = True
        self.window.showMaximized()
        APP.processEvents()
        self.assertFalse(reveal.running)


if __name__ == '__main__':
    unittest.main(verbosity=2)
