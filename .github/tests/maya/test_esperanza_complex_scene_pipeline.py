"""Optional production-scene regression coverage.

Run locally with ANIMKEY_COMPLEX_SCENE and ANIMKEY_ESPERANZA_RIG pointing to
the saved shot and the clean rig master. The test never saves either file.
"""

import os
import shutil
import sys
import tempfile
import time
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
from AnimKey.buttons import animCrash
from AnimKey.core import animation_curve_transfer as curve_transfer
from AnimKey.core.executionGuard import animkey_execution
from AnimKey.sliders import blend_to_neighbors
from AnimKey.sliders import curve_smooth


SCENE_PATH = os.environ.get("ANIMKEY_COMPLEX_SCENE")
RIG_PATH = os.environ.get("ANIMKEY_ESPERANZA_RIG")


@unittest.skipUnless(
    SCENE_PATH and RIG_PATH and os.path.exists(SCENE_PATH) and os.path.exists(RIG_PATH),
    "Set ANIMKEY_COMPLEX_SCENE and ANIMKEY_ESPERANZA_RIG to run this production-scene test.",
)
class EsperanzaComplexScenePipelineTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="animkey_esperanza_")
        self.data_patch = mock.patch.object(
            copyAnimation, "get_user_data_folder", return_value=self.data_dir
        )
        self.data_patch.start()
        copyAnimation._cleanup_buffer()
        cmds.file(SCENE_PATH, open=True, force=True, prompt=False)

    def tearDown(self):
        self.data_patch.stop()
        copyAnimation._cleanup_buffer()
        cmds.file(new=True, force=True)
        shutil.rmtree(self.data_dir, ignore_errors=True)

    @staticmethod
    def _selection_with_multiple_curves(limit=4):
        controls = []
        curves = []
        for curve in cmds.ls(type="animCurve") or []:
            plugs = curve_transfer.driven_plugs_for_curve(curve)
            for plug in plugs:
                node = plug.rsplit(".", 1)[0]
                if "Esperanza_Rigging:" not in node:
                    continue
                if cmds.nodeType(node) != "transform":
                    continue
                times = cmds.keyframe(curve, query=True, timeChange=True) or []
                if len(times) < 3:
                    continue
                if node not in controls:
                    controls.append(node)
                curves.append((curve, float(times[len(times) // 2])))
                break
            if len(curves) >= limit:
                break
        if not curves:
            raise AssertionError("No animated Esperanza control curves found")
        cmds.select(controls, replace=True)
        for index, (curve, frame) in enumerate(curves):
            cmds.selectKey(curve, replace=index == 0, add=index != 0, time=(frame, frame))
        return controls, curves

    @staticmethod
    def _direct_curve_payloads(namespace):
        """Capture concrete curve outputs without inherited DAG attributes."""
        payloads = {}
        for curve in cmds.ls(type="animCurve") or []:
            curve_data = None
            for plug in cmds.listConnections(
                    curve + ".output", source=False, destination=True, plugs=True
            ) or []:
                node = plug.rsplit(".", 1)[0]
                if namespace not in node:
                    continue
                if curve_data is None:
                    curve_data = curve_transfer.capture_curve(
                        curve, layer_name="BaseAnimation"
                    )
                if curve_data:
                    payloads[plug] = curve_data
        return payloads

    def test_selected_graph_keys_stay_fast_and_layer_exact(self):
        controls, curves = self._selection_with_multiple_curves()
        started = time.perf_counter()
        curve_smooth.prepare_curve_data()
        curve_smooth.execute(-35.0)
        curve_smooth.reset()
        smooth_seconds = time.perf_counter() - started

        self._selection_with_multiple_curves()
        started = time.perf_counter()
        blend_to_neighbors.prepare_blend_data()
        blend_to_neighbors.execute(35.0)
        blend_to_neighbors.reset()
        blend_seconds = time.perf_counter() - started

        self.assertTrue(curve_smooth._curve_data_cache == {})
        self.assertTrue(blend_to_neighbors._blend_neighbors_data_cache == {})
        # This threshold catches accidental full-rig keyable scans while
        # allowing slower studio workstations and referenced production rigs.
        self.assertLess(smooth_seconds, 8.0)
        self.assertLess(blend_seconds, 8.0)

    def test_copy_paste_to_clean_esperanza_master_keeps_layered_curves(self):
        controls, _curves = self._selection_with_multiple_curves()
        with animkey_execution("maya_test"):
            copyAnimation.copy_animation()
        self.assertGreater(copyAnimation._animation_payload_count(copyAnimation._anim_buffer), 0)

        cmds.file(new=True, force=True)
        cmds.file(RIG_PATH, reference=True, namespace="EsperanzaCopy")
        targets = []
        for source in controls:
            short_name = source.split("|")[-1].split(":")[-1]
            matches = cmds.ls("EsperanzaCopy:" + short_name, long=True, type="transform") or []
            if matches:
                targets.append(matches[0])
        self.assertTrue(targets, "Could not resolve Esperanza controls in the clean rig")
        cmds.select(targets, replace=True)
        with animkey_execution("maya_test"):
            copyAnimation.paste_animation()

        for target in targets:
            self.assertTrue(
                cmds.keyframe(target, query=True, keyframeCount=True) or
                cmds.listConnections(target, source=True, destination=False, type="animCurve")
            )

    def test_anim_crash_round_trip_restores_esperanza_curve_payload(self):
        objects = [
            obj for obj in animCrash.get_animated_objects(force=True)
            if "Esperanza_Rigging:" in obj
        ]
        self.assertTrue(objects, "No animated Esperanza recovery objects found")
        captured = animCrash.extract_animation(objects)
        self.assertTrue(captured and captured.get("animation"))
        expected_curves = self._direct_curve_payloads("Esperanza_Rigging:")
        self.assertTrue(expected_curves, "No direct Esperanza curves found")

        for curve in cmds.ls(type="animCurve") or []:
            destinations = cmds.listConnections(
                curve + ".output", source=False, destination=True, plugs=True
            ) or []
            if any("Esperanza_Rigging:" in plug for plug in destinations):
                cmds.cutKey(curve, clear=True)

        success, failed, skipped = animCrash.apply_animation(captured)
        self.assertEqual(failed, 0)
        self.assertEqual(skipped, 0)
        self.assertEqual(success, len(captured["animation"]))

        actual_curves = self._direct_curve_payloads("Esperanza_Rigging:")
        self.assertEqual(set(actual_curves), set(expected_curves))
        for plug, expected_curve in expected_curves.items():
            self.assertEqual(
                actual_curves[plug], expected_curve,
                "AnimCrash changed {}".format(plug),
            )


if __name__ == "__main__":
    result = unittest.main(exit=False).result
    result_file = os.environ.get("ANIMKEY_TEST_RESULT_FILE")
    if result_file:
        with open(result_file, "w") as stream:
            stream.write(
                "passed={} failures={} errors={} skipped={}".format(
                    result.wasSuccessful(), len(result.failures), len(result.errors),
                    len(result.skipped),
                )
            )
            for _test, detail in result.failures + result.errors:
                stream.write("\n" + detail[-4000:])
    sys.exit(0 if result.wasSuccessful() else 1)
