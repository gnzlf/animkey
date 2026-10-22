import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons import mirror


class MirrorStabilityTests(unittest.TestCase):
    def test_auto_mirror_does_not_reapply_after_undo_or_scrubbing(self):
        left, right, snapshot = self._pair()
        cmds.select(left)
        groups = {"testRig": {"controls": [left], "label": "testRig"}}
        with mock.patch.object(mirror.cmds, "scriptJob", return_value=201), \
                mock.patch.object(mirror, "_group_controls_by_rig", return_value=groups), \
                mock.patch.object(mirror, "_load_or_build_basic_snapshot", return_value=(snapshot, True)):
            mirror.enable_auto_mirror()
            cmds.setAttr(left + ".translateX", 3.0)
            mirror._auto_mirror_callback()
            self.assertAlmostEqual(cmds.getAttr(right + ".translateX"), -3.0)
            cmds.undo()
            mirror._auto_mirror_state.last_run -= 1.0
            mirror._auto_mirror_callback()
            self.assertAlmostEqual(cmds.getAttr(right + ".translateX"), -2.0)
            cmds.currentTime(9)
            mirror._auto_mirror_state.last_run -= 1.0
            mirror._auto_mirror_callback()
            self.assertAlmostEqual(cmds.getAttr(right + ".translateX"), -2.0)
            mirror.disable_auto_mirror()

    def test_closed_auto_mirror_session_cannot_execute_queued_callback(self):
        with mock.patch.object(mirror.cmds, "scriptJob", return_value=201):
            mirror.enable_auto_mirror()
            old = mirror._auto_mirror_state.resources
            calls = []
            queued = old.guard(lambda: calls.append("stale"))
            mirror.disable_auto_mirror()
            mirror.enable_auto_mirror()
            queued()
            self.assertEqual(calls, [])
            self.assertFalse(old.active)
            mirror.disable_auto_mirror()

    def setUp(self):
        cmds.file(new=True, force=True)
        mirror._auto_mirror_state.enabled = False
        mirror._auto_mirror_state.script_job_id = None
        mirror._auto_mirror_state.scene_job_ids = []
        mirror._auto_mirror_state.last_run = 0.0
        mirror._auto_mirror_state.last_signature = None
        mirror._auto_mirror_state.in_callback = False

    def tearDown(self):
        if mirror._auto_mirror_state.enabled:
            mirror.disable_auto_mirror()
        mirror.clear_mirror_cache()

    @staticmethod
    def _pair():
        left = cmds.createNode("transform", name="arm_L_CTRL")
        right = cmds.createNode("transform", name="arm_R_CTRL")
        cmds.setAttr(left + ".translateX", 2.0)
        cmds.setAttr(right + ".translateX", -2.0)
        snapshot = mirror._build_basic_mirror_snapshot([left, right])
        return left, right, snapshot

    def test_calibrated_pair_mirror_is_one_undoable_change(self):
        left, right, snapshot = self._pair()
        cmds.setAttr(left + ".translateX", 3.0)
        cmds.flushUndo()

        mirror._open_animkey_undo_chunk("Mirror regression")
        try:
            count, _touched = mirror._mirror_selected_pose_two_phase(
                [left], snapshot, mirror._snap_sym_plane(snapshot)
            )
        finally:
            mirror._close_animkey_undo_chunk()

        self.assertGreater(count, 0)
        self.assertAlmostEqual(
            cmds.getAttr(right + ".translateX"), -3.0, places=5
        )
        cmds.undo()
        self.assertAlmostEqual(
            cmds.getAttr(right + ".translateX"), -2.0, places=5
        )

    def test_auto_mirror_does_not_mirror_its_own_writes_again(self):
        left, right, snapshot = self._pair()
        cmds.setAttr(left + ".translateX", 3.0)
        cmds.select([left, right], replace=True)
        mirror._auto_mirror_state.enabled = True

        rig_groups = {
            "testRig": {
                "controls": [left, right],
                "label": "testRig",
            }
        }
        with mock.patch.object(
            mirror, "_group_controls_by_rig", return_value=rig_groups
        ), mock.patch.object(
            mirror,
            "_load_or_build_basic_snapshot",
            return_value=(snapshot, True),
        ):
            mirror._auto_mirror_callback()
            after_first = (
                cmds.getAttr(left + ".translateX"),
                cmds.getAttr(right + ".translateX"),
            )
            mirror._auto_mirror_state.last_run -= 1.0
            mirror._auto_mirror_callback()
            after_second = (
                cmds.getAttr(left + ".translateX"),
                cmds.getAttr(right + ".translateX"),
            )

        self.assertEqual(after_first, after_second)
        self.assertEqual(after_first, (2.0, -3.0))

    def test_enabled_auto_mirror_recreates_a_missing_idle_job(self):
        jobs = set()
        next_job = [100]

        def fake_script_job(**kwargs):
            if "exists" in kwargs:
                return kwargs["exists"] in jobs
            if "kill" in kwargs:
                jobs.discard(kwargs["kill"])
                return None
            next_job[0] += 1
            jobs.add(next_job[0])
            return next_job[0]

        with mock.patch.object(
            mirror.cmds, "scriptJob", side_effect=fake_script_job
        ):
            mirror.enable_auto_mirror()
            old_job = mirror._auto_mirror_state.script_job_id
            self.assertIn(old_job, jobs)
            fake_script_job(kill=old_job, force=True)

            self.assertTrue(mirror.is_auto_mirror_enabled())
            new_job = mirror._auto_mirror_state.script_job_id
            self.assertNotEqual(old_job, new_job)
            self.assertIn(new_job, jobs)
            mirror.disable_auto_mirror()


if __name__ == "__main__":
    unittest.main()
