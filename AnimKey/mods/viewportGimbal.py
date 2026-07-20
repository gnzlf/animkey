"""
Viewport roll gimbal overlay for AnimKey.

Adds a small control in the active model panel that rolls the viewport camera.
"""

import maya.cmds as cmds
import maya.OpenMayaUI as mui

try:
    from PySide2 import QtWidgets, QtCore
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtWidgets, QtCore
    from shiboken6 import wrapInstance

from AnimKey.mods import configMod


_controller = None


def _maya_main_window():
    ptr = mui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None


def _is_model_panel(panel):
    if not panel:
        return False
    try:
        return cmds.getPanel(typeOf=panel) == "modelPanel"
    except Exception:
        return False


def _panel_is_usable(panel):
    if not _is_model_panel(panel):
        return False
    try:
        return bool(cmds.modelPanel(panel, query=True, exists=True))
    except Exception:
        return True


def _active_model_panel(preferred=None):
    candidates = []
    for query in ("underPointer", "withFocus"):
        try:
            panel = cmds.getPanel(**{query: True})
            if panel:
                candidates.append(panel)
        except Exception:
            pass

    if preferred:
        candidates.append(preferred)

    try:
        candidates.extend(cmds.getPanel(type="modelPanel") or [])
    except Exception:
        pass

    for panel in candidates:
        if _panel_is_usable(panel):
            return panel
    return None


def _panel_widget(panel):
    if not panel:
        return None
    ptr = None
    for finder in (mui.MQtUtil.findControl, mui.MQtUtil.findLayout):
        try:
            ptr = finder(panel)
            if ptr:
                break
        except Exception:
            pass
    if not ptr:
        return None
    try:
        return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        return None


def _camera_nodes(panel):
    try:
        camera = cmds.modelPanel(panel, query=True, camera=True)
    except Exception:
        return None, None

    if not camera or not cmds.objExists(camera):
        return None, None

    node_type = cmds.nodeType(camera)
    if node_type == "camera":
        shape = camera
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        transform = parents[0] if parents else None
    else:
        transform = camera
        shapes = cmds.listRelatives(transform, shapes=True, type="camera", fullPath=True) or []
        shape = shapes[0] if shapes else None
    return transform, shape


class RollSlider(QtWidgets.QSlider):
    def __init__(self, parent=None):
        super(RollSlider, self).__init__(QtCore.Qt.Horizontal, parent)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.setValue(0)
            event.accept()
            return
        super(RollSlider, self).mouseDoubleClickEvent(event)


class ViewportGimbalOverlay(QtWidgets.QFrame):
    def __init__(self, controller, parent=None):
        super(ViewportGimbalOverlay, self).__init__(parent or _maya_main_window())
        self.controller = controller
        self.setObjectName("AnimKeyViewportGimbal")
        flags = QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint
        if hasattr(QtCore.Qt, "NoDropShadowWindowHint"):
            flags |= QtCore.Qt.NoDropShadowWindowHint
        if hasattr(QtCore.Qt, "WindowDoesNotAcceptFocus"):
            flags |= QtCore.Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        if hasattr(QtCore.Qt, "WA_ShowWithoutActivating"):
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._updating = False
        self.setFixedSize(190, 58)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 8)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)

        title = QtWidgets.QLabel("VIEW ROLL")
        title.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        header.addWidget(title)
        header.addStretch()

        self.value_label = QtWidgets.QLabel("0 deg")
        self.value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        header.addWidget(self.value_label)
        layout.addLayout(header)

        self.slider = RollSlider()
        self.slider.setRange(-180, 180)
        self.slider.setValue(0)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(15)
        self.slider.setToolTip("Drag to roll the active viewport. Double click to return to 0.")
        self.slider.sliderPressed.connect(self.controller.begin_adjustment)
        self.slider.sliderReleased.connect(self.controller.end_adjustment)
        self.slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.slider)
        self.setStyleSheet("""
            QFrame#AnimKeyViewportGimbal {
                background-color: rgba(30, 30, 30, 220);
                border: 1px solid rgba(70, 70, 70, 210);
                border-radius: 8px;
            }
            QLabel {
                color: #F5F5F7;
                background: transparent;
                border: none;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0px;
            }
            QSlider {
                background: transparent;
                min-height: 18px;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #444444;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #3498DB;
                border-radius: 2px;
            }
            QSlider::add-page:horizontal {
                background: #444444;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                height: 14px;
                margin: -5px 0px;
                border-radius: 7px;
                background: #F5F5F7;
                border: 1px solid #B8C0CC;
            }
            QSlider::handle:horizontal:hover {
                background: #FFFFFF;
                border-color: #3498DB;
            }
            QToolTip {
                color: #F5F5F7;
                background-color: #252525;
                border: 1px solid #444444;
                padding: 5px 7px;
            }
        """)

    def _on_slider_changed(self, value):
        self.value_label.setText(f"{int(value)} deg")
        if self._updating:
            return
        self.controller.set_current_roll(float(value))

    def set_roll_value(self, value):
        if self.slider.isSliderDown():
            return
        self._updating = True
        try:
            self.slider.setValue(int(round(value)))
            self.value_label.setText(f"{int(round(value))} deg")
        finally:
            self._updating = False


class ViewportGimbalController(QtCore.QObject):
    def __init__(self):
        super(ViewportGimbalController, self).__init__(_maya_main_window())
        self.overlay = None
        self.panel = None
        self.panel_widget = None
        self.roll_offsets = {}
        self._drag_context = None
        self._is_adjusting = False
        self._undo_open = False
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(450)
        self.timer.timeout.connect(self.sync_overlay)

    def start(self):
        if not self.timer.isActive():
            self.timer.start()
        self.sync_overlay()

    def stop(self):
        if self.timer.isActive():
            self.timer.stop()
        if self.overlay is not None:
            try:
                self.overlay.hide()
                self.overlay.deleteLater()
            except Exception:
                pass
        self._close_undo_chunk()
        self._drag_context = None
        self._is_adjusting = False
        self.overlay = None
        self.panel = None
        self.panel_widget = None

    def sync_overlay(self):
        if self._is_adjusting:
            return

        panel = _active_model_panel(self.panel)
        panel_widget = _panel_widget(panel)
        if panel is None or panel_widget is None:
            if self.overlay is not None:
                self.overlay.hide()
            return

        if self.overlay is None or panel != self.panel or panel_widget != self.panel_widget:
            if self.overlay is not None:
                self.overlay.deleteLater()
            self.panel = panel
            self.panel_widget = panel_widget
            self.overlay = ViewportGimbalOverlay(self, _maya_main_window())

        margin = 14
        panel_toolbar_clearance = 46
        x_pos = max(8, panel_widget.width() - self.overlay.width() - margin)
        y_pos = panel_toolbar_clearance
        if panel_widget.height() < y_pos + self.overlay.height() + margin:
            y_pos = max(8, panel_widget.height() - self.overlay.height() - margin)
        target_pos = panel_widget.mapToGlobal(QtCore.QPoint(x_pos, y_pos))
        if self.overlay.pos() != target_pos:
            self.overlay.move(target_pos)
        transform, _ = _camera_nodes(panel)
        if transform:
            self.overlay.set_roll_value(self.roll_offsets.get(transform, 0.0))
        if self._is_blocked_by_tool_window():
            self.overlay.hide()
        elif not self.overlay.isVisible():
            self.overlay.show()

    def _current_camera(self):
        if self._drag_context:
            return self._drag_context.get("transform"), self._drag_context.get("shape")
        panel = _active_model_panel(self.panel)
        if not panel:
            return None, None
        return _camera_nodes(panel)

    def _is_blocked_by_tool_window(self):
        if self.overlay is None:
            return False

        app = QtWidgets.QApplication.instance()
        if app is None:
            return False

        overlay_rect = self.overlay.frameGeometry()
        maya_window = _maya_main_window()
        for widget in app.topLevelWidgets():
            if widget is self.overlay or widget is maya_window:
                continue
            try:
                if not widget.isVisible() or widget.isMinimized():
                    continue
                flags = widget.windowFlags()
                if flags & QtCore.Qt.ToolTip:
                    continue
                if widget.frameGeometry().intersects(overlay_rect):
                    return True
            except RuntimeError:
                pass
            except Exception:
                pass
        return False

    def _capture_camera_state(self, transform, shape):
        data = {"matrix": None, "film_roll": None}
        if not transform:
            return data
        try:
            data["matrix"] = cmds.xform(transform, query=True, worldSpace=True, matrix=True)
        except Exception:
            pass
        try:
            if shape and cmds.attributeQuery("filmRollValue", node=shape, exists=True):
                data["film_roll"] = cmds.getAttr(shape + ".filmRollValue")
        except Exception:
            pass
        return data

    def _close_undo_chunk(self):
        if not self._undo_open:
            return
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        self._undo_open = False

    def begin_adjustment(self):
        self._is_adjusting = True
        transform, shape = self._current_camera()
        if transform:
            self._drag_context = {
                "transform": transform,
                "shape": shape,
                "state": self._capture_camera_state(transform, shape),
                "roll": float(self.roll_offsets.get(transform, 0.0)),
            }
        if not self._undo_open:
            try:
                cmds.undoInfo(openChunk=True)
                self._undo_open = True
            except Exception:
                self._undo_open = False

    def end_adjustment(self):
        self._close_undo_chunk()
        self._drag_context = None
        self._is_adjusting = False
        QtCore.QTimer.singleShot(80, self.sync_overlay)

    def set_current_roll(self, degrees):
        transform, shape = self._current_camera()
        if not transform:
            cmds.warning("AnimKey: No active viewport camera found.")
            return

        start_roll = float(self.roll_offsets.get(transform, 0.0))
        data = None
        if self._drag_context and self._drag_context.get("transform") == transform:
            data = self._drag_context.get("state")
            start_roll = float(self._drag_context.get("roll", start_roll))
        if data is None:
            data = self._capture_camera_state(transform, shape)

        delta = float(degrees) - start_roll
        applied = False

        try:
            if data.get("matrix"):
                cmds.xform(transform, worldSpace=True, matrix=data["matrix"])
                if abs(delta) > 0.0001:
                    cmds.rotate(0, 0, delta, transform, relative=True, objectSpace=True)
                applied = True
        except Exception:
            applied = False

        if not applied:
            try:
                if shape and cmds.attributeQuery("filmRollValue", node=shape, exists=True):
                    base_roll = data.get("film_roll") or 0.0
                    cmds.setAttr(shape + ".filmRollValue", base_roll + delta)
                    applied = True
            except Exception:
                applied = False

        if applied:
            self.roll_offsets[transform] = float(degrees)
            try:
                cmds.refresh(currentView=True)
            except Exception:
                pass

    def roll_current(self, degrees):
        transform, shape = self._current_camera()
        if not transform:
            cmds.warning("AnimKey: No active viewport camera found.")
            return
        current = self.roll_offsets.get(transform, 0.0)
        self.set_current_roll(max(-180.0, min(180.0, current + degrees)))
        if self.overlay is not None:
            self.overlay.set_roll_value(self.roll_offsets.get(transform, 0.0))

    def reset_current(self):
        transform, shape = self._current_camera()
        if not transform:
            return

        self.set_current_roll(0.0)
        self.roll_offsets[transform] = 0.0
        if self.overlay is not None:
            self.overlay.set_roll_value(0.0)


def apply(enabled=None):
    global _controller
    if enabled is None:
        enabled = bool(configMod.get_setting("viewport_roll_gimbal_enabled", False))

    if enabled:
        if _controller is None:
            _controller = ViewportGimbalController()
        _controller.start()
    elif _controller is not None:
        _controller.stop()
        _controller = None


def is_enabled():
    return _controller is not None
