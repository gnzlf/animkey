"""Regression checks for a checkout shadowing an existing Maya install."""

import importlib.machinery
import os
import runpy
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class InstallerPathTests(unittest.TestCase):
    def test_installation_already_on_sys_path_is_promoted_above_checkout(self):
        maya = types.ModuleType("maya")
        maya.cmds = types.ModuleType("maya.cmds")
        maya.mel = types.ModuleType("maya.mel")
        with mock.patch.dict(sys.modules, {"maya": maya, "maya.cmds": maya.cmds, "maya.mel": maya.mel}):
            installer = runpy.run_path(os.path.join(ROOT, "AnimKey_Install.py"))
        with tempfile.TemporaryDirectory() as directory:
            app_dir = os.path.join(directory, "maya")
            os.makedirs(os.path.join(app_dir, "AnimKey"))
            installed_init = os.path.join(app_dir, "AnimKey", "__init__.py")
            with open(installed_init, "w", encoding="utf-8") as stream:
                stream.write("# installed copy\n")
            original_path = list(sys.path)
            try:
                sys.path[:] = [ROOT, app_dir + os.sep] + original_path
                self.assertNotEqual(
                    importlib.machinery.PathFinder.find_spec("AnimKey", sys.path).origin,
                    installed_init,
                )
                installer["_prioritize_installation"](app_dir)
                self.assertEqual(sys.path[0], app_dir)
                self.assertEqual(
                    importlib.machinery.PathFinder.find_spec("AnimKey", sys.path).origin,
                    installed_init,
                )
            finally:
                sys.path[:] = original_path


if __name__ == "__main__":
    unittest.main()
