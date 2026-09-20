import unittest
from unittest import mock

import maya.standalone

try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass

import maya.cmds as cmds

from AnimKey.sliders import graph_editor_view, tweener


class GraphEditorSliderViewTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        tweener._restore_api_preview()
        if tweener._is_dragging:
            cmds.undoInfo(closeChunk=True)
        tweener._is_dragging = False
        tweener._tween_cache = []

    def test_only_stacked_and_normalized_editors_are_renormalized(self):
        modes = {
            "absolute": (False, False),
            "stacked": (False, True),
            "normalized": (True, False),
        }
        edits = []

        def editor_command(editor, **flags):
            if flags.get("exists"):
                return editor in modes
            if flags.get("query"):
                if flags.get("displayNormalized"):
                    return modes[editor][0]
                if flags.get("stackedCurves"):
                    return modes[editor][1]
            if flags.get("edit") and flags.get("renormalizeCurves"):
                edits.append(editor)

        with mock.patch.object(
            graph_editor_view.cmds, "lsUI",
            return_value=["absolute", "stacked", "normalized", "notAnEditor"],
        ), mock.patch.object(
            graph_editor_view.cmds, "animCurveEditor",
            side_effect=editor_command,
        ), mock.patch.object(
            graph_editor_view.cmds, "refresh",
        ) as redraw, mock.patch.object(
            graph_editor_view.time, "monotonic",
            side_effect=[10.0, 10.01, 10.05, 10.06],
        ):
            sync = graph_editor_view.GraphEditorViewSync(interval=0.03)
            sync.begin()
            sync.refresh()
            self.assertEqual(edits, [])
            sync.refresh()  # Coalesced with the previous display frame.
            self.assertEqual(edits, [])
            sync.refresh()
            sync.end()  # Always show the final result.
            sync.refresh()  # No editor work after release.
            self.assertEqual(redraw.call_count, 3)

        self.assertEqual(edits, ["stacked", "normalized"])

    def test_slider_keeps_real_key_values_in_each_display_mode(self):
        for mode in ("absolute", "stacked", "normalized"):
            with self.subTest(mode=mode):
                cmds.file(new=True, force=True)
                control = cmds.createNode("transform", name="viewTestControl")
                plug = control + ".translateX"
                for frame, value in ((0, 0), (10, 8), (20, 20)):
                    cmds.setKeyframe(control, attribute="translateX", time=frame,
                                     value=value)
                curve = cmds.keyframe(plug, query=True, name=True)[0]
                cmds.currentTime(10)
                cmds.select(control, replace=True)
                cmds.selectKey(clear=True)
                cmds.selectKey(curve, add=True, time=(10, 10))
                cmds.flushUndo()

                edits = []

                def editor_command(_editor, **flags):
                    if flags.get("exists"):
                        return True
                    if flags.get("query"):
                        return bool(
                            (mode == "normalized" and flags.get("displayNormalized"))
                            or (mode == "stacked" and flags.get("stackedCurves"))
                        )
                    if flags.get("edit") and flags.get("renormalizeCurves"):
                        edits.append(True)

                with mock.patch.object(
                    graph_editor_view.cmds, "lsUI", return_value=["graphEditor"]
                ), mock.patch.object(
                    graph_editor_view.cmds, "animCurveEditor",
                    side_effect=editor_command,
                ), mock.patch.object(
                    graph_editor_view.cmds, "refresh"
                ) as redraw:
                    sync = graph_editor_view.GraphEditorViewSync()
                    sync.begin()
                    tweener.prepare_tween_data()
                    tweener.execute(25)
                    sync.refresh()
                    self.assertAlmostEqual(
                        cmds.keyframe(curve, query=True, time=(10, 10),
                                      valueChange=True)[0],
                        5.0,
                    )
                    tweener.reset()
                    sync.end()

                self.assertEqual(bool(edits), mode != "absolute")
                self.assertEqual(redraw.call_count, 0 if mode == "absolute" else 2)
                cmds.undo()
                self.assertAlmostEqual(
                    cmds.keyframe(curve, query=True, time=(10, 10),
                                  valueChange=True)[0],
                    8.0,
                )


if __name__ == "__main__":
    result = unittest.main(exit=False)
    maya.standalone.uninitialize()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
