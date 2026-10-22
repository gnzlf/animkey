"""Context menus and assignable shortcuts expose the same explicit operations."""

import json
import os
import tempfile
import unittest
from unittest import mock

from AnimKey.mods.maya_compat import QtCore, QtWidgets

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.core import toolbar, settings
from AnimKey.mods import hotkeysMod
from AnimKey.buttons import copyWorldspace
from AnimKey.core.executionGuard import animkey_execution


class AnimationToolMenuTests(unittest.TestCase):
    def setUp(self):
        self.bar = object.__new__(toolbar.AnimKeyToolbar)
        self.keys = []
        self.labels = []
        self.bar._connect_guarded_action = mock.Mock()
        self.bar._add_menu_action = self._action
        self.button = QtWidgets.QPushButton()
        self.addCleanup(self.button.deleteLater)

    def _action(self, menu, label, key):
        self.keys.append(key)
        return menu.addAction(label)

    def _show(self, method):
        def capture(menu, position):
            self.labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        with mock.patch.object(toolbar, "execute_qt", side_effect=capture):
            method(self.button, QtCore.QPoint(0, 0))

    def test_link_and_worldspace_have_the_same_eight_scopes(self):
        scopes = ["All Animation", "Selected Timeline Range", "Playback Range", "Current Frame"]
        for prefix, method in (("Link", self.bar._show_link_objects_context_menu),
                               ("Worldspace", self.bar._show_worldspace_context_menu)):
            self.keys = []
            self._show(method)
            expected = ["{} {} - {}".format(operation, prefix, scope)
                        for operation in ("Copy", "Paste") for scope in scopes]
            self.assertEqual(self.labels, expected)
            for key in self.keys:
                self.assertIn(key, settings.DEFAULT_SHORTCUTS)
                self.assertTrue(hotkeysMod.get_action_function(key), key)

    def test_mirror_menu_removes_quick_and_delete_snapshot(self):
        self._show(self.bar._show_mirror_context_menu)
        self.assertEqual(self.labels, ["All Mirror (Swap Both Sides)", "Mirror to Left",
                                       "Mirror to Right", "Auto Mirror (Real-time)", "Snapshot"])
        for key in self.keys:
            self.assertTrue(hotkeysMod.get_action_function(key), key)

    def test_copy_animation_menu_exposes_file_saves_and_loads(self):
        self.bar.window = self.button
        self._show(self.bar._show_copy_animation_context_menu)
        self.assertEqual(self.labels, ["Copy Animation", "Save Animation", "Load Animation",
                                       "Paste Animation", "Paste Insert", "Paste Opposite",
                                       "Copy Pose", "Paste Pose", "Save Pose", "Load Pose"])
        for key in self.keys:
            self.assertIn(key, settings.DEFAULT_SHORTCUTS)
            self.assertTrue(hotkeysMod.get_action_function(key), key)

    def test_retired_saved_shortcuts_are_ignored(self):
        retired = ("Toggle Auto Worldspace", "Toggle Auto Link", "Delete Snapshot",
                   "Quick Mirror (No Snapshot)", "Paste Link Next Frame", "Paste Link All Keys")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "shortcuts.json")
            with open(path, "w") as output:
                json.dump(dict.fromkeys(retired, "Ctrl+B"), output)
            with mock.patch.object(settings, "get_shortcuts_file", return_value=path):
                loaded = settings.load_shortcuts()
        for key in retired:
            self.assertNotIn(key, loaded)
            self.assertFalse(hotkeysMod.get_action_function(key))

    def test_ctrl_worldspace_cannot_activate_pin(self):
        with animkey_execution("Menu regression"), \
                mock.patch.object(copyWorldspace.mel, "eval", return_value=4), \
                mock.patch.object(copyWorldspace, "toggle_auto_worldspace") as pin, \
                mock.patch.object(copyWorldspace, "copy_worldspace_current_frame") as copy:
            copyWorldspace.execute()
            pin.assert_not_called()
            copy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
