"""Temp Controls bake writes the rig's own curves with only the keys it needs."""
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

from AnimKey.buttons import tempControls as tc
from AnimKey.core import temp_transform as tf

FRAMES = list(range(1, 41))
KEYS = ((1, 0.0), (10, 30.0), (20, -15.0), (30, 20.0), (40, 0.0))


class SmartBakeTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.playbackOptions(minTime=1, maxTime=40)
        self.parent = cmds.group(empty=True, name="cog")
        self.ctrl = cmds.circle(name="hand_ctrl", ch=False)[0]
        self.ctrl = cmds.parent(self.ctrl, self.parent)[0]
        cmds.xform(self.ctrl, rotatePivot=(1, 2, 0), scalePivot=(1, 2, 0))
        for frame, value in KEYS:
            cmds.setKeyframe(self.parent, attribute="translateX", time=frame, value=value * 0.1)
            cmds.setKeyframe(self.ctrl, attribute="rotateY", time=frame, value=value)
            cmds.setKeyframe(self.ctrl, attribute="translateZ", time=frame, value=value * 0.05)
        self.ctrl = cmds.ls(self.ctrl, long=True)[0]
        cmds.currentTime(1)

    def keys(self):
        result = {}
        for curve in cmds.keyframe(self.ctrl, query=True, name=True) or []:
            attr = cmds.listConnections(curve + ".output", plugs=True)[0].rsplit(".", 1)[-1]
            result[attr] = cmds.keyframe(curve, query=True, timeChange=True)
        return result

    def assert_same_motion(self, expected):
        for (_, a), (_, b) in zip(expected, tf.capture([self.ctrl], FRAMES)):
            self.assertLess(max(abs(x - y) for x, y in zip(a[0], b[0])), 2e-5)

    def test_bake_without_edits_keeps_the_original_keys_and_adds_no_layer(self):
        before = self.keys()
        expected = tf.capture([self.ctrl], FRAMES)
        system = tc.create(node=[self.ctrl])[0]
        tc.clear([system], revert=False)
        self.assertEqual(cmds.ls(type="animLayer"), [])
        self.assertEqual(self.keys(), before)
        self.assert_same_motion(expected)

    def test_edit_adds_only_the_keys_it_needs_and_matches_exactly(self):
        system = tc.create(node=[self.ctrl])[0]
        control = tc.controls(system)[0]
        cmds.currentTime(15)
        cmds.setAttr(control + ".translateY", cmds.getAttr(control + ".translateY") + 2)
        cmds.setKeyframe(control, attribute="translateY", time=15)
        expected = tf.capture([self.ctrl], FRAMES)
        tc.clear([system], revert=False)
        self.assertEqual(cmds.ls(type="animLayer"), [])
        self.assert_same_motion(expected)
        keys = self.keys()
        total = sum(len(times) for times in keys.values())
        self.assertLess(total, len(FRAMES) * 2, "smart bake must not key every frame")
        for attr in ("rotateY", "translateZ"):
            self.assertTrue(set(t for t, _ in KEYS) <= set(keys[attr]),
                            "original key timing must be kept on " + attr)

    def test_toggle_off_bakes_the_same_way(self):
        system = tc.create(node=[self.ctrl])[0]
        expected = tf.capture([self.ctrl], FRAMES)
        tc.set_enabled(system, False)
        self.assertEqual(cmds.ls(type="animLayer"), [])
        self.assert_same_motion(expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
