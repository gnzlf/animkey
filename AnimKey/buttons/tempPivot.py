"""Temporary pivot proxy. The rig's own pivots and hierarchy stay intact.

ANIMKEY implementation of the documented interaction and row-vector delta:
target(t) = (target(0) * inverse(proxy(0))) * proxy(t).
"""
import math
import weakref
from contextlib import contextmanager

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.utils

from AnimKey.core import temp_transform as tf

_session = None
_previous_targets = []
_previous_anchor = None
_saved_pivots = {}
_buttons = []
_gesture_filter = None
_window = None


@contextmanager
def _unrecorded():
    state = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        yield
    finally:
        cmds.undoInfo(stateWithoutFlush=state)


class PivotSession:
    def __init__(self, nodes, mode="standard"):
        global _previous_anchor
        self.targets = [tf.NodeRef(n) for n in nodes]
        self.anchor = self.targets[-1]
        self.mode = mode
        if mode == "last":
            if len(self.targets) > 1:
                self.targets.pop()
            elif _previous_anchor and _previous_anchor.name():
                self.anchor = _previous_anchor
            _previous_anchor = self.anchor
        self.callbacks = []
        self.busy = True
        self.pending = False
        self.closed = False
        self.chunk = False
        self.gesture = False
        self.connection = None
        self.timeline = None
        self.editors = []
        self.orientation = (0., 0., 0.)
        self.cache = []
        self.parent = None
        self.proxy = None

    def start(self, edit=True):
        with _unrecorded():
            self.parent = tf.NodeRef(cmds.createNode("transform", name="AnimKey_Temp_Pivot#"))
            node = cmds.createNode("transform", name="AnimKey_Temp_Pivot_xForm#", parent=self.parent.name())
            self.proxy = tf.NodeRef(node)
            for attr in tf.TR + ("scaleX", "scaleY", "scaleZ", "visibility"):
                cmds.setAttr(node + "." + attr, keyable=False, channelBox=True)
            self.reposition(initial=True)
            memory = _saved_pivots.get(self.anchor.name())
            if memory and self.mode == "standard":
                pivot, self.orientation = memory
                cmds.xform(node, objectSpace=True, pivots=pivot)
                self.recache()
            self._connect_editors()
            cmds.select(node, replace=True)
            if not cmds.about(batch=True):
                cmds.setToolTo("moveSuperContext")
                cmds.manipPivot(orientation=self.orientation)
                if edit and (self.mode == "world" or (self.mode == "standard" and not memory)):
                    cmds.ctxEditMode()
        self.busy = False
        self.callbacks.append(om.MNodeMessage.addAttributeChangedCallback(self.proxy.handle.object(), self._changed))
        for event, callback in (("SelectionChanged", self._selection), ("timeChanged", self._defer_refresh),
                                ("Undo", self._undo), ("Redo", self._undo), ("DragRelease", self._release)):
            self.callbacks.append(om.MEventMessage.addEventCallback(event, callback))
        for event in (om.MSceneMessage.kBeforeNew, om.MSceneMessage.kBeforeOpen):
            self.callbacks.append(om.MSceneMessage.addCallback(event, lambda *_: self.close()))
        self.callbacks.append(om.MNodeMessage.addNodePreRemovalCallback(self.proxy.handle.object(), self._removed))
        self.callbacks.append(oma.MAnimMessage.addAnimCurveEditedCallback(self._defer_refresh))
        _install_gesture_filter()
        _sync_buttons()

    def _connect_editors(self):
        if cmds.about(batch=True):
            return
        try:
            self.timeline = mel.eval('$animkeyTempTimeline = $gPlayBackSlider')
            self.old_timeline = cmds.timeControl(self.timeline, query=True, mainListConnection=True)
            self.connection = cmds.selectionConnection()
            for ref in self.targets:
                cmds.selectionConnection(self.connection, edit=True, select=ref.name())
            cmds.timeControl(self.timeline, edit=True, mainListConnection=self.connection)
            for editor in cmds.lsUI(editors=True) or []:
                if cmds.objectTypeUI(editor) == "animCurveEditor":
                    old = cmds.animCurveEditor(editor, query=True, mainListConnection=True)
                    locked = cmds.animCurveEditor(editor, query=True, lockMainConnection=True)
                    self.editors.append((editor, old, locked))
                    cmds.animCurveEditor(editor, edit=True, unlockMainConnection=True,
                                         mainListConnection=self.connection)
                    cmds.animCurveEditor(editor, edit=True, lockMainConnection=True)
        except RuntimeError as exc:
            cmds.warning("AnimKey Temp Pivot: editor connection: {}".format(exc))

    def reposition(self, initial=False):
        if self.closed or not self.proxy or not self.proxy.name() or not self.anchor.name():
            return
        node = self.proxy.name()
        self.busy = True
        try:
            with _unrecorded():
                if self.mode in ("standard", "last"):
                    pivot = cmds.xform(node, query=True, objectSpace=True, rotatePivot=True)
                    cmds.xform(node, zeroTransformPivots=True)
                    cmds.xform(node, worldSpace=True, matrix=list(tf.rigid(tf.world(self.anchor.name()))))
                    cmds.xform(node, objectSpace=True, pivots=pivot)
                elif self.mode == "center" or (self.mode == "world" and initial):
                    names = [r.name() for r in self.targets if r.name()]
                    cmds.xform(node, zeroTransformPivots=True)
                    cmds.xform(node, worldSpace=True, rotation=(0, 0, 0), translation=tf.center(names))
            self.recache()
        finally:
            self.busy = False

    def recache(self, *_):
        if self.closed or not self.proxy or not self.proxy.name():
            return
        inverse = tf.world(self.proxy.name()).inverse()
        self.cache = [(ref, tf.world(ref.name()) * inverse) for ref in self.targets if ref.name()]

    def _editing(self):
        if cmds.about(batch=True):
            return False
        context = cmds.currentCtx()
        try:
            if "Rotate" in context:
                return bool(cmds.manipRotateContext("Rotate", query=True, editPivotMode=True))
            return bool(cmds.manipMoveContext("Move", query=True, editPivotMode=True))
        except RuntimeError:
            return False

    def _changed(self, message, plug, other, *_):
        if self.closed or self.busy or not message & om.MNodeMessage.kAttributeSet:
            return
        if om.MGlobal.isUndoing() or om.MGlobal.isRedoing():
            self._defer_refresh()
            return
        attr = plug.partialName(useLongNames=True).split("[")[0]
        if not any(attr.startswith(a) for a in ("translate", "rotate", "scale", "shear")):
            return
        # Editing the manipulation frame must never move targets.
        if self._editing() or "Pivot" in attr:
            self.recache()
            return
        self.busy = True
        try:
            if not self.chunk:
                cmds.undoInfo(openChunk=True, chunkName="AnimKey Temp Pivot gesture")
                self.chunk = True
            matrix = tf.world(self.proxy.name())
            desired = [(r, relative * matrix) for r, relative in self.cache if r.name()]
            # Resolve parents before children, using the same precomputed world
            # targets. This avoids applying the delta twice to descendants.
            for ref, target in sorted(desired, key=lambda pair: pair[0].name().count("|")):
                tf.set_world(ref.name(), target)
        except Exception as exc:
            cmds.warning("AnimKey Temp Pivot: {}".format(exc))
            self.finish()
        finally:
            self.busy = False
        if cmds.about(batch=True) and not self.gesture:
            self.finish()
        elif not self.gesture:
            # Idle coalescing also closes edits made in the Channel Box.
            maya.utils.executeDeferred(self.finish)

    def finish(self, *_):
        self.gesture = False
        if self.chunk and cmds.autoKeyframe(query=True,state=True):
            self.busy = True
            try:
                cmds.autoKeyframe()
            finally:
                self.busy = False
        if self.chunk:
            cmds.undoInfo(closeChunk=True)
            self.chunk = False
        if self.closed:
            return
        if not cmds.about(batch=True):
            try:
                self.orientation = tuple(cmds.manipPivot(query=True, orientation=True))
            except RuntimeError:
                pass
        if self.mode == "last":
            self.reposition()
        else:
            self.recache()

    def _release(self,*_):
        maya.utils.executeDeferred(self.finish)

    def _defer_refresh(self, *_):
        if self.busy or self.closed or self.pending:
            return
        self.pending = True
        def refresh():
            self.pending = False
            if not self.closed:
                self.reposition()
        maya.utils.executeDeferred(refresh)

    def _undo(self, *_):
        self._defer_refresh()

    def _selection(self, *_):
        if not self.busy and not self.closed:
            if cmds.ls(selection=True, long=True) != [self.proxy.name()]:
                self.close()

    def _removed(self, *_):
        if not self.busy:
            maya.utils.executeDeferred(self.close)

    def close(self):
        global _session, _previous_targets
        if self.closed:
            return
        self.closed = True
        self.busy = True
        if self.chunk:
            cmds.undoInfo(closeChunk=True)
            self.chunk = False
        for callback in self.callbacks:
            try:
                om.MMessage.removeCallback(callback)
            except RuntimeError:
                pass
        self.callbacks = []
        _previous_targets = self.targets[:]
        with _unrecorded():
            if self.proxy and self.proxy.name():
                pivot = cmds.xform(self.proxy.name(), query=True, objectSpace=True, rotatePivot=True)
                anchor = self.anchor.name()
                if anchor:
                    if any(abs(v) > 1e-9 for v in list(pivot) + list(self.orientation)):
                        _saved_pivots[anchor] = (pivot, self.orientation)
                    else:
                        _saved_pivots.pop(anchor, None)
            for editor, old, locked in self.editors:
                try:
                    cmds.animCurveEditor(editor, edit=True, unlockMainConnection=True, mainListConnection=old)
                    if locked:
                        cmds.animCurveEditor(editor, edit=True, lockMainConnection=True)
                except RuntimeError:
                    pass
            if self.timeline:
                try:
                    cmds.timeControl(self.timeline, edit=True, mainListConnection=self.old_timeline or "animationList")
                except RuntimeError:
                    pass
            if self.connection and cmds.selectionConnection(self.connection, exists=True):
                cmds.deleteUI(self.connection)
            if self.parent and self.parent.name():
                cmds.delete(self.parent.name())
            names = [r.name() for r in self.targets if r.name()]
            if names:
                cmds.select(names, replace=True)
            if not cmds.about(batch=True):
                cmds.setToolTo("moveSuperContext")
        if _session is self:
            _session = None
        _sync_buttons()


def activate_temp_pivot(objects=None, pivot_mode="standard", edit_pivot=True):
    global _session
    if is_active():
        return True
    if pivot_mode not in ("standard", "last", "center", "world"):
        raise ValueError("Unknown temporary pivot mode: {}".format(pivot_mode))
    nodes = tf.transforms(objects)
    if not nodes:
        nodes = [r.name() for r in _previous_targets if r.name()]
    if not nodes:
        cmds.warning("AnimKey Temp Pivot: select at least one transform.")
        return False
    _session = PivotSession(nodes, pivot_mode)
    try:
        _session.start(edit=edit_pivot)
    except Exception:
        _session.close()
        raise
    return True


def deactivate_temp_pivot():
    if _session:
        _session.close()
    return True


def begin_gesture():
    """Open before the native manipulator command enters Maya's undo queue."""
    if is_active() and not _session.chunk:
        cmds.undoInfo(openChunk=True, chunkName="AnimKey Temp Pivot gesture")
        _session.chunk = True
        _session.gesture = True


def _install_gesture_filter():
    global _gesture_filter
    if cmds.about(batch=True) or _gesture_filter is not None:
        return
    from AnimKey.mods.maya_compat import QtCore, QtWidgets
    class GestureFilter(QtCore.QObject):
        def eventFilter(self, watched, event):
            if not is_active():
                return False
            kind = event.type()
            key_edit = (kind == QtCore.QEvent.KeyPress and isinstance(watched,(QtWidgets.QLineEdit,QtWidgets.QAbstractSpinBox))
                        and not event.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier))
            if kind == QtCore.QEvent.MouseButtonPress or key_edit:
                begin_gesture()
            elif kind in (QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.KeyRelease):
                session = _session
                QtCore.QTimer.singleShot(0, session.finish)
            return False
    app = QtWidgets.QApplication.instance()
    if app:
        _gesture_filter = GestureFilter(app)
        app.installEventFilter(_gesture_filter)


def toggle_edit_temp_pivot():
    if is_active() and not cmds.about(batch=True):
        cmds.ctxEditMode()


def reset_temp_pivot_offsets(objects=None):
    if not is_active():
        return False
    with _unrecorded():
        _session.busy = True
        try:
            cmds.xform(_session.proxy.name(), zeroTransformPivots=True)
            _session.orientation = (0., 0., 0.)
            if not cmds.about(batch=True):
                cmds.manipPivot(orientation=(0, 0, 0))
            _saved_pivots.pop(_session.anchor.name(), None)
            _session.recache()
        finally:
            _session.busy = False
    return True


def is_active():
    return bool(_session and not _session.closed and _session.proxy and _session.proxy.name())


def _sync_buttons():
    _buttons[:] = [ref for ref in _buttons if ref() is not None]
    for ref in _buttons[:]:
        button = ref()
        if button is not None:
            set_button_active(button, is_active())
    if _window is not None:
        from AnimKey.mods.maya_compat import is_qt_object_valid
        if is_qt_object_valid(_window):
            _window.refresh()


def set_button_active(button, active):
    if button is None:
        return
    try:
        if not any(ref() is button for ref in _buttons):
            _buttons.append(weakref.ref(button))
        was_active = bool(button.property("tempPivotActive"))
        if active and not was_active:
            button.setProperty("tempPivotIdleStyle",button.styleSheet())
        if active:
            button.setStyleSheet("QPushButton {background:#24665e; border:2px solid #5bc0be; border-radius:4px;}")
        elif was_active:
            button.setStyleSheet(button.property("tempPivotIdleStyle") or "")
        button.setProperty("tempPivotActive", bool(active))
    except RuntimeError:
        pass


def execute_temp_pivot(*args, **kwargs):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("Temp Pivot"):
        return
    from AnimKey.mods.maya_compat import QtCore, QtWidgets
    mods = QtWidgets.QApplication.keyboardModifiers()
    ctrl, shift, alt = (bool(mods & flag) for flag in
                        (QtCore.Qt.ControlModifier, QtCore.Qt.ShiftModifier, QtCore.Qt.AltModifier))
    if alt and ctrl:
        return reset_temp_pivot_offsets()
    if alt:
        return toggle_edit_temp_pivot()
    mode = kwargs.get("pivot_mode") or ("world" if ctrl and shift else "center" if ctrl else "last" if shift else "standard")
    if not (ctrl or shift or kwargs.get("pivot_mode")) and is_active():
        return deactivate_temp_pivot()
    result = activate_temp_pivot(kwargs.get("objects"), mode, kwargs.get("edit_pivot", True))
    set_button_active(kwargs.get("button"), is_active())
    return result


def show(button=None):
    global _window
    from AnimKey.mods.maya_compat import is_qt_object_valid
    from AnimKey.buttons.tempPivotUI import TempPivotWindow
    if is_qt_object_valid(_window):
        _window.close()
    _window = TempPivotWindow(anchor_button=button)
    _window.show()
    _window.position_window()
    _window.raise_()
    return _window


def show_menu(button=None, pos=None):
    """Compatibility hook: settings live in the tool window, without menus."""
    return show(button=button)


def execute(*args, **kwargs):
    """Compatibility entry point for older Temp Control shortcuts."""
    from AnimKey.buttons.tempControls import execute as controls_execute
    return controls_execute(*args, **kwargs)


def cleanup_orphans():
    global _gesture_filter, _window
    from AnimKey.mods.maya_compat import is_qt_object_valid
    if is_qt_object_valid(_window):
        _window.close()
    _window = None
    deactivate_temp_pivot()
    if _gesture_filter is not None:
        from AnimKey.mods.maya_compat import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app:
            app.removeEventFilter(_gesture_filter)
        _gesture_filter.deleteLater()
        _gesture_filter = None
