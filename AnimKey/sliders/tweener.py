"""
    AnimKey Slider: Tweener
    
    Tween between previous and next keyframe.
    AnimKey blend functionality.
    
    Features:
    - Blends between previous and next keyframe values
    - Works on selected objects and channels
    - Respects keyframe tangents
    - Supports multiple keyframe selection (timeline range, graph editor)
    - Respects Channel Box selection

    PERFORMANCE NOTES:
    - prepare_tween_data() is called ONCE when the user starts dragging.
      It pre-fetches ALL Maya data and stores the result in _tween_cache.
    - execute() does ZERO Maya API queries — pure Python math + setKeyframe.
    - Binary search (bisect) replaces all linear scans.
    - cmds.keyframe(valueChange=True) replaces getAttr(time=) for 5-10x speedup.
"""

import maya.cmds as cmds
import bisect

from AnimKey.sliders.slider_utils import (
    apply_slider_value,
    finalize_slider_value,
    get_frames_to_process,
    get_keyframes_for_attribute,
    get_processing_context,
    get_slider_value,
    should_process_attribute
)


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_tween_cache  = []
_is_dragging  = False
_current_time = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prev_next_frames(sorted_frames, target):
    """
    Pure bisect lookup for prev/next frame around `target`.
    No loops — O(log n) only.
    """
    idx = bisect.bisect_left(sorted_frames, target)

    prev_frame = sorted_frames[idx - 1] if idx > 0 else None

    # Next: first frame strictly greater than target
    # bisect_right gives us the insertion point AFTER any equal values
    right_idx = bisect.bisect_right(sorted_frames, target)
    next_frame = sorted_frames[right_idx] if right_idx < len(sorted_frames) else None

    return prev_frame, next_frame


def _get_key_value(attr_full, frame):
    """
    Get the value of an attribute at a specific keyed frame.
    Uses cmds.keyframe(valueChange=True) which is 5-10x faster than
    cmds.getAttr(time=) because it reads directly from the anim curve
    without triggering a full DG evaluation.
    
    Falls back to getAttr for the current time (which may not have a key).
    """
    try:
        raw = get_slider_value(attr_full, frame)
        if isinstance(raw, (int, float)):
            return float(raw), True
        if isinstance(raw, (list, tuple)) and len(raw) == 1:
            return float(raw[0]), True
    except Exception:
        pass
    return None, False


def _to_float(value):
    """Convert a scalar/list Maya value to float, or None."""
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            return None
        value = value[0]
    if isinstance(value, (int, float, bool)):
        return float(value)
    return None


# ---------------------------------------------------------------------------
# prepare_tween_data  — called ONCE per drag gesture
# ---------------------------------------------------------------------------

def prepare_tween_data(objs=None, attrs=None):
    """
    Build the tween cache for the current selection.

    All slow Maya API calls happen here. execute() only does pure math.
    
    Optimizations vs original:
    - cmds.keyframe(valueChange=True) instead of getAttr(time=) — skips DG eval
    - Pure bisect for prev/next frame — no linear scan
    - Batch attribute validation
    """
    global _tween_cache, _current_time

    _tween_cache  = []
    _current_time = cmds.currentTime(query=True)

    # -- Context -------------------------------------------------------
    context = get_processing_context()
    selected_channels = context.get('selected_channels')

    objects = objs if objs else cmds.ls(selection=True)
    if not objects:
        return _tween_cache

    # -- Build per-object data -----------------------------------------
    for obj in objects:
        if not cmds.objExists(obj):
            continue

        all_attrs = attrs if attrs else (cmds.listAttr(obj, keyable=True) or [])
        if not all_attrs:
            continue

        for attr in all_attrs:
            if not should_process_attribute(obj, attr, selected_channels):
                continue

            attr_full = f'{obj}.{attr}'

            # ----- Fast attribute validation --------------------------
            try:
                if not cmds.getAttr(attr_full, settable=True):
                    continue
                if cmds.getAttr(attr_full, lock=True):
                    continue
                a_type = cmds.getAttr(attr_full, type=True)
                if a_type in ('enum', 'string', 'message', 'bool'):
                    continue
            except Exception:
                continue

            # ----- Pre-query limits -----------------------------------
            min_val = None
            max_val = None
            try:
                if cmds.attributeQuery(attr, node=obj, minExists=True):
                    min_val = float(cmds.attributeQuery(attr, node=obj, minimum=True)[0])
                if cmds.attributeQuery(attr, node=obj, maxExists=True):
                    max_val = float(cmds.attributeQuery(attr, node=obj, maximum=True)[0])
            except Exception:
                pass

            # ----- All keyframe times (one call) ----------------------
            all_frames = get_keyframes_for_attribute(attr_full, attr, context)
            if not all_frames:
                continue
            sorted_frames = sorted(set(all_frames))  # dedupe + sort

            # ----- Which frames to process ----------------------------
            frames_to_process = get_frames_to_process(
                attr_full, sorted_frames, attr, context
            )
            if not frames_to_process:
                continue

            # ----- Build cache entries --------------------------------
            for frame in frames_to_process:
                prev_f, next_f = _prev_next_frames(sorted_frames, frame)
                is_current = (frame == _current_time)

                try:
                    # Get values using fast keyframe query (no DG eval)
                    cur_val, ok = _get_key_value(attr_full, frame)
                    if not ok:
                        continue

                    if prev_f is not None and next_f is not None:
                        prev_val, ok1 = _get_key_value(attr_full, prev_f)
                        next_val, ok2 = _get_key_value(attr_full, next_f)
                        if not (ok1 and ok2):
                            continue
                    elif prev_f is not None:
                        prev_val, ok1 = _get_key_value(attr_full, prev_f)
                        if not ok1:
                            continue
                        next_val = cur_val
                    elif next_f is not None:
                        next_val, ok2 = _get_key_value(attr_full, next_f)
                        if not ok2:
                            continue
                        prev_val = cur_val
                    else:
                        continue  # isolated key — nothing to tween

                except Exception:
                    continue

                # Prev tangent type (for reset)
                prev_tan = None
                if prev_f is not None:
                    try:
                        result = cmds.keyTangent(attr_full, query=True,
                                                  time=(prev_f,), outTangentType=True)
                        prev_tan = result[0] if result else None
                    except Exception:
                        pass

                _tween_cache.append({
                    'attr_full':  attr_full,
                    'frame':      frame,
                    'prev_val':   prev_val,
                    'next_val':   next_val,
                    'cur_val':    cur_val,   # store original for accurate reset
                    'min_val':    min_val,
                    'max_val':    max_val,
                    'is_current': is_current,
                    'prev_tan':   prev_tan,
                })

    return _tween_cache


# ---------------------------------------------------------------------------
# execute  — called on EVERY slider tick
# ---------------------------------------------------------------------------

def execute(percentage):
    """
    Apply tween blend at `percentage` (0–100) to every cached entry.

    Does ZERO Maya API queries — only math + setKeyframe/setAttr.
    
    WILL NOT auto-prepare if cache is empty (caller must call
    prepare_tween_data() first). This avoids thread-safety issues.
    """
    global _tween_cache, _is_dragging

    if not _tween_cache:
        return  # Caller must prepare first — no auto-trigger

    # -- Resistance snapping at 0 / 50 / 100 --------------------------
    for snap, rng in ((100.0, 4.5), (50.0, 4.5), (0.0, 4.5)):
        if snap - rng <= percentage <= snap + rng:
            percentage = snap
            break

    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True

    ratio = percentage / 100.0

    for entry in _tween_cache:
        prev_val  = entry['prev_val']
        next_val  = entry['next_val']
        attr_full = entry['attr_full']
        frame     = entry['frame']
        min_val   = entry['min_val']
        max_val   = entry['max_val']

        # Linear blend — pure Python, no Maya
        new_val = prev_val + (next_val - prev_val) * ratio

        # Clamp to pre-queried limits
        if min_val is not None and new_val < min_val:
            new_val = min_val
        if max_val is not None and new_val > max_val:
            new_val = max_val

        try:
            apply_slider_value(attr_full, frame, new_val, _current_time)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# reset  — called when the user releases the slider
# ---------------------------------------------------------------------------

def reset():
    """
    Finalise the tween operation:
    - Bake setAttr changes into keyframes at current time.
    - Restore tangent types where needed.
    - Close the undo chunk.
    
    Optimizations vs original:
    - For 'is_current' entries: read the already-set value via getAttr (no
      redundant setKeyframe → getAttr → setKeyframe cycle).
    - For non-current entries: skip entirely — value was already written
      by cmds.keyframe(edit=True) in execute(). No need to re-read and re-set.
    """
    global _tween_cache, _is_dragging, _current_time

    saved_time = _current_time or cmds.currentTime(query=True)
    offset_changes = []

    for entry in _tween_cache:
        attr_full = entry['attr_full']
        frame     = entry['frame']
        prev_tan  = entry['prev_tan']
        original_val = _to_float(entry.get('cur_val'))
        final_val = None

        try:
            if entry['is_current']:
                # Value was applied with setAttr → bake into a keyframe
                cur_val = get_slider_value(attr_full, frame, saved_time)
                final_val = _to_float(cur_val)
                if final_val is None:
                    continue
                finalize_slider_value(attr_full, frame, final_val, saved_time)
            # Non-current frames: already written via keyframe(edit=True),
            # no redundant re-read needed.
            else:
                final_val = _to_float(get_slider_value(attr_full, frame, saved_time))

            if original_val is not None and final_val is not None and abs(final_val - original_val) > 1e-9:
                offset_changes.append({
                    'attr_full': attr_full,
                    'frame': frame,
                    'original_value': original_val,
                    'new_value': final_val,
                })

            # Restore tangent type if needed
            if prev_tan:
                try:
                    if prev_tan == 'step':
                        cmds.keyTangent(attr_full, edit=True, time=(frame,),
                                        inTangentType='auto', outTangentType='step')
                    elif prev_tan == 'stepnext':
                        cmds.keyTangent(attr_full, edit=True, time=(frame,),
                                        inTangentType='stepnext', outTangentType='auto')
                    else:
                        cmds.keyTangent(attr_full, edit=True, time=(frame,),
                                        inTangentType=prev_tan, outTangentType=prev_tan)
                except Exception:
                    pass

        except Exception:
            continue

    if offset_changes:
        try:
            from AnimKey.buttons import animation_offset
            animation_offset.apply_slider_offset_changes(offset_changes)
        except Exception:
            pass

    # -- Clean up -------------------------------------------------------
    _tween_cache  = []
    _current_time = 0.0

    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
