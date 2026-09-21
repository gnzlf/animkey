"""
    AnimKey Settings Window
    
    A comprehensive settings window with multiple tabs for configuring
    shortcuts, preferences, and system options.
"""

import os
import json
import shutil
import sys

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt
QColor = QtGui.QColor
QFont = QtGui.QFont
QKeySequence = QtGui.QKeySequence

from AnimKey.mods.themes import ThemeManager
from AnimKey.mods import configMod as config
from AnimKey.mods import uiMod as ui
from AnimKey.mods.storage import atomic_write_json, backup_corrupt_file

# Global state for picking mode
ACTIVE_PICKER = None


class AnimKeyUninstallDialog(QtWidgets.QDialog):
    """Single modern uninstall decision dialog."""

    KEEP_DATA = "keep"
    DELETE_ALL = "delete"

    def __init__(self, parent=None, user_data_exists=True):
        super(AnimKeyUninstallDialog, self).__init__(parent)
        self.choice = None
        self.user_data_exists = user_data_exists

        self.setWindowTitle("Uninstall AnimKey")
        self.setModal(True)
        self.setFixedSize(460, 310)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._build_ui()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        panel = QtWidgets.QFrame()
        panel.setObjectName("uninstallPanel")
        panel.setStyleSheet("""
            QFrame#uninstallPanel {
                background-color: #202025;
                border: 1px solid #5d4a33;
                border-radius: 12px;
            }
            QLabel {
                color: #e6e0d6;
                background: transparent;
            }
            QPushButton {
                border-radius: 8px;
                font-size: 12px;
                font-weight: 700;
                padding: 10px 14px;
            }
            QPushButton#keepButton {
                background-color: #29382f;
                border: 1px solid #78a878;
                color: #b9e6b5;
            }
            QPushButton#keepButton:hover {
                background-color: #334838;
                color: #ffffff;
            }
            QPushButton#deleteButton {
                background-color: #432628;
                border: 1px solid #b85d5d;
                color: #ffb0a8;
            }
            QPushButton#deleteButton:hover {
                background-color: #5a2e31;
                color: #ffffff;
            }
            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                color: #8f897f;
                padding: 0;
                font-size: 13px;
            }
            QPushButton#closeButton:hover {
                color: #ffffff;
                background-color: #7c3333;
            }
        """)
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 12)
        shadow.setColor(QtGui.QColor(0, 0, 0, 180))
        panel.setGraphicsEffect(shadow)
        root.addWidget(panel)

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(16)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Uninstall AnimKey")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #f0c987;")
        header.addWidget(title)
        header.addStretch()

        close_btn = QtWidgets.QPushButton("X")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(26, 24)
        close_btn.clicked.connect(self.reject)
        header.addWidget(close_btn)
        layout.addLayout(header)

        body = QtWidgets.QLabel(
            "This will remove AnimKey from Maya, close the toolbar, remove startup code, "
            "and delete installed AnimKey files.\n\nChoose what to do with your personal data."
        )
        body.setWordWrap(True)
        body.setStyleSheet("font-size: 12px; line-height: 150%; color: #c9c2b8;")
        layout.addWidget(body)

        data_text = "Preferences, shortcuts, workspace layout, snapshots, saved defaults."
        if not self.user_data_exists:
            data_text = "No AnimKey user data folder was found. Keep Data will uninstall files only."
        detail = QtWidgets.QLabel(data_text)
        detail.setWordWrap(True)
        detail.setStyleSheet("""
            QLabel {
                background-color: #2b2b31;
                border: 1px solid #3d3d46;
                border-radius: 8px;
                padding: 12px;
                color: #aaa39a;
                font-size: 11px;
            }
        """)
        layout.addWidget(detail)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(10)

        keep_btn = QtWidgets.QPushButton("Keep Data")
        keep_btn.setObjectName("keepButton")
        keep_btn.setToolTip("Uninstall AnimKey but keep your settings and saved data.")
        keep_btn.clicked.connect(self._keep_data)
        buttons.addWidget(keep_btn)

        delete_btn = QtWidgets.QPushButton("Delete Everything")
        delete_btn.setObjectName("deleteButton")
        delete_btn.setToolTip("Uninstall AnimKey and delete all AnimKey user data.")
        delete_btn.clicked.connect(self._delete_all)
        buttons.addWidget(delete_btn)

        layout.addLayout(buttons)

    def _keep_data(self):
        self.choice = self.KEEP_DATA
        self.accept()

    def _delete_all(self):
        self.choice = self.DELETE_ALL
        self.accept()

    def showEvent(self, event):
        super(AnimKeyUninstallDialog, self).showEvent(event)
        self._center_on_parent_or_screen()

    def _center_on_parent_or_screen(self):
        parent = self.parentWidget()
        if parent:
            target = parent.frameGeometry().center()
        else:
            app = QtWidgets.QApplication.instance()
            if app is None:
                return
            screen = app.primaryScreen()
            if screen is None:
                return
            target = screen.availableGeometry().center()
        self.move(target.x() - self.width() // 2, target.y() - self.height() // 2)


def ask_uninstall_choice(parent=None, user_data_exists=True):
    dialog = AnimKeyUninstallDialog(parent=parent, user_data_exists=user_data_exists)
    exec_fn = getattr(dialog, "exec_", None) or getattr(dialog, "exec")
    if exec_fn() == QtWidgets.QDialog.Accepted:
        return dialog.choice
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#                           SHORTCUTS CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Default shortcuts for buttons and functions
DEFAULT_SHORTCUTS = {
    # Tool buttons
    "ISO": "",
    "ALN": "",
    "OPP": "",
    "TRL": "",
    "OFF": "",
    "RST": "",
    "TMP": "",
    "HIR": "",
    "SEL": "",
    "MIR": "",
    "C": "",
    "LKN": "",
    "CAM": "",
    "WS": "",
    "PIV": "",
    "RUL": "",
    "RTM": "",
    
    # Tangent buttons
    "PLT": "",
    "STP": "",
    "FLT": "",
    "LIN": "",
    "CLP": "",
    "SPL": "",
    "AUT": "",
    
    # Extra tool buttons
    "RBK": "",
    "GMB": "",
    "SWT": "",
    "BAK": "",
    "SETS": "",
    "ACR": "",
    "BTNS": "",
    
    # Align submenu
    "Align Position": "",
    "Align Orientation": "",
    "Align Scale": "",
    
    # Reset Values submenu
    "Set Default Values": "",
    "Restore Default Values": "",
    "Clear All Saved Data": "",
    
    # Select Opposite submenu
    "Add Select Opposite": "",
    
    # Mirror submenu
    "All Mirror": "",
    "Mirror to Left": "",
    "Mirror to Right": "",
    "Toggle Auto Mirror": "",
    "Snapshot Mirror Settings": "",
    "Delete Snapshot": "",
    "Add Mirror Invert Exception": "",
    "Add Mirror Keep Exception": "",
    "Remove Mirror Exception": "",
    "Show Mirror Exceptions": "",
    "Clear All Mirror Exceptions": "",
    
    # Copy Animation submenu
    "Copy Animation": "",
    "Paste Animation": "",
    "Paste Insert": "",
    "Paste Opposite": "",
    "Copy Pose": "",
    "Paste Pose": "",
    
    # Follow Cam submenu
    "Follow Cam Translation & Rotation": "",
    "Follow Cam Translation Only": "",
    "Follow Cam Rotation Only": "",
    "Remove Follow Cam": "",
    
    # Link Objects submenu
    "Copy Link Frame": "",
    "Copy Link Playback Range": "",
    "Paste Link Frame": "",
    "Paste Link Next Frame": "",
    "Paste Link All Keys": "",
    "Toggle Auto Link": "",
    
    # Worldspace submenu
    "Copy Worldspace All Animation": "",
    "Copy Worldspace Selected Range": "",
    "Copy Worldspace Current Frame": "",
    "Paste Worldspace Animation": "",
    "Paste Worldspace Frame": "",
    "Toggle Auto Worldspace": "",
    
    # Other functions
    "Increase Values": "",
    "Decrease Values": "",
    "Move Keys Left": "",
    "Move Keys Right": "",
    "Clear Keys": "",
    "Select All Animation": "",
}


def get_shortcuts_file():
    """Get the path to the shortcuts configuration file"""
    user_folder = config.get_user_folder_path()
    return os.path.join(user_folder, "shortcuts.json")


def load_shortcuts():
    """Load shortcuts from file"""
    shortcuts_file = get_shortcuts_file()
    
    if os.path.exists(shortcuts_file):
        try:
            with open(shortcuts_file, 'r') as f:
                saved_shortcuts = json.load(f)
                # Merge with defaults
                shortcuts = DEFAULT_SHORTCUTS.copy()
                shortcuts.update(saved_shortcuts)
                if "SWT" not in saved_shortcuts and "COL" in saved_shortcuts:
                    shortcuts["SWT"] = saved_shortcuts["COL"]
                shortcuts.pop("COL", None)
                legacy_trail_key = "T" + "RC"
                if legacy_trail_key in shortcuts:
                    shortcuts["TRL"] = shortcuts.pop(legacy_trail_key)
                return shortcuts
        except (json.JSONDecodeError, IOError):
            backup_corrupt_file(shortcuts_file)
    
    return DEFAULT_SHORTCUTS.copy()


def save_shortcuts(shortcuts):
    """Save shortcuts to file"""
    shortcuts_file = get_shortcuts_file()
    
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(shortcuts_file), exist_ok=True)
        
        atomic_write_json(shortcuts_file, shortcuts)
        return True
    except IOError as e:
        cmds.warning(f"AnimKey: Could not save shortcuts: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#                           SHORTCUT EDIT WIDGET
# ═══════════════════════════════════════════════════════════════════════════════

class ShortcutEditWidget(QtWidgets.QLineEdit):
    """
    A custom line edit that captures keyboard shortcuts.
    Shows shortcuts as text like "Ctrl+Shift+ALN".
    """
    
    shortcutChanged = QtCore.Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Click and press keys...")
        self._shortcut = ""
        self._is_picking = False
        self._update_style()
    
    def _update_style(self):
        theme = ThemeManager.get_current_theme()
        bg = theme["bg_secondary"]
        border = theme["border_color"]
        text = theme["text_primary"]
        
        if self._is_picking:
            bg = "#3d2d1d"  # Slight orange tint
            border = "#d08770"  # Orange accent
        
        self.setStyleSheet(f'''
            QLineEdit {{
                color: {text};
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }}
            QLineEdit:focus {{
                border-color: {theme["accent_primary"]};
            }}
        ''')

    def setPickingMode(self, enabled):
        """Enable or disable picking mode (waiting for toolbar click)"""
        self._is_picking = enabled
        if enabled:
            self.setPlaceholderText("CLICK A BUTTON IN TOOLBAR...")
        else:
            self.setPlaceholderText("Click and press keys...")
        self._update_style()
    
    def keyPressEvent(self, event):
        """Capture key press and convert to shortcut string"""
        key = event.key()
        modifiers = event.modifiers()
        
        # Handle special keys
        if key == Qt.Key_Escape:
            self.clearShortcut()
            return
        
        if key == Qt.Key_Backspace or key == Qt.Key_Delete:
            self.clearShortcut()
            return
        
        # Build shortcut string
        parts = []
        
        if modifiers & Qt.ControlModifier:
            parts.append("Ctrl")
        if modifiers & Qt.AltModifier:
            parts.append("Alt")
        if modifiers & Qt.ShiftModifier:
            parts.append("Shift")
        
        # Get key name (only if it's not a modifier key itself)
        if key not in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            key_sequence = QKeySequence(key)
            key_name = key_sequence.toString()
            if key_name and key_name not in parts:
                parts.append(key_name)
        
        if parts:
            self._shortcut = "+".join(parts)
            self._update_display()
            self.shortcutChanged.emit(self._shortcut)
    
    def appendButtonName(self, button_name):
        """Append a toolbar button name to the current shortcut."""
        if self._shortcut:
            # Remove any existing button name at the end (replace it)
            parts = self._shortcut.split("+")
            # Keep only modifier parts (Ctrl, Alt, Shift)
            modifier_parts = [p for p in parts if p in ("Ctrl", "Alt", "Shift")]
            modifier_parts.append(button_name)
            self._shortcut = "+".join(modifier_parts)
        else:
            self._shortcut = button_name
        self._update_display()
        self.shortcutChanged.emit(self._shortcut)
    
    def setShortcut(self, shortcut):
        """Set the shortcut text"""
        self._shortcut = shortcut
        self._update_display()
        self.shortcutChanged.emit(shortcut)
        
    def _update_display(self):
        """Update display to show icon if the shortcut ends with a button name."""
        # Clear existing action icons
        for action in self.actions():
            self.removeAction(action)
            
        shortcut = self._shortcut
        if not shortcut:
            self.setText("")
            return
            
        parts = shortcut.split('+')
        last_part = parts[-1]
        
        try:
            from AnimKey.mods import mediaMod
            icon_path = mediaMod.get_button_icon(last_part)
            if icon_path and os.path.exists(icon_path):
                # Valid icon found for the last part
                icon = QtGui.QIcon(icon_path)
                # Create an action with this icon at trailing position
                self.addAction(icon, QtWidgets.QLineEdit.TrailingPosition)
                
                # Update text to show modifiers + " " instead of the button name
                mod_text = "+".join(parts[:-1])
                if mod_text:
                    self.setText(mod_text + " + ")
                else:
                    self.setText(" ")
                return
        except Exception:
            pass
            
        # Fallback to normal text
        self.setText(shortcut)
    
    def getShortcut(self):
        """Get the current shortcut"""
        return self._shortcut
    
    def clearShortcut(self):
        """Clear the shortcut completely"""
        self._shortcut = ""
        self._update_display()
        self.shortcutChanged.emit("")


# ═══════════════════════════════════════════════════════════════════════════════
#                           CUSTOM TITLE BAR
# ═══════════════════════════════════════════════════════════════════════════════

class TitleBar(QtWidgets.QWidget):
    def __init__(self, parent=None, title="Settings"):
        super(TitleBar, self).__init__(parent)
        self.parent_window = parent
        self._drag_pos = None
        self.setFixedHeight(32)
        self.setup_ui(title)
        
    def setup_ui(self, title):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
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
                background-color: #363636;
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
            self.parent_window.move(event.globalPos() - self._drag_pos)
            event.accept()
            
    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ═══════════════════════════════════════════════════════════════════════════════
#                           SETTINGS WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

class SettingsWindow(QtWidgets.QWidget):
    """
    Main settings window with tabbed interface.
    """
    
    def __init__(self, parent=None):
        # Get Maya main window as parent
        if parent is None:
            parent = ui.get_maya_main_window()
        
        super().__init__(parent)
        
        self.setObjectName("AnimKey_Settings")
        self.setWindowTitle("AnimKey Settings")
        self.setMinimumSize(600, 500)
        
        # Frameless + translucent — must use QWidget (not QDialog) for this to
        # work reliably on Windows. QDialog ignores WA_TranslucentBackground.
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        
        self._shortcuts = load_shortcuts()
        self._shortcut_widgets = {}
        self._lazy_tabs = {}
        self._drag_pos = None
        
        # Opacity: semi-transparent when idle, fully opaque on hover
        self._base_opacity = 0.82
        self._hover_opacity = 1.0
        self._anim = None
        
        self._build_ui()
        
        # Start semi-transparent like the Retimer window
        self.setWindowOpacity(self._base_opacity)
    
    def _build_ui(self):
        """Build the settings UI"""
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        
        # Main container with rounded corners
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("settingsContainer")
        self.container.setStyleSheet("""
            #settingsContainer {
                background-color: #3a3a3a;
                border: 1px solid #5a5a5a;
                border-radius: 8px;
            }
        """)
        
        main_layout = QtWidgets.QVBoxLayout(self.container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Custom title bar
        self.title_bar = TitleBar(self, "AnimKey Settings")
        main_layout.addWidget(self.title_bar)
        
        # Tab widget
        self.tab_widget = QtWidgets.QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border-top: 1px solid #5a5a5a;
                background-color: #3a3a3a;
            }
            QTabWidget::tab-bar {
                alignment: left;
            }
            QTabBar::tab {
                background-color: #363636;
                color: #888;
                padding: 8px 16px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
                margin-left: 10px;
            }
            QTabBar::tab:selected {
                background-color: #4d4d4d;
                color: #FFF;
                border-bottom: 2px solid #3498DB;
            }
            QTabBar::tab:hover {
                color: #FFF;
                background-color: #4d4d4d;
            }
        """)
        main_layout.addWidget(self.tab_widget)
        
        # Build the first tab immediately and defer heavier tabs until opened.
        self._create_general_tab()
        self._add_lazy_tab("Shortcuts", self._create_shortcuts_tab)
        self._add_lazy_tab("Workspace", self._create_workspace_tab)
        self._add_lazy_tab("Update", self._create_update_tab)
        self._add_lazy_tab("Exit", self._create_exit_tab)
        self.tab_widget.currentChanged.connect(self._load_lazy_tab)
        
        # Bottom buttons
        button_bar = QtWidgets.QFrame()
        button_bar.setStyleSheet("""
            QFrame {
                background-color: #363636;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                border-top: 1px solid #5a5a5a;
            }
        """)
        button_layout = QtWidgets.QHBoxLayout(button_bar)
        button_layout.setContentsMargins(16, 12, 16, 12)
        
        button_layout.addStretch()
        
        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_cancel.setFixedSize(90, 32)
        btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #4d4d4d;
                color: #AAA;
                border: 1px solid #666666;
                border-radius: 6px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3d2d2d;
                color: #FFF;
                border-color: #ff6b6b;
            }
        """)
        btn_cancel.clicked.connect(self.close)
        button_layout.addWidget(btn_cancel)
        
        btn_save = QtWidgets.QPushButton("Save")
        btn_save.setFixedSize(90, 32)
        btn_save.setCursor(QtCore.Qt.PointingHandCursor)
        btn_save.setStyleSheet("""
            QPushButton {
                background-color: #2d4d3d;
                color: #a3be8c;
                border: 1px solid #5a7a5a;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3d5d4d;
                border-color: #a3be8c;
                color: #FFF;
            }
        """)
        btn_save.clicked.connect(self._save_and_close)
        button_layout.addWidget(btn_save)
        self._settings_action_buttons = (btn_cancel, btn_save)
        
        main_layout.addWidget(button_bar)
        outer_layout.addWidget(self.container)

    def _add_lazy_tab(self, title, builder):
        placeholder = QtWidgets.QWidget()
        placeholder.setStyleSheet("background-color: transparent;")
        layout = QtWidgets.QVBoxLayout(placeholder)
        layout.addStretch()
        index = self.tab_widget.addTab(placeholder, title)
        self._lazy_tabs[index] = (title, builder)

    def _load_lazy_tab(self, index):
        data = self._lazy_tabs.pop(index, None)
        if not data:
            return

        title, builder = data
        tab = builder(add_to_tabs=False)

        was_blocked = self.tab_widget.blockSignals(True)
        try:
            self.tab_widget.removeTab(index)
            self.tab_widget.insertTab(index, tab, title)
            self.tab_widget.setCurrentIndex(index)
        finally:
            self.tab_widget.blockSignals(was_blocked)

    def _on_pick_clicked(self, widget):
        global ACTIVE_PICKER
        if ACTIVE_PICKER == widget:
            widget.setPickingMode(False)
            ACTIVE_PICKER = None
        else:
            if ACTIVE_PICKER:
                ACTIVE_PICKER.setPickingMode(False)
            ACTIVE_PICKER = widget
            widget.setPickingMode(True)

    def _clear_active_picker(self):
        global ACTIVE_PICKER
        if ACTIVE_PICKER:
            ACTIVE_PICKER.setPickingMode(False)
            ACTIVE_PICKER = None

    def _title_mouse_press(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
    
    def _title_mouse_move(self, event):
        if event.buttons() == QtCore.Qt.LeftButton and self._drag_pos:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()
    
    def _title_mouse_release(self, event):
        self._drag_pos = None
    
    def _create_general_tab(self, add_to_tabs=True):
        """Create the General/Folders tab"""
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Toolbar Scale Setting
        scale_group = QtWidgets.QGroupBox("Toolbar Scale")
        scale_group.setStyleSheet("""
            QGroupBox { 
                color: #AAA; 
                font-weight: bold; 
                border: 1px solid #5a5a5a; 
                border-radius: 4px; 
                margin-top: 1ex; 
                padding: 10px; 
            } 
            QGroupBox::title { 
                subcontrol-origin: margin; 
                left: 10px; 
                padding: 0 3px 0 3px; 
            }
        """)
        scale_layout = QtWidgets.QHBoxLayout(scale_group)
        
        scale_label = QtWidgets.QLabel("UI Size Multiplier:")
        scale_label.setStyleSheet("color: #CCC;")
        scale_layout.addWidget(scale_label)
        
        self.scale_spinbox = QtWidgets.QDoubleSpinBox()
        self.scale_spinbox.setRange(0.5, 3.0)
        self.scale_spinbox.setSingleStep(0.1)
        self.scale_spinbox.setValue(config.get_setting("toolbar_scale", 1.0))
        self.scale_spinbox.setStyleSheet("""
            QDoubleSpinBox { 
                background-color: #4d4d4d; 
                color: #FFF; 
                border: 1px solid #666666; 
                border-radius: 4px; 
                padding: 4px; 
            }
        """)
        scale_layout.addWidget(self.scale_spinbox)
        scale_layout.addStretch()
        
        layout.addWidget(scale_group)

        # Channel Box helpers
        channel_box_group = QtWidgets.QGroupBox("Channel Box")
        channel_box_group.setStyleSheet("""
            QGroupBox {
                color: #AAA;
                font-weight: bold;
                border: 1px solid #5a5a5a;
                border-radius: 4px;
                margin-top: 1ex;
                padding: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
            QCheckBox {
                color: #DDD;
                font-size: 11px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
        """)
        channel_box_layout = QtWidgets.QVBoxLayout(channel_box_group)
        channel_box_layout.setContentsMargins(12, 16, 12, 12)

        self.channel_box_multi_selection_checkbox = QtWidgets.QCheckBox(
            "Channel Box Multi Selection Helper"
        )
        self.channel_box_multi_selection_checkbox.setToolTip(
            "Shows channels shared by all selected objects, so edits remain safe across the selection."
        )
        self.channel_box_multi_selection_checkbox.setChecked(
            bool(config.get_setting("channel_box_multi_selection_helper", False))
        )
        self.channel_box_multi_selection_checkbox.stateChanged.connect(
            self._on_channel_box_multi_selection_changed
        )
        channel_box_layout.addWidget(self.channel_box_multi_selection_checkbox)

        self.channel_box_key_filter_checkbox = QtWidgets.QCheckBox(
            "Show only selected Channel Box keys in the Timeline"
        )
        self.channel_box_key_filter_checkbox.setToolTip(
            "When Channel Box channels are selected, the Time Slider shows key ticks only for those channels."
        )
        self.channel_box_key_filter_checkbox.setChecked(
            bool(config.get_setting("timeline_channel_box_key_filter", False))
        )
        self.channel_box_key_filter_checkbox.stateChanged.connect(
            self._on_channel_box_key_filter_changed
        )
        channel_box_layout.addWidget(self.channel_box_key_filter_checkbox)
        layout.addWidget(channel_box_group)

        # Viewport roll gimbal
        viewport_group = QtWidgets.QGroupBox("Viewport")
        viewport_group.setStyleSheet("""
            QGroupBox {
                color: #AAA;
                font-weight: bold;
                border: 1px solid #5a5a5a;
                border-radius: 4px;
                margin-top: 1ex;
                padding: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
            QCheckBox {
                color: #DDD;
                font-size: 11px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
        """)
        viewport_layout = QtWidgets.QVBoxLayout(viewport_group)
        viewport_layout.setContentsMargins(12, 16, 12, 12)
        self.viewport_gimbal_checkbox = QtWidgets.QCheckBox(
            "Show viewport roll gimbal in the active viewport"
        )
        self.viewport_gimbal_checkbox.setToolTip(
            "Shows a small viewport control to roll the camera view when a character is upside down."
        )
        self.viewport_gimbal_checkbox.setChecked(
            bool(config.get_setting("viewport_roll_gimbal_enabled", False))
        )
        self.viewport_gimbal_checkbox.stateChanged.connect(
            self._on_viewport_gimbal_changed
        )
        viewport_layout.addWidget(self.viewport_gimbal_checkbox)

        self.tumble_around_selection_checkbox = QtWidgets.QCheckBox(
            "Tumble Around Selection"
        )
        self.tumble_around_selection_checkbox.setToolTip(
            "Automatically moves the active camera tumble pivot to the selected objects."
        )
        self.tumble_around_selection_checkbox.setChecked(
            bool(config.get_setting("tumble_around_selection_enabled", False))
        )
        self.tumble_around_selection_checkbox.stateChanged.connect(
            self._on_tumble_around_selection_changed
        )
        viewport_layout.addWidget(self.tumble_around_selection_checkbox)
        layout.addWidget(viewport_group)
        
        # Folders Setting
        folders_group = QtWidgets.QGroupBox("Folders & Paths")
        folders_group.setStyleSheet("""
            QGroupBox { 
                color: #AAA; 
                font-weight: bold; 
                border: 1px solid #5a5a5a; 
                border-radius: 4px; 
                margin-top: 1ex; 
                padding: 10px; 
            } 
            QGroupBox::title { 
                subcontrol-origin: margin; 
                left: 10px; 
                padding: 0 3px 0 3px; 
            }
        """)
        folders_layout = QtWidgets.QVBoxLayout(folders_group)
        
        backup_label = QtWidgets.QLabel("Animation Backup Folder:")
        backup_label.setStyleSheet("color: #CCC;")
        folders_layout.addWidget(backup_label)
        
        path_layout = QtWidgets.QHBoxLayout()
        self.backup_path_edit = QtWidgets.QLineEdit()
        self.backup_path_edit.setText(config.get_animation_backup_folder(create=False))
        self.backup_path_edit.setStyleSheet("""
            QLineEdit { 
                background-color: #4d4d4d; 
                color: #FFF; 
                border: 1px solid #666666; 
                border-radius: 4px; 
                padding: 4px; 
            }
        """)
        path_layout.addWidget(self.backup_path_edit)
        
        browse_btn = QtWidgets.QPushButton("Browse...")
        browse_btn.setStyleSheet("""
            QPushButton { 
                background-color: #4d4d4d; 
                color: #CCC; 
                border: 1px solid #666666; 
                border-radius: 4px; 
                padding: 4px 8px; 
            } 
            QPushButton:hover { 
                background-color: #3D3D3D; 
                color: #FFF; 
            }
        """)
        browse_btn.clicked.connect(self._browse_backup_folder)
        path_layout.addWidget(browse_btn)
        
        folders_layout.addLayout(path_layout)
        layout.addWidget(folders_group)
        
        layout.addStretch()
        if add_to_tabs:
            self.tab_widget.addTab(tab, "General / Folders")
        return tab
        
    def _browse_backup_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Animation Backup Folder")
        if folder:
            self.backup_path_edit.setText(folder)

    def _on_channel_box_key_filter_changed(self, state):
        checked_state = QtCore.Qt.Checked
        checked_value = checked_state.value if hasattr(checked_state, "value") else int(checked_state)
        enabled = int(state) == checked_value
        config.set_setting("timeline_channel_box_key_filter", enabled)
        try:
            from AnimKey.mods import timelineChannelFilter
            timelineChannelFilter.apply(enabled)
        except Exception:
            pass

    def _on_channel_box_multi_selection_changed(self, state):
        checked_state = QtCore.Qt.Checked
        checked_value = checked_state.value if hasattr(checked_state, "value") else int(checked_state)
        enabled = int(state) == checked_value
        config.set_setting("channel_box_multi_selection_helper", enabled)
        try:
            from AnimKey.mods import channelBoxMultiSelection
            channelBoxMultiSelection.apply(enabled)
        except Exception:
            pass

    def _on_viewport_gimbal_changed(self, state):
        checked_state = QtCore.Qt.Checked
        checked_value = checked_state.value if hasattr(checked_state, "value") else int(checked_state)
        enabled = int(state) == checked_value
        config.set_setting("viewport_roll_gimbal_enabled", enabled)
        try:
            from AnimKey.mods import viewportGimbal
            viewportGimbal.apply(enabled)
        except Exception:
            pass

    def _on_tumble_around_selection_changed(self, state):
        checked_state = QtCore.Qt.Checked
        checked_value = checked_state.value if hasattr(checked_state, "value") else int(checked_state)
        enabled = int(state) == checked_value
        config.set_setting("tumble_around_selection_enabled", enabled)
        try:
            from AnimKey.mods import tumbleAroundSelection
            tumbleAroundSelection.apply(enabled)
        except Exception:
            pass

    def _create_shortcuts_tab(self, add_to_tabs=True):
        """Create the shortcuts configuration tab"""
        
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Description
        desc = QtWidgets.QLabel(
            "Configure keyboard shortcuts for AnimKey tools.\n"
            "Click on a field and press the desired key combination. "
            "Shortcuts are saved and activated automatically."
        )
        desc.setStyleSheet("color: #AAA; font-size: 11px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        # Separator
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("background-color: #5a5a5a;")
        layout.addWidget(line)
        
        # Scroll area for shortcuts
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("background-color: transparent;")
        
        scroll_content = QtWidgets.QWidget()
        scroll_content.setStyleSheet("background-color: transparent;")
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(8)
        
        # Group: Tool Buttons
        group_buttons = self._create_shortcut_group("Tool Buttons", [
            ("ISO", "Isolate Selection"),
            ("ALN", "Align Objects"),
            ("OPP", "Select Opposite"),
            ("TRL", "Trail"),
            ("OFF", "Animation Offset"),
            ("RST", "Reset Values"),
            ("TMP", "Temp Control"),
            ("HIR", "Select Hierarchy"),
            ("SEL", "Selector"),
            ("MIR", "Mirror"),
            ("C", "Copy Animation"),
            ("LKN", "Link Objects"),
            ("CAM", "Follow Cam"),
            ("WS", "World Space Copy"),
            ("PIV", "TEMP"),
            ("RUL", "Ruler"),
            ("RTM", "Retimer"),
        ])
        scroll_layout.addWidget(group_buttons)
        
        # Group: Tangent Buttons
        group_tangents = self._create_shortcut_group("Tangent Buttons", [
            ("PLT", "Plateau"),
            ("STP", "Step"),
            ("FLT", "Flat"),
            ("LIN", "Linear"),
            ("CLP", "Clamped"),
            ("SPL", "Spline"),
            ("AUT", "Auto"),
        ])
        scroll_layout.addWidget(group_tangents)
        
        group_extra = self._create_shortcut_group("Extra Tools", [
            ("RBK", "ReBlock"),
            ("GMB", "Gimbal Fixer"),
            ("SWT", "Switcher"),
            ("BAK", "Bake Animation"),
            ("SETS", "Selection Sets"),
            ("ACR", "Anim Crash"),
            ("BTNS", "Flash Buttons"),
        ])
        scroll_layout.addWidget(group_extra)
        
        # Group: Align Submenu
        group_align = self._create_shortcut_group("Align (Submenu)", [
            ("Align Position", "Align Position"),
            ("Align Orientation", "Align Orientation"),
            ("Align Scale", "Align Scale"),
        ])
        scroll_layout.addWidget(group_align)
        
        # Group: Reset Values Submenu
        group_reset = self._create_shortcut_group("Reset Values (Submenu)", [
            ("Set Default Values", "Set Default Values"),
            ("Restore Default Values", "Restore Default Values"),
            ("Clear All Saved Data", "Clear All Saved Data"),
        ])
        scroll_layout.addWidget(group_reset)
        
        # Group: Select Opposite Submenu
        group_opp = self._create_shortcut_group("Select Opposite (Submenu)", [
            ("Add Select Opposite", "Add to Selection"),
        ])
        scroll_layout.addWidget(group_opp)
        
        # Group: Mirror Submenu
        group_mirror = self._create_shortcut_group("Mirror (Submenu)", [
            ("All Mirror", "All Mirror (Swap)"),
            ("Mirror to Left", "Mirror to Left"),
            ("Mirror to Right", "Mirror to Right"),
            ("Toggle Auto Mirror", "Toggle Auto Mirror"),
            ("Snapshot Mirror Settings", "Snapshot"),
            ("Delete Snapshot", "Delete Snapshot"),
            ("Add Mirror Invert Exception", "Add Invert Exception"),
            ("Add Mirror Keep Exception", "Add Keep Exception"),
            ("Remove Mirror Exception", "Remove Exception"),
            ("Show Mirror Exceptions", "Show Exceptions"),
            ("Clear All Mirror Exceptions", "Clear All Exceptions"),
        ])
        scroll_layout.addWidget(group_mirror)
        
        # Group: Copy Animation Submenu
        group_copy = self._create_shortcut_group("Copy Animation (Submenu)", [
            ("Copy Animation", "Copy Animation"),
            ("Paste Animation", "Paste Animation"),
            ("Paste Insert", "Paste Insert"),
            ("Paste Opposite", "Paste Opposite"),
            ("Copy Pose", "Copy Pose"),
            ("Paste Pose", "Paste Pose"),
        ])
        scroll_layout.addWidget(group_copy)
        
        # Group: Follow Cam Submenu
        group_cam = self._create_shortcut_group("Follow Cam (Submenu)", [
            ("Follow Cam Translation & Rotation", "Translation & Rotation"),
            ("Follow Cam Translation Only", "Translation Only"),
            ("Follow Cam Rotation Only", "Rotation Only"),
            ("Remove Follow Cam", "Remove Follow Cam"),
        ])
        scroll_layout.addWidget(group_cam)
        
        # Group: Link Objects Submenu
        group_link = self._create_shortcut_group("Link Objects (Submenu)", [
            ("Copy Link Frame", "Copy Link Frame"),
            ("Copy Link Playback Range", "Copy Playback Range"),
            ("Paste Link Frame", "Paste Link Frame"),
            ("Paste Link Next Frame", "Paste Next Frame"),
            ("Paste Link All Keys", "Paste All Keys"),
            ("Toggle Auto Link", "Toggle Auto Link"),
        ])
        scroll_layout.addWidget(group_link)
        
        # Group: Worldspace Submenu
        group_ws = self._create_shortcut_group("Worldspace (Submenu)", [
            ("Copy Worldspace All Animation", "Copy All Animation"),
            ("Copy Worldspace Selected Range", "Copy Selected Range"),
            ("Copy Worldspace Current Frame", "Copy Current Frame"),
            ("Paste Worldspace Animation", "Paste Animation"),
            ("Paste Worldspace Frame", "Paste Frame"),
            ("Toggle Auto Worldspace", "Toggle Auto Worldspace"),
        ])
        scroll_layout.addWidget(group_ws)
        
        # Group: Slider Functions
        group_sliders = self._create_shortcut_group("Slider Functions", [
            ("Tweener", "Tweener Slider"),
            ("Push/Pull", "Push/Pull Slider"),
            ("Blend to Neighbors", "Blend to Neighbors"),
            ("Blend to Ease", "Blend to Ease"),
            ("Blend to Undo", "Blend to Undo"),
            ("Mirror Blend", "Mirror Blend Slider"),
        ])
        scroll_layout.addWidget(group_sliders)
        
        # Group: Keyframe Controls
        group_keys = self._create_shortcut_group("Keyframe Controls", [
            ("Increase Values", "Increase Values (+)"),
            ("Decrease Values", "Decrease Values (-)"),
            ("Move Keys Left", "Move Keys Left (<)"),
            ("Move Keys Right", "Move Keys Right (>)"),
            ("Clear Keys", "Clear Selected Keys"),
            ("Select All Animation", "Select All Animation"),
        ])
        scroll_layout.addWidget(group_keys)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # Reset shortcuts button
        btn_reset = QtWidgets.QPushButton("Reset All Shortcuts")
        btn_reset.setCursor(QtCore.Qt.PointingHandCursor)
        btn_reset.setStyleSheet("""
            QPushButton {
                background-color: #4d4d4d;
                color: #AAA;
                border: 1px solid #666666;
                border-radius: 4px;
                padding: 6px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3d2d2d;
                color: #FFF;
                border-color: #ff6b6b;
            }
        """)
        btn_reset.clicked.connect(self._reset_shortcuts)
        layout.addWidget(btn_reset)
        
        if add_to_tabs:
            self.tab_widget.addTab(tab, "Shortcuts")
        return tab
    
    def _create_shortcut_group(self, title, items):
        """Create a group of shortcut settings"""
        
        group = QtWidgets.QGroupBox(title)
        group.setStyleSheet("""
            QGroupBox {
                color: #FFF;
                font-weight: bold;
                border: 1px solid #5a5a5a;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #3498DB;
            }
        """)
        
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(8)
        
        for i, (key, label) in enumerate(items):
            # Label
            lbl = QtWidgets.QLabel(label)
            lbl.setStyleSheet("color: #AAA; font-weight: normal;")
            layout.addWidget(lbl, i, 0)
            
            # Shortcut edit
            shortcut_edit = ShortcutEditWidget()
            shortcut_edit.setFixedWidth(150)
            shortcut_edit.setShortcut(self._shortcuts.get(key, ""))
            shortcut_edit.shortcutChanged.connect(
                lambda s, k=key: self._on_shortcut_changed(k, s)
            )
            layout.addWidget(shortcut_edit, i, 1)
            
            # Clear button
            btn_clear = QtWidgets.QPushButton("✕")
            btn_clear.setFixedSize(24, 24)
            btn_clear.setCursor(QtCore.Qt.PointingHandCursor)
            btn_clear.setStyleSheet("""
                QPushButton {
                    color: #666;
                    background: transparent;
                    border: none;
                    font-size: 14px;
                }
                QPushButton:hover {
                    color: #ff6b6b;
                }
            """)
            btn_clear.clicked.connect(lambda checked=False, w=shortcut_edit: w.clearShortcut())
            layout.addWidget(btn_clear, i, 2)
            
            # Pick button
            btn_pick = QtWidgets.QPushButton("🖱️")
            btn_pick.setFixedSize(24, 24)
            btn_pick.setCursor(QtCore.Qt.PointingHandCursor)
            btn_pick.setToolTip("Pick action from toolbar")
            btn_pick.setStyleSheet("""
                QPushButton {
                    color: #666;
                    background: transparent;
                    border: none;
                    font-size: 14px;
                }
                QPushButton:hover {
                    color: #3498DB;
                }
            """)
            btn_pick.clicked.connect(lambda checked=False, w=shortcut_edit: self._on_pick_clicked(w))
            layout.addWidget(btn_pick, i, 3)
            
            self._shortcut_widgets[key] = shortcut_edit
        
        return group
    
    def _create_workspace_tab(self, add_to_tabs=True):
        """Create the workspace customization tab"""
        
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Load current workspace
        self._workspace = config.load_workspace()
        self._workspace_checkboxes = {"buttons": {}, "sliders": {}, "other": {}}
        
        # Info label
        info_label = QtWidgets.QLabel(
            "Configure which elements appear in the toolbar. Uncheck to hide."
        )
        info_label.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(info_label)
        
        # Scroll area for all checkboxes
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)
        
        scroll_content = QtWidgets.QWidget()
        scroll_content.setStyleSheet("background-color: transparent;")
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(12)
        
        # === BUTTONS SECTION ===
        buttons_group = QtWidgets.QGroupBox("Toolbar Buttons")
        buttons_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                color: #FFF;
                border: 1px solid #5a5a5a;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #3498DB;
            }
        """)
        buttons_layout = QtWidgets.QGridLayout(buttons_group)
        buttons_layout.setSpacing(8)
        
        # ── Button labels (exact keys from toolbar.py) ────────────────────────
        button_labels = {
            # Group 1
            "ISO": "Isolate",      "ALN": "Align",        "OPP": "Opposite",   "TRL": "Trail",
            # Group 2
            "RST": "Reset Values", "OFF": "Anim Offset",  "HIR": "Hierarchy",  "MIR": "Mirror",
            # Group 3
            "C":   "Copy Anim",    "LKN": "Link Objects", "CAM": "Follow Cam",
            "WS":  "Worldspace",   "PIV": "TEMP",         "RUL": "Micro Move",
            # Group 4 – Tangents
            "PLT": "Plateau",      "STP": "Step",         "FLT": "Flat",
            "LIN": "Linear",       "CLP": "Clamped",      "SPL": "Spline",     "AUT": "Auto",
            # Group 5 – Extra
            "GMB": "Gimbal Fix",   "SWT": "Switcher",     "BAK": "Bake Anim",
            "RTM": "Retimer",      "BTNS": "Flash Btns",  "SETS": "Sel Sets",
            "ACL": "Anim Cleaner", "ACR": "Anim Crash",
        }

        row, col = 0, 0
        for btn_key, btn_label in button_labels.items():
            cb = QtWidgets.QCheckBox(f"{btn_key}  {btn_label}")
            cb.setChecked(self._workspace["buttons"].get(btn_key, True))
            cb.setStyleSheet("""
                QCheckBox { color: #DDD; font-size: 11px; }
                QCheckBox::indicator { width: 13px; height: 13px; }
            """)
            cb.stateChanged.connect(
                lambda state, k=btn_key: self._on_workspace_button_toggle(k, bool(state))
            )
            buttons_layout.addWidget(cb, row, col)
            self._workspace_checkboxes["buttons"][btn_key] = cb
            col += 1
            if col >= 4:
                col = 0
                row += 1

        scroll_layout.addWidget(buttons_group)

        # === SLIDERS SECTION =====================================================
        # Each entry = ONE checkbox that hides/shows the entire dropdown+slider unit.
        sliders_group = QtWidgets.QGroupBox("Slider Sections")
        sliders_group.setStyleSheet(buttons_group.styleSheet())
        sl_layout = QtWidgets.QVBoxLayout(sliders_group)
        sl_layout.setSpacing(8)

        slider_sections = [
            ("tween_slider",      "Tweener / Blend Slider  —  Tweener, Blend to Neighbors, Push Pull…"),
            ("curve_slider",      "Curve Tools Slider  —  Smooth, Wave, Scale, Ease In/Out, Noise…"),
            ("mirror_slider",     "Mirror Blend Slider  —  Blend between current pose and mirrored pose"),
            ("keyframe_controls", "Keyframe Controls  —  Increase/Decrease, Move Keys ◀▶, Clear ×"),
        ]

        for section_key, section_label in slider_sections:
            cb = QtWidgets.QCheckBox(section_label)
            cb.setChecked(self._workspace.get("sliders", {}).get(section_key, True))
            cb.setStyleSheet("""
                QCheckBox { color: #DDD; font-size: 11px; }
                QCheckBox::indicator { width: 13px; height: 13px; }
            """)
            cb.stateChanged.connect(
                lambda state, k=section_key: self._on_workspace_slider_toggle(k, bool(state))
            )
            sl_layout.addWidget(cb)
            self._workspace_checkboxes["sliders"][section_key] = cb

        scroll_layout.addWidget(sliders_group)

        # === INDIVIDUAL ELEMENTS SECTION =====================================
        # Things that don't fit neatly into a group or slider section.
        elements_group = QtWidgets.QGroupBox("Individual Elements")
        elements_group.setStyleSheet(buttons_group.styleSheet())
        el_layout = QtWidgets.QVBoxLayout(elements_group)
        el_layout.setSpacing(6)

        individual_buttons = {
            "SELECTOR": "Selector  —  Object count badge + selector window",
        }
        for btn_key, btn_label in individual_buttons.items():
            cb = QtWidgets.QCheckBox(btn_label)
            cb.setChecked(self._workspace["buttons"].get(btn_key, True))
            cb.setStyleSheet("""
                QCheckBox { color: #DDD; font-size: 11px; }
                QCheckBox::indicator { width: 13px; height: 13px; }
            """)
            cb.stateChanged.connect(
                lambda state, k=btn_key: self._on_workspace_button_toggle(k, bool(state))
            )
            el_layout.addWidget(cb)
            self._workspace_checkboxes["buttons"][btn_key] = cb

        scroll_layout.addWidget(elements_group)
        scroll_layout.addStretch()
        
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # === PRESET BUTTONS ===
        preset_layout = QtWidgets.QHBoxLayout()
        preset_layout.setSpacing(8)
        
        btn_export = QtWidgets.QPushButton("Export Preset")
        btn_export.setFixedHeight(30)
        btn_export.setCursor(QtCore.Qt.PointingHandCursor)
        btn_export.clicked.connect(self._export_workspace_preset)
        btn_export.setStyleSheet("""
            QPushButton {
                background-color: #4d4d4d;
                color: #AAA;
                border: 1px solid #666666;
                border-radius: 4px;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background-color: #3498DB;
                color: #FFF;
                border-color: #3498DB;
            }
        """)
        preset_layout.addWidget(btn_export)
        
        btn_import = QtWidgets.QPushButton("Import Preset")
        btn_import.setFixedHeight(30)
        btn_import.setCursor(QtCore.Qt.PointingHandCursor)
        btn_import.clicked.connect(self._import_workspace_preset)
        btn_import.setStyleSheet(btn_export.styleSheet())
        preset_layout.addWidget(btn_import)
        
        preset_layout.addStretch()
        
        btn_reset = QtWidgets.QPushButton("Reset to Default")
        btn_reset.setFixedHeight(30)
        btn_reset.setCursor(QtCore.Qt.PointingHandCursor)
        btn_reset.clicked.connect(self._reset_workspace)
        btn_reset.setStyleSheet("""
            QPushButton {
                background-color: #3d2d2d;
                color: #ff6b6b;
                border: 1px solid #5a3a3a;
                border-radius: 4px;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background-color: #4d3d3d;
                border-color: #ff6b6b;
                color: #FFF;
            }
        """)
        preset_layout.addWidget(btn_reset)
        
        layout.addLayout(preset_layout)
        
        if add_to_tabs:
            self.tab_widget.addTab(tab, "Workspace")
        return tab
    
    def _on_workspace_button_toggle(self, key, visible):
        """Live-update the toolbar when a workspace button checkbox is toggled."""
        try:
            from AnimKey.core.toolbar import AnimKeyToolbar
            toolbar = AnimKeyToolbar.get_instance()
            if toolbar is not None:
                toolbar.set_button_visible(key, visible)
        except Exception:
            pass

    def _on_workspace_slider_toggle(self, section_key, visible):
        """Live-update the toolbar when a slider-section checkbox is toggled."""
        try:
            from AnimKey.core.toolbar import AnimKeyToolbar
            toolbar = AnimKeyToolbar.get_instance()
            if toolbar is not None:
                toolbar.set_section_visible(section_key, visible)
        except Exception:
            pass

    def _save_workspace_from_ui(self):
        """Collect workspace settings from UI checkboxes"""
        workspace = {"buttons": {}, "sliders": {}, "other": {}}
        
        for category, checkboxes in self._workspace_checkboxes.items():
            for key, cb in checkboxes.items():
                workspace[category][key] = cb.isChecked()
        
        # Also save the current layout order from the toolbar
        try:
            from AnimKey.core.toolbar import AnimKeyToolbar
            toolbar = AnimKeyToolbar.get_instance()
            if toolbar is not None and hasattr(toolbar, 'get_layout_order'):
                workspace["order"] = toolbar.get_layout_order()
        except Exception:
            pass
        
        return workspace
    
    def _export_workspace_preset(self):
        """Export workspace preset to file"""
        # First save current UI state
        workspace = self._save_workspace_from_ui()
        config.save_workspace(workspace)
        
        # Open file dialog
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Workspace Preset", "", "JSON Files (*.json)"
        )
        
        if filepath:
            if not filepath.endswith('.json'):
                filepath += '.json'
            if config.export_workspace_preset(filepath):
                QtWidgets.QMessageBox.information(
                    self, "Export Successful", 
                    f"Workspace preset exported to:\n{filepath}"
                )
    
    def _import_workspace_preset(self):
        """Import workspace preset from file"""
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Workspace Preset", "", "JSON Files (*.json)"
        )
        
        if filepath:
            if config.import_workspace_preset(filepath):
                # Reload UI
                self._workspace = config.load_workspace()
                for category, checkboxes in self._workspace_checkboxes.items():
                    for key, cb in checkboxes.items():
                        cb.setChecked(self._workspace[category].get(key, True))
                
                QtWidgets.QMessageBox.information(
                    self, "Import Successful", 
                    "Workspace preset imported successfully.\nRestart toolbar to see changes."
                )
    
    def _reset_workspace(self):
        """Reset workspace to defaults"""
        result = QtWidgets.QMessageBox.question(
            self, "Reset Workspace",
            "Reset all workspace settings to default?\nAll elements will be visible.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        
        if result == QtWidgets.QMessageBox.Yes:
            config.reset_workspace()
            self._workspace = config.load_workspace()
            for category, checkboxes in self._workspace_checkboxes.items():
                for key, cb in checkboxes.items():
                    cb.setChecked(True)
            # Live-update: restore all buttons AND slider sections
            try:
                from AnimKey.core.toolbar import AnimKeyToolbar
                toolbar = AnimKeyToolbar.get_instance()
                if toolbar is not None:
                    for key in self._workspace_checkboxes.get("buttons", {}):
                        toolbar.set_button_visible(key, True)
                    for section_key in self._workspace_checkboxes.get("sliders", {}):
                        toolbar.set_section_visible(section_key, True)
            except Exception:
                pass
    
    def _create_exit_tab(self, add_to_tabs=True):
        """Create the exit/maintenance tab"""
        
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Refresh section
        refresh_group = QtWidgets.QGroupBox("Refresh AnimKey")
        refresh_group.setStyleSheet("""
            QGroupBox {
                color: #FFF;
                font-weight: bold;
                border: 1px solid #5a5a5a;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #3498DB;
            }
        """)
        
        refresh_layout = QtWidgets.QVBoxLayout(refresh_group)
        refresh_layout.setContentsMargins(12, 16, 12, 12)
        
        refresh_desc = QtWidgets.QLabel(
            "Clear AnimKey's cache and reload all modules.\n"
            "Use this if you experience issues or after updating AnimKey."
        )
        refresh_desc.setStyleSheet("color: #AAA; font-weight: normal;")
        refresh_desc.setWordWrap(True)
        refresh_layout.addWidget(refresh_desc)
        
        btn_refresh = QtWidgets.QPushButton("🔄 Refresh AnimKey")
        btn_refresh.setFixedHeight(36)
        btn_refresh.setCursor(QtCore.Qt.PointingHandCursor)
        btn_refresh.setStyleSheet("""
            QPushButton {
                color: #FFF;
                background-color: #4d4d4d;
                border: 1px solid #666666;
                border-radius: 6px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3498DB;
                border-color: #3498DB;
            }
        """)
        btn_refresh.clicked.connect(self._refresh_animkey)
        refresh_layout.addWidget(btn_refresh)
        
        layout.addWidget(refresh_group)
        
        # Uninstall section
        uninstall_group = QtWidgets.QGroupBox("Uninstall AnimKey")
        uninstall_group.setStyleSheet("""
            QGroupBox {
                color: #ff6b6b;
                font-weight: bold;
                border: 1px solid #5a3a3a;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        
        uninstall_layout = QtWidgets.QVBoxLayout(uninstall_group)
        uninstall_layout.setContentsMargins(12, 16, 12, 12)
        
        uninstall_desc = QtWidgets.QLabel(
            "⚠️ WARNING: This will completely remove AnimKey from Maya.\n"
            "All user data, shortcuts, and preferences will be deleted.\n"
            "This action cannot be undone."
        )
        uninstall_desc.setStyleSheet("color: #ff6b6b; font-weight: normal;")
        uninstall_desc.setWordWrap(True)
        uninstall_layout.addWidget(uninstall_desc)
        
        btn_uninstall = QtWidgets.QPushButton("🗑️ Uninstall AnimKey")
        btn_uninstall.setFixedHeight(36)
        btn_uninstall.setCursor(QtCore.Qt.PointingHandCursor)
        btn_uninstall.setStyleSheet("""
            QPushButton {
                color: white;
                background-color: #3d2d2d;
                border: 1px solid #5a3a3a;
                border-radius: 6px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #bf616a;
                border-color: #bf616a;
            }
        """)
        btn_uninstall.clicked.connect(self._uninstall_animkey)
        uninstall_layout.addWidget(btn_uninstall)
        
        layout.addWidget(uninstall_group)
        
        layout.addStretch()
        
        if add_to_tabs:
            self.tab_widget.addTab(tab, "Exit")
        return tab

    def _create_update_tab(self, add_to_tabs=True):
        """Create the stable GitHub Releases update tab."""
        from AnimKey.core.update_ui import UpdateTab

        tab = UpdateTab()
        if add_to_tabs:
            self.tab_widget.addTab(tab, "Update")
        return tab
    
    def _apply_theme(self):
        """Apply theme to the window.
        
        NOTE: Do NOT set background-color on the root widget here —
        that would override WA_TranslucentBackground and make the window opaque.
        The container QFrame already has the dark background via its own
        stylesheet, which is all that is needed.
        """
        # Only style child widgets, never the root QWidget itself
        self.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background-color: #3a3a3a;
            }
            QTabBar::tab {
                color: #888;
                background-color: #363636;
                border: 1px solid #5a5a5a;
                border-bottom: none;
                padding: 8px 20px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                color: #FFF;
                background-color: #3a3a3a;
                border-bottom: 1px solid #3a3a3a;
            }
            QTabBar::tab:hover:!selected {
                background-color: #4d4d4d;
            }
            QScrollArea {
                background-color: transparent;
            }
            QScrollBar:vertical {
                background-color: #444444;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background-color: #666666;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #666;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
    
    def enterEvent(self, event):
        self._animate_opacity(self._hover_opacity)
    
    def leaveEvent(self, event):
        self._animate_opacity(self._base_opacity)
    
    def _animate_opacity(self, val):
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(150)
        anim.setEndValue(val)
        anim.start(QtCore.QPropertyAnimation.DeleteWhenStopped)
        self._anim = anim

    def closeEvent(self, event):
        """Clean up global reference when the window is closed."""
        if getattr(self, "_update_install_in_progress", False):
            event.ignore()
            return
        global _settings_window
        self._clear_active_picker()
        _settings_window = None
        super().closeEvent(event)
    
    def _on_shortcut_changed(self, key, shortcut):
        """Handle shortcut change"""
        self._shortcuts[key] = shortcut
        
        # Save and refresh hotkeys automatically
        save_shortcuts(self._shortcuts)
        try:
            from AnimKey.mods import hotkeysMod
            hotkeysMod.refresh_hotkeys()
        except:
            pass
    

    
    def _reset_shortcuts(self):
        """Reset all shortcuts to defaults"""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Reset Shortcuts",
            "Are you sure you want to reset all shortcuts to their defaults?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            self._shortcuts = DEFAULT_SHORTCUTS.copy()
            
            # Update all widgets
            for key, widget in self._shortcut_widgets.items():
                widget.setShortcut(self._shortcuts.get(key, ""))
            
            # Save and refresh hotkeys
            save_shortcuts(self._shortcuts)
            try:
                from AnimKey.mods import hotkeysMod
                hotkeysMod.refresh_hotkeys()
            except Exception as e:
                cmds.warning(f"AnimKey: Could not refresh hotkeys: {e}")
            
            cmds.inViewMessage(
                amg="<span style='color:#a3be8c'>Shortcuts reset to defaults</span>",
                pos='topCenter',
                fade=True,
                fadeStayTime=1500
            )
    
    def _save_and_close(self):
        """Save settings and close the window"""
        # Save general settings
        if hasattr(self, 'scale_spinbox'):
            config.set_setting("toolbar_scale", self.scale_spinbox.value())
        if hasattr(self, 'backup_path_edit'):
            config.set_setting("animation_backup_folder", self.backup_path_edit.text())
        if hasattr(self, 'channel_box_key_filter_checkbox'):
            config.set_setting(
                "timeline_channel_box_key_filter",
                self.channel_box_key_filter_checkbox.isChecked()
            )
            try:
                from AnimKey.mods import timelineChannelFilter
                timelineChannelFilter.apply(self.channel_box_key_filter_checkbox.isChecked())
            except Exception:
                pass
        if hasattr(self, 'channel_box_multi_selection_checkbox'):
            config.set_setting(
                "channel_box_multi_selection_helper",
                self.channel_box_multi_selection_checkbox.isChecked()
            )
            try:
                from AnimKey.mods import channelBoxMultiSelection
                channelBoxMultiSelection.apply(self.channel_box_multi_selection_checkbox.isChecked())
            except Exception:
                pass
        if hasattr(self, 'viewport_gimbal_checkbox'):
            config.set_setting(
                "viewport_roll_gimbal_enabled",
                self.viewport_gimbal_checkbox.isChecked()
            )
            try:
                from AnimKey.mods import viewportGimbal
                viewportGimbal.apply(self.viewport_gimbal_checkbox.isChecked())
            except Exception:
                pass
        if hasattr(self, 'tumble_around_selection_checkbox'):
            config.set_setting(
                "tumble_around_selection_enabled",
                self.tumble_around_selection_checkbox.isChecked()
            )
            try:
                from AnimKey.mods import tumbleAroundSelection
                tumbleAroundSelection.apply(self.tumble_around_selection_checkbox.isChecked())
            except Exception:
                pass
            
        # Save workspace settings
        if hasattr(self, '_workspace_checkboxes'):
            workspace = self._save_workspace_from_ui()
            config.save_workspace(workspace)
        
        # Save shortcuts
        save_shortcuts(self._shortcuts)
        
        # Close settings window
        self.close()
        
        # Perform Refresh logic automatically after save
        # Clear AnimKey modules from sys.modules to ensure a clean reload
        modules_to_remove = [
            mod for mod in sys.modules.keys()
            if mod.startswith('AnimKey')
        ]
        
        for mod in modules_to_remove:
            try:
                del sys.modules[mod]
            except:
                pass
        
        # Reload AnimKey and show success message
        cmds.evalDeferred('''
import AnimKey
AnimKey.reload()
import maya.cmds as cmds
cmds.inViewMessage(
    amg="<span style='color:#a3be8c'>Settings saved and AnimKey refreshed!</span>",
    pos='topCenter',
    fade=True,
    fadeStayTime=2000
)
''')
    
    def _refresh_animkey(self):
        """Refresh AnimKey by clearing cache and reloading modules"""
        self.close()
        
        # Clear AnimKey modules from sys.modules
        modules_to_remove = [
            mod for mod in sys.modules.keys()
            if mod.startswith('AnimKey')
        ]
        
        for mod in modules_to_remove:
            try:
                del sys.modules[mod]
            except:
                pass
        
        # Reload AnimKey
        cmds.evalDeferred('''
import AnimKey
AnimKey.reload()
import maya.cmds as cmds
cmds.inViewMessage(
    amg="<span style='color:#a3be8c'>AnimKey refreshed successfully</span>",
    pos='topCenter',
    fade=True,
    fadeStayTime=2000
)
''')
    
    def _uninstall_animkey(self):
        """Uninstall AnimKey completely."""
        user_folder = config.get_user_folder_path()
        choice = ask_uninstall_choice(self, user_data_exists=os.path.exists(user_folder))
        if choice is None:
            return
        self._perform_uninstall(delete_user_data=(choice == AnimKeyUninstallDialog.DELETE_ALL))
    
    def _perform_uninstall(self, delete_user_data=False):
        """Actually perform the uninstall"""
        import stat
        unload_entry_plugin = None

        def _on_rm_error(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        try:
            from AnimKey.mods import uiMod
            uiMod.cleanup_animkey_runtime(except_widget=self, full=True)
            unload_entry_plugin = uiMod.unload_animkey_entry_plugin
        except Exception:
            pass

        self.close()
        self.deleteLater()
        
        try:
            # 1. Close the toolbar
            from AnimKey.core.toolbar import WORKSPACE_NAME
            if cmds.workspaceControl(WORKSPACE_NAME, query=True, exists=True):
                cmds.deleteUI(WORKSPACE_NAME, control=True)
            
            # 2. Get paths before removing modules
            user_folder = config.get_user_folder_path()
            maya_app_dir = cmds.internalVar(userAppDir=True)
            maya_scripts_dir = cmds.internalVar(userScriptDir=True)
            
            # AnimKey can be in either location
            animkey_path_new = os.path.join(maya_app_dir, "AnimKey")
            animkey_path_old = os.path.join(maya_scripts_dir, "AnimKey")
            plugin_path = os.path.join(maya_app_dir, "plug-ins", "AnimKey_plugin.py")
            user_setup_path = os.path.join(maya_scripts_dir, "userSetup.py")
            
            # 3. Clear AnimKey modules from sys.modules FIRST
            modules_to_remove = [
                mod for mod in list(sys.modules.keys())
                if 'AnimKey' in mod
            ]
            for mod in modules_to_remove:
                try:
                    del sys.modules[mod]
                except:
                    pass
            
            # 4. Remove AnimKey folders
            if os.path.exists(animkey_path_new):
                try:
                    shutil.rmtree(animkey_path_new, onerror=_on_rm_error)
                    print(f"✓ Removed: {animkey_path_new}")
                except Exception as e:
                    print(f"✗ Could not remove {animkey_path_new}: {e}")
            
            if os.path.exists(animkey_path_old):
                try:
                    shutil.rmtree(animkey_path_old, onerror=_on_rm_error)
                    print(f"✓ Removed: {animkey_path_old}")
                except Exception as e:
                    print(f"✗ Could not remove {animkey_path_old}: {e}")
            
            # 5. Remove plugin
            if os.path.exists(plugin_path):
                try:
                    os.remove(plugin_path)
                    print(f"✓ Removed plugin: {plugin_path}")
                except:
                    pass
            
            # 6. Clean userSetup.py
            if os.path.exists(user_setup_path):
                try:
                    with open(user_setup_path, 'r') as f:
                        content = f.read()
                    
                    if 'AnimKey' in content or '_animkey_' in content:
                        lines = content.split('\n')
                        new_lines = []
                        skip_block = False
                        
                        for line in lines:
                            if '# AnimKey Auto-start' in line:
                                skip_block = True
                                continue
                            if 'def _animkey_deferred_startup' in line:
                                skip_block = True
                                continue
                            
                            if skip_block:
                                if 'AnimKey' in line or '_animkey_' in line:
                                    continue
                                if line.startswith(' ') or line.startswith('\t'):
                                    continue
                                if line.strip().startswith('try:') or line.strip().startswith('except:') or line.strip() == 'pass':
                                    continue
                                if line.strip() == '':
                                    continue
                                skip_block = False
                            
                            if not skip_block and 'AnimKey' not in line and '_animkey_' not in line:
                                new_lines.append(line)
                        
                        with open(user_setup_path, 'w') as f:
                            f.write('\n'.join(new_lines))
                        print("✓ Cleaned userSetup.py")
                except:
                    pass
            
            # 7. User data was decided in the single uninstall dialog.
            if delete_user_data and os.path.exists(user_folder):
                try:
                    shutil.rmtree(user_folder, onerror=_on_rm_error)
                    print(f"Removed user data: {user_folder}")
                except Exception as e:
                    cmds.warning(f"Could not delete user data folder: {e}")
            elif os.path.exists(user_folder):
                print(f"Kept user data: {user_folder}")
            
            # 8. Show success message
            cmds.inViewMessage(
                amg="<span style='color:#a3be8c'>AnimKey has been uninstalled successfully.</span>",
                pos='midCenter',
                fade=True,
                fadeStayTime=3000
            )
            if unload_entry_plugin is not None:
                unload_entry_plugin()
            
        except Exception as e:
            cmds.warning(f"Error during uninstall: {e}")
            cmds.inViewMessage(
                amg="<span style='color:#ff6b6b'>AnimKey uninstall hit an error.</span><br>"
                    "<span style='color:#ebcb8b'>Check the Script Editor for details.</span>",
                pos='midCenter',
                fade=True,
                fadeStayTime=5000
            )

# ═══════════════════════════════════════════════════════════════════════════════
#                           PUBLIC FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

# Global reference — keeps the window alive while open
_settings_window = None


def show_settings():
    """Show the settings window (non-blocking, like the Retimer window)."""
    global _settings_window

    # Reuse the existing instance so clicking Settings again feels instant.
    if _settings_window is not None:
        try:
            _settings_window.show()
            _settings_window.raise_()
            _settings_window.activateWindow()
            return _settings_window
        except Exception:
            _settings_window = None

    _settings_window = SettingsWindow()
    _settings_window.show()
    _settings_window.raise_()
    _settings_window.activateWindow()
    return _settings_window


def get_shortcut(action_name):
    """Get the shortcut for a specific action"""
    shortcuts = load_shortcuts()
    return shortcuts.get(action_name, "")
