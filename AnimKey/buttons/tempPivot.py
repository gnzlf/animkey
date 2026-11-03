"""Animo-based Temp Pivot data engine behind AnimKey's existing controls."""
import os
import weakref
from contextlib import contextmanager

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.utils

from AnimKey.core import temp_transform as tf
from AnimKey.core.transfer_reference import temp_pivot as kernel

_session = None
_previous_targets = []
_previous_anchor = None
_saved_pivots = {}
_buttons = []
_gesture_filter = None
_window = None


def _memory_path():
    from AnimKey.mods import configMod
    return os.path.join(configMod.get_user_folder_path(), "tools", "temp_pivot_offsets.json")


@contextmanager
def _unrecorded():
    state = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        yield
    finally:
        cmds.undoInfo(stateWithoutFlush=state)


class PivotSession(kernel.LiveTempPivot):
    def __init__(self, nodes, mode="standard"):
        global _previous_anchor
        super(PivotSession, self).__init__()
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
        self.parent = None
        self.proxy = None
        self.locator_refs = []
        self.dirty = False
        self.edited = False
        self.keyed_pending = False
        self.frame = cmds.currentTime(query=True)

    @property
    def manip_chunk_open(self):
        return self.chunk

    @manip_chunk_open.setter
    def manip_chunk_open(self, value):
        self.chunk = value

    def start(self, edit=True):
        with _unrecorded():
            self.parent = tf.NodeRef(cmds.createNode("transform", name="AnimKey_Temp_Pivot#"))
            node = cmds.createNode("transform", name="AnimKey_Temp_Pivot_xForm#", parent=self.parent.name())
            self.proxy = tf.NodeRef(node)
            kernel.PIVOT_NULL = node
            kernel._storage_path = _memory_path()
            for attr in tf.TR:
                cmds.setAttr(node + "." + attr, keyable=True, channelBox=True)
            for attr in ("scaleX", "scaleY", "scaleZ", "visibility"):
                cmds.setAttr(node + "." + attr, lock=True, keyable=False, channelBox=False)
            self.sel = [r.name() for r in self.targets if r.name()]
            self.ref_obj = self.anchor.name()
            self.cache_locked_attrs()
            self.objects_had_keys = any(kernel.has_keyframes(n, kernel.TR_ATTRS) for n in self.sel)
            if self.mode in ("center", "world"):
                cmds.xform(node, worldSpace=True, translation=tf.center(self.sel))
            else:
                cmds.matchTransform(node, self.ref_obj, pos=True, rot=True)
            remembered = self.mode == "standard" and self.apply_stored_offset(self.sel)
            self.build_locators()
            self.locator_refs = [(tf.NodeRef(obj), tf.NodeRef(loc)) for obj, loc in self.locators]
            self.store_pivot_offset()
            self.last_pivot_matrix = kernel.get_matrix(node)
            self._connect_editors()
            cmds.select(node, replace=True)
            if not cmds.about(batch=True):
                cmds.setToolTo("moveSuperContext")
                cmds.manipPivot(orientation=self.orientation)
                if edit and (self.mode == "world" or (self.mode == "standard" and not remembered)):
                    cmds.ctxEditMode()
        self.busy = False
        self.callbacks.append(om.MNodeMessage.addAttributeChangedCallback(self.proxy.handle.object(), self._changed))
        for event, callback in (("SelectionChanged", self._selection), ("timeChanged", self._time_changed),
                                ("Undo", self._undo), ("Redo", self._undo), ("DragRelease", self._release)):
            self.callbacks.append(om.MEventMessage.addEventCallback(event, callback))
        for event in (om.MSceneMessage.kBeforeNew, om.MSceneMessage.kBeforeOpen):
            self.callbacks.append(om.MSceneMessage.addCallback(event, lambda *_: self.close(save=False, restore_selection=False)))
        self.callbacks.append(om.MNodeMessage.addNodePreRemovalCallback(self.proxy.handle.object(), self._removed))
        self.callbacks.append(oma.MAnimMessage.addAnimCurveEditedCallback(self._curve_edited))
        if not cmds.about(batch=True):
            from AnimKey.mods.maya_compat import QtCore
            self.timer = QtCore.QTimer()
            self.timer.timeout.connect(self.tick)
            self.timer.start(16)
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
        self.busy = True
        try:
            with _unrecorded():
                self._sync_refs()
                if cmds.currentTime(query=True) == self.frame and not initial:
                    self.recache()
                else:
                    kernel.LiveTempPivot.on_time_changed(self)
                self.frame = cmds.currentTime(query=True)
        finally:
            self.busy = False

    def recache(self, *_):
        if self.closed or not self.proxy or not self.proxy.name():
            return
        with _unrecorded():
            self._sync_refs()
            self.refresh_locators()
            self.store_pivot_offset()
            self.last_pivot_matrix = kernel.get_matrix(self.proxy.name())

    def _sync_refs(self):
        kernel.PIVOT_NULL = self.proxy.name()
        self.sel = [ref.name() for ref in self.targets if ref.name()]
        self.ref_obj = self.anchor.name()
        self.locators = [(obj.name(), loc.name()) for obj, loc in self.locator_refs if obj.name() and loc.name()]
        self.cache_locked_attrs()

    def apply_pivot_from_ref(self):
        if self.mode == "world":
            return
        if self.mode == "center":
            cmds.xform(self.proxy.name(), worldSpace=True, translation=tf.center(self.sel), rotation=(0, 0, 0))
            return
        return kernel.LiveTempPivot.apply_pivot_from_ref(self)

    def follow(self):
        # The normal route is the supplied native locator/matchTransform engine.
        partial = any(0 < len(locked.get(key, [])) < 3 for locked in self.locked_attrs.values()
                      for key in ("skip_t", "skip_r"))
        if not partial:
            kernel.LiveTempPivot.follow(self)
        else:
            # Preserve per-axis locks with AnimKey's existing Maya channel
            # solver. Finish each parent before its children to avoid moving a
            # child twice when only some channels of its parent are writable.
            pairs = sorted(self.locators, key=lambda pair: pair[0].count("|"))
            desired = {obj: tf.world(loc) for obj, loc in pairs}
            for _ in range(len(pairs) + 1):
                changed = False
                for obj, _ in pairs:
                    before = kernel.get_matrix(obj)
                    tf.set_world(obj, desired[obj])
                    changed |= not self.matrices_equal(before, kernel.get_matrix(obj), 1e-6)
                if not changed:
                    break
        # matchTransform redo can reevaluate its live locator after Undo rebases
        # the helper. Record the evaluated channel result explicitly so Redo
        # restores the same pose even after the helper has been recached.
        for obj in self.sel:
            for attr in tf.writable(obj):
                plug = obj + "." + attr
                if cmds.getAttr(plug, settable=True):
                    cmds.setAttr(plug, cmds.getAttr(plug))

    def open_manip_chunk(self):
        if not self.chunk:
            cmds.undoInfo(openChunk=True, chunkName="AnimKey Temp Pivot gesture")
            self.chunk = True

    def close_manip_chunk(self):
        if self.busy and not self.is_time_changing:
            return  # Finish native key forwarding and Euler filtering together.
        if self.chunk:
            cmds.undoInfo(closeChunk=True)
            self.chunk = False

    def maybe_finalize(self):
        # The owning session closes one complete native gesture after key
        # forwarding and Auto Key, rather than opening another timer transaction.
        pass

    def tick(self, force=False):
        if self.closed or self.busy or not self.proxy.name():
            return
        if cmds.currentTime(query=True) != self.frame:
            self.reposition()
            return
        self._sync_refs()
        if not self.sel or not self.ref_obj:
            self.close(save=False)
            return
        before = {n: kernel.get_matrix(n) for n in self.sel}
        had_proxy_keys = bool(kernel.get_key_count(self.proxy.name(), kernel.TR_ATTRS))
        if force:
            self.time_change_cooldown = 0
            self.awaiting_real_manipulation = False
        self.busy = True
        try:
            kernel.LiveTempPivot.on_timer_tick(self)
            if had_proxy_keys and not kernel.get_key_count(self.proxy.name(), kernel.TR_ATTRS):
                self.keyed_pending = True
            if any(not self.matrices_equal(matrix, kernel.get_matrix(n), 1e-8) for n, matrix in before.items()):
                self.dirty = self.edited = True
        finally:
            self.busy = False
        if not self.gesture and (self.chunk or self.pending_reapply or had_proxy_keys):
            from AnimKey.mods.maya_compat import QtWidgets
            if cmds.about(batch=True) or not QtWidgets.QApplication.mouseButtons():
                self.finish()

    def _curve_edited(self, *_):
        if not self.closed and not self.busy:
            # S/Set Key goes through the same native forwarding code as Animo.
            if kernel.get_key_count(self.proxy.name(), kernel.TR_ATTRS):
                self.tick(force=True)
            else:
                self._defer_refresh()

    def _time_changed(self, *_):
        if not self.closed and not self.busy:
            self.reposition()

    def save_relationship_to_disk(self):
        if self.mode != "standard" or not self.proxy.name() or not self.sel:
            return
        kernel.LiveTempPivot.save_relationship_to_disk(self)
        data = kernel.load_offsets()
        record = data["single_offsets"].get(self.sel[0]) if len(self.sel) == 1 else next(
            (g["offset_data"] for g in data["multi_offsets"] if sorted(g["objects"]) == sorted(self.sel)), None)
        if record:
            data["last_selection_ids"] = cmds.ls(self.sel, uuid=True) or []
            record["object_ids"] = sorted(cmds.ls(self.sel, uuid=True) or [])
            record["ref_id"] = (cmds.ls(self.ref_obj, uuid=True) or [None])[0]
            kernel.save_offsets(data)
            _saved_pivots[self.anchor.name()] = record

    def apply_stored_offset(self, selection):
        data = kernel.load_offsets()
        ids = sorted(cmds.ls(selection, uuid=True) or [])
        candidates = list(data["single_offsets"].values()) + [g.get("offset_data") for g in data["multi_offsets"]]
        record = next((r for r in candidates if isinstance(r, dict) and r.get("object_ids") == ids), None)
        if not record:
            return False
        record = dict(record)
        ref = cmds.ls(record.get("ref_id") or "", long=True) or []
        record["ref_obj"] = ref[0] if ref else selection[-1]
        memory = {"single_offsets": {selection[0]: record} if len(selection) == 1 else {},
                  "multi_offsets": [{"objects": selection, "offset_data": record}] if len(selection) > 1 else []}
        kernel._memory_override = memory
        try:
            return kernel.LiveTempPivot.apply_stored_offset(self, selection)
        finally:
            kernel._memory_override = None

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
        try:
            self.tick(force=True)
        except Exception as exc:
            cmds.warning("AnimKey Temp Pivot: {}".format(exc))
            self.finish()

    def finish(self, *_):
        self.gesture = False
        if self.chunk and cmds.autoKeyframe(query=True,state=True):
            self.busy = True
            try:
                cmds.autoKeyframe()
            finally:
                self.busy = False
        if self.chunk and self.dirty and (self.objects_had_keys or self.keys_set_this_session):
            self.busy = True
            try:
                self._key_targets()
            finally:
                self.busy = False
        if self.chunk and (self.dirty or self.keyed_pending) and (self.objects_had_keys or self.keys_set_this_session):
            self.busy = True
            try:
                for node in self.sel:
                    kernel.run_euler_filter(node)
            finally:
                self.busy = False
        if self.chunk:
            cmds.undoInfo(closeChunk=True)
            self.chunk = False
        if self.closed:
            return
        self.dirty = False
        self.keyed_pending = False
        self.pending_reapply = False
        if not cmds.about(batch=True):
            try:
                self.orientation = tuple(cmds.manipPivot(query=True, orientation=True))
            except RuntimeError:
                pass
        with _unrecorded():
            self.recache()
            self.save_relationship_to_disk()

    def _key_targets(self):
        for node in self.sel:
            attrs = [attr for attr in kernel.TR_ATTRS if not kernel.is_attr_locked(node, attr)
                     and cmds.getAttr(node + "." + attr, keyable=True)]
            if attrs:
                cmds.setKeyframe(node, attribute=attrs)

    def _release(self,*_):
        maya.utils.executeDeferred(self.finish)

    def _defer_refresh(self, *_):
        if self.busy or self.closed or self.pending:
            return
        self.pending = True
        def refresh():
            self.pending = False
            if not self.closed:
                with _unrecorded():
                    self.recache()
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

    def close(self, save=True, restore_selection=True):
        global _session, _previous_targets
        if self.closed:
            return
        if save and self.proxy and self.proxy.name():
            if kernel.get_key_count(self.proxy.name(), kernel.TR_ATTRS):
                self.tick(force=True)
            if self.chunk:
                self.finish()
            self._sync_refs()
            self.save_relationship_to_disk()
        self.closed = True
        self.busy = True
        if self.timer:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None
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
            if names and restore_selection:
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
        kernel._storage_path = _memory_path()
        memory = kernel.load_offsets()
        if memory.get("last_selection_ids"):
            for identity in memory["last_selection_ids"]:
                nodes.extend(tf.transforms(cmds.ls(identity, long=True) or []))
        else:
            nodes = tf.transforms(memory.get("last_selection", []))
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


def deactivate_temp_pivot(restore_selection=True):
    if _session:
        _session.close(restore_selection=restore_selection)
    return True


def begin_gesture():
    """Open before the native manipulator command enters Maya's undo queue."""
    if is_active():
        _session.open_manip_chunk()
        _session.gesture = True
        _session.time_change_cooldown = 0
        _session.awaiting_real_manipulation = False


@contextmanager
def gesture():
    """Use the same gesture transaction for scripts and native manipulators."""
    begin_gesture()
    session = _session
    try:
        yield session.proxy.name()
    except Exception:
        session.close_manip_chunk()
        session.gesture = False
        cmds.undo()
        with _unrecorded():
            session.recache()
        raise
    else:
        session.tick(force=True)
        session.finish()


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
            _session._sync_refs()
            data = kernel.load_offsets()
            ids = sorted(cmds.ls(_session.sel, uuid=True) or [])
            data["single_offsets"] = {name: record for name, record in data["single_offsets"].items()
                                      if not isinstance(record, dict) or record.get("object_ids") != ids}
            data["multi_offsets"] = [group for group in data["multi_offsets"]
                                     if group.get("offset_data", {}).get("object_ids") != ids]
            kernel.save_offsets(data)
            if _session.mode in ("standard", "last"):
                cmds.matchTransform(_session.proxy.name(), _session.anchor.name(), pos=True, rot=True)
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
