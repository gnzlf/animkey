import unittest

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.buttons.brush import (
    DrawingCanvas,
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPoint,
    QPointF,
    QtCore,
    QtWidgets,
    Qt,
)


class _TabletEvent:
    """Small Qt-tablet stand-in for input routing regression tests."""

    def __init__(self, event_type, position, pressure):
        self._event_type = event_type
        self._position = QPointF(position)
        self._pressure = pressure

    def type(self):
        return self._event_type

    def position(self):
        return QPointF(self._position)

    def pressure(self):
        return self._pressure

    def tiltX(self):
        return 0.0

    def tiltY(self):
        return 0.0

    def rotation(self):
        return 0.0

    def modifiers(self):
        return Qt.NoModifier

    def button(self):
        return Qt.LeftButton


class BrushRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance()
        if cls.qt_app is None:
            cls.qt_app = QtWidgets.QApplication([])

    @staticmethod
    def _render_outline(points, opacity=128):
        image = QImage(101, 101, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, opacity)))
        painter.drawPath(
            DrawingCanvas._clean_stroke_outline(None, points, 12.0)
        )
        painter.end()
        return image

    def test_self_intersection_has_no_transparent_hole(self):
        image = self._render_outline([
            (20.0, 20.0, 1.0),
            (80.0, 80.0, 1.0),
            (20.0, 80.0, 1.0),
            (80.0, 20.0, 1.0),
        ])

        self.assertEqual(image.pixelColor(50, 50).alpha(), 128)

    def test_intersection_does_not_accumulate_opacity(self):
        image = self._render_outline([
            (20.0, 20.0, 0.35),
            (80.0, 80.0, 1.0),
            (20.0, 80.0, 0.65),
            (80.0, 20.0, 0.35),
        ])

        crossing_alpha = image.pixelColor(50, 50).alpha()
        solid_segment_alpha = image.pixelColor(30, 30).alpha()
        self.assertEqual(crossing_alpha, 128)
        self.assertEqual(solid_segment_alpha, 128)

    def test_tablet_pressure_is_saved_as_variable_stroke_width(self):
        canvas = DrawingCanvas()
        canvas.resize(240, 120)
        canvas._ensure_pixmap_size()

        events = (
            _TabletEvent(QtCore.QEvent.TabletPress, QPointF(20, 30), 0.14),
            _TabletEvent(QtCore.QEvent.TabletMove, QPointF(80, 30), 0.52),
            _TabletEvent(QtCore.QEvent.TabletMove, QPointF(150, 30), 0.94),
            _TabletEvent(QtCore.QEvent.TabletRelease, QPointF(210, 30), 0.06),
        )
        for event in events:
            self.assertTrue(canvas._handle_tablet_event(event, event.position()))

        points = canvas.data.frames[canvas.current_frame].strokes[0].points
        pressures = [point.pressure for point in points]
        self.assertGreater(max(pressures) - min(pressures), 0.25)
        self.assertLess(pressures[0], pressures[-2])
        self.assertLess(pressures[-1], pressures[-2])

    def test_external_tablet_events_convert_global_position_to_canvas(self):
        canvas = DrawingCanvas()
        canvas.resize(240, 120)
        canvas.show()
        self.qt_app.processEvents()

        local = QPoint(36, 48)
        global_pos = canvas.mapToGlobal(local)
        event = _TabletEvent(QtCore.QEvent.TabletPress, QPointF(0, 0), 0.40)
        self.assertTrue(
            canvas.handle_external_tablet_event(event, QPointF(global_pos))
        )
        first_point = canvas.current_points[0]
        x, y = canvas._point_to_pixel(
            first_point,
            canvas._drawing_view_transform,
            canvas._drawing_uniform_transform,
        )
        self.assertAlmostEqual(x, local.x(), delta=1.0)
        self.assertAlmostEqual(y, local.y(), delta=1.0)
        canvas._end_stroke()
        canvas.close()

    def test_strokes_follow_the_full_viewport_when_aspect_ratio_changes(self):
        canvas = DrawingCanvas()
        canvas.resize(100, 100)
        canvas._ensure_pixmap_size()
        canvas._begin_stroke(QPointF(20, 40), 1.0)
        canvas._add_point(QPointF(80, 40), 1.0)
        canvas._end_stroke()

        stroke = canvas.data.frames[canvas.current_frame].strokes[0]
        canvas.resize(200, 100)
        canvas._ensure_pixmap_size()

        x, y = canvas._point_to_pixel(stroke.points[0])
        self.assertAlmostEqual(x, 40.0, delta=0.1)
        self.assertAlmostEqual(y, 40.0, delta=0.1)
        self.assertAlmostEqual(
            canvas._viewport_brush_scale(), 2.0 ** 0.5, delta=0.001
        )


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(not result.result.wasSuccessful())
