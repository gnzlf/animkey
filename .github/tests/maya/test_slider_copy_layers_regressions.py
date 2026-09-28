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

from AnimKey.buttons import copyAnimation
from AnimKey.core import animation_curve_transfer as curve_transfer
from AnimKey.core.executionGuard import animkey_execution
from AnimKey.sliders import curve_smooth
from AnimKey.sliders import curve_scale
from AnimKey.sliders import curve_linear
from AnimKey.sliders import curve_flat
from AnimKey.sliders import curve_ease_in_out
from AnimKey.sliders import curve_noise
from AnimKey.sliders import curve_wave
from AnimKey.sliders import blend_to_neighbors
from AnimKey.sliders import slider_utils


class SliderAndCopyLayerRegressionTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.data_dir = tempfile.mkdtemp(prefix="animkey_slider_copy_")
        self.data_patch = mock.patch.object(
            copyAnimation, "get_user_data_folder", return_value=self.data_dir
        )
        self.data_patch.start()
        copyAnimation._cleanup_buffer()
        copyAnimation._last_disk_mtime = 0

    def tearDown(self):
        self.data_patch.stop()
        copyAnimation._cleanup_buffer()
        shutil.rmtree(self.data_dir, ignore_errors=True)

    @staticmethod
    def _set_keys(control, values, layer=None):
        for frame, value in values:
            kwargs = {"attribute": "translateX", "time": frame, "value": value}
            if layer:
                kwargs["animLayer"] = layer
            cmds.setKeyframe(control, **kwargs)

    def _make_layered_control(self, namespace, name="copy_ctrl"):
        cmds.namespace(add=namespace)
        control = cmds.createNode("transform", name="{}:{}".format(namespace, name))
        self._set_keys(control, ((1.0, 1.0), (5.0, 5.0), (10.0, 10.0)))
        layer = cmds.animLayer("FaceLayer")
        cmds.animLayer(layer, edit=True, attribute=control + ".translateX")
        self._set_keys(control, ((1.0, 2.0), (5.0, 8.0), (10.0, 14.0)), layer=layer)
        return control, layer

    def test_curve_slider_edits_every_selected_layer_curve_on_one_attribute(self):
        control, layer = self._make_layered_control("source")
        base_curve = curve_transfer.resolve_anim_curve(
            control + ".translateX", layer_name="BaseAnimation"
        )
        layer_curve = curve_transfer.resolve_anim_curve(
            control + ".translateX", layer_name=layer
        )
        cmds.select(control)
        cmds.selectKey(base_curve, replace=True, time=(5.0, 5.0))
        cmds.selectKey(layer_curve, add=True, time=(5.0, 5.0))

        curve_smooth.prepare_curve_data()
        self.assertEqual(len(curve_smooth._curve_data_cache), 2)
        curve_smooth.execute(-100.0)
        curve_smooth.reset()

        self.assertEqual(
            cmds.keyframe(base_curve, query=True, time=(5.0, 5.0), valueChange=True),
            [16.0 / 3.0],
        )
        self.assertEqual(
            cmds.keyframe(layer_curve, query=True, time=(5.0, 5.0), valueChange=True),
            [8.0 / 3.0],
        )

    def test_curve_modes_accept_keys_from_multiple_graph_editor_curves(self):
        control = cmds.createNode("transform", name="multi_curve_ctrl")
        for attr, values in (
                ("translateX", ((1.0, 0.0), (5.0, 3.0), (10.0, 9.0))),
                ("translateY", ((1.0, 2.0), (5.0, 7.0), (10.0, 11.0)))):
            for frame, value in values:
                cmds.setKeyframe(control, attribute=attr, time=frame, value=value)
        curves = [
            cmds.keyframe(control + ".translateX", query=True, name=True)[0],
            cmds.keyframe(control + ".translateY", query=True, name=True)[0],
        ]
        cmds.select(control)
        cmds.selectKey(curves[0], replace=True, time=(5.0, 5.0))
        cmds.selectKey(curves[1], add=True, time=(5.0, 5.0))

        for module in (
                curve_smooth, curve_scale, curve_linear, curve_flat,
                curve_ease_in_out, curve_noise, curve_wave):
            module.prepare_curve_data()
            self.assertEqual(len(module._curve_data_cache), 2)
            module.execute(25.0)
            module.reset()

        cmds.selectKey(curves[0], replace=True, time=(5.0, 5.0))
        cmds.selectKey(curves[1], add=True, time=(5.0, 5.0))
        blend_to_neighbors.prepare_blend_data()
        self.assertEqual(len(blend_to_neighbors._blend_neighbors_data_cache), 2)
        blend_to_neighbors.execute(25.0)
        blend_to_neighbors.reset()

    def test_copy_and_paste_preserves_base_and_animation_layer_curves(self):
        source, layer = self._make_layered_control("source")
        source_base = curve_transfer.resolve_anim_curve(
            source + ".translateX", layer_name="BaseAnimation"
        )
        source_layer = curve_transfer.resolve_anim_curve(
            source + ".translateX", layer_name=layer
        )
        expected_base_values = cmds.keyframe(source_base, query=True, valueChange=True)
        expected_layer_values = cmds.keyframe(source_layer, query=True, valueChange=True)
        cmds.select(source)
        with animkey_execution("maya_test"):
            copyAnimation.copy_animation()

        source_key = copyAnimation.get_control_storage_key(source)
        stored = copyAnimation._anim_buffer[source_key]["translateX"]
        payloads = dict(copyAnimation._iter_curve_payloads(stored))
        self.assertEqual(set(payloads), {"BaseAnimation", layer})
        self.assertEqual(payloads["BaseAnimation"]["values"], expected_base_values)
        self.assertEqual(payloads[layer]["values"], expected_layer_values)

        cmds.file(new=True, force=True)
        cmds.namespace(add="target")
        target = cmds.createNode("transform", name="target:copy_ctrl")
        cmds.select(target)
        with animkey_execution("maya_test"):
            copyAnimation.paste_animation()

        self.assertTrue(cmds.objExists(layer))
        target_base = curve_transfer.resolve_anim_curve(
            target + ".translateX", layer_name="BaseAnimation"
        )
        target_layer = curve_transfer.resolve_anim_curve(
            target + ".translateX", layer_name=layer
        )
        self.assertEqual(
            cmds.keyframe(target_base, query=True, valueChange=True), expected_base_values
        )
        self.assertEqual(
            cmds.keyframe(target_layer, query=True, valueChange=True), expected_layer_values
        )


if __name__ == "__main__":
    unittest.main()
