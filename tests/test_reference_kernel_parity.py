"""Independent-source parity and provenance checks for the integrated kernels."""
import ast,copy,hashlib,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
REFERENCE=Path('C:/Users/ANIM-gonzalo/Desktop/Animo_v10.7.5/Animo_v10.7.5/Animo_Data')


class KernelSourceTests(unittest.TestCase):
    def test_bundled_data_operations_match_original_ast(self):
        if not REFERENCE.exists():self.skipTest('Optional original reference unavailable')
        package=ROOT/'AnimKey/core/transfer_reference'
        metadata=json.loads((package/'provenance.json').read_text())
        for name,relative in [('animation','Animo_Transify/transify_engine.py'),('mirror','Animo_Launcher/mirror_launcher.py')]:
            source=REFERENCE/relative
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),metadata['modules'][name]['source_sha256'])
            original=ast.parse(source.read_text(encoding='utf-8-sig'))
            bundled=ast.parse((package/(name+'.py')).read_text(encoding='utf-8'))
            if name=='animation':
                oc=next(n for n in original.body if isinstance(n,ast.ClassDef) and n.name=='AnimationCopyPasteJson')
                bc=next(n for n in bundled.body if isinstance(n,ast.ClassDef) and n.name=='AnimationTransfer')
                originals={n.name:n for n in oc.body if isinstance(n,ast.FunctionDef)}
                candidates={n.name:n for n in bc.body if isinstance(n,ast.FunctionDef)}
                class InterfaceOnly(ast.NodeTransformer):
                    def visit_If(self,node):
                        if 'timelineRange' in ast.unparse(node.test) and 'not paste_in_place' in ast.unparse(node.test):return None
                        return self.generic_visit(node)
                for key,node in candidates.items():
                    expected=copy.deepcopy(originals[key])
                    if key=='apply_animation_data':expected=InterfaceOnly().visit(expected)
                    self.assertEqual(ast.dump(expected),ast.dump(node),key)
                oh=next(n for n in original.body if isinstance(n,ast.ClassDef) and n.name=='OpenMayaAnimHelper')
                bh=next(n for n in bundled.body if isinstance(n,ast.ClassDef) and n.name=='OpenMayaAnimHelper')
                self.assertEqual(ast.dump(oh),ast.dump(bh))
            else:
                originals={n.name:n for n in original.body if isinstance(n,ast.FunctionDef)}
                candidates={n.name:n for n in bundled.body if isinstance(n,ast.FunctionDef)}
                class NoFocus(ast.NodeTransformer):
                    def visit_Expr(self,node):
                        if isinstance(node.value,ast.Call) and ast.unparse(node.value.func)=='cmds.setFocus':return ast.Pass()
                        return self.generic_visit(node)
                for key in metadata['modules'][name]['functions']:
                    self.assertEqual(ast.dump(NoFocus().visit(copy.deepcopy(originals[key]))),ast.dump(candidates[key]),key)

    def test_runtime_does_not_import_desktop_animo(self):
        for path in (ROOT/'AnimKey/core/transfer_reference').glob('*.py'):
            text=path.read_text(encoding='utf-8')
            self.assertNotIn('transify_common',text)
            self.assertNotIn('Desktop',text)
            self.assertNotIn('sys.path.',text)


class OriginalEngineRuntimeTests(unittest.TestCase):
    def test_multi_rig_mirror_is_one_undo_with_original_selection_context(self):
        from AnimKey.mods.maya_compat import QtWidgets
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        import maya.standalone
        try:maya.standalone.initialize(name='python')
        except RuntimeError:pass
        import maya.cmds as cmds
        from AnimKey.buttons import mirror
        from AnimKey.core import reference_transfer as transfer
        from AnimKey.mods import configMod
        cmds.file(new=True,force=True)
        sources=[];targets=[]
        for namespace,angle in [('rigA',40),('rigB',60)]:
            cmds.namespace(add=namespace)
            left=cmds.circle(name=namespace+':arm_L_CTRL',constructionHistory=False)[0]
            right=cmds.circle(name=namespace+':arm_R_CTRL',constructionHistory=False)[0]
            cmds.setAttr(left+'.translateX',2);cmds.setAttr(right+'.translateX',-2)
            cmds.setAttr(left+'.rotateY',angle)
            sources.append(left);targets.append(right)
        with tempfile.TemporaryDirectory() as folder,mock.patch.object(configMod,'get_user_folder_path',return_value=folder):
            for source in sources:transfer.table_for([source])
            cmds.flushUndo();cmds.select(sources,replace=True)
            with mock.patch.object(mirror,'_time_slider_or_playback_range',return_value=(1,24,False)),mock.patch.object(mirror,'_get_selected_channels',return_value=[]):
                mirror.mirror()
            self.assertAlmostEqual(cmds.getAttr(targets[0]+'.rotateY'),-40)
            self.assertAlmostEqual(cmds.getAttr(targets[1]+'.rotateY'),-60)
            cmds.undo()
            for target in targets:self.assertAlmostEqual(cmds.getAttr(target+'.rotateY'),0)

    def test_insert_matches_original_engine_between_keys(self):
        path=REFERENCE/'Animo_Transify/transify_engine.py'
        if not path.exists():self.skipTest('Optional original reference unavailable')
        from AnimKey.mods.maya_compat import QtWidgets
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        import maya.standalone
        try:maya.standalone.initialize(name='python')
        except RuntimeError:pass
        import maya.cmds as cmds
        from AnimKey.buttons import copyAnimation as clip
        from AnimKey.core.executionGuard import animkey_execution
        sys.path.insert(0,str(path.parent))
        spec=importlib.util.spec_from_file_location('original_transify_kernel_test',path)
        original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
        tool=original.AnimationCopyPasteJson.__new__(original.AnimationCopyPasteJson)
        tool.om_helper=original.OpenMayaAnimHelper()
        cmds.file(new=True,force=True)
        nodes={}
        for namespace in ('source','reference','animkey'):
            cmds.namespace(add=namespace);nodes[namespace]=cmds.createNode('transform',name=namespace+':ctrl')
        for frame,value in [(1,2),(11,8)]:cmds.setKeyframe(nodes['source'],attribute='translateX',time=frame,value=value)
        for target in ('reference','animkey'):
            for frame,value in [(1,-5),(44,100),(70,33)]:cmds.setKeyframe(nodes[target],attribute='translateX',time=frame,value=value)
        for node in nodes.values():cmds.keyTangent(node,attribute='translateX',inTangentType='auto',outTangentType='auto')
        cmds.select(nodes['source'])
        curve=cmds.keyframe(nodes['source'],query=True,name=True)[0]
        payload=tool.get_anim_data(anim_curves=[curve],range_all=True)
        cmds.currentTime(40)
        self.assertTrue(tool.apply_animation_data(copy.deepcopy(payload),paste_in_place=True,target_namespace='reference'))
        with animkey_execution('Original kernel test'),mock.patch.object(clip,'get_selected_time_range',return_value=None),mock.patch.object(clip,'_selected_copy_channels',return_value=[]),mock.patch.object(clip,'_export_buffer_to_disk'),mock.patch.object(clip,'_sync_buffer_from_disk'):
            cmds.select(nodes['source']);clip.copy_animation()
            cmds.currentTime(40);cmds.select(nodes['animkey']);clip.paste_insert_animation()
        for flag in ('timeChange','valueChange'):
            self.assertEqual(cmds.keyframe(nodes['reference'],query=True,**{flag:True}),cmds.keyframe(nodes['animkey'],query=True,**{flag:True}))
        for frame in [x/2.0 for x in range(1,161)]:
            a=cmds.keyframe(nodes['reference'],query=True,eval=True,time=(frame,frame))[0]
            b=cmds.keyframe(nodes['animkey'],query=True,eval=True,time=(frame,frame))[0]
            self.assertAlmostEqual(a,b,places=10)
        self.assertAlmostEqual(cmds.keyframe(nodes['reference'],query=True,eval=True,time=(45,45))[0],4.039965986394542)


if __name__=='__main__':unittest.main(verbosity=2)
