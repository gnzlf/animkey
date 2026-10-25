"""World Space animation and ranges keep sparse keys in disposable Maya scenes."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import copyWorldspace as ws
from AnimKey.core.executionGuard import animkey_execution


class WorldspaceKeyTimeTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.autoKeyframe(state=False)
        cmds.undoInfo(state=True)
        cmds.playbackOptions(animationStartTime=1, animationEndTime=24,
                             minTime=1, maxTime=24)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patch = mock.patch.object(ws, "_get_worldspace_folder", return_value=self.folder.name)
        patch.start()
        self.addCleanup(patch.stop)
        context = animkey_execution("Worldspace sparse keys regression")
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def animated(self, name, times, parent=None, attribute="translateX"):
        node = cmds.createNode("transform", name=name, **({"parent": parent} if parent else {}))
        for index, time in enumerate(times):
            cmds.setKeyframe(node, attribute=attribute, time=time, value=index * 7)
        cmds.keyTangent(node, inTangentType="linear", outTangentType="linear")
        return cmds.ls(node, long=True)[0]

    def times(self, node):
        return sorted(set(cmds.keyframe(node, query=True, timeChange=True) or []))

    def assert_poses(self, node, source):
        for time, sample in source["samples"].items():
            cmds.currentTime(float(time))
            actual = cmds.xform(node, query=True, worldSpace=True, matrix=True)
            self.assertLess(max(abs(a-b) for a, b in zip(actual, sample["matrix"])), 1e-5)

    def test_all_animation_pastes_only_original_keys_including_subframes(self):
        times = [1, 7.5, 24]
        source = self.animated("source", times)
        cmds.currentTime(8)
        cmds.select(source)
        data = ws.copy_worldspace_all_animation()
        self.assertEqual(sorted(map(float, data["sources"][0]["samples"])), times)
        target = cmds.createNode("transform", name="target")
        cmds.select(target)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target), times)
        self.assertEqual(cmds.currentTime(query=True), 8)
        self.assert_poses(target, data["sources"][0])

    def test_each_object_keeps_its_own_key_times(self):
        a = self.animated("a", [1, 12, 24])
        b = self.animated("b", [3, 8.25, 20])
        cmds.select([a, b])
        data = ws.copy_worldspace_all_animation()
        targets = [cmds.createNode("transform", name=n) for n in ("targetA", "targetB")]
        cmds.select(targets)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["appliedSamples"], 6)
        for node, source, times in zip(targets, data["sources"], ([1, 12, 24], [3, 8.25, 20])):
            self.assertEqual(self.times(node), times)
            self.assert_poses(node, source)

    def test_all_animation_includes_keys_outside_playback_and_animation_bounds(self):
        node = self.animated("outside", [-4, 7.5, 48])
        cmds.select(node)
        ws.copy_worldspace_all_animation()
        cmds.keyframe(node, edit=True, relative=True, valueChange=40)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(node), [-4, 7.5, 48])
        self.assertAlmostEqual(cmds.getAttr(node+".translateX", time=48), 14)

    def test_parent_curves_are_finished_before_child_subframe_is_solved(self):
        parent = self.animated("parent", [1, 24], attribute="rotateZ")
        child = self.animated("child", [1, 8.5, 24], parent=parent)
        cmds.setAttr(child+".translateY", 5)
        cmds.select([child, parent])
        data = ws.copy_worldspace_all_animation()
        target_parent = self.animated("targetParent", [1, 24], attribute="rotateZ")
        target_child = cmds.createNode("transform", name="targetChild", parent=target_parent)
        cmds.keyframe(target_parent, edit=True, relative=True, valueChange=60)
        cmds.select([target_child, target_parent])
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target_parent), [1, 24])
        self.assertEqual(self.times(target_child), [1, 8.5, 24])
        self.assert_poses(target_child, data["sources"][0])

    def test_unkeyed_child_uses_animated_parent_key_times(self):
        parent = self.animated("driver", [2, 11.5, 22])
        child = cmds.createNode("transform", name="unkeyedChild", parent=parent)
        cmds.setAttr(child+".translateY", 4)
        cmds.select(child)
        data = ws.copy_worldspace_all_animation()
        target = cmds.createNode("transform", name="worldTarget")
        cmds.select(target)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target), [2, 11.5, 22])
        self.assert_poses(target, data["sources"][0])

    def test_constraint_driver_keys_are_preserved_without_a_per_frame_bake(self):
        driver = self.animated("driver", [1, 9.5, 24])
        source = cmds.createNode("transform", name="constrainedSource")
        cmds.parentConstraint(driver, source)
        cmds.select(source)
        data = ws.copy_worldspace_all_animation()
        target = cmds.createNode("transform", name="target")
        cmds.select(target)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target), [1, 9.5, 24])
        self.assert_poses(target, data["sources"][0])

    def test_animation_layer_key_times_are_included(self):
        source = self.animated("layered", [1, 24])
        layer = cmds.animLayer("motionLayer")
        cmds.animLayer(layer, edit=True, attribute=source+".translateX")
        for time, value in ((1, 0), (7.25, 5), (24, 0)):
            cmds.setKeyframe(source, attribute="translateX", time=time,
                            value=value, animLayer=layer, noResolve=True)
        cmds.select(source)
        data = ws.copy_worldspace_all_animation()
        target = cmds.createNode("transform", name="target")
        cmds.select(target)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target), [1, 7.25, 24])
        self.assert_poses(target, data["sources"][0])

    def test_paste_preserves_selection_autokey_and_single_undo(self):
        source = self.animated("source", [1, 12, 24])
        cmds.select(source)
        ws.copy_worldspace_all_animation()
        target = self.animated("target", [1, 6, 24])
        before = cmds.keyframe(target, query=True, timeChange=True)
        values = cmds.keyframe(target, query=True, valueChange=True)
        cmds.select(target)
        cmds.currentTime(5)
        cmds.autoKeyframe(state=True)
        cmds.flushUndo()
        ws.paste_worldspace_animation()
        self.assertEqual(self.times(target), [1, 12, 24])
        self.assertEqual(cmds.ls(selection=True), [target.strip("|")])
        self.assertEqual(cmds.currentTime(query=True), 5)
        self.assertTrue(cmds.autoKeyframe(query=True, state=True))
        cmds.undo()
        self.assertEqual(cmds.keyframe(target, query=True, timeChange=True), before)
        self.assertEqual(cmds.keyframe(target, query=True, valueChange=True), values)

    def test_playback_range_pastes_only_keys_without_adding_range_boundaries(self):
        source = self.animated("source", [1, 3.5, 5, 24])
        cmds.select(source)
        cmds.playbackOptions(minTime=3, maxTime=6)
        ws.copy_worldspace_playback_range()
        target = cmds.createNode("transform", name="target")
        cmds.select(target)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target), [3.5, 5])

    def test_selected_timeline_range_copies_and_pastes_only_inside_keys(self):
        source = self.animated("source", [1, 3.5, 5, 6, 24])
        cmds.select(source)
        with mock.patch.object(ws, "_get_selected_time_range", return_value=(3, 6)):
            data = ws.copy_worldspace_selected_range()
        self.assertEqual(data["range"], [3, 6])
        self.assertEqual(sorted(map(float, data["sources"][0]["samples"])), [3.5, 5, 6])
        target = self.animated("target", [1, 24])
        before = [cmds.getAttr(target+".translateX", time=t) for t in (1, 24)]
        cmds.select(target)
        with mock.patch.object(ws, "_get_selected_time_range", return_value=(4, 6)):
            result = ws.paste_worldspace_selected_range()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target), [1, 5, 6, 24])
        self.assertEqual([cmds.getAttr(target+".translateX", time=t) for t in (1, 24)], before)

    def test_playback_paste_of_all_animation_filters_keys_without_resampling(self):
        source = self.animated("source", [1, 3.5, 5, 24])
        cmds.select(source)
        ws.copy_worldspace_all_animation()
        cmds.playbackOptions(minTime=3, maxTime=6)
        target = cmds.createNode("transform", name="target")
        cmds.select(target)
        self.assertEqual(ws.paste_worldspace_playback_range()["failedSamples"], 0)
        self.assertEqual(self.times(target), [3.5, 5])

    def test_empty_range_keeps_the_previous_clipboard_and_does_not_add_keys(self):
        source = self.animated("source", [1, 24])
        cmds.select(source)
        ws.copy_worldspace_all_animation()
        previous = Path(ws._get_animation_data_file()).read_bytes()
        cmds.playbackOptions(minTime=3, maxTime=6)
        self.assertIsNone(ws.copy_worldspace_playback_range())
        with mock.patch.object(ws, "_get_selected_time_range", return_value=(3, 6)):
            self.assertIsNone(ws.copy_worldspace_selected_range())
            self.assertIsNone(ws.paste_worldspace_selected_range())
        self.assertEqual(Path(ws._get_animation_data_file()).read_bytes(), previous)
        self.assertEqual(self.times(source), [1, 24])

    def test_range_keeps_object_mapping_when_one_source_has_no_keys(self):
        a = self.animated("a", [1, 24])
        b = self.animated("b", [1, 5, 24])
        cmds.select([a, b])
        cmds.playbackOptions(minTime=3, maxTime=7)
        data = ws.copy_worldspace_playback_range()
        self.assertEqual(data["sources"][0]["samples"], {})
        target_a = self.animated("targetA", [3, 7])
        target_b = cmds.createNode("transform", name="targetB")
        before = cmds.keyframe(target_a, query=True, valueChange=True)
        cmds.select([target_a, target_b])
        result = ws.paste_worldspace_playback_range()
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target_a), [3, 7])
        self.assertEqual(cmds.keyframe(target_a, query=True, valueChange=True), before)
        self.assertEqual(self.times(target_b), [5])
        self.assert_poses(target_b, data["sources"][1])

    def test_frame_copy_and_legacy_baked_clipboards_still_paste(self):
        source = self.animated("source", [1, 24])
        cmds.select(source)
        cmds.currentTime(9)
        ws.copy_worldspace_current_frame()
        target = cmds.createNode("transform", name="target")
        cmds.select(target)
        cmds.currentTime(12)
        self.assertEqual(ws.paste_worldspace_current_frame()["failedSamples"], 0)
        self.assertEqual(self.times(target), [12])
        data = ws._capture_payload([source], [2, 3, 4])
        data.pop("sampling")
        result = ws._apply_payload(data, selection=[target])
        self.assertEqual(result["failedSamples"], 0)
        self.assertEqual(self.times(target), [2, 3, 4, 12])

    def master_rig(self, constrained=False, lock_poles=False):
        master = self.animated("master", [1, 8.5, 24])
        for time, value in ((1, 15), (8.5, 40), (24, 80)):
            cmds.setKeyframe(master, attribute="rotateY", time=time, value=value)
        controls, groups = [], []
        for name, position in (("zRoot", (0, 8, 0)), ("aFootL", (3, 0, 2)),
                               ("bFootR", (-3, 0, 2)), ("cKneeL", (3, 4, 6)),
                               ("dKneeR", (-3, 4, 6))):
            group = cmds.createNode("transform", name=name+"Space", parent=master)
            cmds.setAttr(group+".translate", *position)
            node = self.animated(name, [1, 12, 24], parent=group, attribute="translateZ")
            if constrained and controls:
                cmds.parentConstraint(controls[0], group, maintainOffset=True)
            if lock_poles and "Knee" in name:
                for attr in ("rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ",
                             "shearXY", "shearXZ", "shearYZ"):
                    cmds.setAttr(node+"."+attr, lock=True)
            controls.append(node)
            groups.append(cmds.ls(group, long=True)[0])
        cmds.currentTime(6)
        return master, controls, groups

    def test_same_controls_reselected_in_a_different_order_keep_their_own_world_motion(self):
        master, controls, _ = self.master_rig()
        cmds.select(controls)
        data = ws.copy_worldspace_all_animation()
        cmds.cutKey(master, clear=True)
        cmds.setAttr(master+".translateX", 0)
        cmds.setAttr(master+".rotateY", 0)
        cmds.select(list(reversed(controls)))
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        for node, source in zip(controls, data["sources"]):
            self.assert_poses(node, source)

    def test_root_constraint_spaces_are_solved_before_feet_after_master_keys_are_deleted(self):
        master, controls, _ = self.master_rig(constrained=True)
        cmds.select(controls)
        data = ws.copy_worldspace_all_animation()
        cmds.cutKey(master, clear=True)
        cmds.setAttr(master+".translateX", 0)
        cmds.setAttr(master+".rotateY", 0)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        for node, source in zip(controls, data["sources"]):
            self.assert_poses(node, source)

    def test_reparented_controls_keep_identity_after_the_master_node_is_removed(self):
        master, controls, groups = self.master_rig()
        cmds.select(controls)
        data = ws.copy_worldspace_all_animation()
        for group in groups:
            cmds.parent(group, world=True)
        cmds.delete(master)
        controls = [cmds.ls(source["uuid"], long=True)[0] for source in data["sources"]]
        cmds.select(list(reversed(controls)))
        self.assertEqual(ws.paste_worldspace_animation()["failedSamples"], 0)
        for node, source in zip(controls, data["sources"]):
            self.assert_poses(node, source)

    def test_locked_pole_rotation_does_not_block_world_position_restore(self):
        master, controls, _ = self.master_rig(lock_poles=True)
        cmds.select(controls)
        data = ws.copy_worldspace_all_animation()
        cmds.cutKey(master, clear=True)
        cmds.setAttr(master+".translateX", 0)
        cmds.setAttr(master+".rotateY", 0)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        for node, source in zip(controls, data["sources"]):
            for time, sample in source["samples"].items():
                cmds.currentTime(float(time))
                actual = cmds.xform(node, query=True, worldSpace=True, matrix=True)
                self.assertLess(max(abs(actual[i]-sample["matrix"][i]) for i in (12,13,14)), 1e-5)
            if "Knee" in node:
                self.assertTrue(cmds.getAttr(node+".rotateX", lock=True))
                self.assertFalse(cmds.keyframe(node, attribute="rotateX", query=True, timeChange=True))

    def test_pivot_rotate_axis_and_offset_parent_matrix_survive_master_key_removal(self):
        master, controls, _ = self.master_rig(constrained=True)
        offset = cmds.createNode("transform", name="offsetMatrixSource")
        cmds.setAttr(offset+".translate", 2, -1, 3)
        cmds.setAttr(offset+".rotate", 0, 18, 4)
        matrix = cmds.xform(offset, query=True, matrix=True)
        cmds.delete(offset)
        for node in controls:
            cmds.setAttr(node+".rotateOrder", 3)
            cmds.setAttr(node+".rotateAxis", 7, 12, 3)
            cmds.setAttr(node+".rotatePivot", 2, 1, -3)
            cmds.setAttr(node+".offsetParentMatrix", *matrix, type="matrix")
        cmds.select(controls)
        data = ws.copy_worldspace_all_animation()
        cmds.cutKey(master, clear=True)
        cmds.setAttr(master+".translateX", 0)
        cmds.setAttr(master+".rotateY", 0)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        for node, source in zip(controls, data["sources"]):
            self.assert_poses(node, source)
        self.assertFalse(cmds.ls("AnimKey_WorldSolve*"))

    def test_controls_with_existing_animation_layers_restore_after_master_keys_are_deleted(self):
        master, controls, _ = self.master_rig()
        layer = cmds.animLayer("IKCorrections")
        for node in controls:
            cmds.animLayer(layer, edit=True, attribute=node+".translateZ")
            for time, value in ((1, 0), (8.5, 3), (24, 0)):
                cmds.setKeyframe(node, attribute="translateZ", time=time,
                                value=value, animLayer=layer, noResolve=True)
        cmds.select(controls)
        data = ws.copy_worldspace_all_animation()
        cmds.cutKey(master, clear=True)
        cmds.setAttr(master+".translateX", 0)
        cmds.setAttr(master+".rotateY", 0)
        result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 0)
        for node, source in zip(controls, data["sources"]):
            self.assert_poses(node, source)

    def test_original_subset_maps_by_identity_instead_of_requiring_selection_order(self):
        master, controls, _ = self.master_rig()
        cmds.select(controls)
        data = ws.copy_worldspace_all_animation()
        cmds.cutKey(master, clear=True)
        cmds.setAttr(master+".translateX", 0)
        cmds.setAttr(master+".rotateY", 0)
        cmds.select([controls[3], controls[1]])
        self.assertEqual(ws.paste_worldspace_animation()["failedSamples"], 0)
        self.assert_poses(controls[3], data["sources"][3])
        self.assert_poses(controls[1], data["sources"][1])

    def test_final_validation_detects_feet_invalidated_by_a_later_root_solve(self):
        master, controls, _ = self.master_rig(constrained=True)
        cmds.select(controls)
        ws.copy_worldspace_all_animation()
        cmds.cutKey(master, clear=True)
        cmds.setAttr(master+".translateX", 0)
        cmds.setAttr(master+".rotateY", 0)
        with mock.patch.object(ws, "_hierarchy_order", side_effect=lambda mapping: sorted(mapping)):
            result = ws.paste_worldspace_animation()
        self.assertGreater(result["failedSamples"], 0)

    def test_failed_solve_does_not_change_pivot_structure_or_leave_scratch_nodes(self):
        source = self.animated("source", [1, 24])
        cmds.select(source)
        ws.copy_worldspace_all_animation()
        target = cmds.createNode("transform", name="target")
        cmds.setAttr(target+".rotatePivot", 3, 2, 1)
        cmds.setAttr(target+".rotatePivotTranslate", 1, 2, 3)
        cmds.select(target)
        original = {a: cmds.getAttr(target+"."+a) for a in ("rotatePivot", "rotatePivotTranslate")}
        with mock.patch.object(ws, "_apply_world_sample", return_value=(False, 10, False)):
            result = ws.paste_worldspace_animation()
        self.assertEqual(result["failedSamples"], 2)
        for attr, value in original.items():
            self.assertEqual(cmds.getAttr(target+"."+attr), value)
        self.assertFalse(cmds.ls("AnimKey_WorldSolve*"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
