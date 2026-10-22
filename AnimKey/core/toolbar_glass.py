"""An owned native glass surface aligned to Maya's docked toolbar slot.

Windows acrylic needs its own top-level surface. A dock placeholder lets Maya
keep laying out the toolbar, while this surface hosts the existing interactive
strip. Maya's main window never receives a blur effect.
"""

from AnimKey.mods.maya_compat import QtCore, QtWidgets, is_qt_object_valid, get_maya_main_window
from AnimKey.core.window_style import apply_window_style


class ToolbarGlass(QtWidgets.QWidget):
    def __init__(self, strip, anchor):
        # The dock slot owns the surface in C++. Calling Python from its
        # destroyed signal re-enters Maya's partially destroyed QWidget tree.
        super().__init__(anchor, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("AnimKey_ToolbarGlass")
        self.setProperty("animkeyPersistentSurface", True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.anchor = anchor
        self.strip = strip
        self._watched = []
        self._pending = False
        self._disposed = False
        self._sync_timer = QtCore.QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self.sync)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        strip.setParent(self)
        layout.addWidget(strip)
        self.surface = apply_window_style(self, polish=False)
        self._watch_ancestors()
        self.sync()

    def _watch_ancestors(self):
        chain = []
        widget = self.anchor
        while is_qt_object_valid(widget):
            chain.append(widget)
            widget = widget.parentWidget()
        if chain == self._watched:
            return
        for widget in self._watched:
            if is_qt_object_valid(widget):
                widget.removeEventFilter(self)
        self._watched = chain
        for widget in chain:
            widget.installEventFilter(self)

    def schedule_sync(self):
        if not self._pending and not self._disposed:
            self._pending = True
            # An owned timer is cancelled by C++ destruction of the dock.
            self._sync_timer.start(0)

    @QtCore.Slot()
    def sync(self):
        self._pending = False
        if not is_qt_object_valid(self) or self._disposed or not is_qt_object_valid(self.anchor):
            return
        self._watch_ancestors()
        owner = self.anchor.window()
        if not self.anchor.isVisible() or owner.isMinimized():
            self.hide()
            return
        rect = QtCore.QRect(self.anchor.mapToGlobal(QtCore.QPoint(0, 0)), self.anchor.size())
        if self.geometry() != rect:
            self.setGeometry(rect)
        if not self.isVisible():
            self.show()
            self.raise_()
        handle = self.windowHandle()
        owner_handle = owner.windowHandle()
        if handle is not None and owner_handle is not None and handle.transientParent() is not owner_handle:
            handle.setTransientParent(owner_handle)
        self.strip.show()

    def refresh_material(self):
        if self._disposed or not is_qt_object_valid(self):
            return
        self.surface.eventFilter(self, QtCore.QEvent(QtCore.QEvent.Show))
        self.update()

    def eventFilter(self, watched, event):
        if self._disposed:
            return False
        kind = event.type()
        if kind == QtCore.QEvent.ParentChange:
            self.schedule_sync()
        elif kind in (QtCore.QEvent.Move, QtCore.QEvent.Resize, QtCore.QEvent.Show,
                      QtCore.QEvent.Hide, QtCore.QEvent.WindowStateChange,
                      QtCore.QEvent.LayoutRequest):
            self.schedule_sync()
        return False

    def dispose(self):
        if self._disposed or not is_qt_object_valid(self):
            return
        self._disposed = True
        self._sync_timer.stop()
        self._pending = False
        for widget in self._watched:
            if is_qt_object_valid(widget):
                widget.removeEventFilter(self)
        self._watched = []
        self.close()


def install_toolbar_glass(toolbar):
    # Maya tracks workspace controls through its native QWidget hierarchy.
    # Rehosting the strip in a separate tool HWND invalidates those references
    # when a dock floats/closes, causing Qt5/shiboken access violations. Keep
    # Maya's controls in their dock; the shared translucent QSS still applies.
    # Standalone Qt previews can safely exercise the native material surface.
    if get_maya_main_window() is not None:
        return None
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
