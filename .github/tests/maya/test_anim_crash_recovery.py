import json
import os
import shutil
import tempfile
import time
import unittest

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

from AnimKey.mods.maya_compat import QtCore

_APP = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])

import maya.cmds as cmds
import maya.utils as maya_utils

from AnimKey.buttons import animCrash


class AnimCrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.folder = tempfile.mkdtemp(prefix="animkey_recovery_test_")
        self.original_scene_folder = animCrash.get_scene_folder
        self.original_idle_delay = animCrash.Config.IDLE_DELAY
        self.original_save_interval = animCrash.Config.SAVE_INTERVAL
        self.original_scene_snapshot_interval = (
            animCrash.Config.__dict__["scene_snapshot_interval"]
        )
        animCrash.get_scene_folder = lambda: self.folder
        animCrash.Config.IDLE_DELAY = 0.0
        animCrash.Config.SAVE_INTERVAL = 0.0
        animCrash.Config.scene_snapshot_interval = classmethod(
            lambda cls: 0.0
        )

    def tearDown(self):
        animCrash.RecoverySystem.stop()
        animCrash.get_scene_folder = self.original_scene_folder
        animCrash.Config.IDLE_DELAY = self.original_idle_delay
        animCrash.Config.SAVE_INTERVAL = self.original_save_interval
        animCrash.Config.scene_snapshot_interval = (
            self.original_scene_snapshot_interval
        )
        shutil.rmtree(self.folder, ignore_errors=True)

    def _wait_for_checkpoint(self, timeout=8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _APP.processEvents()
            maya_utils.processIdleEvents()
            animCrash.RecoverySystem._tick()
            files = [
                name for name in os.listdir(self.folder)
                if name.endswith(".json") and not name.endswith(".meta")
            ]
            if (
                files
                and not animCrash._async_checkpoint_write_in_progress
                and not animCrash.RecoverySystem._dirty
            ):
                return os.path.join(self.folder, files[0])
            time.sleep(0.005)
        return None

    def _wait_for_scene_snapshot(self, timeout=8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _APP.processEvents()
            maya_utils.processIdleEvents()
            animCrash.RecoverySystem._tick()
            files = [
                name for name in os.listdir(self.folder)
                if name.endswith("_auto_scene.mb")
            ]
            if files:
                return os.path.join(self.folder, files[0])
            time.sleep(0.005)
        return None

    def _wait_until_idle(self, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _APP.processEvents()
            maya_utils.processIdleEvents()
            animCrash.RecoverySystem._tick()
            if (
                animCrash._async_checkpoint_capture is None
                and not animCrash._async_checkpoint_write_in_progress
            ):
                return True
            time.sleep(0.005)
        return False

    def test_start_creates_initial_animation_checkpoint(self):
        node = cmds.createNode("transform", name="AnimCrashProbe")
        for frame, value in ((0, 0.0), (1, 4.25), (2, -1.5)):
            cmds.setKeyframe(
                node, attribute="translateX", time=frame, value=value
            )

        animCrash.RecoverySystem.start()
        path = self._wait_for_checkpoint()

        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path + ".meta"))
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        self.assertEqual(data["meta"]["objects"], 1)
        self.assertEqual(data["meta"]["keys"], 3)
        self.assertFalse(animCrash.RecoverySystem._dirty)
        self.assertFalse(animCrash._async_checkpoint_write_in_progress)

        # A modified Maya scene remains marked modified after an animation-only
        # checkpoint.  AnimCrash must not therefore write the same data on every
        # timer tick when no new key edit occurred.
        initial_files = sorted(os.listdir(self.folder))
        for _index in range(5):
            animCrash.RecoverySystem._tick()
            _APP.processEvents()
            maya_utils.processIdleEvents()
        self.assertEqual(initial_files, sorted(os.listdir(self.folder)))

    def test_scene_snapshot_saves_complete_scene_without_renaming_it(self):
        scene_path = os.path.join(self.folder, "source_scene.ma")
        node = cmds.createNode("transform", name="FullSceneSnapshotProbe")
        cmds.setAttr(node + ".translateX", 7.25)
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)

        cmds.setAttr(node + ".rotateY", -31.5)
        active_scene = cmds.file(query=True, sn=True)
        active_modified = cmds.file(query=True, modified=True)
        animCrash.RecoverySystem.start()
        recovery_serial = animCrash.RecoverySystem._change_serial

        snapshot_path = animCrash.save_scene_snapshot(
            auto=False, desc="Test complete scene"
        )

        self.assertIsNotNone(snapshot_path)
        self.assertTrue(snapshot_path.endswith("_manual_scene.mb"))
        self.assertTrue(os.path.exists(snapshot_path))
        self.assertTrue(os.path.exists(snapshot_path + ".meta"))
        self.assertEqual(cmds.file(query=True, sn=True), active_scene)
        self.assertEqual(cmds.file(query=True, modified=True), active_modified)
        self.assertEqual(
            animCrash.RecoverySystem._change_serial, recovery_serial
        )

        snapshots = animCrash.get_checkpoints(self.folder)
        saved_snapshot = next(
            item for item in snapshots if item["path"] == snapshot_path
        )
        self.assertEqual(saved_snapshot["kind"], "scene_snapshot")
        self.assertEqual(saved_snapshot["type"], "SCENE MANUAL")

        cmds.file(new=True, force=True)
        cmds.file(snapshot_path, open=True, force=True)
        self.assertTrue(cmds.objExists(node))
        self.assertAlmostEqual(
            cmds.getAttr(node + ".translateX"), 7.25, places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(node + ".rotateY"), -31.5, places=5
        )

    def test_first_auto_scene_snapshot_follows_the_json_checkpoint(self):
        node = cmds.createNode("transform", name="FirstAutoSceneProbe")
        cmds.setKeyframe(node, attribute="translateX", time=1, value=4.0)
        original_interval = animCrash.Config.__dict__["scene_snapshot_interval"]
        animCrash.Config.scene_snapshot_interval = classmethod(
            lambda cls: 300.0
        )
        try:
            animCrash.RecoverySystem.start()
            self.assertIsNotNone(self._wait_for_checkpoint())
            snapshot_path = self._wait_for_scene_snapshot()
            self.assertIsNotNone(snapshot_path)
            self.assertTrue(os.path.exists(snapshot_path + ".meta"))
            self.assertFalse(animCrash.RecoverySystem._scene_snapshot_dirty)
        finally:
            animCrash.Config.scene_snapshot_interval = original_interval

    def test_auto_scene_snapshot_uses_its_own_interval_after_idle(self):
        calls = []
        original_save = animCrash.save_scene_snapshot
        original_interval = animCrash.Config.__dict__["scene_snapshot_interval"]
        animCrash.save_scene_snapshot = lambda **kwargs: calls.append(kwargs) or "scene.mb"
        animCrash.Config.scene_snapshot_interval = classmethod(
            lambda cls: 60.0
        )
        try:
            animCrash.RecoverySystem._active = True
            animCrash.RecoverySystem._dirty = False
            animCrash.RecoverySystem._scene_snapshot_dirty = True
            animCrash.RecoverySystem._last_change_time = time.monotonic() - 10.0
            animCrash.RecoverySystem._last_scene_snapshot_time = time.monotonic()

            animCrash.RecoverySystem._tick()
            self.assertEqual(calls, [])

            animCrash.RecoverySystem._last_scene_snapshot_time -= 61.0
            animCrash.RecoverySystem._tick()
            self.assertEqual(calls, [{"auto": True}])
            self.assertFalse(animCrash.RecoverySystem._scene_snapshot_dirty)
        finally:
            animCrash.save_scene_snapshot = original_save
            animCrash.Config.scene_snapshot_interval = original_interval

    def test_v088_capture_keeps_base_and_animation_layer_keys(self):
        node = cmds.createNode("transform", name="LayerProbe")
        cmds.setKeyframe(node, attribute="translateX", time=1, value=1.0)
        cmds.setKeyframe(node, attribute="translateX", time=10, value=3.0)

        layer = cmds.animLayer("RecoveryLayer")
        cmds.select(node)
        cmds.animLayer(layer, edit=True, addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)
        cmds.setKeyframe(node, attribute="translateX", time=1, value=5.0)
        cmds.setKeyframe(node, attribute="translateX", time=10, value=9.0)

        data = animCrash.extract_animation(
            animCrash.get_animated_objects(force=True)
        )

        self.assertIsNotNone(data)
        self.assertEqual(data["meta"]["keys"], 4)
        captured_attributes = {
            attribute
            for object_data in data["animation"].values()
            for attribute in object_data
        }
        self.assertIn("inputA", captured_attributes)
        self.assertIn("inputB", captured_attributes)

        expected = {}
        for object_name, object_data in data["animation"].items():
            for attribute, keys in object_data.items():
                plug = object_name + "." + attribute
                expected[plug] = [
                    (float(key["t"]), float(key["v"])) for key in keys
                ]
                cmds.cutKey(plug, clear=True)

        success, failed, skipped = animCrash.apply_animation(data)
        self.assertEqual(success, len(data["animation"]))
        self.assertEqual(failed, 0)
        self.assertEqual(skipped, 0)
        for plug, expected_keys in expected.items():
            times = cmds.keyframe(plug, query=True, timeChange=True) or []
            values = cmds.keyframe(plug, query=True, valueChange=True) or []
            self.assertEqual(
                list(zip(map(float, times), map(float, values))),
                expected_keys,
            )

    def test_recovery_can_apply_only_one_rig_namespace(self):
        cmds.namespace(add="RigA")
        cmds.namespace(add="RigB")
        rig_a = cmds.createNode("transform", name="RigA:Ctrl")
        rig_b = cmds.createNode("transform", name="RigB:Ctrl")
        for node, values in (
            (rig_a, ((1, 1.0), (10, 2.0))),
            (rig_b, ((1, 10.0), (10, 20.0))),
        ):
            for frame, value in values:
                cmds.setKeyframe(
                    node, attribute="translateX", time=frame, value=value
                )

        data = animCrash.extract_animation(
            animCrash.get_animated_objects(force=True)
        )
        self.assertEqual(
            data["meta"]["namespaces"], {"RigA": 1, "RigB": 1}
        )

        for node in (rig_a, rig_b):
            cmds.cutKey(node, attribute="translateX", clear=True)
            cmds.setKeyframe(
                node, attribute="translateX", time=1, value=99.0
            )
            cmds.setKeyframe(
                node, attribute="translateX", time=10, value=99.0
            )

        success, failed, skipped = animCrash.apply_animation(
            data, namespace_filter="RigA"
        )

        self.assertEqual((success, failed, skipped), (1, 0, 1))
        self.assertEqual(
            list(map(float, cmds.keyframe(
                rig_a,
                attribute="translateX",
                query=True,
                valueChange=True,
            ) or [])),
            [1.0, 2.0],
        )
        self.assertEqual(
            list(map(float, cmds.keyframe(
                rig_b,
                attribute="translateX",
                query=True,
                valueChange=True,
            ) or [])),
            [99.0, 99.0],
        )

    def test_auto_checkpoint_waits_for_idle_and_minimum_interval(self):
        calls = []
        original_request = animCrash.request_checkpoint
        animCrash.request_checkpoint = lambda **kwargs: calls.append(kwargs)
        try:
            animCrash.RecoverySystem._active = True
            animCrash.RecoverySystem._dirty = True
            animCrash.RecoverySystem._last_change_time = time.monotonic()
            animCrash.RecoverySystem._last_checkpoint_time = 0.0
            animCrash.Config.IDLE_DELAY = 10.0
            animCrash.Config.SAVE_INTERVAL = 120.0

            animCrash.RecoverySystem._tick()
            self.assertEqual(calls, [])

            animCrash.RecoverySystem._last_change_time -= 11.0
            animCrash.RecoverySystem._last_checkpoint_time = time.monotonic()
            animCrash.RecoverySystem._tick()
            self.assertEqual(calls, [])

            animCrash.RecoverySystem._last_checkpoint_time = 0.0
            animCrash.RecoverySystem._tick()
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0]["auto"])
        finally:
            animCrash.request_checkpoint = original_request

    def test_recovery_monitor_is_single_shot_and_idle_when_clean(self):
        node = cmds.createNode("transform", name="IdleMonitorProbe")
        cmds.setKeyframe(node, attribute="translateX", time=1, value=1.0)
        animCrash.RecoverySystem.start()
        self.assertTrue(animCrash.RecoverySystem._timer.isSingleShot())
        self.assertIsNotNone(self._wait_for_checkpoint())
        animCrash.RecoverySystem._tick()
        self.assertFalse(animCrash.RecoverySystem._dirty)
        self.assertFalse(animCrash.RecoverySystem._timer.isActive())

    def test_dense_key_edits_arm_recovery_timer_only_once(self):
        class FakeTimer(object):
            def __init__(self):
                self.active = False
                self.start_calls = 0

            def isActive(self):
                return self.active

            def start(self, _milliseconds):
                self.active = True
                self.start_calls += 1

        original_timer = animCrash.RecoverySystem._timer
        original_active = animCrash.RecoverySystem._active
        timer = FakeTimer()
        try:
            animCrash.RecoverySystem._timer = timer
            animCrash.RecoverySystem._active = True
            for _index in range(500):
                animCrash.RecoverySystem._on_change()
            self.assertEqual(timer.start_calls, 1)
        finally:
            animCrash.RecoverySystem._timer = original_timer
            animCrash.RecoverySystem._active = original_active

    def test_auto_capture_is_discarded_when_animation_changes(self):
        cmds.namespace(add="ChangingRig")
        node = cmds.createNode("transform", name="ChangingRig:Ctrl")
        cmds.setKeyframe(node, attribute="translateX", time=1, value=1.0)
        cmds.setKeyframe(node, attribute="translateX", time=10, value=2.0)

        capture = animCrash._AsyncCheckpointCapture(auto=True)
        animCrash._async_checkpoint_capture = capture
        capture.start()
        animCrash.RecoverySystem._change_serial += 1
        animCrash.RecoverySystem._dirty = True
        capture._process_chunk()

        self.assertIsNone(animCrash._async_checkpoint_capture)
        self.assertFalse(capture.timer.isActive())
        self.assertEqual(
            [name for name in os.listdir(self.folder) if name.endswith(".json")],
            [],
        )

    def test_opening_an_unmodified_scene_starts_a_new_checkpoint(self):
        scene_path = os.path.join(self.folder, "opened_shot.ma")
        node = cmds.createNode("transform", name="OpenedShotProbe")
        cmds.setKeyframe(node, attribute="rotateY", time=1, value=12.0)
        cmds.setKeyframe(node, attribute="rotateY", time=8, value=-6.0)
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)

        cmds.file(new=True, force=True)
        animCrash.RecoverySystem.start()
        self.assertTrue(self._wait_until_idle())
        self.assertEqual(
            [name for name in os.listdir(self.folder) if name.endswith("_auto.json")],
            [],
        )

        cmds.file(scene_path, open=True, force=True, prompt=False)
        self.assertFalse(cmds.file(query=True, modified=True))
        path = self._wait_for_checkpoint()

        self.assertIsNotNone(path)
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        self.assertEqual(data["meta"]["objects"], 1)
        self.assertEqual(data["meta"]["keys"], 2)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
