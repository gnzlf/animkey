"""Native glass, device-pixel clipping and a Qt-only compatibility backdrop."""
import sys

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
from AnimKey.mods import material_palette as material

RADIUS = 18


def rounded_path(rect):
    path = QtGui.QPainterPath()
    path.addRoundedRect(QtCore.QRectF(rect).adjusted(.7, .7, -.7, -.7), RADIUS, RADIUS)
    return path


def _windows():
    app = QtWidgets.QApplication.instance()
    return sys.platform == 'win32' and app is not None and app.platformName() == 'windows'


def _transparency_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return bool(winreg.QueryValueEx(key, 'EnableTransparency')[0])
    except (ImportError, OSError):
        return True


def apply_native_backdrop(window):
    if not _windows():
        return False
    import ctypes
    from ctypes import wintypes

    class Accent(ctypes.Structure):
        _fields_ = [('state', ctypes.c_int), ('flags', ctypes.c_int),
                    ('tint', wintypes.DWORD), ('animation', ctypes.c_int)]

    class CompositionData(ctypes.Structure):
        _fields_ = [('attribute', ctypes.c_int), ('data', ctypes.c_void_p),
                    ('size', ctypes.c_size_t)]

    handle = int(window.winId())
    portable = bool(window.property('forcePortableMaterial')) or not _transparency_enabled()
    try:
        dwm = ctypes.WinDLL('dwmapi')
        set_attribute = dwm.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attribute.restype = ctypes.c_long
        if window.property('nativeFrameHandle') != handle:
            # Do not disable DWM non-client composition: that also suppresses
            # glass on some Windows 11 builds. Our region owns the silhouette.
            policy = ctypes.c_int(0)  # DWMNCRP_USEWINDOWSTYLE
            no_border = wintypes.DWORD(0xFFFFFFFE)
            set_attribute(handle, 2, ctypes.byref(policy), ctypes.sizeof(policy))
            set_attribute(handle, 34, ctypes.byref(no_border), ctypes.sizeof(no_border))
            window.setProperty('nativeFrameHandle', handle)
        composition = wintypes.BOOL()
        dwm.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
        if dwm.DwmIsCompositionEnabled(ctypes.byref(composition)) == 0 and not composition.value:
            portable = True
    except (AttributeError, OSError):
        pass

    try:
        compose = ctypes.WinDLL('user32').SetWindowCompositionAttribute
        compose.argtypes = [wintypes.HWND, ctypes.POINTER(CompositionData)]
        compose.restype = wintypes.BOOL
        key = (handle, material.NATIVE_TINT, portable)
        if getattr(window, '_material_accent_key', None) == key:
            return window.property('nativeBlurBackend') == 'accent'
        active = False
        mode = 'portable'
        for state in ((0,) if portable else (4, 3)):
            accent = Accent(state, 0, material.NATIVE_TINT, 0)
            data = CompositionData(19, ctypes.addressof(accent), ctypes.sizeof(accent))
            if compose(handle, ctypes.byref(data)) and state:
                active = True
                mode = 'acrylic' if state == 4 else 'blur'
                break
        window._material_accent_key = key
        window.setProperty('nativeBlurBackend', 'accent' if active else 'fallback')
        window.setProperty('nativeBlurMode', mode)
        window.setProperty('nativeBlurHandle', handle)
        window.setProperty('nativeBlurTint', material.NATIVE_TINT)
        return active
    except (AttributeError, OSError, ValueError):
        window.setProperty('nativeBlurBackend', 'fallback')
        window.setProperty('nativeBlurMode', 'portable')
        return False


def sync_native_region(window, force=False):
    """Use a curved HRGN in physical pixels, rather than a scaled Qt polygon."""
    if not _windows() or not window.testAttribute(QtCore.Qt.WA_WState_Created):
        return
    import ctypes
    from ctypes import wintypes
    try:
        user32 = ctypes.WinDLL('user32')
        gdi32 = ctypes.WinDLL('gdi32')
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        handle = int(window.winId())
        rect = wintypes.RECT()
        if not user32.GetWindowRect(handle, ctypes.byref(rect)):
            return
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return
        scale = min(width / max(1, window.width()), height / max(1, window.height()))
        diameter = max(2, round(2 * RADIUS * scale))
        key = (handle, width, height, diameter)
        if not force and getattr(window, '_material_native_region_key', None) == key:
            return
        region = gdi32.CreateRoundRectRgn(0, 0, width, height, diameter, diameter)
        if region:
            # Windows owns the region on success. Deleting it then is unsafe.
            if user32.SetWindowRgn(handle, region, False):
                window._material_native_region_key = key
            else:
                gdi32.DeleteObject(region)
    except (AttributeError, OSError, ValueError):
        pass


def capture_widget_backdrop(window):
    """Blur only the owning application's widget image; never grab the desktop."""
    source = window.parentWidget()
    if source is None:
        return None
    source = source.window()
    if source is window or not source.isVisible() or source.isMinimized():
        return None
    origin = source.mapFromGlobal(window.mapToGlobal(QtCore.QPoint()))
    area = QtCore.QRect(origin, window.size()).intersected(source.rect())
    if area.isEmpty():
        return None
    try:
        captured = source.grab(area).toImage()
        if captured.isNull():
            return None
        # A downsampled image removes sharp text/details without a continuous
        # graphics effect or a timer that repaints the scene every frame.
        small = captured.scaled(max(1, area.width() // 18), max(1, area.height() // 18),
                                QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        blurred = small.scaled(area.size(), QtCore.Qt.IgnoreAspectRatio,
                               QtCore.Qt.SmoothTransformation)
        image = QtGui.QImage(window.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
        image.fill(QtGui.QColor(*material.SURFACE_RGBA[:3]))
        painter = QtGui.QPainter(image)
        painter.drawImage(area.topLeft() - origin, blurred)
        painter.end()
        return image
    except RuntimeError:
        return None


def paint_material(painter, path, active, image=None):
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    if image is not None:
        painter.save()
        painter.setClipPath(path)
        painter.drawImage(path.boundingRect(), image)
        tint = material.NATIVE_TINT
        painter.fillPath(path, QtGui.QColor(tint & 255, (tint >> 8) & 255,
                                           (tint >> 16) & 255, tint >> 24))
        painter.restore()
    painter.fillPath(path, QtGui.QColor(*(material.SURFACE_RGBA if active or image is not None
                                       else material.FALLBACK_RGBA)))
    painter.setPen(QtGui.QPen(QtGui.QColor(*material.OUTLINE_RGBA), 1.0))
    painter.drawPath(path)
    painter.restore()
