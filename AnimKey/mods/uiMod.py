"""
    AnimKey UI Module
    
    Contains custom UI widgets and utility functions for building the interface.
    Includes modern slider with illuminated dots and modern dropdown menus.
"""

import maya.cmds as cmds
import maya.OpenMayaUI as mui
import math

# Try importing PySide2 or PySide6
try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea
    from PySide2.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QPoint, QRect
    from PySide2.QtGui import QColor, QPainter, QPen, QBrush, QLinearGradient, QRadialGradient, QFont, QPainterPath
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea
    from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QPoint, QRect
    from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QLinearGradient, QRadialGradient, QFont, QPainterPath
    from shiboken6 import wrapInstance

from AnimKey.mods.themes import ThemeManager
from AnimKey.mods import styleMod as style


# ═══════════════════════════════════════════════════════════════════════════════
#                           ANIMKEY STYLE COLOR PALETTE
# ═══════════════════════════════════════════════════════════════════════════════

class AnimKeyColors:
    """Color palette for AnimKey-style widgets"""
    # Background colors
    BG_DARK = QColor(28, 28, 32)
    BG_MEDIUM = QColor(38, 38, 45)
    BG_LIGHT = QColor(52, 52, 62)
    
    # Accent colors
    ACCENT_PRIMARY = QColor(0, 200, 255)      # Cyan
    ACCENT_SECONDARY = QColor(130, 80, 255)   # Purple
    ACCENT_TERTIARY = QColor(255, 100, 150)   # Pink
    ACCENT_SUCCESS = QColor(80, 255, 120)     # Green
    
    # Glow colors
    GLOW_CYAN = QColor(0, 200, 255, 180)
    GLOW_PURPLE = QColor(130, 80, 255, 180)
    GLOW_WHITE = QColor(255, 255, 255, 200)
    
    # Text colors
    TEXT_PRIMARY = QColor(240, 240, 245)
    TEXT_SECONDARY = QColor(160, 160, 175)
    TEXT_MUTED = QColor(100, 100, 115)
    
    # Slider specific
    SLIDER_TRACK = QColor(45, 45, 55)
    SLIDER_FILL = QColor(0, 180, 230)
    SLIDER_HANDLE = QColor(85, 85, 95)  # Dark gray handle instead of white
    SLIDER_HANDLE_BORDER = QColor(110, 110, 120)  # Lighter border for contrast
    
    # Dot button states
    DOT_INACTIVE = QColor(70, 70, 85)
    DOT_HOVER = QColor(0, 220, 255)
    DOT_ACTIVE = QColor(130, 255, 180)
    DOT_GLOW = QColor(0, 200, 255, 100)


# ═══════════════════════════════════════════════════════════════════════════════
#                           UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_maya_main_window():
    """Get the Maya main window as a Qt widget"""
    main_window_ptr = mui.MQtUtil.mainWindow()
    return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)


ANIMKEY_SINGLE_POPUP_OBJECTS = {
    "AnimKey_AnimCleaner",
    "AnimKey_AnimCrash",
    "AnimKey_Settings",
    "AnimKey_Sketchboard",
    "setManagerV7",
    "AnimKey_Retimer",
    "AnimKey_BakeFactory",
    "AnimKey_CollisionTool",
    "AnimKey_GimbalFixer",
    "AnimKey_TempControl",
    "AnimKey_TempPivotPro",
    "AnimKey_CopyAnimation",
    "AnimKey_SaveAnimation",
}


def close_animkey_tool_windows(except_widget=None, include_hidden=False):
    """Hide attached AnimKey tool windows so detached panels can stay open."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return

    for widget in app.topLevelWidgets():
        if widget is except_widget:
            continue
        try:
            if widget.objectName() not in ANIMKEY_SINGLE_POPUP_OBJECTS:
                continue
            if not widget.isVisible() and not include_hidden:
                continue
            if hasattr(widget, "animkey_auto_hide"):
                widget.animkey_auto_hide()
            else:
                widget.close()
                widget.deleteLater()
        except RuntimeError:
            pass
        except Exception:
            pass


def cleanup_animkey_runtime(except_widget=None):
    """Close AnimKey-owned floating tools and stop live helpers when AnimKey exits."""
    def _safe_call(func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            return None

    # Stop/toggle live tools first so their callbacks do not resurrect UI.
    try:
        from AnimKey.buttons import animation_offset
        if _safe_call(animation_offset.is_active):
            _safe_call(animation_offset.execute)
    except Exception:
        pass

    try:
        from AnimKey.buttons import microMove
        if _safe_call(microMove.is_active):
            _safe_call(microMove.execute)
    except Exception:
        pass

    try:
        from AnimKey.buttons import animCrash
        _safe_call(animCrash.RecoverySystem.stop)
    except Exception:
        pass

    try:
        from AnimKey.buttons import brush
        _safe_call(brush.close_instance)
    except Exception:
        pass

    try:
        from AnimKey.buttons import tempPivot
        _safe_call(tempPivot.cleanup_orphans)
    except Exception:
        pass

    try:
        from AnimKey.mods import viewportGimbal
        _safe_call(viewportGimbal.apply, False)
    except Exception:
        pass

    try:
        from AnimKey.mods import tumbleAroundSelection
        _safe_call(tumbleAroundSelection.apply, False)
    except Exception:
        pass

    try:
        from AnimKey.mods import channelBoxMultiSelection
        _safe_call(channelBoxMultiSelection.apply, False)
    except Exception:
        pass

    close_animkey_tool_windows(except_widget=except_widget, include_hidden=True)


def is_valid_qt_widget(widget):
    if widget is None:
        return False
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False
    except Exception:
        return False


def show_existing_animkey_tool_window(widget, anchor_button=None):
    """Reuse a hidden AnimKey tool window without losing its detached position."""
    if not is_valid_qt_widget(widget):
        return None
    try:
        if anchor_button is not None and hasattr(widget, "anchor_button"):
            widget.anchor_button = anchor_button
        if not widget.isVisible():
            widget.show()
        if getattr(widget, "_magnet_attached", False) and hasattr(widget, "position_window"):
            try:
                widget.position_window(force=True)
            except TypeError:
                widget.position_window()
        widget.raise_()
        widget.activateWindow()
        return widget
    except RuntimeError:
        return None
    except Exception:
        return None


def get_screen_resolution():
    """Get the current screen resolution"""
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    
    try:
        from PySide2.QtWidgets import QDesktopWidget
        desktop = QDesktopWidget()
        screen_rect = desktop.screenGeometry()
    except ImportError:
        screen = app.primaryScreen()
        screen_rect = screen.geometry()
    
    return screen_rect.width(), screen_rect.height()


def is_4k_display():
    """Check if the display is 4K or higher"""
    width, _ = get_screen_resolution()
    return width >= 3840


def get_dpi_scale():
    """Get the DPI scale factor for the display"""
    if is_4k_display():
        return 2.0
    return 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#                           CUSTOM WIDGETS
# ═══════════════════════════════════════════════════════════════════════════════

class AnimKeySlider(QtWidgets.QSlider):
    """
    Custom slider with enhanced styling and functionality
    """
    
    # Signal emitted when dragging starts
    dragStarted = QtCore.Signal()
    # Signal emitted when dragging ends
    dragEnded = QtCore.Signal()
    
    def __init__(self, orientation=QtCore.Qt.Horizontal, parent=None, slider_type="tween"):
        super(AnimKeySlider, self).__init__(orientation, parent)
        
        self.slider_type = slider_type
        self._is_dragging = False
        self._original_value = 0
        
        # Apply styling
        self.setStyleSheet(style.get_slider_stylesheet(slider_type))
        
        # Connect signals
        self.sliderPressed.connect(self._on_drag_start)
        self.sliderReleased.connect(self._on_drag_end)
    
    def _on_drag_start(self):
        """Called when the user starts dragging the slider"""
        self._is_dragging = True
        self._original_value = self.value()
        self.dragStarted.emit()
    
    def _on_drag_end(self):
        """Called when the user stops dragging the slider"""
        self._is_dragging = False
        self.dragEnded.emit()
    
    def is_dragging(self):
        """Check if the slider is currently being dragged"""
        return self._is_dragging
    
    def reset_to_default(self, default_value=None):
        """Reset the slider to its default value"""
        if default_value is not None:
            self.setValue(default_value)
        else:
            self.setValue(self._original_value)


# ═══════════════════════════════════════════════════════════════════════════════
#                           ILLUMINATED DOT BUTTON
# ═══════════════════════════════════════════════════════════════════════════════

class IlluminatedDotButton(QWidget):
    """
    A circular button that illuminates when hovered or when the slider
    value is near its assigned percentage value.
    """
    
    clicked = QtCore.Signal(int)
    hovered = QtCore.Signal(bool)
    
    def __init__(self, percentage, parent=None):
        super().__init__(parent)
        self.percentage = percentage
        self._glow_intensity = 0.0
        self._is_hovered = False
        self._is_active = False
        self._is_near = False
        self._pulse_phase = 0.0
        self._parent_slider = parent
        self._accent_color = QColor(0, 200, 255)  # Default cyan
        
        # Size configuration - Compact for toolbar
        self.dot_radius = 5
        self.glow_radius = 10
        self.setFixedSize(self.glow_radius * 2 + 2, self.glow_radius * 2 + 2)
        
        # Animation for glow effect
        self._glow_animation = QPropertyAnimation(self, b"glow_intensity")
        self._glow_animation.setDuration(200)
        self._glow_animation.setEasingCurve(QEasingCurve.OutCubic)
        
        # Pulse animation timer
        self._pulse_timer = QtCore.QTimer(self)
        self._pulse_timer.timeout.connect(self._update_pulse)
        
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        
        # Allow mouse events to pass through when slider is dragging
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
    
    def set_accent_color(self, color):
        """Set the accent color for this dot"""
        self._accent_color = QColor(color)
        self.update()
        
    def get_glow_intensity(self):
        return self._glow_intensity
    
    def set_glow_intensity(self, value):
        self._glow_intensity = value
        self.update()
    
    glow_intensity = Property(float, get_glow_intensity, set_glow_intensity)
    
    def set_near(self, is_near, distance=0):
        """Set if slider value is near this dot's percentage"""
        self._is_near = is_near
        if is_near:
            intensity = max(0.3, 1.0 - (distance / 15.0))
            self._animate_glow(intensity)
            if not self._pulse_timer.isActive():
                self._pulse_timer.start(50)
        else:
            if not self._is_hovered:
                self._animate_glow(0.0)
            self._pulse_timer.stop()
            self._pulse_phase = 0
        self.update()
    
    def set_active(self, is_active):
        """Set if this dot represents the current exact value"""
        self._is_active = is_active
        if is_active:
            self._animate_glow(1.0)
        self.update()
    
    def _animate_glow(self, target):
        self._glow_animation.stop()
        self._glow_animation.setStartValue(self._glow_intensity)
        self._glow_animation.setEndValue(target)
        self._glow_animation.start()
    
    def _update_pulse(self):
        self._pulse_phase += 0.15
        self.update()
    
    def enterEvent(self, event):
        if self._parent_slider and hasattr(self._parent_slider, '_is_dragging') and self._parent_slider._is_dragging:
            return
        self._is_hovered = True
        self._animate_glow(1.0)
        self.hovered.emit(True)
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        self._is_hovered = False
        if not self._is_near and not self._is_active:
            self._animate_glow(0.0)
        self.hovered.emit(False)
        super().leaveEvent(event)
    
    def mousePressEvent(self, event):
        if self._parent_slider and hasattr(self._parent_slider, '_is_dragging') and self._parent_slider._is_dragging:
            event.ignore()
            return
        if event.button() == Qt.LeftButton:
            event.accept()
            self.clicked.emit(self.percentage)
            return
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        if self._parent_slider and hasattr(self._parent_slider, '_is_dragging') and self._parent_slider._is_dragging:
            event.ignore()
            parent_pos = self.mapToParent(event.pos())
            new_event = QtGui.QMouseEvent(
                event.type(),
                parent_pos,
                event.button(),
                event.buttons(),
                event.modifiers()
            )
            self._parent_slider.mouseMoveEvent(new_event)
            return
        super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event):
        if self._parent_slider and hasattr(self._parent_slider, '_is_dragging') and self._parent_slider._is_dragging:
            event.ignore()
            parent_pos = self.mapToParent(event.pos())
            new_event = QtGui.QMouseEvent(
                event.type(),
                parent_pos,
                event.button(),
                event.buttons(),
                event.modifiers()
            )
            self._parent_slider.mouseReleaseEvent(new_event)
            return
        super().mouseReleaseEvent(event)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        
        center = self.rect().center()
        
        # Calculate pulse effect
        pulse_offset = 0
        if self._is_near or self._is_active:
            pulse_offset = math.sin(self._pulse_phase) * 2
        
        # Draw outer glow with accent color
        if self._glow_intensity > 0:
            glow_color = QColor(self._accent_color)
            if self._is_active:
                glow_color = glow_color.lighter(130)
                glow_color.setAlpha(int(180 * self._glow_intensity))
            else:
                glow_color.setAlpha(int(120 * self._glow_intensity))
            
            gradient = QRadialGradient(center, self.glow_radius + pulse_offset)
            gradient.setColorAt(0, glow_color)
            gradient.setColorAt(0.5, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), int(glow_color.alpha() * 0.5)))
            gradient.setColorAt(1, QColor(0, 0, 0, 0))
            
            painter.setBrush(gradient)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(center, int(self.glow_radius + pulse_offset), int(self.glow_radius + pulse_offset))
        
        # Draw main dot
        if self._is_active:
            dot_color = self._accent_color.lighter(130)
        elif self._is_hovered or self._glow_intensity > 0.5:
            t = self._glow_intensity
            dot_color = QColor(
                int(AnimKeyColors.DOT_INACTIVE.red() + (self._accent_color.red() - AnimKeyColors.DOT_INACTIVE.red()) * t),
                int(AnimKeyColors.DOT_INACTIVE.green() + (self._accent_color.green() - AnimKeyColors.DOT_INACTIVE.green()) * t),
                int(AnimKeyColors.DOT_INACTIVE.blue() + (self._accent_color.blue() - AnimKeyColors.DOT_INACTIVE.blue()) * t)
            )
        else:
            dot_color = AnimKeyColors.DOT_INACTIVE
        
        # Draw dot with subtle gradient
        gradient = QRadialGradient(center.x() - 2, center.y() - 2, self.dot_radius * 1.5)
        lighter = dot_color.lighter(140)
        gradient.setColorAt(0, lighter)
        gradient.setColorAt(1, dot_color)
        
        painter.setBrush(gradient)
        painter.setPen(QPen(dot_color.darker(120), 1))
        painter.drawEllipse(center, self.dot_radius, self.dot_radius)
        
        # Draw inner highlight
        if self._glow_intensity > 0.3:
            highlight = QRadialGradient(center.x() - 2, center.y() - 2, self.dot_radius * 0.8)
            highlight.setColorAt(0, QColor(255, 255, 255, int(100 * self._glow_intensity)))
            highlight.setColorAt(1, QColor(255, 255, 255, 0))
            painter.setBrush(highlight)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(center, int(self.dot_radius * 0.6), int(self.dot_radius * 0.6))


# ═══════════════════════════════════════════════════════════════════════════════
#                           VALUE DISPLAY TOOLTIP
# ═══════════════════════════════════════════════════════════════════════════════

class ValueTooltip(QWidget):
    """Floating tooltip showing current value"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0
        self.setFixedSize(50, 30)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._opacity = 0.0
        self._accent_color = QColor(0, 200, 255)
        
        self._fade_animation = QPropertyAnimation(self, b"opacity")
        self._fade_animation.setDuration(150)
    
    def set_accent_color(self, color):
        """Set the accent color for this tooltip"""
        self._accent_color = QColor(color)
        self.update()
    
    def get_opacity(self):
        return self._opacity
    
    def set_opacity(self, value):
        self._opacity = value
        self.update()
    
    opacity = Property(float, get_opacity, set_opacity)
    
    def show_value(self, value, pos):
        self.value = value
        self.move(pos.x() - 25, pos.y() - 40)
        self._fade_animation.stop()
        self._fade_animation.setStartValue(self._opacity)
        self._fade_animation.setEndValue(1.0)
        self._fade_animation.start()
        self.show()
    
    def hide_tooltip(self):
        self._fade_animation.stop()
        self._fade_animation.setStartValue(self._opacity)
        self._fade_animation.setEndValue(0.0)
        self._fade_animation.start()
    
    def paintEvent(self, event):
        if self._opacity <= 0:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setOpacity(self._opacity)
        
        # Draw background
        path = QPainterPath()
        rect = QRect(0, 0, 50, 24)
        path.addRoundedRect(rect, 6, 6)
        
        # Add pointer triangle
        path.moveTo(20, 24)
        path.lineTo(25, 30)
        path.lineTo(30, 24)
        
        gradient = QLinearGradient(0, 0, 0, 24)
        gradient.setColorAt(0, AnimKeyColors.BG_LIGHT)
        gradient.setColorAt(1, AnimKeyColors.BG_MEDIUM)
        
        painter.fillPath(path, gradient)
        painter.setPen(QPen(self._accent_color.darker(120), 1))
        painter.drawPath(path)
        
        # Draw text
        painter.setPen(AnimKeyColors.TEXT_PRIMARY)
        font = QFont("Segoe UI", 10, QFont.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, f"{self.value}%")


# ═══════════════════════════════════════════════════════════════════════════════
#                           SCROLLABLE MENU ITEM WIDGET
# ═══════════════════════════════════════════════════════════════════════════════

class MenuItemWidget(QWidget):
    """Individual menu item with hover effects"""
    
    clicked = QtCore.Signal(str)
    
    def __init__(self, name, description, color, parent=None):
        super().__init__(parent)
        self._name = name
        self._description = description
        self._color = color
        self._is_hovered = False
        
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
    
    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._name)
        super().mousePressEvent(event)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        
        rect = self.rect().adjusted(4, 2, -4, -2)
        
        # Hover background
        if self._is_hovered:
            hover_path = QPainterPath()
            hover_path.addRoundedRect(rect, 4, 4)
            
            hover_gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            hover_gradient.setColorAt(0, QColor(self._color.red(), self._color.green(), self._color.blue(), 50))
            hover_gradient.setColorAt(1, QColor(self._color.red(), self._color.green(), self._color.blue(), 25))
            painter.fillPath(hover_path, hover_gradient)
            
            # Accent line on left
            painter.setPen(QPen(self._color, 2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(rect.x() + 2, rect.y() + 6, rect.x() + 2, rect.bottom() - 6)
        
        # Color indicator dot
        dot_x = rect.x() + 12
        dot_y = rect.y() + 10
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QPoint(dot_x, dot_y), 3, 3)
        
        # Option name
        painter.setPen(AnimKeyColors.TEXT_PRIMARY if self._is_hovered else AnimKeyColors.TEXT_SECONDARY)
        font = QFont("Segoe UI", 9)
        font.setWeight(QFont.Medium if self._is_hovered else QFont.Normal)
        painter.setFont(font)
        
        name_rect = QRect(rect.x() + 22, rect.y() + 2, rect.width() - 24, 14)
        painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, self._name)
        
        # Description
        painter.setPen(AnimKeyColors.TEXT_MUTED)
        font = QFont("Segoe UI", 7)
        painter.setFont(font)
        
        desc_rect = QRect(rect.x() + 22, rect.y() + 16, rect.width() - 24, 12)
        painter.drawText(desc_rect, Qt.AlignLeft | Qt.AlignVCenter, self._description)


# ═══════════════════════════════════════════════════════════════════════════════
#                           MODERN DROPDOWN MENU (SCROLLABLE, OPENS UPWARD)
# ═══════════════════════════════════════════════════════════════════════════════

class ModernDropdownMenu(QWidget):
    """
    Modern styled dropdown menu with scroll support.
    Opens upward and has a maximum height with scrolling.
    """
    
    optionClicked = QtCore.Signal(str)
    menuClosed = QtCore.Signal()
    
    def __init__(self, options, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        self._options = options
        self._width = 200
        self._item_height = 36
        self._max_visible_items = 6  # Maximum items before scrolling
        self._padding = 6
        
        # Calculate height
        visible_items = min(len(options), self._max_visible_items)
        self._height = visible_items * self._item_height + self._padding * 2
        
        self.setFixedSize(self._width, self._height)
        
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(self._padding, self._padding, self._padding, self._padding)
        main_layout.setSpacing(0)
        
        # Scroll area
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll_area.setFrameShape(QScrollArea.NoFrame)
        
        # Style the scrollbar
        self._scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: rgba(40, 40, 50, 150);
                width: 6px;
                margin: 2px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(100, 100, 120, 200);
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(130, 130, 150, 220);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        
        # Container widget for items
        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(2)
        
        # Add menu items
        for name, description, color in options:
            item = MenuItemWidget(name, description, color)
            item.clicked.connect(self._on_item_clicked)
            container_layout.addWidget(item)
        
        container_layout.addStretch()
        
        self._scroll_area.setWidget(self._container)
        main_layout.addWidget(self._scroll_area)
    
    def _on_item_clicked(self, name):
        self.optionClicked.emit(name)
        self.close()
    
    def show_at(self, pos):
        """Show menu at position, opening upward"""
        # Adjust position to open upward (above the label)
        adjusted_pos = QPoint(pos.x(), pos.y() - self._height)
        self.move(adjusted_pos)
        self.show()
        self.setFocus()
    
    def hideEvent(self, event):
        self.menuClosed.emit()
        super().hideEvent(event)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        
        # Shadow
        shadow_rect = QRect(3, 3, self.width() - 3, self.height() - 3)
        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(shadow_rect, 8, 8)
        painter.fillPath(shadow_path, QColor(0, 0, 0, 50))
        
        # Main background
        bg_rect = QRect(0, 0, self.width() - 3, self.height() - 3)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(bg_rect, 8, 8)
        
        theme = ThemeManager.get_current_theme()
        bg_color = QColor(theme["bg_secondary"])
        
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, bg_color.lighter(115))
        gradient.setColorAt(1, bg_color)
        painter.fillPath(bg_path, gradient)
        
        # Border
        painter.setPen(QPen(QColor(theme["border_color"]).lighter(120), 1))
        painter.drawPath(bg_path)


# ═══════════════════════════════════════════════════════════════════════════════
#                           DROPDOWN LABEL WITH MODERN MENU
# ═══════════════════════════════════════════════════════════════════════════════

class DropdownLabel(QWidget):
    """
    A clickable label that shows a modern dropdown menu with options.
    """
    
    optionSelected = QtCore.Signal(str)
    
    def __init__(self, text="Tween", options=None, parent=None):
        super().__init__(parent)
        self._current_text = text
        self._is_hovered = False
        self._menu_visible = False
        
        # Default options if none provided
        if options is None:
            self._options = [
                ("Tween", "Interpolate between keyframes", QColor(0, 200, 255)),        # Cyan
                ("Blend to Frame", "Blend to a specific frame", QColor(255, 150, 50)),  # Orange
                ("Blend to Ease", "Blend with easing curve", QColor(180, 100, 255)),    # Purple
                ("Push/Pull", "Push or pull animation values", QColor(100, 220, 130)),  # Green
            ]
        else:
            self._options = options
        
        # Color mapping
        self._color_map = {opt[0]: opt[2] for opt in self._options}
        self._current_color = self._color_map.get(text, AnimKeyColors.ACCENT_PRIMARY)
        
        self.setFixedWidth(120)
        self.setFixedHeight(22)  # Compact for toolbar
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        
        # Create the popup menu
        self._menu = ModernDropdownMenu(self._options, self)
        self._menu.optionClicked.connect(self._on_option_selected)
        self._menu.menuClosed.connect(self._on_menu_closed)
    
    @property
    def current_option(self):
        return self._current_text
    
    @property
    def current_color(self):
        return self._current_color
    
    def set_options(self, options):
        """Update the options list"""
        self._options = options
        self._color_map = {opt[0]: opt[2] for opt in self._options}
        self._menu = ModernDropdownMenu(self._options, self)
        self._menu.optionClicked.connect(self._on_option_selected)
        self._menu.menuClosed.connect(self._on_menu_closed)
        self.update()
    
    def set_current_option(self, option_name):
        """Set the current option by name"""
        if option_name in self._color_map:
            self._current_text = option_name
            self._current_color = self._color_map[option_name]
            self.update()
    
    def _on_option_selected(self, option_name):
        self._current_text = option_name
        self._current_color = self._color_map.get(option_name, QColor(240, 198, 116))
        self._menu_visible = False
        self.optionSelected.emit(option_name)
        self.update()
    
    def _on_menu_closed(self):
        self._menu_visible = False
        self.update()
    
    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._show_menu()
        super().mousePressEvent(event)
    
    def _show_menu(self):
        self._menu_visible = True
        self.update()
        
        # Position menu above the label (opens upward)
        global_pos = self.mapToGlobal(QPoint(0, -4))
        self._menu.show_at(global_pos)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        
        theme = ThemeManager.get_current_theme()
        bg_color = QColor(theme["bg_secondary"])
        
        # Background with accent color tint
        if self._is_hovered or self._menu_visible:
            bg_color = QColor(
                min(255, self._current_color.red() // 5 + bg_color.red()),
                min(255, self._current_color.green() // 5 + bg_color.green()),
                min(255, self._current_color.blue() // 5 + bg_color.blue())
            )
        
        path = QPainterPath()
        path.addRoundedRect(QRect(0, 0, self.width(), self.height()), 4, 4)
        
        # Gradient background
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, bg_color.lighter(110))
        gradient.setColorAt(1, bg_color)
        painter.fillPath(path, gradient)
        
        # Border with accent color
        border_color = self._current_color if (self._is_hovered or self._menu_visible) else QColor(theme["border_color"])
        painter.setPen(QPen(border_color, 1))
        painter.drawPath(path)
        
        # Accent line on left
        accent_path = QPainterPath()
        accent_path.moveTo(3, 4)
        accent_path.lineTo(3, self.height() - 4)
        painter.setPen(QPen(self._current_color, 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawPath(accent_path)
        
        # Text
        painter.setPen(self._current_color if self._is_hovered else QColor(theme["text_primary"]))
        font = QFont("Segoe UI", 9)
        font.setWeight(QFont.Medium)
        painter.setFont(font)
        
        text_rect = QRect(10, 0, self.width() - 26, self.height())
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self._current_text)
        
        # Dropdown arrow with accent color
        arrow_x = self.width() - 14
        arrow_y = self.height() // 2
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._current_color)
        
        # Draw triangle arrow
        arrow = QPainterPath()
        arrow.moveTo(arrow_x, arrow_y - 2)
        arrow.lineTo(arrow_x + 6, arrow_y - 2)
        arrow.lineTo(arrow_x + 3, arrow_y + 2)
        arrow.closeSubpath()
        painter.drawPath(arrow)


# ═══════════════════════════════════════════════════════════════════════════════
#                           ANIMKEY STYLE SLIDER
# ═══════════════════════════════════════════════════════════════════════════════

class AnimKeySlider(QWidget):
    """
    Main slider widget with AnimKey-style features:
    - Illuminated dot buttons at 15% intervals (0%, 15%, 30% | 70%, 85%, 100%)
    - Clean slider mode when dragging
    - Value display while sliding
    - Smooth animations
    - Returns to origin when released
    - Dynamic accent color
    """
    
    valueChanged = QtCore.Signal(int)
    sliderPressed = QtCore.Signal()
    sliderReleased = QtCore.Signal(int)  # Emits final value before returning to origin
    geometryChanged = QtCore.Signal()
    
    def __init__(self, parent=None, min_val=0, max_val=100, origin=50, return_to_origin=True, show_dots=True):
        super().__init__(parent)
        
        self.min_val = min_val
        self.max_val = max_val
        self._value = origin
        self._origin = origin
        self._return_to_origin = return_to_origin
        self._is_dragging = False
        self._dots_visible = show_dots
        self._dots_enabled = show_dots  # Store whether to show dots (renamed to avoid conflict)
        self._dots_opacity = 1.0 if show_dots else 0.0
        self._display_value = origin
        self._accent_color = QColor(0, 200, 255)  # Default cyan like original
        
        # Dimensions - Compact for toolbar
        self.slider_height = 4
        self.track_margin = 24 if show_dots else 12  # Less margin if no dots
        self.widget_height = 28  # Compact height for toolbar
        
        self.setMinimumWidth(200)
        self.setFixedHeight(self.widget_height)
        self.setMouseTracking(True)
        
        # Create dot buttons only if show_dots is True
        self.dot_buttons = []
        if show_dots:
            # Left side: 0%, 15%, 30%  |  Right side: 70%, 85%, 100%
            percentages = [0, 15, 30, 70, 85, 100]
            
            for pct in percentages:
                dot = IlluminatedDotButton(pct, self)
                dot.clicked.connect(self._on_dot_clicked)
                dot.hovered.connect(self._on_dot_hovered)
                self.dot_buttons.append(dot)
        
        # Create tooltip
        self.tooltip = ValueTooltip(self)
        self.tooltip.hide()
        
        # Dots fade animation
        self._dots_fade = QPropertyAnimation(self, b"dots_opacity")
        self._dots_fade.setDuration(200)
        self._dots_fade.setEasingCurve(QEasingCurve.OutCubic)
        
        # Return to origin animation
        self._return_animation = QPropertyAnimation(self, b"animated_value")
        self._return_animation.setDuration(300)
        self._return_animation.setEasingCurve(QEasingCurve.OutBack)
        self._return_animation.finished.connect(self._on_return_finished)
        
        # Initial layout
        if show_dots:
            self._update_dot_positions()
            
        # --- EXPANSIÓN PROPERTIES ---
        self._is_expanded = False
        self._base_width = 200 # Default
        self._tab_hover_alpha = 0.0
        self._tab_hovered = False
        
        # Animations
        self._tab_anim_hover = QPropertyAnimation(self, b"tab_hover_alpha")
        self._tab_anim_hover.setDuration(150)
        self._tab_anim_hover.setStartValue(0.0)
        self._tab_anim_hover.setEndValue(1.0)
        
        self._expand_anim = QPropertyAnimation(self, b"minimumWidth")
        self._expand_anim.setDuration(450)
        curve = QEasingCurve(QEasingCurve.OutBack)
        curve.setOvershoot(0.6) # Reduced from 1.5 for a subtler, sophisticated pop
        self._expand_anim.setEasingCurve(curve)
        self._expand_anim.valueChanged.connect(lambda *_: self._notify_geometry_changed())
        self._expand_anim.finished.connect(self._notify_geometry_changed)

    def _notify_geometry_changed(self):
        """Tell parent layouts/scrollers that the slider width changed."""
        try:
            self.updateGeometry()
            parent = self.parentWidget()
            while parent is not None:
                parent.updateGeometry()
                if parent.layout() is not None:
                    parent.layout().invalidate()
                    parent.layout().activate()
                parent = parent.parentWidget()
        except Exception:
            pass
        self.geometryChanged.emit()
        self.update()
    
    def set_accent_color(self, color):
        """Set the accent color for the entire slider"""
        self._accent_color = QColor(color)
        for dot in self.dot_buttons:
            dot.set_accent_color(color)
        self.tooltip.set_accent_color(color)
        self.update()
    
    @property
    def accent_color(self):
        return self._accent_color
    
    def get_dots_opacity(self):
        return self._dots_opacity
    
    def set_dots_opacity(self, value):
        self._dots_opacity = value
        for dot in self.dot_buttons:
            dot.setVisible(value > 0.1)
        self.update()
    
    dots_opacity = Property(float, get_dots_opacity, set_dots_opacity)
    
    def get_animated_value(self):
        return self._display_value
    
    def set_animated_value(self, value):
        self._display_value = value
        self._value = int(value)
        self._update_dot_states()
        self.update()
    
    animated_value = Property(float, get_animated_value, set_animated_value)
    
    @property
    def origin(self):
        return self._origin
    
    @origin.setter
    def origin(self, val):
        self._origin = max(self.min_val, min(self.max_val, val))
    
    @property
    def return_to_origin(self):
        return self._return_to_origin
    
    @return_to_origin.setter
    def return_to_origin(self, val):
        self._return_to_origin = val
    
    def value(self):
        return self._value
    
    def setValue(self, val):
        val = max(self.min_val, min(self.max_val, val))
        if val != self._value:
            self._value = val
            self._display_value = val
            self._update_dot_states()
            self.valueChanged.emit(val)
            self.update()
    
    def setMinimum(self, val):
        self.min_val = val
        self._update_dot_positions()
    
    def setMaximum(self, val):
        self.max_val = val
        self._update_dot_positions()

    def setMinimumWidth(self, width):
        if not hasattr(self, '_base_width') or getattr(self, '_base_width', 0) == 0:
            self._base_width = width
        elif not getattr(self, '_is_expanded', False):
            self._base_width = width
        super(AnimKeySlider, self).setMinimumWidth(width)
        self._notify_geometry_changed()

    def _toggle_expand(self):
        """Toggle width expansion state"""
        self._is_expanded = not self._is_expanded
        
        self._expand_anim.stop()
        self._expand_anim.setStartValue(self.minimumWidth())
        if self._is_expanded:
            self._expand_anim.setEndValue(self._base_width * 2)
        else:
            self._expand_anim.setEndValue(self._base_width)
        self._expand_anim.start()
        self._notify_geometry_changed()

    def get_tab_hover_alpha(self):
        return getattr(self, '_tab_hover_alpha', 0.0)
    
    def set_tab_hover_alpha(self, value):
        self._tab_hover_alpha = value
        self.update()
        
    tab_hover_alpha = Property(float, get_tab_hover_alpha, set_tab_hover_alpha)

    def _get_tab_rect(self):
        """Get the geometry of the expand/collapse tab"""
        w = 12
        h = 16
        # Align flush with right end, small margin
        x = self.width() - w - 4
        y = (self.height() - h) // 2
        return QRect(x, y, w, h)
    
    def _on_return_finished(self):
        """Called when return-to-origin animation completes"""
        self._value = self._origin
        self._display_value = self._origin
        self._update_dot_states()
        self.update()
    
    def _get_track_rect(self):
        """Get the slider track rectangle"""
        right_margin = max(self.track_margin, 20)
        return QRect(
            self.track_margin,
            (self.height() - self.slider_height) // 2,
            self.width() - self.track_margin - right_margin,
            self.slider_height
        )
    
    def _value_to_x(self, value):
        """Convert value to x position"""
        track = self._get_track_rect()
        if self.max_val == self.min_val:
            return track.x()
        ratio = (float(value) - self.min_val) / (self.max_val - self.min_val)
        return int(track.x() + ratio * track.width())
    
    def _x_to_value(self, x):
        """Convert x position to value"""
        track = self._get_track_rect()
        x = max(track.x(), min(track.right(), x))
        if track.width() == 0:
            return self.min_val
        ratio = (x - track.x()) / track.width()
        return int(self.min_val + ratio * (self.max_val - self.min_val))
    
    def _update_dot_positions(self):
        """Position dots along the slider"""
        for dot in self.dot_buttons:
            # Map percentage to actual value range
            actual_value = self.min_val + (dot.percentage / 100.0) * (self.max_val - self.min_val)
            x = self._value_to_x(actual_value)
            dot.move(
                x - dot.width() // 2,
                (self.height() - dot.height()) // 2
            )
    
    def _update_dot_states(self):
        """Update dot glow states based on current value"""
        # Map current value to percentage
        if self.max_val == self.min_val:
            current_pct = 0
        else:
            current_pct = ((self._value - self.min_val) / (self.max_val - self.min_val)) * 100
        
        for dot in self.dot_buttons:
            distance = abs(current_pct - dot.percentage)
            if distance == 0:
                dot.set_active(True)
                dot.set_near(False)
            elif distance <= 10:
                dot.set_active(False)
                dot.set_near(True, distance)
            else:
                dot.set_active(False)
                dot.set_near(False)
    
    def _on_dot_clicked(self, percentage):
        """Handle dot button click — full press→change→release cycle"""
        actual_value = self.min_val + (percentage / 100.0) * (self.max_val - self.min_val)
        # Trigger full cycle: prepare → execute → reset
        self.sliderPressed.emit()
        self.setValue(int(actual_value))
        self.valueChanged.emit(int(actual_value))
        self.sliderReleased.emit(int(actual_value))
        # Return to origin after clicking a dot
        self._animate_return_to_origin()
    
    def _animate_return_to_origin(self):
        """Animate slider back to origin"""
        if self._return_to_origin and not self._is_dragging:
            self._return_animation.stop()
            self._return_animation.setStartValue(float(self._value))
            self._return_animation.setEndValue(float(self._origin))
            self._return_animation.start()
    
    def _on_dot_hovered(self, is_hovered):
        """Handle dot hover"""
        pass
    
    def _show_dots_animation(self):
        """Fade in dot buttons"""
        if not self._dots_enabled:
            return
        self._dots_fade.stop()
        self._dots_fade.setStartValue(self._dots_opacity)
        self._dots_fade.setEndValue(1.0)
        self._dots_fade.start()
    
    def _hide_dots_animation(self):
        """Fade out dot buttons"""
        if not self._dots_enabled:
            return
        self._dots_fade.stop()
        self._dots_fade.setStartValue(self._dots_opacity)
        self._dots_fade.setEndValue(0.0)
        self._dots_fade.start()
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_dot_positions()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._get_tab_rect().contains(event.pos()):
                self._toggle_expand()
                event.accept()
                return
                
            track = self._get_track_rect()
            expanded_track = track.adjusted(-12, -20, 12, 20)
            
            if expanded_track.contains(event.pos()):
                self._is_dragging = True
                self._hide_dots_animation()
                
                # Make dots transparent to mouse events while dragging
                for dot in self.dot_buttons:
                    dot.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                
                self.sliderPressed.emit()
                self.setValue(self._x_to_value(event.x()))
                
                # Show tooltip
                handle_x = self._value_to_x(self._value)
                self.tooltip.show_value(self._value, QPoint(handle_x, self.height() // 2))
                
                self.setCursor(Qt.SizeHorCursor)
                self.grabMouse()
                event.accept()
                return
    
    def mouseMoveEvent(self, event):
        if self._is_dragging:
            self.setValue(self._x_to_value(event.x()))
            handle_x = self._value_to_x(self._value)
            self.tooltip.show_value(self._value, QPoint(handle_x, self.height() // 2))
            event.accept()
            return
        else:
            tab_rect = self._get_tab_rect()
            if tab_rect.contains(event.pos()):
                self.setCursor(Qt.PointingHandCursor)
                if not getattr(self, '_tab_hovered', False):
                    self._tab_hovered = True
                    if hasattr(self, '_tab_anim_hover'):
                        self._tab_anim_hover.stop()
                        self._tab_anim_hover.setDirection(QPropertyAnimation.Forward)
                        self._tab_anim_hover.start()
                return
            else:
                if getattr(self, '_tab_hovered', False):
                    self._tab_hovered = False
                    if hasattr(self, '_tab_anim_hover'):
                        self._tab_anim_hover.stop()
                        self._tab_anim_hover.setDirection(QPropertyAnimation.Backward)
                        self._tab_anim_hover.start()
            
            track = self._get_track_rect()
            expanded_track = track.adjusted(-12, -15, 12, 15)
            if expanded_track.contains(event.pos()):
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_dragging:
            self._is_dragging = False
            
            self.releaseMouse()
            
            # Restore dots mouse event handling
            for dot in self.dot_buttons:
                dot.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            
            self._show_dots_animation()
            self.tooltip.hide_tooltip()
            self.setCursor(Qt.ArrowCursor)
            
            # Emit the final value before returning
            self.sliderReleased.emit(self._value)
            
            # Animate back to origin if enabled
            if self._return_to_origin:
                self._return_animation.stop()
                self._return_animation.setStartValue(float(self._value))
                self._return_animation.setEndValue(float(self._origin))
                self._return_animation.start()
            event.accept()
            return
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        
        track = self._get_track_rect()
        # Use display_value for smooth animation rendering
        display_val = self._display_value if hasattr(self, '_display_value') else self._value
        handle_x = self._value_to_x(display_val)
        origin_x = self._value_to_x(self._origin)
        
        # Draw track background with rounded ends
        track_path = QPainterPath()
        track_path.addRoundedRect(track, self.slider_height // 2, self.slider_height // 2)
        
        # Track gradient
        track_gradient = QLinearGradient(track.topLeft(), track.bottomLeft())
        track_gradient.setColorAt(0, AnimKeyColors.SLIDER_TRACK.darker(110))
        track_gradient.setColorAt(0.5, AnimKeyColors.SLIDER_TRACK)
        track_gradient.setColorAt(1, AnimKeyColors.SLIDER_TRACK.lighter(110))
        
        painter.fillPath(track_path, track_gradient)
        
        # Draw origin marker (subtle line where slider returns to)
        if self._return_to_origin:
            painter.setPen(QPen(QColor(80, 80, 95), 2))
            painter.drawLine(origin_x, track.top() + 2, origin_x, track.bottom() - 2)
        
        # Draw filled portion (from origin to current value) with accent color
        if abs(display_val - self._origin) > 0.5:
            if display_val > self._origin:
                # Fill from origin to handle (right direction)
                fill_rect = QRect(origin_x, track.y(), handle_x - origin_x, track.height())
            else:
                # Fill from handle to origin (left direction)
                fill_rect = QRect(handle_x, track.y(), origin_x - handle_x, track.height())
            
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill_rect, self.slider_height // 2, self.slider_height // 2)
            
            # Use accent color for fill
            fill_gradient = QLinearGradient(fill_rect.topLeft(), fill_rect.bottomLeft())
            fill_gradient.setColorAt(0, self._accent_color.lighter(120))
            fill_gradient.setColorAt(0.5, self._accent_color)
            fill_gradient.setColorAt(1, self._accent_color.darker(110))
            
            painter.fillPath(fill_path, fill_gradient)
            
            # Subtle glow effect on fill with accent color
            glow_path = QPainterPath()
            glow_rect = fill_rect.adjusted(0, -1, 0, 1)
            glow_path.addRoundedRect(glow_rect, (self.slider_height + 2) // 2, (self.slider_height + 2) // 2)
            
            glow_gradient = QLinearGradient(glow_rect.topLeft(), glow_rect.bottomLeft())
            glow_color = QColor(self._accent_color)
            glow_color.setAlpha(30)
            glow_gradient.setColorAt(0, glow_color)
            glow_gradient.setColorAt(0.5, QColor(0, 0, 0, 0))
            glow_gradient.setColorAt(1, glow_color)
            
            painter.fillPath(glow_path, glow_gradient)
        
        # Draw handle - compact size for toolbar
        handle_radius = 7
        handle_center = QPoint(handle_x, track.center().y())
        
        # Handle glow when dragging with accent color
        if self._is_dragging:
            glow = QRadialGradient(handle_center, handle_radius + 6)
            glow_color = QColor(self._accent_color)
            glow_color.setAlpha(80)
            glow.setColorAt(0, glow_color)
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            painter.setBrush(glow)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(handle_center, handle_radius + 6, handle_radius + 6)
        
        # Handle gradient - dark gray
        handle_gradient = QRadialGradient(
            handle_center.x() - 2,
            handle_center.y() - 2,
            handle_radius * 1.5
        )
        handle_gradient.setColorAt(0, AnimKeyColors.SLIDER_HANDLE.lighter(130))
        handle_gradient.setColorAt(1, AnimKeyColors.SLIDER_HANDLE.darker(110))
        
        painter.setBrush(handle_gradient)
        painter.setPen(QPen(AnimKeyColors.SLIDER_HANDLE_BORDER, 1))
        painter.drawEllipse(handle_center, handle_radius, handle_radius)
        
        # Inner subtle highlight
        highlight = QRadialGradient(
            handle_center.x() - 1,
            handle_center.y() - 1,
            handle_radius * 0.6
        )
        highlight.setColorAt(0, QColor(255, 255, 255, 30))
        highlight.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(highlight)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(handle_center, int(handle_radius * 0.5), int(handle_radius * 0.5))
        
        # Draw value text when dragging
        if self._is_dragging:
            painter.setPen(AnimKeyColors.TEXT_PRIMARY)
            font = QFont("Segoe UI", 8, QFont.Bold)
            painter.setFont(font)
            value_text = f"{int(display_val)}%"
            painter.drawText(
                QRect(handle_x - 18, track.top() - 16, 36, 14),
                Qt.AlignCenter,
                value_text
            )

        # Draw Expand Tab
        tab_rect = self._get_tab_rect()
        painter.setPen(Qt.NoPen)
        tab_bg = QColor(self._accent_color)
        # Increase visibility: Base alpha 70, hovered + 80
        alpha_val = int(70 + 80 * getattr(self, 'tab_hover_alpha', 0.0))
        tab_bg.setAlpha(alpha_val)
        
        path_tab = QPainterPath()
        path_tab.addRoundedRect(tab_rect, 3, 3)
        painter.fillPath(path_tab, tab_bg)
        
        # Draw chevron < or >
        # Increase chevron visibility: Base alpha 200, hovered + 55
        painter.setPen(QPen(QColor(255, 255, 255, int(200 + 55 * getattr(self, 'tab_hover_alpha', 0.0))), 1.5))
        cy = tab_rect.center().y()
        cx = tab_rect.center().x()
        if getattr(self, '_is_expanded', False):
            # chevron pointing left
            painter.drawLine(cx + 2, cy - 3, cx - 1, cy)
            painter.drawLine(cx - 1, cy, cx + 2, cy + 3)
        else:
            # chevron pointing right
            painter.drawLine(cx - 1, cy - 3, cx + 2, cy)
            painter.drawLine(cx + 2, cy, cx - 1, cy + 3)


class AnimKeySliderWithButtons(QtWidgets.QWidget):
    """
    Modern slider widget with integrated buttons at the ends ()
    The buttons are visually part of the slider container
    """
    
    # Signals forwarded from the slider
    valueChanged = QtCore.Signal(int)
    dragStarted = QtCore.Signal()
    dragEnded = QtCore.Signal()
    
    def __init__(self, slider_type="tween", button_color="#f0c674", parent=None):
        super(AnimKeySliderWithButtons, self).__init__(parent)
        
        self.slider_type = slider_type
        self.button_color = button_color
        theme = ThemeManager.get_current_theme()
        
        # Main container with unified background
        container_layout = QtWidgets.QHBoxLayout(self)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # Create a styled container widget
        self.container = QtWidgets.QWidget()
        self.container.setFixedHeight(26)
        container_layout.addWidget(self.container)
        
        # Internal layout for the container
        layout = QtWidgets.QHBoxLayout(self.container)
        layout.setContentsMargins(3, 2, 3, 2)  # Small padding inside container
        layout.setSpacing(0)
        
        # Left button (<) - integrated into container, smaller like slider handle
        self.left_btn = QtWidgets.QPushButton("<")
        self.left_btn.setFixedSize(16, 16)  # Smaller, similar to slider handle size
        self.left_btn.setCursor(QtCore.Qt.PointingHandCursor)
        layout.addWidget(self.left_btn)
        
        # Spacer between button and slider
        layout.addSpacing(2)
        
        # Slider
        self.slider = AnimKeySlider(slider_type=slider_type)
        self.slider.setFixedHeight(22)
        layout.addWidget(self.slider)
        
        # Spacer between slider and button
        layout.addSpacing(2)
        
        # Right button (>) - integrated into container, smaller like slider handle
        self.right_btn = QtWidgets.QPushButton(">")
        self.right_btn.setFixedSize(16, 16)  # Smaller, similar to slider handle size
        self.right_btn.setCursor(QtCore.Qt.PointingHandCursor)
        layout.addWidget(self.right_btn)
        
        # Apply unified container styling
        self._update_styling()
        
        # Forward slider signals
        self.slider.valueChanged.connect(self.valueChanged.emit)
        self.slider.dragStarted.connect(self.dragStarted.emit)
        self.slider.dragEnded.connect(self.dragEnded.emit)
    
    def _update_styling(self):
        """Update the styling of the container and buttons"""
        theme = ThemeManager.get_current_theme()
        
        # Container styling - unified background that makes buttons look integrated
        self.container.setStyleSheet(f'''
            QWidget {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
            }}
        ''')
        
        # Button styling - minimal, integrated look, smaller size
        button_style = f'''
            QPushButton {{
                color: {theme["text_muted"]};
                background-color: transparent;
                border: none;
                border-radius: 2px;
                font-size: 11px;
                font-weight: bold;
                padding: 0px;
                min-width: 16px;
                min-height: 16px;
            }}
            QPushButton:hover {{
                color: {self.button_color};
                background-color: {theme["button_hover"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
                color: {theme["text_primary"]};
            }}
        '''
        
        self.left_btn.setStyleSheet(button_style)
        self.right_btn.setStyleSheet(button_style)
    
    def setMinimum(self, value):
        """Set minimum value of the slider"""
        self.slider.setMinimum(value)
    
    def setMaximum(self, value):
        """Set maximum value of the slider"""
        self.slider.setMaximum(value)
    
    def setValue(self, value):
        """Set value of the slider"""
        self.slider.setValue(value)
    
    def value(self):
        """Get current value of the slider"""
        return self.slider.value()
    
    def setFixedWidth(self, width):
        """Set fixed width of the widget"""
        super(AnimKeySliderWithButtons, self).setFixedWidth(width)
        # Calculate slider width: total - buttons - spacing - margins
        # buttons (16+16) + spacing (2+2) + margins (3+3) = 42
        slider_width = width - 42
        self.slider.setFixedWidth(max(100, slider_width))  # Minimum 100px for slider
    
    def setButtonColor(self, color):
        """Change the button hover color"""
        self.button_color = color
        self._update_styling()
    
    def getLeftButton(self):
        """Get the left button for connecting signals"""
        return self.left_btn
    
    def getRightButton(self):
        """Get the right button for connecting signals"""
        return self.right_btn
    
    def getSlider(self):
        """Get the slider widget"""
        return self.slider


class AnimKeyButton(QtWidgets.QPushButton):
    """
    Custom button with enhanced styling and optional icon support
    """
    
    def __init__(self, text="", icon=None, button_type="default", parent=None):
        super(AnimKeyButton, self).__init__(text, parent)
        
        self.button_type = button_type
        
        # Set icon if provided
        if icon:
            self.setIcon(QtGui.QIcon(icon))
            self.setIconSize(QtCore.QSize(20, 20))
        
        # Apply styling
        self.setStyleSheet(style.get_button_stylesheet(button_type))
        
        # Set cursor
        self.setCursor(QtCore.Qt.PointingHandCursor)
    
    def set_active(self, active):
        """Set the button to active/inactive state"""
        theme = ThemeManager.get_current_theme()
        if active:
            self.setStyleSheet(f'''
                QPushButton {{
                    background-color: {theme["accent_primary"]};
                    color: {theme["bg_primary"]};
                    border: none;
                    border-radius: {theme["border_radius"]};
                }}
            ''')
        else:
            self.setStyleSheet(style.get_button_stylesheet(self.button_type))


class AnimKeyIconButton(QtWidgets.QPushButton):
    """
    Icon-only button with tooltip support
    """
    
    def __init__(self, icon_path, tooltip="", size=28, parent=None):
        super(AnimKeyIconButton, self).__init__(parent)
        
        self.icon_path = icon_path
        self._size = size
        self._is_active = False
        
        # Set icon
        if icon_path:
            self.setIcon(QtGui.QIcon(icon_path))
            self.setIconSize(QtCore.QSize(size - 6, size - 6))
        
        # Set fixed size
        self.setFixedSize(size, size)
        
        # Set tooltip
        if tooltip:
            self.setToolTip(tooltip)
        
        # Apply styling
        self.setStyleSheet(style.get_button_stylesheet("icon"))
        
        # Set cursor
        self.setCursor(QtCore.Qt.PointingHandCursor)
    
    def set_active(self, active):
        """Toggle active state styling"""
        self._is_active = active
        theme = ThemeManager.get_current_theme()
        
        if active:
            self.setStyleSheet(f'''
                QPushButton {{
                    background-color: {theme["accent_primary"]};
                    border: none;
                    border-radius: {theme["border_radius"]};
                }}
                QPushButton:hover {{
                    background-color: {theme["accent_secondary"]};
                }}
            ''')
        else:
            self.setStyleSheet(style.get_button_stylesheet("icon"))
    
    def is_active(self):
        """Check if button is in active state"""
        return self._is_active


class AnimKeyLabel(QtWidgets.QLabel):
    """
    Custom label with theme support
    """
    
    def __init__(self, text="", label_type="default", parent=None):
        super(AnimKeyLabel, self).__init__(text, parent)
        self.setStyleSheet(style.get_label_stylesheet(label_type))


class AnimKeySeparator(QtWidgets.QFrame):
    """
    Visual separator for toolbar sections
    """
    
    def __init__(self, orientation="vertical", parent=None):
        super(AnimKeySeparator, self).__init__(parent)
        self.setObjectName("separator")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        
        theme = ThemeManager.get_current_theme()
        
        if orientation == "vertical":
            self.setFixedWidth(13)
            self.setStyleSheet(f"""
                QFrame#separator {{
                    background-color: {theme['border_color']};
                    margin-left: 5px;
                    margin-right: 5px;
                }}
            """)
        else:
            self.setFixedHeight(11)
            self.setStyleSheet(f"""
                QFrame#separator {{
                    background-color: {theme['border_color']};
                    margin-top: 5px;
                    margin-bottom: 5px;
                }}
            """)


class AnimKeyComboBox(QtWidgets.QComboBox):
    """
    Custom styled combo box
    """
    
    def __init__(self, parent=None):
        super(AnimKeyComboBox, self).__init__(parent)
        self.setStyleSheet(style.get_combobox_stylesheet())


class AnimKeySpinBox(QtWidgets.QSpinBox):
    """
    Custom styled spin box
    """
    
    def __init__(self, parent=None):
        super(AnimKeySpinBox, self).__init__(parent)
        self.setStyleSheet(style.get_input_stylesheet())


class AnimKeyLineEdit(QtWidgets.QLineEdit):
    """
    Custom styled line edit
    """
    
    def __init__(self, placeholder="", parent=None):
        super(AnimKeyLineEdit, self).__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setStyleSheet(style.get_input_stylesheet())


# ═══════════════════════════════════════════════════════════════════════════════
#                           DRAGGABLE WINDOW MIXIN
# ═══════════════════════════════════════════════════════════════════════════════

class DraggableWindowMixin:
    """
    Mixin class to add drag functionality to frameless windows
    """
    
    def init_draggable(self):
        """Initialize drag tracking variables"""
        self._drag_active = False
        self._drag_position = QtCore.QPoint()
    
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_active = True
            self._drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.LeftButton and self._drag_active:
            self.move(event.globalPos() - self._drag_position)
            event.accept()
    
    def mouseReleaseEvent(self, event):
        self._drag_active = False


# ═══════════════════════════════════════════════════════════════════════════════
#                           POPUP WINDOW BASE
# ═══════════════════════════════════════════════════════════════════════════════

class AnimKeyPopupWindow(QtWidgets.QWidget, DraggableWindowMixin):
    """
    Base class for popup windows with modern styling
    """
    
    def __init__(self, title="AnimKey", width=300, height=200, parent=None):
        if parent is None:
            parent = get_maya_main_window()
        
        super(AnimKeyPopupWindow, self).__init__(parent, QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        
        self.init_draggable()
        
        # Window setup
        self.setWindowTitle(title)
        self.resize(width, height)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        
        # Create main container with rounded corners
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the base UI structure"""
        theme = ThemeManager.get_current_theme()
        
        # Main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Central widget with styling
        self.central_widget = QtWidgets.QWidget()
        self.central_widget.setStyleSheet(f'''
            QWidget {{
                background-color: {theme["bg_primary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 10px;
            }}
        ''')
        main_layout.addWidget(self.central_widget)
        
        # Content layout
        self.content_layout = QtWidgets.QVBoxLayout(self.central_widget)
        self.content_layout.setContentsMargins(12, 12, 12, 12)
        self.content_layout.setSpacing(8)
        
        # Header with close button
        header_layout = QtWidgets.QHBoxLayout()
        header_layout.addStretch()
        
        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(style.get_close_button_stylesheet())
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)
        
        self.content_layout.addLayout(header_layout)
    
    def add_widget(self, widget):
        """Add a widget to the content area"""
        self.content_layout.addWidget(widget)
    
    def add_layout(self, layout):
        """Add a layout to the content area"""
        self.content_layout.addLayout(layout)
    
    def center_on_parent(self):
        """Center the window on its parent"""
        parent = self.parent()
        if parent:
            parent_geo = parent.geometry()
            x = parent_geo.x() + (parent_geo.width() - self.width()) // 2
            y = parent_geo.y() + (parent_geo.height() - self.height()) // 2
            self.move(x, y)


# ═══════════════════════════════════════════════════════════════════════════════
#                           TOOLTIP HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def create_tooltip_text(title, description, shortcuts=None):
    """
    Create formatted tooltip text with HTML styling
    
    Args:
        title: Main tooltip title
        description: Detailed description
        shortcuts: Optional dict of shortcut keys and their actions
    """
    theme = ThemeManager.get_current_theme()
    
    html = f'''
        <div style="font-family: {theme["font_family"]};">
            <b style="color: {theme["text_primary"]}; font-size: 12px;">{title}</b>
            <br><br>
            <span style="color: {theme["text_secondary"]}; font-size: 11px;">{description}</span>
    '''
    
    if shortcuts:
        html += f'''
            <br><br>
            <span style="color: {theme["accent_primary"]}; font-size: 10px;"><b>Shortcuts:</b></span>
            <br>
        '''
        for key, action in shortcuts.items():
            html += f'''
                <span style="color: {theme["text_muted"]}; font-size: 10px;">
                    {key}: {action}
                </span><br>
            '''
    
    html += '</div>'
    return html


# ═══════════════════════════════════════════════════════════════════════════════
#                           DRAG & DROP LAYOUT MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class DragDropReorderManager(QtCore.QObject):
    """
    Manages sophisticated drag and drop reordering for any QHBoxLayout.
    Applies overshoot animations (sliding) to surrounding elements when indices swap.
    Middle Mouse button initiates drag to avoid conflict with UI interactions.
    """
    reorderFinished = QtCore.Signal()

    def __init__(self, target_layout, parent=None):
        super(DragDropReorderManager, self).__init__(parent)
        self.layout = target_layout
        if target_layout is not None:
            self.target_widget = target_layout.parentWidget()
        else:
            self.target_widget = None
        self.pan_scroll_area = None
            
        self._drag_widget = None
        self._start_pos = None
        self._is_dragging = False
        
        self._proxy_label = None
        self._placeholder = None
        self._active_ghosts = []
        
        if self.target_widget:
            self.target_widget.installEventFilter(self)
        
        self.refresh()

    def refresh(self):
        if not self.layout: return
        for i in range(self.layout.count()):
            item = self.layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                self._recursively_install(w)

    def _recursively_install(self, widget):
        widget.removeEventFilter(self)
        widget.installEventFilter(self)
        # Fix: AnimKey relies heavily on layout wrappers, so we need to catch events on sub-components
        for child in widget.findChildren(QtWidgets.QWidget):
            child.removeEventFilter(self)
            child.installEventFilter(self)

    def _get_top_item(self, obj):
        # Traverse up from the clicked widget to find the direct child of the target_layout
        if not self.target_widget: return None
        w = obj
        try:
            # Traverse up until we find the direct child of the target_widget
            while w:
                p = w.parentWidget()
                if p == self.target_widget:
                    return w
                # Stop if we hit a window boundary or None
                if p is None or p.isWindow():
                    break
                w = p
            return None
        except (RuntimeError, AttributeError):
            return None

    def set_locked(self, locked):
        """External API to force lock/unlock state (called by scriptJob)"""
        self._force_locked = locked

    def is_maya_locked(self):
        try:
            import maya.cmds as cmds
            import maya.mel as mel
            for var in ("workspacesLockLayout", "WorkspaceLayoutLocked", 
                        "workspaceLayoutLockUi", "workspacesLocked"):
                if cmds.optionVar(exists=var) and cmds.optionVar(q=var):
                    return True
            try:
                if mel.eval('global int $gWorkspaceControlLock; $temp = $gWorkspaceControlLock;') == 1:
                    return True
            except Exception:
                pass
        except Exception:
            pass
        return False

    def eventFilter(self, obj, event):
        # Shift + Middle-Click for rearranging. Plain middle mouse is reserved
        # for panning the scrollable toolbar strip.
        if event.type() == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.MiddleButton:
                if not (event.modifiers() & QtCore.Qt.ShiftModifier):
                    if self.pan_scroll_area is not None:
                        return self.pan_scroll_area.begin_pan_from_event(event)
                    return False
                top_item = self._get_top_item(obj)
                if top_item and not self.is_maya_locked():
                    self._start_pos = event.globalPos()
                    self._drag_widget = top_item
                    if self.target_widget:
                        self.target_widget.grabMouse()
                    return True
            return False

        elif event.type() == QtCore.QEvent.MouseMove:
            if self.pan_scroll_area is not None and self.pan_scroll_area.is_panning():
                return self.pan_scroll_area.update_pan_from_event(event)
            if self._start_pos and not self._is_dragging:
                if (event.globalPos() - self._start_pos).manhattanLength() > QApplication.startDragDistance():
                    self.start_drag(event.globalPos())
                return True
            if self._is_dragging:
                # Removed 'top_item' check here because during drag, mouse can be anywhere
                self.update_drag(event.globalPos())
                return True

        elif event.type() == QtCore.QEvent.MouseButtonRelease:
            if event.button() == QtCore.Qt.MiddleButton:
                if self.pan_scroll_area is not None and self.pan_scroll_area.is_panning():
                    return self.pan_scroll_area.end_pan_from_event()
                if self._is_dragging:
                    self.finish_drag()
                    return True
                if self._start_pos is not None:
                    self._start_pos = None
                    self._drag_widget = None
                    if self.target_widget:
                        try:
                            self.target_widget.releaseMouse()
                        except:
                            pass
                    return True

        return False

    def start_drag(self, global_pos):
        self._is_dragging = True
        
        pixmap = getattr(self._drag_widget, 'grab')() if hasattr(self._drag_widget, 'grab') else None
        
        self._proxy_label = QtWidgets.QLabel(self.target_widget.window())
        if pixmap:
            self._proxy_label.setPixmap(pixmap)
            self._proxy_label.setScaledContents(True)
        self._proxy_label.setFixedSize(self._drag_widget.size())
        self._proxy_label.setWindowFlags(QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self._proxy_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self._proxy_label.show()
        
        self._drag_offset = self._drag_widget.mapFromGlobal(global_pos)
        self._proxy_label.move(global_pos - self._drag_offset)
        
        self._placeholder = QtWidgets.QWidget()
        self._placeholder.setFixedSize(self._drag_widget.size())
        
        idx = self.layout.indexOf(self._drag_widget)
        self.layout.insertWidget(idx, self._placeholder)
        
        self._drag_widget.hide()
        self.target_widget.setCursor(QtCore.Qt.ClosedHandCursor)
        
        # VERY IMPORTANT: Grab the mouse so Qt doesn't drop MouseMove events!
        if self.target_widget:
            self.target_widget.grabMouse()

    def update_drag(self, global_pos):
        if not self._is_dragging or not self._proxy_label:
            return
            
        self._proxy_label.move(global_pos - self._drag_offset)
        
        target_pos = self.target_widget.mapFromGlobal(global_pos)
        curr_idx = self.layout.indexOf(self._placeholder)
        new_idx = curr_idx
        
        for i in range(self.layout.count()):
            item = self.layout.itemAt(i)
            if not item.widget() or item.widget() == self._placeholder:
                continue
                
            w = item.widget()
            if not w.isVisible(): continue
            
            geo = w.geometry()
            center_x = geo.center().x()
            
            if curr_idx < i and target_pos.x() > center_x:
                new_idx = i
            elif curr_idx > i and target_pos.x() < center_x:
                new_idx = i

        if new_idx != curr_idx:
            displaced_w = self.layout.itemAt(new_idx).widget()
            
            if displaced_w and displaced_w != self._placeholder:
                old_geo = displaced_w.geometry()
                
                # Move the placeholder to the new position
                self.layout.removeWidget(self._placeholder)
                self.layout.insertWidget(new_idx, self._placeholder)
                
                # Force the layout to compute the new positions AFTER placeholder is moved
                self.target_widget.layout().update()
                self.target_widget.layout().activate()
                
                # Now we precisely know where displaced_w MUST go
                new_geo = displaced_w.geometry()
                
                ghost = QtWidgets.QLabel(self.target_widget)
                if hasattr(displaced_w, 'grab'):
                    ghost.setPixmap(displaced_w.grab())
                ghost.setScaledContents(True)
                ghost.setGeometry(old_geo)
                ghost.show()
                
                # Make displaced_w invisible without removing it from the layout
                effect = QtWidgets.QGraphicsOpacityEffect(displaced_w)
                effect.setOpacity(0)
                displaced_w.setGraphicsEffect(effect)
                
                anim = QtCore.QPropertyAnimation(ghost, b"geometry")
                anim.setDuration(400)
                anim.setStartValue(old_geo)
                anim.setEndValue(new_geo)
                
                curve = QtCore.QEasingCurve(QtCore.QEasingCurve.OutBack)
                curve.setOvershoot(0.6)
                anim.setEasingCurve(curve)
                
                def on_anim_finished(g=ghost, w=displaced_w):
                    g.deleteLater()
                    if w and getattr(self, 'layout', None) and self.layout.indexOf(w) != -1:
                        w.setGraphicsEffect(None)
                        
                anim.finished.connect(on_anim_finished)
                anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
                self._active_ghosts.append(anim)

    def finish_drag(self):
        if not self._is_dragging: return
        self._is_dragging = False
        self.target_widget.unsetCursor()
        self.target_widget.releaseMouse()
        
        idx = self.layout.indexOf(self._placeholder)
        self.layout.removeWidget(self._placeholder)
        self._placeholder.deleteLater()
        self._placeholder = None
        
        self.layout.insertWidget(idx, self._drag_widget)
        
        # Show it so layout calculates correctly, but visually hide it with opacity
        self._drag_widget.show()
        effect = QtWidgets.QGraphicsOpacityEffect(self._drag_widget)
        effect.setOpacity(0)
        self._drag_widget.setGraphicsEffect(effect)
        
        self.target_widget.layout().update()
        self.target_widget.layout().activate()
        
        final_geo = self._drag_widget.geometry()
        
        if self._proxy_label is not None:
            anim = QtCore.QPropertyAnimation(self._proxy_label, b"pos")
            anim.setDuration(300)
            anim.setStartValue(self._proxy_label.pos())
            tgt_pos = self.target_widget.mapToGlobal(final_geo.topLeft())
            anim.setEndValue(tgt_pos)
            
            curve = QtCore.QEasingCurve(QtCore.QEasingCurve.OutBack)
            anim.setEasingCurve(curve)
            
            def finish_proxy(l=self._proxy_label, w=self._drag_widget):
                l.deleteLater()
                if w: w.setGraphicsEffect(None)
                
            anim.finished.connect(finish_proxy)
            anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
            self._active_ghosts.append(anim)
            self._proxy_label = None
            
        self._start_pos = None
        self._drag_widget = None

        # Notify listeners that a reorder just completed
        self.reorderFinished.emit()

# ═══════════════════════════════════════════════════════════════════════════════
#                           CONTEXT POPUP WINDOW BASE
# ═══════════════════════════════════════════════════════════════════════════════

class ContextPopupWindow(QtWidgets.QWidget):
    """
    Base class for context-menu style popups anchored to a button.
    Provides the rounded frameless window and speech-bubble tail.
    """
    def __init__(self, anchor_button=None, parent=None):
        if parent is None:
            parent = get_maya_main_window()
        close_animkey_tool_windows()
        super().__init__(parent)
        self.anchor_button = anchor_button
        
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint | QtCore.Qt.NoDropShadowWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        
        self._tail_height = 10
        self._tail_width = 16
        self._tail_x = 0
        self._tail_on_top = False  # False = tail at bottom, True = tail at top
        self._magnet_attached = True
        self._magnet_snap_distance = 42
        self._drag_active = False
        self._drag_started = False
        self._drag_start_global = QtCore.QPoint()
        self._drag_window_pos = QtCore.QPoint()
        self._drag_offset = QtCore.QPoint()
        
        # Default size, subclass should resize
        self.resize(340, 520)

    def paintEvent(self, event):
        """Draw solid rounded rect + speech bubble tail."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bg = QColor(58, 58, 58)
        border_color = QColor(90, 90, 90)
        tail_height = self._tail_height if self._magnet_attached else 0

        if self._tail_on_top and self._magnet_attached:
            body_rect = QtCore.QRectF(0.5, tail_height + 0.5,
                                       self.width() - 1,
                                       self.height() - tail_height - 1)
        else:
            body_rect = QtCore.QRectF(0.5, 0.5,
                                       self.width() - 1,
                                       self.height() - tail_height - 1)

        path = QPainterPath()
        path.addRoundedRect(body_rect, 10, 10)

        if self._magnet_attached:
            tail_cx = max(20, min(self._tail_x, self.width() - 20))
            hw = self._tail_width / 2

            if self._tail_on_top:
                tail_base = body_rect.top()
                path.moveTo(tail_cx - hw, tail_base)
                path.lineTo(tail_cx, tail_base - tail_height)
                path.lineTo(tail_cx + hw, tail_base)
            else:
                tail_base = body_rect.bottom()
                path.moveTo(tail_cx - hw, tail_base)
                path.lineTo(tail_cx, tail_base + tail_height)
                path.lineTo(tail_cx + hw, tail_base)
            path.closeSubpath()

        # Apply mask so the widget shape matches the rounded path exactly
        self.setMask(path.toFillPolygon().toPolygon())

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(bg)
        painter.drawPath(path)
        painter.end()

    def _set_body_margins(self):
        if not self.layout():
            return
        if not self._magnet_attached:
            self.layout().setContentsMargins(1, 1, 1, 1)
        elif self._tail_on_top:
            self.layout().setContentsMargins(1, self._tail_height + 1, 1, 1)
        else:
            self.layout().setContentsMargins(1, 1, 1, self._tail_height + 1)

    def _anchored_position_data(self):
        if self.anchor_button is None:
            cursor_pos = QtGui.QCursor.pos()
            return (
                cursor_pos.x() - self.width() // 2,
                cursor_pos.y() - self.height() - 10,
                self.width() // 2,
                False,
            )

        btn_rect = self.anchor_button.rect()
        btn_top_left = self.anchor_button.mapToGlobal(btn_rect.topLeft())
        btn_center_x = btn_top_left.x() + btn_rect.width() // 2
        btn_top_y = btn_top_left.y()
        btn_bottom_y = btn_top_y + btn_rect.height()

        screen = QtWidgets.QApplication.screenAt(btn_top_left) if hasattr(QtWidgets.QApplication, 'screenAt') else None
        if screen:
            screen_rect = screen.availableGeometry()
        else:
            desktop = QtWidgets.QApplication.desktop()
            screen_rect = desktop.availableGeometry(self.anchor_button)

        popup_w = self.width()
        popup_h = self.height()

        space_above = btn_top_y - screen_rect.top()
        space_below = screen_rect.bottom() - btn_bottom_y

        if space_above >= popup_h:
            y_pos = btn_top_y - popup_h
            tail_on_top = False
        elif space_below >= popup_h:
            y_pos = btn_bottom_y
            tail_on_top = True
        else:
            if space_above >= space_below:
                y_pos = btn_top_y - popup_h
                tail_on_top = False
            else:
                y_pos = btn_bottom_y
                tail_on_top = True

        x_pos = btn_center_x - popup_w // 2

        if x_pos < screen_rect.left():
            x_pos = screen_rect.left()
        elif x_pos + popup_w > screen_rect.right():
            x_pos = screen_rect.right() - popup_w

        return x_pos, y_pos, btn_center_x - x_pos, tail_on_top

    def position_window(self, force=False):
        """Position the popup anchored to the button, like a context menu."""
        if not self._magnet_attached and not force:
            return

        x_pos, y_pos, tail_x, tail_on_top = self._anchored_position_data()
        self._magnet_attached = True
        self._tail_x = tail_x
        self._tail_on_top = tail_on_top
        self.move(x_pos, y_pos)
        self._set_body_margins()

        self.update()

    def attach_to_anchor(self):
        self._magnet_attached = True
        self.position_window(force=True)

    def detach_from_anchor(self):
        if not self._magnet_attached:
            return
        self._magnet_attached = False
        self._set_body_margins()
        self.clearMask()
        self.update()

    def animkey_auto_hide(self):
        if self._magnet_attached:
            self.hide()
            return True
        return False

    def _looks_like_slider_drag_widget(self, widget):
        if widget is None:
            return False

        names = [widget.__class__.__name__]
        try:
            names.append(widget.metaObject().className())
        except Exception:
            pass
        try:
            names.append(widget.objectName())
        except Exception:
            pass

        if any("slider" in name.lower() for name in names if name):
            return True
        if hasattr(widget, "sliderPressed") and hasattr(widget, "sliderReleased"):
            return True
        return all(hasattr(widget, attr) for attr in ("_get_track_rect", "_value_to_x", "_is_dragging"))

    def _is_interactive_drag_widget(self, obj):
        interactive_types = (
            QtWidgets.QAbstractButton,
            QtWidgets.QComboBox,
            QtWidgets.QLineEdit,
            QtWidgets.QTextEdit,
            QtWidgets.QPlainTextEdit,
            QtWidgets.QSpinBox,
            QtWidgets.QDoubleSpinBox,
            QtWidgets.QSlider,
            QtWidgets.QScrollBar,
            QtWidgets.QAbstractItemView,
        )
        widget = obj if isinstance(obj, QtWidgets.QWidget) else None
        while widget is not None and widget is not self:
            if self._looks_like_slider_drag_widget(widget):
                return True
            if isinstance(widget, interactive_types):
                return True
            widget = widget.parentWidget()
        return False

    def _install_magnetic_drag_filters(self):
        widgets = [self]
        widgets.extend(self.findChildren(QtWidgets.QWidget))
        for widget in widgets:
            if self._is_interactive_drag_widget(widget):
                continue
            try:
                widget.removeEventFilter(self)
                widget.installEventFilter(self)
            except Exception:
                pass

    def showEvent(self, event):
        self._install_magnetic_drag_filters()
        super(ContextPopupWindow, self).showEvent(event)

    def _maybe_snap_to_anchor(self):
        if self.anchor_button is None:
            return
        try:
            x_pos, y_pos, tail_x, tail_on_top = self._anchored_position_data()
        except Exception:
            return
        target = QtCore.QPoint(x_pos, y_pos)
        distance = (self.pos() - target).manhattanLength()
        if distance <= self._magnet_snap_distance:
            self._magnet_attached = True
            self._tail_x = tail_x
            self._tail_on_top = tail_on_top
            self.move(target)
            self._set_body_margins()
            self.update()

    def eventFilter(self, obj, event):
        event_type = event.type()

        if event_type == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.LeftButton and not self._is_interactive_drag_widget(obj):
                self._drag_active = True
                self._drag_started = False
                self._drag_start_global = event.globalPos()
                self._drag_window_pos = self.pos()
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
                try:
                    obj.setCursor(QtCore.Qt.ClosedHandCursor)
                except Exception:
                    pass
                return True

        elif event_type == QtCore.QEvent.MouseMove:
            if self._drag_active and event.buttons() & QtCore.Qt.LeftButton:
                if not self._drag_started:
                    delta = event.globalPos() - self._drag_start_global
                    if delta.manhattanLength() < QtWidgets.QApplication.startDragDistance():
                        return True
                    self._drag_started = True
                    self.detach_from_anchor()
                self.move(event.globalPos() - self._drag_offset)
                return True

        elif event_type == QtCore.QEvent.MouseButtonRelease:
            if self._drag_active and event.button() == QtCore.Qt.LeftButton:
                self._drag_active = False
                try:
                    obj.unsetCursor()
                except Exception:
                    pass
                if self._drag_started:
                    self._maybe_snap_to_anchor()
                return True

        return super(ContextPopupWindow, self).eventFilter(obj, event)

