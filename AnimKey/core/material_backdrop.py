"""Application-rendered frosted glass with one rounded alpha silhouette."""
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


def apply_native_backdrop(window):
    """Retire the separate DWM rectangle; the Qt surface owns all glass pixels.

    Kept as a compatibility entry point for existing tools. Native accent can
    ignore a layered window's alpha corners even when SetWindowRgn succeeds.
    """
    if _windows():
        import ctypes
        from ctypes import wintypes

        class Accent(ctypes.Structure):
            _fields_ = [('state', ctypes.c_int), ('flags', ctypes.c_int),
                        ('tint', wintypes.DWORD), ('animation', ctypes.c_int)]

        class CompositionData(ctypes.Structure):
            _fields_ = [('attribute', ctypes.c_int), ('data', ctypes.c_void_p),
                        ('size', ctypes.c_size_t)]

        handle = int(window.winId())
        if getattr(window, '_material_disabled_native_handle', None) != handle:
            try:
                compose = ctypes.WinDLL('user32').SetWindowCompositionAttribute
                compose.argtypes = [wintypes.HWND, ctypes.POINTER(CompositionData)]
                accent = Accent(0, 0, 0, 0)
                data = CompositionData(19, ctypes.addressof(accent), ctypes.sizeof(accent))
                compose(handle, ctypes.byref(data))
                dwm = ctypes.WinDLL('dwmapi')
                set_attribute = dwm.DwmSetWindowAttribute
                set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD]
                for attribute, value in ((2, 1), (38, 1), (34, 0xFFFFFFFE)):
                    data_value = wintypes.DWORD(value)
                    set_attribute(handle, attribute, ctypes.byref(data_value), ctypes.sizeof(data_value))
            except (AttributeError, OSError, ValueError):
                pass
            window._material_disabled_native_handle = handle
        window.setProperty('nativeBlurHandle', handle)
        window.setProperty('nativeFrameHandle', handle)
    window.setProperty('nativeBlurBackend', 'qt')
    window.setProperty('nativeBlurMode', 'frosted')
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


def _maya_viewport_images(source, area):
    """Read intersecting Maya viewports; QWidget.grab cannot read their GPU image."""
    if source.objectName() != 'MayaWindow':
        return
    try:
        import ctypes
        import maya.cmds as cmds
        import maya.api.OpenMaya as om
        import maya.api.OpenMayaUI as omui
        from AnimKey.mods.maya_compat import wrap_instance
        if cmds.about(batch=True):
            return
        panels = cmds.getPanel(type='modelPanel') or []
    except (ImportError, RuntimeError, AttributeError):
        return
    for panel in panels:
        try:
            view = omui.M3dView.getM3dViewFromModelPanel(panel)
            pointer = view.widget()
            if not pointer:
                continue
            widget = wrap_instance(int(pointer), QtWidgets.QWidget)
            if not widget.isVisible() or widget.window() is not source:
                continue
            bounds = QtCore.QRect(widget.mapTo(source, QtCore.QPoint()), widget.size())
            if not bounds.intersects(area):
                continue
            frame = om.MImage()
            frame.create(view.portWidth(), view.portHeight(), 4, om.MImage.kFloat)
            view.readColorBuffer(frame, True)
            # Downsample in Maya before copying pixels to Python. This is a
            # blurred preview, never a full-resolution screenshot or playblast.
            frame.resize(max(1, widget.width() // 10), max(1, widget.height() // 10), False)
            width, height = frame.getSize()
            if not width or not height or frame.depth() != 4:
                continue
            count = width * height * 4
            if frame.pixelType() == om.MImage.kFloat:
                pixels = frame.floatPixels()
                if not pixels:
                    continue
                values = (ctypes.c_float * count).from_address(int(pixels))
                data = bytearray(count)
                for index in range(0, count, 4):
                    for channel in range(3):
                        data[index + channel] = int(max(0.0, min(1.0, values[index + channel])) * 255)
                    data[index + 3] = 255
            else:
                pixels = frame.pixels()
                if not pixels:
                    continue
                data = bytearray(ctypes.string_at(int(pixels), count))
                data[3::4] = b'\xff' * (width * height)
            image = QtGui.QImage(bytes(data), width, height, width * 4,
                                 QtGui.QImage.Format_RGBA8888).copy().mirrored(False, True)
            yield bounds, image
        except (RuntimeError, TypeError, ValueError, AttributeError):
            continue


def _blur_image(image):
    """Blur an image offscreen; never attach a graphics effect to Maya widgets."""
    small = image.scaled(max(1, image.width() // 4), max(1, image.height() // 4),
                         QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
    scene = QtWidgets.QGraphicsScene()
    item = QtWidgets.QGraphicsPixmapItem(QtGui.QPixmap.fromImage(small))
    scene.addItem(item)
    effect = QtWidgets.QGraphicsBlurEffect()
    effect.setBlurRadius(12)
    effect.setBlurHints(QtWidgets.QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(effect)
    blurred = QtGui.QImage(small.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
    blurred.fill(small.pixelColor(small.width() // 2, small.height() // 2))
    painter = QtGui.QPainter(blurred)
    scene.render(painter, QtCore.QRectF(blurred.rect()), QtCore.QRectF(small.rect()))
    painter.end()
    return blurred.scaled(image.size(), QtCore.Qt.IgnoreAspectRatio,
                          QtCore.Qt.SmoothTransformation)


def capture_widget_backdrop(window):
    """Blur the owning application's Qt and viewport pixels, never the desktop."""
    from AnimKey.mods.maya_compat import get_maya_main_window
    source = window.parentWidget()
    source = source.window() if source is not None else get_maya_main_window()
    if source is None or source is window or not source.isVisible() or source.isMinimized():
        return None
    origin = source.mapFromGlobal(window.mapToGlobal(QtCore.QPoint()))
    area = QtCore.QRect(origin, window.size()).intersected(source.rect())
    if area.isEmpty():
        return None
    try:
        captured = source.grab(area).toImage()
        if captured.isNull():
            return None
        captured = captured.scaled(area.size(), QtCore.Qt.IgnoreAspectRatio,
                                   QtCore.Qt.SmoothTransformation)
        captured.setDevicePixelRatio(1.0)
        painter = QtGui.QPainter(captured)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        viewport_count = 0
        for bounds, viewport in _maya_viewport_images(source, area):
            painter.drawImage(QtCore.QRectF(bounds.translated(-area.topLeft())), viewport)
            viewport_count += 1
        painter.end()
        window.setProperty('materialViewportCount', viewport_count)
        # The caller throttles Show/Move/Resize updates. An idle window never
        # polls the viewport, and capture cannot modify the animation scene.
        blurred = _blur_image(captured)
        image = QtGui.QImage(window.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(image)
        painter.drawImage(area.topLeft() - origin, blurred)
        painter.end()
        return image
    except RuntimeError:
        return None


def paint_material(painter, path, active=False, image=None):
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    if image is not None:
        painter.save()
        painter.setClipPath(path)
        painter.setOpacity(material.BACKDROP_OPACITY)
        painter.drawImage(path.boundingRect(), image)
        painter.restore()
    painter.fillPath(path, QtGui.QColor(*(material.SURFACE_RGBA if image is not None or active
                                       else material.FALLBACK_RGBA)))
    painter.setPen(QtGui.QPen(QtGui.QColor(*material.OUTLINE_RGBA), 1.0))
    painter.drawPath(path)
    painter.restore()
