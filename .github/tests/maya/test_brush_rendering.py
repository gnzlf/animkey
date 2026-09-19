import unittest
from unittest import mock
import math

import maya.standalone
from AnimKey.mods.maya_compat import QtWidgets

_QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

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
    Stroke,
    StrokePoint,
    SketchboardData,
    get_active_model_panel,
)
import maya.cmds as cmds
import maya.api.OpenMaya as om2


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

    def test_strokes_keep_proportions_when_aspect_ratio_changes_without_camera(self):
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
        self.assertAlmostEqual(x, 70.0, delta=0.1)
        self.assertAlmostEqual(y, 40.0, delta=0.1)
        self.assertAlmostEqual(
            canvas._viewport_brush_scale(), 1.0, delta=0.001
        )


class BrushViewportMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.camera, self.shape = cmds.camera()
        self.canvas = DrawingCanvas()
        self.canvas.resize(800, 600)
        self.canvas.data.canvas_width = 800
        self.canvas.data.canvas_height = 600
        self.camera_patch = mock.patch.object(self.canvas, '_camera_shape_from_panel', return_value=self.shape)
        self.camera_patch.start()
        self.identity = (1.0, 1.0, 0.0, 0.0)

    def tearDown(self):
        self.camera_patch.stop()
        self.canvas.close()
        self.canvas.deleteLater()
        cmds.delete(self.camera)

    def test_horizontal_film_fit_matches_camera_scale_and_center(self):
        cmds.setAttr(self.shape + '.filmFit', 1)
        self.canvas.resize(1600, 600)
        self.assertEqual(self.canvas._to_pixel(0.25, 0.25, self.identity), (400.0, 0.0))
        self.assertAlmostEqual(self.canvas._viewport_brush_scale(), 2.0)

    def test_vertical_film_fit_expands_sides_without_stretching(self):
        cmds.setAttr(self.shape + '.filmFit', 2)
        self.canvas.resize(1600, 600)
        x, y = self.canvas._to_pixel(0.25, 0.25, self.identity)
        self.assertAlmostEqual(x, 600.0)
        self.assertAlmostEqual(y, 150.0)
        self.assertAlmostEqual(self.canvas._viewport_brush_scale(), 1.0)

    def test_resize_matches_maya_camera_field_of_view(self):
        selection = om2.MSelectionList()
        selection.add(self.shape)
        camera = om2.MFnCamera(selection.getDagPath(0))
        for film_fit in range(4):
            cmds.setAttr(self.shape + '.filmFit', film_fit)
            h0, v0 = camera.getPortFieldOfView(800, 600)
            ray_x = (.32 - .5) * 2 * math.tan(h0 / 2)
            ray_y = (.5 - .64) * 2 * math.tan(v0 / 2)
            for width, height in ((400, 900), (1600, 900), (800, 600)):
                self.canvas.resize(width, height)
                hfov, vfov = camera.getPortFieldOfView(width, height)
                expected_x = width * (.5 + ray_x / (2 * math.tan(hfov / 2)))
                expected_y = height * (.5 - ray_y / (2 * math.tan(vfov / 2)))
                x, y = self.canvas._to_pixel(.32, .64, self.identity)
                self.assertAlmostEqual(x, expected_x, places=5)
                self.assertAlmostEqual(y, expected_y, places=5)

    def test_circle_stays_round_through_split_maximize_and_restore(self):
        reference = [(400 + 80 * math.cos(i * math.pi / 8), 300 + 80 * math.sin(i * math.pi / 8)) for i in range(16)]
        points = [(x / 800, y / 600) for x, y in reference]
        for orthographic in (False, True):
            cmds.setAttr(self.shape + '.orthographic', orthographic)
            for film_fit in range(4):
                cmds.setAttr(self.shape + '.filmFit', film_fit)
                for width, height in ((400, 900), (1600, 900), (800, 600), (1200, 400)):
                    with self.subTest(ortho=orthographic, fit=film_fit, size=(width, height)):
                        self.canvas.resize(width, height)
                        uniform = self.canvas._uniform_transform()
                        pixels = [self.canvas._to_pixel(x, y, self.identity, uniform) for x, y in points]
                        span_x = max(p[0] for p in pixels) - min(p[0] for p in pixels)
                        span_y = max(p[1] for p in pixels) - min(p[1] for p in pixels)
                        self.assertAlmostEqual(span_x, span_y, places=5)
                        self.assertAlmostEqual(span_x / 160.0, self.canvas._viewport_brush_scale())
                        for original, pixel in zip(points, pixels):
                            normalized = self.canvas._to_normalized(*pixel, self.identity, uniform)
                            self.assertAlmostEqual(normalized[0], original[0], places=7)
                            self.assertAlmostEqual(normalized[1], original[1], places=7)

    def test_panzoom_and_export_use_same_mapping_after_resize(self):
        cmds.setAttr(self.shape + '.filmFit', 2)
        cmds.setAttr(self.shape + '.panZoomEnabled', True)
        cmds.setAttr(self.shape + '.zoom', 0.8)
        cmds.setAttr(self.shape + '.horizontalPan', 0.025)
        cmds.setAttr(self.shape + '.verticalPan', -0.01)
        self.canvas.data.get_or_create_frame(1).strokes = [Stroke(
            points=[StrokePoint(.4, .45), StrokePoint(.6, .55)],
            base_size=12, opacity=1, hardness=1,
        )]
        self.canvas.resize(1000, 500)
        self.canvas._ensure_pixmap_size()
        self.canvas._ensure_view_render_current()
        image = self.canvas.render_frame_to_image(1, 1000, 500)
        for point in self.canvas.data.frames[1].strokes[0].points:
            x, y = self.canvas._point_to_pixel(point)
            nx, ny = self.canvas._to_normalized(x, y)
            self.assertAlmostEqual(nx, point.x)
            self.assertAlmostEqual(ny, point.y)
            # The outline ends at the endpoint's subpixel coordinate; allow
            # the adjacent raster pixel when rounding lands outside its cap.
            for rendered in (image, self.canvas.canvas_pixmap.toImage()):
                self.assertGreater(max(
                    rendered.pixelColor(round(x) + dx, round(y) + dy).alpha()
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                ), 0)
        # A 2x capture (HiDPI/playblast) must put each point at exactly 2x.
        uniform = self.canvas._uniform_transform(2000, 1000)
        view = self.canvas._view_panzoom_transform(2000, 1000)
        for point in self.canvas.data.frames[1].strokes[0].points:
            x, y = self.canvas._point_to_pixel(point)
            ex, ey = self.canvas._point_to_pixel(point, view, uniform)
            self.assertAlmostEqual(ex, x * 2)
            self.assertAlmostEqual(ey, y * 2)

    def test_existing_saved_points_survive_resize_and_reload(self):
        data = SketchboardData()
        data.from_dict({'canvas_width': 800, 'canvas_height': 600, 'frames': {'1': [{
            'points': [{'nx': .25, 'ny': .25}, {'nx': .75, 'ny': .75}]
        }]}}, 1600, 600)
        self.canvas.data = data
        payload = data.to_dict()
        self.canvas.resize(1600, 600)
        self.canvas._rebuild_current_frame()
        self.canvas.resize(800, 600)
        self.assertEqual(data.to_dict(), payload)
        self.assertEqual(self.canvas._point_to_pixel(data.frames[1].strokes[0].points[0]), (200.0, 150.0))

    def test_eraser_and_undo_still_hit_drawing_after_resize(self):
        cmds.setAttr(self.shape + '.filmFit', 2)
        self.canvas.data.get_or_create_frame(1).strokes = [Stroke(
            points=[StrokePoint(.25, .5), StrokePoint(.75, .5)],
            base_size=12, opacity=1, hardness=1,
        )]
        self.canvas.resize(1600, 600)
        self.canvas._ensure_pixmap_size()
        self.canvas.tool = 'eraser'
        self.canvas.brush_size = 40
        self.canvas._begin_stroke(QPointF(800, 280), 1.0)
        self.canvas._add_point(QPointF(800, 320), 1.0)
        self.canvas._end_stroke()
        image = self.canvas.canvas_pixmap.toImage()
        self.assertEqual(image.pixelColor(800, 300).alpha(), 0)
        self.assertGreater(image.pixelColor(700, 300).alpha(), 0)
        self.canvas.undo_stroke()
        self.assertGreater(self.canvas.canvas_pixmap.toImage().pixelColor(800, 300).alpha(), 0)


class BrushPanelSelectionTests(unittest.TestCase):
    def test_hidden_focused_panel_does_not_steal_maximized_view(self):
        def panels(**kwargs):
            if kwargs.get('withFocus'):
                return 'modelPanel1'
            if kwargs.get('visiblePanels'):
                return ['modelPanel4']
            return ['modelPanel1', 'modelPanel4']
        with mock.patch.object(cmds, 'getPanel', side_effect=panels):
            self.assertEqual(get_active_model_panel('modelPanel4'), 'modelPanel4')


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(not result.result.wasSuccessful())
