"""Apply Settings' material to tool windows without changing their behavior.

The native window region and the painted surface use the same rounded path.
This also clips Windows' acrylic, which otherwise fills the rectangular HWND
even where Qt paints transparent pixels.
"""

import re

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
from AnimKey.core.settings_style import STYLESHEET, apply_native_backdrop, surface_fill, surface_outline


COMPACT_STYLESHEET = STYLESHEET + """
QWidget { font-size: 11px; }
QPushButton, QToolButton { padding: 3px 8px; min-height: 18px; }
QPushButton[role="icon"], QToolButton[role="icon"] { padding: 0; min-height: 0; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { padding: 3px 7px; min-height: 16px; }
QTabBar::tab { padding: 5px 10px; margin: 3px 2px; }
QTableWidget::item { padding: 3px; }
"""


def polish_tool_widgets(root):
    """Remove legacy chrome once; retain color swatches and custom data views."""
    preserved = {"SetButton", "FlashButton", "ColorFilterDot", "DrawingCanvas",
                 "AnimationLibraryCard", "TabButton"}

    def visit(widget):
        if widget.property("preserveMaterialStyle") or type(widget).__name__ in preserved:
            return
        # Keep labels' typography and status colors. They do not draw a window
        # background; structural frames and stock controls inherit the material.
        if isinstance(widget, QtWidgets.QLabel) and widget.styleSheet():
            def readable_neutral(match):
                color = QtGui.QColor(match.group(1))
                channels = color.getRgb()[:3]
                if max(channels) - min(channels) <= 12 and max(channels) < 170:
                    return "color: #b6c0cf"
                return match.group(0)
            widget.setStyleSheet(re.sub(r"(?<![-\w])color:\s*(#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})(?=[;\s}])",
                                       readable_neutral, widget.styleSheet()))
        elif widget.styleSheet():
            widget.setStyleSheet("")
        if type(widget) in (QtWidgets.QWidget, QtWidgets.QFrame, QtWidgets.QStackedWidget):
            if not isinstance(widget, QtWidgets.QFrame) or widget.frameShape() not in (
                QtWidgets.QFrame.HLine, QtWidgets.QFrame.VLine
            ):
                widget.setProperty("materialContainer", True)
                widget.setAutoFillBackground(False)
        if isinstance(widget, QtWidgets.QAbstractButton):
            widget.setCursor(QtCore.Qt.PointingHandCursor)
            name = widget.objectName().lower()
            text = widget.text().strip()
            if not widget.property("role"):
                if name in ("primarybutton", "savebtn", "keepbutton"):
                    widget.setProperty("role", "primary")
                elif name in ("deletebutton", "removebutton") or text.startswith(("Delete", "Uninstall")):
                    widget.setProperty("role", "danger")
                elif widget.maximumWidth() <= 36:
                    widget.setProperty("role", "icon")
        for child in widget.children():
            if isinstance(child, QtWidgets.QWidget) and not child.isWindow():
                visit(child)

    visit(root)


def window_path(window):
    """Rounded body, optionally including a tool's existing anchor pointer."""
    tail = getattr(window, "_tail_height", 0)
    attached = getattr(window, "_magnet_attached", True)
    tail = tail if attached else 0
    top = bool(tail and getattr(window, "_tail_on_top", False))
    rect = QtCore.QRectF(window.rect()).adjusted(0.7, tail + 0.7 if top else 0.7,
                                              -0.7, -0.7 if top else -tail - 0.7)
    path = QtGui.QPainterPath()
    path.addRoundedRect(rect, 16, 16)
    if tail:
        center = max(24, min(getattr(window, "_tail_x", window.width() // 2), window.width() - 24))
        half = getattr(window, "_tail_width", 16) / 2.0
        base = rect.top() if top else rect.bottom()
        pointer = QtGui.QPainterPath()
        pointer.moveTo(center - half, base + (1 if top else -1))
        pointer.lineTo(center, base - tail if top else base + tail)
        pointer.lineTo(center + half, base + (1 if top else -1))
        pointer.closeSubpath()
        path = path.united(pointer)
    return path


class MaterialSurface(QtCore.QObject):
    """A per-window painter; no application-wide filters or repaint timers."""

    def __init__(self, window, polish=True, paint=True, panel=None, compact=True,
                 defer_polish=False):
        super().__init__(window)
        self.window = window
        self.polish = polish
        self.paint = paint
        self.panel = panel
        self.compact = compact
        self.defer_polish = defer_polish
        self._styled = False
        self._region = None
        self._shape_key = None
        self._path = None
        self.active = False
        window.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        window.setWindowFlags(window.windowFlags() | QtCore.Qt.FramelessWindowHint
                              | QtCore.Qt.NoDropShadowWindowHint)
        window.setProperty("materialSurface", True)
        window.installEventFilter(self)

    def _sync_region(self):
        key = (self.window.width(), self.window.height(),
               getattr(self.window, "_tail_height", 0),
               getattr(self.window, "_tail_width", 16),
               getattr(self.window, "_tail_x", 0),
               getattr(self.window, "_tail_on_top", False),
               getattr(self.window, "_magnet_attached", True))
        if key != self._shape_key:
            self._path = window_path(self.window)
            self._region = QtGui.QRegion(self._path.toFillPolygon().toPolygon())
            self._shape_key = key
        if self.window.mask() != self._region:
            self.window.setMask(self._region)

    def eventFilter(self, watched, event):
        kind = event.type()
        # Maya can polish a child of its main window while the subclass is
        # still constructing its controls. Wait for Show so legacy local QSS
        # added later in __init__ cannot override the shared material palette.
        if kind == QtCore.QEvent.Show or (kind == QtCore.QEvent.Polish and not self.defer_polish):
            if not self._styled:
                self._styled = True
                if self.polish:
                    polish_tool_widgets(watched)
                    watched.setStyleSheet(COMPACT_STYLESHEET if self.compact else STYLESHEET)
        if kind == QtCore.QEvent.Show:
            self._sync_region()
            self.active = apply_native_backdrop(watched)
            if self.panel is not None:
                self.panel.backdrop_active = self.active
                self.panel.update()
        elif kind == QtCore.QEvent.Resize:
            self._sync_region()
        elif kind == QtCore.QEvent.Paint:
            # Tail placement can change without a resize when a popup detaches.
            self._sync_region()
            if self.paint:
                painter = QtGui.QPainter(watched)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
                painter.fillRect(event.rect(), QtCore.Qt.transparent)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                painter.setRenderHint(QtGui.QPainter.Antialiasing)
                path = self._path
                painter.fillPath(path, surface_fill(self.active))
                painter.setPen(QtGui.QPen(surface_outline(), 1.0))
                painter.drawPath(path)
                painter.end()
                return True
        return False


def apply_window_style(window, **options):
    """Install before first show, after the window's controls have been built."""
    surface = getattr(window, "_material_surface", None)
    if surface is None:
        surface = MaterialSurface(window, **options)
        window._material_surface = surface
    return surface
