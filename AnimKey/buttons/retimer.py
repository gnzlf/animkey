# -*- coding: utf-8 -*-
"""
RETIMER + CURVE CLEANER
=======================
Retimer con limpieza automatica de curvas post-bake.
Modern frameless window design matching AnimKey style.
"""

import maya.cmds as cmds
from AnimKey.mods.uiMod import ContextPopupWindow
from AnimKey.mods import nodeMod
import maya.mel as mel
import maya.OpenMayaUI as omui
import json

from AnimKey.mods.maya_compat import (
    QtCore, QtGui, QtWidgets, wrap_instance as wrapInstance,
)


WINDOW_OBJECT = "AnimKey_Retimer"

# Global window reference
_retimer_window = None

# Tag used to identify retimer nodes in the scene
_RETIMER_TAG = "ANIMKEY_RETIMER"
_RETIMER_CONTAINER = "animkey_retimer"
_RETIMER_ACTIVE_ATTR = "activeRetimer"
_RETIMER_BAKE_LAYER_BASE = "Retimer_Animkey"
_RETIMER_BAKE_LAYER_SUFFIX = "_Animkey"


def get_maya_main_window():
    return wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget)


def _short_name(node):
    return node.rsplit("|", 1)[-1]


def _ensure_retimer_container():
    try:
        return nodeMod.create_animkey_child_container(_RETIMER_CONTAINER)
    except Exception:
        return None


def _parent_under_retimer_container(node):
    if not node or not cmds.objExists(node):
        return
    container = _ensure_retimer_container()
    if not container or not cmds.objExists(container):
        return
    try:
        parent = cmds.listRelatives(node, parent=True, fullPath=False) or []
        if container not in parent:
            cmds.parent(node, container)
    except Exception:
        pass


def _set_active_retimer_name(name):
    container = _ensure_retimer_container()
    if not container or not cmds.objExists(container):
        return
    try:
        if not cmds.attributeQuery(_RETIMER_ACTIVE_ATTR, node=container, exists=True):
            cmds.addAttr(container, ln=_RETIMER_ACTIVE_ATTR, dt="string")
        cmds.setAttr(container + "." + _RETIMER_ACTIVE_ATTR, name or "", type="string")
    except Exception:
        pass


def _get_active_retimer_name():
    container = _ensure_retimer_container()
    if not container or not cmds.objExists(container):
        return ""
    try:
        if cmds.attributeQuery(_RETIMER_ACTIVE_ATTR, node=container, exists=True):
            return cmds.getAttr(container + "." + _RETIMER_ACTIVE_ATTR) or ""
    except Exception:
        pass
    return ""


def _retimer_anim_layer_name():
    if not cmds.objExists(_RETIMER_BAKE_LAYER_BASE):
        return _RETIMER_BAKE_LAYER_BASE
    index = 2
    while True:
        name = "Retimer{:02d}{}".format(index, _RETIMER_BAKE_LAYER_SUFFIX)
        if not cmds.objExists(name):
            return name
        index += 1


def _shortest_angle_delta(final_value, base_value, previous_delta=None):
    delta = (final_value - base_value + 180.0) % 360.0 - 180.0
    if previous_delta is not None:
        while delta - previous_delta > 180.0:
            delta -= 360.0
        while delta - previous_delta < -180.0:
            delta += 360.0
    return delta


# ═══════════════════════════════════════════════════════════════════════════════
#                           CURVE CLEANER
# ═══════════════════════════════════════════════════════════════════════════════

class CurveCleaner(object):
    """
    Remove redundant keyframes from animation curves while preserving
    the overall shape within a user-defined deviation tolerance.
    """

    def clean(self, objects, max_deviation=5.0, increase_fidelity=False):
        """
        Clean animation curves on the given objects.

        Args:
            objects (list): Maya node names.
            max_deviation (float): Max allowed value deviation in percent
                                   of the channel's value range.
            increase_fidelity (bool): Preserve extra keys where removing them
                                      would exceed the tolerance.

        Returns:
            dict with 'total', 'removed', 'percent' keys, or None.
        """
        if not objects:
            return None

        total_before = 0
        total_after = 0

        for obj in objects:
            if not cmds.objExists(obj):
                continue

            anim_curves = cmds.listConnections(obj, type='animCurve') or []
            seen = set()

            for curve in anim_curves:
                if curve in seen:
                    continue
                seen.add(curve)

                keys = cmds.keyframe(curve, query=True, timeChange=True) or []
                if len(keys) < 3:
                    total_before += len(keys)
                    total_after += len(keys)
                    continue

                values = cmds.keyframe(curve, query=True, valueChange=True) or []
                total_before += len(keys)

                val_range = max(values) - min(values) if values else 1.0
                if val_range < 0.0001:
                    val_range = 1.0

                threshold = val_range * (max_deviation / 100.0)
                if increase_fidelity:
                    keep_indices = self._fidelity_indices(keys, values, threshold)
                    keys_to_remove = [
                        keys[i] for i in range(1, len(keys) - 1)
                        if i not in keep_indices
                    ]
                else:
                    keys_to_remove = []
                    for i in range(1, len(keys) - 1):
                        t_prev = keys[i - 1]
                        t_curr = keys[i]
                        t_next = keys[i + 1]

                        v_prev = values[i - 1]
                        v_curr = values[i]
                        v_next = values[i + 1]

                        if abs(t_next - t_prev) < 0.0001:
                            continue

                        ratio = (t_curr - t_prev) / (t_next - t_prev)
                        v_interp = v_prev + (v_next - v_prev) * ratio

                        if abs(v_curr - v_interp) < threshold:
                            keys_to_remove.append(t_curr)

                for t in keys_to_remove:
                    try:
                        cmds.cutKey(curve, time=(t, t), option='keys')
                    except Exception:
                        pass

                remaining = cmds.keyframe(curve, query=True, timeChange=True) or []
                total_after += len(remaining)

        removed = total_before - total_after
        percent = (removed / total_before * 100.0) if total_before > 0 else 0.0

        return {
            'total': total_before,
            'final': total_after,
            'removed': removed,
            'percent': percent,
        }

    def _fidelity_indices(self, keys, values, threshold):
        """Return key indices needed to keep the sampled curve within tolerance."""
        if len(keys) <= 2:
            return set(range(len(keys)))

        keep = {0, len(keys) - 1}
        stack = [(0, len(keys) - 1)]

        while stack:
            start_idx, end_idx = stack.pop()
            if end_idx - start_idx <= 1:
                continue

            t_start = keys[start_idx]
            t_end = keys[end_idx]
            v_start = values[start_idx]
            v_end = values[end_idx]
            if abs(t_end - t_start) < 0.0001:
                continue

            worst_idx = None
            worst_error = 0.0
            for i in range(start_idx + 1, end_idx):
                ratio = (keys[i] - t_start) / (t_end - t_start)
                interp = v_start + (v_end - v_start) * ratio
                error = abs(values[i] - interp)
                if error > worst_error:
                    worst_error = error
                    worst_idx = i

            if worst_idx is not None and worst_error > threshold:
                keep.add(worst_idx)
                stack.append((start_idx, worst_idx))
                stack.append((worst_idx, end_idx))

        return keep


# ═══════════════════════════════════════════════════════════════════════════════
#                           RETIMER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class Retimer(object):
    """
    A named retimer that stores a time-warp curve in the Maya scene.

    The curve maps *original frame* → *new frame*.  By default it is a
    straight 1:1 line from the playback range start to end.  The user
    can reshape it in the Graph Editor to speed up / slow down sections,
    and then "bake" the result onto the controlled objects.
    """

    PREFIX = "ANIMKEY_RT_"

    def __init__(self, name):
        self.name = name
        self.curve = self.PREFIX + name
        self._objects_attr = self.curve + ".retimerObjects"
        self._preview_data_attr = self.curve + ".retimerPreviewData"
        self._preview_expr = self.curve + "_EXPR"

    # ── Static helpers ────────────────────────────────────────────────────

    @staticmethod
    def find_all():
        """Return a list of retimer names present in the scene."""
        nodes = []

        # Current retimers are transform nodes tagged with retimerTag.
        for attr in cmds.ls("*.retimerTag") or []:
            node = attr.rsplit(".", 1)[0]
            if not cmds.objExists(node):
                continue
            try:
                if cmds.getAttr(node + ".retimerTag") == _RETIMER_TAG:
                    nodes.append(node)
            except Exception:
                pass

        # Legacy/un-tagged retimers still follow the ANIMKEY_RT_ prefix.
        nodes.extend(cmds.ls(Retimer.PREFIX + "*", type="transform") or [])

        names = []
        seen = set()
        for n in nodes:
            short = _short_name(n)
            if short.startswith(Retimer.PREFIX):
                name = short[len(Retimer.PREFIX):]
                if name not in seen:
                    names.append(name)
                    seen.add(name)
        return sorted(names)

    # ── Curve management ──────────────────────────────────────────────────

    def create_curve(self):
        """Create or repair the time-warp curve holder in the Maya scene."""
        start = int(cmds.playbackOptions(query=True, minTime=True))
        end = int(cmds.playbackOptions(query=True, maxTime=True))

        # Create a transform to host the custom attribute
        if not cmds.objExists(self.curve):
            # We need a transform + a float attr to drive with an animCurve
            node = cmds.createNode("transform", name=self.curve)
            cmds.setAttr(node + ".visibility", 0)
            try:
                cmds.setAttr(node + ".hiddenInOutliner", 0)
            except Exception:
                pass
        else:
            node = self.curve
            try:
                cmds.setAttr(node + ".visibility", 0)
                cmds.setAttr(node + ".hiddenInOutliner", 0)
            except Exception:
                pass

        if not cmds.attributeQuery("timeWarp", node=self.curve, exists=True):
            cmds.addAttr(self.curve, ln="timeWarp", at="float", keyable=True)
        else:
            try:
                cmds.setAttr(self.curve + ".timeWarp", keyable=True, channelBox=True)
            except Exception:
                pass

        if not cmds.attributeQuery("retimerObjects", node=self.curve, exists=True):
            cmds.addAttr(self.curve, ln="retimerObjects", dt="string")
            cmds.setAttr(self._objects_attr, "[]", type="string")

        if not cmds.attributeQuery("retimerPreviewData", node=self.curve, exists=True):
            cmds.addAttr(self.curve, ln="retimerPreviewData", dt="string")
            cmds.setAttr(self._preview_data_attr, "[]", type="string")

        if not cmds.attributeQuery("retimerTag", node=self.curve, exists=True):
            cmds.addAttr(self.curve, ln="retimerTag", dt="string")
        cmds.setAttr(self.curve + ".retimerTag", _RETIMER_TAG, type="string")

        _parent_under_retimer_container(self.curve)

        time_curve = self._ensure_time_curve(start, end)
        if time_curve:
            cmds.keyTangent(time_curve, inTangentType="linear", outTangentType="linear")

    def _time_curve_node(self, rename=False):
        """Return the animCurveTT used as the live time-warp driver."""
        wanted = self.curve + "_timeWarpCurve"
        if cmds.objExists(wanted) and cmds.nodeType(wanted).startswith("animCurve"):
            return wanted

        attr = self.curve + ".timeWarp"
        curves = cmds.listConnections(
            attr,
            source=True,
            destination=False,
            type="animCurve"
        ) or []
        if not curves:
            return None

        curve = curves[0]
        if rename:
            if _short_name(curve) != wanted and not cmds.objExists(wanted):
                try:
                    curve = cmds.rename(curve, wanted)
                except Exception:
                    pass
        return curve

    def _ensure_time_curve(self, start=None, end=None):
        """Create/repair the animCurveTT that behaves like a Maya timeWarp."""
        start = int(cmds.playbackOptions(query=True, minTime=True)) if start is None else int(start)
        end = int(cmds.playbackOptions(query=True, maxTime=True)) if end is None else int(end)
        wanted = self.curve + "_timeWarpCurve"
        curve = self._time_curve_node(rename=True)

        if not curve or not cmds.objExists(curve) or cmds.nodeType(curve) != "animCurveTT":
            old_curve = curve if curve and cmds.objExists(curve) else None
            old_keys = []
            old_values = []
            if old_curve:
                try:
                    old_keys = cmds.keyframe(old_curve, query=True, timeChange=True) or []
                    old_values = cmds.keyframe(old_curve, query=True, valueChange=True) or []
                except Exception:
                    old_keys, old_values = [], []
            if cmds.objExists(wanted) and cmds.nodeType(wanted) == "animCurveTT":
                curve = wanted
            else:
                curve = cmds.createNode("animCurveTT", name=wanted)
            if not cmds.keyframe(curve, query=True, timeChange=True):
                if old_keys and old_values:
                    for frame, value in zip(old_keys, old_values):
                        cmds.setKeyframe(curve, time=frame, value=value)
                else:
                    cmds.setKeyframe(curve, time=start, value=start)
                    cmds.setKeyframe(curve, time=end, value=end)

        input_sources = cmds.listConnections(curve + ".input", source=True, destination=False, plugs=True) or []
        if "time1.outTime" not in input_sources:
            for source in input_sources:
                try:
                    cmds.disconnectAttr(source, curve + ".input")
                except Exception:
                    pass
            try:
                cmds.connectAttr("time1.outTime", curve + ".input", force=True)
            except Exception:
                pass

        attr = self.curve + ".timeWarp"
        if cmds.objExists(attr):
            try:
                current_sources = cmds.listConnections(attr, source=True, destination=False, plugs=True) or []
                if curve + ".output" not in current_sources:
                    for source in current_sources:
                        try:
                            cmds.disconnectAttr(source, attr)
                        except Exception:
                            pass
                    cmds.connectAttr(curve + ".output", attr, force=True)
            except Exception:
                pass

        return curve

    def _time_output_attr(self):
        curve = self._ensure_time_curve()
        return curve + ".output" if curve and cmds.objExists(curve + ".output") else self.curve + ".timeWarp"

    def select_curve(self):
        """Select the retimer curve so the user can edit it in the Graph Editor."""
        self.create_curve()
        attr = self.curve + ".timeWarp"
        if not cmds.objExists(attr):
            return
        anim_curve = self._time_curve_node(rename=True)

        # Select the real animCurve, not the transform holder, so new keys go
        # onto the retimer curve and not onto the Outliner group/host node.
        cmds.select(clear=True)
        if anim_curve and cmds.objExists(anim_curve):
            cmds.select(anim_curve, replace=True)
        try:
            cmds.selectKey(anim_curve or attr, replace=True)
        except Exception:
            pass
        try:
            mel.eval('GraphEditor')
        except Exception:
            pass

    def delete(self):
        """Delete this retimer and its expression/nodes."""
        self.preview_off()
        time_curve = self._time_curve_node(rename=False)
        if time_curve and cmds.objExists(time_curve):
            try:
                cmds.delete(time_curve)
            except Exception:
                pass
        if cmds.objExists(self.curve):
            try:
                cmds.delete(self.curve)
            except Exception:
                pass

    # ── Object list ───────────────────────────────────────────────────────

    @property
    def objects(self):
        """Return the list of objects controlled by this retimer."""
        if not cmds.objExists(self.curve):
            return []
        if not cmds.attributeQuery("retimerObjects", node=self.curve, exists=True):
            return []
        try:
            raw = cmds.getAttr(self._objects_attr) or "[]"
            objs = json.loads(raw)
            return [o for o in objs if cmds.objExists(o)]
        except Exception:
            return []

    @objects.setter
    def objects(self, obj_list):
        if not cmds.objExists(self.curve):
            return
        cmds.setAttr(self._objects_attr, json.dumps(obj_list), type="string")

    def add_objects(self):
        """Add currently selected objects to this retimer. Returns added list."""
        sel = cmds.ls(selection=True, long=True) or []
        if not sel:
            cmds.warning("Select objects to add")
            return []
        current = self.objects
        added = []
        for obj in sel:
            if obj not in current:
                current.append(obj)
                added.append(obj)
        self.objects = current
        return added

    def remove_objects(self, objs):
        """Remove specific objects from this retimer."""
        current = self.objects
        self.objects = [o for o in current if o not in objs]

    # ── Preview ───────────────────────────────────────────────────────────

    def is_preview_on(self):
        """Check if preview has live retime connections."""
        if not cmds.objExists(self.curve):
            return False
        if not cmds.attributeQuery("retimerPreviewData", node=self.curve, exists=True):
            return False
        try:
            data = json.loads(cmds.getAttr(self._preview_data_attr) or "[]")
            return bool(data)
        except Exception:
            return False

    def _animated_curves(self):
        """Return unique animCurves driving controlled objects, including layers."""
        retimer_curve = self._time_curve_node(rename=True)
        obj_long_names = set()
        obj_short_names = set()
        for obj in self.objects:
            if not cmds.objExists(obj):
                continue
            obj_short_names.add(_short_name(obj))
            obj_long_names.update(cmds.ls(obj, long=True) or [])

        def node_is_controlled(node):
            if not node:
                return False
            if _short_name(node) in obj_short_names:
                return True
            return bool(set(cmds.ls(node, long=True) or []) & obj_long_names)

        def curve_drives_controlled_object(curve):
            visited = set()
            stack = [curve + ".output"]
            while stack:
                plug = stack.pop()
                if plug in visited:
                    continue
                visited.add(plug)

                destinations = cmds.listConnections(
                    plug,
                    source=False,
                    destination=True,
                    plugs=True
                ) or []
                for dest in destinations:
                    dest_node = dest.split(".", 1)[0]
                    if node_is_controlled(dest_node):
                        return True
                    try:
                        node_type = cmds.nodeType(dest_node)
                    except Exception:
                        node_type = ""
                    if (
                        node_type.startswith("animBlendNode")
                        or node_type in ("unitConversion", "pairBlend", "blendWeighted")
                    ):
                        try:
                            connections = cmds.listConnections(
                                dest_node,
                                source=False,
                                destination=True,
                                plugs=True,
                                connections=True
                            ) or []
                            for i in range(0, len(connections), 2):
                                source_plug = connections[i]
                                if source_plug.startswith(dest_node + "."):
                                    stack.append(source_plug)
                        except Exception:
                            pass
                        for out_attr in ("output", "outputX", "outputY", "outputZ"):
                            out_plug = dest_node + "." + out_attr
                            if cmds.objExists(out_plug):
                                stack.append(out_plug)
            return False

        curves = []
        seen = set()
        candidates = []
        for obj in self.objects:
            if not cmds.objExists(obj):
                continue
            try:
                candidates.extend(cmds.keyframe(obj, query=True, name=True) or [])
            except Exception:
                pass
            try:
                candidates.extend(cmds.listConnections(obj, type="animCurve") or [])
            except Exception:
                pass
            try:
                history = cmds.listHistory(obj, pruneDagObjects=True) or []
                candidates.extend([
                    node for node in history
                    if cmds.objExists(node) and cmds.nodeType(node).startswith("animCurve")
                ])
            except Exception:
                pass

        for layer in cmds.ls(type="animLayer") or []:
            try:
                candidates.extend(cmds.animLayer(layer, query=True, animCurves=True) or [])
            except Exception:
                pass

        for curve_type in ("animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT"):
            candidates.extend(cmds.ls(type=curve_type) or [])

        for curve in candidates:
            if not curve or not cmds.objExists(curve):
                continue
            if retimer_curve and curve == retimer_curve:
                continue
            if not cmds.nodeType(curve).startswith("animCurve"):
                continue
            curve = _short_name(curve)
            if curve in seen:
                continue
            if not curve_drives_controlled_object(curve):
                continue
            seen.add(curve)
            curves.append(curve)
        return curves

    def _retime_time_map(self, start, end):
        time_curve = self._ensure_time_curve(start, end)
        time_map = {}
        for frame in range(int(start), int(end) + 1):
            warped = cmds.keyframe(time_curve, query=True, time=(frame, frame),
                                   valueChange=True, eval=True) if time_curve else None
            time_map[frame] = warped[0] if warped else float(frame)
        return time_map

    def _keyable_scalar_attrs(self, obj):
        attrs = []
        for attr in cmds.listAttr(obj, keyable=True) or []:
            plug = obj + "." + attr
            if not cmds.objExists(plug):
                continue
            try:
                attr_type = cmds.getAttr(plug, type=True)
                if attr_type in ("bool", "enum", "string"):
                    continue
                value = cmds.getAttr(plug)
            except Exception:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                attrs.append(attr)
        return attrs

    def _sample_object_attrs(self, objects, frame_map):
        return self._sample_object_attrs_for_bake(objects, frame_map)

    def _sample_object_attrs_for_bake(self, objects, frame_map, attrs_by_obj=None):
        samples = {}
        current_time = cmds.currentTime(query=True)
        try:
            for out_frame, eval_frame in sorted(frame_map.items()):
                cmds.currentTime(eval_frame, edit=True)
                for obj in objects:
                    if not cmds.objExists(obj):
                        continue
                    obj_samples = samples.setdefault(obj, {})
                    attrs = attrs_by_obj.get(obj) if attrs_by_obj else self._keyable_scalar_attrs(obj)
                    for attr in attrs or []:
                        plug = obj + "." + attr
                        try:
                            obj_samples.setdefault(attr, []).append((out_frame, cmds.getAttr(plug)))
                        except Exception:
                            pass
        finally:
            cmds.currentTime(current_time, edit=True)
        return samples

    def _animated_scalar_attr_map(self, objects):
        attr_map = {}
        for obj in objects:
            if not cmds.objExists(obj):
                continue
            attrs = []
            for attr in self._keyable_scalar_attrs(obj):
                plug = obj + "." + attr
                try:
                    if cmds.keyframe(plug, query=True, name=True):
                        attrs.append(attr)
                except Exception:
                    pass
            if attrs:
                attr_map[obj] = attrs
        return attr_map

    def _create_retimer_bake_layer(self, objects, sampled_attrs):
        layer = cmds.animLayer(
            _retimer_anim_layer_name(),
            override=True,
            passthrough=False
        )

        for existing_layer in cmds.ls(type="animLayer") or []:
            try:
                cmds.animLayer(existing_layer, edit=True, selected=False, preferred=False)
            except Exception:
                pass

        cmds.animLayer(layer, edit=True, selected=True, preferred=True, mute=False, weight=1.0)

        for obj in objects:
            if not cmds.objExists(obj):
                continue
            for attr in sampled_attrs.get(obj, []):
                plug = obj + "." + attr
                if not cmds.objExists(plug):
                    continue
                try:
                    cmds.animLayer(layer, edit=True, attribute=plug)
                except Exception:
                    pass

        return layer

    def bake_to_anim_layer(self):
        """Bake the retimer result as additive deltas on a new animLayer."""
        objs = self.objects
        if not objs:
            cmds.warning("No objects to bake")
            return None

        time_curve = self._ensure_time_curve()
        if not time_curve or not cmds.objExists(time_curve):
            cmds.warning("Retimer curve not found")
            return None

        start = int(cmds.playbackOptions(query=True, minTime=True))
        end = int(cmds.playbackOptions(query=True, maxTime=True))
        frame_map = {frame: float(frame) for frame in range(start, end + 1)}
        guard_frames = [start - 1, end + 1]
        guard_map = {frame: float(frame) for frame in guard_frames}

        if self.is_preview_on():
            self.preview_off()

        base_frame_map = frame_map.copy()
        base_frame_map.update(guard_map)
        base_samples = self._sample_object_attrs(objs, base_frame_map)

        final_samples = {}
        try:
            self.preview_on()
            if not self.is_preview_on():
                cmds.warning("Preview could not be enabled for layer bake")
                return None
            final_samples = self._sample_object_attrs(objs, frame_map)
        finally:
            if self.is_preview_on():
                self.preview_off()

        sampled_attrs = {}
        for obj, attr_data in final_samples.items():
            sampled_attrs[obj] = list(attr_data.keys())

        layer = self._create_retimer_bake_layer(objs, sampled_attrs)
        keyed = 0

        for obj, obj_samples in final_samples.items():
            if not cmds.objExists(obj):
                continue
            for attr_name, values in obj_samples.items():
                base_values = dict(base_samples.get(obj, {}).get(attr_name, []))
                if not base_values:
                    continue

                for guard_frame in guard_frames:
                    if guard_frame not in base_values:
                        continue
                    try:
                        cmds.setKeyframe(
                            obj,
                            attribute=attr_name,
                            time=(guard_frame, guard_frame),
                            animLayer=layer,
                            value=base_values[guard_frame]
                        )
                    except Exception:
                        pass

                for frame, final_value in values:
                    if frame not in base_values:
                        continue

                    try:
                        cmds.setKeyframe(
                            obj,
                            attribute=attr_name,
                            time=(frame, frame),
                            animLayer=layer,
                            value=final_value
                        )
                        keyed += 1
                    except Exception:
                        pass

        try:
            cmds.animLayer(forceUIRefresh=True)
        except Exception:
            pass

        if not keyed:
            try:
                cmds.delete(layer)
            except Exception:
                pass
            cmds.warning("No retimer layer keys were created")
            return None

        # The new animLayer now holds the retimer result. Remove the retimer
        # curve/holder so the scene stays looking the same without preview.
        self.delete()

        return {
            "layer": layer,
            "keys_final": keyed,
            "keys_removed": 0,
        }

    def preview_on(self):
        """Enable live retime preview by driving object animCurves with timeWarp."""
        if self.is_preview_on():
            return

        self.create_curve()
        objs = self.objects
        if not objs:
            cmds.warning("No objects to preview")
            return

        attr = self._time_output_attr()
        preview_data = []
        connected = 0

        for anim_curve in self._animated_curves():
            input_attr = anim_curve + ".input"
            if not cmds.objExists(input_attr):
                continue

            sources = cmds.listConnections(
                input_attr,
                source=True,
                destination=False,
                plugs=True
            ) or []

            item = {
                "curve": anim_curve,
                "input": input_attr,
                "sources": sources,
            }

            try:
                for source in sources:
                    try:
                        cmds.disconnectAttr(source, input_attr)
                    except Exception:
                        pass
                cmds.connectAttr(attr, input_attr, force=True)
                preview_data.append(item)
                connected += 1
            except Exception as e:
                cmds.warning(f"Preview connection skipped for {anim_curve}: {e}")

        if not connected:
            cmds.warning("No animated curves found on retimer objects")
            return

        try:
            cmds.setAttr(self._preview_data_attr, json.dumps(preview_data), type="string")
        except Exception:
            pass
        cmds.refresh(force=True)

    def preview_off(self):
        """Restore object animCurves to their original time connections."""
        data = []
        if cmds.objExists(self.curve) and cmds.attributeQuery("retimerPreviewData", node=self.curve, exists=True):
            try:
                data = json.loads(cmds.getAttr(self._preview_data_attr) or "[]")
            except Exception:
                data = []

        for item in data:
            anim_curve = item.get("curve")
            input_attr = item.get("input") or (anim_curve + ".input" if anim_curve else "")
            if not input_attr or not cmds.objExists(input_attr):
                continue

            current_sources = cmds.listConnections(
                input_attr,
                source=True,
                destination=False,
                plugs=True
            ) or []
            for source in current_sources:
                try:
                    cmds.disconnectAttr(source, input_attr)
                except Exception:
                    pass

            sources = item.get("sources") or ["time1.outTime"]
            for source in sources:
                if not cmds.objExists(source):
                    continue
                try:
                    cmds.connectAttr(source, input_attr, force=True)
                    break
                except Exception:
                    pass

        if cmds.objExists(self.curve) and cmds.attributeQuery("retimerPreviewData", node=self.curve, exists=True):
            try:
                cmds.setAttr(self._preview_data_attr, "[]", type="string")
            except Exception:
                pass

        if cmds.objExists(self._preview_expr):
            try:
                cmds.delete(self._preview_expr)
            except Exception:
                pass
        cmds.refresh(force=True)

    # ── Bake ──────────────────────────────────────────────────────────────

    def _retimer_source_plugs(self):
        plugs = set()
        if cmds.objExists(self.curve + ".timeWarp"):
            plugs.add(self.curve + ".timeWarp")
        time_curve = self._time_curve_node(rename=False)
        if time_curve and cmds.objExists(time_curve + ".output"):
            plugs.add(time_curve + ".output")
        return plugs

    def _is_retimer_source(self, source_plug):
        if not source_plug:
            return False
        if source_plug in self._retimer_source_plugs():
            return True
        source_node = source_plug.split(".", 1)[0]
        time_curve = self._time_curve_node(rename=False)
        return source_node in {self.curve, time_curve}

    def _force_preview_off(self):
        """Remove any live retimer input left on controlled animCurves."""
        self.preview_off()
        for anim_curve in self._animated_curves():
            input_attr = anim_curve + ".input"
            if not cmds.objExists(input_attr):
                continue

            sources = cmds.listConnections(
                input_attr,
                source=True,
                destination=False,
                plugs=True
            ) or []

            removed_retimer_source = False
            for source in sources:
                if not self._is_retimer_source(source):
                    continue
                try:
                    cmds.disconnectAttr(source, input_attr)
                    removed_retimer_source = True
                except Exception:
                    pass

            remaining_sources = cmds.listConnections(
                input_attr,
                source=True,
                destination=False,
                plugs=True
            ) or []
            if removed_retimer_source and not remaining_sources and cmds.objExists("time1.outTime"):
                try:
                    cmds.connectAttr("time1.outTime", input_attr, force=True)
                except Exception:
                    pass

        if cmds.objExists(self.curve) and cmds.attributeQuery("retimerPreviewData", node=self.curve, exists=True):
            try:
                cmds.setAttr(self._preview_data_attr, "[]", type="string")
            except Exception:
                pass
        cmds.refresh(force=True)

    def bake_full(self, sample_by=1, clean_curves=True, max_deviation=5.0, increase_fidelity=True):
        """
        Bake the retimed animation onto the controlled objects.

        Process:
        1. Read the time-warp curve to build an original→new time mapping.
        2. For each object/channel, evaluate at the warped times and set keys.
        3. Optionally smart-clean the resulting curves within tolerance.

        Returns dict with bake statistics.
        """
        objs = self.objects
        if not objs:
            cmds.warning("No objects to bake")
            return None

        time_curve = self._ensure_time_curve()
        if not time_curve or not cmds.objExists(time_curve):
            cmds.warning("Retimer curve not found")
            return None

        start = int(cmds.playbackOptions(query=True, minTime=True))
        end = int(cmds.playbackOptions(query=True, maxTime=True))
        step = max(1, int(sample_by or 1))
        bake_frames = list(range(start, end + 1, step))
        if bake_frames[-1] != end:
            bake_frames.append(end)
        frame_map = {frame: float(frame) for frame in bake_frames}

        # Sample the live retimed result, then bake it back as normal keys.
        cmds.undoInfo(openChunk=True)
        total_keys_before = 0
        total_keys_after = 0
        keys_removed = 0
        keyed = 0

        try:
            self._force_preview_off()
            anim_curves = self._animated_curves()
            if not anim_curves:
                cmds.warning("No animated curves found on retimer objects")
                return None

            attr_map = self._animated_scalar_attr_map(objs)
            if not attr_map:
                cmds.warning("No animated scalar attributes found on retimer objects")
                return None

            for curve in anim_curves:
                if cmds.objExists(curve):
                    total_keys_before += len(cmds.keyframe(curve, query=True, timeChange=True) or [])

            final_samples = {}
            try:
                self.preview_on()
                if not self.is_preview_on():
                    cmds.warning("Retimer preview could not be enabled for bake")
                    return None
                final_samples = self._sample_object_attrs_for_bake(
                    objs,
                    frame_map,
                    attrs_by_obj=attr_map
                )
            finally:
                self._force_preview_off()

            if not final_samples:
                cmds.warning("No retimed samples were created")
                return None

            for obj, obj_samples in final_samples.items():
                if not cmds.objExists(obj):
                    continue
                for attr_name, values in obj_samples.items():
                    plug = obj + "." + attr_name
                    if not cmds.objExists(plug):
                        continue
                    try:
                        if cmds.getAttr(plug, lock=True) or not cmds.getAttr(plug, settable=True):
                            continue
                    except Exception:
                        continue
                    try:
                        cmds.cutKey(obj, attribute=attr_name, time=(start, end), option='keys')
                    except Exception:
                        pass

                    for frame, value in values:
                        try:
                            cmds.setKeyframe(obj, attribute=attr_name, time=(frame, frame), value=value)
                            keyed += 1
                        except Exception:
                            pass

                    try:
                        cmds.keyTangent(plug, edit=True, time=(start, end), inTangentType="auto", outTangentType="auto")
                    except Exception:
                        pass

            for anim_curve in self._animated_curves():
                try:
                    input_attr = anim_curve + ".input"
                    sources = cmds.listConnections(input_attr, source=True, destination=False, plugs=True) or []
                    for source in sources:
                        if self._is_retimer_source(source):
                            cmds.disconnectAttr(source, input_attr)
                    if not (cmds.listConnections(input_attr, source=True, destination=False, plugs=True) or []):
                        cmds.connectAttr("time1.outTime", input_attr, force=True)
                except Exception:
                    pass

            # Clean curves if requested
            if clean_curves:
                cleaner = CurveCleaner()
                result = cleaner.clean(
                    objs,
                    max_deviation,
                    increase_fidelity=increase_fidelity
                )
                if result:
                    keys_removed = result.get('removed', 0)

            # Count final keys once across every base/layer curve we retimed.
            for curve in self._animated_curves():
                if cmds.objExists(curve):
                    total_keys_after += len(cmds.keyframe(curve, query=True, timeChange=True) or [])

            self.delete()

        finally:
            cmds.undoInfo(closeChunk=True)

        return {
            'keys_before': total_keys_before,
            'keys_final': total_keys_after,
            'keys_removed': keys_removed,
            'keys_set': keyed,
            'retimer_deleted': True,
        }


class RetimerWindow(ContextPopupWindow):
    """Retimer Window with modern frameless design"""
    
    def __init__(self, anchor_button=None, parent=None):
        super().__init__(anchor_button=anchor_button, parent=parent)
        
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle('Retimer')
        self.setFixedSize(380, 660)
        
        # Frameless window
        
        
        
        self._base_opacity = 0.5
        self._hover_opacity = 1.0
        
        self.rt = None
        
        self._setup_ui()
        self.position_window()
        
        # Initial load
        QtCore.QTimer.singleShot(100, self._refresh)
        
        # Start with base opacity
        self.setWindowOpacity(self._base_opacity)
    
    def _setup_ui(self):
        """Setup the UI elements"""
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, self._tail_height + 1)
        main_layout.setSpacing(0)
        main_layout.setSpacing(0)
        
        # Container frame with rounded corners
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet("""
            QFrame#mainContainer {
                background-color: transparent;
                border: none;
            }
        """)
        
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # Header
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(10, 10, 10, 10)
        self.title_label = QtWidgets.QLabel("Retimer")
        self.title_label.setStyleSheet("color: #AAA; font-size: 11px; font-weight: 500; border: none;")
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
        container_layout.addLayout(header)
        
        # Content area
        content = QtWidgets.QWidget()
        content.setStyleSheet("background-color: #3a3a3a;")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(15, 12, 15, 15)
        content_layout.setSpacing(10)
        
        # ─────────────────────────────────────────────────────────────────────
        # RETIMER SELECTOR ROW
        # ─────────────────────────────────────────────────────────────────────
        selector_widget = QtWidgets.QWidget()
        selector_layout = QtWidgets.QHBoxLayout(selector_widget)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(6)
        
        # Dropdown for retimer selection
        self.retimer_combo = QtWidgets.QComboBox()
        self.retimer_combo.setMinimumWidth(140)
        self.retimer_combo.setStyleSheet("""
            QComboBox {
                background-color: #4d4d4d;
                border: 1px solid #666666;
                border-radius: 6px;
                padding: 8px 12px;
                color: #FFF;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; }
            QComboBox QAbstractItemView {
                background-color: #4d4d4d;
                selection-background-color: #3498DB;
                border: 1px solid #666666;
            }
        """)
        self.retimer_combo.currentTextChanged.connect(self._on_select)
        selector_layout.addWidget(self.retimer_combo)
        
        # Refresh button
        refresh_btn = QtWidgets.QPushButton("⟳")
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setCursor(QtCore.Qt.PointingHandCursor)
        refresh_btn.setToolTip("Refresh retimer list")
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #4d4d4d;
                color: #AAA;
                font-size: 14px;
                border: 1px solid #666666;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #3498DB; color: #FFF; }
        """)
        refresh_btn.clicked.connect(self._refresh)
        selector_layout.addWidget(refresh_btn)
        
        # Delete button
        delete_btn = QtWidgets.QPushButton("✕")
        delete_btn.setFixedSize(32, 32)
        delete_btn.setCursor(QtCore.Qt.PointingHandCursor)
        delete_btn.setToolTip("Delete current retimer")
        delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d2d2d;
                color: #ff6b6b;
                font-size: 14px;
                border: 1px solid #5a3a3a;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #E74C3C; color: #FFF; }
        """)
        delete_btn.clicked.connect(self._delete_retimer)
        selector_layout.addWidget(delete_btn)
        
        # Edit curve button
        edit_btn = QtWidgets.QPushButton("Edit")
        edit_btn.setFixedSize(50, 32)
        edit_btn.setCursor(QtCore.Qt.PointingHandCursor)
        edit_btn.setToolTip("Open curve in Graph Editor")
        edit_btn.setStyleSheet("""
            QPushButton {
                background-color: #4d4d4d;
                color: #AAA;
                font-size: 11px;
                border: 1px solid #666666;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #d08770; color: #FFF; }
        """)
        edit_btn.clicked.connect(self._edit_curve)
        selector_layout.addWidget(edit_btn)
        
        content_layout.addWidget(selector_widget)
        
        # ─────────────────────────────────────────────────────────────────────
        # NEW RETIMER ROW
        # ─────────────────────────────────────────────────────────────────────
        new_widget = QtWidgets.QWidget()
        new_layout = QtWidgets.QHBoxLayout(new_widget)
        new_layout.setContentsMargins(0, 0, 0, 0)
        new_layout.setSpacing(6)
        
        self.new_name_input = QtWidgets.QLineEdit()
        self.new_name_input.setPlaceholderText("new_retimer_name")
        self.new_name_input.setStyleSheet("""
            QLineEdit {
                background-color: #444444;
                color: #FFF;
                border: 1px solid #666666;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 11px;
            }
            QLineEdit:focus { border-color: #3498DB; }
        """)
        new_layout.addWidget(self.new_name_input)
        
        create_btn = QtWidgets.QPushButton("Create New")
        create_btn.setFixedHeight(32)
        create_btn.setCursor(QtCore.Qt.PointingHandCursor)
        create_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d4d3d;
                color: #a3be8c;
                font-size: 11px;
                font-weight: bold;
                border: 1px solid #5a7a5a;
                border-radius: 6px;
                padding: 0 16px;
            }
            QPushButton:hover { background-color: #3d5d4d; border-color: #a3be8c; }
        """)
        create_btn.clicked.connect(self._create_new)
        new_layout.addWidget(create_btn)
        
        content_layout.addWidget(new_widget)
        
        # Separator
        line = QtWidgets.QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #666666;")
        content_layout.addWidget(line)
        
        # ─────────────────────────────────────────────────────────────────────
        # OBJECTS SECTION
        # ─────────────────────────────────────────────────────────────────────
        objects_label = QtWidgets.QLabel("Objects:")
        objects_label.setStyleSheet("color: #AAA; font-size: 11px;")
        content_layout.addWidget(objects_label)
        
        self.obj_list = QtWidgets.QListWidget()
        self.obj_list.setMinimumHeight(100)
        self.obj_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.obj_list.setStyleSheet("""
            QListWidget {
                background-color: #444444;
                color: #FFF;
                border: 1px solid #666666;
                border-radius: 6px;
                padding: 6px;
                font-size: 11px;
            }
            QListWidget::item { padding: 4px 8px; }
            QListWidget::item:selected { background-color: #3498DB; }
            QListWidget::item:hover { background-color: #5a5a5a; }
        """)
        content_layout.addWidget(self.obj_list)
        
        # Object buttons row
        obj_btn_widget = QtWidgets.QWidget()
        obj_btn_layout = QtWidgets.QHBoxLayout(obj_btn_widget)
        obj_btn_layout.setContentsMargins(0, 0, 0, 0)
        obj_btn_layout.setSpacing(6)
        
        add_btn = QtWidgets.QPushButton("Add Selected")
        add_btn.setFixedHeight(32)
        add_btn.setCursor(QtCore.Qt.PointingHandCursor)
        add_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d4d3d;
                color: #a3be8c;
                font-size: 11px;
                font-weight: bold;
                border: 1px solid #5a7a5a;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #3d5d4d; border-color: #a3be8c; }
        """)
        add_btn.clicked.connect(self._add_objects)
        obj_btn_layout.addWidget(add_btn)
        
        remove_btn = QtWidgets.QPushButton("Remove")
        remove_btn.setFixedHeight(32)
        remove_btn.setCursor(QtCore.Qt.PointingHandCursor)
        remove_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d2d2d;
                color: #ff6b6b;
                font-size: 11px;
                font-weight: bold;
                border: 1px solid #5a3a3a;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #4d3d3d; border-color: #ff6b6b; }
        """)
        remove_btn.clicked.connect(self._remove_objects)
        obj_btn_layout.addWidget(remove_btn)
        
        content_layout.addWidget(obj_btn_widget)
        
        # Objects count label
        self.obj_count_label = QtWidgets.QLabel("0 objects")
        self.obj_count_label.setStyleSheet("color: #666; font-size: 10px;")
        content_layout.addWidget(self.obj_count_label)
        
        # Separator
        line2 = QtWidgets.QFrame()
        line2.setFixedHeight(1)
        line2.setStyleSheet("background-color: #666666;")
        content_layout.addWidget(line2)
        
        # ─────────────────────────────────────────────────────────────────────
        # PREVIEW SECTION
        # ─────────────────────────────────────────────────────────────────────
        preview_widget = QtWidgets.QWidget()
        preview_layout = QtWidgets.QHBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)
        
        preview_on_btn = QtWidgets.QPushButton("Preview ON")
        preview_on_btn.setFixedHeight(36)
        preview_on_btn.setCursor(QtCore.Qt.PointingHandCursor)
        preview_on_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d5d3d;
                color: #76FF03;
                font-size: 12px;
                font-weight: bold;
                border: 1px solid #5aaa5a;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #3d6d4d; }
        """)
        preview_on_btn.clicked.connect(self._preview_on)
        preview_layout.addWidget(preview_on_btn)
        
        preview_off_btn = QtWidgets.QPushButton("Preview OFF")
        preview_off_btn.setFixedHeight(36)
        preview_off_btn.setCursor(QtCore.Qt.PointingHandCursor)
        preview_off_btn.setStyleSheet("""
            QPushButton {
                background-color: #4d3d3d;
                color: #FF5722;
                font-size: 12px;
                font-weight: bold;
                border: 1px solid #8a5a5a;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #5d4d4d; }
        """)
        preview_off_btn.clicked.connect(self._preview_off)
        preview_layout.addWidget(preview_off_btn)
        
        content_layout.addWidget(preview_widget)
        
        # Preview status label
        self.preview_label = QtWidgets.QLabel("Preview: OFF")
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setFixedHeight(24)
        self.preview_label.setStyleSheet("""
            QLabel {
                background-color: #5a5a5a;
                color: #888;
                border-radius: 4px;
                font-size: 11px;
            }
        """)
        content_layout.addWidget(self.preview_label)
        
        # Separator
        line3 = QtWidgets.QFrame()
        line3.setFixedHeight(1)
        line3.setStyleSheet("background-color: #666666;")
        content_layout.addWidget(line3)
        
        # ─────────────────────────────────────────────────────────────────────
        # BAKE SECTION
        # ─────────────────────────────────────────────────────────────────────
        # Smart bake fidelity row
        dev_widget = QtWidgets.QWidget()
        dev_layout = QtWidgets.QHBoxLayout(dev_widget)
        dev_layout.setContentsMargins(0, 0, 0, 0)
        dev_layout.setSpacing(8)
        
        dev_label = QtWidgets.QLabel("Fidelity Keys Tolerance:")
        dev_label.setStyleSheet("color: #AAA; font-size: 11px;")
        dev_layout.addWidget(dev_label)
        
        self.dev_spinbox = QtWidgets.QDoubleSpinBox()
        self.dev_spinbox.setRange(0.0001, 20.0)
        self.dev_spinbox.setDecimals(4)
        self.dev_spinbox.setSingleStep(0.25)
        self.dev_spinbox.setValue(5.0)
        self.dev_spinbox.setFixedWidth(92)
        self.dev_spinbox.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #444444;
                color: #FFF;
                border: 1px solid #666666;
                border-radius: 6px;
                padding: 6px;
                font-size: 11px;
            }
            QDoubleSpinBox:focus { border-color: #3498DB; }
        """)
        dev_layout.addWidget(self.dev_spinbox)
        dev_layout.addStretch()
        
        content_layout.addWidget(dev_widget)

        self.increase_fidelity_checkbox = QtWidgets.QCheckBox("Increase Fidelity")
        self.increase_fidelity_checkbox.setChecked(True)
        self.increase_fidelity_checkbox.setToolTip(
            "Keeps extra keys whenever removing them would exceed the fidelity tolerance."
        )
        self.increase_fidelity_checkbox.setStyleSheet("""
            QCheckBox {
                color: #AAA;
                font-size: 11px;
                background-color: #444444;
                border-radius: 6px;
                padding: 7px 9px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #666666;
                border-radius: 3px;
                background-color: #333333;
            }
            QCheckBox::indicator:checked {
                background-color: #5e81ac;
                border-color: #88c0d0;
            }
        """)
        content_layout.addWidget(self.increase_fidelity_checkbox)
        
        # Bake button
        bake_btn = QtWidgets.QPushButton("BAKE")
        bake_btn.setFixedHeight(44)
        bake_btn.setCursor(QtCore.Qt.PointingHandCursor)
        bake_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d4d6d;
                color: #88c0d0;
                font-size: 13px;
                font-weight: bold;
                border: 2px solid #5e81ac;
                border-radius: 8px;
            }
            QPushButton:hover { 
                background-color: #4d5d7d; 
                border-color: #88c0d0;
                color: #FFF;
            }
        """)
        bake_btn.clicked.connect(self._bake)
        content_layout.addWidget(bake_btn)

        layer_bake_btn = QtWidgets.QPushButton("BAKE TO ANIM LAYER")
        layer_bake_btn.setFixedHeight(38)
        layer_bake_btn.setCursor(QtCore.Qt.PointingHandCursor)
        layer_bake_btn.setStyleSheet("""
            QPushButton {
                background-color: #4d3f2d;
                color: #ebcb8b;
                font-size: 12px;
                font-weight: bold;
                border: 2px solid #b48ead;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #5d4f3d;
                border-color: #ebcb8b;
                color: #FFF;
            }
        """)
        layer_bake_btn.clicked.connect(self._bake_layer)
        content_layout.addWidget(layer_bake_btn)
        
        # Status label
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setFixedHeight(24)
        self.status_label.setStyleSheet("color: #AAA; font-size: 11px;")
        content_layout.addWidget(self.status_label)
        
        content_layout.addStretch()
        container_layout.addWidget(content)
        main_layout.addWidget(self.container)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _refresh(self):
        """Refresh the retimer list"""
        self.retimer_combo.blockSignals(True)
        self.retimer_combo.clear()
        
        retimers = Retimer.find_all()
        if not retimers:
            retimers = ["main"]

        active_name = _get_active_retimer_name()
        load_name = active_name if active_name in retimers else retimers[0]
        
        for name in sorted(retimers):
            self.retimer_combo.addItem(name)

        idx = self.retimer_combo.findText(load_name)
        if idx >= 0:
            self.retimer_combo.setCurrentIndex(idx)
        
        self.retimer_combo.blockSignals(False)
        self._load(load_name)
        
        self.status_label.setText("List refreshed")
        self.status_label.setStyleSheet("color: #3498DB; font-size: 11px;")
    
    def _on_select(self, name):
        """Called when retimer selection changes"""
        if name:
            self._load(name)
    
    def _load(self, name):
        """Load a retimer by name"""
        self.rt = Retimer(name)
        self.rt.create_curve()
        _set_active_retimer_name(name)
        self._update_ui()
    
    def _edit_curve(self):
        """Open curve in Graph Editor"""
        if self.rt:
            self.rt.select_curve()
    
    def _create_new(self):
        """Create a new retimer"""
        name = self.new_name_input.text().strip()
        if not name:
            cmds.warning("Enter a name for the new retimer")
            return
        
        name = name.replace(" ", "_").lower()
        self.rt = Retimer(name)
        self.rt.create_curve()
        self.new_name_input.setText("")
        self._refresh()
        
        # Select the new retimer
        idx = self.retimer_combo.findText(name)
        if idx >= 0:
            self.retimer_combo.setCurrentIndex(idx)
        
        self.status_label.setText(f"Created: {name}")
        self.status_label.setStyleSheet("color: #a3be8c; font-size: 11px;")
    
    def _delete_retimer(self):
        """Delete current retimer"""
        if self.rt:
            name = self.rt.name
            self.rt.delete()
            self.status_label.setText(f"Deleted: {name}")
            self.status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
            self._refresh()
    
    def _add_objects(self):
        """Add selected objects to retimer"""
        if self.rt:
            added = self.rt.add_objects()
            self._update_ui()
            if added:
                self.status_label.setText(f"Added {len(added)} objects")
                self.status_label.setStyleSheet("color: #a3be8c; font-size: 11px;")
    
    def _remove_objects(self):
        """Remove selected objects from retimer"""
        if not self.rt:
            return
        
        selected_items = self.obj_list.selectedItems()
        if not selected_items:
            cmds.warning("Select objects in the list to remove")
            return
        
        objs = [item.text() for item in selected_items]
        self.rt.remove_objects(objs)
        self._update_ui()
        
        self.status_label.setText(f"Removed {len(objs)} objects")
        self.status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
    
    def _preview_on(self):
        """Turn preview on"""
        if self.rt:
            self.rt.preview_on()
            self._update_ui()
    
    def _preview_off(self):
        """Turn preview off"""
        if self.rt:
            self.rt.preview_off()
            self._update_ui()
    
    def _bake(self):
        """Bake animation with curve cleaning"""
        if not self.rt:
            return
        
        if not self.rt.objects:
            cmds.warning("Add objects first")
            return
        
        max_dev = self.dev_spinbox.value()
        increase_fidelity = self.increase_fidelity_checkbox.isChecked()
        
        cmds.undoInfo(openChunk=True, chunkName='RetimerBake')
        try:
            result = self.rt.bake_full(
                sample_by=1,
                clean_curves=True,
                max_deviation=max_dev,
                increase_fidelity=increase_fidelity
            )
            
            if result:
                msg = f"Smart baked: {result['keys_final']} keys | Cleaned: -{result['keys_removed']} keys"
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #a3be8c; font-size: 11px;")
                cmds.inViewMessage(amg=f"<hl>Smart Bake Complete</hl>: {result['keys_final']} keys", 
                                  pos='midCenter', fade=True)
                if result.get("retimer_deleted"):
                    self.rt = None
                    self.preview_label.setText("Preview: OFF")
                    self.preview_label.setStyleSheet("""
                        QLabel {
                            background-color: #5a5a5a;
                            color: #888;
                            border-radius: 4px;
                            font-size: 11px;
                        }
                    """)
            else:
                self.status_label.setText("Bake error")
                self.status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
        except Exception as e:
            self.status_label.setText(f"Error: {str(e)}")
            self.status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
        finally:
            cmds.undoInfo(closeChunk=True)
        if self.rt:
            self._update_ui()

    def _bake_layer(self):
        """Bake retimer result into a new animation layer."""
        if not self.rt:
            return

        if not self.rt.objects:
            cmds.warning("Add objects first")
            return

        cmds.undoInfo(openChunk=True, chunkName='RetimerBakeAnimLayer')
        try:
            result = self.rt.bake_to_anim_layer()

            if result:
                layer = result.get("layer")
                keys = result.get("keys_final", 0)
                self.status_label.setText(f"Layer baked: {layer} | {keys} keys")
                self.status_label.setStyleSheet("color: #ebcb8b; font-size: 11px;")
                cmds.inViewMessage(
                    amg=f"<hl>Retimer baked</hl> to <hl>{layer}</hl>",
                    pos='midCenter',
                    fade=True
                )
                self.rt = None
                self.preview_label.setText("Preview: OFF")
                self.preview_label.setStyleSheet("""
                    QLabel {
                        background-color: #5a5a5a;
                        color: #888;
                        border-radius: 4px;
                        font-size: 11px;
                    }
                """)
            else:
                self.status_label.setText("Layer bake error")
                self.status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
        except Exception as e:
            self.status_label.setText(f"Error: {str(e)}")
            self.status_label.setStyleSheet("color: #ff6b6b; font-size: 11px;")
        finally:
            cmds.undoInfo(closeChunk=True)
        if self.rt:
            self._update_ui()
    
    def _update_ui(self):
        """Update the UI based on current retimer state"""
        if not self.rt:
            return
        
        # Update object list
        self.obj_list.clear()
        for obj in self.rt.objects:
            self.obj_list.addItem(obj)
        
        self.obj_count_label.setText(f"{len(self.rt.objects)} objects")
        
        # Update preview status
        if self.rt.is_preview_on():
            self.preview_label.setText("Preview: ON")
            self.preview_label.setStyleSheet("""
                QLabel {
                    background-color: #2d5d3d;
                    color: #76FF03;
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
        else:
            self.preview_label.setText("Preview: OFF")
            self.preview_label.setStyleSheet("""
                QLabel {
                    background-color: #5a5a5a;
                    color: #888;
                    border-radius: 4px;
                    font-size: 11px;
                }
            """)
    
    def enterEvent(self, e):
        self._animate(self._hover_opacity)
        
    def leaveEvent(self, e):
        self._animate(self._base_opacity)
        
    def _animate(self, val):
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(150)
        anim.setEndValue(val)
        anim.start(QtCore.QPropertyAnimation.DeleteWhenStopped)
        self._anim = anim
    
    def closeEvent(self, event):
        """Clean up when window closes"""
        global _retimer_window
        _retimer_window = None
        super(RetimerWindow, self).closeEvent(event)


# =============================================================================
# PUBLIC FUNCTIONS
# =============================================================================

def show(anchor_button=None):
    """Open the Retimer window"""
    global _retimer_window
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows(except_widget=_retimer_window)
    
    if _retimer_window is not None:
        existing = uiMod.show_existing_animkey_tool_window(_retimer_window, anchor_button)
        if existing is not None:
            _retimer_window = existing
            return _retimer_window
        _retimer_window = None
    
    if cmds.window(WINDOW_OBJECT, exists=True):
        cmds.deleteUI(WINDOW_OBJECT)
    
    _retimer_window = RetimerWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _retimer_window.show()
    _retimer_window.raise_()
    return _retimer_window


def execute(*args, **kwargs):
    """Main entry point for the button"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.retimer.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))


def clean_curves(max_dev=5.0):
    """Clean curves of selected objects"""
    objs = cmds.ls(sl=True) or []
    if not objs:
        cmds.warning("Select objects")
        return
    cleaner = CurveCleaner()
    result = cleaner.clean(objs, max_dev)
    if result:
        print(f"Removed {result['removed']}/{result['total']} keys ({result['percent']:.1f}%)")
    return result


# Aliases
ui = show
show_ui = show


if __name__ == "__main__":
    show()
