"""Link clipboards preserve evaluated relationships and explicit time scopes."""

import os
import tempfile
import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds
from AnimKey.buttons import linkObjects as link
from AnimKey.core.executionGuard import animkey_execution


class LinkRangeTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        link.disable_auto_link()
        link._state.relative_data = {}
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patch = mock.patch.object(link, "_get_link_data_folder", return_value=self.folder.name)
        patch.start()
        self.addCleanup(patch.stop)
        context = animkey_execution("Link regression")
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        cmds.playbackOptions(animationStartTime=0, animationEndTime=6, minTime=2, maxTime=4)
        self.main = cmds.createNode("transform", name="driver")
        self.follow = cmds.createNode("transform", name="follower")
        for time, driver, follower in ((0, 0, 1), (6, 12, 19)):
            cmds.setKeyframe(self.main, attribute="translateX", time=time, value=driver)
            cmds.setKeyframe(self.follow, attribute="translateX", time=time, value=follower)
        cmds.keyTangent([self.main, self.follow], inTangentType="linear", outTangentType="linear")
        cmds.currentTime(3)
        cmds.select([self.follow, self.main])

    def _x(self, node, time):
        return cmds.getAttr(node + ".translateX", time=time)

    def test_playback_copy_samples_relationships_and_not_highlighted_range(self):
        with mock.patch.object(link.worldspace, "_get_selected_time_range", return_value=(0, 1)):
            data = link.copy_link_playback_range()
        self.assertEqual(data["times"], [2, 3, 4])
        offsets = data["relative_samples"]["|follower"]
        self.assertAlmostEqual(offsets["2"][12], 3)
        self.assertAlmostEqual(offsets["4"][12], 5)
        self.assertEqual(cmds.currentTime(query=True), 3)

    def test_all_animation_includes_frames_outside_playback_and_subframe_keys(self):
        cmds.setKeyframe(self.main, attribute="translateX", time=1.5, value=3)
        data = link.copy_link_all_animation()
        self.assertEqual(data["times"][0], 0)
        self.assertEqual(data["times"][-1], 6)
        self.assertIn(1.5, data["times"])
        # Move the driver after copying: the follower must use the new driver
        # motion plus the relationship recorded separately at each frame.
        cmds.keyframe(self.main, edit=True, relative=True, valueChange=10)
        self.assertGreater(link.paste_link_all_animation(), 0)
        for time in (0, 2, 4, 6):
            self.assertAlmostEqual(self._x(self.follow, time), 10 + 1 + time * 3)

    def test_selected_copy_and_paste_do_not_touch_outside_highlight(self):
        with mock.patch.object(link.worldspace, "_get_selected_time_range", return_value=(1, 3)):
            data = link.copy_link_selected_range()
        self.assertEqual(data["times"], [1, 2, 3])
        cmds.keyframe(self.main, edit=True, relative=True, valueChange=10)
        before = {time: self._x(self.follow, time) for time in (0, 6)}
        with mock.patch.object(link.worldspace, "_get_selected_time_range", return_value=(2, 3)):
            link.paste_link_selected_range()
        self.assertEqual(cmds.keyframe(self.follow, attribute="translateX", query=True, timeChange=True), [0, 2, 3, 6])
        self.assertEqual({time: self._x(self.follow, time) for time in (0, 6)}, before)
        self.assertAlmostEqual(self._x(self.follow, 2), 17)

    def test_playback_paste_filters_animation_samples(self):
        link.copy_link_all_animation()
        cmds.keyframe(self.main, edit=True, relative=True, valueChange=10)
        link.paste_link_playback_range()
        self.assertEqual(cmds.keyframe(self.follow, attribute="translateX", query=True, timeChange=True), [0, 2, 3, 4, 6])

    def test_frame_clipboard_survives_animation_copy_and_repeats_fixed_offset(self):
        link.copy_link_frame()  # Offset at frame 3 is 4.
        link.copy_link_all_animation()
        cmds.currentTime(5)
        link.paste_link_frame()
        self.assertAlmostEqual(self._x(self.follow, 5), self._x(self.main, 5) + 4)
        os.remove(link._get_link_data_file(animation=True))
        link._state.relative_data = {}
        link.paste_link_playback_range()
        for time in (2, 3, 4):
            self.assertAlmostEqual(self._x(self.follow, time), self._x(self.main, time) + 4)

    def test_range_paste_restores_time_selection_and_is_one_undo(self):
        link.copy_link_frame()
        original_keys = cmds.keyframe(self.follow, query=True, timeChange=True)
        original_values = cmds.keyframe(self.follow, query=True, valueChange=True)
        selection = cmds.ls(selection=True, long=True)
        cmds.flushUndo()
        link.paste_link_playback_range()
        self.assertEqual(cmds.currentTime(query=True), 3)
        self.assertEqual(cmds.ls(selection=True, long=True), selection)
        cmds.undo()
        self.assertEqual(cmds.keyframe(self.follow, query=True, timeChange=True), original_keys)
        self.assertEqual(cmds.keyframe(self.follow, query=True, valueChange=True), original_values)

    def test_selected_operations_require_visible_highlight(self):
        with mock.patch.object(link.worldspace, "_get_selected_time_range", return_value=None):
            self.assertIsNone(link.copy_link_selected_range())
            self.assertIsNone(link.paste_link_selected_range())
        self.assertFalse(os.path.exists(link._get_link_data_file(animation=True)))

    def test_legacy_static_clipboard_remains_usable(self):
        data = link.copy_link_frame()
        legacy = {"main_obj": data["main_obj"], "relative_matrices": data["relative_matrices"]}
        os.remove(link._get_link_data_file())
        link._state.relative_data = legacy
        self.assertGreater(link.paste_link_playback_range(), 0)

    def test_new_frame_copy_takes_precedence_over_older_animation_clipboard(self):
        link.copy_link_all_animation()
        frame = link.copy_link_frame()
        # Test both in-memory state and persistence after a module/session reload.
        for reload in (False, True):
            if reload:
                link._state.relative_data = {}
            link.paste_link_playback_range()
            offset = frame["relative_matrices"]["|follower"][12]
            for time in (2, 3, 4):
                self.assertAlmostEqual(self._x(self.follow, time), self._x(self.main, time) + offset)

    def test_exception_restores_time_and_closes_undo_chunk(self):
        link.copy_link_frame()
        with mock.patch.object(link, "_apply_link_at_frame", side_effect=RuntimeError("test failure")), \
                mock.patch.object(cmds, "undoInfo", wraps=cmds.undoInfo) as undo:
            with self.assertRaises(RuntimeError):
                link.paste_link_playback_range()
        self.assertEqual(cmds.currentTime(query=True), 3)
        self.assertTrue(undo.call_args_list[-1].kwargs.get("closeChunk"))

    def test_highlight_end_is_exclusive_and_hidden_range_is_ignored(self):
        with mock.patch.object(cmds, "about", return_value=False), \
                mock.patch.object(link.worldspace.mel, "eval", return_value="timeControl1"), \
                mock.patch.object(cmds, "timeControl", side_effect=[True, [2, 5]]):
            self.assertEqual(link.worldspace._get_selected_time_range(), (2, 4))
        with mock.patch.object(cmds, "about", return_value=False), \
                mock.patch.object(link.worldspace.mel, "eval", return_value="timeControl1"), \
                mock.patch.object(cmds, "timeControl", return_value=False):
            self.assertIsNone(link.worldspace._get_selected_time_range())

    def test_rotated_driver_and_nested_followers_keep_full_relative_matrices(self):
        child = cmds.createNode("transform", name="child", parent=self.follow)
        cmds.setAttr(child + ".translate", 1, 2, 3)
        cmds.setAttr(child + ".rotate", 12, 15, 20)
        cmds.select([child, self.follow, self.main])
        data = link.copy_link_frame()
        cmds.currentTime(4)
        cmds.setAttr(self.main + ".rotateY", 75)
        link.paste_link_playback_range()
        for frame in (2, 3, 4):
            cmds.currentTime(frame)
            driver = link.om.MMatrix(cmds.xform(self.main, query=True, matrix=True, worldSpace=True))
            for node, relative in data["relative_matrices"].items():
                expected = link._matrix_values(link.om.MMatrix(relative) * driver)
                actual = cmds.xform(node, query=True, matrix=True, worldSpace=True)
                self.assertLess(max(abs(a - b) for a, b in zip(actual, expected)), 1e-5)

    def test_worldspace_uses_sparse_keys_and_parent_copy_keeps_per_frame_sampling(self):
        path = os.path.join(self.folder.name, "worldspace.json")
        for node in (self.main, self.follow):
            cmds.setKeyframe(node, attribute="translateX", time=3, insert=True)
        with mock.patch.object(link.worldspace, "_get_animation_data_file", return_value=path), \
                mock.patch.object(link.worldspace, "_get_selected_time_range", return_value=(1, 3)):
            for parent_copy, world_copy, expected in (
                (link.copy_link_all_animation, link.worldspace.copy_worldspace_all_animation, [0, 1, 2, 3, 4, 5, 6]),
                (link.copy_link_playback_range, link.worldspace.copy_worldspace_playback_range, [2, 3, 4]),
                (link.copy_link_selected_range, link.worldspace.copy_worldspace_selected_range, [1, 2, 3]),
            ):
                with self.subTest(operation=parent_copy.__name__):
                    parent = parent_copy()
                    world = world_copy()
                    self.assertEqual(parent["times"], expected)
                    for source in world["sources"]:
                        world_times = [0, 3, 6] if parent_copy == link.copy_link_all_animation else [3]
                        self.assertEqual(sorted(float(time) for time in source["samples"]), world_times)
                    self.assertEqual(cmds.currentTime(query=True), 3)

    def test_worldspace_playback_paste_leaves_external_keys_intact(self):
        path = os.path.join(self.folder.name, "worldspace.json")
        cmds.select(self.follow)
        for time in (2.5, 3.5):
            cmds.setKeyframe(self.follow, attribute="translateX", time=time, insert=True)
        with mock.patch.object(link.worldspace, "_get_animation_data_file", return_value=path):
            link.worldspace.copy_worldspace_playback_range()
            cmds.keyframe(self.follow, edit=True, relative=True, valueChange=20)
            before = {time: self._x(self.follow, time) for time in (0, 6)}
            result = link.worldspace.paste_worldspace_playback_range()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual({time: self._x(self.follow, time) for time in (0, 6)}, before)
        self.assertEqual(sorted(set(cmds.keyframe(self.follow, query=True, timeChange=True))), [0, 2.5, 3.5, 6])
        for time in (2.5, 3.5):
            self.assertAlmostEqual(self._x(self.follow, time), 1 + time * 3)

    def test_fractional_playback_and_autokey_state_are_preserved(self):
        cmds.playbackOptions(minTime=1.5, maxTime=3.25)
        link.copy_link_frame()
        cmds.autoKeyframe(state=True)
        try:
            link.paste_link_playback_range()
            self.assertTrue(cmds.autoKeyframe(query=True, state=True))
            times = cmds.keyframe(self.follow, attribute="translateX", query=True, timeChange=True)
            self.assertEqual(times, [0, 1.5, 2.5, 3.25, 6])
        finally:
            cmds.autoKeyframe(state=False)

    def test_all_animation_paste_is_not_clipped_by_new_playback_bounds(self):
        link.copy_link_all_animation()
        cmds.playbackOptions(animationStartTime=2, animationEndTime=4, minTime=2, maxTime=4)
        cmds.keyframe(self.main, edit=True, relative=True, valueChange=10)
        self.assertEqual(link.paste_link_all_animation(), 7)
        for time in (0, 6):
            self.assertAlmostEqual(self._x(self.follow, time), 11 + time * 3)

    def test_frame_paste_does_not_replace_active_animation_clipboard(self):
        link.copy_link_frame()
        animation = link.copy_link_all_animation()
        link.paste_link_frame()
        self.assertIs(link._state.relative_data, animation)


if __name__ == "__main__":
    unittest.main()
