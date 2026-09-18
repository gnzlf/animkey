import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.buttons import followCam
from AnimKey.core.executionGuard import animkey_execution


class FollowCamTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        followCam._original_camera = None
        followCam._original_panel = None
        followCam._last_model_panel = None

    def _create_camera_and_target(self):
        target = cmds.circle(name="Character_CTRL", normal=(0, 1, 0))[0]
        cmds.xform(target, worldSpace=True, translation=(3, 2, -4))
        camera = cmds.camera(name="shotCam")[0]
        cmds.xform(
            camera,
            worldSpace=True,
            translation=(12, 8, 18),
            rotation=(-12, 32, 0),
        )
        cmds.select(target, replace=True)
        return target, camera

    def test_translation_follow_moves_with_selected_control(self):
        target, camera = self._create_camera_and_target()
        with mock.patch.object(
            followCam, "_get_active_panel", return_value="modelPanelProbe"
        ), mock.patch.object(
            followCam, "_panel_camera", return_value=followCam._camera_transform(camera)
        ), mock.patch.object(cmds, "lookThru"):
            with animkey_execution("test"):
                follow = followCam.create_follow_cam(
                    translation=True, rotation=False
                )

        self.assertTrue(follow and cmds.objExists(follow))
        before_camera = cmds.xform(
            follow, query=True, worldSpace=True, translation=True
        )
        before_target = cmds.xform(
            target, query=True, worldSpace=True, translation=True
        )

        cmds.move(7, -1, 5, target, relative=True, worldSpace=True)
        cmds.dgdirty(allPlugs=True)

        after_camera = cmds.xform(
            follow, query=True, worldSpace=True, translation=True
        )
        after_target = cmds.xform(
            target, query=True, worldSpace=True, translation=True
        )
        camera_delta = [
            after_camera[index] - before_camera[index] for index in range(3)
        ]
        target_delta = [
            after_target[index] - before_target[index] for index in range(3)
        ]
        for actual, expected in zip(camera_delta, target_delta):
            self.assertAlmostEqual(actual, expected, places=5)

        root = followCam._find_follow_roots()[0]
        self.assertTrue(followCam._same_node(
            followCam._message_source(root, followCam.TARGET_ATTR), target
        ))

    def test_toolbar_focus_falls_back_to_active_view_camera(self):
        panel_cameras = {
            "modelPanel1": "top",
            "modelPanel4": "persp",
        }

        def fake_get_panel(**kwargs):
            if kwargs.get("withFocus") or kwargs.get("underPointer"):
                return "AnimKeyToolbar"
            if kwargs.get("visiblePanels"):
                return ["modelPanel1", "modelPanel4"]
            if kwargs.get("type") == "modelPanel":
                return ["modelPanel1", "modelPanel4"]
            if "typeOf" in kwargs:
                return (
                    "modelPanel"
                    if kwargs["typeOf"] in panel_cameras
                    else "scriptedPanel"
                )
            return None

        with mock.patch.object(cmds, "getPanel", side_effect=fake_get_panel), \
                mock.patch.object(
                    followCam,
                    "_active_3d_view_context",
                    return_value=(None, followCam._camera_transform("persp")),
                ), mock.patch.object(
                    followCam,
                    "_panel_camera",
                    side_effect=lambda panel: followCam._camera_transform(
                        panel_cameras[panel]
                    ),
                ):
            panel = followCam._get_active_panel()

        self.assertEqual(panel, "modelPanel4")


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
