"""Apply Settings' material to tool windows without changing their behavior.

The native hit region and the painted surface share a rounded silhouette.
Glass is painted inside that alpha surface; no separate DWM backdrop can
fill the rectangular canvas behind it.
"""

import re

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
from AnimKey.core.settings_style import STYLESHEET, GlassPanel, apply_native_backdrop
from AnimKey.core.material_backdrop import rounded_path, sync_native_region, capture_application_backdrop, paint_material


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
    """Owned glass updates and proximity fade, without application-wide filters."""

    IDLE_OPACITY = 0.5
    NEAR_MARGIN = 28
    CAPTURE_SETTLE_MS = 180

    def __init__(self, window, polish=True, paint=True, panel=None, compact=True,
                 defer_polish=False, auto_fade=True):
        super().__init__(window)
        self.window = window
        self.polish = polish
        self.paint = paint
        self.panel = panel
        self.compact = compact
        self.defer_polish = defer_polish
        self.auto_fade = auto_fade
        self._styled = False
        self._region = None
        self._shape_key = None
        self._path = window_path(window)
        self.active = False
        self._backdrop_image = None
        self._backdrop_cache = None
        self._reproject_count = 0
        self._capturing = False
        self._capture_count = 0
        self._capture_duration_ms = 0
        self._opacity_target = None
        self._capture_timer = QtCore.QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.timeout.connect(self._capture_backdrop)
        self._proximity_timer = QtCore.QTimer(self)
        self._proximity_timer.setInterval(80)
        self._proximity_timer.timeout.connect(self._update_proximity)
        self._opacity_animation = QtCore.QPropertyAnimation(window, b'windowOpacity', self)
        self._opacity_animation.setDuration(150)
        self._opacity_animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        window.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        window.setWindowFlags(window.windowFlags() | QtCore.Qt.FramelessWindowHint
                              | QtCore.Qt.NoDropShadowWindowHint)
        window.setProperty("materialSurface", True)
        window.installEventFilter(self)

    def _schedule_capture(self, immediate=False):
        if self.active or self._capturing or not self.window.isVisible():
            return
        # Restart after every move/resize: never read Maya's GPU while dragging.
        # The cached, already blurred owner supplies the moving glass instead.
        self._capture_timer.start(0 if immediate else self.CAPTURE_SETTLE_MS)

    def _reproject_backdrop(self):
        if self._backdrop_cache is None:
            return
        self._backdrop_image = self._backdrop_cache.render(self.window)
        self._reproject_count += 1
        if self.panel is not None:
            self.panel.backdrop_image = self._backdrop_image
            self.panel.update()
        self.window.update()

    def _belongs_to_window(self, widget):
        while widget is not None:
            if widget is self.window:
                return True
            widget = widget.parentWidget()
        return False

    def _wants_full_opacity(self):
        if not self.auto_fade or self.window.isModal():
            return True
        rect = self.window.frameGeometry().adjusted(-self.NEAR_MARGIN, -self.NEAR_MARGIN,
                                                    self.NEAR_MARGIN, self.NEAR_MARGIN)
        return (rect.contains(QtGui.QCursor.pos())
                or self._belongs_to_window(QtWidgets.QWidget.mouseGrabber())
                or self._belongs_to_window(QtWidgets.QApplication.activePopupWidget()))

    @QtCore.Slot()
    def _update_proximity(self, animate=True):
        if not self.window.isVisible():
            return
        target = 1.0 if self._wants_full_opacity() else self.IDLE_OPACITY
        if target == self._opacity_target:
            return
        self._opacity_target = target
        self._opacity_animation.stop()
        if animate:
            self._opacity_animation.setStartValue(self.window.windowOpacity())
            self._opacity_animation.setEndValue(target)
            self._opacity_animation.start()
        else:
            self.window.setWindowOpacity(target)

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
        # A held drag can pause longer than the debounce interval. Wait for
        # release as well, including native/custom title-bar mouse grabs.
        if QtWidgets.QApplication.mouseButtons() != QtCore.Qt.NoButton:
            self._capture_timer.start(self.CAPTURE_SETTLE_MS)
            return
        self._capturing = True
        elapsed = QtCore.QElapsedTimer()
        elapsed.start()
        try:
            self._backdrop_cache = capture_application_backdrop(self.window)
            self._reproject_backdrop()
            self._capture_count += 1
            self.window.setProperty('materialBackdropMode',
                                    'portable' if self._backdrop_image is not None else 'translucent')
        finally:
            self._capture_duration_ms = elapsed.elapsed()
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
            self._schedule_capture(immediate=True)
            self._opacity_target = None
            self._update_proximity(animate=False)
            if self.auto_fade and not watched.isModal():
                self._proximity_timer.start()
            if self.panel is not None:
                self.panel.backdrop_active = self.active
                self.panel.update()
        elif kind == QtCore.QEvent.Resize:
            self._sync_region()
            self._reproject_backdrop()
            self._schedule_capture()
        elif kind == QtCore.QEvent.Move:
            self._reproject_backdrop()
            self._schedule_capture()
        elif kind in (QtCore.QEvent.Enter, QtCore.QEvent.Leave):
            if self.auto_fade and not watched.isModal():
                self._update_proximity()
                # Legacy tool handlers animated both states to 1.0. The shared
                # material now owns the one opacity animation for the window.
                return True
        elif kind == QtCore.QEvent.Hide:
            self._capture_timer.stop()
            self._proximity_timer.stop()
            self._opacity_animation.stop()
            self._opacity_target = None
            self._backdrop_image = None
            self._backdrop_cache = None
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
