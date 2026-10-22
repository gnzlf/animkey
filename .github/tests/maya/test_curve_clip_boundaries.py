"""Timeline clips preserve the native curve between keys without editing the scene."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.environ.get('ANIMKEY_TEST_ROOT', str(ROOT)))
from PySide2 import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
import maya.cmds as cmds
import maya.api.OpenMaya as om
from AnimKey.core import animation_curve_transfer as transfer


class CurveClipBoundaryTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        cmds.currentUnit(time='film', angle='deg', linear='cm')

    def source(self, keys=((1, 2), (11, 8)), attribute='translateX'):
        node = cmds.createNode('transform', name='source')
        for frame, value in keys:
            cmds.setKeyframe(node, attribute=attribute, time=frame, value=value)
        return node + '.' + attribute

    def capture_clean(self, attr, bounds, key_times=None):
        original = transfer.capture_curve(attr)
        nodes = set(cmds.ls(long=True))
        undo = cmds.undoInfo(query=True, undoName=True)
        redo = cmds.undoInfo(query=True, redoName=True)
        cmds.file(modified=False)
        payload = transfer.capture_curve(attr, time_range=bounds, key_times=key_times)
        self.assertFalse(cmds.file(query=True, modified=True))
        self.assertEqual(set(cmds.ls(long=True)), nodes)
        self.assertEqual(cmds.undoInfo(query=True, undoName=True), undo)
        self.assertEqual(cmds.undoInfo(query=True, redoName=True), redo)
        self.assertEqual(transfer.capture_curve(attr), original)
        return payload

    def assert_shape(self, attr, payload, bounds):
        source_fn = transfer._anim_curve_fn(transfer.resolve_anim_curve(attr))
        clone = transfer.create_temp_curve(payload, name='reconstructed_clip')
        self.assertIsNotNone(clone)
        clip_fn = transfer._anim_curve_fn(clone)
        start, end = bounds
        for step in range(101):
            frame = start + (end-start) * step / 100.
            at = om.MTime(frame, om.MTime.uiUnit())
            self.assertAlmostEqual(source_fn.evaluate(at), clip_fn.evaluate(at), places=7,
                                   msg='Curve shape changed at frame {}'.format(frame))

    def test_range_with_no_source_keys_captures_interpolated_bounds(self):
        attr = self.source()
        payload = self.capture_clean(attr, (3.5, 7.25))
        self.assertEqual(payload['keyframes'], [3.5, 7.25])
        self.assertEqual(payload['clip_range'], [3.5, 7.25])
        self.assertFalse(payload['full_curve'])
        self.assert_shape(attr, payload, (3.5, 7.25))

    def test_weighted_bezier_shape_and_infinity_metadata_survive_clip(self):
        attr = self.source(((1, 0), (10, 10), (20, -5), (30, 7)))
        cmds.keyTangent(attr, edit=True, weightedTangents=True)
        cmds.keyTangent(attr, edit=True, inTangentType='fixed', outTangentType='fixed',
                        inAngle=20, outAngle=30, inWeight=2, outWeight=3)
        cmds.setInfinity(attr, preInfinite='cycle', postInfinite='oscillate')
        payload = self.capture_clean(attr, (4., 16.))
        self.assertEqual(payload['weighted_tangents'], [True])
        self.assertEqual(payload['pre_infinity'], 'cycle')
        self.assertEqual(payload['post_infinity'], 'oscillate')
        self.assert_shape(attr, payload, (4., 16.))

    def test_padding_before_and_after_keys_keeps_constant_shape(self):
        attr = self.source()
        payload = self.capture_clean(attr, (-5., 25.))
        self.assertEqual(payload['keyframes'], [-5., 1., 11., 25.])
        self.assert_shape(attr, payload, (-5., 25.))

    def test_padding_before_and_after_keys_keeps_linear_infinity(self):
        attr = self.source()
        cmds.keyTangent(attr, edit=True, inTangentType='fixed', outTangentType='fixed',
                        inAngle=10, outAngle=15)
        cmds.setInfinity(attr, preInfinite='linear', postInfinite='linear')
        payload = self.capture_clean(attr, (-5., 25.))
        self.assert_shape(attr, payload, (-5., 25.))

    def test_range_entirely_in_linear_infinity_has_both_boundaries(self):
        attr = self.source()
        cmds.keyTangent(attr, edit=True, inTangentType='fixed', outTangentType='fixed',
                        inAngle=10, outAngle=15)
        cmds.setInfinity(attr, preInfinite='linear', postInfinite='linear')
        for bounds in ((-8., -2.), (15., 22.)):
            with self.subTest(bounds=bounds):
                payload = self.capture_clean(attr, bounds)
                self.assertEqual(payload['keyframes'], list(bounds))
                self.assert_shape(attr, payload, bounds)

    def test_one_source_key_extends_as_constant(self):
        attr = self.source(((5, 3),))
        payload = self.capture_clean(attr, (-5., 15.))
        self.assertEqual(payload['keyframes'], [-5., 5., 15.])
        self.assert_shape(attr, payload, (-5., 15.))

    def test_step_interpolation_stays_stepped_at_inserted_boundaries(self):
        attr = self.source(((1, 0), (11, 10), (21, 2)))
        cmds.keyTangent(attr, edit=True, outTangentType='step')
        payload = self.capture_clean(attr, (3., 15.))
        self.assert_shape(attr, payload, (3., 15.))

    def test_trimming_serialized_curve_splits_between_keys_without_scene_changes(self):
        attr = self.source(((1, 2), (11, 8), (21, -5)))
        full = transfer.capture_curve(attr)
        nodes = set(cmds.ls(long=True))
        undo = cmds.undoInfo(query=True, undoName=True)
        cmds.file(modified=False)
        payload = transfer.trim_curve_data(full, 4., 17.)
        self.assertFalse(cmds.file(query=True, modified=True))
        self.assertEqual(set(cmds.ls(long=True)), nodes)
        self.assertEqual(cmds.undoInfo(query=True, undoName=True), undo)
        self.assert_shape(attr, payload, (4., 17.))

    def test_angular_ui_values_and_modified_scene_are_preserved(self):
        attr = self.source(((1, 10), (11, 60)), attribute='rotateY')
        self.assertTrue(cmds.file(query=True, modified=True))
        payload = transfer.capture_curve(attr, time_range=(3., 9.))
        self.assertTrue(cmds.file(query=True, modified=True))
        for frame, ui_value in zip(payload['keyframes'], payload['values']):
            self.assertAlmostEqual(cmds.getAttr(attr, time=frame), ui_value)
        self.assert_shape(attr, payload, (3., 9.))

    def test_graph_editor_key_selection_remains_sparse(self):
        attr = self.source(((1, 0), (11, 10), (21, 2)))
        payload = self.capture_clean(attr, (1., 21.), key_times=[11.])
        self.assertEqual(payload['keyframes'], [11.])


if __name__ == '__main__':
    unittest.main()
