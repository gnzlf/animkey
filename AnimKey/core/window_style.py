"""Apply Settings' material to tool windows without changing their behavior.

The native hit region and the painted surface share a rounded silhouette.
Glass is painted inside that alpha surface; no separate DWM backdrop can
fill the rectangular canvas behind it.
"""

import re

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
from AnimKey.core.settings_style import STYLESHEET, GlassPanel, apply_native_backdrop
from AnimKey.core.material_backdrop import rounded_path, sync_native_region, capture_widget_backdrop, paint_material


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
        elif widget.styleSheet() and not isinstance(widget, GlassPanel):
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
    """One smooth rounded body, including when a popup is magnetically anchored."""
    return rounded_path(window.rect())


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
        self._path = window_path(window)
        self.active = False
        self._backdrop_image = None
        self._capturing = False
        self._capture_timer = QtCore.QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.timeout.connect(self._capture_backdrop)
        window.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        window.setWindowFlags(window.windowFlags() | QtCore.Qt.FramelessWindowHint
                              | QtCore.Qt.NoDropShadowWindowHint)
        window.setProperty("materialSurface", True)
        window.installEventFilter(self)

    def _sync_region(self, force=False):
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
        if force or self.window.mask() != self._region:
            self.window.setMask(self._region)
        sync_native_region(self.window, force=force)

    @QtCore.Slot()
    def _capture_backdrop(self):
        if self.active or not self.window.isVisible() or self._capturing:
            return
        self._capturing = True
        try:
            self._backdrop_image = capture_widget_backdrop(self.window)
            self.window.setProperty('materialBackdropMode',
                                    'portable' if self._backdrop_image is not None else 'translucent')
            if self.panel is not None:
                self.panel.backdrop_image = self._backdrop_image
                self.panel.update()
            self.window.update()
        finally:
            self._capturing = False

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
            self.active = apply_native_backdrop(watched)
            # Reapply after composition setup: the HWND may have been recreated
            # while Qt still reports the same cached mask.
            self._sync_region(force=True)
            watched.setProperty('materialBackdropMode', 'native' if self.active else 'translucent')
            if not self.active:
                self._capture_timer.start(0)
            if self.panel is not None:
                self.panel.backdrop_active = self.active
                self.panel.update()
        elif kind == QtCore.QEvent.Resize:
            self._sync_region()
            if not self.active and watched.isVisible():
                self._capture_timer.start(100)
        elif kind == QtCore.QEvent.Move:
            if not self.active and watched.isVisible() and not self._capturing:
                self._capture_timer.start(100)
        elif kind == QtCore.QEvent.Hide:
            self._capture_timer.stop()
            self._backdrop_image = None
            if self.panel is not None:
                self.panel.backdrop_image = None
        elif kind in tuple(value for value in (
                getattr(QtCore.QEvent, 'ScreenChangeInternal', None),
                getattr(QtCore.QEvent, 'DevicePixelRatioChange', None)) if value is not None):
            self._sync_region(force=True)
        elif kind == QtCore.QEvent.Paint:
            if self.paint:
                painter = QtGui.QPainter(watched)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
                painter.fillRect(event.rect(), QtCore.Qt.transparent)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                paint_material(painter, self._path, self.active, self._backdrop_image)
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
