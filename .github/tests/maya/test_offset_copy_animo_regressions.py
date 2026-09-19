import shutil
import tempfile
import unittest
from unittest import mock

from PySide2 import QtWidgets

import maya.standalone


_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons import animation_offset
from AnimKey.buttons import copyAnimation
from AnimKey.core import animation_curve_transfer as curve_transfer
from AnimKey.core.animation_offset_session import (
    OffsetSession,
    get_selected_animation_layer,
    resolve_target_curve_for_layer,
)
from AnimKey.core.executionGuard import animkey_execution


class OffsetAndCopyAnimoRegressionTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.data_dir = tempfile.mkdtemp(prefix="animkey_animo_regressions_")
        self.data_patch = mock.patch.object(
            copyAnimation,
            "get_user_data_folder",
            return_value=self.data_dir,
        )
        self.data_patch.start()
        copyAnimation._cleanup_buffer()
        copyAnimation._last_disk_mtime = 0

    def tearDown(self):
        self.data_patch.stop()
        copyAnimation._cleanup_buffer()
        shutil.rmtree(self.data_dir, ignore_errors=True)

    @staticmethod
    def _key(control, attr, frame, value, layer=None):
        kwargs = {
            "attribute": attr,
            "time": frame,
            "value": value,
        }
        if layer:
            kwargs["animLayer"] = layer
        cmds.setKeyframe(control, **kwargs)

    def test_offset_graph_key_range_has_priority(self):
        control = cmds.createNode("transform", name="offset_graph_ctrl")
        for frame in (1.0, 5.0, 10.0):
            self._key(control, "translateX", frame, frame)
        curve = cmds.keyframe(control + ".translateX", query=True, name=True)[0]
        cmds.selectKey(curve, replace=True, time=(5.0, 5.0))
        cmds.selectKey(curve, add=True, time=(10.0, 10.0))

        with mock.patch.object(
            animation_offset,
            "_graph_editor_is_active",
            return_value=True,
        ):
            self.assertEqual(
                animation_offset._selected_offset_time_range(),
                [5.0, 10.0],
            )

    def test_preferred_animation_layer_wins_over_stale_selection(self):
        first = cmds.animLayer("OldSelectedLayer")
        second = cmds.animLayer("PreferredKeyingLayer")
        for layer in cmds.ls(type="animLayer") or []:
            cmds.animLayer(layer, edit=True, selected=False, preferred=False)
        cmds.animLayer(first, edit=True, selected=True, preferred=False)
        cmds.animLayer(second, edit=True, selected=False, preferred=True)

        self.assertEqual(get_selected_animation_layer(), second)

    def test_offset_custom_facial_attribute_stays_on_selected_layer(self):
        control = cmds.createNode("transform", name="brow_ctrl")
        cmds.addAttr(
            control,
            longName="browRaise",
            attributeType="double",
            keyable=True,
        )
        for frame, value in ((1.0, 0.0), (5.0, 1.0), (10.0, 2.0)):
            self._key(control, "browRaise", frame, value)
        layer = cmds.animLayer("FacialOffsetLayer")
        cmds.animLayer(layer, edit=True, attribute=control + ".browRaise")
        for frame, value in ((1.0, 3.0), (5.0, 4.0), (10.0, 5.0)):
            self._key(control, "browRaise", frame, value, layer=layer)

        base_curve = resolve_target_curve_for_layer(
            control + ".browRaise",
            "BaseAnimation",
        )
        layer_curve = resolve_target_curve_for_layer(control + ".browRaise", layer)
        base_before = cmds.keyframe(base_curve, query=True, valueChange=True)
        layer_before = cmds.keyframe(layer_curve, query=True, valueChange=True)

        session = OffsetSession()
        try:
            session.start([control], (1.0, 10.0), target_layer=layer)
            cmds.currentTime(5.0)
            cmds.keyframe(
                layer_curve,
                edit=True,
                time=(5.0, 5.0),
                valueChange=layer_before[1] + 2.0,
            )
            session.flush_now(reason="facial_custom_attr")
            self.assertEqual(
                cmds.keyframe(base_curve, query=True, valueChange=True),
                base_before,
            )
            self.assertEqual(
                cmds.keyframe(layer_curve, query=True, valueChange=True),
                [value + 2.0 for value in layer_before],
            )
        finally:
            session.stop(commit=False)

    def test_copy_uses_exact_selected_graph_keys(self):
        source = cmds.createNode("transform", name="graph_copy_source")
        for frame, value in ((1.0, 10.0), (5.0, 50.0), (10.0, 100.0)):
            self._key(source, "translateX", frame, value)
        curve = cmds.keyframe(source + ".translateX", query=True, name=True)[0]
        cmds.select(source)
        cmds.selectKey(curve, replace=True, time=(5.0, 5.0))
        cmds.selectKey(curve, add=True, time=(10.0, 10.0))

        with animkey_execution("maya_test"):
            copyAnimation.copy_animation()

        ctrl_key = copyAnimation.get_control_storage_key(source)
        payload = copyAnimation._anim_buffer[ctrl_key]["translateX"]
        self.assertEqual(payload["keyframes"], [5.0, 10.0])
        self.assertEqual(payload["values"], [50.0, 100.0])
        self.assertEqual(payload["clip_range"], [5.0, 10.0])

    def test_graph_copy_replace_preserves_keys_outside_selected_span(self):
        source = cmds.createNode("transform", name="graph_replace_source")
        target = cmds.createNode("transform", name="graph_replace_target")
        for frame, value in ((1.0, 10.0), (5.0, 50.0), (10.0, 100.0)):
            self._key(source, "translateX", frame, value)
        for frame, value in ((1.0, -1.0), (5.0, -5.0), (7.0, -7.0), (10.0, -10.0), (15.0, -15.0)):
            self._key(target, "translateX", frame, value)

        curve = cmds.keyframe(source + ".translateX", query=True, name=True)[0]
        cmds.select(source)
        cmds.selectKey(curve, replace=True, time=(5.0, 5.0))
        cmds.selectKey(curve, add=True, time=(10.0, 10.0))
        with animkey_execution("maya_test"):
            copyAnimation.copy_animation()

        cmds.select(target)
        with animkey_execution("maya_test"):
            copyAnimation.paste_animation()

        self.assertEqual(
            cmds.keyframe(target + ".translateX", query=True, timeChange=True),
            [1.0, 5.0, 10.0, 15.0],
        )
        self.assertEqual(
            cmds.keyframe(target + ".translateX", query=True, valueChange=True),
            [-1.0, 50.0, 100.0, -15.0],
        )

    def test_selected_graph_curve_identifies_nonpreferred_source_layer(self):
        source = cmds.createNode("transform", name="layer_graph_source")
        self._key(source, "translateX", 1.0, 1.0)
        layer = cmds.animLayer("GraphSourceLayer")
        cmds.animLayer(layer, edit=True, attribute=source + ".translateX")
        for frame, value in ((1.0, 2.0), (5.0, 4.0)):
            self._key(source, "translateX", frame, value, layer=layer)
        layer_curve = curve_transfer.resolve_anim_curve(
            source + ".translateX",
            layer_name=layer,
        )
        self.assertTrue(layer_curve)

        for item in cmds.ls(type="animLayer") or []:
            cmds.animLayer(item, edit=True, selected=False, preferred=False)
        cmds.animLayer("BaseAnimation", edit=True, selected=True, preferred=True)
        cmds.select(source)
        cmds.selectKey(layer_curve, replace=True, time=(1.0, 1.0))
        cmds.selectKey(layer_curve, add=True, time=(5.0, 5.0))

        with animkey_execution("maya_test"):
            copyAnimation.copy_animation()

        ctrl_key = copyAnimation.get_control_storage_key(source)
        payload = copyAnimation._anim_buffer[ctrl_key]["translateX"]
        self.assertEqual(payload["source_layer"], layer)
        self.assertEqual(payload["keyframes"], [1.0, 5.0])

    def test_curve_destination_keeps_axis_through_pair_blend(self):
        control = cmds.createNode("transform", name="pair_blend_target")
        pair_blend = cmds.createNode("pairBlend", name="axis_pair_blend")
        for axis in "XYZ":
            cmds.connectAttr(
                "{}.outTranslate{}".format(pair_blend, axis),
                "{}.translate{}".format(control, axis),
                force=True,
            )

        curves = {}
        for axis in "XYZ":
            curve = cmds.createNode("animCurveTL", name="axis_curve_" + axis)
            cmds.connectAttr(
                curve + ".output",
                "{}.inTranslate{}1".format(pair_blend, axis),
                force=True,
            )
            cmds.setKeyframe(curve, time=1.0, value=1.0)
            curves[axis] = curve

        for axis, curve in curves.items():
            self.assertEqual(
                curve_transfer.driven_plugs_for_curve(curve),
                ["{}.translate{}".format(control, axis)],
            )

    def test_two_selected_rig_roots_receive_complete_assignments(self):
        source_names = ["SourceRoot|arm_ctrl", "SourceRoot|face_ctrl"]
        selected_roots = []
        expected_targets = set()
        for namespace in ("rigOne", "rigTwo"):
            cmds.namespace(add=namespace)
            root = cmds.createNode("transform", name=namespace + ":Root")
            selected_roots.append(cmds.ls(root, long=True)[0])
            for control_name in ("arm_ctrl", "face_ctrl"):
                control = cmds.createNode(
                    "transform",
                    name=namespace + ":" + control_name,
                    parent=root,
                )
                expected_targets.add(cmds.ls(control, long=True)[0])

        assignments = copyAnimation.resolve_animation_target_assignments(
            source_names,
            selected=selected_roots,
        )
        self.assertEqual(len(assignments), 4)
        self.assertEqual({target for _source, target in assignments}, expected_targets)


if __name__ == "__main__":
    unittest.main()
