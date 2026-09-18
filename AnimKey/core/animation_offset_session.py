"""Event-driven Animation Offset engine for AnimKey."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Dict, Optional, Tuple

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from AnimKey.mods.maya_compat import QtCore, QtWidgets

from AnimKey.core.animation_offset_math import (
    FRAME_EPSILON,
    ROTATION_EPSILON,
    VALUE_EPSILON,
    contiguous_index_runs,
    frames_equal,
    is_finite_number,
    normalize_inclusive_time_range,
    rotation_delta_from_observation,
    value_at_frame,
    values_equal,
)


LOGGER = logging.getLogger("AnimKey.AnimationOffset")

ANIMATION_OFFSET_ENGINE_REVISION = 5

SUPPORTED_TYPES = {
    "double",
    "doubleLinear",
    "doubleAngle",
    "float",
}

_ATTR_LONG_NAMES = {
    "tx": "translateX",
    "ty": "translateY",
    "tz": "translateZ",
    "rx": "rotateX",
    "ry": "rotateY",
    "rz": "rotateZ",
    "sx": "scaleX",
    "sy": "scaleY",
    "sz": "scaleZ",
    "v": "visibility",
}


class SessionPhase(Enum):
    INACTIVE = "inactive"
    STARTING = "starting"
    ACTIVE = "active"
    COMMITTING = "committing"
    STOPPING = "stopping"


@dataclass
class TrackState:
    track_id: str
    node_uuid: str
    node_path: str
    attr: str
    attr_type: str
    curve: Optional[str]
    target_layer: Optional[str]
    key_times: Tuple[float, ...]
    key_values: Dict[float, float]
    key_indices: Dict[float, int]
    observe_plug: bool = True
    evaluated_baselines: Dict[float, float] = field(default_factory=dict)
    applied_delta: float = 0.0
    dirty: bool = False
    dirty_time: Optional[float] = None
    last_observed_value: Optional[float] = None
    pending_key_values: Dict[float, float] = field(default_factory=dict)

    @property
    def plug(self):
        return "{}.{}".format(self.node_path, self.attr)

    @property
    def is_rotation(self):
        return self.attr in {"rotateX", "rotateY", "rotateZ"}


def get_active_session():
    return OffsetSession.active_instance()


def has_active_session():
    session = get_active_session()
    return bool(session and session.is_active())


def _as_scalar(value):
    scalar = value
    while isinstance(scalar, (list, tuple)) and len(scalar) == 1:
        scalar = scalar[0]
    if isinstance(scalar, (list, tuple)):
        return None, False
    return scalar, True


def _split_plug(plug):
    if not plug or "." not in plug:
        return None, None
    node, attr = plug.rsplit(".", 1)
    attr = attr.split("[", 1)[0]
    return node, _ATTR_LONG_NAMES.get(attr, attr)


def _node_name_candidates(node):
    names = set()
    if not node:
        return names
    names.add(node)
    names.add(node.split("|")[-1])
    try:
        names.update(cmds.ls(node, long=True) or [])
    except Exception:
        pass
    try:
        names.update(cmds.ls(node, long=False) or [])
    except Exception:
        pass
    return {name for name in names if name}


def _plug_candidates(attr_full_name):
    node, attr = _split_plug(attr_full_name)
    if not node or not attr:
        return []

    candidates = []
    seen = set()
    for node_name in _node_name_candidates(node):
        plug = "{}.{}".format(node_name, attr)
        if plug not in seen:
            seen.add(plug)
            candidates.append(plug)
    return candidates or [attr_full_name]


def _plugs_match(left_plug, right_plug):
    left_node, left_attr = _split_plug(left_plug)
    right_node, right_attr = _split_plug(right_plug)
    if not left_node or not right_node or left_attr != right_attr:
        return False
    return bool(_node_name_candidates(left_node).intersection(_node_name_candidates(right_node)))


def _mel_string(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _has_any_animation_layers():
    try:
        return bool(cmds.ls(type="animLayer"))
    except Exception:
        return False


def _is_base_layer(layer_name):
    return not layer_name or layer_name == "BaseAnimation"


def get_selected_animation_layer():
    try:
        all_layers = cmds.ls(type="animLayer") or []
    except Exception:
        return None

    if not all_layers:
        return None

    # Maya can leave an older layer marked as ``selected`` while the layer
    # that will actually receive keys is marked ``preferred``.  The latter is
    # the authoritative state used by the animation editors, so resolve it
    # first (the same ordering used by Animo's Global Offset).
    preferred = []
    for layer in all_layers:
        try:
            if cmds.animLayer(layer, query=True, preferred=True):
                preferred.append(layer)
        except Exception:
            continue
    if preferred:
        real_layers = [layer for layer in preferred if not _is_base_layer(layer)]
        return (real_layers or preferred)[-1]

    selected = []
    for layer in all_layers:
        try:
            if cmds.animLayer(layer, query=True, selected=True):
                selected.append(layer)
        except Exception:
            continue
    if selected:
        real_layers = [layer for layer in selected if not _is_base_layer(layer)]
        return (real_layers or selected)[-1]

    try:
        root_layer = cmds.animLayer(query=True, root=True)
        if root_layer:
            return root_layer
    except Exception:
        pass

    try:
        if cmds.animLayer("BaseAnimation", query=True, exists=True):
            return "BaseAnimation"
    except Exception:
        pass
    return None


def _layer_anim_curves(layer_name):
    try:
        return cmds.animLayer(layer_name, query=True, animCurves=True) or []
    except Exception:
        return []


def _curve_reaches_attr(curve, attr_full_name, max_depth=8):
    if not curve or not cmds.objExists(curve):
        return False

    seen = set()
    stack = [curve]
    for _depth in range(max_depth):
        next_stack = []
        for node in stack:
            if node in seen:
                continue
            seen.add(node)
            try:
                outputs = cmds.listConnections(
                    node,
                    source=False,
                    destination=True,
                    plugs=True,
                    skipConversionNodes=True,
                ) or []
            except Exception:
                outputs = []

            for out_plug in outputs:
                if _plugs_match(out_plug, attr_full_name):
                    return True
                out_node = out_plug.split(".", 1)[0]
                if out_node not in seen:
                    next_stack.append(out_node)

        if not next_stack:
            break
        stack = next_stack
    return False


def _fallback_layer_curve_for_attr(attr_full_name, layer_name):
    for curve in _layer_anim_curves(layer_name):
        try:
            if cmds.objExists(curve) and cmds.nodeType(curve).startswith("animCurve"):
                if _curve_reaches_attr(curve, attr_full_name):
                    return curve
        except Exception:
            continue
    return None


def _find_curve_for_layer_plug(attr_full_name, layer_name):
    """Use Maya's native layer lookup before walking the blend graph.

    ``findCurveForPlug`` is substantially more reliable for custom facial
    attributes and nested animation layers than inferring ownership only from
    connections.  Older Maya releases can reject the flag for BaseAnimation,
    so every call remains guarded and the existing graph resolver is kept as
    the fallback.
    """
    if not layer_name:
        return None
    for candidate in _plug_candidates(attr_full_name):
        try:
            result = cmds.animLayer(
                layer_name,
                query=True,
                findCurveForPlug=candidate,
            )
        except Exception:
            continue
        curves = result if isinstance(result, (list, tuple)) else [result]
        for curve in curves:
            if not curve:
                continue
            try:
                if cmds.objExists(curve) and cmds.nodeType(curve).startswith("animCurve"):
                    return curve
            except Exception:
                continue
    return None


def _layered_plug_for_attr(attr_full_name, layer_name):
    try:
        if not cmds.objExists(attr_full_name):
            return None
    except Exception:
        return None

    if _is_base_layer(layer_name):
        try:
            conns = cmds.listConnections(
                attr_full_name,
                type="animBlendNodeBase",
                source=True,
                destination=False,
            ) or []
        except Exception:
            conns = []
        if not conns:
            return attr_full_name

        blend_node = conns[0]
        while True:
            try:
                upstream = cmds.listConnections(
                    blend_node,
                    type="animBlendNodeBase",
                    source=True,
                    destination=False,
                ) or []
            except Exception:
                upstream = []
            if not upstream:
                break
            blend_node = upstream[0]

        short_attr = attr_full_name.rsplit(".", 1)[-1]
        try:
            node_type = cmds.objectType(blend_node)
        except Exception:
            node_type = ""
        if node_type == "animBlendNodeAdditiveRotation" and short_attr and short_attr[-1] in "XYZxyz":
            return "{}.inputA{}".format(blend_node, short_attr[-1].upper())
        return "{}.inputA".format(blend_node)

    try:
        if not cmds.animLayer(layer_name, query=True, exists=True):
            return None
        owned_attrs = cmds.animLayer(layer_name, query=True, attribute=True) or []
    except Exception:
        return None

    candidates = _plug_candidates(attr_full_name)
    owns_attr = (
        not owned_attrs
        or any(
            _plugs_match(candidate, owned_attr)
            for candidate in candidates
            for owned_attr in owned_attrs
        )
    )

    for candidate in candidates:
        try:
            plug = mel.eval(
                'animLayer -q -layeredPlug "{0}" "{1}"'.format(
                    _mel_string(candidate),
                    _mel_string(layer_name),
                )
            )
            if plug:
                return plug
        except Exception:
            continue

    if not owns_attr:
        return None
    return None


def _blend_input_weight_attr(input_attr):
    """Return the blend weight that controls one anim-layer input plug."""
    if input_attr.startswith("inputA"):
        return "weightA"
    if input_attr.startswith("inputB"):
        return "weightB"
    return None


def _blend_output_attr(input_attr):
    """Map inputA/inputBX style names to output/outputX."""
    if input_attr.startswith("inputA") or input_attr.startswith("inputB"):
        suffix = input_attr[6:]
        return "output{}".format(suffix)
    return None


def _layer_output_influence(attr_full_name, layer_name):
    """
    Return the effective contribution of a layer driver to the final channel.

    Maya exposes the evaluated foreground/background weights on every
    animBlendNode. Following those values is more reliable than reconstructing
    mute, solo, override and nested-layer rules ourselves, and works for both
    scalar and rotation blend nodes.
    """
    if not _has_any_animation_layers():
        return 1.0

    current_plug = _layered_plug_for_attr(attr_full_name, layer_name)
    if not current_plug:
        return None

    influence = 1.0
    visited = set()
    while current_plug and current_plug not in visited:
        visited.add(current_plug)
        node, input_attr = _split_plug(current_plug)
        if not node or not input_attr:
            return None
        try:
            node_type = cmds.nodeType(node)
        except Exception:
            return None
        if not node_type.startswith("animBlendNode"):
            return 1.0 if _plugs_match(current_plug, attr_full_name) else None

        weight_attr = _blend_input_weight_attr(input_attr)
        output_attr = _blend_output_attr(input_attr)
        if not weight_attr or not output_attr:
            return None
        try:
            influence *= float(cmds.getAttr("{}.{}".format(node, weight_attr)))
        except Exception:
            return None

        output_plug = "{}.{}".format(node, output_attr)
        try:
            destinations = cmds.listConnections(
                output_plug,
                source=False,
                destination=True,
                plugs=True,
                skipConversionNodes=True,
            ) or []
        except Exception:
            return None

        if any(_plugs_match(destination, attr_full_name) for destination in destinations):
            return influence

        current_plug = None
        for destination in destinations:
            destination_node, destination_attr = _split_plug(destination)
            try:
                destination_type = cmds.nodeType(destination_node)
            except Exception:
                continue
            if destination_type.startswith("animBlendNode") and _blend_input_weight_attr(destination_attr):
                current_plug = destination
                break

    return None


def _layer_is_locked(layer_name):
    if not layer_name:
        return False
    try:
        if not cmds.animLayer(layer_name, query=True, exists=True):
            return True
        return bool(cmds.getAttr("{}.lock".format(layer_name)))
    except Exception:
        return False


def resolve_target_curve_for_layer(attr_full_name, layer_name,
                                   has_animation_layers=None):
    if has_animation_layers is None:
        has_animation_layers = _has_any_animation_layers()
    if not has_animation_layers:
        try:
            curves = cmds.listConnections(
                attr_full_name,
                source=True,
                destination=False,
                type="animCurve",
                skipConversionNodes=True,
            ) or []
        except Exception:
            curves = []
        # Constraints can place the original animation behind a pairBlend, so
        # it is no longer a direct connection of the driven channel. Maya's
        # keyframe query still resolves the actual editable animCurve.
        if not curves:
            try:
                candidates = cmds.keyframe(
                    attr_full_name,
                    query=True,
                    name=True,
                ) or []
            except Exception:
                candidates = []
            for candidate in candidates:
                try:
                    if cmds.nodeType(candidate).startswith("animCurve"):
                        curves.append(candidate)
                except Exception:
                    continue
        return curves[0] if curves else None

    native_curve = _find_curve_for_layer_plug(attr_full_name, layer_name)
    if native_curve:
        return native_curve

    plug = _layered_plug_for_attr(attr_full_name, layer_name)
    if not plug:
        return _fallback_layer_curve_for_attr(attr_full_name, layer_name)

    try:
        curves = cmds.listConnections(
            plug,
            source=True,
            destination=False,
            type="animCurve",
            skipConversionNodes=True,
        ) or []
    except Exception:
        curves = []
    return curves[0] if curves else None


def _curve_directly_drives_attr(curve, attr_full_name):
    """True when the curve reaches the channel without a blending node."""
    if not curve:
        return False
    try:
        direct_curves = cmds.listConnections(
            attr_full_name,
            source=True,
            destination=False,
            type="animCurve",
            skipConversionNodes=True,
        ) or []
    except Exception:
        return False
    curve_uuid = _query_node_uuid(curve)
    return any(_query_node_uuid(candidate) == curve_uuid for candidate in direct_curves)


def _selected_objects_long():
    try:
        return cmds.ls(selection=True, long=True) or []
    except Exception:
        return []


def _normalize_attr_name(node, attr):
    attr = _ATTR_LONG_NAMES.get(attr, attr)
    try:
        long_name = cmds.attributeQuery(attr, node=node, longName=True)
        if long_name:
            return long_name
    except Exception:
        pass
    return attr


def _query_node_uuid(node):
    try:
        values = cmds.ls(node, uuid=True) or []
        if values:
            return str(values[0])
    except Exception:
        pass
    return node


def _safe_remove_callback(callback_id):
    try:
        om.MMessage.removeCallback(callback_id)
    except RuntimeError:
        LOGGER.debug("Callback already removed: %s", callback_id, exc_info=True)
    except Exception:
        LOGGER.debug("Could not remove callback: %s", callback_id, exc_info=True)


def _current_ui_time():
    try:
        return float(
            oma.MAnimControl.currentTime.asUnits(om.MTime.uiUnit())
        )
    except Exception:
        return float(cmds.currentTime(query=True))


class _OffsetNavigationFilter(QtCore.QObject):
    def __init__(self, session, parent=None):
        super(_OffsetNavigationFilter, self).__init__(parent)
        self.session = session
        self._last_nav_flush = 0.0
        self._last_nav_key = None
        self._nav_event_types = {
            self._qt_event_type("ShortcutOverride"),
            self._qt_event_type("KeyPress"),
        }
        self._mouse_release_type = self._qt_event_type("MouseButtonRelease")
        self._ctrl_modifier = self._qt_keyboard_modifier("ControlModifier")
        self._nav_keys = {
            self._qt_key("Key_Comma"),
            self._qt_key("Key_Period"),
            self._qt_key("Key_Semicolon"),
            self._qt_key("Key_Less"),
            self._qt_key("Key_Greater"),
        }
        self._nav_keys.discard(None)
        self._undo_keys = {self._qt_key("Key_Z"), self._qt_key("Key_Y")}
        self._undo_keys.discard(None)

    def eventFilter(self, obj, event):
        try:
            event_type = event.type()
            if event_type in self._nav_event_types:
                key = event.key()
                if key in self._nav_keys and not getattr(event, "isAutoRepeat", lambda: False)():
                    now = time.perf_counter()
                    if key != self._last_nav_key or now - self._last_nav_flush > 0.08:
                        self._last_nav_key = key
                        self._last_nav_flush = now
                        self.session.flush_now(reason="key_navigation")
                        self.session.close_undo_chunk()
                elif self._ctrl_modifier is not None and event.modifiers() & self._ctrl_modifier:
                    if key in self._undo_keys:
                        self.session.begin_undo_guard()
            elif event_type == self._mouse_release_type:
                self.session.flush_now(reason="mouse_release")
                self.session.close_undo_chunk()
        except Exception:
            LOGGER.debug("Animation Offset navigation filter failed", exc_info=True)
        return False

    @staticmethod
    def _qt_event_type(name):
        event_enum = getattr(QtCore.QEvent, "Type", None)
        if event_enum is not None and hasattr(event_enum, name):
            return getattr(event_enum, name)
        return getattr(QtCore.QEvent, name)

    @staticmethod
    def _qt_key(name):
        key_enum = getattr(QtCore.Qt, "Key", None)
        if key_enum is not None and hasattr(key_enum, name):
            return getattr(key_enum, name)
        return getattr(QtCore.Qt, name, None)

    @staticmethod
    def _qt_keyboard_modifier(name):
        modifier_enum = getattr(QtCore.Qt, "KeyboardModifier", None)
        if modifier_enum is not None and hasattr(modifier_enum, name):
            return getattr(modifier_enum, name)
        return getattr(QtCore.Qt, name, None)


class OffsetSession(QtCore.QObject):
    _active_instance = None

    def __init__(self, parent=None):
        super(OffsetSession, self).__init__(parent)
        self.phase = SessionPhase.INACTIVE
        self.time_range = None
        self.target_layer = None
        self.current_time = None
        self.tracks = {}
        self.track_ids_by_plug = {}
        self.track_ids_by_node_uuid = defaultdict(set)
        self.track_ids_by_curve_uuid = defaultdict(set)
        self.dirty_track_ids = set()
        self.attribute_dirty_track_ids = set()
        self.callback_ids = []
        self.script_jobs = []
        self.node_callback_uuids = set()
        self.anim_curve_callback_id = None
        self.curve_dirty_track_ids = set()
        self.internal_edit_depth = 0
        self.undo_guard = False
        self.undo_chunk_open = False
        self.generation = 0
        self.max_pending_flushes = 0
        self._navigation_filter = None
        self.has_animation_layers = False

        self.flush_timer = QtCore.QTimer(self)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.setInterval(0)
        self.flush_timer.timeout.connect(self._flush_scheduled)

        self.undo_close_timer = QtCore.QTimer(self)
        self.undo_close_timer.setSingleShot(True)
        self.undo_close_timer.setInterval(150)
        self.undo_close_timer.timeout.connect(self.close_undo_chunk)

    @classmethod
    def active_instance(cls):
        instance = cls._active_instance
        if instance is not None and instance.is_active():
            return instance
        return None

    def is_active(self):
        return self.phase in {SessionPhase.ACTIVE, SessionPhase.COMMITTING}

    def start(self, objects, time_range, target_layer=None):
        existing = OffsetSession.active_instance()
        if existing is not None and existing is not self:
            existing.stop(commit=True)

        self.phase = SessionPhase.STARTING
        self.generation += 1
        self.time_range = normalize_inclusive_time_range(time_range)
        self.target_layer = target_layer
        self.current_time = _current_ui_time()
        self.has_animation_layers = _has_any_animation_layers()

        try:
            for node in objects:
                self.register_object(node)

            if not self.tracks:
                raise RuntimeError("No compatible animated attributes in range")

            self._install_anim_curve_callback()
            self._install_script_jobs()
            self._install_navigation_filter()
            self._anchor_current_time_for_selected_tracks()
            self.phase = SessionPhase.ACTIVE
            OffsetSession._active_instance = self
            return True
        except Exception:
            LOGGER.debug("Animation Offset failed to start", exc_info=True)
            self._cleanup_runtime_state()
            self.phase = SessionPhase.INACTIVE
            if OffsetSession._active_instance is self:
                OffsetSession._active_instance = None
            raise

    def stop(self, commit=True):
        if self.phase is SessionPhase.INACTIVE:
            return
        if self.phase is SessionPhase.STOPPING:
            return

        self.phase = SessionPhase.STOPPING
        self.generation += 1
        try:
            if commit:
                self.commit_dirty(reason="stop", allow_while_stopping=True)
        finally:
            self.flush_timer.stop()
            self.undo_close_timer.stop()
            self.close_undo_chunk()
            self._remove_navigation_filter()
            self._remove_script_jobs()
            self._remove_callbacks()
            self._cleanup_runtime_state()
            self.phase = SessionPhase.INACTIVE
            if OffsetSession._active_instance is self:
                OffsetSession._active_instance = None

    def register_object(self, node):
        long_paths = cmds.ls(node, long=True) or []
        if not long_paths:
            return False
        node_path = long_paths[0]
        if not cmds.objExists(node_path):
            return False

        attrs = cmds.listAttr(node_path, keyable=True, scalar=True) or []
        added_any = False
        for attr in attrs:
            attr = _normalize_attr_name(node_path, attr)
            track = self._build_track(node_path, attr)
            if track is None:
                continue
            if track.track_id in self.tracks:
                continue
            self.tracks[track.track_id] = track
            self.track_ids_by_node_uuid[track.node_uuid].add(track.track_id)
            self._index_track_plug(track)
            self._index_track_curve(track)
            added_any = True

        if added_any:
            self._install_node_callbacks(node_path)
        return added_any

    def schedule_flush(self):
        if self.phase is not SessionPhase.ACTIVE:
            return
        if not self.flush_timer.isActive():
            self.flush_timer.start()

    def flush_now(self, reason="manual"):
        return self.commit_dirty(reason=reason)

    def commit_dirty(self, reason, allow_while_stopping=False, scan_changed_keys=False):
        valid_phase = self.phase is SessionPhase.ACTIVE
        if allow_while_stopping:
            valid_phase = valid_phase or self.phase is SessionPhase.STOPPING
        if not valid_phase and self.phase is not SessionPhase.COMMITTING:
            return False

        scan_track_ids = set(self.curve_dirty_track_ids)
        if scan_changed_keys:
            scan_track_ids.update(self.tracks)
        for track_id in scan_track_ids:
            track = self.tracks.get(track_id)
            if track is not None:
                self._capture_changed_keys(track)
        self.curve_dirty_track_ids.difference_update(scan_track_ids)

        if not self.dirty_track_ids:
            return False

        dirty_ids = set(self.dirty_track_ids)
        self.dirty_track_ids.difference_update(dirty_ids)

        previous_phase = self.phase
        self.phase = SessionPhase.COMMITTING
        applied_any = False
        chunk_was_open = self.undo_chunk_open
        try:
            self.open_undo_chunk()
            with self.internal_edit():
                for track_id in dirty_ids:
                    track = self.tracks.get(track_id)
                    if track is None:
                        continue
                    if self._commit_track(track):
                        applied_any = True
        except Exception:
            LOGGER.exception("Animation Offset commit failed during %s", reason)
        finally:
            for track_id in dirty_ids:
                track = self.tracks.get(track_id)
                if track is not None:
                    track.dirty = False
                    track.dirty_time = None
            self.attribute_dirty_track_ids.difference_update(dirty_ids)
            if previous_phase is SessionPhase.ACTIVE:
                self.phase = SessionPhase.ACTIVE
            elif previous_phase is SessionPhase.STOPPING:
                self.phase = SessionPhase.STOPPING
            else:
                self.phase = previous_phase

        if applied_any:
            self.undo_close_timer.start()
        elif not chunk_was_open:
            self.close_undo_chunk()
        return applied_any

    def apply_slider_changes(self, changes):
        if self.phase is not SessionPhase.ACTIVE or not changes:
            return False

        grouped = defaultdict(list)
        for change in changes:
            plug = change.get("attr_full") if isinstance(change, dict) else None
            if plug:
                grouped[plug].append(change)

        if not grouped:
            return False

        applied_any = False
        self.open_undo_chunk()
        with self.internal_edit():
            for plug, plug_changes in grouped.items():
                if self._apply_slider_group(plug, plug_changes):
                    applied_any = True

        if applied_any:
            self.undo_close_timer.start()
        return applied_any

    def mark_dirty_for_test(self, node, attr, frame=None):
        attr = _normalize_attr_name(node, attr)
        track = self._track_for_node_attr(node, attr)
        if track is None:
            return False
        track.dirty = True
        track.dirty_time = float(frame if frame is not None else _current_ui_time())
        self.dirty_track_ids.add(track.track_id)
        self.attribute_dirty_track_ids.add(track.track_id)
        return True

    def debug_snapshot(self):
        return {
            "phase": self.phase.value,
            "generation": self.generation,
            "track_count": len(self.tracks),
            "dirty_count": len(self.dirty_track_ids),
            "flush_pending": self.flush_timer.isActive(),
            "callback_count": len(self.callback_ids),
            "curve_dirty_count": len(self.curve_dirty_track_ids),
            "script_job_count": len(self.script_jobs),
            "undo_chunk_open": self.undo_chunk_open,
            "time_range": self.time_range,
            "target_layer": self.target_layer,
        }

    def open_undo_chunk(self):
        if self.undo_chunk_open:
            return
        try:
            cmds.undoInfo(openChunk=True, chunkName="AnimKey_AnimOffset")
            self.undo_chunk_open = True
        except Exception:
            LOGGER.debug("Could not open Animation Offset undo chunk", exc_info=True)

    def close_undo_chunk(self):
        if not self.undo_chunk_open:
            return
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            LOGGER.debug("Could not close Animation Offset undo chunk", exc_info=True)
        finally:
            self.undo_chunk_open = False

    def begin_undo_guard(self):
        self.undo_guard = True
        self.flush_timer.stop()
        self.close_undo_chunk()
        self.dirty_track_ids.clear()
        self.attribute_dirty_track_ids.clear()
        self.curve_dirty_track_ids.clear()
        for track in self.tracks.values():
            track.dirty = False
            track.dirty_time = None
            track.pending_key_values.clear()
        QtCore.QTimer.singleShot(350, self.finish_undo_guard)

    def finish_undo_guard(self):
        self.flush_timer.stop()
        self.dirty_track_ids.clear()
        self.attribute_dirty_track_ids.clear()
        self.curve_dirty_track_ids.clear()
        for track in self.tracks.values():
            track.dirty = False
            track.dirty_time = None
            track.pending_key_values.clear()
        self.undo_guard = False
        if self.phase is SessionPhase.ACTIVE:
            self._resnapshot_all_tracks()
            self._anchor_current_time_for_selected_tracks()

    @contextmanager
    def internal_edit(self):
        self.internal_edit_depth += 1
        try:
            yield
        finally:
            self.internal_edit_depth -= 1

    def _build_track(self, node_path, attr):
        plug = "{}.{}".format(node_path, attr)
        if not self._is_supported_plug(plug):
            return None

        # Most rig controls expose many keyable channels but animate only a
        # small subset. This cheap query avoids layer graph/MEL resolution for
        # every unanimated channel, which is the dominant activation cost on
        # large selections.
        try:
            key_count = cmds.keyframe(
                plug,
                query=True,
                time=self.time_range,
                keyframeCount=True,
            )
        except Exception:
            key_count = 0
        if not key_count:
            return None

        curve = resolve_target_curve_for_layer(
            plug,
            self.target_layer,
            has_animation_layers=self.has_animation_layers,
        )
        if curve is None:
            if _has_any_animation_layers():
                return None
            try:
                keys = cmds.keyframe(node_path, attribute=attr, query=True, time=self.time_range) or []
            except Exception:
                keys = []
            if not keys:
                return None

        key_data = self._query_curve_key_data(curve, node_path, attr)
        if not key_data:
            return None

        key_times = tuple(item[1] for item in key_data)
        key_values = {item[1]: item[2] for item in key_data}
        key_indices = {item[1]: item[0] for item in key_data}
        node_uuid = _query_node_uuid(node_path)

        return TrackState(
            track_id="{}.{}".format(node_uuid, attr),
            node_uuid=node_uuid,
            node_path=node_path,
            attr=attr,
            attr_type=cmds.getAttr(plug, type=True),
            curve=curve,
            target_layer=self.target_layer,
            key_times=key_times,
            key_values=key_values,
            key_indices=key_indices,
            observe_plug=(
                self.has_animation_layers
                or curve is None
                or _curve_directly_drives_attr(curve, plug)
            ),
        )

    def _query_curve_key_data(self, curve, node_path, attr):
        target = curve if curve is not None else "{}.{}".format(node_path, attr)
        try:
            all_times = cmds.keyframe(target, query=True, timeChange=True) or []
            all_values = cmds.keyframe(target, query=True, valueChange=True) or []
        except Exception:
            return []

        if len(all_times) != len(all_values):
            return []

        start, end = self.time_range
        data = []
        for index, (frame, value) in enumerate(zip(all_times, all_values)):
            frame = float(frame)
            if frame < start - FRAME_EPSILON or frame > end + FRAME_EPSILON:
                continue
            if not is_finite_number(value):
                continue
            data.append((index, frame, float(value)))
        return data

    def _refresh_track_keys(self, track):
        key_data = self._query_curve_key_data(track.curve, track.node_path, track.attr)
        if not key_data:
            return False

        existing_baselines = dict(track.key_values)
        for _index, frame, value in key_data:
            if not value_at_frame(existing_baselines, frame)[1]:
                baseline = self._baseline_at(track, frame)
                existing_baselines[float(frame)] = float(baseline)

        track.key_times = tuple(item[1] for item in key_data)
        track.key_values = existing_baselines
        track.key_indices = {item[1]: item[0] for item in key_data}
        return True

    def _refresh_all_tracks(self):
        for track in list(self.tracks.values()):
            if cmds.objExists(track.node_path):
                self._refresh_track_keys(track)

    def _resnapshot_all_tracks(self):
        for track in list(self.tracks.values()):
            if not cmds.objExists(track.node_path):
                continue
            key_data = self._query_curve_key_data(
                track.curve,
                track.node_path,
                track.attr,
            )
            if not key_data:
                continue
            track.key_times = tuple(item[1] for item in key_data)
            track.key_values = {
                item[1]: item[2]
                for item in key_data
            }
            track.key_indices = {
                item[1]: item[0]
                for item in key_data
            }
            track.evaluated_baselines.clear()
            track.pending_key_values.clear()
            track.applied_delta = 0.0
            track.last_observed_value = None

    def _is_supported_plug(self, plug):
        try:
            if not cmds.objExists(plug):
                return False
            if cmds.getAttr(plug, lock=True):
                return False
            attr_type = cmds.getAttr(plug, type=True)
            if attr_type not in SUPPORTED_TYPES:
                return False
            value, ok = _as_scalar(cmds.getAttr(plug))
            return ok and is_finite_number(value)
        except Exception:
            return False

    def _install_node_callbacks(self, node_path):
        node_uuid = _query_node_uuid(node_path)
        if node_uuid in self.node_callback_uuids:
            return
        try:
            selection = om.MSelectionList()
            selection.add(node_path)
            node_object = selection.getDependNode(0)
            self.callback_ids.append(
                om.MNodeMessage.addAttributeChangedCallback(
                    node_object,
                    self._on_attribute_changed,
                )
            )
            try:
                self.callback_ids.append(
                    om.MNodeMessage.addNodeDestroyedCallback(
                        node_object,
                        self._on_node_destroyed,
                    )
                )
            except Exception:
                pass
            self.node_callback_uuids.add(node_uuid)
        except Exception:
            LOGGER.debug("Could not install Animation Offset callback on %s", node_path, exc_info=True)

    def _install_anim_curve_callback(self):
        if self.anim_curve_callback_id is not None:
            return
        try:
            callback_id = oma.MAnimMessage.addAnimCurveEditedCallback(
                self._on_anim_curves_edited,
            )
            self.callback_ids.append(callback_id)
            self.anim_curve_callback_id = callback_id
        except Exception:
            LOGGER.debug(
                "Could not install Animation Offset animCurve callback",
                exc_info=True,
            )

    def _install_script_jobs(self):
        self._remove_script_jobs()
        for event_name, callback in (
            ("timeChanged", self._on_time_changed),
            ("SelectionChanged", self._on_selection_changed),
            ("Undo", self._on_undo_redo),
            ("Redo", self._on_undo_redo),
            ("NewSceneOpened", self._on_scene_reset),
        ):
            try:
                self.script_jobs.append(
                    cmds.scriptJob(event=[event_name, callback], killWithScene=True)
                )
            except Exception:
                LOGGER.debug("Could not install scriptJob %s", event_name, exc_info=True)

    def _remove_script_jobs(self):
        for job in list(self.script_jobs):
            try:
                if cmds.scriptJob(exists=job):
                    cmds.scriptJob(kill=job, force=True)
            except Exception:
                LOGGER.debug("Could not remove scriptJob %s", job, exc_info=True)
        self.script_jobs = []

    def _install_navigation_filter(self):
        self._remove_navigation_filter()
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        self._navigation_filter = _OffsetNavigationFilter(self, app)
        app.installEventFilter(self._navigation_filter)

    def _remove_navigation_filter(self):
        if self._navigation_filter is None:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self._navigation_filter)
            except Exception:
                LOGGER.debug("Could not remove Animation Offset event filter", exc_info=True)
        self._navigation_filter = None

    def _remove_callbacks(self):
        for callback_id in list(self.callback_ids):
            _safe_remove_callback(callback_id)
        self.callback_ids = []
        self.node_callback_uuids.clear()
        self.anim_curve_callback_id = None

    def _cleanup_runtime_state(self):
        self.tracks.clear()
        self.track_ids_by_plug.clear()
        self.track_ids_by_node_uuid.clear()
        self.track_ids_by_curve_uuid.clear()
        self.dirty_track_ids.clear()
        self.attribute_dirty_track_ids.clear()
        self.curve_dirty_track_ids.clear()
        self.time_range = None
        self.target_layer = None
        self.current_time = None
        self.internal_edit_depth = 0
        self.undo_guard = False
        self.has_animation_layers = False

    def _index_track_plug(self, track):
        for plug in _plug_candidates(track.plug):
            self.track_ids_by_plug[plug] = track.track_id
        self.track_ids_by_plug[track.plug] = track.track_id

    def _index_track_curve(self, track):
        if not track.curve:
            return
        curve_uuid = _query_node_uuid(track.curve)
        if curve_uuid:
            self.track_ids_by_curve_uuid[curve_uuid].add(track.track_id)

    def _on_attribute_changed(self, message, plug, other_plug, client_data=None):
        if self.phase is not SessionPhase.ACTIVE:
            return
        if self.internal_edit_depth or self.undo_guard:
            return
        if not (message & om.MNodeMessage.kAttributeSet):
            return

        track_id = self._track_id_from_mplug(plug)
        if track_id is None:
            return
        track = self.tracks.get(track_id)
        if track is None:
            return

        track.dirty = True
        track.dirty_time = _current_ui_time()
        self.dirty_track_ids.add(track_id)
        self.attribute_dirty_track_ids.add(track_id)
        self.schedule_flush()

    def _on_anim_curves_edited(self, curve_objects, client_data=None):
        if self.phase is not SessionPhase.ACTIVE:
            return
        if self.internal_edit_depth or self.undo_guard:
            return

        edited_track_ids = set()
        for curve_object in curve_objects:
            try:
                curve_uuid = str(om.MFnDependencyNode(curve_object).uuid())
            except Exception:
                continue
            edited_track_ids.update(
                self.track_ids_by_curve_uuid.get(curve_uuid, ())
            )

        if not edited_track_ids:
            return

        dirty_time = _current_ui_time()
        for track_id in edited_track_ids:
            track = self.tracks.get(track_id)
            if track is None:
                continue
            track.dirty = True
            track.dirty_time = dirty_time
            self.dirty_track_ids.add(track_id)
            self.curve_dirty_track_ids.add(track_id)
        self.schedule_flush()

    def _on_node_destroyed(self, node_object, client_data=None):
        remove_ids = []
        for track_id, track in self.tracks.items():
            if not cmds.objExists(track.node_path):
                remove_ids.append(track_id)
        for track_id in remove_ids:
            self._remove_track(track_id)

    def _on_time_changed(self, *args):
        if self.phase is not SessionPhase.ACTIVE or self.undo_guard:
            return
        self.commit_dirty(reason="timeChanged")
        self.close_undo_chunk()
        self.current_time = _current_ui_time()
        self._anchor_current_time_for_selected_tracks()

    def _on_selection_changed(self, *args):
        if self.phase is not SessionPhase.ACTIVE:
            return
        for node in _selected_objects_long():
            if self._node_registered(node):
                continue
            self.register_object(node)
        self._anchor_current_time_for_selected_tracks()

    def _on_undo_redo(self, *args):
        self.begin_undo_guard()

    def _on_scene_reset(self, *args):
        self.stop(commit=False)

    def _flush_scheduled(self):
        generation = self.generation
        if self.phase is not SessionPhase.ACTIVE:
            return
        self.commit_dirty(reason="scheduled")
        if generation != self.generation:
            return
        if self.dirty_track_ids and not self.flush_timer.isActive():
            self.flush_timer.start()

    def _track_id_from_mplug(self, plug):
        try:
            attr = _ATTR_LONG_NAMES.get(plug.partialName(useLongNames=True).split(".", 1)[-1], None)
        except Exception:
            attr = None
        if not attr:
            try:
                _node, attr = _split_plug(plug.name())
            except Exception:
                attr = None
        if not attr:
            return None
        try:
            node_uuid = str(om.MFnDependencyNode(plug.node()).uuid())
        except Exception:
            node_uuid = None
        if node_uuid:
            track_id = "{}.{}".format(node_uuid, attr)
            if track_id in self.tracks:
                return track_id
        try:
            return self.track_ids_by_plug.get(plug.name())
        except Exception:
            return None

    def _node_registered(self, node):
        node_uuid = _query_node_uuid(node)
        return node_uuid in self.track_ids_by_node_uuid

    def _track_for_node_attr(self, node, attr):
        node_uuid = _query_node_uuid(node)
        track_id = "{}.{}".format(node_uuid, attr)
        track = self.tracks.get(track_id)
        if track is not None:
            return track

        for candidate in _plug_candidates("{}.{}".format(node, attr)):
            track_id = self.track_ids_by_plug.get(candidate)
            if track_id:
                return self.tracks.get(track_id)
        return None

    def _remove_track(self, track_id):
        track = self.tracks.pop(track_id, None)
        self.dirty_track_ids.discard(track_id)
        self.attribute_dirty_track_ids.discard(track_id)
        self.curve_dirty_track_ids.discard(track_id)
        if track is None:
            return
        self.track_ids_by_node_uuid[track.node_uuid].discard(track_id)
        if track.curve:
            curve_uuid = _query_node_uuid(track.curve)
            if curve_uuid:
                self.track_ids_by_curve_uuid[curve_uuid].discard(track_id)
        for plug, mapped_id in list(self.track_ids_by_plug.items()):
            if mapped_id == track_id:
                self.track_ids_by_plug.pop(plug, None)

    def _commit_track(self, track):
        if not cmds.objExists(track.node_path):
            self._remove_track(track.track_id)
            return False

        frame = float(track.dirty_time if track.dirty_time is not None else cmds.currentTime(query=True))
        if frame < self.time_range[0] - FRAME_EPSILON or frame > self.time_range[1] + FRAME_EPSILON:
            return False

        if track.pending_key_values:
            return self._commit_pending_key_changes(track)

        baseline = self._baseline_at(track, frame)
        if self.has_animation_layers and track.track_id in self.attribute_dirty_track_ids:
            current_value = self._capture_layered_driver_key(
                track,
                frame,
                baseline,
            )
        else:
            current_value = self._observed_value(track, frame, baseline)
        if baseline is None or current_value is None:
            return False

        if track.is_rotation:
            desired_delta = rotation_delta_from_observation(
                baseline,
                track.applied_delta,
                current_value,
            )
            epsilon = ROTATION_EPSILON
        else:
            desired_delta = current_value - baseline
            epsilon = VALUE_EPSILON

        delta_to_apply = desired_delta - track.applied_delta
        if abs(delta_to_apply) <= epsilon:
            return False

        protected_times = []
        if self._has_key_at_time(track, frame):
            protected_times.append(frame)
            self._set_track_key_value(track, frame, current_value)

        applied = self._apply_delta_to_track(track, delta_to_apply, protected_times)
        track.applied_delta = desired_delta
        track.last_observed_value = current_value
        return applied or bool(protected_times)

    def _baseline_at(self, track, frame):
        value, found = value_at_frame(track.key_values, frame)
        if found:
            return float(value)

        value, found = value_at_frame(track.evaluated_baselines, frame)
        if found:
            return float(value)

        curve_value = self._evaluate_curve(track, frame)
        if curve_value is None:
            return None
        baseline = float(curve_value) - float(track.applied_delta)
        track.evaluated_baselines[float(frame)] = baseline
        return baseline

    def _observed_value(self, track, frame, baseline=None):
        current_time = float(cmds.currentTime(query=True))
        if frames_equal(frame, current_time):
            plug_value = self._read_plug_value(track.plug)
            curve_value = self._evaluate_curve(track, frame)
            if self.has_animation_layers and curve_value is not None:
                return curve_value
            if not track.observe_plug and curve_value is not None:
                return curve_value
            if baseline is not None:
                expected = float(baseline) + float(track.applied_delta)
                plug_distance = abs(float(plug_value) - expected) if plug_value is not None else -1.0
                curve_distance = abs(float(curve_value) - expected) if curve_value is not None else -1.0
                if plug_distance > curve_distance + VALUE_EPSILON:
                    return plug_value
                if curve_distance >= 0.0:
                    return curve_value
            if plug_value is not None:
                return plug_value
            if curve_value is not None:
                return curve_value

        return self._evaluate_curve(track, frame)

    def _read_plug_value(self, plug):
        try:
            value, ok = _as_scalar(cmds.getAttr(plug))
            if ok and is_finite_number(value):
                return float(value)
        except Exception:
            return None
        return None

    def _capture_layered_driver_key(self, track, frame, baseline):
        layer_name = self.target_layer or "BaseAnimation"
        try:
            if not cmds.animLayer(layer_name, query=True, exists=True):
                return None
        except Exception:
            return None

        # A muted/zero-weight layer, or BaseAnimation hidden by a full
        # override, has no influence on the evaluated channel. Asking Maya to
        # solve a key for such a driver produces arbitrary curve values and
        # can corrupt an otherwise untouched layer. Direct Graph Editor edits
        # are still handled by the animCurve callback; only composite channel
        # capture is skipped here.
        if _layer_is_locked(layer_name):
            return None
        influence = _layer_output_influence(track.plug, layer_name)
        if influence is not None and abs(influence) <= VALUE_EPSILON:
            return None

        try:
            cmds.setKeyframe(
                track.node_path,
                attribute=track.attr,
                time=float(frame),
                animLayer=layer_name,
            )
        except Exception:
            LOGGER.debug(
                "Could not capture Animation Offset driver on layer %s for %s",
                layer_name,
                track.plug,
                exc_info=True,
            )
            return None

        resolved_curve = resolve_target_curve_for_layer(
            track.plug,
            layer_name,
            has_animation_layers=self.has_animation_layers,
        )
        if resolved_curve and resolved_curve != track.curve:
            track.curve = resolved_curve
            self._index_track_curve(track)

        key_data = self._query_curve_key_data(track.curve, track.node_path, track.attr)
        if key_data:
            track.key_times = tuple(item[1] for item in key_data)
            track.key_indices = {item[1]: item[0] for item in key_data}
        if not value_at_frame(track.key_values, frame)[1]:
            track.key_values[float(frame)] = float(baseline)
        return self._evaluate_curve(track, frame)

    def _evaluate_curve(self, track, frame):
        target = track.curve if track.curve is not None else track.plug
        try:
            values = cmds.keyframe(target, query=True, time=(frame, frame), valueChange=True) or []
            if values and is_finite_number(values[0]):
                return float(values[0])
        except Exception:
            pass
        try:
            values = cmds.keyframe(target, query=True, eval=True, time=(frame, frame)) or []
            if values and is_finite_number(values[0]):
                return float(values[0])
        except Exception:
            pass
        return None

    def _has_key_at_time(self, track, frame):
        return value_at_frame({time_value: True for time_value in track.key_times}, frame)[1]

    def _set_track_key_value(self, track, frame, value):
        curve_value = self._evaluate_curve(track, frame)
        if curve_value is not None and values_equal(curve_value, value, epsilon=VALUE_EPSILON):
            return True

        target = track.curve if track.curve is not None else track.plug
        try:
            cmds.keyframe(
                target,
                edit=True,
                time=(frame, frame),
                valueChange=float(value),
            )
            return True
        except Exception:
            LOGGER.debug(
                "Could not set Animation Offset driver key on %s at %s",
                target,
                frame,
                exc_info=True,
            )
            return False

    def _apply_delta_to_track(self, track, delta, protected_times=None):
        protected_times = protected_times or []
        editable_indices = []
        for key_time in track.key_times:
            if any(frames_equal(key_time, protected_time) for protected_time in protected_times):
                continue
            index = track.key_indices.get(key_time)
            if index is not None:
                editable_indices.append(index)

        if not editable_indices:
            return False

        target = track.curve if track.curve is not None else track.plug
        applied = False
        for start, end in contiguous_index_runs(editable_indices):
            try:
                cmds.keyframe(
                    target,
                    edit=True,
                    index=(start, end),
                    relative=True,
                    valueChange=delta,
                )
                applied = True
            except Exception:
                LOGGER.debug(
                    "Could not apply Animation Offset delta to %s indices %s-%s",
                    target,
                    start,
                    end,
                    exc_info=True,
                )
        return applied

    def _capture_changed_keys(self, track):
        key_data = self._query_curve_key_data(track.curve, track.node_path, track.attr)
        if not key_data:
            return False

        current_times = tuple(item[1] for item in key_data)
        current_indices = {item[1]: item[0] for item in key_data}
        changed = []

        for _index, key_time, current_value in key_data:
            baseline, found = value_at_frame(track.key_values, key_time)
            if not found:
                track.key_values[float(key_time)] = (
                    float(current_value) - float(track.applied_delta)
                )
                continue

            if track.is_rotation:
                observed_delta = rotation_delta_from_observation(
                    baseline,
                    track.applied_delta,
                    current_value,
                )
                epsilon = ROTATION_EPSILON
            else:
                observed_delta = float(current_value) - float(baseline)
                epsilon = VALUE_EPSILON

            if not values_equal(observed_delta, track.applied_delta, epsilon=epsilon):
                changed.append((float(key_time), float(current_value), observed_delta))

        track.key_times = current_times
        track.key_indices = current_indices
        if not changed:
            track.pending_key_values.clear()
            return False

        current_time = float(cmds.currentTime(query=True))
        changed.sort(
            key=lambda item: (
                not frames_equal(item[0], current_time),
                abs(item[0] - current_time),
            )
        )
        track.pending_key_values = {
            key_time: current_value
            for key_time, current_value, _observed_delta in changed
        }
        track.dirty = True
        track.dirty_time = changed[0][0]
        self.dirty_track_ids.add(track.track_id)
        return True

    def _commit_pending_key_changes(self, track):
        current_time = float(cmds.currentTime(query=True))
        changes = []
        for key_time, current_value in track.pending_key_values.items():
            baseline, found = value_at_frame(track.key_values, key_time)
            if not found:
                continue
            if track.is_rotation:
                desired_delta = rotation_delta_from_observation(
                    baseline,
                    track.applied_delta,
                    current_value,
                )
            else:
                desired_delta = float(current_value) - float(baseline)
            changes.append((float(key_time), float(current_value), desired_delta))

        track.pending_key_values.clear()
        if not changes:
            return False

        changes.sort(
            key=lambda item: (
                not frames_equal(item[0], current_time),
                abs(item[0] - current_time),
            )
        )
        desired_delta = changes[0][2]
        delta_to_apply = desired_delta - track.applied_delta
        protected_times = [item[0] for item in changes]
        epsilon = ROTATION_EPSILON if track.is_rotation else VALUE_EPSILON

        applied = False
        if abs(delta_to_apply) > epsilon:
            applied = self._apply_delta_to_track(
                track,
                delta_to_apply,
                protected_times,
            )

        for key_time, current_value, _key_delta in changes:
            track.key_values[float(key_time)] = (
                float(current_value) - float(desired_delta)
            )
        track.applied_delta = desired_delta
        track.last_observed_value = changes[0][1]
        return applied or bool(protected_times)

    def _apply_slider_group(self, plug, changes):
        track = self._track_for_plug(plug)
        if track is None:
            return False

        valid_changes = []
        for change in changes:
            try:
                frame = float(change.get("frame"))
                original_value = change.get("original_value")
                new_value = change.get("new_value")
            except Exception:
                continue
            original_value, ok_original = _as_scalar(original_value)
            new_value, ok_new = _as_scalar(new_value)
            if not ok_original or not ok_new:
                continue
            if not is_finite_number(original_value) or not is_finite_number(new_value):
                continue
            if frame < self.time_range[0] - FRAME_EPSILON or frame > self.time_range[1] + FRAME_EPSILON:
                continue
            desired_delta = float(new_value) - float(original_value)
            if abs(desired_delta) <= VALUE_EPSILON:
                continue
            valid_changes.append((frame, float(original_value), float(new_value), desired_delta))

        if not valid_changes:
            return False

        self._refresh_track_keys(track)
        for frame, original_value, _new_value, _desired_delta in valid_changes:
            track.key_values[float(frame)] = float(original_value)

        desired_delta = self._slider_driver_delta(valid_changes)
        delta_to_apply = desired_delta - track.applied_delta
        protected_times = [item[0] for item in valid_changes]

        if abs(delta_to_apply) <= VALUE_EPSILON:
            track.applied_delta = desired_delta
            return False

        applied = self._apply_delta_to_track(track, delta_to_apply, protected_times)
        track.applied_delta = desired_delta
        return applied or bool(protected_times)

    def _track_for_plug(self, plug):
        node, attr = _split_plug(plug)
        if not node or not attr:
            return None
        return self._track_for_node_attr(node, attr)

    def _slider_driver_delta(self, changes):
        deltas = [item[3] for item in changes]
        first_delta = deltas[0]
        if all(values_equal(delta, first_delta, epsilon=1e-7) for delta in deltas):
            return first_delta

        current_time = float(cmds.currentTime(query=True))
        changes = sorted(changes, key=lambda item: (not frames_equal(item[0], current_time), abs(item[0] - current_time)))
        return changes[0][3]

    def _anchor_current_time_for_selected_tracks(self):
        current_time = float(cmds.currentTime(query=True))
        selected = set(_selected_objects_long())
        selected_uuids = {_query_node_uuid(node) for node in selected}
        for track in self.tracks.values():
            if track.node_uuid not in selected_uuids:
                continue
            if self._has_key_at_time(track, current_time):
                continue
            if value_at_frame(track.evaluated_baselines, current_time)[1]:
                continue
            curve_value = self._evaluate_curve(track, current_time)
            if curve_value is None:
                continue
            track.evaluated_baselines[float(current_time)] = float(curve_value) - float(track.applied_delta)
