"""Existing copy/paste and mirror actions follow the reference transfer contract."""
import ast
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from AnimKey.mods.maya_compat import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import copyAnimation as clip, mirror
from AnimKey.core import animation_curve_transfer as curves
from AnimKey.core.executionGuard import animkey_execution


def keyed(node, attr, pairs):
    for frame, value in pairs:
        cmds.setKeyframe(node, attribute=attr, time=frame, value=value)


def data(node, attr='translateX'):
    return (cmds.keyframe(node, attribute=attr, query=True, timeChange=True) or [],
            cmds.keyframe(node, attribute=attr, query=True, valueChange=True) or [])


class TransferContractTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        cmds.autoKeyframe(state=False)
        self.folder = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(clip, 'get_user_data_folder', return_value=self.folder.name)
        self.patch.start()
        clip._cleanup_buffer()
        mirror.clear_mirror_cache()
        cmds.namespace(add='Source')
        cmds.namespace(add='Target')
        self.source = cmds.createNode('transform', name='Source:ctrl')
        self.target = cmds.createNode('transform', name='Target:ctrl')
        keyed(self.source, 'translateX', [(-2,-20),(2,20),(5.5,55),(11,110)])
        cmds.select(self.source)

    def tearDown(self):
        # Wait for the owned clipboard writer before removing its temp folder.
        path = clip.get_copy_paste_animation_file()
        with clip._json_write_lock:
            clip._pending_json_writes.pop(path, None)
        self.patch.stop()
        self.folder.cleanup()

    def copy(self, span=None, channels=None):
        with animkey_execution('Transfer regression'), \
             mock.patch.object(clip, 'get_selected_time_range', return_value=span), \
             mock.patch.object(clip, '_selected_copy_channels', return_value=channels or []):
            clip.copy_animation()

    def paste(self, insert=False):
        cmds.select(self.target)
        with animkey_execution('Transfer regression'):
            (clip.paste_insert_animation if insert else clip.paste_animation)()

    def test_range_copies_only_existing_fractional_keys_without_boundary_samples(self):
        self.copy((1,8))
        payload = next(iter(clip._anim_buffer.values()))['translateX']
        self.assertEqual(payload['keyframes'], [2,5.5])
        self.paste()
        self.assertEqual(data(self.target), ([2,5.5],[20,55]))

    def test_empty_range_does_not_manufacture_a_clip(self):
        self.copy((7,9))
        self.assertEqual(clip._anim_buffer, {})

    def test_channel_box_short_names_limit_the_copy(self):
        keyed(self.source, 'translateY', [(2,7),(5,8)])
        self.copy(channels=['ty'])
        self.assertEqual(set(next(iter(clip._anim_buffer.values()))), {'translateY'})

    def test_copy_retains_exact_noncontiguous_graph_selection(self):
        cmds.selectKey(self.source, attribute='translateX', time=(2,2), replace=True)
        cmds.selectKey(self.source, attribute='translateX', time=(11,11), add=True)
        self.copy()
        self.paste()
        self.assertEqual(data(self.target), ([2,11],[20,110]))

    def test_replace_removes_old_animation_outside_clip_and_is_one_undo(self):
        self.copy((1,8))
        keyed(self.target, 'translateX', [(-10,99),(4,99),(30,99)])
        keyed(self.target, 'translateY', [(4,17),(30,27)])
        cmds.currentTime(100)
        cmds.flushUndo()
        self.paste()
        self.assertEqual(data(self.target), ([2,5.5],[20,55]))
        self.assertEqual(data(self.target,'translateY'), ([4,30],[17,27]))
        self.assertEqual(cmds.currentTime(query=True),100)
        cmds.undo()
        self.assertEqual(data(self.target), ([-10,4,30],[99,99,99]))

    def test_insert_clears_global_clip_span_for_every_channel(self):
        cmds.cutKey(self.source, clear=True)
        keyed(self.source,'translateX',[(1,10),(10,100)])
        keyed(self.source,'translateY',[(4,40),(7,70)])
        self.copy()
        keyed(self.target,'translateY',[(19,19),(20.5,99),(25,99),(28,99),(30,30)])
        cmds.currentTime(20)
        self.paste(insert=True)
        self.assertEqual(data(self.target), ([20,29],[10,100]))
        self.assertEqual(data(self.target,'translateY'), ([19,23,26,30],[19,40,70,30]))

    def test_insert_aligns_first_actual_key_instead_of_empty_range_start(self):
        self.copy((1,8))
        cmds.currentTime(40)
        self.paste(insert=True)
        self.assertEqual(data(self.target), ([40,43.5],[20,55]))

    def test_tangents_units_and_infinity_survive_original_time_replace(self):
        keyed(self.source,'rotateY',[(2,35),(8,125)])
        cmds.keyTangent(self.source, attribute='rotateY', weightedTangents=True)
        cmds.keyTangent(self.source, attribute='rotateY', time=(2,2),
                        outTangentType='fixed', outAngle=25, outWeight=2)
        cmds.setInfinity(self.source,attribute='rotateY',preInfinite='linear',postInfinite='cycle')
        expected = [cmds.getAttr(self.source+'.rotateY',time=t) for t in (2,3,4.5,7,8)]
        self.copy()
        self.paste()
        for t,value in zip((2,3,4.5,7,8),expected):
            self.assertAlmostEqual(cmds.getAttr(self.target+'.rotateY',time=t),value,places=5)
        self.assertEqual(cmds.setInfinity(self.target,attribute='rotateY',query=True,postInfinite=True),['cycle'])

    def test_disk_clip_survives_deleted_sources(self):
        self.copy()
        # Force the documented synchronous export for a cross-session read.
        with mock.patch.object(clip,'get_animation_backup_folder',return_value=self.folder.name):
            clip._export_buffer_to_disk(save_to_library=True)
        clip._cleanup_buffer()
        cmds.delete(self.source)
        self.paste()
        self.assertEqual(data(self.target), ([-2,2,5.5,11],[-20,20,55,110]))


class MirrorContractTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True,force=True)
        from AnimKey.mods import configMod
        from AnimKey.core import reference_transfer
        self._folder=tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        patch=mock.patch.object(configMod,'get_user_folder_path',return_value=self._folder.name)
        patch.start();self.addCleanup(patch.stop)
        reference_transfer._TABLES.clear()
        cmds.undoInfo(state=True)
        cmds.autoKeyframe(state=False)
        self.left=cmds.circle(name='armFK_L_CTRL',normal=(1,0,0),constructionHistory=False)[0]
        self.right=cmds.circle(name='armFK_R_CTRL',normal=(1,0,0),constructionHistory=False)[0]
        cmds.setAttr(self.left+'.translateX',2)
        cmds.setAttr(self.right+'.translateX',-2)
        self.snapshot=mirror._build_basic_mirror_snapshot([self.left,self.right])
        self.plane=mirror._snap_sym_plane(self.snapshot)
        self.groups=[dict(info={'controls':[self.left]},snapshot=self.snapshot,sym_plane=self.plane)]
        cmds.select(self.left)

    def tearDown(self):
        mirror._quick_profile_cache.clear()
        mirror.clear_mirror_cache()

    def action(self, func=mirror.mirror, span=None, graph=None):
        value=(span[0],span[1],True) if span else (1,24,False)
        # Exercise the native reader with a real Maya key selection. Only the
        # timeline/UI context is simulated; no transfer/profile work is mocked.
        cmds.select(self.groups[0]['info']['controls'],replace=True)
        curve_nodes=cmds.ls(type='animCurve') or []
        if curve_nodes:cmds.selectKey(curve_nodes,clear=True)
        for entry in graph or []:
            cmds.selectKey(entry['curve'],time=[(frame,frame) for frame in entry['frames']],add=True)
        with animkey_execution('Mirror regression'), \
             mock.patch.object(mirror,'_time_slider_or_playback_range',return_value=value), \
             mock.patch.object(mirror,'_get_selected_channels',return_value=[]):
            return func()

    def test_pose_uses_axes_instead_of_fk_name_rules(self):
        for attr,value in [('translateX',5),('translateY',7),('rotateX',20),('rotateY',35),('rotateZ',45)]:
            cmds.setAttr(self.left+'.'+attr,value)
        self.action()
        for attr,value in [('translateX',-5),('translateY',7),('rotateX',20),('rotateY',-35),('rotateZ',-45)]:
            self.assertAlmostEqual(cmds.getAttr(self.right+'.'+attr),value)

    def test_mirrored_parent_space_copies_all_local_rotations(self):
        parent=cmds.group(self.right,name='mirrored_space')
        cmds.setAttr(parent+'.scaleX',-1)
        mapping=mirror._axis_channel_attr_map(self.left,self.right,self.plane)
        self.assertTrue(all(mapping['rotate'+axis]['mult']==1 for axis in 'XYZ'))
        self.assertTrue(all(mapping['translate'+axis]['mult']==1 for axis in 'XYZ'))

    def test_previous_snapshot_maps_are_upgraded_from_rest_axes(self):
        self.snapshot.pop('channel_mirror_version',None)
        source=mirror._snap_ctrl_data(self.left,self.snapshot)
        source['attr_map']['rotateY']['mult']=1
        mapping=mirror._effective_attr_map(self.left,self.right,self.snapshot,self.plane,source)
        self.assertEqual(mapping['rotateY']['mult'],-1)

    def test_range_mirrors_only_source_keys_and_preserves_outside_animation(self):
        keyed(self.left,'translateX',[(0,2),(2,5),(5.5,9),(12,2)])
        keyed(self.right,'translateX',[(-1,17),(3,99),(13,27)])
        self.action(span=(1,8))
        self.assertEqual(data(self.right), ([-1,2,5.5,13],[17,-5,-9,27]))
        self.assertEqual(data(self.left), ([0,2,5.5,12],[2,5,9,2]))

    def test_graph_mirror_copies_the_selected_span_with_interior_keys(self):
        keyed(self.left,'translateX',[(2,5),(6,9),(10,13)])
        keyed(self.right,'translateX',[(0,17),(4,99),(14,27)])
        curve=curves.resolve_anim_curve(self.left+'.translateX')
        entries=[dict(node=self.left,attr='translateX',curve=curve,frames=[2,10])]
        self.action(graph=entries)
        self.assertEqual(data(self.right), ([0,2,6,10,14],[17,-5,-9,-13,27]))

    def test_both_selected_sides_are_captured_before_the_swap(self):
        self.groups[0]['info']['controls']=[self.left,self.right]
        keyed(self.left,'translateX',[(2,5),(8,9)])
        keyed(self.right,'translateX',[(2,-17),(8,-27)])
        self.action(span=(2,8))
        self.assertEqual(data(self.left), ([2,8],[17,27]))
        self.assertEqual(data(self.right), ([2,8],[-5,-9]))

    def test_pose_on_animated_target_survives_scrubbing_without_autokey(self):
        cmds.setAttr(self.left+'.rotateY',42)
        keyed(self.right,'rotateY',[(1,0),(10,0)])
        cmds.currentTime(4)
        self.action()
        cmds.currentTime(10)
        cmds.currentTime(4)
        self.assertAlmostEqual(cmds.getAttr(self.right+'.rotateY'),-42)
        self.assertEqual(data(self.right,'rotateY')[0],[1,4,10])

    def test_all_mirror_includes_keys_outside_playback(self):
        keyed(self.left,'translateX',[(-10,5),(90,9)])
        keyed(self.right,'translateX',[(-10,-17),(90,-27)])
        self.action(func=mirror.all_mirror)
        self.assertEqual(data(self.left), ([-10,90],[17,27]))
        self.assertEqual(data(self.right), ([-10,90],[-5,-9]))

    def test_directional_mirror_respects_timeline_animation(self):
        keyed(self.left,'translateX',[(2,5),(8,9)])
        self.action(func=mirror.mirror_to_right,span=(2,8))
        self.assertEqual(data(self.right), ([2,8],[-5,-9]))

    def test_paste_opposite_uses_the_same_axes_and_replace_rules(self):
        keyed(self.left,'rotateY',[(2,35),(8,65)])
        keyed(self.right,'rotateY',[(-10,99),(4,99),(30,99)])
        with tempfile.TemporaryDirectory() as folder, animkey_execution('Mirror regression'), \
             mock.patch.object(clip,'get_user_data_folder',return_value=folder), \
             mock.patch.object(mirror,'get_user_data_folder',return_value=folder), \
             mock.patch.object(clip,'get_selected_time_range',return_value=None), \
             mock.patch.object(clip,'_selected_copy_channels',return_value=[]):
            clip.copy_animation()
            clip.paste_opposite_animation()
            self.assertEqual(data(self.right,'rotateY'),([2,8],[-35,-65]))
            self.assertEqual(data(self.left,'rotateY'),([2,8],[35,65]))
            cmds.undo()
            self.assertEqual(data(self.right,'rotateY'),([-10,4,30],[99,99,99]))
            self.assertEqual(data(self.left,'rotateY'),([2,8],[35,65]))
            with clip._json_write_lock:
                clip._pending_json_writes.pop(clip.get_copy_paste_animation_file(),None)

    def test_graph_mirror_keeps_the_original_key_selection(self):
        keyed(self.left,'translateX',[(2,5),(6,9),(10,13)])
        curve=curves.resolve_anim_curve(self.left+'.translateX')
        cmds.selectKey(curve,time=(2,2),replace=True)
        cmds.selectKey(curve,time=(10,10),add=True)
        entries=[dict(node=self.left,attr='translateX',curve=curve,frames=[2,10])]
        self.action(graph=entries)
        self.assertEqual(cmds.keyframe(curve,query=True,selected=True,timeChange=True),[2,10])
        self.assertEqual(cmds.ls(selection=True),[self.left])

    def test_main_action_resolves_a_real_rig_without_injected_profiles(self):
        cmds.setAttr(self.left+'.rotateY',42)
        with tempfile.TemporaryDirectory() as folder, animkey_execution('Mirror regression'), \
             mock.patch.object(mirror,'get_user_data_folder',return_value=folder), \
             mock.patch.object(mirror,'_time_slider_or_playback_range',return_value=(1,24,False)), \
             mock.patch.object(mirror,'_selected_graph_curve_entries',return_value=[]), \
             mock.patch.object(mirror,'_get_selected_channels',return_value=[]):
            mirror.mirror()
        self.assertAlmostEqual(cmds.getAttr(self.right+'.rotateY'),-42)

    def test_central_control_reflects_itself(self):
        node=cmds.circle(name='center',constructionHistory=False)[0]
        cmds.setAttr(node+'.translateX',5)
        cmds.setAttr(node+'.rotateY',25)
        profile=mirror._build_basic_mirror_snapshot([node])
        plane=mirror._snap_sym_plane(profile)
        mirror._mirror_selected_pose_two_phase([node],profile,plane)
        self.assertAlmostEqual(cmds.getAttr(node+'.translateX'),-5)
        self.assertAlmostEqual(cmds.getAttr(node+'.rotateY'),-25)

    def test_axis_signs_match_the_actual_reference_source(self):
        reference=Path(os.environ.get('ANIMO_REFERENCE_ROOT',
            'C:/Users/ANIM-gonzalo/Desktop/Animo_v10.7.5/Animo_v10.7.5/Animo_Data'))/'Animo_Launcher/mirror_launcher.py'
        if not reference.exists():
            self.skipTest('Optional local reference source is unavailable')
        names={'axisWorldPosition','maxIndex','isAxisMirrored','calculateMirrorAxisForCenter',
               'calculateMirrorAxis','isAttrMirrored','formatValue'}
        tree=ast.parse(reference.read_text(encoding='utf-8-sig'))
        selected=ast.Module(body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names],type_ignores=[])
        class Plane:
            YZ=(-1,1,1);XZ=(1,-1,1);XY=(1,1,-1)
        oracle={'cmds':cmds,'MirrorPlane':Plane}
        exec(compile(selected,str(reference),'exec'),oracle)
        for rotate,scale in [((0,0,0),(1,1,1)),((0,0,0),(-1,1,1)),((0,0,180),(1,1,1))]:
            parent=cmds.group(self.right,name='space')
            cmds.setAttr(parent+'.rotate',*rotate)
            cmds.setAttr(parent+'.scale',*scale)
            axes=oracle['calculateMirrorAxis'](self.left,self.right,Plane.YZ)
            mapping=mirror._axis_channel_attr_map(self.left,self.right,self.plane)
            for attr in ('translateX','translateY','translateZ','rotateX','rotateY','rotateZ','scaleX'):
                self.assertEqual(mapping[attr]['mult'],oracle['formatValue'](attr,1,axes),(rotate,scale,attr))
            cmds.parent(self.right,world=True)
            cmds.delete(parent)
            cmds.setAttr(self.right+'.rotate',0,0,0)
            cmds.setAttr(self.right+'.scale',1,1,1)


if __name__=='__main__':
    unittest.main(verbosity=2)
