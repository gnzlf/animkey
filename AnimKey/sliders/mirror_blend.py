"""
    AnimKey Slider: Mirror Blend
    
    Blend between current pose and mirrored pose.
    
    Features:
    - Captures current pose values
    - Calculates mirrored pose values
    - Blends between them based on slider percentage (0% = current, 100% = mirrored)
    - Creates keyframes on release
"""

import maya.cmds as cmds
from AnimKey.sliders.slider_utils import apply_slider_value


# Global cache for mirror blend data
_mirror_blend_data_cache = {}
_is_dragging = False


def _shortest_angle_delta(target, source):
    return ((target - source + 180.0) % 360.0) - 180.0


def is_attribute_modifiable(control, attr):
    try:
        return cmds.getAttr(control + "." + attr, settable=True) or \
            bool(cmds.keyframe(control + "." + attr, query=True, keyframeCount=True))
    except Exception:
        return False


def prepare_mirror_blend_data(objs=None):
    """
    Capture, per destination channel, its current value and the value the
    Mirror button would give it. Uses AnimKey's mirror engine, so the slider
    at 100% matches Mirror exactly (pairs, exceptions, channel filter).
    """
    global _mirror_blend_data_cache
    _mirror_blend_data_cache = {}
    from AnimKey.core import mirror_engine as engine

    objects = [o for o in (objs if objs else cmds.ls(selection=True, long=True) or [])
               if cmds.objExists(o)]
    if not objects:
        return _mirror_blend_data_cache

    pairs, tables = engine.plan_pairs(objects, mode="to_opposite")
    attr_filter = engine.selected_channels(objects)
    for source, target, attr, rule in engine._channel_jobs(pairs, tables, attr_filter):
        source_value = engine._get(source + "." + attr)
        target_attr = target + "." + attr
        original = engine._get(target_attr)
        if source_value is None or original is None:
            continue
        _mirror_blend_data_cache[target_attr] = {
            "original": original,
            "mirrored": engine.mirrored_value(source_value, rule),
            "attr": attr,
        }
    return _mirror_blend_data_cache


def execute(percentage):
    """
    Execute mirror blend slider functionality.
    
    Args:
        percentage (float): Percentage value from slider (0-100)
            - 0% = current pose (original values)
            - 100% = fully mirrored pose
    """
    global _mirror_blend_data_cache, _is_dragging
    
    if not _mirror_blend_data_cache:
        _mirror_blend_data_cache = prepare_mirror_blend_data()
    
    if not _mirror_blend_data_cache:
        return
    
    if not _is_dragging:
        cmds.undoInfo(openChunk=True)
        _is_dragging = True
    
    # Clamp percentage
    percentage = max(0.0, min(100.0, percentage))
    current_time = cmds.currentTime(query=True)
    
    # Apply blend to each attribute
    for attr_full, cache in _mirror_blend_data_cache.items():
        try:
            original = cache["original"]
            mirrored = cache["mirrored"]
            opposite = cache.get("opposite")
            is_central = cache.get("is_central", False)
            
            # Calculate blended value
            attr = cache.get("attr", attr_full.split('.', 1)[-1])
            if attr in ("rotateX", "rotateY", "rotateZ"):
                difference = _shortest_angle_delta(mirrored, original)
            else:
                difference = mirrored - original
            blended_value = original + (difference * percentage / 100.0)
            
            # The cache key is already the exact target attr produced by the calibrated mirror.
            if is_central or not opposite:
                # Central control: set on itself
                target_attr = attr_full
            else:
                # Paired control: set on opposite
                if opposite and cmds.objExists(opposite):
                    obj, attr = attr_full.split('.', 1)
                    target_attr = f"{opposite}.{attr}"
                    
                    # Check if opposite has this attribute and it's modifiable
                    if not is_attribute_modifiable(opposite, attr):
                        continue
                else:
                    continue
            
            # Check min/max limits
            obj, attr = target_attr.split('.', 1)
            if cmds.attributeQuery(attr, node=obj, minExists=True):
                min_limit = cmds.attributeQuery(attr, node=obj, minimum=True)[0]
                blended_value = max(blended_value, min_limit)
            
            if cmds.attributeQuery(attr, node=obj, maxExists=True):
                max_limit = cmds.attributeQuery(attr, node=obj, maximum=True)[0]
                blended_value = min(blended_value, max_limit)
            
            # Write through the same layer-aware path as the other sliders.
            # Direct setAttr can edit BaseAnimation (or fail on an animBlend
            # destination) while another animation layer is active.
            apply_slider_value(
                target_attr,
                current_time,
                blended_value,
                current_time,
            )
            
        except Exception:
            continue


def reset():
    """
    Reset mirror blend slider state.
    Creates keyframes at current time with current values (already modified by slider).
    """
    global _mirror_blend_data_cache, _is_dragging
    
    # apply_slider_value creates/edits the preview key during the drag, so no
    # second destination-plug keying pass is needed here.  That old pass was
    # what sent Mirror Blend back to BaseAnimation on layered rigs.
    # Reset cache
    _mirror_blend_data_cache = {}
    
    if _is_dragging:
        cmds.undoInfo(closeChunk=True)
        _is_dragging = False
