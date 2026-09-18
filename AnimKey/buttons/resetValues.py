"""
    AnimKey Button: Reset Values
    
    Resets objects to their default values.
    Based on AnimKey's reset_objects_mods functionality.
    
    Features:
    - Reset all attributes to default values (default)
    - Reset only translations (Shift + Click)
    - Reset only rotations (Ctrl + Click)
    - Select channels in Channel Box to reset only those channels
    - Save custom default values for objects
    - Restore saved default values
"""

import maya.cmds as cmds
import maya.mel as mel
import os
import json
import time

from AnimKey.mods.storage import atomic_write_json, read_json


RESET_SCHEMA_KEY = "__animkey_schema__"
RESET_SNAPSHOTS_KEY = "snapshots"
RESET_SCHEMA_VERSION = 2

ANIM_CURVE_PREFIX = "animCurve"
ANIM_DRIVER_TYPES = {
    "animBlendNodeAdditive",
    "animBlendNodeAdditiveDA",
    "animBlendNodeAdditiveDL",
    "animBlendNodeAdditiveF",
    "animBlendNodeAdditiveFA",
    "animBlendNodeAdditiveFL",
    "animBlendNodeAdditiveI16",
    "animBlendNodeAdditiveI32",
    "animBlendNodeAdditiveRotation",
    "animBlendNodeAdditiveScale",
    "animBlendNodeBase",
    "animBlendNodeBoolean",
    "animBlendNodeEnum",
    "animBlendNodeTime",
    "blendWeighted",
    "pairBlend",
    "unitConversion",
}

TRANSLATE_ATTRS = {"translate", "translatex", "translatey", "translatez", "tx", "ty", "tz"}
ROTATE_ATTRS = {"rotate", "rotatex", "rotatey", "rotatez", "rx", "ry", "rz"}
SCALE_ATTRS = {"scale", "scalex", "scaley", "scalez", "sx", "sy", "sz"}
SHEAR_ATTRS = {"shear", "shearxy", "shearxz", "shearyz"}

ATTR_ALIASES = {
    "tx": {"tx", "translatex"},
    "ty": {"ty", "translatey"},
    "tz": {"tz", "translatez"},
    "translatex": {"tx", "translatex"},
    "translatey": {"ty", "translatey"},
    "translatez": {"tz", "translatez"},
    "rx": {"rx", "rotatex"},
    "ry": {"ry", "rotatey"},
    "rz": {"rz", "rotatez"},
    "rotatex": {"rx", "rotatex"},
    "rotatey": {"ry", "rotatey"},
    "rotatez": {"rz", "rotatez"},
    "sx": {"sx", "scalex"},
    "sy": {"sy", "scaley"},
    "sz": {"sz", "scalez"},
    "scalex": {"sx", "scalex"},
    "scaley": {"sy", "scaley"},
    "scalez": {"sz", "scalez"},
}


def get_selected_channels():
    """
    Get selected channels from the Channel Box.
    
    Returns:
        list: List of selected channel names, or None if no channels are selected
    """
    try:
        # Get the main Channel Box name
        main_channel_box = mel.eval('global string $gChannelBoxName; $temp=$gChannelBoxName;')
        
        # Get selected channels
        selected_channels = cmds.channelBox(main_channel_box, query=True, selectedMainAttributes=True)
        
        return selected_channels if selected_channels else None
    except:
        return None


def get_default_data_file():
    """
    Get the path to the default values JSON file.
    
    Returns:
        str: Path to the JSON file
    """
    # Create AnimKey data folder path
    from AnimKey.mods import configMod
    user_folder = configMod.get_user_folder_path()
    animkey_data_folder = os.path.join(user_folder, "tools", "reset_values")
    
    # Ensure folder exists
    os.makedirs(animkey_data_folder, exist_ok=True)
    # Return file path
    return os.path.join(animkey_data_folder, "snapshot_reset_values.json")


def _load_default_data(json_file_path):
    if not os.path.exists(json_file_path):
        return {}

    data = read_json(json_file_path, default=None, backup_corrupt=True)
    if isinstance(data, dict):
        return data
    cmds.warning("AnimKey: Could not read reset values snapshot; a corrupt backup was kept.")
    return {}


def _safe_key(text):
    text = str(text or "default")
    safe = []
    for char in text:
        if char.isalnum() or char in ("_", "-", "."):
            safe.append(char)
        else:
            safe.append("_")
    key = "".join(safe).strip("_")
    return key or "default"


def _leaf_name(obj):
    return obj.rsplit("|", 1)[-1]


def _split_namespace(obj):
    leaf = _leaf_name(obj)
    if ":" in leaf:
        namespace, _, short_name = leaf.rpartition(":")
        return namespace, short_name
    return "", leaf


def _long_name(obj):
    try:
        return (cmds.ls(obj, long=True) or [obj])[0]
    except Exception:
        return obj


def _dag_parts(obj):
    return [part for part in _long_name(obj).split("|") if part]


def _is_shared_scene_container(name):
    clean = "".join(ch for ch in name.lower() if ch.isalnum())
    shared_names = {
        "characters", "character", "chars", "char",
        "rigs", "assets", "references", "refs",
        "scene", "world", "root", "main",
    }
    return clean in shared_names or clean.endswith("characters") or clean.endswith("rigs")


def _scope_for_object(obj):
    namespace, _ = _split_namespace(obj)
    if namespace:
        return "namespace", namespace, namespace

    parts = _dag_parts(obj)
    if not parts:
        return "scene", "default", "Scene"

    root_index = 0
    while root_index < len(parts) - 1 and _is_shared_scene_container(parts[root_index]):
        root_index += 1
    scope_parts = parts[:root_index + 1]
    scope_path = "|" + "|".join(scope_parts)
    return "root", scope_path, scope_parts[-1]


def _snapshot_key_for_object(obj):
    kind, scope, label = _scope_for_object(obj)
    return _safe_key("{}:{}".format(kind, scope)), label, kind, scope


def _ensure_snapshot_container(data):
    if not isinstance(data, dict):
        data = {}
    data[RESET_SCHEMA_KEY] = RESET_SCHEMA_VERSION
    data.setdefault(RESET_SNAPSHOTS_KEY, {})
    return data


def _legacy_namespace_key(obj):
    namespace, _ = _split_namespace(obj)
    return namespace if namespace else "default"


def _snapshot_scope_matches(obj, snapshot):
    scope = snapshot.get("scope")
    if not scope:
        return False
    obj_long = _long_name(obj)
    return obj_long == scope or obj_long.startswith(scope + "|")


def _snapshot_values_for_object(data, obj):
    snapshots = data.get(RESET_SNAPSHOTS_KEY, {}) if isinstance(data, dict) else {}
    key, _, _, _ = _snapshot_key_for_object(obj)
    snapshot = snapshots.get(key)
    if snapshot:
        return snapshot.get("values", {})

    best = None
    best_len = -1
    for candidate in snapshots.values():
        if not isinstance(candidate, dict):
            continue
        scope = candidate.get("scope", "")
        if _snapshot_scope_matches(obj, candidate) and len(scope) > best_len:
            best = candidate
            best_len = len(scope)
    if best:
        return best.get("values", {})

    legacy_key = _legacy_namespace_key(obj)
    legacy_values = data.get(legacy_key, {}) if isinstance(data, dict) else {}
    return legacy_values if isinstance(legacy_values, dict) else {}


def _snapshot_candidates_for_object(data, obj):
    snapshots = data.get(RESET_SNAPSHOTS_KEY, {}) if isinstance(data, dict) else {}
    key, _, _, _ = _snapshot_key_for_object(obj)
    candidates = []
    if key in snapshots:
        candidates.append((key, snapshots[key]))
    for snap_key, snapshot in snapshots.items():
        if snap_key == key or not isinstance(snapshot, dict):
            continue
        if _snapshot_scope_matches(obj, snapshot):
            candidates.append((snap_key, snapshot))
    return candidates


def _has_control_shape(node):
    try:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        return any(cmds.nodeType(shape) in ("nurbsCurve", "locator") for shape in shapes)
    except Exception:
        return False


def _has_keyable_attrs(node):
    try:
        return bool(cmds.listAttr(node, keyable=True) or [])
    except Exception:
        return False


def _expand_selection_for_snapshot(selection):
    expanded = []
    shape_cache = {}
    keyable_cache = {}

    def _has_control_shape_cached(node):
        if node not in shape_cache:
            shape_cache[node] = _has_control_shape(node)
        return shape_cache[node]

    def _has_keyable_attrs_cached(node):
        if node not in keyable_cache:
            keyable_cache[node] = _has_keyable_attrs(node)
        return keyable_cache[node]

    for item in selection or []:
        if not cmds.objExists(item):
            continue
        candidates = []
        if not _has_control_shape_cached(item):
            try:
                descendants = cmds.listRelatives(
                    item, allDescendents=True, fullPath=True, type="transform"
                ) or []
            except Exception:
                descendants = []
            for node in descendants:
                if _has_control_shape_cached(node) and _has_keyable_attrs_cached(node):
                    candidates.append(node)
        expanded.extend(candidates or [item])
    return list(dict.fromkeys(expanded))


def _snapshot_attrs_for_object(obj, selected_channels=None):
    if selected_channels:
        try:
            existing_attrs = set(cmds.listAttr(obj) or [])
        except Exception:
            existing_attrs = set()
        return [attr for attr in selected_channels if attr in existing_attrs and attr != "tag"]
    try:
        return [
            attr for attr in (cmds.listAttr(obj, keyable=True, unlocked=True, visible=True) or [])
            if attr != "tag"
        ]
    except Exception:
        return []


def _write_reset_data(json_file_path, data):
    atomic_write_json(json_file_path, data, indent=None)


def _standard_default_for_attr(attr):
    attr_lower = attr.lower()

    if attr_lower in TRANSLATE_ATTRS or attr_lower in ROTATE_ATTRS:
        return 0.0
    if attr_lower in SCALE_ATTRS:
        return 1.0
    if attr_lower in SHEAR_ATTRS:
        return 0.0
    if attr_lower in ("visibility", "v"):
        return 1

    return None


def _is_transform_attr(attr):
    attr_lower = attr.lower()
    return (
        attr_lower in TRANSLATE_ATTRS
        or attr_lower in ROTATE_ATTRS
        or attr_lower in SCALE_ATTRS
        or attr_lower in SHEAR_ATTRS
    )


def _fallback_without_snapshot(attr):
    attr_lower = attr.lower()
    if attr_lower in TRANSLATE_ATTRS or attr_lower in ROTATE_ATTRS:
        return 0.0
    return None


def _attr_match_keys(attr):
    attr_lower = str(attr or "").lower()
    return ATTR_ALIASES.get(attr_lower, {attr_lower})


def _attr_matches_normalized_set(attr, normalized_names):
    if not normalized_names:
        return False
    return bool(_attr_match_keys(attr) & normalized_names)


def _normalized_attr_name_set(attrs):
    result = set()
    for attr in attrs or []:
        result.update(_attr_match_keys(attr))
    return result


def _snapshot_attr_names(snapshot_values, short_name):
    if not snapshot_values:
        return set()
    prefix = "{}.".format(short_name)
    return {
        key[len(prefix):]
        for key in snapshot_values
        if isinstance(key, str) and key.startswith(prefix)
    }


def _snapshot_value_lookup(snapshot_values, short_name):
    lookup = {}
    if not snapshot_values:
        return lookup
    prefix = "{}.".format(short_name)
    for key, value in snapshot_values.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        attr = key[len(prefix):]
        for match_key in _attr_match_keys(attr):
            lookup[match_key] = value
    return lookup


def _lookup_snapshot_value(snapshot_lookup, attr):
    for match_key in _attr_match_keys(attr):
        if match_key in snapshot_lookup:
            return True, snapshot_lookup[match_key]
    return False, None


def _connected_attrs_for_object(obj):
    try:
        connections = cmds.listConnections(
            obj,
            source=True,
            destination=False,
            plugs=True,
            connections=True
        ) or []
    except Exception:
        return set()

    obj_names = {_leaf_name(obj), obj}
    if not str(obj).startswith("|"):
        obj_names.add(_long_name(obj))
    connected = set()
    for plug in connections:
        if not isinstance(plug, str) or "." not in plug:
            continue
        node, attr = plug.rsplit(".", 1)
        if node not in obj_names:
            continue
        connected.update(_attr_match_keys(attr.split("[", 1)[0]))
    return connected


def _attrs_to_reset(obj, selected_channels, snapshot_values, short_name,
                    reset_translations, reset_rotations):
    if selected_channels:
        attrs = list(selected_channels)
        assume_existing = False
        assume_unlocked = False
    else:
        try:
            attrs = cmds.listAttr(obj, keyable=True, unlocked=True) or []
        except Exception:
            attrs = []
        assume_existing = True
        assume_unlocked = True

        snapshot_names = _snapshot_attr_names(snapshot_values, short_name)
        if snapshot_names:
            normalized_snapshot_names = _normalized_attr_name_set(snapshot_names)
            attrs = [
                attr for attr in attrs
                if _attr_matches_normalized_set(attr, normalized_snapshot_names)
                or _fallback_without_snapshot(attr) is not None
            ]
        else:
            attrs = [
                attr for attr in attrs
                if _fallback_without_snapshot(attr) is not None
            ]

    filtered = []
    seen = set()
    for attr in attrs:
        if attr == "tag":
            continue
        if not _attr_allowed_by_mode(attr, reset_translations, reset_rotations):
            continue
        attr_key = attr.lower()
        if attr_key in seen:
            continue
        seen.add(attr_key)
        filtered.append(attr)

    return filtered, assume_existing, assume_unlocked


def _get_default_value(obj, attr):
    try:
        default_value = cmds.attributeQuery(attr, node=obj, listDefault=True)
        if default_value:
            return default_value[0]
    except Exception:
        pass

    return _standard_default_for_attr(attr)


def _attr_allowed_by_mode(attr, reset_translations, reset_rotations):
    attr_lower = attr.lower()
    if reset_translations and attr_lower not in TRANSLATE_ATTRS:
        return False
    if reset_rotations and attr_lower not in ROTATE_ATTRS:
        return False
    return True


def _node_type_from_plug(plug):
    node = plug.split('.', 1)[0]
    try:
        return cmds.nodeType(node)
    except Exception:
        return ""


def _source_has_animation(plug, visited=None, depth=0):
    if visited is None:
        visited = set()
    if depth > 8:
        return False

    node = plug.split('.', 1)[0]
    if node in visited:
        return False
    visited.add(node)

    node_type = _node_type_from_plug(plug)
    if node_type.startswith(ANIM_CURVE_PREFIX):
        return True
    if node_type.startswith("animBlendNode"):
        return True
    if node_type not in ANIM_DRIVER_TYPES:
        return False

    try:
        upstream = cmds.listConnections(node, source=True, destination=False, plugs=True) or []
    except Exception:
        upstream = []

    return any(_source_has_animation(source, visited, depth + 1) for source in upstream)


def _attr_is_animation_driven(obj, attr, attr_path, sources):
    if any(_source_has_animation(source) for source in sources):
        return True

    try:
        return bool(cmds.keyframe(attr_path, query=True, keyframeCount=True))
    except Exception:
        return False


def _current_time():
    try:
        return cmds.currentTime(query=True)
    except Exception:
        return None


def _refresh_current_time(time_value):
    if time_value is None:
        return
    try:
        cmds.currentTime(time_value, edit=True, update=True)
    except TypeError:
        try:
            cmds.currentTime(time_value, edit=True)
        except Exception:
            pass
    except Exception:
        pass
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _compound_child_attrs(attr):
    attr_lower = attr.lower()
    return {
        "translate": ("translateX", "translateY", "translateZ"),
        "rotate": ("rotateX", "rotateY", "rotateZ"),
        "scale": ("scaleX", "scaleY", "scaleZ"),
        "shear": ("shearXY", "shearXZ", "shearYZ"),
    }.get(attr_lower)


def _set_key_value(attr_path, time_value, value):
    try:
        if time_value is None:
            cmds.setKeyframe(attr_path, value=value)
        else:
            cmds.setKeyframe(attr_path, time=(time_value, time_value), value=value)
        return True
    except Exception:
        return False


def _set_attr_if_settable(attr_path, value):
    try:
        cmds.setAttr(attr_path, value)
        return True
    except Exception:
        return False


def _set_reset_value(obj, attr, value, time_value=None, connected_attrs=None,
                     assume_existing=False, assume_unlocked=False):
    child_attrs = _compound_child_attrs(attr)
    if child_attrs:
        did_set = False
        for child_attr in child_attrs:
            did_set = _set_reset_value(
                obj,
                child_attr,
                value,
                time_value=time_value,
                connected_attrs=connected_attrs,
                assume_existing=False,
                assume_unlocked=assume_unlocked
            ) or did_set
        return did_set

    attr_path = "{}.{}".format(obj, attr)

    try:
        if not assume_existing and not cmds.attributeQuery(attr, node=obj, exists=True):
            return False
        if not assume_unlocked and cmds.getAttr(attr_path, lock=True):
            return False

        is_connected = _attr_matches_normalized_set(attr, connected_attrs)
        if _set_attr_if_settable(attr_path, value):
            if is_connected:
                _set_key_value(attr_path, time_value, value)
            return True

        if is_connected:
            return _set_key_value(attr_path, time_value, value)
        return False
    except Exception as e:
        print("Could not reset the attribute {} on {}: {}".format(attr, obj, str(e)))
        return False


def reset_object_values(reset_translations=False, reset_rotations=False):
    """
    Reset object values to their default values.
    If custom defaults are saved, those are used instead.
    
    Args:
        reset_translations: If True, only reset translation attributes
        reset_rotations: If True, only reset rotation attributes
    """
    cmds.undoInfo(openChunk=True)
    refresh_suspended = False
    did_set_any = False
    time_value = None
    
    try:
        json_file_path = get_default_data_file()
        
        data = _load_default_data(json_file_path)
        
        selected_objects = _expand_selection_for_snapshot(cmds.ls(selection=True, long=True))
        selected_channels = get_selected_channels()
        
        if not selected_objects:
            cmds.warning("AnimKey: Please select at least one object.")
            return

        time_value = _current_time()
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            refresh_suspended = False
        
        for obj in selected_objects:
            # ── Unified Name Parsing (Fixes DAG path issues) ────────────────
            _, nombre_corto = _split_namespace(obj)
            snapshot_values = _snapshot_values_for_object(data, obj)
            snapshot_lookup = _snapshot_value_lookup(snapshot_values, nombre_corto)
            attrs, assume_existing, assume_unlocked = _attrs_to_reset(
                obj,
                selected_channels,
                snapshot_values,
                nombre_corto,
                reset_translations,
                reset_rotations
            )
            
            if not attrs:
                continue

            connected_attrs = _connected_attrs_for_object(obj)
            
            for attr in attrs:
                has_snapshot_value, snapshot_value = _lookup_snapshot_value(snapshot_lookup, attr)

                if has_snapshot_value:
                    value = snapshot_value
                else:
                    fallback_value = _fallback_without_snapshot(attr)
                    if fallback_value is not None:
                        value = fallback_value
                    elif _is_transform_attr(attr):
                        continue
                    else:
                        value = _get_default_value(obj, attr)
                if value is None:
                    continue

                if _set_reset_value(
                    obj,
                    attr,
                    value,
                    time_value=time_value,
                    connected_attrs=connected_attrs,
                    assume_existing=assume_existing,
                    assume_unlocked=assume_unlocked
                ):
                    did_set_any = True
    
    except Exception as e:
        cmds.warning("AnimKey: Error during reset: {}".format(str(e)))
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        if did_set_any:
            _refresh_current_time(time_value)
        cmds.undoInfo(closeChunk=True)


def save_default_values(*args):
    """
    Save current values as default values for selected objects.
    These values will be used when resetting objects.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.resetValues.save_default_values"):
        return None
    started = time.time()
    selected_objects = _expand_selection_for_snapshot(cmds.ls(selection=True, long=True))
    
    if not selected_objects:
        cmds.warning("AnimKey: Please select at least one object.")
        return
    
    json_file_path = get_default_data_file()
    
    # Ensure folder exists
    os.makedirs(os.path.dirname(json_file_path), exist_ok=True)
    
    # Read existing data from JSON file if it exists
    data = _load_default_data(json_file_path)
    data = _ensure_snapshot_container(data)
    
    selected_channels = get_selected_channels()
    touched_labels = set()
    refresh_suspended = False
    try:
        cmds.refresh(suspend=True)
        refresh_suspended = True
    except Exception:
        pass

    for obj in selected_objects:
        # ── Unified Name Parsing (Fixes DAG path issues) ────────────────
        namespace, nombre_corto = _split_namespace(obj)

        snapshot_key, label, kind, scope = _snapshot_key_for_object(obj)
        snapshot = data[RESET_SNAPSHOTS_KEY].setdefault(snapshot_key, {
            "label": label,
            "kind": kind,
            "scope": scope,
            "values": {},
        })
        snapshot["label"] = label
        snapshot["kind"] = kind
        snapshot["scope"] = scope
        snapshot.setdefault("values", {})
        touched_labels.add(label)

        legacy_key = namespace if namespace else None
        if legacy_key and legacy_key not in data:
            data[legacy_key] = {}
        
        # Determine which attributes to save
        # If channels are selected in Channel Box, take only those. 
        # Otherwise take all keyable/unlocked/visible.
        atributos = _snapshot_attrs_for_object(obj, selected_channels)
        
        # Update or add attribute values, excluding "tag" attribute
        for attr in atributos:
            atributo_completo = f'{nombre_corto}.{attr}'
            try:
                valor = cmds.getAttr(f'{obj}.{attr}')
                snapshot["values"][atributo_completo] = valor
                if legacy_key:
                    data[legacy_key][atributo_completo] = valor
            except:
                continue

    if refresh_suspended:
        try:
            cmds.refresh(suspend=False)
        except Exception:
            pass
        refresh_suspended = False
    
    # Save updated data to JSON file
    _write_reset_data(json_file_path, data)
    
    if touched_labels:
        cmds.warning("AnimKey: Default values snapshot saved for {} ({:.2f}s)".format(
            ", ".join(sorted(touched_labels)), time.time() - started
        ))
    else:
        cmds.warning('AnimKey: Default values saved')


def restore_default_data(*args):
    """
    Clear all saved default values data.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.resetValues.restore_default_data"):
        return None
    json_file_path = get_default_data_file()
    
    # Check if file exists and empty its content
    if os.path.exists(json_file_path):
        _write_reset_data(json_file_path, {})
        
        cmds.warning("AnimKey: All default values cleared")
    else:
        cmds.warning("AnimKey: No saved data file found.")


def remove_default_values_for_selected_object(*args):
    """
    Remove saved default values for selected objects.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.resetValues.remove_default_values_for_selected_object"):
        return None
    json_file_path = get_default_data_file()
    
    # Read existing data from JSON file if it exists
    if os.path.exists(json_file_path):
        data = _load_default_data(json_file_path)
    else:
        cmds.warning("AnimKey: No saved data file found.")
        return
    data = _ensure_snapshot_container(data)
    
    selected_objects = _expand_selection_for_snapshot(cmds.ls(selection=True, long=True))
    
    if not selected_objects:
        cmds.warning("AnimKey: Please select at least one object.")
        return
    
    for obj in selected_objects:
        # ── Unified Name Parsing (Fixes DAG path issues) ────────────────
        namespace, nombre_corto = _split_namespace(obj)

        for snapshot_key, snapshot in _snapshot_candidates_for_object(data, obj):
            values = snapshot.get("values", {})
            keys_to_remove = [key for key in values if key.startswith(nombre_corto + ".")]
            for key in keys_to_remove:
                del values[key]
            if not values:
                del data[RESET_SNAPSHOTS_KEY][snapshot_key]

        legacy_key = namespace if namespace else "default"
        # Remove object information from JSON
        if legacy_key in data:
            # Create list of keys to remove to avoid modifying dictionary during iteration
            keys_to_remove = [key for key in data[legacy_key] if key.startswith(nombre_corto + ".")]
            
            for key in keys_to_remove:
                del data[legacy_key][key]
            
            # If namespace is empty, remove it too
            if not data[legacy_key]:
                del data[legacy_key]
    
    # Save updated data to JSON file
    _write_reset_data(json_file_path, data)
    
    cmds.warning(f"AnimKey: Default values removed for selected objects")


def execute_shortcut(*args):
    """Reset from the shortcut system without interpreting Ctrl/Shift as click modes."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.resetValues.execute_shortcut"):
        return None
    reset_object_values()


def execute(*args, **kwargs):
    """
    Main function to execute when button is clicked.
    Checks if Shift or Ctrl is pressed to determine which function to call.
    
    - Normal click: Reset all attributes to default
    - Shift + Click: Reset only translations
    - Ctrl + Click: Reset only rotations
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.resetValues.execute"):
        return None
    if kwargs.get("ignore_modifiers") or kwargs.get("from_shortcut"):
        reset_object_values()
        return

    # Get the current state of the modifiers
    mods = mel.eval('getModifiers')
    shift_pressed = bool(mods % 2)  # Check if Shift is pressed
    ctrl_pressed = bool(mods & 4)  # Check if Ctrl is pressed
    
    if shift_pressed:
        reset_object_values(reset_translations=True)
    elif ctrl_pressed:
        reset_object_values(reset_rotations=True)
    else:
        reset_object_values()


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Reset Values",
        "tooltip": "Reset objects to their default values. Select channels in Channel Box to reset only those channels. Shift + Click: Reset Translations. Ctrl + Click: Reset Rotations.",
        "icon": "reset_values.svg",
        "shortcut": None,
    }
