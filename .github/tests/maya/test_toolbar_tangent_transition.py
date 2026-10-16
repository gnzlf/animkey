import time
import unittest
from unittest import mock
from AnimKey.mods.maya_compat import QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
from AnimKey.core.toolbar import AnimKeyToolbar


class ToolbarTransitionTests(unittest.TestCase):
    def test_transition_finishes_without_full_refresh_per_button_per_tick(self):
        bar = AnimKeyToolbar.__new__(AnimKeyToolbar)
        bar._tangent_toggle_btn = QtWidgets.QPushButton()
        bar._button_widgets = {key: QtWidgets.QPushButton() for key in ("PLT", "STP", "FLT", "LIN", "CLP", "SPL", "AUT")}
        bar._btn_anims = {}
        bar.config = {"toolbar_icon_size": 28, "toolbar_scale": 1.0}
        bar.toolbar_scroll_area = mock.Mock()
        bar._refresh_toolbar_scroll_area = mock.Mock()
        with mock.patch("AnimKey.mods.configMod.load_workspace", return_value={}):
            for collapsed in (True, False, True, False):
                bar._refresh_toolbar_scroll_area.reset_mock()
                bar._set_tangent_buttons_collapsed(collapsed, persist=False)
                stop = time.monotonic() + .2
                while time.monotonic() < stop:
                    app.processEvents()
                    time.sleep(.005)
                self.assertLessEqual(bar._refresh_toolbar_scroll_area.call_count, 2)
                for button in bar._button_widgets.values():
                    self.assertEqual(button.maximumWidth(), 0 if collapsed else 28)
        for button in bar._button_widgets.values():
            button.close()


if __name__ == "__main__":
    unittest.main()
