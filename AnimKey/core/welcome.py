"""
    AnimKey Welcome Window
    
    A beautiful welcome screen that appears on first installation.
    Supports multiple languages (English and Spanish).
"""

import os

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets, execute_qt

QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QPushButton = QtWidgets.QPushButton
QComboBox = QtWidgets.QComboBox
QFrame = QtWidgets.QFrame
QSpacerItem = QtWidgets.QSpacerItem
QSizePolicy = QtWidgets.QSizePolicy
QGraphicsDropShadowEffect = QtWidgets.QGraphicsDropShadowEffect
Qt = QtCore.Qt
QPropertyAnimation = QtCore.QPropertyAnimation
QEasingCurve = QtCore.QEasingCurve
QSize = QtCore.QSize
QColor = QtGui.QColor
QPainter = QtGui.QPainter
QPixmap = QtGui.QPixmap
QFont = QtGui.QFont
QLinearGradient = QtGui.QLinearGradient
QPainterPath = QtGui.QPainterPath
QBrush = QtGui.QBrush
QPen = QtGui.QPen

try:
    import maya.cmds as cmds
    import maya.OpenMayaUI as omui
    from AnimKey.mods.maya_compat import wrap_instance as wrapInstance
    MAYA_AVAILABLE = True
except (ImportError, RuntimeError):
    MAYA_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
#                           TRANSLATIONS
# ═══════════════════════════════════════════════════════════════════════════════

TRANSLATIONS = {
    "en": {
        "window_title": "Welcome to AnimKey",
        "subtitle": "Professional Animation Toolkit for Maya",
        "welcome_header": "Welcome!",
        "description": """


<p style="font-size: 11px; color: #808080; margin-top: 15px; font-style: italic;">
The toolbar is now available at the top of your Maya window.
</p>
""",
        "language_label": "Language:",
        "get_started": "Get Started",
        "version": "Version 1.0",
    },
    "es": {
        "window_title": "Bienvenido a AnimKey",
        "subtitle": "Kit de Herramientas de Animación Profesional para Maya",
        "welcome_header": "¡Bienvenido!",
        "description": """


<p style="font-size: 11px; color: #808080; margin-top: 15px; font-style: italic;">
La barra de herramientas está ahora disponible en la parte superior de tu ventana de Maya.
</p>
""",
        "language_label": "Idioma:",
        "get_started": "Comenzar",
        "version": "Versión 1.0",
    }
}


# ═══════════════════════════════════════════════════════════════════════════════
#                           STYLED BUTTON
# ═══════════════════════════════════════════════════════════════════════════════

class GlowButton(QPushButton):
    """A modern button with glow effect"""
    
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._glow_intensity = 0.0
        self.setFixedHeight(40)
        self.setMinimumWidth(140)
        self.setCursor(Qt.PointingHandCursor)
        
        self.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #d44a4a, stop:1 #a83232);
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 24px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e05555, stop:1 #c03c3c);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #a83232, stop:1 #8a2828);
            }
        """)
        
        # Add shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(200, 60, 60, 100))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)


# ═══════════════════════════════════════════════════════════════════════════════
#                           WELCOME WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

class WelcomeWindow(QWidget):
    """
    AnimKey Welcome Window with multi-language support.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self._current_language = "en"
        self._setup_ui()
        self._apply_translations()
    
    def _setup_ui(self):
        """Setup the UI components"""
        self.setWindowTitle("Welcome to AnimKey")
        self.setFixedSize(520, 580)
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Background widget with gradient
        self._bg_widget = QWidget()
        self._bg_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a1a1e, stop:0.5 #222228, stop:1 #1a1a1e);
            }
        """)
        bg_layout = QVBoxLayout(self._bg_widget)
        bg_layout.setContentsMargins(30, 25, 30, 25)
        bg_layout.setSpacing(15)
        
        # ─── Header with language selector ───
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        # Language selector
        lang_container = QHBoxLayout()
        lang_container.setSpacing(8)
        
        self._lang_label = QLabel("Language:")
        self._lang_label.setStyleSheet("color: #808080; font-size: 11px;")
        lang_container.addWidget(self._lang_label)
        
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("English", "en")
        self._lang_combo.addItem("Español", "es")
        self._lang_combo.setFixedWidth(100)
        self._lang_combo.setStyleSheet("""
            QComboBox {
                background: #2a2a32;
                color: #e0e0e0;
                border: 1px solid #3a3a45;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QComboBox:hover {
                border-color: #f0c674;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #808080;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background: #2a2a32;
                color: #e0e0e0;
                selection-background-color: #3a3a45;
                border: 1px solid #3a3a45;
            }
        """)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_container.addWidget(self._lang_combo)
        
        header_layout.addStretch()
        header_layout.addLayout(lang_container)
        bg_layout.addLayout(header_layout)
        
        # ─── Logo ───
        logo_container = QHBoxLayout()
        logo_container.setAlignment(Qt.AlignCenter)
        
        self._logo_label = QLabel()
        logo_path = self._get_logo_path()
        if logo_path and os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            scaled_pixmap = pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._logo_label.setPixmap(scaled_pixmap)
        else:
            # Fallback text logo
            self._logo_label.setText("AnimKey")
            self._logo_label.setStyleSheet("""
                font-size: 48px;
                font-weight: bold;
                color: #f0c674;
            """)
        self._logo_label.setAlignment(Qt.AlignCenter)
        logo_container.addWidget(self._logo_label)
        bg_layout.addLayout(logo_container)
        
        # ─── Subtitle ───
        self._subtitle_label = QLabel("Professional Animation Toolkit for Maya")
        self._subtitle_label.setAlignment(Qt.AlignCenter)
        self._subtitle_label.setStyleSheet("""
            color: #f0c674;
            font-size: 14px;
            font-weight: 500;
            letter-spacing: 1px;
        """)
        bg_layout.addWidget(self._subtitle_label)
        
        bg_layout.addSpacing(10)
        
        # ─── Separator ───
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("background: #3a3a45; max-height: 1px;")
        bg_layout.addWidget(separator)
        
        bg_layout.addSpacing(5)
        
        # ─── Welcome header ───
        self._welcome_header = QLabel("Welcome!")
        self._welcome_header.setAlignment(Qt.AlignCenter)
        self._welcome_header.setStyleSheet("""
            color: #e0e0e0;
            font-size: 20px;
            font-weight: bold;
        """)
        bg_layout.addWidget(self._welcome_header)
        
        # ─── Description ───
        self._description_label = QLabel()
        self._description_label.setWordWrap(True)
        self._description_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._description_label.setStyleSheet("""
            QLabel {
                background: transparent;
                padding: 10px;
            }
        """)
        self._description_label.setTextFormat(Qt.RichText)
        bg_layout.addWidget(self._description_label, 1)
        
        # ─── Bottom section ───
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 10, 0, 0)
        
        # Version label
        self._version_label = QLabel("Version 1.0")
        self._version_label.setStyleSheet("color: #505050; font-size: 10px;")
        bottom_layout.addWidget(self._version_label)
        
        bottom_layout.addStretch()
        
        # Get Started button
        self._start_button = GlowButton("Get Started")
        self._start_button.clicked.connect(self.close)
        bottom_layout.addWidget(self._start_button)
        
        bg_layout.addLayout(bottom_layout)
        
        main_layout.addWidget(self._bg_widget)
    
    def _get_logo_path(self):
        """Get the path to the logo file"""
        # Try different possible locations
        possible_paths = []
        
        # Relative to this file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths.append(os.path.join(current_dir, "..", "data", "icons", "Logo_AnimKey.png"))
        
        for path in possible_paths:
            normalized_path = os.path.normpath(path)
            if os.path.exists(normalized_path):
                return normalized_path
        
        return None
    
    def _on_language_changed(self, index):
        """Handle language change"""
        self._current_language = self._lang_combo.itemData(index)
        self._apply_translations()
    
    def _apply_translations(self):
        """Apply translations based on current language"""
        t = TRANSLATIONS[self._current_language]
        
        self.setWindowTitle(t["window_title"])
        self._subtitle_label.setText(t["subtitle"])
        self._welcome_header.setText(t["welcome_header"])
        self._description_label.setText(t["description"])
        self._lang_label.setText(t["language_label"])
        self._start_button.setText(t["get_started"])
        self._version_label.setText(t["version"])
    
    def paintEvent(self, event):
        """Custom paint for rounded corners"""
        super().paintEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
#                           MAYA INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

def get_maya_window():
    """Get Maya's main window as a QWidget"""
    if MAYA_AVAILABLE:
        try:
            main_window_ptr = omui.MQtUtil.mainWindow()
            if main_window_ptr:
                return wrapInstance(int(main_window_ptr), QWidget)
        except:
            pass
    return None


# Global reference to prevent garbage collection
_welcome_window = None


def show_welcome():
    """Show the welcome window"""
    global _welcome_window
    
    # Close existing window if open
    if _welcome_window is not None:
        try:
            _welcome_window.close()
            _welcome_window.deleteLater()
        except:
            pass
    
    # Create new window
    parent = get_maya_window()
    _welcome_window = WelcomeWindow(parent)
    
    if MAYA_AVAILABLE:
        _welcome_window.setWindowFlags(_welcome_window.windowFlags() | Qt.Window)
    
    # Center on screen or parent
    if parent:
        parent_geo = parent.geometry()
        x = parent_geo.x() + (parent_geo.width() - _welcome_window.width()) // 2
        y = parent_geo.y() + (parent_geo.height() - _welcome_window.height()) // 2
        _welcome_window.move(x, y)
    
    _welcome_window.show()
    _welcome_window.raise_()
    
    return _welcome_window


def close_welcome():
    """Close the welcome window"""
    global _welcome_window
    if _welcome_window is not None:
        try:
            _welcome_window.close()
            _welcome_window.deleteLater()
        except:
            pass
        _welcome_window = None


# ═══════════════════════════════════════════════════════════════════════════════
#                           STANDALONE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    if not MAYA_AVAILABLE:
        app = QtWidgets.QApplication.instance()
        if not app:
            app = QtWidgets.QApplication(sys.argv)
        
        window = WelcomeWindow()
        window.show()
        
        sys.exit(execute_qt(app))
    else:
        show_welcome()
