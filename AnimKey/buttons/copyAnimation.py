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
import copy
import re
import threading
from datetime import datetime
from AnimKey.mods.storage import atomic_write_json
from AnimKey.core import animation_curve_transfer as curve_transfer


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


_LAYER_BUNDLE_MARKER = "__animkey_layered_curves__"


def _is_layer_bundle(curve_data):
    return bool(
        isinstance(curve_data, dict)
        and curve_data.get(_LAYER_BUNDLE_MARKER)
        and isinstance(curve_data.get("layers"), dict)
    )


def _iter_curve_payloads(curve_data):
    """Yield ``(source_layer, payload)`` while accepting v1-v3 clips."""
    if _is_layer_bundle(curve_data):
        for layer_name, payload in curve_data.get("layers", {}).items():
            if isinstance(payload, dict) and payload.get("keyframes"):
                yield layer_name, payload
        return
    if isinstance(curve_data, dict) and curve_data.get("keyframes"):
        yield curve_data.get("source_layer") or "BaseAnimation", curve_data


def _animation_payload_count(animation_data):
    return sum(
        1
        for channels in (animation_data or {}).values()
        for curve_data in channels.values()
        for _layer, _payload in _iter_curve_payloads(curve_data)
    )


def _store_curve_payload(animation_data, control_key, channel, snapshot):
    """Store a curve without dropping a second animation layer on one plug."""
    if not snapshot:
        return
    channel_data = animation_data.setdefault(control_key, {})
    source_layer = snapshot.get("source_layer") or "BaseAnimation"
    current = channel_data.get(channel)
    if current is None:
        channel_data[channel] = snapshot
        return

    if _is_layer_bundle(current):
        current["layers"][source_layer] = snapshot
        return

    existing_layer = current.get("source_layer") or "BaseAnimation"
    if existing_layer == source_layer:
        channel_data[channel] = snapshot
        return

    channel_data[channel] = {
        _LAYER_BUNDLE_MARKER: True,
        "layers": {
            existing_layer: current,
            source_layer: snapshot,
        },
    }


def _capture_layer_state(layer_name):
    """Capture the layer knobs that change the evaluated animation result."""
    if curve_transfer.is_base_layer(layer_name):
        return {"name": "BaseAnimation", "base": True}
    state = {"name": layer_name, "base": False}
    for flag in ("override", "weight", "mute", "solo", "lock", "parent", "passthrough"):
        try:
            state[flag] = cmds.animLayer(layer_name, query=True, **{flag: True})
        except Exception:
            pass
    for attr in ("rotationAccumulationMode", "scaleAccumulationMode"):
        if cmds.objExists(layer_name + "." + attr):
            state[attr] = cmds.getAttr(layer_name + "." + attr)
    weight_curves = cmds.listConnections(layer_name + ".weight", source=True,
                                         destination=False, type="animCurve") or []
    if weight_curves:
        state["weight_curve"] = curve_transfer.capture_curve(
            weight_curves[0], layer_name="BaseAnimation")
    return state


def _remember_layer_state(layer_name):
    if not layer_name:
        layer_name = "BaseAnimation"
    if layer_name not in _anim_layer_metadata:
        _anim_layer_metadata[layer_name] = _capture_layer_state(layer_name)
        parent = _anim_layer_metadata[layer_name].get("parent")
        if parent and parent != layer_name:
            _remember_layer_state(parent)


def _ensure_target_layer(layer_name):
    """Return/create the destination layer without changing the user's active layer."""
    layer_name = layer_name or "BaseAnimation"
    if curve_transfer.is_base_layer(layer_name):
        return "BaseAnimation"
    if not cmds.objExists(layer_name):
        state = _anim_layer_metadata.get(layer_name, {})
        try:
            cmds.animLayer(layer_name, override=bool(state.get("override", False)))
        except Exception:
            try:
                cmds.animLayer(layer_name)
            except Exception:
                return None
        parent = state.get("parent")
        if parent and parent != layer_name and not curve_transfer.is_base_layer(parent):
            # The child already exists, so even malformed cyclic metadata
            # cannot recurse forever. Maya rejects an invalid parent change.
            if _ensure_target_layer(parent):
                cmds.animLayer(layer_name, edit=True, parent=parent)
    # Existing layers belong to the scene, potentially to several rigs. Never
    # unlock them or reset their weight/animation as a side effect of paste.
    if curve_transfer._layer_is_locked(layer_name):
        return None
    return layer_name


def _restore_layer_state(layer_name, time_offset=0.0, _seen=None, _allowed=None):
    if curve_transfer.is_base_layer(layer_name):
        return
    if _allowed is not None and layer_name not in _allowed:
        return
    _seen = set() if _seen is None else _seen
    if layer_name in _seen:
        return
    _seen.add(layer_name)
    state = _anim_layer_metadata.get(layer_name, {})
    for flag in ("override", "weight", "mute", "solo", "passthrough"):
        if flag not in state:
            continue
        try:
            cmds.animLayer(layer_name, edit=True, **{flag: state[flag]})
        except Exception:
            pass
    for attr in ("rotationAccumulationMode", "scaleAccumulationMode"):
        if attr in state and cmds.objExists(layer_name + "." + attr):
            cmds.setAttr(layer_name + "." + attr, state[attr])
    if state.get("weight_curve"):
        curve_transfer.paste_curve(layer_name + ".weight", state["weight_curve"],
                                   layer_name="BaseAnimation", time_offset=time_offset)
    if "lock" in state:
        cmds.animLayer(layer_name, edit=True, lock=state["lock"])
    parent = state.get("parent")
    if parent and not curve_transfer.is_base_layer(parent) and cmds.objExists(parent):
        _restore_layer_state(parent, time_offset=time_offset, _seen=_seen, _allowed=_allowed)


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


def _build_layered_animation_transfers(assignments, animation_data):
    """Build batch-paste inputs grouped by their original animation layer."""
    groups = {}
    skipped = 0
    attr_cache = {}
    for ctrl_key, control in assignments:
        if control not in attr_cache:
            attr_cache[control] = _control_channel_sets(control)
        all_attrs, locked_attrs = attr_cache[control]
        for channel, stored_data in animation_data.get(ctrl_key, {}).items():
            base_channel = channel.split("[", 1)[0]
            if (not _channel_exists(channel, all_attrs) or
                    channel in locked_attrs or base_channel in locked_attrs):
                skipped += len(list(_iter_curve_payloads(stored_data))) or 1
                continue
            attr_path = f"{control}.{channel}"
            for layer_name, curve_data in _iter_curve_payloads(stored_data):
                group = groups.setdefault(
                    layer_name or "BaseAnimation",
                    {"transfers": [], "prevalidated": set()},
                )
                group["transfers"].append((attr_path, curve_data))
                group["prevalidated"].add(attr_path)
    return groups, skipped


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


def collect_animation_for_controls(controls, time_range=None, layer_name=None,
                                   all_layers=False):
    """Capture selected controls with one curve query instead of per-attribute scans."""
    controls = list_selected_transform_targets(controls)
    controls = cmds.ls(controls, long=True, type="transform") if controls else []
    if not controls:
        return {}, {}
    layer_name = curve_transfer.active_animation_layer() if layer_name is None else layer_name
    layer_by_curve = {}
    for candidate_layer in cmds.ls(type="animLayer") or []:
        if curve_transfer.is_base_layer(candidate_layer):
            continue
        for curve in cmds.animLayer(candidate_layer, query=True, animCurves=True) or []:
            layer_by_curve[curve] = candidate_layer
    try:
        candidate_curves = cmds.keyframe(controls, query=True, name=True) or []
    except Exception:
        candidate_curves = []
    if all_layers:
        # Maya's attribute query often exposes only the currently composed
        # layer curve.  Include concrete curves owned by every layer, then
        # resolve the BaseAnimation counterpart from their driven plug below.
        candidate_curves.extend(layer_by_curve)
    curves = list(dict.fromkeys(candidate_curves)) if all_layers else _curves_on_layer(
        candidate_curves, layer_name
    )
    if not curves:
        return {}, {}

    selected_set = set(controls)
    animation_data = {}
    source_controls = {}
    curve_index = 0
    seen_curves = set()
    captured_layers = set()
    while curve_index < len(curves):
        curve = curves[curve_index]
        curve_index += 1
        if curve in seen_curves:
            continue
        seen_curves.add(curve)
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
            source_layer = layer_by_curve.get(curve, "BaseAnimation")
            requested_layer = layer_name or "BaseAnimation"
            if not all_layers and source_layer != requested_layer:
                continue
            if all_layers and source_layer != "BaseAnimation":
                base_curve = curve_transfer.resolve_anim_curve(
                    attr_path, layer_name="BaseAnimation"
                )
                if base_curve and base_curve not in seen_curves:
                    curves.append(base_curve)
            snapshot = curve_transfer.capture_curve(
                curve,
                time_range=time_range,
                layer_name=source_layer,
            )
            if not snapshot:
                continue
            ctrl_key = get_control_storage_key(control)
            _store_curve_payload(animation_data, ctrl_key, channel, snapshot)
            if source_layer not in captured_layers:
                _anim_layer_metadata[source_layer] = _capture_layer_state(source_layer)
                captured_layers.add(source_layer)
                parent = _anim_layer_metadata[source_layer].get("parent")
                while parent and parent not in captured_layers:
                    _anim_layer_metadata[parent] = _capture_layer_state(parent)
                    captured_layers.add(parent)
                    parent = _anim_layer_metadata[parent].get("parent")
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
            candidates = selected_candidates
        if not candidates:
            continue

        control, channel = candidates[0]
        source_layer = curve_transfer.animation_layer_for_curve(
            "{}.{}".format(control, channel),
            curve,
        ) or "BaseAnimation"
        snapshot = curve_transfer.capture_curve(
            curve,
            layer_name=source_layer,
            key_times=key_times,
        )
        if not snapshot:
            continue

        ctrl_key = get_control_storage_key(control)
        _store_curve_payload(animation_data, ctrl_key, channel, snapshot)
        _remember_layer_state(source_layer)
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

# {control identity: {channel: serialized curve or layered curve bundle}}
_anim_buffer = {}
_anim_sources = {}
_anim_source_snapshots = {}
_anim_layer_metadata = {}
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
    global _anim_layer_metadata
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
    _anim_layer_metadata = {}
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
            for _layer_name, payload in _iter_curve_payloads(curve_data):
                clip_range = payload.get("clip_range")
                if isinstance(clip_range, (list, tuple)) and len(clip_range) == 2:
                    all_frames.extend(clip_range)
                else:
                    all_frames.extend(payload.get("keyframes", []))
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
    paste_mode="replace",
    layer_metadata=None,
):
    """Apply serialized animation data to matching controls in the current scene."""
    global _anim_layer_metadata
    if layer_metadata is not None:
        _anim_layer_metadata = dict(layer_metadata or {})
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
    cmds.undoInfo(openChunk=True)
    refresh_suspended = False
    try:
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            pass

        layer_groups, skipped_channels = _build_layered_animation_transfers(
            target_assignments,
            animation_data,
        )
        skipped += skipped_channels
        for source_layer in sorted(
                layer_groups,
                key=lambda name: 0 if curve_transfer.is_base_layer(name) else 1):
            group = layer_groups[source_layer]
            target_layer = _ensure_target_layer(source_layer)
            if not target_layer:
                skipped += len(group["transfers"])
                continue
            results = curve_transfer.paste_curves_batch(
                group["transfers"],
                layer_name=target_layer,
                time_offset=float(time_offset or 0.0),
                clear_existing=paste_mode != "insert",
                prevalidated_attr_paths=group["prevalidated"],
            )
            applied += sum(1 for result in results if result)
            skipped += sum(1 for result in results if not result)
            _restore_layer_state(target_layer, time_offset=float(time_offset or 0.0))
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
    with open(file_path, "r", encoding="utf-8-sig") as f:
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
            payloads = list(_iter_curve_payloads(curve_data))
            trimmed_payloads = {
                layer_name: trim_curve_data_to_range(payload, start_frame, end_frame)
                for layer_name, payload in payloads
            }
            trimmed_payloads = {
                layer_name: payload
                for layer_name, payload in trimmed_payloads.items()
                if payload
            }
            if len(trimmed_payloads) == 1 and not _is_layer_bundle(curve_data):
                trimmed_channels[channel] = next(iter(trimmed_payloads.values()))
            elif trimmed_payloads:
                trimmed_channels[channel] = {
                    _LAYER_BUNDLE_MARKER: True,
                    "layers": trimmed_payloads,
                }
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


_pending_json_writes = {}


def _schedule_json_write(file_path, data, indent=None, on_written=None):
    """Persist pure Python data off Maya's UI thread, keeping newest-write wins."""
    global _json_write_generation
    generation = _json_write_generation.get(file_path, 0) + 1
    _json_write_generation[file_path] = generation
    _pending_json_writes[file_path] = generation
    # The clipboard dictionaries are cleared/reused by the next copy.
    data = copy.deepcopy(data)

    def _writer():
        with _json_write_lock:
            try:
                if generation < _json_write_generation.get(file_path, 0):
                    return
                atomic_write_json(file_path, data, indent=indent)
                mtime = os.path.getmtime(file_path)
                if on_written:
                    on_written(mtime)
            except Exception:
                pass
            finally:
                if _pending_json_writes.get(file_path) == generation:
                    _pending_json_writes.pop(file_path, None)

    worker = threading.Thread(
        target=_writer,
        name="AnimKeyJsonWriter",
    )
    worker.daemon = True
    worker.start()
    return worker


def _export_buffer_to_disk(save_to_library=False):
    global _anim_buffer, _anim_layer_metadata, _last_disk_mtime, _buffer_is_current
    if not _anim_buffer:
        _buffer_is_current = True
        json_path = get_copy_paste_animation_file()
        try:
            with _json_write_lock:
                _json_write_generation[json_path] = _json_write_generation.get(json_path, 0) + 1
                _pending_json_writes.pop(json_path, None)
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
            "format_version": 4,
            "layers": _anim_layer_metadata,
        },
    }

    json_path = get_copy_paste_animation_file()
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    try:
        if save_to_library:
            with _json_write_lock:
                _json_write_generation[json_path] = _json_write_generation.get(json_path, 0) + 1
                atomic_write_json(json_path, animation_data, indent=None)
                _last_disk_mtime = os.path.getmtime(json_path)
                _pending_json_writes.pop(json_path, None)
            # Also save to library/backup folder
            backup_folder = get_animation_backup_folder()
            latest_backup = get_latest_animation_backup_file()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            snapshot_backup = os.path.join(backup_folder, f"anim_backup_{timestamp}.animkey_anim")

            atomic_write_json(latest_backup, animation_data, indent=None)
            atomic_write_json(snapshot_backup, animation_data, indent=None)
            
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
    global _anim_layer_metadata
    global _last_disk_mtime, _buffer_is_current
    json_path = get_copy_paste_animation_file()
    if not os.path.exists(json_path):
        return
        
    try:
        mtime = os.path.getmtime(json_path)
        if _buffer_is_current and (json_path in _pending_json_writes or not _last_disk_mtime):
            return
        if _anim_buffer and _last_disk_mtime and mtime <= _last_disk_mtime:
            return
            
        with open(json_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            
        animation_data = data.get("animation", data)
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        if not animation_data:
            return
            
        _cleanup_buffer()
        _anim_sources = {}
        _anim_source_snapshots = {}
        _anim_buffer = {
            ctrl_key: {
                channel: curve_data
                for channel, curve_data in channels_data.items()
                if list(_iter_curve_payloads(curve_data))
            }
            for ctrl_key, channels_data in animation_data.items()
            if isinstance(channels_data, dict)
        }
        _anim_buffer = {
            ctrl_key: channels
            for ctrl_key, channels in _anim_buffer.items()
            if channels
        }
        _anim_layer_metadata = dict(meta.get("layers", {}) or {})

        _last_disk_mtime = mtime
        _buffer_is_current = True
    except Exception:
        pass


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
    clipboard_metadata = dict(_anim_layer_metadata)
    try:
        _anim_layer_metadata.clear()
        animation_payload, _ = collect_animation_for_controls(
            selected, time_range=time_range, all_layers=True,
        )
        saved_metadata = copy.deepcopy(_anim_layer_metadata)
    finally:
        _anim_layer_metadata.clear()
        _anim_layer_metadata.update(clipboard_metadata)

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
            "format_version": 4,
            "layers": saved_metadata,
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

    atomic_write_json(file_path, save_data, indent=None)

    cmds.inViewMessage(
        amg=f"<span style='color:#a3be8c'>Saved: {file_name}</span>",
        pos='topCenter',
        fade=True,
        fadeStayTime=2000,
    )


def copy_animation(*args):
    """Copy selected keys, the active layer, or the full stack from Base Animation."""
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
        copied_channels = _animation_payload_count(graph_animation)
    else:
        time_range = get_selected_time_range()
        captured, sources = collect_animation_for_controls(
            selected,
            time_range=time_range,
            all_layers=curve_transfer.is_base_layer(curve_transfer.active_animation_layer()),
        )
        _anim_buffer.update(captured)
        _anim_sources.update(sources)
        _anim_source_snapshots.update(captured)
        copied_channels = _animation_payload_count(captured)

    # Sync buffer to disk for cross-instance copy/paste
    _export_buffer_to_disk(save_to_library=False)

    if copied_channels:
        cmds.warning(
            f"AnimKey: Animation copied  "
            f"({len(_anim_buffer)} ctrl(s), {copied_channels} channel(s))."
        )
    else:
        cmds.warning("AnimKey: No animation found on selected controls.")


def _capture_file_animation(selected, time_range=None):
    """Capture a saved clip without modifying either clipboard."""
    old_metadata = copy.deepcopy(_anim_layer_metadata)
    try:
        _anim_layer_metadata.clear()
        animation, _ = collect_animation_for_controls(
            selected, time_range=time_range, all_layers=True,
        )
        layers = copy.deepcopy(_anim_layer_metadata)
    finally:
        _anim_layer_metadata.clear()
        _anim_layer_metadata.update(old_metadata)
    return animation, layers


def _capture_file_pose(selected):
    """Capture evaluated values at the playhead without modifying the pose clipboard."""
    frame = cmds.currentTime(query=True)
    animation = {}
    for control in selected:
        try:
            attrs = cmds.listAttr(control, keyable=True, unlocked=True) or []
        except Exception:
            attrs = cmds.listAttr(control, keyable=True) or []
        channels = {}
        for attr in attrs:
            try:
                value = cmds.getAttr(f"{control}.{attr}")
                if _is_numeric_pose_value(value):
                    channels[attr] = _make_single_frame_curve_snapshot(value, frame)
            except Exception:
                continue
        if channels:
            animation[get_control_storage_key(control)] = channels
    return animation, {}


def _choose_clip_file(clip_type, save=False):
    extension = ".animkey_pose" if clip_type == "pose" else ".animkey_anim"
    label = "Pose" if clip_type == "pose" else "Animation"
    paths = cmds.fileDialog2(
        caption=f"{'Save' if save else 'Load'} {label}",
        fileMode=0 if save else 1,
        fileFilter=f"AnimKey {label} (*{extension})",
        dialogStyle=2,
        okCaption="Save" if save else "Load",
    )
    if not paths:
        return None
    path = paths[0]
    if save and not path.lower().endswith(extension):
        path += extension
    return path


def _save_clip_file(clip_type):
    selected = cmds.ls(selection=True, long=True) or []
    if not selected:
        cmds.warning("AnimKey: Please select at least one control to save.")
        return None
    file_path = _choose_clip_file(clip_type, save=True)
    if not file_path:
        return None
    try:
        if clip_type == "pose":
            animation, layers = _capture_file_pose(selected)
        else:
            animation, layers = _capture_file_animation(selected, get_selected_time_range())
        if not animation:
            cmds.warning(f"AnimKey: No {clip_type} data found on selected controls.")
            return None
        meta = {
            "name": os.path.splitext(os.path.basename(file_path))[0],
            "type": clip_type,
            "format_version": 4,
            "frame_range": get_animation_frame_range(animation),
            "controls_count": len(animation),
            "scene": cmds.file(query=True, sceneName=True, shortName=True) or "untitled",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_layer": curve_transfer.active_animation_layer(),
            "layers": layers,
            "source_controls": {
                key: {"short_name": get_control_short_name(key),
                      "path_signature": get_control_path_signature(key)}
                for key in animation
            },
        }
        if clip_type == "pose":
            meta["source_frame"] = cmds.currentTime(query=True)
        atomic_write_json(file_path, {"meta": meta, "animation": animation}, indent=None)
        cmds.warning(f"AnimKey: {clip_type.capitalize()} saved: {file_path}")
        return file_path
    except Exception as exc:
        cmds.warning(f"AnimKey: Error saving {clip_type}: {exc}")
        return None


def _read_clip_file(file_path, clip_type):
    with open(file_path, "r", encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("The selected file is not an AnimKey clip.")
    meta = data.get("meta", {})
    animation = data.get("animation", data)
    if not isinstance(meta, dict) or meta.get("type", clip_type) != clip_type:
        raise ValueError(f"The selected file does not contain a {clip_type}.")
    if not isinstance(meta.get("layers", {}), dict):
        raise ValueError("The selected clip has invalid animation layer data.")
    if not isinstance(animation, dict) or not animation:
        raise ValueError("The selected clip is empty.")
    for control, channels in animation.items():
        if not isinstance(control, str) or not isinstance(channels, dict) or not channels:
            raise ValueError("The selected clip has invalid control data.")
        for channel, payload in channels.items():
            if not isinstance(channel, str) or not isinstance(payload, dict):
                raise ValueError("The selected clip has invalid channel data.")
            curves = list(_iter_curve_payloads(payload))
            if not curves:
                raise ValueError("The selected clip has invalid curve data.")
            for _layer, curve in curves:
                frames, values = curve.get("keyframes"), curve.get("values")
                if (not isinstance(frames, list) or not isinstance(values, list)
                        or len(frames) != len(values)
                        or not all(isinstance(value, (int, float)) for value in frames + values)):
                    raise ValueError("The selected clip has invalid keyframe data.")
    return {"meta": meta, "animation": animation}


def save_animation(*args, **kwargs):
    """Save selected controls and layers to a file chosen by the user."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.save_animation"):
        return None
    return _save_clip_file("animation")


def save_pose(*args, **kwargs):
    """Save the selected controls' current pose to a chosen file."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.save_pose"):
        return None
    return _save_clip_file("pose")


def load_animation(*args, **kwargs):
    """Load a saved animation and paste it with the regular target/layer rules."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.load_animation"):
        return None
    file_path = _choose_clip_file("animation")
    if not file_path:
        return None
    try:
        data = _read_clip_file(file_path, "animation")
        global _anim_buffer, _anim_layer_metadata
        _cleanup_buffer()
        _anim_buffer = data["animation"]
        _anim_layer_metadata = dict(data["meta"].get("layers", {}) or {})
        _export_buffer_to_disk(save_to_library=False)
        paste_animation()
        return file_path
    except Exception as exc:
        cmds.warning(f"AnimKey: Error loading animation: {exc}")
        return None


def load_pose(*args, **kwargs):
    """Load a saved pose at the current frame and animation layer."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.copyAnimation.load_pose"):
        return None
    file_path = _choose_clip_file("pose")
    if not file_path:
        return None
    try:
        data = _read_clip_file(file_path, "pose")
        global _pose_buffer, _pose_buffer_mtime, _pose_buffer_is_current
        _pose_buffer = data
        _pose_buffer_mtime = 0
        _pose_buffer_is_current = True
        def _mark_written(mtime):
            global _pose_buffer_mtime
            _pose_buffer_mtime = mtime
        _schedule_json_write(get_copy_paste_pose_file(), data, on_written=_mark_written)
        paste_pose()
        return file_path
    except Exception as exc:
        cmds.warning(f"AnimKey: Error loading pose: {exc}")
        return None


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


def _paste_clip_to_targets(target_map, time_offset=0.0, clear_existing=True,
                           use_active_layer=False):
    """Paste the in-memory clip to a resolved source-key -> target-control map."""
    pasted_count = 0
    skipped_count = 0
    assignments = target_map.items() if hasattr(target_map, "items") else target_map
    layer_groups, skipped_channels = _build_layered_animation_transfers(
        assignments,
        _anim_buffer,
    )
    skipped_count += skipped_channels
    # A one-layer clipboard is transferable to the user's keying layer. A
    # stack remains a stack: combining its raw additive/override curves into
    # one curve would discard its composition.
    active_layer = curve_transfer.active_animation_layer() or "BaseAnimation"
    remap_single_layer = use_active_layer and len(layer_groups) == 1
    existing_layers = set(cmds.ls(type="animLayer") or [])
    for source_layer in sorted(
            layer_groups,
            key=lambda name: 0 if curve_transfer.is_base_layer(name) else 1):
        group = layer_groups[source_layer]
        destination = active_layer if remap_single_layer else source_layer
        layer_existed = cmds.objExists(destination)
        target_layer = _ensure_target_layer(destination)
        if not target_layer:
            skipped_count += len(group["transfers"])
            continue
        results = curve_transfer.paste_curves_batch(
            group["transfers"],
            layer_name=target_layer,
            time_offset=float(time_offset or 0.0),
            clear_existing=clear_existing,
            prevalidated_attr_paths=group["prevalidated"],
        )
        pasted_count += sum(1 for result in results if result)
        skipped_count += sum(1 for result in results if not result)
        if not layer_existed and not remap_single_layer:
            new_layers = set(cmds.ls(type="animLayer") or []) - existing_layers
            _restore_layer_state(target_layer, time_offset=time_offset, _allowed=new_layers)
    if pasted_count:
        cmds.currentTime(cmds.currentTime(query=True), edit=True, update=True)
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
            use_active_layer=True,
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
    """Paste the copied span at the playhead, preserving keys outside that span."""
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
            # A merge leaves old destination keys inside the inserted motion,
            # changing its interpolation even when every copied key is right.
            clear_existing=True,
            use_active_layer=True,
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
            for channel, stored_data in _anim_buffer.get(ctrl_key, {}).items():
                spec = attr_map.get(channel, {})
                target_channel = spec.get("target", channel)
                if not cmds.attributeQuery(target_channel, node=opposite, exists=True):
                    skipped_count += len(list(_iter_curve_payloads(stored_data))) or 1
                    continue

                multiplier = float(
                    spec.get(
                        "mult",
                        -1.0 if channel in MIRROR_ATTRS else 1.0,
                    )
                )
                value_offset = 0.0
                for source_layer, curve_data in _iter_curve_payloads(stored_data):
                    layer_existed = cmds.objExists(source_layer)
                    target_layer = _ensure_target_layer(source_layer)
                    if not target_layer:
                        skipped_count += 1
                        continue
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
                    if not layer_existed:
                        _restore_layer_state(target_layer)

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
            target_assignments = resolve_animation_target_assignments(
                animation_payload.keys(), selected_objects)
            if not target_assignments:
                cmds.warning("AnimKey: No matching target controls found for the copied pose.")
                return

            applied = 0
            skipped = 0
            target_layer = curve_transfer.active_animation_layer()
            cmds.undoInfo(openChunk=True)
            try:
                key_values = []
                for control_name, control in target_assignments:
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
                if applied:
                    # setKeyframe(value=...) updates curves but Maya can keep
                    # the previous evaluated pose cached until time is updated.
                    cmds.currentTime(current_frame, edit=True, update=True)
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
    return copy_animation(*args)


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Copy Animation",
        "tooltip": "Copy/Paste Animation. Right-click for more options.",
        "icon": "copy_animation.svg",
        "shortcut": None,
    }
