"""Connect AnimKey's existing menus and clipboard to the bundled transfer kernels."""
import copy
import json
import math
import os
from contextlib import contextmanager
import maya.cmds as cmds
from AnimKey.core import animation_curve_transfer as curves
from AnimKey.core.transfer_reference import animation as animation_core, mirror as mirror_core
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


_TABLES={}


def _table_path(rig):
    from AnimKey.buttons import mirror
    folder=os.path.join(mirror.get_user_data_folder(),'tools','mirror','channel_tables')
    os.makedirs(folder,exist_ok=True)
    return os.path.join(folder,str(rig)+'.json')


def _profile_controls(selected):
    # Use the reference's namespace/curve discovery, including its geometry
    # fallback pairing. Preserve selection while discovering the rig.
    original=cmds.ls(selection=True,long=True) or []
    try:return mirror_core.autoSelectAllCtrls(selected) or list(selected)
    finally:
        if original:cmds.select(original,replace=True)


@contextmanager
def _storage(path):
    previous=(mirror_core.MIRROR_TABLE_PATH,mirror_core.MIRROR_SETTINGS_DIR)
    mirror_core.MIRROR_TABLE_PATH=path
    mirror_core.MIRROR_SETTINGS_DIR=os.path.dirname(path)
    try:yield
    finally:mirror_core.MIRROR_TABLE_PATH,mirror_core.MIRROR_SETTINGS_DIR=previous


def table_for(selected, force=False):
    from AnimKey.buttons import mirror
    selected=[(cmds.ls(node) or [node])[0] for node in selected]
    rig=mirror.get_rig_identifier(selected)
    path=_table_path(rig)
    key=(path,cmds.file(query=True,sceneName=True))
    table=None if force else _TABLES.get(key)
    if table is None and not force and os.path.exists(path):
        try:
            with open(path,'r',encoding='utf-8') as handle:table=json.load(handle)
        except (OSError,ValueError):table=None
    if force or not mirror_core.tableCoversSelection(table,selected):
        with _storage(path):
            table=mirror_core.resetPoseAndSaveMirrorTable(list(selected),existingTable=None if force else table)
        if table:
            # Store a scene-independent table, using the same control identities
            # as the original reader; no dependency on an Animo installation.
            atomic_write_json(path,table,indent=2)
    if table:_TABLES[key]=table
    return table or {'controls':{}}


def snapshot(selected):
    from AnimKey.buttons import mirror
    for _,info in mirror._group_controls_by_rig(selected).items():table_for(info['controls'],force=True)
    cmds.inViewMessage(amg='AnimKey: Mirror snapshot saved',pos='topCenter',fade=True)


def remove_snapshot(selected):
    from AnimKey.buttons import mirror
    for _,info in mirror._group_controls_by_rig(selected).items():
        path=_table_path(mirror.get_rig_identifier(info['controls']))
        if os.path.exists(path):os.remove(path)
        for key in list(_TABLES):
            if key[0]==path:_TABLES.pop(key,None)


def mirror_action(selected, time_range=None, all_keys=False, target_side=None, pose_only=False):
    """Run one native transaction for the complete original selection."""
    from AnimKey.buttons import mirror
    controls = [(cmds.ls(node) or [node])[0] for node in selected]
    if not controls:
        cmds.warning('AnimKey: Select controls to mirror.')
        return 0
    graph = mirror_core.getSelectedGraphEditorKeys() if not pose_only else []
    attrs = mirror._get_selected_channels()
    table = {}
    for _, info in mirror._group_controls_by_rig(controls).items():
        table.update(table_for(info['controls'])['controls'])
    if target_side:
        controls = [node for node in controls if table.get(node, {}).get('partner') and
                    mirror._control_side(table[node]['partner']) == target_side]
    if not controls:
        return 0
    if all_keys:
        controls = list(dict.fromkeys(controls + [table.get(node, {}).get('partner')
            for node in controls if table.get(node, {}).get('partner')]))
        frames = cmds.keyframe(controls, query=True, timeChange=True) or []
        scope = time_range or ((min(frames), max(frames)) if frames else None)
        partner = {node: table.get(node, {}).get('partner') or node for node in controls}
        cmds.undoInfo(openChunk=True, chunkName='AnimKey All Mirror')
        try:
            if scope:
                count, _, _ = mirror_core.mirrorTimeRange(controls, partner, table, *scope, attrFilter=attrs)
            else:
                count, _ = mirror_core.mirrorCurrentPose(controls, partner, table, attrFilter=attrs)
        finally:
            cmds.undoInfo(closeChunk=True)
    elif pose_only:
        partner = {node: table.get(node, {}).get('partner') or node for node in controls}
        count, _ = mirror_core.mirrorCurrentPose(controls, partner, table, attrFilter=attrs)
    else:
        mirror_core.applyMirror(controls, table, timeRange=time_range,
            preCapturedGraphKeys=graph, preCapturedChannelBoxAttrs=attrs)
        count = len(controls)
    # Match the reference launcher's refresh: evaluation must not insert a
    # second undo step after the native mirror transaction.
    undo_state = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        cmds.currentTime(cmds.currentTime(query=True), edit=True, update=True)
    finally:
        cmds.undoInfo(stateWithoutFlush=undo_state)
    return count


def prepare_opposites(assignments):
    """Build missing tables before the caller opens its paste undo chunk."""
    for _, source in assignments:
        table_for([(cmds.ls(source) or [source])[0]])


def prepare_profiles(controls):
    from AnimKey.buttons import mirror
    for _, info in mirror._group_controls_by_rig(controls).items():
        table_for(info['controls'])


def paste_opposite(assignments, buffer):
    from AnimKey.buttons import copyAnimation as clip
    items=[];inversions=[];skipped=0
    for key,source in assignments:
        source=(cmds.ls(source) or [source])[0]
        table=table_for([source])['controls']
        target=table.get(source,{}).get('partner') or source
        axis=mirror_core.mirrorAxisFor(source,target,table)
        for attr,stored in buffer.get(key,{}).items():
            plug=target+'.'+attr
            if not cmds.objExists(plug) or cmds.getAttr(plug,lock=True):
                skipped+=1;continue
            for _,payload in clip._iter_curve_payloads(stored):
                item=snapshot_to_reference(plug,payload)
                if item:
                    items.append(item)
                    if mirror_core.isAttrMirrored(attr,axis):
                        frames=[key[0] for key in item['keyframeData']]
                        inversions.append((plug,(min(frames),max(frames))))
                else:skipped+=1
    if not items:return 0,skipped
    success=AnimationTool().apply_animation_data({'animData':items},paste_in_place=False,skip_undo_chunk=True)
    if success:
        for plug,scope in inversions:
            cmds.scaleKey(plug,time=scope,valueScale=-1,valuePivot=0)
        cmds.currentTime(cmds.currentTime(query=True),edit=True,update=True)
    return (len(items) if success else 0),skipped
