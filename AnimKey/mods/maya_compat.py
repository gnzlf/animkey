"""Shared Maya and Qt compatibility helpers for AnimKey.

Maya 2022-2024 ship Qt5/PySide2, while Maya 2025 and newer ship
Qt6/PySide6.  Keep the binding decision in one place so a tool never imports
the wrong Qt family after Maya's Qt6 migration.
"""

import re
import sys

import maya.cmds as cmds


try:
    _MAYA_MAJOR = int(re.match(r"(\d{4})", str(cmds.about(version=True))).group(1))
except Exception:
    _MAYA_MAJOR = 0

if _MAYA_MAJOR >= 2025:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        import shiboken6 as shiboken
        from shiboken6 import wrapInstance as wrap_instance
        PYSIDE_MAJOR = 6
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
        import shiboken2 as shiboken
        from shiboken2 import wrapInstance as wrap_instance
        PYSIDE_MAJOR = 2
else:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
        import shiboken2 as shiboken
        from shiboken2 import wrapInstance as wrap_instance
        PYSIDE_MAJOR = 2
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets
        import shiboken6 as shiboken
        from shiboken6 import wrapInstance as wrap_instance
        PYSIDE_MAJOR = 6


Qt = QtCore.Qt
QApplication = QtWidgets.QApplication
QTimer = QtCore.QTimer
QColor = QtGui.QColor

SUPPORTED_MAYA_VERSIONS = tuple(range(2022, 2028))


def maya_version():
    """Return Maya's major release as an integer, or ``None`` when unknown."""
    try:
        value = str(cmds.about(version=True))
    except Exception:
        return None
    match = re.match(r"(\d{4})", value)
    return int(match.group(1)) if match else None


def is_supported_maya():
    """Whether the active Maya release is in AnimKey's tested range."""
    return maya_version() in SUPPORTED_MAYA_VERSIONS


def runtime_info():
    """Expose concise diagnostic data for the installer and support reports."""
    return {
        "maya_version": maya_version(),
        "python_version": "{}.{}.{}".format(*sys.version_info[:3]),
        "pyside_major": PYSIDE_MAJOR,
        "qt_version": QtCore.qVersion(),
        "supported": is_supported_maya(),
    }


def warn_if_unsupported():
    """Warn without preventing newer Maya releases from attempting to run."""
    info = runtime_info()
    if info["supported"]:
        return True
    try:
        cmds.warning(
            "AnimKey: Maya {} is outside the tested 2022-2027 range. "
            "Continuing with the {} compatibility layer.".format(
                info["maya_version"] or "unknown", "PySide6" if PYSIDE_MAJOR == 6 else "PySide2"
            )
        )
    except Exception:
        pass
    return False


def get_maya_main_window():
    """Return Maya's main window wrapped by the active PySide binding."""
    try:
        import maya.OpenMayaUI as omui

        pointer = omui.MQtUtil.mainWindow()
        return wrap_instance(int(pointer), QtWidgets.QWidget) if pointer else None
    except Exception:
        return None


def is_qt_object_valid(widget):
    """Safely test a wrapped QObject after Maya has closed or deleted it."""
    if widget is None:
        return False
    try:
        return bool(shiboken.isValid(widget))
    except Exception:
        try:
            widget.objectName()
            return True
        except Exception:
            return False


def execute_qt(target, *args, **kwargs):
    """Call Qt's Qt6 ``exec`` or Qt5 ``exec_`` spelling as appropriate."""
    execute = getattr(target, "exec", None) or getattr(target, "exec_", None)
    if execute is None:
        raise AttributeError("Qt object does not provide exec/exec_")
    return execute(*args, **kwargs)


def screen_available_geometry(widget=None, point=None):
    """Return usable screen geometry without Qt5's removed desktop API."""
    screen = None
    if widget is not None:
        try:
            screen = widget.screen()
        except Exception:
            pass
    if screen is None and point is not None:
        try:
            screen_at = getattr(QtGui.QGuiApplication, "screenAt", None)
            if screen_at is not None:
                screen = screen_at(point)
        except Exception:
            pass
    if screen is None:
        try:
            screen = QtGui.QGuiApplication.primaryScreen()
        except Exception:
            pass
    if screen is not None:
        try:
            return screen.availableGeometry()
        except Exception:
            pass

    # Last-resort Qt5 fallback. This branch is never needed on Qt6, where
    # QApplication.desktop() was removed.
    try:
        desktop = getattr(QtWidgets.QApplication, "desktop", None)
        if desktop is not None:
            return desktop().availableGeometry(widget)
    except Exception:
        pass
    return QtCore.QRect(0, 0, 1920, 1080)
