"""Animation transfer kernel adapted from the user-provided Animo v10.7.5 source.
Only Maya data operations are bundled; AnimKey owns UI, storage and dispatch.
See provenance.json for original source hashes and deliberate interface changes.
"""
from __future__ import division
from __future__ import absolute_import
import json
import os
import sys
import glob
import copy
import time as time_module
import math
from maya import cmds
from maya import mel
import maya.OpenMaya as om
import maya.OpenMayaAnim as oma
try:
    import maya.api.OpenMaya as om2
    import maya.api.OpenMayaAnim as oma2
    API2_AVAILABLE = True
except ImportError:
    API2_AVAILABLE = False
try:
    import __builtin__ as builtins
except ImportError:
    import builtins
try:
    _max = builtins.max
    _min = builtins.min
    _int = builtins.int
    _str = builtins.str
    _range = builtins.range
except Exception:
    _max = max
    _min = min
    _int = int
    _str = str
    _range = range
if API2_AVAILABLE:
    TANGENT_ENUM_MAP = {'global': oma2.MFnAnimCurve.kTangentGlobal, 'fixed': oma2.MFnAnimCurve.kTangentFixed, 'linear': oma2.MFnAnimCurve.kTangentLinear, 'flat': oma2.MFnAnimCurve.kTangentFlat, 'smooth': oma2.MFnAnimCurve.kTangentSmooth, 'spline': oma2.MFnAnimCurve.kTangentSmooth, 'step': oma2.MFnAnimCurve.kTangentStep, 'stepped': oma2.MFnAnimCurve.kTangentStep, 'slow': oma2.MFnAnimCurve.kTangentSlow, 'fast': oma2.MFnAnimCurve.kTangentFast, 'clamped': oma2.MFnAnimCurve.kTangentClamped, 'plateau': oma2.MFnAnimCurve.kTangentPlateau, 'stepnext': oma2.MFnAnimCurve.kTangentStepNext, 'stepNext': oma2.MFnAnimCurve.kTangentStepNext, 'auto': oma2.MFnAnimCurve.kTangentAuto}
    INFINITY_ENUM_MAP = {'constant': oma2.MFnAnimCurve.kConstant, 'linear': oma2.MFnAnimCurve.kLinear, 'cycle': oma2.MFnAnimCurve.kCycle, 'cycleRelative': oma2.MFnAnimCurve.kCycleRelative, 'oscillate': oma2.MFnAnimCurve.kOscillate}
    TANGENT_ENUM_TO_STRING = {oma2.MFnAnimCurve.kTangentGlobal: 'global', oma2.MFnAnimCurve.kTangentFixed: 'fixed', oma2.MFnAnimCurve.kTangentLinear: 'linear', oma2.MFnAnimCurve.kTangentFlat: 'flat', oma2.MFnAnimCurve.kTangentSmooth: 'spline', oma2.MFnAnimCurve.kTangentStep: 'step', oma2.MFnAnimCurve.kTangentSlow: 'slow', oma2.MFnAnimCurve.kTangentFast: 'fast', oma2.MFnAnimCurve.kTangentClamped: 'clamped', oma2.MFnAnimCurve.kTangentPlateau: 'plateau', oma2.MFnAnimCurve.kTangentStepNext: 'stepnext', oma2.MFnAnimCurve.kTangentAuto: 'auto'}
else:
    TANGENT_ENUM_MAP = {}
    INFINITY_ENUM_MAP = {}
    TANGENT_ENUM_TO_STRING = {}

def _is_linear_attr(obj_attr):
    try:
        return cmds.getAttr(obj_attr, type=True) in ('doubleLinear', 'floatLinear')
    except Exception:
        return False

def _ui_to_internal_factor(obj_attr):
    if _is_linear_attr(obj_attr):
        return om.MDistance.uiToInternal(1.0)
    return 1.0

def _internal_to_ui_factor(obj_attr):
    if _is_linear_attr(obj_attr):
        return om.MDistance.internalToUI(1.0)
    return 1.0

def _scale_tangent_angle(angle_degrees, factor):
    radians = math.radians(angle_degrees)
    return math.degrees(math.atan2(math.sin(radians) * factor, math.cos(radians)))

class OpenMayaAnimHelper(object):

    @staticmethod
    def get_mobj_from_name(name):
        sel = om.MSelectionList()
        try:
            sel.add(name)
            mobj = om.MObject()
            sel.getDependNode(0, mobj)
            return mobj
        except Exception:
            return None

    @staticmethod
    def get_plug_from_attr(obj_attr):
        try:
            sel = om.MSelectionList()
            sel.add(obj_attr)
            plug = om.MPlug()
            sel.getPlug(0, plug)
            return plug
        except Exception:
            return None

    @staticmethod
    def get_anim_curve_from_plug(plug):
        if plug is None or plug.isNull():
            return None
        try:
            if plug.isConnected():
                connections = om.MPlugArray()
                plug.connectedTo(connections, True, False)
                for i in _range(connections.length()):
                    node = connections[i].node()
                    if node.hasFn(om.MFn.kAnimCurve):
                        return oma.MFnAnimCurve(node)
            return None
        except Exception:
            return None

    @staticmethod
    def get_curve_data_fast(curve_fn):
        if curve_fn is None:
            return None
        try:
            num_keys = curve_fn.numKeys()
            if num_keys == 0:
                return None
            times = []
            values = []
            for i in _range(num_keys):
                time_val = curve_fn.time(i)
                times.append(time_val.value())
                values.append(curve_fn.value(i))
            return {'times': times, 'values': values, 'num_keys': num_keys}
        except Exception:
            return None

    @staticmethod
    def get_selected_objects():
        sel = om.MSelectionList()
        om.MGlobal.getActiveSelectionList(sel)
        objects = []
        for i in _range(sel.length()):
            try:
                dag_path = om.MDagPath()
                sel.getDagPath(i, dag_path)
                objects.append(dag_path.fullPathName())
            except Exception:
                try:
                    mobj = om.MObject()
                    sel.getDependNode(i, mobj)
                    dep_fn = om.MFnDependencyNode(mobj)
                    objects.append(dep_fn.name())
                except Exception:
                    pass
        return objects

    @staticmethod
    def get_attribute_value_fast(obj_attr):
        plug = OpenMayaAnimHelper.get_plug_from_attr(obj_attr)
        if plug is None:
            return None
        try:
            attr_obj = plug.attribute()
            api_type = attr_obj.apiType()
            if api_type in (om.MFn.kNumericAttribute, om.MFn.kDoubleLinearAttribute, om.MFn.kDoubleAngleAttribute, om.MFn.kTimeAttribute):
                return plug.asDouble()
            elif api_type == om.MFn.kTypedAttribute:
                return plug.asString()
            else:
                return plug.asDouble()
        except Exception:
            return None

    @staticmethod
    def set_attribute_value_fast(obj_attr, value):
        plug = OpenMayaAnimHelper.get_plug_from_attr(obj_attr)
        if plug is None:
            return False
        try:
            if plug.isLocked():
                return False
            plug.setDouble(float(value))
            return True
        except Exception:
            return False

    @staticmethod
    def set_attribute_value_api2(obj_attr, value):
        """Set attribute value using API 2.0 - faster than cmds"""
        if not API2_AVAILABLE:
            return False
        try:
            sel = om2.MSelectionList()
            sel.add(obj_attr)
            plug = sel.getPlug(0)
            if plug.isLocked:
                return False
            plug.setDouble(float(value))
            return True
        except:
            return False

    @staticmethod
    def get_plug_api2(obj_attr):
        if not API2_AVAILABLE:
            return None
        try:
            sel = om2.MSelectionList()
            sel.add(obj_attr)
            return sel.getPlug(0)
        except Exception:
            return None

    @staticmethod
    def get_or_create_anim_curve_api2(obj_attr):
        if not API2_AVAILABLE:
            return None
        try:
            plug = OpenMayaAnimHelper.get_plug_api2(obj_attr)
            if plug is None:
                return None
            if plug.isConnected:
                source = plug.source()
                if not source.isNull:
                    node = source.node()
                    if node.hasFn(om2.MFn.kAnimCurve):
                        return oma2.MFnAnimCurve(node)
            attr_obj = plug.attribute()
            if attr_obj.hasFn(om2.MFn.kDoubleLinearAttribute):
                curve_type = oma2.MFnAnimCurve.kAnimCurveTL
            elif attr_obj.hasFn(om2.MFn.kDoubleAngleAttribute):
                curve_type = oma2.MFnAnimCurve.kAnimCurveTA
            elif attr_obj.hasFn(om2.MFn.kTimeAttribute):
                curve_type = oma2.MFnAnimCurve.kAnimCurveTT
            else:
                curve_type = oma2.MFnAnimCurve.kAnimCurveTU
            curve_fn = oma2.MFnAnimCurve()
            curve_fn.create(plug, curve_type)
            return curve_fn
        except Exception:
            return None

    @staticmethod
    def get_anim_curve_fn_api2(curve_name):
        """Get MFnAnimCurve from anim curve node name using API 2.0"""
        if not API2_AVAILABLE:
            return None
        try:
            sel = om2.MSelectionList()
            sel.add(curve_name)
            node = sel.getDependNode(0)
            if node.hasFn(om2.MFn.kAnimCurve):
                return oma2.MFnAnimCurve(node)
            return None
        except Exception:
            return None

    @staticmethod
    def get_curve_data_api2(curve_name, key_times=None):
        """Read keyframe and tangent data from curve using API 2.0
        
        Returns: (keyframe_data, tangent_data, weighted, is_angle) or None
        keyframe_data: [[time, value, breakdown], ...]
        tangent_data: [[in_type, out_type, in_angle, in_weight, out_angle, out_weight, lock, weight_lock], ...]
        """
        if not API2_AVAILABLE:
            return None
        try:
            curve_fn = OpenMayaAnimHelper.get_anim_curve_fn_api2(curve_name)
            if curve_fn is None:
                return None
            num_keys = curve_fn.numKeys
            if num_keys == 0:
                return None
            weighted = curve_fn.isWeighted
            is_angle = curve_fn.animCurveType == oma2.MFnAnimCurve.kAnimCurveTA
            keyframe_data = []
            tangent_data = []
            if key_times is not None:
                key_times_set = set(key_times)
            else:
                key_times_set = None
            for i in range(num_keys):
                time_val = curve_fn.input(i).value
                if key_times_set is not None and time_val not in key_times_set:
                    continue
                value = curve_fn.value(i)
                if is_angle:
                    value = math.degrees(value)
                breakdown = curve_fn.isBreakdown(i)
                in_type_enum = curve_fn.inTangentType(i)
                out_type_enum = curve_fn.outTangentType(i)
                (in_angle_obj, in_weight) = curve_fn.getTangentAngleWeight(i, True)
                (out_angle_obj, out_weight) = curve_fn.getTangentAngleWeight(i, False)
                in_angle = in_angle_obj.asDegrees()
                out_angle = out_angle_obj.asDegrees()
                lock = curve_fn.tangentsLocked(i)
                weight_lock = curve_fn.weightsLocked(i)
                in_type_str = TANGENT_ENUM_TO_STRING.get(in_type_enum, 'auto')
                out_type_str = TANGENT_ENUM_TO_STRING.get(out_type_enum, 'auto')
                keyframe_data.append([time_val, value, breakdown])
                tangent_data.append([in_type_str, out_type_str, in_angle, in_weight, out_angle, out_weight, lock, weight_lock])
            return (keyframe_data, tangent_data, weighted, is_angle)
        except Exception:
            return None

    @staticmethod
    def get_curve_name_from_attr(obj_attr):
        """Get anim curve node name from attribute string"""
        if not API2_AVAILABLE:
            return None
        try:
            plug = OpenMayaAnimHelper.get_plug_api2(obj_attr)
            if plug is None or not plug.isConnected:
                return None
            source = plug.source()
            if source.isNull:
                return None
            node = source.node()
            if node.hasFn(om2.MFn.kAnimCurve):
                return om2.MFnDependencyNode(node).name()
            return None
        except Exception:
            return None

    @staticmethod
    def apply_curve_batch_api2(obj_attr, keyframe_data, tangent_data, weighted, infinity, time_offset):
        if not API2_AVAILABLE or not keyframe_data:
            return False
        try:
            curve_fn = OpenMayaAnimHelper.get_or_create_anim_curve_api2(obj_attr)
            if curve_fn is None:
                return False
            curve_fn.setIsWeighted(weighted if weighted else False)
            value_factor = _internal_to_ui_factor(obj_attr)
            if infinity:
                pre_inf = INFINITY_ENUM_MAP.get(infinity[0], oma2.MFnAnimCurve.kConstant)
                post_inf = INFINITY_ENUM_MAP.get(infinity[1], oma2.MFnAnimCurve.kConstant) if len(infinity) > 1 else oma2.MFnAnimCurve.kConstant
                curve_fn.setPreInfinityType(pre_inf)
                curve_fn.setPostInfinityType(post_inf)
            for kf in keyframe_data:
                time_val = kf[0] + time_offset
                value = float(kf[1]) * value_factor
                try:
                    cmds.setKeyframe(obj_attr, time=time_val, value=value)
                except:
                    pass
            all_times = []
            in_types = []
            out_types = []
            in_angles = []
            in_weights = []
            out_angles = []
            out_weights = []
            breakdown_times = []
            lock_times = []
            weight_lock_times = []
            for (i, tangent) in enumerate(tangent_data):
                if i >= len(keyframe_data):
                    break
                time_val = keyframe_data[i][0] + time_offset
                all_times.append(time_val)
                in_type = tangent[0] if len(tangent) > 0 else 'auto'
                out_type = tangent[1] if len(tangent) > 1 else 'auto'
                in_types.append(in_type)
                out_types.append(out_type)
                in_angles.append(_scale_tangent_angle(float(tangent[2]), value_factor) if len(tangent) > 2 else 0.0)
                in_weights.append(float(tangent[3]) if len(tangent) > 3 else 1.0)
                out_angles.append(_scale_tangent_angle(float(tangent[4]), value_factor) if len(tangent) > 4 else 0.0)
                out_weights.append(float(tangent[5]) if len(tangent) > 5 else 1.0)
                lock = tangent[6] if len(tangent) > 6 else False
                weight_lock = tangent[7] if len(tangent) > 7 else False
                if len(keyframe_data[i]) > 2 and keyframe_data[i][2]:
                    breakdown_times.append(time_val)
                if lock:
                    lock_times.append(time_val)
                if weight_lock:
                    weight_lock_times.append(time_val)
            unique_in_types = set(in_types)
            unique_out_types = set(out_types)
            for time_val in all_times:
                try:
                    cmds.keyTangent(obj_attr, time=(time_val, time_val), lock=False)
                except:
                    pass
            if len(unique_in_types) == 1 and list(unique_in_types)[0] != 'fixed':
                try:
                    cmds.keyTangent(obj_attr, inTangentType=list(unique_in_types)[0])
                except:
                    pass
            else:
                for (i, time_val) in enumerate(all_times):
                    t = (time_val, time_val)
                    try:
                        if in_types[i] == 'fixed':
                            cmds.keyTangent(obj_attr, time=t, inTangentType='fixed', inAngle=in_angles[i], inWeight=in_weights[i])
                        else:
                            cmds.keyTangent(obj_attr, time=t, inTangentType=in_types[i])
                    except:
                        pass
            if len(unique_out_types) == 1 and list(unique_out_types)[0] != 'fixed':
                try:
                    cmds.keyTangent(obj_attr, outTangentType=list(unique_out_types)[0])
                except:
                    pass
            else:
                for (i, time_val) in enumerate(all_times):
                    t = (time_val, time_val)
                    try:
                        if out_types[i] == 'fixed':
                            cmds.keyTangent(obj_attr, time=t, outTangentType='fixed', outAngle=out_angles[i], outWeight=out_weights[i])
                        else:
                            cmds.keyTangent(obj_attr, time=t, outTangentType=out_types[i])
                    except:
                        pass
            for t in breakdown_times:
                try:
                    cmds.keyframe(obj_attr, time=(t, t), breakdown=True)
                except:
                    pass
            if lock_times:
                for t in lock_times:
                    try:
                        cmds.keyTangent(obj_attr, time=(t, t), lock=True)
                    except:
                        pass
            if weight_lock_times:
                for t in weight_lock_times:
                    try:
                        cmds.keyTangent(obj_attr, time=(t, t), weightLock=True)
                    except:
                        pass
            return True
        except Exception:
            return False

class AnimationTransfer:

    def get_rotation_order_name(self, value):
        rot_order_names = {0: 'xyz', 1: 'yzx', 2: 'zxy', 3: 'xzy', 4: 'yxz', 5: 'zyx'}
        return rot_order_names.get(value, 'xyz')

    def get_rotation_orders_from_objects(self, objects):
        rotation_orders = {}
        for obj in objects:
            if not obj or not cmds.objExists(obj):
                continue
            try:
                short_name = obj.split('|')[-1]
                if ':' in short_name:
                    base_name = short_name.split(':')[-1]
                else:
                    base_name = short_name
                rot_order_value = cmds.getAttr(obj + '.rotateOrder')
                rotation_orders[base_name] = {'short_name': short_name, 'rotation_order': self.get_rotation_order_name(rot_order_value), 'rotation_order_value': rot_order_value}
            except Exception:
                pass
        return rotation_orders

    def check_rotation_order_differences(self, saved_rotation_orders, target_objects):
        if not saved_rotation_orders:
            return []
        differences = []
        for obj in target_objects:
            if not obj or not cmds.objExists(obj):
                continue
            try:
                short_name = obj.split('|')[-1]
                if ':' in short_name:
                    base_name = short_name.split(':')[-1]
                else:
                    base_name = short_name
                saved_data = saved_rotation_orders.get(base_name)
                if not saved_data:
                    continue
                current_order = cmds.getAttr(obj + '.rotateOrder')
                saved_order = saved_data.get('rotation_order_value', 0)
                if current_order != saved_order:
                    differences.append({'object': obj, 'current_order': current_order, 'saved_order': saved_order, 'current_order_name': self.get_rotation_order_name(current_order), 'saved_order_name': self.get_rotation_order_name(saved_order)})
            except Exception:
                pass
        return differences

    def change_rotation_order(self, obj, target_order):
        if not cmds.objExists(obj):
            return False
        try:
            target_order_name = self.get_rotation_order_name(target_order)
            current_time = cmds.currentTime(query=True)
            keys = cmds.keyframe(obj, attribute=['rx', 'ry', 'rz'], query=True, timeChange=True)
            if keys:
                keys = sorted(list(set(keys)))
                temp_loc = cmds.spaceLocator(name='roo_tempLoc')[0]
                try:
                    cmds.refresh(suspend=True)
                    cmds.evaluationManager(mode='off')
                    for key in keys:
                        cmds.currentTime(key)
                        cmds.matchTransform(temp_loc, obj, rotation=True)
                        cmds.setKeyframe(temp_loc, attribute=['rx', 'ry', 'rz'])
                    cmds.cutKey(obj, attribute='rotateOrder', clear=True)
                    cmds.xform(obj, rotateOrder=target_order_name, preserve=True)
                    for key in keys:
                        cmds.currentTime(key)
                        cmds.matchTransform(obj, temp_loc, rotation=True)
                        cmds.setKeyframe(obj, attribute=['rx', 'ry', 'rz'])
                    cmds.currentTime(current_time)
                    cmds.filterCurve(obj + '.rx', obj + '.ry', obj + '.rz')
                    cmds.selectKey(clear=True)
                finally:
                    try:
                        cmds.refresh(suspend=False)
                    except:
                        pass
                    try:
                        cmds.evaluationManager(mode='parallel')
                    except:
                        pass
                    if cmds.objExists(temp_loc):
                        cmds.delete(temp_loc)
            else:
                cmds.cutKey(obj, attribute='rotateOrder', clear=True)
                cmds.xform(obj, rotateOrder=target_order_name, preserve=True)
            return True
        except Exception:
            return False

    def apply_rotation_order_changes(self, differences):
        for diff in differences:
            self.change_rotation_order(diff['object'], diff['saved_order'])

    def get_anim_curves(self, force_graph_editor=False):
        anim_curves = cmds.keyframe(query=True, name=True, selected=True)
        visible_panels = cmds.getPanel(visiblePanels=True)
        graph_editor = 'graphEditor1' in visible_panels
        get_from = 'graphEditor'
        if not anim_curves or (not graph_editor and (not force_graph_editor)):
            get_from = 'timeline'
            try:
                playback_slider = mel.eval('$temp=$gPlayBackSlider')
                anim_curves = cmds.timeControl(playback_slider, query=True, animCurveNames=True)
            except Exception:
                sel = cmds.ls(selection=True)
                if sel:
                    anim_curves = cmds.keyframe(sel, query=True, name=True)
                else:
                    anim_curves = []
        return [anim_curves, get_from]

    def get_timeline_range(self, float_val=True):
        try:
            playback_slider = mel.eval('$temp=$gPlayBackSlider')
            range_val = cmds.timeControl(playback_slider, query=True, rangeArray=True)
            if float_val:
                range_val[1] -= 0.0001
            return range_val
        except Exception:
            start = cmds.playbackOptions(query=True, minTime=True)
            end = cmds.playbackOptions(query=True, maxTime=True)
            if float_val:
                end -= 0.0001
            return [start, end]

    def get_objects_and_attributes(self, anim_curves):
        if not anim_curves:
            return [[], []]
        objects = []
        attributes = []
        for node in anim_curves:
            if not cmds.objExists(node):
                objects.append(None)
                attributes.append(None)
                continue
            try:
                mobj = self.om_helper.get_mobj_from_name(node.split('.')[0])
                if mobj:
                    curve_fn = oma.MFnAnimCurve(mobj)
                    plug_array = om.MPlugArray()
                    try:
                        curve_fn.getDestinations(plug_array)
                        if plug_array.length() > 0:
                            dest_plug = plug_array[0]
                            obj_name = om.MFnDependencyNode(dest_plug.node()).name()
                            attr_name = dest_plug.partialName(False, False, False, False, False, True)
                            objects.append(obj_name)
                            attributes.append(attr_name)
                            continue
                    except Exception:
                        pass
                connections = cmds.listConnections('{}.output'.format(node.split('.')[0]), source=False, destination=True, plugs=True, skipConversionNodes=True)
                if connections:
                    target = connections[0]
                    obj = target.split('.')[0]
                    attr = target.split('.')[-1]
                    objects.append(obj)
                    attributes.append(attr)
                else:
                    objects.append(None)
                    attributes.append(None)
            except Exception:
                objects.append(None)
                attributes.append(None)
        return [objects, attributes]

    def get_keys_in_range(self, anim_curves, get_from, range_all=None):
        if not anim_curves:
            return []
        keys_sel = []
        if get_from == 'graphEditor':
            for node in anim_curves:
                selected_keys = cmds.keyframe(node, selected=True, query=True, timeChange=True)
                keys_sel.append(selected_keys if selected_keys else [])
        else:
            if range_all is None:
                timeline_range = self.get_timeline_range()
            else:
                timeline_range = None
            for node in anim_curves:
                if not cmds.objExists(node):
                    keys_sel.append([])
                    continue
                mobj = self.om_helper.get_mobj_from_name(node)
                if mobj:
                    try:
                        curve_fn = oma.MFnAnimCurve(mobj)
                        num_keys = curve_fn.numKeys()
                        all_keys = [curve_fn.time(i).value() for i in _range(num_keys)]
                    except Exception:
                        all_keys = cmds.keyframe(node, query=True, timeChange=True)
                else:
                    all_keys = cmds.keyframe(node, query=True, timeChange=True)
                if not all_keys:
                    keys_sel.append([])
                    continue
                if range_all or not timeline_range:
                    keys_sel.append(all_keys)
                else:
                    range_keys = [key for key in all_keys if timeline_range[0] <= key < timeline_range[1]]
                    keys_sel.append(range_keys)
        return keys_sel

    def get_all_animatable_attributes(self, objects):
        all_obj_attrs = []
        common_attrs = ['translateX', 'translateY', 'translateZ', 'rotateX', 'rotateY', 'rotateZ', 'scaleX', 'scaleY', 'scaleZ', 'visibility']
        for obj in objects:
            if not cmds.objExists(obj):
                continue
            obj_attrs = []
            for attr in common_attrs:
                full_attr = '{}.{}'.format(obj, attr)
                if cmds.objExists(full_attr) and cmds.getAttr(full_attr, keyable=True):
                    obj_attrs.append(full_attr)
            try:
                keyable_attrs = cmds.listAttr(obj, keyable=True) or []
                for attr in keyable_attrs:
                    if attr not in common_attrs:
                        full_attr = '{}.{}'.format(obj, attr)
                        if cmds.objExists(full_attr):
                            obj_attrs.append(full_attr)
            except Exception:
                pass
            all_obj_attrs.extend(obj_attrs)
        return all_obj_attrs

    def copy_animation(self, range_mode='selected'):
        cmds.waitCursor(state=True)
        try:
            if range_mode == 'all':
                selected_objects = self.om_helper.get_selected_objects()
                if not selected_objects:
                    selected_objects = cmds.ls(selection=True)
                if not selected_objects:
                    cmds.warning('No objects selected. Please select controls to copy all their animation.')
                    return None
                all_obj_attrs = self.get_all_animatable_attributes(selected_objects)
                if not all_obj_attrs:
                    cmds.warning('No animatable attributes found on selected objects.')
                    return None
                anim_data = self.get_anim_data_from_attributes(all_obj_attrs, range_all=True)
            else:
                anim_data = self.get_anim_data()
            return anim_data
        finally:
            cmds.waitCursor(state=False)

    def get_anim_data_from_attributes(self, obj_attrs, range_all=False):
        if not obj_attrs:
            cmds.warning('No object attributes found.')
            return None
        current_time = cmds.currentTime(query=True)
        anim_data = {'objects': [], 'animData': []}
        objects_added = set()
        for obj_attr in obj_attrs:
            if not cmds.objExists(obj_attr):
                continue
            obj_name = obj_attr.split('.')[0]
            if obj_name not in objects_added:
                anim_data['objects'].append(obj_name)
                objects_added.add(obj_name)
            plug = self.om_helper.get_plug_from_attr(obj_attr)
            curve_fn = self.om_helper.get_anim_curve_from_plug(plug) if plug else None
            if curve_fn and curve_fn.numKeys() > 0:
                try:
                    weighted = cmds.keyTangent(obj_attr, query=True, weightedTangents=True)
                    if weighted:
                        weighted = weighted[0]
                    else:
                        weighted = False
                    infinity = cmds.setInfinity(obj_attr, query=True, preInfinite=True, postInfinite=True)
                except Exception:
                    weighted = False
                    infinity = ['constant', 'constant']
                curve_data = {'objAttr': obj_attr, 'curveData': [weighted, infinity], 'keyframeData': [], 'tangentData': []}
                try:
                    if range_all:
                        curve_name = OpenMayaAnimHelper.get_curve_name_from_attr(obj_attr)
                        if curve_name and API2_AVAILABLE:
                            api_data = OpenMayaAnimHelper.get_curve_data_api2(curve_name, None)
                            if api_data:
                                curve_data['keyframeData'] = api_data[0]
                                curve_data['tangentData'] = api_data[1]
                            else:
                                raise Exception('API 2.0 failed, use fallback')
                        else:
                            raise Exception('No curve name, use fallback')
                except Exception:
                    try:
                        num_keys = curve_fn.numKeys()
                        time_change = [curve_fn.time(i).value() for i in _range(num_keys)]
                        value_change = [curve_fn.value(i) for i in _range(num_keys)]
                        angle_factor = _ui_to_internal_factor(obj_attr)
                        breakdowns = cmds.keyframe(obj_attr, query=True, breakdown=True)
                        in_tangent_type = cmds.keyTangent(obj_attr, query=True, inTangentType=True)
                        out_tangent_type = cmds.keyTangent(obj_attr, query=True, outTangentType=True)
                        in_angle = cmds.keyTangent(obj_attr, query=True, inAngle=True)
                        in_weight = cmds.keyTangent(obj_attr, query=True, inWeight=True)
                        out_angle = cmds.keyTangent(obj_attr, query=True, outAngle=True)
                        out_weight = cmds.keyTangent(obj_attr, query=True, outWeight=True)
                        lock = cmds.keyTangent(obj_attr, query=True, lock=True)
                        weight_lock = cmds.keyTangent(obj_attr, query=True, weightLock=True)
                        for (j, key_time) in enumerate(time_change):
                            breakdown = key_time in breakdowns if breakdowns else False
                            keyframe = [time_change[j], value_change[j], breakdown]
                            tangent = [in_tangent_type[j], out_tangent_type[j], _scale_tangent_angle(in_angle[j], angle_factor), in_weight[j], _scale_tangent_angle(out_angle[j], angle_factor), out_weight[j], lock[j], weight_lock[j]]
                            curve_data['keyframeData'].append(keyframe)
                            curve_data['tangentData'].append(tangent)
                    except Exception:
                        continue
                anim_data['animData'].append(curve_data)
            else:
                try:
                    current_value = self.om_helper.get_attribute_value_fast(obj_attr)
                    if current_value is None:
                        current_value = cmds.getAttr(obj_attr)
                        if isinstance(current_value, (int, float)):
                            current_value = current_value * _ui_to_internal_factor(obj_attr)
                    curve_data = {'objAttr': obj_attr, 'curveData': [False, ['constant', 'constant']], 'keyframeData': [[current_time, current_value, False]], 'tangentData': [['auto', 'auto', 0, 0, 0, 0, False, False]]}
                    anim_data['animData'].append(curve_data)
                except Exception:
                    continue
        return anim_data

    def get_anim_data(self, anim_curves=None, range_all=False):
        if anim_curves is None:
            get_curves = self.get_anim_curves(True)
            anim_curves = get_curves[0]
            get_from = get_curves[1]
        else:
            get_from = None
        if not anim_curves:
            cmds.warning('No animation curves found.')
            return None
        if get_from is None:
            keys_sel = self.get_keys_in_range(anim_curves, 'timeline', range_all=True)
        else:
            keys_sel = self.get_keys_in_range(anim_curves, get_from)
        if not keys_sel or all((not keys for keys in keys_sel)):
            cmds.warning('No keys found to copy.')
            return None
        objs_attrs = self.get_objects_and_attributes(anim_curves)
        objects = objs_attrs[0]
        attributes = objs_attrs[1]
        anim_data = {'objects': objects, 'animData': []}
        for (i, curve) in enumerate(anim_curves):
            if objects[i] is None or not keys_sel[i]:
                continue
            obj_attr = '{}.{}'.format(objects[i], attributes[i])
            try:
                weighted = cmds.keyTangent(curve, query=True, weightedTangents=True)
                if weighted:
                    weighted = weighted[0]
                else:
                    weighted = False
                infinity = cmds.setInfinity(obj_attr, query=True, preInfinite=True, postInfinite=True)
            except Exception:
                weighted = False
                infinity = ['constant', 'constant']
            curve_data = {'objAttr': obj_attr, 'curveData': [weighted, infinity], 'keyframeData': [], 'tangentData': []}
            try:
                if API2_AVAILABLE:
                    api_data = OpenMayaAnimHelper.get_curve_data_api2(curve, keys_sel[i])
                    if api_data:
                        curve_data['keyframeData'] = api_data[0]
                        curve_data['tangentData'] = api_data[1]
                    else:
                        raise Exception('API 2.0 failed, use fallback')
                else:
                    raise Exception('API 2.0 not available, use fallback')
            except Exception:
                time_range = (keys_sel[i][0], keys_sel[i][-1])
                try:
                    time_change = cmds.keyframe(curve, query=True, time=time_range, timeChange=True)
                    value_change = cmds.keyframe(curve, query=True, time=time_range, valueChange=True)
                    unit_factor = _ui_to_internal_factor(obj_attr)
                    value_change = [v * unit_factor for v in value_change] if value_change else value_change
                    breakdowns = cmds.keyframe(curve, query=True, time=time_range, breakdown=True)
                    in_tangent_type = cmds.keyTangent(curve, query=True, time=time_range, inTangentType=True)
                    out_tangent_type = cmds.keyTangent(curve, query=True, time=time_range, outTangentType=True)
                    in_angle = cmds.keyTangent(curve, query=True, time=time_range, inAngle=True)
                    in_weight = cmds.keyTangent(curve, query=True, time=time_range, inWeight=True)
                    out_angle = cmds.keyTangent(curve, query=True, time=time_range, outAngle=True)
                    out_weight = cmds.keyTangent(curve, query=True, time=time_range, outWeight=True)
                    lock = cmds.keyTangent(curve, query=True, time=time_range, lock=True)
                    weight_lock = cmds.keyTangent(curve, query=True, time=time_range, weightLock=True)
                    for (j, key_time) in enumerate(keys_sel[i]):
                        if key_time in time_change:
                            idx = time_change.index(key_time)
                            breakdown = key_time in breakdowns if breakdowns else False
                            keyframe = [time_change[idx], value_change[idx], breakdown]
                            tangent = [in_tangent_type[idx], out_tangent_type[idx], _scale_tangent_angle(in_angle[idx], unit_factor), in_weight[idx], _scale_tangent_angle(out_angle[idx], unit_factor), out_weight[idx], lock[idx], weight_lock[idx]]
                            curve_data['keyframeData'].append(keyframe)
                            curve_data['tangentData'].append(tangent)
                except Exception:
                    continue
            anim_data['animData'].append(curve_data)
        return anim_data

    def copy_all_animation_to_json(self):
        cmds.waitCursor(state=True)
        try:
            selected_objects = cmds.ls(selection=True)
            if not selected_objects:
                cmds.warning('No objects selected. Please select controls to copy all their animation.')
                return None
            cmds.waitCursor(state=False)
            if self.check_objects_in_animation_layers(selected_objects):
                cmds.waitCursor(state=True)
                return None
            cmds.waitCursor(state=True)
            all_anim_curves = []
            objects_with_curves = set()
            for obj in selected_objects:
                curves = cmds.keyframe(obj, query=True, name=True)
                if curves:
                    all_anim_curves.extend(curves)
                    objects_with_curves.add(obj)
            if all_anim_curves:
                anim_data = self.get_anim_data(anim_curves=all_anim_curves, range_all=True)
            else:
                anim_data = {'objects': [], 'animData': []}
            if anim_data is None:
                anim_data = {'objects': [], 'animData': []}
            current_time = cmds.currentTime(query=True)
            for obj in selected_objects:
                if obj in objects_with_curves:
                    continue
                short_name = obj.split('|')[-1]
                if short_name not in anim_data['objects']:
                    anim_data['objects'].append(short_name)
                attrs = cmds.listAttr(obj, keyable=True, unlocked=True) or []
                for attr in attrs:
                    obj_attr = '{}.{}'.format(obj, attr)
                    short_obj_attr = '{}.{}'.format(short_name, attr)
                    try:
                        if not cmds.objExists(obj_attr):
                            continue
                        value = cmds.getAttr(obj_attr)
                        if value is None or isinstance(value, (list, tuple)):
                            continue
                        if isinstance(value, float):
                            value = value * _ui_to_internal_factor(obj_attr)
                        anim_data['animData'].append({'objAttr': short_obj_attr, 'curveData': [False, ['constant', 'constant']], 'keyframeData': [[current_time, value, False]], 'tangentData': [['auto', 'auto', 1.0, 0.0, 1.0, 0.0, False, False]]})
                    except Exception:
                        continue
            if not anim_data['animData']:
                cmds.warning('No animation or attribute data found on selected objects.')
                return None
            return self.save_to_json(anim_data, selected_objects=selected_objects)
        finally:
            cmds.waitCursor(state=False)

    def create_dummy_key(self, objects):
        if not objects:
            return []
        keyed_objects = []
        for obj in objects:
            if not obj or not cmds.objExists(obj):
                continue
            try:
                cmds.setKeyframe(obj, time=(-50000, -50000), insert=False)
                keyed_objects.append(obj)
            except Exception:
                pass
        return keyed_objects

    def delete_dummy_key(self, objects):
        if not objects:
            return
        for obj in objects:
            if not obj or not cmds.objExists(obj):
                continue
            try:
                cmds.cutKey(obj, time=(-50000, -50000), clear=True)
            except Exception:
                pass

    def remap_namespace_in_data(self, anim_data, source_namespace, target_namespace):
        if not anim_data or 'animData' not in anim_data:
            return anim_data
        remapped_data = copy.deepcopy(anim_data)
        if source_namespace is None:
            source_namespace = self.detect_source_namespace(anim_data)
        source_ns = source_namespace + ':' if source_namespace else ''
        target_ns = target_namespace + ':' if target_namespace else ''
        if source_ns == target_ns:
            return remapped_data
        for anim_item in remapped_data['animData']:
            if 'objAttr' in anim_item and anim_item['objAttr']:
                obj_attr = anim_item['objAttr']
                if obj_attr.startswith(source_ns):
                    new_obj_attr = obj_attr.replace(source_ns, target_ns, 1)
                    anim_item['objAttr'] = new_obj_attr
                elif not source_ns and target_ns:
                    anim_item['objAttr'] = target_ns + obj_attr
        return remapped_data

    def detect_source_namespace(self, anim_data):
        if not anim_data or 'animData' not in anim_data:
            return ''
        namespace_counts = {}
        for anim_item in anim_data['animData']:
            if 'objAttr' in anim_item and anim_item['objAttr']:
                obj_attr = anim_item['objAttr']
                if ':' in obj_attr:
                    namespace = obj_attr.split(':')[0]
                    namespace_counts[namespace] = namespace_counts.get(namespace, 0) + 1
                else:
                    namespace_counts[''] = namespace_counts.get('', 0) + 1
        if namespace_counts:
            most_common = _max(namespace_counts, key=namespace_counts.get)
            return most_common
        return ''

    def apply_animation_data(self, anim_data, paste_in_place=True, only_selected_nodes=False, target_namespace=None, skip_undo_chunk=False):
        if not anim_data or 'animData' not in anim_data:
            cmds.warning('Invalid animation data.')
            return False
        try:
            if target_namespace is not None:
                source_namespace = self.detect_source_namespace(anim_data)
                anim_data = self.remap_namespace_in_data(anim_data, source_namespace, target_namespace)
            if only_selected_nodes:
                selected_objects = cmds.ls(selection=True)
                if not selected_objects:
                    cmds.warning('No objects selected for paste operation.')
                    return False
                valid_objects = set()
                for sel_obj in selected_objects:
                    valid_objects.add(sel_obj)
                    short_name = sel_obj.split('|')[-1]
                    valid_objects.add(short_name)
                    try:
                        descendants = cmds.listRelatives(sel_obj, allDescendents=True) or []
                        valid_objects.update(descendants)
                    except:
                        pass
                existing_attrs = set()
                for sel_obj in selected_objects:
                    try:
                        attrs = cmds.listAttr(sel_obj, keyable=True) or []
                        for attr in attrs:
                            existing_attrs.add('{}.{}'.format(sel_obj, attr))
                            short_name = sel_obj.split('|')[-1]
                            existing_attrs.add('{}.{}'.format(short_name, attr))
                    except:
                        pass
                filtered_anim_data = []
                for anim_item in anim_data['animData']:
                    obj_attr = anim_item['objAttr']
                    if obj_attr:
                        obj_name = obj_attr.split('.')[0]
                        if obj_name in valid_objects or obj_attr in existing_attrs:
                            if cmds.objExists(obj_attr) or cmds.objExists(obj_name):
                                filtered_anim_data.append(anim_item)
                if not filtered_anim_data:
                    cmds.warning('None of the selected objects have matching animation data in the JSON file.')
                    return False
                anim_data['animData'] = filtered_anim_data
                objects = selected_objects
            else:
                objects = []
                for item in anim_data['animData']:
                    if item['objAttr']:
                        obj_name = item['objAttr'].split('.')[0]
                        if cmds.objExists(obj_name) and obj_name not in objects:
                            objects.append(obj_name)
            if not objects:
                cmds.warning('No valid objects to apply animation to.')
                return False
            if 'rotationOrders' in anim_data and anim_data['rotationOrders']:
                rot_differences = self.check_rotation_order_differences(anim_data['rotationOrders'], objects)
                if rot_differences:
                    cmds.waitCursor(state=False)
                    rot_choice = self.show_rotation_order_warning(rot_differences)
                    if rot_choice == 'cancel':
                        cmds.waitCursor(state=True)
                        return False
                    if rot_choice == 'change':
                        self.apply_rotation_order_changes(rot_differences)
                    cmds.waitCursor(state=True)
            if objects:
                cmds.select(objects)
            if not skip_undo_chunk:
                cmds.undoInfo(openChunk=True, chunkName='Paste Animation')
            try:
                cmds.refresh(suspend=True)
                cmds.evaluationManager(mode='off')
                first_key = None
                all_keys = []
                if paste_in_place:
                    curr_key = cmds.currentTime(query=True)
                    for anim_item in anim_data['animData']:
                        for keyframe in anim_item['keyframeData']:
                            all_keys.append(keyframe[0])
                    if all_keys:
                        first_key = _min(all_keys)
                        time_offset = curr_key - first_key
                    else:
                        time_offset = 0
                else:
                    for anim_item in anim_data['animData']:
                        for keyframe in anim_item['keyframeData']:
                            all_keys.append(keyframe[0])
                    time_offset = 0
                existing_obj_attrs = []
                for anim_item in anim_data['animData']:
                    obj_attr = anim_item['objAttr']
                    if obj_attr and cmds.objExists(obj_attr.split('.')[0]):
                        existing_obj_attrs.append(obj_attr)
                if not existing_obj_attrs:
                    cmds.warning('No matching object attributes found in scene.')
                    return False
                keyed_obj_attrs = self.create_dummy_key(existing_obj_attrs)
                if paste_in_place and first_key is not None:
                    cut_in = curr_key
                    cut_out = curr_key + (_max(all_keys) - first_key)
                    for obj_attr in existing_obj_attrs:
                        try:
                            cmds.cutKey(obj_attr, time=(cut_in, cut_out), clear=True)
                        except Exception:
                            pass
                else:
                    for obj_attr in existing_obj_attrs:
                        try:
                            cmds.cutKey(obj_attr, time=(-49999, 50000), clear=True)
                        except Exception:
                            pass
                for anim_item in anim_data['animData']:
                    obj_attr = anim_item['objAttr']
                    if not obj_attr:
                        continue
                    curve_data = anim_item['curveData']
                    weighted = curve_data[0] if len(curve_data) > 0 else False
                    infinity = curve_data[1] if len(curve_data) > 1 else ['constant', 'constant']
                    keyframe_data = anim_item['keyframeData']
                    tangent_data = anim_item['tangentData']
                    self.om_helper.apply_curve_batch_api2(obj_attr, keyframe_data, tangent_data, weighted, infinity, time_offset)
                self.delete_dummy_key(keyed_obj_attrs)
            finally:
                try:
                    cmds.refresh(suspend=False)
                except:
                    pass
                try:
                    cmds.evaluationManager(mode='parallel')
                except:
                    pass
                if not skip_undo_chunk:
                    cmds.undoInfo(closeChunk=True)
            return True
        except Exception:
            try:
                cmds.refresh(suspend=False)
            except:
                pass
            try:
                cmds.evaluationManager(mode='parallel')
            except:
                pass
            return False

    def attribute_exists_and_keyable(self, obj_attr):
        if not obj_attr or '.' not in obj_attr:
            return False
        if cmds.objExists(obj_attr):
            try:
                return cmds.getAttr(obj_attr, keyable=True) is not False
            except Exception:
                try:
                    cmds.getAttr(obj_attr)
                    return True
                except Exception:
                    return False
        obj_name = obj_attr.split('.')[0]
        attr_name = obj_attr.split('.')[-1]
        if not cmds.objExists(obj_name):
            return False
        try:
            cmds.getAttr(obj_attr)
            return True
        except Exception:
            pass
        try:
            all_attrs = cmds.listAttr(obj_name) or []
            if attr_name in all_attrs:
                return True
        except Exception:
            pass
        return False

    def is_object_or_transform_selected(self, obj_name, selected_objects):
        if obj_name in selected_objects:
            return True
        try:
            if cmds.objectType(obj_name) != 'transform':
                parents = cmds.listRelatives(obj_name, parent=True, type='transform')
                if parents and parents[0] in selected_objects:
                    return True
        except Exception:
            pass
        for sel_obj in selected_objects:
            try:
                if cmds.objExists(sel_obj):
                    relatives = cmds.listRelatives(sel_obj, allDescendents=True) or []
                    if obj_name in relatives:
                        return True
                    relatives = cmds.listRelatives(obj_name, allDescendents=True) or []
                    if sel_obj in relatives:
                        return True
            except Exception:
                continue
        return False

    def copy_pose_to_json(self):
        cmds.waitCursor(state=True)
        try:
            selected_objects = self.om_helper.get_selected_objects()
            if not selected_objects:
                selected_objects = cmds.ls(selection=True, long=True)
            if not selected_objects:
                cmds.warning('No objects selected. Please select controls to copy their pose.')
                return None
            current_time = cmds.currentTime(query=True)
            pose_data = {}
            anim_data_list = []
            for obj in selected_objects:
                short_name = obj.split('|')[-1]
                pose_data[short_name] = {}
                attrs = cmds.listAttr(obj, keyable=True, unlocked=True)
                if not attrs:
                    continue
                for attr in attrs:
                    full_attr = obj + '.' + attr
                    try:
                        value = self.om_helper.get_attribute_value_fast(full_attr)
                        if value is None:
                            value = cmds.getAttr(full_attr)
                            if isinstance(value, float):
                                value = value * _ui_to_internal_factor(full_attr)
                        pose_data[short_name][attr] = value
                        anim_data_list.append({'objAttr': short_name + '.' + attr, 'curveData': [False, ['constant', 'constant']], 'keyframeData': [[current_time, value, False]], 'tangentData': [['auto', 'auto', 0, 0, 0, 0, False, False]]})
                    except Exception:
                        continue
            if not pose_data or all((not attrs for attrs in pose_data.values())):
                cmds.warning('No keyable attributes found on selected objects.')
                return None
            scene_name = cmds.file(query=True, sceneName=True, shortName=True)
            if scene_name:
                base_name = '.'.join(scene_name.split('.')[:-1])
            else:
                base_name = 'untitled_scene'
            timestamp = time_module.strftime('%Y%m%d_%H%M%S')
            filename = '{}_animation_{}.json'.format(base_name, timestamp)
            filepath = os.path.join(self.documents_path, filename)
            save_data = {'pose': pose_data, 'animData': anim_data_list, 'rotationOrders': self.get_rotation_orders_from_objects(selected_objects)}
            with open(filepath, 'w') as f:
                json.dump(save_data, f, indent=4)
            return filepath
        finally:
            cmds.waitCursor(state=False)

    def paste_pose_from_json(self, filepath=None, target_namespace=None, selected_objects=None, skip_undo_chunk=False):
        cmds.waitCursor(state=True)
        try:
            if not filepath:
                json_files = glob.glob(os.path.join(self.documents_path, '*_animation_*.json'))
                pose_files = glob.glob(os.path.join(self.documents_path, 'pose_*.json'))
                all_files = json_files + pose_files
                if not all_files:
                    cmds.warning('No pose or animation files found.')
                    return
                all_files.sort(key=os.path.getmtime, reverse=True)
                filepath = all_files[0]
            if not os.path.exists(filepath):
                cmds.warning('File not found: {}'.format(filepath))
                return
            with open(filepath, 'r') as f:
                data = json.load(f)
            if not data:
                cmds.warning('No pose data found in file.')
                return
            if 'pose' in data:
                pose_data = data['pose']
            else:
                pose_data = data
            if 'rotationOrders' in data and data['rotationOrders']:
                target_objs = []
                if selected_objects:
                    target_objs = selected_objects
                else:
                    for obj_name in pose_data.keys():
                        if cmds.objExists(obj_name):
                            target_objs.append(obj_name)
                        else:
                            matching = cmds.ls('*:' + obj_name.split(':')[-1], long=True) or cmds.ls(obj_name.split(':')[-1], long=True)
                            if matching:
                                target_objs.append(matching[0])
                if target_objs:
                    rot_differences = self.check_rotation_order_differences(data['rotationOrders'], target_objs)
                    if rot_differences:
                        cmds.waitCursor(state=False)
                        rot_choice = self.show_rotation_order_warning(rot_differences)
                        if rot_choice == 'cancel':
                            cmds.waitCursor(state=True)
                            return
                        if rot_choice == 'change':
                            self.apply_rotation_order_changes(rot_differences)
                        cmds.waitCursor(state=True)
            try:
                obj_name_map = {}
                if selected_objects:
                    for sel_obj in selected_objects:
                        short = sel_obj.split('|')[-1]
                        short_no_ns = short.split(':')[-1]
                        obj_name_map[short] = sel_obj
                        obj_name_map[short_no_ns] = sel_obj
                        if ':' in short:
                            obj_name_map[short.split(':')[-1]] = sel_obj
                else:
                    for obj_name in pose_data.keys():
                        if cmds.objExists(obj_name):
                            obj_name_map[obj_name] = obj_name
                            obj_name_map[obj_name.split(':')[-1]] = obj_name
                        else:
                            short = obj_name.split(':')[-1]
                            matching = cmds.ls('*:' + short, long=True) or cmds.ls(short, long=True)
                            if matching:
                                obj_name_map[obj_name] = matching[0]
                                obj_name_map[short] = matching[0]
                if target_namespace is not None:
                    remapped = {}
                    for obj_name in pose_data.keys():
                        if target_namespace:
                            target_obj = target_namespace + ':' + obj_name.split(':')[-1]
                        else:
                            target_obj = obj_name.split(':')[-1]
                        if cmds.objExists(target_obj):
                            remapped[obj_name] = target_obj
                        elif target_obj in obj_name_map:
                            remapped[obj_name] = obj_name_map[target_obj]
                    for (src, dst) in remapped.items():
                        obj_name_map[src] = dst
                if not skip_undo_chunk:
                    cmds.undoInfo(openChunk=True, chunkName='Paste Pose')
                try:
                    for (obj_name, attrs) in pose_data.items():
                        target_obj_full = None
                        if obj_name in obj_name_map:
                            target_obj_full = obj_name_map[obj_name]
                        else:
                            short = obj_name.split(':')[-1]
                            if short in obj_name_map:
                                target_obj_full = obj_name_map[short]
                        if not target_obj_full:
                            continue
                        for (attr_name, value) in attrs.items():
                            full_attr = target_obj_full + '.' + attr_name
                            try:
                                if not self.om_helper.set_attribute_value_api2(full_attr, value):
                                    if not self.om_helper.set_attribute_value_fast(full_attr, value):
                                        if cmds.objExists(full_attr) and (not cmds.getAttr(full_attr, lock=True)):
                                            cmds.setAttr(full_attr, value * _internal_to_ui_factor(full_attr))
                            except:
                                pass
                finally:
                    if not skip_undo_chunk:
                        cmds.undoInfo(closeChunk=True)
            except Exception:
                pass
        finally:
            cmds.waitCursor(state=False)
