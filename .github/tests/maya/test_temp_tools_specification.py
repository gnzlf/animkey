"""Independent contracts from the supplied Temp Pivot / Controls specifications."""

import os
import tempfile
import unittest
from unittest import mock
from AnimKey.mods.maya_compat import QtCore,QtWidgets
APP=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import tempPivot as pivot,tempControls as control
from AnimKey.core import temp_tools as tt
from AnimKey.core.runtime_lifecycle import close_scope


class SceneCase(unittest.TestCase):
    def setUp(self):
        pivot.deactivate_temp_pivot(restore_selection=False)
        cmds.file(new=True,force=True)
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patch=mock.patch.object(control,"_memory_path",return_value=os.path.join(self.folder.name,"appearance.json"))
        patch.start();self.addCleanup(patch.stop)
        pivot._saved_pivots.clear();pivot._previous_targets=[];pivot._last_anchor=None
        cmds.playbackOptions(minTime=1,maxTime=6,animationStartTime=1,animationEndTime=6)

    def tearDown(self):
        pivot.deactivate_temp_pivot(restore_selection=False)
        close_scope("temp_controls.pivots")

    def animated(self,name="source",x=0,parent=None):
        node=cmds.createNode("transform",name=name,**({"parent":parent} if parent else {}))
        for time,position,rotation in ((1,x,0),(3,x+3,25),(6,x+7,65)):
            cmds.setKeyframe(node,attribute="translateX",time=time,value=position)
            cmds.setKeyframe(node,attribute="rotateY",time=time,value=rotation)
        cmds.currentTime(1)
        return node

    def assert_motion(self,node,expected,places=5):
        for time,values in expected.items():
            actual=list(tt.matrix(node,time))
            for a,b in zip(actual,values): self.assertAlmostEqual(a,b,places=places,msg="{} at {}".format(node,time))


class TempPivotSpecificationTests(SceneCase):
    def test_standard_moves_the_anchor_as_well_as_the_other_targets(self):
        a=cmds.createNode("transform",name="a");b=cmds.createNode("transform",name="b")
        cmds.setAttr(a+".tx",12);cmds.setAttr(b+".tx",10)
        state=pivot.activate_temp_pivot([a,b],edit_pivot=False)
        with pivot.gesture() as helper: cmds.setAttr(helper+".rz",90)
        self.assertAlmostEqual(cmds.getAttr(a+".tx"),10)
        self.assertAlmostEqual(cmds.getAttr(a+".ty"),2)
        self.assertAlmostEqual(cmds.getAttr(b+".rz"),90)

    def test_last_object_is_not_transformed_and_can_be_reused(self):
        a=cmds.createNode("transform",name="a");anchor=cmds.createNode("transform",name="anchor")
        cmds.setAttr(a+".tx",12);cmds.setAttr(anchor+".tx",10)
        pivot.activate_temp_pivot([a,anchor],pivot_mode="last",edit_pivot=False)
        with pivot.gesture() as helper: cmds.setAttr(helper+".rz",90)
        self.assertEqual(cmds.getAttr(anchor+".rz"),0)
        pivot.deactivate_temp_pivot()
        self.assertEqual(cmds.ls(selection=True),[a])
        state=pivot.activate_temp_pivot([a],pivot_mode="last",edit_pivot=False)
        self.assertEqual(tt.resolve(state["anchor_id"]),tt.resolve(anchor))

    def test_center_is_extents_of_translations_not_average_or_shape_bounds(self):
        nodes=[cmds.polyCube(name="cube"+str(i))[0] for i in range(3)]
        for node,x in zip(nodes,(0,1,10)): cmds.setAttr(node+".tx",x)
        cmds.setAttr(nodes[0]+".sx",100)
        state=pivot.activate_temp_pivot(nodes,pivot_mode="center",edit_pivot=False)
        self.assertAlmostEqual(list(tt.matrix(tt.resolve(state["helper_id"])))[12],5)

    def test_world_frame_is_fixed_across_time_and_standard_tracks_anchor(self):
        source=self.animated()
        state=pivot.activate_temp_pivot([source],pivot_mode="world",edit_pivot=False)
        helper=tt.resolve(state["helper_id"]);before=list(tt.matrix(helper))
        cmds.currentTime(6)
        self.assertEqual(list(tt.matrix(helper)),before)
        pivot.deactivate_temp_pivot()
        state=pivot.activate_temp_pivot([source],edit_pivot=False)
        cmds.currentTime(1)
        self.assertAlmostEqual(list(tt.matrix(tt.resolve(state["helper_id"])))[12],0)

    def test_pivots_are_never_modified_and_helpers_are_deleted_after_gesture(self):
        node=cmds.createNode("transform",name="rig")
        cmds.xform(node,objectSpace=True,rotatePivot=(1,2,3),scalePivot=(3,2,1))
        original={attr:cmds.getAttr(node+"."+attr) for attr in ("rotatePivot","scalePivot","rotatePivotTranslate","scalePivotTranslate")}
        state=pivot.activate_temp_pivot([node],edit_pivot=False)
        root=state["root_id"]
        with pivot.gesture() as helper: cmds.setAttr(helper+".tx",4)
        pivot.deactivate_temp_pivot()
        self.assertIsNone(tt.resolve(root))
        for attr,value in original.items(): self.assertEqual(cmds.getAttr(node+"."+attr),value)
        self.assertEqual(cmds.ls("AnimKey_Temp_Pivot*"),[])

    def test_one_undo_after_closing_restores_targets_and_redo_restores_change(self):
        node=cmds.createNode("transform",name="rig")
        pivot.activate_temp_pivot([node],edit_pivot=False)
        cmds.flushUndo()
        with pivot.gesture() as helper: cmds.setAttr(helper+".tx",4)
        pivot.deactivate_temp_pivot()
        self.assertAlmostEqual(cmds.getAttr(node+".tx"),4)
        cmds.undo();self.assertAlmostEqual(cmds.getAttr(node+".tx"),0)
        cmds.redo();self.assertAlmostEqual(cmds.getAttr(node+".tx"),4)

    def test_undo_redo_rebases_without_reapplying_delta(self):
        node=cmds.createNode("transform",name="rig")
        pivot.activate_temp_pivot([node],edit_pivot=False)
        with pivot.gesture() as helper: cmds.setAttr(helper+".tx",4)
        cmds.undo();self.assertAlmostEqual(cmds.getAttr(node+".tx"),0)
        cmds.redo();self.assertAlmostEqual(cmds.getAttr(node+".tx"),4)
        with pivot.gesture() as helper: cmds.setAttr(helper+".tx",5)
        self.assertAlmostEqual(cmds.getAttr(node+".tx"),5)

    def test_hierarchy_does_not_double_the_motion(self):
        parent=cmds.createNode("transform",name="parent")
        child=cmds.createNode("transform",name="child",parent=parent)
        cmds.setAttr(child+".tx",2)
        pivot.activate_temp_pivot([child,parent],edit_pivot=False)
        with pivot.gesture() as helper: cmds.setAttr(helper+".ty",3)
        self.assertAlmostEqual(cmds.xform(child,query=True,worldSpace=True,translation=True)[1],3)

    def test_session_memory_is_local_to_anchor_and_reset_clears_it(self):
        node=cmds.createNode("transform",name="rig")
        state=pivot.activate_temp_pivot([node],edit_pivot=False)
        helper=tt.resolve(state["helper_id"])
        with tt.unrecorded(): cmds.xform(helper,objectSpace=True,rotatePivot=(2,0,0),scalePivot=(2,0,0))
        pivot.deactivate_temp_pivot()
        self.assertIn(tt.resolve(node),pivot._saved_pivots)
        state=pivot.activate_temp_pivot([node],edit_pivot=False)
        self.assertEqual(cmds.xform(tt.resolve(state["helper_id"]),query=True,objectSpace=True,rotatePivot=True),[2,0,0])
        pivot.reset_temp_pivot_offsets();pivot.deactivate_temp_pivot()
        self.assertNotIn(tt.resolve(node),pivot._saved_pivots)

    def test_scene_change_retires_callbacks_and_pending_work(self):
        node=cmds.createNode("transform",name="rig")
        state=pivot.activate_temp_pivot([node],edit_pivot=False)
        scope=state["scope"]
        cmds.file(new=True,force=True)
        self.assertFalse(scope.active);self.assertEqual(scope.pending_cleanup,())
        self.assertFalse(pivot.is_active())

    def test_deleting_proxy_closes_session_even_in_last_object_mode(self):
        a=cmds.createNode("transform",name="a");b=cmds.createNode("transform",name="anchor")
        state=pivot.activate_temp_pivot([a,b],pivot_mode="last",edit_pivot=False)
        cmds.delete(tt.resolve(state["helper_id"]))
        APP.processEvents()
        self.assertFalse(pivot.is_active())
        self.assertFalse(state["scope"].active)


class TempControlsSpecificationTests(SceneCase):
    def test_four_systems_preserve_source_motion_at_baked_samples(self):
        for family in range(4):
            with self.subTest(family=family):
                cmds.file(new=True,force=True)
                a=self.animated("sourceA",2);b=self.animated("sourceB",8)
                expected=tt.sample([a,b],[1,2,3,4,5,6])
                roots=control.create([a,b],system=family,frames=True)
                self.assertEqual(len(roots),2 if family in (0,2) else 1)
                for node in (a,b): self.assert_motion(node,expected[node])
                control.clear(roots,revert=True)
                for node in (a,b): self.assert_motion(node,expected[node])

    def test_object_space_follows_parent_scale_and_preserves_local_key_tangents(self):
        parent=self.animated("parent")
        cmds.setAttr(parent+".scale",2,3,4)
        node=self.animated("source",2,parent)
        expected=tt.sample([node],[1,3,6])[node]
        roots=control.create([node],position=1)
        self.assert_motion(node,expected)
        receiver=control._node(roots[0],control._data(roots[0])["entries"][0]["receiver"])
        self.assertEqual(cmds.keyframe(receiver,attribute="tx",query=True,timeChange=True),[1,3,6])

    def test_relative_reserves_last_object_and_child_has_no_feedback_constraint(self):
        source=self.animated();reference=self.animated("reference",10)
        root=control.create([source,reference],position=2)[0]
        self.assertEqual(control._links(root,"sourceNodes"),[tt.resolve(source)])
        self.assertEqual(control._node(root,control._data(root)["relative"]),tt.resolve(reference))
        control.clear([root],revert=True)
        root=control.create([source],position=4)[0]
        self.assertNotIn("output_constraint",control._data(root)["entries"][0])
        self.assertEqual(cmds.listConnections(source+".tx",source=True,plugs=True),[source+"_translateX.output"])

    def test_group_scale_changes_member_spacing(self):
        a=cmds.createNode("transform",name="a");b=cmds.createNode("transform",name="b")
        cmds.setAttr(b+".tx",10)
        root=control.create([a,b],system=1)[0]
        main=control.controls(root)[0]
        cmds.setAttr(main+".sx",2)
        self.assertAlmostEqual(cmds.xform(a,query=True,worldSpace=True,translation=True)[0],-5)
        self.assertAlmostEqual(cmds.xform(b,query=True,worldSpace=True,translation=True)[0],15)

    def test_toggle_revert_both_directions_retains_system(self):
        source=self.animated()
        expected=tt.sample([source],[1,3,6])[source]
        root=control.create([source])[0]
        receiver=control.controls(root)[0]
        cmds.setKeyframe(receiver,attribute="ty",time=3,value=4)
        changed=tt.sample([source],[1,3,6])[source]
        control.set_enabled(root,False,revert=True)
        self.assert_motion(source,expected)
        self.assertTrue(cmds.objExists(root))
        cmds.setKeyframe(receiver,attribute="ty",time=3,value=99)
        control.set_enabled(root,True,revert=True)
        self.assert_motion(source,changed)

    def test_clear_bake_preserves_original_curves_and_result(self):
        source=self.animated()
        original=cmds.listConnections(source+".tx",source=True,type="animCurve")[0]
        original_keys=cmds.keyframe(original,query=True,valueChange=True)
        root=control.create([source],frames=True)[0]
        receiver=control.controls(root)[0]
        cmds.setKeyframe(receiver,attribute="ty",time=3,value=4)
        expected=tt.sample([source],[1,2,3,4,5,6])[source]
        control.clear([root],revert=False)
        self.assert_motion(source,expected)
        self.assertEqual(cmds.keyframe(original,query=True,valueChange=True),original_keys)
        self.assertEqual(control.list_systems(),[])

    def test_rename_save_reopen_and_revert_follow_message_identity(self):
        source=self.animated()
        root=control.create([source])[0]
        source=cmds.rename(source,"renamed")
        path=os.path.join(self.folder.name,"scene.ma")
        cmds.file(rename=path);cmds.file(save=True,type="mayaAscii")
        cmds.file(path,open=True,force=True)
        roots=control.list_systems()
        self.assertEqual(control._links(roots[0],"sourceNodes"),[tt.resolve(source)])
        control.clear(roots,revert=True)
        self.assertEqual(cmds.getAttr(source+".tx",time=6),7)

    def test_failure_rolls_back_sources_and_owned_nodes(self):
        source=self.animated()
        original=cmds.listConnections(source+".tx",source=True,plugs=True)
        before=set(cmds.ls())
        with mock.patch.object(control,"_bind",side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError): control.create([source])
        self.assertEqual(cmds.listConnections(source+".tx",source=True,plugs=True),original)
        self.assertEqual(set(cmds.ls()),before)

    def test_foreign_children_and_names_are_preserved(self):
        source=self.animated()
        foreign=cmds.createNode("transform",name="TEMP_CTRL_foreign")
        root=control.create([source])[0]
        child=cmds.createNode("transform",name="userAttachment",parent=control.controls(root)[0])
        expected=tt.sample([child],[1,2,3,4,5,6])[child]
        control.clear([root],revert=True)
        self.assertTrue(cmds.objExists(foreign))
        self.assert_motion("userAttachment",expected)

    def test_aim_preview_does_not_drive_source_until_confirmed(self):
        source=self.animated()
        original=cmds.listConnections(source+".tx",source=True,plugs=True)
        root=control.create([source],system=2,presetup=True)[0]
        self.assertTrue(cmds.getAttr(root+".preSetup"))
        self.assertEqual(cmds.listConnections(source+".tx",source=True,plugs=True),original)
        data=control._data(root);entry=data["entries"][0]
        aim=control._node(root,entry["aim"])
        cmds.setAttr(aim+".ty",4)
        control.confirm_aim([root])
        self.assertFalse(cmds.getAttr(root+".preSetup"))
        owned=control._links(root,"packNodes")
        self.assertEqual(sum(cmds.nodeType(node)=="joint" for node in owned),2)
        self.assertEqual(sum(cmds.nodeType(node)=="ikHandle" for node in owned),1)
        self.assertTrue(cmds.attributeQuery("twist",node=aim,exists=True))
        self.assertTrue(cmds.attributeQuery("spaceMode",node=aim,exists=True))

    def test_group_add_remove_members_preserves_other_members(self):
        a=self.animated("a");b=self.animated("b",10);c=self.animated("c",20)
        root=control.create([a,b],system=1,frames=True)[0]
        main=control.controls(root)[0]
        cmds.setKeyframe(main,attribute="ty",time=3,value=5)
        expected=tt.sample([a,b],[1,3,6])
        control.add_members([root],[c])
        for node in (a,b): self.assert_motion(node,expected[node])
        data=control._data(root)
        receiver=control._node(root,data["entries"][1]["receiver"])
        control.remove_members([receiver])
        self.assertEqual(set(control._links(root,"sourceNodes")),{tt.resolve(a),tt.resolve(c)})
        self.assert_motion(b,expected[b])
        self.assert_motion(a,expected[a])

    def test_extra_child_parent_and_remove_preserve_motion(self):
        source=self.animated()
        root=control.create([source],frames=True)[0]
        original=control.controls(root)[0]
        expected=tt.sample([source],[1,3,6])[source]
        child=control.add_control([original])[0]
        self.assert_motion(source,expected)
        self.assertTrue(cmds.getAttr(child+".isExtra"))
        control.remove_control([child])
        self.assert_motion(source,expected)
        original=control.controls(root)[0]
        parent=control.add_control([original],parent=True)[0]
        self.assert_motion(source,expected)
        self.assertTrue(cmds.getAttr(parent+".isExtra"))

    def test_fk_reverse_preserves_world_motion_and_has_orientation_choices(self):
        nodes=[self.animated("link"+str(i),i*5) for i in range(3)]
        root=control.create(nodes,system=3,frames=True)[0]
        expected=tt.sample(nodes,[1,3,6])
        control.reorder_fk([root],reverse=True)
        for node in nodes: self.assert_motion(node,expected[node])
        data=control._data(root)
        self.assertEqual(control._node(root,data["entries"][0]["source"]),tt.resolve(nodes[-1]))
        self.assertTrue(all(cmds.attributeQuery("orientationMode",node=control._node(root,e["receiver"]),exists=True) for e in data["entries"]))

    def test_grab_release_has_step_weights_compensation_and_persistent_events(self):
        source=self.animated()
        a=self.animated("driverA",10);b=self.animated("driverB",20)
        root=control.create([source],frames=True)[0]
        expected=tt.sample([source],[1,2,3,4,5,6])[source]
        control.grab([root],a,frame=1)
        control.grab([root],b,frame=3)
        control.grab([root],None,frame=5)
        data=control._data(root)
        self.assertEqual([event[0] for event in data["events"]],[1,3,5])
        self.assert_motion(source,expected)
        output=control._node(root,data["grab_output"])
        self.assertTrue(all(tangent=="step" for tangent in cmds.keyTangent(output,attribute="tx",query=True,outTangentType=True)))
        self.assertTrue(any(cmds.nodeType(n)=="parentConstraint" for n in control._links(root,"packNodes")))
        control.shift_grabs([root],delta=1)
        self.assertIn(4,[event[0] for event in control._data(root)["events"]])

    def test_json_export_import_restores_system_and_control_animation(self):
        source=self.animated()
        root=control.create([source],frames=True)[0]
        receiver=control.controls(root)[0]
        cmds.setKeyframe(receiver,attribute="ty",time=3,value=4)
        expected=tt.sample([source],[1,3,6])[source]
        path=os.path.join(self.folder.name,"controls.json")
        control.export_file(path,[root])
        control.clear([root],revert=True)
        roots=control.import_file(path)
        self.assertEqual(len(roots),1)
        self.assert_motion(source,expected)

    def test_export_import_preserves_extra_parent_and_child_controls(self):
        source=self.animated()
        root=control.create([source],frames=True)[0]
        original=control.controls(root)[0]
        parent=control.add_control([original],parent=True)[0]
        child=control.add_control([control.controls(root)[0]])[0]
        cmds.setKeyframe(child,attribute="ty",time=3,value=4)
        expected=tt.sample([source],[1,3,6])[source]
        path=os.path.join(self.folder.name,"extras.json")
        control.export_file(path,[root]);control.clear([root],revert=True)
        roots=control.import_file(path)
        self.assertEqual(len(control.controls(roots[0])),3)
        self.assert_motion(source,expected)

    def test_fk_member_removal_and_append_keep_other_source_animation(self):
        nodes=[self.animated("source"+str(i),i*5) for i in range(3)]
        root=control.create(nodes,system=3,frames=True)[0]
        expected=tt.sample(nodes,[1,3,6])
        entry=control._data(root)["entries"][1]
        control.remove_members([control._node(root,entry["receiver"])])
        self.assert_motion(nodes[0],expected[nodes[0]])
        self.assert_motion(nodes[1],expected[nodes[1]])
        self.assert_motion(nodes[2],expected[nodes[2]])
        extra=self.animated("newSource",20)
        control.add_members([root],[extra])
        self.assertEqual(len(control._data(root)["entries"]),3)

    def test_expression_motion_without_keys_is_sampled_over_the_playback_range(self):
        source=cmds.createNode("transform",name="expressionSource")
        cmds.expression(string=source+".translateX = frame * 2;",alwaysEvaluate=True,unitConversion="all")
        root=control.create([source])[0]
        self.assertEqual(control._data(root)["times"],[1,2,3,4,5,6])
        self.assertAlmostEqual(list(tt.matrix(source,6))[12],12)

    def test_grab_release_json_round_trip_restores_the_compensated_spaces(self):
        source=self.animated();driver=self.animated("grabDriver",10)
        root=control.create([source],frames=True)[0]
        control.grab([root],driver,frame=1)
        control.grab([root],None,frame=4)
        expected=tt.sample([source],[1,2,3,4,5,6])[source]
        path=os.path.join(self.folder.name,"grab.json")
        control.export_file(path,[root]);control.clear([root],revert=True)
        control.import_file(path)
        self.assert_motion(source,expected)

    def test_bake_with_existing_animation_layers_preserves_composed_motion(self):
        source=self.animated()
        layer=cmds.animLayer("artistLayer")
        cmds.animLayer(layer,edit=True,attribute=source+".translateY")
        cmds.setKeyframe(source,attribute="translateY",time=1,value=2,animLayer=layer)
        cmds.setKeyframe(source,attribute="translateY",time=6,value=5,animLayer=layer)
        root=control.create([source],frames=True)[0]
        receiver=control.controls(root)[0]
        cmds.setKeyframe(receiver,attribute="ty",time=3,value=10)
        expected=tt.sample([source],[1,2,3,4,5,6])[source]
        control.clear([root],revert=False)
        self.assert_motion(source,expected)
        self.assertTrue(cmds.objExists(layer))

    def test_pivot_frame_compensation_and_reset_do_not_change_source_motion(self):
        source=self.animated()
        root=control.create([source],frames=True)[0]
        receiver=control.controls(root)[0]
        expected=tt.sample([source],[1,3,6])[source]
        offset=control.om.MMatrix();offset[12]=2
        with control.transaction("test pivot"):
            control._offset_control_pivot(receiver,offset)
        self.assert_motion(source,expected)
        control.reset_pivot([receiver])
        self.assert_motion(source,expected)

    def test_control_pivot_editor_cleans_helpers_and_undo_restores_control_frame(self):
        source=self.animated()
        root=control.create([source],frames=True)[0]
        receiver=control.controls(root)[0]
        original=list(tt.matrix(receiver))
        helpers=control.edit_pivot([receiver])
        with tt.unrecorded():
            cmds.setAttr(helpers[0]+".tx",cmds.getAttr(helpers[0]+".tx")+2)
        cmds.flushUndo()
        with tt.unrecorded():
            cmds.select(receiver,replace=True)
        APP.processEvents()
        self.assertFalse(cmds.objExists(helpers[0]))
        cmds.undo()
        for actual,expected in zip(list(tt.matrix(receiver)),original):
            self.assertAlmostEqual(actual,expected,places=5)


if __name__=="__main__": unittest.main()
