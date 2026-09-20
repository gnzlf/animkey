"""
    AnimKey Main Toolbar
    
    The main toolbar class that creates and manages the AnimKey UI.
"""

import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as mui

import os
import importlib
import threading
import time

from AnimKey.mods.maya_compat import (
    QApplication,
    QColor,
    QTimer,
    QtCore,
    QtGui,
    QtWidgets,
    is_qt_object_valid,
    execute_qt,
    shiboken,
    wrap_instance,
)

wrapInstance = wrap_instance

# AnimKey modules
from AnimKey.mods.themes import ThemeManager
from AnimKey.mods import styleMod as style
from AnimKey.mods import configMod as config
from AnimKey.mods import uiMod as ui
from AnimKey.core.executionGuard import animkey_execution
from AnimKey.sliders.graph_editor_view import GraphEditorViewSync


def _qt_object_is_alive(obj):
    return is_qt_object_valid(obj)


# ═══════════════════════════════════════════════════════════════════════════════
#                           BUTTON MODULE IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════

# Import button functions at module level to ensure consistent scope for UI triggers
from AnimKey.buttons.isolate import execute as isolate_execute
from AnimKey.buttons.align_objects import (
    execute as align_execute, 
    align_position, 
    align_orientation, 
    align_scale
)
from AnimKey.buttons.resetValues import execute as reset_values_execute
from AnimKey.buttons.animation_offset import (
    execute as anim_offset_execute, 
    has_active_offset, 
    set_button_active as set_offset_button_active,
    is_active as is_offset_active,
    adjust_keyframes as anim_offset_adjust_keyframes
)
from AnimKey.buttons.selectOpposite import execute as select_opposite_execute
from AnimKey.buttons.mirror import execute as mirror_execute
from AnimKey.buttons.copyAnimation import (
    execute as copy_animation_execute,
    copy_animation,
    save_animation,
    paste_animation,
    paste_insert_animation,
    paste_opposite_animation,
    copy_pose,
    paste_pose
)
from AnimKey.buttons.microMove import (
    execute as micro_move_execute, 
    is_active as is_micro_move_active, 
    set_button_active as set_micro_move_button_active
)
from AnimKey.buttons.followCam import (
    execute as follow_cam_execute,
    create_follow_cam,
    remove_follow_cam
)
from AnimKey.buttons.linkObjects import (
    execute as link_objects_execute,
    copy_link_frame,
    copy_link_playback_range,
    paste_link_frame,
    paste_link_playback_range,
    toggle_auto_link,
    is_auto_link_enabled
)
from AnimKey.buttons.copyWorldspace import execute as copy_worldspace_execute
from AnimKey.buttons.select_hierarchy import (
    execute as select_hierarchy_execute,
    select_rig_controls,
    select_animated_controls,
    select_visible_nurbs_curves
)
from AnimKey.buttons.tempPivot import (
    execute as temp_pivot_execute,
    execute_temp_pivot as temp_pivot_quick_execute,
    is_active as is_temp_pivot_active, 
    set_button_active as set_temp_pivot_button_active
)
from AnimKey.buttons.trail import (
    execute as trail_execute,
    has_active_trail, 
    set_button_active as set_trail_button_active
)

from AnimKey.buttons.tangents import (
    execute_plateau, execute_step, execute_flat, execute_linear,
    execute_clamped, execute_spline, execute_auto
)

from AnimKey.buttons.gimbalFixer import execute as gimbal_execute
from AnimKey.buttons.switcher import execute as switcher_execute
from AnimKey.buttons.bakeAnim import execute as bake_execute
from AnimKey.buttons.retimer import execute as retimer_execute
from AnimKey.buttons.animCleaner import execute as cleaner_execute
from AnimKey.buttons.selectionSets import execute as sets_execute
from AnimKey.buttons.animCrash import execute as anim_crash_execute
from AnimKey.buttons.flashbuttons import execute as flash_buttons_execute

# ═══════════════════════════════════════════════════════════════════════════════
#                           GLOBAL CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

WORKSPACE_NAME = "AnimKey_Toolbar"
TOOLBAR_HEIGHT = 42
DEFAULT_DOCK_AREA = "bottom"


def _looks_like_slider_widget(widget):
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


def _is_toolbar_pan_interactive_widget(widget, stop_widget=None):
    interactive_types = (
        QtWidgets.QAbstractButton,
        QtWidgets.QAbstractSlider,
        QtWidgets.QAbstractSpinBox,
        QtWidgets.QComboBox,
        QtWidgets.QLineEdit,
        QtWidgets.QMenu,
        QtWidgets.QScrollBar,
        QtWidgets.QTextEdit,
        QtWidgets.QPlainTextEdit,
        QtWidgets.QAbstractItemView,
    )

    current = widget if isinstance(widget, QtWidgets.QWidget) else None
    while current is not None and current is not stop_widget:
        if _looks_like_slider_widget(current):
            return True
        if isinstance(current, interactive_types):
            return True
        try:
            if current.cursor().shape() == QtCore.Qt.PointingHandCursor:
                return True
        except Exception:
            pass
        current = current.parentWidget()
    return False


def _clear_toolbar_pan_cursor(widget):
    if widget is None:
        return
    try:
        if widget.cursor().shape() in (QtCore.Qt.OpenHandCursor, QtCore.Qt.ClosedHandCursor):
            widget.setCursor(QtCore.Qt.SizeHorCursor)
    except Exception:
        pass


class ToolbarPanViewport(QtWidgets.QFrame):
    """Horizontal toolbar strip that pans its content inside a clipped frame."""

    def __init__(self, parent=None):
        super(ToolbarPanViewport, self).__init__(parent)
        self._content_widget = None
        self._is_panning = False
        self._last_global_pos = None
        self._last_time = QtCore.QElapsedTimer()
        self._velocity = 0.0
        self._pan_anim = None
        self._scroll_offset = 0
        self._content_width = 0
        self._start_offset = 0
        self._start_global_x = 0
        self._base_item_geometries = {}
        self._base_layout_margins = None
        self._app_filter_installed = False
        self._override_cursor_active = False

        self.setObjectName("AnimKeyToolbarPanStrip")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setLineWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoMousePropagation, True)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.installEventFilter(self)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_filter_installed = True
        self.destroyed.connect(self._remove_app_filter)

    def is_panning(self):
        return self._is_panning

    def begin_pan_from_event(self, event):
        return self._begin_pan(event)

    def update_pan_from_event(self, event):
        return self._update_pan(event)

    def end_pan_from_event(self):
        return self._end_pan()

    def viewport(self):
        return self

    def widget(self):
        return self._content_widget

    def set_pan_widget(self, widget):
        self._content_widget = widget
        widget.setParent(self)
        widget.setMouseTracking(True)
        widget.setCursor(QtCore.Qt.OpenHandCursor)
        widget.show()
        widget.raise_()
        if widget.layout() is not None:
            margins = widget.layout().contentsMargins()
            self._base_layout_margins = (
                margins.left(), margins.top(), margins.right(), margins.bottom()
            )
        self._install_pan_filter(widget)
        self._update_content_width()

    def _remove_app_filter(self, *_):
        if not self._app_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._app_filter_installed = False

    def _is_interactive_widget(self, widget):
        return _is_toolbar_pan_interactive_widget(widget, self)

    def _install_pan_filter(self, widget):
        if widget is None:
            return
        for child in [widget] + widget.findChildren(QtWidgets.QWidget):
            try:
                child.setMouseTracking(True)
                child.removeEventFilter(self)
                if self._is_interactive_widget(child):
                    if _looks_like_slider_widget(child):
                        _clear_toolbar_pan_cursor(child)
                    continue
                child.setCursor(QtCore.Qt.OpenHandCursor)
                child.installEventFilter(self)
            except Exception:
                pass

    def _update_content_width(self):
        widget = self.widget()
        if widget is None or widget.layout() is None:
            return
        widget.layout().activate()
        layout = widget.layout()
        margins = layout.contentsMargins()
        spacing = max(0, layout.spacing())
        visible_items = 0
        width = margins.left() + margins.right()

        for i in range(layout.count()):
            item = layout.itemAt(i)
            if not item:
                continue
            child = item.widget()
            if child is None:
                continue
            if not child.isVisible() or child.maximumWidth() == 0:
                continue

            hint = child.sizeHint()
            min_hint = child.minimumSizeHint()
            child_width = max(
                hint.width(),
                min_hint.width(),
                child.minimumWidth(),
                child.width(),
            )
            if child.layout() is not None:
                child_width = max(
                    child_width,
                    child.layout().sizeHint().width(),
                    child.layout().minimumSize().width(),
                )
            if child.maximumWidth() < 16777215:
                child_width = min(child_width, child.maximumWidth())
            width += child_width
            visible_items += 1

        if visible_items > 1:
            width += spacing * (visible_items - 1)

        right_edge = 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            child = item.widget() if item else None
            if child is None or not child.isVisible() or child.maximumWidth() == 0:
                continue
            geom = child.geometry()
            if geom.isValid():
                right_edge = max(right_edge, geom.right() + margins.right() + 1)

        deep_right_edge = 0
        for child in widget.findChildren(QtWidgets.QWidget):
            try:
                if not child.isVisible() or child.maximumWidth() == 0:
                    continue
                child_pos = child.mapTo(widget, QtCore.QPoint(0, 0))
                deep_right_edge = max(deep_right_edge, child_pos.x() + child.width() + margins.right())
            except Exception:
                pass

        width = max(width, widget.layout().sizeHint().width(), widget.sizeHint().width())
        width = max(width, right_edge, deep_right_edge, self._effective_viewport_width())
        height = self.viewport().height() or widget.sizeHint().height()
        self._content_width = int(width)
        widget.setMinimumWidth(self._content_width)
        widget.setMaximumWidth(self._content_width)
        widget.setFixedHeight(height)
        widget.setGeometry(0, 0, self._content_width, height)
        layout.invalidate()
        layout.activate()
        self._capture_base_item_geometries(layout)
        self._set_scroll_offset(self._scroll_offset)

    def _capture_base_item_geometries(self, layout):
        self._base_item_geometries = {}
        if layout is None:
            return
        for i in range(layout.count()):
            item = layout.itemAt(i)
            child = item.widget() if item else None
            if child is None:
                continue
            self._base_item_geometries[child] = QtCore.QRect(child.geometry())

    def _apply_child_offset(self):
        if not self._base_item_geometries:
            return
        for child, base_geometry in list(self._base_item_geometries.items()):
            try:
                child.setGeometry(base_geometry.translated(-self._scroll_offset, 0))
            except RuntimeError:
                self._base_item_geometries.pop(child, None)
            except Exception:
                pass

    def _screen_rect_for_global_pos(self, global_pos):
        app = QtWidgets.QApplication.instance()
        if app is None:
            return None
        try:
            if hasattr(QtWidgets.QApplication, "screenAt"):
                screen = QtWidgets.QApplication.screenAt(global_pos)
                if screen is not None:
                    return screen.availableGeometry()
        except Exception:
            pass
        try:
            desktop = app.desktop()
            return desktop.availableGeometry(global_pos)
        except Exception:
            return None

    def _global_widget_rect(self, widget):
        top_left = widget.mapToGlobal(QtCore.QPoint(0, 0))
        return QtCore.QRect(top_left, QtCore.QSize(max(1, widget.width()), max(1, widget.height())))

    def _effective_viewport_width(self):
        fallback_width = max(1, int(self.width() or self.viewport().width() or 1))
        try:
            visible_rect = self._global_widget_rect(self)
        except Exception:
            return fallback_width

        parent = self.parentWidget()
        while parent is not None:
            try:
                if parent.isVisible():
                    visible_rect = visible_rect.intersected(self._global_widget_rect(parent))
            except Exception:
                pass
            parent = parent.parentWidget()

        try:
            screen_rect = self._screen_rect_for_global_pos(self.mapToGlobal(QtCore.QPoint(0, 0)))
            if screen_rect is not None:
                visible_rect = visible_rect.intersected(screen_rect)
        except Exception:
            pass

        if visible_rect.isValid() and visible_rect.width() > 0:
            return max(1, int(visible_rect.width()))

        width = fallback_width
        try:
            visible_width = self.visibleRegion().boundingRect().width()
            if visible_width > 0:
                width = min(width, visible_width)
        except Exception:
            pass
        try:
            screen_rect = self._screen_rect_for_global_pos(self.mapToGlobal(QtCore.QPoint(0, 0)))
            if screen_rect is not None:
                global_left = self.mapToGlobal(QtCore.QPoint(0, 0))
                width = min(width, max(1, screen_rect.right() - global_left.x() + 1))
        except Exception:
            pass
        return max(1, int(width))

    def _max_scroll_offset(self):
        widget = self.widget()
        if widget is None:
            return 0
        content_width = max(self._content_width, widget.width(), widget.sizeHint().width())
        return max(0, int(content_width - self._effective_viewport_width()))

    def _set_scroll_offset(self, value):
        widget = self.widget()
        if widget is None:
            return
        max_offset = self._max_scroll_offset()
        self._scroll_offset = max(0, min(max_offset, int(value)))

        # Maya workspace controls can report a wider viewport than the pixels
        # actually visible on screen. The content stays anchored, while each
        # top-level toolbar block is shifted manually for reliable panning.
        widget.setGeometry(
            0,
            0,
            max(self._content_width, widget.width(), self._effective_viewport_width()),
            self.height(),
        )
        self._apply_child_offset()
        widget.raise_()
        self.update()

    def _can_start_pan(self, obj, event):
        return event.button() == QtCore.Qt.MiddleButton

    def _contains_global_pos(self, global_pos):
        try:
            if not self.isVisible():
                return False
            local_pos = self.mapFromGlobal(global_pos)
            return self.rect().contains(local_pos)
        except RuntimeError:
            return False

    def _begin_pan(self, event):
        if event.modifiers() & QtCore.Qt.ShiftModifier:
            return False
        self._update_content_width()
        self._stop_anim()
        self._is_panning = True
        self._last_global_pos = self._global_pos(event)
        self._start_global_x = self._last_global_pos.x()
        self._start_offset = self._scroll_offset
        self._last_time.restart()
        self._velocity = 0.0
        try:
            self.grabMouse()
        except Exception:
            pass
        self.setCursor(QtCore.Qt.ClosedHandCursor)
        if self.widget() is not None:
            self.widget().setCursor(QtCore.Qt.ClosedHandCursor)
        if not self._override_cursor_active:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.ClosedHandCursor)
            self._override_cursor_active = True
        return True

    def _update_pan(self, event):
        global_pos = self._global_pos(event)
        if self._last_global_pos is None:
            self._last_global_pos = global_pos
            return True

        elapsed = max(1, self._last_time.elapsed())
        delta_x = global_pos.x() - self._last_global_pos.x()
        total_delta_x = global_pos.x() - self._start_global_x
        delta_scroll = -delta_x
        self._set_scroll_offset(self._start_offset - total_delta_x)

        self._velocity = (delta_scroll / float(elapsed)) * 0.75 + self._velocity * 0.25
        self._last_global_pos = global_pos
        self._last_time.restart()
        return True

    def _end_pan(self):
        self._is_panning = False
        try:
            self.releaseMouse()
        except Exception:
            pass
        if self._override_cursor_active:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._override_cursor_active = False
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if self.widget() is not None:
            self.widget().setCursor(QtCore.Qt.OpenHandCursor)
        self._animate_inertia()
        return True

    def mouseMoveEvent(self, event):
        if self._is_panning:
            self._update_pan(event)
            return
        super(ToolbarPanViewport, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton and self._is_panning:
            self._end_pan()
            return
        super(ToolbarPanViewport, self).mouseReleaseEvent(event)

    def wheelEvent(self, event):
        self._update_content_width()
        if self._max_scroll_offset() <= 0:
            return

        delta = event.angleDelta()
        amount = delta.x() if delta.x() else delta.y()
        self._set_scroll_offset(self._scroll_offset - amount * 0.45)
        event.accept()

    def resizeEvent(self, event):
        super(ToolbarPanViewport, self).resizeEvent(event)
        self._update_content_width()

    def _global_pos(self, event):
        if hasattr(event, "globalPos"):
            return event.globalPos()
        return event.globalPosition().toPoint()

    def _stop_anim(self):
        if self._pan_anim is not None:
            try:
                self._pan_anim.stop()
            except Exception:
                pass
            self._pan_anim = None

    def _clamp_scroll(self, value):
        return max(0, min(self._max_scroll_offset(), int(value)))

    def eventFilter(self, obj, event):
        if not _qt_object_is_alive(self) or not _qt_object_is_alive(obj):
            return False
        event_type = event.type()

        if self._is_panning:
            if event_type == QtCore.QEvent.MouseMove:
                return self._update_pan(event)
            if event_type == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.MiddleButton:
                return self._end_pan()

        if event_type == QtCore.QEvent.MouseButtonPress and self._can_start_pan(obj, event):
            if not self._contains_global_pos(self._global_pos(event)):
                return False
            return self._begin_pan(event)

        if event_type == QtCore.QEvent.MouseMove:
            try:
                if self._contains_global_pos(self._global_pos(event)) and not self._is_interactive_widget(obj):
                    self.setCursor(QtCore.Qt.OpenHandCursor)
                    if self.widget() is not None:
                        self.widget().setCursor(QtCore.Qt.OpenHandCursor)
            except Exception:
                pass

        if event_type == QtCore.QEvent.Wheel:
            if not self._contains_global_pos(self._global_pos(event)):
                return False
            self.wheelEvent(event)
            return event.isAccepted()

        return False

    def _animate_inertia(self):
        self._update_content_width()
        start = self._scroll_offset
        target = self._clamp_scroll(start + self._velocity * 180.0)
        distance = abs(target - start)
        if distance < 3:
            return

        self._pan_anim = QtCore.QVariantAnimation(self)
        self._pan_anim.setDuration(max(160, min(460, int(distance * 1.8))))
        self._pan_anim.setStartValue(start)
        self._pan_anim.setEndValue(target)
        self._pan_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._pan_anim.valueChanged.connect(lambda value: self._set_scroll_offset(value))
        self._pan_anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)


# ═══════════════════════════════════════════════════════════════════════════════
class ToolbarHorizontalScroller(QtWidgets.QFrame):
    """Clipped horizontal viewport for the AnimKey toolbar."""

    def __init__(self, parent=None):
        super(ToolbarHorizontalScroller, self).__init__(parent)
        self._content_widget = None
        self._content_width = 1
        self._scroll_offset = 0
        self._is_panning = False
        self._start_global_x = 0
        self._start_offset = 0
        self._last_global_pos = None
        self._last_time = QtCore.QElapsedTimer()
        self._velocity = 0.0
        self._pan_anim = None
        self._app_filter_installed = False
        self._override_cursor_active = False

        self.setObjectName("AnimKeyToolbarPanStrip")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setLineWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoMousePropagation, True)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.installEventFilter(self)

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_filter_installed = True
        self.destroyed.connect(self._remove_app_filter)

    def widget(self):
        return self._content_widget

    def viewport(self):
        return self

    def is_panning(self):
        return self._is_panning

    def set_pan_widget(self, widget):
        self._content_widget = widget
        widget.setParent(self)
        widget.setMouseTracking(True)
        widget.setCursor(QtCore.Qt.OpenHandCursor)
        widget.show()
        self._install_pan_filter(widget)
        self._update_content_width()

    def begin_pan_from_event(self, event):
        return self._begin_pan(event)

    def update_pan_from_event(self, event):
        return self._update_pan(event)

    def end_pan_from_event(self):
        return self._end_pan()

    def _remove_app_filter(self, *_):
        if not self._app_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._app_filter_installed = False

    def _global_pos(self, event):
        if hasattr(event, "globalPos"):
            return event.globalPos()
        return event.globalPosition().toPoint()

    def _contains_global_pos(self, global_pos):
        try:
            if not self.isVisible():
                return False
            return self.rect().contains(self.mapFromGlobal(global_pos))
        except Exception:
            return False

    def _is_interactive_widget(self, widget):
        return _is_toolbar_pan_interactive_widget(widget, self)

    def _install_pan_filter(self, widget):
        if widget is None:
            return
        for child in [widget] + widget.findChildren(QtWidgets.QWidget):
            try:
                child.removeEventFilter(self)
                child.setMouseTracking(True)
                if self._is_interactive_widget(child):
                    if _looks_like_slider_widget(child):
                        _clear_toolbar_pan_cursor(child)
                    continue
                child.setCursor(QtCore.Qt.OpenHandCursor)
                child.installEventFilter(self)
            except Exception:
                pass

    def _visible_width(self):
        width = max(1, int(self.width()))
        try:
            region_width = self.visibleRegion().boundingRect().width()
            if region_width > 0:
                width = min(width, region_width)
        except Exception:
            pass
        try:
            global_pos = self.mapToGlobal(QtCore.QPoint(0, 0))
            screen = QtWidgets.QApplication.screenAt(global_pos) if hasattr(QtWidgets.QApplication, "screenAt") else None
            if screen is not None:
                rect = screen.availableGeometry()
                width = min(width, max(1, rect.right() - global_pos.x() + 1))
        except Exception:
            pass
        return max(1, int(width))

    def _layout_natural_width(self, layout):
        margins = layout.contentsMargins()
        spacing = max(0, layout.spacing())
        width = margins.left() + margins.right()
        visible_count = 0

        for i in range(layout.count()):
            item = layout.itemAt(i)
            child = item.widget() if item else None
            if child is None or not child.isVisible() or child.maximumWidth() == 0:
                continue
            hint = child.sizeHint()
            min_hint = child.minimumSizeHint()
            child_width = max(hint.width(), min_hint.width(), child.minimumWidth())
            if child.layout() is not None:
                child_width = max(child_width, child.layout().sizeHint().width())
            if child.maximumWidth() < 16777215:
                child_width = min(child_width, child.maximumWidth())
            width += max(1, child_width)
            visible_count += 1

        if visible_count > 1:
            width += spacing * (visible_count - 1)
        return max(1, int(width + 12))

    def _update_content_width(self):
        widget = self._content_widget
        if widget is None:
            return

        layout = widget.layout()
        height = max(1, int(self.height() or widget.height() or widget.sizeHint().height()))
        if layout is not None:
            layout.invalidate()
            natural_width = self._layout_natural_width(layout)
        else:
            natural_width = max(widget.sizeHint().width(), widget.minimumSizeHint().width(), widget.width(), 1)

        self._content_width = max(natural_width, 1)
        widget.setMinimumWidth(self._content_width)
        widget.setMaximumWidth(self._content_width)
        widget.setFixedHeight(height)
        widget.resize(self._content_width, height)
        if layout is not None:
            layout.setGeometry(QtCore.QRect(0, 0, self._content_width, height))
            layout.activate()
            right_edge = 0
            margins = layout.contentsMargins()
            for i in range(layout.count()):
                item = layout.itemAt(i)
                child = item.widget() if item else None
                if child is None or not child.isVisible() or child.maximumWidth() == 0:
                    continue
                right_edge = max(right_edge, child.geometry().right() + margins.right() + 1)
            if right_edge > self._content_width:
                self._content_width = right_edge
                widget.setMinimumWidth(self._content_width)
                widget.setMaximumWidth(self._content_width)
                widget.resize(self._content_width, height)
                layout.setGeometry(QtCore.QRect(0, 0, self._content_width, height))
                layout.activate()
        self._set_scroll_offset(self._scroll_offset)

    def _max_scroll_offset(self):
        return max(0, int(self._content_width - self._visible_width()))

    def _set_scroll_offset(self, value):
        widget = self._content_widget
        if widget is None:
            return
        max_offset = self._max_scroll_offset()
        self._scroll_offset = max(0, min(max_offset, int(value)))
        widget.move(-self._scroll_offset, 0)
        widget.raise_()
        self.update()

    def scroll_by_pixels(self, pixels, animate=True):
        self._update_content_width()
        target = max(0, min(self._max_scroll_offset(), int(self._scroll_offset + pixels)))
        if not animate:
            self._set_scroll_offset(target)
            return
        self._stop_anim()
        self._pan_anim = QtCore.QVariantAnimation(self)
        self._pan_anim.setDuration(220)
        self._pan_anim.setStartValue(self._scroll_offset)
        self._pan_anim.setEndValue(target)
        self._pan_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._pan_anim.valueChanged.connect(lambda value: self._set_scroll_offset(value))
        self._pan_anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def _stop_anim(self):
        if self._pan_anim is not None:
            try:
                self._pan_anim.stop()
            except Exception:
                pass
            self._pan_anim = None

    def _can_start_pan(self, obj, event):
        return event.button() == QtCore.Qt.MiddleButton

    def _begin_pan(self, event):
        if event.modifiers() & QtCore.Qt.ShiftModifier:
            return False
        self._update_content_width()
        self._stop_anim()
        self._is_panning = True
        self._last_global_pos = self._global_pos(event)
        self._start_global_x = self._last_global_pos.x()
        self._start_offset = self._scroll_offset
        self._last_time.restart()
        self._velocity = 0.0
        try:
            self.grabMouse()
        except Exception:
            pass
        if not self._override_cursor_active:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.ClosedHandCursor)
            self._override_cursor_active = True
        return True

    def _update_pan(self, event):
        global_pos = self._global_pos(event)
        elapsed = max(1, self._last_time.elapsed())
        last_x = self._last_global_pos.x() if self._last_global_pos is not None else global_pos.x()
        delta_x = global_pos.x() - last_x
        total_delta_x = global_pos.x() - self._start_global_x
        self._set_scroll_offset(self._start_offset - total_delta_x)
        self._velocity = ((-delta_x) / float(elapsed)) * 0.7 + self._velocity * 0.3
        self._last_global_pos = global_pos
        self._last_time.restart()
        return True

    def _end_pan(self):
        self._is_panning = False
        try:
            self.releaseMouse()
        except Exception:
            pass
        if self._override_cursor_active:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._override_cursor_active = False
        self._animate_inertia()
        return True

    def _scroll_by_wheel_event(self, event):
        self._update_content_width()
        if self._max_scroll_offset() <= 0:
            return False

        amount = 0
        try:
            pixel_delta = event.pixelDelta()
            if not pixel_delta.isNull():
                amount = pixel_delta.x() if abs(pixel_delta.x()) > abs(pixel_delta.y()) else pixel_delta.y()
        except Exception:
            pass
        if not amount:
            delta = event.angleDelta()
            amount = delta.x() if abs(delta.x()) > abs(delta.y()) else delta.y()
        if not amount:
            return False

        self._set_scroll_offset(self._scroll_offset - amount * 0.55)
        event.accept()
        return True

    def wheelEvent(self, event):
        if not self._scroll_by_wheel_event(event):
            event.ignore()

    def resizeEvent(self, event):
        super(ToolbarHorizontalScroller, self).resizeEvent(event)
        self._update_content_width()

    def mouseMoveEvent(self, event):
        if self._is_panning:
            self._update_pan(event)
            return
        super(ToolbarHorizontalScroller, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_panning and event.button() == QtCore.Qt.MiddleButton:
            self._end_pan()
            return
        super(ToolbarHorizontalScroller, self).mouseReleaseEvent(event)

    def eventFilter(self, obj, event):
        if not _qt_object_is_alive(self) or not _qt_object_is_alive(obj):
            return False
        event_type = event.type()

        if self._is_panning:
            if event_type == QtCore.QEvent.MouseMove:
                return self._update_pan(event)
            if event_type == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.MiddleButton:
                return self._end_pan()

        if event_type == QtCore.QEvent.MouseButtonPress and self._can_start_pan(obj, event):
            if self._contains_global_pos(self._global_pos(event)):
                return self._begin_pan(event)

        if event_type == QtCore.QEvent.Wheel:
            if self._contains_global_pos(self._global_pos(event)):
                return self._scroll_by_wheel_event(event)

        if event_type == QtCore.QEvent.MouseMove:
            try:
                if self._contains_global_pos(self._global_pos(event)) and not self._is_interactive_widget(obj):
                    self.setCursor(QtCore.Qt.OpenHandCursor)
            except Exception:
                pass

        return False

    def _animate_inertia(self):
        self._update_content_width()
        start = self._scroll_offset
        target = max(0, min(self._max_scroll_offset(), int(start + self._velocity * 180.0)))
        distance = abs(target - start)
        if distance < 3:
            return

        self._pan_anim = QtCore.QVariantAnimation(self)
        self._pan_anim.setDuration(max(140, min(420, int(distance * 1.6))))
        self._pan_anim.setStartValue(start)
        self._pan_anim.setEndValue(target)
        self._pan_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._pan_anim.valueChanged.connect(lambda value: self._set_scroll_offset(value))
        self._pan_anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)


class ToolbarGifPreviewController(QtCore.QObject):
    """Delayed hover preview for toolbar help GIFs."""

    def __init__(self, parent=None):
        super(ToolbarGifPreviewController, self).__init__(parent)
        self._button_to_gif = {}
        self._hover_button = None
        self._popup = None
        self._movie = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(650)
        self._timer.timeout.connect(self._show_current_preview)

    def install(self, button, gif_path):
        if button is None or not gif_path or not os.path.exists(gif_path):
            return False
        try:
            button.removeEventFilter(self)
            button.installEventFilter(self)
            button.setMouseTracking(True)
            button.destroyed.connect(lambda *_: self._forget_button(button))
            self._button_to_gif[button] = gif_path
            return True
        except Exception:
            return False

    def eventFilter(self, obj, event):
        if not _qt_object_is_alive(self) or not _qt_object_is_alive(obj):
            return False
        event_type = event.type()
        if obj in self._button_to_gif:
            if event_type == QtCore.QEvent.Enter:
                self._hover_button = obj
                self._timer.start()
            elif event_type in (QtCore.QEvent.Leave, QtCore.QEvent.Hide):
                if obj is self._hover_button:
                    self._hover_button = None
                self._timer.stop()
                self._hide_preview()
            elif event_type in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                self._timer.stop()
                self._hide_preview()
        return False

    def hide(self):
        self._timer.stop()
        self._hover_button = None
        self._hide_preview()

    def _forget_button(self, button):
        self._button_to_gif.pop(button, None)
        if self._hover_button is button:
            self._hover_button = None
            self._timer.stop()
            self._hide_preview()

    def _show_current_preview(self):
        button = self._hover_button
        if button is None or not button.isVisible():
            return
        try:
            if not button.rect().contains(button.mapFromGlobal(QtGui.QCursor.pos())):
                return
        except Exception:
            return

        gif_path = self._button_to_gif.get(button)
        if not gif_path or not os.path.exists(gif_path):
            return

        self._hide_preview()
        self._popup = self._create_popup(button, gif_path)
        if self._popup is None:
            return
        self._position_popup(button)
        self._popup.show()
        if self._movie is not None:
            self._movie.start()

    def _create_popup(self, button, gif_path):
        popup = QtWidgets.QWidget(
            button.window(),
            QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint
        )
        popup.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        popup.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        popup.setObjectName("AnimKeyGifPreview")

        frame = QtWidgets.QFrame(popup)
        frame.setObjectName("gifPreviewFrame")
        frame.setStyleSheet("""
            QFrame#gifPreviewFrame {
                background-color: #202024;
                border: 1px solid #5a5a5a;
                border-radius: 8px;
            }
        """)

        layout = QtWidgets.QVBoxLayout(popup)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(frame)

        frame_layout = QtWidgets.QVBoxLayout(frame)
        frame_layout.setContentsMargins(8, 8, 8, 8)
        frame_layout.setSpacing(0)

        preview = QtWidgets.QLabel(frame)
        preview.setAlignment(QtCore.Qt.AlignCenter)
        preview.setStyleSheet("background-color: transparent; border: none;")

        reader = QtGui.QImageReader(gif_path)
        source_size = reader.size()
        max_w = 360
        max_h = 230
        if source_size.isValid() and source_size.width() > 0 and source_size.height() > 0:
            scaled_size = QtCore.QSize(source_size)
            scaled_size.scale(max_w, max_h, QtCore.Qt.KeepAspectRatio)
        else:
            scaled_size = QtCore.QSize(max_w, max_h)

        preview.setFixedSize(scaled_size)
        movie = QtGui.QMovie(gif_path)
        if not movie.isValid():
            popup.deleteLater()
            return None
        movie.setCacheMode(QtGui.QMovie.CacheAll)
        movie.setScaledSize(scaled_size)
        preview.setMovie(movie)
        self._movie = movie
        frame_layout.addWidget(preview)
        popup.adjustSize()
        return popup

    def _position_popup(self, button):
        if self._popup is None:
            return
        margin = 8
        pos = button.mapToGlobal(QtCore.QPoint(0, button.height() + margin))
        size = self._popup.sizeHint()

        app = QtWidgets.QApplication.instance()
        screen = None
        if app is not None and hasattr(QtWidgets.QApplication, "screenAt"):
            try:
                screen = QtWidgets.QApplication.screenAt(pos)
            except Exception:
                screen = None
        if screen is None and app is not None:
            try:
                screen = app.primaryScreen()
            except Exception:
                screen = None

        if screen is not None:
            rect = screen.availableGeometry()
            x = min(max(pos.x(), rect.left() + margin), rect.right() - size.width() - margin)
            y = pos.y()
            if y + size.height() + margin > rect.bottom():
                y = button.mapToGlobal(QtCore.QPoint(0, -size.height() - margin)).y()
            y = min(max(y, rect.top() + margin), rect.bottom() - size.height() - margin)
            pos = QtCore.QPoint(x, y)

        self._popup.move(pos)

    def _hide_preview(self):
        if self._movie is not None:
            try:
                self._movie.stop()
            except Exception:
                pass
            self._movie = None
        if self._popup is not None:
            try:
                self._popup.hide()
                self._popup.deleteLater()
            except Exception:
                pass
            self._popup = None


#                           MAIN TOOLBAR CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class AnimKeyToolbar:
    """
    Main AnimKey Toolbar class
    """
    
    _instance = None
    _scene_callback = None
    _selector_scriptjob = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AnimKeyToolbar, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls):
        """Return the singleton instance (None if not yet created)."""
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        
        # State variables
        self.is_visible = False
        self.tween_slider = None
        self.curve_slider = None
        
        # Slider state
        self._tween_is_dragging = False
        self._curve_is_dragging = False
        self._original_keyframes = {}
        self._slider_graph_view_sync = GraphEditorViewSync()
        
        # Button widget registry — populated by _create_tool_buttons()
        # Maps button key (e.g. "ISO", "PLT") -> QPushButton instance
        self._button_widgets = {}
        # Keeps running animations alive
        self._btn_anims = {}
        self._gif_preview_controller = ToolbarGifPreviewController()
        
        # Load configuration
        self.config = config.get_config()
        
        # Force Maya Classic theme
        ThemeManager.set_theme("maya_classic")
        
        # Setup scene callback
        self._setup_scene_callback()

        try:
            from AnimKey.mods import viewportGimbal
            viewportGimbal.apply()
        except Exception:
            pass
        try:
            from AnimKey.mods import tumbleAroundSelection
            tumbleAroundSelection.apply()
        except Exception:
            pass
        try:
            from AnimKey.mods import channelBoxMultiSelection
            channelBoxMultiSelection.apply()
        except Exception:
            pass
    
    def _setup_scene_callback(self):
        """Setup callback for scene changes"""
        if AnimKeyToolbar._scene_callback is not None:
            callbacks = AnimKeyToolbar._scene_callback
            if not isinstance(callbacks, (list, tuple)):
                callbacks = [callbacks]
            for callback in callbacks:
                try:
                    cmds.scriptJob(kill=callback, force=True)
                except:
                    pass
        
        def on_scene_opened():
            try:
                from AnimKey.buttons import trail
                trail.handle_scene_changed()
            except Exception:
                pass
            # Re-apply timeline key tick preference after scene changes.
            try:
                from AnimKey.mods import timelineChannelFilter
                timelineChannelFilter.apply()
            except Exception:
                pass
            try:
                from AnimKey.mods import viewportGimbal
                viewportGimbal.apply()
            except Exception:
                pass
            try:
                from AnimKey.mods import tumbleAroundSelection
                tumbleAroundSelection.apply()
            except Exception:
                pass
            try:
                from AnimKey.mods import channelBoxMultiSelection
                channelBoxMultiSelection.apply()
            except Exception:
                pass
        
        callbacks = []
        for event_name in ("SceneOpened", "NewSceneOpened"):
            try:
                callbacks.append(cmds.scriptJob(event=(event_name, on_scene_opened), protected=True))
            except Exception:
                pass
        AnimKeyToolbar._scene_callback = callbacks

    def _remove_scene_callbacks(self):
        """Remove scene jobs owned by the toolbar during reload/uninstall."""
        callbacks = AnimKeyToolbar._scene_callback or []
        if not isinstance(callbacks, (list, tuple)):
            callbacks = [callbacks]
        for callback in callbacks:
            try:
                if cmds.scriptJob(exists=callback):
                    cmds.scriptJob(kill=callback, force=True)
            except Exception:
                pass
        AnimKeyToolbar._scene_callback = None

    def _apply_wide_button_icon(self, button, icon_key, base_width=50):
        """Apply a non-square toolbar icon without distorting its aspect ratio."""
        from AnimKey.mods import mediaMod as media

        scale = float(self.config.get("toolbar_scale", 1.0))
        height = int(self.config.get("toolbar_icon_size", 28) * scale)
        width = int(base_width * scale)
        button.setFixedSize(width, height)

        icon_path = media.get_button_icon(icon_key)
        if not icon_path or not os.path.exists(icon_path):
            return False

        icon = QtGui.QIcon(icon_path)
        if icon.isNull():
            return False

        button.setIcon(icon)

        padding = max(2, int(4 * scale))
        icon_height = max(1, height - padding)
        icon_width = max(1, width - padding)
        pixmap = QtGui.QPixmap(icon_path)
        if not pixmap.isNull() and pixmap.height() > 0:
            aspect_width = int(round(icon_height * (float(pixmap.width()) / float(pixmap.height()))))
            icon_width = max(1, min(icon_width, aspect_width))

        button.setIconSize(QtCore.QSize(icon_width, icon_height))
        button.setText("")
        return True

    def _toolbar_gif_path(self, gif_name):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        animkey_dir = os.path.dirname(current_dir)
        return os.path.join(animkey_dir, "data", "Gifs", gif_name)

    def _install_toolbar_gif_preview(self, button, gif_name):
        controller = getattr(self, "_gif_preview_controller", None)
        if controller is None:
            controller = ToolbarGifPreviewController()
            self._gif_preview_controller = controller
        return controller.install(button, self._toolbar_gif_path(gif_name))

    def _hide_toolbar_gif_preview(self):
        controller = getattr(self, "_gif_preview_controller", None)
        if controller is not None:
            controller.hide()
    
    # ═══════════════════════════════════════════════════════════════════════════
    #                           PUBLIC METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def show(self):
        """Show the AnimKey toolbar"""
        self._setup_scene_callback()
        self._create_workspace()
        self.is_visible = True
        
        # Load and register hotkeys, then activate AnimKey hotkey set
        try:
            from AnimKey.mods import hotkeysMod
            hotkeysMod.load_and_register_all_hotkeys()
            # Remove AnimKey menu if it exists (user requested no top menu)
            hotkeysMod.create_animkey_menu()  # This function now deletes the menu
        except Exception as e:
            print(f"AnimKey: Could not load hotkeys: {e}")

        try:
            from AnimKey.mods import timelineChannelFilter
            timelineChannelFilter.apply()
        except Exception as e:
            print(f"AnimKey: Could not apply timeline key filter setting: {e}")
        try:
            from AnimKey.mods import viewportGimbal
            viewportGimbal.apply()
        except Exception as e:
            print(f"AnimKey: Could not apply viewport roll gimbal setting: {e}")
        try:
            from AnimKey.mods import tumbleAroundSelection
            tumbleAroundSelection.apply()
        except Exception as e:
            print(f"AnimKey: Could not apply tumble around selection setting: {e}")
        try:
            from AnimKey.mods import channelBoxMultiSelection
            channelBoxMultiSelection.apply()
        except Exception as e:
            print(f"AnimKey: Could not apply Channel Box multi selection setting: {e}")
    
    def hide(self):
        """Hide the AnimKey toolbar"""
        self._hide_toolbar_gif_preview()
        try:
            ui.cleanup_animkey_runtime()
        except Exception:
            pass
        if cmds.workspaceControl(WORKSPACE_NAME, query=True, exists=True):
            cmds.workspaceControl(WORKSPACE_NAME, edit=True, visible=False)
        self.is_visible = False
    
    def toggle(self):
        """Toggle toolbar visibility"""
        if cmds.workspaceControl(WORKSPACE_NAME, query=True, exists=True):
            if cmds.workspaceControl(WORKSPACE_NAME, query=True, visible=True):
                self.hide()
            else:
                self.show()
        else:
            self.show()
    
    def reload(self):
        """Reload the toolbar"""
        self._hide_toolbar_gif_preview()
        try:
            ui.cleanup_animkey_runtime(full=True)
        except Exception:
            pass
        # Delete existing workspace
        if cmds.workspaceControl(WORKSPACE_NAME, query=True, exists=True):
            cmds.deleteUI(WORKSPACE_NAME, control=True)
        
        # Recreate
        self._initialized = False
        self.__init__()
        self.show()
    

    def set_button_visible(self, key, visible):
        """
        Show or hide a toolbar button with a cartoon pop-in / pop-out.

        Two animations run IN PARALLEL:
          • maximumWidth  — bouncy spring (OutBack) on appear,
                            anticipation+slam (InBack) on disappear
          • opacity       — fade-in on appear, fade-out on disappear
                            via QGraphicsOpacityEffect

        Combining space + transparency gives a genuine "materialization"
        feel instead of a plain sliding-panel.
        """
        btn = self._button_widgets.get(key)
        if btn is None:
            return

        # ── Cancel any running animation on this button ──────────────────────
        old = self._btn_anims.pop(key, None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass

        natural_w = btn.sizeHint().width() or 28

        # Guard: already at the desired visual state
        if visible and btn.maximumWidth() > 1 and btn.isVisible():
            return
        if not visible and btn.maximumWidth() == 0:
            return

        # ── Set up the opacity effect (reuse if already present) ─────────────
        effect = btn.graphicsEffect()
        if not isinstance(effect, QtWidgets.QGraphicsOpacityEffect):
            effect = QtWidgets.QGraphicsOpacityEffect(btn)
            btn.setGraphicsEffect(effect)

        # ── Build parallel group ─────────────────────────────────────────────
        group = QtCore.QParallelAnimationGroup()

        # Width animation
        w_anim = QtCore.QPropertyAnimation(btn, b"maximumWidth")

        # Opacity animation (targets the graphics effect, not the widget)
        op_anim = QtCore.QPropertyAnimation(effect, b"opacity")

        if visible:
            # ── APPEAR: pop in with springy bounce + fade in ─────────────────
            btn.setMaximumWidth(0)
            effect.setOpacity(0.0)
            btn.setVisible(True)

            # Width: 0 → natural with OutBack overshoot (rubber-band bounce)
            w_anim.setDuration(420)
            w_anim.setStartValue(0)
            w_anim.setEndValue(natural_w)
            curve_w = QtCore.QEasingCurve(QtCore.QEasingCurve.OutBack)
            curve_w.setOvershoot(2.8)        # high = very springy
            w_anim.setEasingCurve(curve_w)

            # Opacity: 0 → 1  (slightly shorter so it "arrives" before settling)
            op_anim.setDuration(280)
            op_anim.setStartValue(0.0)
            op_anim.setEndValue(1.0)
            op_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        else:
            # ── DISAPPEAR: inhale + dissolve simultaneously ───────────────────
            effect.setOpacity(1.0)

            # Width: natural → 0 with InBack (brief bump before collapsing)
            w_anim.setDuration(280)
            w_anim.setStartValue(natural_w)
            w_anim.setEndValue(0)
            curve_w = QtCore.QEasingCurve(QtCore.QEasingCurve.InBack)
            curve_w.setOvershoot(1.8)        # anticipation "inhale"
            w_anim.setEasingCurve(curve_w)

            # Opacity: 1 → 0  (fades out in sync with collapse)
            op_anim.setDuration(220)
            op_anim.setStartValue(1.0)
            op_anim.setEndValue(0.0)
            op_anim.setEasingCurve(QtCore.QEasingCurve.InCubic)

            # Fully hide once both animations finish
            group.finished.connect(lambda b=btn: b.setVisible(False))

        group.addAnimation(w_anim)
        group.addAnimation(op_anim)

        self._btn_anims[key] = group
        group.finished.connect(self._refresh_toolbar_scroll_area)
        group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

        # ── Persist state so restart remembers ───────────────────────────────
        try:
            from AnimKey.mods import configMod as config_mod
            ws = config_mod.load_workspace()
            ws["buttons"][key] = visible
            config_mod.save_workspace(ws)
        except Exception:
            pass


    def set_section_visible(self, section_key, visible):
        """
        Show or hide a whole toolbar section (tween_slider or curve_slider)
        with the same cartoon pop-in / pop-out used by individual buttons.

        Args:
            section_key (str): 'tween_slider' or 'curve_slider'
            visible (bool):    True to show, False to hide
        """
        section_map = {
            "tween_slider":      getattr(self, "_tween_widget",         None),
            "curve_slider":      getattr(self, "_curve_widget",          None),
            "mirror_slider":     getattr(self, "_mirror_blend_widget",   None),
            "keyframe_controls": getattr(self, "_keyframe_widget",       None),
        }
        widget = section_map.get(section_key)
        if widget is None:
            return

        # Cancel any previous animation for this section
        old = self._btn_anims.pop(section_key, None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass

        natural_w = widget.sizeHint().width() or 350

        if visible and widget.maximumWidth() > 1 and widget.isVisible():
            return
        if not visible and widget.maximumWidth() == 0:
            return

        # Reuse / create opacity effect
        effect = widget.graphicsEffect()
        if not isinstance(effect, QtWidgets.QGraphicsOpacityEffect):
            effect = QtWidgets.QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)

        group   = QtCore.QParallelAnimationGroup()
        w_anim  = QtCore.QPropertyAnimation(widget, b"maximumWidth")
        op_anim = QtCore.QPropertyAnimation(effect,  b"opacity")

        if visible:
            widget.setMaximumWidth(0)
            effect.setOpacity(0.0)
            widget.setVisible(True)

            w_anim.setDuration(420)
            w_anim.setStartValue(0)
            w_anim.setEndValue(natural_w)
            c = QtCore.QEasingCurve(QtCore.QEasingCurve.OutBack)
            c.setOvershoot(2.2)
            w_anim.setEasingCurve(c)

            op_anim.setDuration(300)
            op_anim.setStartValue(0.0)
            op_anim.setEndValue(1.0)
            op_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        else:
            effect.setOpacity(1.0)

            w_anim.setDuration(260)
            w_anim.setStartValue(natural_w)
            w_anim.setEndValue(0)
            c = QtCore.QEasingCurve(QtCore.QEasingCurve.InBack)
            c.setOvershoot(1.6)
            w_anim.setEasingCurve(c)

            op_anim.setDuration(200)
            op_anim.setStartValue(1.0)
            op_anim.setEndValue(0.0)
            op_anim.setEasingCurve(QtCore.QEasingCurve.InCubic)

            group.finished.connect(lambda w=widget: w.setVisible(False))

        group.addAnimation(w_anim)
        group.addAnimation(op_anim)
        self._btn_anims[section_key] = group
        group.finished.connect(self._refresh_toolbar_scroll_area)
        group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

        # Persist
        try:
            from AnimKey.mods import configMod as config_mod
            ws = config_mod.load_workspace()
            ws.setdefault("sliders", {})[section_key] = visible
            config_mod.save_workspace(ws)
        except Exception:
            pass


    # ═══════════════════════════════════════════════════════════════════════════
    #                           WORKSPACE CREATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _connect_guarded_action(self, action, action_name, callback):
        """Run context-menu actions through the same gate as toolbar buttons.

        Qt invokes QAction callbacks from C++, so the Python stack no longer
        contains the toolbar dispatcher when the slot begins.  Keeping the
        execution context open here makes guarded tools behave identically
        whether they are launched from a button, shortcut, or context menu.
        """
        def _run(_checked=False):
            with animkey_execution("toolbar_menu", action_name):
                return callback()

        action.triggered.connect(_run)
        return action

    def _add_menu_action(self, menu, text, action_key=None):
        """Add a menu action with shortcut icon support on the right side"""
        from AnimKey.core.settings import load_shortcuts
        from AnimKey.mods import mediaMod
        import os
        
        shortcut_parts = None
        icon_path = None
        
        if action_key:
            try:
                shortcuts = load_shortcuts()
                if action_key in shortcuts and shortcuts[action_key]:
                    shortcut = shortcuts[action_key]
                    parts = shortcut.split('+')
                    last_part = parts[-1]
                    
                    ip = mediaMod.get_button_icon(last_part)
                    if ip and os.path.exists(ip):
                        shortcut_parts = parts
                        icon_path = ip
                    else:
                        shortcut_parts = parts
            except:
                pass
        
        if not icon_path:
            # Normal action
            shortcut_str = ""
            if shortcut_parts:
                shortcut_str = f"  ({'+'.join(shortcut_parts)})"
            return menu.addAction(text + shortcut_str)
            
        # Create a QWidgetAction to properly place the icon on the right side
        action = QtWidgets.QWidgetAction(menu)
        
        class ShortcutWidget(QtWidgets.QWidget):
            def __init__(self, action_ref):
                super().__init__()
                self.action_ref = action_ref
                
            def mouseReleaseEvent(self, event):
                if event.button() == QtCore.Qt.LeftButton:
                    self.action_ref.trigger()
                    # Close the parent menu if it exists
                    parent = self.parentWidget()
                    while parent:
                        if isinstance(parent, QtWidgets.QMenu):
                            parent.close()
                            break
                        parent = parent.parentWidget()
                super().mouseReleaseEvent(event)
                
            def enterEvent(self, event):
                # When hovered, tell the menu this is the active action
                parent = self.parentWidget()
                while parent:
                    if isinstance(parent, QtWidgets.QMenu):
                        parent.setActiveAction(self.action_ref)
                        break
                    parent = parent.parentWidget()
                super().enterEvent(event)
                
        # We create a custom widget that mimics a QMenu item
        widget = ShortcutWidget(action)
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(20, 6, 20, 6) # Standard Maya menu padding
        layout.setSpacing(4)
        
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        
        # Action text (left)
        lbl_text = QtWidgets.QLabel(text)
        lbl_text.setStyleSheet(f"color: {theme['text_primary']}; background: transparent;")
        lbl_text.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(lbl_text)
        
        layout.addStretch()
        
        # Shortcut text & icon (right)
        shortcut_layout = QtWidgets.QHBoxLayout()
        shortcut_layout.setSpacing(2)
        
        mod_text = "+".join(shortcut_parts[:-1])
        if mod_text:
            lbl_mod = QtWidgets.QLabel(f"({mod_text} + ")
        else:
            lbl_mod = QtWidgets.QLabel("(")
        lbl_mod.setStyleSheet("color: #888; background: transparent;")
        shortcut_layout.addWidget(lbl_mod)
        
        lbl_icon = QtWidgets.QLabel()
        pixmap = QtGui.QPixmap(icon_path).scaled(14, 14, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        lbl_icon.setPixmap(pixmap)
        lbl_icon.setStyleSheet("background: transparent;")
        shortcut_layout.addWidget(lbl_icon)
        
        lbl_close = QtWidgets.QLabel(")")
        lbl_close.setStyleSheet("color: #888; background: transparent;")
        shortcut_layout.addWidget(lbl_close)
        
        layout.addLayout(shortcut_layout)
        
        # Make the widget react to hover like a normal menu item
        widget.setObjectName("shortcut_widget")
        widget.setStyleSheet(f'''
            QWidget#shortcut_widget {{ background-color: transparent; border-radius: 3px; }}
            QWidget#shortcut_widget:hover {{ background-color: {theme["button_hover"]}; }}
        ''')
        
        # Ensure clicks on the labels pass through to the widget
        lbl_text.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        lbl_mod.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        lbl_icon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        lbl_close.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        
        action.setDefaultWidget(widget)
        menu.addAction(action)
        return action

    def _create_workspace(self):
        """Create the workspace control and build the UI"""
        
        if cmds.workspaceControl(WORKSPACE_NAME, query=True, exists=True):
            try:
                cmds.workspaceControl(WORKSPACE_NAME, edit=True, restore=True)
            except Exception:
                cmds.workspaceControl(WORKSPACE_NAME, edit=True, visible=True)
            QtCore.QTimer.singleShot(0, self._refresh_toolbar_scroll_area)
            return
        
        # Create workspace control
        cmds.workspaceControl(
            WORKSPACE_NAME,
            label="AnimKey",
            minimumWidth=220,
            initialWidth=220,
            heightProperty="fixed",
            initialHeight=int(TOOLBAR_HEIGHT * float(self.config.get("toolbar_scale", 1.0))),
            retain=False,
            floating=False,
            closeCommand='python("from AnimKey.mods import uiMod; uiMod.cleanup_animkey_runtime()")',
            dockToMainWindow=(DEFAULT_DOCK_AREA, True)
        )
        
        # Build the UI
        self._build_ui()

    def _refresh_toolbar_scroll_area(self):
        """Refresh pan filters and content width after toolbar layout changes."""
        if not hasattr(self, "toolbar_scroll_area") or not hasattr(self, "content_widget"):
            return
        try:
            layout = self.content_widget.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            self.content_widget.updateGeometry()
            self.toolbar_scroll_area._install_pan_filter(self.content_widget)
            self.toolbar_scroll_area._update_content_width()
            self.toolbar_scroll_area.updateGeometry()
            self.toolbar_scroll_area.update()
            self.content_widget.update()
        except Exception:
            pass

    def _connect_slider_geometry_refresh(self, slider):
        """Refresh the custom toolbar scroller while a slider expands/collapses."""
        if slider is None or not hasattr(slider, "geometryChanged"):
            return
        try:
            slider.geometryChanged.connect(self._refresh_toolbar_scroll_area)
            slider.geometryChanged.connect(lambda: QtCore.QTimer.singleShot(0, self._refresh_toolbar_scroll_area))
        except Exception:
            pass
    
    def _build_ui(self):
        """Build the main toolbar UI"""
        theme = ThemeManager.get_current_theme()
        
        # Get the workspace control as Qt widget
        workspace_ptr = mui.MQtUtil.findControl(WORKSPACE_NAME)
        workspace_widget = wrapInstance(int(workspace_ptr), QtWidgets.QWidget)
        
        # Clear existing layout
        if workspace_widget.layout():
            QtWidgets.QWidget().setLayout(workspace_widget.layout())
        
        # Root layout hosts a custom pan viewport. The inner content keeps every
        # toolbar element as a direct child so drag-reorder still works.
        root_layout = QtWidgets.QHBoxLayout(workspace_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll_height = int(TOOLBAR_HEIGHT * float(self.config.get("toolbar_scale", 1.0)))

        self.toolbar_scroll_area = ToolbarHorizontalScroller(workspace_widget)
        self.toolbar_scroll_area.setFixedHeight(scroll_height)
        self.toolbar_scroll_area.setStyleSheet(f'''
            QFrame#AnimKeyToolbarPanStrip {{
                background-color: {theme["bg_primary"]};
                border: none;
            }}
            QWidget#toolbar_content {{
                background-color: {theme["bg_primary"]};
            }}
        ''')

        self.content_widget = QtWidgets.QWidget()
        self.content_widget.setObjectName("toolbar_content")
        self.content_widget.setFixedHeight(scroll_height)
        self.content_widget.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        main_layout = QtWidgets.QHBoxLayout(self.content_widget)
        main_layout.setContentsMargins(6, 4, 6, 4)
        main_layout.setSpacing(2)
        self.toolbar_scroll_area.set_pan_widget(self.content_widget)
        root_layout.addWidget(self.toolbar_scroll_area)
        
        # Apply background color
        workspace_widget.setStyleSheet(f'''
            QWidget {{
                background-color: {theme["bg_primary"]};
            }}
        ''')
        
        # ─────────────────────────────────────────────────────────────────────
        # SECTION 1: Keyframe Controls Container
        # ─────────────────────────────────────────────────────────────────────
        keyframe_container = QtWidgets.QWidget()
        keyframe_container.setObjectName("keyframe_controls")
        kf_layout = QtWidgets.QHBoxLayout(keyframe_container)
        kf_layout.setContentsMargins(0, 0, 0, 0)
        kf_layout.setSpacing(2)
        
        increase_widget = self._create_increase_values_block()
        self._increase_values_widget = increase_widget
        kf_layout.addWidget(increase_widget)
        
        move_keys_widget = self._create_move_keys_block()
        self._move_keys_widget = move_keys_widget
        kf_layout.addWidget(move_keys_widget)
        
        self._keyframe_widget = keyframe_container
        main_layout.addWidget(keyframe_container)
        
        # Separator
        main_layout.addWidget(ui.AnimKeySeparator("vertical"))
        
        # ─────────────────────────────────────────────────────────────────────
        # SECTION 2: Tween/Blend Slider (UNIFIED)
        # ─────────────────────────────────────────────────────────────────────
        tween_widget = self._create_tween_slider()
        tween_widget.setObjectName("tween_slider")
        self._tween_widget = tween_widget
        main_layout.addWidget(tween_widget)
        
        # Separator
        main_layout.addWidget(ui.AnimKeySeparator("vertical"))
        
        # ─────────────────────────────────────────────────────────────────────
        # SECTION 3: Curve Tools Slider
        # ─────────────────────────────────────────────────────────────────────
        curve_widget = self._create_curve_slider()
        curve_widget.setObjectName("curve_slider")
        self._curve_widget = curve_widget
        main_layout.addWidget(curve_widget)
        
        # Separator
        main_layout.addWidget(ui.AnimKeySeparator("vertical"))
        
        # ─────────────────────────────────────────────────────────────────────
        # SECTION 4: Tool Buttons (added DIRECTLY to main_layout)
        # ─────────────────────────────────────────────────────────────────────
        self._add_tool_buttons_to_layout(main_layout)
        
        # Stretch to push right-side items
        main_layout.addStretch()
        
        # ─────────────────────────────────────────────────────────────────────
        # SECTION 5: Settings items (BRUSH, CRASH, Settings) added directly
        # ─────────────────────────────────────────────────────────────────────
        self._add_settings_items_to_layout(main_layout)

        # ─────────────────────────────────────────────────────────────────────
        # Apply initial slider visibility from workspace config (no animation)
        # ─────────────────────────────────────────────────────────────────────
        try:
            from AnimKey.mods import configMod as cfg
            _ws = cfg.load_workspace()
            _sliders = _ws.get("sliders", {})
            for _key, _widget in [
                ("tween_slider",      self._tween_widget),
                ("curve_slider",      self._curve_widget),
                ("keyframe_controls", self._keyframe_widget),
            ]:
                if not _sliders.get(_key, True):
                    _widget.setVisible(False)
                    _widget.setMaximumWidth(0)
            
            if "order" in _ws and _ws["order"]:
                self._apply_layout_order(main_layout, _ws["order"])
            self.content_widget.adjustSize()
        except Exception:
            pass

        # ─────────────────────────────────────────────────────────────────────
        # Enable Drag-and-Drop Reordering on the ENTIRE main_layout
        # ─────────────────────────────────────────────────────────────────────

        # ─────────────────────────────────────────────────────────────────────
        # Enable Drag-and-Drop Reordering on the ENTIRE main_layout
        # ─────────────────────────────────────────────────────────────────────
        self._drag_manager = ui.DragDropReorderManager(main_layout)
        self._drag_manager.pan_scroll_area = self.toolbar_scroll_area
        self._drag_manager.reorderFinished.connect(
            lambda: self._save_layout_order(main_layout)
        )
        self._drag_manager.reorderFinished.connect(self._refresh_toolbar_scroll_area)
        self.toolbar_scroll_area._install_pan_filter(self.content_widget)
        self.toolbar_scroll_area._update_content_width()
        QtCore.QTimer.singleShot(0, self._refresh_toolbar_scroll_area)
        QtCore.QTimer.singleShot(250, self._refresh_toolbar_scroll_area)
        QtCore.QTimer.singleShot(750, self._refresh_toolbar_scroll_area)
    
    def _save_layout_order(self, layout):
        """Persist the current widget order to workspace.json after a drag-reorder."""
        try:
            order = []
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget() and item.widget().objectName():
                    order.append(item.widget().objectName())
            if order:
                from AnimKey.mods import configMod as cfg
                ws = cfg.load_workspace()
                ws["order"] = order
                cfg.save_workspace(ws)
        except Exception:
            pass
    
    # ═══════════════════════════════════════════════════════════════════════════
    #                           UI SECTIONS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _apply_layout_order(self, main_layout, order):
        widget_map = {}
        for i in range(main_layout.count()):
            w = main_layout.itemAt(i).widget()
            if w and w.objectName():
                name = w.objectName()
                if name == "separator":
                    widget_map.setdefault(name, []).append(w)
                else:
                    widget_map[name] = w
        
        widgets_to_insert = []

        def append_widget(name):
            if name == "SETTINGS_SEPARATOR":
                return
            if name == "SETTINGS" and "SETTINGS_SEPARATOR" in widget_map:
                settings_separator = widget_map["SETTINGS_SEPARATOR"]
                if settings_separator not in widgets_to_insert:
                    widgets_to_insert.append(settings_separator)
            if name == "separator" and "separator" in widget_map and widget_map["separator"]:
                widgets_to_insert.append(widget_map["separator"].pop(0))
            elif name in widget_map and widget_map[name] not in widgets_to_insert:
                widgets_to_insert.append(widget_map[name])

        legacy_tangent_inserted = False
        legacy_tangent_names = ("TANGENT_TOGGLE", "PLT", "STP", "FLT", "LIN", "CLP", "SPL", "AUT")
        for name in order:
            if name == "tangent_buttons":
                if legacy_tangent_inserted:
                    continue
                legacy_tangent_inserted = True
                for tangent_name in legacy_tangent_names:
                    append_widget(tangent_name)
                continue
            append_widget(name)
        
        for i in range(main_layout.count()):
            w = main_layout.itemAt(i).widget()
            if w and w not in widgets_to_insert and w.objectName():
                widgets_to_insert.append(w)
        
        while main_layout.count():
            main_layout.takeAt(0)
        
        for w in widgets_to_insert:
            main_layout.addWidget(w)
        main_layout.addStretch()

    def get_layout_order(self):
        order = []
        if not hasattr(self, 'content_widget') or not self.content_widget:
            return order
        main_layout = self.content_widget.layout()
        if not main_layout:
            return order
        for i in range(main_layout.count()):
            w = main_layout.itemAt(i).widget()
            if w and w.objectName():
                order.append(w.objectName())
        return order
    
    def _create_increase_values_block(self):
        """Create the increase/decrease values block (- [spinbox] +)"""
        scale = float(self.config.get("toolbar_scale", 1.0))
        theme = ThemeManager.get_current_theme()
        from AnimKey.mods import mediaMod as media
        
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        # Decrease button (-)
        btn_minus = ui.AnimKeyButton("", icon=media.get_icon("animkey_btn_Decrease_128.png"), button_type="small")
        btn_minus.setFixedSize(int(26 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        btn_minus.setToolTip(ui.create_tooltip_text(
            "Decrease Values",
            "Decrease keyframe or attribute values by the specified amount"
        ))
        btn_minus.clicked.connect(self._remove_inbetween)
        layout.addWidget(btn_minus)
        
        # Value amount spinbox
        self.frame_count_spinbox = QtWidgets.QDoubleSpinBox()
        self.frame_count_spinbox.setFixedSize(int(65 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        self.frame_count_spinbox.setMinimum(0.001)
        self.frame_count_spinbox.setMaximum(100.0)
        self.frame_count_spinbox.setValue(0.001)
        self.frame_count_spinbox.setSingleStep(0.001)
        self.frame_count_spinbox.setDecimals(3)
        self.frame_count_spinbox.setStyleSheet(f'''
            QDoubleSpinBox {{
                color: {theme["text_primary"]};
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }}
            QDoubleSpinBox:focus {{
                border-color: {theme["accent_primary"]};
            }}
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                background-color: {theme["button_bg"]};
                border: none;
                width: 16px;
            }}
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
                background-color: {theme["button_hover"]};
            }}
        ''')
        self.frame_count_spinbox.setToolTip(ui.create_tooltip_text(
            "Value Amount",
            "Amount to increase or decrease keyframe/attribute values"
        ))
        layout.addWidget(self.frame_count_spinbox)
        
        # Increase button (+)
        btn_plus = ui.AnimKeyButton("", icon=media.get_icon("animkey_btn_Increase_128.png"), button_type="small")
        btn_plus.setFixedSize(int(26 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        btn_plus.setToolTip(ui.create_tooltip_text(
            "Increase Values",
            "Increase keyframe or attribute values by the specified amount"
        ))
        btn_plus.clicked.connect(self._add_inbetween)
        layout.addWidget(btn_plus)
        
        return widget
    
    def _create_move_keys_block(self):
        """Create the move keys block (< [spinbox] >)"""
        theme = ThemeManager.get_current_theme()
        from AnimKey.mods import mediaMod as media
        
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        # Move key left button (<)
        btn_left = ui.AnimKeyButton("", icon=media.get_icon("animkey_btn_LeftKeys_128.png"), button_type="small")
        btn_left.setFixedSize(int(26 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        btn_left.setToolTip(ui.create_tooltip_text(
            "Move Keys Left",
            "On a keyed frame, move that key left by the frame amount. Otherwise pull the nearest key on the right.",
            {"Shift+Click": "Move selected/current keys left by 5x the frame amount"}
        ))
        btn_left.clicked.connect(lambda: self._move_keyframes(-1))
        layout.addWidget(btn_left)
        
        # Key offset spinbox
        self.key_offset_spinbox = QtWidgets.QSpinBox()
        self.key_offset_spinbox.setFixedSize(int(45 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        self.key_offset_spinbox.setMinimum(1)
        self.key_offset_spinbox.setMaximum(100)
        self.key_offset_spinbox.setValue(1)
        self.key_offset_spinbox.setStyleSheet(f'''
            QSpinBox {{
                color: {theme["text_primary"]};
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 3px;
                padding: 2px 4px;
                font-size: 11px;
            }}
            QSpinBox:focus {{
                border-color: {theme["accent_primary"]};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {theme["button_bg"]};
                border: none;
                width: 14px;
            }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
                background-color: {theme["button_hover"]};
            }}
        ''')
        self.key_offset_spinbox.setToolTip(ui.create_tooltip_text(
            "Key Offset Frames",
            "Shift-click nudge amount for the arrow buttons"
        ))
        layout.addWidget(self.key_offset_spinbox)
        
        # Move key right button (>)
        btn_right = ui.AnimKeyButton("", icon=media.get_icon("animkey_btn_RightKeys_128.png"), button_type="small")
        btn_right.setFixedSize(int(26 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        btn_right.setToolTip(ui.create_tooltip_text(
            "Move Keys Right",
            "On a keyed frame, move that key right by the frame amount. Otherwise pull the nearest key on the left.",
            {"Shift+Click": "Move selected/current keys right by 5x the frame amount"}
        ))
        btn_right.clicked.connect(lambda: self._move_keyframes(1))
        layout.addWidget(btn_right)
        
        return widget
    
    def _create_tween_slider(self):
        """Create the unified Tween/Blend/Push-Pull slider with AnimKey-style design"""
        theme = ThemeManager.get_current_theme()
        
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        # Define slider mode options with colors (name, description, color)
        self._tween_mode_options = [
            ("Tweener", "Interpolate between keyframes", QColor(0, 200, 255)),          # Cyan
            ("Tweener World Space", "Tween in world space", QColor(0, 200, 255)),       # Cyan
            ("Blend to Buffer", "Blend to stored buffer", QColor(130, 80, 255)),        # Purple
            ("Blend to Default", "Blend to default values", QColor(130, 80, 255)),      # Purple
            ("Blend to Ease", "Blend with easing curve", QColor(180, 100, 255)),        # Light Purple
            ("Blend to Frame", "Blend to specific frame", QColor(255, 150, 50)),        # Orange
            ("Blend to Frame World Space", "Blend to frame (world)", QColor(255, 150, 50)),  # Orange
            ("Blend to Neighbors", "Blend to neighbors", QColor(100, 220, 130)),        # Green
            ("Blend to Neighbors World Space", "Blend to neighbors (world)", QColor(100, 220, 130)),  # Green
            ("Blend to Infinity", "Blend to infinity values", QColor(100, 180, 255)),   # Light Blue
            ("Blend to Infinity World Space", "Blend to infinity (world)", QColor(100, 180, 255)),  # Light Blue
            ("Blend to Undo", "Blend to previous state", QColor(255, 100, 150)),        # Pink
            ("Push/Pull", "Push or pull animation values", QColor(100, 220, 130)),      # Green
        ]
        
        # Modern dropdown label (AnimKey style) - compact
        self.tween_mode_dropdown = ui.DropdownLabel("Tweener", self._tween_mode_options)
        self.tween_mode_dropdown.setFixedWidth(120)
        self.tween_mode_dropdown.optionSelected.connect(self._on_tween_mode_change_new)
        layout.addWidget(self.tween_mode_dropdown)
        
        # AnimKey-style slider with illuminated dots - compact
        self.tween_slider = ui.AnimKeySlider(
            min_val=0,
            max_val=100,
            origin=50,
            return_to_origin=True
        )
        self.tween_slider.setMinimumWidth(int(200 * float(self.config.get("toolbar_scale", 1.0))))  # Compact for toolbar
        self.tween_slider.set_accent_color(QColor(0, 200, 255))  # Cyan for Tweener
        self._connect_slider_geometry_refresh(self.tween_slider)
        
        # Store original/default value for reset
        self._tween_slider_original_value = 50  # Default for Tweener mode
        
        # Connect slider signals
        self.tween_slider.sliderPressed.connect(self._on_tween_press)
        self.tween_slider.valueChanged.connect(self._on_tween_change)
        self.tween_slider.sliderReleased.connect(self._on_tween_release_new)
        
        layout.addWidget(self.tween_slider)
        
        # Update tooltip based on mode
        self._update_tween_tooltip()
        
        return widget
    
    def _create_curve_slider(self):
        """Create the curve manipulation slider with AnimKey-style design"""
        theme = ThemeManager.get_current_theme()
        
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        # Define curve mode options with colors
        self._curve_mode_options = [
            ("Smooth/Rough", "Smooth or rough animation curves", QColor(100, 220, 130)),  # Green
            ("Wave", "Add wave motion", QColor(0, 200, 255)),                     # Cyan
            ("Scale", "Scale keyframe values", QColor(255, 200, 100)),            # Yellow
            ("Linear", "Convert to linear", QColor(130, 80, 255)),                # Purple
            ("Flat", "Flatten values to average", QColor(255, 100, 150)),         # Pink
            ("Ease In/Out", "Apply ease curves", QColor(255, 150, 50)),           # Orange
            ("Noise", "Add noise to curves", QColor(100, 180, 255)),              # Light Blue
        ]
        
        # Modern dropdown label (AnimKey style) - compact
        self.curve_mode_dropdown = ui.DropdownLabel("Smooth/Rough", self._curve_mode_options)
        self.curve_mode_dropdown.setFixedWidth(110)
        self.curve_mode_dropdown.optionSelected.connect(self._on_curve_mode_change_new)
        layout.addWidget(self.curve_mode_dropdown)
        
        # AnimKey-style slider - compact, sin botones de porcentaje
        self.curve_slider = ui.AnimKeySlider(
            min_val=-100,
            max_val=100,
            origin=0,
            return_to_origin=True,
            show_dots=False  # Sin botones de porcentaje
        )
        self.curve_slider.setMinimumWidth(int(180 * float(self.config.get("toolbar_scale", 1.0))))  # Compact for toolbar
        self.curve_slider.set_accent_color(QColor(100, 220, 130))  # Green for Smooth
        self._connect_slider_geometry_refresh(self.curve_slider)
        
        # Connect slider signals
        self.curve_slider.sliderPressed.connect(self._on_curve_press)
        self.curve_slider.valueChanged.connect(self._on_curve_change)
        self.curve_slider.sliderReleased.connect(self._on_curve_release_new)
        
        # Store current curve mode
        self._current_curve_mode = "Smooth/Rough"
        
        layout.addWidget(self.curve_slider)
        
        return widget

    def _create_tangent_buttons_section(self, tangent_buttons, create_button, workspace, icon_size):
        """Create the tangent toggle button; tangent buttons are direct toolbar items."""
        theme = ThemeManager.get_current_theme()
        scale = float(self.config.get("toolbar_scale", 1.0))

        self._tangent_toggle_btn = QtWidgets.QPushButton()
        self._tangent_toggle_btn.setObjectName("TANGENT_TOGGLE")
        self._tangent_toggle_btn.setFixedSize(max(14, int(16 * scale)), icon_size)
        self._tangent_toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._tangent_toggle_btn.setToolTip(ui.create_tooltip_text(
            "Tangents",
            "Show or hide the tangent buttons"
        ))
        self._tangent_toggle_btn.setStyleSheet(f'''
            QPushButton {{
                color: #88c0d0;
                background-color: {theme["button_bg"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                font-size: 10px;
                font-weight: bold;
                padding: 0;
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
                border-color: #88c0d0;
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
        ''')
        self._tangent_toggle_btn.clicked.connect(self._toggle_tangent_buttons)
        self._tangent_button_keys = tuple(text for text, *_ in tangent_buttons)
        self._tangent_button_widgets = []
        self._tangent_icon_size = int(icon_size)
        return self._tangent_toggle_btn

    def _toggle_tangent_buttons(self):
        collapsed = not bool(config.get_setting("tangent_buttons_collapsed", False))
        self._set_tangent_buttons_collapsed(collapsed, animate=True, persist=True)

    def _restore_visible_tangent_button_widths(self):
        workspace = {}
        try:
            from AnimKey.mods import configMod as config_mod
            workspace = config_mod.load_workspace()
        except Exception:
            workspace = {}

        tangent_keys = ("PLT", "STP", "FLT", "LIN", "CLP", "SPL", "AUT")
        buttons_state = workspace.get("buttons", {})
        if not any(buttons_state.get(key, True) for key in tangent_keys):
            for key in tangent_keys:
                buttons_state[key] = True
            workspace["buttons"] = buttons_state
            try:
                from AnimKey.mods import configMod as config_mod
                config_mod.save_workspace(workspace)
            except Exception:
                pass

        icon_size = int(self.config.get("toolbar_icon_size", 28) * float(self.config.get("toolbar_scale", 1.0)))
        for key in tangent_keys:
            btn = self._button_widgets.get(key)
            if btn is None:
                continue
            if not buttons_state.get(key, True):
                continue
            try:
                btn.setVisible(True)
                btn.setMinimumWidth(0)
                btn.setMaximumWidth(icon_size)
                btn.setFixedSize(icon_size, icon_size)
                effect = btn.graphicsEffect()
                if isinstance(effect, QtWidgets.QGraphicsOpacityEffect):
                    effect.setOpacity(1.0)
            except Exception:
                pass

    def _set_tangent_buttons_collapsed(self, collapsed, animate=True, persist=True):
        button = getattr(self, "_tangent_toggle_btn", None)
        tangent_keys = tuple(getattr(self, "_tangent_button_keys", ("PLT", "STP", "FLT", "LIN", "CLP", "SPL", "AUT")))
        button_items = []
        for key in tangent_keys:
            btn = self._button_widgets.get(key)
            if btn is not None:
                button_items.append((key, btn))
        if button is None or not button_items:
            return

        button.setText(">" if collapsed else "<")
        button.setToolTip(ui.create_tooltip_text(
            "Tangents",
            "Show tangent buttons" if collapsed else "Hide tangent buttons"
        ))

        if persist:
            try:
                config.set_setting("tangent_buttons_collapsed", bool(collapsed))
            except Exception:
                pass

        workspace = {}
        try:
            from AnimKey.mods import configMod as config_mod
            workspace = config_mod.load_workspace()
        except Exception:
            workspace = {}
        buttons_state = workspace.get("buttons", {})
        icon_size = int(getattr(self, "_tangent_icon_size", 0) or self.config.get("toolbar_icon_size", 28) * float(self.config.get("toolbar_scale", 1.0)))
        visible_buttons = []
        for key, btn in button_items:
            if not buttons_state.get(key, True):
                btn.setVisible(False)
                btn.setMinimumWidth(0)
                btn.setMaximumWidth(0)
                continue
            btn.setVisible(True)
            visible_buttons.append(btn)

        old = self._btn_anims.pop("tangent_buttons_collapse", None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        old_refresh = self._btn_anims.pop("tangent_buttons_collapse_refresh", None)
        if old_refresh is not None:
            try:
                old_refresh.stop()
                old_refresh.deleteLater()
            except Exception:
                pass

        if not animate:
            for btn in visible_buttons:
                effect = btn.graphicsEffect()
                if not isinstance(effect, QtWidgets.QGraphicsOpacityEffect):
                    effect = QtWidgets.QGraphicsOpacityEffect(btn)
                    btn.setGraphicsEffect(effect)
                btn.setFixedHeight(icon_size)
                btn.setMinimumWidth(0 if collapsed else icon_size)
                btn.setMaximumWidth(0 if collapsed else icon_size)
                effect.setOpacity(0.0 if collapsed else 1.0)
            self._refresh_toolbar_scroll_area()
            return

        group = QtCore.QParallelAnimationGroup()
        for btn in visible_buttons:
            btn.setFixedHeight(icon_size)
            btn.setVisible(True)
            effect = btn.graphicsEffect()
            if not isinstance(effect, QtWidgets.QGraphicsOpacityEffect):
                effect = QtWidgets.QGraphicsOpacityEffect(btn)
                btn.setGraphicsEffect(effect)

            w_anim = QtCore.QPropertyAnimation(btn, b"maximumWidth")
            op_anim = QtCore.QPropertyAnimation(effect, b"opacity")
            w_anim.valueChanged.connect(lambda *_: self._refresh_toolbar_scroll_area())

            if collapsed:
                btn.setMinimumWidth(0)
                effect.setOpacity(1.0)
                w_anim.setDuration(260)
                w_anim.setStartValue(btn.maximumWidth() if btn.maximumWidth() > 0 else icon_size)
                w_anim.setEndValue(0)
                w_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
                op_anim.setDuration(180)
                op_anim.setStartValue(1.0)
                op_anim.setEndValue(0.0)
                op_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            else:
                btn.setMinimumWidth(0)
                btn.setMaximumWidth(0)
                effect.setOpacity(0.0)
                w_anim.setDuration(290)
                w_anim.setStartValue(0)
                w_anim.setEndValue(icon_size)
                w_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
                op_anim.setDuration(220)
                op_anim.setStartValue(0.0)
                op_anim.setEndValue(1.0)
                op_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

            group.addAnimation(w_anim)
            group.addAnimation(op_anim)

        def finish_widths():
            for btn in visible_buttons:
                btn.setFixedHeight(icon_size)
                btn.setMinimumWidth(0 if collapsed else icon_size)
                btn.setMaximumWidth(0 if collapsed else icon_size)
                effect = btn.graphicsEffect()
                if isinstance(effect, QtWidgets.QGraphicsOpacityEffect):
                    effect.setOpacity(0.0 if collapsed else 1.0)
                btn.updateGeometry()

        group.finished.connect(finish_widths)
        self._btn_anims["tangent_buttons_collapse"] = group
        group.finished.connect(self._refresh_toolbar_scroll_area)
        QtCore.QTimer.singleShot(0, self._refresh_toolbar_scroll_area)
        group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
    
    def _add_tool_buttons_to_layout(self, target_layout):
        """Add tool buttons directly to the given layout for flat drag-reorder"""
        theme = ThemeManager.get_current_theme()
        scale = float(self.config.get("toolbar_scale", 1.0))
        icon_size = int(self.config.get("toolbar_icon_size", 28) * scale)
        
        # Tool button functions are now imported at the module level to ensure scope stability
        
        # Define tool buttons with COLORS
        # Group 1: ISO, ALN, OPP, TRL
        tools_group1 = [
            ("ISO", "Isolate Selection", isolate_execute, "#5bc0be"),      # Cyan
            ("ALN", "Align Objects", align_execute, "#88c0d0"),            # Light Blue
            ("OPP", "Select Opposite", select_opposite_execute, "#d08770"), # Orange
            ("TRL", "Trail", None, "#a3be8c"),                             # Green (callback set separately)
        ]
        
        # Group 2: RST, OFF, HIR, MIR (before slider)
        tools_group2 = [
            ("RST", "Reset Values", reset_values_execute, "#ebcb8b"),      # Yellow
            ("OFF", "Animation Offset", None, "#d08770"),                  # Orange (callback set separately)
            ("HIR", "Select Hierarchy", select_hierarchy_execute, "#ebcb8b"), # Yellow - Select Hierarchy
            ("MIR", "Mirror", mirror_execute, "#81a1c1"),                  # Blue
        ]
        
        
        
        tools_after_slider = [
            ("C", "Copy Animation", copy_animation_execute, "#a3be8c"),    # Green - Copy Animation with submenu
            ("LKN", "Link Objects", link_objects_execute, "#b48ead"),      # Purple - Link Objects
            ("CAM", "Follow Cam", follow_cam_execute, "#5e81ac"),          # Dark Blue - Follow Cam
            ("WS", "World Space — Click: copy frame | Shift: paste | Ctrl: Auto | Right-click: ranges", copy_worldspace_execute, "#a3be8c"),
            ("PIV", "TEMP (Right-click: Temp Control)", temp_pivot_quick_execute, "#bf616a"),
            ("RUL", "Micro Move", None, "#ebcb8b"),                        # Yellow - Micro Move (callback set separately)
        ]
        
        tangent_buttons = [
            ("PLT", "Plateau Tangent", execute_plateau, "#88c0d0"),    # Light Blue
            ("STP", "Step Tangent", execute_step, "#bf616a"),          # Red
            ("FLT", "Flat Tangent", execute_flat, "#a3be8c"),          # Green
            ("LIN", "Linear Tangent", execute_linear, "#ebcb8b"),      # Yellow
            ("CLP", "Clamped Tangent", execute_clamped, "#d08770"),    # Orange
            ("SPL", "Spline Tangent", execute_spline, "#b48ead"),      # Purple
            ("AUT", "Auto Tangent", execute_auto, "#5e81ac"),          # Dark Blue
        ]
        
        extra_buttons = [
            ("GMB", "Gimbal Fixer", gimbal_execute, "#d08770"),       # Orange
            ("SWT", "Switcher", switcher_execute, "#5bc0be"),        # Cyan
            ("BAK", "Bake Animation", bake_execute, "#a3be8c"),       # Green
            ("RTM", "Retimer", retimer_execute, "#b48ead"),           # Purple
            ("ACL", "Animation Cleaner", cleaner_execute, "#88c0d0"), # Light Blue - Animation Cleaner
            ("BTNS", "Flash Buttons", flash_buttons_execute, "#5bc0be"), # Cyan - Flash Buttons
        ]
        
        # Import media module for button icons
        from AnimKey.mods import mediaMod as media
        
        # Helper function to create and configure buttons with icons
        def create_button(text, tooltip, callback, color):
            def picking_wrapper(cb, t=text):
                def wrapped(*args, **kwargs):
                    from AnimKey.core import settings
                    if settings.ACTIVE_PICKER:
                        # Append this button's name to the existing shortcut
                        settings.ACTIVE_PICKER.appendButtonName(t)
                        print(f"AnimKey: Added button '{t}' to shortcut → '{settings.ACTIVE_PICKER.getShortcut()}'")
                        settings.ACTIVE_PICKER.setPickingMode(False)
                        settings.ACTIVE_PICKER = None
                        return
                    
                    # Intercept button clicks with keyboard modifiers (e.g., Ctrl+Click)
                    mods = QtWidgets.QApplication.keyboardModifiers()
                    if mods != QtCore.Qt.NoModifier:
                        mod_parts = []
                        if mods & QtCore.Qt.ControlModifier: mod_parts.append('Ctrl')
                        if mods & QtCore.Qt.AltModifier: mod_parts.append('Alt')
                        if mods & QtCore.Qt.ShiftModifier: mod_parts.append('Shift')
                        if mods & QtCore.Qt.MetaModifier: mod_parts.append('Meta')
                        
                        if mod_parts:
                            mod_parts.sort()
                            shortcut_str = "+".join(mod_parts) + "+" + t
                            
                            # Check if this shortcut is mapped
                            from AnimKey.core.settings import load_shortcuts
                            from AnimKey.mods.hotkeysMod import execute_action, normalize_shortcut_string
                            shortcuts = load_shortcuts()
                            
                            # Find action by shortcut
                            action_map = {
                                normalize_shortcut_string(v): k
                                for k, v in shortcuts.items()
                                if normalize_shortcut_string(v)
                            }
                            shortcut_str = normalize_shortcut_string(shortcut_str)
                            
                            if shortcut_str in action_map:
                                action_name = action_map[shortcut_str]
                                print(f"AnimKey: Triggered '{action_name}' via button click shortcut '{shortcut_str}'")
                                execute_action(action_name)
                                return  # Successfully executed mapped action
                            else:
                                # Modifiers were pressed but not mapped to anything. Do nothing.
                                return
                                
                    if cb:
                        with animkey_execution("toolbar", t):
                            return cb(*args, **kwargs)
                return wrapped

            # Get icon path for this button
            icon_path = media.get_button_icon(text)
            
            # Always create the button first
            btn = QtWidgets.QPushButton()
            btn.setObjectName(text)
            btn.setText(text)  # Set text as fallback
            
            # Try to set icon if available
            if icon_path and os.path.exists(icon_path):
                icon = QtGui.QIcon(icon_path)
                if not icon.isNull():
                    btn.setIcon(icon)
                    btn.setIconSize(QtCore.QSize(icon_size - 4, icon_size - 4))
                    btn.setText("")  # Clear text if icon loaded successfully
            
            btn.setFixedSize(icon_size, icon_size)
            btn.setToolTip(tooltip)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            
            # Special handling for TRL button (trail)
            if text == "TRL":
                trail_btn = btn
                btn.clicked.connect(picking_wrapper(lambda checked=False, b=trail_btn: trail_execute(button=b)))
                if has_active_trail():
                    set_trail_button_active(btn, True)
            # Special handling for OFF button (animation offset)
            elif text == "OFF":
                offset_btn = btn
                btn.clicked.connect(picking_wrapper(lambda checked=False, b=offset_btn: anim_offset_execute(button=b)))
                if has_active_offset():
                    set_offset_button_active(btn, True)
            # Special handling for RUL button (micro move)
            elif text == "RUL":
                micro_move_btn = btn
                btn.clicked.connect(picking_wrapper(lambda checked=False, b=micro_move_btn: micro_move_execute(button=b)))
                if is_micro_move_active():
                    set_micro_move_button_active(btn, True)
            # Special handling for PIV button (temp control)
            elif text == "PIV":
                piv_btn = btn
                btn.clicked.connect(
                    picking_wrapper(
                        lambda checked=False, b=piv_btn: temp_pivot_quick_execute(button=b)
                    )
                )
                if is_temp_pivot_active():
                    set_temp_pivot_button_active(piv_btn, True)
            # Special handling for C button (copy animation) – pass button ref for popup anchoring
            elif text == "C":
                copy_btn = btn
                btn.clicked.connect(picking_wrapper(lambda checked=False, b=copy_btn: copy_animation_execute(button=b)))
            # Special handling for tools that need button ref for popup anchoring
            elif text in ["GMB", "SWT", "BAK", "RTM", "ACL"]:
                tool_btn = btn
                btn.clicked.connect(picking_wrapper(lambda checked=False, b=tool_btn, cb=callback: cb(button=b)))
            else:
                if callback:
                    btn.clicked.connect(picking_wrapper(callback))
            
            # Context menus
            if text == "ISO":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_isolate_context_menu(button, pos)
                )
            elif text == "ALN":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_align_context_menu(button, pos)
                )
                original_mouse_press = btn.mousePressEvent

                def align_mouse_press_event(event, button=btn, original=original_mouse_press):
                    if event.button() == QtCore.Qt.RightButton:
                        self._show_align_context_menu(button, event.pos())
                        event.accept()
                        return
                    original(event)

                btn.mousePressEvent = align_mouse_press_event
            elif text == "RST":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_reset_values_context_menu(button, pos)
                )
            elif text == "OPP":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_select_opposite_context_menu(button, pos)
                )
            elif text == "MIR":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_mirror_context_menu(button, pos)
                )
            elif text == "C":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_copy_animation_context_menu(button, pos)
                )
            elif text == "CAM":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_follow_cam_context_menu(button, pos)
                )
            elif text == "LKN":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_link_objects_context_menu(button, pos)
                )
            elif text == "WS":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_worldspace_context_menu(button, pos)
                )
            elif text == "BTNS":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_flash_buttons_context_menu(button, pos)
                )
            elif text == "RUL":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_micro_move_context_menu(button, pos)
                )
            elif text == "PIV":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: temp_pivot_execute(button=button)
                )
            elif text == "TRL":
                btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn: self._show_trail_context_menu(button, pos)
                )
                original_mouse_press = btn.mousePressEvent

                def trail_mouse_press_event(event, button=btn, original=original_mouse_press):
                    if event.button() == QtCore.Qt.RightButton:
                        self._show_trail_context_menu(button, event.pos())
                        event.accept()
                        return
                    original(event)

                btn.mousePressEvent = trail_mouse_press_event
            
            # Apply style with icon support
            btn.setStyleSheet(f'''
                QPushButton {{
                    background-color: {theme["button_bg"]};
                    border: 1px solid {theme["border_color"]};
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: {theme["button_hover"]};
                    border-color: {color};
                }}
                QPushButton:pressed {{
                    background-color: {theme["button_pressed"]};
                }}
            ''')

            if text == "PIV" and is_temp_pivot_active():
                set_temp_pivot_button_active(btn, True)
            
            # Register in the button widget map for live hide/show
            self._button_widgets[text] = btn
            
            return btn
        
        # Load workspace visibility settings
        from AnimKey.mods import configMod as config_mod
        workspace = config_mod.load_workspace()
        
        # ─────────────────────────────────────────────────────────────────────
        # Group 1: ISO, ALN, OPP, TRL
        # ─────────────────────────────────────────────────────────────────────
        for text, tooltip, callback, color in tools_group1:
            btn = create_button(text, tooltip, callback, color)
            target_layout.addWidget(btn)
            if not workspace["buttons"].get(text, True):
                btn.setVisible(False)
                btn.setMaximumWidth(0)
        
        # Separator between groups
        target_layout.addWidget(ui.AnimKeySeparator("vertical"))
        
        # ─────────────────────────────────────────────────────────────────────
        # Group 2: OFF, RST, TMP, HIR, MIR
        # ─────────────────────────────────────────────────────────────────────
        for text, tooltip, callback, color in tools_group2:
            btn = create_button(text, tooltip, callback, color)
            target_layout.addWidget(btn)
            if not workspace["buttons"].get(text, True):
                btn.setVisible(False)
                btn.setMaximumWidth(0)
        
        # ─────────────────────────────────────────────────────────────────────
        # Mirror Blend Slider
        # ─────────────────────────────────────────────────────────────────────
        from AnimKey.sliders.mirror_blend import prepare_mirror_blend_data, execute as mirror_blend_execute, reset as mirror_blend_reset
        
        # Wrap label + slider in a single widget so it can be toggled as one unit
        mirror_blend_label = ui.DropdownLabel("Mirror Blend", [("Mirror Blend", "Blend between current and mirrored pose", QColor(129, 161, 193))])
        mirror_blend_label.setFixedWidth(100)
        
        self.mirror_blend_slider = ui.AnimKeySlider(
            min_val=0,
            max_val=100,
            origin=0,
            return_to_origin=True,
            show_dots=False
        )
        self.mirror_blend_slider.setMinimumWidth(150)
        self.mirror_blend_slider.set_accent_color(QColor(129, 161, 193))
        self._connect_slider_geometry_refresh(self.mirror_blend_slider)
        self.mirror_blend_slider.setToolTip(ui.create_tooltip_text(
            "Mirror Blend",
            "Blend between current pose and mirrored pose",
            {"0%": "Current pose", "50%": "Half mirrored", "100%": "Fully mirrored"}
        ))
        self.mirror_blend_slider.sliderPressed.connect(self._on_mirror_blend_press)
        self.mirror_blend_slider.valueChanged.connect(self._on_mirror_blend_change)
        self.mirror_blend_slider.sliderReleased.connect(self._on_mirror_blend_release)
        
        mirror_widget = QtWidgets.QWidget()
        mirror_widget.setObjectName("mirror_slider")
        mirror_layout = QtWidgets.QHBoxLayout(mirror_widget)
        mirror_layout.setContentsMargins(0, 0, 0, 0)
        mirror_layout.setSpacing(4)
        mirror_layout.addWidget(mirror_blend_label)
        mirror_layout.addWidget(self.mirror_blend_slider)
        self._mirror_blend_widget = mirror_widget   # keep ref for workspace toggle
        target_layout.addWidget(mirror_widget)
        
        # Apply initial visibility from workspace config
        if not workspace.get("sliders", {}).get("mirror_slider", True):
            mirror_widget.setVisible(False)
            mirror_widget.setMaximumWidth(0)
        
        # Separator after mirror blend slider
        target_layout.addWidget(ui.AnimKeySeparator("vertical"))
        
        # ─────────────────────────────────────────────────────────────────────
        # Selector Button (shows count of selected objects)
        # ─────────────────────────────────────────────────────────────────────
        self.selector_button = QtWidgets.QPushButton("0")
        self.selector_button.setObjectName("SELECTOR")
        self.selector_button.setFixedSize(int(32 * float(self.config.get("toolbar_scale", 1.0))), int(28 * float(self.config.get("toolbar_scale", 1.0))))
        self.selector_button.setStyleSheet(f'''
            QPushButton {{
                color: {theme["text_primary"]};
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
                border-color: {theme["accent_primary"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
        ''')
        self.selector_button.setToolTip(ui.create_tooltip_text(
            "Selector",
            "Shows the number of selected objects. Click to open the selector window.",
            {"Click": "Open selector window"}
        ))
        self.selector_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.selector_button.clicked.connect(self._open_selector_window)
        target_layout.addWidget(self.selector_button)
        # Register in button widgets so workspace can toggle it
        self._button_widgets["SELECTOR"] = self.selector_button
        if not workspace["buttons"].get("SELECTOR", True):
            self.selector_button.setVisible(False)
            self.selector_button.setMaximumWidth(0)
        
        # Setup scriptJob to update selector button text
        self._setup_selector_scriptjob()
        
        # Separator after selector button
        target_layout.addWidget(ui.AnimKeySeparator("vertical"))
        
        # ─────────────────────────────────────────────────────────────────────
        # Group 3: After slider - C, LKN, CAM, WS, PIV, RUL
        # ─────────────────────────────────────────────────────────────────────
        for text, tooltip, callback, color in tools_after_slider:
            btn = create_button(text, tooltip, callback, color)
            target_layout.addWidget(btn)
            if not workspace["buttons"].get(text, True):
                btn.setVisible(False)
                btn.setMaximumWidth(0)
        
        # Separator before tangent buttons
        target_layout.addWidget(ui.AnimKeySeparator("vertical"))
        
        # ─────────────────────────────────────────────────────────────────────
        # Group 4: Tangent buttons - PLT, STP, FLT, LIN, CLP, SPL, AUT
        # ─────────────────────────────────────────────────────────────────────
        tangent_toggle = self._create_tangent_buttons_section(
            tangent_buttons,
            create_button,
            workspace,
            icon_size
        )
        target_layout.addWidget(tangent_toggle)

        tangent_keys = [text for text, *_ in tangent_buttons]
        tangent_visibility = {
            text: workspace["buttons"].get(text, True)
            for text in tangent_keys
        }
        if not any(tangent_visibility.values()):
            for text in tangent_keys:
                tangent_visibility[text] = True
                workspace["buttons"][text] = True
            try:
                from AnimKey.mods import configMod as config_mod
                config_mod.save_workspace(workspace)
            except Exception:
                pass

        self._tangent_button_widgets = []
        for text, tooltip, callback, color in tangent_buttons:
            btn = create_button(text, tooltip, callback, color)
            target_layout.addWidget(btn)
            self._tangent_button_widgets.append(btn)
            if not tangent_visibility.get(text, True):
                btn.setVisible(False)
                btn.setMaximumWidth(0)

        self._tangent_buttons_natural_width = max(
            1,
            sum(btn.sizeHint().width() or icon_size for btn in self._tangent_button_widgets)
            + max(0, len(self._tangent_button_widgets) - 1) * target_layout.spacing()
        )
        self._set_tangent_buttons_collapsed(
            bool(config.get_setting("tangent_buttons_collapsed", False)),
            animate=False,
            persist=False
        )
        
        # Separator before extra tools
        target_layout.addWidget(ui.AnimKeySeparator("vertical"))
        
        # ─────────────────────────────────────────────────────────────────────
        # Group 5: Extra tools - GMB, SWT, BAK, RTM, BTNS
        for text, tooltip, callback, color in extra_buttons:
            btn = create_button(text, tooltip, callback, color)
            target_layout.addWidget(btn)
            self._button_widgets[text] = btn
            if not workspace["buttons"].get(text, True):
                btn.setVisible(False)
                btn.setMaximumWidth(0)
        
    def _add_settings_items_to_layout(self, target_layout):
        """Add settings items directly to the given layout for flat drag-reorder"""
        theme = ThemeManager.get_current_theme()
        
        # Load workspace for visibility
        from AnimKey.mods import configMod as config_mod
        workspace = config_mod.load_workspace()

        def run_guarded(action_name, callback):
            with animkey_execution("toolbar", action_name):
                return callback()

        # Separator between Flash Buttons and the right-side utility buttons.
        target_layout.addWidget(ui.AnimKeySeparator("vertical"))

        # Selection Sets button lives with the right-side utility buttons.
        sets_btn = QtWidgets.QPushButton("Sel Sets")
        sets_btn.setObjectName("SETS")
        self._apply_wide_button_icon(sets_btn, "SETS", base_width=55)

        sets_btn.setToolTip("Selection Sets - Manage quick selection groups")
        sets_btn.setCursor(QtCore.Qt.PointingHandCursor)
        sets_btn.clicked.connect(
            lambda checked=False, b=sets_btn: run_guarded("SETS", lambda: sets_execute(button=b))
        )
        sets_btn.setStyleSheet(f'''
            QPushButton {{
                background-color: {theme["button_bg"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                color: #b48ead;
                font-size: 9px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
                border-color: #b48ead;
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
        ''')

        sets_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        sets_btn.customContextMenuRequested.connect(
            lambda pos, button=sets_btn: self._show_selection_sets_context_menu(button, pos)
        )

        target_layout.addWidget(sets_btn)
        self._button_widgets["SETS"] = sets_btn
        self._install_toolbar_gif_preview(sets_btn, "Gifs_SelSets.gif")
        if not workspace["buttons"].get("SETS", True):
            sets_btn.setVisible(False)
            sets_btn.setMaximumWidth(0)
        
        # BRUSH button
        from AnimKey.buttons.brush import execute as brush_execute
        self.brush_btn = QtWidgets.QPushButton("BRUSH")
        self.brush_btn.setObjectName("BRUSH")
        self._apply_wide_button_icon(self.brush_btn, "BRUSH", base_width=50)
        self.brush_btn.setToolTip("Viewport Sketchboard")
        self.brush_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.brush_btn.clicked.connect(
            lambda checked=False: run_guarded("BRUSH", brush_execute)
        )
        self.brush_btn.setStyleSheet(f'''
            QPushButton {{
                background-color: {theme["button_bg"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                color: {theme["text_primary"]};
                font-size: 10px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
                border-color: {theme["accent_primary"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
        ''')
        target_layout.addWidget(self.brush_btn)
        
        self._button_widgets["BRUSH"] = self.brush_btn
        if not workspace["buttons"].get("BRUSH", True):
            self.brush_btn.setVisible(False)
            self.brush_btn.setMaximumWidth(0)

        # ACR (Anim Crash) button with blinking capability
        from AnimKey.buttons.animCrash import execute as anim_crash_execute, RecoverySystem
        self.crash_btn = QtWidgets.QPushButton("CRASH")
        self.crash_btn.setObjectName("ACR")
        self._apply_wide_button_icon(self.crash_btn, "ACR", base_width=50)

        self.crash_btn.setToolTip("Animation Recovery - Save and restore animation checkpoints")
        self.crash_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.crash_btn.clicked.connect(
            lambda checked=False, b=self.crash_btn: run_guarded("ACR", lambda: anim_crash_execute(button=b))
        )
        
        self._crash_theme = theme
        self._crash_style_off = f'''
            QPushButton {{
                background-color: {theme["button_bg"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                color: #bf616a;
                font-size: 9px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
                border-color: #bf616a;
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
        '''
        dot_size = max(7, int(8 * float(self.config.get("toolbar_scale", 1.0))))
        self._crash_dot_style_off = f'''
            QLabel {{
                min-width: {dot_size}px;
                max-width: {dot_size}px;
                min-height: {dot_size}px;
                max-height: {dot_size}px;
                border-radius: {dot_size // 2}px;
                border: 1px solid transparent;
                background-color: transparent;
            }}
        '''
        self._crash_dot_style_on = f'''
            QLabel {{
                min-width: {dot_size}px;
                max-width: {dot_size}px;
                min-height: {dot_size}px;
                max-height: {dot_size}px;
                border-radius: {dot_size // 2}px;
                border: 1px solid #d8f3b4;
                background-color: #a3be8c;
            }}
        '''
        self._crash_dot_style_dim = f'''
            QLabel {{
                min-width: {dot_size}px;
                max-width: {dot_size}px;
                min-height: {dot_size}px;
                max-height: {dot_size}px;
                border-radius: {dot_size // 2}px;
                border: 1px solid #5f7452;
                background-color: #41543a;
            }}
        '''
        
        self.crash_btn.setStyleSheet(self._crash_style_off)
        target_layout.addWidget(self.crash_btn)
        self.crash_indicator_dot = QtWidgets.QLabel()
        self.crash_indicator_dot.setObjectName("ACR_STATUS_DOT")
        self.crash_indicator_dot.setFixedSize(dot_size, dot_size)
        self.crash_indicator_dot.setToolTip(
            "AnimCrash is active; waiting for the first checkpoint"
        )
        self.crash_indicator_dot.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.crash_indicator_dot.setStyleSheet(self._crash_dot_style_off)
        target_layout.addWidget(self.crash_indicator_dot, 0, QtCore.Qt.AlignVCenter)
        
        self._button_widgets["ACR"] = self.crash_btn
        if not workspace["buttons"].get("ACR", True):
            self.crash_btn.setVisible(False)
            self.crash_btn.setMaximumWidth(0)
            self.crash_indicator_dot.setVisible(False)
            self.crash_indicator_dot.setMaximumWidth(0)
        
        self._blink_timer = QtCore.QTimer()
        self._blink_timer.timeout.connect(self._toggle_crash_blink)
        self._blink_state = False
        
        try:
            RecoverySystem.ensure_preferred_state()
            if RecoverySystem.is_active():
                self._start_crash_blink()
        except Exception:
            pass

        settings_separator = ui.AnimKeySeparator("vertical")
        settings_separator.setObjectName("SETTINGS_SEPARATOR")
        target_layout.addWidget(settings_separator)
        
        # Settings button
        btn_settings = QtWidgets.QPushButton("\u2699 Settings")
        btn_settings.setObjectName("SETTINGS")
        btn_settings.setFixedSize(int(80 * float(self.config.get("toolbar_scale", 1.0))), int(28 * float(self.config.get("toolbar_scale", 1.0))))
        btn_settings.setStyleSheet(f'''
            QPushButton {{
                color: {theme["text_secondary"]};
                background-color: {theme["button_bg"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                font-size: 11px;
                padding: 2px 8px;
            }}
            QPushButton:hover {{
                background-color: {theme["button_hover"]};
                color: {theme["text_primary"]};
            }}
            QPushButton:pressed {{
                background-color: {theme["button_pressed"]};
            }}
        ''')
        btn_settings.setToolTip("Open AnimKey Settings")
        btn_settings.clicked.connect(self._open_settings)
        target_layout.addWidget(btn_settings)
    
    

    def _start_crash_blink(self):
        """Start the CRASH status-dot blink."""
        if not hasattr(self, 'crash_indicator_dot') or not self.crash_indicator_dot:
            return
        try:
            self._blink_timer.start(800)
            self._blink_state = True
            if hasattr(self, 'crash_btn') and self.crash_btn:
                self.crash_btn.setStyleSheet(self._crash_style_off)
                self.crash_btn.setToolTip(
                    "AnimCrash is active; waiting for the first checkpoint"
                )
            self.crash_indicator_dot.setStyleSheet(self._crash_dot_style_on)
        except RuntimeError:
            pass
    
    def _stop_crash_blink(self):
        """Stop the CRASH status-dot blink."""
        try:
            self._blink_timer.stop()
        except:
            pass
        self._blink_state = False
        if hasattr(self, 'crash_btn') and self.crash_btn:
            try:
                self.crash_btn.setStyleSheet(self._crash_style_off)
            except RuntimeError:
                pass
        if hasattr(self, 'crash_indicator_dot') and self.crash_indicator_dot:
            try:
                self.crash_indicator_dot.setStyleSheet(self._crash_dot_style_off)
                self.crash_indicator_dot.setToolTip("AnimCrash recovery is off")
            except RuntimeError:
                pass
        if hasattr(self, 'crash_btn') and self.crash_btn:
            try:
                self.crash_btn.setToolTip("AnimCrash recovery is off")
            except RuntimeError:
                pass
    
    def _toggle_crash_blink(self):
        """Toggle the CRASH status-dot blink state."""
        if not hasattr(self, 'crash_indicator_dot') or not self.crash_indicator_dot:
            self._blink_timer.stop()
            return
        try:
            self._blink_state = not self._blink_state
            if self._blink_state:
                self.crash_indicator_dot.setStyleSheet(self._crash_dot_style_on)
            else:
                self.crash_indicator_dot.setStyleSheet(self._crash_dot_style_dim)
        except RuntimeError:
            self._blink_timer.stop()
    
    def update_recovery_indicator(self, is_active):
        """Update the CRASH button blink based on recovery system status"""
        if is_active:
            self._start_crash_blink()
        else:
            self._stop_crash_blink()

    def update_recovery_checkpoint(self, filepath):
        """Expose the last completed write instead of only showing power."""
        if not filepath:
            return
        tooltip = "AnimCrash active\nLast checkpoint: {}\n{}".format(
            time.strftime("%H:%M:%S"), filepath
        )
        try:
            # Blinking only means "waiting for the first real file".  Once a
            # checkpoint exists, keep a steady dot and no visual timer.
            self._blink_timer.stop()
            self._blink_state = True
            if hasattr(self, "crash_indicator_dot") and self.crash_indicator_dot:
                self.crash_indicator_dot.setStyleSheet(
                    self._crash_dot_style_on
                )
                self.crash_indicator_dot.setToolTip(tooltip)
            if hasattr(self, "crash_btn") and self.crash_btn:
                self.crash_btn.setToolTip(tooltip)
        except RuntimeError:
            pass
    
    # ═══════════════════════════════════════════════════════════════════════════
    #                           CALLBACK METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _move_keyframes(self, direction):
        """Pull the nearest neighboring key to the current frame."""
        try:
            mods = mel.eval('getModifiers')
            shift_pressed = bool(mods % 2)
            if shift_pressed:
                self._nudge_keyframes_relative(direction)
                return

            from AnimKey.buttons.keyframe_move import move_key_with_arrow

            frame_amount = int(self.key_offset_spinbox.value())
            result = move_key_with_arrow(direction, frame_amount=frame_amount)
            moved = result.get("moved", 0)
            mode = result.get("mode")
            if moved:
                if mode in ("current", "selected"):
                    signed_amount = frame_amount * direction
                    cmds.inViewMessage(
                        amg=(
                            f"<span style='color:#88c0d0'>Moved {moved} key(s) "
                            f"by {signed_amount} frame(s)</span>"
                        ),
                        pos='topCenter', fade=True, fadeStayTime=1000
                    )
                else:
                    side = "right" if direction < 0 else "left"
                    current_time = cmds.currentTime(query=True)
                    cmds.inViewMessage(
                        amg=(
                            f"<span style='color:#88c0d0'>Pulled {moved} key(s) "
                            f"from the {side} to frame {current_time:g}</span>"
                        ),
                        pos='topCenter', fade=True, fadeStayTime=1000
                    )
            else:
                side = "right" if direction < 0 else "left"
                cmds.warning(f"AnimKey: No keyframes found on the {side} side of the current frame")
        
        except Exception as e:
            cmds.warning(f"AnimKey: Error moving keyframes: {e}")

    def _nudge_keyframes_relative(self, direction):
        """Move selected/current keys by the spinbox amount for legacy shift-click use."""
        frame_amount = int(self.key_offset_spinbox.value()) * direction * 5

        selection = cmds.ls(selection=True)
        if not selection:
            cmds.warning("AnimKey: No objects selected")
            return

        selected_curves = cmds.keyframe(query=True, name=True, selected=True)

        if selected_curves:
            cmds.keyframe(edit=True, relative=True, timeChange=frame_amount)
            cmds.inViewMessage(
                amg=f"<span style='color:#88c0d0'>Keys moved by {frame_amount} frame(s)</span>",
                pos='topCenter', fade=True, fadeStayTime=1000
            )
            return

        current_time = cmds.currentTime(query=True)
        keys_found = False
        for obj in selection:
            keyframes = cmds.keyframe(obj, query=True, time=(current_time, current_time))
            if keyframes:
                keys_found = True
                cmds.keyframe(
                    obj,
                    edit=True,
                    time=(current_time, current_time),
                    relative=True,
                    timeChange=frame_amount,
                )

        if keys_found:
            cmds.inViewMessage(
                amg=f"<span style='color:#88c0d0'>Keys at frame {int(current_time)} moved by {frame_amount}</span>",
                pos='topCenter', fade=True, fadeStayTime=1000
            )
        else:
            cmds.warning("AnimKey: No keyframes selected or at current time")
    
    def _add_inbetween(self):
        """Increase keyframe/attribute values (like AnimKey increase)"""
        from AnimKey.buttons.increase_decrease import execute as increase_decrease_execute
        amount = self.frame_count_spinbox.value()
        increase_decrease_execute(amount, increase=True)
    
    def _remove_inbetween(self):
        """Decrease keyframe/attribute values (like AnimKey decrease)"""
        from AnimKey.buttons.increase_decrease import execute as increase_decrease_execute
        amount = self.frame_count_spinbox.value()
        increase_decrease_execute(amount, increase=False)
    
    
    def _select_all_animation(self):
        """Select all animation curves"""
        print("Selecting all animation")
    
    # ═══════════════════════════════════════════════════════════════════════════
    #                           SELECTOR METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _setup_selector_scriptjob(self):
        """Setup scriptJob to update selector button when selection changes"""
        # Kill existing scriptJob if any
        if AnimKeyToolbar._selector_scriptjob is not None:
            try:
                cmds.scriptJob(kill=AnimKeyToolbar._selector_scriptjob, force=True)
            except:
                pass
        
        def update_selector_button():
            if hasattr(self, 'selector_button') and self.selector_button:
                try:
                    selected_objects = cmds.ls(selection=True)
                    num_selected = len(selected_objects)
                    self.selector_button.setText(str(num_selected))
                except:
                    pass
        
        AnimKeyToolbar._selector_scriptjob = cmds.scriptJob(
            event=["SelectionChanged", update_selector_button],
            protected=True
        )
        
        # Initial update
        update_selector_button()
    
    def _open_selector_window(self):
        """Open the selector window showing selected objects"""
        window_name = "AnimKey_SelectorWindow"
        
        # Delete existing window if it exists
        if cmds.window(window_name, exists=True):
            cmds.deleteUI(window_name)
        
        # Get current selection
        selected_objects = cmds.ls(selection=True)
        sorted_objects = sorted(selected_objects)
        
        # Create window
        window = cmds.window(
            window_name,
            title="Selector",
            widthHeight=(230, 365),
            sizeable=False
        )
        
        cmds.columnLayout(adjustableColumn=True)
        
        # Create text scroll list
        object_list = cmds.textScrollList(
            numberOfRows=30,
            allowMultiSelection=True,
            width=100,
            height=332
        )
        
        # Add objects to list
        if sorted_objects:
            cmds.textScrollList(object_list, edit=True, append=sorted_objects)
        
        # Reload button
        def reload_list(*args):
            current_selection = cmds.ls(selection=True)
            sorted_selection = sorted(current_selection)
            cmds.textScrollList(object_list, edit=True, removeAll=True)
            if sorted_selection:
                cmds.textScrollList(object_list, edit=True, append=sorted_selection)
        
        # Select from list callback
        def select_from_list(*args):
            selected_items = cmds.textScrollList(object_list, query=True, selectItem=True)
            if selected_items:
                cmds.select(selected_items, replace=True)
        
        cmds.textScrollList(object_list, edit=True, selectCommand=select_from_list)
        
        reload_button = cmds.button(
            label="Reload",
            width=100,
            height=30,
            command=reload_list
        )
        
        cmds.showWindow(window)
    
    def _update_tween_tooltip(self):
        """Update the tween slider tooltip based on current mode"""
        mode = self.tween_mode_dropdown.current_option if hasattr(self, 'tween_mode_dropdown') else "Tweener"
        
        tooltips = {
            "Tweener": ("Tweener", "Blend between previous and next keyframes", {"0%": "Previous key", "50%": "Middle", "100%": "Next key"}),
            "Tweener World Space": ("Tweener World Space", "Tween between previous and next keyframe in world space"),
            "Blend to Buffer": ("Blend to Buffer", "Blend to a stored buffer state"),
            "Blend to Default": ("Blend to Default", "Blend to default attribute values"),
            "Blend to Ease": ("Blend to Ease", "Blend to ease in/out curve"),
            "Blend to Frame": ("Blend to Frame", "Blend to a specific frame"),
            "Blend to Frame World Space": ("Blend to Frame World Space", "Blend to frame in world space"),
            "Blend to Neighbors": ("Blend to Neighbors", "Blend to neighboring keyframes"),
            "Blend to Neighbors World Space": ("Blend to Neighbors World Space", "Blend to neighbors in world space"),
            "Blend to Infinity": ("Blend to Infinity", "Blend to infinity curve values"),
            "Blend to Infinity World Space": ("Blend to Infinity World Space", "Blend to infinity in world space"),
            "Blend to Undo": ("Blend to Undo", "Blend to previous undo state"),
            "Push/Pull": ("Push/Pull", "Pull keys toward linear spacing or push them away", {"Left": "Pull to line", "Right": "Push away"})
        }
        
        if mode in tooltips:
            tooltip_data = tooltips[mode]
            if len(tooltip_data) == 3:
                tooltip = ui.create_tooltip_text(tooltip_data[0], tooltip_data[1], tooltip_data[2])
            else:
                tooltip = ui.create_tooltip_text(tooltip_data[0], tooltip_data[1])
        else:
            tooltip = ui.create_tooltip_text(mode, f"{mode} slider functionality")
        
        if hasattr(self, 'tween_slider'):
            self.tween_slider.setToolTip(tooltip)
    
    def _on_tween_mode_change_new(self, mode):
        """Handle tween mode change from new dropdown"""
        
        # Update slider range, default value, and color based on mode
        if mode == "Tweener" or mode == "Tweener World Space":
            self.tween_slider.setMinimum(0)
            self.tween_slider.setMaximum(100)
            self.tween_slider.setValue(50)
            self.tween_slider.origin = 50
            self._tween_slider_original_value = 50
            color = QColor(0, 200, 255)  # Cyan like original
        elif mode == "Push/Pull":
            self.tween_slider.setMinimum(-100)
            self.tween_slider.setMaximum(100)
            self.tween_slider.setValue(0)
            self.tween_slider.origin = 0
            self._tween_slider_original_value = 0
            color = QColor(100, 220, 130)  # Green
        elif mode == "Blend to Ease":
            self.tween_slider.setMinimum(-98)
            self.tween_slider.setMaximum(98)
            self.tween_slider.setValue(0)
            self.tween_slider.origin = 0
            self._tween_slider_original_value = 0
            color = QColor(180, 100, 255)  # Default purple
            for opt in self._tween_mode_options:
                if opt[0] == mode:
                    color = opt[2]
                    break
        else:  # All blend modes: -100 to 100 with origin at 0
            self.tween_slider.setMinimum(-100)
            self.tween_slider.setMaximum(100)
            self.tween_slider.setValue(0)
            self.tween_slider.origin = 0
            self._tween_slider_original_value = 0
            # Get color from options
            color = QColor(180, 100, 255)  # Default purple
            for opt in self._tween_mode_options:
                if opt[0] == mode:
                    color = opt[2]
                    break
        
        # Update slider accent color
        self.tween_slider.set_accent_color(color)
        
        # Update tooltip
        self._update_tween_tooltip()
    
    def _sync_animation_offset_after_slider(self, force=False):
        """Immediately propagate slider-made key changes through Animation Offset."""
        try:
            if not is_offset_active():
                return

            now = time.time()
            if not force:
                last_sync = getattr(self, "_last_offset_slider_sync", 0.0)
                if now - last_sync < 0.08:
                    return

            self._last_offset_slider_sync = now
            anim_offset_adjust_keyframes(scan_changed_keys=True)
        except Exception as e:
            print(f"Error syncing Animation Offset after slider: {e}")

    def _on_tween_change(self, value):
        """Handle tween slider value change based on mode"""
        mode = self.tween_mode_dropdown.current_option if hasattr(self, 'tween_mode_dropdown') else "Tweener"
        
        from AnimKey.sliders.tweener import execute as tweener_execute
        from AnimKey.sliders.tweener_world_space import execute as tweener_ws_execute
        from AnimKey.sliders.blend_to_buffer import execute as blend_buffer_execute
        from AnimKey.sliders.blend_to_default import execute as blend_default_execute
        from AnimKey.sliders.blend_to_ease import execute as blend_ease_execute
        from AnimKey.sliders.blend_to_frame import execute as blend_frame_execute
        from AnimKey.sliders.blend_to_frame_world_space import execute as blend_frame_ws_execute
        from AnimKey.sliders.blend_to_neighbors import execute as blend_neighbors_execute
        from AnimKey.sliders.blend_to_neighbors_world_space import execute as blend_neighbors_ws_execute
        from AnimKey.sliders.blend_to_infinity import execute as blend_infinity_execute
        from AnimKey.sliders.blend_to_infinity_world_space import execute as blend_infinity_ws_execute
        from AnimKey.sliders.blend_to_undo import execute as blend_undo_execute
        from AnimKey.sliders.push_pull import execute as push_pull_execute
        
        try:
            if mode == "Tweener":
                # Match Animo's responsive preview: suppress intermediate
                # viewport paints while all selected curves are updated, then
                # let Maya draw the completed slider tick once.
                cmds.refresh(suspend=True)
                try:
                    tweener_execute(value)
                finally:
                    cmds.refresh(suspend=False)
            elif mode == "Tweener World Space":
                tweener_ws_execute(value)
            elif mode == "Blend to Buffer":
                blend_buffer_execute(value)
            elif mode == "Blend to Default":
                blend_default_execute(value)
            elif mode == "Blend to Ease":
                blend_ease_execute(value)
            elif mode == "Blend to Frame":
                blend_frame_execute(value)
            elif mode == "Blend to Frame World Space":
                blend_frame_ws_execute(value)
            elif mode == "Blend to Neighbors":
                blend_neighbors_execute(value)
            elif mode == "Blend to Neighbors World Space":
                blend_neighbors_ws_execute(value)
            elif mode == "Blend to Infinity":
                blend_infinity_execute(value)
            elif mode == "Blend to Infinity World Space":
                blend_infinity_ws_execute(value)
            elif mode == "Blend to Undo":
                blend_undo_execute(value)
            elif mode == "Push/Pull":
                push_pull_execute(value)
        except Exception as e:
            print(f"Error in slider: {e}")
        else:
            self._slider_graph_view_sync.refresh()
    
    def _on_tween_press(self):
        """Handle tween slider press - prepare data cache before any changes"""
        self._slider_graph_view_sync.begin()
        # Store the current value as original before user starts dragging
        self._tween_slider_original_value = self.tween_slider.value()
        
        # Prepare data cache based on current mode
        mode = self.tween_mode_dropdown.current_option if hasattr(self, 'tween_mode_dropdown') else "Tweener"
        
        from AnimKey.sliders.tweener import prepare_tween_data as prep_tween
        from AnimKey.sliders.tweener_world_space import prepare_tween_data as prep_tween_ws
        from AnimKey.sliders.blend_to_buffer import prepare_blend_data as prep_buffer
        from AnimKey.sliders.blend_to_default import prepare_blend_data as prep_default
        from AnimKey.sliders.blend_to_ease import prepare_blend_data as prep_ease
        from AnimKey.sliders.blend_to_frame import prepare_blend_data as prep_frame
        from AnimKey.sliders.blend_to_frame_world_space import prepare_blend_data as prep_frame_ws
        from AnimKey.sliders.blend_to_neighbors import prepare_blend_data as prep_neighbors
        from AnimKey.sliders.blend_to_neighbors_world_space import prepare_blend_data as prep_neighbors_ws
        from AnimKey.sliders.blend_to_infinity import prepare_blend_data as prep_infinity
        from AnimKey.sliders.blend_to_infinity_world_space import prepare_blend_data as prep_infinity_ws
        from AnimKey.sliders.blend_to_undo import prepare_blend_data as prep_undo
        from AnimKey.sliders.push_pull import prepare_push_pull_data as prep_push
        
        try:
            if mode == "Tweener":
                prep_tween()
            elif mode == "Tweener World Space":
                prep_tween_ws()
            elif mode == "Blend to Buffer":
                prep_buffer()
            elif mode == "Blend to Default":
                prep_default()
            elif mode == "Blend to Ease":
                prep_ease()
            elif mode == "Blend to Frame":
                prep_frame()
            elif mode == "Blend to Frame World Space":
                prep_frame_ws()
            elif mode == "Blend to Neighbors":
                prep_neighbors()
            elif mode == "Blend to Neighbors World Space":
                prep_neighbors_ws()
            elif mode == "Blend to Infinity":
                prep_infinity()
            elif mode == "Blend to Infinity World Space":
                prep_infinity_ws()
            elif mode == "Blend to Undo":
                prep_undo()
            elif mode == "Push/Pull":
                prep_push()
        except Exception as e:
            print(f"Error preparing slider data: {e}")
    
    def _on_tween_release_new(self, final_value):
        """Handle tween slider release - apply final value, create keyframes (slider auto-returns to origin)"""
        mode = self.tween_mode_dropdown.current_option if hasattr(self, 'tween_mode_dropdown') else "Tweener"
        
        from AnimKey.sliders.tweener import execute as exe_tween, reset as res_tween
        from AnimKey.sliders.tweener_world_space import execute as exe_tween_ws, reset as res_tween_ws
        from AnimKey.sliders.blend_to_buffer import execute as exe_buffer, reset as res_buffer
        from AnimKey.sliders.blend_to_default import execute as exe_default, reset as res_default
        from AnimKey.sliders.blend_to_ease import execute as exe_ease, reset as res_ease
        from AnimKey.sliders.blend_to_frame import execute as exe_frame, reset as res_frame
        from AnimKey.sliders.blend_to_frame_world_space import execute as exe_frame_ws, reset as res_frame_ws
        from AnimKey.sliders.blend_to_neighbors import execute as exe_neighbors, reset as res_neighbors
        from AnimKey.sliders.blend_to_neighbors_world_space import execute as exe_neighbors_ws, reset as res_neighbors_ws
        from AnimKey.sliders.blend_to_infinity import execute as exe_infinity, reset as res_infinity
        from AnimKey.sliders.blend_to_infinity_world_space import execute as exe_infinity_ws, reset as res_infinity_ws
        from AnimKey.sliders.blend_to_undo import execute as exe_undo, reset as res_undo
        from AnimKey.sliders.push_pull import execute as exe_push, reset as res_push
        
        # IMPORTANT: Apply the final slider value and create keyframes
        try:
            if mode == "Tweener":
                cmds.refresh(suspend=True)
                try:
                    exe_tween(final_value)
                    res_tween()
                finally:
                    cmds.refresh(suspend=False)
            elif mode == "Tweener World Space":
                exe_tween_ws(final_value)
                res_tween_ws()
            elif mode == "Blend to Buffer":
                exe_buffer(final_value)
                res_buffer()
            elif mode == "Blend to Default":
                exe_default(final_value)
                res_default()
            elif mode == "Blend to Ease":
                exe_ease(final_value)
                res_ease()
            elif mode == "Blend to Frame":
                exe_frame(final_value)
                res_frame()
            elif mode == "Blend to Frame World Space":
                exe_frame_ws(final_value)
                res_frame_ws()
            elif mode == "Blend to Neighbors":
                exe_neighbors(final_value)
                res_neighbors()
            elif mode == "Blend to Neighbors World Space":
                exe_neighbors_ws(final_value)
                res_neighbors_ws()
            elif mode == "Blend to Infinity":
                exe_infinity(final_value)
                res_infinity()
            elif mode == "Blend to Infinity World Space":
                exe_infinity_ws(final_value)
                res_infinity_ws()
            elif mode == "Blend to Undo":
                exe_undo(final_value)
                res_undo()
            elif mode == "Push/Pull":
                exe_push(final_value)
                res_push()
        except Exception as e:
            print(f"Error in slider release: {e}")
        finally:
            self._sync_animation_offset_after_slider(force=True)
            self._slider_graph_view_sync.end()
    
    def _on_curve_press(self):
        """Handle curve slider press - prepare curve data"""
        self._slider_graph_view_sync.begin()
        mode = self._current_curve_mode
        
        from AnimKey.sliders.curve_smooth import prepare_curve_data as prep_smooth
        from AnimKey.sliders.curve_wave import prepare_curve_data as prep_wave
        from AnimKey.sliders.curve_scale import prepare_curve_data as prep_scale
        from AnimKey.sliders.curve_linear import prepare_curve_data as prep_linear
        from AnimKey.sliders.curve_flat import prepare_curve_data as prep_flat
        from AnimKey.sliders.curve_ease_in_out import prepare_curve_data as prep_ease
        from AnimKey.sliders.curve_noise import prepare_curve_data as prep_noise
        
        # Import and prepare data based on mode
        if mode in ("Smooth", "Smooth/Rough"):
            prep_smooth()
        elif mode == "Wave":
            prep_wave()
        elif mode == "Scale":
            prep_scale()
        elif mode == "Linear":
            prep_linear()
        elif mode == "Flat":
            prep_flat()
        elif mode == "Ease In/Out":
            prep_ease()
        elif mode == "Noise":
            prep_noise()
    
    def _on_curve_change(self, value):
        """Handle curve slider value change"""
        mode = self._current_curve_mode
        
        from AnimKey.sliders.curve_smooth import execute as exe_smooth
        from AnimKey.sliders.curve_wave import execute as exe_wave
        from AnimKey.sliders.curve_scale import execute as exe_scale
        from AnimKey.sliders.curve_linear import execute as exe_linear
        from AnimKey.sliders.curve_flat import execute as exe_flat
        from AnimKey.sliders.curve_ease_in_out import execute as exe_ease
        from AnimKey.sliders.curve_noise import execute as exe_noise
        
        # Execute based on mode
        if mode in ("Smooth", "Smooth/Rough"):
            exe_smooth(value)
        elif mode == "Wave":
            exe_wave(value)
        elif mode == "Scale":
            exe_scale(value)
        elif mode == "Linear":
            exe_linear(value)
        elif mode == "Flat":
            exe_flat(value)
        elif mode == "Ease In/Out":
            exe_ease(value)
        elif mode == "Noise":
            exe_noise(value)
        self._slider_graph_view_sync.refresh()
    
    def _on_curve_release_new(self, final_value):
        """Handle curve slider release (slider auto-returns to origin)"""
        mode = self._current_curve_mode
        
        from AnimKey.sliders.curve_smooth import reset as res_smooth
        from AnimKey.sliders.curve_wave import reset as res_wave
        from AnimKey.sliders.curve_scale import reset as res_scale
        from AnimKey.sliders.curve_linear import reset as res_linear
        from AnimKey.sliders.curve_flat import reset as res_flat
        from AnimKey.sliders.curve_ease_in_out import reset as res_ease
        from AnimKey.sliders.curve_noise import reset as res_noise
        
        try:
            # First execute with final value, then save the keys.
            self._on_curve_change(final_value)
            if mode in ("Smooth", "Smooth/Rough"):
                res_smooth()
            elif mode == "Wave":
                res_wave()
            elif mode == "Scale":
                res_scale()
            elif mode == "Linear":
                res_linear()
            elif mode == "Flat":
                res_flat()
            elif mode == "Ease In/Out":
                res_ease()
            elif mode == "Noise":
                res_noise()
        finally:
            self._slider_graph_view_sync.end()
    
    def _on_curve_mode_change_new(self, mode):
        """Handle curve mode change from new dropdown"""
        
        # Store current mode
        self._current_curve_mode = mode
        
        # Update slider based on mode
        if mode == "Ease In/Out":
            self.curve_slider.setMinimum(-98)
            self.curve_slider.setMaximum(98)
            self.curve_slider.origin = 0
            self.curve_slider.setValue(0)
        elif mode in ("Scale", "Smooth", "Smooth/Rough"):
            self.curve_slider.setMinimum(-100)
            self.curve_slider.setMaximum(100)
            self.curve_slider.origin = 0
            self.curve_slider.setValue(0)
        else:
            self.curve_slider.setMinimum(0)
            self.curve_slider.setMaximum(100)
            self.curve_slider.origin = 0
            self.curve_slider.setValue(0)
        
        # Get color from options
        color = QColor(163, 190, 140)  # Default green
        for opt in self._curve_mode_options:
            if opt[0] == mode:
                color = opt[2]
                break
        
        # Update slider accent color
        self.curve_slider.set_accent_color(color)
    
    def _on_mirror_blend_press(self):
        """Handle mirror blend slider press - prepare mirror blend data"""
        self._slider_graph_view_sync.begin()
        from AnimKey.sliders.mirror_blend import prepare_mirror_blend_data
        prepare_mirror_blend_data()
    
    def _on_mirror_blend_change(self, value):
        """Handle mirror blend slider value change"""
        from AnimKey.sliders.mirror_blend import execute
        try:
            execute(value)
        except Exception as e:
            print(f"Error in mirror blend slider: {e}")
        else:
            self._slider_graph_view_sync.refresh()
    
    def _on_mirror_blend_release(self, final_value):
        """Handle mirror blend slider release - apply final value, create keyframes, reset slider"""
        from AnimKey.sliders.mirror_blend import execute, reset
        try:
            # Apply final value
            execute(final_value)
            # Create keyframes and reset
            reset()
        except Exception as e:
            print(f"Error in mirror blend release: {e}")
        finally:
            self._slider_graph_view_sync.end()
    
    def _open_settings(self):
        """Open the settings window"""
        from AnimKey.core.settings import show_settings
        show_settings()
    
    def _placeholder_action(self):
        """Placeholder for unimplemented button actions"""
        print("Button clicked - functionality coming soon!")

    def _show_trail_context_menu(self, button, position):
        """Show context menu for the custom motion trail button."""
        from AnimKey.buttons import trail

        settings = trail.get_trail_settings()
        theme = ThemeManager.get_current_theme()
        menu = QtWidgets.QMenu(button)
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                color: {theme["text_primary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 22px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: #a3be8c;
                color: #1b1f23;
            }}
            QMenu::item:checked {{
                color: #a3be8c;
            }}
            QMenu::separator {{
                height: 1px;
                background: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')

        def _add_trail_slider(parent_menu, label, value, minimum, maximum, step, callback):
            scale = max(1, int(round(1.0 / float(step))))
            widget = QtWidgets.QWidget(parent_menu)
            layout = QtWidgets.QHBoxLayout(widget)
            layout.setContentsMargins(10, 4, 10, 4)
            layout.setSpacing(8)

            value_label = QtWidgets.QLabel()
            value_label.setMinimumWidth(98)
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, widget)
            slider.setRange(int(round(float(minimum) * scale)), int(round(float(maximum) * scale)))
            slider.setValue(int(round(float(value) * scale)))
            slider.setMinimumWidth(145)
            slider.setFocusPolicy(QtCore.Qt.NoFocus)

            def _set_value_label(raw_value):
                numeric_value = float(raw_value) / float(scale)
                if scale == 1:
                    value_label.setText(f"{label}: {int(numeric_value)}")
                else:
                    value_label.setText(f"{label}: {numeric_value:.1f}")

            def _apply_slider(raw_value):
                _set_value_label(raw_value)
                numeric_value = float(raw_value) / float(scale)
                callback(int(numeric_value) if scale == 1 else numeric_value)

            _set_value_label(slider.value())
            slider.valueChanged.connect(_apply_slider)
            layout.addWidget(value_label)
            layout.addWidget(slider, 1)

            action = QtWidgets.QWidgetAction(parent_menu)
            action.setDefaultWidget(widget)
            parent_menu.addAction(action)
            return slider

        action_refresh = menu.addAction("Refresh Trail")
        action_refresh.triggered.connect(trail.trail_refresh)

        action_reset = menu.addAction("Reset Trail Settings")
        action_reset.triggered.connect(trail.reset_trail_settings)

        action_show_hide = menu.addAction("Show / Hide Trail")
        action_show_hide.triggered.connect(trail.trail_show_hide)

        menu.addSeparator()

        action_keys = menu.addAction("Show Key Markers")
        action_keys.setCheckable(True)
        action_keys.setChecked(bool(settings.get("show_key_markers", True)))
        action_keys.toggled.connect(trail.set_trail_key_markers)

        action_frames = menu.addAction("Show Frame Markers")
        action_frames.setCheckable(True)
        action_frames.setChecked(bool(settings.get("show_frame_markers", True)))
        action_frames.toggled.connect(trail.set_trail_frame_markers)

        action_camera_space = menu.addAction("Camera Space")
        action_camera_space.setCheckable(True)
        action_camera_space.setChecked(bool(settings.get("camera_space", False)))
        action_camera_space.toggled.connect(trail.set_trail_camera_space)

        action_key_handles = menu.addAction("Editable Key Handles")
        action_key_handles.setCheckable(True)
        action_key_handles.setChecked(bool(settings.get("show_key_handles", False)))
        action_key_handles.toggled.connect(trail.set_trail_key_handles)

        action_tangent_handles = menu.addAction("Editable Tangent Handles")
        action_tangent_handles.setCheckable(True)
        action_tangent_handles.setChecked(bool(settings.get("show_tangent_handles", False)))
        action_tangent_handles.toggled.connect(trail.set_trail_tangent_handles)

        action_pop = menu.addAction("Show Pop Warnings")
        action_pop.setCheckable(True)
        action_pop.setChecked(bool(settings.get("show_pop_warnings", True)))
        action_pop.toggled.connect(trail.set_trail_pop_warnings)

        quality_menu = menu.addMenu("Quality")
        quality_options = [
            ("Performance (4 Frames)", "performance", 4, 1),
            ("Balanced (2 Frames)", "balanced", 2, 1),
            ("Every Frame", "frame", 1, 1),
            ("Fine (2 Dots per Frame)", "fine", 1, 2),
        ]
        current_increment = int(settings.get("trail_increment", 1))
        current_density = int(settings.get("sample_density", 1))
        for label, preset, increment, density in quality_options:
            action = quality_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(
                current_increment == increment and current_density == density
            )
            action.triggered.connect(lambda checked=False, p=preset: trail.set_trail_quality(p))

        quality_menu.addSeparator()
        _add_trail_slider(
            quality_menu,
            "Subframe Density",
            current_density,
            1,
            8,
            1,
            trail.set_trail_sample_density,
        )

        range_menu = menu.addMenu("Visible Range")
        current_display_range = int(settings.get("display_frame_range", 24))
        for label, frame_range in (
            ("+- 12 Frames", 12),
            ("+- 18 Frames", 18),
            ("+- 24 Frames", 24),
            ("+- 48 Frames", 48),
            ("Full Trail", 0),
        ):
            action = range_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current_display_range == frame_range)
            action.triggered.connect(
                lambda checked=False, value=frame_range: trail.set_trail_display_range(value)
            )

        _add_trail_slider(
            menu,
            "Line Size",
            float(settings.get("line_width", 2.0)),
            1.0,
            10.0,
            0.25,
            trail.set_trail_line_width,
        )
        _add_trail_slider(
            menu,
            "Frame Dot Size",
            float(settings.get("frame_marker_size", 3.0)),
            1.0,
            12.0,
            0.25,
            trail.set_trail_frame_marker_size,
        )
        _add_trail_slider(
            menu,
            "Key Dot Size",
            float(settings.get("marker_size", 5.5)),
            2.0,
            24.0,
            0.5,
            trail.set_trail_marker_size,
        )

        pop_menu = menu.addMenu("Pop Sensitivity")
        current_pop_threshold = float(settings.get("pop_threshold", 0.4))
        for label, threshold in (("Sensitive", 0.25), ("Normal", 0.4), ("Relaxed", 0.75)):
            action = pop_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(abs(current_pop_threshold - threshold) < 0.05)
            action.triggered.connect(lambda checked=False, t=threshold: trail.set_trail_pop_threshold(t))

        def _qcolor_from_rgb(rgb):
            try:
                return QtGui.QColor(
                    int(max(0.0, min(1.0, float(rgb[0]))) * 255),
                    int(max(0.0, min(1.0, float(rgb[1]))) * 255),
                    int(max(0.0, min(1.0, float(rgb[2]))) * 255),
                )
            except Exception:
                return QtGui.QColor(0, 200, 255)

        def _choose_trail_color(slot, label):
            current = _qcolor_from_rgb(trail.get_trail_color(slot))
            color = QtWidgets.QColorDialog.getColor(current, button, f"Trail {label} Color")
            if color.isValid():
                trail.set_trail_custom_color(slot, (color.redF(), color.greenF(), color.blueF()))

        color_menu = menu.addMenu("Color")
        color_mode_menu = color_menu.addMenu("Color Mode")
        for label, mode in (
            ("Solid", "solid"),
            ("Spectrum", "spectrum"),
            ("Warm", "warm"),
            ("Ocean", "ocean"),
            ("Candy", "candy"),
        ):
            action = color_mode_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(settings.get("color_mode", "solid") == mode)
            action.triggered.connect(
                lambda checked=False, value=mode: trail.set_trail_color_mode(value)
            )
        color_menu.addSeparator()
        for label, palette in (
            ("Animo", "animo"),
            ("Default", "cyan"),
            ("Red", "red"),
            ("Grey", "grey"),
            ("Green", "green"),
            ("Purple", "purple"),
            ("Gold", "gold"),
            ("Custom", "custom"),
        ):
            action = color_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(settings.get("palette", "cyan") == palette)
            action.triggered.connect(lambda checked=False, p=palette: trail.set_trail_palette(p))

        custom_color_menu = color_menu.addMenu("Custom Colors")
        for label, slot in (
            ("Past Trail", "past"),
            ("Future Trail", "future"),
            ("Current Frame", "current"),
            ("Key Markers", "key"),
            ("Past Keys", "past_key"),
            ("Future Keys", "future_key"),
            ("Pop Warning", "pop"),
        ):
            action = custom_color_menu.addAction(label)
            action.triggered.connect(lambda checked=False, s=slot, l=label: _choose_trail_color(s, l))

        execute_qt(menu, button.mapToGlobal(position))

    def _show_isolate_context_menu(self, button, position):
        """Show context menu for Isolate Selection button"""
        from AnimKey.buttons import isolate

        theme = ThemeManager.get_current_theme()
        menu = QtWidgets.QMenu(button)
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                color: {theme["text_primary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 22px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: #5bc0be;
                color: #1b1f23;
            }}
            QMenu::item:checked {{
                color: #5bc0be;
            }}
        ''')

        include_action = menu.addAction("Include Parented Objects")
        include_action.setCheckable(True)
        include_action.setChecked(isolate.include_parented_objects_enabled())
        include_action.setToolTip("Also isolate children/accessories parented under the selected object or rig.")
        include_action.toggled.connect(isolate.set_include_parented_objects)

        execute_qt(menu, button.mapToGlobal(position))
    
    def _show_legacy_align_context_menu(self, button, position):
        """Show context menu for Align Objects button"""
        from AnimKey.buttons.align_objects import (
            align_position,
            align_orientation,
            align_scale
        )
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        
        # Style the menu
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
        ''')
        
        # Add menu items
        action_position = self._add_menu_action(menu, "Translate", "Align Translate")
        action_orientation = self._add_menu_action(menu, "Rotate", "Align Rotate")
        action_scale = self._add_menu_action(menu, "Scale", "Align Scale")
        
        # Connect actions
        self._connect_guarded_action(action_position, "Align Translate", align_position)
        self._connect_guarded_action(action_orientation, "Align Rotate", align_orientation)
        self._connect_guarded_action(action_scale, "Align Scale", align_scale)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    
    
    
    def _show_reset_values_context_menu(self, button, position):
        """Show context menu for Reset Values button"""
        from AnimKey.buttons.resetValues import (
            save_default_values,
            remove_default_values_for_selected_object,
            restore_default_data
        )
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        
        # Style the menu
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
        ''')
        
        # Add menu items
        action_save = self._add_menu_action(menu, "Snapshot Default Values", "Snapshot Default Values")
        action_restore = self._add_menu_action(menu, "Delete Snapshot", "Delete Snapshot")
        menu.addSeparator()
        action_clear = self._add_menu_action(menu, "Clear All Saved Data", "Clear All Saved Data")
        
        # Connect actions
        self._connect_guarded_action(action_save, "Snapshot Default Values", save_default_values)
        self._connect_guarded_action(action_restore, "Delete Default Snapshot", remove_default_values_for_selected_object)
        self._connect_guarded_action(action_clear, "Clear Default Snapshots", restore_default_data)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    
    
    def _show_select_opposite_context_menu(self, button, position):
        """Show context menu for Select Opposite button"""
        from AnimKey.buttons.selectOpposite import add_select_opposite
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        
        # Style the menu
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
        ''')
        
        # Add menu item
        action_add = self._add_menu_action(menu, "Add Select Opposite", "Add Select Opposite")
        
        # Connect action
        self._connect_guarded_action(action_add, "Add Select Opposite", add_select_opposite)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    
    
    def _show_mirror_context_menu(self, button, position):
        """Show context menu for Mirror button"""
        from AnimKey.buttons.mirror import (
            all_mirror,
            quick_mirror,
            mirror_to_left,
            mirror_to_right,
            toggle_auto_mirror,
            is_auto_mirror_enabled,
            snapshot_mirror_settings,
            delete_mirror_snapshot
        )
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        
        # Style the menu
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
            QMenu::item:checked {{
                background-color: {theme["accent_primary"]};
                color: white;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')
        
        # Mirror modes section
        action_all_mirror = self._add_menu_action(menu, "All Mirror (Swap Both Sides)", "All Mirror (Swap Both Sides)")
        action_all_mirror.setToolTip("Swap values between left/right controls")

        action_mirror_left = self._add_menu_action(menu, "Mirror to Left", "Mirror to Left")
        action_mirror_left.setToolTip("Copy selected right-side controls onto the left side")
        action_mirror_right = self._add_menu_action(menu, "Mirror to Right", "Mirror to Right")
        action_mirror_right.setToolTip("Copy selected left-side controls onto the right side")

        action_quick_mirror = self._add_menu_action(
            menu, "Quick Mirror (No Snapshot)", "Quick Mirror (No Snapshot)"
        )
        action_quick_mirror.setToolTip(
            "Mirror the current pose immediately using automatic left/right detection"
        )
        
        menu.addSeparator()
        
        # Auto Mirror checkbox
        action_auto_mirror = self._add_menu_action(menu, "Auto Mirror (Real-time)", "Auto Mirror (Real-time)")
        action_auto_mirror.setCheckable(True)
        action_auto_mirror.setChecked(is_auto_mirror_enabled())
        action_auto_mirror.setToolTip("Enable real-time mirroring while moving controls")
        
        menu.addSeparator()
        
        # Snapshot section
        action_snapshot = self._add_menu_action(menu, "Snapshot", "Snapshot")
        action_snapshot.setToolTip("Calibrate and validate a reusable precise mirror profile for the selected rig")
        
        action_delete_snapshot = self._add_menu_action(menu, "Delete Snapshot", "Delete Snapshot")
        action_delete_snapshot.setToolTip("Delete mirror snapshot for current rig")
        
        # Connect actions
        self._connect_guarded_action(action_all_mirror, "All Mirror", all_mirror)
        self._connect_guarded_action(action_mirror_left, "Mirror to Left", mirror_to_left)
        self._connect_guarded_action(action_mirror_right, "Mirror to Right", mirror_to_right)
        self._connect_guarded_action(action_quick_mirror, "Quick Mirror", quick_mirror)
        self._connect_guarded_action(action_auto_mirror, "Toggle Auto Mirror", toggle_auto_mirror)
        self._connect_guarded_action(action_snapshot, "Mirror Snapshot", snapshot_mirror_settings)
        self._connect_guarded_action(action_delete_snapshot, "Delete Mirror Snapshot", delete_mirror_snapshot)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    
    
    def _show_copy_animation_context_menu(self, button, position):
        """Show context menu for Copy Animation button"""
        # All required functions (copy_animation, save_animation, etc.) are imported at module level
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        
        # Style the menu
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')
        
        # Animation section
        act_copy = self._add_menu_action(menu, "Copy Animation", "Copy Animation")
        act_copy.setToolTip("Copy animation to temporary buffer")
        
        act_save = self._add_menu_action(menu, "Save Animation", "Save Animation")
        act_save.setToolTip("Save animation permanently to the AnimKey library")
        
        menu.addSeparator()

        act_paste = self._add_menu_action(menu, "Paste Animation", "Paste Animation")
        act_paste.setToolTip("Paste animation to selected controls (replaces existing)")
        
        act_insert = self._add_menu_action(menu, "Paste Insert", "Paste Insert")
        act_insert.setToolTip("Paste animation at current time")
        
        act_opposite = self._add_menu_action(menu, "Paste Opposite", "Paste Opposite")
        act_opposite.setToolTip("Paste animation to opposite (mirror) controls")
        
        menu.addSeparator()
        
        # Pose section
        act_copy_pose = self._add_menu_action(menu, "Copy Pose", "Copy Pose")
        act_copy_pose.setToolTip("Copy current pose from selected controls")
        
        act_paste_pose = self._add_menu_action(menu, "Paste Pose", "Paste Pose")
        act_paste_pose.setToolTip("Paste pose to selected controls")
        
        menu.addSeparator()
        
        # Connect actions
        self._connect_guarded_action(act_copy, "Copy Animation", copy_animation)
        self._connect_guarded_action(act_save, "Save Animation", lambda: save_animation(button=button))
        self._connect_guarded_action(act_paste, "Paste Animation", paste_animation)
        self._connect_guarded_action(act_insert, "Paste Insert Animation", paste_insert_animation)
        self._connect_guarded_action(act_opposite, "Paste Opposite Animation", paste_opposite_animation)
        self._connect_guarded_action(act_copy_pose, "Copy Pose", copy_pose)
        self._connect_guarded_action(act_paste_pose, "Paste Pose", paste_pose)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    def _show_hierarchy_context_menu(self, button, position):
        """Show context menu for Select Hierarchy button"""
        # select_hierarchy_execute, select_rig_controls, select_animated_controls are imported at module level
        
        menu = QtWidgets.QMenu(self.window)
        menu.setStyleSheet(style.get_menu_style())
        
        act_children = menu.addAction("Select Hierarchy Controls")
        self._connect_guarded_action(act_children, "Select Hierarchy Controls", select_hierarchy_execute)
        
        menu.addSeparator()
        
        act_controls = menu.addAction("Select Rig Controls")
        self._connect_guarded_action(act_controls, "Select Rig Controls", select_rig_controls)
        
        act_animated = menu.addAction("Select Animated Controls")
        self._connect_guarded_action(act_animated, "Select Animated Controls", select_animated_controls)

        act_visible_curves = menu.addAction("Select Visible NURBS Curves")
        self._connect_guarded_action(act_visible_curves, "Select Visible NURBS Curves", select_visible_nurbs_curves)
        
        execute_qt(menu, button.mapToGlobal(position))

    def _show_align_context_menu(self, button, position):
        """Show context menu for Align button"""
        from AnimKey.buttons import align_objects
        
        theme = ThemeManager.get_current_theme()
        menu = QtWidgets.QMenu(button)
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                color: {theme["text_primary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 22px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: #5bc0be;
                color: #1b1f23;
            }}
            QMenu::item:checked {{
                color: #5bc0be;
            }}
        ''')

        channels = align_objects.get_align_channels()
        channel_items = (
            ("Translate", "translate"),
            ("Rotate", "rotate"),
            ("Scale", "scale"),
        )

        for label, channel in channel_items:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(channels.get(channel, False))
            action.toggled.connect(
                lambda checked, channel_name=channel: align_objects.set_align_channel(channel_name, checked)
            )

        menu.addSeparator()
        act_apply = menu.addAction("Apply Align")
        self._connect_guarded_action(act_apply, "Align Objects", align_objects.execute)
        
        execute_qt(menu, button.mapToGlobal(position))

    def _show_offset_context_menu(self, button, position):
        """Show context menu for Offset button"""
        # set_offset_button_active, is_offset_active are imported at module level
        
        menu = QtWidgets.QMenu(self.window)
        menu.setStyleSheet(style.get_menu_style())
        
        act_info = menu.addAction("Animation Offset Active" if has_active_offset() else "No Active Offset")
        act_info.setEnabled(False)
        
        execute_qt(menu, button.mapToGlobal(position))

    def _show_follow_cam_context_menu(self, button, position):
        """Show context menu for Follow Cam button"""
        # create_follow_cam, remove_follow_cam are imported at module level
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        
        # Style the menu
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')
        
        # Add menu items
        action_both = self._add_menu_action(menu, "Follow Translation & Rotation", "Follow Translation & Rotation")
        action_both.setToolTip("Camera follows both position and rotation")
        
        action_translation = self._add_menu_action(menu, "Follow Translation Only", "Follow Translation Only")
        action_translation.setToolTip("Camera follows position only")
        
        action_rotation = self._add_menu_action(menu, "Follow Rotation Only", "Follow Rotation Only")
        action_rotation.setToolTip("Camera follows rotation only")
        
        menu.addSeparator()
        
        action_remove = self._add_menu_action(menu, "Remove Follow Cam", "Remove Follow Cam")
        action_remove.setToolTip("Remove the follow camera")
        
        # Connect actions
        self._connect_guarded_action(
            action_both,
            "Follow Camera Translation Rotation",
            lambda: create_follow_cam(translation=True, rotation=True),
        )
        self._connect_guarded_action(
            action_translation,
            "Follow Camera Translation",
            lambda: create_follow_cam(translation=True, rotation=False),
        )
        self._connect_guarded_action(
            action_rotation,
            "Follow Camera Rotation",
            lambda: create_follow_cam(translation=False, rotation=True),
        )
        self._connect_guarded_action(action_remove, "Remove Follow Camera", remove_follow_cam)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    
    
    def _show_link_objects_context_menu(self, button, position):
        """Show context menu for Link Objects button"""
        # copy_link_frame, copy_link_playback_range, etc. are imported at module level
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        
        # Style the menu
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
            QMenu::item:checked {{
                background-color: {theme["accent_primary"]};
                color: white;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')
        
        # Copy section
        action_copy_frame = self._add_menu_action(menu, "Copy Link Frame", "Copy Link Frame")
        action_copy_frame.setToolTip("Save relationship at current frame (last selected = main)")
        
        action_copy_range = self._add_menu_action(menu, "Copy Link Playback Range", "Copy Link Playback Range")
        action_copy_range.setToolTip("Save relationship for the entire playback range")
        
        menu.addSeparator()
        
        # Paste section
        action_paste_frame = self._add_menu_action(menu, "Paste Link Frame", "Paste Link Frame")
        action_paste_frame.setToolTip("Apply relationship at current frame")
        
        action_paste_range = self._add_menu_action(menu, "Paste Link Playback Range", "Paste Link Playback Range")
        action_paste_range.setToolTip("Apply relationship to all frames in playback range")
        
        menu.addSeparator()
        
        # Auto-link
        action_auto = self._add_menu_action(menu, "Auto-Link", "Auto-Link")
        action_auto.setCheckable(True)
        action_auto.setChecked(is_auto_link_enabled())
        action_auto.setToolTip("Update object relationship in real-time")
        
        # Connect actions
        self._connect_guarded_action(action_copy_frame, "Copy Link Frame", copy_link_frame)
        self._connect_guarded_action(action_copy_range, "Copy Link Playback Range", copy_link_playback_range)
        self._connect_guarded_action(action_paste_frame, "Paste Link Frame", paste_link_frame)
        self._connect_guarded_action(action_paste_range, "Paste Link Playback Range", paste_link_playback_range)
        self._connect_guarded_action(action_auto, "Toggle Auto Link", toggle_auto_link)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    
    
    def _show_worldspace_context_menu(self, button, position):
        """Show context menu for Copy Worldspace button"""
        from AnimKey.buttons.copyWorldspace import (
            copy_worldspace_all_animation,
            copy_worldspace_selected_range,
            copy_worldspace_playback_range,
            copy_worldspace_current_frame,
            paste_worldspace_animation,
            paste_worldspace_selected_range,
            paste_worldspace_playback_range,
            paste_worldspace_current_frame,
            toggle_auto_worldspace,
            is_auto_worldspace_enabled,
        )
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        
        # Style the menu
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
            QMenu::item:checked {{
                background-color: {theme["accent_primary"]};
                color: white;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')
        
        # Copy section
        action_copy_all = self._add_menu_action(menu, "Copy Worldspace - All Animation", "Copy Worldspace All Animation")
        action_copy_all.setToolTip("Bake evaluated world matrices over the full animation range")
        
        action_copy_selected = self._add_menu_action(menu, "Copy Worldspace - Selected Timeline Range", "Copy Worldspace Selected Range")
        action_copy_selected.setToolTip("Bake evaluated world matrices over the highlighted timeline range")

        action_copy_playback = menu.addAction("Copy Worldspace - Playback Range")
        action_copy_playback.setToolTip("Bake evaluated world matrices over the playback range")
        
        action_copy_frame = self._add_menu_action(menu, "Copy Worldspace - Current Frame", "Copy Worldspace Current Frame")
        action_copy_frame.setToolTip("Copy the evaluated world matrix at the current frame")
        
        menu.addSeparator()
        
        # Paste section
        action_paste_anim = self._add_menu_action(menu, "Paste Worldspace Animation", "Paste Worldspace Animation")
        action_paste_anim.setToolTip("Paste all worldspace animation")

        action_paste_selected = menu.addAction("Paste Worldspace - Selected Timeline Range")
        action_paste_selected.setToolTip("Paste only samples inside the highlighted timeline range")
        
        action_paste_range = menu.addAction("Paste Worldspace - Playback Range") # Not in shortcuts yet
        action_paste_range.setToolTip("Paste worldspace values within playback range")
        
        action_paste_frame = self._add_menu_action(menu, "Paste Worldspace Frame", "Paste Worldspace Frame")
        action_paste_frame.setToolTip("Paste worldspace values at current frame")

        menu.addSeparator()

        action_auto = self._add_menu_action(menu, "Auto Worldspace Pin", "Toggle Auto Worldspace")
        action_auto.setToolTip("Keep copied controls fixed in world space while parents or spaces change")
        action_auto.setCheckable(True)
        action_auto.setChecked(is_auto_worldspace_enabled())
        
        # Connect actions
        self._connect_guarded_action(action_copy_all, "Copy Worldspace All", copy_worldspace_all_animation)
        self._connect_guarded_action(action_copy_selected, "Copy Worldspace Selected", copy_worldspace_selected_range)
        self._connect_guarded_action(action_copy_playback, "Copy Worldspace Playback", copy_worldspace_playback_range)
        self._connect_guarded_action(action_copy_frame, "Copy Worldspace Frame", copy_worldspace_current_frame)
        self._connect_guarded_action(action_paste_anim, "Paste Worldspace Animation", paste_worldspace_animation)
        self._connect_guarded_action(action_paste_selected, "Paste Worldspace Selected", paste_worldspace_selected_range)
        self._connect_guarded_action(action_paste_range, "Paste Worldspace Playback", paste_worldspace_playback_range)
        self._connect_guarded_action(action_paste_frame, "Paste Worldspace Frame", paste_worldspace_current_frame)
        self._connect_guarded_action(action_auto, "Toggle Auto Worldspace", toggle_auto_worldspace)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    
    
    def _show_micro_move_context_menu(self, button, position):
        """Show context menu for Micro Move precision"""
        from functools import partial
        from AnimKey.buttons.microMove import (
            get_magnitude, set_magnitude,
            execute as micro_move_execute, is_active
        )
        
        menu = QtWidgets.QMenu(button)
        theme = ThemeManager.get_current_theme()
        current_mag = get_magnitude()
        active = is_active()
        
        # Menu styling
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {theme["text_primary"]};
                padding: 8px 20px;
                border-radius: 4px;
                margin: 2px 4px;
            }}
            QMenu::item:selected {{
                background-color: {theme["button_hover"]};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')
        
        # Toggle
        toggle_text = "Disable" if active else "Enable"
        action_toggle = menu.addAction(toggle_text)
        self._connect_guarded_action(
            action_toggle,
            "Toggle Micro Move",
            partial(micro_move_execute, button=button),
        )
        
        menu.addSeparator()
        
        # Precision options (fewer)
        for mag in [2, 4, 6, 10, 20]:
            mark = "● " if mag == current_mag else "   "
            action = menu.addAction(f"{mark}{mag}x")
            self._connect_guarded_action(action, "Set Micro Move Magnitude", partial(set_magnitude, mag))
        
        menu.addSeparator()
        act_children = menu.addAction("Select Hierarchy Controls")
        self._connect_guarded_action(act_children, "Select Hierarchy Controls", select_hierarchy_execute)
        
        execute_qt(menu, button.mapToGlobal(position))
    
    def _show_selection_sets_context_menu(self, button, position):
        """Show context menu for Selection Sets button"""
        from AnimKey.mods.themes import ThemeManager
        from AnimKey.buttons.selectionSets import export_sets, import_sets, clear_all_sets
        
        theme = ThemeManager.get_current_theme()
        
        menu = QtWidgets.QMenu(button)
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px 0px;
            }}
            QMenu::item {{
                padding: 6px 20px;
                color: {theme["text_primary"]};
            }}
            QMenu::item:selected {{
                background-color: {theme["accent_primary"]};
            }}
            QMenu::separator {{
                height: 1px;
                background: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')
        
        action_export = self._add_menu_action(menu, "Export Sets...", "Export Sets...")
        action_export.setToolTip("Export all selection sets to a file")
        
        action_import = self._add_menu_action(menu, "Import Sets...", "Import Sets...")
        action_import.setToolTip("Import selection sets from a file")
        
        menu.addSeparator()
        
        action_clear = self._add_menu_action(menu, "Clear All", "Clear All")
        action_clear.setToolTip("Delete all sets and tabs to start fresh")
        
        # Connect actions
        self._connect_guarded_action(action_export, "Export Selection Sets", export_sets)
        self._connect_guarded_action(action_import, "Import Selection Sets", import_sets)
        self._connect_guarded_action(action_clear, "Clear Selection Sets", clear_all_sets)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))
    
    def _show_flash_buttons_context_menu(self, button, position):
        """Show context menu for Flash Buttons button"""
        from AnimKey.mods.themes import ThemeManager
        from AnimKey.buttons.flashbuttons import export_config, import_config
        
        theme = ThemeManager.get_current_theme()
        
        menu = QtWidgets.QMenu(button)
        menu.setStyleSheet(f'''
            QMenu {{
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 4px;
                padding: 4px 0px;
            }}
            QMenu::item {{
                padding: 6px 20px;
                color: {theme["text_primary"]};
            }}
            QMenu::item:selected {{
                background-color: {theme["accent_primary"]};
            }}
            QMenu::separator {{
                height: 1px;
                background: {theme["border_color"]};
                margin: 4px 8px;
            }}
        ''')
        
        action_export = self._add_menu_action(menu, "📤 Export Flash Buttons...", "📤 Export Flash Buttons...")
        action_export.setToolTip("Export all flash buttons configuration to a file")
        
        action_import = self._add_menu_action(menu, "📥 Import Flash Buttons...", "📥 Import Flash Buttons...")
        action_import.setToolTip("Import flash buttons configuration from a file")
        
        # Connect actions
        self._connect_guarded_action(action_export, "Export Flash Buttons", export_config)
        self._connect_guarded_action(action_import, "Import Flash Buttons", import_config)
        
        # Show menu at cursor position
        execute_qt(menu, button.mapToGlobal(position))


# ═══════════════════════════════════════════════════════════════════════════════
#                           GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════

animkey_toolbar = AnimKeyToolbar()
