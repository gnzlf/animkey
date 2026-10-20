"""Bake Animation must resample only the active animation layer."""

import os
import sys
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

from AnimKey.buttons import bakeAnim
from AnimKey.core.animation_offset_session import resolve_target_curve_for_layer


class BakeAnimationLayerTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        for patch in (
            mock.patch.object(bakeAnim, "get_time_range", return_value=(1.0, 9.0)),
            mock.patch.object(bakeAnim.cmds, "channelBox", return_value=[]),
            mock.patch.object(bakeAnim.cmds, "inViewMessage"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def _key(plug, values, layer=None):
        for frame, value in values:
            kwargs = dict(time=frame, value=value)
            if layer:
                kwargs.update(animLayer=layer, noResolve=True)
            cmds.setKeyframe(plug, **kwargs)

    @staticmethod
    def _activate(layer):
        for candidate in cmds.ls(type="animLayer") or []:
            cmds.animLayer(candidate, edit=True, selected=False, preferred=False)
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)

    @staticmethod
    def _snapshot(curve):
        return (
            cmds.keyframe(curve, query=True, timeChange=True),
            cmds.keyframe(curve, query=True, valueChange=True),
        )

    def _scene(self, override=False):
        obj = cmds.createNode("transform", name="bakeControl")
        for attr in ("translateX", "translateY", "rotateZ", "scaleX"):
            self._key(obj + "." + attr, ((0.5, 2), (1, 10), (9, 20), (10.5, 30)))
        layer = cmds.animLayer("BakeAction", override=override)
        for attr in ("translateX", "translateY", "rotateZ", "scaleX"):
            plug = obj + "." + attr
            cmds.animLayer(layer, edit=True, attribute=plug)
            self._key(plug, ((0.5, 1), (1, 3), (9, 7), (10.5, 9)), layer)
        self._activate(layer)
        cmds.select(obj, replace=True)
        return obj, layer

    def _curve(self, obj, layer, attr="translateX"):
        curve = resolve_target_curve_for_layer(obj + "." + attr, layer)
        self.assertTrue(curve)
        return curve

    def _assert_samples(self, curve):
        self.assertEqual(
            cmds.keyframe(curve, query=True, timeChange=True),
            [0.5, 1.0, 3.0, 5.0, 7.0, 9.0, 10.5],
        )

    def test_additive_bake_preserves_base_and_other_layers(self):
        obj, layer = self._scene()
        other = cmds.animLayer("OtherAction")
        cmds.animLayer(other, edit=True, attribute=obj + ".translateX")
        self._key(obj + ".translateX", ((0.25, 2), (9.75, 5)), other)
        self._activate(layer)
        protected = [self._curve(obj, "BaseAnimation", attr) for attr in
                     ("translateX", "translateY", "rotateZ", "scaleX")]
        protected.append(self._curve(obj, other))
        before = {curve: self._snapshot(curve) for curve in protected}
        samples = [cmds.getAttr(obj + ".translateX", time=t) for t in (1, 3, 5, 7, 9)]

        self.assertTrue(bakeAnim.bake_factory_math(2))

        self._assert_samples(self._curve(obj, layer))
        for curve, snapshot in before.items():
            self.assertEqual(self._snapshot(curve), snapshot)
        for frame, value in zip((1, 3, 5, 7, 9), samples):
            self.assertAlmostEqual(cmds.getAttr(obj + ".translateX", time=frame), value)
        self.assertFalse(cmds.getAttr(other + ".mute"))

    def test_override_and_weighted_layers_keep_their_values(self):
        for override, weight, muted in ((True, 1.0, False), (False, 0.35, False),
                                        (True, 0.35, False), (False, 1.0, True)):
            with self.subTest(override=override, weight=weight, muted=muted):
                cmds.file(new=True, force=True)
                obj, layer = self._scene(override=override)
                cmds.setAttr(layer + ".weight", weight)
                cmds.setAttr(layer + ".mute", muted)
                curve = self._curve(obj, layer)
                raw = cmds.keyframe(curve, query=True, valueChange=True)
                samples = {
                    attr: [cmds.getAttr(obj + "." + attr, time=t) for t in (1, 3, 5, 7, 9)]
                    for attr in ("translateX", "translateY", "rotateZ", "scaleX")
                }

                self.assertTrue(bakeAnim.bake_factory_math(2))

                self._assert_samples(self._curve(obj, layer))
                after = cmds.keyframe(curve, query=True, valueChange=True)
                self.assertEqual([after[0], after[1], after[-2], after[-1]], raw)
                for attr, values in samples.items():
                    self._assert_samples(self._curve(obj, layer, attr))
                    for frame, value in zip((1, 3, 5, 7, 9), values):
                        self.assertAlmostEqual(cmds.getAttr(obj + "." + attr, time=frame), value)
                self.assertAlmostEqual(cmds.getAttr(layer + ".weight"), weight)
                self.assertEqual(cmds.getAttr(layer + ".mute"), muted)

    def test_selected_channel_leaves_other_channels_unchanged(self):
        obj, layer = self._scene()
        protected = self._curve(obj, layer, "translateY")
        before = self._snapshot(protected)
        with mock.patch.object(bakeAnim.cmds, "channelBox", return_value=["tx"]):
            self.assertTrue(bakeAnim.bake_factory_math(2))
        self._assert_samples(self._curve(obj, layer))
        self.assertEqual(self._snapshot(protected), before)

    def test_base_bake_keeps_other_layers(self):
        obj, layer = self._scene()
        protected = self._curve(obj, layer)
        before = self._snapshot(protected)
        self._activate("BaseAnimation")

        self.assertTrue(bakeAnim.bake_factory_math(2))

        self._assert_samples(self._curve(obj, "BaseAnimation"))
        self.assertEqual(self._snapshot(protected), before)

    def test_bake_without_layers_still_works(self):
        obj = cmds.createNode("transform")
        self._key(obj + ".tx", ((0.5, 1), (1, 2), (9, 10), (10.5, 12)))
        cmds.select(obj, replace=True)
        self.assertTrue(bakeAnim.bake_factory_math(2))
        self._assert_samples(self._curve(obj, None))

    def test_fractional_range_keeps_fractional_bake_samples(self):
        obj, layer = self._scene()
        with mock.patch.object(bakeAnim, "get_time_range", return_value=(1.5, 7.5)):
            self.assertTrue(bakeAnim.bake_factory_math(2))
        self.assertEqual(
            cmds.keyframe(self._curve(obj, layer), query=True, timeChange=True),
            [0.5, 1.0, 1.5, 3.5, 5.5, 7.5, 9.0, 10.5],
        )

    def test_empty_layer_does_not_write_to_base(self):
        obj, layer = self._scene()
        empty = cmds.animLayer("EmptyAction")
        self._activate(empty)
        curves = cmds.ls(type="animCurve")
        before = {curve: self._snapshot(curve) for curve in curves}
        self.assertFalse(bakeAnim.bake_factory_math(2))
        for curve, snapshot in before.items():
            self.assertEqual(self._snapshot(curve), snapshot)

    def test_nested_layer_preserves_parent_contribution(self):
        obj, layer = self._scene()
        parent = cmds.animLayer("ParentAction")
        cmds.animLayer(parent, edit=True, attribute=obj + ".translateX")
        self._key(obj + ".translateX", ((0.25, 2), (9.75, 5)), parent)
        cmds.animLayer(layer, edit=True, parent=parent)
        self._activate(layer)
        curve = self._curve(obj, parent)
        before = self._snapshot(curve)
        values = [cmds.getAttr(obj + ".translateX", time=t) for t in (1, 3, 5, 7, 9)]
        self.assertTrue(bakeAnim.bake_factory_math(2))
        self._assert_samples(self._curve(obj, layer))
        self.assertEqual(self._snapshot(curve), before)
        for frame, value in zip((1, 3, 5, 7, 9), values):
            self.assertAlmostEqual(cmds.getAttr(obj + ".translateX", time=frame), value)

    def test_blend_parent_fix_leaves_base_keys_unchanged(self):
        obj, layer = self._scene()
        plug = obj + ".blendParent1"
        cmds.addAttr(obj, longName="blendParent1", attributeType="double", keyable=True)
        self._key(plug, ((0.5, 0), (1, 0), (4.5, 1), (9, 1), (10.5, 0)))
        cmds.animLayer(layer, edit=True, attribute=plug)
        self._key(plug, ((1, 0), (4.5, 1), (9, 0)), layer)
        protected = self._curve(obj, "BaseAnimation", "blendParent1")
        before = self._snapshot(protected)
        self.assertTrue(bakeAnim.bake_factory_math(2))
        self.assertEqual(self._snapshot(protected), before)

    def test_locked_layer_does_not_fall_back_to_base(self):
        obj, layer = self._scene()
        curves = cmds.ls(type="animCurve")
        before = {curve: self._snapshot(curve) for curve in curves}
        cmds.animLayer(layer, edit=True, lock=True)
        self.assertFalse(bakeAnim.bake_factory_math(2))
        for curve, snapshot in before.items():
            self.assertEqual(self._snapshot(curve), snapshot)

    def test_preferred_layer_wins_over_stale_selection(self):
        obj, layer = self._scene()
        cmds.animLayer("BaseAnimation", edit=True, selected=True)
        self.assertTrue(bakeAnim.bake_factory_math(2))
        self._assert_samples(self._curve(obj, layer))

    def test_one_undo_restores_original_layer_keys(self):
        obj, layer = self._scene()
        curve = self._curve(obj, layer)
        before = self._snapshot(curve)
        cmds.flushUndo()
        self.assertTrue(bakeAnim.bake_factory_math(2))
        self._assert_samples(self._curve(obj, layer))
        cmds.undo()
        self.assertEqual(self._snapshot(curve), before)
        self.assertTrue(cmds.undoInfo(query=True, undoQueueEmpty=True))


if __name__ == "__main__":
    unittest.main()
