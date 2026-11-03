"""Regression checks for AnimKey's persistent row above the Time Slider."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from AnimKey.mods.maya_compat import QtWidgets

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.core import toolbar
from AnimKey.core.runtime_lifecycle import replace_scope


class ToolbarWorkspaceRestoreTests(unittest.TestCase):
    def setUp(self):
        self.bar = object.__new__(toolbar.AnimKeyToolbar)
        self.bar.config = {"toolbar_scale": 1.0}
        self.bar.is_visible = True
        self.bar._scene_resources = replace_scope("test.toolbar.workspace")
        self.addCleanup(self.bar._scene_resources.close)
        self.bar._defer_toolbar_refresh = mock.Mock()
        self.root = QtWidgets.QWidget()
        self.addCleanup(self.root.deleteLater)
        self.state = dict(exists=False, visible=False, collapsed=True, tabbed=True)
        self.timeline_exists = True
        self.run_ui_script = False
        self.queued = []
        self.edits = []
        for patch in (
            mock.patch.object(toolbar.cmds, "workspaceControl", side_effect=self._workspace),
            mock.patch.object(toolbar.cmds, "evalDeferred", side_effect=lambda cb, **kw: self.queued.append(cb)),
            mock.patch.object(toolbar.mui.MQtUtil, "findControl", side_effect=lambda name: 1 if self.state["exists"] else None),
            mock.patch.object(toolbar, "wrapInstance", side_effect=lambda *args: self.root),
            mock.patch.object(self.bar, "_build_ui", side_effect=self._build_ui),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _workspace(self, name, **flags):
        if flags.get("exists"):
            return self.timeline_exists if name == "TimeSlider" else self.state["exists"]
        if flags.get("query"):
            return self.state.get("visible", False)
        self.edits.append((name, flags))
        if not flags.get("edit"):
            self.state["exists"] = True
        if "dockToMainWindow" in flags:
            self.state["tabbed"] = flags["dockToMainWindow"][1]
        if "dockToControl" in flags:
            self.state["tabbed"] = False
            self.state["position"] = flags["dockToControl"]
        if "actLikeMayaUIElement" in flags:
            self.state["native"] = flags["actLikeMayaUIElement"]
        if "visible" in flags:
            self.state["visible"] = flags["visible"]
        if "collapse" in flags:
            self.state["collapsed"] = flags["collapse"]
        if flags.get("restore"):
            self.state.update(visible=True, collapsed=False)
        if self.run_ui_script and flags.get("loadImmediately"):
            with mock.patch.object(toolbar, "animkey_toolbar", self.bar):
                toolbar.restore_workspace()

    def _build_ui(self):
        self.bar.toolbar_scroll_area = QtWidgets.QWidget(self.root)
        self.bar.content_widget = QtWidgets.QWidget(self.bar.toolbar_scroll_area)

    def _assert_visible_row(self):
        self.assertTrue(self.state["exists"])
        self.assertTrue(self.state["visible"])
        self.assertFalse(self.state["collapsed"])
        self.assertFalse(self.state["tabbed"])
        self.assertTrue(self.state["native"])
        self.assertEqual(self.state["position"], ("TimeSlider", "top"))

    def _flush_deferred(self):
        queued, self.queued = self.queued, []
        for callback in queued:
            callback()
        self.assertFalse(self.queued, "Workspace repair must not queue itself forever")

    def test_new_toolbar_is_an_expanded_independent_row_above_timeline(self):
        self.bar._create_workspace()
        self._assert_visible_row()
        creation = next(flags for name, flags in self.edits if not flags.get("edit"))
        self.assertTrue(creation["loadImmediately"])
        self.assertIn("restore_workspace", creation["uiScript"])

    def test_saved_hidden_tab_without_content_is_rebuilt_and_undocked(self):
        self.state["exists"] = True
        self.bar._create_workspace()
        self.bar._build_ui.assert_called_once()
        self._assert_visible_row()

    def test_show_reuses_existing_widgets_without_duplicate_builds(self):
        self.state["exists"] = True
        self._build_ui()
        self.bar._create_workspace()
        self.bar._create_workspace()
        self.bar._build_ui.assert_not_called()
        self._assert_visible_row()

    def test_startup_layout_cannot_hide_toolbar_after_show(self):
        self.bar._create_workspace()
        self.state.update(visible=False, collapsed=True, tabbed=True)
        self._flush_deferred()
        self._assert_visible_row()

    def test_early_startup_uses_non_tabbed_fallback_until_timeline_exists(self):
        self.timeline_exists = False
        self.bar._create_workspace()
        self.assertFalse(self.state["tabbed"])
        self.timeline_exists = True
        self._flush_deferred()
        self._assert_visible_row()

    def test_explicit_hide_is_not_reversed_by_queued_startup_repair(self):
        self.bar._create_workspace()
        self.bar._hide_toolbar_gif_preview = mock.Mock()
        with mock.patch.object(toolbar.ui, "cleanup_animkey_runtime"):
            self.bar.hide()
        self._flush_deferred()
        self.assertFalse(self.state["visible"])
        self.assertFalse(self.bar.is_visible)

    def test_unload_invalidates_queued_workspace_repairs(self):
        self.bar._create_workspace()
        self.bar._scene_resources.close()
        self.state.update(exists=False, visible=False)
        self._flush_deferred()
        self.assertFalse(self.state["exists"])

    def test_maya_ui_script_restores_content_without_reentering_show(self):
        self.state["exists"] = True
        self.bar.is_visible = False
        with mock.patch.object(toolbar, "animkey_toolbar", self.bar), \
                mock.patch.object(self.bar, "show") as show:
            toolbar.restore_workspace()
            show.assert_not_called()
        self._flush_deferred()
        self._assert_visible_row()
        self.bar._build_ui.assert_called_once()

    def test_immediate_ui_script_does_not_duplicate_widgets_or_loop_at_idle(self):
        self.run_ui_script = True
        self.bar._create_workspace()
        self._flush_deferred()
        self._assert_visible_row()
        self.bar._build_ui.assert_called_once()

    def test_live_widgets_from_an_old_workspace_are_not_mistaken_for_current_ui(self):
        self.state["exists"] = True
        self._build_ui()
        self.root = QtWidgets.QWidget()
        self.addCleanup(self.root.deleteLater)
        self.bar._create_workspace()
        self.bar._build_ui.assert_called_once()
        self.assertIs(self.bar.toolbar_scroll_area.parentWidget(), self.root)
        self._assert_visible_row()


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
