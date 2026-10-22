"""Numerical mirror calibration must respect paired local-space handedness."""
import unittest
import json
from pathlib import Path
import tempfile
from unittest import mock
import shutil

import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import mirror


class MirrorCalibrationTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        mirror.clear_mirror_cache()

    def pair(self, negative_scale=False, order=0, name="FKIndexFinger1"):
        left_parent = cmds.createNode("transform", name="leftSpace")
        right_parent = cmds.createNode("transform", name="rightSpace")
        cmds.setAttr(left_parent + ".translateX", 3.0)
        cmds.setAttr(left_parent + ".rotate", 13.0, 42.0, 32.0)
        plane = mirror.SymmetryPlane()
        reflected = mirror._calibration_reflected_matrix(mirror._mat4_from_maya(left_parent), plane)
        values = reflected.as_list()
        if not negative_scale:
            for row in range(3):
                for column in range(3):
                    values[row * 4 + column] *= -1.0
        cmds.xform(right_parent, worldSpace=True, matrix=values)
        left = cmds.circle(name=name + "_L", constructionHistory=False)[0]
        right = cmds.circle(name=name + "_R", constructionHistory=False)[0]
        left = cmds.parent(left, left_parent, relative=True)[0]
        right = cmds.parent(right, right_parent, relative=True)[0]
        for node in (left, right):
            cmds.setAttr(node + ".rotateOrder", order)
        return cmds.ls(left, right, long=True), plane

    def snapshot(self, controls):
        return mirror._create_mirror_snapshot(controls, show_message=False,
            allow_finger_calibration=True, persist=False, return_snapshot=True,
            print_report=False)

    def test_finger_calibration_keeps_rotations_in_all_euler_orders(self):
        for order in range(6):
            with self.subTest(order=order):
                cmds.file(new=True, force=True)
                controls, _ = self.pair(order=order)
                snapshot = self.snapshot(controls)
                mapping = mirror._snap_ctrl_data(controls[0], snapshot)["attr_map"]
                for prefix, sign in (("rotate", 1.0), ("translate", -1.0)):
                    for axis in "XYZ":
                        attr = prefix + axis
                        self.assertEqual(mapping[attr]["target"], attr)
                        self.assertEqual(mapping[attr]["mult"], sign)

    def test_negative_scale_parent_keeps_the_same_local_channels(self):
        controls, _ = self.pair(negative_scale=True)
        mapping = mirror._snap_ctrl_data(controls[0], self.snapshot(controls))["attr_map"]
        for prefix in ("rotate", "translate"):
            for axis in "XYZ":
                attr = prefix + axis
                self.assertEqual(mapping[attr]["target"], attr)
                self.assertEqual(mapping[attr]["mult"], 1.0)

    def test_bend_controls_are_calibrated_instead_of_forced_to_copy_translation(self):
        controls, _ = self.pair(name="BendElbow")
        mapping = mirror._snap_ctrl_data(controls[0], self.snapshot(controls))["attr_map"]
        for axis in "XYZ":
            self.assertEqual(mapping["translate" + axis]["mult"], -1.0)

    def test_pose_swap_is_two_phase_and_involutive(self):
        controls, plane = self.pair()
        snapshot = self.snapshot(controls)
        poses = ((25.0, -17.0, 65.0), (-8.0, 38.0, 12.0))
        for node, pose in zip(controls, poses):
            cmds.setAttr(node + ".rotate", *pose)
        mirror._mirror_selected_pose_two_phase(controls, snapshot, plane)
        for node, expected in zip(controls, reversed(poses)):
            for axis, value in zip("XYZ", expected):
                self.assertAlmostEqual(cmds.getAttr(node + ".rotate" + axis), value, places=6)
        mirror._mirror_selected_pose_two_phase(controls, snapshot, plane)
        for node, expected in zip(controls, poses):
            for axis, value in zip("XYZ", expected):
                self.assertAlmostEqual(cmds.getAttr(node + ".rotate" + axis), value, places=6)

    def test_ik_channels_are_not_overridden_by_world_matrix_solving(self):
        controls, plane = self.pair(name="IKArm")
        snapshot = self.snapshot(controls)
        self.assertFalse(mirror._can_apply_world_mirror(*controls, snapshot=snapshot))
        cmds.setAttr(controls[0] + ".rotate", 725.0, -17.0, 65.0)
        cmds.setAttr(controls[0] + ".scaleX", 1.25)
        mirror._mirror_selected_pose_two_phase([controls[0]], snapshot, plane)
        self.assertAlmostEqual(cmds.getAttr(controls[1] + ".rotateX"), 725.0)
        self.assertAlmostEqual(cmds.getAttr(controls[1] + ".scaleX"), 1.25)

    def test_custom_channels_do_not_add_asymmetric_default_offsets(self):
        controls, plane = self.pair()
        cmds.addAttr(controls[0], longName="curl", attributeType="double", defaultValue=2.0, keyable=True)
        cmds.addAttr(controls[1], longName="curl", attributeType="double", defaultValue=3.0, keyable=True)
        snapshot = self.snapshot(controls)
        cmds.setAttr(controls[0] + ".curl", 4.0)
        mirror._mirror_selected_pose_two_phase([controls[0]], snapshot, plane)
        self.assertAlmostEqual(cmds.getAttr(controls[1] + ".curl"), 4.0)

    def test_indirect_custom_rotation_is_measured_on_driven_nodes(self):
        controls, plane = self.pair()
        for node, name, x in zip(controls, ("driven_L", "driven_R"), (3.0, -3.0)):
            cmds.addAttr(node, longName="poleTwist", attributeType="double", keyable=True)
            driven = cmds.createNode("joint", name=name)
            cmds.setAttr(driven + ".translateX", x)
            mult = cmds.createNode("multDoubleLinear")
            cmds.setAttr(mult + ".input2", 1.0)
            cmds.connectAttr(node + ".poleTwist", mult + ".input1")
            cmds.connectAttr(mult + ".output", driven + ".rotateZ")
        snapshot = self.snapshot(controls)
        mapping = mirror._snap_ctrl_data(controls[0], snapshot)["attr_map"]
        self.assertEqual(mapping["poleTwist"]["mult"], -1.0)
        cmds.setAttr(controls[0] + ".poleTwist", 28.0)
        mirror._mirror_selected_pose_two_phase([controls[0]], snapshot, plane)
        self.assertAlmostEqual(cmds.getAttr(controls[1] + ".poleTwist"), -28.0)

    def test_old_calibration_is_not_reused(self):
        controls, _ = self.pair()
        snapshot = self.snapshot(controls)
        self.assertTrue(mirror._snapshot_covers_controls(snapshot, controls))
        snapshot.pop("calibration_version")
        self.assertFalse(mirror._snapshot_covers_controls(snapshot, controls))

    def saved_calibration(self, controls, signs, namespace=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "mirror_settings_snapshot.anb"
        namespace = mirror._namespace(controls[0]) if namespace is None else namespace
        profile = {node + ".swivel": sign for node, sign in zip(controls, signs)}
        path.write_text(json.dumps({namespace: profile}), encoding="utf-8")
        return str(path)

    def swivel_pair(self):
        controls, plane = self.pair(name="IKArm")
        for node in controls:
            cmds.addAttr(node, longName="swivel", attributeType="double", keyable=True)
        return controls, plane

    def test_saved_user_calibration_is_copied_to_both_sides_and_is_independent(self):
        controls, plane = self.swivel_pair()
        path = self.saved_calibration(controls, (-1.0, -1.0))
        snapshot = mirror._create_mirror_snapshot(controls, show_message=False,
            allow_finger_calibration=True, persist=False, return_snapshot=True,
            print_report=False, calibration_file=path)
        self.assertEqual(snapshot["saved_calibration"]["channels"], 2)
        Path(path).unlink()
        cmds.setAttr(controls[0] + ".swivel", 31.0)
        cmds.setAttr(controls[1] + ".swivel", -17.0)
        mirror._mirror_selected_pose_two_phase(controls, snapshot, plane)
        self.assertEqual(cmds.getAttr(controls[0] + ".swivel"), 17.0)
        self.assertEqual(cmds.getAttr(controls[1] + ".swivel"), -31.0)
        mirror._mirror_selected_pose_two_phase(controls, snapshot, plane)
        self.assertEqual(cmds.getAttr(controls[0] + ".swivel"), 31.0)
        self.assertEqual(cmds.getAttr(controls[1] + ".swivel"), -17.0)

    def test_saved_user_calibration_rejects_other_namespace(self):
        controls, _ = self.swivel_pair()
        snapshot = self.snapshot(controls)
        path = self.saved_calibration(controls, (-1.0, -1.0), namespace="otherRig")
        self.assertEqual(mirror._apply_saved_channel_calibration(snapshot, controls, path), 0)

    def test_saved_user_calibration_rejects_other_hierarchy(self):
        controls, _ = self.swivel_pair()
        snapshot = self.snapshot(controls)
        wrong_controls = ["|differentRoot" + node for node in controls]
        path = self.saved_calibration(wrong_controls, (-1.0, -1.0))
        self.assertEqual(mirror._apply_saved_channel_calibration(snapshot, controls, path), 0)

    def test_saved_user_calibration_rejects_inconsistent_pair_signs(self):
        controls, _ = self.swivel_pair()
        snapshot = self.snapshot(controls)
        path = self.saved_calibration(controls, (1.0, -1.0))
        self.assertEqual(mirror._apply_saved_channel_calibration(snapshot, controls, path), 0)

    def test_saved_user_calibration_accepts_unique_parent_control_paths(self):
        controls, _ = self.swivel_pair()
        snapshot = self.snapshot(controls)
        path = self.saved_calibration(controls, (-1.0, -1.0))
        namespace = mirror._namespace(controls[0])
        partial = {"|".join(mirror._namespace_free_parts(node)[-2:]) + ".swivel": -1
                   for node in controls}
        Path(path).write_text(json.dumps({namespace: partial}), encoding="utf-8")
        self.assertEqual(mirror._apply_saved_channel_calibration(snapshot, controls, path), 2)

    def test_saved_user_calibration_rejects_ambiguous_parent_control_paths(self):
        controls, _ = self.swivel_pair()
        original_parents = [cmds.listRelatives(node, parent=True, fullPath=True)[0] for node in controls]
        first_root = cmds.createNode("transform", name="rigA")
        second_root = cmds.createNode("transform", name="rigB")
        # Use two copies with identical immediate parent/control names.
        for parent in original_parents:
            duplicate = cmds.duplicate(parent)[0]
            cmds.parent(duplicate, second_root)
            cmds.rename(second_root + "|" + duplicate, parent.rsplit("|", 1)[-1])
            cmds.parent(parent, first_root)
        candidates = [node for node in cmds.ls(type="transform", long=True)
                      if node.rsplit("|", 1)[-1] in ("IKArm_L", "IKArm_R")]
        snapshot = self.snapshot(candidates)
        path = self.saved_calibration(controls, (-1.0, -1.0))
        self.assertEqual(mirror._apply_saved_channel_calibration(snapshot, candidates, path), 0)

    def test_saved_user_calibration_rejects_non_sign_values_and_bad_json(self):
        controls, _ = self.swivel_pair()
        snapshot = self.snapshot(controls)
        for signs in ((True, True), (float("nan"), float("nan")), (2.0, 2.0)):
            path = self.saved_calibration(controls, signs)
            self.assertEqual(mirror._apply_saved_channel_calibration(snapshot, controls, path), 0)
        Path(path).write_text("not json", encoding="utf-8")
        self.assertEqual(mirror._apply_saved_channel_calibration(snapshot, controls, path), 0)

    def test_saved_user_calibration_is_used_for_animation(self):
        controls, plane = self.swivel_pair()
        snapshot = self.snapshot(controls)
        path = self.saved_calibration(controls, (-1.0, -1.0))
        mirror._apply_saved_channel_calibration(snapshot, controls, path)
        for frame, value in ((1, 17.0), (11, 29.0)):
            cmds.setKeyframe(controls[0], attribute="swivel", time=frame, value=value)
        jobs = mirror._animation_mirror_channel_jobs([controls[0]], snapshot, plane)
        self.assertGreater(mirror._transfer_animation_jobs(jobs, (1, 11)), 0)
        curve = mirror.curve_transfer.resolve_anim_curve(controls[1] + ".swivel")
        self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True), [-17.0, -29.0])

    def animation_roundtrip(self, layer_kind=None):
        controls, plane = self.pair()
        snapshot = self.snapshot(controls)
        layer = None
        if layer_kind:
            layer = cmds.animLayer("MirrorTestLayer", override=(layer_kind == "override"))
            cmds.select(controls, replace=True)
            cmds.animLayer(layer, edit=True, addSelectedObjects=True)
            for other in cmds.ls(type="animLayer"):
                cmds.animLayer(other, edit=True, selected=(other == layer), preferred=(other == layer))
            self.assertEqual(mirror.curve_transfer.active_animation_layer(), layer)
        for frame, value in ((1, 725.0), (11, 765.0)):
            kwargs = {"animLayer": layer} if layer else {}
            cmds.setKeyframe(controls[0], attribute="rotateX", time=frame, value=value, **kwargs)
        jobs = mirror._animation_mirror_channel_jobs([controls[0]], snapshot, plane)
        self.assertTrue(any(job["source_attr"] == "rotateX" for job in jobs))
        self.assertGreater(mirror._transfer_animation_jobs(jobs, (1, 11)), 0)
        curve = mirror.curve_transfer.resolve_anim_curve(controls[1] + ".rotateX", layer_name=layer)
        self.assertTrue(curve)
        self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), [1.0, 11.0])
        self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True), [725.0, 765.0])

    def test_animation_uses_the_same_calibration_on_base_animation(self):
        self.animation_roundtrip()

    def test_animation_uses_the_same_calibration_on_additive_layer(self):
        self.animation_roundtrip("additive")

    def test_animation_uses_the_same_calibration_on_override_layer(self):
        self.animation_roundtrip("override")

    def test_snapshot_with_animation_layer_keeps_axis_calibration(self):
        controls, _ = self.pair()
        layer = cmds.animLayer("ExistingAnimationLayer")
        cmds.select(controls, replace=True)
        cmds.animLayer(layer, edit=True, addSelectedObjects=True)
        for node in controls:
            cmds.setKeyframe(node, attribute="rotateZ", time=1, value=35, animLayer=layer)
        snapshot = mirror._create_default_pose_mirror_snapshot(controls,
            show_message=False, expand_controls=False, persist=False,
            return_snapshot=True, print_report=False)
        mapping = mirror._snap_ctrl_data(controls[0], snapshot)["attr_map"]
        for prefix, sign in (("rotate", 1.0), ("translate", -1.0)):
            for axis in "XYZ":
                self.assertEqual(mapping[prefix + axis]["mult"], sign)

    def test_pose_on_animation_layer_applies_evaluated_channels(self):
        controls, plane = self.pair()
        snapshot = self.snapshot(controls)
        layer = cmds.animLayer("PoseAnimationLayer")
        cmds.select(controls, replace=True)
        cmds.animLayer(layer, edit=True, addSelectedObjects=True)
        for other in cmds.ls(type="animLayer"):
            cmds.animLayer(other, edit=True, selected=(other == layer), preferred=(other == layer))
        cmds.setKeyframe(controls[0], attribute="rotateZ", time=1, value=35, animLayer=layer)
        cmds.setKeyframe(controls[1], attribute="rotateZ", time=1, value=-17, animLayer=layer)
        cmds.currentTime(1)
        changed, _ = mirror._mirror_selected_pose_two_phase([controls[0]], snapshot, plane, ["rotateZ"])
        self.assertGreater(changed, 0)
        self.assertAlmostEqual(cmds.getAttr(controls[1] + ".rotateZ"), cmds.getAttr(controls[0] + ".rotateZ"))

    def test_animation_undo_and_redo_leave_no_temporary_curves(self):
        controls, plane = self.pair()
        snapshot = self.snapshot(controls)
        for frame, value in ((1, 17.0), (11, 29.0)):
            cmds.setKeyframe(controls[0], attribute="rotateZ", time=frame, value=value)
        before = set(cmds.ls(type="animCurve"))
        jobs = mirror._animation_mirror_channel_jobs([controls[0]], snapshot, plane, ["rotateZ"])
        cmds.undoInfo(openChunk=True, chunkName="Mirror undo regression")
        try:
            self.assertGreater(mirror._transfer_animation_jobs(jobs, (1, 11)), 0)
        finally:
            cmds.undoInfo(closeChunk=True)
        cmds.undo()
        self.assertEqual(set(cmds.ls(type="animCurve")), before)
        cmds.redo()
        curve = mirror.curve_transfer.resolve_anim_curve(controls[1] + ".rotateZ")
        values = cmds.keyframe(curve, query=True, valueChange=True)
        self.assertEqual(len(values), 2)
        for actual, expected in zip(values, (17.0, 29.0)):
            self.assertAlmostEqual(actual, expected, places=8)
        self.assertFalse(cmds.ls("ANIMKEY_TEMP_CURVE*"))

    def test_visibility_only_helper_does_not_discard_precise_profile(self):
        controls, _ = self.pair()
        helper = cmds.createNode("transform", name="visibilityHelper")
        for attr in mirror.TRANSFORM_ATTRS:
            cmds.setAttr(helper + "." + attr, keyable=False)
        controls.append(helper)
        snapshot = self.snapshot(controls)
        self.assertFalse(mirror._snap_ctrl_data(helper, snapshot)["attr_map"])
        self.assertTrue(mirror._snapshot_covers_controls(snapshot, controls))

    def referenced_pair(self, different_asset=False):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        controls, _ = self.pair()
        cmds.group("leftSpace", "rightSpace", name="CharacterRig")
        path = str(Path(folder.name) / "character.ma")
        cmds.file(rename=path)
        cmds.file(save=True, force=True, type="mayaAscii")
        other_path = path
        if different_asset:
            other_path = str(Path(folder.name) / "differentCharacter.ma")
            shutil.copyfile(path, other_path)
        cmds.file(new=True, force=True)
        cmds.file(path, reference=True, namespace="hero")
        cmds.file(other_path, reference=True, namespace="hero1")
        source = [cmds.ls("hero:" + mirror._strip_namespace(node), long=True)[0] for node in controls]
        target = [cmds.ls("hero1:" + mirror._strip_namespace(node), long=True)[0] for node in controls]
        patch = mock.patch.object(mirror, "get_user_data_folder", return_value=str(Path(folder.name) / "data"))
        patch.start()
        self.addCleanup(patch.stop)
        snapshot = self.snapshot(source)
        mirror._write_snapshot_file(mirror.get_mirror_snapshot_file(mirror.get_rig_identifier(source)), snapshot)
        return source, target, snapshot

    def test_same_referenced_asset_reuses_precise_profile_in_new_namespace(self):
        source, target, snapshot = self.referenced_pair()
        profile, quick = mirror._load_or_build_basic_snapshot(target)
        self.assertFalse(quick)
        self.assertEqual(profile["shared_from"], mirror.get_rig_identifier(source))
        self.assertTrue(mirror._snapshot_covers_controls(profile, target))
        self.assertIn("hero1:", profile["mirror_space"]["anchor_path"])
        self.assertEqual(mirror._snap_opposite(target[0], profile), target[1])

    def test_different_referenced_assets_do_not_share_profiles(self):
        source, target, snapshot = self.referenced_pair(different_asset=True)
        profile, quick = mirror._load_or_build_basic_snapshot(target)
        self.assertTrue(quick)
        self.assertNotIn("shared_from", profile)
        self.assertFalse(mirror._snapshot_covers_controls(snapshot, target))

    def test_selecting_rig_root_expands_controls_but_not_fit_guides_or_panel_frames(self):
        controls, _ = self.pair()
        root = cmds.group("leftSpace", "rightSpace", name="CharacterRig")
        controls = [cmds.ls(mirror._strip_namespace(node), long=True)[0] for node in controls]
        guide_root = cmds.createNode("transform", name="FaceFitSkeleton", parent=root)
        guide = cmds.circle(name="FitEyeBall", constructionHistory=False)[0]
        cmds.parent(guide, guide_root)
        frame = cmds.circle(name="ctrlBoxPhonemes_M", constructionHistory=False)[0]
        cmds.parent(frame, root)
        deformation = cmds.createNode("transform", name="FaceDeformationSystem", parent=root)
        internal_curve = cmds.circle(name="squashIKECurve", constructionHistory=False)[0]
        cmds.parent(internal_curve, deformation)
        prepared = mirror._prepared_snapshot_groups([root])
        actual = {node for group in prepared for node in group["info"]["controls"]}
        self.assertEqual(actual, set(controls))
        one_control = mirror._prepared_snapshot_groups([controls[0]])
        self.assertEqual(one_control[0]["info"]["controls"], [controls[0]])
        explicit = mirror._prepared_snapshot_groups(controls + [
            cmds.ls("FitEyeBall", long=True)[0], cmds.ls("squashIKECurve", long=True)[0]])
        self.assertEqual({node for group in explicit for node in group["info"]["controls"]}, set(controls))

    def test_batch_mirror_preserves_weighted_curve_shape_when_negating(self):
        controls, plane = self.pair()
        snapshot = self.snapshot(controls)
        for frame, value in ((1, 2.0), (11, 7.0)):
            cmds.setKeyframe(controls[0], attribute="translateX", time=frame, value=value)
        source = mirror.curve_transfer.resolve_anim_curve(controls[0] + ".translateX")
        cmds.keyTangent(source, edit=True, weightedTangents=True)
        cmds.keyTangent(source, edit=True, lock=False, inTangentType="fixed", outTangentType="fixed",
                        inAngle=15, outAngle=27, inWeight=1.25, outWeight=2.0)
        frames = (1, 2, 5.5, 10, 11)
        expected = [-cmds.keyframe(source, query=True, eval=True, time=(frame, frame))[0] for frame in frames]
        jobs = mirror._animation_mirror_channel_jobs([controls[0]], snapshot, plane, ["translateX"])
        self.assertGreater(mirror._transfer_animation_jobs(jobs, (1, 11)), 0)
        target = mirror.curve_transfer.resolve_anim_curve(controls[1] + ".translateX")
        actual = [cmds.keyframe(target, query=True, eval=True, time=(frame, frame))[0] for frame in frames]
        for value, reference in zip(actual, expected):
            self.assertAlmostEqual(value, reference, places=7)

    def test_empty_layer_key_probe_does_not_resolve_each_control_attribute(self):
        controls, plane = self.pair()
        snapshot = self.snapshot(controls)
        layer = cmds.animLayer("EmptyLayer")
        cmds.select(controls)
        cmds.animLayer(layer, edit=True, addSelectedObjects=True)
        for other in cmds.ls(type="animLayer"):
            cmds.animLayer(other, edit=True, selected=(other == layer), preferred=(other == layer))
        jobs = mirror._animation_mirror_channel_jobs(controls, snapshot, plane)
        with mock.patch.object(mirror.curve_transfer, "resolve_anim_curve", side_effect=AssertionError("Per-attribute scan")):
            self.assertFalse(mirror._animation_jobs_have_keys(jobs, (1, 11)))

    def test_batch_mirror_is_not_limited_by_unrelated_selected_graph_keys(self):
        controls, plane = self.pair()
        snapshot = self.snapshot(controls)
        cmds.setKeyframe(controls[0], attribute="translateX", time=1, value=17)
        other = cmds.createNode("transform", name="unrelated")
        cmds.setKeyframe(other, attribute="translateX", time=1, value=5)
        curve = mirror.curve_transfer.resolve_anim_curve(other + ".translateX")
        cmds.selectKey(curve, replace=True)
        jobs = mirror._animation_mirror_channel_jobs([controls[0]], snapshot, plane, ["translateX"])
        self.assertGreater(mirror._transfer_animation_jobs(jobs, (1, 11)), 0)
        target = mirror.curve_transfer.resolve_anim_curve(controls[1] + ".translateX")
        self.assertAlmostEqual(cmds.keyframe(target, query=True, eval=True, time=(1, 1))[0], -17)


if __name__ == "__main__":
    unittest.main()
