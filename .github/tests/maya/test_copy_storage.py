import json
import os
import tempfile
import threading
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
from AnimKey.core.executionGuard import animkey_execution


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

    def test_save_and_load_animation_file_preserve_layers_and_clipboard(self):
        control, layer = self.make_layered_clip()
        copy_anim._anim_buffer = {"previous_clipboard": {}}
        copy_anim._anim_layer_metadata = {"previous_layer": {"weight": 0.25}}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "chosen_animation")
            with animkey_execution("Storage test"), \
                    mock.patch.object(cmds, "fileDialog2", return_value=[path]), \
                    mock.patch.object(copy_anim, "get_selected_time_range", return_value=(1, 10)):
                saved = copy_anim.save_animation()
            self.assertEqual(saved, path + ".animkey_anim")
            self.assertEqual(copy_anim._anim_buffer, {"previous_clipboard": {}})
            self.assertEqual(copy_anim._anim_layer_metadata, {"previous_layer": {"weight": 0.25}})
            meta, payload = copy_anim.load_animation_file(saved)
            self.assertEqual(meta["type"], "animation")
            self.assertIn(layer, meta["layers"])

            cmds.file(new=True, force=True)
            cmds.namespace(add="Target")
            target = cmds.createNode("transform", name="Target:ctrl")
            cmds.select(target)
            with animkey_execution("Storage test"), \
                    mock.patch.object(cmds, "fileDialog2", return_value=[saved]), \
                    mock.patch.object(copy_anim, "get_user_data_folder", return_value=directory):
                self.assertEqual(copy_anim.load_animation(), saved)
                for worker in threading.enumerate():
                    if worker.name == "AnimKeyJsonWriter":
                        worker.join(5)
            for layer_name, expected in (("BaseAnimation", [1., 5., 10.]),
                                         (layer, [2., 10., 20.])):
                curve = transfer.resolve_anim_curve(target + ".translateX", layer_name=layer_name)
                self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True), expected)

    def test_save_pose_and_load_on_active_layer_at_playhead(self):
        source, layer = self.make_layered_clip()
        cmds.currentTime(5)
        expected = cmds.getAttr(source + ".translateX")
        previous = {"meta": {"name": "previous_pose"}, "animation": {}}
        copy_anim._pose_buffer = previous
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "chosen_pose.animkey_pose")
            with animkey_execution("Storage test"), \
                    mock.patch.object(cmds, "fileDialog2", return_value=[path]):
                self.assertEqual(copy_anim.save_pose(), path)
            self.assertIs(copy_anim._pose_buffer, previous)
            self.assertEqual(copy_anim._read_clip_file(path, "pose")["meta"]["source_frame"], 5)

            cmds.file(new=True, force=True)
            cmds.namespace(add="Target")
            target = cmds.createNode("transform", name="Target:ctrl")
            cmds.setKeyframe(target, attribute="translateX", time=21, value=100)
            destination = cmds.animLayer("Destination")
            cmds.animLayer(destination, edit=True, attribute=target + ".translateX")
            for name in cmds.ls(type="animLayer"):
                cmds.animLayer(name, edit=True, selected=False, preferred=False)
            cmds.animLayer(destination, edit=True, selected=True, preferred=True)
            cmds.currentTime(21)
            cmds.select(target)
            with animkey_execution("Storage test"), \
                    mock.patch.object(cmds, "fileDialog2", return_value=[path]), \
                    mock.patch.object(copy_anim, "get_user_data_folder", return_value=directory):
                self.assertEqual(copy_anim.load_pose(), path)
                for worker in threading.enumerate():
                    if worker.name == "AnimKeyJsonWriter":
                        worker.join(5)
            self.assertAlmostEqual(cmds.getAttr(target + ".translateX"), expected)
            curve = transfer.resolve_anim_curve(target + ".translateX", layer_name=destination)
            self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), [21.])

    def test_cancel_save_does_not_capture_or_write(self):
        self.make_layered_clip()
        with animkey_execution("Storage test"), \
                mock.patch.object(cmds, "fileDialog2", return_value=None), \
                mock.patch.object(copy_anim, "_capture_file_animation") as capture:
            self.assertIsNone(copy_anim.save_animation())
            capture.assert_not_called()

    def test_invalid_animation_file_does_not_replace_clipboard(self):
        previous = {"previous_clipboard": {}}
        copy_anim._anim_buffer = previous
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "invalid.animkey_anim")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"animation": {"ctrl": {"translateX": {
                    "keyframes": [1, 2], "values": [3],
                }}}}, handle)
            with animkey_execution("Storage test"), \
                    mock.patch.object(cmds, "fileDialog2", return_value=[path]):
                self.assertIsNone(copy_anim.load_animation())
            self.assertIs(copy_anim._anim_buffer, previous)


if __name__ == "__main__":
    unittest.main()
