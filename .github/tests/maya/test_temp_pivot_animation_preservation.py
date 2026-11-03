"""Animation preservation contracts for the proxy-based Temp Pivot rewrite."""
import unittest
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import tempPivot as pivot
from AnimKey.core import temp_tools as tt, animation_curve_transfer as transfer


class TempPivotAnimationPreservationTests(unittest.TestCase):
    def setUp(self):
        pivot.deactivate_temp_pivot(restore_selection=False)
        cmds.file(new=True,force=True)
        pivot._saved_pivots.clear()

    def tearDown(self):
        pivot.deactivate_temp_pivot(restore_selection=False)

    def animated(self):
        node=cmds.createNode('transform',name='control')
        for frame,value in ((1,2),(6,6),(12,3)):
            cmds.setKeyframe(node,attribute='translateX',time=frame,value=value)
        cmds.keyTangent(node,attribute='translateX',weightedTangents=True)
        cmds.currentTime(6)
        return node

    def test_activation_without_edit_preserves_all_curves_and_adds_no_bake_layer(self):
        node=self.animated()
        before=transfer.capture_curve(node+'.tx')
        layers=cmds.ls(type='animLayer') or []
        pivot.activate_temp_pivot([node],edit_pivot=False)
        for frame in (1,6,12): cmds.currentTime(frame)
        pivot.deactivate_temp_pivot()
        self.assertEqual(transfer.capture_curve(node+'.tx'),before)
        self.assertEqual(cmds.ls(type='animLayer') or [],layers)

    def test_edit_only_changes_current_frame_and_keeps_other_key_values_and_tangents(self):
        node=self.animated()
        curve=transfer.resolve_anim_curve(node+'.tx')
        before={frame:(cmds.keyframe(curve,query=True,time=(frame,frame),valueChange=True),
                       cmds.keyTangent(curve,query=True,time=(frame,frame),inTangentType=True),
                       cmds.keyTangent(curve,query=True,time=(frame,frame),outTangentType=True)) for frame in (1,12)}
        pivot.activate_temp_pivot([node],edit_pivot=False)
        with pivot.gesture() as helper:
            cmds.setAttr(helper+'.tx',cmds.getAttr(helper+'.tx')+3)
        self.assertAlmostEqual(cmds.getAttr(node+'.tx'),9)
        for frame,expected in before.items():
            actual=(cmds.keyframe(curve,query=True,time=(frame,frame),valueChange=True),
                    cmds.keyTangent(curve,query=True,time=(frame,frame),inTangentType=True),
                    cmds.keyTangent(curve,query=True,time=(frame,frame),outTangentType=True))
            self.assertEqual(actual,expected)

    def test_active_additive_layer_receives_keys_without_changing_base_curve(self):
        node=self.animated()
        base=transfer.resolve_anim_curve(node+'.tx')
        base_values=cmds.keyframe(base,query=True,valueChange=True)
        layer=cmds.animLayer('artistLayer')
        cmds.animLayer(layer,edit=True,attribute=node+'.translateX',selected=True,preferred=True)
        cmds.setKeyframe(node,attribute='translateX',time=6,value=8,animLayer=layer)
        before=cmds.getAttr(node+'.tx')
        pivot.activate_temp_pivot([node],edit_pivot=False)
        with pivot.gesture() as helper:
            cmds.setAttr(helper+'.tx',cmds.getAttr(helper+'.tx')+2)
        self.assertAlmostEqual(cmds.getAttr(node+'.tx'),before+2)
        self.assertEqual(cmds.keyframe(base,query=True,valueChange=True),base_values)
        self.assertIsNotNone(transfer.resolve_anim_curve(node+'.tx',layer_name=layer))

    def test_locked_scale_and_unrelated_locked_axis_remain_unchanged(self):
        node=cmds.createNode('transform',name='rig')
        for attr in ('scaleX','scaleY','scaleZ','translateX'): cmds.setAttr(node+'.'+attr,lock=True)
        pivot.activate_temp_pivot([node],edit_pivot=False)
        with pivot.gesture() as helper: cmds.setAttr(helper+'.ty',3)
        self.assertEqual(cmds.getAttr(node+'.tx'),0)
        self.assertAlmostEqual(cmds.getAttr(node+'.ty'),3)
        self.assertEqual(cmds.getAttr(node+'.scale')[0],(1,1,1))

    def test_joint_orientation_pivots_and_scaled_parent_keep_world_delta(self):
        parent=cmds.createNode('transform',name='parent')
        cmds.setAttr(parent+'.scale',2,3,4)
        node=cmds.createNode('joint',name='joint',parent=parent)
        cmds.setAttr(node+'.jointOrient',12,25,5)
        cmds.setAttr(node+'.translate',2,3,4)
        expected=list(tt.matrix(node));expected[13]+=3
        pivot.activate_temp_pivot([node],edit_pivot=False)
        with pivot.gesture() as helper:
            cmds.xform(helper,worldSpace=True,translation=[v+d for v,d in zip(cmds.xform(helper,query=True,worldSpace=True,translation=True),(0,3,0))])
        for a,b in zip(list(tt.matrix(node)),expected): self.assertAlmostEqual(a,b,places=5)
        for actual,expected in zip(cmds.getAttr(node+'.jointOrient')[0],(12,25,5)):
            self.assertAlmostEqual(actual,expected)

    def test_cancelling_gesture_restores_targets_and_the_undo_preference(self):
        node=self.animated()
        before=transfer.capture_curve(node+'.tx')
        pivot.activate_temp_pivot([node],edit_pivot=False)
        with self.assertRaises(RuntimeError):
            with pivot.gesture() as helper:
                cmds.setAttr(helper+'.tx',50)
                raise RuntimeError('cancel')
        self.assertTrue(cmds.undoInfo(query=True,state=True))
        self.assertEqual(transfer.capture_curve(node+'.tx'),before)
        self.assertAlmostEqual(cmds.getAttr(node+'.tx'),6)

    def test_rename_target_does_not_affect_a_new_object_with_the_old_name(self):
        node=self.animated()
        pivot.activate_temp_pivot([node],edit_pivot=False)
        node=cmds.rename(node,'renamed')
        replacement=cmds.createNode('transform',name='control')
        # Creating another node changes the selection in some Maya builds.
        if not pivot.is_active(): pivot.activate_temp_pivot([node],edit_pivot=False)
        with pivot.gesture() as helper:
            cmds.setAttr(helper+'.ty',2)
        self.assertAlmostEqual(cmds.getAttr(node+'.ty'),2)
        self.assertEqual(cmds.getAttr(replacement+'.ty'),0)


if __name__=='__main__': unittest.main()
