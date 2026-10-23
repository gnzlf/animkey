"""Render toolbar controls and verify styling preserves their interactions."""

import os
from pathlib import Path
import sys
from types import SimpleNamespace

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
output = root / "dist" / "dark-toolbar-verification"
output.mkdir(parents=True, exist_ok=True)
os.environ["MAYA_APP_DIR"] = str(output / "maya-user")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets

app = QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
app.setFont(QtGui.QFont("Segoe UI", 9))
import maya.standalone
maya.standalone.initialize(name="python")

from AnimKey.mods import uiMod, mediaMod, material_palette
from AnimKey.core.toolbar_style import apply_toolbar_style
from AnimKey.core.settings_style import surface_fill

workspace = QtWidgets.QWidget()
workspace.setWindowTitle("AnimKey material toolbar preview")
workspace.resize(1300, 56)
outer = QtWidgets.QVBoxLayout(workspace)
outer.setContentsMargins(0, 0, 0, 0)
strip = QtWidgets.QFrame(workspace)
strip.setObjectName("AnimKeyToolbarPanStrip")
outer.addWidget(strip)
strip_layout = QtWidgets.QHBoxLayout(strip)
strip_layout.setContentsMargins(7, 4, 7, 4)
content = QtWidgets.QWidget(strip)
content.setObjectName("toolbar_content")
strip_layout.addWidget(content)
layout = QtWidgets.QHBoxLayout(content)
layout.setContentsMargins(0, 0, 0, 0)
layout.setSpacing(4)
buttons = []
narrow = uiMod.HoverButton(">")
narrow.setFixedSize(16, 28)
layout.addWidget(narrow)
for name in ("ISO", "ALN", "MIR", "RTM", "SETS"):
    button = uiMod.HoverButton(name)
    button.setFixedSize(40, 28)
    button.setStyleSheet("QPushButton { background: #dddddd; border-radius: 2px; }")
    layout.addWidget(button)
    buttons.append(button)
spin = QtWidgets.QSpinBox()
spin.setRange(1, 100)
spin.setValue(5)
spin.setFixedSize(46, 28)
layout.addWidget(spin)
dropdown = uiMod.DropdownLabel("Tweener")
layout.addWidget(dropdown)
slider = uiMod.AnimKeySlider()
slider.setMinimumWidth(260)
layout.addWidget(slider)
layout.addWidget(uiMod.DropdownLabel("Smooth/Rough", [("Smooth/Rough", "Smooth", QtGui.QColor("#8ed3b0"))]))
second = uiMod.AnimKeySlider(min_val=-100, max_val=100, origin=0)
second.setMinimumWidth(260)
second.set_accent_color(QtGui.QColor("#8ed3b0"))
layout.addWidget(second)
settings = uiMod.HoverButton("Settings")
settings.setFixedSize(80, 28)
layout.addWidget(settings)
layout.addStretch()
toolbar = SimpleNamespace(content_widget=content, toolbar_scroll_area=strip)
calls = []
buttons[0].clicked.connect(lambda: calls.append("button"))
slider.sliderPressed.connect(lambda: calls.append("press"))
slider.valueChanged.connect(lambda value: calls.append(("value", value)))
slider.sliderReleased.connect(lambda value: calls.append(("release", value)))
apply_toolbar_style(toolbar)
workspace.show()
app.processEvents()
workspace.grab().save(str(output / "toolbar.png"))
geometry = buttons[0].geometry()
buttons[0].click()
assert calls == ["button"]
slider._on_dot_clicked(15)
assert calls[1:] == ["press", ("value", 15), ("release", 15)], calls
assert buttons[0].geometry() == geometry
assert spin.value() == 5
option = QtWidgets.QStyleOptionButton()
narrow.initStyleOption(option)
text_rect = narrow.style().subElementRect(QtWidgets.QStyle.SE_PushButtonContents, option, narrow)
assert text_rect.width() >= narrow.fontMetrics().horizontalAdvance(">"), text_rect
assert not workspace.testAttribute(QtCore.Qt.WA_TranslucentBackground)
assert all("rgba(255,255,255,14)" in button.styleSheet() and "8px" in button.styleSheet() for button in buttons)
slider._return_animation.stop()
slider._value = slider._display_value = 75
slider.repaint()
workspace.grab().save(str(output / "toolbar-value.png"))

def luminance(rgb):
    channels = [value / 255.0 for value in rgb]
    channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return sum(a * b for a, b in zip(channels, (0.2126, 0.7152, 0.0722)))

# Check the application-rendered material over Maya's usual dark neutral UI.
from AnimKey.core.material_backdrop import paint_material, rounded_path
backdrop = QtGui.QImage(120, 100, QtGui.QImage.Format_ARGB32_Premultiplied)
backdrop.fill(QtGui.QColor(68, 68, 68))
preview = QtGui.QImage(backdrop.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
preview.fill(QtCore.Qt.transparent)
painter = QtGui.QPainter(preview)
paint_material(painter, rounded_path(preview.rect()), image=backdrop)
painter.end()
color = preview.pixelColor(60, 50)
assert 180 < color.alpha() < 255, 'Glass must retain real transparency'
a = color.alphaF()
background = tuple(c * a + 68 * (1 - a) for c in color.getRgb()[:3])
assert min(background) >= 88, 'Material must stand out from the Maya background'
text = QtGui.QColor(material_palette.TEXT).getRgb()[:3]
contrast = (luminance(text) + 0.05) / (luminance(background) + 0.05)
assert contrast >= 4.5, contrast
print('PASS toolbar: controls, fixed geometry, neutral grey and contrast {:.2f}:1'.format(contrast))
workspace.close()
