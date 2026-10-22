"""Test the docked toolbar's actual Windows acrylic surface and lifecycle."""

import os
from pathlib import Path
import sys
from types import SimpleNamespace

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
output = root / "dist" / "frosted-toolbar-verification"
output.mkdir(parents=True, exist_ok=True)
os.environ["MAYA_APP_DIR"] = str(output / "maya-user")
from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets, is_qt_object_valid
app = QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
app.setFont(QtGui.QFont("Segoe UI", 9))
import maya.standalone
maya.standalone.initialize(name="python")
from AnimKey.core.toolbar_style import apply_toolbar_style
from AnimKey.mods import uiMod


class Backboard(QtWidgets.QWidget):
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        colors = ("#9f5141", "#347285", "#56784d", "#6d5495")
        for x in range(0, self.width(), 120):
            painter.fillRect(x, 0, 120, self.height(), QtGui.QColor(colors[(x // 120) % 4]))
        painter.setPen(QtGui.QColor("white"))
        for x in range(0, self.width(), 18):
            painter.drawLine(x, 0, x, self.height())


owner = Backboard()
owner.setGeometry(140, 170, 1120, 320)
owner.setWindowTitle("AnimKey own glass test backdrop")
layout = QtWidgets.QVBoxLayout(owner)
layout.addStretch()
workspace = QtWidgets.QWidget(owner)
workspace.setFixedHeight(56)
layout.addWidget(workspace)
layout.addStretch()
root_layout = QtWidgets.QHBoxLayout(workspace)
root_layout.setContentsMargins(0, 0, 0, 0)
strip = QtWidgets.QFrame(workspace)
strip.setObjectName("AnimKeyToolbarPanStrip")
strip.setFixedHeight(56)
root_layout.addWidget(strip)
inner = QtWidgets.QHBoxLayout(strip)
inner.setContentsMargins(10, 5, 10, 5)
content = QtWidgets.QWidget(strip)
content.setObjectName("toolbar_content")
inner.addWidget(content)
controls = QtWidgets.QHBoxLayout(content)
controls.setContentsMargins(0, 0, 0, 0)
button = uiMod.HoverButton("Test button")
button.setFixedSize(110, 30)
controls.addWidget(button)
controls.addWidget(uiMod.DropdownLabel("Tweener"))
slider = uiMod.AnimKeySlider()
slider.setFixedWidth(260)
controls.addWidget(slider)
controls.addStretch()
toolbar = SimpleNamespace(content_widget=content, toolbar_scroll_area=strip)
owner.show()
app.processEvents()
apply_toolbar_style(toolbar)
app.processEvents()
app.processEvents()
glass = toolbar._material_glass
assert glass.isVisible() and glass.surface.active
assert not owner.property("nativeBlurBackend"), "Maya's parent must never receive acrylic"
assert strip.parentWidget() is glass
assert not uiMod._is_animkey_tool_window(glass), "Opening another tool must preserve the toolbar"
assert glass.geometry().topLeft() == glass.anchor.mapToGlobal(QtCore.QPoint())
calls = []
button.clicked.connect(lambda: calls.append(1))
button.click()
assert calls == [1]
apply_toolbar_style(toolbar)
assert toolbar._material_glass is glass

owner.move(owner.x() + 40, owner.y() + 30)
owner.resize(owner.width() + 100, owner.height())
app.processEvents()
app.processEvents()
assert glass.geometry() == QtCore.QRect(glass.anchor.mapToGlobal(QtCore.QPoint()), glass.anchor.size())
owner.hide()
app.processEvents()
assert not glass.isVisible()
owner.show()
app.processEvents()
app.processEvents()
assert glass.isVisible()
owner.showMinimized()
app.processEvents()
assert not glass.isVisible()
owner.showNormal()
app.processEvents()
app.processEvents()
assert glass.isVisible()

# Reparenting the dock slot models floating/redocking a Maya workspace.
floating = Backboard()
floating.setGeometry(220, 250, 1080, 120)
floating_layout = QtWidgets.QVBoxLayout(floating)
workspace.setParent(floating)
floating_layout.addWidget(workspace)
floating.show()
app.processEvents()
app.processEvents()
assert glass.parentWidget() is glass.anchor
assert glass.windowHandle().transientParent() is floating.windowHandle()
assert glass.geometry().topLeft() == glass.anchor.mapToGlobal(QtCore.QPoint())
workspace.setParent(owner)
layout.addWidget(workspace)
workspace.show()
app.processEvents()
app.processEvents()
assert glass.parentWidget() is glass.anchor
assert glass.windowHandle().transientParent() is owner.windowHandle()
floating.close()


def capture_and_finish():
    # Only capture this test's own surface pixels, excluding the user's apps.
    glass.grab().save(str(output / "toolbar-alpha.png"))
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    own_handles = (int(owner.winId()), int(glass.winId()))
    if user32.GetForegroundWindow() in own_handles:
        rect = glass.frameGeometry()
        glass.screen().grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).save(str(output / "toolbar-composited.png"))
        print("Captured compositor over the test's colored backdrop", flush=True)
    workspace.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    assert not is_qt_object_valid(glass), "Deleting the dock must destroy its native surface"
    owner.close()
    print("PASS native toolbar: acrylic, alignment, resize, hide/show, minimize, clicks and cleanup", flush=True)
    app.quit()


owner.raise_()
owner.activateWindow()
QtCore.QTimer.singleShot(600, capture_and_finish)
(getattr(app, "exec", None) or app.exec_)()
