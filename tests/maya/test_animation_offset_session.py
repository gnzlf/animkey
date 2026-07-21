import unittest

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.core.animation_offset_session import (
    OffsetSession,
    get_selected_animation_layer,
    resolve_target_curve_for_layer,
)


class AnimationOffsetSessionTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.control = cmds.createNode("transform", name="offset_ctrl")
        for frame, value in ((1, 0.0), (10, 5.0), (20, 10.0)):
            cmds.setKeyframe(
                self.control,
                attribute="translateX",
                time=frame,
                value=value,
            )

    def tearDown(self):
        session = OffsetSession.active_instance()
        if session is not None:
            session.stop(commit=False)

    def _values(self, attr="translateX"):
        return cmds.keyframe(
            self.control,
            attribute=attr,
            query=True,
            valueChange=True,
        )

    def _curve_values(self, curve):
        return [
            float(value)
            for value in (cmds.keyframe(curve, query=True, valueChange=True) or [])
        ]

    def _select_layer(self, selected_layer):
        for layer in cmds.ls(type="animLayer") or []:
            cmds.animLayer(layer, edit=True, selected=False, preferred=False)
        cmds.animLayer(
            selected_layer,
            edit=True,
            selected=True,
            preferred=True,
        )

    def _create_keyed_layer(self, name, override=False):
        layer = cmds.animLayer(name, override=override)
        cmds.animLayer(
            layer,
            edit=True,
            attribute="{}.translateX".format(self.control),
        )
        self._select_layer(layer)
        for frame, value in ((1.0, 1.0), (10.0, 2.0), (20.0, 3.0)):
            cmds.setKeyframe(
                self.control,
                attribute="translateX",
                time=frame,
                value=value,
                animLayer=layer,
            )
        curve = resolve_target_curve_for_layer(
            "{}.translateX".format(self.control),
            layer,
        )
        self.assertTrue(curve)
        return layer, curve

    def test_translation_is_applied_once(self):
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)

        cmds.currentTime(10.0)
        cmds.setKeyframe(
            self.control,
            attribute="translateX",
            time=10.0,
            value=7.0,
        )
        self.assertTrue(session.mark_dirty_for_test(self.control, "translateX", 10.0))
        session.flush_now()

        self.assertEqual(self._values(), [2.0, 7.0, 12.0])

        session.flush_now()
        self.assertEqual(self._values(), [2.0, 7.0, 12.0])

    def test_partial_range_offsets_every_key_inside_only(self):
        cmds.setKeyframe(
            self.control,
            attribute="translateX",
            time=30.0,
            value=15.0,
        )
        session = OffsetSession()
        session.start([self.control], (9.0, 21.0), target_layer=None)

        cmds.currentTime(10.0)
        cmds.setKeyframe(
            self.control,
            attribute="translateX",
            time=10.0,
            value=8.0,
        )
        session.mark_dirty_for_test(self.control, "translateX", 10.0)
        session.flush_now()

        self.assertEqual(self._values(), [0.0, 8.0, 13.0, 15.0])

    def test_relative_offset_preserves_tangent_types(self):
        cmds.keyTangent(
            self.control,
            attribute="translateX",
            edit=True,
            time=(1.0, 1.0),
            inTangentType="linear",
            outTangentType="step",
        )
        before_in = cmds.keyTangent(
            self.control,
            attribute="translateX",
            query=True,
            inTangentType=True,
        )
        before_out = cmds.keyTangent(
            self.control,
            attribute="translateX",
            query=True,
            outTangentType=True,
        )

        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)
        cmds.currentTime(10.0)
        cmds.setKeyframe(
            self.control,
            attribute="translateX",
            time=10.0,
            value=7.0,
        )
        session.flush_now(reason="preserve_tangents")

        self.assertEqual(
            cmds.keyTangent(
                self.control,
                attribute="translateX",
                query=True,
                inTangentType=True,
            ),
            before_in,
        )
        self.assertEqual(
            cmds.keyTangent(
                self.control,
                attribute="translateX",
                query=True,
                outTangentType=True,
            ),
            before_out,
        )

    def test_many_keys_in_range_are_all_offset(self):
        cmds.cutKey(self.control, attribute="translateX", clear=True)
        for frame in range(1, 101):
            cmds.setKeyframe(
                self.control,
                attribute="translateX",
                time=float(frame),
                value=float(frame),
            )

        session = OffsetSession()
        session.start([self.control], (10.0, 90.0), target_layer=None)
        cmds.currentTime(50.0)
        cmds.setKeyframe(
            self.control,
            attribute="translateX",
            time=50.0,
            value=55.0,
        )
        session.mark_dirty_for_test(self.control, "translateX", 50.0)
        session.flush_now()

        values = self._values()
        self.assertEqual(values[:9], [float(frame) for frame in range(1, 10)])
        self.assertEqual(values[9:90], [float(frame + 5) for frame in range(10, 91)])
        self.assertEqual(values[90:], [float(frame) for frame in range(91, 101)])

    def test_multiple_controls_are_independent(self):
        second = cmds.createNode("transform", name="offset_ctrl_two")
        for frame, value in ((1, 20.0), (10, 25.0), (20, 30.0)):
            cmds.setKeyframe(
                second,
                attribute="translateX",
                time=frame,
                value=value,
            )

        session = OffsetSession()
        session.start([self.control, second], (1.0, 20.0), target_layer=None)
        cmds.currentTime(10.0)
        cmds.setKeyframe(
            self.control,
            attribute="translateX",
            time=10.0,
            value=7.0,
        )
        cmds.setKeyframe(
            second,
            attribute="translateX",
            time=10.0,
            value=29.0,
        )
        session.mark_dirty_for_test(self.control, "translateX", 10.0)
        session.mark_dirty_for_test(second, "translateX", 10.0)
        session.flush_now()

        self.assertEqual(self._values(), [2.0, 7.0, 12.0])
        self.assertEqual(
            cmds.keyframe(second, attribute="translateX", query=True, valueChange=True),
            [4.0 + value for value in (20.0, 25.0, 30.0)],
        )

    def test_additive_layer_offsets_only_its_curve(self):
        layer, curve = self._create_keyed_layer("OffsetAdditive")
        base_curve = resolve_target_curve_for_layer(
            "{}.translateX".format(self.control),
            "BaseAnimation",
        )
        base_before = self._curve_values(base_curve)
        layer_before = self._curve_values(curve)

        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=layer)
        cmds.currentTime(10.0)
        cmds.keyframe(
            curve,
            edit=True,
            time=(10.0, 10.0),
            valueChange=layer_before[1] + 2.0,
        )
        session.mark_dirty_for_test(self.control, "translateX", 10.0)
        session.flush_now()

        self.assertEqual(self._curve_values(base_curve), base_before)
        self.assertEqual(
            self._curve_values(curve),
            [value + 2.0 for value in layer_before],
        )

    def test_weighted_additive_layer_converts_composite_edit_to_curve_delta(self):
        layer, curve = self._create_keyed_layer("OffsetWeightedAdditive")
        cmds.setAttr(layer + ".weight", 0.25)
        layer_before = self._curve_values(curve)

        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=layer)
        cmds.currentTime(10.0)
        composite_before = cmds.getAttr(self.control + ".translateX")
        cmds.setAttr(self.control + ".translateX", composite_before + 2.0)
        session.flush_now(reason="weighted_additive_drag")

        self.assertEqual(
            self._curve_values(curve),
            [value + 8.0 for value in layer_before],
        )

    def test_override_layer_offsets_only_its_curve(self):
        layer, curve = self._create_keyed_layer("OffsetOverride", override=True)
        base_curve = resolve_target_curve_for_layer(
            "{}.translateX".format(self.control),
            "BaseAnimation",
        )
        base_before = self._curve_values(base_curve)
        layer_before = self._curve_values(curve)

        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=layer)
        cmds.currentTime(10.0)
        cmds.keyframe(
            curve,
            edit=True,
            time=(10.0, 10.0),
            valueChange=layer_before[1] - 3.0,
        )
        session.mark_dirty_for_test(self.control, "translateX", 10.0)
        session.flush_now()

        self.assertEqual(self._curve_values(base_curve), base_before)
        self.assertEqual(
            self._curve_values(curve),
            [value - 3.0 for value in layer_before],
        )

    def test_weighted_override_layer_converts_composite_edit_to_curve_delta(self):
        layer, curve = self._create_keyed_layer(
            "OffsetWeightedOverride",
            override=True,
        )
        cmds.setAttr(layer + ".weight", 0.25)
        layer_before = self._curve_values(curve)

        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=layer)
        cmds.currentTime(10.0)
        composite_before = cmds.getAttr(self.control + ".translateX")
        cmds.setAttr(self.control + ".translateX", composite_before - 1.5)
        session.flush_now(reason="weighted_override_drag")

        self.assertEqual(
            self._curve_values(curve),
            [value - 6.0 for value in layer_before],
        )

    def test_weighted_additive_rotation_uses_layer_curve_units(self):
        cmds.file(new=True, force=True)
        self.control = cmds.createNode("transform", name="layer_rotate_ctrl")
        for frame, value in ((1.0, 170.0), (10.0, 179.0), (20.0, 190.0)):
            cmds.setKeyframe(
                self.control,
                attribute="rotateX",
                time=frame,
                value=value,
            )
        layer = cmds.animLayer("OffsetRotateLayer")
        cmds.animLayer(
            layer,
            edit=True,
            attribute="{}.rotateX".format(self.control),
        )
        self._select_layer(layer)
        for frame, value in ((1.0, 172.0), (10.0, -179.0), (20.0, 192.0)):
            cmds.setKeyframe(
                self.control,
                attribute="rotateX",
                time=frame,
                value=value,
                animLayer=layer,
            )
        cmds.setAttr(layer + ".weight", 0.5)
        curve = resolve_target_curve_for_layer(
            "{}.rotateX".format(self.control),
            layer,
        )
        before = self._curve_values(curve)

        cmds.currentTime(10.0)
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=layer)
        composite_before = cmds.getAttr(self.control + ".rotateX")
        cmds.setAttr(self.control + ".rotateX", composite_before + 2.0)
        session.flush_now(reason="weighted_rotation_drag")

        for actual, expected in zip(
            self._curve_values(curve),
            [value + 4.0 for value in before],
        ):
            self.assertAlmostEqual(actual, expected)

    def test_base_animation_with_layer_offsets_only_base_curve(self):
        layer, layer_curve = self._create_keyed_layer("OffsetAdditive")
        base_curve = resolve_target_curve_for_layer(
            "{}.translateX".format(self.control),
            "BaseAnimation",
        )
        base_before = self._curve_values(base_curve)
        layer_before = self._curve_values(layer_curve)

        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer="BaseAnimation")
        cmds.currentTime(10.0)
        cmds.keyframe(
            base_curve,
            edit=True,
            time=(10.0, 10.0),
            valueChange=base_before[1] + 4.0,
        )
        session.mark_dirty_for_test(self.control, "translateX", 10.0)
        session.flush_now()

        self.assertEqual(
            self._curve_values(base_curve),
            [value + 4.0 for value in base_before],
        )
        self.assertEqual(self._curve_values(layer_curve), layer_before)

    def test_base_animation_composite_drag_offsets_only_base_curve(self):
        _layer, layer_curve = self._create_keyed_layer("OffsetAboveBase")
        base_curve = resolve_target_curve_for_layer(
            "{}.translateX".format(self.control),
            "BaseAnimation",
        )
        base_before = self._curve_values(base_curve)
        layer_before = self._curve_values(layer_curve)

        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer="BaseAnimation")
        cmds.currentTime(10.0)
        composite_before = cmds.getAttr(self.control + ".translateX")
        cmds.setAttr(self.control + ".translateX", composite_before + 2.0)
        session.flush_now(reason="base_layer_drag")

        self.assertEqual(
            self._curve_values(base_curve),
            [value + 2.0 for value in base_before],
        )
        self.assertEqual(self._curve_values(layer_curve), layer_before)

    def test_selected_animation_layer_is_the_frozen_target(self):
        first_layer, first_curve = self._create_keyed_layer("OffsetLayerOne")
        second_layer, second_curve = self._create_keyed_layer("OffsetLayerTwo")
        self._select_layer(first_layer)
        self.assertEqual(get_selected_animation_layer(), first_layer)

        first_before = self._curve_values(first_curve)
        second_before = self._curve_values(second_curve)
        session = OffsetSession()
        session.start(
            [self.control],
            (1.0, 20.0),
            target_layer=get_selected_animation_layer(),
        )

        self._select_layer(second_layer)
        cmds.currentTime(10.0)
        cmds.keyframe(
            first_curve,
            edit=True,
            time=(10.0, 10.0),
            valueChange=first_before[1] + 2.0,
        )
        session.flush_now(reason="frozen_layer")

        self.assertEqual(
            self._curve_values(first_curve),
            [value + 2.0 for value in first_before],
        )
        self.assertEqual(self._curve_values(second_curve), second_before)

    def test_set_attr_callback_drives_offset(self):
        cmds.currentTime(10.0)
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)

        cmds.setAttr(self.control + ".translateX", 7.0)
        self.assertEqual(session.debug_snapshot()["dirty_count"], 1)
        session.flush_now()

        self.assertEqual(self._values(), [2.0, 7.0, 12.0])

    def test_time_changes_do_not_modify_curves(self):
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)

        before = self._values()
        for frame in range(1, 21):
            cmds.currentTime(float(frame))
            session._on_time_changed()

        self.assertEqual(self._values(), before)

    def test_start_stop_cleans_runtime_state(self):
        for _index in range(10):
            session = OffsetSession()
            session.start([self.control], (1.0, 20.0), target_layer=None)
            self.assertTrue(session.debug_snapshot()["callback_count"] >= 1)
            session.stop(commit=False)
            snapshot = session.debug_snapshot()
            self.assertEqual(snapshot["callback_count"], 0)
            self.assertEqual(snapshot["script_job_count"], 0)
            self.assertFalse(snapshot["undo_chunk_open"])

    def test_slider_changes_are_grouped_per_attr(self):
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)

        cmds.setKeyframe(self.control, attribute="translateX", time=10.0, value=8.0)
        cmds.setKeyframe(self.control, attribute="translateX", time=20.0, value=13.0)
        applied = session.apply_slider_changes([
            {
                "attr_full": "{}.translateX".format(self.control),
                "frame": 10.0,
                "original_value": 5.0,
                "new_value": 8.0,
            },
            {
                "attr_full": "{}.translateX".format(self.control),
                "frame": 20.0,
                "original_value": 10.0,
                "new_value": 13.0,
            },
        ])

        self.assertTrue(applied)
        self.assertEqual(self._values(), [3.0, 8.0, 13.0])

    def test_scanned_multi_key_edit_protects_every_driver_key(self):
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)

        cmds.currentTime(10.0)
        cmds.keyframe(
            self.control,
            attribute="translateX",
            edit=True,
            time=(10.0, 10.0),
            valueChange=8.0,
        )
        cmds.keyframe(
            self.control,
            attribute="translateX",
            edit=True,
            time=(20.0, 20.0),
            valueChange=13.0,
        )
        session.commit_dirty(reason="multi_key_slider", scan_changed_keys=True)

        self.assertEqual(self._values(), [3.0, 8.0, 13.0])

        session.commit_dirty(reason="multi_key_slider", scan_changed_keys=True)
        self.assertEqual(self._values(), [3.0, 8.0, 13.0])

    def test_anim_curve_callback_detects_direct_key_edit(self):
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)
        curve = resolve_target_curve_for_layer(
            "{}.translateX".format(self.control),
            None,
        )

        cmds.currentTime(10.0)
        cmds.keyframe(
            curve,
            edit=True,
            time=(10.0, 10.0),
            valueChange=7.0,
        )
        session.flush_now(reason="direct_curve_edit")

        self.assertEqual(self._values(), [2.0, 7.0, 12.0])

    def test_tangent_only_edit_does_not_leave_undo_chunk_open(self):
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)

        cmds.keyTangent(
            self.control,
            attribute="translateX",
            edit=True,
            time=(10.0, 10.0),
            inTangentType="linear",
            outTangentType="linear",
        )
        session.flush_now(reason="tangent_only")

        self.assertFalse(session.debug_snapshot()["undo_chunk_open"])

    def test_undo_redo_rebaselines_without_pending_work(self):
        cmds.undoInfo(state=True)
        cmds.flushUndo()
        cmds.currentTime(10.0)
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)

        cmds.setAttr(self.control + ".translateX", 7.0)
        session.flush_now(reason="undoable_drag")
        session.close_undo_chunk()
        offset_values = self._values()

        cmds.undo()
        session.finish_undo_guard()
        self.assertEqual(self._values(), [0.0, 5.0, 10.0])
        self.assertEqual(session.debug_snapshot()["dirty_count"], 0)

        cmds.redo()
        session.finish_undo_guard()
        self.assertEqual(self._values(), offset_values)
        self.assertEqual(session.debug_snapshot()["dirty_count"], 0)

    def test_rotation_wrap_does_not_apply_360_delta(self):
        cmds.file(new=True, force=True)
        ctrl = cmds.createNode("transform", name="rotate_ctrl")
        self.control = ctrl
        for frame, value in ((1, 170.0), (10, 179.0), (20, 190.0)):
            cmds.setKeyframe(ctrl, attribute="rotateX", time=frame, value=value)

        session = OffsetSession()
        session.start([ctrl], (1.0, 20.0), target_layer=None)
        cmds.currentTime(10.0)
        cmds.setKeyframe(ctrl, attribute="rotateX", time=10.0, value=-179.0)
        session.mark_dirty_for_test(ctrl, "rotateX", 10.0)
        session.flush_now()

        values = self._values(attr="rotateX")
        self.assertAlmostEqual(values[0], 172.0)
        self.assertAlmostEqual(values[1], -179.0)
        self.assertAlmostEqual(values[2], 192.0)


if __name__ == "__main__":
    unittest.main()
