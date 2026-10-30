"""Compare native Animo/AnimKey transfers on a disposable copy of a production shot.
No desktop input, no original-file writes, no user Maya session interaction.
"""
import argparse,ast,copy,hashlib,importlib.util,json,math,os,sys,time,traceback
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'dist/scene-parity'
sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide2 import QtWidgets
APP=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

def write(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2),encoding='utf-8')

def log(message):
    print(message,flush=True)
    (OUT/'progress.txt').write_text(message,encoding='utf-8')

def load_shot():
    manifest=json.loads((OUT/'source_manifest.json').read_text(encoding='utf-8'))
    cmds.file(prompt=False)
    cmds.scriptEditorInfo(suppressWarnings=True,suppressInfo=True)
    log('Loading the local production scene snapshot in standalone Maya')
    started=time.perf_counter()
    try:
        cmds.file(str(OUT/'prepared_snapshot.mb') if (OUT/'prepared_snapshot.mb').exists() else manifest['snapshot'],open=True,force=True,prompt=False,
                  ignoreVersion=True,executeScriptNodes=False,loadReferenceDepth='all')
    except RuntimeError as exc:
        log('File loader reported: '+str(exc))
    cmds.evaluationManager(mode='off')
    cmds.autoKeyframe(state=False)
    log('Scene loaded in %.1f seconds'%(time.perf_counter()-started))
    return manifest

def inventory():
    load_shot()
    curves=cmds.ls(type='animCurve') or []
    controls={}
    for shape in cmds.ls(type='nurbsCurve',long=True) or []:
        parent=(cmds.listRelatives(shape,parent=True,fullPath=True) or [None])[0]
        if not parent or parent in controls:continue
        if not parent.rsplit('|',1)[-1].startswith(('King_Rigging:','Goblin_Rigging:','Cup_King_Rigging_master:','Kings_sword_Rigging_master:')):continue
        times=cmds.keyframe(parent,query=True,timeChange=True) or []
        channels=cmds.listAttr(parent,keyable=True,unlocked=True) or []
        controls[parent]={'key_count':len(times),'frames':sorted(set(times)),
                          'channels':channels,'rotate_order':cmds.getAttr(parent+'.rotateOrder')}
    refs=[]
    for ref in cmds.ls(type='reference') or []:
        try:refs.append({'node':ref,'file':cmds.referenceQuery(ref,filename=True),'loaded':cmds.referenceQuery(ref,isLoaded=True)})
        except RuntimeError:pass
    unknown=cmds.ls(type='unknown') or []
    info={'maya':cmds.about(version=True),'range':[cmds.playbackOptions(query=True,min=True),cmds.playbackOptions(query=True,max=True)],
          'current_time':cmds.currentTime(query=True),'animation_curves':len(curves),'controls':controls,
          'layers':cmds.ls(type='animLayer') or [],'references':refs,'unknown_nodes':unknown[:50],'unknown_count':len(unknown)}
    write('inventory.json',info)
    log('Inventory saved: %d controls, %d animated, %d curves'%(len(controls),sum(bool(c['key_count']) for c in controls.values()),len(curves)))


REFERENCE=Path('C:/Users/ANIM-gonzalo/Desktop/Animo_v10.7.5/Animo_v10.7.5/Animo_Data')

def reference_tools():
    import types,builtins
    sys.path.insert(0,str(REFERENCE/'Animo_Transify'))
    spec=importlib.util.spec_from_file_location('parity_transify',REFERENCE/'Animo_Transify/transify_engine.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    tool=module.AnimationCopyPasteJson.__new__(module.AnimationCopyPasteJson)
    tool.documents_path=str(OUT/'animo-data');Path(tool.documents_path).mkdir(exist_ok=True)
    tool.om_helper=module.OpenMayaAnimHelper()
    # Redirect storage and suppress launcher UI only; transfer/snapshot math
    # executes the original module functions without changes.
    path=REFERENCE/'Animo_Launcher/mirror_launcher.py'
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name=='_resolveDocumentsDir':
            node.body=[ast.Return(value=ast.Constant(value=str(OUT/'animo-data')))]
    builtins._ANIMO_MIRROR_LAUNCHER_SUPPRESS_AUTORUN=True
    reference=types.ModuleType('parity_animo_mirror');reference.__file__=str(path)
    exec(compile(ast.fix_missing_locations(tree),str(path),'exec'),reference.__dict__)
    for name in ('showSnapshotNotice','hideSnapshotNotice','beginProgress','updateProgress','endProgress','_smoothPause'):
        setattr(reference,name,lambda *args,**kwargs:None)
    reference.progressCancelled=lambda:False
    return tool,reference


def control_nodes(namespace):
    inventory=json.loads((OUT/'inventory.json').read_text(encoding='utf-8'))['controls']
    return [name.rsplit('|',1)[-1] for name in inventory if name.rsplit('|',1)[-1].startswith(namespace+':')]


def actor_sources(namespace):
    names=['Main','RootX_M','FKRoot_M','FKChest_M','FKHead_M','FKShoulder_L',
           'FKWrist_L','FKIndexFinger1_L','IKArm_L','PoleArm_L','IKLeg_L','PoleLeg_L','EyeBrowInner_L']
    return [namespace+':'+name for name in names if cmds.objExists(namespace+':'+name)]


def context_graph(controls,span):
    entries=[]
    for node in controls:
        for attr in ('translateX','rotateY'):
            plug=node+'.'+attr
            if not cmds.objExists(plug):continue
            connected=cmds.keyframe(plug,query=True,name=True) or []
            if not connected:continue
            curve=connected[0]
            frames=cmds.keyframe(curve,query=True,time=span,timeChange=True) or []
            frames=frames[::max(1,len(frames)//3)][:3]
            if frames:entries.append({'node':node,'object':node,'attr':attr,'curve':curve,'frames':frames})
    return entries


def select_graph(entries):
    cmds.selectKey(cmds.ls(type='animCurve') or [],clear=True)
    for entry in entries:
        cmds.selectKey(entry['curve'],time=[(t,t) for t in entry['frames']],add=True)


def graph_reference(entries):
    return [{'object':e['node'],'attr':e['attr'],'curve':e['curve'],'time':t}
            for e in entries for t in e['frames']]


def state_for(nodes,sample_frames):
    from AnimKey.core import animation_curve_transfer as transfer
    result={};paths=[]
    for node in nodes:
        for attr in cmds.listAttr(node,keyable=True,unlocked=True) or []:
            plug=node+'.'+attr
            curve=transfer.resolve_anim_curve(plug,layer_name='BaseAnimation')
            if curve:
                payload=transfer.capture_curve(curve,layer_name='BaseAnimation')
                if payload:
                    frames=payload['keyframes'];samples=list(frames)
                    samples.extend((a+b)*0.5 for a,b in zip(frames,frames[1:]))
                    fn=transfer._anim_curve_fn(curve)
                    payload['evaluated_times']=sorted(set(samples))
                    payload['evaluated_values']=[float(fn.evaluate(om.MTime(t,om.MTime.uiUnit()))) for t in payload['evaluated_times']]
                    result[plug]=payload
    matrices={node:[] for node in nodes}
    for frame in sample_frames:
        cmds.currentTime(frame,edit=True,update=True)
        for node in nodes:
            matrices[node].append(list(cmds.xform(node,query=True,worldSpace=True,matrix=True)))
    return {'curves':result,'world_frames':sample_frames,'world_matrices':matrices}


def capture_baseline(nodes):
    from AnimKey.core import animation_curve_transfer as transfer
    values={};transfers=[]
    for node in nodes:
        for attr in cmds.listAttr(node,keyable=True,unlocked=True) or []:
            plug=node+'.'+attr
            try:value=cmds.getAttr(plug)
            except RuntimeError:continue
            if not isinstance(value,(int,float,bool)):continue
            payload=transfer.capture_curve(plug,layer_name='BaseAnimation')
            values[plug]=value
            if payload:transfers.append((plug,payload))
    return values,transfers


def restore_baseline(values,transfers):
    from AnimKey.core import animation_curve_transfer as transfer
    keyed={plug for plug,_ in transfers}
    for plug,value in values.items():
        if plug in keyed:continue
        curve=transfer.resolve_anim_curve(plug,layer_name='BaseAnimation')
        if curve:
            try:cmds.delete(curve)
            except RuntimeError:pass
        if cmds.getAttr(plug,settable=True):
            try:cmds.setAttr(plug,value)
            except RuntimeError:pass
    done=transfer.paste_curves_batch(transfers,layer_name='BaseAnimation',clear_all=True)
    assert all(done),'Failed to restore the disposable scene baseline'
    cmds.selectKey(cmds.ls(type='animCurve') or [],clear=True)
    cmds.currentTime(257,edit=True,update=True)
    cmds.flushUndo()


def run_engine(engine):
    manifest=load_shot()
    from AnimKey.buttons import copyAnimation as clip,mirror
    from AnimKey.core.executionGuard import animkey_execution
    from AnimKey.mods import configMod
    configMod.get_user_folder_path=lambda:str(OUT/(engine+'-data'))
    Path(configMod.get_user_folder_path()).mkdir(exist_ok=True)
    tool,reference=reference_tools()
    namespaces=['King_Rigging','Goblin_Rigging']
    actors={namespace:actor_sources(namespace) for namespace in namespaces}
    nodes=list(dict.fromkeys(node for sources in actors.values() for node in sources+
                            [n[:-2]+'_R' for n in sources if n.endswith('_L') and cmds.objExists(n[:-2]+'_R')]))
    baseline_values,baseline_curves=capture_baseline(nodes)
    write(engine+'-baseline.json',state_for(nodes,[1,60,257,500,777,1044]))
    tables={};profiles={}
    if engine=='animo':
        for namespace in namespaces:
            log('Building original Animo default-pose table for '+namespace)
            sources=actors[namespace]
            # Exact default-pose setup implementation. UI helpers are no-ops.
            cache_path=OUT/('animo-table-'+namespace+'.json')
            table=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else None
            if not table or table.get('source_scene_sha256')!=manifest['snapshot_sha256']:
                table=reference.resetPoseAndSaveMirrorTable(sources,existingTable=None)
                if table:table['source_scene_sha256']=manifest['snapshot_sha256']
            assert table and table.get('controls'),'Animo did not produce a table'
            tables[namespace]=table
            write('animo-table-'+namespace+'.json',table)
    results={'engine':engine,'source_sha256':manifest['snapshot_sha256'],'cases':{},'errors':[]}
    modes=['copy_all_replace','copy_range_replace','copy_range_insert','copy_graph_replace',
           'mirror_pose','mirror_range','mirror_graph','mirror_all_swap',
           'mirror_snapshot_pose','mirror_snapshot_range']
    for namespace in namespaces:
        sources=actors[namespace]
        observed=list(dict.fromkeys(sources+[n[:-2]+'_R' for n in sources if n.endswith('_L') and cmds.objExists(n[:-2]+'_R')]))
        span=(25,85) if namespace=='King_Rigging' else (200,300)
        samples=[float(span[0]),span[0]+0.5,(span[0]+span[1])/2.0,span[1]-0.5,float(span[1]),1100,1105.5,1160]
        for mode in modes:
            name=namespace+'__'+mode
            log(engine+': '+name)
            # Reopen the untouched baseline per operation. Rebuilding curves
            # cannot preserve shared-curve topology or every rig connection.
            load_shot()
            cmds.select(sources,replace=True)
            cmds.currentTime((span[0]+span[1])/2.0,edit=True,update=True)
            graph=context_graph(sources,span) if 'graph' in mode else []
            selected_span=span if 'range' in mode else None
            if mode.startswith('copy'):
                try:
                    if engine=='animo':
                        if mode=='copy_all_replace':
                            path=tool.copy_all_animation_to_json()
                            assert path,'Animo copy returned no file'
                            payload=json.loads(Path(path).read_text())
                        else:
                            select_graph(graph)
                            names=list(dict.fromkeys(e['curve'] for e in graph)) if graph else (cmds.keyframe(sources,query=True,name=True) or [])
                            with mock.patch.object(tool,'get_anim_curves',return_value=[names,'graphEditor' if graph else 'timeline']),mock.patch.object(tool,'get_timeline_range',return_value=span):
                                payload=tool.copy_animation(range_mode='selected')
                            assert payload and payload.get('animData'),'Animo selected copy returned no data'
                        copied=sum(len(item['keyframeData']) for item in payload['animData'])
                    else:
                        select_graph(graph)
                        with animkey_execution('Production scene comparison'),mock.patch.object(clip,'get_selected_time_range',return_value=selected_span),mock.patch.object(clip,'_selected_copy_channels',return_value=[]):
                            clip.copy_animation()
                        copied=sum(len(curve['keyframes']) for attrs in clip._anim_buffer.values() for stored in attrs.values() for _,curve in clip._iter_curve_payloads(stored))
                    if mode!='copy_range_insert':
                        # Delete only the copied channels; actual rigs remain
                        # connected and referenced throughout the comparison.
                        if engine=='animo':plugs=[item['objAttr'] for item in payload['animData']]
                        else:plugs=[clip._anim_sources[key]+'.'+attr for key,attrs in clip._anim_buffer.items() for attr in attrs]
                        cmds.cutKey(plugs,clear=True)
                    cmds.select(sources,replace=True);cmds.selectKey(cmds.ls(type='animCurve') or [],clear=True)
                    cmds.currentTime(1100,edit=True,update=True)
                    if engine=='animo':
                        ok=tool.apply_animation_data(copy.deepcopy(payload),paste_in_place=mode=='copy_range_insert')
                        assert ok,'Animo paste failed'
                    else:
                        with animkey_execution('Production scene comparison'):
                            (clip.paste_insert_animation if mode=='copy_range_insert' else clip.paste_animation)()
                    results['cases'][name]={'copied_keys':copied,'state':state_for(observed,samples)}
                except Exception:
                    results['errors'].append({'case':name,'error':traceback.format_exc()})
            else:
                try:
                    selected_span=span if 'range' in mode else None
                    if engine=='animo':
                        table=tables[namespace]['controls']
                        if mode=='mirror_all_swap':
                            expanded=list(dict.fromkeys(sources+[table.get(n,{}).get('partner') for n in sources if table.get(n,{}).get('partner')]))
                            frames=cmds.keyframe(expanded,query=True,timeChange=True) or []
                            partners={node:table.get(node,{}).get('partner') or node for node in expanded}
                            reference.mirrorTimeRange(expanded,partners,table,min(frames),max(frames))
                        else:
                            select_graph(graph)
                            actual_graph=reference.getSelectedGraphEditorKeys()
                            with mock.patch.object(reference,'getSelectedTimeRange',return_value=(None,None)),mock.patch.object(cmds,'setFocus',return_value=None):
                                reference.applyMirror(sources,table,timeRange=selected_span,
                                    preCapturedGraphKeys=actual_graph,preCapturedChannelBoxAttrs=[])
                    else:
                        if 'snapshot' in mode and namespace not in profiles:
                            with animkey_execution('Production scene comparison'):
                                mirror.snapshot_mirror_settings()
                            profiles[namespace]=True
                        select_graph(graph)
                        range_state=(span[0],span[1],True) if selected_span else (1,120,False)
                        with animkey_execution('Production scene comparison'),mock.patch.object(mirror,'_time_slider_or_playback_range',return_value=range_state),mock.patch.object(mirror,'_selected_graph_curve_entries',return_value=graph),mock.patch.object(mirror,'_get_selected_channels',return_value=[]):
                            (mirror.all_mirror if mode=='mirror_all_swap' else mirror.mirror)()
                    results['cases'][name]={'state':state_for(observed,samples)}
                except Exception:
                    results['errors'].append({'case':name,'error':traceback.format_exc()})
            write(engine+'-checkpoint.json',{'completed':list(results['cases']),'errors':results['errors']})
    write(engine+'-results.json',results)
    log(engine+': completed %d cases, %d errors'%(len(results['cases']),len(results['errors'])))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','inventory','animo','animkey'])
    args=parser.parse_args()
    try:
        if args.mode=='prepare':
            load_shot()
            target=OUT/'prepared_snapshot.mb'
            assert target.resolve().is_relative_to(OUT.resolve())
            def animation_signature():
                digest=hashlib.sha256()
                for curve in sorted(cmds.ls(type='animCurve') or []):
                    selection=om.MSelectionList();selection.add(curve);fn=oma.MFnAnimCurve(selection.getDependNode(0))
                    digest.update(curve.encode('utf-8'))
                    digest.update(str((fn.animCurveType,fn.numKeys,fn.isWeighted,fn.preInfinityType,fn.postInfinityType)).encode())
                    for index in range(fn.numKeys):
                        inp=fn.input(index)
                        inp=inp.value if hasattr(inp,'value') else inp
                        ia,iw=fn.getTangentAngleWeight(index,True);oa,ow=fn.getTangentAngleWeight(index,False)
                        digest.update(str((inp,fn.value(index),fn.inTangentType(index),fn.outTangentType(index),ia.asRadians(),iw,oa.asRadians(),ow)).encode())
                controls=list(json.loads((OUT/'inventory.json').read_text(encoding='utf-8'))['controls'])
                for frame in (1,257,777):
                    cmds.currentTime(frame,edit=True,update=True)
                    for node in controls:digest.update(str(cmds.xform(node,query=True,worldSpace=True,matrix=True)).encode())
                return digest.hexdigest()
            before=animation_signature()
            unknown=cmds.ls(type='unknown') or []
            allowed={'MayaNodeEditorSavedTabsInfo','defaultRedshiftPostEffects','hyperShadePrimaryNodeEditorSavedTabsInfo','redshiftOptions'}
            assert set(unknown)<=allowed,'Unexpected unsupported rig data; cache conversion stopped'
            for node in unknown:
                if cmds.objExists(node):cmds.lockNode(node,lock=False);cmds.delete(node)
            after=animation_signature()
            assert before==after,'Animation changed while preparing the disposable cache'
            cmds.file(rename=str(target));cmds.file(save=True,type='mayaBinary',force=True)
            write('prepared_manifest.json',{'source_sha256':json.loads((OUT/'source_manifest.json').read_text())['snapshot_sha256'],'path':str(target),
                  'cache_only_removed_unsupported_render_editor_nodes':unknown,'animation_before_sha256':before,'animation_after_sha256':after})
        elif args.mode=='inventory':inventory()
        else:run_engine(args.mode)
    except BaseException:
        write(args.mode+'-error.json',{'error':traceback.format_exc()});raise
