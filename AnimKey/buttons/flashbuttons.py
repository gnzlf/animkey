"""
    AnimKey Button: Flash Buttons
    
    Panel circular de botones personalizables que aparece en el viewport.
    Los botones permiten asignar funciones del toolbar de AnimKey.
"""

from maya import cmds
from maya import OpenMayaUI as omui
import math
import json
import os
import random

from AnimKey.mods.storage import atomic_write_json, read_json

from AnimKey.mods.maya_compat import (
    QtCore, QtGui, QtWidgets, execute_qt, wrap_instance as wrapInstance,
)

Qt = QtCore.Qt
QApplication = QtWidgets.QApplication
QAbstractItemView = QtWidgets.QAbstractItemView
QComboBox = QtWidgets.QComboBox
QDialog = QtWidgets.QDialog
QGraphicsOpacityEffect = QtWidgets.QGraphicsOpacityEffect
QGroupBox = QtWidgets.QGroupBox
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QLineEdit = QtWidgets.QLineEdit
QMenu = QtWidgets.QMenu
QMessageBox = QtWidgets.QMessageBox
QPushButton = QtWidgets.QPushButton
QTabBar = QtWidgets.QTabBar
QTabWidget = QtWidgets.QTabWidget
QTextEdit = QtWidgets.QTextEdit
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget
QColor = QtGui.QColor
QCursor = QtGui.QCursor
QIcon = QtGui.QIcon
QLinearGradient = QtGui.QLinearGradient
QPainter = QtGui.QPainter
QPen = QtGui.QPen
QPixmap = QtGui.QPixmap
QRadialGradient = QtGui.QRadialGradient
QEasingCurve = QtCore.QEasingCurve
QPoint = QtCore.QPoint
QPropertyAnimation = QtCore.QPropertyAnimation
QRect = QtCore.QRect
QRectF = QtCore.QRectF
QSize = QtCore.QSize
QTimer = QtCore.QTimer
Signal = QtCore.Signal


def get_maya_main_window():
    """Obtiene la ventana principal de Maya"""
    main_window_ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(main_window_ptr), QWidget)


def get_flash_buttons_folder(create=True):
    """Retorna la carpeta de configuracion de Flash Buttons dentro de tools."""
    from AnimKey.mods import configMod

    folder = os.path.join(configMod.get_user_folder_path(), "tools", "flash_buttons")
    if create and not os.path.exists(folder):
        os.makedirs(folder)
    return folder


# ==============================================================================
#                           AVAILABLE ACTIONS
# ==============================================================================

def get_available_actions():
    """Returns all available AnimKey actions that can be assigned to buttons"""
    actions = {
        # Main toolbar buttons
        "Isolate": ("AnimKey.buttons.isolate", "execute"),
        "Align Objects": ("AnimKey.buttons.align_objects", "execute"),
        "Align Position": ("AnimKey.buttons.align_objects", "align_position"),
        "Align Orientation": ("AnimKey.buttons.align_objects", "align_orientation"),
        "Align Scale": ("AnimKey.buttons.align_objects", "align_scale"),
        "Trail": ("AnimKey.buttons.trail", "execute"),
        "Reset Values": ("AnimKey.buttons.resetValues", "execute"),
        "Reset to Default": ("AnimKey.buttons.resetValues", "reset_to_default"),
        "Save as Default": ("AnimKey.buttons.resetValues", "save_as_default"),
        "Select Opposite": ("AnimKey.buttons.selectOpposite", "execute"),
        "Mirror": ("AnimKey.buttons.mirror", "execute"),
        "Mirror Pose": ("AnimKey.buttons.mirror", "mirror_pose"),
        "Mirror Animation": ("AnimKey.buttons.mirror", "mirror_animation"),
        "Copy Animation": ("AnimKey.buttons.copyAnimation", "execute"),
        "Paste Animation": ("AnimKey.buttons.copyAnimation", "paste"),
        "Paste Flipped": ("AnimKey.buttons.copyAnimation", "paste_flipped"),
        "Micro Move": ("AnimKey.buttons.microMove", "execute"),
        "Follow Cam": ("AnimKey.buttons.followCam", "execute"),
        "Link Objects": ("AnimKey.buttons.linkObjects", "execute"),
        "Copy Worldspace": ("AnimKey.buttons.copyWorldspace", "execute"),
        "Temp Control": ("AnimKey.buttons.tempPivot", "execute"),
        "Animation Offset": ("AnimKey.buttons.animation_offset", "execute"),
        "Select Hierarchy": ("AnimKey.buttons.select_hierarchy", "execute"),
        # Tangents
        "Plateau Tangent": ("AnimKey.buttons.tangents", "execute_plateau"),
        "Step Tangent": ("AnimKey.buttons.tangents", "execute_step"),
        "Flat Tangent": ("AnimKey.buttons.tangents", "execute_flat"),
        "Linear Tangent": ("AnimKey.buttons.tangents", "execute_linear"),
        "Clamped Tangent": ("AnimKey.buttons.tangents", "execute_clamped"),
        "Spline Tangent": ("AnimKey.buttons.tangents", "execute_spline"),
        "Auto Tangent": ("AnimKey.buttons.tangents", "execute_auto"),
        # Extra tools
        "ReBlock": ("AnimKey.buttons.reblock", "execute"),
        "Gimbal Fixer": ("AnimKey.buttons.gimbalFixer", "execute"),
        "Bake Animation": ("AnimKey.buttons.bakeAnim", "execute"),
        "Retimer": ("AnimKey.buttons.retimer", "execute"),
        "Selection Sets": ("AnimKey.buttons.selectionSets", "execute"),
        "Anim Crash": ("AnimKey.buttons.animCrash", "execute"),
    }
    return actions


FLASH_ACTION_ICON_KEYS = {
    "Isolate": "ISO",
    "Align Objects": "ALN",
    "Align Position": "ALN",
    "Align Orientation": "ALN",
    "Align Scale": "ALN",
    "Trail": "TRL",
    "Reset Values": "RST",
    "Reset to Default": "RST",
    "Save as Default": "RST",
    "Select Opposite": "OPP",
    "Mirror": "MIR",
    "Mirror Pose": "MIR",
    "Mirror Animation": "MIR",
    "Copy Animation": "C",
    "Paste Animation": "C",
    "Paste Flipped": "C",
    "Micro Move": "RUL",
    "Follow Cam": "CAM",
    "Link Objects": "LKN",
    "Copy Worldspace": "WS",
    "Temp Control": "PIV",
    "Animation Offset": "OFF",
    "Select Hierarchy": "HIR",
    "Plateau Tangent": "PLT",
    "Step Tangent": "STP",
    "Flat Tangent": "FLT",
    "Linear Tangent": "LIN",
    "Clamped Tangent": "CLP",
    "Spline Tangent": "SPL",
    "Auto Tangent": "AUT",
    "ReBlock": "RBK",
    "Gimbal Fixer": "GMB",
    "Bake Animation": "BAK",
    "Retimer": "RTM",
    "Selection Sets": "SETS",
    "Anim Crash": "ACR",
}

FLASH_LABEL_ICON_KEYS = {
    "ISO": "ISO",
    "ISOLATE": "ISO",
    "ALN": "ALN",
    "ALIGN": "ALN",
    "ALIGNOBJECTS": "ALN",
    "OPP": "OPP",
    "SELECTOPPOSITE": "OPP",
    "TRL": "TRL",
    "TRAIL": "TRL",
    "OFF": "OFF",
    "OFFSET": "OFF",
    "ANIMATIONOFFSET": "OFF",
    "RST": "RST",
    "RESET": "RST",
    "RESETVALUES": "RST",
    "HIR": "HIR",
    "HIERARCHY": "HIR",
    "SELECTHIERARCHY": "HIR",
    "MIR": "MIR",
    "MIRROR": "MIR",
    "C": "C",
    "COPY": "C",
    "COPYANIMATION": "C",
    "LKN": "LKN",
    "LINK": "LKN",
    "LINKOBJECTS": "LKN",
    "CAM": "CAM",
    "FOLLOWCAM": "CAM",
    "WS": "WS",
    "WORLDSPACE": "WS",
    "COPYWORLDSPACE": "WS",
    "PIV": "PIV",
    "PIVOT": "PIV",
    "TEMPCONTROL": "PIV",
    "RUL": "RUL",
    "MICROMOVE": "RUL",
    "PLT": "PLT",
    "PLATEAU": "PLT",
    "STP": "STP",
    "STEP": "STP",
    "FLT": "FLT",
    "FLAT": "FLT",
    "LIN": "LIN",
    "LINEAR": "LIN",
    "CLP": "CLP",
    "CLAMPED": "CLP",
    "SPL": "SPL",
    "SPLINE": "SPL",
    "AUT": "AUT",
    "AUTO": "AUT",
    "GMB": "GMB",
    "GIMBAL": "GMB",
    "BAK": "BAK",
    "BAKE": "BAK",
    "RTM": "RTM",
    "RETIMER": "RTM",
    "SETS": "SETS",
    "SELSETS": "SETS",
    "SELECTIONSETS": "SETS",
    "ACR": "ACR",
    "CRASH": "ACR",
    "ANIMCRASH": "ACR",
    "BTNS": "BTNS",
    "FLASHBUTTONS": "BTNS",
}


def _normalize_flash_label(text):
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def get_flash_icon_key(function_data=None, label_text=""):
    """Return the toolbar icon key that best represents a Flash Button."""
    function_data = function_data or {}
    if function_data.get("script_type") == "custom":
        return None

    action_name = function_data.get("action", "")
    if action_name in FLASH_ACTION_ICON_KEYS:
        return FLASH_ACTION_ICON_KEYS[action_name]

    return FLASH_LABEL_ICON_KEYS.get(_normalize_flash_label(label_text))


def get_flash_icon_path(icon_key):
    if not icon_key:
        return None
    try:
        from AnimKey.mods import mediaMod as media
        icon_path = media.get_button_icon(icon_key)
    except Exception:
        icon_path = None
    if icon_path and os.path.exists(icon_path):
        return icon_path
    return None


def get_flash_lock_icon_path():
    try:
        from AnimKey.mods import mediaMod as media
        icon_path = media.get_icon("animkey_btn_LockFB_128.png")
    except Exception:
        icon_path = None
    if icon_path and os.path.exists(icon_path):
        return icon_path
    return None


# ==============================================================================
#                           FLASH BUTTON (CIRCULAR)
# ==============================================================================

class FlashButton(QPushButton):
    """Botón circular con color que cambia al hover"""
    
    # Signals para comunicar con el panel
    position_changed = Signal(object, QPoint)
    delete_requested = Signal(int)  # Emite button_id cuando se quiere borrar
    edit_requested = Signal(int)    # Emite button_id cuando se quiere editar
    
    # Colores base para los botones (oscuros pero con matiz de color)
    BASE_COLORS = [
        QColor(72, 77, 85),
        QColor(79, 74, 84),
        QColor(73, 82, 77),
        QColor(84, 78, 72),
        QColor(82, 74, 78),
        QColor(81, 81, 74),
    ]
    
    # Colores vibrantes para hover
    HOVER_COLORS = [
        QColor(142, 168, 202),
        QColor(177, 157, 199),
        QColor(139, 180, 153),
        QColor(200, 157, 124),
        QColor(197, 143, 157),
        QColor(188, 171, 118),
        QColor(133, 181, 188),
        QColor(188, 146, 136),
    ]
    
    def __init__(self, size, label, button_id, base_color_index=None, parent=None):
        super(FlashButton, self).__init__(parent)
        self.btn_size = size
        self.original_size = size
        self.label_text = label
        self.button_id = button_id
        self.hover = False
        self.pressed_state = False
        self.is_locked = True
        self.dragging = False
        self.drag_start_pos = None
        self.function_data = {}
        self.icon_key = None
        self.icon_path = None
        self._icon_cache_key = None
        self._icon_pixmap = QPixmap()
        
        # Color base con matiz (no completamente negro)
        if base_color_index is not None:
            self.base_color = self.BASE_COLORS[base_color_index % len(self.BASE_COLORS)]
        else:
            self.base_color = random.choice(self.BASE_COLORS)
        self.base_color_index = base_color_index if base_color_index is not None else self.BASE_COLORS.index(self.base_color)
        
        # Color para hover (aleatorio)
        self.hover_color = random.choice(self.HOVER_COLORS)
        self.pressed_color = self.base_color.darker(106)
        
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        
        # Menú contextual
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.set_function_data({'label': label})
    
    def set_function_data(self, function_data):
        """Refresh label, tooltip and icon from the assigned action data."""
        self.function_data = function_data or {}
        self.label_text = self.function_data.get('label', self.label_text) or "BTN"
        self.icon_key = get_flash_icon_key(self.function_data, self.label_text)
        self.icon_path = get_flash_icon_path(self.icon_key)
        self._icon_cache_key = None
        self._icon_pixmap = QPixmap()

        action_name = self.function_data.get('action') or self.label_text
        if self.function_data.get('script_type') == 'custom':
            action_name = f"Custom Script - {self.label_text}"
        self.setToolTip(action_name)
        self.update()
    
    def set_locked(self, locked):
        """Bloquea o desbloquea el botón para arrastrar"""
        self.is_locked = locked
        if locked:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)
        self.update()
    
    def show_context_menu(self, pos):
        """Muestra el menú contextual para editar el botón"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(248, 249, 252, 245);
                color: #46515d;
                border: 1px solid rgba(150, 164, 185, 190);
                border-radius: 8px;
                padding: 5px;
            }
            QMenu::item {
                padding: 8px 22px;
                border-radius: 5px;
            }
            QMenu::item:selected {
                background-color: rgba(225, 235, 249, 230);
            }
        """)
        
        edit_action = menu.addAction("✏️ Edit Function")
        menu.addSeparator()
        delete_action = menu.addAction("🗑️ Delete Button")
        
        action = execute_qt(menu, self.mapToGlobal(pos))
        
        if action == edit_action:
            self.edit_requested.emit(self.button_id)
        elif action == delete_action:
            self.request_delete()
    
    def request_delete(self):
        """Solicita borrar este botón"""
        reply = QMessageBox.question(
            self,
            'Delete Button',
            f'Delete button "{self.label_text}"?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.delete_requested.emit(self.button_id)
    
    def _legacy_paintEvent(self, event):
        """Dibuja el botón circular con color"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Determinar color según estado
        if self.pressed_state:
            color = self.pressed_color
            border_color = self.hover_color
        elif self.hover:
            color = self.hover_color
            border_color = self.hover_color.lighter(130)
        else:
            color = self.base_color
            border_color = self.base_color.lighter(150)
        
        # Dibujar sombra
        shadow_rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
        shadow_gradient = QRadialGradient(shadow_rect.center(), self.width() / 2)
        shadow_gradient.setColorAt(0, QColor(0, 0, 0, 80))
        shadow_gradient.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(shadow_gradient)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(shadow_rect)
        
        # Dibujar círculo principal
        circle_rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        
        # Gradiente según estado
        gradient = QRadialGradient(circle_rect.center(), self.width() / 2)
        gradient.setColorAt(0, color.lighter(130))
        gradient.setColorAt(0.6, color)
        gradient.setColorAt(1, color.darker(130))
        
        painter.setBrush(gradient)
        painter.setPen(QPen(border_color, 2))
        painter.drawEllipse(circle_rect)
        
        # Dibujar brillo
        shine_rect = QRectF(
            self.width() * 0.2, 
            self.height() * 0.1, 
            self.width() * 0.4, 
            self.height() * 0.35
        )
        shine_gradient = QRadialGradient(shine_rect.center(), self.width() / 3)
        if self.hover:
            shine_gradient.setColorAt(0, QColor(255, 255, 255, 120))
        else:
            shine_gradient.setColorAt(0, QColor(255, 255, 255, 60))
        shine_gradient.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(shine_gradient)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(shine_rect)
        
        # Indicador de modo edición (desbloqueado)
        if not self.is_locked:
            # Dibujar X de borrar
            delete_rect = QRectF(self.width() - 18, 2, 16, 16)
            painter.setBrush(QColor(200, 60, 60, 200))
            painter.setPen(QPen(QColor(255, 100, 100), 1))
            painter.drawEllipse(delete_rect)
            
            # X
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.drawLine(
                int(delete_rect.center().x() - 3), int(delete_rect.center().y() - 3),
                int(delete_rect.center().x() + 3), int(delete_rect.center().y() + 3)
            )
            painter.drawLine(
                int(delete_rect.center().x() + 3), int(delete_rect.center().y() - 3),
                int(delete_rect.center().x() - 3), int(delete_rect.center().y() + 3)
            )
        
        # Dibujar texto
        if self.hover:
            painter.setPen(QColor(255, 255, 255))
        else:
            painter.setPen(QColor(200, 200, 200))
        
        font = painter.font()
        font.setPointSize(max(7, int(self.width() / 7)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(circle_rect, Qt.AlignCenter, self.label_text)
    
    def _fallback_label(self):
        label = (self.label_text or "BTN").strip()
        if len(label) <= 5:
            return label.upper()
        words = [w for w in label.replace("_", " ").split(" ") if w]
        if len(words) > 1:
            return "".join(w[0] for w in words[:4]).upper()
        return label[:5].upper()

    def _scaled_icon(self, max_size):
        if not self.icon_path:
            return QPixmap()
        cache_key = (self.icon_path, int(max_size))
        if self._icon_cache_key == cache_key:
            return self._icon_pixmap
        pixmap = QPixmap(self.icon_path)
        if pixmap.isNull():
            self._icon_pixmap = QPixmap()
        else:
            self._icon_pixmap = pixmap.scaled(
                int(max_size),
                int(max_size),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
        self._icon_cache_key = cache_key
        return self._icon_pixmap

    def paintEvent(self, event):
        """Draw a circular grey Flash Button with a subtle colored tint."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        accent = self.hover_color if self.hover or self.pressed_state else self.HOVER_COLORS[
            self.base_color_index % len(self.HOVER_COLORS)
        ]
        if self.pressed_state:
            color = self.pressed_color
        elif self.hover:
            color = self.base_color.lighter(106)
        else:
            color = self.base_color

        circle_rect = QRectF(3, 2, self.width() - 8, self.height() - 8)
        shadow_rect = QRectF(5, 6, self.width() - 10, self.height() - 10)
        shadow_gradient = QRadialGradient(shadow_rect.center(), shadow_rect.width() * 0.55)
        shadow_gradient.setColorAt(0.0, QColor(0, 0, 0, 54 if self.hover else 36))
        shadow_gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(shadow_gradient)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(shadow_rect)

        body_gradient = QLinearGradient(circle_rect.topLeft(), circle_rect.bottomRight())
        body_gradient.setColorAt(0.0, color.lighter(128))
        body_gradient.setColorAt(0.44, color.lighter(104))
        body_gradient.setColorAt(1.0, color.darker(132))

        rim_color = color.lighter(158)
        painter.setBrush(body_gradient)
        painter.setPen(QPen(rim_color, 1.25))
        painter.drawEllipse(circle_rect)

        accent_rim = QColor(accent)
        accent_rim.setAlpha(72 if self.hover else 42)
        painter.setPen(QPen(accent_rim, 2.0 if self.hover else 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(circle_rect.adjusted(2.5, 2.5, -2.5, -2.5))

        rim_rect = circle_rect.adjusted(5, 5, -5, -5)
        painter.setPen(QPen(QColor(255, 255, 255, 88 if self.hover else 58), 2.0))
        painter.drawArc(rim_rect, 42 * 16, 94 * 16)

        if not self.is_locked:
            delete_rect = QRectF(self.width() - 18, 3, 15, 15)
            painter.setBrush(QColor(188, 94, 102, 235))
            painter.setPen(QPen(QColor(232, 160, 166, 220), 1))
            painter.drawEllipse(delete_rect)

            painter.setPen(QPen(QColor(255, 255, 255), 1.6))
            painter.drawLine(
                int(delete_rect.center().x() - 3), int(delete_rect.center().y() - 3),
                int(delete_rect.center().x() + 3), int(delete_rect.center().y() + 3)
            )
            painter.drawLine(
                int(delete_rect.center().x() + 3), int(delete_rect.center().y() - 3),
                int(delete_rect.center().x() - 3), int(delete_rect.center().y() + 3)
            )

        icon_size = int(min(self.width(), self.height()) * 0.66)
        icon = self._scaled_icon(icon_size)
        if not icon.isNull():
            x = int((self.width() - icon.width()) / 2)
            y = int((self.height() - icon.height()) / 2) - 1
            painter.drawPixmap(x, y, icon)
        else:
            painter.setPen(QColor(228, 232, 238))
            font = painter.font()
            font.setPointSize(max(8, int(self.width() / 7)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(circle_rect, Qt.AlignCenter, self._fallback_label())

    def enterEvent(self, event):
        """Efecto hover - nuevo color aleatorio"""
        self.hover = True
        self.hover_color = random.choice(self.HOVER_COLORS)
        self.update()
        
        if self.is_locked:
            if not hasattr(self, 'base_pos'):
                self.base_pos = self.pos()
            
            if hasattr(self, 'animation') and self.animation.state() == QPropertyAnimation.Running:
                self.animation.stop()
            
            # Animación de escala
            new_size = int(self.original_size * 1.15)
            offset = (new_size - self.original_size) // 2
            
            self.animation = QPropertyAnimation(self, b"geometry")
            new_geo = QRect(
                self.base_pos.x() - offset,
                self.base_pos.y() - offset,
                new_size,
                new_size
            )
            
            self.animation.setDuration(150)
            self.animation.setStartValue(self.geometry())
            self.animation.setEndValue(new_geo)
            self.animation.setEasingCurve(QEasingCurve.OutBack)
            self.animation.start()
    
    def leaveEvent(self, event):
        """Fin del hover"""
        self.hover = False
        self.update()
        
        if self.is_locked and hasattr(self, 'base_pos'):
            if hasattr(self, 'animation') and self.animation.state() == QPropertyAnimation.Running:
                self.animation.stop()
            
            self.animation = QPropertyAnimation(self, b"geometry")
            restore_geo = QRect(
                self.base_pos.x(),
                self.base_pos.y(),
                self.original_size,
                self.original_size
            )
            
            self.animation.setDuration(150)
            self.animation.setStartValue(self.geometry())
            self.animation.setEndValue(restore_geo)
            self.animation.setEasingCurve(QEasingCurve.InBack)
            self.animation.finished.connect(lambda: self.move(self.base_pos))
            self.animation.start()
    
    def mousePressEvent(self, event):
        """Estado presionado y inicio de arrastre"""
        if event.button() == Qt.LeftButton:
            # Check if clicking on delete button area (when unlocked)
            if not self.is_locked:
                delete_rect = QRectF(self.width() - 18, 2, 16, 16)
                if delete_rect.contains(event.pos()):
                    self.request_delete()
                    return
            
            self.pressed_state = True
            self.update()
            
            if not self.is_locked:
                self.dragging = True
                self.drag_start_pos = event.pos()
                self.setCursor(Qt.ClosedHandCursor)
            else:
                super(FlashButton, self).mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        """Movimiento durante el arrastre"""
        if self.dragging and not self.is_locked:
            delta = event.pos() - self.drag_start_pos
            new_pos = self.pos() + delta
            
            parent_rect = self.parent().rect()
            new_pos.setX(max(0, min(new_pos.x(), parent_rect.width() - self.width())))
            new_pos.setY(max(0, min(new_pos.y(), parent_rect.height() - self.height())))
            
            self.move(new_pos)
            self.position_changed.emit(self, new_pos)
        else:
            super(FlashButton, self).mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event):
        """Estado liberado"""
        if event.button() == Qt.LeftButton:
            self.pressed_state = False
            self.update()
            
            if self.dragging:
                self.dragging = False
                if not self.is_locked:
                    self.setCursor(Qt.OpenHandCursor)
                    self.base_pos = self.pos()
                self.position_changed.emit(self, self.pos())
            else:
                super(FlashButton, self).mouseReleaseEvent(event)


# ==============================================================================
#                           BUTTON EDIT DIALOG
# ==============================================================================

class ButtonEditDialog(QDialog):
    """Diálogo para editar la función de un botón con tabs - Estilo moderno"""
    
    def __init__(self, button, parent=None):
        super(ButtonEditDialog, self).__init__(parent)
        self.button = button
        self.setWindowTitle("Edit Flash Button")
        self.setModal(True)
        self.resize(450, 420)
        
        # Frameless window with rounded corners
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        self.setStyleSheet("""
            QDialog {
                background-color: transparent;
            }
            #mainContainer {
                background-color: rgba(30, 30, 35, 240);
                border: 1px solid rgba(80, 80, 90, 180);
                border-radius: 12px;
            }
            QLabel {
                color: #e0e0e0;
                background: transparent;
            }
            QLabel#titleLabel {
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
                background: transparent;
            }
            QLineEdit, QComboBox, QTextEdit {
                background-color: rgba(50, 50, 55, 200);
                color: #e0e0e0;
                border: 1px solid rgba(80, 80, 90, 150);
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #4a6b8a;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid rgba(100, 150, 200, 200);
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #888;
            }
            QComboBox QAbstractItemView {
                background-color: rgba(40, 40, 45, 250);
                color: #e0e0e0;
                border: 1px solid rgba(80, 80, 90, 150);
                border-radius: 4px;
                selection-background-color: rgba(70, 100, 140, 200);
            }
            QPushButton {
                background-color: rgba(55, 55, 60, 200);
                color: #e0e0e0;
                border: 1px solid rgba(80, 80, 90, 150);
                border-radius: 6px;
                padding: 8px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(70, 70, 75, 220);
                border: 1px solid rgba(100, 100, 110, 180);
            }
            QPushButton#saveBtn {
                background-color: rgba(60, 100, 80, 220);
                border: 1px solid rgba(80, 140, 100, 180);
            }
            QPushButton#saveBtn:hover {
                background-color: rgba(70, 120, 90, 240);
            }
            QPushButton#closeBtn {
                background-color: rgba(80, 50, 50, 200);
                border: 1px solid rgba(120, 70, 70, 150);
                padding: 4px 10px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton#closeBtn:hover {
                background-color: rgba(120, 60, 60, 220);
            }
            QTabWidget::pane {
                background-color: rgba(35, 35, 40, 200);
                border: 1px solid rgba(70, 70, 80, 150);
                border-radius: 8px;
                padding: 8px;
            }
            QTabBar::tab {
                background-color: rgba(45, 45, 50, 180);
                color: #b0b0b0;
                padding: 10px 20px;
                border: 1px solid rgba(60, 60, 70, 150);
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: rgba(55, 80, 110, 220);
                color: #ffffff;
            }
            QTabBar::tab:hover:!selected {
                background-color: rgba(55, 55, 60, 200);
            }
            QGroupBox {
                background-color: rgba(40, 40, 45, 150);
                border: 1px solid rgba(70, 70, 80, 120);
                border-radius: 8px;
                margin-top: 12px;
                padding: 12px;
                padding-top: 20px;
            }
            QGroupBox::title {
                color: #a0a0a0;
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        
        # Obtener datos actuales del botón
        panel = parent
        if hasattr(panel, 'button_functions') and button.button_id in panel.button_functions:
            self.current_data = panel.button_functions[button.button_id]
        else:
            self.current_data = {
                'label': button.label_text,
                'action': '',
                'script_type': 'animkey',
                'custom_script': '',
            }
        
        self.setup_ui()
        self.load_data()
    
    def setup_ui(self):
        """Configura la interfaz del diálogo"""
        # Main layout (for the transparent dialog)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        
        # Main container with rounded corners
        self.main_container = QWidget()
        self.main_container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(16, 12, 16, 16)
        container_layout.setSpacing(12)
        
        # ─────────────────────────────────────────────────────────────────────
        # TITLE BAR (draggable)
        # ─────────────────────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(30)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        
        title_label = QLabel("✏️ Edit Flash Button")
        title_label.setObjectName("titleLabel")
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.reject)
        title_layout.addWidget(close_btn)
        
        container_layout.addWidget(title_bar)
        
        # ─────────────────────────────────────────────────────────────────────
        # LABEL NAME
        # ─────────────────────────────────────────────────────────────────────
        label_group = QGroupBox("Button Label")
        label_layout = QHBoxLayout(label_group)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Enter label (e.g: ISO, MIR, etc)")
        label_layout.addWidget(self.label_edit)
        container_layout.addWidget(label_group)
        
        outer_layout.addWidget(self.main_container)
        
        # Store reference to container_layout for adding more widgets
        self._container_layout = container_layout
        
        # ─────────────────────────────────────────────────────────────────────
        # TABS FOR ACTION TYPE
        # ─────────────────────────────────────────────────────────────────────
        self.tab_widget = QTabWidget()
        
        # ═══════════════════════════════════════════════════════════════════
        # TAB 1: AnimKey Actions
        # ═══════════════════════════════════════════════════════════════════
        animkey_tab = QWidget()
        animkey_layout = QVBoxLayout(animkey_tab)
        animkey_layout.setContentsMargins(10, 15, 10, 10)
        
        # Description
        desc1 = QLabel("Select an AnimKey toolbar function:")
        desc1.setStyleSheet("color: #888;")
        animkey_layout.addWidget(desc1)
        
        # Dropdown with categories
        self.action_combo = QComboBox()
        self.action_combo.setMinimumHeight(30)
        self.action_combo.addItem("-- Select Action --", "")
        
        # Add actions grouped by category
        self._populate_action_combo()
        
        animkey_layout.addWidget(self.action_combo)
        animkey_layout.addStretch()
        
        # Preview of selected action
        self.action_preview = QLabel("")
        self.action_preview.setStyleSheet("color: #6a9; font-size: 10px; padding: 5px;")
        self.action_preview.setWordWrap(True)
        animkey_layout.addWidget(self.action_preview)
        
        self.action_combo.currentIndexChanged.connect(self._on_action_changed)
        
        self.tab_widget.addTab(animkey_tab, "🎯 AnimKey Actions")
        
        # ═══════════════════════════════════════════════════════════════════
        # TAB 2: Custom Script
        # ═══════════════════════════════════════════════════════════════════
        script_tab = QWidget()
        script_layout = QVBoxLayout(script_tab)
        script_layout.setContentsMargins(10, 15, 10, 10)
        
        # Script type selector
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Script Type:"))
        self.script_type_combo = QComboBox()
        self.script_type_combo.addItem("Python", "python")
        self.script_type_combo.addItem("MEL", "mel")
        type_layout.addWidget(self.script_type_combo)
        type_layout.addStretch()
        script_layout.addLayout(type_layout)
        
        # Script editor
        script_layout.addWidget(QLabel("Custom Script:"))
        self.script_edit = QTextEdit()
        self.script_edit.setPlaceholderText("Enter your Python or MEL script here...\n\nExample Python:\nimport maya.cmds as cmds\ncmds.warning('Hello!')\n\nExample MEL:\nwarning \"Hello from MEL!\";")
        self.script_edit.setMinimumHeight(150)
        self.script_edit.setStyleSheet("""
            QTextEdit {
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 11px;
            }
        """)
        script_layout.addWidget(self.script_edit)
        
        self.tab_widget.addTab(script_tab, "📝 Custom Script")
        
        self._container_layout.addWidget(self.tab_widget)
        
        # ─────────────────────────────────────────────────────────────────────
        # TIP
        # ─────────────────────────────────────────────────────────────────────
        tip_label = QLabel("💡 Use AnimKey Actions for toolbar shortcuts, Custom Script for your own code")
        tip_label.setStyleSheet("color: #888; font-size: 10px;")
        tip_label.setWordWrap(True)
        self._container_layout.addWidget(tip_label)
        
        # ─────────────────────────────────────────────────────────────────────
        # BUTTONS
        # ─────────────────────────────────────────────────────────────────────
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("✓ Save")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self.accept)
        save_btn.setDefault(True)
        button_layout.addWidget(save_btn)
        
        self._container_layout.addLayout(button_layout)
    
    def _populate_action_combo(self):
        """Populate the action combo with categorized actions"""
        # Categories for better organization
        categories = {
            "── Main Tools ──": [
                ("Isolate", "Isolate"),
                ("Select Opposite", "Select Opposite"),
                ("Mirror", "Mirror"),
                ("Copy Animation", "Copy Animation"),
                ("Reset Values", "Reset Values"),
                ("Micro Move", "Micro Move"),
            ],
            "── Alignment ──": [
                ("Align Objects", "Align Objects"),
                ("Align Position", "Align Position"),
                ("Align Orientation", "Align Orientation"),
                ("Align Scale", "Align Scale"),
            ],
            "── Animation ──": [
                ("Copy Animation", "Copy Animation"),
                ("Paste Animation", "Paste Animation"),
                ("Animation Offset", "Animation Offset"),
                ("Trail", "Trail"),
                ("Retimer", "Retimer"),
            ],
            "── Tangents ──": [
                ("Plateau Tangent", "Plateau Tangent"),
                ("Step Tangent", "Step Tangent"),
                ("Flat Tangent", "Flat Tangent"),
                ("Linear Tangent", "Linear Tangent"),
                ("Clamped Tangent", "Clamped Tangent"),
                ("Spline Tangent", "Spline Tangent"),
                ("Auto Tangent", "Auto Tangent"),
            ],
            "── Utilities ──": [
                ("Follow Cam", "Follow Cam"),
                ("Link Objects", "Link Objects"),
                ("Copy Worldspace", "Copy Worldspace"),
                ("Temp Control", "Temp Control"),
                ("Select Hierarchy", "Select Hierarchy"),
            ],
            "── Extra Tools ──": [
                ("ReBlock", "ReBlock"),
                ("Gimbal Fixer", "Gimbal Fixer"),
                ("Bake Animation", "Bake Animation"),
                ("Selection Sets", "Selection Sets"),
                ("Anim Crash", "Anim Crash"),
            ],
        }
        
        for category, items in categories.items():
            # Add category header (not selectable)
            self.action_combo.addItem(category, "")
            # Make the category header look different
            idx = self.action_combo.count() - 1
            self.action_combo.model().item(idx).setEnabled(False)
            
            # Add items in category
            for display_name, action_key in items:
                self.action_combo.addItem(f"    {display_name}", action_key)
    
    def _on_action_changed(self, index):
        """Update preview when action changes"""
        action_key = self.action_combo.currentData()
        if action_key:
            actions = get_available_actions()
            if action_key in actions:
                module, func = actions[action_key]
                self.action_preview.setText(f"→ {module}.{func}()")
            else:
                self.action_preview.setText("")
        else:
            self.action_preview.setText("")
    
    def load_data(self):
        """Carga los datos actuales del botón"""
        self.label_edit.setText(self.current_data.get('label', ''))
        
        # Determine which tab to show
        script_type = self.current_data.get('script_type', 'animkey')
        custom_script = self.current_data.get('custom_script', '')
        action = self.current_data.get('action', '')
        
        if script_type == 'custom' and custom_script:
            # Show custom script tab
            self.tab_widget.setCurrentIndex(1)
            self.script_edit.setPlainText(custom_script)
            # Set script language
            script_lang = self.current_data.get('script_lang', 'python')
            idx = self.script_type_combo.findData(script_lang)
            if idx >= 0:
                self.script_type_combo.setCurrentIndex(idx)
        else:
            # Show AnimKey action tab
            self.tab_widget.setCurrentIndex(0)
            # Find and select the action
            for i in range(self.action_combo.count()):
                if self.action_combo.itemData(i) == action:
                    self.action_combo.setCurrentIndex(i)
                    break
    
    def get_data(self):
        """Retorna los datos editados"""
        current_tab = self.tab_widget.currentIndex()
        
        if current_tab == 0:
            # AnimKey action
            return {
                'label': self.label_edit.text() or "BTN",
                'action': self.action_combo.currentData() or "",
                'script_type': 'animkey',
                'custom_script': '',
                'script_lang': 'python',
            }
        else:
            # Custom script
            return {
                'label': self.label_edit.text() or "BTN",
                'action': '',
                'script_type': 'custom',
                'custom_script': self.script_edit.toPlainText(),
                'script_lang': self.script_type_combo.currentData(),
            }
    
    def mousePressEvent(self, event):
        """Allow dragging the frameless window"""
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event):
        """Handle window dragging"""
        if event.buttons() == Qt.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPos() - self._drag_pos)
            event.accept()


# ==============================================================================
#                           FLASH BUTTONS PANEL
# ==============================================================================

class FlashButtonsPanel(QWidget):
    """Panel principal con botones circulares"""
    
    def __init__(self, parent=None):
        super(FlashButtonsPanel, self).__init__(parent)
        
        # Configuración de la ventana
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        
        # Parámetros
        self.button_size = 55
        self.spacing = 10
        self.buttons = []
        self.button_functions = {}
        self.button_positions = {}
        self.next_button_id = 0
        
        # Estado
        self.is_visible = False
        self.is_locked = True
        self.mouse_inside = False
        
        # Timer para detectar mouse fuera del área
        self.hide_timer = QTimer(self)
        self.hide_timer.timeout.connect(self.check_mouse_position)
        self.hide_timer.setInterval(50)
        
        # Configurar UI
        self.setup_ui()
        self.load_config()
        
        # Posicionar en el centro de la pantalla
        self.center_on_screen()
        
        # Efecto de opacidad
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)
        
        self.setMouseTracking(True)
    
    def setup_ui(self):
        """Configura la interfaz de usuario"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 50, 50, 50)  # Reduced margins
        
        # Widget central para los botones
        self.button_container = QWidget()
        self.button_container.setStyleSheet("background: transparent;")
        self.button_container.setMouseTracking(True)
        layout.addWidget(self.button_container)
        
        # Botones de control (candado y agregar)
        self.create_control_buttons()
        
        # Si no hay config guardada, crear un solo botón inicial
        if not self.buttons:
            self.create_initial_button()
        
        # Ajustar tamaño
        self.update_panel_size()
    
    def create_control_buttons(self):
        """Crea los botones de control (candado, agregar)"""
        control_layout = QHBoxLayout()
        control_layout.setSpacing(6)
        control_btn_style = """
            QPushButton {
                background-color: rgba(72, 76, 84, 225);
                color: #e5e8ee;
                border: 1px solid rgba(150, 156, 166, 170);
                border-radius: 14px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(88, 94, 104, 240);
                border-color: rgba(190, 198, 210, 220);
            }
            QPushButton:pressed {
                background-color: rgba(62, 66, 74, 245);
            }
        """
        
        # Botón de candado
        self.lock_btn = QPushButton("🔒")
        self.lock_btn.setText("")
        self.lock_btn.setFixedSize(28, 28)
        self.lock_btn.setToolTip("Lock/Unlock buttons for dragging")
        self.lock_btn.clicked.connect(self.toggle_lock)
        self._lock_fb_has_icon = False
        lock_icon_path = get_flash_lock_icon_path()
        if lock_icon_path:
            lock_icon = QIcon(lock_icon_path)
            if not lock_icon.isNull():
                self.lock_btn.setIcon(lock_icon)
                self.lock_btn.setIconSize(QSize(21, 21))
                self._lock_fb_has_icon = True
        if not self._lock_fb_has_icon:
            self.lock_btn.setText("L")
        self.lock_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(50, 50, 50, 200);
                color: white;
                border: 2px solid rgba(100, 220, 130, 200);
                border-radius: 15px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(70, 70, 70, 220);
            }
        """)
        self.lock_btn.setStyleSheet(control_btn_style)
        control_layout.addWidget(self.lock_btn)
        
        # Botón agregar
        add_btn = QPushButton("➕")
        add_btn.setText("+")
        add_btn.setFixedSize(28, 28)
        add_btn.setToolTip("Add new button")
        add_btn.clicked.connect(self.add_new_button)
        add_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(50, 50, 50, 200);
                color: white;
                border: 2px solid rgba(0, 200, 255, 200);
                border-radius: 15px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(70, 70, 70, 220);
            }
        """)
        add_btn.setStyleSheet(control_btn_style)
        control_layout.addWidget(add_btn)
        
        control_layout.addStretch()
        self.layout().insertLayout(0, control_layout)
    
    def toggle_lock(self):
        """Alterna el estado de bloqueo"""
        self.is_locked = not self.is_locked
        self.lock_btn.setText("🔒" if self.is_locked else "🔓")
        
        self.lock_btn.setText("L" if self.is_locked else "M")
        if getattr(self, "_lock_fb_has_icon", False):
            self.lock_btn.setText("")

        for button in self.buttons:
            button.set_locked(self.is_locked)
    
    def create_initial_button(self):
        """Crea un solo botón inicial en el centro"""
        # Center in the container (container size is 340x340 after update_panel_size)
        container_size = 340  # 500 - 160
        center_x = (container_size - self.button_size) / 2
        center_y = (container_size - self.button_size) / 2
        self.add_button_at_position(center_x, center_y, "+")
    
    def add_button_at_position(self, x, y, label=None, button_data=None):
        """Agrega un botón en una posición específica"""
        button_id = self.next_button_id
        self.next_button_id += 1
        
        if label is None:
            label = "+"
        
        # Get color index from button_data or use button_id for variety
        color_index = button_data.get('color_index', button_id) if button_data else button_id
        
        button = FlashButton(self.button_size, label, button_id, color_index, self.button_container)
        button.move(int(x), int(y))
        button.base_pos = QPoint(int(x), int(y))
        
        # Connect signals - use default value for checked to handle PySide2/6 differences
        button.clicked.connect(lambda checked=False, bid=button_id: self.execute_button_function(bid))
        button.position_changed.connect(self.on_button_position_changed)
        button.delete_requested.connect(self.remove_button)
        button.edit_requested.connect(self.edit_button)
        
        button.hide()
        button.set_locked(self.is_locked)
        
        self.buttons.append(button)
        self.button_positions[button_id] = QPoint(int(x), int(y))
        
        # Guardar función
        if button_data:
            self.button_functions[button_id] = button_data
        else:
            self.button_functions[button_id] = {
                'label': label,
                'action': '',
                'color_index': color_index,
            }
        button.set_function_data(self.button_functions[button_id])
        
        return button
    
    def on_button_position_changed(self, button, new_pos):
        """Callback cuando un botón cambia de posición"""
        self.button_positions[button.button_id] = new_pos
        button.base_pos = new_pos
        self.save_config()
    
    def edit_button(self, button_id):
        """Abre el diálogo para editar un botón"""
        button = None
        for btn in self.buttons:
            if btn.button_id == button_id:
                button = btn
                break
        
        if button is None:
            return
        
        dialog = ButtonEditDialog(button, self)
        if execute_qt(dialog):
            data = dialog.get_data()
            button.set_function_data(data)
            self.update_button_function(button_id, data)
    
    def add_new_button(self):
        """Agrega un nuevo botón - lo coloca en círculo alrededor del centro"""
        num_buttons = len(self.buttons)
        
        if num_buttons == 0:
            x, y = 0, 0
        else:
            # Calcular posición en círculo
            angle = (num_buttons - 1) * (2 * math.pi / max(6, num_buttons))
            radius = self.button_size + self.spacing + 10
            x = radius * math.cos(angle - math.pi/2)
            y = radius * math.sin(angle - math.pi/2)
        
        # Ajustar al centro del container
        container_center_x = self.button_container.width() / 2 - self.button_size / 2
        container_center_y = self.button_container.height() / 2 - self.button_size / 2
        
        button = self.add_button_at_position(
            container_center_x + x, 
            container_center_y + y, 
            "+"
        )
        
        # Mostrar el botón con animación
        button.show()
        animation = QPropertyAnimation(button, b"geometry")
        center = self.button_container.rect().center()
        start_geo = QRect(center.x(), center.y(), 0, 0)
        end_geo = QRect(int(container_center_x + x), int(container_center_y + y), self.button_size, self.button_size)
        
        animation.setDuration(300)
        animation.setStartValue(start_geo)
        animation.setEndValue(end_geo)
        animation.setEasingCurve(QEasingCurve.OutBack)
        animation.start()
        
        # Abrir editor después de la animación
        QTimer.singleShot(350, lambda: self.edit_button(button.button_id))
        
        self.save_config()
    
    def remove_button(self, button_id):
        """Elimina un botón inmediatamente"""
        button_to_remove = None
        button_index = -1
        
        for i, button in enumerate(self.buttons):
            if button.button_id == button_id:
                button_to_remove = button
                button_index = i
                break
        
        if button_to_remove:
            # Remove from list first
            if button_index >= 0:
                self.buttons.pop(button_index)
            
            # Remove from data
            if button_id in self.button_functions:
                del self.button_functions[button_id]
            if button_id in self.button_positions:
                del self.button_positions[button_id]
            
            # Hide and delete immediately
            button_to_remove.hide()
            button_to_remove.setParent(None)
            button_to_remove.deleteLater()
            
            # Save config
            self.save_config()
            
            print(f"✓ Button {button_id} deleted")
    
    def reorganize_buttons(self):
        """Reorganiza los botones en círculo alrededor del centro"""
        if not self.buttons:
            return
        
        container_center_x = self.button_container.width() / 2 - self.button_size / 2
        container_center_y = self.button_container.height() / 2 - self.button_size / 2
        
        num_buttons = len(self.buttons)
        
        for i, button in enumerate(self.buttons):
            if num_buttons == 1:
                x, y = 0, 0
            else:
                angle = i * (2 * math.pi / num_buttons)
                radius = self.button_size + self.spacing + 10
                x = radius * math.cos(angle - math.pi/2)
                y = radius * math.sin(angle - math.pi/2)
            
            new_pos = QPoint(int(container_center_x + x), int(container_center_y + y))
            button.base_pos = new_pos
            self.button_positions[button.button_id] = new_pos
            
            # Animar a la nueva posición
            animation = QPropertyAnimation(button, b"pos")
            animation.setDuration(200)
            animation.setStartValue(button.pos())
            animation.setEndValue(new_pos)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.start()
        
        self.save_config()
    
    def update_button_function(self, button_id, data):
        """Actualiza la función de un botón"""
        self.button_functions[button_id] = data
        for button in self.buttons:
            if button.button_id == button_id:
                button.set_function_data(data)
                break
        self.save_config()
    
    def on_button_moved(self, button, new_pos):
        """Callback cuando un botón es movido"""
        pass
    
    def save_button_position(self, button_id, pos):
        """Guarda la posición de un botón"""
        self.button_positions[button_id] = pos
        
        for button in self.buttons:
            if button.button_id == button_id:
                button.base_pos = pos
                break
        
        self.save_config()
    
    def update_panel_size(self):
        """Actualiza el tamaño del panel"""
        base_size = 500
        self.setFixedSize(base_size, base_size)
        # Container más grande para dar espacio a los botones
        self.button_container.setFixedSize(base_size - 100, base_size - 100)
    
    def paintEvent(self, event):
        """Dibuja el fondo del panel"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        center = self.rect().center()
        radius = min(self.width(), self.height()) / 2 - 40
        
        # Fondo circular semi-transparente (más transparente)
        gradient = QRadialGradient(center, radius)
        gradient.setColorAt(0, QColor(25, 25, 30, 140))   # Más transparente
        gradient.setColorAt(0.7, QColor(15, 15, 20, 100))
        gradient.setColorAt(1, QColor(0, 0, 0, 0))
        
        painter.setBrush(gradient)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, int(radius), int(radius))
        
        self.panel_radius = radius
        self.panel_center = center
    
    def mouseMoveEvent(self, event):
        """Detecta cuando el mouse está dentro del área"""
        if hasattr(self, 'panel_center') and hasattr(self, 'panel_radius'):
            dx = event.pos().x() - self.panel_center.x()
            dy = event.pos().y() - self.panel_center.y()
            distance = math.sqrt(dx * dx + dy * dy)
            self.mouse_inside = distance <= self.panel_radius
        
        super(FlashButtonsPanel, self).mouseMoveEvent(event)
    
    def leaveEvent(self, event):
        """Detecta cuando el mouse sale del widget"""
        self.mouse_inside = False
        if self.is_visible:
            QTimer.singleShot(100, self.check_and_hide)
        super(FlashButtonsPanel, self).leaveEvent(event)
    
    def check_and_hide(self):
        """Verifica y oculta si el mouse sigue fuera"""
        if not self.mouse_inside and self.is_visible:
            global_pos = QCursor.pos()
            local_pos = self.mapFromGlobal(global_pos)
            if not self.rect().contains(local_pos):
                self.hide_panel()
    
    def check_mouse_position(self):
        """Verifica si el mouse está fuera del panel"""
        if not self.mouse_inside and self.is_visible:
            global_pos = QCursor.pos()
            local_pos = self.mapFromGlobal(global_pos)
            
            if not self.rect().contains(local_pos):
                self.hide_panel()
    
    def center_on_screen(self):
        """Posiciona el panel centrado donde está el mouse"""
        cursor_pos = QCursor.pos()
        screen_geo = QApplication.primaryScreen().geometry()
        
        # Calcular posición centrada en el cursor
        x = cursor_pos.x() - self.width() / 2
        y = cursor_pos.y() - self.height() / 2
        
        # Mantener dentro de la pantalla
        x = max(0, min(x, screen_geo.width() - self.width()))
        y = max(0, min(y, screen_geo.height() - self.height()))
        
        self.move(int(x), int(y))
    
    def toggle_visibility(self):
        """Alterna la visibilidad del panel"""
        if self.is_visible:
            self.hide_panel()
        else:
            self.show_panel()
    
    def show_panel(self):
        """Muestra el panel con animación"""
        self.is_visible = True
        self.mouse_inside = True
        self.show()
        self.raise_()
        
        self.hide_timer.start()
        
        # Animación de opacidad
        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_animation.setDuration(300)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.fade_animation.start()
        
        # Animar botones desde el centro
        center = self.button_container.rect().center()
        
        for i, button in enumerate(self.buttons):
            button.show()
            
            animation = QPropertyAnimation(button, b"geometry")
            start_geo = QRect(center.x(), center.y(), 0, 0)
            
            if button.button_id in self.button_positions:
                pos = self.button_positions[button.button_id]
                end_geo = QRect(pos.x(), pos.y(), self.button_size, self.button_size)
            else:
                end_geo = button.geometry()
                end_geo.setSize(QSize(self.button_size, self.button_size))
            
            animation.setDuration(500)
            animation.setStartValue(start_geo)
            animation.setEndValue(end_geo)
            animation.setEasingCurve(QEasingCurve.OutElastic)
            
            QTimer.singleShot(i * 50, animation.start)
    
    def hide_panel(self):
        """Oculta el panel inmediatamente"""
        self.is_visible = False
        self.hide_timer.stop()
        self.opacity_effect.setOpacity(0.0)
        self.hide()
    
    def execute_button_function(self, button_id):
        """Ejecuta la función de un botón (AnimKey action o script personalizado)"""
        # Ocultar el panel primero
        self.hide_panel()
        
        if button_id not in self.button_functions:
            cmds.warning("Flash Buttons: Button has no function data")
            return
        
        func_data = self.button_functions[button_id]
        script_type = func_data.get('script_type', 'animkey')
        custom_script = func_data.get('custom_script', '')
        action_name = func_data.get('action', '')
        
        print(f"[Flash Buttons] Executing button {button_id}: script_type={script_type}, action={action_name}")
        from AnimKey.core.executionGuard import animkey_execution
        
        # ─────────────────────────────────────────────────────────────────────
        # CUSTOM SCRIPT
        # ─────────────────────────────────────────────────────────────────────
        if script_type == 'custom' and custom_script.strip():
            script_lang = func_data.get('script_lang', 'python')
            
            try:
                with animkey_execution("flash_buttons", func_data.get('label', 'Custom Script')):
                    if script_lang == 'mel':
                        import maya.mel as mel
                        mel.eval(custom_script)
                    else:
                        exec(custom_script)
                print(f"✓ Executed custom {script_lang} script")
            except Exception as e:
                cmds.warning(f"Flash Buttons: Error executing script: {str(e)}")
            return
        
        # ─────────────────────────────────────────────────────────────────────
        # ANIMKEY ACTION
        # ─────────────────────────────────────────────────────────────────────
        if not action_name:
            cmds.warning("Flash Buttons: No action assigned to this button. Right-click to edit.")
            return
        
        actions = get_available_actions()
        if action_name not in actions:
            cmds.warning(f"Flash Buttons: Action '{action_name}' not found. Available: {list(actions.keys())}")
            return
        
        module_path, func_name = actions[action_name]
        
        try:
            import importlib
            module = importlib.import_module(module_path)
            func = getattr(module, func_name)
            with animkey_execution("flash_buttons", action_name):
                func()
            print(f"✓ Executed: {action_name}")
        except Exception as e:
            cmds.warning(f"Flash Buttons: Error executing {action_name}: {str(e)}")
    
    def get_config_path(self):
        """Obtiene la ruta del archivo de configuración"""
        return os.path.join(get_flash_buttons_folder(create=True), "config.json")
    
    def save_config(self):
        """Guarda la configuración actual"""
        config = {
            'buttons': {},
            'positions': {},
            'next_id': self.next_button_id,
        }
        
        for button_id, func_data in self.button_functions.items():
            config['buttons'][str(button_id)] = func_data
        
        for button_id, pos in self.button_positions.items():
            config['positions'][str(button_id)] = {
                'x': pos.x(),
                'y': pos.y()
            }
        
        try:
            atomic_write_json(self.get_config_path(), config, indent=2)
        except Exception as e:
            print(f"Flash Buttons: Error saving config: {e}")
    
    def load_config(self):
        """Carga la configuración guardada"""
        config_path = self.get_config_path()
        
        if not os.path.exists(config_path):
            return
        
        try:
            config = read_json(config_path, default=None, backup_corrupt=True)
            if not isinstance(config, dict):
                raise ValueError("invalid or corrupt configuration")
            
            self.next_button_id = config.get('next_id', 0)
            
            # Limpiar botones existentes
            for button in self.buttons:
                button.deleteLater()
            self.buttons = []
            self.button_functions = {}
            self.button_positions = {}
            
            # Cargar botones
            if 'buttons' in config:
                for button_id_str, func_data in config['buttons'].items():
                    button_id = int(button_id_str)
                    
                    if 'positions' in config and button_id_str in config['positions']:
                        pos_data = config['positions'][button_id_str]
                        x, y = pos_data['x'], pos_data['y']
                    else:
                        x, y = 0, 0
                    
                    self.next_button_id = max(self.next_button_id, button_id + 1)
                    self.add_button_at_position(x, y, func_data.get('label', '+'), func_data)
            
            self.update_panel_size()
            
        except Exception as e:
            print(f"Flash Buttons: Error loading config: {e}")
    
    def keyPressEvent(self, event):
        """Manejo de teclas"""
        if event.key() == Qt.Key_Escape:
            self.hide_panel()


# ==============================================================================
#                           PANEL MANAGER
# ==============================================================================

class FlashButtonsManager:
    """Gestor global del panel"""
    
    instance = None
    
    @classmethod
    def show_panel(cls):
        """Muestra el panel o lo crea si no existe"""
        if cls.instance is None or not cls.instance.isVisible():
            maya_window = get_maya_main_window()
            cls.instance = FlashButtonsPanel(maya_window)
        
        cls.instance.toggle_visibility()
    
    @classmethod
    def get_instance(cls):
        """Obtiene la instancia actual del panel"""
        return cls.instance


# ==============================================================================
#                           PUBLIC FUNCTIONS
# ==============================================================================

def execute(*args):
    """Main function to execute when button is clicked"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.flashbuttons.execute"):
        return None
    FlashButtonsManager.show_panel()


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Flash Buttons",
        "tooltip": "Show Flash Buttons panel - Quick access to customizable action buttons",
        "icon": "btns.svg",
        "shortcut": "",
    }


def export_config():
    """
    Export Flash Buttons configuration to a file.
    Exports all buttons, positions, actions, and custom scripts.
    """
    from datetime import datetime
    
    # Get the save file path from user
    default_dir = get_flash_buttons_folder(create=True)
    
    # Open file dialog
    file_filter = "Flash Buttons Config (*.json);;All Files (*.*)"
    file_path = cmds.fileDialog2(
        fileFilter=file_filter,
        dialogStyle=2,
        fileMode=0,  # Save mode
        caption="Export Flash Buttons Configuration",
        startingDirectory=default_dir
    )
    
    if not file_path:
        return
    
    file_path = file_path[0]
    
    # Ensure correct extension
    if not file_path.endswith('.json'):
        file_path += '.json'
    
    # Get current config from manager instance or load from file
    panel = FlashButtonsManager.get_instance()
    
    if panel is not None:
        # Export from active panel
        config = {
            'version': '1.0',
            'exported_at': datetime.now().isoformat(),
            'buttons': {},
            'positions': {},
            'next_id': panel.next_button_id,
        }
        
        for button_id, func_data in panel.button_functions.items():
            config['buttons'][str(button_id)] = func_data
        
        for button_id, pos in panel.button_positions.items():
            config['positions'][str(button_id)] = {
                'x': pos.x(),
                'y': pos.y()
            }
    else:
        # Load from saved config file
        config_path = os.path.join(get_flash_buttons_folder(create=True), "config.json")
        
        if os.path.exists(config_path):
            try:
                config = read_json(config_path, default=None, backup_corrupt=True)
                if not isinstance(config, dict):
                    raise ValueError("invalid or corrupt configuration")
                config['version'] = '1.0'
                config['exported_at'] = datetime.now().isoformat()
            except Exception as e:
                cmds.warning(f"Flash Buttons: Error reading config: {e}")
                return
        else:
            cmds.warning("Flash Buttons: No configuration found to export")
            return
    
    # Write to file
    try:
        atomic_write_json(
            file_path, config, indent=2, ensure_ascii=False
        )
        
        button_count = len(config.get('buttons', {}))
        cmds.inViewMessage(
            amg=f'<span style="color:#a3be8c;">✓</span> Exported {button_count} Flash Button(s)',
            pos='topCenter',
            fade=True,
            fadeStayTime=2000
        )
        print(f"Flash Buttons: Exported {button_count} button(s) to: {file_path}")
    except Exception as e:
        cmds.warning(f"Flash Buttons: Error saving file: {e}")


def import_config():
    """
    Import Flash Buttons configuration from a file.
    Imports all buttons, positions, actions, and custom scripts.
    """
    default_dir = get_flash_buttons_folder(create=True)
    
    # Open file dialog
    file_filter = "Flash Buttons Config (*.json);;All Files (*.*)"
    file_path = cmds.fileDialog2(
        fileFilter=file_filter,
        dialogStyle=2,
        fileMode=1,  # Open mode
        caption="Import Flash Buttons Configuration",
        startingDirectory=default_dir
    )
    
    if not file_path:
        return
    
    file_path = file_path[0]
    
    if not os.path.exists(file_path):
        cmds.warning(f"Flash Buttons: File not found: {file_path}")
        return
    
    # Read the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        cmds.warning(f"Flash Buttons: Error reading file: {e}")
        return
    
    # Validate config
    if 'buttons' not in config:
        cmds.warning("Flash Buttons: Invalid configuration file - no buttons found")
        return
    
    # Save to local config
    local_config_path = os.path.join(get_flash_buttons_folder(create=True), "config.json")
    
    # Remove export metadata before saving locally
    local_config = {
        'buttons': config.get('buttons', {}),
        'positions': config.get('positions', {}),
        'next_id': config.get('next_id', 0)
    }
    
    try:
        atomic_write_json(
            local_config_path, local_config, indent=2, ensure_ascii=False
        )
    except Exception as e:
        cmds.warning(f"Flash Buttons: Error saving config: {e}")
        return
    
    # Reload the panel if it exists
    panel = FlashButtonsManager.get_instance()
    if panel is not None:
        panel.load_config()
        panel.update()
    
    button_count = len(config.get('buttons', {}))
    cmds.inViewMessage(
        amg=f'<span style="color:#a3be8c;">✓</span> Imported {button_count} Flash Button(s)',
        pos='topCenter',
        fade=True,
        fadeStayTime=2000
    )
    print(f"Flash Buttons: Imported {button_count} button(s) from: {file_path}")
