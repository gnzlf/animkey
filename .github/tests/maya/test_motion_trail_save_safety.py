"""Save-boundary regression checks for AnimKey's custom motion trail."""

import os
import sys
import tempfile
import unittest
from unittest import mock

import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds


ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class MotionTrailSaveSafetyTests(unittest.TestCase):
    def test_vertex_rebake_fallback_is_guarded_after_shutdown(self):
        from AnimKey.buttons import trail
        container = cmds.createNode("transform")
        trail._set_string_attr(container, trail.TRAIL_COMPONENT_ATTR, "mesh.vtx[0]")
        scope = trail._trail_scope("deferred")
        queued = []
        with mock.patch.object(scope, "script_job", side_effect=RuntimeError("idle unavailable")), \
                mock.patch.object(trail.cmds, "evalDeferred", side_effect=lambda cb, **kw: queued.append(cb)), \
                mock.patch.object(trail, "_rebake_vertex_trail") as rebake:
            trail._schedule_vertex_trail_rebake(container)
            self.assertEqual(len(queued), 1)
            trail.shutdown_runtime()
            queued[0]()
            rebake.assert_not_called()

    def test_late_deferred_redraw_does_not_touch_replacement_scene(self):
        from AnimKey.buttons import trail, trail_runtime
        work = mock.Mock()
        queued = []
        with mock.patch.object(trail_runtime.cmds, "evalDeferred", side_effect=lambda cb, **kw: queued.append(cb)):
            self.assertTrue(trail_runtime._defer_low(work))
        trail.shutdown_runtime()
        for callback in queued:
            callback()
        work.assert_not_called()

    def test_serialized_job_ids_are_never_killed_as_runtime_owners(self):
        from AnimKey.buttons import trail
        node = cmds.createNode("transform")
        trail._set_string_attr(node, trail.TRAIL_SCRIPTJOBS_ATTR, "777")
        with mock.patch.object(trail, "_kill_scriptjob") as kill:
            trail._clear_trail_scriptjobs(node)
        kill.assert_not_called()

    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        try:
            from AnimKey.buttons import trail
            trail.remove_all_trails()
        except Exception:
            pass

    def test_custom_trail_pauses_cache_writes_during_scene_save(self):
        from AnimKey.buttons import trail, trail_runtime

        source = cmds.spaceLocator(name="saveSafetySource")[0]
        cmds.setKeyframe(source, attribute="translateX", time=1, value=0.0)
        cmds.setKeyframe(source, attribute="translateX", time=20, value=12.0)
        cmds.select(source, replace=True)
        created = trail.create_trail()
        self.assertTrue(created)

        shape = trail._trail_shape_in_container(created[0])
        self.assertTrue(shape and cmds.objExists(shape))
        before = cmds.getAttr(shape + ".cacheData")

        trail_runtime._on_before_scene_save()
        self.assertTrue(trail_runtime.is_scene_save_in_progress())
        trail_runtime._sync_cache(shape)
        self.assertEqual(before, cmds.getAttr(shape + ".cacheData"))

        output = os.path.join(tempfile.gettempdir(), "animkey_trail_save_safety.ma")
        cmds.file(rename=output)
        cmds.file(save=True, force=True, type="mayaAscii")

        trail_runtime._resume_after_scene_save()
        self.assertFalse(trail_runtime.is_scene_save_in_progress())
        self.assertTrue(trail_runtime.job_ids())

    def test_recovery_cancels_capture_before_scene_save(self):
        from AnimKey.buttons import animCrash

        source = cmds.spaceLocator(name="recoverySaveSafetySource")[0]
        cmds.setKeyframe(source, attribute="translateX", time=1, value=0.0)
        cmds.setKeyframe(source, attribute="translateX", time=120, value=30.0)
        animCrash.cancel_async_checkpoints()
        animCrash.request_checkpoint(auto=True, desc="Save boundary test")
        self.assertIsNotNone(animCrash._async_checkpoint_capture)
        animCrash.RecoverySystem._on_before_save()
        self.assertIsNone(animCrash._async_checkpoint_capture)


if __name__ == "__main__":
    unittest.main()
