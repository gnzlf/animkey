"""Check material geometry and Windows blur on Qt6 without launching Maya."""

import os
from pathlib import Path
import sys
from types import ModuleType
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
maya = ModuleType("maya")
cmds = ModuleType("maya.cmds")
cmds.about = lambda **kwargs: "2025"
maya.cmds = cmds
sys.modules.update({"maya": maya, "maya.cmds": cmds})

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
from AnimKey.core.window_style import apply_window_style
from AnimKey.core.settings_style import apply_native_backdrop

app = QtWidgets.QApplication([])
window = QtWidgets.QWidget()
window.resize(420, 280)
surface = apply_window_style(window, defer_polish=True)
window.ensurePolished()
layout = QtWidgets.QVBoxLayout(window)
layout.setContentsMargins(20, 20, 20, 20)
button = QtWidgets.QPushButton("A tool action")
button.setStyleSheet("background: red;")
button.setCheckable(True)
layout.addWidget(button)
window.show()
app.processEvents()
assert button.styleSheet() == ""
assert not window.mask().contains(QtCore.QPoint(0, 0))
assert window.mask().contains(window.rect().center())
assert surface.active, "Native acrylic must be active for this Windows test"
handle = int(window.winId())
assert window.property("nativeBlurHandle") == handle
for _ in range(3):
    window.hide()
    window.show()
    app.processEvents()
    assert int(window.winId()) == handle
    assert apply_native_backdrop(window)
    assert apply_window_style(window) is surface
window.resize(520, 380)
app.processEvents()
assert window.mask().boundingRect().width() >= 518
image = window.grab().toImage()
assert image.pixelColor(0, 0).alpha() == 0
assert image.pixelColor(window.rect().center()).alpha() > 0

# Query the actual HWND region, not only Qt's cached mask. This verifies that
# the compositor cannot draw acrylic in the square exterior corners.
import ctypes
from ctypes import wintypes

gdi32 = ctypes.WinDLL("gdi32")
user32 = ctypes.WinDLL("user32")
gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
gdi32.CreateRectRgn.restype = wintypes.HANDLE
gdi32.PtInRegion.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_int]
gdi32.PtInRegion.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
region = gdi32.CreateRectRgn(0, 0, 0, 0)
try:
    assert user32.GetWindowRgn(handle, region) > 0
    assert not gdi32.PtInRegion(region, 0, 0)
    assert gdi32.PtInRegion(region, window.width() // 2, window.height() // 2)
finally:
    gdi32.DeleteObject(region)

# Exercise top/bottom pointers and detachment on the same native surface.
window._tail_height = 10
window._tail_width = 16
window._tail_x = window.width() // 2
window._magnet_attached = True
for top in (True, False):
    window._tail_on_top = top
    window.repaint()
    app.processEvents()
    y = 2 if top else window.height() - 3
    assert window.mask().contains(QtCore.QPoint(window.width() // 2, y))
window._magnet_attached = False
window.repaint()
app.processEvents()
assert window.mask().contains(QtCore.QPoint(window.width() // 2, window.height() - 3))
window.close()
print("PASS Qt {}: acrylic, native rounded region, reopening, resizing and pointers".format(QtCore.qVersion()))

from types import SimpleNamespace
from AnimKey.core.toolbar_style import apply_toolbar_style
from AnimKey.mods.maya_compat import is_qt_object_valid

owner = QtWidgets.QWidget()
owner.setGeometry(100, 100, 700, 100)
layout = QtWidgets.QHBoxLayout(owner)
layout.setContentsMargins(0, 0, 0, 0)
strip = QtWidgets.QFrame(owner)
strip.setObjectName("AnimKeyToolbarPanStrip")
strip.setFixedHeight(44)
layout.addWidget(strip)
content = QtWidgets.QWidget(strip)
content.setObjectName("toolbar_content")
toolbar = SimpleNamespace(content_widget=content, toolbar_scroll_area=strip)
owner.show()
app.processEvents()
apply_toolbar_style(toolbar)
app.processEvents()
app.processEvents()
glass = toolbar._material_glass
assert glass.surface.active and glass.isVisible()
assert strip.parentWidget() is glass
assert not owner.property("nativeBlurBackend")
owner.move(130, 150)
app.processEvents()
app.processEvents()
assert glass.geometry().topLeft() == glass.anchor.mapToGlobal(QtCore.QPoint())
owner.hide()
app.processEvents()
assert not glass.isVisible()
owner.deleteLater()
QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
assert not is_qt_object_valid(glass)
print("PASS Qt {}: native toolbar glass, alignment, visibility and ownership cleanup".format(QtCore.qVersion()))
