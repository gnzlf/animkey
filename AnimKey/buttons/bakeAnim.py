# -*- coding: utf-8 -*-
"""
    AnimKey - Bake Animation (BakeFactory)
    
    Based on BakeFactory v1.1 by Sergey Loza
    Converted to Python with modern frameless UI.
    
    Features:
    - Bake with customizable step interval
    - Bake current timeslider or selected time range
    - Bake specific channels (if selected in Channel Box)
    - Bake channels on the active animation layer
    - Preserve Blend Parent keys
"""

import maya.cmds as cmds
from AnimKey.mods.uiMod import ContextPopupWindow
import maya.mel as mel
import maya.OpenMayaUI as mui

from AnimKey.mods.maya_compat import (
    QtCore, QtGui, QtWidgets, wrap_instance as wrapInstance,
)


WINDOW_OBJECT = "AnimKey_BakeFactory"

# Global window reference
_bake_window = None


def get_maya_main_window():
    return wrapInstance(int(mui.MQtUtil.mainWindow()), QtWidgets.QWidget)


def get_time_range():
    """Get selected time range or full playback range"""
    try:
        time_slider = mel.eval('$tmpVar=$gPlayBackSlider')
        if cmds.timeControl(time_slider, query=True, rangeVisible=True):
            range_array = cmds.timeControl(
                time_slider,
                query=True,
                rangeArray=True,
            ) or []
            if len(range_array) >= 2:
                start_time = float(range_array[0])
                # Maya returns the right boundary, one frame past the last
                # highlighted frame.
                end_time = float(range_array[1]) - 1.0
                if end_time >= start_time:
                    return start_time, end_time
    except Exception:
        pass

    # No highlighted range: preserve the original visible playback scope.
    min_time = cmds.playbackOptions(query=True, minTime=True)
    max_time = cmds.playbackOptions(query=True, maxTime=True)
    return min_time, max_time


def _bake_targets(selected, selected_channels, layer):
    """Resolve channels to the active layer's inputs, before layer blending."""
    from AnimKey.core.animation_offset_session import _layered_plug_for_attr

    targets = []
    blend_parents = []
    for obj in selected:
        if selected_channels:
            plugs = []
            for channel in selected_channels:
                if not cmds.attributeQuery(channel, node=obj, exists=True):
                    continue
                children = cmds.attributeQuery(channel, node=obj, listChildren=True)
                plugs.extend(obj + '.' + attr for attr in (children or [channel]))
        else:
            plugs = cmds.listAnimatable(obj) or []

        for plug in plugs:
            if cmds.getAttr(plug, lock=True):
                continue
            target = _layered_plug_for_attr(plug, layer) if layer else plug
            if not target:
                continue
            targets.append(target)
            if plug.rsplit('.', 1)[-1] == 'blendParent1':
                blend_parents.append(target)

    return list(dict.fromkeys(targets)), list(dict.fromkeys(blend_parents))


def constrain_save(targets):
    """Template (lock) blendParent attributes to preserve during bake"""
    for target in targets:
        cmds.selectKey(target, add=True, keyframe=True)
        try:
            mel.eval('doTemplateChannel graphEditor1FromOutliner 1')
        except Exception:
            pass


def constrain_load(targets):
    """Untemplate (unlock) blendParent attributes after bake"""
    for target in targets:
        cmds.selectKey(target, add=True, keyframe=True)
        try:
            mel.eval('doTemplateChannel graphEditor1FromOutliner 0')
        except Exception:
            pass


def blend_parent_fix(bake_step, targets, time_range):
    """Fix blendParent keys to align with bake step"""
    if not targets:
        return
    start_time, end_time = time_range
    
    # Snap keys to integers
    try:
        cmds.snapKey(targets, time=(start_time, end_time), timeMultiple=1.1)
        cmds.snapKey(targets, time=(start_time, end_time), timeMultiple=1)
    except:
        pass
    
    for target in targets:
        # Ensure at least one key exists
        key_count = cmds.keyframe(target, query=True, keyframeCount=True) or 0
        if key_count < 1:
            cmds.setKeyframe(target)

        # Get blendParent keys in range
        blend_keys = cmds.keyframe(
            target, time=(start_time, end_time), query=True,
        ) or []

        for i, key_time in enumerate(blend_keys):
            diff = (key_time - start_time) % bake_step
            if diff > 0:
                if i > 0:
                    if (key_time - blend_keys[i-1]) <= bake_step:
                        # Too close, remove
                        cmds.cutKey(target, time=(key_time, key_time))
                    else:
                        # Shift to align
                        cmds.keyframe(
                            target, time=(key_time, key_time),
                            timeChange=key_time - diff,
                        )
                else:
                    cmds.keyframe(
                        target, time=(key_time, key_time),
                        timeChange=key_time - diff,
                    )


def bake_factory_math(bake_step):
    """Main bake function - equivalent to BakeFactoryMath()"""
    from AnimKey.core.animation_offset_session import get_selected_animation_layer

    selected = cmds.ls(sl=True)
    if not selected:
        cmds.warning("Please select at least one object.")
        return False
    
    start_time, end_time = get_time_range()
    layer = get_selected_animation_layer()
    if layer and cmds.getAttr(layer + '.lock'):
        cmds.warning(f"Animation layer '{layer}' is locked. Unlock it before baking.")
        return False

    selected_channels = cmds.channelBox(
        'mainChannelBox', query=True, selectedMainAttributes=True,
    ) or []
    targets, blend_parents = _bake_targets(selected, selected_channels, layer)
    if not targets:
        cmds.warning("No bakeable channels on the active animation layer for the selected objects.")
        return False
    
    cmds.undoInfo(openChunk=True)
    
    try:
        # Fix blendParent keys
        blend_parent_fix(bake_step, blend_parents, (start_time, end_time))
        
        # Save constraints (template blendParent)
        constrain_save(blend_parents)
        
        # Clear selection
        cmds.selectKey(clear=True)
        
        # Bake the layer inputs directly. Baking the composed object values
        # into an additive/weighted layer would apply its contribution twice.
        cmds.bakeResults(
            targets,
            preserveOutsideKeys=True,
            time=(start_time, end_time),
            sampleBy=bake_step,
        )

        # bakeResults already replaces the keys inside the requested interval.
        # A global unsnapped-key cut would also erase other layers and keys
        # outside the bake range (including valid fractional-frame samples).
        cmds.selectKey(clear=True)
        
        cmds.select(selected)
        cmds.inViewMessage(amg=f"<hl>Baked</hl> (step: {bake_step})", pos='midCenter', fade=True)
        return True
        
    except Exception as e:
        cmds.warning(f"Bake error: {e}")
        return False
        
    finally:
        try:
            constrain_load(blend_parents)
            cmds.selectKey(clear=True)
        finally:
            cmds.undoInfo(closeChunk=True)


class BakeFactoryWindow(ContextPopupWindow):
    """Bake Factory Window with modern frameless design"""
    
    def __init__(self, anchor_button=None, parent=None):
        super().__init__(anchor_button=anchor_button, parent=parent)
        
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle('Bake Animation')
        self.setFixedSize(280, 180)
        
        # Frameless window
        
        
        
        self._base_opacity = 1.0
        self._hover_opacity = 1.0
        self._bake_step = 2
        self._anim = None
        
        self._setup_ui()
        self.position_window()
        self.setWindowOpacity(self._base_opacity)
    
    def _setup_ui(self):
        """Setup the UI elements"""
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, self._tail_height + 1)
        main_layout.setSpacing(0)
        main_layout.setSpacing(0)
        
        # Container frame with rounded corners
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet("""
            QFrame#mainContainer {
                background-color: transparent;
                border: none;
            }
        """)
        
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # Header
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(10, 10, 10, 10)
        self.title_label = QtWidgets.QLabel("BAKE ANIMATION")
        self.title_label.setStyleSheet("color: #AAA; font-size: 11px; font-weight: 500; border: none;")
        header.addWidget(self.title_label)
        header.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        container_layout.addLayout(header)
        
        # Content area
        content = QtWidgets.QWidget()
        content.setStyleSheet("background-color: #3a3a3a;")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(15, 20, 15, 20)
        content_layout.setSpacing(15)
        
        # Step control row
        step_widget = QtWidgets.QWidget()
        step_layout = QtWidgets.QHBoxLayout(step_widget)
        step_layout.setContentsMargins(0, 0, 0, 0)
        step_layout.setSpacing(8)
        
        step_label = QtWidgets.QLabel("STEP:")
        step_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold;")
        step_layout.addWidget(step_label)
        
        # Minus button
        self.minus_btn = QtWidgets.QPushButton("-")
        self.minus_btn.setFixedSize(30, 30)
        self.minus_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.minus_btn.setStyleSheet("""
            QPushButton {
                background-color: #5a5a5a;
                color: #AAA;
                border: 1px solid #666666;
                border-radius: 6px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #666666; color: #FFF; border-color: #666; }
        """)
        self.minus_btn.clicked.connect(self._decrease_step)
        step_layout.addWidget(self.minus_btn)
        
        # Step value display
        self.step_display = QtWidgets.QLineEdit(str(self._bake_step))
        self.step_display.setFixedSize(50, 30)
        self.step_display.setAlignment(QtCore.Qt.AlignCenter)
        self.step_display.setValidator(QtGui.QIntValidator(1, 99))
        self.step_display.setStyleSheet("""
            QLineEdit {
                background-color: #3a3a3a;
                color: #3498DB;
                border: 1px solid #5a5a5a;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }
        """)
        self.step_display.textChanged.connect(self._on_step_changed)
        step_layout.addWidget(self.step_display)
        
        # Plus button
        self.plus_btn = QtWidgets.QPushButton("+")
        self.plus_btn.setFixedSize(30, 30)
        self.plus_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.plus_btn.setStyleSheet("""
            QPushButton {
                background-color: #5a5a5a;
                color: #AAA;
                border: 1px solid #666666;
                border-radius: 6px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #666666; color: #FFF; border-color: #666; }
        """)
        self.plus_btn.clicked.connect(self._increase_step)
        step_layout.addWidget(self.plus_btn)
        
        step_layout.addStretch()
        content_layout.addWidget(step_widget)
        
        # Buttons row
        buttons_layout = QtWidgets.QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        # Bake button (green style matching retimer)
        self.bake_btn = QtWidgets.QPushButton("BAKE")
        self.bake_btn.setFixedHeight(40)
        self.bake_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.bake_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d5d3d;
                color: #a3be8c;
                border: 1px solid #5aaa5a;
                border-radius: 8px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #3d6d4d; }
            QPushButton:pressed { background-color: #254a32; }
        """)
        self.bake_btn.clicked.connect(self._do_bake)
        buttons_layout.addWidget(self.bake_btn)
        
        content_layout.addLayout(buttons_layout)
        
        # Info label
        info_label = QtWidgets.QLabel("• Selected range or full timeline")
        info_label.setStyleSheet("color: #555; font-size: 10px; margin-top: 5px;")
        content_layout.addWidget(info_label)
        
        content_layout.addStretch()
        container_layout.addWidget(content)
        main_layout.addWidget(self.container)
    
    def _decrease_step(self):
        if self._bake_step > 1:
            self._bake_step -= 1
            self.step_display.setText(str(self._bake_step))
    
    def _increase_step(self):
        if self._bake_step < 99:
            self._bake_step += 1
            self.step_display.setText(str(self._bake_step))
    
    def _on_step_changed(self, text):
        try:
            self._bake_step = max(1, int(text)) if text else 1
        except:
            self._bake_step = 1
    
    def _do_bake(self):
        bake_factory_math(self._bake_step)
    
    def enterEvent(self, e):
        self._animate(self._hover_opacity)
        super(BakeFactoryWindow, self).enterEvent(e)
        
    def leaveEvent(self, e):
        self._animate(self._base_opacity)
        super(BakeFactoryWindow, self).leaveEvent(e)
        
    def _animate(self, val):
        if self._anim is not None:
            try:
                if self._anim.state() == QtCore.QPropertyAnimation.Running:
                    self._anim.stop()
            except RuntimeError:
                self._anim = None
        self._anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(150)
        self._anim.setEndValue(val)
        self._anim.finished.connect(self._on_anim_finished)
        self._anim.start()

    def _on_anim_finished(self):
        self._anim = None
    
    def closeEvent(self, event):
        global _bake_window
        _bake_window = None
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
            self._anim = None
        super(BakeFactoryWindow, self).closeEvent(event)


def show(anchor_button=None):
    """Open the Bake Factory window"""
    global _bake_window
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows(except_widget=_bake_window)
    
    if _bake_window is not None:
        existing = uiMod.show_existing_animkey_tool_window(_bake_window, anchor_button)
        if existing is not None:
            _bake_window = existing
            return _bake_window
        _bake_window = None
    
    if cmds.window(WINDOW_OBJECT, exists=True):
        cmds.deleteUI(WINDOW_OBJECT)
    
    _bake_window = BakeFactoryWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _bake_window.show()
    _bake_window.raise_()
    return _bake_window


def execute(*args, **kwargs):
    """Main entry point for the button"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.bakeAnim.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))
