"""
    AnimKey Style Module
    
    Generates Qt StyleSheets dynamically based on the current theme.
    Provides advanced styling for all UI components.
"""

from AnimKey.mods.themes import ThemeManager


# ═══════════════════════════════════════════════════════════════════════════════
#                           STYLESHEET GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def get_toolbar_stylesheet():
    """Generate stylesheet for the main toolbar container"""
    theme = ThemeManager.get_current_theme()
    
    return f'''
        QWidget#AnimKeyToolbar {{
            background-color: {theme["bg_primary"]};
            border: 1px solid {theme["border_color"]};
            border-radius: {theme["border_radius"]};
        }}
    '''


def get_button_stylesheet(button_type="default"):
    """
    Generate stylesheet for buttons
    
    button_type options:
        - default: Standard toolbar button
        - icon: Icon-only button
        - accent: Highlighted accent button
        - danger: Delete/remove action button
        - ghost: Transparent button
    """
    theme = ThemeManager.get_current_theme()
    
    styles = {
        "default": f'''
            QPushButton {{
                color: {theme["text_primary"]};
                background-color: {theme["button_bg"]};
                border: 1px solid {theme["border_color"]};
                border-radius: {theme["border_radius"]};
                padding: 4px 12px;
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_normal"]};
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
                border-color: {theme["accent_primary"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
            QPushButton:disabled {{
                color: {theme["text_muted"]};
                background-color: {theme["bg_secondary"]};
            }}
            QToolTip {{
                color: {theme["text_primary"]};
                background-color: {theme["bg_tertiary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 6px;
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_normal"]};
            }}
        ''',
        
        "icon": f'''
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: {theme["border_radius"]};
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
            QToolTip {{
                color: {theme["text_primary"]};
                background-color: {theme["bg_tertiary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 6px;
            }}
        ''',
        
        "accent": f'''
            QPushButton {{
                color: {theme["bg_primary"]};
                background-color: {theme["accent_primary"]};
                border: none;
                border-radius: {theme["border_radius"]};
                padding: 4px 12px;
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_normal"]};
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {theme["accent_secondary"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["accent_tertiary"]};
            }}
        ''',
        
        "danger": f'''
            QPushButton {{
                color: {theme["text_primary"]};
                background-color: {theme["button_bg"]};
                border: 1px solid {theme["accent_danger"]};
                border-radius: {theme["border_radius"]};
                padding: 4px 12px;
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_normal"]};
            }}
            QPushButton:hover {{
                background-color: {theme["accent_danger"]};
                color: white;
            }}
            QPushButton:pressed {{
                background-color: #cc3344;
            }}
        ''',
        
        "ghost": f'''
            QPushButton {{
                color: {theme["text_secondary"]};
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: {theme["border_radius"]};
                padding: 4px 12px;
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_normal"]};
            }}
            QPushButton:hover {{
                color: {theme["text_primary"]};
                background-color: {theme["button_hover"]};
                border-color: {theme["border_color"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
        ''',
        
        "small": f'''
            QPushButton {{
                color: {theme["text_primary"]};
                background-color: {theme["button_bg"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 2px 6px;
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_small"]};
                min-width: 20px;
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
                border-color: {theme["accent_primary"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
        ''',
    }
    
    return styles.get(button_type, styles["default"])


def get_slider_stylesheet(slider_type="tween"):
    """
    Generate stylesheet for sliders
    
    slider_type options:
        - tween: Tween machine slider with tick marks
        - blend: Blend to frame slider
        - curve: Curve manipulation slider
        - simple: Basic slider without decorations
    """
    theme = ThemeManager.get_current_theme()
    
    # Base slider style
    base_groove = f'''
        QSlider::groove:horizontal {{
            height: 4px;
            background: {theme["slider_groove"]};
            border-radius: 2px;
        }}
        
        QSlider::handle:horizontal {{
            background: {theme["slider_handle"]};
            width: 12px;
            height: 12px;
            margin: -4px 0;
            border-radius: 6px;
        }}
        
        QSlider::handle:horizontal:hover {{
            background: {theme["slider_handle_hover"]};
        }}
    '''
    
    styles = {
        "tween": f'''
            QSlider {{
                background: transparent;
            }}
            
            QSlider::groove:horizontal {{
                height: 4px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {theme["slider_tick"]},
                    stop:0.01 {theme["slider_tick"]},
                    stop:0.02 {theme["slider_groove"]},
                    stop:0.115 {theme["slider_groove"]},
                    stop:0.125 {theme["slider_tick"]},
                    stop:0.135 {theme["slider_groove"]},
                    stop:0.235 {theme["slider_groove"]},
                    stop:0.25 {theme["slider_tick"]},
                    stop:0.26 {theme["slider_groove"]},
                    stop:0.36 {theme["slider_groove"]},
                    stop:0.375 {theme["slider_tick"]},
                    stop:0.385 {theme["slider_groove"]},
                    stop:0.485 {theme["slider_groove"]},
                    stop:0.50 {theme["slider_tick"]},
                    stop:0.515 {theme["slider_groove"]},
                    stop:0.615 {theme["slider_groove"]},
                    stop:0.625 {theme["slider_tick"]},
                    stop:0.635 {theme["slider_groove"]},
                    stop:0.735 {theme["slider_groove"]},
                    stop:0.75 {theme["slider_tick"]},
                    stop:0.76 {theme["slider_groove"]},
                    stop:0.86 {theme["slider_groove"]},
                    stop:0.875 {theme["slider_tick"]},
                    stop:0.885 {theme["slider_groove"]},
                    stop:0.98 {theme["slider_groove"]},
                    stop:0.99 {theme["slider_tick"]},
                    stop:1 {theme["slider_tick"]}
                );
                border-radius: 2px;
            }}
            
            QSlider::handle:horizontal {{
                background: {theme["slider_handle"]};
                width: 10px;
                height: 14px;
                margin: -5px 0;
                border-radius: 3px;
            }}
            
            QSlider::handle:horizontal:hover {{
                background: {theme["slider_handle_hover"]};
            }}
            
            QToolTip {{
                color: {theme["text_primary"]};
                background-color: {theme["bg_tertiary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
        ''',
        
        "blend": f'''
            QSlider {{
                background: transparent;
            }}
            
            QSlider::groove:horizontal {{
                height: 4px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {theme["accent_secondary"]},
                    stop:0.02 {theme["slider_groove"]},
                    stop:0.49 {theme["slider_groove"]},
                    stop:0.50 {theme["accent_primary"]},
                    stop:0.51 {theme["slider_groove"]},
                    stop:0.98 {theme["slider_groove"]},
                    stop:1 {theme["accent_secondary"]}
                );
                border-radius: 2px;
            }}
            
            QSlider::handle:horizontal {{
                background: {theme["slider_handle"]};
                width: 10px;
                height: 14px;
                margin: -5px 0;
                border-radius: 3px;
            }}
            
            QSlider::handle:horizontal:hover {{
                background: {theme["slider_handle_hover"]};
            }}
        ''',
        
        "curve": f'''
            QSlider {{
                background: transparent;
            }}
            
            QSlider::groove:horizontal {{
                height: 4px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {theme["accent_success"]},
                    stop:0.01 {theme["accent_success"]},
                    stop:0.02 {theme["slider_groove"]},
                    stop:0.115 {theme["slider_groove"]},
                    stop:0.125 {theme["accent_success"]},
                    stop:0.135 {theme["slider_groove"]},
                    stop:0.235 {theme["slider_groove"]},
                    stop:0.25 {theme["accent_success"]},
                    stop:0.26 {theme["slider_groove"]},
                    stop:0.36 {theme["slider_groove"]},
                    stop:0.375 {theme["accent_success"]},
                    stop:0.385 {theme["slider_groove"]},
                    stop:0.485 {theme["slider_groove"]},
                    stop:0.50 {theme["accent_success"]},
                    stop:0.515 {theme["slider_groove"]},
                    stop:0.615 {theme["slider_groove"]},
                    stop:0.625 {theme["accent_success"]},
                    stop:0.635 {theme["slider_groove"]},
                    stop:0.735 {theme["slider_groove"]},
                    stop:0.75 {theme["accent_success"]},
                    stop:0.76 {theme["slider_groove"]},
                    stop:0.86 {theme["slider_groove"]},
                    stop:0.875 {theme["accent_success"]},
                    stop:0.885 {theme["slider_groove"]},
                    stop:0.98 {theme["slider_groove"]},
                    stop:0.99 {theme["accent_success"]},
                    stop:1 {theme["accent_success"]}
                );
                border-radius: 2px;
            }}
            
            QSlider::handle:horizontal {{
                background: {theme["slider_handle"]};
                width: 10px;
                height: 14px;
                margin: -5px 0;
                border-radius: 3px;
            }}
            
            QSlider::handle:horizontal:hover {{
                background: {theme["slider_handle_hover"]};
            }}
        ''',
        
        "simple": f'''
            QSlider {{
                background: transparent;
            }}
            {base_groove}
        ''',
    }
    
    return styles.get(slider_type, styles["simple"])


def get_combobox_stylesheet():
    """Generate stylesheet for combo boxes / dropdowns"""
    theme = ThemeManager.get_current_theme()
    
    return f'''
        QComboBox {{
            color: {theme["text_primary"]};
            background-color: {theme["bg_secondary"]};
            border: 1px solid {theme["border_color"]};
            border-radius: {theme["border_radius"]};
            padding: 4px 8px;
            padding-right: 20px;
            font-family: {theme["font_family"]};
            font-size: {theme["font_size_normal"]};
            min-width: 80px;
        }}
        
        QComboBox:hover {{
            border-color: {theme["accent_primary"]};
        }}
        
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        
        QComboBox::down-arrow {{
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 6px solid {theme["text_secondary"]};
            margin-right: 6px;
        }}
        
        QComboBox QAbstractItemView {{
            color: {theme["text_primary"]};
            background-color: {theme["bg_secondary"]};
            border: 1px solid {theme["border_color"]};
            selection-background-color: {theme["accent_primary"]};
            selection-color: {theme["bg_primary"]};
            outline: none;
        }}
        
        QComboBox QAbstractItemView::item {{
            padding: 6px 8px;
        }}
        
        QComboBox QAbstractItemView::item:hover {{
            background-color: {theme["button_hover"]};
        }}
    '''


def get_input_stylesheet():
    """Generate stylesheet for input fields"""
    theme = ThemeManager.get_current_theme()
    
    return f'''
        QLineEdit, QSpinBox, QDoubleSpinBox {{
            color: {theme["text_primary"]};
            background-color: {theme["bg_secondary"]};
            border: 1px solid {theme["border_color"]};
            border-radius: {theme["border_radius"]};
            padding: 4px 8px;
            font-family: {theme["font_family"]};
            font-size: {theme["font_size_normal"]};
            selection-background-color: {theme["accent_primary"]};
        }}
        
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border-color: {theme["accent_primary"]};
        }}
        
        QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
            color: {theme["text_muted"]};
            background-color: {theme["bg_tertiary"]};
        }}
        
        QSpinBox::up-button, QDoubleSpinBox::up-button,
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            background-color: {theme["button_bg"]};
            border: none;
            width: 16px;
        }}
        
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
            background-color: {theme["button_hover"]};
        }}
    '''


def get_label_stylesheet(label_type="default"):
    """Generate stylesheet for labels"""
    theme = ThemeManager.get_current_theme()
    
    styles = {
        "default": f'''
            QLabel {{
                color: {theme["text_primary"]};
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_normal"]};
                background: transparent;
            }}
        ''',
        
        "title": f'''
            QLabel {{
                color: {theme["text_primary"]};
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_large"]};
                font-weight: bold;
                background: transparent;
            }}
        ''',
        
        "accent": f'''
            QLabel {{
                color: {theme["accent_primary"]};
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_normal"]};
                background: transparent;
            }}
        ''',
        
        "muted": f'''
            QLabel {{
                color: {theme["text_muted"]};
                font-family: {theme["font_family"]};
                font-size: {theme["font_size_small"]};
                background: transparent;
            }}
        ''',
    }
    
    return styles.get(label_type, styles["default"])


def get_menu_stylesheet():
    """Generate stylesheet for context menus"""
    theme = ThemeManager.get_current_theme()
    
    return f'''
        QMenu {{
            background-color: {theme["bg_secondary"]};
            border: 1px solid {theme["border_color"]};
            border-radius: {theme["border_radius"]};
            padding: 4px 0;
        }}
        
        QMenu::item {{
            color: {theme["text_primary"]};
            background-color: transparent;
            padding: 8px 24px 8px 12px;
            font-family: {theme["font_family"]};
            font-size: {theme["font_size_normal"]};
        }}
        
        QMenu::item:selected {{
            background-color: {theme["accent_primary"]};
            color: {theme["bg_primary"]};
        }}
        
        QMenu::item:disabled {{
            color: {theme["text_muted"]};
        }}
        
        QMenu::separator {{
            height: 1px;
            background-color: {theme["border_color"]};
            margin: 4px 8px;
        }}
        
        QMenu::indicator {{
            width: 16px;
            height: 16px;
            margin-left: 8px;
        }}
    '''


def get_scrollbar_stylesheet():
    """Generate stylesheet for scrollbars"""
    theme = ThemeManager.get_current_theme()
    
    return f'''
        QScrollBar:vertical {{
            background-color: {theme["bg_secondary"]};
            width: 10px;
            border-radius: 5px;
        }}
        
        QScrollBar::handle:vertical {{
            background-color: {theme["button_bg"]};
            border-radius: 5px;
            min-height: 30px;
        }}
        
        QScrollBar::handle:vertical:hover {{
            background-color: {theme["button_hover"]};
        }}
        
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        
        QScrollBar:horizontal {{
            background-color: {theme["bg_secondary"]};
            height: 10px;
            border-radius: 5px;
        }}
        
        QScrollBar::handle:horizontal {{
            background-color: {theme["button_bg"]};
            border-radius: 5px;
            min-width: 30px;
        }}
        
        QScrollBar::handle:horizontal:hover {{
            background-color: {theme["button_hover"]};
        }}
        
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
    '''


def get_separator_stylesheet(orientation="horizontal"):
    """Generate stylesheet for separators"""
    theme = ThemeManager.get_current_theme()
    
    if orientation == "horizontal":
        return f'''
            QFrame {{
                background-color: {theme["border_color"]};
                max-height: 1px;
                min-height: 1px;
            }}
        '''
    else:
        return f'''
            QFrame {{
                background-color: {theme["border_color"]};
                max-width: 1px;
                min-width: 1px;
            }}
        '''


def get_window_stylesheet():
    """Generate stylesheet for popup windows"""
    theme = ThemeManager.get_current_theme()
    
    return f'''
        QWidget {{
            background-color: {theme["bg_primary"]};
            color: {theme["text_primary"]};
            font-family: {theme["font_family"]};
        }}
    '''


def get_close_button_stylesheet():
    """Generate stylesheet for close buttons"""
    theme = ThemeManager.get_current_theme()
    
    return f'''
        QPushButton {{
            background-color: {theme["button_bg"]};
            color: {theme["text_secondary"]};
            border: none;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }}
        QPushButton:hover {{
            background-color: {theme["accent_danger"]};
            color: white;
        }}
        QPushButton:pressed {{
            background-color: #cc3344;
        }}
    '''


# ═══════════════════════════════════════════════════════════════════════════════
#                           SELECTION SET BUTTON STYLES
# ═══════════════════════════════════════════════════════════════════════════════

def get_selection_set_button_stylesheet(color_normal, color_hover, text_color="#333333"):
    """Generate stylesheet for selection set buttons"""
    theme = ThemeManager.get_current_theme()
    
    return f'''
        QPushButton {{
            color: {text_color};
            background-color: {color_normal};
            border: none;
            border-radius: 4px;
            padding: 4px 8px;
            font-family: {theme["font_family"]};
            font-size: {theme["font_size_normal"]};
            font-weight: bold;
        }}
        QPushButton:hover {{
            background-color: {color_hover};
        }}
        QPushButton:pressed {{
            background-color: {color_normal};
        }}
    '''

