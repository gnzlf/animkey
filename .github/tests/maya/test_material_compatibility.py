"""Portable glass, inherited backgrounds and window-shape regressions."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets, shiboken, is_qt_object_valid
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
from AnimKey.core import material_backdrop, window_style, settings_style
from AnimKey.mods import uiMod


class Stripes(QtWidgets.QWidget):
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        for x in range(0, self.width(), 8):
            painter.fillRect(x, 0, 8, self.height(),
                             QtGui.QColor('white' if (x // 8) % 2 else 'black'))


class MaterialCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.owner = Stripes()
        self.owner.setGeometry(50, 50, 500, 400)
        self.owner.show()
        self.window = QtWidgets.QWidget(self.owner, QtCore.Qt.Tool)
        self.window.setGeometry(100, 100, 240, 180)
        self.patch = mock.patch.object(window_style, 'apply_native_backdrop', return_value=False)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.owner.deleteLater()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        APP.processEvents()

    def show(self, **options):
        surface = window_style.apply_window_style(self.window, **options)
        self.window.show()
        APP.processEvents()
        APP.processEvents()
        return surface

    def test_unsupported_native_blur_still_gets_a_blurred_application_backdrop(self):
        surface = self.show()
        self.assertEqual(self.window.property('materialBackdropMode'), 'portable')
        image = surface._backdrop_image
        self.assertIsNotNone(image)
        values = [image.pixelColor(x, image.height() // 2).red() for x in range(30, image.width() - 30)]
        self.assertLess(max(values) - min(values), 80)

    def test_blurred_body_has_real_alpha_and_no_rectangular_canvas(self):
        self.show()
        image = self.window.grab().toImage()
        alpha = image.pixelColor(image.width() // 2, image.height() // 2).alpha()
        self.assertGreater(alpha, 150)
        self.assertLess(alpha, 255)
        for point in ((0, 0), (image.width() - 1, 0), (0, image.height() - 1),
                      (image.width() - 1, image.height() - 1)):
            self.assertEqual(image.pixelColor(*point).alpha(), 0)

    def test_gpu_viewport_image_is_composited_before_blurring(self):
        viewport = QtGui.QImage(20, 20, QtGui.QImage.Format_ARGB32)
        viewport.fill(QtGui.QColor('#d62f36'))
        area = QtCore.QRect(0, 0, self.owner.width(), self.owner.height())
        with mock.patch.object(material_backdrop, '_maya_viewport_images',
                               return_value=iter([(area, viewport)])):
            surface = self.show()
        color = surface._backdrop_image.pixelColor(100, 80)
        self.assertGreater(color.red(), 180)
        self.assertLess(color.green(), 80)

    def test_opengl_content_is_not_required_for_plain_qt_windows(self):
        self.assertEqual(list(material_backdrop._maya_viewport_images(self.owner, self.owner.rect())), [])

    def test_rounded_mask_survives_detaching_before_the_next_paint(self):
        with mock.patch.object(uiMod, 'close_animkey_tool_windows'):
            popup = uiMod.ContextPopupWindow(parent=self.owner)
        popup.show()
        APP.processEvents()
        self.assertEqual(popup._tail_height, 0)
        self.assertTrue(popup.mask().contains(QtCore.QPoint(40, 2)))
        popup.detach_from_anchor()
        self.assertFalse(popup.mask().contains(QtCore.QPoint(0, 0)))
        self.assertTrue(popup.mask().contains(QtCore.QPoint(40, 2)))

    def test_inherited_square_frame_does_not_show_behind_the_rounded_panel(self):
        self.window.setStyleSheet(settings_style.STYLESHEET + ' QFrame { background: red; }')
        panel = settings_style.GlassPanel(self.window)
        window_style.polish_tool_widgets(panel)
        layout = QtWidgets.QVBoxLayout(self.window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)
        self.show(polish=False, paint=False, panel=panel)
        image = panel.grab().toImage()
        for x, y in ((1, 1), (image.width() - 2, 1), (1, image.height() - 2),
                     (image.width() - 2, image.height() - 2)):
            self.assertEqual(image.pixelColor(x, y).alpha(), 0)

    def test_legacy_pointer_fields_do_not_turn_the_body_into_a_pointed_bubble(self):
        self.window._tail_height = 10
        self.window._tail_on_top = True
        self.show()
        self.assertTrue(self.window.mask().contains(QtCore.QPoint(40, 2)))
        self.assertFalse(self.window.mask().contains(QtCore.QPoint(0, 0)))

    def test_closing_cancels_pending_fallback_capture(self):
        surface = window_style.apply_window_style(self.window)
        self.window.show()
        timer = surface._capture_timer
        shiboken.delete(self.window)
        self.assertFalse(is_qt_object_valid(timer))
        APP.processEvents()

    def test_no_parent_still_has_translucency_when_native_blur_is_missing(self):
        fill = settings_style.surface_fill(False)
        self.assertGreater(fill.alpha(), 100)
        self.assertLess(fill.alpha(), 220)


if __name__ == '__main__':
    unittest.main(verbosity=2)
