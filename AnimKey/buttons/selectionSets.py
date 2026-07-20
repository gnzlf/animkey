# -*- coding: utf-8 -*-
"""
SET MANAGER FOR MAYA - v7
Frameless window with custom title bar
Compatible: Maya 2020+
"""

import maya.cmds as cmds
import maya.OpenMayaUI as omui
import json
import os

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance


WINDOW_OBJECT = "setManagerV7"
DEFAULT_COLOR = "#3498DB"
UI_BG = "#2B2B2B"
UI_PANEL = "#333333"
UI_CONTROL = "#383838"
UI_BORDER = "#333333"
UI_BORDER_SOFT = "#444444"
UI_TEXT = "#F5F5F7"
UI_TEXT_DIM = "#A0A7B4"
UI_TEXT_MUTED = "#888888"
UI_ACCENT = "#3498DB"
SETS_CONTAINER_NAME = "animkey_sets"
SETS_DATA_ATTR = "setsData"
SET_BUTTON_MIN_WIDTH = 40
SET_BUTTON_MIN_HEIGHT = 24
SET_BUTTON_MAX_WIDTH = 720
SET_BUTTON_MAX_HEIGHT = 320

# Global window instance
_win = None

def _get_icon_path(icon_name=None):
    """
    Get path to AnimKey icon file for outliner display.
    """
    if icon_name is None:
        icon_name = "animkey_outliner_minimal_32.png"
    
    # Try different possible locations
    possible_paths = []
    
    # Relative to this file (development and installed location)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths.append(os.path.join(current_dir, "..", "data", "icons", icon_name))
    
    # Find and return the first existing path
    for path in possible_paths:
        normalized_path = os.path.normpath(path)
        if os.path.exists(normalized_path):
            return normalized_path
    
    return ""


def _create_animkey_container():
    """Create AnimKey dagContainer if it doesn't exist"""
    if not cmds.objExists("AnimKey"):
        container = cmds.container(type='dagContainer', name="AnimKey")
        
        icon_path = _get_icon_path()
        if icon_path:
            try:
                cmds.setAttr(container + '.iconName', icon_path, type='string')
            except:
                pass
        
        attributes = ["translateX", "translateY", "translateZ",
                     "rotateX", "rotateY", "rotateZ",
                     "scaleX", "scaleY", "scaleZ", "visibility"]
        
        for attr in attributes:
            try:
                cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
            except:
                pass


def _create_sets_container():
    """Create animkey_sets dagContainer if it doesn't exist"""
    _create_animkey_container()
    
    icon_path = _get_icon_path()
    
    if not cmds.objExists(SETS_CONTAINER_NAME):
        container = cmds.container(type='dagContainer', name=SETS_CONTAINER_NAME)
        
        if icon_path:
            try:
                cmds.setAttr(container + '.iconName', icon_path, type='string')
            except:
                pass
        
        # Parent to AnimKey
        if cmds.objExists("AnimKey"):
            cmds.parent(container, "AnimKey")
        
        # Lock and hide transform attributes
        attributes = ["translateX", "translateY", "translateZ",
                     "rotateX", "rotateY", "rotateZ",
                     "scaleX", "scaleY", "scaleZ", "visibility"]
        
        for attr in attributes:
            try:
                cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
            except:
                pass
        
        # Add custom attribute to store sets data as JSON string
        if not cmds.attributeQuery(SETS_DATA_ATTR, node=container, exists=True):
            cmds.addAttr(container, longName=SETS_DATA_ATTR, dataType="string")
            cmds.setAttr(f"{container}.{SETS_DATA_ATTR}", "{}", type="string")
    else:
        # Container exists - ensure icon is set
        if icon_path:
            try:
                cmds.setAttr(SETS_CONTAINER_NAME + '.iconName', icon_path, type='string')
            except:
                pass
    
    return SETS_CONTAINER_NAME


def _save_to_scene(data):
    """Save sets data to the scene's animkey_sets container"""
    container = _create_sets_container()
    
    try:
        json_str = json.dumps(data)
        print(f"[_save_to_scene] Saving to {container}.{SETS_DATA_ATTR}, data length: {len(json_str)}")
        cmds.setAttr(f"{container}.{SETS_DATA_ATTR}", json_str, type="string")
        print(f"[_save_to_scene] SUCCESS - saved to scene")
    except Exception as e:
        cmds.warning(f"Failed to save sets to scene: {e}")
        print(f"[_save_to_scene] ERROR: {e}")


def _load_from_scene():
    """Load sets data from the scene's animkey_sets container"""
    if not cmds.objExists(SETS_CONTAINER_NAME):
        return None
    
    if not cmds.attributeQuery(SETS_DATA_ATTR, node=SETS_CONTAINER_NAME, exists=True):
        return None
    
    try:
        json_str = cmds.getAttr(f"{SETS_CONTAINER_NAME}.{SETS_DATA_ATTR}")
        if json_str:
            return json.loads(json_str)
    except Exception as e:
        cmds.warning(f"Failed to load sets from scene: {e}")
    
    return None


def get_maya_main_window():
    return wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget)


def text_color_for_bg(bg):
    c = QtGui.QColor(bg)
    lum = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
    return "#000" if lum > 140 else "#FFF"


# ============================================================================
# NAMESPACE UTILITIES
# ============================================================================

def strip_namespace(name):
    if ':' in name:
        return name.split(':')[-1]
    return name


def get_namespace(name):
    if ':' in name:
        return name.rsplit(':', 1)[0]
    return ""


def apply_namespace(name, namespace):
    base = strip_namespace(name)
    if namespace:
        return f"{namespace}:{base}"
    return base


def get_scene_namespaces():
    all_ns = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or []
    exclude = {'UI', 'shared'}
    return sorted([ns for ns in all_ns if ns not in exclude])


def get_namespace_from_selection():
    sel = cmds.ls(sl=True, long=True)
    if sel:
        short_name = sel[0].split('|')[-1]
        return get_namespace(short_name)
    return ""


def get_namespaces_from_selection():
    sel = cmds.ls(sl=True, long=True) or []
    namespaces = []
    for item in sel:
        short_name = item.split('|')[-1]
        ns = get_namespace(short_name)
        if ns not in namespaces:
            namespaces.append(ns)
    return namespaces


def get_member_namespace_map(items):
    """Return {base_control: [namespace, ...]} for saved or selected members."""
    mapping = {}
    for item in items or []:
        short_name = item.split('|')[-1]
        base = strip_namespace(short_name)
        ns = get_namespace(short_name)
        if base not in mapping:
            mapping[base] = []
        if ns not in mapping[base]:
            mapping[base].append(ns)
    return mapping


def unique_namespaces_from_member_map(member_namespaces):
    namespaces = []
    for ns_list in (member_namespaces or {}).values():
        for ns in ns_list or []:
            ns = ns or ""
            if ns not in namespaces:
                namespaces.append(ns)
    return namespaces


# ============================================================================
# CUSTOM TITLE BAR
# ============================================================================

class TitleBar(QtWidgets.QWidget):
    def __init__(self, parent=None, title="Set Manager"):
        super(TitleBar, self).__init__(parent)
        self.parent_window = parent
        self._drag_pos = None
        self.setFixedHeight(32)
        self.setup_ui(title)
        
    def setup_ui(self, title):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(8)
        
        # Title
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setStyleSheet("""
            QLabel {
                color: #AAA;
                font-size: 11px;
                font-weight: 500;
            }
        """)
        layout.addWidget(self.title_label)
        
        layout.addStretch()
        
        # Close button
        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 12px;
                font-weight: bold;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        self.close_btn.clicked.connect(self.close_window)
        layout.addWidget(self.close_btn)
        
        self.setStyleSheet("""
            TitleBar {
                background-color: #2B2B2B;
                border-bottom: 1px solid #333333;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
        """)
        
    def close_window(self):
        if self.parent_window:
            self.parent_window.close()
            
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.parent_window.frameGeometry().topLeft()
            event.accept()
            
    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.LeftButton and self._drag_pos:
            if hasattr(self.parent_window, "detach_from_anchor"):
                self.parent_window.detach_from_anchor()
            self.parent_window.move(event.globalPos() - self._drag_pos)
            event.accept()
            
    def mouseReleaseEvent(self, event):
        if hasattr(self.parent_window, "maybe_attach_to_anchor"):
            self.parent_window.maybe_attach_to_anchor()
        self._drag_pos = None


# ============================================================================
# RESIZE GRIP
# ============================================================================

class ResizeGrip(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(ResizeGrip, self).__init__(parent)
        self.parent_window = parent
        self.setFixedSize(16, 16)
        self.setCursor(QtCore.Qt.SizeFDiagCursor)
        self._drag_pos = None
        
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#555"), 1))
        
        # Draw grip lines
        for i in range(3):
            x = 4 + i * 4
            y = 12 - i * 4
            painter.drawLine(x, 12, 12, y)
            
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_pos = event.globalPos()
            self._window_size = self.parent_window.size()
            event.accept()
            
    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.LeftButton and self._drag_pos:
            diff = event.globalPos() - self._drag_pos
            new_width = max(self.parent_window.minimumWidth(), self._window_size.width() + diff.x())
            new_height = max(self.parent_window.minimumHeight(), self._window_size.height() + diff.y())
            self.parent_window.resize(new_width, new_height)
            event.accept()
            
    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ============================================================================
# POPUP INPUT
# ============================================================================

class PopupInput(QtWidgets.QWidget):
    submitted = QtCore.Signal(str)
    
    def __init__(self, parent=None, placeholder="Set name...", initial_text=""):
        super(PopupInput, self).__init__(parent)
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self._initial_text = initial_text
        self._placeholder = placeholder
        self.setup_ui()
        
    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        container = QtWidgets.QFrame()
        container.setStyleSheet("""
            QFrame {
                background-color: #333333;
                border: 1px solid #333333;
                border-radius: 8px;
            }
        """)
        
        c_layout = QtWidgets.QHBoxLayout(container)
        c_layout.setContentsMargins(12, 12, 12, 12)
        c_layout.setSpacing(8)
        
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText(self._placeholder)
        self.input.setText(self._initial_text)
        self.input.setMinimumWidth(180)
        self.input.setStyleSheet("""
            QLineEdit {
                background-color: #383838;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 10px 12px;
                color: #F5F5F7;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #3498DB; }
        """)
        self.input.returnPressed.connect(self.submit)
        c_layout.addWidget(self.input)
        
        ok_btn = QtWidgets.QPushButton("✓")
        ok_btn.setFixedSize(36, 36)
        ok_btn.setCursor(QtCore.Qt.PointingHandCursor)
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498DB;
                color: white;
                font-size: 16px;
                font-weight: bold;
                border: 1px solid #3498DB;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #4AA3DF; border-color: #4AA3DF; }
        """)
        ok_btn.clicked.connect(self.submit)
        c_layout.addWidget(ok_btn)
        
        layout.addWidget(container)
        
    def submit(self):
        self.submitted.emit(self.input.text().strip())
        self.close()
        
    def showEvent(self, e):
        self.input.setFocus()
        self.input.selectAll()
        super(PopupInput, self).showEvent(e)
        
    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape:
            self.close()
        super(PopupInput, self).keyPressEvent(e)


# ============================================================================
# SET BUTTON
# ============================================================================

class ButtonResizeGrip(QtWidgets.QWidget):
    def __init__(self, button):
        super(ButtonResizeGrip, self).__init__(button)
        self.button = button
        self.setFixedSize(18, 18)
        self.setCursor(QtCore.Qt.SizeFDiagCursor)
        self.setMouseTracking(True)
        self.setToolTip("Drag to resize")
        self._drag_global = None
        self._start_size = None

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        color = QtGui.QColor(text_color_for_bg(self.button.color))
        color.setAlpha(185)
        painter.setPen(QtGui.QPen(color, 1.3))
        right = self.width() - 4
        bottom = self.height() - 4
        for offset in (0, 5, 10):
            painter.drawLine(right - offset, bottom, right, bottom - offset)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_global = event.globalPos()
            self._start_size = QtCore.QSize(self.button.board_size)
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
            event.accept()
            return
        super(ButtonResizeGrip, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_global and event.buttons() & QtCore.Qt.LeftButton:
            diff = event.globalPos() - self._drag_global
            zoom = self.button._board_zoom()
            self.button.set_board_size(
                self._start_size.width() + (diff.x() / zoom),
                self._start_size.height() + (diff.y() / zoom),
                save=False,
                custom=True
            )
            event.accept()
            return
        super(ButtonResizeGrip, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_global and event.button() == QtCore.Qt.LeftButton:
            self._drag_global = None
            self._start_size = None
            self.button.position_changed.emit(self.button)
            self.button._auto_save()
            event.accept()
            return
        super(ButtonResizeGrip, self).mouseReleaseEvent(event)


class SetButton(QtWidgets.QFrame):
    deleted = QtCore.Signal(object)
    drag_started = QtCore.Signal(object)
    position_changed = QtCore.Signal(object)
    
    def __init__(
        self, name, members, color=DEFAULT_COLOR, get_namespace_func=None,
        get_namespaces_func=None, board_pos=None, namespaces=None, size_scale=1.0,
        board_size=None, namespace_mode="page", member_namespaces=None,
        namespace_dynamic_func=None
    ):
        super(SetButton, self).__init__()
        self.set_name = name
        self.members = []
        self.member_namespaces = {}
        saved_member_namespaces = member_namespaces if isinstance(member_namespaces, dict) else {}
        for member in members:
            short_name = member.split('|')[-1]
            base = strip_namespace(short_name)
            if base not in self.members:
                self.members.append(base)
            ns = get_namespace(short_name)
            if ns:
                self.member_namespaces.setdefault(base, [])
                if ns not in self.member_namespaces[base]:
                    self.member_namespaces[base].append(ns)

        for base, ns_list in saved_member_namespaces.items():
            base = strip_namespace(str(base).split('|')[-1])
            if base not in self.members:
                self.members.append(base)
            self.member_namespaces.setdefault(base, [])
            for ns in ns_list or []:
                ns = ns or ""
                if ns not in self.member_namespaces[base]:
                    self.member_namespaces[base].append(ns)

        self.color = color
        self.get_namespace = get_namespace_func or (lambda: "")
        self.get_namespaces = get_namespaces_func or (lambda: [self.get_namespace()])
        self.get_namespace_dynamic = namespace_dynamic_func or (lambda: True)
        self.namespace_mode = "custom" if namespace_mode == "custom" else "page"
        self.namespaces = []
        for ns in namespaces or []:
            ns = ns or ""
            if ns not in self.namespaces:
                self.namespaces.append(ns)

        if not self.namespaces:
            self.namespaces = unique_namespaces_from_member_map(self.member_namespaces)

        if self.namespaces and not self.member_namespaces:
            for base in self.members:
                self.member_namespaces[base] = list(self.namespaces)

        self.size_scale = max(0.75, min(2.2, float(size_scale or 1.0)))
        self.custom_size = isinstance(board_size, dict)
        self.board_size = self._size_from_data(board_size)
        if self.board_size is None:
            self.board_size = self._default_size_for_scale(self.size_scale)
        self._drag_pos = None
        self._drag_start_global = None
        self._was_dragged = False
        self._resize_active = False
        self._resize_drag_start = None
        self._resize_start_size = None
        self._resize_handle_size = 22
        self.free_move_mode = False
        self.board_pos = QtCore.QPoint(0, 0)
        if isinstance(board_pos, dict):
            self.board_pos = QtCore.QPoint(int(board_pos.get("x", 0)), int(board_pos.get("y", 0)))
        self.setup_ui()
        
    def set_free_move_mode(self, enabled):
        self.free_move_mode = bool(enabled)
        self._drag_pos = None
        self._drag_start_global = None
        self._was_dragged = False
        self._resize_active = False
        self.setCursor(QtCore.Qt.OpenHandCursor if self.free_move_mode else QtCore.Qt.PointingHandCursor)
        self._apply_button_size()
        if hasattr(self, "resize_grip"):
            self._position_resize_grip()
            self.resize_grip.setVisible(self.free_move_mode)

    def setup_ui(self):
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_menu)
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)
        
        self.label = QtWidgets.QLabel(self.set_name)
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.label)

        self.resize_grip = ButtonResizeGrip(self)
        self.resize_grip.hide()
        
        self.apply_style()
        self.update_size()
        self.update_tooltip()

    def _default_size_for_scale(self, scale=None):
        scale = self.size_scale if scale is None else float(scale or 1.0)
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.set_name) + 24
        return QtCore.QSize(
            int(max(SET_BUTTON_MIN_WIDTH, w) * scale),
            int(max(26, SET_BUTTON_MIN_HEIGHT) * scale)
        )

    def _row_size(self):
        fm = self.fontMetrics()
        width = max(SET_BUTTON_MIN_WIDTH, fm.horizontalAdvance(self.set_name) + 24)
        return QtCore.QSize(int(width), max(26, SET_BUTTON_MIN_HEIGHT))

    def _size_from_data(self, data):
        if not isinstance(data, dict):
            return None
        width = data.get("w", data.get("width"))
        height = data.get("h", data.get("height"))
        if width is None or height is None:
            return None
        return QtCore.QSize(
            max(SET_BUTTON_MIN_WIDTH, min(SET_BUTTON_MAX_WIDTH, int(width))),
            max(SET_BUTTON_MIN_HEIGHT, min(SET_BUTTON_MAX_HEIGHT, int(height)))
        )

    def _resize_handle_rect(self):
        size = self._current_resize_handle_size()
        return QtCore.QRect(self.width() - size, self.height() - size, size, size)

    def _is_over_resize_handle(self, pos):
        return self.free_move_mode and self._resize_handle_rect().contains(pos)

    def _board_parent(self):
        parent = self.parent()
        if parent is not None and getattr(parent, "layout_mode", "ordered") == "board":
            return parent
        return None

    def _board_zoom(self):
        parent = self._board_parent()
        if parent is None:
            return 1.0
        return max(0.05, float(getattr(parent, "board_zoom", 1.0) or 1.0))

    def _current_resize_handle_size(self):
        if not self.free_move_mode:
            return self._resize_handle_size
        return max(5, min(36, int(round(self._resize_handle_size * self._board_zoom()))))

    def _visual_board_size(self):
        zoom = self._board_zoom()
        return QtCore.QSize(
            max(1, int(round(self.board_size.width() * zoom))),
            max(1, int(round(self.board_size.height() * zoom)))
        )

    def _apply_button_size(self):
        self.board_size.setWidth(max(SET_BUTTON_MIN_WIDTH, min(SET_BUTTON_MAX_WIDTH, self.board_size.width())))
        self.board_size.setHeight(max(SET_BUTTON_MIN_HEIGHT, min(SET_BUTTON_MAX_HEIGHT, self.board_size.height())))
        layout = self.layout()
        if layout is not None:
            margin = max(1, min(10, int(round(10 * self._board_zoom())))) if self.free_move_mode else 10
            layout.setContentsMargins(margin, 0, margin, 0)
        self.setFixedSize(self._visual_board_size() if self.free_move_mode else self._row_size())
        self.label.setText(self.set_name)
        self.apply_style()
        self._position_resize_grip()
        self.update()

    def _position_resize_grip(self):
        if not hasattr(self, "resize_grip"):
            return
        grip_size = max(5, min(30, int(round(18 * self._board_zoom())))) if self.free_move_mode else 18
        if self.resize_grip.width() != grip_size or self.resize_grip.height() != grip_size:
            self.resize_grip.setFixedSize(grip_size, grip_size)
        self.resize_grip.move(self.width() - self.resize_grip.width(), self.height() - self.resize_grip.height())
        self.resize_grip.raise_()

    def set_board_size(self, width, height, save=True, custom=True):
        self.custom_size = bool(custom)
        self.board_size = QtCore.QSize(
            max(SET_BUTTON_MIN_WIDTH, min(SET_BUTTON_MAX_WIDTH, int(width))),
            max(SET_BUTTON_MIN_HEIGHT, min(SET_BUTTON_MAX_HEIGHT, int(height)))
        )
        self._apply_button_size()
        self.update_tooltip()
        parent = self.parent()
        if parent:
            if getattr(parent, "layout_mode", "ordered") == "board" and hasattr(parent, "update_board_bounds"):
                parent.update_board_bounds()
            elif hasattr(parent, "reflow"):
                parent.reflow()
        if save:
            self._auto_save()
        
    def apply_style(self):
        tc = text_color_for_bg(self.color)
        hover = QtGui.QColor(self.color).lighter(115).name()
        zoom = self._board_zoom() if self.free_move_mode else 1.0
        font_size = max(4, min(28, int(round(11 * zoom))))
        radius = max(1, min(18, int(round(6 * zoom))))
        self.setStyleSheet(f"""
            SetButton {{
                background-color: {self.color};
                border-radius: {radius}px;
            }}
            SetButton:hover {{
                background-color: {hover};
            }}
            QLabel {{
                color: {tc};
                font-size: {font_size}px;
                font-weight: 500;
                background: transparent;
            }}
        """)
        
    def update_size(self):
        if not self.custom_size:
            self.board_size = self._default_size_for_scale(self.size_scale)
        self._apply_button_size()

    def set_size_scale(self, scale, save=True):
        self.size_scale = max(0.75, min(2.2, float(scale)))
        self.custom_size = False
        default_size = self._default_size_for_scale(self.size_scale)
        self.set_board_size(default_size.width(), default_size.height(), save=save, custom=False)
        
    def update_tooltip(self):
        targets = self._target_namespaces()
        if self.namespace_mode == "custom" and self.namespaces:
            ns_display = ", ".join(ns if ns else "(no namespace)" for ns in targets[:4])
            if len(targets) > 4:
                ns_display += f" +{len(targets) - 4}"
            ns_display = f"Pinned: {ns_display}"
        elif not self._uses_dynamic_namespace() and self.namespaces:
            ns_display = ", ".join(ns if ns else "(no namespace)" for ns in targets[:4])
            if len(targets) > 4:
                ns_display += f" +{len(targets) - 4}"
            ns_display = f"Static: {ns_display}"
        else:
            fallback = self.get_namespace()
            if len(targets) > 1:
                ns_display = ", ".join(ns if ns else "(no namespace)" for ns in targets[:4])
                if len(targets) > 4:
                    ns_display += f" +{len(targets) - 4}"
                ns_display = f"Dynamic: {ns_display}"
            else:
                ns_display = f"Dynamic: {fallback if fallback else '(no namespace)'}"
        self.setToolTip(
            f"{self.set_name}\n"
            f"{len(self.members)} controls\n"
            f"Targets: {ns_display}\n"
            f"Button size: {self.width()} x {self.height()} px\n"
            f"Board: drag bottom-right corner to resize"
        )

    def _uses_dynamic_namespace(self):
        if self.namespace_mode == "custom":
            return False
        try:
            return bool(self.get_namespace_dynamic())
        except Exception:
            return True

    def _target_namespaces(self):
        if self.namespace_mode == "custom" and self.namespaces:
            return list(self.namespaces)
        if not self._uses_dynamic_namespace() and self.namespaces:
            return list(self.namespaces)
        try:
            namespaces = self.get_namespaces() or []
        except Exception:
            namespaces = []
        clean = []
        for ns in namespaces:
            ns = ns or ""
            if ns not in clean:
                clean.append(ns)
        if clean:
            return clean
        return [self.get_namespace() or ""]

    def _resolve_static_members(self):
        resolved = []
        seen = set()
        fallback_namespaces = self.namespaces or [self.get_namespace() or ""]
        for member in self.members:
            namespaces = self.member_namespaces.get(member) or fallback_namespaces
            for ns in namespaces:
                full_name = apply_namespace(member, ns or "")
                if full_name in seen:
                    continue
                if cmds.objExists(full_name):
                    resolved.append(full_name)
                    seen.add(full_name)
        return resolved
        
    def get_resolved_members(self):
        if self.namespace_mode != "custom" and not self._uses_dynamic_namespace():
            return self._resolve_static_members()

        resolved = []
        seen = set()
        for ns in self._target_namespaces():
            for m in self.members:
                full_name = apply_namespace(m, ns)
                if full_name in seen:
                    continue
                if cmds.objExists(full_name):
                    resolved.append(full_name)
                    seen.add(full_name)
        return resolved

    def _add_dimension_slider_action(self, menu, title, value, minimum, maximum, on_changed):
        widget = QtWidgets.QWidget(menu)
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(5)

        label = QtWidgets.QLabel(f"{title}: {int(value)} px")
        label.setStyleSheet("color: #D8DEE9; font-size: 11px;")
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(int(value))
        slider.setFixedWidth(190)

        def update_value(v):
            label.setText(f"{title}: {int(v)} px")
            on_changed(v)

        slider.valueChanged.connect(update_value)
        slider.sliderReleased.connect(self._auto_save)
        layout.addWidget(label)
        layout.addWidget(slider)

        action = QtWidgets.QWidgetAction(menu)
        action.setDefaultWidget(widget)
        menu.addAction(action)
        return action
        
    def show_menu(self, pos):
        m = QtWidgets.QMenu(self)
        m.setStyleSheet("""
            QMenu { background: #333333; color: #F5F5F7; border: 1px solid #444444; padding: 4px; }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background: #3498DB; }
            QMenu::separator { height: 1px; background: #333333; margin: 4px 8px; }
            QSlider::groove:horizontal { height: 4px; background: #444444; border-radius: 2px; }
            QSlider::handle:horizontal { width: 13px; margin: -5px 0; background: #3498DB; border-radius: 6px; }
            QSlider::sub-page:horizontal { background: #3498DB; border-radius: 2px; }
        """)
        
        m.addAction("Select", self.do_select)
        m.addAction("Add Selection to Set", self.do_add_sel)
        m.addAction("Remove Selection from Set", self.do_rem_sel)
        m.addSeparator()
        follow_action = m.addAction("Follow Panel Namespace", self.do_follow_panel_namespace)
        follow_action.setCheckable(True)
        follow_action.setChecked(self.namespace_mode != "custom")
        m.addAction("Use Selection Namespaces", self.do_use_selection_namespaces)
        m.addAction("Add Selection Namespaces", self.do_add_selection_namespaces)
        if self.free_move_mode:
            size_menu = m.addMenu("Board Size")
            for label, scale in (("Small", 0.85), ("Normal", 1.0), ("Large", 1.3), ("XL", 1.65), ("XXL", 2.0)):
                act = size_menu.addAction(label)
                act.setCheckable(True)
                act.setChecked((not self.custom_size) and abs(self.size_scale - scale) < 0.05)
                act.triggered.connect(lambda checked=False, s=scale: self.set_size_scale(s))
            size_menu.addSeparator()
            size_menu.addAction("Auto Fit Text", lambda: self.set_size_scale(1.0))
            self._add_dimension_slider_action(
                size_menu,
                "Width",
                self.board_size.width(),
                SET_BUTTON_MIN_WIDTH,
                max(SET_BUTTON_MAX_WIDTH, self.board_size.width()),
                lambda v: self.set_board_size(v, self.board_size.height(), save=False, custom=True)
            )
            self._add_dimension_slider_action(
                size_menu,
                "Height",
                self.board_size.height(),
                SET_BUTTON_MIN_HEIGHT,
                max(SET_BUTTON_MAX_HEIGHT, self.board_size.height()),
                lambda v: self.set_board_size(self.board_size.width(), v, save=False, custom=True)
            )
        m.addSeparator()
        m.addAction("Rename", self.do_rename)
        m.addAction("Change Color", self.do_color)
        m.addSeparator()
        m.addAction("Delete", self.do_delete)
        m.exec_(self.mapToGlobal(pos))

    def paintEvent(self, event):
        super(SetButton, self).paintEvent(event)
        if not self.free_move_mode:
            return
        if hasattr(self, "resize_grip") and self.resize_grip.isVisible():
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        tc = QtGui.QColor(text_color_for_bg(self.color))
        tc.setAlpha(150)
        painter.setPen(QtGui.QPen(tc, 1.2))
        rect = self._resize_handle_rect().adjusted(1, 1, -3, -3)
        for offset in (0, 4, 8):
            painter.drawLine(
                rect.right() - offset,
                rect.bottom(),
                rect.right(),
                rect.bottom() - offset
            )
        painter.end()
        
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton and self.free_move_mode:
            parent = self.parent()
            if hasattr(parent, "_start_board_pan") and parent._start_board_pan(e):
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                return
        if e.button() == QtCore.Qt.LeftButton:
            if self._is_over_resize_handle(e.pos()):
                self._resize_active = True
                self._resize_drag_start = e.globalPos()
                self._resize_start_size = QtCore.QSize(self.board_size)
                self._drag_pos = None
                self._drag_start_global = None
                self._was_dragged = False
                self.setCursor(QtCore.Qt.SizeFDiagCursor)
                e.accept()
                return
            self._drag_pos = e.pos()
            self._drag_start_global = e.globalPos()
            self._was_dragged = False
            if self.free_move_mode:
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                e.accept()
                return
        super(SetButton, self).mousePressEvent(e)
        
    def mouseMoveEvent(self, e):
        parent = self.parent()
        if self.free_move_mode and hasattr(parent, "_update_board_pan") and parent._update_board_pan(e):
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            return

        if self._resize_active and self._resize_drag_start:
            diff = e.globalPos() - self._resize_drag_start
            zoom = self._board_zoom()
            self.set_board_size(
                self._resize_start_size.width() + (diff.x() / zoom),
                self._resize_start_size.height() + (diff.y() / zoom),
                save=False,
                custom=True
            )
            e.accept()
            return

        if self._drag_pos and e.buttons() == QtCore.Qt.LeftButton:
            if self.free_move_mode:
                if self._drag_start_global and (e.globalPos() - self._drag_start_global).manhattanLength() > 3:
                    self._was_dragged = True
                if self._was_dragged and self.parent():
                    delta = e.pos() - self._drag_pos
                    new_pos = self.pos() + delta
                    parent = self.parent()
                    if hasattr(parent, "visual_to_board_point"):
                        logical_pos = parent.visual_to_board_point(new_pos)
                        logical_pos.setX(max(8, logical_pos.x()))
                        logical_pos.setY(max(8, logical_pos.y()))
                        self.board_pos = QtCore.QPoint(logical_pos)
                        self.move(parent.board_to_visual_point(logical_pos))
                    else:
                        new_pos.setX(max(8, new_pos.x()))
                        new_pos.setY(max(8, new_pos.y()))
                        self.move(new_pos)
                        self.board_pos = QtCore.QPoint(new_pos)
                    if hasattr(parent, "update_board_bounds"):
                        parent.update_board_bounds()
                e.accept()
                return
            if (e.pos() - self._drag_pos).manhattanLength() > 10:
                self.drag_started.emit(self)
                self._start_drag()
                self._drag_pos = None
        if self.free_move_mode and not (e.buttons() & QtCore.Qt.LeftButton):
            self.setCursor(QtCore.Qt.SizeFDiagCursor if self._is_over_resize_handle(e.pos()) else QtCore.Qt.OpenHandCursor)
        super(SetButton, self).mouseMoveEvent(e)
        
    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton:
            parent = self.parent()
            if hasattr(parent, "_end_board_pan") and parent._end_board_pan(e):
                self.setCursor(QtCore.Qt.OpenHandCursor if self.free_move_mode else QtCore.Qt.PointingHandCursor)
                return

        if self._resize_active and e.button() == QtCore.Qt.LeftButton:
            self._resize_active = False
            self._resize_drag_start = None
            self._resize_start_size = None
            self.setCursor(QtCore.Qt.OpenHandCursor if self.free_move_mode else QtCore.Qt.PointingHandCursor)
            self.position_changed.emit(self)
            self._auto_save()
            e.accept()
            return

        if e.button() == QtCore.Qt.LeftButton and self._drag_pos:
            if self.free_move_mode:
                self.setCursor(QtCore.Qt.OpenHandCursor)
                if self._was_dragged:
                    parent = self.parent()
                    if hasattr(parent, "visual_to_board_point"):
                        self.board_pos = parent.visual_to_board_point(self.pos())
                    else:
                        self.board_pos = QtCore.QPoint(self.pos())
                    self.position_changed.emit(self)
                    self._drag_pos = None
                    self._drag_start_global = None
                    self._was_dragged = False
                    e.accept()
                    return
            mods = QtWidgets.QApplication.keyboardModifiers()
            if mods == QtCore.Qt.ShiftModifier:
                self.do_add()
            elif mods == QtCore.Qt.ControlModifier:
                self.do_toggle()
            else:
                self.do_select()
        self._drag_pos = None
        self._drag_start_global = None
        super(SetButton, self).mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if self.free_move_mode and QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ControlModifier:
            delta = e.angleDelta().y()
            parent = self.parent()
            if delta and hasattr(parent, "set_board_zoom"):
                factor = 1.12 if delta > 0 else 1.0 / 1.12
                parent.set_board_zoom(parent.board_zoom * factor, anchor_pos=parent._cursor_anchor_pos(), save=True)
                e.accept()
                return
        super(SetButton, self).wheelEvent(e)
        
    def _start_drag(self):
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        mime.setText(self.set_name)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(QtCore.QPoint(self.width()//2, self.height()//2))
        drag.exec_(QtCore.Qt.MoveAction)
        
    def do_select(self):
        v = self.get_resolved_members()
        if v:
            cmds.select(v, r=True)
        else:
            cmds.warning(f"No valid objects found")
        
    def do_add(self):
        v = self.get_resolved_members()
        if v: cmds.select(v, add=True)
        
    def do_toggle(self):
        v = self.get_resolved_members()
        if v: cmds.select(v, tgl=True)

    def _selection_namespaces(self):
        return get_namespaces_from_selection()

    def do_follow_panel_namespace(self):
        self.namespace_mode = "page"
        if not self.namespaces:
            self.namespaces = unique_namespaces_from_member_map(self.member_namespaces)
        self.update_tooltip()
        self._auto_save()
        cmds.inViewMessage(msg=f"{self.set_name}: follows page namespace mode", pos='midCenter', fade=True)

    def do_use_selection_namespaces(self):
        namespaces = self._selection_namespaces()
        if not namespaces:
            cmds.warning("No namespace found in current selection.")
            return
        self.namespace_mode = "custom"
        self.namespaces = []
        for ns in namespaces:
            if ns not in self.namespaces:
                self.namespaces.append(ns)
        self.update_tooltip()
        self._auto_save()
        cmds.inViewMessage(msg=f"{self.set_name}: {len(self.namespaces)} namespace(s)", pos='midCenter', fade=True)

    def do_add_selection_namespaces(self):
        namespaces = self._selection_namespaces()
        if not namespaces:
            cmds.warning("No namespace found in current selection.")
            return
        self.namespace_mode = "custom"
        added = 0
        for ns in namespaces:
            if ns not in self.namespaces:
                self.namespaces.append(ns)
                added += 1
        self.update_tooltip()
        self._auto_save()
        cmds.inViewMessage(msg=f"Added {added} namespace(s)", pos='midCenter', fade=True)
    
    def _auto_save(self):
        """Trigger auto-save on the parent window"""
        try:
            # Navigate up to the SetManagerWindow
            parent = self.parent()  # FlowContainer
            if parent:
                tab_page = parent.parent()  # TabPage's scroll area's widget
                if tab_page:
                    window = tab_page.window()  # SetManagerWindow
                    if hasattr(window, 'save_data'):
                        window.save_data()
        except:
            pass
        
    def do_add_sel(self):
        sel = cmds.ls(sl=True, long=True)
        if sel:
            added = 0
            for s in sel:
                short_name = s.split('|')[-1]
                base = strip_namespace(short_name)
                ns = get_namespace(short_name)
                self.member_namespaces.setdefault(base, [])
                if ns not in self.member_namespaces[base]:
                    self.member_namespaces[base].append(ns)
                if ns not in self.namespaces:
                    self.namespaces.append(ns)
                if base not in self.members:
                    self.members.append(base)
                    added += 1
            self.update_tooltip()
            self._auto_save()
            cmds.inViewMessage(msg=f"Added {added}", pos='midCenter', fade=True)
            
    def do_rem_sel(self):
        sel = cmds.ls(sl=True, long=True)
        if sel:
            c = 0
            for s in sel:
                base = strip_namespace(s.split('|')[-1])
                if base in self.members:
                    self.members.remove(base)
                    self.member_namespaces.pop(base, None)
                    c += 1
            self.namespaces = unique_namespaces_from_member_map(self.member_namespaces) or self.namespaces
            self.update_tooltip()
            self._auto_save()
            cmds.inViewMessage(msg=f"Removed {c}", pos='midCenter', fade=True)
            
    def do_rename(self):
        n, ok = QtWidgets.QInputDialog.getText(self, "Rename", "Name:", text=self.set_name)
        if ok and n:
            self.set_name = n
            self.update_size()
            self.update_tooltip()
            if self.parent():
                self.parent().reflow()
            self._auto_save()
            
    def do_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color), self)
        if c.isValid():
            self.color = c.name()
            self.apply_style()
            self._auto_save()
            
    def do_delete(self):
        self.deleted.emit(self)
        self._auto_save()
        
    def get_data(self):
        return {
            "name": self.set_name,
            "members": self.members,
            "color": self.color,
            "namespace_mode": self.namespace_mode,
            "namespaces": self.namespaces,
            "member_namespaces": self.member_namespaces,
            "size_scale": self.size_scale,
            "board_size": {"w": int(self.board_size.width()), "h": int(self.board_size.height())},
            "board_pos": {"x": int(self.board_pos.x()), "y": int(self.board_pos.y())}
        }


# ============================================================================
# FLOW CONTAINER
# ============================================================================

class FlowContainer(QtWidgets.QWidget):
    layout_mode_changed = QtCore.Signal(str)

    def __init__(self):
        super(FlowContainer, self).__init__()
        self.buttons = []
        self.manual_mode = True
        self.layout_mode = "ordered"
        self.drop_index = -1
        self.background_path = ""
        self.background_scale = 1.0
        self.background_opacity = 0.92
        self.board_zoom = 1.0
        self._background_pixmap = QtGui.QPixmap()
        self._selection_active = False
        self._selection_origin = QtCore.QPoint()
        self._selection_rect = QtCore.QRect()
        self._pan_active = False
        self._pan_start_global = QtCore.QPoint()
        self._pan_start_h = 0
        self._pan_start_v = 0
        self._zoom_save_timer = QtCore.QTimer(self)
        self._zoom_save_timer.setSingleShot(True)
        self._zoom_save_timer.setInterval(350)
        self._zoom_save_timer.timeout.connect(self._auto_save)
        
        self.setAcceptDrops(True)
        self.setMinimumHeight(50)
        self.setMouseTracking(True)
        
        self.indicator = QtWidgets.QFrame(self)
        self.indicator.setFixedWidth(3)
        self.indicator.setStyleSheet("background-color: #3498DB;")
        self.indicator.hide()

    def set_background_image(self, path, save=True):
        self.background_path = path or ""
        self._background_pixmap = QtGui.QPixmap()
        if self.background_path and os.path.exists(self.background_path):
            self._background_pixmap = QtGui.QPixmap(self.background_path)
        self.update_board_bounds()
        self.update()
        if save:
            self._auto_save()

    def clear_background_image(self, save=True):
        self.set_background_image("", save=save)

    def set_background_scale(self, scale, save=True):
        self.background_scale = max(0.1, min(4.0, float(scale or 1.0)))
        self.update_board_bounds()
        self.update()
        if save:
            self._auto_save()

    def set_background_opacity(self, opacity, save=True):
        self.background_opacity = max(0.05, min(1.0, float(opacity or 0.92)))
        self.update()
        if save:
            self._auto_save()

    def _scroll_area(self):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QtWidgets.QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _viewport_size(self):
        scroll = self._scroll_area()
        if scroll is not None:
            return scroll.viewport().size()
        return self.size()

    def _event_pos(self, event):
        if hasattr(event, "position"):
            try:
                return event.position().toPoint()
            except Exception:
                pass
        return event.pos()

    def _event_global_pos(self, event):
        if hasattr(event, "globalPosition"):
            try:
                return event.globalPosition().toPoint()
            except Exception:
                pass
        return event.globalPos()

    def _cursor_anchor_pos(self):
        try:
            return self.mapFromGlobal(QtGui.QCursor.pos())
        except Exception:
            return self.rect().center()

    def _schedule_zoom_save(self):
        try:
            self._zoom_save_timer.start()
        except Exception:
            self._auto_save()

    def _set_pan_cursor(self, active):
        cursor = QtCore.Qt.ClosedHandCursor if active else QtCore.Qt.ArrowCursor
        try:
            self.setCursor(cursor)
            scroll = self._scroll_area()
            if scroll is not None:
                scroll.viewport().setCursor(cursor)
        except Exception:
            pass

    def _start_board_pan(self, event):
        if self.layout_mode != "board":
            return False
        mods = event.modifiers() if hasattr(event, "modifiers") else QtWidgets.QApplication.keyboardModifiers()
        if not (mods & QtCore.Qt.ControlModifier):
            return False
        scroll = self._scroll_area()
        if scroll is None:
            return False
        self._pan_active = True
        self._pan_start_global = self._event_global_pos(event)
        self._pan_start_h = scroll.horizontalScrollBar().value()
        self._pan_start_v = scroll.verticalScrollBar().value()
        self._set_pan_cursor(True)
        event.accept()
        return True

    def _update_board_pan(self, event):
        if not self._pan_active:
            return False
        scroll = self._scroll_area()
        if scroll is None:
            self._end_board_pan(event)
            return False
        delta = self._event_global_pos(event) - self._pan_start_global
        scroll.horizontalScrollBar().setValue(self._pan_start_h - delta.x())
        scroll.verticalScrollBar().setValue(self._pan_start_v - delta.y())
        event.accept()
        return True

    def _end_board_pan(self, event=None):
        if not self._pan_active:
            return False
        self._pan_active = False
        self._set_pan_cursor(False)
        if event is not None:
            event.accept()
        return True

    def visual_to_board_point(self, point):
        zoom = max(0.05, float(self.board_zoom or 1.0))
        return QtCore.QPoint(
            int(round(point.x() / zoom)),
            int(round(point.y() / zoom))
        )

    def board_to_visual_point(self, point):
        zoom = max(0.05, float(self.board_zoom or 1.0))
        return QtCore.QPoint(
            int(round(point.x() * zoom)),
            int(round(point.y() * zoom))
        )

    def board_to_visual_size(self, size):
        zoom = max(0.05, float(self.board_zoom or 1.0))
        return QtCore.QSize(
            max(1, int(round(size.width() * zoom))),
            max(1, int(round(size.height() * zoom)))
        )

    def set_board_zoom(self, zoom, anchor_pos=None, save=True):
        try:
            zoom = float(zoom)
        except Exception:
            zoom = 1.0
        zoom = max(0.25, min(4.0, zoom))
        if abs(zoom - self.board_zoom) < 0.001:
            return

        scroll = self._scroll_area()
        if scroll is not None and (not self.isVisible() or not scroll.isVisible()):
            scroll = None
        if anchor_pos is None:
            anchor_pos = self._cursor_anchor_pos() if self.isVisible() else self.rect().center()

        viewport_anchor = None
        if scroll is not None:
            try:
                viewport_anchor = scroll.viewport().mapFromGlobal(QtGui.QCursor.pos())
            except Exception:
                viewport_anchor = None
        logical_anchor = self.visual_to_board_point(anchor_pos)

        self.board_zoom = zoom
        self.reflow()
        self.update()

        if scroll is not None and viewport_anchor is not None:
            visual_anchor = self.board_to_visual_point(logical_anchor)
            scroll.horizontalScrollBar().setValue(visual_anchor.x() - viewport_anchor.x())
            scroll.verticalScrollBar().setValue(visual_anchor.y() - viewport_anchor.y())

        if save:
            self._schedule_zoom_save()

    def _handle_board_zoom_wheel(self, event, anchor_pos=None):
        if self.layout_mode != "board":
            return False
        mods = event.modifiers() if hasattr(event, "modifiers") else QtWidgets.QApplication.keyboardModifiers()
        if not (mods & QtCore.Qt.ControlModifier):
            return False
        delta = event.angleDelta().y()
        if not delta:
            return False
        factor = 1.12 if delta > 0 else 1.0 / 1.12
        if anchor_pos is None:
            anchor_pos = self._cursor_anchor_pos()
        self.set_board_zoom(self.board_zoom * factor, anchor_pos=anchor_pos, save=True)
        event.accept()
        return True

    def choose_background_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.window() or self,
            "Choose Board Background",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.gif);;All Files (*.*)"
        )
        if not path:
            return
        self.set_layout_mode("board")
        self.set_background_image(path)

    def _menu_style(self):
        return """
            QMenu { background: #333333; color: #F5F5F7; border: 1px solid #444444; padding: 4px; }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background: #3498DB; }
            QMenu::separator { height: 1px; background: #333333; margin: 4px 8px; }
            QLabel { color: #D8DEE9; font-size: 11px; }
            QSlider::groove:horizontal { height: 4px; background: #444444; border-radius: 2px; }
            QSlider::handle:horizontal { width: 13px; margin: -5px 0; background: #3498DB; border-radius: 6px; }
            QSlider::sub-page:horizontal { background: #3498DB; border-radius: 2px; }
        """

    def _add_slider_action(self, menu, title, value, minimum, maximum, on_changed, on_finished):
        widget = QtWidgets.QWidget(menu)
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(5)

        label = QtWidgets.QLabel(f"{title}: {int(value)}%")
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(int(value))
        slider.setFixedWidth(190)

        def update_value(v):
            label.setText(f"{title}: {int(v)}%")
            on_changed(v)

        slider.valueChanged.connect(update_value)
        slider.sliderReleased.connect(on_finished)
        layout.addWidget(label)
        layout.addWidget(slider)

        action = QtWidgets.QWidgetAction(menu)
        action.setDefaultWidget(widget)
        menu.addAction(action)
        return action

    def show_background_menu(self, global_pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(self._menu_style())
        menu.aboutToHide.connect(self._auto_save)
        menu.addAction("Add Background", self.choose_background_image)
        if self.background_path:
            menu.addAction("Clear Background", self.clear_background_image)
            menu.addSeparator()
            self._add_slider_action(
                menu,
                "Scale",
                self.background_scale * 100.0,
                10,
                400,
                lambda v: self.set_background_scale(v / 100.0, save=False),
                self._auto_save
            )
            self._add_slider_action(
                menu,
                "Opacity",
                self.background_opacity * 100.0,
                5,
                100,
                lambda v: self.set_background_opacity(v / 100.0, save=False),
                self._auto_save
            )
        menu.exec_(global_pos)
        
    def add_button(self, btn):
        btn.setParent(self)
        btn.show()
        btn.deleted.connect(self.remove_button)
        btn.position_changed.connect(self.on_button_position_changed)
        self.buttons.append(btn)
        if self.layout_mode == "board" and btn.board_pos == QtCore.QPoint(0, 0):
            btn.board_pos = self._next_board_position()
        btn.set_free_move_mode(self.layout_mode == "board")
        self.reflow()
        
    def remove_button(self, btn):
        if btn in self.buttons:
            self.buttons.remove(btn)
            btn.hide()
            btn.deleteLater()
            self.reflow()
            
    def clear_all(self):
        for b in self.buttons[:]:
            b.deleteLater()
        self.buttons = []
        self.reflow()

    def _auto_save(self):
        try:
            window = self.window()
            if hasattr(window, 'save_data'):
                window.save_data()
        except Exception:
            pass

    def set_layout_mode(self, mode, save=True):
        mode = "board" if mode == "board" else "ordered"
        if mode == self.layout_mode:
            return
        self.layout_mode = mode
        self.layout_mode_changed.emit(mode)
        self.setAcceptDrops(mode == "ordered")
        self.indicator.hide()
        self.drop_index = -1
        if mode == "board":
            for btn in self.buttons:
                if btn.board_pos == QtCore.QPoint(0, 0):
                    btn.board_pos = self.visual_to_board_point(btn.pos())
                btn.set_free_move_mode(True)
        else:
            for btn in self.buttons:
                btn.board_pos = self.visual_to_board_point(btn.pos())
                btn.set_free_move_mode(False)
        self.reflow()
        if save:
            self._auto_save()

    def _next_board_position(self):
        margin = 12
        spacing = 10
        zoom = max(0.05, float(self.board_zoom or 1.0))
        max_width = max(int(round(max(self.width(), 220) / zoom)), 220)
        placed = [btn for btn in self.buttons if btn.board_pos != QtCore.QPoint(0, 0)]
        if not placed:
            return QtCore.QPoint(margin, margin)
        last = placed[-1]
        x = last.board_pos.x() + last.board_size.width() + spacing
        y = last.board_pos.y()
        if x + max(80, last.board_size.width()) > max_width - margin:
            x = margin
            y = max(btn.board_pos.y() + btn.board_size.height() for btn in placed) + spacing
        return QtCore.QPoint(x, y)

    def update_board_bounds(self):
        if self.layout_mode != "board":
            return
        viewport = self._viewport_size()
        bottom = max(50, viewport.height())
        right = max(viewport.width(), 220)
        margin = 16
        zoom = max(0.05, float(self.board_zoom or 1.0))
        if not self._background_pixmap.isNull():
            bg_w = int(self._background_pixmap.width() * self.background_scale * zoom)
            bg_h = int(self._background_pixmap.height() * self.background_scale * zoom)
            bottom = max(bottom, bg_h + margin)
            right = max(right, bg_w + margin)
        for btn in self.buttons:
            btn_right = int(round((btn.board_pos.x() + btn.board_size.width()) * zoom))
            btn_bottom = int(round((btn.board_pos.y() + btn.board_size.height()) * zoom))
            bottom = max(bottom, btn_bottom + margin)
            right = max(right, btn_right + margin)
        self.setMinimumHeight(bottom)
        self.setMinimumWidth(right)
        if self.width() != right or self.height() != bottom:
            self.resize(right, bottom)

    def on_button_position_changed(self, btn):
        if self.layout_mode != "board":
            return
        btn.board_pos = self.visual_to_board_point(btn.pos())
        self.update_board_bounds()
        self._auto_save()
        
    def reflow(self):
        if self.layout_mode == "board":
            if not self.buttons:
                self.update_board_bounds()
                return
            for btn in self.buttons:
                if btn.free_move_mode:
                    btn._apply_button_size()
                else:
                    btn.set_free_move_mode(True)
                pos = QtCore.QPoint(btn.board_pos)
                if pos == QtCore.QPoint(0, 0):
                    pos = self._next_board_position()
                    btn.board_pos = QtCore.QPoint(pos)
                pos.setX(max(8, pos.x()))
                pos.setY(max(8, pos.y()))
                btn.board_pos = QtCore.QPoint(pos)
                btn.move(self.board_to_visual_point(pos))
            self.update_board_bounds()
            return

        if not self.buttons:
            self.setMinimumWidth(0)
            self.setMinimumHeight(50)
            return
            
        margin = 8
        spacing = 8
        x = margin
        y = margin
        row_height = 0
        max_width = max(self.width(), 200)
        
        for btn in self.buttons:
            btn.set_free_move_mode(False)
            bw = btn.width()
            bh = btn.height()
            
            if x + bw > max_width - margin and x > margin:
                x = margin
                y += row_height + spacing
                row_height = 0
                
            btn.move(x, y)
            x += bw + spacing
            row_height = max(row_height, bh)
            
        self.setMinimumWidth(0)
        self.setMinimumHeight(y + row_height + margin)
        
    def resizeEvent(self, e):
        super(FlowContainer, self).resizeEvent(e)
        self.reflow()

    def _normalized_rect(self, start, end):
        return QtCore.QRect(start, end).normalized()

    def _button_at(self, pos):
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, SetButton):
                return child
            child = child.parentWidget()
        return None

    def _select_buttons_in_rect(self, rect, modifiers):
        buttons = [btn for btn in self.buttons if rect.intersects(btn.geometry())]
        members = []
        seen = set()
        for btn in buttons:
            for member in btn.get_resolved_members():
                if member not in seen:
                    members.append(member)
                    seen.add(member)

        if members:
            if modifiers & QtCore.Qt.ControlModifier:
                cmds.select(members, tgl=True)
            elif modifiers & QtCore.Qt.ShiftModifier:
                cmds.select(members, add=True)
            else:
                cmds.select(members, r=True)
            cmds.inViewMessage(msg=f"Selected {len(members)} control(s)", pos='midCenter', fade=True)
        elif not (modifiers & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier)):
            cmds.select(clear=True)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton and self._start_board_pan(e):
            return
        if self.layout_mode == "board" and e.button() == QtCore.Qt.LeftButton and self._button_at(e.pos()) is None:
            self._selection_active = True
            self._selection_origin = QtCore.QPoint(e.pos())
            self._selection_rect = QtCore.QRect(self._selection_origin, self._selection_origin)
            self.update()
            e.accept()
            return
        super(FlowContainer, self).mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._update_board_pan(e):
            return
        if self._selection_active:
            self._selection_rect = self._normalized_rect(self._selection_origin, e.pos())
            self.update()
            e.accept()
            return
        super(FlowContainer, self).mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton and self._end_board_pan(e):
            return
        if self._selection_active and e.button() == QtCore.Qt.LeftButton:
            rect = QtCore.QRect(self._selection_rect)
            self._selection_active = False
            self._selection_rect = QtCore.QRect()
            self.update()
            if rect.width() > 4 and rect.height() > 4:
                self._select_buttons_in_rect(rect, e.modifiers())
            e.accept()
            return
        super(FlowContainer, self).mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if self._handle_board_zoom_wheel(e):
            return
        super(FlowContainer, self).wheelEvent(e)

    def contextMenuEvent(self, e):
        if self._button_at(e.pos()) is None:
            e.accept()
            self.show_background_menu(e.globalPos())
            return
        super(FlowContainer, self).contextMenuEvent(e)

    def paintEvent(self, e):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if self.layout_mode == "board":
            painter.fillRect(self.rect(), QtGui.QColor("#333333"))
            if not self._background_pixmap.isNull():
                painter.setOpacity(self.background_opacity)
                zoom = max(0.05, float(self.board_zoom or 1.0))
                bg_w = int(self._background_pixmap.width() * self.background_scale * zoom)
                bg_h = int(self._background_pixmap.height() * self.background_scale * zoom)
                target = QtCore.QRect(0, 0, bg_w, bg_h)
                painter.drawPixmap(target, self._background_pixmap)
                painter.setOpacity(1.0)
        if self._selection_active and not self._selection_rect.isNull():
            fill = QtGui.QColor(52, 152, 219, 45)
            border = QtGui.QColor(90, 180, 255, 220)
            painter.fillRect(self._selection_rect, fill)
            painter.setPen(QtGui.QPen(border, 1.4, QtCore.Qt.DashLine))
            painter.drawRect(self._selection_rect.adjusted(0, 0, -1, -1))
        painter.end()
        
    def get_insert_index(self, pos):
        if not self.buttons:
            return 0
        for i, btn in enumerate(self.buttons):
            geo = btn.geometry()
            if abs(pos.y() - geo.center().y()) < geo.height() * 0.7:
                if pos.x() < geo.center().x():
                    return i
        return len(self.buttons)
        
    def get_indicator_pos(self, index):
        if not self.buttons:
            return QtCore.QPoint(8, 8)
        if index >= len(self.buttons):
            btn = self.buttons[-1]
            return QtCore.QPoint(btn.geometry().right() + 4, btn.y())
        return QtCore.QPoint(self.buttons[index].x() - 4, self.buttons[index].y())
            
    def dragEnterEvent(self, e):
        if self.layout_mode == "ordered" and self.manual_mode and e.mimeData().hasText():
            e.acceptProposedAction()
            
    def dragMoveEvent(self, e):
        if self.layout_mode != "ordered" or not self.manual_mode:
            return
        if e.mimeData().hasText():
            e.acceptProposedAction()
            idx = self.get_insert_index(e.pos())
            self.drop_index = idx
            pos = self.get_indicator_pos(idx)
            self.indicator.setFixedHeight(26)
            self.indicator.move(pos)
            self.indicator.show()
            
    def dragLeaveEvent(self, e):
        self.indicator.hide()
        self.drop_index = -1
        
    def dropEvent(self, e):
        self.indicator.hide()
        if self.layout_mode != "ordered" or not self.manual_mode:
            return
            
        name = e.mimeData().text()
        src_btn = None
        src_idx = -1
        for i, b in enumerate(self.buttons):
            if b.set_name == name:
                src_btn = b
                src_idx = i
                break
                
        if src_btn is None:
            return
            
        target_idx = self.drop_index if self.drop_index != -1 else len(self.buttons)
            
        if src_idx == target_idx or src_idx == target_idx - 1:
            self.drop_index = -1
            return
            
        self.buttons.pop(src_idx)
        if target_idx > src_idx:
            target_idx -= 1
        self.buttons.insert(target_idx, src_btn)
        
        self.drop_index = -1
        self.reflow()
        self._auto_save()
        e.acceptProposedAction()
        
    def sort_name(self):
        self.buttons.sort(key=lambda b: b.set_name.lower())
        self.set_layout_mode("ordered", save=False)
        self.reflow()
        self._auto_save()
        
    def sort_color(self):
        self.buttons.sort(key=lambda b: b.color.lower())
        self.set_layout_mode("ordered", save=False)
        self.reflow()
        self._auto_save()
        
    def update_all_tooltips(self):
        for btn in self.buttons:
            btn.update_tooltip()


# ============================================================================
# TAB PAGE
# ============================================================================

class TabPage(QtWidgets.QWidget):
    namespace_changed = QtCore.Signal()
    
    def __init__(self, parent=None):
        super(TabPage, self).__init__(parent)
        self.current_namespace = ""
        self.namespace_dynamic = False
        self._last_auto_namespaces = []
        self._manual_namespace_selection_signature = None
        self._setting_namespace_programmatically = False
        self.setup_ui()
        self._auto_ns_timer = QtCore.QTimer(self)
        self._auto_ns_timer.setInterval(600)
        self._auto_ns_timer.timeout.connect(self.auto_namespace_from_selection)
        self._auto_ns_timer.start()
        
    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # Namespace row
        ns_row = QtWidgets.QHBoxLayout()
        ns_row.setSpacing(6)
        
        self.ns_combo = QtWidgets.QComboBox()
        self.ns_combo.setMinimumWidth(100)
        self.ns_combo.setStyleSheet("""
            QComboBox {
                background-color: #383838;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 6px 10px;
                color: #F5F5F7;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; }
            QComboBox QAbstractItemView {
                background-color: #383838;
                selection-background-color: #3498DB;
                border: 1px solid #444444;
            }
        """)
        self.ns_combo.currentTextChanged.connect(self.on_ns_changed)
        ns_row.addWidget(self.ns_combo)
        
        self.namespace_lock_btn = QtWidgets.QPushButton()
        self.namespace_lock_btn.setFixedSize(28, 28)
        self.namespace_lock_btn.setCheckable(True)
        self.namespace_lock_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.namespace_lock_btn.setStyleSheet("""
            QPushButton {
                background-color: #383838;
                color: #A0A7B4;
                font-size: 13px;
                border: 1px solid #444444;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #3D3D3D; color: #FFF; border-color: #555; }
            QPushButton:checked {
                background-color: #E67E22;
                color: white;
                border-color: #E67E22;
            }
        """)
        self.namespace_lock_btn.clicked.connect(lambda checked=False: self.set_namespace_dynamic(not checked))
        ns_row.addWidget(self.namespace_lock_btn)
        self._update_namespace_lock_button()
        
        pick_btn = QtWidgets.QPushButton("◎")
        pick_btn.setFixedSize(28, 28)
        pick_btn.setCursor(QtCore.Qt.PointingHandCursor)
        pick_btn.setToolTip("Get namespace from selection")
        pick_btn.setStyleSheet("""
            QPushButton {
                background-color: #E67E22;
                color: white;
                font-size: 14px;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #D35400; }
        """)
        pick_btn.clicked.connect(self.pick_from_selection)
        ns_row.addWidget(pick_btn)
        pick_btn.setVisible(False)
        pick_btn.setFixedSize(0, 0)
        
        ns_row.addSpacing(4)

        mode_style = """
            QPushButton {
                background-color: #383838;
                color: #A0A7B4;
                font-size: 10px;
                font-weight: 600;
                border: 1px solid #444444;
                padding: 0 9px;
            }
            QPushButton:hover { background-color: #3D3D3D; color: #F5F5F7; }
            QPushButton:checked {
                background-color: #3498DB;
                color: white;
                border-color: #3498DB;
            }
        """
        self.row_mode_btn = QtWidgets.QPushButton("ROW")
        self.row_mode_btn.setFixedHeight(28)
        self.row_mode_btn.setCheckable(True)
        self.row_mode_btn.setChecked(True)
        self.row_mode_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.row_mode_btn.setToolTip("Ordered row mode")
        self.row_mode_btn.setStyleSheet(mode_style + "QPushButton { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }")
        self.row_mode_btn.clicked.connect(lambda: self.set_layout_mode("ordered"))
        ns_row.addWidget(self.row_mode_btn)

        self.board_mode_btn = QtWidgets.QPushButton("BOARD")
        self.board_mode_btn.setFixedHeight(28)
        self.board_mode_btn.setCheckable(True)
        self.board_mode_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.board_mode_btn.setToolTip("Free board mode: drag sets anywhere")
        self.board_mode_btn.setStyleSheet(mode_style + "QPushButton { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }")
        self.board_mode_btn.clicked.connect(lambda: self.set_layout_mode("board"))
        ns_row.addWidget(self.board_mode_btn)

        ns_row.addStretch()
        
        # Add set button
        self.add_btn = QtWidgets.QPushButton("+")
        self.add_btn.setFixedSize(38, 38)
        self.add_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.add_btn.setToolTip("Create set from selection")
        self.add_btn.setStyleSheet("""
            QPushButton {
                background-color: #383838;
                color: white;
                font-size: 22px;
                font-weight: bold;
                border: 1px solid #444444;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #3498DB; border-color: #3498DB; }
        """)
        self.add_btn.clicked.connect(self.show_add_popup)
        ns_row.addWidget(self.add_btn)
        
        layout.addLayout(ns_row)
        
        # Scroll + container
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("""
            QScrollArea {
                background-color: #333333;
                border: 1px solid #333333;
                border-radius: 6px;
            }
        """)
        
        self.container = FlowContainer()
        self.container.setStyleSheet("background-color: #333333;")
        self.container.layout_mode_changed.connect(self._sync_layout_mode_buttons)
        self.scroll.setWidget(self.container)
        self.scroll.viewport().installEventFilter(self)
        self.container.installEventFilter(self)
        
        layout.addWidget(self.scroll)

    def eventFilter(self, obj, event):
        if self.container.layout_mode == "board":
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.MiddleButton:
                if self.container._start_board_pan(event):
                    return True
            if event.type() == QtCore.QEvent.MouseMove:
                if self.container._update_board_pan(event):
                    return True
            if event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.MiddleButton:
                if self.container._end_board_pan(event):
                    return True
        if event.type() == QtCore.QEvent.Wheel and self.container.layout_mode == "board":
            mods = event.modifiers() if hasattr(event, "modifiers") else QtWidgets.QApplication.keyboardModifiers()
            if mods & QtCore.Qt.ControlModifier:
                delta = event.angleDelta().y()
                if delta:
                    anchor = self.container._cursor_anchor_pos()
                    factor = 1.12 if delta > 0 else 1.0 / 1.12
                    self.container.set_board_zoom(self.container.board_zoom * factor, anchor_pos=anchor, save=True)
                    event.accept()
                    return True
        return super(TabPage, self).eventFilter(obj, event)
        
    def refresh_namespaces(self):
        current = self.ns_combo.currentText()
        self.ns_combo.blockSignals(True)
        self.ns_combo.clear()
        self.ns_combo.addItem("(no namespace)")
        for ns in get_scene_namespaces():
            self.ns_combo.addItem(ns)
        idx = self.ns_combo.findText(current)
        if idx >= 0:
            self.ns_combo.setCurrentIndex(idx)
        self.ns_combo.blockSignals(False)

    def _update_namespace_lock_button(self):
        if not hasattr(self, "namespace_lock_btn"):
            return
        locked = not bool(self.namespace_dynamic)
        self.namespace_lock_btn.blockSignals(True)
        self.namespace_lock_btn.setChecked(locked)
        self.namespace_lock_btn.setText(u"\U0001F512" if locked else u"\U0001F513")
        self.namespace_lock_btn.setToolTip(
            "Static namespaces: sets use saved rig namespaces"
            if locked else
            "Dynamic namespaces: sets follow the active/selected rig namespace"
        )
        self.namespace_lock_btn.blockSignals(False)

    def set_namespace_dynamic(self, dynamic, save=True):
        self.namespace_dynamic = bool(dynamic)
        self._manual_namespace_selection_signature = None
        self._update_namespace_lock_button()
        self.refresh_namespaces()
        if self.namespace_dynamic:
            self.auto_namespace_from_selection()
        else:
            current = self.current_namespace or "(no namespace)"
            self.ns_combo.setToolTip(f"Static namespace mode: {current}")
            self.container.update_all_tooltips()
        self.namespace_changed.emit()
        if save:
            window = self.window()
            if hasattr(window, 'save_data'):
                window.save_data()

    def auto_namespace_from_selection(self):
        if not self.namespace_dynamic:
            current = self.current_namespace or "(no namespace)"
            self.ns_combo.setToolTip(f"Static namespace mode: {current}")
            self.container.update_all_tooltips()
            return
        namespaces = get_namespaces_from_selection()
        selection_signature = tuple(namespaces)
        if self._manual_namespace_selection_signature == selection_signature:
            current = self.current_namespace or "(no namespace)"
            self.ns_combo.setToolTip(f"Manual namespace: {current}")
            self.container.update_all_tooltips()
            return
        self._manual_namespace_selection_signature = None
        if namespaces == self._last_auto_namespaces:
            return
        self._last_auto_namespaces = list(namespaces)
        if len(namespaces) == 1:
            self.set_namespace(namespaces[0])
            self.ns_combo.setToolTip(f"Auto namespace from selection: {namespaces[0] or '(no namespace)'}")
        elif len(namespaces) > 1:
            preview = ", ".join(ns if ns else "(no namespace)" for ns in namespaces[:5])
            if len(namespaces) > 5:
                preview += f" +{len(namespaces) - 5}"
            self.ns_combo.setToolTip(f"Auto multi namespace target: {preview}")
        else:
            self.ns_combo.setToolTip("Select a rig control to auto-detect namespace")
        self.container.update_all_tooltips()
        
    def pick_from_selection(self):
        ns = get_namespace_from_selection()
        if ns:
            self.set_namespace(ns)
            cmds.inViewMessage(msg=f"Namespace: {ns}", pos='midCenter', fade=True)
        else:
            self.set_namespace("")
            
    def on_ns_changed(self, text):
        self.current_namespace = "" if text == "(no namespace)" else text
        if self._setting_namespace_programmatically:
            self._manual_namespace_selection_signature = None
        else:
            self._manual_namespace_selection_signature = tuple(get_namespaces_from_selection())
        self.container.update_all_tooltips()
        self.namespace_changed.emit()
        
    def get_namespace(self):
        return self.current_namespace

    def is_namespace_dynamic(self):
        return bool(self.namespace_dynamic)

    def get_target_namespaces(self):
        if self.namespace_dynamic:
            clean = []
            for ns in self._last_auto_namespaces or []:
                ns = ns or ""
                if ns not in clean:
                    clean.append(ns)
            if clean:
                return clean
        return [self.current_namespace or ""]
        
    def set_namespace(self, ns):
        self._setting_namespace_programmatically = True
        try:
            if ns:
                idx = self.ns_combo.findText(ns)
                if idx >= 0:
                    self.ns_combo.setCurrentIndex(idx)
                else:
                    self.ns_combo.addItem(ns)
                    self.ns_combo.setCurrentText(ns)
            else:
                self.ns_combo.setCurrentIndex(0)
            self.current_namespace = ns
            self._manual_namespace_selection_signature = None
            self.container.update_all_tooltips()
        finally:
            self._setting_namespace_programmatically = False

    def _sync_layout_mode_buttons(self, mode):
        mode = "board" if mode == "board" else "ordered"
        self.row_mode_btn.blockSignals(True)
        self.board_mode_btn.blockSignals(True)
        self.row_mode_btn.setChecked(mode == "ordered")
        self.board_mode_btn.setChecked(mode == "board")
        self.row_mode_btn.blockSignals(False)
        self.board_mode_btn.blockSignals(False)
        if hasattr(self, "scroll"):
            self.scroll.setWidgetResizable(mode != "board")
            self.scroll.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarAsNeeded if mode == "board" else QtCore.Qt.ScrollBarAlwaysOff
            )
            self.scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

    def set_layout_mode(self, mode, save=True):
        mode = "board" if mode == "board" else "ordered"
        self._sync_layout_mode_buttons(mode)
        self.container.set_layout_mode(mode, save=save)

    def choose_background_image(self):
        self.container.choose_background_image()

    def show_background_menu(self, pos):
        self.container.show_background_menu(self.container.mapToGlobal(pos))

    def clear_background_image(self):
        self.container.clear_background_image()
        
    def show_add_popup(self):
        sel = cmds.ls(sl=True, long=True)
        if not sel:
            cmds.warning("Nothing selected!")
            return
            
        popup = PopupInput(self, "Set name...")
        popup.submitted.connect(lambda name: self.create_set(name, sel))
        btn_pos = self.add_btn.mapToGlobal(QtCore.QPoint(0, self.add_btn.height() + 5))
        popup.move(btn_pos.x() - 150, btn_pos.y())
        popup.show()
        
    def create_set(self, name, sel):
        if not name:
            name = f"Set{len(self.container.buttons)+1}"
        member_namespaces = get_member_namespace_map(sel)
        namespaces = unique_namespaces_from_member_map(member_namespaces)
        if len(namespaces) == 1:
            self.set_namespace(namespaces[0])
        btn = SetButton(
            name,
            sel,
            DEFAULT_COLOR,
            get_namespace_func=self.get_namespace,
            get_namespaces_func=self.get_target_namespaces,
            namespace_dynamic_func=self.is_namespace_dynamic,
            namespaces=namespaces,
            member_namespaces=member_namespaces,
            namespace_mode="page"
        )
        self.container.add_button(btn)
        
        # Auto-save to scene (this also creates the dagContainer if needed)
        window = self.window()
        if hasattr(window, 'save_data'):
            window.save_data()
        
        cmds.inViewMessage(msg=f"'{name}' ({len(sel)})", pos='midCenter', fade=True)
        
    def get_data(self):
        return {
            "namespace": self.current_namespace,
            "namespace_dynamic": self.namespace_dynamic,
            "layout_mode": self.container.layout_mode,
            "background_image": self.container.background_path,
            "background_scale": self.container.background_scale,
            "background_opacity": self.container.background_opacity,
            "board_zoom": round(float(self.container.board_zoom), 4),
            "sets": [b.get_data() for b in self.container.buttons]
        }
        
    def load_data(self, data):
        self.namespace_dynamic = bool(data.get("namespace_dynamic", False))
        self._update_namespace_lock_button()
        self.set_namespace(data.get("namespace", ""))
        self.set_layout_mode(data.get("layout_mode", "ordered"), save=False)
        self.container.set_background_image(data.get("background_image", ""), save=False)
        self.container.set_background_scale(data.get("background_scale", 1.0), save=False)
        self.container.set_background_opacity(data.get("background_opacity", 0.92), save=False)
        self.container.set_board_zoom(data.get("board_zoom", 1.0), save=False)
        for s in data.get("sets", []):
            saved_member_namespaces = s.get("member_namespaces")
            saved_namespaces = s.get("namespaces", [])
            if not saved_namespaces and not saved_member_namespaces:
                saved_namespaces = [self.current_namespace or ""]
            btn = SetButton(
                s.get("name", "Set"),
                s.get("members", []),
                s.get("color", DEFAULT_COLOR),
                get_namespace_func=self.get_namespace,
                get_namespaces_func=self.get_target_namespaces,
                namespace_dynamic_func=self.is_namespace_dynamic,
                board_pos=s.get("board_pos"),
                namespaces=saved_namespaces,
                namespace_mode=s.get("namespace_mode", "page"),
                member_namespaces=saved_member_namespaces,
                size_scale=s.get("size_scale", 1.0),
                board_size=s.get("board_size")
            )
            self.container.add_button(btn)
        self.container.reflow()


# ============================================================================
# TAB BUTTON
# ============================================================================

class TabButton(QtWidgets.QPushButton):
    close_clicked = QtCore.Signal(int)
    rename_requested = QtCore.Signal(int)
    
    def __init__(self, text, index, parent=None):
        super(TabButton, self).__init__(text, parent)
        self.index = index
        self.setCheckable(True)
        self.setFixedHeight(26)
        self.setMinimumWidth(60)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.update_style(False)
        
    def update_style(self, selected):
        if selected:
            self.setStyleSheet("""
                QPushButton {
                background-color: #383838;
                    color: #FFF;
                    font-size: 11px;
                    font-weight: 500;
                    border: 1px solid #333333;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                    padding: 4px 12px;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #333333;
                    color: #888;
                    font-size: 11px;
                    border: 1px solid #333333;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                    padding: 4px 12px;
                }
                QPushButton:hover {
                background-color: #383838;
                    color: #FFF;
                }
            """)
            
    def mouseDoubleClickEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.rename_requested.emit(self.index)
        else:
            super(TabButton, self).mouseDoubleClickEvent(e)
            
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton:
            self.close_clicked.emit(self.index)
        else:
            super(TabButton, self).mousePressEvent(e)
            
    def contextMenuEvent(self, e):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #333333; color: #F5F5F7; border: 1px solid #444444; padding: 4px; }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background: #3498DB; }
            QMenu::separator { height: 1px; background: #333333; margin: 4px 8px; }
        """)
        
        rename_act = menu.addAction("Rename Tab")
        rename_act.triggered.connect(lambda: self.rename_requested.emit(self.index))
        
        menu.addSeparator()
        close_act = menu.addAction("Close Tab")
        close_act.triggered.connect(lambda: self.close_clicked.emit(self.index))
            
        menu.exec_(e.globalPos())


# ============================================================================
# TAB BAR WIDGET
# ============================================================================

class TabBarWidget(QtWidgets.QWidget):
    tab_changed = QtCore.Signal(int)
    tab_close_requested = QtCore.Signal(int)
    new_tab_requested = QtCore.Signal()
    tab_rename_requested = QtCore.Signal(int)
    
    def __init__(self, parent=None):
        super(TabBarWidget, self).__init__(parent)
        self.tab_buttons = []
        self.current_index = -1
        self.setup_ui()
        
    def setup_ui(self):
        self.setFixedHeight(32)
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        self.tabs_layout = QtWidgets.QHBoxLayout()
        self.tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs_layout.setSpacing(2)
        layout.addLayout(self.tabs_layout)
        
        # Add tab button
        self.add_btn = QtWidgets.QPushButton("+")
        self.add_btn.setFixedSize(28, 26)
        self.add_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.add_btn.setToolTip("New tab")
        self.add_btn.setStyleSheet("""
            QPushButton {
                background-color: #333333;
                color: #666;
                font-size: 16px;
                font-weight: bold;
                border: 1px solid #333333;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #3498DB;
                color: white;
                border-color: #3498DB;
            }
        """)
        self.add_btn.clicked.connect(self.new_tab_requested.emit)
        layout.addWidget(self.add_btn)
        
        layout.addStretch()
        
    def add_tab(self, name):
        idx = len(self.tab_buttons)
        
        btn = TabButton(name, idx)
        btn.clicked.connect(self._make_tab_callback(idx))
        btn.close_clicked.connect(self.tab_close_requested.emit)
        btn.rename_requested.connect(self.tab_rename_requested.emit)
        
        self.tab_buttons.append(btn)
        self.tabs_layout.addWidget(btn)
        
        self.set_current_index(idx)
        return idx
        
    def _make_tab_callback(self, idx):
        def callback():
            self.set_current_index(idx)
        return callback
        
    def set_current_index(self, idx):
        if idx < 0 or idx >= len(self.tab_buttons):
            return
            
        self.current_index = idx
        
        for i, btn in enumerate(self.tab_buttons):
            btn.update_style(i == idx)
            btn.setChecked(i == idx)
                
        self.tab_changed.emit(idx)
        
    def remove_tab(self, idx):
        if idx < 0 or idx >= len(self.tab_buttons) or len(self.tab_buttons) <= 1:
            return
            
        btn = self.tab_buttons.pop(idx)
        self.tabs_layout.removeWidget(btn)
        btn.deleteLater()
        
        for i, b in enumerate(self.tab_buttons):
            b.index = i
            try:
                b.clicked.disconnect()
            except:
                pass
            b.clicked.connect(self._make_tab_callback(i))
                
        new_idx = min(idx, len(self.tab_buttons) - 1)
        self.set_current_index(new_idx)
        
    def set_tab_text(self, idx, text):
        if 0 <= idx < len(self.tab_buttons):
            self.tab_buttons[idx].setText(text)
                
    def tab_text(self, idx):
        if 0 <= idx < len(self.tab_buttons):
            return self.tab_buttons[idx].text()
        return ""
        
    def count(self):
        return len(self.tab_buttons)


# ============================================================================
# MAIN WINDOW - FRAMELESS
# ============================================================================

class SetManagerWindow(QtWidgets.QWidget):
    def __init__(self, parent=None, anchor_button=None):
        super(SetManagerWindow, self).__init__(parent)
        
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle("Set Manager")
        self.setMinimumSize(300, 200)
        self.resize(360, 300)
        self.anchor_button = anchor_button
        self._magnet_attached = True
        
        # Frameless window
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        
        self._base_opacity = 0.5
        self._hover_opacity = 1.0
        self._anim = None
        self._sort_mode = "Manual"
        
        self.pages = []
        
        self.build_ui()
        self.load_data()
        
        if len(self.pages) == 0:
            self.add_new_tab()
            
        QtCore.QTimer.singleShot(100, self.refresh_all_namespaces)

    def position_window(self, force=False):
        if not self._magnet_attached and not force:
            return
        if self.anchor_button is None:
            return

        try:
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

            x_pos = btn_center_x - self.width() // 2
            if x_pos < screen_rect.left():
                x_pos = screen_rect.left()
            elif x_pos + self.width() > screen_rect.right():
                x_pos = screen_rect.right() - self.width()

            if btn_top_y - screen_rect.top() >= self.height():
                y_pos = btn_top_y - self.height()
            else:
                y_pos = btn_bottom_y

            self._magnet_attached = True
            self.move(x_pos, y_pos)
        except Exception:
            pass

    def detach_from_anchor(self):
        self._magnet_attached = False

    def maybe_attach_to_anchor(self):
        if self.anchor_button is None:
            return
        current_pos = self.pos()
        was_attached = self._magnet_attached
        self._magnet_attached = True
        self.position_window(force=True)
        target_pos = self.pos()
        self.move(current_pos)
        self._magnet_attached = was_attached

        if (current_pos - target_pos).manhattanLength() <= 42:
            self._magnet_attached = True
            self.position_window(force=True)

    def animkey_auto_hide(self):
        if self._magnet_attached:
            self.hide()
            return True
        return False
        
    def build_ui(self):
        tooltip_palette = QtGui.QPalette()
        tooltip_palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#252525"))
        tooltip_palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor("#F5F5F7"))
        QtWidgets.QToolTip.setPalette(tooltip_palette)
        
        # Main container with rounded corners
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Container frame
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet("""
            #mainContainer {
                background-color: #2B2B2B;
                border: 1px solid #333333;
                border-radius: 8px;
            }
        """)
        
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # Custom title bar
        self.title_bar = TitleBar(self, "Set Manager")
        container_layout.addWidget(self.title_bar)
        
        # Content area
        content = QtWidgets.QWidget()
        content.setStyleSheet("background-color: #2B2B2B;")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(8, 4, 8, 8)
        content_layout.setSpacing(0)
        
        # Tab bar
        self.tab_bar = TabBarWidget()
        self.tab_bar.tab_changed.connect(self.on_tab_changed)
        self.tab_bar.new_tab_requested.connect(self.add_new_tab)
        self.tab_bar.tab_close_requested.connect(self.close_tab)
        self.tab_bar.tab_rename_requested.connect(self.rename_tab)
        content_layout.addWidget(self.tab_bar)
        
        # Stacked widget
        self.stack = QtWidgets.QStackedWidget()
        self.stack.setStyleSheet("""
            QStackedWidget {
                background-color: #333333;
                border-radius: 0 6px 6px 6px;
            }
        """)
        content_layout.addWidget(self.stack)
        
        container_layout.addWidget(content)
        
        # Resize grip
        grip_container = QtWidgets.QWidget()
        grip_container.setFixedHeight(16)
        grip_container.setStyleSheet("background-color: #2B2B2B; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;")
        grip_layout = QtWidgets.QHBoxLayout(grip_container)
        grip_layout.setContentsMargins(0, 0, 4, 4)
        grip_layout.addStretch()
        
        self.resize_grip = ResizeGrip(self)
        grip_layout.addWidget(self.resize_grip)
        
        container_layout.addWidget(grip_container)
        
        main_layout.addWidget(self.container)
        
        # Context menu
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_window_menu)
        
        # Scrollbar style
        self.setStyleSheet("""
            QToolTip {
                color: #F5F5F7;
                background-color: #252525;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 5px 7px;
                font-size: 11px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #444;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #555;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
                height: 0;
            }
        """)
        
        self.setWindowOpacity(self._base_opacity)
        
    def add_new_tab(self, name=None):
        if not name:
            name = f"Tab {self.tab_bar.count() + 1}"
            
        page = TabPage()
        self.pages.append(page)
        self.stack.addWidget(page)
        
        idx = self.tab_bar.add_tab(name)
        self.stack.setCurrentIndex(idx)
        
        QtCore.QTimer.singleShot(50, page.refresh_namespaces)
        
        return page
        
    def close_tab(self, idx):
        if self.tab_bar.count() <= 1:
            return
            
        page = self.pages.pop(idx)
        self.stack.removeWidget(page)
        page.deleteLater()
        
        self.tab_bar.remove_tab(idx)
        
    def rename_tab(self, idx):
        current_name = self.tab_bar.tab_text(idx)
        popup = PopupInput(self, "Tab name...", current_name)
        popup.submitted.connect(lambda name: self.do_rename_tab(idx, name))
        
        pos = self.mapToGlobal(QtCore.QPoint(50, 50))
        popup.move(pos)
        popup.show()
        
    def do_rename_tab(self, idx, name):
        if name:
            self.tab_bar.set_tab_text(idx, name)
            
    def on_tab_changed(self, idx):
        self.stack.setCurrentIndex(idx)
        
    def current_page(self):
        idx = self.tab_bar.current_index
        if 0 <= idx < len(self.pages):
            return self.pages[idx]
        return None
        
    def refresh_all_namespaces(self):
        for page in self.pages:
            page.refresh_namespaces()
        
    def show_window_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #333333; color: #F5F5F7; border: 1px solid #444444; padding: 4px; }
            QMenu::item { padding: 6px 24px; }
            QMenu::item:selected { background: #3498DB; }
            QMenu::separator { height: 1px; background: #333333; margin: 4px 8px; }
        """)
        
        sort_menu = menu.addMenu("Sort")
        for mode in ["Manual", "Name", "Color"]:
            act = sort_menu.addAction(f"By {mode}" if mode != "Manual" else mode)
            act.setCheckable(True)
            act.setChecked(self._sort_mode == mode)
            act.triggered.connect(lambda checked, m=mode: self.set_sort(m))
            
        menu.addSeparator()
        
        clear_act = menu.addAction("Clear Current Tab")
        clear_act.triggered.connect(self.clear_current)
        
        menu.exec_(self.mapToGlobal(pos))
        
    def set_sort(self, mode):
        self._sort_mode = mode
        page = self.current_page()
        if page:
            page.container.manual_mode = (mode == "Manual")
            if mode == "Name":
                page.container.sort_name()
            elif mode == "Color":
                page.container.sort_color()
                
    def clear_current(self):
        page = self.current_page()
        if page and page.container.buttons:
            r = QtWidgets.QMessageBox.question(self, "Clear", "Delete all sets in this tab?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if r == QtWidgets.QMessageBox.Yes:
                page.container.clear_all()
        
    def enterEvent(self, e):
        self._animate(self._hover_opacity)
        
    def leaveEvent(self, e):
        self._animate(self._base_opacity)
        
    def _animate(self, val):
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(150)
        anim.setEndValue(val)
        anim.start(QtCore.QPropertyAnimation.DeleteWhenStopped)
        self._anim = anim
        
    def save_data(self):
        """Save sets data to the scene's animkey_sets container"""
        tabs = []
        for i, page in enumerate(self.pages):
            tabs.append({
                "name": self.tab_bar.tab_text(i),
                "data": page.get_data()
            })
            
        data = {
            "sort_mode": self._sort_mode,
            "current_tab": self.tab_bar.current_index,
            "tabs": tabs,
            "geometry": {
                "x": self.x(),
                "y": self.y(),
                "width": self.width(),
                "height": self.height()
            }
        }
        
        _save_to_scene(data)
            
    def load_data(self):
        """Load sets data from the scene's animkey_sets container"""
        data = _load_from_scene()
        if not data:
            return
        
        try:
            self._sort_mode = data.get("sort_mode", "Manual")
            
            # Restore geometry
            geo = data.get("geometry", {})
            if geo:
                self.move(geo.get("x", 100), geo.get("y", 100))
                self.resize(geo.get("width", 360), geo.get("height", 300))
            
            for tab_info in data.get("tabs", []):
                name = tab_info.get("name", "Tab")
                page = self.add_new_tab(name)
                page.load_data(tab_info.get("data", {}))
                page.container.manual_mode = (self._sort_mode == "Manual")
                
            current = data.get("current_tab", 0)
            if 0 <= current < self.tab_bar.count():
                self.tab_bar.set_current_index(current)
                
        except Exception as e:
            print(f"Load error: {e}")
            
    def closeEvent(self, event):
        global _win
        if not getattr(self, '_skip_save', False):
            self.save_data()
        _win = None
        QtWidgets.QWidget.closeEvent(self, event)


def show(anchor_button=None):
    global _win
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows()
    existing = uiMod.show_existing_animkey_tool_window(_win, anchor_button)
    if existing is not None:
        _win = existing
        return _win
    try:
        _win.close()
        _win.deleteLater()
    except:
        pass
    if cmds.window(WINDOW_OBJECT, exists=True):
        cmds.deleteUI(WINDOW_OBJECT)
        
    _win = SetManagerWindow(get_maya_main_window(), anchor_button=anchor_button)
    _win.position_window()
    _win.show()
    return _win


# Alias for toolbar compatibility
def execute(*args, **kwargs):
    """Entry point for toolbar button"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.selectionSets.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))

def clear_all_sets(*args):
    """
    Clear all selection sets and tabs.
    Resets to a single empty tab.
    """
    global _win
    
    # Ensure window exists
    if _win is None or not _win.isVisible():
        show()
    
    # Ask for confirmation
    result = QtWidgets.QMessageBox.question(
        _win, 
        "Clear All",
        "Delete ALL selection sets and tabs?\nThis cannot be undone.",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
    )
    
    if result != QtWidgets.QMessageBox.Yes:
        return
    
    # Clear all tabs except one
    while len(_win.pages) > 1:
        _win.close_tab(len(_win.pages) - 1)
    
    # Clear the remaining tab
    if _win.pages:
        _win.pages[0].container.clear_all()
        _win.pages[0].set_namespace("")
        _win.tab_bar.set_tab_text(0, "Tab 1")
    
    # Save the cleared state to the scene
    _win.save_data()
    
    cmds.inViewMessage(msg="All sets cleared", pos='midCenter', fade=True)


def export_sets(*args):
    """
    Export all selection sets to a JSON file.
    Reads directly from the scene's animkey_sets container.
    Opens a file dialog for the user to choose the save location.
    """
    # Get data from scene's dagContainer
    data = _load_from_scene()
    if not data or not data.get("tabs"):
        cmds.warning("Set Manager: No sets found in scene to export.")
        return
    
    # Get file path from user
    file_path = cmds.fileDialog2(
        caption="Export Selection Sets",
        fileFilter="JSON Files (*.json);;All Files (*.*)",
        dialogStyle=2,
        fileMode=0  # Save mode
    )
    
    if not file_path:
        return
    
    file_path = file_path[0]
    if not file_path.endswith('.json'):
        file_path += '.json'
    
    # Add version for export
    export_data = {
        "version": "1.3",
        "sort_mode": data.get("sort_mode", "Manual"),
        "current_tab": data.get("current_tab", 0),
        "tabs": data.get("tabs", []),
        "geometry": data.get("geometry", {})
    }
    
    try:
        with open(file_path, 'w') as f:
            json.dump(export_data, f, indent=2)
        cmds.inViewMessage(msg=f"Exported to: {os.path.basename(file_path)}", pos='midCenter', fade=True)
    except Exception as e:
        cmds.warning(f"Export failed: {e}")


def import_sets(*args):
    """
    Import selection sets from a JSON file.
    Replaces all existing tabs with the imported ones.
    Opens a file dialog for the user to choose the file.
    """
    global _win
    
    # Get file path from user
    file_path = cmds.fileDialog2(
        caption="Import Selection Sets",
        fileFilter="JSON Files (*.json);;All Files (*.*)",
        dialogStyle=2,
        fileMode=1  # Open existing file mode
    )
    
    if not file_path:
        return
    
    file_path = file_path[0]
    
    if not os.path.exists(file_path):
        cmds.warning(f"File not found: {file_path}")
        return
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        cmds.warning(f"Import failed: {e}")
        return
    
    tabs_data = data.get("tabs", [])
    if not tabs_data:
        cmds.warning("No tabs found in import file")
        return
    
    # Build the import data structure
    import_data = {
        "sort_mode": data.get("sort_mode", "Manual"),
        "current_tab": data.get("current_tab", 0),
        "tabs": tabs_data,
        "geometry": data.get("geometry", {"x": 100, "y": 100, "width": 360, "height": 300})
    }
    
    # Close existing window WITHOUT triggering save
    if _win is not None:
        try:
            _win._skip_save = True
            _win.close()
            _win.deleteLater()
        except:
            pass
        _win = None
    
    # Create the container and write data AFTER closing old window
    container = _create_sets_container()
    
    try:
        json_str = json.dumps(import_data)
        cmds.setAttr(f"{container}.{SETS_DATA_ATTR}", json_str, type="string")
    except Exception as e:
        cmds.warning(f"Failed to save imported data: {e}")
        return
    
    # Count total sets for the message
    total_sets = sum(len(t.get("data", {}).get("sets", [])) for t in tabs_data)
    
    # Open the window to show imported data
    show()
    
    cmds.inViewMessage(
        msg=f"Imported {len(tabs_data)} tab(s), {total_sets} set(s)",
        pos='midCenter', fade=True, fadeStayTime=3000
    )


if __name__ == "__main__":
    show()
