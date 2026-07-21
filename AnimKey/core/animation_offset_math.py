"""Pure helpers for AnimKey Animation Offset."""

import math


FRAME_EPSILON = 1e-4
VALUE_EPSILON = 1e-8
ROTATION_EPSILON = 1e-4


def is_finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def frames_equal(left, right, epsilon=FRAME_EPSILON):
    return abs(float(left) - float(right)) <= float(epsilon)


def values_equal(left, right, epsilon=VALUE_EPSILON):
    return abs(float(left) - float(right)) <= float(epsilon)


def shortest_angle_step(previous, current):
    """Return the nearest signed angular step in degrees."""
    step = float(current) - float(previous)
    while step > 180.0:
        step -= 360.0
    while step < -180.0:
        step += 360.0
    return step


def rotation_delta_from_observation(baseline, applied_delta, observed_value):
    """
    Convert an observed Euler channel value into a cumulative offset delta.

    Maya can display the same orientation around the 180/-180 boundary with a
    different numeric sign. This keeps that boundary continuous while
    preserving explicit large channel values such as 400 degrees.
    """
    expected = float(baseline) + float(applied_delta)
    observed = float(observed_value)
    raw_increment = observed - expected

    if expected > 90.0 and observed < -90.0 and raw_increment < -180.0:
        raw_increment += 360.0
    elif expected < -90.0 and observed > 90.0 and raw_increment > 180.0:
        raw_increment -= 360.0

    return float(applied_delta) + raw_increment


def contiguous_index_runs(indices):
    """Convert [0, 1, 2, 5, 6] into [(0, 2), (5, 6)]."""
    ordered = sorted(set(int(index) for index in indices))
    if not ordered:
        return []

    runs = []
    start = previous = ordered[0]
    for index in ordered[1:]:
        if index != previous + 1:
            runs.append((start, previous))
            start = index
        previous = index
    runs.append((start, previous))
    return runs


def value_at_frame(frame_values, frame, epsilon=FRAME_EPSILON):
    for key_time, value in frame_values.items():
        if frames_equal(key_time, frame, epsilon=epsilon):
            return value, True
    return None, False


def normalize_inclusive_time_range(time_range):
    start = float(time_range[0])
    end = float(time_range[1])
    if end < start:
        start, end = end, start
    return start, end


def time_slider_range_to_inclusive(time_range, frame_step=1.0):
    """Convert Maya's exclusive right Time Slider edge to an inclusive range."""
    start = float(time_range[0])
    end_edge = float(time_range[1])
    if end_edge < start:
        start, end_edge = end_edge, start

    step = abs(float(frame_step))
    end = end_edge - step
    if end < start:
        end = start
    return start, end
