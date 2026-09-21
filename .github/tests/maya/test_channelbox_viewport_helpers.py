import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

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
        tumble_helper._context_name = None
        tumble_helper._original_settings = None
        tumble_helper._script_jobs = []

    def test_native_context_changes_and_restores_without_camera_edits(self):
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
                "objectTumble": True,
                "autoSetPivot": True,
                "localTumble": 0,
            })
            tumble_helper.refresh()
            tumble_helper.apply(False)
            set_attr.assert_not_called()
            xform.assert_not_called()
        self.assertEqual(settings, original)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
