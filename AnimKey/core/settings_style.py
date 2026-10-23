"""Shared material palette, painting and controls for AnimKey windows."""

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
from AnimKey.mods import material_palette as material
from AnimKey.core.material_backdrop import apply_native_backdrop, rounded_path, paint_material


STYLESHEET = """
QWidget { color: #e6e7eb; font-family: 'Segoe UI'; font-size: 12px; }
QWidget[materialSurface="true"] { background: transparent; }
QWidget[materialContainer="true"] { background: transparent; border: none; }
QLabel { background: transparent; border: none; }
QLabel[muted="true"] { color: #c4c8ce; }
QWidget#settingsTitleBar { background: transparent; }
QLabel#settingsTitle { font-size: 17px; font-weight: 600; color: #f4f5f7; }
QLabel#settingsSubtitle { color: #bec3ca; font-size: 11px; }
QFrame#settingsFooter { background: rgba(255,255,255,4); border-top: 1px solid rgba(255,255,255,16); border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
QTabWidget::pane { border: none; background: transparent; }
QTabWidget::tab-bar { left: 22px; }
QTabBar { background: rgba(12,16,23,60); border: 1px solid rgba(222,233,250,50); border-radius: 16px; }
QTabBar::tab {
    color: #c8ccd3; background: transparent; padding: 8px 18px;
    border: 1px solid transparent; border-radius: 12px; margin: 4px 3px;
}
QTabBar::tab:selected { color: #ffffff; border-color: rgba(235,242,255,100); background: rgba(255,255,255,30); }
QTabBar::tab:hover:!selected { color: white; background: rgba(255,255,255,18); }
QTabBar::tab:disabled { color: #656772; }
QGroupBox {
    background: transparent; border: none; border-bottom: 1px solid rgba(255,255,255,15);
    margin-top: 12px; padding-top: 12px; padding-bottom: 10px;
    font-weight: 600; color: #f0f1f4;
}
QGroupBox::title { subcontrol-origin: margin; left: 0px; padding: 0 0 5px 0; }
QPushButton, QToolButton {
    color: #e8e9ed; font-weight: 500;
    background: rgba(255,255,255,14);
    border: 1px solid rgba(222,233,250,65);
    border-radius: 8px; padding: 6px 13px; min-height: 18px;
}
QPushButton:hover, QToolButton:hover {
    background: rgba(255,255,255,30);
    border-color: rgba(235,242,255,110); color: white;
}
QPushButton:pressed, QToolButton:pressed { background: rgba(0,0,0,45); border-color: rgba(222,233,250,80); }
QPushButton:checked, QToolButton:checked { background: rgba(22,127,240,80); border-color: #70b7ff; color: white; }
QPushButton:focus, QToolButton:focus { border-color: #99cfff; }
QPushButton:disabled, QToolButton:disabled { color: #9aa4b5; background: rgba(0,0,0,20); border-color: rgba(222,233,250,25); }
QPushButton[role="primary"] {
    background: #167ff0;
    color: #ffffff; border-color: #70b7ff; font-weight: 600;
}
QPushButton[role="primary"]:hover { background: #3899ff; border-color: #b1daff; }
QPushButton[role="primary"]:pressed { background: #1266be; }
QPushButton[role="primary"]:disabled { background: rgba(22,127,240,45); color: #849cb4; border-color: #46596e; }
QPushButton[role="danger"] { color: #ffb3b6; border-color: rgba(255,132,143,65); background: rgba(255,83,105,15); }
QPushButton[role="danger"]:hover { color: white; background: rgba(255,83,105,42); border-color: #dd8a99; }
QPushButton[role="icon"] { padding: 0; min-height: 0; border-radius: 6px; background: rgba(255,255,255,8); border-color: rgba(222,233,250,40); }
QPushButton[role="icon"]:hover { background: rgba(255,255,255,28); border-color: rgba(235,242,255,110); }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: rgba(8,12,19,85);
    color: #f0f2f5; border: 1px solid rgba(255,255,255,35);
    border-radius: 7px; padding: 6px 10px; selection-background-color: #559a85;
    min-height: 18px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #8bc8ff; }
QLineEdit[picking="true"] { background: #32493f; border-color: #a7e7ca; color: #d4f5e4; }
QLineEdit:disabled { color: #757783; }
QComboBox QAbstractItemView { background: #292d35; color: #f0f2f5; border: 1px solid #626571; selection-background-color: #167ff0; }
QTextEdit, QPlainTextEdit, QListWidget, QTreeWidget { background: rgba(8,12,19,85); color: #e6e7eb; border: 1px solid rgba(222,233,250,65); border-radius: 7px; selection-background-color: #167ff0; }
QCheckBox, QRadioButton { background: transparent; spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #747b88; border-radius: 5px; background: #363c46; }
QCheckBox::indicator:checked { background: #34c759; border-color: #86e6a0; }
QRadioButton::indicator { width: 14px; height: 14px; border: 1px solid #747b88; border-radius: 8px; background: #363c46; }
QRadioButton::indicator:checked { background: #167ff0; border-color: #99cfff; }
QCheckBox:disabled, QRadioButton:disabled { color: #858a94; }
QSlider::groove:horizontal { height: 7px; border: 1px solid rgba(222,233,250,60); border-radius: 4px; background: rgba(8,12,19,100); }
QSlider::sub-page:horizontal { background: #8b9db6; border-radius: 4px; }
QSlider::handle:horizontal { width: 20px; margin: -7px 0; border-radius: 10px; border: 1px solid #e1e7f0; background: #c8d0dd; }
QSlider::handle:horizontal:hover { border-color: #b7d8ff; background: #e1e7f0; }
QSlider::handle:horizontal:disabled { background: #777d86; border-color: #777d86; }
QSpinBox::up-button, QDoubleSpinBox::up-button { width: 20px; border: none; border-left: 1px solid #454752; border-top-right-radius: 6px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { width: 20px; border: none; border-left: 1px solid #454752; border-bottom-right-radius: 6px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #535562; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 3px 0; }
QScrollBar::handle:vertical { background: #626571; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #9296a2; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QTableWidget { background: rgba(8,12,19,85); alternate-background-color: rgba(255,255,255,6); border: 1px solid rgba(222,233,250,65); border-radius: 8px; color: #e9ecf0; }
QHeaderView::section { background: rgba(255,255,255,12); color: #d3dbe7; border: none; border-bottom: 1px solid rgba(222,233,250,45); padding: 9px 6px; }
QTableWidget::item { padding: 6px; }
QToolTip { background: #3b3e48; border: 1px solid #737985; color: #f3f4f5; padding: 6px; border-radius: 4px; }
QSizeGrip { background: transparent; width: 14px; height: 14px; }
"""


def surface_fill(backdrop_active):
    return QtGui.QColor(*(material.SURFACE_RGBA if backdrop_active else material.FALLBACK_RGBA))


def surface_outline():
    return QtGui.QColor(*material.OUTLINE_RGBA)


class GlassPanel(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.backdrop_active = False
        self.backdrop_image = None
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setAutoFillBackground(False)
        self.setStyleSheet("GlassPanel { background: transparent; border: none; }")

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
        painter.fillRect(event.rect(), QtCore.Qt.transparent)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        paint_material(painter, rounded_path(self.rect()), self.backdrop_active, self.backdrop_image)


def prepare_native_backdrop(window):
    # Compatibility entry point: retain Qt alpha and disable the native canvas.
    return apply_native_backdrop(window)


class WindowControl(QtWidgets.QAbstractButton):
    """White window symbols with animated, paint-only growth."""

    def __init__(self, kind, label, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._hover = 0.0
        self._keyboard_focus = False
        self._animation = QtCore.QPropertyAnimation(self, b"hover", self)
        self._animation.setDuration(120)
        self._animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self.setFixedSize(28, 28)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def _set_hover(self, value):
        self._hover = value
        self.update()

    hover = QtCore.Property(float, lambda self: self._hover, _set_hover)

    def _animate(self, active):
        self._animation.stop()
        self._animation.setStartValue(self._hover)
        self._animation.setEndValue(1.0 if active and self.isEnabled() else 0.0)
        self._animation.start()

    def enterEvent(self, event):
        self._animate(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate(False)
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._animation.stop()
        self._set_hover(0.0)
        super().hideEvent(event)

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.EnabledChange and not self.isEnabled():
            self._animation.stop()
            self._set_hover(0.0)
        super().changeEvent(event)

    def hitButton(self, point):
        return self.rect().contains(point)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (
            QtCore.Qt.TabFocusReason, QtCore.Qt.BacktabFocusReason,
            QtCore.Qt.ShortcutFocusReason,
        )
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_focus = False
        self.update()
        super().focusOutEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.35)
        elif self.isDown():
            painter.setOpacity(0.65)
        center = QtCore.QRectF(self.rect()).center()
        if self._hover > 0:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, int(18 * self._hover)))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        if self.hasFocus() and self._keyboard_focus:
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 160), 1))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        painter.translate(center)
        scale = 1.0 + 0.15 * self._hover
        painter.scale(scale, scale)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.4))
        painter.setBrush(QtCore.Qt.NoBrush)
        if self.kind == "minimize":
            painter.drawLine(QtCore.QPointF(-5, 0), QtCore.QPointF(5, 0))
        elif self.kind == "maximize":
            painter.drawRect(QtCore.QRectF(-4.5, -4.5, 9, 9))
        elif self.kind == "close":
            painter.drawLine(QtCore.QPointF(-4.5, -4.5), QtCore.QPointF(4.5, 4.5))
            painter.drawLine(QtCore.QPointF(-4.5, 4.5), QtCore.QPointF(4.5, -4.5))


class SettingsSwitch(QtWidgets.QCheckBox):
    """Native checkbox semantics, with an animated switch and full-row hit area."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._position = 0.0
        self._animation = QtCore.QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMinimumHeight(36)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def _set_position(self, value):
        self._position = value
        self.update()

    position = QtCore.Property(float, lambda self: self._position, _set_position)

    def _animate(self, checked):
        self._animation.stop()
        target = 1.0 if checked else 0.0
        if not self.isVisible():
            self._set_position(target)
            return
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(target)
        self._animation.start()

    def hitButton(self, point):
        return self.rect().contains(point)

    def sizeHint(self):
        return QtCore.QSize(min(500, self.fontMetrics().horizontalAdvance(self.text()) + 72), 38)

    def minimumSizeHint(self):
        return QtCore.QSize(170, 38)

    def showEvent(self, event):
        self._animation.stop()
        self._set_position(1.0 if self.isChecked() else 0.0)
        super().showEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        track = QtCore.QRectF(self.width() - 47, (self.height() - 24) / 2.0, 44, 24)
        if self.underMouse() or self.hasFocus():
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, 9))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0, 1, 0, -1), 6, 6)
        painter.setPen(QtGui.QColor("#e2e4ea"))
        text_rect = self.rect().adjusted(1, 0, -64, 0)
        painter.drawText(text_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.TextWordWrap, self.text())
        off = QtGui.QColor("#454751")
        on = QtGui.QColor("#34c759")
        t = self._position
        color = QtGui.QColor(*[int(a + (b - a) * t) for a, b in zip(off.getRgb()[:3], on.getRgb()[:3])])
        painter.setBrush(color)
        painter.setPen(QtGui.QPen(color.lighter(135), 0.8))
        painter.drawRoundedRect(track, 12, 12)
        center = QtCore.QPointF(track.left() + 12 + 20 * t, track.center().y())
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 70))
        painter.drawEllipse(center + QtCore.QPointF(0, 1.4), 9.3, 9.3)
        painter.setBrush(QtGui.QColor("#f7f9fc"))
        painter.drawEllipse(center, 9.3, 9.3)
        if self.hasFocus():
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor("#b5ead8"), 1))
            painter.drawRoundedRect(track.adjusted(-3, -3, 3, 3), 15, 15)


def polish_page(root):
    """Remove legacy local QSS so Settings' material palette can inherit."""
    for widget in [root] + root.findChildren(QtWidgets.QWidget):
        if widget.styleSheet():
            widget.setStyleSheet("")
        if isinstance(widget, QtWidgets.QLabel):
            widget.setProperty("muted", True)
        if isinstance(widget, QtWidgets.QPushButton):
            text = widget.text()
            if "Uninstall" in text or "Reset" in text:
                widget.setProperty("role", "danger")
            if widget.maximumWidth() <= 28:
                widget.setProperty("role", "icon")
            widget.setCursor(QtCore.Qt.PointingHandCursor)
        if isinstance(widget, QtWidgets.QGroupBox):
            widget.layout().setContentsMargins(2, 18, 2, 12)
    root.update()
