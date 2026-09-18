"""Layer-aware Copy/Paste Animation tools for AnimKey.

Curves are captured directly through OpenMaya into a persistent clip. Paste
rebuilds short-lived native curves and lets Maya transfer them to the selected
Base Animation/animation layer, preserving tangents and undo without leaving
buffer nodes in the scene.
"""

import maya.cmds as cmds
import maya.mel as mel
import os
import json
import re
import threading
from datetime import datetime
from AnimKey.mods.themes import ThemeManager
from AnimKey.mods.uiMod import ContextPopupWindow
from AnimKey.mods.storage import atomic_write_json
from AnimKey.core import animation_curve_transfer as curve_transfer

from AnimKey.mods.maya_compat import (
    QtCore, QtGui, QtWidgets, screen_available_geometry,
)


# ═══════════════════════════════════════════════════════════════════════════════
#                           FILE PATHS
# ═══════════════════════════════════════════════════════════════════════════════

def get_user_data_folder():
    """Get the user data folder for AnimKey"""
    from AnimKey.mods import configMod
    return configMod.get_user_folder_path()


def get_copy_paste_animation_folder():
    """Get the shared folder for copy/paste animation and pose cache data."""
    cache_folder = os.path.join(get_user_data_folder(), "tools", "copy_paste_animation")
    os.makedirs(cache_folder, exist_ok=True)
    return cache_folder


def get_copy_paste_animation_file():
    """Get the file path for copy/paste animation data"""
    return os.path.join(get_copy_paste_animation_folder(), "copy_paste_animation_data.json")


def get_animation_backup_folder():
    """Return the folder used for persistent animation backups."""
    from AnimKey.mods import configMod
    return configMod.get_animation_backup_folder(create=True)


def _resolve_preview_gif_path(file_path, meta):
    """Resolve the best existing GIF path for a saved library clip."""
    gif_path = meta.get("preview_gif")
    candidates = []

    if gif_path:
        if not os.path.isabs(gif_path):
            candidates.append(os.path.join(os.path.dirname(file_path), gif_path))
        candidates.append(gif_path)

    base = os.path.splitext(file_path)[0]
    candidates.extend([
        base + ".low.gif",
        base + "_low.gif",
        base + ".gif",
    ])

    for candidate in candidates:
        if not candidate:
            continue
        normalized = os.path.normpath(candidate)
        if os.path.exists(normalized):
            return normalized
    return None


def get_latest_animation_backup_file():
    """Return the latest persistent backup file."""
    return os.path.join(get_animation_backup_folder(), "latest.animkey_anim")


def get_copy_paste_pose_file():
    """Get the shared file path for copy/paste pose data."""
    return os.path.join(get_copy_paste_animation_folder(), "copy_paste_pose_data.json")


def get_legacy_copy_paste_pose_file():
    """Return the old pose cache path for backwards-compatible reads."""
    cache_folder = os.path.join(get_user_data_folder(), "tools", "copy_paste_pose")
    return os.path.join(cache_folder, "copy_paste_pose_data.json")


# ═══════════════════════════════════════════════════════════════════════════════
#                           UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_selected_time_range():
    """Return the inclusive selected time range, or None."""
    return curve_transfer.selected_time_range()


def clear_timeslider_selection():
    """Clear the timeline range selection on the time slider."""
    # BUG FIX: The old approach (create+delete a node) did NOT clear the
    # timeline selection at all.  The correct way is to reset the timeControl
    # range to a single-frame span, which collapses the blue selection bar.
    try:
        aTimeSlider = mel.eval('$tmpVar=$gPlayBackSlider')
        current_time = cmds.currentTime(query=True)
        cmds.timeControl(aTimeSlider, edit=True,
                         rangeArray=[current_time, current_time + 1])
    except Exception:
        pass


def get_animated_channels(control):
    """Get channels animated on the selected Base Animation/animation layer."""
    animated_channels = []
    attributes = []
    for attr_list in (
        cmds.listAttr(control, keyable=True) or [],
        cmds.listAttr(control, channelBox=True) or [],
    ):
        for attr in attr_list:
            if attr not in attributes:
                attributes.append(attr)
    if not attributes:
        return animated_channels

    layer_name = curve_transfer.active_animation_layer()
    for attr in attributes:
        attr_full = f"{control}.{attr}"
        curve = curve_transfer.resolve_anim_curve(attr_full, layer_name=layer_name)
        if not curve:
            continue
        try:
            if cmds.keyframe(curve, query=True, keyframeCount=True):
                animated_channels.append(attr)
        except Exception:
            pass
    return animated_channels


def _control_channel_sets(control):
    """Return all and locked channel names for one control using bulk queries."""
    all_attrs = set()
    locked_attrs = set()
    for short_names in (False, True):
        kwargs = {"shortNames": True} if short_names else {}
        try:
            all_attrs.update(cmds.listAttr(control, **kwargs) or [])
        except Exception:
            pass
        try:
            locked_attrs.update(cmds.listAttr(control, locked=True, **kwargs) or [])
        except Exception:
            pass
    return all_attrs, locked_attrs


def _channel_exists(channel, all_attrs):
    return channel in all_attrs or channel.split("[", 1)[0] in all_attrs


def _build_animation_transfers(assignments, animation_data):
    transfers = []
    prevalidated = set()
    skipped = 0
    attr_cache = {}
    for ctrl_key, control in assignments:
        if control not in attr_cache:
            attr_cache[control] = _control_channel_sets(control)
        all_attrs, locked_attrs = attr_cache[control]
        for channel, curve_data in animation_data.get(ctrl_key, {}).items():
            base_channel = channel.split("[", 1)[0]
            if not _channel_exists(channel, all_attrs):
                skipped += 1
                continue
            if channel in locked_attrs or base_channel in locked_attrs:
                skipped += 1
                continue
            attr_path = f"{control}.{channel}"
            transfers.append((attr_path, curve_data))
            prevalidated.add(attr_path)
    return transfers, prevalidated, skipped


def get_control_short_name(control):
    """Return a namespace-agnostic control name."""
    leaf = control.rsplit("|", 1)[-1]
    return leaf.rsplit(":", 1)[-1]


def get_control_namespace(control):
    """Return the namespace prefix for a control, if any."""
    leaf = control.rsplit("|", 1)[-1]
    if ":" not in leaf:
        return ""
    return leaf.rsplit(":", 1)[0]


def _strip_namespace_from_segment(segment):
    return segment.rsplit(":", 1)[-1] if segment else ""


def get_control_path_parts(control):
    """Return DAG path parts without namespaces for robust rig-to-rig matching."""
    if not control:
        return []
    parts = [_strip_namespace_from_segment(part) for part in str(control).split("|") if part]
    if parts:
        return parts
    short_name = get_control_short_name(control)
    return [short_name] if short_name else []


def get_control_path_signature(control):
    """Return a namespace-free DAG signature suitable for storage and matching."""
    return "|".join(get_control_path_parts(control))


def get_control_storage_key(control):
    """Use the path signature as the durable key so duplicate facial names stay unique."""
    return get_control_path_signature(control) or get_control_short_name(control)


def _add_lookup_value(lookup, key, control):
    if not key:
        return
    lookup.setdefault(key, [])
    if control not in lookup[key]:
        lookup[key].append(control)


def build_control_identity_lookup(controls):
    """Build path, suffix, and short-name lookups for namespace-agnostic matching."""
    lookup = {"path": {}, "suffix": {}, "short": {}}
    for control in controls:
        short_name = get_control_short_name(control)
        path_signature = get_control_path_signature(control)
        _add_lookup_value(lookup["short"], short_name, control)
        _add_lookup_value(lookup["path"], path_signature, control)

        parts = path_signature.split("|") if path_signature else []
        max_suffix = min(len(parts), 10)
        for length in range(2, max_suffix + 1):
            _add_lookup_value(lookup["suffix"], "|".join(parts[-length:]), control)
    return lookup


def _unique_match(matches, namespace=None, used_targets=None):
    used_targets = used_targets or set()
    filtered = [match for match in matches or [] if match not in used_targets]
    if namespace is not None:
        filtered = [match for match in filtered if get_control_namespace(match) == namespace]
    if len(filtered) == 1:
        return filtered[0]
    return None


def _resolve_from_identity_lookup(source_name, lookup, namespaces=None, used_targets=None):
    namespaces = namespaces or [None]
    used_targets = used_targets or set()
    path_signature = get_control_path_signature(source_name)
    parts = path_signature.split("|") if path_signature else []
    short_name = get_control_short_name(source_name)

    for namespace in namespaces:
        target = _unique_match(lookup.get("path", {}).get(path_signature, []), namespace, used_targets)
        if target:
            return target

    suffixes = []
    max_suffix = min(len(parts), 10)
    for length in range(max_suffix, 1, -1):
        suffixes.append("|".join(parts[-length:]))

    for suffix in suffixes:
        for namespace in namespaces:
            target = _unique_match(lookup.get("suffix", {}).get(suffix, []), namespace, used_targets)
            if target:
                return target

    for namespace in namespaces:
        target = _unique_match(lookup.get("short", {}).get(short_name, []), namespace, used_targets)
        if target:
            return target

    return None


def list_candidate_controls(selected):
    """Return selected controls plus descendants when useful."""
    direct = list_selected_transform_targets(selected)
    if not direct:
        return []

    candidates = list(direct)
    seen = set(candidates)
    direct_set = set(direct)
    roots = [
        node for node in direct
        if not any(
            node != possible_parent and node.startswith(possible_parent + "|")
            for possible_parent in direct_set
        )
    ]
    try:
        descendants = cmds.listRelatives(
            roots,
            ad=True,
            fullPath=True,
            type="transform",
        ) or []
    except Exception:
        descendants = []
    for item in descendants:
        if item not in seen:
            seen.add(item)
            candidates.append(item)
    return candidates


def list_selected_transform_targets(selected):
    """Return selected transforms only, preserving the current selection order."""
    targets = []
    seen = set()
    for node in selected or []:
        if not cmds.objExists(node):
            continue
        try:
            if cmds.nodeType(node) != "transform":
                parents = cmds.listRelatives(node, parent=True, fullPath=True, type="transform") or []
                if not parents:
                    continue
                node = parents[0]
        except Exception:
            continue
        if node not in seen:
            seen.add(node)
            targets.append(node)
    return targets


def build_short_name_lookup(controls):
    """Build a short-name lookup for scene controls."""
    lookup = {}
    for control in controls:
        short_name = get_control_short_name(control)
        lookup.setdefault(short_name, [])
        if control not in lookup[short_name]:
            lookup[short_name].append(control)
    return lookup


def find_control_in_namespace(short_name, namespace, lookup):
    """Find a control by short name, preferring a namespace match."""
    matches = lookup.get(short_name, [])
    if not matches:
        return None
    if namespace:
        for control in matches:
            if get_control_namespace(control) == namespace:
                return control
    return matches[0] if len(matches) == 1 else None


def resolve_target_controls(source_names, selected=None):
    """
    Resolve copied controls to target scene controls without relying on namespace.

    Priority:
    1. Controls inside the current selection/selection hierarchy
    2. Controls inside the selected namespace
    3. Unique matches in the whole scene
    """
    selected = cmds.ls(selection=True, long=True) if selected is None else selected
    selected = selected or []
    source_names = list(source_names)
    direct_selected = list_selected_transform_targets(selected)

    # The common copy/paste case selects the destination controls directly.
    # Resolve that flat selection first; walking every selected hierarchy made
    # nested rigs quadratic and was the main paste bottleneck on large scenes.
    direct_lookup = build_control_identity_lookup(direct_selected)
    direct_resolved = {}
    direct_used = set()
    for source_name in source_names:
        target = _resolve_from_identity_lookup(
            source_name,
            direct_lookup,
            namespaces=[None],
            used_targets=direct_used,
        )
        if target is not None:
            direct_resolved[source_name] = target
            direct_used.add(target)
    if len(direct_resolved) == len(source_names):
        return direct_resolved

    if direct_selected and len(direct_selected) == len(source_names):
        resolved = dict(direct_resolved)
        used_targets = set(direct_resolved.values())
        remaining_sources = [
            source_name for source_name in source_names if source_name not in resolved
        ]
        remaining_targets = [
            target for target in direct_selected if target not in used_targets
        ]
        for source_name, target in zip(remaining_sources, remaining_targets):
            resolved[source_name] = target
        return resolved

    selected_candidates = list_candidate_controls(selected)
    selected_lookup = build_control_identity_lookup(selected_candidates)

    selected_namespaces = []
    for control in selected_candidates:
        namespace = get_control_namespace(control)
        if namespace and namespace not in selected_namespaces:
            selected_namespaces.append(namespace)

    if selected_candidates:
        # An explicit selection is authoritative.  Falling through to a
        # whole-scene lookup can otherwise paste back onto the copied source
        # rig when the selected destination has a different hierarchy/name.
        resolved = {}
        used_targets = set()
        for source_name in source_names:
            target = _resolve_from_identity_lookup(
                source_name,
                selected_lookup,
                namespaces=[None],
                used_targets=used_targets,
            )
            if target is not None:
                resolved[source_name] = target
                used_targets.add(target)

        remaining_sources = [
            source_name for source_name in source_names if source_name not in resolved
        ]
        remaining_targets = [
            target for target in direct_selected if target not in used_targets
        ]
        if remaining_sources and len(remaining_sources) == len(remaining_targets):
            for source_name, target in zip(remaining_sources, remaining_targets):
                resolved[source_name] = target
        return resolved

    # Whole-scene enumeration is only necessary when there is no authoritative
    # destination selection. Avoid building this large lookup for normal paste.
    scene_controls = cmds.ls(type="transform", long=True) or []
    scene_lookup = build_control_identity_lookup(scene_controls)

    resolved = {}
    used_targets = set()
    for source_name in source_names:
        target = None

        target = _resolve_from_identity_lookup(
            source_name,
            selected_lookup,
            namespaces=[None],
            used_targets=used_targets,
        )

        if target is None:
            target = _resolve_from_identity_lookup(
                source_name,
                scene_lookup,
                namespaces=selected_namespaces,
                used_targets=used_targets,
            )

        if target is None:
            target = _resolve_from_identity_lookup(
                source_name,
                scene_lookup,
                namespaces=[None],
                used_targets=used_targets,
            )

        if target is not None:
            resolved[source_name] = target
            used_targets.add(target)

    unresolved = [name for name in source_names if name not in resolved]
    direct_selected = [
        target for target in list_selected_transform_targets(selected)
        if target not in used_targets
    ]

    if len(unresolved) == 1 and len(direct_selected) == 1:
        resolved[unresolved[0]] = direct_selected[0]
    elif unresolved and len(unresolved) == len(direct_selected):
        for short_name, target in zip(unresolved, direct_selected):
            resolved[short_name] = target

    return resolved


def resolve_animation_target_assignments(source_names, selected=None):
    """Return source/destination pairs for animation paste operations.

    A single copied control is intentionally repeatable across several directly
    selected controls.  Multi-control clips keep the identity/order mapping
    used by resolve_target_controls().
    """
    selected = cmds.ls(selection=True, long=True) if selected is None else selected
    selected = selected or []
    source_names = list(source_names)
    direct_targets = list_selected_transform_targets(selected)
    if len(source_names) == 1 and len(direct_targets) > 1:
        return [(source_names[0], target) for target in direct_targets]

    # Selecting several rig roots should paste the complete clip to every
    # selected hierarchy. The older global lookup saw duplicate control names
    # across those rigs as ambiguous and pasted to neither of them. Resolve
    # each hierarchy independently, as Transify does for multiple namespaces,
    # while also supporting rigs that intentionally have no namespace.
    if len(source_names) > 1 and len(direct_targets) > 1:
        complete_hierarchies = []
        for root in direct_targets:
            candidates = list_candidate_controls([root])
            if len(candidates) <= 1:
                continue
            lookup = build_control_identity_lookup(candidates)
            used_targets = set()
            hierarchy_map = []
            for source_name in source_names:
                target = _resolve_from_identity_lookup(
                    source_name,
                    lookup,
                    namespaces=[None],
                    used_targets=used_targets,
                )
                if target is None:
                    hierarchy_map = []
                    break
                used_targets.add(target)
                hierarchy_map.append((source_name, target))
            if len(hierarchy_map) == len(source_names):
                complete_hierarchies.append(hierarchy_map)
        if len(complete_hierarchies) > 1:
            return [
                assignment
                for hierarchy_map in complete_hierarchies
                for assignment in hierarchy_map
            ]
    return list(resolve_target_controls(source_names, selected).items())


def collect_curve_snapshot(attr_path, time_range=None, layer_name=None):
    """Serialize the requested layer curve without using Maya's clipboard."""
    fast_snapshot = curve_transfer.capture_curve(
        attr_path,
        time_range=time_range,
        layer_name=layer_name,
    )
    if fast_snapshot:
        return fast_snapshot

    # Backwards-compatible fallback for unusual curve types.
    key_query = {"q": True}
    tangent_query = {"q": True}
    if time_range:
        key_query["time"] = time_range
        tangent_query["time"] = time_range

    keyframes = cmds.keyframe(attr_path, **key_query)
    values = cmds.keyframe(attr_path, valueChange=True, **key_query)
    if not keyframes or not values:
        return None

    curve_data = {
        "keyframes": list(keyframes),
        "values": list(values),
        "in_tangent": cmds.keyTangent(attr_path, itt=True, **tangent_query) or [],
        "out_tangent": cmds.keyTangent(attr_path, ott=True, **tangent_query) or [],
        "in_angle": cmds.keyTangent(attr_path, inAngle=True, **tangent_query) or [],
        "out_angle": cmds.keyTangent(attr_path, outAngle=True, **tangent_query) or [],
        "in_weight": cmds.keyTangent(attr_path, inWeight=True, **tangent_query) or [],
        "out_weight": cmds.keyTangent(attr_path, outWeight=True, **tangent_query) or [],
        "weighted_tangents": cmds.keyTangent(attr_path, weightedTangents=True, **tangent_query) or [],
        "locked": cmds.keyTangent(attr_path, lock=True, **tangent_query) or [],
        "weight_locked": cmds.keyTangent(attr_path, weightLock=True, **tangent_query) or [],
        "breakdown": cmds.keyframe(attr_path, breakdown=True, **key_query) or [],
    }

    if time_range:
        curve_data["clip_range"] = [float(time_range[0]), float(time_range[1])]

    try:
        curve_data["pre_infinity"] = cmds.setInfinity(attr_path, q=True, pri=True)[0]
        curve_data["post_infinity"] = cmds.setInfinity(attr_path, q=True, poi=True)[0]
    except Exception:
        curve_data["pre_infinity"] = None
        curve_data["post_infinity"] = None

    return curve_data


def _curves_on_layer(candidate_curves, layer_name):
    """Filter candidate curves to one concrete animation layer in bulk."""
    candidate_curves = list(dict.fromkeys(candidate_curves or []))
    if not candidate_curves:
        return []

    layer_name = curve_transfer.active_animation_layer() if layer_name is None else layer_name
    try:
        layers = cmds.ls(type="animLayer") or []
    except Exception:
        layers = []
    non_base_curves = set()
    requested_curves = set()
    for name in layers:
        if curve_transfer.is_base_layer(name):
            continue
        try:
            owned = cmds.animLayer(name, query=True, animCurves=True) or []
        except Exception:
            owned = []
        owned = set(owned)
        non_base_curves.update(owned)
        if name == layer_name:
            requested_curves.update(owned)

    if curve_transfer.is_base_layer(layer_name):
        return [curve for curve in candidate_curves if curve not in non_base_curves]
    return [curve for curve in candidate_curves if curve in requested_curves]


def collect_animation_for_controls(controls, time_range=None, layer_name=None):
    """Capture selected controls with one curve query instead of per-attribute scans."""
    controls = list_selected_transform_targets(controls)
    if not controls:
        return {}, {}
    layer_name = curve_transfer.active_animation_layer() if layer_name is None else layer_name
    try:
        candidate_curves = cmds.keyframe(controls, query=True, name=True) or []
    except Exception:
        candidate_curves = []
    curves = _curves_on_layer(candidate_curves, layer_name)
    if not curves:
        return {}, {}

    selected_set = set(controls)
    animation_data = {}
    source_controls = {}
    for curve in curves:
        try:
            driven_plugs = curve_transfer.driven_plugs_for_curve(curve)
        except Exception:
            driven_plugs = []
        for plug in driven_plugs:
            if not plug or "." not in plug:
                continue
            driven_node, driven_attr = plug.rsplit(".", 1)
            control = _long_transform_name(driven_node)
            if not control or control not in selected_set:
                continue
            channel = _long_attribute_name(control, driven_attr.split("[", 1)[0])
            attr_path = "{}.{}".format(control, channel)
            if not cmds.objExists(attr_path):
                continue
            snapshot = curve_transfer.capture_curve(
                curve,
                time_range=time_range,
                layer_name=layer_name,
            )
            if not snapshot:
                continue
            ctrl_key = get_control_storage_key(control)
            animation_data.setdefault(ctrl_key, {})
            if channel not in animation_data[ctrl_key]:
                animation_data[ctrl_key][channel] = snapshot
                source_controls[ctrl_key] = control
            break
    return animation_data, source_controls


def _long_transform_name(node):
    """Return a stable long transform path for a driven plug node."""
    try:
        matches = cmds.ls(node, long=True, type="transform") or []
    except Exception:
        matches = []
    if matches:
        return matches[0]
    # Shape animation is not a control channel. Mapping a shape's visibility
    # curve onto its parent transform creates a different animation result.
    return None


def _long_attribute_name(node, attr):
    try:
        return cmds.attributeQuery(attr, node=node, longName=True) or attr
    except Exception:
        return attr


def _collect_selected_graph_animation(selected):
    """Capture exactly the selected Graph Editor keys and concrete curves.

    This deliberately bypasses the currently preferred animation layer: a
    selected Graph Editor curve already identifies the exact layer the user
    intends to copy. The destination still goes to its active layer at paste
    time.
    """
    selected_curve_times = curve_transfer.selected_curve_key_times()
    if not selected_curve_times:
        return {}, {}

    selected_transforms = set(list_selected_transform_targets(selected))
    animation_data = {}
    source_controls = {}

    for curve, key_times in selected_curve_times.items():
        driven_plugs = curve_transfer.driven_plugs_for_curve(curve)
        candidates = []
        for plug in driven_plugs:
            if not plug or "." not in plug:
                continue
            driven_node, driven_attr = plug.rsplit(".", 1)
            control = _long_transform_name(driven_node)
            if not control:
                continue
            channel = _long_attribute_name(control, driven_attr.split("[", 1)[0])
            candidates.append((control, channel))

        if selected_transforms:
            selected_candidates = [
                item for item in candidates if item[0] in selected_transforms
            ]
            if selected_candidates:
                candidates = selected_candidates
        if not candidates:
            continue

        control, channel = candidates[0]
        source_layer = curve_transfer.animation_layer_for_curve(
            "{}.{}".format(control, channel),
            curve,
        )
        snapshot = curve_transfer.capture_curve(
            curve,
            layer_name=source_layer,
            key_times=key_times,
        )
        if not snapshot:
            continue

        ctrl_key = get_control_storage_key(control)
        animation_data.setdefault(ctrl_key, {})
        # One channel cannot represent two source layers simultaneously. Keep
        # the first concrete curve instead of replacing it by enumeration order.
        if channel in animation_data[ctrl_key]:
            continue
        animation_data[ctrl_key][channel] = snapshot
        source_controls[ctrl_key] = control

    return animation_data, source_controls


# ═══════════════════════════════════════════════════════════════════════════════
#                           MIRROR PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

PATRONES_MIRROR = [
    ("_L_", "_R_"), ("_R_", "_L_"),
    ("_l_", "_r_"), ("_r_", "_l_"),
    ("_L",  "_R"),  ("_R",  "_L"),
    ("_l",  "_r"),  ("_r",  "_l"),
    ("L_",  "R_"),  ("R_",  "L_"),
    ("l_",  "r_"),  ("r_",  "l_"),
    ("Left", "Right"), ("Right", "Left"),
    ("left", "right"), ("right", "left"),
    ("LEFT", "RIGHT"), ("RIGHT", "LEFT"),
    ("Lft", "Rgt"),  ("Rgt", "Lft"),
    ("lft", "rgt"),  ("rgt", "lft"),
    ("LFT", "RGT"),  ("RGT", "LFT"),
]

# BUG FIX 5: MIRROR_ATTRS was hardcoded to only flip translateX/rotateY/rotateZ.
# This is only correct for rigs mirrored across the YZ plane (X-axis symmetry).
# The set has been extended to cover common secondary axes AND is user-overridable
# at runtime via set_mirror_attrs().
#
# Default: standard X-axis mirror (left/right character symmetry)
#   Flip: translateX, rotateY, rotateZ
# If your rig mirrors across a different axis, call set_mirror_attrs() with the
# correct set before using Paste Opposite.
MIRROR_ATTRS = {'translateX', 'rotateY', 'rotateZ'}


def set_mirror_attrs(attrs_set):
    """
    Override which attributes are sign-flipped during Paste Opposite.

    Common configurations:
      X-axis mirror (default): {'translateX', 'rotateY', 'rotateZ'}
      Y-axis mirror:           {'translateY', 'rotateX', 'rotateZ'}
      Z-axis mirror:           {'translateZ', 'rotateX', 'rotateY'}

    Args:
        attrs_set (set): Set of attribute names to flip (e.g. {'translateX', 'rotateY', 'rotateZ'})
    """
    global MIRROR_ATTRS
    MIRROR_ATTRS = set(attrs_set)


def find_mirror_control(control_name):
    """Find the mirror control name"""
    for pattern, opposite_pattern in PATRONES_MIRROR:
        if pattern in control_name:
            return control_name.replace(pattern, opposite_pattern, 1)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#                   ANIMATION CLIP STATE
# ═══════════════════════════════════════════════════════════════════════════════

_BUFFER_PREFIX = "ANIMKEY_BUF_"

# {control identity: {channel: serialized curve}}
_anim_buffer = {}
_anim_sources = {}
_anim_source_snapshots = {}
_last_disk_mtime = 0
_buffer_is_current = False
_pose_buffer = None
_pose_buffer_mtime = 0
_pose_buffer_is_current = False
_json_write_lock = threading.Lock()
_json_write_generation = {}


def _cleanup_buffer():
    """Clear the clip and remove buffer nodes left by AnimKey versions before v3."""
    global _anim_buffer, _anim_sources, _anim_source_snapshots
    global _last_disk_mtime, _buffer_is_current
    nodes = cmds.ls(f"{_BUFFER_PREFIX}*", type='transform')
    if nodes:
        try:
            cmds.delete(nodes)
        except Exception:
            pass
    _anim_buffer = {}
    _anim_sources = {}
    _anim_source_snapshots = {}
    _last_disk_mtime = 0
    _buffer_is_current = True


def _safe_name(ctrl_short):
    """Sanitise a control's short name so it is a valid Maya node name."""
    return ctrl_short.replace("|", "_").replace(":", "_").replace(".", "_")


def _get_buffer_node(ctrl_short):
    """Return (and create if needed) the hidden buffer node for *ctrl_short*."""
    name = f"{_BUFFER_PREFIX}{_safe_name(ctrl_short)}"
    if not cmds.objExists(name):
        name = cmds.createNode('transform', name=name)
        cmds.setAttr(f"{name}.visibility", 0)
        try:
            cmds.setAttr(f"{name}.hiddenInOutliner", 1)
        except Exception:
            pass
    return name


def _ensure_attr(node, channel):
    """Make sure *channel* exists as a keyable double attr on *node*."""
    if not cmds.attributeQuery(channel, node=node, exists=True):
        cmds.addAttr(node, longName=channel, attributeType='double', keyable=True)


def _split_attr_path(attr_path):
    """Split node.attr while keeping full DAG paths intact."""
    return attr_path.rsplit(".", 1)


def _curve_time_range(curve_data):
    keyframes = curve_data.get("keyframes", []) if isinstance(curve_data, dict) else []
    if not keyframes:
        return None
    return min(keyframes), max(keyframes)


def _curve_time_range_from_attr(attr_path):
    try:
        keyframes = cmds.keyframe(attr_path, query=True) or []
    except Exception:
        keyframes = []
    if not keyframes:
        return None
    return min(keyframes), max(keyframes)


def _offset_range(time_range, offset):
    if time_range is None:
        return None
    if offset is None:
        return time_range
    return time_range[0] + offset, time_range[1] + offset


def _target_attr_is_editable(attr_path):
    try:
        node_name, channel = _split_attr_path(attr_path)
        if not cmds.objExists(node_name):
            return False
        if not cmds.attributeQuery(channel, node=node_name, exists=True):
            return False
        if cmds.getAttr(attr_path, lock=True):
            return False
    except Exception:
        return False
    return True


def _clear_keys_without_touching_clipboard(attr_path, time_range=None, clear_entire_curve=False):
    node_name, channel = _split_attr_path(attr_path)
    try:
        if clear_entire_curve or time_range is None:
            cmds.cutKey(
                node_name,
                attribute=channel,
                time=(-1000000, 1000000),
                option="keys",
                clear=True,
            )
        else:
            cmds.cutKey(
                node_name,
                attribute=channel,
                time=time_range,
                option="keys",
                clear=True,
            )
    except TypeError:
        try:
            if clear_entire_curve or time_range is None:
                cmds.cutKey(node_name, attribute=channel, time=(-1000000, 1000000), option="keys", cl=True)
            else:
                cmds.cutKey(node_name, attribute=channel, time=time_range, option="keys", cl=True)
        except Exception:
            pass
    except Exception:
        pass


def _lists_match(a, b, tolerance=0.0001):
    if len(a or []) != len(b or []):
        return False
    for left, right in zip(a or [], b or []):
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if abs(float(left) - float(right)) > tolerance:
                return False
        elif left != right:
            return False
    return True


def _source_still_matches_snapshot(source_attr, curve_data):
    time_range = _curve_time_range(curve_data)
    if time_range is None:
        return False
    try:
        snapshot = collect_curve_snapshot(source_attr, time_range)
    except Exception:
        return False
    if not snapshot:
        return False
    return (
        _lists_match(snapshot.get("keyframes", []), curve_data.get("keyframes", [])) and
        _lists_match(snapshot.get("values", []), curve_data.get("values", [])) and
        _lists_match(snapshot.get("in_tangent", []), curve_data.get("in_tangent", []), tolerance=0.0) and
        _lists_match(snapshot.get("out_tangent", []), curve_data.get("out_tangent", []), tolerance=0.0)
    )


def _copy_keys_native(source_attr, target_attr, curve_data=None, clear_existing=True, clear_entire_curve=False, time_offset=None):
    """Paste with Maya's native clipboard so tangent data survives as faithfully as possible."""
    if not _target_attr_is_editable(target_attr):
        return False

    try:
        source_node, source_channel = _split_attr_path(source_attr)
        target_node, target_channel = _split_attr_path(target_attr)
    except Exception:
        return False

    if not cmds.objExists(source_node):
        return False
    try:
        if not cmds.attributeQuery(source_channel, node=source_node, exists=True):
            return False
    except Exception:
        return False

    source_range = _curve_time_range(curve_data) or _curve_time_range_from_attr(source_attr)
    target_range = _offset_range(source_range, time_offset)

    try:
        if clear_existing:
            _clear_keys_without_touching_clipboard(
                target_attr,
                time_range=target_range,
                clear_entire_curve=clear_entire_curve,
            )

        copy_kwargs = {}
        if source_range is not None:
            copy_kwargs["time"] = source_range
        copied = cmds.copyKey(source_node, attribute=source_channel, **copy_kwargs)
        if not copied:
            return False

        paste_kwargs = {
            "attribute": target_channel,
            "option": "replace" if clear_existing else "merge",
        }
        if time_offset is not None:
            paste_kwargs["timeOffset"] = time_offset
            paste_kwargs["option"] = "merge"

        pasted = cmds.pasteKey(target_node, **paste_kwargs)
        if isinstance(pasted, (int, float)):
            return pasted > 0
        return pasted is None or bool(pasted)
    except TypeError:
        return False
    except Exception:
        return False


def _apply_curve_via_temp_native(attr_path, curve_data, clear_existing=True, clear_entire_curve=False, time_offset=None):
    """Fallback: rebuild onto a temp curve, then native-paste to the rig attr."""
    temp_node = None
    try:
        _, channel = _split_attr_path(attr_path)
        temp_node = cmds.createNode("transform", name=f"{_BUFFER_PREFIX}TMP")
        cmds.setAttr(f"{temp_node}.visibility", 0)
        try:
            cmds.setAttr(f"{temp_node}.hiddenInOutliner", 1)
        except Exception:
            pass
        _ensure_attr(temp_node, channel)
        temp_attr = f"{temp_node}.{channel}"
        if not apply_curve_snapshot(temp_attr, curve_data, clear_entire_curve=True, clear_existing=True):
            return False
        return _copy_keys_native(
            temp_attr,
            attr_path,
            curve_data,
            clear_existing=clear_existing,
            clear_entire_curve=clear_entire_curve,
            time_offset=time_offset,
        )
    except Exception:
        return False
    finally:
        if temp_node and cmds.objExists(temp_node):
            try:
                cmds.delete(temp_node)
            except Exception:
                pass


def apply_curve_snapshot(attr_path, curve_data, clear_entire_curve=True, clear_existing=True):
    """Restore serialized curve data onto an attribute."""
    keyframes = curve_data.get("keyframes", [])
    values = curve_data.get("values", [])
    if not keyframes or not values:
        return False

    node_name, channel = _split_attr_path(attr_path)

    if clear_existing:
        try:
            if clear_entire_curve:
                cmds.cutKey(node_name, attribute=channel, time=(-1000000, 1000000), option="keys", clear=True)
            else:
                cmds.cutKey(
                    node_name,
                    attribute=channel,
                    time=(keyframes[0], keyframes[-1]),
                    option="keys",
                    clear=True
                )
        except TypeError:
            try:
                if clear_entire_curve:
                    cmds.cutKey(node_name, attribute=channel, time=(-1000000, 1000000), option="keys", cl=True)
                else:
                    cmds.cutKey(node_name, attribute=channel, time=(keyframes[0], keyframes[-1]), option="keys", cl=True)
            except Exception:
                pass
        except Exception:
            pass

    for frame, value in zip(keyframes, values):
        try:
            cmds.setKeyframe(node_name, attribute=channel, time=frame, value=value)
        except Exception:
            pass

    weighted = curve_data.get("weighted_tangents", [])
    if weighted:
        try:
            cmds.keyTangent(attr_path, e=True, weightedTangents=bool(weighted[0] if len(weighted) == 1 else any(weighted)))
        except Exception:
            pass

    itt_list = curve_data.get("in_tangent", [])
    ott_list = curve_data.get("out_tangent", [])
    ia_list = curve_data.get("in_angle", [])
    oa_list = curve_data.get("out_angle", [])
    iw_list = curve_data.get("in_weight", [])
    ow_list = curve_data.get("out_weight", [])
    lock_list = curve_data.get("locked", [])
    weight_lock_list = curve_data.get("weight_locked", [])
    breakdown_list = curve_data.get("breakdown", [])

    for i, frame in enumerate(keyframes):
        time_value = (frame, frame)
        try:
            if i < len(itt_list) and itt_list[i]:
                cmds.keyTangent(attr_path, e=True, time=time_value, itt=itt_list[i])
        except Exception:
            pass
        try:
            if i < len(ott_list) and ott_list[i]:
                cmds.keyTangent(attr_path, e=True, time=time_value, ott=ott_list[i])
        except Exception:
            pass
        try:
            if i < len(ia_list) and ia_list[i] is not None:
                cmds.keyTangent(attr_path, e=True, time=time_value, inAngle=ia_list[i])
        except Exception:
            pass
        try:
            if i < len(oa_list) and oa_list[i] is not None:
                cmds.keyTangent(attr_path, e=True, time=time_value, outAngle=oa_list[i])
        except Exception:
            pass
        try:
            if i < len(iw_list) and iw_list[i] is not None:
                cmds.keyTangent(attr_path, e=True, time=time_value, inWeight=iw_list[i])
        except Exception:
            pass
        try:
            if i < len(ow_list) and ow_list[i] is not None:
                cmds.keyTangent(attr_path, e=True, time=time_value, outWeight=ow_list[i])
        except Exception:
            pass
        try:
            if i < len(lock_list):
                cmds.keyTangent(attr_path, e=True, time=time_value, lock=bool(lock_list[i]))
        except Exception:
            pass
        try:
            if i < len(weight_lock_list):
                cmds.keyTangent(attr_path, e=True, time=time_value, weightLock=bool(weight_lock_list[i]))
        except Exception:
            pass
        try:
            if i < len(breakdown_list) and breakdown_list[i]:
                cmds.keyframe(attr_path, e=True, time=time_value, breakdown=True)
        except Exception:
            pass

    try:
        pre_infinity = curve_data.get("pre_infinity")
        post_infinity = curve_data.get("post_infinity")
        kwargs = {}
        if pre_infinity:
            kwargs["pri"] = pre_infinity
        if post_infinity:
            kwargs["poi"] = post_infinity
        if kwargs:
            cmds.setInfinity(attr_path, **kwargs)
    except Exception:
        pass

    return True


def offset_curve_snapshot(curve_data, time_offset):
    """Return a copy of curve data with all key times offset."""
    return curve_transfer.offset_curve_data(curve_data, time_offset)


def load_animation_snapshot_data():
    """Load the serialized animation snapshot from disk."""
    json_path = get_copy_paste_animation_file()
    if not os.path.exists(json_path):
        return {}

    try:
        with open(json_path, "r") as f:
            data = json.load(f)
        return data.get("animation", data) or {}
    except Exception:
        return {}


def get_animation_frame_range(animation_data):
    """Return the full frame range covered by the animation payload."""
    all_frames = []
    for channels in animation_data.values():
        for curve_data in channels.values():
            clip_range = curve_data.get("clip_range")
            if isinstance(clip_range, (list, tuple)) and len(clip_range) == 2:
                all_frames.extend(clip_range)
            else:
                all_frames.extend(curve_data.get("keyframes", []))
    if not all_frames:
        return None
    return min(all_frames), max(all_frames)


def _is_numeric_pose_value(value):
    """Return True for values that can be safely stored as a single pose key."""
    return isinstance(value, (int, float, bool))


def _make_single_frame_curve_snapshot(value, frame):
    """Create a one-key animation payload for a pose value."""
    return {
        "keyframes": [frame],
        "values": [float(value)],
        "in_tangent": ["auto"],
        "out_tangent": ["auto"],
        "in_angle": [],
        "out_angle": [],
        "in_weight": [],
        "out_weight": [],
        "weighted_tangents": [],
        "locked": [],
        "weight_locked": [],
        "breakdown": [False],
        "pre_infinity": None,
        "post_infinity": None,
    }


def _extract_pose_value(curve_data):
    """Return the stored pose value from a one-frame curve payload."""
    values = curve_data.get("values", []) if isinstance(curve_data, dict) else []
    if not values:
        return None
    value = values[-1]
    return float(value) if isinstance(value, (int, float, bool)) else None


def apply_animation_data_to_scene(
    animation_data,
    selected_objects=None,
    time_offset=None,
    preferred_namespace=None,
    paste_mode="replace"
):
    """Apply serialized animation data to matching controls in the current scene."""
    selected_objects = cmds.ls(selection=True, long=True) if selected_objects is None else selected_objects
    selected_objects = selected_objects or []
    if preferred_namespace is None:
        target_assignments = resolve_animation_target_assignments(
            animation_data.keys(),
            selected_objects,
        )
    else:
        target_map = resolve_target_controls_for_namespace(
            animation_data.keys(),
            namespace=preferred_namespace,
            selected=selected_objects
        )
        target_assignments = list(target_map.items())
    if not target_assignments:
        cmds.warning("AnimKey: No matching target controls found.")
        return 0, 0

    applied = 0
    skipped = 0
    target_layer = curve_transfer.active_animation_layer()
    cmds.undoInfo(openChunk=True)
    refresh_suspended = False
    try:
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            pass

        transfers, prevalidated, skipped_channels = _build_animation_transfers(
            target_assignments,
            animation_data,
        )
        skipped += skipped_channels
        results = curve_transfer.paste_curves_batch(
            transfers,
            layer_name=target_layer,
            time_offset=float(time_offset or 0.0),
            clear_existing=paste_mode != "insert",
            prevalidated_attr_paths=prevalidated,
        )
        applied += sum(1 for result in results if result)
        skipped += sum(1 for result in results if not result)
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        cmds.undoInfo(closeChunk=True)

    return applied, skipped


def load_animation_file(file_path):
    """Load an animation file and return meta and animation payload."""
    with open(file_path, "r") as f:
        data = json.load(f)
    meta = data.get("meta", {})
    animation_data = data.get("animation", data)
    return meta, animation_data


def trim_curve_data_to_range(curve_data, start_frame, end_frame):
    """Return curve data trimmed to a specific frame range."""
    return curve_transfer.trim_curve_data(curve_data, start_frame, end_frame)


def trim_animation_data_to_range(animation_data, start_frame, end_frame):
    """Trim all channels in an animation payload to the requested range."""
    trimmed_animation = {}
    for ctrl_name, channels in animation_data.items():
        trimmed_channels = {}
        for channel, curve_data in channels.items():
            trimmed_curve = trim_curve_data_to_range(curve_data, start_frame, end_frame)
            if trimmed_curve:
                trimmed_channels[channel] = trimmed_curve
        if trimmed_channels:
            trimmed_animation[ctrl_name] = trimmed_channels
    return trimmed_animation


def resolve_target_controls_for_namespace(source_names, namespace=None, selected=None):
    """Resolve controls against a preferred namespace or the current selection."""
    if namespace == "__selection__":
        return resolve_target_controls(source_names, selected=selected)

    scene_controls = cmds.ls(type="transform", long=True) or []
    scene_lookup = build_control_identity_lookup(scene_controls)
    resolved = {}
    namespace_filter = [namespace] if namespace is not None else [None]
    used_targets = set()
    for source_name in source_names:
        target = _resolve_from_identity_lookup(
            source_name,
            scene_lookup,
            namespaces=namespace_filter,
            used_targets=used_targets,
        )
        if target is not None:
            resolved[source_name] = target
            used_targets.add(target)
    return resolved


def list_target_rig_options(animation_data):
    """Return dropdown options for possible rig targets."""
    options = [{"label": "Current Selection", "value": "__selection__"}]
    scene_controls = cmds.ls(type="transform", long=True) or []
    namespace_matches = {}
    source_names = list(animation_data.keys())
    source_short_names = {get_control_short_name(source_name) for source_name in source_names}

    for control in scene_controls:
        short_name = get_control_short_name(control)
        if short_name not in source_short_names:
            continue
        namespace = get_control_namespace(control) or "__root__"
        namespace_matches.setdefault(namespace, set()).add(short_name)

    sorted_options = sorted(
        namespace_matches.items(),
        key=lambda item: (-len(item[1]), item[0])
    )
    for namespace, matches in sorted_options:
        label = "Scene Root" if namespace == "__root__" else namespace
        options.append({
            "label": f"{label} ({len(matches)}/{len(source_names)})",
            "value": "" if namespace == "__root__" else namespace,
        })
    return options


def list_animation_library_entries():
    """Return available animation backup entries sorted by newest first."""
    backup_folder = get_animation_backup_folder()
    if not os.path.exists(backup_folder):
        return []

    entries = []
    for name in os.listdir(backup_folder):
        if not name.endswith(".animkey_anim"):
            continue
        file_path = os.path.join(backup_folder, name)
        try:
            meta, animation_data = load_animation_file(file_path)
            frame_range = meta.get("frame_range")
            if not isinstance(frame_range, (list, tuple)) or len(frame_range) != 2:
                frame_range = get_animation_frame_range(animation_data)

            gif_path = _resolve_preview_gif_path(file_path, meta)

            entries.append({
                "name": os.path.splitext(name)[0],
                "file_path": file_path,
                "gif_path": gif_path,
                "frame_range": frame_range,
                "controls_count": len(animation_data),
                "modified": os.path.getmtime(file_path),
                "meta": meta,
                "animation": animation_data,
            })
        except Exception:
            pass

    entries.sort(key=lambda entry: entry["modified"], reverse=True)
    return entries


def _schedule_json_write(file_path, data, indent=None, on_written=None):
    """Persist pure Python data off Maya's UI thread, keeping newest-write wins."""
    global _json_write_generation
    generation = _json_write_generation.get(file_path, 0) + 1
    _json_write_generation[file_path] = generation

    def _writer():
        with _json_write_lock:
            if generation < _json_write_generation.get(file_path, 0):
                return
            try:
                atomic_write_json(file_path, data, indent=indent)
                mtime = os.path.getmtime(file_path)
                if on_written:
                    on_written(mtime)
            except Exception:
                pass

    worker = threading.Thread(
        target=_writer,
        name="AnimKeyJsonWriter",
    )
    worker.daemon = True
    worker.start()
    return worker


def _export_buffer_to_disk(save_to_library=False):
    global _anim_buffer, _last_disk_mtime, _buffer_is_current
    if not _anim_buffer:
        _buffer_is_current = True
        json_path = get_copy_paste_animation_file()
        try:
            if os.path.exists(json_path):
                os.remove(json_path)
        except Exception:
            pass
        return

    source_controls_meta = {
        ctrl_key: {
            "short_name": get_control_short_name(ctrl_key),
            "path_signature": get_control_path_signature(ctrl_key),
        }
        for ctrl_key in _anim_buffer
    }
    animation_data = {
        "animation": _anim_buffer,
        "meta": {
            "source_controls": source_controls_meta,
            "format_version": 3,
            "source_layer": curve_transfer.active_animation_layer(),
        },
    }

    json_path = get_copy_paste_animation_file()
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    try:
        if save_to_library:
            atomic_write_json(json_path, animation_data, indent=None)
            _last_disk_mtime = os.path.getmtime(json_path)
            # Also save to library/backup folder
            backup_folder = get_animation_backup_folder()
            latest_backup = get_latest_animation_backup_file()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            snapshot_backup = os.path.join(backup_folder, f"anim_backup_{timestamp}.animkey_anim")

            atomic_write_json(latest_backup, animation_data, indent=2)
            atomic_write_json(snapshot_backup, animation_data, indent=2)
            
            # If library window is open, refresh it
            global _library_window
            if _library_window and _library_window.isVisible():
                _library_window.refresh_library()
        else:
            def _mark_written(mtime):
                global _last_disk_mtime
                _last_disk_mtime = mtime

            _schedule_json_write(
                json_path,
                animation_data,
                indent=None,
                on_written=_mark_written,
            )
        _buffer_is_current = True
    except Exception:
        pass


def _sync_buffer_from_disk():
    global _anim_buffer, _anim_sources, _anim_source_snapshots
    global _last_disk_mtime, _buffer_is_current
    json_path = get_copy_paste_animation_file()
    if not os.path.exists(json_path):
        return
        
    try:
        mtime = os.path.getmtime(json_path)
        if _buffer_is_current and not _last_disk_mtime:
            return
        if _anim_buffer and _last_disk_mtime and mtime <= _last_disk_mtime:
            return
            
        with open(json_path, "r") as f:
            data = json.load(f)
            
        animation_data = data.get("animation", data)
        if not animation_data:
            return
            
        _cleanup_buffer()
        _anim_sources = {}
        _anim_source_snapshots = {}
        _anim_buffer = {
            ctrl_key: {
                channel: curve_data
                for channel, curve_data in channels_data.items()
                if curve_data.get("keyframes")
            }
            for ctrl_key, channels_data in animation_data.items()
            if isinstance(channels_data, dict)
        }
        _anim_buffer = {
            ctrl_key: channels
            for ctrl_key, channels in _anim_buffer.items()
            if channels
        }

        _last_disk_mtime = mtime
        _buffer_is_current = True
    except Exception:
        pass


_library_window = None
_save_window = None


# ═══════════════════════════════════════════════════════════════════════════════
#                       VIEWPORT GIF CAPTURE HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _capture_viewport_gif(output_path, start_frame, end_frame, size=128, step=2):
    """
    Capture a small animated GIF of the viewport for preview thumbnails.

    Uses Maya playblast to grab frames, then assembles them into a GIF.
    Returns the output path on success, None on failure.
    """
    import tempfile
    import glob
    import struct
    import shutil

    temp_dir = tempfile.mkdtemp(prefix="animkey_gif_")
    try:
        # Playblast individual frames to temp dir
        frame_files = []
        original_time = cmds.currentTime(query=True)

        # Determine which frames to capture (skip frames for smaller GIF)
        frames_to_capture = list(range(int(start_frame), int(end_frame) + 1, step))
        if not frames_to_capture:
            return None

        for frame in frames_to_capture:
            cmds.currentTime(frame, edit=True)
            temp_file = os.path.join(temp_dir, f"frame_{int(frame):06d}")
            try:
                result = cmds.playblast(
                    startTime=frame,
                    endTime=frame,
                    format="image",
                    compression="png",
                    quality=50,
                    widthHeight=[size, size],
                    showOrnaments=False,
                    viewer=False,
                    offScreen=True,
                    filename=temp_file,
                    forceOverwrite=True,
                    framePadding=6,
                    percent=100,
                )
                if result:
                    # Maya appends frame number to filename
                    pattern = temp_file + ".*"
                    found = glob.glob(pattern)
                    if found:
                        frame_files.append(found[0])
            except Exception:
                pass

        cmds.currentTime(original_time, edit=True)

        if len(frame_files) < 2:
            return None

        # Assemble GIF from PNG frames using a pure-Python approach
        # Load images via Qt since we already have PySide available
        gif_frames = []
        for fp in frame_files:
            img = QtGui.QImage(fp)
            if img.isNull():
                continue
            scaled = img.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            gif_frames.append(scaled)

        if len(gif_frames) < 2:
            return None

        # Write as animated GIF using QImageWriter (if available) or
        # fall back to saving just a static thumbnail
        try:
            # Try using imageio if available (common in Maya envs)
            import imageio
            frames_np = []
            for qimg in gif_frames:
                qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                ptr = qimg.bits()
                w, h = qimg.width(), qimg.height()
                # PySide2 returns a memoryview or shiboken wrapper
                try:
                    ptr.setsize(w * h * 4)
                except Exception:
                    pass
                import numpy as np
                arr = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape((h, w, 4))
                frames_np.append(arr[:, :, :3])  # drop alpha for GIF

            # Calculate frame duration: ~24fps feels smooth, but cap for small gifs
            fps = max(4, min(24, len(frames_np)))
            duration = 1.0 / fps

            imageio.mimsave(output_path, frames_np, duration=duration, loop=0)
            return output_path
        except ImportError:
            pass

        # Fallback: save first frame as static PNG thumbnail
        static_path = os.path.splitext(output_path)[0] + ".gif"
        gif_frames[0].save(static_path, "PNG")
        if os.path.exists(static_path):
            return static_path
        return None

    except Exception as e:
        cmds.warning(f"AnimKey: GIF capture failed: {e}")
        return None
    finally:
        # Clean up temp dir
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#                       SAVE ANIMATION WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

class SaveAnimationWindow(QtWidgets.QWidget):
    """Popup dialog for saving animation to the library with custom name, range, and GIF."""

    def __init__(self, anchor_button=None, parent=None):
        if parent is None:
            from AnimKey.mods import uiMod
            parent = uiMod.get_maya_main_window()
        super(SaveAnimationWindow, self).__init__(parent)
        self.setObjectName("AnimKey_SaveAnimation")
        self.anchor_button = anchor_button
        self.theme = ThemeManager.get_current_theme()
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self._tail_height = 10
        self._tail_width = 16
        self._tail_x = 0
        self._tail_on_top = False
        self.resize(340, 370)
        self._build_ui()
        self._position_window()

    # ── Paint: rounded rect + speech-bubble tail ──────────────────────────
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        bg = QtGui.QColor(30, 30, 30)
        border_color = QtGui.QColor(51, 51, 51)

        if self._tail_on_top:
            body_rect = QtCore.QRectF(0.5, self._tail_height + 0.5,
                                       self.width() - 1,
                                       self.height() - self._tail_height - 1)
        else:
            body_rect = QtCore.QRectF(0.5, 0.5,
                                       self.width() - 1,
                                       self.height() - self._tail_height - 1)

        path = QtGui.QPainterPath()
        path.addRoundedRect(body_rect, 10, 10)

        tail_cx = max(20, min(self._tail_x, self.width() - 20))
        hw = self._tail_width / 2

        if self._tail_on_top:
            tail_base = body_rect.top()
            path.moveTo(tail_cx - hw, tail_base)
            path.lineTo(tail_cx, tail_base - self._tail_height)
            path.lineTo(tail_cx + hw, tail_base)
        else:
            tail_base = body_rect.bottom()
            path.moveTo(tail_cx - hw, tail_base)
            path.lineTo(tail_cx, tail_base + self._tail_height)
            path.lineTo(tail_cx + hw, tail_base)
        path.closeSubpath()

        self.setMask(path.toFillPolygon().toPolygon())

        painter.setPen(QtGui.QPen(border_color, 1))
        painter.setBrush(bg)
        painter.drawPath(path)
        painter.end()

    # ── Build UI ──────────────────────────────────────────────────────────
    def _build_ui(self):
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setContentsMargins(1, 1, 1, self._tail_height + 1)
        outer_layout.setSpacing(0)

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("saveAnimContainer")
        self.container.setStyleSheet("""
            QFrame#saveAnimContainer {
                background-color: transparent;
                border: none;
            }
        """)
        outer_layout.addWidget(self.container)

        layout = QtWidgets.QVBoxLayout(self.container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ── Header ──
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Save Animation")
        title.setStyleSheet("color: #a3be8c; font-size: 13px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Subtitle ──
        subtitle = QtWidgets.QLabel("Save animation from selected controls to the library.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #9ca3af; font-size: 10px;")
        layout.addWidget(subtitle)

        # ── Shared form styles ──
        form_style = """
            QLabel { color: #9ca3af; font-size: 9px; font-weight: 700; border: none; }
            QLineEdit, QSpinBox {
                color: #FFF;
                background-color: #2D2D2D;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 6px 8px;
                font-size: 11px;
            }
            QLineEdit:hover, QSpinBox:hover { border-color: #555; }
            QLineEdit:focus, QSpinBox:focus { border-color: #a3be8c; }
            QCheckBox {
                color: #d7dbe1;
                font-size: 11px;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border-radius: 3px;
                border: 1px solid #555;
                background-color: #2D2D2D;
            }
            QCheckBox::indicator:checked {
                background-color: #a3be8c;
                border-color: #a3be8c;
            }
            QCheckBox::indicator:hover { border-color: #a3be8c; }
        """
        form_widget = QtWidgets.QWidget()
        form_widget.setStyleSheet(form_style)
        form_layout = QtWidgets.QVBoxLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(8)

        # ── File Name ──
        name_label = QtWidgets.QLabel("FILE NAME")
        form_layout.addWidget(name_label)

        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("e.g. walk_cycle, idle_pose...")
        # Default: scene name + timestamp
        scene_name = cmds.file(query=True, sceneName=True, shortName=True) or "untitled"
        scene_base = os.path.splitext(scene_name)[0]
        timestamp = datetime.now().strftime("%H%M%S")
        self.name_input.setText(f"{scene_base}_{timestamp}")
        self.name_input.selectAll()
        form_layout.addWidget(self.name_input)

        # ── Frame Range ──
        range_label = QtWidgets.QLabel("FRAME RANGE")
        form_layout.addWidget(range_label)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setSpacing(6)

        self.start_frame = QtWidgets.QSpinBox()
        self.start_frame.setRange(-100000, 100000)
        self.end_frame = QtWidgets.QSpinBox()
        self.end_frame.setRange(-100000, 100000)

        # Default range: timeline selection or playback range
        time_range = get_selected_time_range()
        if time_range:
            self.start_frame.setValue(int(time_range[0]))
            self.end_frame.setValue(int(time_range[1]))
        else:
            self.start_frame.setValue(int(cmds.playbackOptions(query=True, min=True)))
            self.end_frame.setValue(int(cmds.playbackOptions(query=True, max=True)))

        start_col = QtWidgets.QVBoxLayout()
        start_col.setSpacing(2)
        start_lbl = QtWidgets.QLabel("Start")
        start_col.addWidget(start_lbl)
        start_col.addWidget(self.start_frame)

        end_col = QtWidgets.QVBoxLayout()
        end_col.setSpacing(2)
        end_lbl = QtWidgets.QLabel("End")
        end_col.addWidget(end_lbl)
        end_col.addWidget(self.end_frame)

        range_row.addLayout(start_col, 1)
        range_row.addLayout(end_col, 1)
        form_layout.addLayout(range_row)

        # ── GIF Preview Checkbox ──
        self.gif_checkbox = QtWidgets.QCheckBox("Capture GIF preview")
        self.gif_checkbox.setChecked(False)
        self.gif_checkbox.setToolTip(
            "Record a small viewport GIF thumbnail for the library card.\n"
            "This takes a few seconds to capture."
        )
        form_layout.addWidget(self.gif_checkbox)

        layout.addWidget(form_widget)

        # ── Save Button ──
        self.save_btn = QtWidgets.QPushButton("💾  Save to Library")
        self.save_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #a3be8c;
                color: #1e1e1e;
                border: none;
                border-radius: 6px;
                padding: 10px 16px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #b5d19c;
            }
            QPushButton:pressed {
                background-color: #8faa78;
            }
        """)
        self.save_btn.clicked.connect(self._do_save)
        layout.addWidget(self.save_btn)

        # ── Status label (hidden initially) ──
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #a3be8c; font-size: 10px;")
        self.status_label.hide()
        layout.addWidget(self.status_label)

    # ── Position window (same logic as SmartAnimationLibraryWindow) ────────
    def _position_window(self):
        if self.anchor_button is None:
            cursor_pos = QtGui.QCursor.pos()
            self.move(cursor_pos.x() - self.width() // 2,
                      cursor_pos.y() - self.height() - 10)
            self._tail_x = self.width() // 2
            self._tail_on_top = False
            self.update()
            return

        btn_rect = self.anchor_button.rect()
        btn_top_left = self.anchor_button.mapToGlobal(btn_rect.topLeft())
        btn_center_x = btn_top_left.x() + btn_rect.width() // 2
        btn_top_y = btn_top_left.y()
        btn_bottom_y = btn_top_y + btn_rect.height()

        screen_rect = screen_available_geometry(self.anchor_button, btn_top_left)

        popup_w = self.width()
        popup_h = self.height()

        space_above = btn_top_y - screen_rect.top()
        space_below = screen_rect.bottom() - btn_bottom_y

        if space_above >= popup_h:
            y_pos = btn_top_y - popup_h
            self._tail_on_top = False
        elif space_below >= popup_h:
            y_pos = btn_bottom_y
            self._tail_on_top = True
        else:
            if space_above >= space_below:
                y_pos = btn_top_y - popup_h
                self._tail_on_top = False
            else:
                y_pos = btn_bottom_y
                self._tail_on_top = True

        x_pos = btn_center_x - popup_w // 2
        if x_pos < screen_rect.left():
            x_pos = screen_rect.left()
        elif x_pos + popup_w > screen_rect.right():
            x_pos = screen_rect.right() - popup_w

        self.move(x_pos, y_pos)
        self._tail_x = btn_center_x - x_pos

        if self._tail_on_top:
            self.layout().setContentsMargins(1, self._tail_height + 1, 1, 1)
        else:
            self.layout().setContentsMargins(1, 1, 1, self._tail_height + 1)
        self.update()

    # ── Execute Save ──────────────────────────────────────────────────────
    def _do_save(self):
        file_name = self.name_input.text().strip()
        if not file_name:
            self.status_label.setText("⚠ Please enter a file name.")
            self.status_label.setStyleSheet("color: #ebcb8b; font-size: 10px;")
            self.status_label.show()
            return

        # Sanitize filename
        safe_name = re.sub(r'[^\w\-. ]', '_', file_name)
        if not safe_name:
            self.status_label.setText("⚠ Invalid file name.")
            self.status_label.setStyleSheet("color: #ebcb8b; font-size: 10px;")
            self.status_label.show()
            return

        start = self.start_frame.value()
        end = self.end_frame.value()
        if end < start:
            end = start
            self.end_frame.setValue(end)

        capture_gif = self.gif_checkbox.isChecked()

        # Disable save button during operation
        self.save_btn.setEnabled(False)
        self.save_btn.setText("Saving...")
        QtWidgets.QApplication.processEvents()

        try:
            _do_save_to_library(safe_name, start, end, capture_gif, self.anchor_button)

            self.status_label.setText("✓ Animation saved!")
            self.status_label.setStyleSheet("color: #a3be8c; font-size: 10px;")
            self.status_label.show()

            # Auto-close after a short delay
            QtCore.QTimer.singleShot(800, self.close)

        except Exception as e:
            self.save_btn.setEnabled(True)
            self.save_btn.setText("💾  Save to Library")
            self.status_label.setText(f"⚠ Error: {e}")
            self.status_label.setStyleSheet("color: #bf616a; font-size: 10px;")
            self.status_label.show()

    def closeEvent(self, event):
        global _save_window
        _save_window = None
        super(SaveAnimationWindow, self).closeEvent(event)


def _do_save_to_library(file_name, start_frame, end_frame, capture_gif=False, anchor_button=None):
    """
    Collect animation from selected controls and save a single
    .animkey_anim file to the library folder.
    """
    selected = cmds.ls(selection=True, long=True)
    if not selected:
        cmds.warning("AnimKey: Please select at least one control.")
        return

    # Collect animation data from the scene (no buffer nodes needed)
    time_range = (start_frame, end_frame)
    animation_payload, _ = collect_animation_for_controls(
        selected,
        time_range=time_range,
        layer_name=curve_transfer.active_animation_layer(),
    )

    if not animation_payload:
        cmds.warning("AnimKey: No animation found on selected controls in the given range.")
        return

    # Build final data with metadata
    save_data = {
        "meta": {
            "name": file_name,
            "frame_range": [start_frame, end_frame],
            "controls_count": len(animation_payload),
            "scene": cmds.file(query=True, sceneName=True, shortName=True) or "untitled",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format_version": 3,
            "source_layer": curve_transfer.active_animation_layer(),
            "source_controls": {
                ctrl_key: {
                    "short_name": get_control_short_name(ctrl_key),
                    "path_signature": get_control_path_signature(ctrl_key),
                }
                for ctrl_key in animation_payload.keys()
            },
        },
        "animation": animation_payload,
    }

    # Write single file to library folder
    backup_folder = get_animation_backup_folder()
    file_path = os.path.join(backup_folder, f"{file_name}.animkey_anim")

    # If file already exists, ask before overwriting
    if os.path.exists(file_path):
        # Just append a timestamp to avoid conflicts
        timestamp = datetime.now().strftime("%H%M%S")
        file_path = os.path.join(backup_folder, f"{file_name}_{timestamp}.animkey_anim")

    atomic_write_json(file_path, save_data, indent=2)

    # Optionally capture GIF preview
    gif_path = None
    if capture_gif:
        gif_output = os.path.splitext(file_path)[0] + ".gif"
        gif_path = _capture_viewport_gif(gif_output, start_frame, end_frame, size=128, step=2)
        if gif_path:
            # Update meta with gif path
            save_data["meta"]["preview_gif"] = gif_path
            atomic_write_json(file_path, save_data, indent=2)

    # Refresh library window if open
    global _library_window
    if _library_window and _library_window.isVisible():
        _library_window.refresh_library()

    cmds.inViewMessage(
        amg=f"<span style='color:#a3be8c'>Saved: {file_name}</span>",
        pos='topCenter',
        fade=True,
        fadeStayTime=2000,
    )


def show_save_animation_window(anchor_button=None):
    """Show the Save Animation dialog."""
    global _save_window
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows()
    if _save_window is not None:
        try:
            if _save_window.isVisible():
                _save_window.close()
                return None
        except RuntimeError:
            _save_window = None
        except Exception:
            _save_window = None

    _save_window = SaveAnimationWindow(anchor_button=anchor_button)
    _save_window.show()
    _save_window.raise_()
    _save_window.activateWindow()
    return _save_window


class AnimationLibraryCard(QtWidgets.QFrame):
    """Preview card for one saved animation."""

    selected = QtCore.Signal(dict)

    def __init__(self, entry, parent=None):
        super(AnimationLibraryCard, self).__init__(parent)
        self.entry = entry
        self.movie = None
        self.preview = None
        self.setObjectName("animationLibraryCard")
        self.setFixedHeight(80)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.theme = ThemeManager.get_current_theme()
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self._apply_card_style(False)

        preview = QtWidgets.QLabel()
        self.preview = preview
        preview.setAlignment(QtCore.Qt.AlignCenter)
        preview.setFixedSize(64, 64)
        preview.setStyleSheet("""
            QLabel {
                background-color: #1a1a1a;
                color: #666;
                border: 1px solid #333;
                border-radius: 5px;
                font-size: 9px;
                font-weight: 600;
            }
        """)
        gif_path = self.entry.get("gif_path")
        if gif_path and os.path.exists(gif_path):
            self.movie = QtGui.QMovie(gif_path)
            if self.movie.isValid():
                self.movie.setCacheMode(QtGui.QMovie.CacheAll)
                self.movie.setScaledSize(QtCore.QSize(64, 64))
                preview.setMovie(self.movie)
                self.movie.start()
            else:
                self.movie = None
                preview.setText("BAD GIF")
        else:
            preview.setText("NO GIF")
        layout.addWidget(preview)

        info_col = QtWidgets.QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(2)

        name_label = QtWidgets.QLabel(self.entry["name"])
        name_label.setWordWrap(True)
        name_label.setStyleSheet("color: #f1f3f5; font-size: 11px; font-weight: 700;")
        info_col.addWidget(name_label)

        frame_range = self.entry.get("frame_range")
        if frame_range:
            range_text = f"{int(frame_range[0])} - {int(frame_range[1])}"
            duration = int(frame_range[1] - frame_range[0]) + 1
            range_sub = f"{duration} frames"
        else:
            range_text = "No range"
            range_sub = "0 frames"

        range_label = QtWidgets.QLabel(range_text)
        range_label.setStyleSheet("color: #d7dbe1; font-size: 12px; font-weight: 700;")
        info_col.addWidget(range_label)

        sub_label = QtWidgets.QLabel(
            f"{range_sub}  |  {self.entry.get('controls_count', 0)} ctrls"
        )
        sub_label.setStyleSheet("color: #9aa3b2; font-size: 9px;")
        info_col.addWidget(sub_label)

        info_col.addStretch()
        layout.addLayout(info_col, 1)

    def _apply_card_style(self, is_selected):
        border = "#3498DB" if is_selected else "#333"
        background = "#2a2a2a" if is_selected else "#222"
        self.setStyleSheet(f"""
            QFrame#animationLibraryCard {{
                background-color: {background};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QFrame#animationLibraryCard:hover {{
                border-color: #3498DB;
                background-color: #2a2a2a;
            }}
        """)

    def set_selected(self, is_selected):
        self._apply_card_style(is_selected)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.selected.emit(self.entry)
            event.accept()
            return
        super(AnimationLibraryCard, self).mousePressEvent(event)

    def showEvent(self, event):
        super(AnimationLibraryCard, self).showEvent(event)
        if self.movie is not None:
            self.movie.start()


class SmartAnimationLibraryWindow(ContextPopupWindow):
    """Compact popup-style smart animation browser – context-menu feel."""

    def __init__(self, anchor_button=None, parent=None):
        if parent is None:
            from AnimKey.mods import uiMod
            parent = uiMod.get_maya_main_window()
        super(SmartAnimationLibraryWindow, self).__init__(anchor_button=anchor_button, parent=parent)
        self.setObjectName("AnimKey_CopyAnimation")
        self.anchor_button = anchor_button
        self.selected_entry = None
        self.card_widgets = []
        self.theme = ThemeManager.get_current_theme()
        self._tail_x = 0
        self._tail_on_top = False  # False = tail at bottom, True = tail at top
        self.resize(340, 520)
        self._drag_pos = None
        self._base_opacity = 0.5
        self._hover_opacity = 1.0
        self._anim = None
        self._build_ui()
        self.refresh_library()
        self.position_window()
        self.setWindowOpacity(self._base_opacity)

    def paintEvent(self, event):
        """Draw solid rounded rect + speech bubble tail."""
        return super(SmartAnimationLibraryWindow, self).paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        bg = QtGui.QColor(30, 30, 30)
        border_color = QtGui.QColor(51, 51, 51)

        if self._tail_on_top:
            body_rect = QtCore.QRectF(0.5, self._tail_height + 0.5,
                                       self.width() - 1,
                                       self.height() - self._tail_height - 1)
        else:
            body_rect = QtCore.QRectF(0.5, 0.5,
                                       self.width() - 1,
                                       self.height() - self._tail_height - 1)

        path = QtGui.QPainterPath()
        path.addRoundedRect(body_rect, 10, 10)

        tail_cx = max(20, min(self._tail_x, self.width() - 20))
        hw = self._tail_width / 2

        if self._tail_on_top:
            tail_base = body_rect.top()
            path.moveTo(tail_cx - hw, tail_base)
            path.lineTo(tail_cx, tail_base - self._tail_height)
            path.lineTo(tail_cx + hw, tail_base)
        else:
            tail_base = body_rect.bottom()
            path.moveTo(tail_cx - hw, tail_base)
            path.lineTo(tail_cx, tail_base + self._tail_height)
            path.lineTo(tail_cx + hw, tail_base)
        path.closeSubpath()

        # Apply mask so the widget shape matches the rounded path exactly
        # This removes the rectangular transparent frame behind the rounded corners
        self.setMask(path.toFillPolygon().toPolygon())

        painter.setPen(QtGui.QPen(border_color, 1))
        painter.setBrush(bg)
        painter.drawPath(path)
        painter.end()

    def _build_ui(self):
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setContentsMargins(1, 1, 1, self._tail_height + 1)
        outer_layout.setSpacing(0)

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("smartLibraryContainer")
        self.container.setStyleSheet("""
            QFrame#smartLibraryContainer {
                background-color: transparent;
                border: none;
            }
        """)
        outer_layout.addWidget(self.container)

        layout = QtWidgets.QVBoxLayout(self.container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ── Header ──
        header = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("Copy Animation")
        self.title_label.setStyleSheet("color: #AAA; font-size: 11px; font-weight: 500;")
        header.addWidget(self.title_label)
        header.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        layout.addLayout(header)

        self.info_label = QtWidgets.QLabel("")
        self.info_label.setStyleSheet("color: #a0a7b4; font-size: 9px;")
        layout.addWidget(self.info_label)

        # ── Clips scroll area (vertical list) ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 2px 0 2px 0;
            }
            QScrollBar::handle:vertical {
                background: #444;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #555;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self.cards_widget = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(2, 2, 2, 2)
        self.cards_layout.setSpacing(6)
        self.cards_layout.addStretch()
        scroll.setWidget(self.cards_widget)
        layout.addWidget(scroll, 1)

        # ── Detail / paste options panel ──
        self.detail_panel = QtWidgets.QFrame()
        self.detail_panel.setStyleSheet("""
            QFrame {
                background-color: #444444;
                border: 1px solid #666666;
                border-radius: 6px;
            }
            QLabel {
                color: #BBB;
                border: none;
            }
            QComboBox, QSpinBox {
                color: #FFF;
                background-color: #4d4d4d;
                border: 1px solid #666666;
                border-radius: 6px;
                padding: 4px 6px;
                font-size: 11px;
                selection-background-color: #3498DB;
            }
            QComboBox:hover, QSpinBox:hover {
                border-color: #555;
            }
            QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QComboBox QAbstractItemView {
                color: #FFF;
                background-color: #4d4d4d;
                border: 1px solid #666666;
                selection-background-color: #3498DB;
            }
            QPushButton {
                background-color: #4d4d4d;
                color: #BBB;
                border: 1px solid #666666;
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5a5a5a;
                color: #FFF;
                border-color: #888;
            }
            QPushButton:disabled {
                color: #555;
                background-color: #222;
                border-color: #333;
            }
        """)
        detail_layout = QtWidgets.QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(10, 10, 10, 10)
        detail_layout.setSpacing(6)

        self.detail_title = QtWidgets.QLabel("Select a Clip")
        self.detail_title.setStyleSheet("color: #f5f5f7; font-size: 12px; font-weight: 700; border: none;")
        detail_layout.addWidget(self.detail_title)

        self.detail_range = QtWidgets.QLabel("Choose a saved animation to configure paste options.")
        self.detail_range.setWordWrap(True)
        self.detail_range.setStyleSheet("color: #a0a7b4; font-size: 10px; border: none;")
        detail_layout.addWidget(self.detail_range)

        # Compact row: Target Rig + Paste Mode side by side
        combos_row = QtWidgets.QHBoxLayout()
        combos_row.setSpacing(6)

        rig_col = QtWidgets.QVBoxLayout()
        rig_col.setSpacing(2)
        rig_col.addWidget(self._make_form_label("Target Rig"))
        self.rig_combo = QtWidgets.QComboBox()
        rig_col.addWidget(self.rig_combo)
        combos_row.addLayout(rig_col, 1)

        mode_col = QtWidgets.QVBoxLayout()
        mode_col.setSpacing(2)
        mode_col.addWidget(self._make_form_label("Mode"))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("Replace", "replace")
        self.mode_combo.addItem("Insert", "insert")
        mode_col.addWidget(self.mode_combo)
        combos_row.addLayout(mode_col, 1)

        detail_layout.addLayout(combos_row)

        # Frame range row
        frame_row = QtWidgets.QHBoxLayout()
        frame_row.setSpacing(6)
        frame_lbl_col = QtWidgets.QVBoxLayout()
        frame_lbl_col.setSpacing(2)
        frame_lbl_col.addWidget(self._make_form_label("Paste Range"))
        fr = QtWidgets.QHBoxLayout()
        fr.setSpacing(4)
        self.start_frame = QtWidgets.QSpinBox()
        self.start_frame.setRange(-100000, 100000)
        self.end_frame = QtWidgets.QSpinBox()
        self.end_frame.setRange(-100000, 100000)
        fr.addWidget(self.start_frame)
        fr.addWidget(self.end_frame)
        frame_lbl_col.addLayout(fr)
        frame_row.addLayout(frame_lbl_col, 1)
        detail_layout.addLayout(frame_row)

        self.apply_btn = QtWidgets.QPushButton("Paste Selected Animation")
        self.apply_btn.clicked.connect(self._apply_selected_entry)
        self.apply_btn.setEnabled(False)
        detail_layout.addWidget(self.apply_btn)

        layout.addWidget(self.detail_panel)

    def _make_form_label(self, text):
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("color: #9ca3af; font-size: 9px; font-weight: 700; border: none;")
        return label

    def _position_window(self):
        """Position the popup anchored to the button, like a context menu."""
        if self.anchor_button is None:
            # Fallback: center on cursor
            cursor_pos = QtGui.QCursor.pos()
            self.move(cursor_pos.x() - self.width() // 2,
                      cursor_pos.y() - self.height() - 10)
            self._tail_x = self.width() // 2
            self._tail_on_top = False
            self.update()
            return

        btn_rect = self.anchor_button.rect()
        btn_top_left = self.anchor_button.mapToGlobal(btn_rect.topLeft())
        btn_center_x = btn_top_left.x() + btn_rect.width() // 2
        btn_top_y = btn_top_left.y()
        btn_bottom_y = btn_top_y + btn_rect.height()

        # Get available screen geometry
        screen_rect = screen_available_geometry(self.anchor_button, btn_top_left)

        popup_w = self.width()
        popup_h = self.height()

        # Prefer opening ABOVE the button (tail at bottom pointing down)
        space_above = btn_top_y - screen_rect.top()
        space_below = screen_rect.bottom() - btn_bottom_y

        if space_above >= popup_h:
            # Place above button – tail at bottom
            y_pos = btn_top_y - popup_h
            self._tail_on_top = False
        elif space_below >= popup_h:
            # Place below button – tail at top
            y_pos = btn_bottom_y
            self._tail_on_top = True
        else:
            # Not enough room either way, use whichever has more space
            if space_above >= space_below:
                y_pos = btn_top_y - popup_h
                self._tail_on_top = False
            else:
                y_pos = btn_bottom_y
                self._tail_on_top = True

        # Horizontal: try to align so tail points at button center
        x_pos = btn_center_x - popup_w // 2

        # Clamp to screen edges
        if x_pos < screen_rect.left():
            x_pos = screen_rect.left()
        elif x_pos + popup_w > screen_rect.right():
            x_pos = screen_rect.right() - popup_w

        self.move(x_pos, y_pos)

        # Calculate tail x relative to popup
        self._tail_x = btn_center_x - x_pos

        # Adjust outer layout margins for tail position
        if self._tail_on_top:
            self.layout().setContentsMargins(1, self._tail_height + 1, 1, 1)
        else:
            self.layout().setContentsMargins(1, 1, 1, self._tail_height + 1)

        self.update()

    def refresh_library(self):
        self.card_widgets = []
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        entries = list_animation_library_entries()
        if not entries:
            empty = QtWidgets.QLabel("No saved clips yet.\nCopy animation to start building the library.")
            empty.setAlignment(QtCore.Qt.AlignCenter)
            empty.setMinimumHeight(100)
            empty.setStyleSheet("""
                QLabel {
                    background-color: #444444;
                    color: #9ca3af;
                    border: 1px solid #666666;
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: 600;
                    padding: 20px;
                }
            """)
            self.cards_layout.addWidget(empty)
            self.cards_layout.addStretch()
            self.info_label.setText("0 clips")
            return

        self.info_label.setText(f"{len(entries)} clips available")
        for entry in entries:
            card = AnimationLibraryCard(entry)
            card.selected.connect(self._select_entry)
            self.cards_layout.addWidget(card)
            self.card_widgets.append(card)
        self.cards_layout.addStretch()

    def _select_entry(self, entry):
        self.selected_entry = entry
        for card in self.card_widgets:
            card.set_selected(card.entry["file_path"] == entry["file_path"])

        self.detail_title.setText(entry["name"])
        frame_range = entry.get("frame_range") or (0, 0)
        self.detail_range.setText(
            f"Frames {int(frame_range[0])} to {int(frame_range[1])}  |  "
            f"{entry.get('controls_count', 0)} controls"
        )
        self.start_frame.setValue(int(frame_range[0]))
        self.end_frame.setValue(int(frame_range[1]))
        self._refresh_rig_combo(entry["animation"])
        self.apply_btn.setEnabled(True)

    def _refresh_rig_combo(self, animation_data):
        self.rig_combo.clear()
        for option in list_target_rig_options(animation_data):
            self.rig_combo.addItem(option["label"], option["value"])

    def _apply_selected_entry(self):
        entry = self.selected_entry
        if not entry:
            return

        target_mode = self.mode_combo.currentData()
        preferred_namespace = self.rig_combo.currentData()
        original_range = entry.get("frame_range")
        selected_start = self.start_frame.value()
        selected_end = self.end_frame.value()
        if selected_end < selected_start:
            selected_end = selected_start
            self.end_frame.setValue(selected_end)

        animation_data = entry["animation"]
        if original_range:
            source_start = int(original_range[0])
            source_end = int(original_range[1])
            selected_duration = selected_end - selected_start
            source_duration = source_end - source_start
            if selected_duration < source_duration:
                relative_end = source_start + selected_duration
                animation_data = trim_animation_data_to_range(
                    animation_data,
                    source_start,
                    relative_end
                )

        time_offset = None
        if original_range:
            time_offset = selected_start - int(original_range[0])

        applied, skipped = apply_animation_data_to_scene(
            animation_data,
            preferred_namespace=preferred_namespace,
            time_offset=time_offset,
            paste_mode=target_mode
        )
        if applied:
            cmds.inViewMessage(
                amg=(
                    f"<span style='color:#a3be8c'>Pasted: {entry['name']}</span>"
                    f"<br><span style='color:#88c0d0'>{applied} channel(s)</span>"
                ),
                pos='topCenter',
                fade=True,
                fadeStayTime=1800,
            )
        else:
            cmds.warning(
                f"AnimKey: Could not paste '{entry['name']}'. Matching controls were not found."
            )
        if skipped:
            print(f"AnimKey: Smart library skipped {skipped} channel(s) while pasting {entry['name']}.")

    def resizeEvent(self, event):
        super(SmartAnimationLibraryWindow, self).resizeEvent(event)

    def enterEvent(self, e):
        self._animate(self._hover_opacity)
        super(SmartAnimationLibraryWindow, self).enterEvent(e)

    def leaveEvent(self, e):
        self._animate(self._base_opacity)
        super(SmartAnimationLibraryWindow, self).leaveEvent(e)

    def _animate(self, val):
        if self._anim is not None:
            try:
                if self._anim.state() == QtCore.QPropertyAnimation.Running:
                    self._anim.stop()
            except RuntimeError:
                self._anim = None
        self._anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(150)
        self._anim.setEndValue(val)
        self._anim.finished.connect(self._on_anim_finished)
        self._anim.start()

    def _on_anim_finished(self):
        self._anim = None

    def closeEvent(self, event):
        global _library_window
        _library_window = None
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
            self._anim = None
        super(SmartAnimationLibraryWindow, self).closeEvent(event)


def show_smart_animation_library(anchor_button=None):
    """Show the large animation library panel."""
    global _library_window
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows(except_widget=_library_window)
    existing = uiMod.show_existing_animkey_tool_window(_library_window, anchor_button)
    if existing is not None:
        _library_window = existing
        return _library_window

    _library_window = SmartAnimationLibraryWindow(anchor_button=anchor_button)
    _library_window.show()
    _library_window.raise_()
    _library_window.activateWindow()
    return _library_window


# ═══════════════════════════════════════════════════════════════════════════════
#                           COPY ANIMATION
# ═══════════════════════════════════════════════════════════════════════════════

def copy_animation(*args):
    """Copy animation from selected controls on the active layer."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.copy_animation"):
        return None
    global _anim_buffer, _anim_sources, _anim_source_snapshots

    selected = cmds.ls(selection=True, long=True)
    if not selected:
        cmds.warning("AnimKey: Please select at least one control.")
        return

    _cleanup_buffer()
    copied_channels = 0

    graph_animation, graph_sources = _collect_selected_graph_animation(selected)
    if graph_animation:
        _anim_buffer.update(graph_animation)
        _anim_sources.update(graph_sources)
        _anim_source_snapshots.update(graph_animation)
        copied_channels = sum(len(channels) for channels in graph_animation.values())
    else:
        time_range = get_selected_time_range()
        source_layer = curve_transfer.active_animation_layer()
        captured, sources = collect_animation_for_controls(
            selected,
            time_range=time_range,
            layer_name=source_layer,
        )
        _anim_buffer.update(captured)
        _anim_sources.update(sources)
        _anim_source_snapshots.update(captured)
        copied_channels = sum(len(channels) for channels in captured.values())

    # Sync buffer to disk for cross-instance copy/paste
    _export_buffer_to_disk(save_to_library=False)

    if copied_channels:
        cmds.warning(
            f"AnimKey: Animation copied  "
            f"({len(_anim_buffer)} ctrl(s), {copied_channels} channel(s))."
        )
    else:
        cmds.warning("AnimKey: No animation found on selected controls.")


def save_animation(*args, **kwargs):
    """
    Open the Save Animation dialog to save animation to the library.

    The dialog lets the user choose a file name, frame range,
    and optionally capture a GIF preview before saving.
    """
    selected = cmds.ls(selection=True, long=True)
    if not selected:
        cmds.warning("AnimKey: Please select at least one control to save.")
        return

    # Get anchor button from the Copy Animation toolbar button, if available
    anchor_button = kwargs.get("button", None)
    show_save_animation_window(anchor_button=anchor_button)


# ═══════════════════════════════════════════════════════════════════════════════
#                           PASTE ANIMATION
# ═══════════════════════════════════════════════════════════════════════════════

def _get_buffer_time_range(buf_node, channels):
    """
    Return (start, end) frame range stored in the buffer node across all
    given channels, or None if no keys are found.
    """
    all_frames = []
    for ch in channels:
        try:
            kf = cmds.keyframe(buf_node, attribute=ch, query=True) or []
            all_frames.extend(kf)
        except Exception:
            pass
    if not all_frames:
        return None
    return min(all_frames), max(all_frames)


def _validate_buffer(ctrl_short):
    """
    BUG FIX 4: Check that the buffer node still exists in the scene.
    If it was lost (undo, scene reload), clean up the stale entry and
    warn the user instead of failing silently.
    Returns the buffer node name, or None if invalid.
    """
    buf_node = f"{_BUFFER_PREFIX}{_safe_name(ctrl_short)}"
    if not cmds.objExists(buf_node):
        # Stale reference — remove it
        _anim_buffer.pop(ctrl_short, None)
        return None
    return buf_node


def _paste_clip_to_targets(target_map, time_offset=0.0, clear_existing=True):
    """Paste the in-memory clip to a resolved source-key -> target-control map."""
    target_layer = curve_transfer.active_animation_layer()
    pasted_count = 0
    skipped_count = 0
    assignments = target_map.items() if hasattr(target_map, "items") else target_map
    transfers, prevalidated, skipped_channels = _build_animation_transfers(
        assignments,
        _anim_buffer,
    )
    skipped_count += skipped_channels
    results = curve_transfer.paste_curves_batch(
        transfers,
        layer_name=target_layer,
        time_offset=float(time_offset or 0.0),
        clear_existing=clear_existing,
        prevalidated_attr_paths=prevalidated,
    )
    pasted_count += sum(1 for result in results if result)
    skipped_count += sum(1 for result in results if not result)
    return pasted_count, skipped_count


def paste_animation(*args):
    """Replace keys in the copied range while preserving keys outside it."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.paste_animation"):
        return None
    _sync_buffer_from_disk()
    if not _anim_buffer:
        cmds.warning("AnimKey: No animation data found. Copy animation first.")
        return

    selected = cmds.ls(selection=True, long=True) or []
    target_assignments = resolve_animation_target_assignments(_anim_buffer.keys(), selected)
    if not target_assignments:
        cmds.warning("AnimKey: No matching target controls found in the selection or scene.")
        return

    pasted_count = 0
    skipped_count = 0
    cmds.undoInfo(openChunk=True)
    refresh_suspended = False
    try:
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            pass

        pasted_count, skipped_count = _paste_clip_to_targets(
            target_assignments,
            time_offset=0.0,
            clear_existing=True,
        )

        if pasted_count:
            cmds.warning(f"AnimKey: Animation pasted ({pasted_count} channel(s)).")
        else:
            cmds.warning("AnimKey: Nothing was pasted. "
                         "Check that selected controls match the copied source.")
        if skipped_count:
            print(f"AnimKey: Animation paste skipped {skipped_count} channel(s).")
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        cmds.undoInfo(closeChunk=True)


# ═══════════════════════════════════════════════════════════════════════════════
#                           PASTE INSERT
# ═══════════════════════════════════════════════════════════════════════════════

def paste_insert_animation(*args):
    """Paste animation at current time (merge, offset to playhead)."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.paste_insert_animation"):
        return None
    _sync_buffer_from_disk()
    if not _anim_buffer:
        cmds.warning("AnimKey: No animation data found. Copy animation first.")
        return

    selected = cmds.ls(selection=True, long=True) or []
    current_time = cmds.currentTime(query=True)
    target_assignments = resolve_animation_target_assignments(_anim_buffer.keys(), selected)
    if not target_assignments:
        cmds.warning("AnimKey: No matching target controls found in the selection or scene.")
        return

    clip_range = get_animation_frame_range(_anim_buffer)
    if not clip_range:
        cmds.warning("AnimKey: The copied clip has no keys.")
        return
    time_offset = float(current_time) - float(clip_range[0])

    cmds.undoInfo(openChunk=True)
    refresh_suspended = False
    try:
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            pass

        inserted_count, skipped_count = _paste_clip_to_targets(
            target_assignments,
            time_offset=time_offset,
            clear_existing=False,
        )
        if inserted_count:
            cmds.warning(
                f"AnimKey: Animation inserted at current time "
                f"({inserted_count} channel(s))."
            )
        else:
            cmds.warning("AnimKey: Nothing was inserted.")
        if skipped_count:
            print(f"AnimKey: Animation insert skipped {skipped_count} channel(s).")
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        cmds.undoInfo(closeChunk=True)


# ═══════════════════════════════════════════════════════════════════════════════
#                           PASTE OPPOSITE
# ═══════════════════════════════════════════════════════════════════════════════

def paste_opposite_animation(*args):
    """Paste the clip through the calibrated mapping onto opposite controls."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.paste_opposite_animation"):
        return None
    _sync_buffer_from_disk()
    if not _anim_buffer:
        cmds.warning("AnimKey: No animation data found. Copy animation first.")
        return

    selected = cmds.ls(selection=True, long=True) or []
    target_map = resolve_target_controls(_anim_buffer.keys(), selected)
    if not target_map:
        cmds.warning("AnimKey: No matching source-side controls found in the selection or scene.")
        return

    pasted_count = 0
    skipped_count = 0
    cmds.undoInfo(openChunk=True)
    refresh_suspended = False
    try:
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            pass

        from AnimKey.buttons import mirror as mirror_module

        target_layer = curve_transfer.active_animation_layer()
        snapshots = {}
        for ctrl_key, source_side in target_map.items():
            rig_name = mirror_module.get_rig_identifier([source_side])
            if rig_name not in snapshots:
                snapshots[rig_name] = mirror_module._load_or_build_basic_snapshot(
                    [source_side],
                    rig_name=rig_name,
                )[0]
            snapshot = snapshots.get(rig_name)
            sym_plane = mirror_module._snap_sym_plane(snapshot)
            opposite = (
                mirror_module._snap_opposite(source_side, snapshot)
                if snapshot else None
            ) or mirror_module.find_opposite_smart(source_side, sym_plane)
            if not opposite or not cmds.objExists(opposite):
                skipped_count += len(_anim_buffer.get(ctrl_key, {}))
                continue

            source_data = mirror_module._snap_ctrl_data(source_side, snapshot) or {}
            target_data = mirror_module._snap_ctrl_data(opposite, snapshot) or {}
            attr_map = mirror_module._effective_attr_map(
                source_side,
                opposite,
                snapshot,
                sym_plane,
                source_data=source_data,
            )
            for channel, curve_data in _anim_buffer.get(ctrl_key, {}).items():
                spec = attr_map.get(channel, {})
                target_channel = spec.get("target", channel)
                if not cmds.attributeQuery(target_channel, node=opposite, exists=True):
                    skipped_count += 1
                    continue

                multiplier = float(
                    spec.get(
                        "mult",
                        -1.0 if channel in MIRROR_ATTRS else 1.0,
                    )
                )
                value_offset = 0.0
                if (
                    not curve_transfer.is_additive_layer(target_layer)
                    and spec.get("mode") != "copy_value"
                ):
                    source_rest = source_data.get("rest_attrs", {}).get(channel, 0.0)
                    target_rest = target_data.get("rest_attrs", {}).get(
                        target_channel,
                        0.0,
                    )
                    value_offset = float(target_rest) - (
                        float(source_rest) * multiplier
                    )

                pasted, _ = curve_transfer.paste_curve(
                    f"{opposite}.{target_channel}",
                    curve_data,
                    layer_name=target_layer,
                    clear_existing=True,
                    value_scale=multiplier,
                    value_offset=value_offset,
                )
                if pasted:
                    pasted_count += 1
                else:
                    skipped_count += 1

        if pasted_count:
            cmds.warning(
                f"AnimKey: Animation pasted to opposite controls "
                f"({pasted_count} channel(s))."
            )
        else:
            cmds.warning("AnimKey: No opposite controls found. "
                         "Check naming convention (L_/R_, Left/Right, etc).")
        if skipped_count:
            print(f"AnimKey: Opposite paste skipped {skipped_count} channel(s).")
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        cmds.undoInfo(closeChunk=True)


# ═══════════════════════════════════════════════════════════════════════════════
#                           COPY POSE
# ═══════════════════════════════════════════════════════════════════════════════

def copy_pose(*args):
    """Copy the current-frame pose from selected controls using animation payloads."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.copy_pose"):
        return None
    selected_objects = cmds.ls(selection=True, long=True)

    if not selected_objects:
        cmds.warning("AnimKey: Please select at least one control.")
        return

    current_frame = cmds.currentTime(query=True)
    animation_payload = {}
    global _pose_buffer, _pose_buffer_mtime, _pose_buffer_is_current

    try:
        for control in selected_objects:
            control_name = get_control_storage_key(control)
            try:
                attributes = cmds.listAttr(
                    control,
                    keyable=True,
                    unlocked=True,
                    settable=True,
                )
            except Exception:
                attributes = cmds.listAttr(control, keyable=True)

            if not attributes:
                continue

            ctrl_data = {}
            for attr in attributes:
                try:
                    attr_path = f"{control}.{attr}"
                    value = cmds.getAttr(attr_path)
                    if not _is_numeric_pose_value(value):
                        continue
                    ctrl_data[attr] = _make_single_frame_curve_snapshot(value, current_frame)
                except Exception:
                    pass

            if ctrl_data:
                animation_payload[control_name] = ctrl_data

        if not animation_payload:
            cmds.warning("AnimKey: No pose data found on selected controls.")
            return

        pose_data = {
            "meta": {
                "name": "copy_pose",
                "type": "pose",
                "frame_range": [current_frame, current_frame],
                "source_frame": current_frame,
                "controls_count": len(animation_payload),
                "scene": cmds.file(query=True, sceneName=True, shortName=True) or "untitled",
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            "animation": animation_payload,
        }

        json_file_path = get_copy_paste_pose_file()
        os.makedirs(os.path.dirname(json_file_path), exist_ok=True)
        _pose_buffer = pose_data
        _pose_buffer_mtime = 0
        _pose_buffer_is_current = True

        def _mark_pose_written(mtime):
            global _pose_buffer_mtime
            _pose_buffer_mtime = mtime

        _schedule_json_write(
            json_file_path,
            pose_data,
            indent=None,
            on_written=_mark_pose_written,
        )

        cmds.warning("AnimKey: Pose copied.")

    except Exception as e:
        cmds.warning(f"AnimKey: Error copying pose: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#                           PASTE POSE
# ═══════════════════════════════════════════════════════════════════════════════

def paste_pose(*args):
    """Paste copied pose onto selected controls at the current frame."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.paste_pose"):
        return None
    selected_objects = cmds.ls(selection=True, long=True) or []
    global _pose_buffer, _pose_buffer_mtime, _pose_buffer_is_current

    json_file_path = get_copy_paste_pose_file()

    if not os.path.exists(json_file_path) and not _pose_buffer:
        legacy_json_file_path = get_legacy_copy_paste_pose_file()
        if os.path.exists(legacy_json_file_path):
            json_file_path = legacy_json_file_path
        else:
            cmds.warning("AnimKey: No pose data found. Copy pose first.")
            return

    try:
        pose_data = _pose_buffer if _pose_buffer_is_current else None
        disk_mtime = 0
        if os.path.exists(json_file_path):
            disk_mtime = os.path.getmtime(json_file_path)
        should_read_disk = pose_data is None or (
            _pose_buffer_mtime and disk_mtime > _pose_buffer_mtime
        )
        if should_read_disk:
            with open(json_file_path, "r") as json_file:
                pose_data = json.load(json_file)
            _pose_buffer = pose_data
            _pose_buffer_mtime = disk_mtime
            _pose_buffer_is_current = True

        if isinstance(pose_data, dict) and "animation" in pose_data:
            animation_payload = pose_data.get("animation") or {}
            current_frame = cmds.currentTime(query=True)
            target_map = resolve_target_controls(animation_payload.keys(), selected_objects)
            if not target_map:
                cmds.warning("AnimKey: No matching target controls found for the copied pose.")
                return

            applied = 0
            skipped = 0
            target_layer = curve_transfer.active_animation_layer()
            cmds.undoInfo(openChunk=True)
            try:
                key_values = []
                for control_name, control in target_map.items():
                    try:
                        editable_attrs = set(cmds.listAttr(
                            control,
                            keyable=True,
                            unlocked=True,
                            settable=True,
                        ) or [])
                    except Exception:
                        editable_attrs = set(cmds.listAttr(control, keyable=True) or [])
                    for attr, curve_data in animation_payload.get(control_name, {}).items():
                        try:
                            if attr not in editable_attrs:
                                skipped += 1
                                continue
                            value = _extract_pose_value(curve_data)
                            if value is None:
                                skipped += 1
                                continue
                            key_values.append((f"{control}.{attr}", value))
                        except Exception:
                            skipped += 1
                results = curve_transfer.set_keys_on_layer(
                    key_values,
                    current_frame,
                    layer_name=target_layer,
                )
                applied += sum(1 for result in results if result)
                skipped += sum(1 for result in results if not result)
            finally:
                cmds.undoInfo(closeChunk=True)

            if applied:
                cmds.warning(f"AnimKey: Pose pasted ({applied} channel(s)).")
            else:
                cmds.warning("AnimKey: Nothing was pasted. Check that selected controls match the copied pose.")
            if skipped:
                print(f"AnimKey: Pose paste skipped {skipped} channel(s).")
            return

        target_layer = curve_transfer.active_animation_layer()
        cmds.undoInfo(openChunk=True)
        try:
            for control in selected_objects:
                control_name = get_control_short_name(control)

                if control_name in pose_data:
                    for attr, value in pose_data[control_name].items():
                        try:
                            if not _is_numeric_pose_value(value):
                                continue
                            attr_path = f"{control}.{attr}"
                            if not cmds.getAttr(attr_path, lock=True):
                                curve_transfer.set_key_on_layer(
                                    attr_path,
                                    cmds.currentTime(query=True),
                                    value,
                                    layer_name=target_layer,
                                )
                        except Exception:
                            pass
        finally:
            cmds.undoInfo(closeChunk=True)

        cmds.warning("AnimKey: Pose pasted.")

    except Exception as e:
        cmds.warning(f"AnimKey: Error pasting pose: {e}")



# ═══════════════════════════════════════════════════════════════════════════════
#                           EXECUTE (Main button action)
# ═══════════════════════════════════════════════════════════════════════════════


def execute(*args, **kwargs):
    """Main function to execute when button is clicked."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.execute"):
        return None
    show_smart_animation_library(anchor_button=kwargs.get("button"))


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Copy Animation",
        "tooltip": "Copy/Paste Animation. Right-click for more options.",
        "icon": "copy_animation.svg",
        "shortcut": None,
    }
