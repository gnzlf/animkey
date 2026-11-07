"""Connect AnimKey's existing menus and clipboard to the bundled transfer kernels."""
import copy
import json
import math
import os
from contextlib import contextmanager
import maya.cmds as cmds
from AnimKey.core import animation_curve_transfer as curves
from AnimKey.core.transfer_reference import animation as animation_core
from AnimKey.mods.storage import atomic_write_json


class AnimationTool(animation_core.AnimationTransfer):
    def __init__(self):
        self.om_helper=animation_core.OpenMayaAnimHelper()

    def check_objects_in_animation_layers(self, objects):
        # The dispatcher routes layer stacks through AnimKey's layer adapter.
        return False

    def save_to_json(self, data, filename=None, selected_objects=None):
        if data and selected_objects:
            data['rotationOrders']=self.get_rotation_orders_from_objects(selected_objects)
        return data

    def show_rotation_order_warning(self, differences):
        # Keep the existing destination order. Rig/namespace matching remains
        # AnimKey's responsibility; do not add Animo's independent UI.
        cmds.warning('AnimKey: Source and target rotation orders differ; retaining target orders.')
        return 'continue'


def supports_direct_transfer():
    return not any(not curves.is_base_layer(layer) for layer in cmds.ls(type='animLayer') or [])


def supports_clip(buffer):
    from AnimKey.buttons import copyAnimation as clip
    return supports_direct_transfer() and all(
        curves.is_base_layer(layer)
        for attrs in buffer.values() for stored in attrs.values()
        for layer, _ in clip._iter_curve_payloads(stored))


def capture_animation(selected, time_range=None, attrs=None):
    """Use the reference reader, including its exact key/curve ownership rules."""
    tool=AnimationTool()
    selected=list(selected)
    # Read the graph key set before any native command can replace it.
    graph=cmds.keyframe(query=True,selected=True,name=True) or []
    if not graph and time_range is None and not attrs:
        return tool.copy_all_animation_to_json()
    candidate=graph or (cmds.keyframe(selected,query=True,name=True) or [])
    if attrs and not graph:
        candidate=cmds.keyframe(selected,attribute=attrs,query=True,name=True) or []
    tool.get_anim_curves=lambda force_graph_editor=False:[candidate,'graphEditor' if graph else 'timeline']
    if time_range is not None:
        tool.get_timeline_range=lambda float_val=True:list(time_range)
    elif not graph:
        return tool.get_anim_data(anim_curves=candidate,range_all=True)
    return tool.copy_animation(range_mode='selected')


def snapshot_to_reference(plug, payload):
    """Bridge old AnimKey clips to the same reader, without a live source node."""
    if payload.get('reference_curve'):
        result=copy.deepcopy(payload['reference_curve']);result['objAttr']=plug
        return result
    temp=curves.create_temp_curve(payload)
    if not temp:return None
    try:
        captured=animation_core.OpenMayaAnimHelper.get_curve_data_api2(temp)
        if not captured:return None
        return {'objAttr':plug,'curveData':[captured[2],[payload.get('pre_infinity','constant'),payload.get('post_infinity','constant')]],
                'keyframeData':captured[0],'tangentData':captured[1]}
    finally:
        if cmds.objExists(temp):cmds.delete(temp)


def paste_animation(assignments, buffer, insert=False):
    """Keep original keys/tangents and use the reference replace/insert algorithm."""
    from AnimKey.buttons import copyAnimation as clip
    items=[];skipped=0
    for key,target in assignments:
        for attr,stored in buffer.get(key,{}).items():
            plug=target+'.'+attr
            if not cmds.objExists(plug) or cmds.getAttr(plug,lock=True):
                skipped+=1;continue
            for layer,payload in clip._iter_curve_payloads(stored):
                item=snapshot_to_reference(plug,payload)
                if item:items.append(item)
                else:skipped+=1
    if not items:return 0,skipped
    selected=cmds.ls(selection=True,long=True) or []
    mode=cmds.evaluationManager(query=True,mode=True)
    try:
        success=AnimationTool().apply_animation_data({'animData':items},paste_in_place=insert,skip_undo_chunk=True)
        cmds.currentTime(cmds.currentTime(query=True),edit=True,update=True)
        return (len(items) if success else 0),skipped
    finally:
        if mode:cmds.evaluationManager(mode=mode[0])
        if selected:cmds.select(selected,replace=True)


# Mirror tables live in AnimKey's own engine (AnimKey/core/mirror_engine.py).

def table_for(selected, force=False):
    from AnimKey.core import mirror_engine as engine
    selected = [(cmds.ls(node, long=True) or [node])[0] for node in selected]
    if force and selected:
        engine.build_table(selected[0], force=True)
    tables = engine.table_for(selected)
    merged = {'controls': {}}
    for table in tables.values():
        merged['controls'].update(table.get('controls', {}))
    return merged


def snapshot(selected):
    from AnimKey.core import mirror_engine as engine
    for node in selected:
        engine.build_table((cmds.ls(node, long=True) or [node])[0], force=True)
    cmds.inViewMessage(amg='AnimKey: Mirror snapshot saved', pos='topCenter', fade=True)


def remove_snapshot(selected):
    from AnimKey.core import mirror_engine as engine
    from AnimKey.buttons import mirror
    for rig_id in mirror._group_controls_by_rig(selected):
        engine.delete_table(rig_id)


def mirror_action(selected, time_range=None, all_keys=False, target_side=None, pose_only=False):
    """Compatibility wrapper: every mirror runs through AnimKey's mirror engine."""
    from AnimKey.core import mirror_engine as engine
    return engine.run(selected, mode='swap' if all_keys else 'to_opposite',
                      target_side=target_side, time_range=time_range,
                      whole_animation=all_keys, pose_only=pose_only)


def prepare_opposites(assignments):
    """Build missing tables before the caller opens its paste undo chunk."""
    from AnimKey.core import mirror_engine as engine
    engine.table_for([(cmds.ls(source, long=True) or [source])[0] for _, source in assignments
                      if cmds.objExists(source)])


def prepare_profiles(controls):
    from AnimKey.core import mirror_engine as engine
    engine.table_for([node for node in controls if cmds.objExists(node)])


def paste_opposite(assignments, buffer):
    from AnimKey.buttons import copyAnimation as clip
    from AnimKey.core import mirror_engine as engine
    items=[];adjust=[];skipped=0
    for key,source in assignments:
        source=(cmds.ls(source,long=True) or [source])[0]
        tables=engine.table_for([source])
        table=tables[engine._rig_id(source)]
        target=engine.partner_of(source,table) or source
        for attr,stored in buffer.get(key,{}).items():
            plug=target+'.'+attr
            if not cmds.objExists(plug) or cmds.getAttr(plug,lock=True):
                skipped+=1;continue
            rule=engine.channel_rule(source,target,attr,table)
            if rule is None:
                skipped+=1;continue
            for _,payload in clip._iter_curve_payloads(stored):
                item=snapshot_to_reference(plug,payload)
                if item:
                    items.append(item)
                    frames=[k[0] for k in item['keyframeData']]
                    if frames:adjust.append((plug,(min(frames),max(frames)),rule))
                else:skipped+=1
    if not items:return 0,skipped
    success=AnimationTool().apply_animation_data({'animData':items},paste_in_place=False,skip_undo_chunk=True)
    if success:
        for plug,scope,(sign,source_rest,target_rest) in adjust:
            if sign<0:
                cmds.scaleKey(plug,time=scope,valueScale=-1,valuePivot=source_rest)
            if abs(target_rest-source_rest)>1e-9:
                cmds.keyframe(plug,edit=True,relative=True,time=scope,valueChange=target_rest-source_rest)
        cmds.currentTime(cmds.currentTime(query=True),edit=True,update=True)
    return (len(items) if success else 0),skipped
