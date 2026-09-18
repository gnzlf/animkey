"""Save-boundary regression checks for AnimKey's custom motion trail."""

import os
import sys
import tempfile
import unittest

import maya.cmds as cmds


ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class MotionTrailSaveSafetyTests(unittest.TestCase):
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
