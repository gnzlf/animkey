"""Independent-source comparisons and lifecycle coverage for Animo Temp Pivot."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from AnimKey.mods.maya_compat import QtCore, QtWidgets, PYSIDE_MAJOR
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import tempPivot as pivot
from AnimKey.core import temp_transform as tf
from AnimKey.core.transfer_reference import temp_pivot as kernel
from AnimKey.mods import configMod

REFERENCE = Path('C:/Users/ANIM-gonzalo/Desktop/Animo_v10.7.5/Animo_v10.7.5/Animo_Data/Animo_Launcher/temp_pivot_launcher.py')


def original_module():
    """Load actual reference definitions, suppressing only the final launcher."""
    if not REFERENCE.exists():
        raise unittest.SkipTest('Optional supplied Animo source is unavailable')
    tree = ast.parse(REFERENCE.read_text(encoding='utf-8-sig'))
    tree.body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Name) and n.value.func.id in ('pivot', 'load_offsets'))]
    module = types.ModuleType('independent_original_temp_pivot')
    exec(compile(tree, str(REFERENCE), 'exec'), module.__dict__)
    return module


class TempPivotAnimoTests(unittest.TestCase):
    def setUp(self):
        pivot.deactivate_temp_pivot(restore_selection=False)
        cmds.file(new=True, force=True)
        cmds.autoKeyframe(state=False)
        cmds.currentTime(6)
        self.temp = tempfile.TemporaryDirectory(prefix='animkey-pivot-')
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(configMod, 'get_user_folder_path', return_value=self.temp.name)
        patch.start()
        self.addCleanup(patch.stop)
        pivot._previous_targets = []
        pivot._previous_anchor = None
        pivot._saved_pivots.clear()

    def tearDown(self):
        pivot.deactivate_temp_pivot(restore_selection=False)
        cmds.file(new=True, force=True)

    def assertMatrix(self, expected, actual):
        self.assertLess(max(abs(a-b) for a,b in zip(expected,actual)), 1e-6)

    def make_scene(self, case):
        parent = cmds.createNode('transform', name='rigParent')
        cmds.setAttr(parent + '.translate', 2, 3, 4)
        cmds.setAttr(parent + '.rotate', 10, -5, 15)
        if case == 'scaled_parent':
            cmds.setAttr(parent + '.scale', 2, 3, 4)
        a = cmds.createNode('joint' if case == 'joint' else 'transform', name='controlA', parent=parent)
        b = cmds.createNode('transform', name='controlB', parent=a if case == 'hierarchy' else parent)
        cmds.setAttr(a + '.translate', 4, 2, -1)
        cmds.setAttr(a + '.rotate', 12, 25, 7)
        cmds.setAttr(b + '.translate', -2, 1, 6)
        cmds.setAttr(b + '.rotate', -10, 8, 20)
        if case == 'joint':
            cmds.setAttr(a + '.jointOrient', 13, 20, 2)
        if case == 'pivots':
            cmds.xform(a, objectSpace=True, rotatePivot=(2,1,0), scalePivot=(-1,2,1))
            cmds.setAttr(a + '.rotateAxis', 8, -3, 11)
        if case == 'rotation_orders':
            cmds.setAttr(a + '.rotateOrder', 4)
            cmds.setAttr(b + '.rotateOrder', 2)
        if case in ('animated','animated_wrap'):
            for node in (a,b):
                for frame,x,ry in ((1,2,4),(6,7,25),(12,3,40)):
                    cmds.setKeyframe(node, attribute='tx', time=frame, value=x)
                    cmds.setKeyframe(node, attribute='ry', time=frame, value=ry-360 if case=='animated_wrap' else ry)
            cmds.currentTime(6)
        return cmds.ls([a,b], long=True)

    def setup_original(self, module, nodes):
        controller = module.LiveTempPivot()
        module._pivot = controller
        module._prefs_path = os.path.join(self.temp.name, 'original.json')
        module._cache_valid = False
        controller.sel = nodes
        controller.ref_obj = nodes[-1]
        cmds.sets(nodes, name=module.PIVOT_SET)
        cmds.group(empty=True, name=module.PIVOT_NULL)
        for attr in ('sx','sy','sz','v'):
            cmds.setAttr(module.PIVOT_NULL + '.' + attr, lock=True, keyable=False)
        cmds.matchTransform(module.PIVOT_NULL, nodes[-1], pos=True, rot=True)
        controller.cache_locked_attrs()
        controller.objects_had_keys = any(module.has_keyframes(n, module.TR_ATTRS) for n in nodes)
        controller.build_locators()
        controller.store_pivot_offset()
        controller.last_pivot_matrix = module.get_matrix(module.PIVOT_NULL)
        controller.maybe_finalize = lambda: None  # Pump one deterministic native tick.
        return controller

    def snapshot(self, nodes):
        return {n.rsplit('|',1)[-1]: list(tf.world(n)) for n in nodes}

    def curves(self, nodes):
        result = {}
        for node in nodes:
            for attr in kernel.TR_ATTRS:
                plug = node + '.' + attr
                result[node.rsplit('|',1)[-1] + '.' + attr] = {
                    'time': cmds.keyframe(plug, query=True, timeChange=True) or [],
                    'value': cmds.keyframe(plug, query=True, valueChange=True) or [],
                    'in': cmds.keyTangent(plug, query=True, inTangentType=True) or [],
                    'out': cmds.keyTangent(plug, query=True, outTangentType=True) or [],
                }
        return result

    def test_bundled_engine_ast_matches_supplied_source(self):
        if not REFERENCE.exists():
            self.skipTest('Optional source unavailable')
        metadata = json.loads((ROOT/'AnimKey/core/transfer_reference/temp_pivot_provenance.json').read_text())
        self.assertEqual(hashlib.sha256(REFERENCE.read_bytes()).hexdigest(), metadata['source_sha256'])
        original = ast.parse(REFERENCE.read_text(encoding='utf-8-sig'))
        bundled = ast.parse((ROOT/'AnimKey/core/transfer_reference/temp_pivot.py').read_text())
        original_class = next(n for n in original.body if isinstance(n,ast.ClassDef) and n.name=='LiveTempPivot')
        bundled_class = next(n for n in bundled.body if isinstance(n,ast.ClassDef) and n.name=='LiveTempPivot')
        original_methods = {n.name:n for n in original_class.body if isinstance(n,ast.FunctionDef)}
        class DeliberateInterface(ast.NodeTransformer):
            def visit_Constant(self,n):
                if n.value=='Animo_PivotLoc_{}': n.value='AnimKey_Temp_Pivot_Locator_{}'
                return n
            def visit_Assign(self,n):
                if any(ast.unparse(t)=='self.time_job' for t in n.targets) and isinstance(n.value,ast.Call) and ast.unparse(n.value.func)=='cmds.scriptJob':
                    return None
                return self.generic_visit(n)
        for method in bundled_class.body:
            self.assertEqual(ast.dump(DeliberateInterface().visit(copy.deepcopy(original_methods[method.name]))), ast.dump(method), method.name)
        original_helpers = {n.name:n for n in original.body if isinstance(n,ast.FunctionDef)}
        for helper in [n for n in bundled.body if isinstance(n,ast.FunctionDef) and n.name in metadata['helpers']]:
            self.assertEqual(ast.dump(original_helpers[helper.name]),ast.dump(helper),helper.name)

    def test_native_manipulation_matches_original_for_rig_setups(self):
        original = original_module()
        for case in ('simple','hierarchy','scaled_parent','joint','pivots','rotation_orders','animated','animated_wrap'):
            with self.subTest(case=case):
                outputs = []
                for implementation in ('original','animkey'):
                    pivot.deactivate_temp_pivot(restore_selection=False)
                    cmds.file(new=True,force=True)
                    cmds.currentTime(6)
                    nodes = self.make_scene(case)
                    if implementation == 'original':
                        controller = self.setup_original(original,nodes)
                        helper = original.PIVOT_NULL
                    else:
                        pivot.activate_temp_pivot(nodes,edit_pivot=False)
                        controller = pivot._session
                        helper = controller.proxy.name()
                        pivot.begin_gesture()
                    # Relocate the manipulation pivot, then apply native moves.
                    cmds.xform(helper, objectSpace=True, rotatePivot=(1,2,-1), scalePivot=(1,2,-1))
                    if implementation == 'original': controller.refresh_locators()
                    for attr,value in (('rz',35),('tx',cmds.getAttr(helper+'.tx')+3),('ry',-12)):
                        cmds.setAttr(helper+'.'+attr,value)
                        if implementation == 'original': controller.on_timer_tick()
                    if implementation == 'original':
                        controller.finalize_manip_chunk()
                    else:
                        controller.finish()
                    pose = self.snapshot(nodes)
                    if implementation == 'original': controller.clear()
                    else: pivot.deactivate_temp_pivot()
                    outputs.append((pose,self.curves(nodes)))
                for node in outputs[0][0]:
                    self.assertMatrix(outputs[0][0][node],outputs[1][0][node])
                for plug,expected in outputs[0][1].items():
                    actual=outputs[1][1][plug]
                    self.assertEqual(expected['time'],actual['time'],plug)
                    self.assertEqual(expected['in'],actual['in'],plug)
                    self.assertEqual(expected['out'],actual['out'],plug)
                    if expected['value']:self.assertMatrix(expected['value'],actual['value'])

    def test_set_key_on_proxy_forwards_to_targets_like_original(self):
        original = original_module()
        outputs=[]
        for engine in ('original','animkey'):
            pivot.deactivate_temp_pivot(restore_selection=False)
            cmds.file(new=True,force=True)
            cmds.currentTime(6)
            nodes=self.make_scene('simple')
            if engine=='original':
                controller=self.setup_original(original,nodes);helper=original.PIVOT_NULL
            else:
                pivot.activate_temp_pivot(nodes,edit_pivot=False);controller=pivot._session;helper=controller.proxy.name()
                pivot.begin_gesture()
            cmds.setAttr(helper+'.ty',cmds.getAttr(helper+'.ty')+4)
            if engine=='original':controller.on_timer_tick()
            cmds.setKeyframe(helper,attribute=kernel.TR_ATTRS)
            if engine=='original':controller.on_timer_tick()
            else:controller.tick(force=True)
            self.assertEqual(kernel.get_key_count(helper,kernel.TR_ATTRS),0)
            outputs.append((self.snapshot(nodes),self.curves(nodes)))
            if engine=='original':controller.cleanup_setup()
            else:pivot.deactivate_temp_pivot()
        for node in outputs[0][0]: self.assertMatrix(outputs[0][0][node],outputs[1][0][node])
        for plug,expected in outputs[0][1].items():
            self.assertEqual(expected['time'],outputs[1][1][plug]['time'])
            self.assertMatrix(expected['value'],outputs[1][1][plug]['value'])

    def test_time_follow_matches_original_without_modifying_animation(self):
        original=original_module();outputs=[]
        for engine in ('original','animkey'):
            pivot.deactivate_temp_pivot(restore_selection=False)
            cmds.file(new=True,force=True);cmds.currentTime(6)
            nodes=self.make_scene('animated');before=self.curves(nodes)
            if engine=='original':
                controller=self.setup_original(original,nodes);helper=original.PIVOT_NULL
            else:
                pivot.activate_temp_pivot(nodes,edit_pivot=False);controller=pivot._session;helper=controller.proxy.name()
            cmds.xform(helper,objectSpace=True,rotatePivot=(2,0,1),scalePivot=(2,0,1))
            controller.store_pivot_offset();controller.refresh_locators()
            result=[]
            for frame in (1,12,3,6):
                cmds.currentTime(frame)
                if engine=='original':controller.on_time_changed();controller.kill_jobs()
                else:controller.reposition()
                result.append(list(tf.world(helper)))
            self.assertEqual(before,self.curves(nodes))
            outputs.append(result)
            if engine=='original':controller.cleanup_setup()
            else:pivot.deactivate_temp_pivot()
        for a,b in zip(*outputs):self.assertMatrix(a,b)

    def test_saved_pivot_survives_reactivation_and_renamed_targets(self):
        node=cmds.createNode('transform',name='control')
        cmds.setAttr(node+'.translate',4,2,0)
        pivot.activate_temp_pivot([node],edit_pivot=False)
        helper=pivot._session.proxy.name()
        cmds.xform(helper,objectSpace=True,rotatePivot=(2,3,1),scalePivot=(2,3,1))
        expected=list(tf.world(helper))
        pivot.deactivate_temp_pivot()
        node=cmds.rename(node,'renamedControl')
        cmds.createNode('transform',name='control')
        pivot.activate_temp_pivot([node],edit_pivot=False)
        self.assertMatrix(expected,list(tf.world(pivot._session.proxy.name())))
        self.assertEqual(cmds.getAttr(pivot._session.proxy.name()+'.rotatePivot')[0],(2,3,1))

    def test_no_selection_recovers_persisted_targets_by_identity(self):
        node=cmds.createNode('transform',name='control')
        pivot.activate_temp_pivot([node],edit_pivot=False)
        pivot.deactivate_temp_pivot()
        node=cmds.rename(node,'renamed')
        pivot._previous_targets=[]
        cmds.select(clear=True)
        self.assertTrue(pivot.activate_temp_pivot(edit_pivot=False))
        self.assertEqual(pivot._session.targets[0].name(),cmds.ls(node,long=True)[0])

    def test_no_edit_or_time_scrub_does_not_add_target_keys(self):
        nodes=self.make_scene('animated');before=self.curves(nodes)
        pivot.activate_temp_pivot(nodes,edit_pivot=False)
        for frame in (1,12,3,6):cmds.currentTime(frame);pivot._session.reposition()
        pivot.deactivate_temp_pivot()
        self.assertEqual(before,self.curves(nodes))

    def test_undo_redo_one_gesture_and_callback_cleanup(self):
        node=cmds.createNode('transform',name='control')
        pivot.activate_temp_pivot([node],edit_pivot=False)
        session=pivot._session
        with pivot.gesture() as helper:cmds.setAttr(helper+'.tx',5)
        cmds.undo();session.recache();self.assertAlmostEqual(cmds.getAttr(node+'.tx'),0)
        cmds.redo();session.recache();self.assertAlmostEqual(cmds.getAttr(node+'.tx'),5)
        pivot.deactivate_temp_pivot()
        self.assertTrue(session.closed);self.assertFalse(session.callbacks);self.assertIsNone(session.timer)
        self.assertFalse(cmds.ls('AnimKey_Temp_Pivot*'))

    def test_scene_change_retires_session_without_saving_unrelated_selection(self):
        node=cmds.createNode('transform',name='control')
        pivot.activate_temp_pivot([node],edit_pivot=False);session=pivot._session
        cmds.file(new=True,force=True)
        self.assertTrue(session.closed);self.assertFalse(session.callbacks)
        self.assertFalse(pivot.is_active())

    def test_reset_keeps_targets_and_resets_the_manipulation_frame(self):
        node=cmds.createNode('transform',name='control')
        pivot.activate_temp_pivot([node],edit_pivot=False)
        helper=pivot._session.proxy.name()
        cmds.xform(helper,objectSpace=True,rotatePivot=(3,2,1),scalePivot=(3,2,1))
        before=list(tf.world(node))
        pivot.reset_temp_pivot_offsets()
        self.assertMatrix(before,list(tf.world(node)))
        self.assertEqual(cmds.getAttr(helper+'.rotatePivot')[0],(0,0,0))

    def test_pivot_memory_belongs_to_the_full_group_not_just_the_anchor(self):
        a=cmds.createNode('transform',name='a')
        b=cmds.createNode('transform',name='b')
        anchor=cmds.createNode('transform',name='anchor')
        pivot.activate_temp_pivot([a,anchor],edit_pivot=False)
        cmds.xform(pivot._session.proxy.name(),objectSpace=True,rotatePivot=(2,0,0),scalePivot=(2,0,0))
        pivot.deactivate_temp_pivot()
        pivot.activate_temp_pivot([b,anchor],edit_pivot=False)
        self.assertEqual(cmds.getAttr(pivot._session.proxy.name()+'.rotatePivot')[0],(0,0,0))
        pivot.deactivate_temp_pivot()
        pivot.activate_temp_pivot([a,anchor],edit_pivot=False)
        self.assertEqual(cmds.getAttr(pivot._session.proxy.name()+'.rotatePivot')[0],(2,0,0))

    def test_qt_timer_forwards_keys_and_stops_after_close(self):
        def pump(milliseconds):
            loop=QtCore.QEventLoop()
            QtCore.QTimer.singleShot(milliseconds,loop.quit)
            (getattr(loop,'exec',None) or loop.exec_)()
        native_about=cmds.about
        def about(*args,**kwargs):
            return False if kwargs.get('batch') else native_about(*args,**kwargs)
        node=cmds.createNode('transform',name='control')
        with mock.patch.object(cmds,'about',side_effect=about), \
             mock.patch.object(pivot.PivotSession,'_connect_editors'), \
             mock.patch.object(pivot.PivotSession,'_editing',return_value=False), \
             mock.patch.object(cmds,'setToolTo'), mock.patch.object(cmds,'ctxEditMode'), \
             mock.patch.object(cmds,'manipPivot',return_value=(0,0,0)):
            pivot.activate_temp_pivot([node],edit_pivot=False)
            session=pivot._session;timer=session.timer
            self.assertTrue(timer.isActive())
            cmds.setKeyframe(session.proxy.name(),attribute=kernel.TR_ATTRS)
            pump(70)
            self.assertEqual(cmds.keyframe(node+'.tx',query=True,timeChange=True),[6.0])
            self.assertEqual(kernel.get_key_count(session.proxy.name(),kernel.TR_ATTRS),0)
            with mock.patch.object(session,'save_relationship_to_disk',wraps=session.save_relationship_to_disk) as save:
                pump(70)
                save.assert_not_called()
            pivot.deactivate_temp_pivot()
            self.assertFalse(timer.isActive())
            self.assertIsNone(session.timer)
            pump(40)
            self.assertFalse(cmds.ls('AnimKey_Temp_Pivot*'))

    def test_partial_parent_lock_does_not_double_child_motion(self):
        parent=cmds.createNode('transform',name='parent')
        child=cmds.createNode('transform',name='child',parent=parent)
        cmds.setAttr(child+'.translate',2,3,0)
        cmds.setAttr(parent+'.tx',lock=True)
        before=[list(tf.world(n)) for n in (parent,child)]
        pivot.activate_temp_pivot([parent,child],edit_pivot=False)
        with pivot.gesture() as helper:cmds.setAttr(helper+'.ty',cmds.getAttr(helper+'.ty')+4)
        for node,matrix in zip((parent,child),before):
            self.assertAlmostEqual(list(tf.world(node))[13]-matrix[13],4)
        self.assertTrue(cmds.getAttr(parent+'.tx',lock=True))

    def test_existing_options_window_keeps_four_modes_and_direct_actions(self):
        from AnimKey.buttons.tempPivotUI import TempPivotWindow
        node=cmds.createNode('transform',name='control')
        cmds.select(node)
        parent=QtWidgets.QWidget()
        with mock.patch('AnimKey.buttons.tempPivotUI.get_maya_main_window',return_value=parent):
            window=TempPivotWindow()
        try:
            self.assertEqual(window.mode.count(),4)
            window.toggle.click()
            self.assertTrue(pivot.is_active())
            window.mode.setCurrentIndex(3)
            window._run(window._activate)
            self.assertEqual(pivot._session.mode,'world')
            window.reset.click()
            window.toggle.click()
            self.assertFalse(pivot.is_active())
        finally:
            window.close()
            window.deleteLater()
            parent.deleteLater()
            APP.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)


if __name__=='__main__':
    unittest.main(verbosity=2)
