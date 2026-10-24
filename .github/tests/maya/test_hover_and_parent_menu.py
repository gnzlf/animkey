"""Paint-only hover behavior and the new Parent range menu routes."""
import os
import unittest
from unittest import mock

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.mods import uiMod as ui
from AnimKey.core import toolbar, settings
from AnimKey.mods import hotkeysMod


class HoverTests(unittest.TestCase):
    def setUp(self):
        self.root = QtWidgets.QWidget()
        # Maya standalone does not initialize its normal UI font configuration.
        QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
        self.root.setFont(QtGui.QFont("Arial", 9))
        self.root.setStyleSheet("background: #252525;")
        self.layout = QtWidgets.QHBoxLayout(self.root)
        self.button = ui.HoverButton("Parent")
        self.button.setFixedSize(60, 28)
        self.button.setStyleSheet("QPushButton {background:#444; color:white; border:1px solid #7bbca6; border-radius:3px;}")
        self.slider = ui.AnimKeySlider(show_dots=False)
        self.layout.addWidget(self.button)
        self.layout.addWidget(self.slider)
        self.root.show()
        APP.processEvents()
        self.addCleanup(self.root.close)

    def test_button_growth_is_paint_only_and_changes_pixels(self):
        geometry = self.button.geometry()
        slider_geometry = self.slider.geometry()
        before = self.button.grab().toImage()
        self.button._animate_hover(True)
        self.button._hover_animation.setCurrentTime(110)
        after = self.button.grab().toImage()
        self.assertEqual(self.button.geometry(), geometry)
        self.assertEqual(self.slider.geometry(), slider_geometry)
        self.assertNotEqual(before, after)
        self.assertAlmostEqual(self.button._hover_amount, 1.0)
        self.button._animate_hover(False)
        self.button._hover_animation.setCurrentTime(110)
        self.assertAlmostEqual(self.button._hover_amount, 0.0)

    def test_track_does_not_grow_only_thumb_does(self):
        track = self.slider._get_track_rect()
        geometry = self.slider.geometry()
        signals = mock.Mock()
        self.slider.valueChanged.connect(signals)
        self.slider._update_handle_hover(QtCore.QPoint(track.left(), track.center().y()))
        self.assertFalse(self.slider._handle_hovered)
        center = QtCore.QPoint(self.slider._value_to_x(self.slider._display_value), track.center().y())
        before = self.slider.grab().toImage()
        self.slider._update_handle_hover(center)
        self.slider._handle_hover_animation.setCurrentTime(110)
        after = self.slider.grab().toImage()
        self.assertTrue(self.slider._handle_hovered)
        self.assertNotEqual(before, after)
        self.assertEqual(self.slider._get_track_rect(), track)
        self.assertEqual(self.slider.geometry(), geometry)
        signals.assert_not_called()
        self.slider._update_handle_hover()
        self.slider._handle_hover_animation.setCurrentTime(110)
        self.assertEqual(self.slider._handle_hover, 0.0)

    def test_disabled_and_hidden_controls_reset_hover(self):
        self.button._set_hover_amount(1.0)
        self.slider._set_handle_hover(1.0)
        self.button.setEnabled(False)
        self.slider.setEnabled(False)
        self.assertEqual(self.button._hover_amount, 0.0)
        self.assertEqual(self.slider._handle_hover, 0.0)
        self.slider._set_handle_hover(1.0)
        self.slider.hide()
        self.assertEqual(self.slider._handle_hover, 0.0)

    def test_render_before_after(self):
        output = os.environ.get("ANIMKEY_TEST_ARTIFACTS")
        if not output:
            self.skipTest("No render destination")
        os.makedirs(output, exist_ok=True)
        stock = QtWidgets.QPushButton("Parent", self.root)
        stock.setFixedSize(60, 28)
        stock.setStyleSheet(self.button.styleSheet())
        self.layout.addWidget(stock)
        APP.processEvents()
        option = QtWidgets.QStyleOptionButton()
        self.button.initStyleOption(option)
        self.assertEqual(option.text, "Parent")
        self.assertEqual(self.button.text(), stock.text())
        self.root.grab().save(os.path.join(output, "hover-normal.png"))
        self.button._set_hover_amount(1.0)
        self.slider._set_handle_hover(1.0)
        self.root.grab().save(os.path.join(output, "hover-active.png"))


class ParentMenuTests(unittest.TestCase):
    def test_new_menu_routes_and_hotkeys_resolve_to_callable_functions(self):
        bar = object.__new__(toolbar.AnimKeyToolbar)
        bar._add_menu_action = lambda menu, label, key: menu.addAction(label)
        bar._connect_guarded_action = mock.Mock()
        button = QtWidgets.QPushButton()
        self.addCleanup(button.deleteLater)
        with mock.patch.object(toolbar, "execute_qt"):
            bar._show_link_objects_context_menu(button, QtCore.QPoint())
        routes = {call.args[1]: call.args[2] for call in bar._connect_guarded_action.call_args_list}
        for operation in ("Copy", "Paste"):
            for scope in ("Frame", "All Animation", "Selected Range", "Playback Range"):
                key = operation + " Link " + scope
                self.assertTrue(callable(routes[key]))
                self.assertIn(key, settings.DEFAULT_SHORTCUTS)
                self.assertTrue(hotkeysMod.get_action_function(key))


if __name__ == "__main__":
    unittest.main()
