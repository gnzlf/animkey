"""Run with Maya 2024 mayapy; creates only disposable standalone scenes."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import maya.api.OpenMaya as om
from AnimKey.buttons import tempPivot as pivot
from AnimKey.buttons import tempControls as tc
from AnimKey.core import temp_transform as tf
from AnimKey.mods import configMod

TEST_DATA = tempfile.mkdtemp(prefix="animkey_temp_test_")
configMod.get_user_folder_path = lambda:TEST_DATA


class TempToolsTest(unittest.TestCase):
    def setUp(self):
        pivot.deactivate_temp_pivot()
        cmds.file(new=True,force=True)
        cmds.undoInfo(state=True)
        cmds.playbackOptions(minTime=1,maxTime=10)
        cmds.currentTime(1)
        cmds.autoKeyframe(state=False)

    def tearDown(self):
        pivot.deactivate_temp_pivot()

    def node(self,name,x=0,y=0,z=0):
        node = cmds.createNode("transform",name=name)
        cmds.setAttr(node+".translate",x,y,z)
        return cmds.ls(node,long=True)[0]

    def animated(self,name="rig"):
        node = self.node(name)
        for frame,x,ry in ((1,0,0),(5,3,25),(10,10,60)):
            cmds.setKeyframe(node,attribute="translateX",time=frame,value=x)
            cmds.setKeyframe(node,attribute="rotateY",time=frame,value=ry)
        return node

    def assertMatrix(self,a,b,epsilon=2e-5):
        self.assertLess(max(abs(x-y) for x,y in zip(list(a),list(b))),epsilon)

    def test_pivot_rigid_delta_and_original_pivots(self):
        node = self.node("target",12)
        anchor = self.node("anchor",10)
        original = cmds.xform(node,query=True,objectSpace=True,rotatePivot=True)
        pivot.activate_temp_pivot([node,anchor],pivot_mode="last",edit_pivot=False)
        cmds.setAttr(pivot._session.proxy.name()+".rotateZ",90)
        self.assertAlmostEqual(cmds.getAttr(node+".translateX"),10,places=5)
        self.assertAlmostEqual(cmds.getAttr(node+".translateY"),2,places=5)
        self.assertAlmostEqual(cmds.getAttr(anchor+".translateX"),10)
        pivot.deactivate_temp_pivot()
        self.assertEqual(original,cmds.xform(node,query=True,objectSpace=True,rotatePivot=True))
        self.assertFalse(cmds.ls("AnimKey_Temp_Pivot*"))

    def test_pivot_center_uses_transform_extrema(self):
        nodes = [self.node("a",0),self.node("b",1),self.node("c",10)]
        pivot.activate_temp_pivot(nodes,pivot_mode="center",edit_pivot=False)
        self.assertAlmostEqual(cmds.xform(pivot._session.proxy.name(),query=True,worldSpace=True,translation=True)[0],5)

    def test_pivot_hierarchy_and_locks(self):
        parent = self.node("parent",2)
        child = self.node("child",4)
        child = cmds.parent(child,parent)[0]
        nodes = cmds.ls([parent,child],long=True)
        cmds.setAttr(parent+".scaleX",lock=True)
        before = [tf.world(n) for n in nodes]
        pivot.activate_temp_pivot(nodes,edit_pivot=False)
        cmds.setAttr(pivot._session.proxy.name()+".translateX",8)
        after = [tf.world(n) for n in nodes]
        self.assertAlmostEqual(list(after[0])[12]-list(before[0])[12],4)
        self.assertAlmostEqual(list(after[1])[12]-list(before[1])[12],4)

    def test_pivot_undo_redo(self):
        node = self.node("target")
        pivot.activate_temp_pivot([node],edit_pivot=False)
        pivot.begin_gesture()
        cmds.setAttr(pivot._session.proxy.name()+".translateX",5)
        pivot._session.finish()
        self.assertAlmostEqual(cmds.getAttr(node+".translateX"),5)
        cmds.undo()
        pivot._session.reposition()
        self.assertAlmostEqual(cmds.getAttr(node+".translateX"),0)
        cmds.redo()
        pivot._session.reposition()
        self.assertAlmostEqual(cmds.getAttr(node+".translateX"),5)

    def test_pivot_autokey(self):
        node = self.animated()
        cmds.currentTime(5)
        cmds.autoKeyframe(state=True)
        pivot.activate_temp_pivot([node],edit_pivot=False)
        pivot.begin_gesture()
        proxy = pivot._session.proxy.name()
        cmds.setAttr(proxy+".translateX",cmds.getAttr(proxy+".translateX")+2)
        pivot._session.finish()
        self.assertAlmostEqual(cmds.keyframe(node,attribute="translateX",query=True,time=(5,5),valueChange=True)[0],5)
        cmds.autoKeyframe(state=False)

    def test_deleted_source_cleanup(self):
        a,b = self.animated("a"),self.animated("b")
        system = tc.create(node=[a,b],system=1,restoreDefaultProperties=True)[0]
        cmds.delete(a)
        tc.clear([system],revert=False)
        self.assertTrue(cmds.objExists(b))
        self.assertFalse(tc.all_systems())

    def test_switch_pivot_to_controls_uses_rig_targets(self):
        source = self.animated()
        pivot.activate_temp_pivot([source],edit_pivot=False)
        system = tc.create(restoreDefaultProperties=True)[0]
        self.assertEqual(tc._sources(system),[source])
        self.assertFalse(pivot.is_active())

    def test_child_space_toggle_both_directions(self):
        source = self.animated()
        system = tc.create(node=[source],space=4,bake_frames=True,restoreDefaultProperties=True)[0]
        control = tc.controls(system)[0]
        cmds.setKeyframe(control,attribute="translateY",time=1,value=5)
        edited = tf.capture([source],[1,5,10])
        tc.set_enabled(system,False,revert=False)
        tc.set_enabled(system,True,revert=True)
        for (_,a),(_,b) in zip(edited,tf.capture([source],[1,5,10])):
            self.assertMatrix(a[0],b[0])
        tc.set_enabled(system,False,revert=True)
        tc.set_enabled(system,True,revert=False)
        for (_,a),(_,b) in zip(edited,tf.capture([source],[1,5,10])):
            self.assertMatrix(a[0],b[0])

    def test_all_families_preserve_samples_and_revert(self):
        for family in range(4):
            with self.subTest(family=family):
                cmds.file(new=True,force=True)
                a = self.animated("a")
                b = self.animated("b")
                cmds.setAttr(b+".translateY",4)
                nodes = [a,b] if family in (1,3) else [a]
                before = tf.capture(nodes,[1,5,10])
                systems = tc.create(node=nodes,system=family,bake_frames=True)
                after = tf.capture(nodes,[1,5,10])
                for (_,first),(_,second) in zip(before,after):
                    for one,two in zip(first,second):
                        self.assertMatrix(one,two)
                tc.clear(systems,revert=True)
                reverted = tf.capture(nodes,[1,5,10])
                for (_,first),(_,second) in zip(before,reverted):
                    for one,two in zip(first,second):
                        self.assertMatrix(one,two)
                self.assertFalse(tc.all_systems())
                self.assertFalse(cmds.ls("joint_a*","joint_b*","Space*","Sub_Orient*",type="transform"))

    def test_bake_toggle_and_clear(self):
        source = self.animated()
        system = tc.create(node=[source])[0]
        control = tc.controls(system)[0]
        cmds.setKeyframe(control,attribute="translateY",time=1,value=7)
        expected = tf.capture([source],[1,5,10])
        tc.set_enabled(system,False,revert=False)
        actual = tf.capture([source],[1,5,10])
        for (_,a),(_,b) in zip(expected,actual):
            self.assertMatrix(a[0],b[0])
        tc.set_enabled(system,True,revert=True)
        self.assertAlmostEqual(cmds.getAttr(control+".translateY",time=1),7)
        tc.clear([system],revert=False)
        final = tf.capture([source],[1,5,10])
        for (_,a),(_,b) in zip(expected,final):
            self.assertMatrix(a[0],b[0])

    def test_relative_object_child_spaces(self):
        for mode in (0,1,2,4,5):
            with self.subTest(mode=mode):
                cmds.file(new=True,force=True)
                parent = self.animated("parent")
                source = self.animated("source")
                source = cmds.parent(source,parent)[0]
                source = cmds.ls(source,long=True)[0]
                relative = self.animated("relative")
                before = tf.capture([source],[1,5,10])
                system = tc.create(node=[source],space=mode,relative=relative if mode in (2,5) else None,bake_frames=True)[0]
                after = tf.capture([source],[1,5,10])
                for (_,a),(_,b) in zip(before,after):
                    self.assertMatrix(a[0],b[0])
                tc.clear([system],revert=True)

    def test_save_reopen_rename_and_clear(self):
        source = self.animated()
        system = tc.create(node=[source])[0]
        source = cmds.rename(source,"renamedRig")
        system = cmds.rename(system,"renamedSystem")
        path = os.path.join(TEST_DATA,"scene.ma")
        cmds.file(rename=path)
        cmds.file(save=True,type="mayaAscii",force=True)
        cmds.file(new=True,force=True)
        cmds.file(path,open=True,force=True)
        systems = tc.all_systems()
        self.assertEqual(len(systems),1)
        tc.clear(systems,revert=True)
        self.assertEqual(cmds.keyframe(source+".translateX",query=True,timeChange=True),[1,5,10])

    def test_fk_orientation_and_position_inheritance(self):
        for orientation in (0,1,2):
            with self.subTest(orientation=orientation):
                cmds.file(new=True,force=True)
                a,b = self.node("a"),self.node("b",y=5)
                system = tc.create(node=[a,b],system=3,restoreDefaultProperties=True)[0]
                cmds.setAttr(system+".chainOrientation",orientation)
                first,second = tc.controls(system)
                cmds.setAttr(first+".rotateZ",90)
                self.assertAlmostEqual(list(tf.world(b))[12],-5,places=5)
                rotation = cmds.xform(second,query=True,worldSpace=True,rotation=True)
                self.assertAlmostEqual(rotation[2],90 if orientation == 2 else 0,places=5)

    def test_group_scale_changes_spacing(self):
        a,b = self.node("a",-2),self.node("b",2)
        system = tc.create(node=[a,b],system=1)[0]
        master = tc._first(system,"master")
        cmds.setAttr(master+".scale",2,2,2)
        self.assertAlmostEqual(list(tf.world(a))[12],-4,places=4)
        self.assertAlmostEqual(list(tf.world(b))[12],4,places=4)

    def test_locked_channels_and_layer_network(self):
        source = self.animated()
        layer = cmds.animLayer("ExistingAnimation")
        cmds.animLayer(layer,edit=True,attribute=source+".translateX")
        cmds.setKeyframe(source,attribute="translateX",animLayer=layer,time=1,value=2)
        cmds.setKeyframe(source,attribute="translateX",animLayer=layer,time=10,value=4)
        cmds.setAttr(source+".rotateZ",lock=True)
        before = tf.capture([source],[1,5,10])
        system = tc.create(node=[source],bake_frames=True)[0]
        tc.clear([system],revert=True)
        self.assertTrue(cmds.objExists(layer))
        self.assertTrue(cmds.getAttr(source+".rotateZ",lock=True))
        for (_,a),(_,b) in zip(before,tf.capture([source],[1,5,10])):
            self.assertMatrix(a[0],b[0])

    def test_extras_and_grab_continuity(self):
        source = self.animated()
        driver = self.animated("driver")
        system = tc.create(node=[source],bake_frames=True)[0]
        before = tf.capture([source],[1,5,10])
        cmds.select(tc.controls(system)[0])
        extra = tc.add_extra()[0]
        cmds.setKeyframe(extra,attribute="translateZ",time=1,value=2)
        self.assertAlmostEqual(list(tf.world(source))[14],2,places=4)
        tc.remove_extra()
        before = tf.capture([source],[1,5,10])
        tc.grab(system,driver=driver,frame=5)
        for (_,a),(_,b) in zip(before,tf.capture([source],[1,5,10])):
            self.assertMatrix(a[0],b[0])
        tc.grab(system,frame=8)
        for (_,a),(_,b) in zip(before,tf.capture([source],[1,5,10])):
            self.assertMatrix(a[0],b[0])

    def test_pivot_edit_preserves_rig_animation(self):
        source = self.animated()
        system = tc.create(node=[source],bake_frames=True,restoreDefaultProperties=True)[0]
        control = tc.controls(system)[0]
        cmds.select(control)
        before = tf.capture([source],[1,5,10])
        helpers = tc.edit_pivot()
        cmds.setAttr(helpers[-1]+".translateY",5)
        tc.finish_pivot_edit()
        for (_,a),(_,b) in zip(before,tf.capture([source],[1,5,10])):
            self.assertMatrix(a[0],b[0])
        self.assertAlmostEqual(list(tf.world(control))[13],5,places=5)
        cmds.select(control)
        tc.reset_pivot()
        self.assertAlmostEqual(list(tf.world(control))[13],0,places=5)
        for (_,a),(_,b) in zip(before,tf.capture([source],[1,5,10])):
            self.assertMatrix(a[0],b[0])

    def test_clear_undo_redo_and_foreign_child(self):
        source = self.animated()
        system = tc.create(node=[source],restoreDefaultProperties=True)[0]
        control = tc.controls(system)[0]
        prop = self.node("foreignProp",3,2,1)
        prop = tf.NodeRef(cmds.parent(prop,control)[0])
        expected = tf.capture([prop.name()],[1,5,10])
        tc.clear([system],revert=True)
        self.assertTrue(prop.name())
        for (_,a),(_,b) in zip(expected,tf.capture([prop.name()],[1,5,10])):
            self.assertMatrix(a[0],b[0])
        cmds.undo()
        self.assertTrue(tc.all_systems())
        cmds.redo()
        self.assertFalse(tc.all_systems())
        self.assertTrue(prop.name())

    def test_joint_orientation_opm_and_pivots(self):
        joint = cmds.createNode("joint",name="jointRig")
        cmds.setAttr(joint+".jointOrient",10,20,30)
        cmds.setAttr(joint+".rotateOrder",4)
        cmds.setAttr(joint+".rotatePivot",1,2,0)
        opm = om.MTransformationMatrix(om.MEulerRotation(0,.3,0).asMatrix())
        opm.setTranslation(om.MVector(4,0,2),om.MSpace.kWorld)
        cmds.setAttr(joint+".offsetParentMatrix",*list(opm.asMatrix()),type="matrix")
        for frame,angle in ((1,0),(5,35),(10,90)):
            cmds.setKeyframe(joint,attribute="rotateY",time=frame,value=angle)
        before = tf.capture([joint],[1,5,10])
        system = tc.create(node=[joint],bake_frames=True,restoreDefaultProperties=True)[0]
        for (_,a),(_,b) in zip(before,tf.capture([joint],[1,5,10])):
            self.assertMatrix(a[0],b[0])
        tc.clear([system],revert=False)
        for (_,a),(_,b) in zip(before,tf.capture([joint],[1,5,10])):
            self.assertMatrix(a[0],b[0])

    def test_membership_rebuild(self):
        a,b,c = [self.animated(name) for name in ("a","b","c")]
        system = tc.create(node=[a,b],system=1,bake_frames=True,restoreDefaultProperties=True)[0]
        before = tf.capture([a,b],[1,5,10])
        rebuilt = tc.rebuild_members(system,[a,b,c])[0]
        self.assertEqual(len(tc.records(rebuilt)),3)
        for (_,first),(_,second) in zip(before,tf.capture([a,b],[1,5,10])):
            for one,two in zip(first,second):
                self.assertMatrix(one,two)

    def test_export_import_with_extra(self):
        from AnimKey.buttons.tempControlsUI import export_setup,import_setup
        source = self.animated()
        system = tc.create(node=[source],restoreDefaultProperties=True)[0]
        cmds.select(tc.controls(system)[0])
        extra = tc.add_extra(parent=True)[0]
        cmds.setKeyframe(extra,attribute="translateZ",time=1,value=4)
        expected = tf.capture([source],[1,5,10])
        path = os.path.join(TEST_DATA,"test.aktc.json")
        export_setup([system],path=path)
        tc.clear([system],revert=True)
        system = import_setup(path=path)[0]
        self.assertEqual(len(tc.controls(system)),2)
        for (_,a),(_,b) in zip(expected,tf.capture([source],[1,5,10])):
            self.assertMatrix(a[0],b[0])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TempToolsTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    pivot.deactivate_temp_pivot()
    maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
