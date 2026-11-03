import unittest
from unittest import mock

import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds
import maya.api.OpenMaya as om
from AnimKey.core import runtime_lifecycle as lifecycle
from AnimKey.mods.maya_compat import QtCore

_APP = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])


class RuntimeLifecycleTests(unittest.TestCase):
    def test_auto_worldspace_owns_callbacks_and_disables_before_scene_change(self):
        from AnimKey.buttons import copyWorldspace as ws
        node = cmds.createNode("transform", name="autoWorldOwner")
        source = {"samples": {"1": cmds.xform(node, query=True, worldSpace=True, matrix=True)}}
        with mock.patch.object(ws, "_get_frame_data_file", return_value="unused.json"), \
                mock.patch.object(ws, "_read_payload", return_value={"sources": [source]}), \
                mock.patch.object(ws, "_target_mapping", return_value=[(node, source)]):
            try:
                self.assertTrue(ws.enable_auto_worldspace())
                scope = ws._state.auto_scope
                self.assertGreaterEqual(len(scope.pending_cleanup), 5)
                calls = mock.Mock()
                queued = scope.guard(calls)
                cmds.file(new=True, force=True)
                self.assertFalse(ws._state.auto_enabled)
                self.assertFalse(scope.active)
                queued()
                calls.assert_not_called()
            finally:
                ws.disable_auto_worldspace()

    def test_auto_link_reenable_is_idempotent_and_new_scene_disables_it(self):
        from AnimKey.buttons import linkObjects
        node = cmds.createNode("transform", name="autoLinkOwner")
        linkObjects._state.relative_data = {"main_obj": node, "relative_matrices": {}}
        try:
            self.assertTrue(linkObjects.enable_auto_link())
            first = linkObjects._state.attribute_callback_id
            self.assertTrue(linkObjects.enable_auto_link())
            self.assertEqual(linkObjects._state.attribute_callback_id, first)
            cmds.file(new=True, force=True)
            self.assertFalse(linkObjects.is_auto_link_enabled())
        finally:
            linkObjects.disable_auto_link()

    def tearDown(self):
        self.assertTrue(lifecycle.close_all())

    def test_replace_invalidates_deferred_and_removes_real_api_callback(self):
        calls = []
        node = cmds.createNode("transform")
        old = lifecycle.replace_scope("test.selection")
        old.api_callback("selection", om.MEventMessage.addEventCallback,
                         "SelectionChanged", lambda *args: calls.append("old"))
        queued = old.guard(lambda: calls.append("deferred"))
        cmds.select(clear=True)
        cmds.select(node)
        self.assertTrue(calls)
        calls[:] = []
        lifecycle.replace_scope("test.selection")
        queued()
        cmds.select(clear=True)
        self.assertEqual(calls, [])
        self.assertEqual(old.pending_cleanup, ())

    def test_failed_cleanup_is_retained_and_prevents_duplicate_registration(self):
        scope = lifecycle.replace_scope("test.retry")
        cleanup = mock.Mock(side_effect=RuntimeError("busy"))
        scope.own("resource", cleanup)
        with self.assertLogs(lifecycle.__name__, level="WARNING"):
            self.assertFalse(lifecycle.close_scope("test.retry"))
            with self.assertRaises(RuntimeError):
                lifecycle.replace_scope("test.retry")
        self.assertEqual(scope.pending_cleanup, ("resource",))
        self.assertFalse(scope.active)
        cleanup.side_effect = None
        self.assertTrue(lifecycle.close_scope("test.retry"))
        self.assertEqual(cleanup.call_count, 3)

    def test_scopes_do_not_release_other_owners(self):
        first = lifecycle.replace_scope("test.first")
        second = lifecycle.replace_scope("test.second")
        cleanup = mock.Mock()
        second.own("resource", cleanup)
        first.close()
        cleanup.assert_not_called()
        self.assertTrue(second.active)

    def test_closing_scope_stops_timer_and_guards_queued_timeout(self):
        scope = lifecycle.replace_scope("test.timer")
        timer = scope.timer("timer", QtCore.QTimer())
        callback = mock.Mock()
        queued = scope.guard(callback)
        timer.timeout.connect(queued)
        timer.start(1000)
        self.assertTrue(scope.close())
        self.assertFalse(timer.isActive())
        queued()
        callback.assert_not_called()

    def test_script_job_cleanup_does_not_scan_or_kill_foreign_jobs(self):
        scope = lifecycle.replace_scope("test.jobs")
        with mock.patch.object(lifecycle.cmds, "scriptJob") as jobs:
            jobs.return_value = 13
            scope.script_job("idle", idleEvent=lambda: None)
            jobs.return_value = True
            self.assertTrue(scope.close())
            self.assertEqual(jobs.call_args_list[-2:], [
                mock.call(exists=13), mock.call(kill=13, force=True)])
            self.assertFalse(any("listJobs" in call.kwargs for call in jobs.call_args_list))


if __name__ == "__main__":
    unittest.main()
