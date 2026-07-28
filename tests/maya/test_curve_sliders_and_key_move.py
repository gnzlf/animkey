import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons import keyframe_move
from AnimKey.sliders import (
    curve_ease_in_out,
    curve_flat,
    curve_linear,
    curve_noise,
    curve_scale,
    curve_smooth,
    curve_wave,
)


CURVE_SLIDERS = (
    ("Smooth/Rough", curve_smooth, -50),
    ("Wave", curve_wave, 60),
    ("Scale", curve_scale, 50),
    ("Linear", curve_linear, 100),
    ("Flat", curve_flat, 100),
    ("Ease In/Out", curve_ease_in_out, 50),
    ("Noise", curve_noise, 50),
)


class CurveSliderKeyCreationTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    def _create_control(self, keyed=True):
        control = cmds.createNode("transform", name="curve_slider_ctrl")
        cmds.setAttr("{}.translateX".format(control), 3.0)
        if keyed:
            cmds.setKeyframe(control, attribute="translateX", time=1.0, value=0.0)
            cmds.setKeyframe(control, attribute="translateX", time=10.0, value=10.0)
        cmds.currentTime(5.0)
        cmds.select(control, replace=True)
        return control

    def _key_times(self, attr_full):
        return [
            float(frame)
            for frame in (cmds.keyframe(attr_full, query=True, timeChange=True) or [])
        ]

    def _apply_slider(self, module, value, control):
        module.prepare_curve_data(objs=[control], attrs=["translateX"])
        module.execute(value)
        module.reset()

    def test_curve_sliders_create_current_key_when_current_frame_is_unkeyed(self):
        for _label, module, value in CURVE_SLIDERS:
            with self.subTest(slider=module.__name__):
                control = self._create_control(keyed=True)
                self._apply_slider(module, value, control)

                self.assertEqual(
                    self._key_times("{}.translateX".format(control)),
                    [1.0, 5.0, 10.0],
                )

    def test_curve_sliders_create_current_key_when_attribute_has_no_animation(self):
        for _label, module, value in CURVE_SLIDERS:
            with self.subTest(slider=module.__name__):
                control = self._create_control(keyed=False)
                self._apply_slider(module, value, control)

                self.assertEqual(
                    self._key_times("{}.translateX".format(control)),
                    [5.0],
                )


class KeyframeMoveTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.control = cmds.createNode("transform", name="move_key_ctrl")

    def _set_keys(self, attr, frames):
        for frame, value in frames:
            cmds.setKeyframe(self.control, attribute=attr, time=frame, value=value)

    def _keys(self, attr):
        attr_full = "{}.{}".format(self.control, attr)
        times = cmds.keyframe(attr_full, query=True, timeChange=True) or []
        values = cmds.keyframe(attr_full, query=True, valueChange=True) or []
        return list(zip([float(time) for time in times], [float(value) for value in values]))

    def test_left_arrow_pulls_nearest_right_key_to_current_frame(self):
        self._set_keys("translateX", ((1.0, 1.0), (10.0, 10.0), (20.0, 20.0)))
        cmds.currentTime(12.0)
        cmds.select(self.control, replace=True)

        moved = keyframe_move.move_neighbor_key_to_current(-1)

        self.assertEqual(moved, 1)
        self.assertEqual(self._keys("translateX"), [(1.0, 1.0), (10.0, 10.0), (12.0, 20.0)])

    def test_right_arrow_pulls_nearest_left_key_to_current_frame(self):
        self._set_keys("translateX", ((1.0, 1.0), (10.0, 10.0), (20.0, 20.0)))
        cmds.currentTime(12.0)
        cmds.select(self.control, replace=True)

        moved = keyframe_move.move_neighbor_key_to_current(1)

        self.assertEqual(moved, 1)
        self.assertEqual(self._keys("translateX"), [(1.0, 1.0), (12.0, 10.0), (20.0, 20.0)])

    def test_arrow_move_respects_selected_channel_filter(self):
        self._set_keys("translateX", ((1.0, 1.0), (20.0, 20.0)))
        self._set_keys("translateY", ((1.0, 2.0), (20.0, 40.0)))
        cmds.currentTime(12.0)
        cmds.select(self.control, replace=True)

        with mock.patch.object(keyframe_move, "get_selected_channels", return_value=["translateX"]):
            moved = keyframe_move.move_neighbor_key_to_current(-1)

        self.assertEqual(moved, 1)
        self.assertEqual(self._keys("translateX"), [(1.0, 1.0), (12.0, 20.0)])
        self.assertEqual(self._keys("translateY"), [(1.0, 2.0), (20.0, 40.0)])


if __name__ == "__main__":
    unittest.main()
