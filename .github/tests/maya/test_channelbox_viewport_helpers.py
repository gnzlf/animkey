import importlib
import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds
import maya.api.OpenMaya as om

from AnimKey.mods import (
    channelBoxMultiSelection as channel_helper,
    timelineChannelFilter as timeline_filter,
    tumbleAroundSelection as tumble_helper,
)


class ChannelBoxHelperTests(unittest.TestCase):
    def setUp(self):
        channel_helper._active = False
        channel_helper._pending = False
        channel_helper._refreshing = False
        channel_helper._original_fixed_attr_list = None
        channel_helper._last_objects = None
        channel_helper._last_attr_list = None
        channel_helper._script_jobs = []

    def test_multi_selection_uses_common_attrs_without_sticky_selection(self):
        state = {
            "fixed": ["translateX", "translateY", "rotateZ"],
            "connection": "mayaSelection",
            "selection": ["controlA", "controlB"],
        }
        edits = []
        attrs = {
            "controlA": ["translateX", "translateY"],
            "controlB": ["translateX", "rotateZ"],
        }

        def channel_box(_name, **flags):
            if flags.get("exists"):
                return True
            if flags.get("query"):
                if flags.get("fixedAttrList"):
                    return state["fixed"]
                if flags.get("mainListConnection"):
                    return state["connection"]
            if flags.get("edit"):
                edits.append(flags)
                if "fixedAttrList" in flags:
                    state["fixed"] = list(flags["fixedAttrList"])
                if "mainListConnection" in flags:
                    state["connection"] = flags["mainListConnection"]

        with mock.patch.object(channel_helper.mel, "eval", return_value="mainChannelBox"), \
                mock.patch.object(channel_helper.cmds, "channelBox", side_effect=channel_box), \
                mock.patch.object(channel_helper.cmds, "ls", side_effect=lambda **_kw: state["selection"]), \
                mock.patch.object(channel_helper.cmds, "objExists", return_value=True), \
                mock.patch.object(channel_helper.cmds, "listAttr",
                                  side_effect=lambda node, **kw: attrs[node]
                                  if kw.get("keyable") else []), \
                mock.patch.object(channel_helper.cmds, "scriptJob", return_value=1), \
                mock.patch.object(channel_helper.cmds, "evalDeferred"):
            channel_helper.apply(True)
            channel_helper.refresh()
            self.assertEqual(state["fixed"], ["translateX"])
            self.assertEqual(state["connection"], "mayaSelection")

            state["selection"] = ["controlB"]
            channel_helper.refresh()
            self.assertEqual(
                state["fixed"], ["translateX", "translateY", "rotateZ"]
            )
            channel_helper.apply(False)

        self.assertFalse(any("select" in edit for edit in edits))
        self.assertFalse(any("mainListConnection" in edit for edit in edits))

    def test_legacy_private_connection_is_released(self):
        state = {
            "connection": channel_helper.LEGACY_CONNECTION_NAME,
            "fixed": ["staleAttribute"],
        }

        def channel_box(_name, **flags):
            if flags.get("exists"):
                return True
            if flags.get("query"):
                if flags.get("mainListConnection"):
                    return state["connection"]
                if flags.get("fixedAttrList"):
                    return state["fixed"]
            if "mainListConnection" in flags:
                state["connection"] = flags["mainListConnection"]
            if "fixedAttrList" in flags:
                state["fixed"] = list(flags["fixedAttrList"])

        with mock.patch.object(channel_helper.mel, "eval", return_value="mainChannelBox"), \
                mock.patch.object(channel_helper.cmds, "channelBox", side_effect=channel_box), \
                mock.patch.object(channel_helper.cmds, "scriptJob", return_value=1), \
                mock.patch.object(channel_helper.cmds, "evalDeferred"):
            channel_helper.apply(True)
        self.assertEqual(state["connection"], "")
        self.assertEqual(state["fixed"], [])


class TimelineFilterTests(unittest.TestCase):
    def setUp(self):
        timeline_filter._active = False
        timeline_filter._original_show_keys = None
        timeline_filter._original_show_keys_combined = None

    def test_disabling_restores_previous_time_slider_mode(self):
        state = {"showKeys": "none", "showKeysCombined": False}

        def time_control(_name, **flags):
            if flags.get("query"):
                return state["showKeys"] if flags.get("showKeys") else state["showKeysCombined"]
            if flags.get("edit"):
                state.update({key: flags[key] for key in state})

        with mock.patch.object(timeline_filter, "_get_time_slider", return_value="timeSlider"), \
                mock.patch.object(timeline_filter, "_get_channel_box", return_value="mainChannelBox"), \
                mock.patch.object(timeline_filter.cmds, "timeControl", side_effect=time_control):
            self.assertTrue(timeline_filter.apply(True))
            self.assertTrue(timeline_filter.apply(True))
            self.assertEqual(state, {
                "showKeys": "mainChannelBox", "showKeysCombined": True
            })
            self.assertTrue(timeline_filter.apply(False))
        self.assertEqual(state, {"showKeys": "none", "showKeysCombined": False})


class TumbleHelperTests(unittest.TestCase):
    def setUp(self):
        tumble_helper._active = False
        tumble_helper._pending = False
        tumble_helper._script_jobs = []

    def test_native_selection_pivot_and_stable_disabled_mode_without_camera_edits(self):
        original = {
            "objectTumble": False,
            "autoSetPivot": False,
            "localTumble": 2,
        }
        settings = dict(original)

        def tumble_ctx(_name, **flags):
            if flags.get("exists"):
                return True
            if flags.get("query"):
                return next(settings[key] for key in settings if flags.get(key))
            if flags.get("edit"):
                for key in settings:
                    if key in flags:
                        settings[key] = flags[key]

        with mock.patch.object(tumble_helper.mel, "eval", return_value="tumbleContext"), \
                mock.patch.object(tumble_helper.cmds, "tumbleCtx", side_effect=tumble_ctx), \
                mock.patch.object(tumble_helper.cmds, "scriptJob", return_value=1), \
                mock.patch.object(tumble_helper.cmds, "setAttr") as set_attr, \
                mock.patch.object(tumble_helper.cmds, "xform") as xform:
            tumble_helper.apply(True)
            self.assertEqual(settings, {
                "objectTumble": False,
                "autoSetPivot": True,
                "localTumble": 0,
            })
            tumble_helper.refresh()
            tumble_helper.apply(False)
            set_attr.assert_not_called()
            xform.assert_not_called()
        self.assertEqual(settings, {
            "objectTumble": False, "autoSetPivot": False, "localTumble": 1,
        })

    def _legacy_context(self, _name, **flags):
        if flags.get("exists"):
            return True
        if flags.get("query"):
            return next(self.settings[key] for key in self.settings if flags.get(key))
        if flags.get("edit"):
            for key in self.settings:
                if key in flags:
                    self.settings[key] = flags[key]

    def _patch_legacy_context(self):
        self.settings = {"objectTumble": True, "autoSetPivot": True, "localTumble": 0}
        for patch in (
            mock.patch.object(tumble_helper.mel, "eval", return_value="tumbleContext"),
            mock.patch.object(tumble_helper.cmds, "tumbleCtx", side_effect=self._legacy_context),
            mock.patch.object(tumble_helper.cmds, "scriptJob", return_value=1),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _assert_disabled(self):
        self.assertEqual(self.settings, {
            "objectTumble": False, "autoSetPivot": False, "localTumble": 1,
        })
        self.assertFalse(tumble_helper.is_active())
        self.assertFalse(tumble_helper._script_jobs)

    def test_disabled_startup_repairs_legacy_cursor_pivot_without_snapshot(self):
        self._patch_legacy_context()
        with mock.patch.object(tumble_helper.config, "get_setting", return_value=False):
            tumble_helper.apply()
        self._assert_disabled()

    def test_disable_does_not_restore_legacy_cursor_orbit(self):
        self._patch_legacy_context()
        tumble_helper.apply(True)
        tumble_helper.apply(False)
        self._assert_disabled()

    def test_deferred_refresh_after_disable_keeps_normal_navigation(self):
        self._patch_legacy_context()
        queued = []
        with mock.patch.object(tumble_helper.cmds, "evalDeferred", side_effect=lambda cb, **kw: queued.append(cb)):
            tumble_helper.apply(True)
            tumble_helper._refresh_deferred()
            self.assertEqual(len(queued), 1)
            tumble_helper.apply(False)
            # The queued command resolves refresh() on the current module.
            tumble_helper.refresh()
        self._assert_disabled()

    def test_disabled_startup_retries_when_maya_creates_context_later(self):
        self._patch_legacy_context()
        with mock.patch.object(tumble_helper, "_tumble_context", return_value=None), \
                mock.patch.object(tumble_helper.cmds, "evalDeferred") as deferred:
            tumble_helper.apply(False)
            deferred.assert_called_once()
        tumble_helper.refresh()
        self._assert_disabled()

    def test_scene_new_and_tool_change_refresh_only_native_context(self):
        self._patch_legacy_context()
        with mock.patch.object(tumble_helper.cmds, "scriptJob", return_value=1) as jobs:
            tumble_helper.apply(True)
        events = {call.kwargs["event"][0] for call in jobs.call_args_list}
        self.assertEqual(events, {"ToolChanged", "SceneOpened", "NewSceneOpened"})

    def test_reload_retains_jobs_so_disable_can_remove_them(self):
        self._patch_legacy_context()
        with mock.patch.object(tumble_helper.cmds, "scriptJob", return_value=1) as jobs:
            tumble_helper.apply(True)
            handles = list(tumble_helper._script_jobs)
            importlib.reload(tumble_helper)
            self.assertTrue(tumble_helper.is_active())
            self.assertEqual(tumble_helper._script_jobs, handles)
            tumble_helper.apply(False)
            self.assertEqual(sum("kill" in call.kwargs for call in jobs.call_args_list), len(handles))
        self._assert_disabled()

    def test_frame_selection_then_orbit_keeps_target_centered_in_both_modes(self):
        self._patch_legacy_context()
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                cmds.file(new=True, force=True)
                obj = cmds.polyCube()[0]
                cmds.move(45, 25, -30, obj)
                other = cmds.polyCube()[0]
                cmds.move(-100, -50, 75, other)
                cmds.select(obj, replace=True)
                tumble_helper.apply(enabled)

                # viewFit is the native Frame Selection operation bound to F.
                cmds.viewFit("persp", animate=False)
                target_matrix = cmds.xform(obj, query=True, worldSpace=True, matrix=True)
                target = om.MPoint(*cmds.xform(obj, query=True, worldSpace=True, translation=True))
                camera_before = om.MMatrix(cmds.xform("persp", query=True, worldSpace=True, matrix=True))
                framed_point = target * camera_before.inverse()
                self.assertAlmostEqual(framed_point.x, 0.0, places=6)
                self.assertAlmostEqual(framed_point.y, 0.0, places=6)
                self.assertFalse(self.settings["objectTumble"])

                # Context tools are unavailable in batch, so use the native
                # tumble command with the mode chosen by the helper.
                for angles in ((35, 20), (-70, 15), (10, -40)):
                    cmds.tumble("persp", rotationAngles=angles, localTumble=self.settings["localTumble"])
                    camera = om.MMatrix(cmds.xform("persp", query=True, worldSpace=True, matrix=True))
                    point = target * camera.inverse()
                    self.assertAlmostEqual(point.x, 0.0, places=6)
                    self.assertAlmostEqual(point.y, 0.0, places=6)
                    self.assertAlmostEqual(point.z, framed_point.z, places=6)
                self.assertEqual(cmds.xform(obj, query=True, worldSpace=True, matrix=True), target_matrix)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
