"""Curve and tween slider modes on Graph Editor key selections.

Covers the selections that only the Graph Editor can make: separate key
groups on one curve, keys on curves of objects that are not selected in the
viewport (pinned controls, blendShape weights) and driven keys. Unselected
keys must keep their values and tangent types; one undo restores everything.
"""
import importlib
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds

from AnimKey.sliders import slider_utils

FRAMES = list(range(1, 25, 2))
VALUES = [0, 6, -3, 9, 2, 12, -4, 7, 1, 10, -2, 5]

CURVE_MODES = {
    "smooth": ("curve_smooth", "prepare_curve_data", -100),
    "rough": ("curve_smooth", "prepare_curve_data", 100),
    "wave": ("curve_wave", "prepare_curve_data", 100),
    "scale": ("curve_scale", "prepare_curve_data", 60),
    "linear": ("curve_linear", "prepare_curve_data", 100),
    "flat": ("curve_flat", "prepare_curve_data", 100),
    "ease_in": ("curve_ease_in_out", "prepare_curve_data", -98),
    "ease_out": ("curve_ease_in_out", "prepare_curve_data", 98),
    "noise": ("curve_noise", "prepare_curve_data", 100),
    "tween": ("tweener", "prepare_tween_data", 90),
    "push_pull": ("push_pull", "prepare_push_pull_data", 60),
    "blend_ease_in": ("blend_to_ease", "prepare_blend_data", -98),
    "blend_ease_out": ("blend_to_ease", "prepare_blend_data", 98),
    "neighbors": ("blend_to_neighbors", "prepare_blend_data", 100),
    "default": ("blend_to_default", "prepare_blend_data", 100),
    "infinity": ("blend_to_infinity", "prepare_blend_data", -100),
}


def run_mode(mode):
    module_name, prepare, value = CURVE_MODES[mode]
    module = importlib.import_module("AnimKey.sliders." + module_name)
    getattr(module, prepare)()
    module.execute(value * 0.5)
    module.execute(value)
    module.reset()


def inputs(curve):
    return [float(v) for v in slider_utils.curve_key_inputs(curve)]


def keys(curve):
    values = cmds.keyframe(curve, query=True, valueChange=True) or []
    return list(zip(inputs(curve), values))


def tangent_types(curve, frame):
    key_range = slider_utils.curve_key_range(curve, frame)
    return (cmds.keyTangent(curve, query=True, itt=True, **key_range),
            cmds.keyTangent(curve, query=True, ott=True, **key_range))


def select_keys(selection):
    cmds.selectKey(clear=True)
    for curve, frames in selection.items():
        for frame in frames:
            cmds.selectKey(curve, add=True, **slider_utils.curve_key_range(curve, frame))


def keyed(plug, scale=1.0):
    for frame, value in zip(FRAMES, VALUES):
        cmds.setKeyframe(plug, time=frame, value=value * scale)
    return cmds.listConnections(plug, source=True, destination=False)[0]


class GraphEditorSliderTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)

    def assert_mode(self, mode, curves, selection, expect_all_changed=True):
        before = {c: keys(c) for c in curves}
        tangents = {(c, f): tangent_types(c, f) for c in curves for f, _ in before[c]
                    if f not in selection.get(c, ())}
        run_mode(mode)
        after = {c: keys(c) for c in curves}
        for curve in curves:
            for (frame, old), (_, new) in zip(before[curve], after[curve]):
                if frame in selection.get(curve, ()):
                    if expect_all_changed:
                        self.assertNotAlmostEqual(old, new, places=6,
                                                  msg="%s %s %s not edited" % (mode, curve, frame))
                else:
                    self.assertAlmostEqual(old, new, places=6,
                                           msg="%s changed unselected %s %s" % (mode, curve, frame))
        for (curve, frame), types in tangents.items():
            self.assertEqual(tangent_types(curve, frame), types,
                             "%s changed tangents of unselected %s %s" % (mode, curve, frame))
        cmds.undo()
        self.assertEqual({c: keys(c) for c in curves}, before, "%s: one undo must restore" % mode)
        return before, after

    def assert_selected_moved(self, mode, curve, selection):
        before, after = self.assert_mode(mode, [curve], selection, expect_all_changed=False)
        moved = [frame for (frame, old), (_, new) in zip(before[curve], after[curve])
                 if frame in selection[curve] and abs(old - new) > 1e-6]
        self.assertTrue(moved, "%s did not edit any selected key on %s" % (mode, curve))

    def test_separate_key_groups_use_their_own_neighbours(self):
        node = cmds.polyCube(name="ctrl")[0]
        curve = keyed(node + ".tx")
        first, second = FRAMES[1:4], FRAMES[7:10]
        selection = {curve: set(first + second)}
        for mode in ("linear", "flat"):
            cmds.select(node)
            select_keys(selection)
            before, after = self.assert_mode(mode, [curve], selection, expect_all_changed=False)
            old = dict(before[curve]); new = dict(after[curve])
            for group in (first, second):
                index = FRAMES.index(group[0])
                if mode == "linear":
                    f0, f1 = FRAMES[index - 1], FRAMES[index + len(group)]
                    for frame in group:
                        line = old[f0] + (old[f1] - old[f0]) * (frame - f0) / float(f1 - f0)
                        self.assertAlmostEqual(new[frame], line, places=4)
                else:
                    average = sum(old[f] for f in group) / len(group)
                    for frame in group:
                        self.assertAlmostEqual(new[frame], average, places=4)

    def test_keys_of_objects_not_selected_in_viewport_are_edited(self):
        left = cmds.polyCube(name="armL")[0]
        right = cmds.polyCube(name="armR")[0]
        curves = [keyed(left + ".ty"), keyed(right + ".ty", -1)]
        selection = {c: set(FRAMES[3:7]) for c in curves}
        for mode in ("smooth", "flat", "ease_out", "tween", "blend_ease_in", "default"):
            cmds.select(left)
            select_keys(selection)
            self.assert_mode(mode, curves, selection)

    def test_blendshape_weight_keys(self):
        base = cmds.polyCube(name="face")[0]
        target = cmds.polyCube(name="smile")[0]
        blend = cmds.blendShape(target, base, name="faceBS")[0]
        for frame, value in zip(FRAMES, VALUES):
            cmds.setKeyframe(blend + ".w[0]", time=frame, value=abs(value) / 12.0)
        curve = cmds.listConnections(blend + ".w[0]", source=True, destination=False)[0]
        selection = {curve: set(FRAMES[3:7])}
        for mode in ("smooth", "noise", "ease_in", "tween", "push_pull"):
            cmds.select(base)
            select_keys(selection)
            self.assert_selected_moved(mode, curve, selection)

    def test_driven_keys(self):
        driver = cmds.polyCube(name="driver")[0]
        driven = cmds.polyCube(name="driven")[0]
        for index, value in enumerate(VALUES):
            cmds.setDrivenKeyframe(driven + ".ty", currentDriver=driver + ".tx",
                                   driverValue=index, value=value)
        curve = cmds.listConnections(driven + ".ty", source=True, destination=False)[0]
        self.assertTrue(slider_utils.curve_uses_float_input(curve))
        selection = {curve: set(inputs(curve)[3:7])}
        for mode in ("smooth", "wave", "scale", "ease_out", "tween", "neighbors"):
            cmds.select(driven)
            select_keys(selection)
            self.assert_selected_moved(mode, curve, selection)

    def test_every_mode_on_a_plain_selection(self):
        node = cmds.polyCube(name="ctrl")[0]
        curve = keyed(node + ".tx")
        selection = {curve: set(FRAMES[3:7])}
        for mode in CURVE_MODES:
            cmds.select(node)
            select_keys(selection)
            self.assert_mode(mode, [curve], selection,
                             expect_all_changed=mode not in ("linear", "push_pull"))

    def test_split_contiguous_frames(self):
        keys_ = [1.0, 3.0, 5.0, 7.0, 9.0, 11.0]
        self.assertEqual(slider_utils.split_contiguous_frames(keys_, [3, 5, 9, 11]),
                         [[3.0, 5.0], [9.0, 11.0]])
        self.assertEqual(slider_utils.split_contiguous_frames(keys_, [5]), [[5.0]])
        self.assertEqual(slider_utils.split_contiguous_frames(keys_, [1, 3, 5]), [[1.0, 3.0, 5.0]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
