import json
import os
import tempfile
import unittest
from unittest import mock
from AnimKey.mods.maya_compat import QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.buttons import copyAnimation as copy_anim
from AnimKey.core import animation_curve_transfer as transfer


class CopyStorageTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        copy_anim._cleanup_buffer()

    def test_async_save_owns_snapshot_when_clipboard_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "clip.json")
            payload = {"animation": {"control": {"keys": [1, 2, 3]}}}
            copy_anim._json_write_lock.acquire()
            try:
                worker = copy_anim._schedule_json_write(path, payload)
                payload["animation"].clear()
            finally:
                copy_anim._json_write_lock.release()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["animation"]["control"]["keys"], [1, 2, 3])

    def make_layered_clip(self):
        control = cmds.createNode("transform", name="Source:ctrl") if cmds.namespace(exists="Source") else None
        if control is None:
            cmds.namespace(add="Source")
            control = cmds.createNode("transform", name="Source:ctrl")
        for t in (1, 5, 10):
            cmds.setKeyframe(control, attribute="translateX", time=t, value=t)
        parent = cmds.animLayer("GroupLayer")
        layer = cmds.animLayer("Action", parent=parent)
        cmds.animLayer(layer, edit=True, attribute=control + ".translateX")
        for t in (1, 5, 10):
            cmds.setKeyframe(control, attribute="translateX", animLayer=layer,
                             time=t, value=2*t, noResolve=True)
            cmds.setKeyframe(layer, attribute="weight", time=t, value=t/10.)
        cmds.select(control)
        return control, layer

    def test_weight_animation_and_layer_parent_survive_shifted_paste(self):
        control, layer = self.make_layered_clip()
        payload, _ = copy_anim.collect_animation_for_controls([control], all_layers=True)
        metadata = json.loads(json.dumps(copy_anim._anim_layer_metadata))
        self.assertEqual(metadata[layer]["parent"], "GroupLayer")
        self.assertIn("weight_curve", metadata[layer])
        cmds.file(new=True, force=True)
        target = cmds.createNode("transform", name="target")
        copy_anim._anim_buffer = payload
        copy_anim._anim_layer_metadata = metadata
        copied, skipped = copy_anim._paste_clip_to_targets({next(iter(payload)): target}, time_offset=20.)
        self.assertEqual((copied, skipped), (2, 0))
        self.assertEqual(cmds.animLayer("Action", query=True, parent=True), "GroupLayer")
        self.assertEqual(cmds.keyframe("Action.weight", query=True, timeChange=True), [21., 25., 30.])
        self.assertEqual(cmds.keyframe("Action.weight", query=True, valueChange=True), [.1, .5, 1.])

    def test_library_save_contains_every_layer_and_is_readable_with_bom(self):
        control, layer = self.make_layered_clip()
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(copy_anim, "get_animation_backup_folder", return_value=directory):
                copy_anim._do_save_to_library("test", 1, 10)
            path = os.path.join(directory, "test.animkey_anim")
            meta, payload = copy_anim.load_animation_file(path)
            layers = dict(copy_anim._iter_curve_payloads(next(iter(payload.values()))["translateX"]))
            self.assertEqual(set(layers), {"BaseAnimation", layer})
            self.assertIn(layer, meta["layers"])
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
            with open(path, "w", encoding="utf-8-sig") as handle:
                handle.write(content)
            self.assertEqual(copy_anim.load_animation_file(path), (meta, payload))


if __name__ == "__main__":
    unittest.main()
