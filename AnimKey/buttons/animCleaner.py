# -*- coding: utf-8 -*-
"""
AnimKey - Animation Cleaner

Modern frameless window for cleaning animation curves.
Features:
- Delete static channels (curves with no value change)
- Delete redundant keys (keys with same value as neighbors)
- Delete sub-frame keys (keys not on integer frames)
"""

import maya.cmds as mc
from AnimKey.mods.uiMod import ContextPopupWindow
import maya.mel as mm
import maya.OpenMayaUI as mui
import traceback
import math
from functools import partial

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance


WINDOW_OBJECT = "AnimKey_AnimCleaner"

# Global window reference
_cleaner_window = None


def get_maya_main_window():
    return wrapInstance(int(mui.MQtUtil.mainWindow()), QtWidgets.QWidget)


# ════════════════════════════════════════════════════════════════════════════════
#                           UTILITY FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════════

def cast_to_time_tuple(time_value):
    """Ensure time value is a proper tuple for Maya commands."""
    if isinstance(time_value, (list, tuple)):
        return [(float(x),) for x in time_value]
    return (float(time_value),)


def get_channel_from_anim_curve(curve_node_name, plugs=True):
    """
    Find the attribute (plug) or node connected to an animation curve,
    navigating through intermediate nodes like unitConversion or animBlend.
    """
    if not isinstance(curve_node_name, str):
        return None
    base_node_name = curve_node_name.split('.')[0]
    if not mc.objExists(base_node_name):
        return None

    node_type = mc.nodeType(base_node_name)
    if node_type not in ['animCurveTL', 'animCurveTA', 'animCurveTU'] and not node_type.startswith('animBlendNode'):
        return None

    output_attr = f"{base_node_name}.output"
    destinations = mc.listConnections(output_attr, source=False, destination=True, plugs=True)

    if destinations:
        connected_plug = destinations[0]
        dest_node = connected_plug.split('.')[0]
        if mc.objExists(dest_node):
            dest_node_type = mc.nodeType(dest_node)
            if dest_node_type.startswith(('animCurve', 'animBlendNode')):
                return get_channel_from_anim_curve(dest_node, plugs=plugs)
            else:
                return connected_plug if plugs else dest_node
        else:
            return connected_plug if plugs else None
    return None


def get_selected_channels_from_channelbox():
    """Get selected attribute names from Channel Box."""
    if not mc.ls(sl=True):
        return []
    gChannelBoxName = mm.eval('$temp=$gChannelBoxName')
    if not mc.channelBox(gChannelBoxName, query=True, exists=True):
        return []

    selected_attributes = []
    for flag_attr_name in ['sma', 'ssa', 'sha', 'soa']:
        try:
            attrs = mc.channelBox(gChannelBoxName, query=True, **{flag_attr_name: True})
            if attrs:
                selected_attributes.extend(attrs)
        except RuntimeError:
            pass
    return list(set(selected_attributes))


def get_node_namespace(node_name):
    """Extract namespace from a Maya node name."""
    if not isinstance(node_name, str) or ':' not in node_name:
        return ''
    return node_name.rsplit('|', 1)[-1].rsplit(':', 1)[0] + ':'


def get_hierarchy_root_nodes(node_list):
    """Find the top-level root nodes for a given list of nodes."""
    if not node_list:
        return []
    objects_long_names = mc.ls(node_list, long=True)
    if not objects_long_names:
        return []

    top_level_nodes = set()
    processed_namespaces = set()

    for obj_long_name in objects_long_names:
        namespace = get_node_namespace(obj_long_name)
        if namespace and namespace in processed_namespaces:
            continue

        current_node = obj_long_name
        current_top_ancestor = obj_long_name

        while True:
            parent = mc.listRelatives(current_node, parent=True, fullPath=True)
            if not parent:
                current_top_ancestor = current_node
                break
            parent_name_long = parent[0]

            if namespace:
                if get_node_namespace(parent_name_long) != namespace:
                    current_top_ancestor = current_node
                    break
            elif not namespace and get_node_namespace(parent_name_long):
                current_top_ancestor = current_node
                break
            
            current_node = parent_name_long

        top_level_nodes.add(current_top_ancestor)
        if namespace:
            processed_namespaces.add(namespace)
            
    return list(top_level_nodes)


# ════════════════════════════════════════════════════════════════════════════════
#                           ANIMATION SELECTION CLASS
# ════════════════════════════════════════════════════════════════════════════════

class AnimationSelection(object):
    """
    Manages selection of animation curves and their properties
    based on different scopes (objects, channels, scene, etc.).
    """
    def __init__(self):
        self.initial_maya_selection = mc.ls(sl=True, long=True) or []
        self._target_nodes = []
        self._target_curves = []
        self._target_channels = []
        
        self._time_override = None
        self._use_selected_keys_time = False
        self._curves_are_culled = False

        self.subframe_tolerance = 1e-5
        self.value_comparison_tolerance = 1e-6

    def _reset_internal_targets(self):
        """Reset internal targets before new scope initialization."""
        self._target_nodes, self._target_curves, self._target_channels = [], [], []
        self._curves_are_culled = False
        self._time_override = None
        self._use_selected_keys_time = False

    @property
    def curves(self):
        """Get and filter valid animation curves based on current targets."""
        if not self._target_curves and not self._curves_are_culled:
            curves_to_query_from = []
            if self._target_channels:
                curves_to_query_from = self._target_channels
            elif self._target_nodes:
                curves_to_query_from = self._target_nodes
            
            if curves_to_query_from:
                found_raw_curves = mc.keyframe(curves_to_query_from, query=True, name=True, time=(':',))
                self._target_curves = list(set(found_raw_curves)) if found_raw_curves else []

            if self._target_curves:
                valid_curves = []
                for curve_name in self._target_curves:
                    if not mc.objExists(curve_name):
                        continue
                    if mc.referenceQuery(curve_name, isNodeReferenced=True):
                        continue

                    attr_plug = get_channel_from_anim_curve(curve_name, plugs=True)
                    if attr_plug:
                        node_of_plug, attr_of_plug = attr_plug.split('.', 1)
                        if mc.objExists(node_of_plug) and \
                           mc.attributeQuery(attr_of_plug, node=node_of_plug, exists=True) and \
                           mc.getAttr(attr_plug, settable=True):
                            valid_curves.append(curve_name)
                self._target_curves = valid_curves
            self._curves_are_culled = True
        return self._target_curves

    @property
    def active_targets_for_command(self):
        """Return main arguments for Maya commands."""
        if self._target_channels:
            return self._target_channels
        if self.curves:
            return self.curves
        if self._target_nodes:
            return self._target_nodes
        return []

    @property
    def time_range_for_query(self):
        """Determine the time range for key queries."""
        if self._time_override is not None:
            return self._time_override
        
        if self._use_selected_keys_time:
            selected_key_times = mc.keyframe(query=True, selected=True, timeChange=True)
            return tuple(sorted(list(set(selected_key_times)))) if selected_key_times else (':',)
        
        return (':',)

    @property
    def values_from_curves(self):
        """Get values from curves in the time range."""
        value_list_per_curve = []
        query_time = self.time_range_for_query
        
        curves_to_get_values_from = self.curves 
        if not curves_to_get_values_from:
            return []

        for curve_name in curves_to_get_values_from:
            try:
                key_values = mc.keyframe(curve_name, time=query_time, query=True, valueChange=True)
                value_list_per_curve.append(tuple(key_values) if key_values is not None else tuple())
            except RuntimeError:
                value_list_per_curve.append(tuple())
        return value_list_per_curve

    # Scope initialization methods
    def set_scope_to_selected_objects(self):
        self._reset_internal_targets()
        if not self.initial_maya_selection:
            return False
        self._target_nodes = self.initial_maya_selection
        return True

    def set_scope_to_selected_channels(self):
        self._reset_internal_targets()
        selected_attr_names = get_selected_channels_from_channelbox()
        if not selected_attr_names or not self.initial_maya_selection:
            return False

        for node_long_name in self.initial_maya_selection:
            for attr_name in selected_attr_names:
                potential_plug = f"{node_long_name}.{attr_name}"
                if mc.objExists(potential_plug):
                    self._target_channels.append(potential_plug)
                else:
                    if mc.nodeType(node_long_name) == 'transform':
                        shapes = mc.listRelatives(node_long_name, shapes=True, fullPath=True) or []
                        for shape_node in shapes:
                            potential_shape_plug = f"{shape_node}.{attr_name}"
                            if mc.objExists(potential_shape_plug):
                                self._target_channels.append(potential_shape_plug)
                                break

        self._target_channels = list(set(self._target_channels))
        return bool(self._target_channels)

    def set_scope_to_selected_hierarchy(self, include_root=True):
        self._reset_internal_targets()
        if not self.initial_maya_selection:
            return False
        
        root_nodes_for_hierarchy = get_hierarchy_root_nodes(self.initial_maya_selection)
        if not root_nodes_for_hierarchy:
            return False

        all_nodes_in_hierarchies = mc.listRelatives(root_nodes_for_hierarchy, allDescendents=True, fullPath=True, type='transform') or []
        if include_root:
            all_nodes_in_hierarchies.extend(root_nodes_for_hierarchy)
        
        if not all_nodes_in_hierarchies:
            return False
        self._target_nodes = list(set(all_nodes_in_hierarchies))
        return True

    def set_scope_to_graph_editor_visible(self):
        self._reset_internal_targets()
        active_graph_editor = None
        for panel_name in mc.getPanel(visiblePanels=True):
            if mc.getPanel(typeOf=panel_name) == 'graphEditor':
                active_graph_editor = panel_name
                break
        if not active_graph_editor:
            return False

        sel_connection_name = mc.graphEditor(active_graph_editor, query=True, selectionConnection=True)
        if not sel_connection_name or not mc.selectionConnection(sel_connection_name, exists=True):
             sel_connection_name = f"{active_graph_editor}FromOutliner"
             if not mc.selectionConnection(sel_connection_name, exists=True):
                  return False

        items_in_graph_editor = mc.selectionConnection(sel_connection_name, query=True, object=True)
        if not items_in_graph_editor:
            return False
        
        found_curves = mc.keyframe(items_in_graph_editor, query=True, name=True, time=(':',))
        if not found_curves:
            return False
        self._target_curves = list(set(found_curves))
        self._curves_are_culled = False
        return True

    def set_scope_to_entire_scene(self):
        self._reset_internal_targets()
        all_scene_anim_curves = (mc.ls(type='animCurveTL', long=True) or []) + \
                                (mc.ls(type='animCurveTA', long=True) or []) + \
                                (mc.ls(type='animCurveTU', long=True) or [])
        if not all_scene_anim_curves:
            return False
        self._target_curves = list(set(all_scene_anim_curves))
        self._curves_are_culled = False
        return True

    def set_scope_to_selected_keys_in_graph_editor(self):
        """Set scope to curves with selected keys in Graph Editor."""
        self._reset_internal_targets()
        selected_curve_nodes_from_keys = mc.keyframe(query=True, selected=True, name=True)
        if not selected_curve_nodes_from_keys:
            return False
        
        self._target_curves = list(set(selected_curve_nodes_from_keys))
        self._curves_are_culled = False
        self._use_selected_keys_time = True
        return True


# ════════════════════════════════════════════════════════════════════════════════
#                           CLEANUP FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════════

def _get_animation_selection_object(ui_selection_index, anim_selection_instance=None):
    """Create or use an AnimationSelection and initialize it based on UI option."""
    selector = anim_selection_instance if anim_selection_instance else AnimationSelection()

    scope_initializers = {
        1: selector.set_scope_to_selected_objects,
        2: selector.set_scope_to_selected_channels,
        3: selector.set_scope_to_selected_hierarchy,
        4: selector.set_scope_to_graph_editor_visible,
        5: selector.set_scope_to_entire_scene,
        6: selector.set_scope_to_selected_keys_in_graph_editor
    }
    initializer_method = scope_initializers.get(ui_selection_index)

    if initializer_method:
        if not initializer_method():
            print(f"Info: Scope option {ui_selection_index} found no initial elements to process.")
    else:
        mc.warning(f"Error: Unknown scope option: {ui_selection_index}")
        return None
    return selector


def delete_static_channels(selection_option_idx=1):
    """Delete animation curves whose values don't change over time."""
    mc.undoInfo(openChunk=True, chunkName="Delete Static Channels")
    deleted_count = 0
    try:
        anim_selector = _get_animation_selection_object(selection_option_idx)
        if not anim_selector:
            raise ValueError("Could not initialize animation scope.")

        anim_selector._time_override = (':',)
        
        curves_to_check = anim_selector.curves
        if not curves_to_check:
            print("No valid curves to process for static channels.")
            mc.undoInfo(closeChunk=True)
            return

        all_key_values_per_curve = anim_selector.values_from_curves

        if len(curves_to_check) != len(all_key_values_per_curve):
             mc.warning(f"Mismatch in curves and values count. Skipping.")
             mc.undoInfo(closeChunk=True)
             return

        curves_identified_as_static = []
        for i, curve_name in enumerate(curves_to_check):
            key_values = all_key_values_per_curve[i]
            
            if not key_values:
                continue
            if len(key_values) == 1:
                curves_identified_as_static.append(curve_name)
                continue

            first_val = key_values[0]
            is_static = all(
                abs(v - first_val) < anim_selector.value_comparison_tolerance for v in key_values[1:]
            )
            if is_static:
                curves_identified_as_static.append(curve_name)

        if curves_identified_as_static:
            mc.delete(curves_identified_as_static)
            deleted_count = len(curves_identified_as_static)
            
    except Exception as e:
        mc.warning(f"Error in 'Delete Static Channels': {e}")
        traceback.print_exc()
    finally:
        mc.undoInfo(closeChunk=True)
        msg = f"Deleted {deleted_count} static channels." if deleted_count > 0 else "No static channels found."
        mc.inViewMessage(amg=f"<hl>{msg}</hl>", pos='midCenter', fade=True)
        print(msg)


def delete_redundant_keys(selection_option_idx=1):
    """Delete keys that are redundant (same value as previous and next key)."""
    mc.undoInfo(openChunk=True, chunkName="Delete Redundant Keys")
    total_keys_cut_count = 0
    try:
        anim_selector = _get_animation_selection_object(selection_option_idx)
        if not anim_selector:
            raise ValueError("Could not initialize animation scope.")

        curves_to_process = anim_selector.curves
        if not curves_to_process:
            print("No valid curves to process for redundant keys.")
            mc.undoInfo(closeChunk=True)
            return

        for curve_name in curves_to_process:
            try:
                 key_times = mc.keyframe(curve_name, query=True, timeChange=True)
                 key_values = mc.keyframe(curve_name, query=True, valueChange=True)
            except RuntimeError:
                continue

            if not key_times or not key_values or len(key_times) < 3 or len(key_times) != len(key_values):
                continue

            sorted_key_data = sorted(zip(key_times, key_values))
            
            redundant_key_times_to_cut = []
            for i in range(1, len(sorted_key_data) - 1):
                _prev_time, prev_val = sorted_key_data[i-1]
                curr_time, curr_val = sorted_key_data[i]
                _next_time, next_val = sorted_key_data[i+1]

                if abs(curr_val - prev_val) < anim_selector.value_comparison_tolerance and \
                   abs(curr_val - next_val) < anim_selector.value_comparison_tolerance:
                    redundant_key_times_to_cut.append(curr_time)
            
            if redundant_key_times_to_cut:
                mc.cutKey(curve_name, time=cast_to_time_tuple(redundant_key_times_to_cut), clear=True)
                total_keys_cut_count += len(redundant_key_times_to_cut)
                
    except Exception as e:
        mc.warning(f"Error in 'Delete Redundant Keys': {e}")
        traceback.print_exc()
    finally:
        mc.undoInfo(closeChunk=True)
        msg = f"Deleted {total_keys_cut_count} redundant keys." if total_keys_cut_count > 0 else "No redundant keys found."
        mc.inViewMessage(amg=f"<hl>{msg}</hl>", pos='midCenter', fade=True)
        print(msg)


def delete_sub_frame_keys(selection_option_idx=1):
    """Delete keys that are not on integer frame numbers (sub-frames)."""
    mc.undoInfo(openChunk=True, chunkName="Delete Sub-Frame Keys")
    total_keys_cut_count = 0
    try:
        anim_selector = _get_animation_selection_object(selection_option_idx)
        if not anim_selector:
            raise ValueError("Could not initialize animation scope.")

        curves_to_process = anim_selector.curves
        if not curves_to_process:
            print("No valid curves to process for sub-frame keys.")
            mc.undoInfo(closeChunk=True)
            return

        for curve_name in curves_to_process:
            try:
                key_times = mc.keyframe(curve_name, query=True, timeChange=True)
            except RuntimeError:
                continue

            if not key_times:
                continue

            sub_frame_key_times_to_cut = [
                t for t in key_times if abs(t - round(t)) > anim_selector.subframe_tolerance
            ]

            if sub_frame_key_times_to_cut:
                mc.cutKey(curve_name, time=cast_to_time_tuple(sub_frame_key_times_to_cut), clear=True)
                total_keys_cut_count += len(sub_frame_key_times_to_cut)
                
    except Exception as e:
        mc.warning(f"Error in 'Delete Sub-Frame Keys': {e}")
        traceback.print_exc()
    finally:
        mc.undoInfo(closeChunk=True)
        msg = f"Deleted {total_keys_cut_count} sub-frame keys." if total_keys_cut_count > 0 else "No sub-frame keys found."
        mc.inViewMessage(amg=f"<hl>{msg}</hl>", pos='midCenter', fade=True)
        print(msg)


def _distance(values_a, values_b):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(values_a, values_b)))


def _median(values):
    if not values:
        return 0.0
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) * 0.5


def _lerp_values(values_a, values_b, factor):
    return [a + ((b - a) * factor) for a, b in zip(values_a, values_b)]


def _node_from_anim_curve(curve_name):
    plug = get_channel_from_anim_curve(curve_name, plugs=True)
    if not plug or "." not in plug:
        return None
    node_name = plug.split(".", 1)[0]
    if not mc.objExists(node_name):
        return None
    if mc.nodeType(node_name) != "transform":
        return None
    return node_name


def _unique_transform_nodes(nodes):
    result = []
    seen = set()
    for node in nodes or []:
        if not node or not mc.objExists(node):
            continue
        if mc.nodeType(node) != "transform":
            continue
        long_name = (mc.ls(node, long=True) or [node])[0]
        if long_name not in seen:
            seen.add(long_name)
            result.append(long_name)
    return result


def _visual_nodes_from_selection(anim_selector):
    nodes = []
    nodes.extend(anim_selector._target_nodes)
    for plug in anim_selector._target_channels:
        node_name = plug.split(".", 1)[0]
        nodes.append(node_name)
    for curve_name in anim_selector.curves:
        node_name = _node_from_anim_curve(curve_name)
        if node_name:
            nodes.append(node_name)
    return _unique_transform_nodes(nodes)


def _selected_key_times_for_node(node_name):
    times = []
    try:
        curves = mc.keyframe(node_name, query=True, selected=True, name=True) or []
    except RuntimeError:
        curves = []
    for curve_name in curves:
        curve_node = _node_from_anim_curve(curve_name)
        if curve_node and (mc.ls(curve_node, long=True) or [curve_node])[0] != node_name:
            continue
        try:
            times.extend(mc.keyframe(curve_name, query=True, selected=True, timeChange=True) or [])
        except RuntimeError:
            pass
    return sorted(set(times))


def _visual_sample_times(node_name, anim_selector, max_full_range_frames=600):
    if anim_selector._use_selected_keys_time:
        return _selected_key_times_for_node(node_name)

    key_times = mc.keyframe(node_name, query=True, timeChange=True) or []
    if len(key_times) < 3:
        return []

    start = int(math.floor(min(key_times)))
    end = int(math.ceil(max(key_times)))
    if end <= start:
        return sorted(set(key_times))

    if (end - start) <= max_full_range_frames:
        sampled = set(range(start, end + 1))
        sampled.update(key_times)
        return sorted(sampled)

    return sorted(set(key_times))


def _unwrap_rotations(samples):
    if not samples:
        return
    for axis in range(3):
        prev = samples[0]["rot"][axis]
        for sample in samples[1:]:
            value = sample["rot"][axis]
            while value - prev > 180.0:
                value -= 360.0
            while value - prev < -180.0:
                value += 360.0
            sample["rot"][axis] = value
            prev = value


def _set_auto_tangents(node_name, attrs, frame):
    for attr in attrs:
        attr_path = f"{node_name}.{attr}"
        if not mc.objExists(attr_path):
            continue
        try:
            mc.keyTangent(attr_path, edit=True, time=(frame,), itt='auto', ott='auto')
        except RuntimeError:
            pass


def _keyable_transform_attrs(node_name, attrs):
    editable = []
    for attr in attrs:
        attr_path = f"{node_name}.{attr}"
        if not mc.objExists(attr_path):
            continue
        try:
            if not mc.getAttr(attr_path, lock=True) and mc.getAttr(attr_path, settable=True):
                editable.append(attr)
        except RuntimeError:
            pass
    return editable


def _smooth_visual_worldspace_pops(anim_selector, smooth_strength=0.75):
    """Smooth visible transform spikes by sampling world-space motion."""
    nodes = _visual_nodes_from_selection(anim_selector)
    if not nodes:
        return 0, 0

    original_time = mc.currentTime(query=True)
    visual_keys_smoothed = 0
    visual_nodes_smoothed = 0
    mc.refresh(suspend=True)

    try:
        for node_name in nodes:
            sample_times = _visual_sample_times(node_name, anim_selector)
            if len(sample_times) < 3:
                continue

            samples = []
            for frame in sample_times:
                try:
                    mc.currentTime(frame, edit=True)
                    pos = mc.xform(node_name, query=True, translation=True, worldSpace=True)
                    rot = mc.xform(node_name, query=True, rotation=True, worldSpace=True)
                    samples.append({"frame": frame, "pos": list(pos), "rot": list(rot)})
                except RuntimeError:
                    pass

            if len(samples) < 3:
                continue

            _unwrap_rotations(samples)
            pos_steps = [
                _distance(samples[i]["pos"], samples[i - 1]["pos"])
                for i in range(1, len(samples))
            ]
            rot_steps = [
                _distance(samples[i]["rot"], samples[i - 1]["rot"])
                for i in range(1, len(samples))
            ]
            pos_threshold = max(_median(pos_steps) * 1.8, 0.001)
            rot_threshold = max(_median(rot_steps) * 1.8, 0.25)
            pos_attrs = _keyable_transform_attrs(node_name, ["tx", "ty", "tz"])
            rot_attrs = _keyable_transform_attrs(node_name, ["rx", "ry", "rz"])
            node_was_smoothed = False

            for i in range(1, len(samples) - 1):
                prev_sample = samples[i - 1]
                sample = samples[i]
                next_sample = samples[i + 1]
                time_span = next_sample["frame"] - prev_sample["frame"]
                if abs(time_span) <= 1e-5:
                    continue

                factor = (sample["frame"] - prev_sample["frame"]) / float(time_span)
                target_pos = _lerp_values(prev_sample["pos"], next_sample["pos"], factor)
                target_rot = _lerp_values(prev_sample["rot"], next_sample["rot"], factor)
                pos_deviation = _distance(sample["pos"], target_pos)
                rot_deviation = _distance(sample["rot"], target_rot)

                changed_attrs = []
                try:
                    mc.currentTime(sample["frame"], edit=True)
                    if pos_attrs and pos_deviation > pos_threshold:
                        new_pos = _lerp_values(sample["pos"], target_pos, smooth_strength)
                        mc.xform(node_name, translation=new_pos, worldSpace=True)
                        mc.setKeyframe(node_name, attribute=pos_attrs, time=sample["frame"])
                        changed_attrs.extend(pos_attrs)

                    if rot_attrs and rot_deviation > rot_threshold:
                        new_rot = _lerp_values(sample["rot"], target_rot, smooth_strength)
                        mc.xform(node_name, rotation=new_rot, worldSpace=True)
                        mc.setKeyframe(node_name, attribute=rot_attrs, time=sample["frame"])
                        changed_attrs.extend(rot_attrs)

                    if changed_attrs:
                        _set_auto_tangents(node_name, changed_attrs, sample["frame"])
                        visual_keys_smoothed += 1
                        node_was_smoothed = True
                except RuntimeError:
                    pass

            if node_was_smoothed:
                visual_nodes_smoothed += 1
                try:
                    mc.filterCurve(node_name)
                except RuntimeError:
                    pass
    finally:
        mc.currentTime(original_time, edit=True)
        mc.refresh(suspend=False)

    return visual_nodes_smoothed, visual_keys_smoothed


def auto_smooth_noise(selection_option_idx=1, smooth_strength=0.7, passes=2):
    """Clean graph-curve noise and smooth visible world-space pops."""
    mc.undoInfo(openChunk=True, chunkName="Auto Smooth Noise")
    curves_smoothed = 0
    keys_smoothed = 0
    redundant_keys_deleted = 0
    visual_nodes_smoothed = 0
    visual_keys_smoothed = 0
    try:
        anim_selector = _get_animation_selection_object(selection_option_idx)
        if not anim_selector:
            raise ValueError("Could not initialize animation scope.")

        curves_to_process = anim_selector.curves
        if not curves_to_process:
            print("No valid curves to process for auto noise smoothing.")
            curves_to_process = []

        for curve_name in curves_to_process:
            curve_was_smoothed = False
            selected_key_times = set()
            if anim_selector._use_selected_keys_time:
                try:
                    selected_key_times = set(
                        mc.keyframe(curve_name, query=True, selected=True, timeChange=True) or []
                    )
                except RuntimeError:
                    selected_key_times = set()
                if not selected_key_times:
                    continue

            for _ in range(max(1, int(passes))):
                try:
                    key_times = mc.keyframe(curve_name, query=True, timeChange=True) or []
                    key_values = mc.keyframe(curve_name, query=True, valueChange=True) or []
                except RuntimeError:
                    break

                if len(key_times) < 3 or len(key_times) != len(key_values):
                    break

                sorted_key_data = sorted(zip(key_times, key_values))
                step_sizes = [
                    abs(sorted_key_data[i][1] - sorted_key_data[i - 1][1])
                    for i in range(1, len(sorted_key_data))
                ]
                avg_step = sum(step_sizes) / float(len(step_sizes)) if step_sizes else 0.0
                median_step = _median(step_sizes)
                noise_step_limit = max(
                    avg_step * 0.65,
                    anim_selector.value_comparison_tolerance * 100.0
                )
                pop_step_limit = max(
                    median_step * 2.5,
                    avg_step * 1.2,
                    anim_selector.value_comparison_tolerance * 100.0
                )

                adjusted_times = []
                for i in range(1, len(sorted_key_data) - 1):
                    prev_time, prev_val = sorted_key_data[i - 1]
                    curr_time, curr_val = sorted_key_data[i]
                    next_time, next_val = sorted_key_data[i + 1]

                    if selected_key_times and curr_time not in selected_key_times:
                        continue

                    time_span = next_time - prev_time
                    if abs(time_span) <= anim_selector.subframe_tolerance:
                        continue

                    incoming = curr_val - prev_val
                    outgoing = next_val - curr_val
                    local_peak = max(abs(incoming), abs(outgoing))
                    if local_peak <= anim_selector.value_comparison_tolerance:
                        continue

                    interp_value = prev_val + (
                        ((curr_time - prev_time) / float(time_span)) * (next_val - prev_val)
                    )
                    deviation = curr_val - interp_value
                    direction_flip = incoming * outgoing < 0.0
                    micro_wobble = (
                        abs(deviation) > anim_selector.value_comparison_tolerance and
                        local_peak <= noise_step_limit
                    )
                    pop_spike = (
                        abs(deviation) > pop_step_limit and
                        local_peak > pop_step_limit
                    )

                    if not direction_flip or not (micro_wobble or pop_spike):
                        continue

                    new_value = curr_val + ((interp_value - curr_val) * smooth_strength)
                    if abs(new_value - curr_val) <= anim_selector.value_comparison_tolerance:
                        continue

                    try:
                        mc.keyframe(curve_name, edit=True, time=(curr_time,), valueChange=new_value)
                        adjusted_times.append(curr_time)
                    except RuntimeError:
                        continue

                if not adjusted_times:
                    break

                curve_was_smoothed = True
                keys_smoothed += len(adjusted_times)
                try:
                    mc.keyTangent(
                        curve_name,
                        edit=True,
                        time=cast_to_time_tuple(adjusted_times),
                        itt='auto',
                        ott='auto'
                    )
                except RuntimeError:
                    pass

            try:
                key_times = mc.keyframe(curve_name, query=True, timeChange=True) or []
                key_values = mc.keyframe(curve_name, query=True, valueChange=True) or []
            except RuntimeError:
                key_times, key_values = [], []

            if len(key_times) >= 3 and len(key_times) == len(key_values):
                sorted_key_data = sorted(zip(key_times, key_values))
                redundant_key_times_to_cut = []
                for i in range(1, len(sorted_key_data) - 1):
                    _prev_time, prev_val = sorted_key_data[i - 1]
                    curr_time, curr_val = sorted_key_data[i]
                    _next_time, next_val = sorted_key_data[i + 1]

                    if selected_key_times and curr_time not in selected_key_times:
                        continue

                    if (
                        abs(curr_val - prev_val) < anim_selector.value_comparison_tolerance and
                        abs(curr_val - next_val) < anim_selector.value_comparison_tolerance
                    ):
                        redundant_key_times_to_cut.append(curr_time)

                if redundant_key_times_to_cut:
                    try:
                        mc.cutKey(
                            curve_name,
                            time=cast_to_time_tuple(redundant_key_times_to_cut),
                            clear=True
                        )
                        redundant_keys_deleted += len(redundant_key_times_to_cut)
                    except RuntimeError:
                        pass

            if curve_was_smoothed:
                curves_smoothed += 1

        visual_nodes_smoothed, visual_keys_smoothed = _smooth_visual_worldspace_pops(
            anim_selector,
            smooth_strength=0.75
        )

    except Exception as e:
        mc.warning(f"Error in 'Auto Smooth Noise': {e}")
        traceback.print_exc()
    finally:
        mc.undoInfo(closeChunk=True)
        if curves_smoothed or visual_nodes_smoothed:
            msg = (
                f"Smoothed {keys_smoothed + visual_keys_smoothed} pop/noise point(s), "
                f"{curves_smoothed} curve(s), {visual_nodes_smoothed} object(s); "
                f"removed {redundant_keys_deleted} redundant keys."
            )
        else:
            msg = "No visual pops or noisy keys found to smooth."
        mc.inViewMessage(amg=f"<hl>{msg}</hl>", pos='midCenter', fade=True)
        print(msg)


# ════════════════════════════════════════════════════════════════════════════════
#                           CUSTOM TITLE BAR
# ════════════════════════════════════════════════════════════════════════════════

class AnimCleanerWindow(ContextPopupWindow):
    """Animation Cleaner Window with modern frameless design."""
    
    def __init__(self, anchor_button=None, parent=None):
        super().__init__(anchor_button=anchor_button, parent=parent)
        
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle('Animation Cleaner')
        self.setFixedSize(300, 230)
        
        # Frameless window
        
        
        
        self._base_opacity = 0.5
        self._hover_opacity = 1.0
        self._scope_index = 1  # Default: Selected Objects
        
        self._setup_ui()
        self.position_window()
        self.setWindowOpacity(self._base_opacity)
    
    def _setup_ui(self):
        """Setup the UI elements."""
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
        self.title_label = QtWidgets.QLabel("Animation Cleaner")
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
        content_layout.setContentsMargins(15, 15, 15, 15)
        content_layout.setSpacing(12)
        
        # Scope selector
        scope_widget = QtWidgets.QWidget()
        scope_layout = QtWidgets.QHBoxLayout(scope_widget)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.setSpacing(8)
        
        # Empty label (removed "Scope:" text)
        scope_label = QtWidgets.QLabel("")
        scope_label.setStyleSheet("color: #AAA; font-size: 11px;")
        scope_layout.addWidget(scope_label)
        
        self.scope_combo = QtWidgets.QComboBox()
        self.scope_combo.addItems([
            "Selected Objects",
            "Selected Channels (ChannelBox)",
            "Selected Keys (Graph Editor)"
        ])
        self.scope_combo.setStyleSheet("""
            QComboBox {
                background-color: #4d4d4d;
                color: #FFF;
                border: 1px solid #666666;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; }
            QComboBox QAbstractItemView {
                background-color: #4d4d4d;
                selection-background-color: #3498DB;
                border: 1px solid #666666;
                color: #FFF;
            }
        """)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_layout.addWidget(self.scope_combo, 1)
        
        content_layout.addWidget(scope_widget)
        
        # Action buttons
        
        # Delete Static Channels (Grey -> Red Hover)
        self.btn_static = QtWidgets.QPushButton("Delete Static Channels")
        self.btn_static.setFixedHeight(36)
        self.btn_static.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_static.setStyleSheet("""
            QPushButton {
                background-color: #4d4d4d;
                color: #BBB;
                border: 1px solid #666666;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover { 
                background-color: #3d2d2d; 
                color: #ff6b6b;
                border-color: #ff6b6b;
            }
            QPushButton:pressed { 
                background-color: #4d3d3d; 
                color: #ff8b8b;
            }
        """)
        self.btn_static.clicked.connect(self._do_delete_static)
        content_layout.addWidget(self.btn_static)
        
        # Delete Redundant Keys (Grey -> Orange Hover)
        self.btn_redundant = QtWidgets.QPushButton("Delete Redundant Keys")
        self.btn_redundant.setFixedHeight(36)
        self.btn_redundant.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_redundant.setStyleSheet("""
            QPushButton {
                background-color: #4d4d4d;
                color: #BBB;
                border: 1px solid #666666;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover { 
                background-color: #3d3d2d; 
                color: #ebcb8b;
                border-color: #ebcb8b;
            }
            QPushButton:pressed { 
                background-color: #4d4d3d; 
                color: #f7e0a3;
            }
        """)
        self.btn_redundant.clicked.connect(self._do_delete_redundant)
        content_layout.addWidget(self.btn_redundant)
        
        # Delete Sub-Frame Keys (Grey -> Blue Hover)
        self.btn_subframe = QtWidgets.QPushButton("Delete Sub-Frame Keys")
        self.btn_subframe.setFixedHeight(36)
        self.btn_subframe.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_subframe.setStyleSheet("""
            QPushButton {
                background-color: #4d4d4d;
                color: #BBB;
                border: 1px solid #666666;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover { 
                background-color: #2d3d4d; 
                color: #88c0d0;
                border-color: #88c0d0;
            }
            QPushButton:pressed { 
                background-color: #3d4d5d; 
                color: #a3dbe8;
            }
        """)
        self.btn_subframe.clicked.connect(self._do_delete_subframe)
        content_layout.addWidget(self.btn_subframe)

        content_layout.addStretch()
        container_layout.addWidget(content)
        main_layout.addWidget(self.container)
    
    def _on_scope_changed(self, index):
        # Map new simplified indices to original scope IDs
        # 0: Selected Objects -> 1
        # 1: Selected Channels -> 2
        # 2: Selected Keys (Graph Editor) -> 6
        scope_map = {
            0: 1,
            1: 2,
            2: 6
        }
        self._scope_index = scope_map.get(index, 1)
    
    def _do_delete_static(self):
        delete_static_channels(self._scope_index)
    
    def _do_delete_redundant(self):
        delete_redundant_keys(self._scope_index)
    
    def _do_delete_subframe(self):
        delete_sub_frame_keys(self._scope_index)

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
        global _cleaner_window
        _cleaner_window = None
        super(AnimCleanerWindow, self).closeEvent(event)


# ════════════════════════════════════════════════════════════════════════════════
#                           PUBLIC FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════════

def show(anchor_button=None):
    """Open the Animation Cleaner window."""
    global _cleaner_window
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows(except_widget=_cleaner_window)
    
    if _cleaner_window is not None:
        existing = uiMod.show_existing_animkey_tool_window(_cleaner_window, anchor_button)
        if existing is not None:
            _cleaner_window = existing
            return _cleaner_window
        _cleaner_window = None
    
    if mc.window(WINDOW_OBJECT, exists=True):
        mc.deleteUI(WINDOW_OBJECT)
    
    _cleaner_window = AnimCleanerWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _cleaner_window.show()
    _cleaner_window.raise_()
    return _cleaner_window


def execute(*args, **kwargs):
    """Main entry point for the button."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.animCleaner.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))
