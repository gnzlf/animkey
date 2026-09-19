"""Regression tests for Time Slider-limited animation cleanup."""

import os
import sys
import unittest

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds


ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class AnimCleanerTimeRangeTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        from AnimKey.buttons import animCleaner

        self.cleaner = animCleaner
        self.original_time_range = animCleaner._selected_time_slider_range

    def tearDown(self):
        self.cleaner._selected_time_slider_range = self.original_time_range

    @staticmethod
    def _key_times(node):
        return [
            float(time)
            for time in (cmds.keyframe(node, attribute="translateX", query=True, timeChange=True) or [])
        ]

    @staticmethod
    def _set_keys(node, values):
        for frame, value in values:
            cmds.setKeyframe(node, attribute="translateX", time=frame, value=value)

    @staticmethod
    def _select_keys(node, attribute, times):
        cmds.selectKey(clear=True)
        for key_time in times:
            cmds.selectKey(
                node,
                attribute=attribute,
                time=(key_time, key_time),
                add=True,
            )

    def test_static_cleanup_cuts_only_selected_range(self):
        node = cmds.spaceLocator(name="staticRangeSource")[0]
        self._set_keys(node, [(1, 2), (5, 2), (10, 2), (15, 2), (20, 3)])
        cmds.select(node, replace=True)
        self.cleaner._selected_time_slider_range = lambda: (5.0, 15.0)

        self.cleaner.delete_static_channels()

        self.assertEqual(self._key_times(node), [1.0, 20.0])

    def test_redundant_cleanup_preserves_redundant_keys_outside_range(self):
        node = cmds.spaceLocator(name="redundantRangeSource")[0]
        self._set_keys(node, [(1, 0), (5, 0), (10, 0), (15, 0), (20, 0)])
        cmds.select(node, replace=True)
        self.cleaner._selected_time_slider_range = lambda: (5.0, 10.0)

        self.cleaner.delete_redundant_keys()

        self.assertEqual(self._key_times(node), [1.0, 15.0, 20.0])

    def test_subframe_cleanup_preserves_subframes_outside_range(self):
        node = cmds.spaceLocator(name="subframeRangeSource")[0]
        self._set_keys(node, [(1, 0), (5.5, 1), (8.25, 2), (12.5, 3)])
        cmds.select(node, replace=True)
        self.cleaner._selected_time_slider_range = lambda: (5.0, 10.0)

        self.cleaner.delete_sub_frame_keys()

        self.assertEqual(self._key_times(node), [1.0, 12.5])

    def test_timeline_range_works_when_ui_was_left_on_selected_keys(self):
        node = cmds.spaceLocator(name="selectedKeysFallbackSource")[0]
        self._set_keys(node, [(1, 0), (5, 0), (10, 0), (15, 0), (20, 0)])
        cmds.select(node, replace=True)
        cmds.selectKey(clear=True)
        self.cleaner._selected_time_slider_range = lambda: (5.0, 10.0)

        self.cleaner.delete_redundant_keys(selection_option_idx=6)

        self.assertEqual(self._key_times(node), [1.0, 15.0, 20.0])

    def test_graph_key_selection_automatically_overrides_object_scope(self):
        node = cmds.spaceLocator(name="graphRedundantSource")[0]
        self._set_keys(node, [(1, 0), (5, 0), (10, 0), (15, 0), (20, 0)])
        cmds.select(node, replace=True)
        self._select_keys(node, "translateX", [5, 10])

        self.cleaner.delete_redundant_keys(selection_option_idx=1)

        self.assertEqual(self._key_times(node), [1.0, 15.0, 20.0])

    def test_graph_selection_is_tracked_per_curve(self):
        node = cmds.spaceLocator(name="perCurveSelectionSource")[0]
        for attribute in ("translateX", "translateY"):
            for frame in (1, 5, 10, 15):
                cmds.setKeyframe(node, attribute=attribute, time=frame, value=0)
        cmds.select(node, replace=True)
        cmds.selectKey(clear=True)
        cmds.selectKey(
            node, attribute="translateX", time=(5, 5), add=True
        )
        cmds.selectKey(
            node, attribute="translateY", time=(10, 10), add=True
        )

        self.cleaner.delete_redundant_keys(selection_option_idx=1)

        tx_times = cmds.keyframe(
            node, attribute="translateX", query=True, timeChange=True
        ) or []
        ty_times = cmds.keyframe(
            node, attribute="translateY", query=True, timeChange=True
        ) or []
        self.assertEqual(list(map(float, tx_times)), [1.0, 10.0, 15.0])
        self.assertEqual(list(map(float, ty_times)), [1.0, 5.0, 15.0])

    def test_graph_keys_take_priority_over_timeline_range(self):
        node = cmds.spaceLocator(name="graphPrioritySource")[0]
        self._set_keys(node, [(1, 0), (5, 0), (10, 0), (15, 0), (20, 0)])
        cmds.select(node, replace=True)
        self.cleaner._selected_time_slider_range = lambda: (5.0, 10.0)
        self._select_keys(node, "translateX", [15])

        self.cleaner.delete_redundant_keys(selection_option_idx=1)

        self.assertEqual(self._key_times(node), [1.0, 5.0, 10.0, 20.0])

    def test_static_cleanup_cuts_only_selected_graph_keys(self):
        node = cmds.spaceLocator(name="graphStaticSource")[0]
        self._set_keys(node, [(1, 0), (5, 2), (10, 2), (15, 4)])
        cmds.select(node, replace=True)
        self._select_keys(node, "translateX", [5, 10])

        self.cleaner.delete_static_channels(selection_option_idx=1)

        self.assertEqual(self._key_times(node), [1.0, 15.0])

    def test_subframe_cleanup_cuts_only_selected_graph_keys(self):
        node = cmds.spaceLocator(name="graphSubframeSource")[0]
        self._set_keys(node, [(1, 0), (5.5, 1), (8.25, 2), (12.5, 3)])
        cmds.select(node, replace=True)
        self._select_keys(node, "translateX", [8.25])

        self.cleaner.delete_sub_frame_keys(selection_option_idx=1)

        self.assertEqual(self._key_times(node), [1.0, 5.5, 12.5])


if __name__ == "__main__":
    unittest.main()
