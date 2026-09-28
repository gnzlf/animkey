"""
    AnimKey Curve Slider: Wave
    
    Adds sinusoidal wave pattern to animation curves.
    
    Features:
    - Adds oscillating pattern to keyframe values
    - 0% = no wave, 100% = full wave amplitude
    - When Graph Editor keys selected, ONLY those curves are affected
"""

import maya.cmds as cmds
import math
from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_keyframes_for_attribute,
    get_slider_value,
    get_selection_guide_frames,
    get_processing_context,
    get_slider_candidate_attributes,
    get_slider_target_records,
    get_value_at_time,
    should_process_attribute,
    slider_attribute_is_editable,
)


# Global cache
_curve_data_cache = {}
_is_dragging = False
_processing_context = {}

WAVE_FREQUENCY = 0.5
WAVE_MAX_AMPLITUDE = 5.0


def prepare_curve_data(objs=None, attrs=None):
    """Prepare curve data cache for current selection."""
    global _curve_data_cache, _processing_context
    _curve_data_cache = {}
    
    _processing_context = get_processing_context(explicit_attributes=attrs is not None)
    for target in get_slider_target_records(objs, attrs, _processing_context):
        attr_full = target['attr_full']
        curve = target['curve']
        frames_to_process = target['frames']
        first_frame, _ = get_selection_guide_frames(
            target['keyframes'], frames_to_process
        )
        first_frame = min(frames_to_process) if first_frame is None else first_frame
        for frame in frames_to_process:
            try:
                original_value = get_value_at_time(attr_full, frame, curve=curve)
                if isinstance(original_value, (list, tuple)):
                    continue
                phase = (frame - first_frame) * WAVE_FREQUENCY * 0.1 * 2 * math.pi
                cache_key = '{}@{}'.format(curve or attr_full, frame)
                _curve_data_cache[cache_key] = {
                    "originalValue": original_value,
                    "waveOffset": math.sin(phase),
                    "attr_full": attr_full,
                    "curve": curve,
                    "frame": frame,
                }
            except Exception:
                continue
    
    return _curve_data_cache


def execute(percentage):
    global _curve_data_cache, _is_dragging
    
    if not _curve_data_cache:
        _curve_data_cache = prepare_curve_data()
    
    if not _curve_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    amplitude = (percentage / 100.0) * WAVE_MAX_AMPLITUDE
    
    for cache_key, cache in _curve_data_cache.items():
        attr_full = cache.get("attr_full")
        curve = cache.get("curve")
        frame = cache.get("frame")
        
        if not attr_full:
            continue
        
        try:
            if not cmds.objExists(attr_full):
                continue
            if not slider_attribute_is_editable(attr_full):
                continue
            
            original_value = cache.get("originalValue")
            wave_offset = cache.get("waveOffset", 0)
            
            if original_value is None:
                continue
            
            new_value = original_value + (wave_offset * amplitude)
            apply_slider_value(
                attr_full, frame, new_value,
                _processing_context.get('current_time'), curve=curve,
            )
            
        except Exception:
            continue


def reset():
    global _curve_data_cache, _is_dragging, _processing_context
    
    if _curve_data_cache:
        for cache_key, cache_data in _curve_data_cache.items():
            try:
                attr_full = cache_data.get("attr_full")
                curve = cache_data.get("curve")
                frame = cache_data.get("frame")
                
                if not attr_full:
                    continue
                
                current_value = get_slider_value(
                    attr_full, frame, _processing_context.get('current_time'), curve=curve
                )
                finalize_slider_value(
                    attr_full, frame, current_value,
                    _processing_context.get('current_time'), curve=curve,
                )
            except Exception:
                continue
    
    _curve_data_cache = {}
    _processing_context = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
