"""Keep non-absolute Graph Editor displays in sync with slider previews.

Maya can postpone redraws while a script updates curves, especially when the
preview uses MFnAnimCurve. Force a bounded redraw while dragging, but retain
the editor's original normalization factor so scaling a curve is visible.
Only renormalize once after the gesture to fit the finished curve.
"""

import time

import maya.cmds as cmds


class GraphEditorViewSync:
    def __init__(self, interval=1.0 / 20.0):
        self._interval = interval
        self._editors = ()
        self._last_update = None

    def begin(self):
        """Capture Graph Editors needing normalization once per gesture."""
        self._editors = ()
        self._last_update = None
        try:
            editors = cmds.lsUI(editors=True) or []
        except Exception:
            return

        active = []
        for editor in editors:
            try:
                if not cmds.animCurveEditor(editor, exists=True):
                    continue
                normalized = cmds.animCurveEditor(
                    editor, query=True, displayNormalized=True
                )
                stacked = cmds.animCurveEditor(
                    editor, query=True, stackedCurves=True
                )
                if normalized or stacked:
                    active.append(editor)
            except Exception:
                continue
        self._editors = tuple(active)

    def refresh(self, force=False):
        """Redraw the live preview without changing the display scale."""
        if not self._editors:
            return
        now = time.monotonic()
        if (
            not force
            and self._last_update is not None
            and now - self._last_update < self._interval
        ):
            return

        try:
            cmds.refresh(force=True)
            self._last_update = now
        except Exception:
            pass

    def end(self):
        """Leave the final curve shape visible, even after a short gesture."""
        try:
            for editor in self._editors:
                try:
                    if cmds.animCurveEditor(editor, exists=True):
                        cmds.animCurveEditor(
                            editor, edit=True, renormalizeCurves=True
                        )
                except Exception:
                    continue
            self.refresh(force=True)
        finally:
            self._editors = ()
            self._last_update = None
