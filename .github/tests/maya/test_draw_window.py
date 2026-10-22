"""Draw lifecycle, scene preservation and Settings material regression checks."""
from pathlib import Path
import sys
import unittest
from unittest import mock
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from AnimKey.mods.maya_compat import QtCore, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import brush
from AnimKey.core.settings_style import GlassPanel, WindowControl


class DrawWindowTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        self.main = QtWidgets.QMainWindow()
        self.main.resize(1280, 800)
        self.main.show()
        self.viewport = QtWidgets.QWidget(self.main)
        self.viewport.setGeometry(0, 0, 1280, 700)
        self.viewport.show()
        self.patches = [mock.patch.object(brush, "get_maya_main_window", return_value=self.main),
                        mock.patch.object(brush, "get_active_model_panel", return_value="modelPanel4"),
                        mock.patch.object(brush, "get_active_model_panel_widget", return_value=self.viewport),
                        mock.patch.object(brush.SketchboardWindow, "_install_native_pen_event_filter")]
        for patch in self.patches:
            patch.start()
        self.window = None

    def tearDown(self):
        if self.window:
            self.window.close()
        brush.close_instance()
        self.main.close()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        for patch in reversed(self.patches):
            patch.stop()

    def open(self):
        self.window = brush.launch()
        APP.processEvents()
        return self.window

    def test_open_preserves_scene_selection_undo_and_does_not_create_nodes(self):
        selected = cmds.createNode("transform", name="animatorSelection")
        cmds.select(selected)
        cmds.currentTime(17)
        cmds.file(modified=False)
        before = (cmds.ls(sl=True), cmds.undoInfo(q=True, undoName=True), cmds.ls(long=True))
        window = self.open()
        self.assertEqual((cmds.ls(sl=True), cmds.undoInfo(q=True, undoName=True), cmds.ls(long=True)), before)
        self.assertFalse(cmds.file(q=True, modified=True))
        self.assertEqual(window.canvas.current_frame, 17)
        self.assertIn("Frame: 17", window.status_bar.text())

    def test_open_existing_drawings_does_not_rebuild_timeline_keys(self):
        data = brush.SketchboardData()
        data.get_or_create_frame(3).strokes = [brush.Stroke(points=[brush.StrokePoint(.1, .2), brush.StrokePoint(.3, .4)])]
        brush.save_brush_data_to_scene(data)
        cmds.file(modified=False)
        before = cmds.undoInfo(q=True, undoName=True)
        window = self.open()
        self.assertEqual(len(window.canvas.data.frames[3].strokes), 1)
        self.assertEqual(cmds.undoInfo(q=True, undoName=True), before)
        self.assertFalse(cmds.file(q=True, modified=True))

    def test_controls_use_settings_shell_without_blurring_canvas(self):
        window = self.open()
        palette = window.hud_wrapper
        self.assertIsInstance(palette.container, GlassPanel)
        self.assertIsInstance(palette.title_bar.close_btn, WindowControl)
        self.assertEqual(palette.findChild(QtWidgets.QLabel, "settingsSubtitle").text(), "Draw")
        self.assertTrue(palette.property("materialSurface"))
        self.assertFalse(bool(window.property("materialSurface")))
        self.assertEqual(window.tool_panel.styleSheet(), "")
        self.assertIs(palette.canvas, window.canvas)
        self.assertLessEqual(palette.height(), 90)
        self.assertGreaterEqual(palette.width() / palette.height(), 8)
        self.assertIs(window.status_bar_wrapper.parentWidget(), palette.title_bar)

    def test_saved_frames_are_not_all_smoothed_while_loading(self):
        data = brush.SketchboardData()
        payload = {"frames": {str(frame): [{"points": [{"nx": .1, "ny": .2}, {"nx": .7, "ny": .6}]}]
                              for frame in range(1, 20)}}
        with mock.patch.object(brush.Stroke, "compute_smoothed") as smooth:
            data.from_dict(payload, 640, 480)
            smooth.assert_not_called()
        self.assertEqual(len(data.frames), 19)

    def test_hide_stops_sync_and_reopen_reuses_window(self):
        window = self.open()
        timer = window.sync_timer
        palette = window.hud_wrapper
        brush.launch()
        self.assertFalse(window.isVisible())
        self.assertFalse(palette.isVisible())
        self.assertFalse(timer.isActive())
        self.assertFalse(window._tablet_event_filter_installed)
        self.assertIs(brush.launch(), window)
        self.assertTrue(window.isVisible())
        self.assertTrue(palette.isVisible())
        self.assertIs(window.sync_timer, timer)
        self.assertTrue(timer.isActive())

    def test_palette_close_stops_callbacks_and_closes_canvas(self):
        window = self.open()
        job = window.timeline.script_job_id
        window.hud_wrapper.close()
        self.assertFalse(window.isVisible())
        self.assertFalse(window.sync_timer.isActive())
        self.assertIsNone(brush._instance)
        if job is not None:
            self.assertFalse(cmds.scriptJob(exists=job))
        self.window = None

    def test_idle_sync_does_not_repaint_or_reacquire_viewport(self):
        window = self.open()
        window._next_panel_refresh = 0
        with mock.patch.object(window.canvas, "_ensure_view_render_current", return_value=False), \
                mock.patch.object(window.canvas, "update") as repaint, \
                mock.patch.object(brush, "get_active_model_panel_widget") as lookup:
            for _ in range(30):
                window._sync_geometry()
            repaint.assert_not_called()
            lookup.assert_not_called()

    def test_collapse_and_navigation_keep_controls_and_input_working(self):
        window = self.open()
        window.toggle_sidebar()
        self.assertFalse(window.sidebar_widget.isVisible())
        self.assertTrue(window.hud_wrapper.title_bar.isVisible())
        window.toggle_sidebar()
        self.assertTrue(window.sidebar_widget.isVisible())
        window.toggle_mode_btn.setChecked(True)
        self.assertTrue(window.canvas.testAttribute(brush.Qt.WA_TransparentForMouseEvents))
        self.assertTrue(bool(window.windowFlags() & brush.Qt.WindowTransparentForInput))
        window.toggle_mode_btn.setChecked(False)
        self.assertFalse(window.canvas.testAttribute(brush.Qt.WA_TransparentForMouseEvents))
        self.assertFalse(bool(window.windowFlags() & brush.Qt.WindowTransparentForInput))
        self.assertTrue(window._is_controls_widget(window.tool_panel.tool_buttons["brush"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
