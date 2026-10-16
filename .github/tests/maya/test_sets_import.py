import unittest
from unittest import mock
from AnimKey.mods.maya_compat import QtCore, QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
from AnimKey.buttons import selectionSets as sets


class SetsImportTests(unittest.TestCase):
    def setUp(self):
        self.loader = mock.patch.object(sets, "_load_from_scene", return_value=None)
        self.saver = mock.patch.object(sets, "_save_to_scene")
        self.loader.start()
        self.save = self.saver.start()
        self.window = sets.SetManagerWindow()
        self.window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        self.window.show()
        app.processEvents()

    def tearDown(self):
        self.window._skip_save = True
        self.window.close()
        self.window.deleteLater()
        app.processEvents()
        self.loader.stop()
        self.saver.stop()
        sets._win = None

    def payload(self):
        return sets._normalize_import_data({"tabs": [
            {"name": "Face", "data": {"layout_mode": "board",
                "background_image": "Z:/otherPC/nonexistent.png", "sets": [
                    {"name": "Left", "members": ["otherRig:brow_L"], "board_pos": [24, 24]},
                    {"name": "Right", "members": ["otherRig:brow_R"], "board_pos": [96, 24]}
                ]}},
            {"name": "Body", "data": {"sets": [{"name": "Body", "members": ["otherRig:body"]}]}}
        ], "geometry": {"x": 99999, "y": -99999, "width": 450, "height": 350}})

    def test_append_after_clear_and_repeat_without_processing_events(self):
        for _ in range(3):
            self.window.replace_data({"tabs": [{"name": "Tab 1", "data": {"sets": []}}]})
            pages = self.window.append_data(self.payload())
            self.assertEqual(len(pages), 2)
            self.assertEqual(self.window.stack.currentWidget(), pages[0])
            self.assertEqual(len(pages[0].container.buttons), 2)
            self.assertTrue(all(not b.isHidden() for b in pages[0].container.buttons))
            self.window.append_data(self.payload())
            self.assertEqual(len(self.window.pages), 4)
            self.assertEqual(self.window.tab_bar.tab_text(2), "Face 2")
        app.processEvents()
        self.assertEqual(len(self.window.pages), 4)

    def test_load_does_not_save_partial_tabs_or_keep_foreign_screen_position(self):
        self.save.reset_mock()
        self.window.replace_data(self.payload())
        self.save.assert_not_called()
        self.assertEqual(len(self.window.pages), 2)
        self.assertLess(self.window.x(), 99999)
        self.assertGreater(self.window.y(), -99999)
        self.window.save_data()
        self.assertEqual(len(self.save.call_args[0][0]["tabs"]), 2)


if __name__ == "__main__":
    unittest.main()
