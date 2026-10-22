"""Automatic profile selection must never borrow another rig's snapshot."""

import tempfile
import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds
from AnimKey.buttons import mirror


class MirrorRigSnapshotTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        mirror.clear_mirror_cache()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(mirror, "get_user_data_folder", return_value=folder.name)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(mirror.clear_mirror_cache)

    def _pair(self, namespace):
        cmds.namespace(add=namespace)
        left = cmds.createNode("transform", name=namespace + ":arm_L_CTRL")
        right = cmds.createNode("transform", name=namespace + ":arm_R_CTRL")
        cmds.setAttr(left + ".translateX", 2)
        cmds.setAttr(right + ".translateX", -2)
        return left, right

    def _save_profile(self, controls):
        snapshot = mirror._build_basic_mirror_snapshot(controls)
        snapshot.pop("basic_snapshot", None)
        snapshot["profile_kind"] = "precise"
        snapshot["rig_scope"] = list(mirror._rig_scope(controls))
        name = mirror.get_rig_identifier(controls)
        mirror._write_snapshot_file(mirror.get_mirror_snapshot_file(name), snapshot)
        return snapshot

    def test_multiple_rigs_choose_their_own_saved_or_default_profile(self):
        left_a, right_a = self._pair("rigA")
        left_b, right_b = self._pair("rigB")
        self._save_profile([left_a, right_a])
        groups = mirror._prepared_snapshot_groups([left_a, left_b])
        self.assertEqual(len(groups), 2)
        basic = {item["info"]["label"]: item["is_basic_snapshot"] for item in groups}
        self.assertEqual(basic, {"rigA": False, "rigB": True})
        cmds.setAttr(left_a + ".translateX", 3)
        cmds.setAttr(left_b + ".translateX", 4)
        for item in groups:
            mirror._mirror_selected_pose_two_phase(item["info"]["controls"], item["snapshot"], item["sym_plane"])
        self.assertAlmostEqual(cmds.getAttr(right_a + ".translateX"), -3)
        self.assertAlmostEqual(cmds.getAttr(right_b + ".translateX"), -4)

    def test_misfiled_profile_cannot_be_used_by_another_rig(self):
        a = self._pair("rigA")
        b = self._pair("rigB")
        snapshot = self._save_profile(a)
        mirror._write_snapshot_file(mirror.get_mirror_snapshot_file(mirror.get_rig_identifier(b)), snapshot)
        _profile, basic = mirror._load_or_build_basic_snapshot([b[0]])
        self.assertTrue(basic)

    def test_nested_namespace_does_not_collide_with_underscore_namespace(self):
        cmds.namespace(add="cast")
        nested = self._pair("cast:hero")
        flat = self._pair("cast_hero")
        self.assertNotEqual(mirror.get_rig_identifier(nested), mirror.get_rig_identifier(flat))
        self._save_profile(nested)
        self.assertTrue(mirror._load_or_build_basic_snapshot(flat)[1])

    def test_legacy_quick_entry_point_uses_automatic_profile_selection(self):
        with mock.patch.object(mirror, "mirror", return_value=9) as automatic:
            self.assertEqual(mirror.quick_mirror(), 9)
            automatic.assert_called_once()

    def test_old_nested_namespace_profile_is_reused_only_with_matching_anchor(self):
        cmds.namespace(add="cast")
        pair = self._pair("cast:hero")
        snapshot = mirror._build_basic_mirror_snapshot(pair)
        snapshot.pop("basic_snapshot", None)
        snapshot["rig_name"] = "namespace_cast_hero"
        # Older profiles carry their namespace in the saved mirror-space anchor.
        snapshot["mirror_space"]["anchor_path"] = pair[0]
        mirror._write_snapshot_file(mirror.get_mirror_snapshot_file("namespace_cast_hero"), snapshot)
        self.assertFalse(mirror._load_or_build_basic_snapshot(pair)[1])
        other = self._pair("cast_hero")
        # A valid rig name alone cannot authorize a profile whose saved scope
        # points at another rig, even if both rigs share all control names.
        mirror._write_snapshot_file(mirror.get_mirror_snapshot_file("namespace_cast_hero"), snapshot)
        self.assertTrue(mirror._load_or_build_basic_snapshot(other)[1])


if __name__ == "__main__":
    unittest.main()
