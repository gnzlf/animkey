"""Refresh material chrome in an already-running Maya without reloading tools.

Execute in Maya's Python interpreter after installing the source fix. Existing
tool instances, timers, callbacks and tool-module references remain valid.
"""

import functools
import importlib
import json

from AnimKey.mods import uiMod
from AnimKey.mods.maya_compat import QtCore, QtWidgets
from AnimKey.mods import material_palette
from AnimKey.core import settings_style

importlib.reload(material_palette)
importlib.reload(settings_style)
from AnimKey.core import window_style

importlib.reload(window_style)


popup_class = uiMod.ContextPopupWindow
if not getattr(popup_class.__init__, "_animkey_material_refresh", False):
    previous_init = popup_class.__init__

    @functools.wraps(previous_init)
    def material_popup_init(self, *arguments, **keywords):
        previous_init(self, *arguments, **keywords)
        window_style.apply_window_style(self, defer_polish=True)

    material_popup_init._animkey_material_refresh = True
    popup_class.__init__ = material_popup_init

if not getattr(popup_class.showEvent, "_animkey_material_refresh", False):
    previous_show = popup_class.showEvent

    @functools.wraps(previous_show)
    def material_popup_show(self, event):
        # Cached tool constructors still contain their earlier 50% opacity.
        if hasattr(self, "_base_opacity"):
            self._base_opacity = self._hover_opacity = 1.0
            self.setWindowOpacity(1.0)
        previous_show(self, event)

    material_popup_show._animkey_material_refresh = True
    popup_class.showEvent = material_popup_show


refreshed = []
app = QtWidgets.QApplication.instance()
for window in app.topLevelWidgets():
    if window.property("animkeyPersistentSurface"):
        continue
    is_settings = window.objectName() == "AnimKey_Settings"
    if (not isinstance(window, popup_class) and not is_settings
            and not window.property("materialSurface")):
        continue
    was_visible = window.isVisible()
    geometry = window.geometry()
    old_surface = getattr(window, "_material_surface", None)
    old_panel = getattr(old_surface, "panel", None)
    old_paint = getattr(old_surface, "paint", True)
    old_polish = getattr(old_surface, "polish", True)
    if old_surface is not None:
        window.removeEventFilter(old_surface)
        old_surface.deleteLater()
        del window._material_surface
    if is_settings:
        window.layout().setContentsMargins(0, 0, 0, 0)
        window.setStyleSheet(settings_style.STYLESHEET)
        surface = window_style.apply_window_style(
            window, polish=False, paint=False, panel=window.container)
    elif old_panel is not None and not old_paint:
        window.setStyleSheet(window_style.COMPACT_STYLESHEET)
        surface = window_style.apply_window_style(
            window, polish=old_polish, paint=False, panel=old_panel)
    else:
        surface = window_style.apply_window_style(window, defer_polish=True)
        if hasattr(window, "_base_opacity"):
            window._base_opacity = window._hover_opacity = 1.0
            for name in ("_anim", "_opacity_anim", "_opacity_animation"):
                animation = getattr(window, name, None)
                if animation is not None:
                    try:
                        animation.stop()
                    except RuntimeError:
                        # DeleteWhenStopped animations can leave a Python
                        # wrapper on a cached, hidden tool window.
                        pass
            window.setWindowOpacity(1.0)
    window.setGeometry(geometry)
    if was_visible:
        window.show()
    # Polish hidden cached windows too, without invoking their show handlers.
    surface.eventFilter(window, QtCore.QEvent(QtCore.QEvent.Show))
    window.update()
    refreshed.append({"window": window.objectName(), "visible": window.isVisible(),
                      "legacy_container": bool(window.container.styleSheet()) if hasattr(window, "container") else False,
                      "material": bool(window.property("materialSurface"))})

print("AnimKey material refreshed: " + json.dumps(refreshed))
