"""Real Alembic retiming regressions. Run with Maya's mayapy, offscreen."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons.retimer import Retimer


class AlembicRetimerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="animkey-alembic-")
        cls.cache_file = str(Path(cls.temp.name) / "deforming.abc").replace("\\", "/")
        cmds.loadPlugin("AbcExport", quiet=True)
        cmds.loadPlugin("AbcImport", quiet=True)
        cmds.file(new=True, force=True)
        cmds.currentUnit(time="film")
        cube = cmds.polyCube(name="cachedCube")[0]
        target = cmds.duplicate(cube, name="target")[0]
        cmds.move(0, 8, 0, target + ".vtx[0]", relative=True)
        blend = cmds.blendShape(target, cube)[0]
        cmds.setKeyframe(blend + ".weight[0]", time=1, value=0)
        cmds.setKeyframe(blend + ".weight[0]", time=41, value=1)
        cmds.setKeyframe(cube + ".translateX", time=1, value=0)
        cmds.setKeyframe(cube + ".translateX", time=41, value=40)
        cmds.keyTangent([blend, cube], inTangentType="linear", outTangentType="linear")
        cmds.AbcExport(j='-frameRange 1 41 -worldSpace -root {} -file "{}"'.format(cube, cls.cache_file))

    @classmethod
    def tearDownClass(cls):
        cmds.file(new=True, force=True)
        cls.temp.cleanup()

    def setUp(self):
        self.open_cache()

    def open_cache(self, unit="film", offset=5.0, speed=1.0, upstream_offset=0.0):
        cmds.file(new=True, force=True)
        cmds.currentUnit(time=unit)
        cmds.AbcImport(self.cache_file, mode="import")
        self.cache = cmds.ls(type="AlembicNode")[0]
        self.cube = cmds.ls("cachedCube", long=True)[0]
        self.shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]
        cmds.setAttr(self.cache + ".offset", offset)
        cmds.setAttr(self.cache + ".speed", speed)
        if upstream_offset:
            node = cmds.createNode("animBlendNodeTime", name="originalOffset")
            cmds.connectAttr("time1.outTime", node + ".inputA")
            cmds.setAttr(node + ".inputB", upstream_offset)
            cmds.setAttr(node + ".weightA", 1)
            cmds.setAttr(node + ".weightB", 1)
            cmds.connectAttr(node + ".output", self.cache + ".time", force=True)
        self.source = cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True)
        cmds.playbackOptions(minTime=1, maxTime=21)
        cmds.currentTime(10)
        self.rt = Retimer("cache_test")
        self.rt.create_curve()
        self.rt.objects = [self.cube]

    def point(self, frame):
        cmds.currentTime(frame)
        return cmds.pointPosition(self.shape + ".vtx[0]", world=True)

    def set_warp(self, first=4, last=14):
        curve = self.rt._time_curve_node()
        cmds.setKeyframe(curve, time=1, value=first)
        cmds.setKeyframe(curve, time=21, value=last)
        cmds.keyTangent(curve, inTangentType="linear", outTangentType="linear")

    def assertPoint(self, expected, actual):
        for a, b in zip(expected, actual):
            self.assertAlmostEqual(a, b, places=5)

    def test_curve_changes_real_deformation_with_offsets_and_scene_fps(self):
        for unit in ("film", "pal", "ntsc"):
            with self.subTest(unit=unit):
                self.open_cache(unit=unit, speed=1.3, upstream_offset=-2)
                frames = (3, 8, 15, 20)
                original = {t: self.point(t) for t in frames}
                expected = {t: self.point(0.5 * t + 3.5) for t in frames}
                self.set_warp()
                self.rt.preview_on()
                self.assertTrue(self.rt.is_preview_on())
                for t in frames:
                    self.assertPoint(expected[t], self.point(t))
                self.assertGreater(max(abs(a-b) for a,b in zip(original[20], self.point(20))), 0.1)
                self.rt.preview_off()
                self.assertEqual(self.source, cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True))
                self.assertEqual(5, cmds.getAttr(self.cache + ".offset"))
                self.assertAlmostEqual(1.3, cmds.getAttr(self.cache + ".speed"))
                for t in frames:
                    self.assertPoint(original[t], self.point(t))
                self.assertFalse(cmds.ls("ANIMKEY_RT_cache_test_cache*"))

    def test_group_shape_and_cache_selection_find_same_reader_once(self):
        group = cmds.group(self.cube, name="cacheGroup")
        self.rt.objects = [group]
        self.assertEqual([self.cache], self.rt._alembic_nodes())
        self.rt.objects = cmds.ls("cachedCubeShape", long=True)
        self.assertEqual([self.cache], self.rt._alembic_nodes())
        self.rt.objects = [self.cache]
        self.assertEqual([self.cache], self.rt._alembic_nodes())

    def test_identity_and_edit_while_previewing(self):
        baseline = self.point(15)
        self.rt.preview_on()
        self.assertPoint(baseline, self.point(15))
        self.set_warp(first=8, last=8)
        preview = self.point(15)
        self.assertGreater(max(abs(a-b) for a,b in zip(baseline, preview)), 0.1)
        self.rt.preview_off()
        self.assertPoint(self.point(8), preview)

    def test_expression_offset_and_reverse_playback(self):
        cmds.disconnectAttr(self.source, self.cache + ".time")
        cmds.expression(string=self.cache + ".time = frame - 2;", alwaysEvaluate=True)
        self.source = cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True)
        expected = {t: self.point(22-t) for t in (3, 8, 15, 20)}
        self.set_warp(first=21, last=1)
        self.rt.preview_on()
        for t, point in expected.items():
            self.assertPoint(point, self.point(t))
        self.rt.preview_off()
        self.assertEqual(self.source, cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True))

    def test_removing_cache_during_preview_restores_its_clock(self):
        self.rt.preview_on()
        self.rt.remove_objects([self.cube])
        self.assertFalse(self.rt.is_preview_on())
        self.assertEqual(self.source, cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True))
        self.assertFalse(cmds.ls("ANIMKEY_RT_cache_test_cache*"))

    def test_locked_cache_input_leaves_no_partial_nodes_or_connections(self):
        cmds.setAttr(self.cache + ".time", lock=True)
        self.rt.preview_on()
        self.assertFalse(self.rt.is_preview_on())
        self.assertEqual(self.source, cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True))
        self.assertFalse(cmds.ls("ANIMKEY_RT_cache_test_cache*"))

    def test_referenced_alembic_responds_and_restores_connection(self):
        path = str(Path(self.temp.name) / "cache_reference.ma")
        self.rt.delete()
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(path, reference=True, namespace="asset")
        self.cache = cmds.ls(type="AlembicNode")[0]
        self.cube = cmds.ls("asset:cachedCube", long=True)[0]
        self.shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]
        self.source = cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True)
        cmds.playbackOptions(minTime=1, maxTime=21)
        self.rt = Retimer("cache_test")
        self.rt.create_curve()
        self.rt.objects = [self.cube]
        expected = self.point(11)
        self.set_warp()
        self.rt.preview_on()
        self.assertPoint(expected, self.point(15))
        self.rt.preview_off()
        self.assertEqual(self.source, cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True))

    def test_preview_survives_scene_reopen_and_delete_restores_source(self):
        self.set_warp()
        self.rt.preview_on()
        expected = self.point(17)
        path = str(Path(self.temp.name) / "preview.ma")
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(path, open=True, force=True)
        self.rt = Retimer("cache_test")
        self.assertTrue(self.rt.is_preview_on())
        self.assertPoint(expected, self.point(17))
        self.rt.delete()
        self.assertEqual(self.source, cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True))
        self.assertFalse(cmds.ls("ANIMKEY_RT_cache_test*"))

    def test_bake_keeps_retimed_geometry_and_one_undo(self):
        self.open_cache(upstream_offset=-2, speed=1.2)
        self.set_warp()
        self.rt.preview_on()
        expected = {t: self.point(t) for t in range(1, 22)}
        result = self.rt.bake_full(clean_curves=False)
        self.assertTrue(result["retimer_deleted"])
        self.assertFalse(cmds.objExists(self.rt.curve))
        self.assertFalse(cmds.keyframe(self.cube + ".scaleX", query=True, keyframeCount=True))
        cmds.undo()
        self.assertTrue(cmds.objExists(self.rt.curve))
        self.assertTrue(self.rt.is_preview_on())
        cmds.redo()
        self.assertFalse(cmds.objExists(self.rt.curve))
        for t, point in expected.items():
            self.assertPoint(point, self.point(t))

    def test_alembic_layer_bake_does_not_silently_bake_transform(self):
        self.rt.preview_on()
        source = cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True)
        self.assertIsNone(self.rt.bake_to_anim_layer())
        self.assertEqual(source, cmds.connectionInfo(self.cache + ".time", sourceFromDestination=True))
        self.assertTrue(cmds.objExists(self.rt.curve))

    def test_regular_animation_still_retimes_with_cache(self):
        ctrl = cmds.createNode("transform", name="control")
        cmds.setKeyframe(ctrl + ".translateX", time=1, value=1)
        cmds.setKeyframe(ctrl + ".translateX", time=21, value=21)
        cmds.keyTangent(ctrl, inTangentType="linear", outTangentType="linear")
        self.rt.objects = [ctrl, self.cube]
        self.set_warp()
        self.rt.preview_on()
        cmds.currentTime(15)
        self.assertAlmostEqual(11.0, cmds.getAttr(ctrl + ".translateX"))
        expected = self.point(15)
        self.rt.bake_full(clean_curves=False)
        cmds.currentTime(15)
        self.assertAlmostEqual(11.0, cmds.getAttr(ctrl + ".translateX"))
        self.assertPoint(expected, self.point(15))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AlembicRetimerTests))
    sys.exit(not result.wasSuccessful())
