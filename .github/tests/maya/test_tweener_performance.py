import time
import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.core import animation_curve_transfer
from AnimKey.sliders import tweener


class TweenerPerformanceTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        tweener._restore_api_preview()
        if tweener._is_dragging:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass
        tweener._is_dragging = False
        tweener._tween_cache = []

    @staticmethod
    def _key(control, attribute, values):
        for frame, value in values:
            cmds.setKeyframe(
                control, attribute=attribute, time=frame, value=value
            )

    def test_unkeyed_preview_preserves_units_and_single_undo(self):
        control = cmds.createNode("transform", name="tweenControl")
        self._key(control, "translateX", ((0, 0), (10, 10)))
        self._key(control, "rotateY", ((0, 0), (10, 90)))
        cmds.currentTime(5)
        cmds.select(control, replace=True)
        cmds.flushUndo()

        data = tweener.prepare_tween_data(
            objs=[control], attrs=["translateX", "rotateY"]
        )
        self.assertEqual(len(data), 2)
        self.assertTrue(all(entry.get("api_curve_fn") for entry in data))
        with mock.patch.object(
            cmds, "keyframe", wraps=cmds.keyframe
        ) as keyframe_command, mock.patch.object(
            cmds, "setKeyframe", wraps=cmds.setKeyframe
        ) as set_key_command:
            tweener.execute(25)
            self.assertEqual(keyframe_command.call_count, 0)
            self.assertEqual(set_key_command.call_count, 0)
        self.assertAlmostEqual(
            cmds.keyframe(
                control + ".translateX", query=True,
                time=(5, 5), valueChange=True,
            )[0],
            2.5,
        )
        self.assertAlmostEqual(
            cmds.keyframe(
                control + ".rotateY", query=True,
                time=(5, 5), valueChange=True,
            )[0],
            22.5,
        )
        tweener.reset()

        cmds.undo()
        self.assertEqual(
            cmds.keyframe(
                control + ".translateX", query=True, timeChange=True
            ),
            [0.0, 10.0],
        )
        self.assertEqual(
            cmds.keyframe(
                control + ".rotateY", query=True, timeChange=True
            ),
            [0.0, 10.0],
        )

    def test_selected_animation_layer_curve_is_the_only_curve_edited(self):
        control = cmds.createNode("transform", name="layerTweenControl")
        plug = control + ".translateX"
        self._key(control, "translateX", ((0, 0), (10, 10)))
        layer = cmds.animLayer("TweenActionLayer", override=False)
        cmds.animLayer(layer, edit=True, attribute=plug)
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)
        animation_curve_transfer.set_key_on_layer(plug, 0, 2, layer_name=layer)
        animation_curve_transfer.set_key_on_layer(plug, 10, 6, layer_name=layer)
        layer_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name=layer
        )
        base_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name="BaseAnimation"
        )
        base_before = cmds.keyframe(base_curve, query=True, valueChange=True)

        cmds.currentTime(5)
        cmds.select(control, replace=True)
        data = tweener.prepare_tween_data(
            objs=[control], attrs=["translateX"]
        )
        self.assertEqual(data[0]["curve_target"], layer_curve)
        value_before = cmds.getAttr(plug)
        tweener.execute(100)
        # The test must observe the evaluated scene plug, not only the
        # animCurve value.  This catches preview paths that fail to dirty an
        # Animation Layer's animBlend network in a real Maya scene.
        self.assertNotAlmostEqual(cmds.getAttr(plug), value_before)
        tweener.reset()

        self.assertTrue(cmds.keyframe(
            layer_curve, query=True, time=(5, 5), valueChange=True
        ))
        self.assertEqual(
            cmds.keyframe(base_curve, query=True, valueChange=True),
            base_before,
        )

    def test_animation_layer_rotation_axes_map_to_distinct_curves(self):
        control = cmds.createNode("transform", name="rotationLayerControl")
        attributes = ("rotateX", "rotateY", "rotateZ")
        for index, attribute in enumerate(attributes):
            self._key(control, attribute, ((0, 0), (10, 20 + index)))
        layer = cmds.animLayer("RotationTweenLayer", override=False)
        cmds.animLayer(
            layer,
            edit=True,
            attribute=[control + "." + attr for attr in attributes],
        )
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)
        for index, attribute in enumerate(attributes):
            animation_curve_transfer.set_key_on_layer(
                control + "." + attribute, 0, index + 1, layer_name=layer
            )
            animation_curve_transfer.set_key_on_layer(
                control + "." + attribute, 10, (index + 1) * 5,
                layer_name=layer,
            )

        expected = {
            control + "." + attribute:
                animation_curve_transfer.resolve_anim_curve(
                    control + "." + attribute, layer_name=layer
                )
            for attribute in attributes
        }
        cmds.currentTime(5)
        cmds.select(control, replace=True)
        data = tweener.prepare_tween_data(objs=[control], attrs=attributes)
        actual = {
            entry["attr_full"]: entry["curve_target"] for entry in data
        }

        self.assertEqual(actual, expected)
        self.assertEqual(len(set(actual.values())), 3)
        tweener.execute(50)
        tweener.reset()
        for curve in expected.values():
            self.assertTrue(cmds.keyframe(
                curve, query=True, time=(5, 5), valueChange=True
            ))

    def test_animation_layer_uses_safe_fallback_when_fast_plug_map_misses(self):
        control = cmds.createNode("transform", name="layerFallbackControl")
        plug = control + ".translateX"
        self._key(control, "translateX", ((0, 0), (10, 10)))
        layer = cmds.animLayer("TweenFallbackLayer", override=False)
        cmds.animLayer(layer, edit=True, attribute=plug)
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)
        animation_curve_transfer.set_key_on_layer(plug, 0, 2, layer_name=layer)
        animation_curve_transfer.set_key_on_layer(plug, 10, 8, layer_name=layer)
        layer_curve = animation_curve_transfer.resolve_anim_curve(
            plug, layer_name=layer
        )

        cmds.currentTime(5)
        cmds.select(control, replace=True)
        original_layer_map = tweener._layer_curve_map
        tweener._layer_curve_map = lambda _layer: {}
        try:
            data = tweener.prepare_tween_data(
                objs=[control], attrs=["translateX"]
            )
        finally:
            tweener._layer_curve_map = original_layer_map

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["curve_target"], layer_curve)
        tweener.execute(50)
        tweener.reset()
        self.assertTrue(cmds.keyframe(
            layer_curve, query=True, time=(5, 5), valueChange=True
        ))

    def test_one_selected_key_on_each_graph_curve_stays_independent(self):
        first = cmds.createNode("transform", name="firstTweenControl")
        second = cmds.createNode("transform", name="secondTweenControl")
        self._key(first, "translateX", ((0, 0), (10, 3), (20, 10)))
        self._key(second, "rotateY", ((0, 0), (10, -5), (20, -20)))
        first_curve = cmds.keyframe(
            first + ".translateX", query=True, name=True
        )[0]
        second_curve = cmds.keyframe(
            second + ".rotateY", query=True, name=True
        )[0]
        cmds.select([first, second], replace=True)
        cmds.selectKey(clear=True)
        cmds.selectKey(first_curve, add=True, time=(10, 10))
        cmds.selectKey(second_curve, add=True, time=(10, 10))

        data = tweener.prepare_tween_data()
        self.assertEqual(
            {(entry["curve_target"], entry["frame"]) for entry in data},
            {(first_curve, 10.0), (second_curve, 10.0)},
        )
        tweener.execute(100)
        tweener.reset()

        self.assertEqual(
            cmds.keyframe(first_curve, query=True, valueChange=True),
            [0.0, 10.0, 10.0],
        )
        self.assertEqual(
            cmds.keyframe(second_curve, query=True, valueChange=True),
            [0.0, -20.0, -20.0],
        )

    def test_timeline_range_uses_curve_first_path_and_only_range_keys(self):
        control = cmds.createNode("transform", name="rangeTweenControl")
        self._key(control, "translateX", (
            (0, 0), (5, 5), (10, 10), (15, 15), (20, 20),
        ))
        cmds.currentTime(10)
        cmds.select(control, replace=True)
        context = {
            "time_range": (5.0, 15.0),
            "selected_channels": None,
            "graph_editor_selection": {},
            "ge_selection": {},
            "current_time": 10.0,
        }

        with mock.patch.object(
            tweener, "get_processing_context", return_value=context
        ), mock.patch.object(
            cmds, "listAttr", wraps=cmds.listAttr
        ) as list_attr_command:
            data = tweener.prepare_tween_data()

        self.assertEqual(
            [entry["frame"] for entry in data], [5.0, 10.0, 15.0]
        )
        self.assertEqual(list_attr_command.call_count, 0)
        tweener.execute(100)
        tweener.reset()
        self.assertEqual(
            list(map(float, cmds.keyframe(
                control + ".translateX", query=True, valueChange=True
            ) or [])),
            [0.0, 10.0, 15.0, 20.0, 20.0],
        )

    def test_existing_rotation_key_uses_unit_safe_api_preview(self):
        control = cmds.createNode("transform", name="apiRotationControl")
        self._key(control, "rotateY", ((0, 0), (10, 90), (20, 180)))
        cmds.currentTime(10)
        cmds.select(control, replace=True)
        cmds.flushUndo()

        data = tweener.prepare_tween_data(
            objs=[control], attrs=["rotateY"]
        )
        self.assertIsNotNone(data[0].get("api_curve_fn"))
        with mock.patch.object(
            cmds, "keyframe", wraps=cmds.keyframe
        ) as keyframe_command:
            tweener.execute(25)
            self.assertEqual(keyframe_command.call_count, 0)

        self.assertAlmostEqual(
            cmds.getAttr(control + ".rotateY"), 45.0, places=5
        )
        tweener.reset()
        cmds.undo()
        self.assertAlmostEqual(
            cmds.getAttr(control + ".rotateY"), 90.0, places=5
        )

    def test_api_preview_preserves_non_centimeter_linear_units(self):
        original_unit = cmds.currentUnit(query=True, linear=True)
        try:
            cmds.currentUnit(linear="m")
            control = cmds.createNode("transform", name="meterTweenControl")
            self._key(control, "translateX", ((0, 0), (10, 1), (20, 2)))
            cmds.currentTime(10)
            cmds.select(control, replace=True)
            cmds.flushUndo()

            tweener.prepare_tween_data(
                objs=[control], attrs=["translateX"]
            )
            tweener.execute(25)
            self.assertAlmostEqual(
                cmds.getAttr(control + ".translateX"), 0.5, places=5
            )
            tweener.reset()
            cmds.undo()
            self.assertAlmostEqual(
                cmds.getAttr(control + ".translateX"), 1.0, places=5
            )
        finally:
            cmds.currentUnit(linear=original_unit)

    def test_interrupted_api_preview_is_restored_before_next_drag(self):
        control = cmds.createNode("transform", name="interruptedTweenControl")
        self._key(control, "translateX", ((0, 0), (10, 10), (20, 20)))
        cmds.currentTime(10)
        cmds.select(control, replace=True)

        tweener.prepare_tween_data(
            objs=[control], attrs=["translateX"]
        )
        tweener.execute(100)
        self.assertAlmostEqual(
            cmds.getAttr(control + ".translateX"), 20.0, places=5
        )

        tweener.prepare_tween_data(
            objs=[control], attrs=["translateX"]
        )
        self.assertAlmostEqual(
            cmds.getAttr(control + ".translateX"), 10.0, places=5
        )
        tweener.reset()

    def test_large_rig_prepare_and_drag_are_bounded(self):
        controls = []
        attributes = (
            "translateX", "translateY", "translateZ",
            "rotateX", "rotateY", "rotateZ",
        )
        for control_index in range(60):
            control = cmds.createNode(
                "transform", name="perf_CTRL_{:03d}".format(control_index)
            )
            controls.append(control)
            for attr_index, attribute in enumerate(attributes):
                self._key(control, attribute, (
                    (0, 0.0),
                    (10, float(control_index + attr_index)),
                    (20, float((control_index * 2) + attr_index)),
                ))

        cmds.currentTime(10)
        cmds.select(controls, replace=True)
        started = time.perf_counter()
        data = tweener.prepare_tween_data()
        prepare_seconds = time.perf_counter() - started

        started = time.perf_counter()
        for percentage in range(0, 101, 10):
            tweener.execute(percentage)
        drag_seconds = time.perf_counter() - started
        tweener.reset()

        self.assertEqual(len(data), 360)
        self.assertLess(prepare_seconds, 1.0)
        self.assertLess(drag_seconds, 0.75)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
