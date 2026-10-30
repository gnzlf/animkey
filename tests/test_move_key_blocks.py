"""Move arrows preserve the complete timeline/Graph selection in Maya."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, os.environ.get('ANIMKEY_TEST_ROOT', str(Path(__file__).resolve().parents[1])))
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import keyframe_move as move


class MoveKeyBlocksTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        self.node = cmds.createNode('transform', name='control')
        for attr in ('translateX','translateY'):
            for time,value in ((0,0),(2,20),(4,40),(6,60),(12,120)):
                cmds.setKeyframe(self.node, attribute=attr, time=time, value=value)
        cmds.select(self.node)
        cmds.currentTime(6)

    def times(self, attr='translateX'):
        return cmds.keyframe(self.node, attribute=attr, query=True, timeChange=True)

    def invoke(self, direction, time_range=None, **options):
        with mock.patch.object(move,'selected_time_range',return_value=time_range):
            return move.move_key_with_arrow(direction,objects=[self.node],attrs=['translateX','translateY'],**options)

    def test_timeline_block_moves_all_keys_left_and_keeps_outside_keys(self):
        result = self.invoke(-1,(2,6))
        self.assertEqual(result['moved'],6)
        self.assertEqual(self.times(),[0,1,3,5,12])
        self.assertEqual(self.times('translateY'),[0,1,3,5,12])
        self.assertEqual(cmds.currentTime(query=True),6)

    def test_timeline_block_moves_all_keys_right_from_the_last_selected_frame(self):
        self.invoke(1,(2,6),frame_amount=2)
        self.assertEqual(self.times(),[0,4,6,8,12])
        self.assertEqual(cmds.keyframe(self.node,attribute='translateX',query=True,valueChange=True),[0,20,40,60,120])

    def test_graph_selection_moves_all_selected_curves_and_noncontiguous_keys(self):
        curves = cmds.keyframe(self.node,query=True,name=True)
        for curve in curves:
            cmds.selectKey(curve,time=(2,2),add=True)
            cmds.selectKey(curve,time=(6,6),add=True)
        result = self.invoke(1)
        self.assertEqual(result['moved'],4)
        self.assertEqual(self.times(),[0,3,4,7,12])
        self.assertEqual(self.times('translateY'),[0,3,4,7,12])

    def test_range_takes_precedence_over_a_stale_graph_selection(self):
        other = cmds.createNode('transform',name='unrelated')
        cmds.setKeyframe(other,attribute='translateX',time=3,value=99)
        cmds.selectKey(other,time=(3,3),replace=True)
        cmds.select(self.node)
        self.invoke(1,(2,6))
        self.assertEqual(self.times(),[0,3,5,7,12])
        self.assertEqual(cmds.keyframe(other,query=True,timeChange=True),[3])

    def test_collision_leaves_the_entire_block_unchanged(self):
        before = self.times()
        result = self.invoke(1,(2,6),frame_amount=6)
        self.assertEqual(result['moved'],0)
        self.assertEqual(self.times(),before)
        self.assertEqual(self.times('translateY'),before)

    def test_graph_collision_does_not_fall_back_to_moving_the_current_key(self):
        cmds.selectKey(self.node,attribute='translateX',time=(2,2),replace=True)
        before = self.times()
        result = self.invoke(1,frame_amount=2)
        self.assertEqual(result['moved'],0)
        self.assertEqual(self.times(),before)

    def test_a_single_undo_restores_the_complete_range(self):
        cmds.flushUndo()
        self.invoke(-1,(2,6))
        cmds.undo()
        self.assertEqual(self.times(),[0,2,4,6,12])
        self.assertEqual(self.times('translateY'),[0,2,4,6,12])

    def test_without_range_or_graph_selection_current_and_neighbor_rules_remain(self):
        self.assertEqual(self.invoke(1)['mode'],'current')
        self.assertEqual(self.times(),[0,2,4,7,12])
        cmds.currentTime(5)
        self.assertEqual(self.invoke(-1)['mode'],'neighbor')
        self.assertEqual(self.times(),[0,2,4,5,12])


if __name__ == '__main__':
    unittest.main(verbosity=2)
