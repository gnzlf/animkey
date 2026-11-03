"""Refresh only toolbar presentation; keep scene/tool/slider connections alive."""

import ast
import importlib
from pathlib import Path
import sys

from AnimKey.mods import uiMod, themes, styleMod
from AnimKey.mods.maya_compat import QtWidgets
from AnimKey.core import toolbar_style
from AnimKey.core import settings_style

importlib.reload(themes)
importlib.reload(styleMod)
importlib.reload(toolbar_style)


def source_method(module, class_name, method_name, target):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    namespace = dict(module.__dict__)
    exec(compile(ast.Module(body=[method], type_ignores=[]), module.__file__, "exec"), namespace)
    setattr(target, method_name, namespace[method_name])


tree = ast.parse(Path(uiMod.__file__).read_text(encoding="utf-8-sig"))
predicate = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_is_animkey_tool_window")
namespace = dict(uiMod.__dict__)
exec(compile(ast.Module(body=[predicate], type_ignores=[]), uiMod.__file__, "exec"), namespace)
uiMod._is_animkey_tool_window = namespace["_is_animkey_tool_window"]


# Replace paint methods on the original classes. Reloading uiMod would create
# different QObject classes while the existing toolbar still holds the old ones.
source_method(uiMod, "AnimKeySlider", "paintEvent", uiMod.AnimKeySlider)
source_method(uiMod, "DropdownLabel", "paintEvent", uiMod.DropdownLabel)
theme = themes.ThemeManager.get_current_theme()
for name, value in {
    "SLIDER_TRACK": "#64080c13", "SLIDER_HANDLE": "#bdc9da",
    "SLIDER_HANDLE_BORDER": "#e1e9f5", "DOT_INACTIVE": "#6f7e94",
}.items():
    setattr(uiMod.AnimKeyColors, name, uiMod.QColor(value))

toolbar_module = sys.modules.get("AnimKey.core.toolbar")
if toolbar_module is not None:
    toolbar_module.apply_toolbar_style = toolbar_style.apply_toolbar_style
    source_method(toolbar_module, "AnimKeyToolbar", "_build_ui", toolbar_module.AnimKeyToolbar)
    toolbar = toolbar_module.animkey_toolbar
    if getattr(toolbar, "content_widget", None) is not None:
        toolbar_style.apply_toolbar_style(toolbar)
        for widget in toolbar.content_widget.findChildren(QtWidgets.QWidget):
            if isinstance(widget, uiMod.AnimKeySlider):
                widget.slider_height = 6
                widget._update_dot_positions()
                widget.update()

# Existing Settings and Draw constructors retain imported stylesheet constants.
for name in ("AnimKey.core.settings", "AnimKey.buttons.brush"):
    module = sys.modules.get(name)
    if module is not None:
        module.STYLESHEET = settings_style.STYLESHEET
        from AnimKey.core.window_style import COMPACT_STYLESHEET
        module.COMPACT_STYLESHEET = COMPACT_STYLESHEET

print("AnimKey: dark material applied to the toolbar and its existing controls.")
