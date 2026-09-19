import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.core import animation_curve_transfer
from AnimKey.sliders import (
    blend_to_default,
    blend_to_neighbors,
    curve_ease_in_out,
    curve_flat,
    curve_linear,
    curve_noise,
    curve_scale,
    curve_smooth,
    curve_wave,
    mirror_blend,
    push_pull,
    tweener_world_space,
)


class SliderAnimoCompatibilityTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    @staticmethod
    def _select_layer(layer):
        for current in cmds.ls(type="animLayer") or []:
            try:
                cmds.animLayer(
                    current,
                    edit=True,
                    selected=current == layer,
                    preferred=current == layer,
                )
            except Exception:
                pass

    @staticmethod
    def _base_keys(control, attribute, values):
        for frame, value in values:
            cmds.setKeyframe(
                control,
                attribute=attribute,
                time=(frame, frame),
                value=value,
            )

    def test_empty_active_layer_receives_slider_key_without_editing_base(self):
        control = cmds.createNode("transform", name="emptyLayerControl")
        plug = control + ".translateX"
        self._base_keys(control, "translateX", ((0, 0), (10, 10), (20, 20)))
        base_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name="BaseAnimation"
        )
        base_before = cmds.keyframe(base_curve, query=True, valueChange=True)
        layer = cmds.animLayer("EmptySliderLayer", override=False)
        self._select_layer(layer)
        cmds.currentTime(10)
        cmds.select(control, replace=True)

        blend_to_default.prepare_blend_data(
            objs=[control], attrs=["translateX"]
        )
        blend_to_default.execute(50)
        self.assertAlmostEqual(cmds.getAttr(plug), 5.0, places=5)
        blend_to_default.execute(100)
        blend_to_default.reset()

        layer_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name=layer
        )
        self.assertTrue(layer_curve)
        self.assertEqual(
            cmds.keyframe(base_curve, query=True, valueChange=True),
            base_before,
        )
        self.assertEqual(
            cmds.keyframe(layer_curve, query=True, timeChange=True),
            [10.0],
        )
        self.assertAlmostEqual(cmds.getAttr(plug), 0.0, places=5)

    def test_implicit_object_workflow_only_collects_animated_channels(self):
        control = cmds.createNode("transform", name="animatedChannelsOnly")
        self._base_keys(control, "translateX", ((0, 0), (20, 20)))
        cmds.currentTime(10)
        cmds.select(control, replace=True)

        cache = blend_to_default.prepare_blend_data()

        self.assertEqual(
            {entry["attr_full"] for entry in cache.values()},
            {control + ".translateX"},
        )
        blend_to_default.execute(50)
        blend_to_default.reset()
        self.assertFalse(cmds.keyframe(
            control + ".translateY", query=True, name=True
        ))
        self.assertFalse(cmds.keyframe(
            control + ".rotateY", query=True, name=True
        ))

    def test_noop_slider_does_not_create_an_accidental_key(self):
        control = cmds.createNode("transform", name="noOpSliderControl")
        cmds.currentTime(10)
        cmds.select(control, replace=True)

        blend_to_default.prepare_blend_data(
            objs=[control], attrs=["translateX"]
        )
        blend_to_default.execute(50)
        blend_to_default.reset()

        self.assertFalse(cmds.keyframe(
            control + ".translateX", query=True, name=True
        ))

    def test_explicit_unanimated_curve_slider_creates_only_the_requested_key(self):
        modules = (
            (curve_smooth, -50),
            (curve_wave, 60),
            (curve_scale, 50),
            (curve_linear, 100),
            (curve_flat, 100),
            (curve_ease_in_out, 50),
            (curve_noise, 50),
        )
        for module, percentage in modules:
            with self.subTest(slider=module.__name__):
                cmds.file(new=True, force=True)
                control = cmds.createNode(
                    "transform", name="explicitCurveControl"
                )
                cmds.setAttr(control + ".translateX", 3.0)
                cmds.currentTime(5)
                cmds.select(control, replace=True)

                module.prepare_curve_data(
                    objs=[control], attrs=["translateX"]
                )
                module.execute(percentage)
                module.reset()

                self.assertEqual(
                    cmds.keyframe(
                        control + ".translateX",
                        query=True,
                        timeChange=True,
                    ),
                    [5.0],
                )
                self.assertFalse(cmds.keyframe(
                    control + ".translateY", query=True, name=True
                ))

    def test_one_graph_key_on_different_layer_curves_stays_independent(self):
        modules = (
            (blend_to_default, "prepare_blend_data", 75),
            (blend_to_neighbors, "prepare_blend_data", 75),
            (curve_linear, "prepare_curve_data", 75),
            (push_pull, "prepare_push_pull_data", 75),
        )
        for module, prepare_name, percentage in modules:
            with self.subTest(slider=module.__name__):
                cmds.file(new=True, force=True)
                first = cmds.createNode("transform", name="firstSliderControl")
                second = cmds.createNode("transform", name="secondSliderControl")
                first_plug = first + ".translateX"
                second_plug = second + ".rotateY"
                self._base_keys(first, "translateX", ((0, 0), (10, 8), (20, 2)))
                self._base_keys(second, "rotateY", ((0, 0), (10, -12), (20, -4)))
                layer = cmds.animLayer("SelectedKeysLayer", override=False)
                cmds.animLayer(
                    layer,
                    edit=True,
                    attribute=[first_plug, second_plug],
                )
                self._select_layer(layer)
                for plug, values in (
                    (first_plug, ((0, 1), (10, 5), (20, 3))),
                    (second_plug, ((0, -1), (10, -6), (20, -2))),
                ):
                    for frame, value in values:
                        animation_curve_transfer.set_key_on_layer(
                            plug, frame, value, layer_name=layer
                        )
                first_curve = animation_curve_transfer.resolve_anim_curve(
                    first_plug, layer_name=layer
                )
                second_curve = animation_curve_transfer.resolve_anim_curve(
                    second_plug, layer_name=layer
                )
                first_before = cmds.keyframe(
                    first_curve, query=True, valueChange=True
                )
                second_before = cmds.keyframe(
                    second_curve, query=True, valueChange=True
                )
                cmds.select([first, second], replace=True)
                cmds.selectKey(clear=True)
                cmds.selectKey(first_curve, add=True, time=(10, 10))
                cmds.selectKey(second_curve, add=True, time=(10, 10))
                cmds.currentTime(10)

                cache = getattr(module, prepare_name)()
                self.assertEqual(
                    {(entry["attr_full"], entry["frame"])
                     for entry in cache.values()},
                    {(first_plug, 10.0), (second_plug, 10.0)},
                )
                module.execute(percentage)
                module.reset()

                first_after = cmds.keyframe(
                    first_curve, query=True, valueChange=True
                )
                second_after = cmds.keyframe(
                    second_curve, query=True, valueChange=True
                )
                self.assertEqual(first_after[0], first_before[0])
                self.assertEqual(first_after[2], first_before[2])
                self.assertEqual(second_after[0], second_before[0])
                self.assertEqual(second_after[2], second_before[2])

    def test_timeline_range_edits_only_active_layer_keys_in_range(self):
        control = cmds.createNode("transform", name="rangeLayerControl")
        plug = control + ".translateX"
        values = ((0, 0), (5, 5), (10, 10), (15, 15), (20, 20))
        self._base_keys(control, "translateX", values)
        layer = cmds.animLayer("RangeSliderLayer", override=False)
        cmds.animLayer(layer, edit=True, attribute=plug)
        self._select_layer(layer)
        for frame, value in values:
            animation_curve_transfer.set_key_on_layer(
                plug, frame, value * 0.5, layer_name=layer
            )
        layer_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name=layer
        )
        base_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name="BaseAnimation"
        )
        before_layer = cmds.keyframe(layer_curve, query=True, valueChange=True)
        before_base = cmds.keyframe(base_curve, query=True, valueChange=True)
        context = {
            "time_range": (5.0, 15.0),
            "selected_channels": None,
            "graph_editor_selection": {},
            "ge_selection": {},
            "current_time": 10.0,
            "explicit_attributes": False,
        }
        cmds.select(control, replace=True)

        with mock.patch.object(
            blend_to_default, "get_processing_context", return_value=context
        ):
            cache = blend_to_default.prepare_blend_data()
            blend_to_default.execute(100)
            blend_to_default.reset()

        self.assertEqual(
            sorted(entry["frame"] for entry in cache.values()),
            [5.0, 10.0, 15.0],
        )
        after_layer = cmds.keyframe(layer_curve, query=True, valueChange=True)
        self.assertEqual(after_layer[0], before_layer[0])
        self.assertEqual(after_layer[4], before_layer[4])
        self.assertEqual(
            cmds.keyframe(base_curve, query=True, valueChange=True),
            before_base,
        )

    def test_worldspace_slider_keys_active_layer_and_preserves_base(self):
        parent = cmds.createNode("transform", name="sliderParent")
        control = cmds.createNode(
            "transform", name="worldSliderControl", parent=parent
        )
        plug = control + ".translateX"
        self._base_keys(control, "translateX", ((0, 0), (10, 10), (20, 20)))
        self._base_keys(parent, "rotateY", ((0, 0), (10, 30), (20, 0)))
        layer = cmds.animLayer("WorldSliderLayer", override=False)
        cmds.animLayer(layer, edit=True, attribute=plug)
        self._select_layer(layer)
        for frame, value in ((0, 0), (10, 15), (20, 20)):
            animation_curve_transfer.set_key_on_layer(
                plug, frame, value, layer_name=layer
            )
        layer_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name=layer
        )
        base_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name="BaseAnimation"
        )
        base_before = cmds.keyframe(base_curve, query=True, valueChange=True)
        layer_before = cmds.keyframe(layer_curve, query=True, valueChange=True)
        cmds.currentTime(10)
        cmds.select(control, replace=True)
        cmds.selectKey(clear=True)
        cmds.selectKey(layer_curve, add=True, time=(10, 10))
        world_before = cmds.xform(
            control, query=True, worldSpace=True, translation=True
        )

        cache = tweener_world_space.prepare_tween_data()
        tweener_world_space.execute(100)
        world_after = cmds.xform(
            control, query=True, worldSpace=True, translation=True
        )
        tweener_world_space.reset()

        self.assertEqual(len(cache), 1)
        self.assertNotEqual(world_after, world_before)
        self.assertNotEqual(
            cmds.keyframe(layer_curve, query=True, valueChange=True),
            layer_before,
        )
        self.assertEqual(
            cmds.keyframe(base_curve, query=True, valueChange=True),
            base_before,
        )

    def test_mirror_blend_writes_to_the_active_layer(self):
        control = cmds.createNode("transform", name="mirrorSliderControl")
        plug = control + ".translateX"
        self._base_keys(control, "translateX", ((0, 0), (10, 10), (20, 20)))
        base_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name="BaseAnimation"
        )
        base_before = cmds.keyframe(base_curve, query=True, valueChange=True)
        layer = cmds.animLayer("MirrorSliderLayer", override=False)
        self._select_layer(layer)
        cmds.currentTime(10)
        cmds.select(control, replace=True)

        with mock.patch.object(
            mirror_blend, "_load_or_build_basic_snapshot", return_value=({}, False)
        ), mock.patch.object(
            mirror_blend, "get_opposite_control", return_value=None
        ), mock.patch.object(
            mirror_blend, "find_opposite_name", return_value=None
        ), mock.patch.object(
            mirror_blend,
            "compute_mirror_values",
            return_value={"translateX": -10.0},
        ):
            mirror_blend.prepare_mirror_blend_data([control])
            mirror_blend.execute(100)
            mirror_blend.reset()

        layer_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name=layer
        )
        self.assertTrue(layer_curve)
        self.assertEqual(
            cmds.keyframe(base_curve, query=True, valueChange=True),
            base_before,
        )
        self.assertEqual(
            cmds.keyframe(layer_curve, query=True, timeChange=True),
            [10.0],
        )
        self.assertAlmostEqual(cmds.getAttr(plug), -10.0, places=5)


if __name__ == "__main__":
    unittest.main()
