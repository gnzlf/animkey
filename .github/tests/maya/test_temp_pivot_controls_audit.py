"""Scene ownership and transaction audit for the new Temp Controls engine."""
import os
import tempfile
import unittest
from unittest import mock
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import tempControls as temp
from AnimKey.core import temp_tools as tt


class TempControlsOwnershipTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True,force=True)
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        patch=mock.patch.object(temp,'_memory_path',return_value=os.path.join(self.folder.name,'memory.json'))
        patch.start();self.addCleanup(patch.stop)

    def source(self,name):
        node=cmds.createNode('transform',name=name)
        cmds.setKeyframe(node,attribute='tx',time=1,value=2)
        cmds.setKeyframe(node,attribute='tx',time=6,value=8)
        cmds.currentTime(1)
        return node

    def test_partial_creation_failure_rolls_back_entire_batch(self):
        a=self.source('a');b=self.source('b')
        before=set(cmds.ls())
        original=temp._bind;calls=[]
        def bind(system):
            calls.append(system)
            if len(calls)==2: raise RuntimeError('second source failed')
            return original(system)
        with mock.patch.object(temp,'_bind',side_effect=bind):
            with self.assertRaises(RuntimeError): temp.create([a,b])
        self.assertEqual(set(cmds.ls()),before)

    def test_renamed_source_curve_and_owner_are_resolved_by_identity(self):
        source=self.source('original')
        curve=cmds.listConnections(source+'.tx',source=True,type='animCurve')[0]
        root=temp.create([source])[0]
        source=cmds.rename(source,'newName');curve=cmds.rename(curve,'newCurve');root=cmds.rename(root,'newSystem')
        replacement=cmds.createNode('transform',name='original');cmds.setAttr(replacement+'.tx',99)
        temp.clear([root],revert=True)
        self.assertTrue(cmds.isConnected(curve+'.output',source+'.tx'))
        self.assertEqual(cmds.getAttr(replacement+'.tx'),99)

    def test_clearing_one_system_leaves_the_other_and_foreign_names_intact(self):
        a=self.source('a');b=self.source('b')
        roots=temp.create([a,b])
        foreign=cmds.createNode('transform',name='TEMP_CTRL_userRig')
        temp.clear([roots[0]],revert=True)
        self.assertTrue(cmds.objExists(roots[1]));self.assertTrue(cmds.objExists(foreign))
        self.assertEqual(len(temp.list_systems()),1)

    def test_clear_revert_is_one_undoable_transaction(self):
        source=self.source('source')
        root=temp.create([source])[0]
        cmds.flushUndo();temp.clear([root],revert=True)
        self.assertEqual(temp.list_systems(),[])
        cmds.undo()
        self.assertEqual(len(temp.list_systems()),1)
        self.assertTrue(cmds.getAttr(temp.list_systems()[0]+'.isEnabled'))

    def test_failed_bake_rolls_back_to_live_controls(self):
        source=self.source('source');root=temp.create([source])[0]
        connection=cmds.listConnections(source+'.tx',source=True,plugs=True)
        with mock.patch.object(temp,'_bake_sources',side_effect=RuntimeError('bake failed')):
            with self.assertRaises(RuntimeError): temp.clear([root],revert=False)
        self.assertTrue(cmds.objExists(root));self.assertTrue(cmds.getAttr(root+'.isEnabled'))
        self.assertEqual(cmds.listConnections(source+'.tx',source=True,plugs=True),connection)

    def test_undo_disabled_does_not_mutate_live_system_on_bake(self):
        source=self.source('source');root=temp.create([source])[0]
        cmds.undoInfo(state=False)
        try:
            with self.assertRaises(RuntimeError): temp.clear([root],revert=False)
            self.assertFalse(cmds.undoInfo(query=True,state=True))
            self.assertTrue(cmds.objExists(root))
            self.assertTrue(cmds.getAttr(root+'.isEnabled'))
        finally: cmds.undoInfo(state=True)

    def test_parented_joint_and_locked_axis_keep_source_channels(self):
        parent=self.source('parent')
        joint=cmds.createNode('joint',name='rigJoint',parent=parent)
        cmds.setAttr(joint+'.jointOrient',10,20,0);cmds.setAttr(joint+'.ty',2)
        cmds.setAttr(joint+'.tx',lock=True)
        expected=tt.sample([joint],[1,3,6])[joint]
        root=temp.create([joint],frames=True)[0]
        for time,values in expected.items():
            for a,b in zip(list(tt.matrix(joint,time)),values): self.assertAlmostEqual(a,b,places=5)
        temp.clear([root],revert=False)
        self.assertTrue(cmds.getAttr(joint+'.tx',lock=True))
        self.assertEqual(cmds.getAttr(joint+'.jointOrient')[0],(10,20,0))

    def test_namespaces_do_not_merge_systems_or_sources(self):
        for namespace in ('a','b'): cmds.namespace(add=namespace)
        a=self.source('a:control');b=self.source('b:control')
        roots=temp.create([a,b])
        self.assertEqual(len(roots),2)
        temp.clear([roots[0]],revert=True)
        self.assertEqual(temp._links(roots[1],'sourceNodes'),[tt.resolve(b)])

    def test_selected_range_bake_preserves_keys_outside_that_range(self):
        source=self.source('source');root=temp.create([source],frames=True)[0]
        receiver=temp.controls(root)[0]
        cmds.setKeyframe(receiver,attribute='ty',time=3,value=4)
        with mock.patch.object(tt.ws,'_get_selected_time_range',return_value=(2,4)):
            temp.clear([root],revert=False)
        self.assertEqual(cmds.getAttr(source+'.tx',time=1),2)
        self.assertEqual(cmds.getAttr(source+'.tx',time=6),8)
        self.assertEqual(cmds.getAttr(source+'.ty',time=1),0)
        self.assertEqual(cmds.getAttr(source+'.ty',time=6),0)

    def test_legacy_inputs_are_not_silently_wrapped_in_a_new_system(self):
        source=cmds.createNode('transform',name='legacySource')
        driver=cmds.createNode('decomposeMatrix',name='legacyDriver')
        cmds.addAttr(driver,longName='tempTag',dataType='string')
        cmds.setAttr(driver+'.tempTag','TEMP_CTRL_MATRIX_V2',type='string')
        cmds.connectAttr(driver+'.outputTranslateX',source+'.translateX')
        with self.assertRaisesRegex(ValueError,'legacy Temp Control'):
            temp.create([source])
        self.assertTrue(cmds.isConnected(driver+'.outputTranslateX',source+'.translateX'))
        self.assertEqual(temp.list_systems(),[])


if __name__=='__main__': unittest.main()
