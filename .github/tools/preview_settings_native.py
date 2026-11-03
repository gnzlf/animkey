"""Render only our own diagnostic windows to verify the Windows compositor."""
import os
import sys
from unittest import mock

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
app.setFont(QtGui.QFont("Segoe UI", 9))

import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
from AnimKey.core import settings


class Backboard(QtWidgets.QWidget):
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#22262b"))
        for index, color in enumerate(("#258b84", "#4479b5", "#a25768", "#776db2")):
            rect = QtCore.QRect(30 + index * 210, 80, 180, 650)
            painter.fillRect(rect, QtGui.QColor(color))
        painter.setPen(QtGui.QColor("#dceef2"))
        painter.setFont(QtGui.QFont("Segoe UI", 16))
        for y in range(90, 800, 65):
            painter.drawText(50, y, "MATERIAL TEST   /   viewport   /   01 02 03 04   /   MATERIAL TEST")


mode = sys.argv[1] if len(sys.argv) > 1 else "layered"
destination = os.environ["ANIMKEY_TEST_ARTIFACTS"]
os.makedirs(destination, exist_ok=True)
back = Backboard()
back.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
back.setGeometry(80, 60, 920, 850)
back.show()
with mock.patch.object(settings.config, "get_setting", side_effect=lambda key, default=None: False if key == "check_updates" else default):
    window = settings.SettingsWindow(parent=back)
window.setGeometry(140, 100, 780, 760)
window.show()
window.raise_()
window.activateWindow()

import ctypes
from ctypes import wintypes
user32 = ctypes.WinDLL("user32")
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
for own_window in (back, window):
    user32.SetWindowPos(int(own_window.winId()), wintypes.HWND(-1), 0, 0, 0, 0, 0x43)
user32.SetForegroundWindow(int(window.winId()))

print("platform={}, backend={}, alpha={}".format(
    app.platformName(), window.property("nativeBlurBackend"),
    window.testAttribute(QtCore.Qt.WA_TranslucentBackground)), flush=True)


def capture():
    # Capture the compositor only when our topmost test window is foreground;
    # otherwise use our QWidget pixels instead of reading other applications.
    composited = user32.GetForegroundWindow() == int(window.winId())
    if not composited:
        # Foreground activation can be refused while the user works elsewhere.
        # Only capture when every sampled point still belongs to our two own
        # topmost diagnostic windows, including the rounded corners.
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        rect = window.frameGeometry()
        own_handles = (int(window.winId()), int(back.winId()))
        roots = []
        for x in (rect.left() + 2, rect.center().x(), rect.right() - 2):
            for y in (rect.top() + 2, rect.center().y(), rect.bottom() - 2):
                handle = user32.WindowFromPoint(wintypes.POINT(x, y))
                roots.append(user32.GetAncestor(handle, 2))
        composited = all(handle in own_handles for handle in roots)
    if composited:
        rect = window.frameGeometry()
        screenshot = window.screen().grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
    else:
        screenshot = window.grab()
    filename = os.path.join(destination, "settings-native-{}.png".format(mode))
    screenshot.save(filename)
    print("mode={}, native_backdrop={}, composited={}, screenshot={}".format(
        mode, window.container.backdrop_active, composited, filename), flush=True)
    window.close()
    back.close()
    app.quit()


QtCore.QTimer.singleShot(1800, capture)
(getattr(app, "exec", None) or app.exec_)()
