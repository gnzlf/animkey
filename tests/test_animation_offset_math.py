import unittest

from AnimKey.core.animation_offset_math import (
    contiguous_index_runs,
    frames_equal,
    is_finite_number,
    rotation_delta_from_observation,
    shortest_angle_step,
    time_slider_range_to_inclusive,
)


class AnimationOffsetMathTests(unittest.TestCase):
    def test_contiguous_runs(self):
        self.assertEqual(
            contiguous_index_runs([0, 1, 2, 5, 6, 9]),
            [(0, 2), (5, 6), (9, 9)],
        )

    def test_shortest_positive_wrap(self):
        self.assertAlmostEqual(shortest_angle_step(179.0, -179.0), 2.0)

    def test_shortest_negative_wrap(self):
        self.assertAlmostEqual(shortest_angle_step(-179.0, 179.0), -2.0)

    def test_rejects_non_finite_values(self):
        self.assertFalse(is_finite_number(float("nan")))
        self.assertFalse(is_finite_number(float("inf")))
        self.assertFalse(is_finite_number(True))

    def test_frame_tolerance(self):
        self.assertTrue(frames_equal(10.0, 10.00001))
        self.assertFalse(frames_equal(10.0, 10.01))

    def test_rotation_wrap_keeps_continuity(self):
        self.assertAlmostEqual(
            rotation_delta_from_observation(179.0, 0.0, -179.0),
            2.0,
        )

    def test_rotation_large_value_is_preserved(self):
        self.assertAlmostEqual(
            rotation_delta_from_observation(0.0, 0.0, 400.0),
            400.0,
        )

    def test_time_slider_range_uses_inclusive_right_frame(self):
        self.assertEqual(
            time_slider_range_to_inclusive((10.0, 21.0)),
            (10.0, 20.0),
        )

    def test_time_slider_range_preserves_subframes(self):
        self.assertEqual(
            time_slider_range_to_inclusive((10.5, 15.5), frame_step=0.5),
            (10.5, 15.0),
        )

    def test_single_frame_time_slider_range_is_valid(self):
        self.assertEqual(
            time_slider_range_to_inclusive((7.0, 8.0)),
            (7.0, 7.0),
        )


if __name__ == "__main__":
    unittest.main()
