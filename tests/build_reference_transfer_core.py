"""Bundle the user-provided Animo transfer kernels without launcher/UI code.
Run with ordinary Python; the resulting product modules have no Desktop dependency.
"""
import ast,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REFERENCE=Path('C:/Users/ANIM-gonzalo/Desktop/Animo_v10.7.5/Animo_v10.7.5/Animo_Data')
DEST=ROOT/'AnimKey/core/transfer_reference'
DEST.mkdir(parents=True,exist_ok=True)
NOTICE='"""Animation transfer kernel adapted from the user-provided Animo v10.7.5 source.\nOnly Maya data operations are bundled; AnimKey owns UI, storage and dispatch.\nSee provenance.json for original source hashes and deliberate interface changes.\n"""\n'
manifest={'reference_version':'Animo v10.7.5','runtime_reference_dependency':False,'modules':{}}

path=REFERENCE/'Animo_Transify/transify_engine.py';source=path.read_text(encoding='utf-8-sig');tree=ast.parse(source)
body=[]
for node in tree.body:
    if isinstance(node,ast.ImportFrom) and node.module=='transify_common':continue
    if isinstance(node,ast.ClassDef) and node.name=='AnimationCopyPasteJson':break
    body.append(node)
original=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='AnimationCopyPasteJson')
keep={'get_anim_curves','get_timeline_range','get_objects_and_attributes','get_keys_in_range','get_anim_data',
      'get_all_animatable_attributes','copy_animation','get_anim_data_from_attributes','copy_all_animation_to_json',
      'get_rotation_order_name','get_rotation_orders_from_objects','check_rotation_order_differences',
      'change_rotation_order','apply_rotation_order_changes','create_dummy_key','delete_dummy_key',
      'detect_source_namespace','remap_namespace_in_data','apply_animation_data','attribute_exists_and_keyable',
      'is_object_or_transform_selected','copy_pose_to_json','paste_pose_from_json'}
methods=[node for node in original.body if isinstance(node,ast.FunctionDef) and node.name in keep]
# This final block offered an Animo-specific playback-range dialog. AnimKey
# keeps its existing timeline range and existing UI, so omit only that dialog.
class RemoveRangeDialog(ast.NodeTransformer):
    def visit_If(self,node):
        if 'timelineRange' in ast.unparse(node.test) and 'not paste_in_place' in ast.unparse(node.test):return None
        return self.generic_visit(node)
methods=[RemoveRangeDialog().visit(node) if node.name=='apply_animation_data' else node for node in methods]
body.append(ast.ClassDef(name='AnimationTransfer',bases=[],keywords=[],body=methods,decorator_list=[]))
module=ast.fix_missing_locations(ast.Module(body=body,type_ignores=[]))
(DEST/'animation.py').write_text(NOTICE+ast.unparse(module)+'\n',encoding='utf-8')
manifest['modules']['animation']={'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'methods':sorted(keep),'interface_changes':['Omitted Animo playback-range dialog; target dispatch, layer handling, clipboard files and rotation-order UI belong to AnimKey.']}

path=REFERENCE/'Animo_Launcher/mirror_launcher.py';tree=ast.parse(path.read_text(encoding='utf-8-sig'))
defs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
roots={'tableCoversSelection','resetPoseAndSaveMirrorTable','mirrorTimeRange','mirrorGraphEditorKeys','mirrorCurrentPose',
       'applyMirror','getSelectedGraphEditorKeys','getSelectedTimeRange','_getSelectedChannelBoxAttrs','_findRotateOrderMismatches',
       '_ownerControlMap','_restoreGraphEditorSelection','isAttrMirrored','formatValue','mirrorAxisFor','buildMirrorTableData'}
ui={'showSnapshotNotice','hideSnapshotNotice','beginProgress','updateProgress','endProgress','_smoothPause','progressCancelled'}
selected=set(roots)
while True:
    refs={n.id for name in selected for n in ast.walk(defs[name]) if isinstance(n,ast.Name)}
    new=(refs & defs.keys())-ui
    if new<=selected:break
    selected.update(new)
prefix=[]
for node in tree.body:
    if isinstance(node,ast.FunctionDef) and node.name=='_warn':break
    if isinstance(node,ast.FunctionDef):continue
    text=ast.unparse(node)
    if 'MIRROR_SETTINGS_DIR' in text or 'MIRROR_TABLE_PATH' in text:continue
    prefix.append(node)
# Read the same optional custom reset values as Animo. This block computes
# a path only and performs no file writes or launcher actions.
for node in tree.body:
    if isinstance(node,ast.Try) and '_CUSTOM_RESET_JSON_PATH' in ast.unparse(node):prefix.append(node)
body=prefix+[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in selected]
class RemoveDesktopFocus(ast.NodeTransformer):
    def visit_Expr(self,node):
        if isinstance(node.value,ast.Call) and ast.unparse(node.value.func)=='cmds.setFocus':return ast.Pass()
        return self.generic_visit(node)
body=[RemoveDesktopFocus().visit(node) for node in body]
module=ast.fix_missing_locations(ast.Module(body=body,type_ignores=[]))
hooks="\nMIRROR_TABLE_PATH = None\nMIRROR_SETTINGS_DIR = None\n\ndef _quiet_interface(*args, **kwargs):\n    return None\n\nshowSnapshotNotice = hideSnapshotNotice = beginProgress = updateProgress = endProgress = _smoothPause = _quiet_interface\ndef progressCancelled():\n    return False\n"
(DEST/'mirror.py').write_text(NOTICE+ast.unparse(module)+hooks,encoding='utf-8')
manifest['modules']['mirror']={'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'functions':sorted(selected),'interface_changes':['No launcher autorun, desktop focus, progress dialogs, sleeps or notice windows. AnimKey supplies the rig-specific storage path and context.']}
(DEST/'__init__.py').write_text('"""Private transfer kernels; all user entry points remain in AnimKey.buttons."""\n',encoding='utf-8')
(DEST/'provenance.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print({name:len((DEST/(name+'.py')).read_text().splitlines()) for name in ('animation','mirror')})
