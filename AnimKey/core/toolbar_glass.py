"""An owned native glass surface aligned to Maya's docked toolbar slot.

Windows acrylic needs its own top-level surface. A dock placeholder lets Maya
keep laying out the toolbar, while this surface hosts the existing interactive
strip. Maya's main window never receives a blur effect.
"""

from AnimKey.mods.maya_compat import QtCore, QtWidgets, is_qt_object_valid
from AnimKey.core.window_style import apply_window_style


class ToolbarGlass(QtWidgets.QWidget):
    def __init__(self, strip, anchor):
        super().__init__(anchor.window(), QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("AnimKey_ToolbarGlass")
        self.setProperty("animkeyPersistentSurface", True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.anchor = anchor
        self.strip = strip
        self._watched = []
        self._pending = False
        self._disposed = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        strip.setParent(self)
        layout.addWidget(strip)
        self.surface = apply_window_style(self, polish=False)
        self.anchor.destroyed.connect(self.dispose)
        self._watch_ancestors()
        self.sync()

    def _watch_ancestors(self):
        for widget in self._watched:
            if is_qt_object_valid(widget):
                widget.removeEventFilter(self)
        self._watched = []
        widget = self.anchor
        while widget is not None:
            widget.installEventFilter(self)
            self._watched.append(widget)
            widget = widget.parentWidget()

    def schedule_sync(self):
        if not self._pending and not self._disposed:
            self._pending = True
            QtCore.QTimer.singleShot(0, self.sync)

    def sync(self):
        self._pending = False
        if self._disposed or not is_qt_object_valid(self.anchor):
            return
        owner = self.anchor.window()
        if self.parentWidget() is not owner:
            self.setParent(owner, self.windowFlags())
        if not self.anchor.isVisible() or owner.isMinimized():
            self.hide()
            return
        rect = QtCore.QRect(self.anchor.mapToGlobal(QtCore.QPoint(0, 0)), self.anchor.size())
        if self.geometry() != rect:
            self.setGeometry(rect)
        if not self.isVisible():
            self.show()
            self.raise_()
        self.strip.show()

    def refresh_material(self):
        self.surface.eventFilter(self, QtCore.QEvent(QtCore.QEvent.Show))
        self.update()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QtCore.QEvent.ParentChange:
            self._watch_ancestors()
            self.schedule_sync()
        elif kind in (QtCore.QEvent.Move, QtCore.QEvent.Resize, QtCore.QEvent.Show,
                      QtCore.QEvent.Hide, QtCore.QEvent.WindowStateChange,
                      QtCore.QEvent.LayoutRequest):
            self.schedule_sync()
        return False

    def dispose(self, *unused):
        if self._disposed:
            return
        self._disposed = True
        for widget in self._watched:
            if is_qt_object_valid(widget):
                widget.removeEventFilter(self)
        self._watched = []
        self.close()


def install_toolbar_glass(toolbar):
    existing = getattr(toolbar, "_material_glass", None)
    if (existing is not None and is_qt_object_valid(existing)
            and not existing._disposed):
        existing.refresh_material()
        existing.schedule_sync()
        return existing
    app = QtWidgets.QApplication.instance()
    if app is None or app.platformName() != "windows":
        return None
    strip = toolbar.toolbar_scroll_area
    workspace = strip.parentWidget()
    if workspace is None or workspace.layout() is None:
        return None
    anchor = QtWidgets.QWidget(workspace)
    anchor.setObjectName("AnimKeyToolbarDockSlot")
    anchor.setFixedHeight(strip.height())
    anchor.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    item = workspace.layout().replaceWidget(strip, anchor)
    if item is None:
        anchor.deleteLater()
        return None
    del item
    anchor.show()
    glass = ToolbarGlass(strip, anchor)
    toolbar._material_glass = glass
    return glass
