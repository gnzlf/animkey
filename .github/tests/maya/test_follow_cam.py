import unittest
from unittest import mock
from types import SimpleNamespace

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

    def _panel_query(self,focus='AnimKeyToolbar',pointer='AnimKeyToolbar',visible=None):
        visible = ['leftView','rightView'] if visible is None else visible
        def query(**kwargs):
            if kwargs.get('withFocus'):
                return focus
            if kwargs.get('underPointer'):
                return pointer
            if kwargs.get('visiblePanels'):
                return visible
            if kwargs.get('type') == 'modelPanel':
                return ['hiddenView','leftView','rightView']
            if 'typeOf' in kwargs:
                return 'modelPanel' if kwargs['typeOf'] in ('hiddenView','leftView','rightView') else 'scriptedPanel'
            return None
        return query

    def test_active_view_matches_the_viewport_widget_when_cameras_are_shared(self):
        camera = followCam._camera_transform('persp')
        path = SimpleNamespace(isValid=lambda:True,fullPathName=lambda:camera)
        def view(pointer):
            return SimpleNamespace(widget=lambda:pointer,getCamera=lambda:path)
        views = {'hiddenView':view(1),'leftView':view(2),'rightView':view(3)}
        api = SimpleNamespace(active3dView=lambda:views['rightView'],
                              getM3dViewFromModelPanel=lambda p:views[p])
        with mock.patch.object(cmds,'getPanel',side_effect=self._panel_query()), \
                mock.patch.object(followCam.omui2,'M3dView',api), \
                mock.patch.object(followCam.omui1.MQtUtil,'fullName',return_value='viewPanes|rightView|rightView|'):
            panel,source = followCam._active_3d_view_context()
        self.assertEqual(panel,'rightView')
        self.assertEqual(source,camera)

    def test_shared_camera_fallback_retains_the_last_visible_panel(self):
        followCam._last_model_panel = 'rightView'
        camera = followCam._camera_transform('persp')
        with mock.patch.object(cmds,'getPanel',side_effect=self._panel_query()), \
                mock.patch.object(followCam,'_active_3d_view_context',return_value=(None,camera)), \
                mock.patch.object(followCam,'_panel_camera',return_value=camera):
            self.assertEqual(followCam._get_active_panel(),'rightView')

    def test_hidden_focused_panel_is_not_used_after_a_layout_change(self):
        with mock.patch.object(cmds,'getPanel',side_effect=self._panel_query(focus='hiddenView')), \
                mock.patch.object(followCam,'_active_3d_view_context',return_value=('rightView',None)):
            self.assertEqual(followCam._get_active_panel(),'rightView')

    def test_hotkey_prefers_the_viewport_under_the_pointer(self):
        with mock.patch.object(cmds,'getPanel',side_effect=self._panel_query(focus='leftView',pointer='rightView')):
            self.assertEqual(followCam._get_active_panel(),'rightView')

    def test_replacing_follow_cam_in_another_panel_restores_the_previous_view(self):
        target,left = self._create_camera_and_target()
        right = cmds.camera(name='SecondShot')[0]
        original = {'leftView':followCam._camera_transform(left),'rightView':followCam._camera_transform(right)}
        cameras = dict(original)
        def look(panel,camera):
            cameras[panel] = followCam._camera_transform(camera)
        with mock.patch.object(followCam,'_get_active_panel',side_effect=['leftView','rightView']), \
                mock.patch.object(followCam,'_panel_camera',side_effect=cameras.get), \
                mock.patch.object(cmds,'getPanel',side_effect=self._panel_query()), \
                mock.patch.object(cmds,'lookThru',side_effect=look), animkey_execution('test'):
            first = followCam.create_follow_cam()
            first_id = cmds.ls(first,uuid=True)[0]
            second = followCam.create_follow_cam()
            self.assertNotEqual(first_id,cmds.ls(second,uuid=True)[0])
            self.assertEqual(cameras['leftView'],original['leftView'])
            self.assertEqual(cameras['rightView'],second)
            self.assertEqual(len(followCam._find_follow_roots()),1)
            followCam.remove_follow_cam()
            self.assertEqual(cameras,original)

    def test_remove_does_not_overwrite_a_manually_changed_view(self):
        target,camera = self._create_camera_and_target()
        manual = cmds.camera(name='ManualShot')[0]
        cameras = {'leftView':followCam._camera_transform(camera)}
        def look(panel,camera):
            cameras[panel] = followCam._camera_transform(camera)
        with mock.patch.object(followCam,'_get_active_panel',return_value='leftView'), \
                mock.patch.object(followCam,'_panel_camera',side_effect=cameras.get), \
                mock.patch.object(cmds,'getPanel',side_effect=self._panel_query()), \
                mock.patch.object(cmds,'lookThru',side_effect=look), animkey_execution('test'):
            self.assertTrue(followCam.create_follow_cam())
            cameras['leftView'] = followCam._camera_transform(manual)
            followCam.remove_follow_cam()
            self.assertEqual(cameras['leftView'],followCam._camera_transform(manual))


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
