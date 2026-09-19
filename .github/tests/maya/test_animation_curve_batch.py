from __future__ import annotations

import os
import sys
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.core import animation_curve_transfer as transfer


class BatchCurveTransferTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.source = cmds.createNode("transform", name="source_ctrl")
        self.target = cmds.createNode("transform", name="target_ctrl")
        for attr, values in (("translateX", (1.25, 4.5)), ("rotateY", (-12.0, 31.0))):
            for frame, value in zip((1.0, 12.0), values):
                cmds.setKeyframe(self.source, attribute=attr, time=frame, value=value)
            cmds.keyTangent(
                self.source,
                attribute=attr,
                edit=True,
                inTangentType="linear",
                outTangentType="linear",
            )

    def test_batch_paste_preserves_multiple_curve_order_and_undo(self):
        payloads = [
            transfer.capture_curve("{}.translateX".format(self.source)),
            transfer.capture_curve("{}.rotateY".format(self.source)),
        ]
        transfers = [
            ("{}.translateX".format(self.target), payloads[0]),
            ("{}.rotateY".format(self.target), payloads[1]),
        ]
        for attr in ("translateX", "rotateY"):
            cmds.setKeyframe(self.target, attribute=attr, time=-5.0, value=99.0)
            cmds.setKeyframe(self.target, attribute=attr, time=20.0, value=101.0)

        cmds.undoInfo(openChunk=True)
        try:
            results = transfer.paste_curves_batch(transfers, layer_name=None)
        finally:
            cmds.undoInfo(closeChunk=True)

        self.assertEqual(results, [True, True])
        self.assertEqual(
            cmds.keyframe(self.target, attribute="translateX", query=True),
            [-5.0, 1.0, 12.0, 20.0],
        )
        self.assertEqual(
            cmds.keyframe(self.target, attribute="rotateY", query=True),
            [-5.0, 1.0, 12.0, 20.0],
        )
        for actual, expected in zip(
            cmds.keyframe(self.target, attribute="translateX", query=True, valueChange=True),
            [99.0, 1.25, 4.5, 101.0],
        ):
            self.assertAlmostEqual(actual, expected, places=7)
        for actual, expected in zip(
            cmds.keyframe(self.target, attribute="rotateY", query=True, valueChange=True),
            [99.0, -12.0, 31.0, 101.0],
        ):
            self.assertAlmostEqual(actual, expected, places=7)

        cmds.undo()
        self.assertEqual(
            cmds.keyframe(self.target, attribute="translateX", query=True),
            [-5.0, 20.0],
        )
        self.assertEqual(
            cmds.keyframe(self.target, attribute="rotateY", query=True),
            [-5.0, 20.0],
        )


if __name__ == "__main__":
    unittest.main()
