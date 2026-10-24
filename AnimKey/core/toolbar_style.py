"""Neutral grey translucent controls sharing the tool windows' frosted style."""

import re

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets, is_qt_object_valid, get_maya_font
from AnimKey.mods import material_palette as material


def button_stylesheet(accent=None, icon=False):
    padding = "0" if icon else "2px 7px"
    return """
        QPushButton {{
            background: {bg}; color: {text}; border: 1px solid {border};
            border-radius: 8px; padding: {padding}; min-height: 0;
            font-size: 11px;
        }}
        QPushButton:hover {{ background: {hover}; border-color: {border}; color: #ffffff; }}
        QPushButton:pressed {{ background: {pressed}; border-color: {border}; }}
        QPushButton:checked, QPushButton[materialActive="true"] {{
            background: rgba(125,125,125,58); border-color: {border}; color: #f4f4f4;
        }}
        QPushButton:disabled {{ color: #a4a4a4; background: rgba(0,0,0,20); border-color: {border}; }}
        QToolTip {{ background: #252525; color: #eeeeee; border: 1px solid #5b5b5b; padding: 6px; border-radius: 5px; }}
    """.format(bg="rgba(255,255,255,14)", text=material.TEXT, border=material.BORDER,
               hover="rgba(255,255,255,30)", pressed="rgba(0,0,0,45)", padding=padding)


# At alpha 200, this tint over Maya's usual #444444 renders near #4f4f4f.
# Keep the edge fade narrow for a more defined transition into Maya's UI.
STRIP_STYLESHEET = """
QFrame#AnimKeyToolbarPanStrip {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(80,80,80,35), stop:0.12 rgba(80,80,80,200),
        stop:0.88 rgba(80,80,80,200), stop:1 rgba(80,80,80,35));
    border: 1px solid transparent; border-radius: 12px;
}
QWidget#toolbar_content { background: transparent; border: none; }
"""

CONTENT_STYLESHEET = """
QWidget { background: transparent; color: #eeeeee; font-size: 11px; }
QSpinBox, QDoubleSpinBox, QLineEdit {
    background: rgba(14,14,14,100); color: #eeeeee; border: 1px solid #424242;
    border-radius: 7px; padding: 2px 4px; min-height: 0; selection-background-color: #5a5a5a;
}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: #424242; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 13px; background: transparent; border: none;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #393939; }
"""


def apply_toolbar_style(toolbar):
    """Style existing controls, preserving their connections, icons and geometry."""
    content = toolbar.content_widget
    content.setFont(get_maya_font())
    strip = toolbar.toolbar_scroll_area
    glass = getattr(toolbar, "_material_glass", None)
    if glass is not None and (not is_qt_object_valid(glass) or glass._disposed):
        glass = None
    workspace = glass.anchor.parentWidget() if glass is not None else strip.parentWidget()
    if workspace is not None:
        workspace.setStyleSheet("QWidget { background: transparent; }")
    strip.setStyleSheet(STRIP_STYLESHEET)
    content.setStyleSheet(CONTENT_STYLESHEET)
    for widget in content.findChildren(QtWidgets.QWidget):
        if isinstance(widget, QtWidgets.QPushButton):
            accent = widget.property("materialAccent")
            if not accent:
                match = re.search(r"QPushButton:hover\s*\{[^}]*border-color:\s*(#[a-fA-F0-9]{6})", widget.styleSheet())
                accent = match.group(1) if match else material.ACCENT
                widget.setProperty("materialAccent", accent)
            widget.setProperty("materialActive", bool(getattr(widget, "_active", False)
                                                       or getattr(widget, "_is_active", False)))
            widget.setStyleSheet(button_stylesheet(
                accent, icon=not widget.icon().isNull() or widget.maximumWidth() <= 36))
        elif isinstance(widget, (QtWidgets.QAbstractSpinBox, QtWidgets.QLineEdit)):
            widget.setStyleSheet("")
        widget.update()
    if hasattr(toolbar, "_crash_style_off"):
        toolbar._crash_style_off = button_stylesheet("#df929b", icon=True)
    content.update()
    strip.update()
    from AnimKey.core.toolbar_glass import install_toolbar_glass
    glass = install_toolbar_glass(toolbar)
    if glass is not None:
        strip.setStyleSheet("QFrame#AnimKeyToolbarPanStrip { background: transparent; border: none; }\n"
                           "QWidget#toolbar_content { background: transparent; border: none; }")
