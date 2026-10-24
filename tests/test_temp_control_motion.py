"""Frame-by-frame temporary-control regressions, in disposable Maya scenes."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import tempControls as tc, tempPivot
from AnimKey.core import temp_transform as tf
from AnimKey.mods import configMod


class TempControlMotionTests(unittest.TestCase):
    def setUp(self):
        tempPivot.deactivate_temp_pivot()
        cmds.file(new=True, force=True)
        cmds.playbackOptions(minTime=1, maxTime=12)
        cmds.currentTime(1)
        cmds.autoKeyframe(state=False)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.preference = mock.patch.object(configMod, 'get_user_folder_path', return_value=self.folder.name)
        self.preference.start()
        self.addCleanup(self.preference.stop)
        self.frames = list(range(1, 13))

    def scene(self):
        parent = cmds.createNode('transform', name='animatedParent')
        cmds.setKeyframe(parent, attribute='rotateZ', time=1, value=0)
        cmds.setKeyframe(parent, attribute='rotateZ', time=12, value=105)
        nodes = [cmds.createNode('transform', name=name, parent=parent) for name in ('hand', 'prop')]
        for i, node in enumerate(nodes):
            cmds.setAttr(node+'.translateX', 5+i*4)
            cmds.setKeyframe(node, attribute='translateY', time=1, value=i)
            cmds.setKeyframe(node, attribute='translateY', time=12, value=3+i*2)
        return cmds.ls(nodes, long=True)

    def assertMotion(self, nodes, expected):
        actual = tf.capture(nodes, self.frames)
        error = max(abs(a-b) for (_, left), (_, right) in zip(expected, actual)
                    for one, two in zip(left, right) for a, b in zip(one, two))
        self.assertLess(error, 2e-5)

    def test_default_creation_preserves_rotating_parent_motion_on_every_frame(self):
        nodes = self.scene()
        expected = tf.capture(nodes, self.frames)
        tc.create(node=nodes, restoreDefaultProperties=True)
        self.assertMotion(nodes, expected)

    def test_group_master_follows_motion_with_zero_local_offset(self):
        nodes = self.scene()
        expected = tf.capture(nodes, self.frames)
        system = tc.create(node=nodes, system=1, restoreDefaultProperties=True)[0]
        master = tc._first(system, 'master')
        poses = tf.capture([master], self.frames)
        self.assertGreater(max(abs(a-b) for a,b in zip(poses[0][1][0], poses[-1][1][0])), 1)
        self.assertTrue(all(abs(cmds.getAttr(master+'.'+attr)) < 1e-6 for attr in tf.TR))
        self.assertMotion(nodes, expected)

    def test_group_follow_matches_the_legacy_average_motion(self):
        nodes = self.scene()
        # The first GitHub implementation followed independent world-baked
        # locators with an averaged parentConstraint and maintainOffset.
        locators = []
        for node in nodes:
            locator = cmds.spaceLocator()[0]
            constraint = cmds.parentConstraint(node,locator)[0]
            cmds.bakeResults(locator,time=(1,12),sampleBy=1,simulation=True,attribute=list(tf.TR))
            cmds.delete(constraint)
            locators.append(locator)
        cmds.currentTime(1)
        control = cmds.createNode('transform')
        positions = [list(tf.world(n))[12:15] for n in nodes]
        cmds.xform(control,worldSpace=True,translation=[sum(p[i] for p in positions)/len(nodes) for i in range(3)])
        cmds.parentConstraint(*(locators+[control]),maintainOffset=True)
        expected = tf.capture([control], self.frames)
        cmds.file(new=True, force=True)
        nodes = self.scene()
        system = tc.create(node=nodes, system=1, restoreDefaultProperties=True)[0]
        self.assertMotion([tc._first(system, 'master')], expected)

    def test_bake_keeps_the_visible_motion_even_for_an_existing_sparse_setup(self):
        nodes = self.scene()
        systems = tc.create(node=nodes, bake_frames=False, restoreDefaultProperties=True)
        for system in systems:
            control = tc.controls(system)[0]
            cmds.setKeyframe(control, attribute='rotateY', time=1, value=0)
            cmds.setKeyframe(control, attribute='rotateY', time=7, value=35)
            cmds.setKeyframe(control, attribute='rotateY', time=12, value=10)
        expected = tf.capture(nodes, self.frames)
        tc.clear(systems, revert=False)
        self.assertMotion(nodes, expected)
        self.assertFalse(tc.all_systems())

    def test_group_offset_and_animated_edit_survive_bake_and_clear(self):
        nodes = self.scene()
        system = tc.create(node=nodes, system=1, restoreDefaultProperties=True)[0]
        master = tc._first(system, 'master')
        cmds.setAttr(master+'.translateZ', 4)
        cmds.setKeyframe(master, attribute='rotateY', time=1, value=0)
        cmds.setKeyframe(master, attribute='rotateY', time=6, value=30)
        cmds.setKeyframe(master, attribute='rotateY', time=12, value=-15)
        expected = tf.capture(nodes, self.frames)
        tc.clear([system], revert=False)
        self.assertMotion(nodes, expected)
        self.assertFalse(cmds.ls('AnimKey_GroupFollowSolve*'))

    def test_group_follow_has_no_feedback_with_selected_parent_and_child(self):
        nodes = self.scene()
        parent = cmds.listRelatives(nodes[0], parent=True, fullPath=True)[0]
        sources = [parent, nodes[0]]
        expected = tf.capture(sources, self.frames)
        system = tc.create(node=sources, system=1, restoreDefaultProperties=True)[0]
        self.assertMotion(sources, expected)
        tc.clear([system], revert=False)
        self.assertMotion(sources, expected)

    def test_group_edited_parent_and_child_survive_bake(self):
        nodes = self.scene()
        parent = cmds.listRelatives(nodes[0],parent=True,fullPath=True)[0]
        sources = [parent,nodes[0]]
        system = tc.create(node=sources,system=1,restoreDefaultProperties=True)[0]
        master = tc._first(system,'master')
        cmds.setKeyframe(master,attribute='rotateY',time=1,value=-10)
        cmds.setKeyframe(master,attribute='rotateY',time=12,value=35)
        cmds.setAttr(master+'.translateZ',3)
        expected = tf.capture(sources,self.frames)
        tc.clear([system],revert=False)
        self.assertMotion(sources,expected)

    def test_group_toggle_and_scene_reopen_preserve_motion(self):
        nodes = self.scene()
        system = tc.create(node=nodes,system=1,restoreDefaultProperties=True)[0]
        cmds.setAttr(tc._first(system,'master')+'.translateZ',3)
        expected = tf.capture(nodes,self.frames)
        tc.set_enabled(system,False)
        self.assertMotion(nodes,expected)
        tc.set_enabled(system,True)
        self.assertMotion(nodes,expected)
        cmds.file(rename=str(Path(self.folder.name)/'follow.ma'))
        cmds.file(save=True,type='mayaAscii',force=True)
        cmds.file(new=True,force=True)
        cmds.file(str(Path(self.folder.name)/'follow.ma'),open=True,force=True)
        self.assertMotion(nodes,expected)
        tc.clear(tc.all_systems(),revert=False)
        self.assertMotion(nodes,expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
