"""
    AnimKey Button: Trail (Version 02)
    
    Creates a native Maya motion trail on the selected object.
    AnimKey owns the generated trail through dagContainers.
    
    Structure:
    - AnimKey (dagContainer)
      - animkey_trail (dagContainer)
        - animkey_[objectName]_Trail (dagContainer)
        - trailHandle and trail history node
"""

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import os
import math
import bisect
import re
import time
import sys

from AnimKey.mods import configMod as config


TRAIL_ROOT = "animkey_trail"
TRAIL_OBJECT_ATTR = "animKeyTrailObject"
TRAIL_COMPONENT_ATTR = "animKeyTrailComponent"
TRAIL_CURRENT_MARKER_ATTR = "animKeyTrailCurrentMarker"
TRAIL_CURRENT_MATRIX_ATTR = "animKeyTrailCurrentMatrixNode"
TRAIL_SCRIPTJOBS_ATTR = "animKeyTrailScriptJobs"
TRAIL_TANGENT_GROUP_ATTR = "animKeyTrailTangentGroup"
TRAIL_KEY_GROUP_ATTR = "animKeyTrailKeyGroup"
TRAIL_KEY_SIGNATURE_ATTR = "animKeyTrailKeySignature"
TRAIL_RANGE_START_ATTR = "animKeyTrailStartFrame"
TRAIL_RANGE_END_ATTR = "animKeyTrailEndFrame"
TRAIL_DIRTY_START_ATTR = "dirtyStartTime"
TRAIL_DIRTY_END_ATTR = "dirtyEndTime"
TRAIL_SETTINGS_KEY = "motion_trail_settings"
TRAIL_MAX_SAMPLES = 180
TRAIL_MAX_KEY_MARKERS = 70
TRAIL_USE_CUSTOM_TANGENT_NODES = True
_TRAIL_SCRIPTJOBS = []
_TRAIL_REBUILD_PENDING = set()
_TRAIL_REDRAW_PENDING = set()
_TRAIL_KEY_SYNC_PENDING = set()
_TRAIL_PENDING_RANGES = {}
_TRAIL_TANGENT_CALLBACKS = []
_TRAIL_KEY_CALLBACKS = []
_TRAIL_ANIM_CALLBACKS = []
_TRAIL_VERTEX_CALLBACKS = []
_TRAIL_KEY_TIMES_CACHE = {}
_TRAIL_WATCHED_CURVES_CACHE = {}
_TRAIL_LAST_TANGENT_REDRAW = {}
_TRAIL_VERTEX_REBAKE_PENDING = set()
_TRAIL_REBUILDING = False
_TRAIL_TANGENT_UPDATE_LOCK = False
_TRAIL_KEY_UPDATE_LOCK = False
_TRAIL_INTERNAL_UPDATE = False
_TRAIL_TANGENT_REDRAW_INTERVAL = 0.18

DEFAULT_TRAIL_SETTINGS = {
    "trail_increment": 1,
    "sample_density": 1,
    "max_samples": 180,
    "max_key_markers": 70,
    "show_key_markers": True,
    "show_glow": False,
    "line_width": 2.0,
    "glow_width": 7.0,
    "marker_scale": 1.0,
    "live_refresh": False,
    "show_key_handles": True,
    "show_tangent_handles": False,
    "show_pop_warnings": True,
    "pop_threshold": 0.4,
    "palette": "cyan",
    "custom_colors": {},
}

TRAIL_PALETTES = {
    "cyan": {
        "main": (1.0, 0.82, 0.18),
        "past": (1.0, 0.82, 0.18),
        "future": (1.0, 0.82, 0.18),
        "extra": (0.60, 0.42, 0.08),
        "glow": (0.18, 0.12, 0.03),
        "key": (1.0, 0.86, 0.36),
        "past_key": (1.0, 0.82, 0.18),
        "future_key": (1.0, 0.82, 0.18),
        "pop": (1.0, 0.12, 0.24),
        "current": (0.18, 0.56, 1.0),
    },
    "red": {
        "main": (1.0, 0.34, 0.30),
        "past": (0.55, 0.12, 0.10),
        "future": (1.0, 0.34, 0.30),
        "extra": (0.50, 0.13, 0.11),
        "glow": (0.18, 0.055, 0.045),
        "key": (1.0, 0.82, 0.50),
        "past_key": (1.0, 0.72, 0.22),
        "future_key": (0.35, 0.86, 1.0),
        "pop": (1.0, 0.10, 0.18),
        "current": (0.35, 0.86, 1.0),
    },
    "grey": {
        "main": (0.70, 0.74, 0.78),
        "past": (0.34, 0.36, 0.38),
        "future": (0.70, 0.74, 0.78),
        "extra": (0.34, 0.36, 0.38),
        "glow": (0.10, 0.11, 0.12),
        "key": (0.98, 0.86, 0.45),
        "past_key": (0.96, 0.76, 0.28),
        "future_key": (0.48, 0.78, 1.0),
        "pop": (1.0, 0.18, 0.28),
        "current": (1.0, 0.42, 0.18),
    },
    "green": {
        "main": (0.35, 0.88, 0.48),
        "past": (0.12, 0.42, 0.25),
        "future": (0.35, 0.88, 0.48),
        "extra": (0.12, 0.42, 0.25),
        "glow": (0.045, 0.16, 0.09),
        "key": (1.0, 0.86, 0.36),
        "past_key": (0.95, 0.78, 0.24),
        "future_key": (0.35, 0.86, 1.0),
        "pop": (1.0, 0.12, 0.24),
        "current": (1.0, 0.45, 0.18),
    },
    "purple": {
        "main": (0.72, 0.42, 1.0),
        "past": (0.30, 0.18, 0.54),
        "future": (0.72, 0.42, 1.0),
        "extra": (0.30, 0.18, 0.54),
        "glow": (0.12, 0.055, 0.20),
        "key": (1.0, 0.76, 0.38),
        "past_key": (1.0, 0.76, 0.32),
        "future_key": (0.32, 0.90, 1.0),
        "pop": (1.0, 0.12, 0.36),
        "current": (0.30, 0.90, 1.0),
    },
    "gold": {
        "main": (1.0, 0.66, 0.18),
        "past": (0.46, 0.27, 0.08),
        "future": (1.0, 0.66, 0.18),
        "extra": (0.46, 0.27, 0.08),
        "glow": (0.18, 0.10, 0.035),
        "key": (0.52, 0.90, 1.0),
        "past_key": (0.88, 0.70, 0.18),
        "future_key": (0.52, 0.90, 1.0),
        "pop": (1.0, 0.10, 0.20),
        "current": (1.0, 0.24, 0.24),
    },
    "custom": {
        "main": (1.0, 0.82, 0.18),
        "past": (1.0, 0.82, 0.18),
        "future": (1.0, 0.82, 0.18),
        "extra": (0.60, 0.42, 0.08),
        "glow": (0.18, 0.12, 0.03),
        "key": (1.0, 0.86, 0.36),
        "past_key": (1.0, 0.82, 0.18),
        "future_key": (1.0, 0.82, 0.18),
        "pop": (1.0, 0.12, 0.24),
        "current": (0.18, 0.56, 1.0),
    },
}

TRAIL_COLOR_SLOTS = ("past", "future", "current", "key", "past_key", "future_key", "pop")


def _get_icon_path(icon_name=None):
    """
    Get path to AnimKey icon file for outliner display.
    
    Args:
        icon_name: Optional specific icon name (defaults to Outliner_AnimKey.png)
    
    Returns:
        Full path to the icon file, or empty string if not found
    """
    if icon_name is None:
        icon_name = "animkey_outliner_minimal_32.png"
    
    # Try different possible locations
    possible_paths = []
    
    # 1. Relative to this file (development and installed location)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths.append(os.path.join(current_dir, "..", "data", "icons", icon_name))
    
    # Find and return the first existing path
    for path in possible_paths:
        normalized_path = os.path.normpath(path)
        if os.path.exists(normalized_path):
            return normalized_path
    
    return ""


def _motion_trail_plugin_path():
    plugin_name = "animKeyTrailPlugin.py"
    current_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_paths = [
        os.path.normpath(os.path.join(current_dir, "..", "plugins", plugin_name)),
        os.path.normpath(os.path.join(cmds.internalVar(userAppDir=True), "AnimKey", "plugins", plugin_name)),
        os.path.normpath(os.path.join(cmds.internalVar(userScriptDir=True), "AnimKey", "plugins", plugin_name)),
    ]
    return next((path for path in plugin_paths if os.path.exists(path)), None)


def _motion_trail_plugin_names():
    plugin_name = "animKeyTrailPlugin.py"
    return plugin_name, os.path.splitext(plugin_name)[0]


def _is_motion_trail_plugin_loaded():
    for query_name in _motion_trail_plugin_names():
        try:
            if cmds.pluginInfo(query_name, query=True, loaded=True):
                return True
        except Exception:
            pass
    return False


def _ensure_plugin_loaded():
    """Ensure the custom Python API 2.0 motion trail plugin is loaded"""
    if _is_motion_trail_plugin_loaded():
        return True
    try:
        plugin_path = _motion_trail_plugin_path()
        if not plugin_path:
            cmds.warning(
                "AnimKey: Motion trail plugin file was not found. "
                "Reinstall AnimKey so AnimKey/plugins is copied correctly."
            )
            return False
        cmds.loadPlugin(plugin_path)
    except Exception as e:
        cmds.warning(f"AnimKey: Failed to load motion trail plugin: {e}")
        return False

    if not _is_motion_trail_plugin_loaded():
        cmds.warning("AnimKey: Motion trail plugin did not finish loading.")
        return False
    return True


def _reload_motion_trail_plugin():
    """Reload the plugin from disk so new attributes are registered in Maya."""
    plugin_path = _motion_trail_plugin_path()
    if not plugin_path:
        cmds.warning("AnimKey: Motion trail plugin file was not found.")
        return False

    _cleanup_failed_custom_trail_nodes()

    for query_name in _motion_trail_plugin_names():
        try:
            if cmds.pluginInfo(query_name, query=True, loaded=True):
                cmds.unloadPlugin(query_name, force=True)
                break
        except Exception:
            pass

    for module_name in ("animKeyTrailPlugin", "AnimKey.plugins.animKeyTrailPlugin"):
        try:
            sys.modules.pop(module_name, None)
        except Exception:
            pass

    try:
        cmds.loadPlugin(plugin_path)
    except Exception as e:
        cmds.warning(f"AnimKey: Could not reload motion trail plugin: {e}")
        return False

    return _is_motion_trail_plugin_loaded()


def _create_animkey_motion_trail_shape():
    try:
        return cmds.createNode("animKeyMotionTrail", name="trailShape")
    except Exception:
        return None


def _cleanup_failed_custom_trail_nodes():
    for node in ("trailShape", "trailHandleShape", "trailHandle"):
        if cmds.objExists(node):
            try:
                cmds.delete(node)
            except Exception:
                pass


def _create_custom_trail_node(object_name, start_frame, end_frame, increment, sample_density=1, source_component=""):
    if not _ensure_plugin_loaded():
        return None

    trail_node = None
    for attempt in range(2):
        trail_node = _create_animkey_motion_trail_shape()
        if not trail_node or not cmds.objExists(trail_node) or cmds.nodeType(trail_node) != "animKeyMotionTrail":
            _cleanup_failed_custom_trail_nodes()
            raise RuntimeError("custom motion trail node was not registered")

        cmds.connectAttr(object_name + ".worldMatrix[0]", trail_node + ".targetMatrix", force=True)
        if not source_component:
            break
        if _set_attr_if_exists(trail_node, "sourceComponent", source_component, attr_type="string"):
            break
        _cleanup_failed_custom_trail_nodes()
        if attempt == 0 and _reload_motion_trail_plugin():
            continue
        raise RuntimeError(
            "vertex motion trails require the updated animKeyTrailPlugin.py to be reloaded"
        )

    cmds.setAttr(trail_node + ".startTime", start_frame)
    cmds.setAttr(trail_node + ".endTime", end_frame)
    cmds.setAttr(trail_node + ".increment", increment)
    _set_attr_if_exists(trail_node, "sampleDensity", max(1, int(sample_density)))

    trail_parent = cmds.listRelatives(trail_node, parent=True, fullPath=False) or []
    if not trail_parent:
        _cleanup_failed_custom_trail_nodes()
        raise RuntimeError("custom motion trail node has no transform parent")

    trail_transform = cmds.rename(trail_parent[0], "trailHandle")
    _make_node_display_only(trail_transform)
    return trail_transform


def _create_fallback_trail_visuals(trail_container, object_name):
    """Fallback trail that does not depend on the custom viewport plugin."""
    return _create_premium_trail_visuals(trail_container, object_name)


def _create_animkey_container():
    """Create AnimKey dagContainer if it doesn't exist"""
    if not cmds.objExists("AnimKey"):
        # Create dagContainer (like AnimKey's AnimKey)
        container = cmds.container(type='dagContainer', name="AnimKey")
        
        # Set custom icon for outliner
        icon_path = _get_icon_path()
        if icon_path:
            try:
                cmds.setAttr(container + '.iconName', icon_path, type='string')
            except Exception as e:
                print(f"AnimKey: Could not set icon: {e}")
        
        # Lock and hide transform attributes
        attributes = ["translateX", "translateY", "translateZ",
                     "rotateX", "rotateY", "rotateZ",
                     "scaleX", "scaleY", "scaleZ", "visibility"]
        
        for attr in attributes:
            try:
                cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
            except:
                pass
    else:
        # Update icon on existing container if missing
        container = "AnimKey"
        try:
            current_icon = cmds.getAttr(container + '.iconName')
            if not current_icon:
                icon_path = _get_icon_path()
                if icon_path:
                    cmds.setAttr(container + '.iconName', icon_path, type='string')
        except:
            pass


def _create_trail_root_container():
    """Create animkey_trail dagContainer if it doesn't exist"""
    if not cmds.objExists("animkey_trail"):
        # Create dagContainer for AnimKey motion trails.
        container = cmds.container(type='dagContainer', name="animkey_trail")
        
        # Set custom icon for outliner (same icon as parent)
        icon_path = _get_icon_path()
        if icon_path:
            try:
                cmds.setAttr(container + '.iconName', icon_path, type='string')
            except Exception as e:
                print(f"AnimKey: Could not set icon: {e}")
        
        # Parent to AnimKey
        if cmds.objExists("AnimKey"):
            cmds.parent(container, "AnimKey")
        
        # Lock and hide transform attributes
        attributes = ["translateX", "translateY", "translateZ",
                     "rotateX", "rotateY", "rotateZ",
                     "scaleX", "scaleY", "scaleZ", "visibility"]
        
        for attr in attributes:
            try:
                cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
            except:
                pass
    else:
        # Update icon on existing container if missing
        container = "animkey_trail"
        try:
            current_icon = cmds.getAttr(container + '.iconName')
            if not current_icon:
                icon_path = _get_icon_path()
                if icon_path:
                    cmds.setAttr(container + '.iconName', icon_path, type='string')
        except:
            pass


def _get_object_short_name(obj):
    """Get short name of object (without namespace)"""
    return _safe_node_fragment(obj)


def _coerce_color_tuple(value, fallback):
    try:
        values = list(value)
    except Exception:
        return tuple(fallback)
    if len(values) < 3:
        return tuple(fallback)
    coerced = []
    for channel in values[:3]:
        try:
            coerced.append(max(0.0, min(1.0, float(channel))))
        except Exception:
            return tuple(fallback)
    return tuple(coerced)


def _resolved_trail_palette(settings=None):
    settings = settings or get_trail_settings()
    palette_name = settings.get("palette", "cyan")
    base = TRAIL_PALETTES.get(palette_name, TRAIL_PALETTES["cyan"]).copy()
    if palette_name == "custom":
        custom_colors = settings.get("custom_colors") or {}
        for slot in TRAIL_COLOR_SLOTS:
            if slot in custom_colors:
                base[slot] = _coerce_color_tuple(custom_colors[slot], base[slot])
    base["main"] = base.get("future", base.get("main", TRAIL_PALETTES["cyan"]["main"]))
    base["extra"] = base.get("past", base.get("extra", TRAIL_PALETTES["cyan"]["extra"]))
    return base


def _coerce_trail_settings(settings):
    merged = DEFAULT_TRAIL_SETTINGS.copy()
    if isinstance(settings, dict):
        merged.update(settings)

    def _int_value(key, minimum, maximum):
        try:
            value = int(merged.get(key, DEFAULT_TRAIL_SETTINGS[key]))
        except Exception:
            value = DEFAULT_TRAIL_SETTINGS[key]
        merged[key] = max(minimum, min(maximum, value))

    def _float_value(key, minimum, maximum):
        try:
            value = float(merged.get(key, DEFAULT_TRAIL_SETTINGS[key]))
        except Exception:
            value = DEFAULT_TRAIL_SETTINGS[key]
        merged[key] = max(minimum, min(maximum, value))

    _int_value("trail_increment", 1, 12)
    _int_value("sample_density", 1, 8)
    _int_value("max_samples", 24, 500)
    _int_value("max_key_markers", 0, 240)
    _float_value("line_width", 1.0, 10.0)
    _float_value("glow_width", 1.0, 16.0)
    _float_value("marker_scale", 0.25, 4.0)
    _float_value("pop_threshold", 0.1, 3.0)

    for key in ("show_key_markers", "show_glow", "show_key_handles", "show_tangent_handles", "show_pop_warnings"):
        merged[key] = bool(merged.get(key, DEFAULT_TRAIL_SETTINGS[key]))
    merged["live_refresh"] = False

    if merged.get("palette") not in TRAIL_PALETTES:
        merged["palette"] = DEFAULT_TRAIL_SETTINGS["palette"]

    custom_colors = merged.get("custom_colors")
    if not isinstance(custom_colors, dict):
        custom_colors = {}
    coerced_custom = {}
    for slot in TRAIL_COLOR_SLOTS:
        fallback = TRAIL_PALETTES["custom"][slot]
        if slot in custom_colors:
            coerced_custom[slot] = _coerce_color_tuple(custom_colors[slot], fallback)
    merged["custom_colors"] = coerced_custom

    return merged


def get_trail_settings():
    """Return persisted custom motion-trail settings."""
    return _coerce_trail_settings(config.get_setting(TRAIL_SETTINGS_KEY, DEFAULT_TRAIL_SETTINGS.copy()))


def _save_trail_settings(settings):
    config.set_setting(TRAIL_SETTINGS_KEY, _coerce_trail_settings(settings))


def _refresh_active_trail_if_needed(refresh=True):
    if refresh and has_active_trail():
        _apply_trail_appearance()


def set_trail_setting(key, value, refresh=True):
    settings = get_trail_settings()
    settings[key] = value
    _save_trail_settings(settings)
    _refresh_active_trail_if_needed(refresh)


def set_trail_quality(preset, refresh=True):
    settings = get_trail_settings()
    if preset == "performance":
        settings.update({"trail_increment": 4, "sample_density": 1, "max_samples": 90, "max_key_markers": 35})
    elif preset == "cinematic":
        settings.update({"trail_increment": 1, "sample_density": 4, "max_samples": 360, "max_key_markers": 160})
    else:
        settings.update({"trail_increment": 2, "sample_density": 1, "max_samples": 180, "max_key_markers": 70})
    _save_trail_settings(settings)
    _refresh_active_trail_if_needed(refresh)


def set_trail_palette(palette, refresh=True):
    settings = get_trail_settings()
    settings["palette"] = palette
    _save_trail_settings(settings)
    if refresh:
        _apply_trail_appearance(rebuild_cache=False)


def get_trail_color(slot):
    settings = get_trail_settings()
    palette = _resolved_trail_palette(settings)
    return tuple(palette.get(slot, TRAIL_PALETTES["cyan"].get(slot, TRAIL_PALETTES["cyan"]["main"])))


def set_trail_custom_color(slot, color, refresh=True):
    if slot not in TRAIL_COLOR_SLOTS:
        return
    settings = get_trail_settings()
    settings["palette"] = "custom"
    custom_colors = settings.get("custom_colors") or {}
    custom_colors[slot] = _coerce_color_tuple(color, TRAIL_PALETTES["custom"][slot])
    settings["custom_colors"] = custom_colors
    _save_trail_settings(settings)
    if refresh:
        _apply_trail_appearance(rebuild_cache=False)


def set_trail_pop_warnings(enabled, refresh=True):
    settings = get_trail_settings()
    settings["show_pop_warnings"] = bool(enabled)
    _save_trail_settings(settings)
    if refresh:
        _apply_trail_appearance()


def set_trail_tangent_handles(enabled, refresh=True):
    settings = get_trail_settings()
    settings["show_tangent_handles"] = bool(enabled)
    _save_trail_settings(settings)
    if not refresh:
        return
    trail_targets = _iter_trail_containers()
    if not trail_targets:
        return
    _clear_tangent_callbacks()
    for trail_container, object_name in trail_targets:
        if not trail_container or not cmds.objExists(trail_container):
            continue
        source_component = _get_string_attr(trail_container, TRAIL_COMPONENT_ATTR, "")
        if enabled and not source_component and object_name and cmds.objExists(object_name):
            _create_editable_tangent_handles(trail_container, object_name)
        else:
            _clear_editable_tangent_handles(trail_container)
        _register_tangent_handle_callbacks(trail_container)
        _dirty_custom_trail(trail_container)


def set_trail_key_handles(enabled, refresh=True):
    settings = get_trail_settings()
    settings["show_key_handles"] = bool(enabled)
    _save_trail_settings(settings)
    if not refresh:
        return
    trail_targets = _iter_trail_containers()
    if not trail_targets:
        return
    _clear_key_callbacks()
    for trail_container, object_name in trail_targets:
        if not trail_container or not cmds.objExists(trail_container):
            continue
        source_component = _get_string_attr(trail_container, TRAIL_COMPONENT_ATTR, "")
        if enabled and not source_component and object_name and cmds.objExists(object_name):
            _create_editable_key_handles(trail_container, object_name)
        else:
            _clear_editable_key_handles(trail_container)
        _register_key_handle_callbacks(trail_container)
        _dirty_custom_trail(trail_container)


def set_trail_pop_threshold(value, refresh=True):
    settings = get_trail_settings()
    settings["pop_threshold"] = max(0.1, min(3.0, float(value)))
    _save_trail_settings(settings)
    if refresh:
        _apply_trail_appearance()


def set_trail_live_refresh(enabled, refresh=False):
    set_trail_setting("live_refresh", False, refresh=False)
    existing_trail, _ = _find_existing_trail()
    if existing_trail:
        _clear_trail_scriptjobs(existing_trail)


def set_trail_key_markers(enabled, refresh=True):
    settings = get_trail_settings()
    settings["show_key_markers"] = bool(enabled)
    _save_trail_settings(settings)
    if refresh:
        _apply_trail_appearance()


def set_trail_glow(enabled, refresh=True):
    settings = get_trail_settings()
    settings["show_glow"] = False
    _save_trail_settings(settings)
    if refresh:
        _apply_trail_appearance()


def set_trail_line_width(width, refresh=True):
    settings = get_trail_settings()
    settings["line_width"] = float(width)
    settings["glow_width"] = max(float(width) + 3.5, float(width) * 2.2)
    _save_trail_settings(settings)
    if refresh:
        _apply_trail_appearance()


def _safe_node_fragment(name):
    """Return a Maya-safe short name fragment."""
    short = name.split("|")[-1].split(":")[-1]
    short = re.sub(r"[^A-Za-z0-9_]+", "_", short).strip("_")
    return short or "object"


def _component_owner_node(component):
    node = component.split(".vtx[", 1)[0]
    try:
        node_type = cmds.nodeType(node)
    except Exception:
        node_type = None
    if node_type == "mesh":
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        return parents[0] if parents else node
    return node


def _selected_trail_source():
    raw_selection = cmds.ls(selection=True, long=True, flatten=True) or []
    vertices = cmds.filterExpand(raw_selection, selectionMask=31, fullPath=True) or []
    if vertices:
        if len(vertices) != 1:
            cmds.warning("AnimKey: Please select only one vertex for a vertex motion trail.")
            return None
        component = vertices[0]
        owner = _component_owner_node(component)
        if not owner or not cmds.objExists(owner):
            cmds.warning("AnimKey: Could not resolve the selected vertex owner.")
            return None
        label = f"{_safe_node_fragment(owner)}_{_safe_node_fragment(component)}"
        return {
            "object": owner,
            "component": component,
            "label": label,
            "is_component": True,
        }

    selected_objects = cmds.ls(selection=True, long=True) or []
    if len(selected_objects) != 1:
        cmds.warning("AnimKey: Please select only one object or one vertex.")
        return None
    object_name = selected_objects[0]
    return {
        "object": object_name,
        "component": "",
        "label": object_name,
        "is_component": False,
    }


def _vertex_bake_times(start, end):
    start = float(start)
    end = float(end)
    if end < start:
        start, end = end, start
    start_i = int(math.floor(start))
    end_i = int(math.ceil(end))
    times = {float(frame) for frame in range(start_i, end_i + 1)}
    times.add(start)
    times.add(end)
    return sorted(times)


def _create_or_get_vertex_bake_locator(trail_container, locator=None):
    if locator and cmds.objExists(locator):
        return locator
    locator = cmds.spaceLocator(name=f"{trail_container}_vertexBake_LOC")[0]
    try:
        cmds.parent(locator, trail_container, absolute=True)
    except Exception:
        pass
    _add_node_to_container(locator, trail_container)
    shapes = cmds.listRelatives(locator, shapes=True, fullPath=True) or []
    for shape in shapes:
        try:
            cmds.setAttr(f"{shape}.visibility", 0)
        except Exception:
            pass
    try:
        cmds.setAttr(f"{locator}.visibility", 0)
    except Exception:
        pass
    return locator


def _bake_vertex_locator(trail_container, component, start, end, locator=None):
    flattened = cmds.ls(component, flatten=True, long=True) or []
    component = flattened[0] if flattened else component
    owner = component.split(".vtx[", 1)[0] if component else ""
    if not component or not owner or not cmds.objExists(owner):
        raise RuntimeError("selected vertex no longer exists")

    locator = _create_or_get_vertex_bake_locator(trail_container, locator)
    times = _vertex_bake_times(start, end)
    current_time = cmds.currentTime(query=True)
    selection = cmds.ls(selection=True, long=True, flatten=True) or []

    try:
        for attr in ("translateX", "translateY", "translateZ"):
            try:
                cmds.cutKey(locator, attribute=attr, clear=True)
            except Exception:
                pass
        for attr, value in (("rotateX", 0), ("rotateY", 0), ("rotateZ", 0), ("scaleX", 1), ("scaleY", 1), ("scaleZ", 1)):
            try:
                cmds.setAttr(f"{locator}.{attr}", value)
            except Exception:
                pass

        try:
            cmds.refresh(suspend=True)
        except Exception:
            pass

        for frame in times:
            cmds.currentTime(frame, edit=True, update=True)
            position = cmds.pointPosition(component, world=True)
            cmds.xform(locator, worldSpace=True, translation=position)
            cmds.setKeyframe(locator, attribute="translateX", time=frame, value=float(position[0]))
            cmds.setKeyframe(locator, attribute="translateY", time=frame, value=float(position[1]))
            cmds.setKeyframe(locator, attribute="translateZ", time=frame, value=float(position[2]))

        for attr in ("translateX", "translateY", "translateZ"):
            try:
                cmds.keyTangent(locator, attribute=attr, edit=True, inTangentType="linear", outTangentType="linear")
            except Exception:
                pass
    finally:
        try:
            cmds.refresh(suspend=False)
        except Exception:
            pass
        try:
            cmds.currentTime(current_time, edit=True, update=True)
        except Exception:
            pass
        try:
            if selection:
                cmds.select(selection, replace=True)
        except Exception:
            pass

    return locator


def _rebake_vertex_trail(trail_container):
    if not trail_container or not cmds.objExists(trail_container):
        return False
    source_component = _get_string_attr(trail_container, TRAIL_COMPONENT_ATTR, "")
    if not source_component:
        return False

    locator = _get_string_attr(trail_container, TRAIL_OBJECT_ATTR, "")
    start_frame, end_frame = _frame_range(trail_container)

    def _do_rebake():
        baked_locator = _bake_vertex_locator(
            trail_container,
            source_component,
            start_frame,
            end_frame,
            locator=locator if locator and cmds.objExists(locator) else None,
        )
        _set_string_attr(trail_container, TRAIL_OBJECT_ATTR, baked_locator)
        _connect_custom_trail_target(trail_container, baked_locator)
        _invalidate_trail_runtime_cache(trail_container)
        return baked_locator

    try:
        baked_locator = _run_without_undo(_do_rebake)
    except Exception as exc:
        cmds.warning(f"AnimKey: Could not update vertex motion trail: {exc}")
        return False

    if baked_locator and cmds.objExists(baked_locator):
        _dirty_custom_trail(trail_container)
        return True
    return False


def _schedule_vertex_trail_rebake(trail_container):
    if _TRAIL_INTERNAL_UPDATE:
        return False
    if not trail_container or not cmds.objExists(trail_container):
        return False
    if not _get_string_attr(trail_container, TRAIL_COMPONENT_ATTR, ""):
        return False
    if trail_container in _TRAIL_VERTEX_REBAKE_PENDING:
        return True

    _TRAIL_VERTEX_REBAKE_PENDING.add(trail_container)

    def _do_rebake(c=trail_container):
        try:
            if c and cmds.objExists(c):
                _rebake_vertex_trail(c)
        finally:
            _TRAIL_VERTEX_REBAKE_PENDING.discard(c)

    try:
        cmds.scriptJob(runOnce=True, idleEvent=_do_rebake, protected=True)
    except Exception:
        try:
            cmds.evalDeferred(_do_rebake, lowestPriority=True)
        except TypeError:
            cmds.evalDeferred(_do_rebake)
        except Exception:
            _TRAIL_VERTEX_REBAKE_PENDING.discard(trail_container)
            return False
    return True


def _connect_custom_trail_target(trail_container, target_object):
    shape = _trail_shape_in_container(trail_container)
    if not shape or not target_object or not cmds.objExists(shape) or not cmds.objExists(target_object):
        return False
    try:
        existing = cmds.listConnections(f"{shape}.targetMatrix", source=True, destination=False, plugs=True) or []
        for plug in existing:
            try:
                cmds.disconnectAttr(plug, f"{shape}.targetMatrix")
            except Exception:
                pass
        cmds.connectAttr(f"{target_object}.worldMatrix[0]", f"{shape}.targetMatrix", force=True)
        return True
    except Exception:
        return False


def _set_string_attr(node, attr, value):
    if not cmds.objExists(node):
        return
    try:
        if not cmds.attributeQuery(attr, node=node, exists=True):
            cmds.addAttr(node, longName=attr, dataType="string")
        cmds.setAttr(f"{node}.{attr}", value or "", type="string")
    except Exception:
        pass


def _get_string_attr(node, attr, default=""):
    try:
        if cmds.objExists(node) and cmds.attributeQuery(attr, node=node, exists=True):
            value = cmds.getAttr(f"{node}.{attr}")
            return value if value is not None else default
    except Exception:
        pass
    return default


def _set_string_list_attr(node, attr, values):
    values = [str(value) for value in values if value]
    _set_string_attr(node, attr, ";".join(values))


def _get_string_list_attr(node, attr):
    raw = _get_string_attr(node, attr, "")
    if not raw:
        return []
    return [part for part in raw.split(";") if part]


def _set_double_attr(node, attr, value):
    if not cmds.objExists(node):
        return
    try:
        if not cmds.attributeQuery(attr, node=node, exists=True):
            cmds.addAttr(node, longName=attr, attributeType="double")
            cmds.setAttr(f"{node}.{attr}", keyable=False, channelBox=False)
        cmds.setAttr(f"{node}.{attr}", float(value))
    except Exception:
        pass


def _get_double_attr(node, attr, default=0.0):
    try:
        if cmds.objExists(node) and cmds.attributeQuery(attr, node=node, exists=True):
            value = cmds.getAttr(f"{node}.{attr}")
            return float(value) if value is not None else float(default)
    except Exception:
        pass
    return float(default)


def _round_frame(value):
    value = float(value)
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _timeline_control():
    try:
        if cmds.about(batch=True):
            return None
    except Exception:
        pass
    for expression in ("$tmpVar=$gPlayBackSlider",):
        try:
            slider = mel.eval(expression)
        except Exception:
            continue
        if slider:
            return slider
    return None


def _selected_time_slider_range():
    slider = _timeline_control()
    if not slider:
        return None

    try:
        if not cmds.timeControl(slider, query=True, rangeVisible=True):
            return None
    except Exception:
        return None

    try:
        raw_range = cmds.timeControl(slider, query=True, rangeArray=True) or []
    except Exception:
        return None

    if len(raw_range) < 2:
        return None

    start = _round_frame(raw_range[0])
    end_edge = _round_frame(raw_range[1])
    if end_edge < start:
        start, end_edge = end_edge, start

    # Maya stores the right edge of the highlighted frame cell.
    end = max(start, end_edge - 1)
    return float(start), float(end)


def _store_trail_frame_range(container, start, end):
    if not container or not cmds.objExists(container):
        return
    if end < start:
        start, end = end, start
    _set_double_attr(container, TRAIL_RANGE_START_ATTR, float(start))
    _set_double_attr(container, TRAIL_RANGE_END_ATTR, float(end))


def _stored_trail_frame_range(container):
    if not container or not cmds.objExists(container):
        return None
    try:
        has_start = cmds.attributeQuery(TRAIL_RANGE_START_ATTR, node=container, exists=True)
        has_end = cmds.attributeQuery(TRAIL_RANGE_END_ATTR, node=container, exists=True)
    except Exception:
        return None
    if not has_start or not has_end:
        return None
    start = _get_double_attr(container, TRAIL_RANGE_START_ATTR, 0.0)
    end = _get_double_attr(container, TRAIL_RANGE_END_ATTR, start)
    if end < start:
        start, end = end, start
    return float(start), float(end)


def _lock_node_transforms(node):
    attrs = [
        "translateX", "translateY", "translateZ",
        "rotateX", "rotateY", "rotateZ",
        "scaleX", "scaleY", "scaleZ",
        "visibility",
    ]
    for attr in attrs:
        try:
            cmds.setAttr(f"{node}.{attr}", lock=True, keyable=False, channelBox=False)
        except Exception:
            pass


def _make_node_display_only(node):
    if not node or not cmds.objExists(node):
        return
    nodes = [node]
    try:
        nodes.extend(cmds.listRelatives(node, allDescendents=True, fullPath=False) or [])
    except Exception:
        pass
    for item in nodes:
        if not item or not cmds.objExists(item):
            continue
        try:
            if cmds.attributeQuery("overrideEnabled", node=item, exists=True):
                cmds.setAttr(f"{item}.overrideEnabled", 1)
            if cmds.attributeQuery("overrideDisplayType", node=item, exists=True):
                cmds.setAttr(f"{item}.overrideDisplayType", 2)
        except Exception:
            pass


def _style_shapes(node, color, line_width=1.0, alpha=None):
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    for shape in shapes:
        try:
            cmds.setAttr(f"{shape}.overrideEnabled", 1)
            if cmds.attributeQuery("overrideRGBColors", node=shape, exists=True):
                cmds.setAttr(f"{shape}.overrideRGBColors", 1)
            if cmds.attributeQuery("overrideColorRGB", node=shape, exists=True):
                cmds.setAttr(f"{shape}.overrideColorRGB", color[0], color[1], color[2], type="double3")
            if cmds.attributeQuery("lineWidth", node=shape, exists=True):
                cmds.setAttr(f"{shape}.lineWidth", line_width)
            if alpha is not None and cmds.attributeQuery("overrideAlpha", node=shape, exists=True):
                cmds.setAttr(f"{shape}.overrideAlpha", alpha)
        except Exception:
            pass


def _parent_to_container(node, container):
    if not node or not cmds.objExists(node) or not cmds.objExists(container):
        return
    try:
        cmds.parent(node, container)
    except Exception:
        pass
    try:
        cmds.container(container, edit=True, addNode=node, includeNetwork=True)
    except Exception:
        pass


def _cleanup_legacy_snapshot_nodes():
    """Remove orphan Maya snapshot nodes before creating or rebuilding a trail."""
    for node in ("trailShape", "trailHandleShape", "trailHandle", "trail"):
        if cmds.objExists(node):
            try:
                cmds.delete(node)
            except Exception:
                pass


def _node_attr_exists(node, attr):
    try:
        return cmds.objExists(node) and cmds.attributeQuery(attr, node=node, exists=True)
    except Exception:
        return False


def _run_without_undo(callback, *args, **kwargs):
    global _TRAIL_INTERNAL_UPDATE
    previous_internal = _TRAIL_INTERNAL_UPDATE
    _TRAIL_INTERNAL_UPDATE = True
    undo_state = True
    undo_changed = False
    try:
        try:
            undo_state = bool(cmds.undoInfo(query=True, state=True))
            cmds.undoInfo(stateWithoutFlush=False)
            undo_changed = True
        except Exception:
            pass
        return callback(*args, **kwargs)
    finally:
        if undo_changed:
            try:
                cmds.undoInfo(stateWithoutFlush=undo_state)
            except Exception:
                try:
                    cmds.undoInfo(state=undo_state)
                except Exception:
                    pass
        _TRAIL_INTERNAL_UPDATE = previous_internal


def _run_internal_update(callback, *args, **kwargs):
    global _TRAIL_INTERNAL_UPDATE
    previous_internal = _TRAIL_INTERNAL_UPDATE
    _TRAIL_INTERNAL_UPDATE = True
    try:
        return callback(*args, **kwargs)
    finally:
        _TRAIL_INTERNAL_UPDATE = previous_internal


def _set_attr_if_exists(node, attr, *values, **kwargs):
    if not _node_attr_exists(node, attr):
        return False
    try:
        attr_type = kwargs.get("attr_type")
        if attr_type:
            attr_types = [attr_type]
            if attr_type == "double3":
                attr_types.append("float3")
            elif attr_type == "float3":
                attr_types.append("double3")
            for candidate_type in attr_types:
                try:
                    cmds.setAttr(f"{node}.{attr}", *values, type=candidate_type)
                    return True
                except Exception:
                    pass
        try:
            cmds.setAttr(f"{node}.{attr}", *values)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _native_trail_shape(trail_container=None):
    existing_trail = trail_container
    if not existing_trail:
        existing_trail, _ = _find_existing_trail()
    if existing_trail and cmds.objExists(existing_trail):
        try:
            shapes = cmds.listRelatives(existing_trail, allDescendents=True, shapes=True, fullPath=False) or []
            for shape in shapes:
                if cmds.nodeType(shape) == "animKeyMotionTrail":
                    return shape
            if shapes:
                return shapes[0]
        except Exception:
            pass

    if cmds.objExists("trailShape"):
        return "trailShape"
    if cmds.objExists("trailHandleShape"):
        return "trailHandleShape"
    if cmds.objExists("trailHandle"):
        try:
            shapes = cmds.listRelatives("trailHandle", shapes=True, fullPath=False) or []
            if shapes:
                return shapes[0]
        except Exception:
            pass
    return None


def _apply_trail_appearance(rebuild_cache=True):
    """Apply persisted visual settings to the native Maya motion trail or our custom one."""
    settings = get_trail_settings()
    palette = _resolved_trail_palette(settings)

    trail_targets = _iter_trail_containers()
    if not trail_targets:
        existing_trail, object_name = _find_existing_trail()
        trail_targets = [(existing_trail, object_name)]

    refresh_keys = (not rebuild_cache and settings.get("show_key_handles", True))
    refresh_tangents = (not rebuild_cache and settings.get("show_tangent_handles", False))
    if refresh_keys:
        _clear_key_callbacks()
    if refresh_tangents:
        _clear_tangent_callbacks()

    if cmds.objExists("trail"):
        increment = max(1, int(settings.get("trail_increment", 1)))
        _set_attr_if_exists("trail", "increment", increment)

    for existing_trail, _object_name in trail_targets:
        source_component = _get_string_attr(existing_trail, TRAIL_COMPONENT_ATTR, "") if existing_trail else ""
        trail_shape = _native_trail_shape(existing_trail)
        if not trail_shape:
            continue

        # Check if this is our custom node
        if cmds.nodeType(trail_shape) == "animKeyMotionTrail":
            start, end = _frame_range(existing_trail)
            effective_increment = _effective_trail_increment(start, end, settings)
            _set_attr_if_exists(trail_shape, "startTime", int(start))
            _set_attr_if_exists(trail_shape, "endTime", int(end))
            _set_attr_if_exists(trail_shape, "increment", effective_increment)
            _set_attr_if_exists(trail_shape, "sampleDensity", max(1, int(settings.get("sample_density", 1))))
            _set_attr_if_exists(trail_shape, "trailColor", *palette["main"], attr_type="double3")
            _set_attr_if_exists(trail_shape, "pastColor", *palette.get("past", palette["extra"]), attr_type="double3")
            _set_attr_if_exists(trail_shape, "futureColor", *palette.get("future", palette["main"]), attr_type="double3")
            _set_attr_if_exists(trail_shape, "currentFrameColor", *palette["current"], attr_type="double3")
            _set_attr_if_exists(trail_shape, "keyframeColor", *palette["key"], attr_type="double3")
            _set_attr_if_exists(trail_shape, "previousKeyColor", *palette["past_key"], attr_type="double3")
            _set_attr_if_exists(trail_shape, "nextKeyColor", *palette["future_key"], attr_type="double3")
            _set_attr_if_exists(trail_shape, "popColor", *palette["pop"], attr_type="double3")
            _set_attr_if_exists(trail_shape, "showPopWarnings", bool(settings.get("show_pop_warnings", True)))
            _set_attr_if_exists(trail_shape, "popThreshold", float(settings.get("pop_threshold", 0.4)))
            line_width = max(1.0, float(settings.get("line_width", 3.0)))
            _set_attr_if_exists(trail_shape, "trailLineWidth", line_width)
            if existing_trail and rebuild_cache:
                _dirty_custom_trail(existing_trail)
            elif existing_trail:
                _redraw_custom_trail(existing_trail)
            else:
                try:
                    cmds.dgdirty(trail_shape)
                except Exception:
                    pass
                try:
                    if cmds.attributeQuery("outData", node=trail_shape, exists=True):
                        cmds.dgdirty(f"{trail_shape}.outData")
                except Exception:
                    pass
            if (
                existing_trail and
                refresh_keys and
                not source_component and
                _object_name and
                cmds.objExists(_object_name)
            ):
                _create_editable_key_handles(existing_trail, _object_name)
                _register_key_handle_callbacks(existing_trail)
            if (
                existing_trail and
                refresh_tangents and
                not source_component and
                _object_name and
                cmds.objExists(_object_name)
            ):
                _create_editable_tangent_handles(existing_trail, _object_name)
                _register_tangent_handle_callbacks(existing_trail)
        else:
            _set_attr_if_exists(
                trail_shape,
                "trailDrawMode",
                1 if settings.get("show_key_markers", True) else 0
            )
            _set_attr_if_exists(trail_shape, "trailColor", *palette["main"], attr_type="double3")
            _set_attr_if_exists(trail_shape, "extraTrailColor", *palette.get("extra", palette["main"]), attr_type="double3")
            _set_attr_if_exists(trail_shape, "keyframeColor", *palette["key"], attr_type="double3")

            line_width = max(1.0, float(settings.get("line_width", 3.0)))
            for attr in ("lineWidth", "trailLineWidth", "width"):
                if _set_attr_if_exists(trail_shape, attr, line_width):
                    break


def _frame_range(trail_container=None, use_time_slider_selection=False):
    stored_range = _stored_trail_frame_range(trail_container)
    if stored_range:
        return stored_range

    if use_time_slider_selection:
        selected_range = _selected_time_slider_range()
        if selected_range:
            return selected_range

    start = cmds.playbackOptions(query=True, minTime=True)
    end = cmds.playbackOptions(query=True, maxTime=True)
    if end < start:
        start, end = end, start
    return float(start), float(end)


def _sample_times(start, end, max_samples=None):
    start_i = int(math.floor(start))
    end_i = int(math.ceil(end))
    frame_count = max(1, end_i - start_i + 1)
    max_samples = int(max_samples or TRAIL_MAX_SAMPLES)
    step = max(1, int(math.ceil(frame_count / float(max(1, max_samples)))))
    times = [float(frame) for frame in range(start_i, end_i + 1, step)]
    if not times or times[-1] != float(end_i):
        times.append(float(end_i))
    return times


def _effective_trail_increment(start, end, settings=None):
    settings = settings or get_trail_settings()
    base_increment = max(1, int(settings.get("trail_increment", 1)))
    sample_density = max(1, int(settings.get("sample_density", 1)))
    max_samples = max(24, int(settings.get("max_samples", TRAIL_MAX_SAMPLES)))
    frame_count = max(1, int(math.ceil(float(end) - float(start))) + 1)
    capped_increment = int(math.ceil((frame_count * float(sample_density)) / float(max_samples)))
    return max(base_increment, capped_increment)


def _key_times_for_object(object_name, start, end, max_key_markers=None):
    try:
        raw = cmds.keyframe(object_name, query=True, time=(start, end), timeChange=True) or []
    except Exception:
        raw = []
    times = sorted(set(float(t) for t in raw))
    max_key_markers = int(TRAIL_MAX_KEY_MARKERS if max_key_markers is None else max_key_markers)
    if max_key_markers <= 0:
        return []
    if len(times) > max_key_markers:
        stride = int(math.ceil(len(times) / float(max_key_markers)))
        times = times[::stride]
    return times


def _current_time_value():
    try:
        return float(cmds.currentTime(query=True))
    except Exception:
        return 0.0


def _is_anim_playing():
    try:
        return bool(oma.MAnimControl.isPlaying())
    except Exception:
        pass
    try:
        return bool(cmds.play(query=True, state=True))
    except Exception:
        return False


def _invalidate_trail_runtime_cache(container=None):
    if container:
        _TRAIL_KEY_TIMES_CACHE.pop(container, None)
        _TRAIL_WATCHED_CURVES_CACHE.pop(container, None)
        return
    _TRAIL_KEY_TIMES_CACHE.clear()
    _TRAIL_WATCHED_CURVES_CACHE.clear()


def _cached_key_times_for_container(container, object_name, start, end):
    signature = _get_string_attr(container, TRAIL_KEY_SIGNATURE_ATTR, "")
    group = _get_string_attr(container, TRAIL_KEY_GROUP_ATTR, "")
    cache_key = (
        object_name or "",
        _frame_cache_key(start),
        _frame_cache_key(end),
        signature,
        group,
    )
    cached = _TRAIL_KEY_TIMES_CACHE.get(container)
    if cached and cached.get("key") == cache_key:
        return list(cached.get("times") or [])

    key_times = []
    try:
        key_times = [
            _get_double_attr(handle, "animKeyTrailKeyFrame")
            for handle in _key_handles_in_container(container)
        ]
    except Exception:
        key_times = []
    if not key_times and object_name:
        key_times = _key_times_for_object(object_name, start, end, max_key_markers=100000)

    filtered_times = []
    for frame in key_times:
        try:
            frame_value = float(frame)
        except Exception:
            continue
        if start - 0.001 <= frame_value <= end + 0.001:
            filtered_times.append(frame_value)
    key_times = sorted(set(filtered_times))
    _TRAIL_KEY_TIMES_CACHE[container] = {"key": cache_key, "times": key_times}
    return list(key_times)


def _dirty_range_for_times(container, object_name, times):
    if not container or not cmds.objExists(container):
        return None
    start, end = _frame_range(container)
    if end < start:
        start, end = end, start

    valid_times = []
    for time_value in times or []:
        try:
            frame = float(time_value)
        except Exception:
            continue
        if start - 0.001 <= frame <= end + 0.001:
            valid_times.append(frame)

    if not valid_times:
        current = _current_time_value()
        if start - 0.001 <= current <= end + 0.001:
            valid_times = [current]
    if not valid_times:
        return None

    key_times = _cached_key_times_for_container(container, object_name, start, end)

    lower = end
    upper = start
    for frame in valid_times:
        before_index = bisect.bisect_left(key_times, frame - 0.001) - 1
        after_index = bisect.bisect_right(key_times, frame + 0.001)
        frame_lower = key_times[before_index] if before_index >= 0 else start
        frame_upper = key_times[after_index] if after_index < len(key_times) else end
        lower = min(lower, frame_lower, frame)
        upper = max(upper, frame_upper, frame)

    settings = get_trail_settings()
    increment = float(_effective_trail_increment(start, end, settings))
    sample_density = max(1.0, float(settings.get("sample_density", 1)))
    padding = max(1.0, increment / sample_density)
    return max(start, lower - padding), min(end, upper + padding)


def _curve_names_from_callback_args(args):
    curves = []
    seen = set()
    for arg in args or []:
        if isinstance(arg, str):
            continue
        if not hasattr(arg, "length"):
            continue
        try:
            count = arg.length()
        except Exception:
            continue
        for index in range(count):
            try:
                obj = arg[index]
                if obj.isNull():
                    continue
                name = om.MFnDependencyNode(obj).name()
            except Exception:
                continue
            if name and name not in seen:
                seen.add(name)
                curves.append(name)
    return curves


def _cached_trail_anim_curves_for_container(container, object_name):
    if not container or not object_name or not cmds.objExists(object_name):
        return []
    cache_key = object_name
    cached = _TRAIL_WATCHED_CURVES_CACHE.get(container)
    if cached and cached.get("key") == cache_key:
        return list(cached.get("curves") or [])
    curves = _trail_anim_curves_for_object(object_name)
    _TRAIL_WATCHED_CURVES_CACHE[container] = {"key": cache_key, "curves": list(curves)}
    return list(curves)


def _dirty_range_for_anim_curves(container, object_name, curve_names):
    if not container or not cmds.objExists(container):
        return None

    watched = set(_cached_trail_anim_curves_for_container(container, object_name))
    if curve_names:
        relevant = [curve for curve in curve_names if curve in watched]
        if not relevant:
            short_watched = {curve.split("|")[-1]: curve for curve in watched}
            relevant = [short_watched[curve.split("|")[-1]] for curve in curve_names if curve.split("|")[-1] in short_watched]
        if not relevant:
            return None
    else:
        relevant = list(watched)

    times = []
    for curve in relevant:
        try:
            selected = cmds.keyframe(curve, query=True, selected=True, timeChange=True) or []
            times.extend(float(value) for value in selected)
        except Exception:
            pass

    if not times:
        times.append(_current_time_value())
    return _dirty_range_for_times(container, object_name, times)


def _world_pivot(object_name):
    try:
        matrix = cmds.getAttr(f"{object_name}.worldMatrix[0]")
        if matrix and len(matrix) >= 16:
            return (float(matrix[12]), float(matrix[13]), float(matrix[14]))
    except Exception:
        pass
    return tuple(cmds.xform(object_name, query=True, worldSpace=True, translation=True))


def _matrix_position_from_plug_at_time(matrix_plug, frame):
    try:
        selection = om.MSelectionList()
        selection.add(matrix_plug)
        plug = selection.getPlug(0)
        context = om.MDGContext(om.MTime(float(frame), om.MTime.uiUnit()))
        mat_obj = plug.asMObject(context)
        if mat_obj.isNull():
            return None
        matrix = om.MFnMatrixData(mat_obj).matrix()
        return (float(matrix[12]), float(matrix[13]), float(matrix[14]))
    except Exception:
        return None


def _trail_target_matrix_plug(trail_container, object_name):
    try:
        shape = _trail_shape_in_container(trail_container)
        if shape and cmds.objExists(shape) and cmds.nodeType(shape) == "animKeyMotionTrail":
            if cmds.attributeQuery("targetMatrix", node=shape, exists=True):
                return f"{shape}.targetMatrix"
    except Exception:
        pass
    try:
        object_long = (cmds.ls(object_name, long=True) or [object_name])[0]
        for shape in cmds.ls(type="animKeyMotionTrail") or []:
            source_plugs = cmds.listConnections(
                f"{shape}.targetMatrix",
                source=True,
                destination=False,
                plugs=True,
            ) or []
            for source_plug in source_plugs:
                source_node = source_plug.split(".", 1)[0]
                source_long = (cmds.ls(source_node, long=True) or [source_node])[0]
                if source_long == object_long:
                    return f"{shape}.targetMatrix"
    except Exception:
        pass
    if object_name and cmds.objExists(object_name):
        return f"{object_name}.worldMatrix[0]"
    return ""


def _sample_world_positions(object_name, times, matrix_plug=None):
    matrix_plug = matrix_plug or _trail_target_matrix_plug("", object_name)
    positions = {}
    missing_times = []
    if matrix_plug:
        for t in times:
            pos = _matrix_position_from_plug_at_time(matrix_plug, t)
            if pos is None:
                missing_times.append(t)
            else:
                positions[float(t)] = pos
    else:
        missing_times = list(times)
    if not missing_times:
        return positions

    current_time = cmds.currentTime(query=True)
    selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.refresh(suspend=True)
    except Exception:
        pass
    try:
        for t in missing_times:
            try:
                cmds.currentTime(t, edit=True, update=True)
                positions[float(t)] = _world_pivot(object_name)
            except Exception:
                pass
    finally:
        try:
            cmds.currentTime(current_time, edit=True, update=True)
        except Exception:
            pass
        try:
            cmds.refresh(suspend=False)
        except Exception:
            pass
        try:
            if selection:
                cmds.select(selection, replace=True)
        except Exception:
            pass
    return positions


def _scene_radius(points):
    if not points:
        return 0.25
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    diag = math.sqrt((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2)
    return max(0.08, min(1.2, diag * 0.018))


def _ensure_curve_points(points):
    if len(points) >= 2:
        return points
    if points:
        x, y, z = points[0]
    else:
        x, y, z = 0.0, 0.0, 0.0
    return [(x, y, z), (x + 0.001, y, z)]


def _create_curve_node(name, points, color, line_width):
    points = _ensure_curve_points(points)
    degree = 3 if len(points) > 3 else 1
    curve = cmds.curve(name=name, degree=degree, point=points)
    _style_shapes(curve, color, line_width=line_width)
    return curve


def _create_locator_marker(name, position, radius, color, line_width=2.0):
    marker = cmds.spaceLocator(name=name, position=position)[0]
    try:
        cmds.setAttr(f"{marker}.localScaleX", radius)
        cmds.setAttr(f"{marker}.localScaleY", radius)
        cmds.setAttr(f"{marker}.localScaleZ", radius)
    except Exception:
        pass
    _style_shapes(marker, color, line_width=line_width)
    return marker


def _add_node_to_container(node, container):
    if not node or not cmds.objExists(node) or not cmds.objExists(container):
        return
    try:
        cmds.container(container, edit=True, addNode=node, includeNetwork=True)
    except Exception:
        pass


def _hide_renderable_shape_attrs(node):
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    for shape in shapes:
        for attr in (
            "castsShadows", "receiveShadows", "primaryVisibility",
            "visibleInReflections", "visibleInRefractions",
        ):
            try:
                if cmds.attributeQuery(attr, node=shape, exists=True):
                    cmds.setAttr(f"{shape}.{attr}", 0)
            except Exception:
                pass


def _custom_tangent_shape(handle):
    try:
        shapes = cmds.listRelatives(handle, shapes=True, fullPath=False) or []
    except Exception:
        shapes = []
    for shape in shapes:
        try:
            if cmds.nodeType(shape) == "animKeyTangentHandle":
                return shape
        except Exception:
            pass
    return None


def _create_tangent_handle_node(
    name,
    position,
    radius,
    color,
    key_position=None,
    line_color=None,
    draw_line=True,
    screen_radius=None,
):
    handle = None
    key_position = key_position or position
    line_color = line_color or color

    if TRAIL_USE_CUSTOM_TANGENT_NODES and _ensure_plugin_loaded():
        try:
            shape = cmds.createNode("animKeyTangentHandle", name=f"{name}Shape")
            parents = cmds.listRelatives(shape, parent=True, fullPath=False) or []
            if parents:
                handle = cmds.rename(parents[0], name)
                shape = _custom_tangent_shape(handle) or shape
                _set_attr_if_exists(shape, "keyPosition", key_position[0], key_position[1], key_position[2], attr_type="double3")
                _set_attr_if_exists(shape, "handleColor", color[0], color[1], color[2], attr_type="double3")
                _set_attr_if_exists(shape, "lineColor", line_color[0], line_color[1], line_color[2], attr_type="double3")
                display_radius = screen_radius if screen_radius is not None else max(7.0, min(18.0, radius * 38.0))
                _set_attr_if_exists(shape, "screenRadius", max(4.5, min(18.0, float(display_radius))))
                _set_attr_if_exists(shape, "worldRadius", max(0.05, radius))
                if not _set_attr_if_exists(shape, "drawLine", bool(draw_line)) and not draw_line:
                    try:
                        cmds.delete(handle)
                    except Exception:
                        pass
                    handle = None
        except Exception:
            handle = None

    if not handle or not cmds.objExists(handle):
        try:
            handle = cmds.circle(
                name=name,
                normal=(0, 0, 1),
                radius=radius,
                constructionHistory=False,
            )[0]
        except Exception:
            handle = cmds.spaceLocator(name=name, position=position)[0]
            try:
                cmds.setAttr(f"{handle}.localScaleX", radius)
                cmds.setAttr(f"{handle}.localScaleY", radius)
                cmds.setAttr(f"{handle}.localScaleZ", radius)
            except Exception:
                pass
        _style_shapes(handle, color, line_width=2.0)
        _hide_renderable_shape_attrs(handle)

    try:
        cmds.xform(handle, worldSpace=True, translation=position)
    except Exception:
        pass

    for attr in ("rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ", "visibility"):
        try:
            cmds.setAttr(f"{handle}.{attr}", lock=True, keyable=False, channelBox=False)
        except Exception:
            pass
    for attr in ("translateX", "translateY", "translateZ"):
        try:
            cmds.setAttr(f"{handle}.{attr}", lock=False, keyable=True, channelBox=True)
        except Exception:
            pass

    return handle


def _vector_sub(point_a, point_b):
    return (
        float(point_a[0]) - float(point_b[0]),
        float(point_a[1]) - float(point_b[1]),
        float(point_a[2]) - float(point_b[2]),
    )


def _vector_add(point, vector):
    return (
        float(point[0]) + float(vector[0]),
        float(point[1]) + float(vector[1]),
        float(point[2]) + float(vector[2]),
    )


def _vector_scale(vector, scale):
    return (
        float(vector[0]) * float(scale),
        float(vector[1]) * float(scale),
        float(vector[2]) * float(scale),
    )


def _vector_length(vector):
    return math.sqrt(
        float(vector[0]) * float(vector[0]) +
        float(vector[1]) * float(vector[1]) +
        float(vector[2]) * float(vector[2])
    )


def _fit_tangent_handle_display(vector, time_delta, fallback_vector, handle_radius):
    raw_length = _vector_length(vector)
    if raw_length <= 0.000001:
        return vector, time_delta, 1.0

    nearby_length = _vector_length(fallback_vector)
    min_visual_length = handle_radius * 2.6
    max_visual_length = handle_radius * 4.0
    if nearby_length > 0.000001:
        max_visual_length = min(max_visual_length, max(min_visual_length, nearby_length))

    if raw_length <= max_visual_length:
        return vector, time_delta, 1.0

    display_scale = max(0.02, min(1.0, max_visual_length / raw_length))
    return _vector_scale(vector, display_scale), float(time_delta) * display_scale, display_scale


def _world_vector_to_parent_space(object_name, vector):
    try:
        parents = cmds.listRelatives(object_name, parent=True, fullPath=True) or []
        if not parents:
            return tuple(vector)
        matrix_values = cmds.getAttr(f"{parents[0]}.worldInverseMatrix[0]")
        local = om.MVector(*vector) * om.MMatrix(matrix_values)
        return (local.x, local.y, local.z)
    except Exception:
        return tuple(vector)


def _local_vector_to_world_space(object_name, vector):
    try:
        parents = cmds.listRelatives(object_name, parent=True, fullPath=True) or []
        if not parents:
            return tuple(vector)
        matrix_values = cmds.getAttr(f"{parents[0]}.worldMatrix[0]")
        world = om.MVector(*vector) * om.MMatrix(matrix_values)
        return (world.x, world.y, world.z)
    except Exception:
        return tuple(vector)


def _upstream_anim_curves_from_plug(plug):
    curves = []
    visited = set()

    def _walk(item, depth=0):
        if not item or depth > 12:
            return
        node = item.split(".", 1)[0]
        if not node or node in visited or not cmds.objExists(node):
            return
        visited.add(node)
        try:
            node_type = cmds.nodeType(node)
        except Exception:
            return
        if node_type.startswith("animCurve"):
            if node not in curves:
                curves.append(node)
            return
        try:
            upstream = cmds.listConnections(
                item,
                source=True,
                destination=False,
                plugs=True,
                skipConversionNodes=True,
            ) or []
        except Exception:
            upstream = []
        if not upstream and "." not in item:
            try:
                upstream = cmds.listConnections(
                    node,
                    source=True,
                    destination=False,
                    plugs=True,
                    skipConversionNodes=True,
                ) or []
            except Exception:
                upstream = []
        for upstream_item in upstream:
            _walk(upstream_item, depth + 1)

    _walk(plug)
    return curves


def _curve_has_key_at_frame(curve, frame):
    try:
        keys = cmds.keyframe(curve, query=True, time=(float(frame) - 0.001, float(frame) + 0.001), timeChange=True) or []
    except Exception:
        return False
    return any(abs(float(key) - float(frame)) <= 0.001 for key in keys)


def _frame_cache_key(frame):
    return round(float(frame), 3)


def _set_anim_curve_tangent(curve, frame, side, tangent_x, tangent_y):
    tangent_x_ui = max(0.001, abs(float(tangent_x)))
    tangent_y = float(tangent_y)
    try:
        selection = om.MSelectionList()
        selection.add(curve)
        anim_fn = oma.MFnAnimCurve(selection.getDependNode(0))
        key_index = anim_fn.find(om.MTime(float(frame), om.MTime.uiUnit()))
        if key_index is None:
            return False
        tangents_locked = bool(anim_fn.tangentsLocked(key_index))
        anim_fn.setIsWeighted(True)
        is_in_tangent = side == "in"
        if is_in_tangent:
            anim_fn.setInTangentType(key_index, oma.MFnAnimCurve.kTangentFixed)
        else:
            anim_fn.setOutTangentType(key_index, oma.MFnAnimCurve.kTangentFixed)
        tangent_x_seconds = max(
            0.000001,
            abs(float(om.MTime(tangent_x_ui, om.MTime.uiUnit()).asUnits(om.MTime.kSeconds)))
        )
        if hasattr(anim_fn, "setTangentXY"):
            anim_fn.setTangentXY(key_index, tangent_x_seconds, tangent_y, is_in_tangent)
        else:
            anim_fn.setTangent(key_index, tangent_x_seconds, tangent_y, is_in_tangent)
        return tangents_locked
    except Exception:
        pass

    angle = math.degrees(math.atan2(tangent_y, tangent_x_ui))
    weight = max(0.1, min(20.0, math.sqrt(tangent_x_ui * tangent_x_ui + tangent_y * tangent_y) / tangent_x_ui))
    try:
        if side == "in":
            cmds.keyTangent(
                curve,
                edit=True,
                weightedTangents=True,
                time=(frame, frame),
                inTangentType="fixed",
                inAngle=angle,
                inWeight=weight,
            )
        else:
            cmds.keyTangent(
                curve,
                edit=True,
                weightedTangents=True,
                time=(frame, frame),
                outTangentType="fixed",
                outAngle=angle,
                outWeight=weight,
            )
        return False
    except Exception:
        return False


def _get_anim_curve_tangent_xy(curve, frame, side):
    try:
        selection = om.MSelectionList()
        selection.add(curve)
        anim_fn = oma.MFnAnimCurve(selection.getDependNode(0))
        key_index = anim_fn.find(om.MTime(float(frame), om.MTime.uiUnit()))
        if key_index is None:
            return None
        tangent_x, tangent_y = anim_fn.getTangentXY(key_index, side == "in")
        tangent_x = abs(float(tangent_x))
        tangent_y = float(tangent_y)
        if not math.isfinite(tangent_x) or not math.isfinite(tangent_y):
            return None
        tangent_x_ui = om.MTime(tangent_x, om.MTime.kSeconds).asUnits(om.MTime.uiUnit())
        tangent_x_ui = max(0.001, abs(float(tangent_x_ui)))
        return tangent_x_ui, tangent_y
    except Exception:
        pass

    try:
        angle_flag = "inAngle" if side == "in" else "outAngle"
        weight_flag = "inWeight" if side == "in" else "outWeight"
        angles = cmds.keyTangent(curve, query=True, time=(frame, frame), **{angle_flag: True}) or []
        weights = cmds.keyTangent(curve, query=True, time=(frame, frame), **{weight_flag: True}) or []
        angle = math.radians(float(angles[0]))
        tangent_x = max(0.001, abs(float(weights[0] if weights else 1.0)))
        tangent_y = math.tan(angle) * tangent_x
        return tangent_x, tangent_y
    except Exception:
        return None


def _initial_tangent_vector_from_curves(object_name, curve_map, key_time, side, fallback_world_vector, fallback_time_delta):
    local_components = [0.0, 0.0, 0.0]
    tangent_x_values = []
    found_tangent = False

    for attr, axis_index in (("translateX", 0), ("translateY", 1), ("translateZ", 2)):
        for curve in curve_map.get(attr, []):
            tangent = _get_anim_curve_tangent_xy(curve, key_time, side)
            if tangent is None:
                continue
            tangent_x, tangent_y = tangent
            local_components[axis_index] = -tangent_y if side == "in" else tangent_y
            tangent_x_values.append(tangent_x)
            found_tangent = True
            break

    if found_tangent:
        world_vector = _local_vector_to_world_space(object_name, local_components)
        if _vector_length(world_vector) > 0.000001:
            tangent_x = sum(tangent_x_values) / float(len(tangent_x_values)) if tangent_x_values else fallback_time_delta
            return world_vector, max(0.001, abs(float(tangent_x)))

    return fallback_world_vector, max(0.001, abs(float(fallback_time_delta)))


def _translate_anim_curves_for_attr(object_name, attr):
    if not cmds.objExists(object_name):
        return []
    return _upstream_anim_curves_from_plug(f"{object_name}.{attr}")


def _translate_key_times_for_object(object_name, start, end, max_keys=None):
    key_times = set()
    for attr in ("translateX", "translateY", "translateZ"):
        for curve in _translate_anim_curves_for_attr(object_name, attr):
            try:
                keys = cmds.keyframe(curve, query=True, time=(start, end), timeChange=True) or []
            except Exception:
                keys = []
            key_times.update(float(key) for key in keys)
    times = sorted(key_times)
    if max_keys and len(times) > max_keys:
        stride = int(math.ceil(len(times) / float(max(1, max_keys))))
        times = times[::stride]
    return times


def _key_times_signature(key_times):
    parts = []
    for key_time in sorted(key_times or []):
        try:
            parts.append(f"{float(_frame_cache_key(key_time)):.3f}")
        except Exception:
            continue
    return ";".join(parts)


def _editable_key_times_for_container(trail_container, object_name=None):
    if not trail_container or not cmds.objExists(trail_container):
        return []
    object_name = object_name or _get_string_attr(trail_container, TRAIL_OBJECT_ATTR)
    if not object_name or not cmds.objExists(object_name):
        return []
    settings = get_trail_settings()
    start, end = _frame_range(trail_container)
    max_keys = min(160, max(1, int(settings.get("max_key_markers", TRAIL_MAX_KEY_MARKERS))))
    return _translate_key_times_for_object(object_name, start, end, max_keys=max_keys)


def _key_handle_signature_from_handles(trail_container):
    frames = []
    for handle in _key_handles_in_container(trail_container):
        frames.append(_get_double_attr(handle, "animKeyTrailKeyFrame"))
    return _key_times_signature(frames)


def _register_all_key_handle_callbacks():
    _clear_key_callbacks()
    if not get_trail_settings().get("show_key_handles", True):
        return
    for trail_container, _ in _iter_trail_containers():
        if trail_container and cmds.objExists(trail_container):
            _register_key_handle_callbacks(trail_container)


def _sync_editable_key_handles_if_needed(trail_container, object_name=None, force=False):
    if not get_trail_settings().get("show_key_handles", True):
        return False
    if not trail_container or not cmds.objExists(trail_container):
        return False
    if _get_string_attr(trail_container, TRAIL_COMPONENT_ATTR, ""):
        return False
    object_name = object_name or _get_string_attr(trail_container, TRAIL_OBJECT_ATTR)
    if not object_name or not cmds.objExists(object_name):
        return False

    key_times = _editable_key_times_for_container(trail_container, object_name)
    current_signature = _key_times_signature(key_times)
    stored_signature = _get_string_attr(trail_container, TRAIL_KEY_SIGNATURE_ATTR, "")
    if not stored_signature:
        stored_signature = _key_handle_signature_from_handles(trail_container)
        if stored_signature == current_signature:
            _set_string_attr(trail_container, TRAIL_KEY_SIGNATURE_ATTR, current_signature)
            return False

    if not force and stored_signature == current_signature:
        return False

    selection = cmds.ls(selection=True, long=True) or []

    def _rebuild_handles():
        _clear_key_callbacks()
        _create_editable_key_handles(trail_container, object_name)
        _register_all_key_handle_callbacks()

    _run_without_undo(_rebuild_handles)

    try:
        valid_selection = [node for node in selection if cmds.objExists(node)]
        if valid_selection:
            cmds.select(valid_selection, replace=True)
    except Exception:
        pass
    return True


def _line_curve_between(name, start_pos, end_pos, color):
    line = _create_curve_node(name, [start_pos, end_pos], color, line_width=2.5)
    _make_node_display_only(line)
    return line


def _update_tangent_line(handle):
    custom_shape = _custom_tangent_shape(handle)
    if custom_shape:
        key_pos = (
            _get_double_attr(handle, "animKeyTangentKeyX"),
            _get_double_attr(handle, "animKeyTangentKeyY"),
            _get_double_attr(handle, "animKeyTangentKeyZ"),
        )
        _set_attr_if_exists(custom_shape, "keyPosition", key_pos[0], key_pos[1], key_pos[2], attr_type="double3")
        try:
            cmds.dgdirty(custom_shape)
        except Exception:
            pass
        return

    line = _get_string_attr(handle, "animKeyTangentLine")
    if not line or not cmds.objExists(line):
        return
    key_pos = (
        _get_double_attr(handle, "animKeyTangentKeyX"),
        _get_double_attr(handle, "animKeyTangentKeyY"),
        _get_double_attr(handle, "animKeyTangentKeyZ"),
    )
    try:
        handle_pos = cmds.xform(handle, query=True, worldSpace=True, translation=True)
        cmds.xform(f"{line}.cv[0]", worldSpace=True, translation=key_pos)
        cmds.xform(f"{line}.cv[1]", worldSpace=True, translation=handle_pos)
    except Exception:
        pass


def _sync_paired_tangent_handle(handle, key_pos, world_vector):
    pair = _get_string_attr(handle, "animKeyTangentPair")
    if not pair or not cmds.objExists(pair):
        return
    pair_pos = _vector_add(key_pos, _vector_scale(world_vector, -1.0))
    try:
        cmds.xform(pair, worldSpace=True, translation=pair_pos)
    except Exception:
        return
    _set_double_attr(pair, "animKeyTangentTimeDelta", abs(_get_double_attr(handle, "animKeyTangentTimeDelta", 1.0)))
    _set_double_attr(pair, "animKeyTangentDisplayScale", _get_double_attr(handle, "animKeyTangentDisplayScale", 1.0))
    _update_tangent_line(pair)


def _apply_tangent_handle_to_curves(handle):
    global _TRAIL_TANGENT_UPDATE_LOCK
    if _TRAIL_TANGENT_UPDATE_LOCK or not handle or not cmds.objExists(handle):
        return

    object_name = _get_string_attr(handle, "animKeyTangentObject")
    side = _get_string_attr(handle, "animKeyTangentSide")
    container = _get_string_attr(handle, "animKeyTangentContainer")
    frame = _get_double_attr(handle, "animKeyTangentFrame")
    time_delta = _get_double_attr(handle, "animKeyTangentTimeDelta")
    if not object_name or not cmds.objExists(object_name) or side not in ("in", "out") or abs(time_delta) < 0.001:
        return

    key_pos = (
        _get_double_attr(handle, "animKeyTangentKeyX"),
        _get_double_attr(handle, "animKeyTangentKeyY"),
        _get_double_attr(handle, "animKeyTangentKeyZ"),
    )
    try:
        handle_pos = cmds.xform(handle, query=True, worldSpace=True, translation=True)
    except Exception:
        return

    world_vector = _vector_sub(handle_pos, key_pos)
    display_scale = max(0.001, _get_double_attr(handle, "animKeyTangentDisplayScale", 1.0))
    curve_scale = 1.0 / display_scale
    local_vector = _vector_scale(_world_vector_to_parent_space(object_name, world_vector), curve_scale)
    tangent_x = max(0.001, abs(time_delta) * curve_scale)

    _TRAIL_TANGENT_UPDATE_LOCK = True
    try:
        def _edit_tangents():
            sync_pair_state = False
            for attr, delta in (
                ("translateX", local_vector[0]),
                ("translateY", local_vector[1]),
                ("translateZ", local_vector[2]),
            ):
                curves = _get_string_list_attr(handle, f"animKeyTangentCurves{attr[-1]}")
                if not curves:
                    continue
                for curve in curves:
                    if not cmds.objExists(curve):
                        continue
                    tangent_y = -delta if side == "in" else delta
                    if _set_anim_curve_tangent(curve, frame, side, tangent_x, tangent_y):
                        sync_pair_state = True
            return sync_pair_state

        sync_pair = bool(_run_internal_update(_edit_tangents))
        _run_without_undo(_update_tangent_line, handle)
        if sync_pair:
            _run_without_undo(_sync_paired_tangent_handle, handle, key_pos, world_vector)
        if container and cmds.objExists(container):
            _request_tangent_trail_redraw(container, frame)
    finally:
        _TRAIL_TANGENT_UPDATE_LOCK = False


def _request_tangent_trail_redraw(container, frame=None):
    if not container or not cmds.objExists(container):
        return
    now = time.time()
    last = _TRAIL_LAST_TANGENT_REDRAW.get(container, 0.0)
    if now - last < _TRAIL_TANGENT_REDRAW_INTERVAL:
        return
    _TRAIL_LAST_TANGENT_REDRAW[container] = now
    object_name = _get_string_attr(container, TRAIL_OBJECT_ATTR)
    dirty_time = frame if frame is not None else _current_time_value()
    dirty_range = _dirty_range_for_times(container, object_name, [dirty_time])
    _schedule_trail_cache_dirty(container, dirty_range=dirty_range)


def _trail_anim_curves_for_object(object_name):
    if not object_name or not cmds.objExists(object_name):
        return []

    attrs = cmds.listAttr(object_name, keyable=True) or []
    attrs.extend([
        "translateX", "translateY", "translateZ",
        "rotateX", "rotateY", "rotateZ",
        "scaleX", "scaleY", "scaleZ",
    ])

    curves = []
    seen = set()
    for attr in attrs:
        plug = f"{object_name}.{attr}"
        if not cmds.objExists(plug):
            continue
        for curve in _upstream_anim_curves_from_plug(plug):
            if curve not in seen:
                seen.add(curve)
                curves.append(curve)
    return curves


def _schedule_trail_cache_dirty(container, dirty_range=None, sync_key_handles=False):
    if container and cmds.objExists(container):
        _schedule_trail_redraw(container, dirty_range=dirty_range, sync_key_handles=sync_key_handles)


def _trail_source_attr_changed(message, plug, other_plug, client_data):
    if _TRAIL_INTERNAL_UPDATE:
        return
    flags = (
        getattr(om.MNodeMessage, "kAttributeSet", 0) |
        getattr(om.MNodeMessage, "kConnectionMade", 0) |
        getattr(om.MNodeMessage, "kConnectionBroken", 0)
    )
    if not (message & flags):
        return

    try:
        attr_name = plug.partialName(useLongNames=True)
    except Exception:
        attr_name = ""

    if attr_name:
        interesting = (
            attr_name.startswith("translate") or
            attr_name.startswith("rotate") or
            attr_name.startswith("scale") or
            attr_name in ("matrix", "worldMatrix", "parentMatrix")
        )
        if not interesting and not (message & getattr(om.MNodeMessage, "kConnectionMade", 0)):
            return

    connection_changed = bool(
        message & (
            getattr(om.MNodeMessage, "kConnectionMade", 0) |
            getattr(om.MNodeMessage, "kConnectionBroken", 0)
        )
    )
    if connection_changed:
        _invalidate_trail_runtime_cache(client_data)
    object_name = _get_string_attr(client_data, TRAIL_OBJECT_ATTR)
    dirty_range = _dirty_range_for_times(client_data, object_name, [_current_time_value()])
    _schedule_trail_cache_dirty(client_data, dirty_range=dirty_range, sync_key_handles=connection_changed)


def _trail_anim_curve_attr_changed(message, plug, other_plug, client_data):
    if _TRAIL_INTERNAL_UPDATE:
        return
    flags = (
        getattr(om.MNodeMessage, "kAttributeSet", 0) |
        getattr(om.MNodeMessage, "kConnectionMade", 0) |
        getattr(om.MNodeMessage, "kConnectionBroken", 0)
    )
    if message & flags:
        if message & (
            getattr(om.MNodeMessage, "kConnectionMade", 0) |
            getattr(om.MNodeMessage, "kConnectionBroken", 0)
        ):
            _invalidate_trail_runtime_cache(client_data)
        object_name = _get_string_attr(client_data, TRAIL_OBJECT_ATTR)
        curve_name = _handle_from_callback_node(plug.node())
        dirty_range = _dirty_range_for_anim_curves(client_data, object_name, [curve_name] if curve_name else [])
        if dirty_range:
            _schedule_trail_cache_dirty(client_data, dirty_range=dirty_range, sync_key_handles=True)


def _trail_anim_curve_edited_callback(*args):
    if _TRAIL_INTERNAL_UPDATE:
        return
    container = None
    for arg in reversed(args):
        if isinstance(arg, str):
            container = arg
            break
    if not container:
        container, _ = _find_existing_trail()
    if _get_string_attr(container, TRAIL_COMPONENT_ATTR, ""):
        _schedule_vertex_trail_rebake(container)
        return
    object_name = _get_string_attr(container, TRAIL_OBJECT_ATTR)
    curve_names = _curve_names_from_callback_args(args)
    dirty_range = _dirty_range_for_anim_curves(container, object_name, curve_names)
    if dirty_range:
        _schedule_trail_cache_dirty(container, dirty_range=dirty_range, sync_key_handles=True)


def _clear_anim_callbacks():
    global _TRAIL_ANIM_CALLBACKS
    for callback_id in list(_TRAIL_ANIM_CALLBACKS):
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass
    _TRAIL_ANIM_CALLBACKS = []


def _clear_vertex_callbacks():
    global _TRAIL_VERTEX_CALLBACKS
    for callback_id in list(_TRAIL_VERTEX_CALLBACKS):
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass
    _TRAIL_VERTEX_CALLBACKS = []


def _component_watch_nodes(component):
    if not component:
        return []
    try:
        flattened = cmds.ls(component, flatten=True, long=True) or []
        component = flattened[0] if flattened else component
    except Exception:
        pass

    owner = component.split(".vtx[", 1)[0] if ".vtx[" in component else component
    if not owner or not cmds.objExists(owner):
        return []

    nodes = []
    try:
        node_type = cmds.nodeType(owner)
    except Exception:
        node_type = None

    if node_type == "mesh":
        nodes.append(owner)
        try:
            nodes.extend(cmds.listRelatives(owner, parent=True, fullPath=True) or [])
        except Exception:
            pass
    else:
        nodes.append(owner)
        try:
            nodes.extend(cmds.listRelatives(owner, shapes=True, noIntermediate=True, fullPath=True, type="mesh") or [])
        except Exception:
            pass

    unique_nodes = []
    seen = set()
    for node in nodes:
        if node and node not in seen and cmds.objExists(node):
            seen.add(node)
            unique_nodes.append(node)
    return unique_nodes


def _vertex_source_attr_changed(message, plug, other_plug, client_data):
    if _TRAIL_INTERNAL_UPDATE or _is_anim_playing():
        return
    flags = (
        getattr(om.MNodeMessage, "kAttributeSet", 0) |
        getattr(om.MNodeMessage, "kOtherPlugSet", 0) |
        getattr(om.MNodeMessage, "kConnectionMade", 0) |
        getattr(om.MNodeMessage, "kConnectionBroken", 0)
    )
    if not (message & flags):
        return

    try:
        attr_name = plug.partialName(useLongNames=True).lower()
    except Exception:
        attr_name = ""

    if attr_name:
        relevant_tokens = (
            "translate", "rotate", "scale", "matrix",
            "mesh", "geometry", "pnts", "vrts", "controlpoints",
        )
        connection_changed = bool(
            message & (
                getattr(om.MNodeMessage, "kConnectionMade", 0) |
                getattr(om.MNodeMessage, "kConnectionBroken", 0)
            )
        )
        if not connection_changed and not any(token in attr_name for token in relevant_tokens):
            return

    _schedule_vertex_trail_rebake(client_data)


def _register_vertex_source_callbacks(container):
    global _TRAIL_VERTEX_CALLBACKS
    source_component = _get_string_attr(container, TRAIL_COMPONENT_ATTR, "")
    if not source_component:
        return []

    callback_ids = []
    for node in _component_watch_nodes(source_component):
        try:
            selection = om.MSelectionList()
            selection.add(node)
            callback_id = om.MNodeMessage.addAttributeChangedCallback(
                selection.getDependNode(0),
                _vertex_source_attr_changed,
                container,
            )
            callback_ids.append(callback_id)
        except Exception:
            pass
    _TRAIL_VERTEX_CALLBACKS.extend(callback_ids)
    return callback_ids


def _register_anim_curve_callbacks(container):
    global _TRAIL_ANIM_CALLBACKS
    _clear_anim_callbacks()
    if not container or not cmds.objExists(container):
        return []

    object_name = _get_string_attr(container, TRAIL_OBJECT_ATTR)
    if not object_name or not cmds.objExists(object_name):
        return []

    callback_ids = []

    try:
        add_edited_callback = getattr(oma.MAnimMessage, "addAnimCurveEditedCallback", None)
        if add_edited_callback:
            callback_ids.append(add_edited_callback(_trail_anim_curve_edited_callback, container))
    except Exception:
        pass

    _TRAIL_ANIM_CALLBACKS.extend(callback_ids)
    return callback_ids


def _handle_from_callback_node(node):
    try:
        fn_node = om.MFnDependencyNode(node)
        return fn_node.name()
    except Exception:
        return None


def _tangent_handle_attr_changed(message, plug, other_plug, client_data):
    if not (message & om.MNodeMessage.kAttributeSet):
        return
    handle = client_data or _handle_from_callback_node(plug.node())
    if not handle or not cmds.objExists(handle):
        return
    try:
        attr_name = plug.partialName(useLongNames=True)
    except Exception:
        attr_name = ""
    if attr_name and not attr_name.startswith("translate") and attr_name not in ("matrix", "worldMatrix"):
        return
    _apply_tangent_handle_to_curves(handle)


def _tangent_handles_in_container(container):
    group = _get_string_attr(container, TRAIL_TANGENT_GROUP_ATTR)
    if not group or not cmds.objExists(group):
        return []
    try:
        descendants = cmds.listRelatives(group, allDescendents=True, type="transform", fullPath=False) or []
    except Exception:
        descendants = []
    handles = []
    for node in descendants:
        try:
            if cmds.attributeQuery("animKeyTangentObject", node=node, exists=True):
                handles.append(node)
        except Exception:
            pass
    return handles


def _clear_tangent_callbacks():
    global _TRAIL_TANGENT_CALLBACKS
    for callback_id in list(_TRAIL_TANGENT_CALLBACKS):
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass
    _TRAIL_TANGENT_CALLBACKS = []


def _key_handles_in_container(container):
    group = _get_string_attr(container, TRAIL_KEY_GROUP_ATTR)
    if not group or not cmds.objExists(group):
        return []
    try:
        descendants = cmds.listRelatives(group, allDescendents=True, type="transform", fullPath=False) or []
    except Exception:
        descendants = []
    handles = []
    for node in descendants:
        try:
            if cmds.attributeQuery("animKeyTrailKeyObject", node=node, exists=True):
                handles.append(node)
        except Exception:
            pass
    return handles


def _selected_key_handles(container=None):
    selection = cmds.ls(selection=True, long=False) or []
    handles = []
    for node in selection:
        try:
            if not cmds.objExists(node) or not cmds.attributeQuery("animKeyTrailKeyObject", node=node, exists=True):
                continue
            if container and _get_string_attr(node, "animKeyTrailKeyContainer") != container:
                continue
            handles.append(node)
        except Exception:
            pass
    return handles


def _clear_key_callbacks():
    global _TRAIL_KEY_CALLBACKS
    for callback_id in list(_TRAIL_KEY_CALLBACKS):
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass
    _TRAIL_KEY_CALLBACKS = []


def _register_key_handle_callbacks(container):
    callback_ids = []
    for handle in _key_handles_in_container(container):
        try:
            selection = om.MSelectionList()
            selection.add(handle)
            callback_id = om.MNodeMessage.addAttributeChangedCallback(
                selection.getDependNode(0),
                _key_handle_attr_changed,
                handle,
            )
            callback_ids.append(callback_id)
        except Exception:
            pass
    _TRAIL_KEY_CALLBACKS.extend(callback_ids)
    return callback_ids


def _register_tangent_handle_callbacks(container):
    callback_ids = []
    for handle in _tangent_handles_in_container(container):
        try:
            selection = om.MSelectionList()
            selection.add(handle)
            callback_id = om.MNodeMessage.addAttributeChangedCallback(
                selection.getDependNode(0),
                _tangent_handle_attr_changed,
                handle,
            )
            callback_ids.append(callback_id)
        except Exception:
            pass
    _TRAIL_TANGENT_CALLBACKS.extend(callback_ids)
    return callback_ids


def _clear_editable_key_handles(trail_container):
    _invalidate_trail_runtime_cache(trail_container)
    group = _get_string_attr(trail_container, TRAIL_KEY_GROUP_ATTR)
    if group and cmds.objExists(group):
        try:
            cmds.delete(group)
        except Exception:
            pass
    fallback_group = f"{trail_container}_editableKeys"
    if cmds.objExists(fallback_group):
        try:
            cmds.delete(fallback_group)
        except Exception:
            pass
    try:
        descendants = cmds.listRelatives(trail_container, allDescendents=True, type="transform", fullPath=False) or []
    except Exception:
        descendants = []
    for node in list(descendants):
        try:
            if cmds.objExists(node) and cmds.attributeQuery("animKeyTrailKeyObject", node=node, exists=True):
                cmds.delete(node)
        except Exception:
            pass
    for node in cmds.ls(f"{trail_container}_keyHandle_*") or []:
        try:
            if cmds.objExists(node):
                cmds.delete(node)
        except Exception:
            pass
    _set_string_attr(trail_container, TRAIL_KEY_GROUP_ATTR, "")
    _set_string_attr(trail_container, TRAIL_KEY_SIGNATURE_ATTR, "")


def _clear_editable_tangent_handles(trail_container):
    group = _get_string_attr(trail_container, TRAIL_TANGENT_GROUP_ATTR)
    if group and cmds.objExists(group):
        try:
            cmds.delete(group)
        except Exception:
            pass
    fallback_group = f"{trail_container}_editableTangents"
    if cmds.objExists(fallback_group):
        try:
            cmds.delete(fallback_group)
        except Exception:
            pass
    try:
        descendants = cmds.listRelatives(trail_container, allDescendents=True, type="transform", fullPath=False) or []
    except Exception:
        descendants = []
    for node in list(descendants):
        try:
            if cmds.objExists(node) and cmds.attributeQuery("animKeyTangentObject", node=node, exists=True):
                cmds.delete(node)
        except Exception:
            pass
    for node in cmds.ls(f"{trail_container}_tan_*") or []:
        try:
            if cmds.objExists(node):
                cmds.delete(node)
        except Exception:
            pass
    _set_string_attr(trail_container, TRAIL_TANGENT_GROUP_ATTR, "")


def _key_handle_stored_world_position(handle):
    return (
        _get_double_attr(handle, "animKeyTrailKeyWorldX"),
        _get_double_attr(handle, "animKeyTrailKeyWorldY"),
        _get_double_attr(handle, "animKeyTrailKeyWorldZ"),
    )


def _store_key_handle_world_position(handle, position):
    _set_double_attr(handle, "animKeyTrailKeyWorldX", position[0])
    _set_double_attr(handle, "animKeyTrailKeyWorldY", position[1])
    _set_double_attr(handle, "animKeyTrailKeyWorldZ", position[2])
    shape = _custom_tangent_shape(handle)
    if shape:
        _set_attr_if_exists(shape, "keyPosition", position[0], position[1], position[2], attr_type="double3")
        _set_attr_if_exists(shape, "drawLine", False)
        try:
            cmds.dgdirty(shape)
        except Exception:
            pass


def _nudge_curve_key(curve, frame, delta):
    if not curve or not cmds.objExists(curve):
        return False
    try:
        values = cmds.keyframe(curve, query=True, time=(frame, frame), valueChange=True) or []
        if values:
            cmds.keyframe(curve, edit=True, time=(frame, frame), valueChange=float(values[0]) + float(delta))
            return True
    except Exception:
        pass
    return False


def _nudge_object_translate_key(object_name, attr, frame, delta):
    attr_full = f"{object_name}.{attr}"
    if not cmds.objExists(attr_full):
        return False
    try:
        current_value = cmds.getAttr(attr_full, time=frame)
        if isinstance(current_value, (list, tuple)):
            return False
        cmds.setKeyframe(attr_full, time=frame, value=float(current_value) + float(delta))
        return True
    except Exception:
        return False


def _apply_key_handle_to_curves(handle):
    global _TRAIL_KEY_UPDATE_LOCK
    if _TRAIL_KEY_UPDATE_LOCK or not handle or not cmds.objExists(handle):
        return

    object_name = _get_string_attr(handle, "animKeyTrailKeyObject")
    container = _get_string_attr(handle, "animKeyTrailKeyContainer")
    frame = _get_double_attr(handle, "animKeyTrailKeyFrame")
    if not object_name or not cmds.objExists(object_name):
        return

    try:
        new_world = tuple(cmds.xform(handle, query=True, worldSpace=True, translation=True))
    except Exception:
        return

    old_world = _key_handle_stored_world_position(handle)
    delta_world = _vector_sub(new_world, old_world)
    if _vector_length(delta_world) <= 0.000001:
        return

    delta_local = _world_vector_to_parent_space(object_name, delta_world)

    _TRAIL_KEY_UPDATE_LOCK = True
    try:
        def _edit_curves():
            for attr, axis_index in (("translateX", 0), ("translateY", 1), ("translateZ", 2)):
                delta = delta_local[axis_index]
                if abs(delta) <= 0.000001:
                    continue
                curves = _get_string_list_attr(handle, f"animKeyTrailKeyCurves{attr[-1]}")
                applied = False
                for curve in curves:
                    applied = _nudge_curve_key(curve, frame, delta) or applied
                if not applied:
                    _nudge_object_translate_key(object_name, attr, frame, delta)

        _run_internal_update(_edit_curves)

        _store_key_handle_world_position(handle, new_world)
        if container and cmds.objExists(container):
            dirty_range = _dirty_range_for_times(container, object_name, [frame])
            _schedule_trail_cache_dirty(container, dirty_range=dirty_range)
    finally:
        _TRAIL_KEY_UPDATE_LOCK = False


def _key_handle_attr_changed(message, plug, other_plug, client_data):
    if not (message & om.MNodeMessage.kAttributeSet):
        return
    handle = client_data or _handle_from_callback_node(plug.node())
    if not handle or not cmds.objExists(handle):
        return
    try:
        attr_name = plug.partialName(useLongNames=True)
    except Exception:
        attr_name = ""
    if attr_name and not attr_name.startswith("translate") and attr_name not in ("matrix", "worldMatrix"):
        return
    _apply_key_handle_to_curves(handle)


def _create_editable_key_handles(trail_container, object_name):
    _clear_editable_key_handles(trail_container)

    settings = get_trail_settings()
    if not settings.get("show_key_handles", True):
        return []
    if not cmds.objExists(trail_container) or not cmds.objExists(object_name):
        return []

    start, end = _frame_range(trail_container)
    max_keys = min(160, max(1, int(settings.get("max_key_markers", TRAIL_MAX_KEY_MARKERS))))
    key_times = _translate_key_times_for_object(object_name, start, end, max_keys=max_keys)
    _set_string_attr(trail_container, TRAIL_KEY_SIGNATURE_ATTR, _key_times_signature(key_times))
    _invalidate_trail_runtime_cache(trail_container)
    if not key_times:
        return []

    curves_by_key_attr = {}
    for attr in ("translateX", "translateY", "translateZ"):
        for curve in _translate_anim_curves_for_attr(object_name, attr):
            try:
                curve_keys = cmds.keyframe(curve, query=True, time=(start, end), timeChange=True) or []
            except Exception:
                curve_keys = []
            for curve_key in curve_keys:
                curves_by_key_attr.setdefault((attr, _frame_cache_key(curve_key)), []).append(curve)

    matrix_plug = _trail_target_matrix_plug(trail_container, object_name)
    positions = _sample_world_positions(object_name, key_times, matrix_plug=matrix_plug)
    if not positions:
        return []

    radius = _scene_radius(list(positions.values()))
    handle_radius = max(0.055, radius * 0.40 * float(settings.get("marker_scale", 1.0)))
    handle_screen_radius = max(5.5, min(10.0, handle_radius * 34.0))
    palette = _resolved_trail_palette(settings)
    color = palette.get("key", (1.0, 0.86, 0.36))

    group = cmds.createNode("transform", name=f"{trail_container}_editableKeys")
    try:
        cmds.parent(group, trail_container)
    except Exception:
        pass
    _add_node_to_container(group, trail_container)
    _set_string_attr(trail_container, TRAIL_KEY_GROUP_ATTR, group)

    created = [group]
    for index, key_time in enumerate(key_times, start=1):
        key_pos = positions.get(key_time)
        if key_pos is None:
            continue
        key_lookup = _frame_cache_key(key_time)
        curve_map = {}
        for attr in ("translateX", "translateY", "translateZ"):
            curves = curves_by_key_attr.get((attr, key_lookup), [])
            if curves:
                curve_map[attr] = curves

        base_name = f"{trail_container}_keyHandle_{index:03d}"
        handle = _create_tangent_handle_node(
            f"{base_name}_ctrl",
            key_pos,
            handle_radius,
            color,
            key_position=key_pos,
            line_color=color,
            draw_line=False,
            screen_radius=handle_screen_radius,
        )
        if not handle:
            continue

        try:
            cmds.parent(handle, group, absolute=True)
        except Exception:
            pass

        _set_string_attr(handle, "animKeyTrailKeyObject", object_name)
        _set_string_attr(handle, "animKeyTrailKeyContainer", trail_container)
        _set_double_attr(handle, "animKeyTrailKeyFrame", key_time)
        _store_key_handle_world_position(handle, key_pos)
        _set_string_list_attr(handle, "animKeyTrailKeyCurvesX", curve_map.get("translateX", []))
        _set_string_list_attr(handle, "animKeyTrailKeyCurvesY", curve_map.get("translateY", []))
        _set_string_list_attr(handle, "animKeyTrailKeyCurvesZ", curve_map.get("translateZ", []))
        created.append(handle)

    if len(created) == 1:
        _clear_editable_key_handles(trail_container)
        return []
    _lock_node_transforms(group)
    return created


def _create_editable_tangent_handles(trail_container, object_name):
    _clear_editable_tangent_handles(trail_container)

    settings = get_trail_settings()
    if not settings.get("show_tangent_handles", False):
        return []
    if not cmds.objExists(trail_container) or not cmds.objExists(object_name):
        return []

    start, end = _frame_range(trail_container)
    max_keys = min(48, max(1, int(settings.get("max_key_markers", TRAIL_MAX_KEY_MARKERS))))
    key_times = _translate_key_times_for_object(object_name, start, end, max_keys=max_keys)
    if not key_times:
        return []

    curves_by_key_attr = {}
    for attr in ("translateX", "translateY", "translateZ"):
        for curve in _translate_anim_curves_for_attr(object_name, attr):
            try:
                curve_keys = cmds.keyframe(curve, query=True, time=(start, end), timeChange=True) or []
            except Exception:
                curve_keys = []
            for curve_key in curve_keys:
                curves_by_key_attr.setdefault((attr, _frame_cache_key(curve_key)), []).append(curve)

    sample_times = set(key_times)
    offsets_by_key = {}
    for index, key_time in enumerate(key_times):
        previous_gap = key_time - key_times[index - 1] if index > 0 else key_time - start
        next_gap = key_times[index + 1] - key_time if index + 1 < len(key_times) else end - key_time
        available_gap = max(0.5, min(gap for gap in (previous_gap, next_gap) if gap > 0.001) if any(gap > 0.001 for gap in (previous_gap, next_gap)) else 1.0)
        sample_offset = max(0.25, min(1.0, available_gap * 0.25))
        in_time = max(start, key_time - sample_offset)
        out_time = min(end, key_time + sample_offset)
        offsets_by_key[key_time] = (sample_offset, in_time, out_time)
        sample_times.add(in_time)
        sample_times.add(out_time)

    matrix_plug = _trail_target_matrix_plug(trail_container, object_name)
    positions = _sample_world_positions(object_name, sorted(sample_times), matrix_plug=matrix_plug)
    if not positions:
        return []

    radius = _scene_radius(list(positions.values()))
    handle_radius = max(0.06, radius * 0.45 * float(settings.get("marker_scale", 1.0)))
    palette = _resolved_trail_palette(settings)

    group = cmds.createNode("transform", name=f"{trail_container}_editableTangents")
    try:
        cmds.parent(group, trail_container)
    except Exception:
        pass
    _add_node_to_container(group, trail_container)
    _set_string_attr(trail_container, TRAIL_TANGENT_GROUP_ATTR, group)

    created = [group]

    def _create_side(key_time, side, sample_time, color, index):
        key_pos = positions.get(key_time)
        sample_pos = positions.get(sample_time)
        if key_pos is None or sample_pos is None:
            return None
        curve_map = {}
        key_lookup = _frame_cache_key(key_time)
        for attr in ("translateX", "translateY", "translateZ"):
            curves = curves_by_key_attr.get((attr, key_lookup), [])
            if curves:
                curve_map[attr] = curves
        if not curve_map:
            return None
        fallback_vector = _vector_sub(sample_pos, key_pos)
        fallback_time_delta = max(0.25, abs(sample_time - key_time))
        handle_vector, time_delta = _initial_tangent_vector_from_curves(
            object_name,
            curve_map,
            key_time,
            side,
            fallback_vector,
            fallback_time_delta,
        )
        handle_vector, time_delta, display_scale = _fit_tangent_handle_display(
            handle_vector,
            time_delta,
            fallback_vector,
            handle_radius,
        )
        if _vector_length(handle_vector) <= 0.000001:
            return None
        handle_pos = _vector_add(key_pos, handle_vector)
        if abs(time_delta) < 0.001:
            return None

        base_name = f"{trail_container}_tan_{index:03d}_{side}"
        handle = _create_tangent_handle_node(
            f"{base_name}_ctrl",
            handle_pos,
            handle_radius,
            color,
            key_position=key_pos,
            line_color=color,
        )
        line = None
        if not _custom_tangent_shape(handle):
            line = _line_curve_between(f"{base_name}_line", key_pos, handle_pos, color)

        for node in (line, handle):
            if not node:
                continue
            try:
                cmds.parent(node, group, absolute=True)
            except Exception:
                pass
        if line:
            _lock_node_transforms(line)

        _set_string_attr(handle, "animKeyTangentObject", object_name)
        _set_string_attr(handle, "animKeyTangentSide", side)
        _set_string_attr(handle, "animKeyTangentLine", line or "")
        _set_string_attr(handle, "animKeyTangentContainer", trail_container)
        _set_double_attr(handle, "animKeyTangentFrame", key_time)
        _set_double_attr(handle, "animKeyTangentTimeDelta", time_delta)
        _set_double_attr(handle, "animKeyTangentDisplayScale", display_scale)
        _set_double_attr(handle, "animKeyTangentKeyX", key_pos[0])
        _set_double_attr(handle, "animKeyTangentKeyY", key_pos[1])
        _set_double_attr(handle, "animKeyTangentKeyZ", key_pos[2])
        _set_string_list_attr(handle, "animKeyTangentCurvesX", curve_map.get("translateX", []))
        _set_string_list_attr(handle, "animKeyTangentCurvesY", curve_map.get("translateY", []))
        _set_string_list_attr(handle, "animKeyTangentCurvesZ", curve_map.get("translateZ", []))
        if line:
            created.append(line)
        created.append(handle)
        return handle

    for index, key_time in enumerate(key_times, start=1):
        sample_offset, in_time, out_time = offsets_by_key[key_time]
        in_handle = None
        out_handle = None
        if key_time - in_time > 0.001:
            in_handle = _create_side(key_time, "in", in_time, palette.get("past_key", palette["key"]), index)
        if out_time - key_time > 0.001:
            out_handle = _create_side(key_time, "out", out_time, palette.get("future_key", palette["key"]), index)
        if in_handle and out_handle:
            _set_string_attr(in_handle, "animKeyTangentPair", out_handle)
            _set_string_attr(out_handle, "animKeyTangentPair", in_handle)

    if len(created) == 1:
        _clear_editable_tangent_handles(trail_container)
        return []
    _lock_node_transforms(group)
    return created


def _unlock_translate_for_connection(node):
    for attr in ("translateX", "translateY", "translateZ"):
        try:
            cmds.setAttr(f"{node}.{attr}", lock=False, keyable=False, channelBox=False)
        except Exception:
            pass


def _connect_current_marker_matrix(trail_container, object_name, marker):
    if not cmds.objExists(object_name) or not cmds.objExists(marker):
        return None
    matrix_node = cmds.createNode("decomposeMatrix", name=f"{trail_container}_currentMatrix")
    try:
        cmds.connectAttr(f"{object_name}.worldMatrix[0]", f"{matrix_node}.inputMatrix", force=True)
        _unlock_translate_for_connection(marker)
        cmds.connectAttr(f"{matrix_node}.outputTranslate", f"{marker}.translate", force=True)
    except Exception:
        try:
            if cmds.objExists(matrix_node):
                cmds.delete(matrix_node)
        except Exception:
            pass
        return None

    try:
        cmds.container(trail_container, edit=True, addNode=matrix_node, includeNetwork=True)
    except Exception:
        pass
    _set_string_attr(trail_container, TRAIL_CURRENT_MATRIX_ATTR, matrix_node)
    return matrix_node


def _kill_scriptjob(job_id):
    try:
        job_id = int(job_id)
    except Exception:
        return
    try:
        if cmds.scriptJob(exists=job_id):
            cmds.scriptJob(kill=job_id, force=True)
    except Exception:
        pass


def _clear_trail_scriptjobs(container=None):
    global _TRAIL_SCRIPTJOBS
    _clear_key_callbacks()
    _clear_tangent_callbacks()
    _clear_anim_callbacks()
    _clear_vertex_callbacks()
    for job in list(_TRAIL_SCRIPTJOBS):
        _kill_scriptjob(job)
    _TRAIL_SCRIPTJOBS = []
    _TRAIL_LAST_TANGENT_REDRAW.clear()
    if container:
        _TRAIL_REBUILD_PENDING.discard(container)
        _TRAIL_REDRAW_PENDING.discard(container)
        _TRAIL_KEY_SYNC_PENDING.discard(container)
        _TRAIL_PENDING_RANGES.pop(container, None)
        _TRAIL_VERTEX_REBAKE_PENDING.discard(container)
    else:
        _TRAIL_REBUILD_PENDING.clear()
        _TRAIL_REDRAW_PENDING.clear()
        _TRAIL_KEY_SYNC_PENDING.clear()
        _TRAIL_PENDING_RANGES.clear()
        _TRAIL_VERTEX_REBAKE_PENDING.clear()

    if container and cmds.objExists(container):
        raw = _get_string_attr(container, TRAIL_SCRIPTJOBS_ATTR, "")
        for part in raw.split(","):
            if part.strip():
                _kill_scriptjob(part.strip())
        _set_string_attr(container, TRAIL_SCRIPTJOBS_ATTR, "")


def handle_scene_changed(*args):
    """Reset trail runtime state after Maya opens or creates a scene."""
    _clear_trail_scriptjobs()

    try:
        current_context = cmds.currentCtx()
        if current_context and "animKeyTrail" in current_context:
            cmds.setToolTo("moveSuperContext")
    except Exception:
        pass

    try:
        _cleanup_failed_custom_trail_nodes()
    except Exception:
        pass

    trail_containers = _iter_trail_containers()
    if trail_containers:
        try:
            _apply_trail_appearance(rebuild_cache=False)
        except Exception:
            pass

    registered_callbacks = False
    for container, object_name in trail_containers:
        if not container or not cmds.objExists(container):
            continue
        try:
            _set_string_attr(container, TRAIL_SCRIPTJOBS_ATTR, "")
            if object_name and cmds.objExists(object_name):
                if not registered_callbacks:
                    _register_trail_scriptjobs(container)
                    registered_callbacks = True
                _redraw_custom_trail(container)
        except Exception:
            pass


def _rebuild_trail_from_container(container):
    global _TRAIL_REBUILDING
    if _TRAIL_REBUILDING or not cmds.objExists(container):
        return
    object_name = _get_string_attr(container, TRAIL_OBJECT_ATTR)
    if not object_name or not cmds.objExists(object_name):
        return
    selection = cmds.ls(selection=True, long=True) or []
    _TRAIL_REBUILDING = True
    try:
        _clear_trail_scriptjobs(container)
        if cmds.objExists(container):
            cmds.delete(container)
        cmds.select(object_name, replace=True)
        create_trail()
    except Exception:
        pass
    finally:
        _TRAIL_REBUILDING = False
        try:
            if selection:
                cmds.select(selection, replace=True)
        except Exception:
            pass


def _schedule_trail_rebuild(container):
    if _TRAIL_REBUILDING or not cmds.objExists(container):
        return
    if container in _TRAIL_REBUILD_PENDING:
        return
    _TRAIL_REBUILD_PENDING.add(container)

    def _do_rebuild(c=container):
        try:
            if cmds.objExists(c):
                _rebuild_trail_from_container(c)
        finally:
            _TRAIL_REBUILD_PENDING.discard(c)

    try:
        cmds.scriptJob(runOnce=True, idleEvent=_do_rebuild, protected=True)
    except Exception:
        _TRAIL_REBUILD_PENDING.discard(container)


def _trail_shape_in_container(container):
    if not container or not cmds.objExists(container):
        return None
    shapes = []
    try:
        shapes.extend(cmds.listRelatives(container, allDescendents=True, shapes=True, fullPath=False) or [])
    except Exception:
        pass
    try:
        members = cmds.container(container, query=True, nodeList=True) or []
    except Exception:
        members = []
    for member in members:
        if not member or not cmds.objExists(member):
            continue
        try:
            if cmds.nodeType(member) == "animKeyMotionTrail":
                shapes.append(member)
                continue
        except Exception:
            pass
        try:
            shapes.extend(cmds.listRelatives(member, shapes=True, fullPath=False) or [])
        except Exception:
            pass
    for shape in shapes:
        try:
            if cmds.nodeType(shape) == "animKeyMotionTrail":
                return shape
        except Exception:
            pass
    return shapes[0] if shapes else None


def _set_custom_trail_dirty_range(shape, dirty_range):
    if not shape or not cmds.objExists(shape):
        return
    if dirty_range and len(dirty_range) >= 2:
        start, end = float(dirty_range[0]), float(dirty_range[1])
        if end < start:
            start, end = end, start
    else:
        start, end = 1.0e20, -1.0e20
    _set_attr_if_exists(shape, TRAIL_DIRTY_START_ATTR, start)
    _set_attr_if_exists(shape, TRAIL_DIRTY_END_ATTR, end)


def _redraw_custom_trail(container):
    shape = _trail_shape_in_container(container)
    if not shape or not cmds.objExists(shape):
        return

    try:
        cmds.dgdirty(shape)
    except Exception:
        pass
    try:
        if cmds.attributeQuery("outData", node=shape, exists=True):
            cmds.dgdirty(f"{shape}.outData")
    except Exception:
        pass


def _dirty_custom_trail(container, dirty_range=None):
    def _apply_dirty():
        shape = _trail_shape_in_container(container)
        if not shape or not cmds.objExists(shape):
            return

        _set_custom_trail_dirty_range(shape, dirty_range)

        # Bump cacheVersion to force cache update on next draw.
        try:
            if cmds.attributeQuery("cacheVersion", node=shape, exists=True):
                current_version = cmds.getAttr(f"{shape}.cacheVersion")
                cmds.setAttr(f"{shape}.cacheVersion", current_version + 1)
        except Exception:
            pass

        _redraw_custom_trail(container)

    return _run_without_undo(_apply_dirty)


def _merge_pending_dirty_range(container, dirty_range):
    if container not in _TRAIL_PENDING_RANGES:
        _TRAIL_PENDING_RANGES[container] = dirty_range
        return
    existing = _TRAIL_PENDING_RANGES.get(container)
    if existing is None or dirty_range is None:
        _TRAIL_PENDING_RANGES[container] = None
        return
    _TRAIL_PENDING_RANGES[container] = (
        min(float(existing[0]), float(dirty_range[0])),
        max(float(existing[1]), float(dirty_range[1])),
    )


def _schedule_trail_redraw(container, dirty_range=None, sync_key_handles=False):
    if not container or not cmds.objExists(container):
        return
    _merge_pending_dirty_range(container, dirty_range)
    if sync_key_handles:
        _TRAIL_KEY_SYNC_PENDING.add(container)
    if container in _TRAIL_REDRAW_PENDING:
        return
    _TRAIL_REDRAW_PENDING.add(container)

    def _do_redraw(c=container):
        try:
            dirty_range_to_apply = _TRAIL_PENDING_RANGES.pop(c, None)
            if not cmds.objExists(c):
                return
            shape = _trail_shape_in_container(c)
            if shape and cmds.objExists(shape) and cmds.nodeType(shape) == "animKeyMotionTrail":
                _dirty_custom_trail(c, dirty_range=dirty_range_to_apply)
                if c in _TRAIL_KEY_SYNC_PENDING:
                    if _sync_editable_key_handles_if_needed(c):
                        _dirty_custom_trail(c)
            else:
                _schedule_trail_rebuild(c)
        finally:
            _TRAIL_KEY_SYNC_PENDING.discard(c)
            _TRAIL_REDRAW_PENDING.discard(c)

    try:
        try:
            cmds.evalDeferred(_do_redraw, lowestPriority=True)
        except TypeError:
            cmds.evalDeferred(_do_redraw)
    except Exception:
        try:
            cmds.scriptJob(runOnce=True, idleEvent=_do_redraw, protected=True)
        except Exception:
            _TRAIL_REDRAW_PENDING.discard(container)
            _TRAIL_KEY_SYNC_PENDING.discard(container)
            dirty_range_to_apply = _TRAIL_PENDING_RANGES.pop(container, dirty_range)
            _dirty_custom_trail(container, dirty_range=dirty_range_to_apply)
            if sync_key_handles:
                _sync_editable_key_handles_if_needed(container)


def _dirty_range_for_undo_redo(container):
    object_name = _get_string_attr(container, TRAIL_OBJECT_ATTR)
    frames = []
    try:
        frames = [
            _get_double_attr(handle, "animKeyTrailKeyFrame")
            for handle in _selected_key_handles(container)
        ]
    except Exception:
        frames = []
    if not frames:
        frames = [_current_time_value()]
    return _dirty_range_for_times(container, object_name, frames)


def _schedule_undo_redo_trail_update(container):
    if _get_string_attr(container, TRAIL_COMPONENT_ATTR, ""):
        _schedule_vertex_trail_rebake(container)
        return
    _schedule_trail_redraw(
        container,
        dirty_range=_dirty_range_for_undo_redo(container),
        sync_key_handles=True,
    )


def _register_trail_scriptjobs(container):
    global _TRAIL_SCRIPTJOBS
    _clear_trail_scriptjobs(container)
    if not container or not cmds.objExists(container):
        return

    _register_key_handle_callbacks(container)
    _register_tangent_handle_callbacks(container)
    _register_vertex_source_callbacks(container)
    _register_anim_curve_callbacks(container)

    jobs = []
    for event_name in ("Undo", "Redo"):
        try:
            job = cmds.scriptJob(
                event=[event_name, lambda c=container: _schedule_undo_redo_trail_update(c)],
                protected=True
            )
            jobs.append(job)
        except Exception:
            pass

    _TRAIL_SCRIPTJOBS.extend(jobs)
    _set_string_attr(container, TRAIL_SCRIPTJOBS_ATTR, ",".join(str(job) for job in jobs))


def _create_trail_container(object_name, source_label=None):
    """
    Create trail container for specific object.
    Format: animkey_[objectName]_Trail
    
    Args:
        object_name: Name of the object being followed
    
    Returns:
        Name of the created container
    """
    short_name = _get_object_short_name(source_label or object_name)
    if short_name.startswith("animkey_"):
        trail_name = f"{short_name}_Trail"
    else:
        trail_name = f"animkey_{short_name}_Trail"
    
    # Delete existing trail if it exists
    if cmds.objExists(trail_name):
        cmds.delete(trail_name)
    
    # Create dagContainer (like AnimKey's AnimKey_pSphere1_Trail)
    container = cmds.container(type='dagContainer', name=trail_name)
    
    # Set icon if available
    icon_path = _get_icon_path("animkey_btn_trl_128.png")
    if icon_path and os.path.exists(icon_path):
        try:
            cmds.setAttr(container + '.iconName', icon_path, type='string')
        except:
            pass
    
    # Parent to animkey_trail
    if cmds.objExists("animkey_trail"):
        cmds.parent(container, "animkey_trail")
    
    # Lock and hide transform attributes
    attributes = ["translateX", "translateY", "translateZ",
                 "rotateX", "rotateY", "rotateZ",
                 "scaleX", "scaleY", "scaleZ", "visibility"]
    
    for attr in attributes:
        try:
            cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
        except:
            pass
    
    return trail_name


def _create_premium_trail_visuals(trail_container, object_name):
    settings = get_trail_settings()
    palette = _resolved_trail_palette(settings)
    start, end = _frame_range(trail_container)
    curve_times = _sample_times(start, end, settings["max_samples"])
    key_times = _key_times_for_object(object_name, start, end, settings["max_key_markers"])
    current_time = float(cmds.currentTime(query=True))
    all_times = sorted(set(curve_times + key_times + [current_time]))
    matrix_plug = _trail_target_matrix_plug(trail_container, object_name)
    positions_by_time = _sample_world_positions(object_name, all_times, matrix_plug=matrix_plug)

    curve_points = [positions_by_time[t] for t in curve_times if t in positions_by_time]
    if not curve_points:
        curve_points = [_world_pivot(object_name)]

    radius = _scene_radius(curve_points)

    nodes = []
    if settings["show_glow"]:
        glow = _create_curve_node(
            f"{trail_container}_glowCurve",
            curve_points,
            palette["glow"],
            settings["glow_width"]
        )
        nodes.append(glow)

    main_curve = _create_curve_node(
        f"{trail_container}_mainCurve",
        curve_points,
        palette["main"],
        settings["line_width"]
    )
    nodes.append(main_curve)

    key_marker_radius = radius * 0.55 * settings["marker_scale"]
    if settings["show_key_markers"]:
        for index, key_time in enumerate(key_times):
            pos = positions_by_time.get(key_time)
            if pos is None:
                continue
            marker = _create_locator_marker(
                f"{trail_container}_key_{index + 1:03d}",
                pos,
                key_marker_radius,
                palette["key"],
                line_width=2.0
            )
            nodes.append(marker)

    current_pos = positions_by_time.get(current_time) or _world_pivot(object_name)
    current_marker = _create_locator_marker(
        f"{trail_container}_currentFrame",
        current_pos,
        radius * 1.15 * settings["marker_scale"],
        palette["current"],
        line_width=3.0
    )
    nodes.append(current_marker)

    for node in nodes:
        _parent_to_container(node, trail_container)
        _make_node_display_only(node)
        if node != current_marker:
            _lock_node_transforms(node)
        else:
            for attr in ("rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ", "visibility"):
                try:
                    cmds.setAttr(f"{node}.{attr}", lock=True, keyable=False, channelBox=False)
                except Exception:
                    pass

    matrix_node = _connect_current_marker_matrix(trail_container, object_name, current_marker)
    _set_string_attr(trail_container, TRAIL_OBJECT_ATTR, object_name)
    _set_string_attr(trail_container, TRAIL_CURRENT_MARKER_ATTR, current_marker)
    if matrix_node:
        _set_string_attr(trail_container, TRAIL_CURRENT_MATRIX_ATTR, matrix_node)
    nodes.extend(_create_editable_tangent_handles(trail_container, object_name))
    _register_trail_scriptjobs(trail_container)

    return nodes


def _create_tracking_nodes(trail_container, object_name):
    """
    Create tracking and constraint nodes inside the trail container.
    Similar to AnimKey's AnimKey_MotionTrail_Tracking3 and AnimKey_parentConstraint.
    
    Args:
        trail_container: Name of the trail container
        object_name: Name of the object being traced
    
    Returns:
        tuple: (tracking_node_name, constraint_node_name, matrix_nodes_list)
    """
    # Create tracking node (like AnimKey_MotionTrail_Tracking3)
    # Use counter to avoid name conflicts
    tracking_counter = 1
    tracking_name = "AnimKey_MotionTrail_Tracking"
    while cmds.objExists(f"{tracking_name}{tracking_counter}"):
        tracking_counter += 1
    
    tracking_node_name = f"{tracking_name}{tracking_counter}"
    
    # Create a transform node for tracking (this represents the tracking node)
    tracking_node = cmds.createNode("transform", name=tracking_node_name)
    
    # Create matrix nodes for tracking
    # These will be connected to track the object's motion
    # Note: multMatrix nodes are dependency nodes and cannot be parented to dagContainers
    # They exist in the scene but are referenced by the container
    matrix_nodes = []
    for i in range(3):  # Create 3 matrix nodes like AnimKey
        matrix_name = f"{trail_container}_matrix{i+1}"
        if cmds.objExists(matrix_name):
            cmds.delete(matrix_name)
        
        # Create multMatrix node for matrix operations
        matrix = cmds.createNode("multMatrix", name=matrix_name)
        matrix_nodes.append(matrix)
    
    # Create parentConstraint node (like AnimKey_parentConstraint)
    constraint_counter = tracking_counter
    constraint_name = f"AnimKey_parentConstraint"
    if cmds.objExists(constraint_name):
        constraint_counter = 1
        while cmds.objExists(f"{constraint_name}_{constraint_counter}"):
            constraint_counter += 1
        constraint_name = f"{constraint_name}_{constraint_counter}"
    else:
        constraint_name = f"{constraint_name}_1"
    
    # Create a transform node as constraint target (DAG node that can be parented)
    constraint_target = cmds.createNode("transform", name=constraint_name)
    
    # Create actual parentConstraint connection
    constraint_node_name = constraint_name
    try:
        # Create a parentConstraint from object to constraint target
        if cmds.objExists(object_name):
            # Create constraint (this will create the constraint node)
            constraint_nodes = cmds.parentConstraint(object_name, constraint_target, maintainOffset=True)
            if constraint_nodes:
                # Get the actual constraint node name
                constraint_node_name = constraint_nodes[0] if isinstance(constraint_nodes, list) else constraint_nodes
        else:
            constraint_node_name = constraint_name
    except Exception as e:
        # If constraint creation fails, just use the transform name
        constraint_node_name = constraint_name
    
    # Parent DAG nodes to trail container (only DAG nodes can be parented)
    dag_nodes_to_parent = [tracking_node, constraint_target]
    for node in dag_nodes_to_parent:
        try:
            if cmds.objExists(node):
                cmds.parent(node, trail_container)
        except:
            pass
    
    # Add dependency nodes (multMatrix) to container as members
    # These nodes are referenced by the container but not parented
    for matrix_node in matrix_nodes:
        try:
            if cmds.objExists(matrix_node):
                # Add node to container as a member
                cmds.container(trail_container, edit=True, addNode=matrix_node, includeNetwork=True)
        except:
            pass
    
    # Also add constraint node to container if it exists and is different from target
    if constraint_node_name != constraint_name and cmds.objExists(constraint_node_name):
        try:
            cmds.container(trail_container, edit=True, addNode=constraint_node_name, includeNetwork=True)
        except:
            pass
    
    # Connect matrix nodes to tracking
    # Connect object's worldMatrix to matrix nodes
    if matrix_nodes and cmds.objExists(object_name):
        try:
            # Get object's worldMatrix
            obj_world_matrix = f"{object_name}.worldMatrix[0]"
            if cmds.attributeQuery("worldMatrix", node=object_name, exists=True):
                # Connect to first matrix node
                try:
                    cmds.connectAttr(obj_world_matrix, f"{matrix_nodes[0]}.matrixIn[0]", force=True)
                except:
                    pass
                
                # Connect matrix nodes in chain
                for i in range(len(matrix_nodes) - 1):
                    try:
                        cmds.connectAttr(f"{matrix_nodes[i]}.matrixSum", f"{matrix_nodes[i+1]}.matrixIn[0]", force=True)
                    except:
                        pass
        except:
            pass
    
    return tracking_node_name, constraint_node_name, matrix_nodes


def _iter_trail_containers():
    """Return AnimKey-managed trail containers with their stored source object."""
    if not cmds.objExists(TRAIL_ROOT):
        return []

    containers = []
    try:
        temp_children = cmds.listRelatives(TRAIL_ROOT, children=True, fullPath=False) or []
        for child in temp_children:
            if child.startswith("animkey_") and child.endswith("_Trail"):
                stored_object = _get_string_attr(child, TRAIL_OBJECT_ATTR)
                if stored_object:
                    containers.append((child, stored_object))
                    continue
                containers.append((child, child.replace("animkey_", "").replace("_Trail", "")))
    except Exception:
        pass
    return containers


def _find_existing_trail():
    """
    Find any existing trail container in the scene.
    Returns the trail container name and the object name it belongs to, or None.
    """
    containers = _iter_trail_containers()
    if containers:
        return containers[0]
    return None, None


def has_active_trail():
    """
    Check if there's an active trail in the scene.
    
    Returns:
        bool: True if a trail exists, False otherwise
    """
    existing_trail, _ = _find_existing_trail()
    return existing_trail is not None


def set_button_active(button, is_active):
    """
    Set the button's active state (highlighted when trail is active).
    
    Args:
        button: The Qt button widget (can be None)
        is_active: True to highlight, False to restore normal state
    """
    if button is None:
        return
    
    try:
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        
        # Get the button's color from its current stylesheet or use default green
        color = "#a3be8c"  # Default green for TRL button
        
        if is_active:
            # Active state: highlighted background with brighter border
            # Use a brighter green background to indicate active state
            active_bg = "#6a8f5a"  # Darker green for active state
            button.setStyleSheet(f'''
                QPushButton {{
                    color: #ffffff;
                    background-color: {active_bg};
                    border: 2px solid {color};
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {active_bg};
                    border-color: {color};
                }}
                QPushButton:pressed {{
                    background-color: {theme["button_pressed"]};
                }}
            ''')
        else:
            # Normal state: restore original style
            button.setStyleSheet(f'''
                QPushButton {{
                    color: {color};
                    background-color: {theme["button_bg"]};
                    border: 1px solid {theme["border_color"]};
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {theme["button_hover"]};
                    border-color: {color};
                }}
                QPushButton:pressed {{
                    background-color: {theme["button_pressed"]};
                }}
            ''')
    except Exception as e:
        # If styling fails, just continue
        pass


def execute(*args, button=None):
    """
    Main function to execute when button is clicked.
    Toggles motion trail: removes any existing trail, then creates new one if object is selected.
    
    Args:
        button: Optional Qt button widget to update visual state
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.trail.execute"):
        return None
    # First, check if there's any existing trail and remove it
    existing_trail, existing_object = _find_existing_trail()
    if existing_trail:
        # Remove the existing trail (this will work regardless of current selection)
        _clear_trail_scriptjobs(existing_trail)
        if cmds.objExists(existing_trail):
            cmds.delete(existing_trail)
        # Also clean up legacy Maya snapshot nodes if they exist.
        _cleanup_legacy_snapshot_nodes()
        
        # Update button state to inactive
        set_button_active(button, False)
        cmds.warning(f"AnimKey: Removed existing trail")
        return
    
    # If no existing trail, check if we can create a new one
    source = _selected_trail_source()
    if not source:
        return
    
    # Create new trail
    create_trail()
    
    # Update button state to active
    set_button_active(button, True)


def create_trail(*args):
    """
    Create an AnimKey-managed custom motion trail on the selected object.
    """
    source = _selected_trail_source()
    if not source:
        return
    
    source_object = source["object"]
    source_component = source.get("component", "")
    is_component_source = bool(source.get("is_component"))
    object_name = source_object
    original_selection = cmds.ls(selection=True, long=True) or []
    
    # Create container structure
    _create_animkey_container()
    _create_trail_root_container()
    _cleanup_legacy_snapshot_nodes()
    
    # Create trail container for this object
    trail_container = _create_trail_container(source_object, source.get("label"))

    try:
        start_frame, end_frame = _frame_range(use_time_slider_selection=True)
        settings = get_trail_settings()
        increment = _effective_trail_increment(start_frame, end_frame, settings)
        sample_density = max(1, int(settings.get("sample_density", 1)))

        if is_component_source:
            object_name = _bake_vertex_locator(trail_container, source_component, start_frame, end_frame)

        _set_string_attr(trail_container, TRAIL_OBJECT_ATTR, object_name)
        _set_string_attr(trail_container, TRAIL_COMPONENT_ATTR, source_component)
        _set_string_attr(trail_container, TRAIL_SCRIPTJOBS_ATTR, "")
        _store_trail_frame_range(trail_container, start_frame, end_frame)

        trail_transform = None
        try:
            trail_transform = _create_custom_trail_node(
                object_name,
                start_frame,
                end_frame,
                increment,
                sample_density,
            )
        except Exception as plugin_error:
            _cleanup_failed_custom_trail_nodes()
            print(f"AnimKey: Custom motion trail unavailable, using curve fallback: {plugin_error}")

        if trail_transform and cmds.objExists(trail_transform):
            try:
                cmds.parent(trail_transform, trail_container)
            except Exception:
                pass

            _apply_trail_appearance()
            if settings.get("show_key_handles", True) and not is_component_source:
                _create_editable_key_handles(trail_container, object_name)
            if not is_component_source:
                _create_editable_tangent_handles(trail_container, object_name)
            _register_trail_scriptjobs(trail_container)
        else:
            if is_component_source:
                raise RuntimeError("vertex motion trails require the custom AnimKey trail plugin")
            _create_fallback_trail_visuals(trail_container, object_name)
    except Exception as e:
        if cmds.objExists(trail_container):
            try:
                cmds.delete(trail_container)
            except Exception:
                pass
        _cleanup_legacy_snapshot_nodes()
        cmds.warning(f"AnimKey: Could not create motion trail: {e}")
        return
    
    # Restore original selection (the object being followed, not the constraint)
    if original_selection:
        cmds.select(original_selection, replace=True)
    else:
        cmds.select(object_name, replace=True)


def select_trail_container(object_name):
    """Select the trail container for a specific object"""
    short_name = _get_object_short_name(object_name)
    trail_name = f"animkey_{short_name}_Trail"
    
    if cmds.objExists(trail_name):
        cmds.select(trail_name, replace=True)
    else:
        cmds.warning(f"AnimKey: Trail container '{trail_name}' does not exist.")


def remove_trail(object_name):
    """
    Remove the trail for a specific object.
    Also removes legacy Maya snapshot nodes if they exist.
    """
    short_name = _get_object_short_name(object_name)
    trail_name = f"animkey_{short_name}_Trail"
    
    if cmds.objExists(trail_name):
        _clear_trail_scriptjobs(trail_name)
        # Delete the trail container (this will remove all its contents)
        cmds.delete(trail_name)
    
    _cleanup_legacy_snapshot_nodes()


def remove_all_trails(*args):
    """Remove all trails (delete animkey_trail container)"""
    if cmds.objExists(TRAIL_ROOT):
        try:
            children = cmds.listRelatives(TRAIL_ROOT, children=True, fullPath=False) or []
            for child in children:
                _clear_trail_scriptjobs(child)
        except Exception:
            pass
        cmds.delete(TRAIL_ROOT)
    _cleanup_legacy_snapshot_nodes()


def trail_refresh(*args):
    """Refresh the active trail in place without deleting the trail container."""
    existing_trail, object_name = _find_existing_trail()
    if not existing_trail or not object_name or not cmds.objExists(object_name):
        cmds.warning("AnimKey: No custom trail in the scene")
        return

    selection = cmds.ls(selection=True, long=True) or []
    _clear_trail_scriptjobs(existing_trail)
    try:
        settings = get_trail_settings()
        source_component = _get_string_attr(existing_trail, TRAIL_COMPONENT_ATTR, "")
        if source_component:
            start_frame, end_frame = _frame_range(existing_trail)
            object_name = _bake_vertex_locator(
                existing_trail,
                source_component,
                start_frame,
                end_frame,
                locator=object_name,
            )
            _set_string_attr(existing_trail, TRAIL_OBJECT_ATTR, object_name)
            _connect_custom_trail_target(existing_trail, object_name)
        _apply_trail_appearance()
        _clear_editable_key_handles(existing_trail)
        _clear_editable_tangent_handles(existing_trail)
        if settings.get("show_key_handles", True) and not source_component:
            _create_editable_key_handles(existing_trail, object_name)
        if settings.get("show_tangent_handles", False) and not source_component:
            _create_editable_tangent_handles(existing_trail, object_name)
        _register_trail_scriptjobs(existing_trail)
        _dirty_custom_trail(existing_trail)
    finally:
        try:
            if selection:
                cmds.select(selection, replace=True)
        except Exception:
            pass


def _trail_visual_nodes():
    existing_trail, _ = _find_existing_trail()
    if not existing_trail or not cmds.objExists(existing_trail):
        return []
    try:
        return cmds.listRelatives(existing_trail, children=True, fullPath=False) or []
    except Exception:
        return []


def _set_trail_palette(main_color, glow_color, key_color, current_color):
    settings = get_trail_settings()
    for node in _trail_visual_nodes():
        if node.endswith("_mainCurve"):
            _style_shapes(node, main_color, line_width=settings["line_width"])
        elif node.endswith("_glowCurve"):
            _style_shapes(node, glow_color, line_width=settings["glow_width"])
        elif "_currentFrame" in node:
            _style_shapes(node, current_color, line_width=3.0)
        elif "_key_" in node:
            _style_shapes(node, key_color, line_width=2.0)


def set_trail_blue_color(*args):
    """Set trail color to blue"""
    set_trail_palette("cyan", refresh=True)


def set_trail_red_color(*args):
    """Set trail color to red"""
    set_trail_palette("red", refresh=True)


def set_trail_grey_color(*args):
    """Set trail color to grey"""
    set_trail_palette("grey", refresh=True)


def trail_show_hide(*args):
    """Toggle trail visibility"""
    existing_trail, _ = _find_existing_trail()
    if not existing_trail or not cmds.objExists(existing_trail):
        return
    visibility = cmds.getAttr(f"{existing_trail}.visibility")
    cmds.setAttr(f"{existing_trail}.visibility", not visibility)


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Trail",
        "tooltip": "Create a customizable motion trail on the selected object",
        "icon": "trail.svg",
        "shortcut": None,
    }

