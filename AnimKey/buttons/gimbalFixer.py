"""
    AnimKey - Gimbal Fixer
    
    Allows changing the rotation order of a control without altering existing animation.
    Ideal for fixing gimbal lock issues.
    
    Modern frameless window design matching Set Manager style.
"""

import maya.cmds as cmds
from AnimKey.mods.uiMod import ContextPopupWindow
import maya.OpenMayaUI as mui

from AnimKey.mods.maya_compat import (
    QtCore, QtGui, QtWidgets, wrap_instance as wrapInstance,
)


ROTATE_ORDERS = ['xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx']
WINDOW_OBJECT = "AnimKey_GimbalFixer"

# Global window reference
_gimbal_window = None
_selection_callback_id = None


def get_maya_main_window():
    return wrapInstance(int(mui.MQtUtil.mainWindow()), QtWidgets.QWidget)


def get_gimbal_tolerance(obj, rot_order):
    """
    Calculate gimbal tolerance for a specific rotation order.
    Returns a value between 0 (best) and 1 (worst).
    """
    rx = cmds.getAttr(f'{obj}.rotateX')
    ry = cmds.getAttr(f'{obj}.rotateY')
    rz = cmds.getAttr(f'{obj}.rotateZ')
    
    middle_axis = rot_order[1]
    
    if middle_axis == 'x':
        mid_value = rx
    elif middle_axis == 'y':
        mid_value = ry
    else:
        mid_value = rz
    
    gimbal_test = abs(((mid_value + 90) % 180) - 90) / 90
    return gimbal_test


def analyze_rotation_orders(obj):
    """
    Analyze all rotation orders and return them sorted by quality.
    Returns list of tuples: (tolerance, rotation_order)
    """
    tolerances = []
    
    for rot_order in ROTATE_ORDERS:
        tolerance = get_gimbal_tolerance(obj, rot_order)
        tolerances.append((tolerance, rot_order))
    
    tolerances.sort(key=lambda x: x[0])
    return tolerances


def apply_rotation_order(obj, new_order):
    """
    Apply a new rotation order to an object, preserving animation.
    """
    if new_order not in ROTATE_ORDERS:
        cmds.warning(f'Invalid rotation order: {new_order}')
        return False
    
    current_order_idx = cmds.getAttr(f'{obj}.rotateOrder')
    current_order = ROTATE_ORDERS[current_order_idx]
    
    if new_order == current_order:
        cmds.warning(f'Already using {new_order.upper()} rotation order.')
        return False
    
    new_order_idx = ROTATE_ORDERS.index(new_order)
    rot_keys = cmds.keyframe(obj, attribute='rotate', query=True, timeChange=True)
    
    cmds.undoInfo(openChunk=True)
    
    try:
        if rot_keys:
            key_times = sorted(list(set(rot_keys)))
            current_time = cmds.currentTime(query=True)
            
            autokey_state = cmds.autoKeyframe(query=True, state=True)
            cmds.autoKeyframe(state=False)
            cmds.refresh(suspend=True)
            
            try:
                for frame in key_times:
                    cmds.currentTime(frame, edit=True)
                    cmds.setKeyframe(obj, attribute='rotate')
                
                for frame in key_times:
                    cmds.currentTime(frame, edit=True)
                    cmds.xform(obj, preserve=True, rotateOrder=new_order)
                    cmds.setKeyframe(obj, attribute='rotate')
                    cmds.setAttr(f'{obj}.rotateOrder', current_order_idx)
                
                cmds.currentTime(current_time, edit=True)
                cmds.setAttr(f'{obj}.rotateOrder', new_order_idx)
                
                try:
                    cmds.filterCurve(obj)
                except:
                    pass
                    
            finally:
                cmds.autoKeyframe(state=autokey_state)
                cmds.refresh(suspend=False)
        else:
            cmds.xform(obj, preserve=True, rotateOrder=new_order)
        
        cmds.inViewMessage(amg=f"Rotation order: <hl>{new_order.upper()}</hl>", pos='midCenter', fade=True)
        return True
        
    except Exception as e:
        cmds.warning(f"Error changing rotation order: {e}")
        return False
        
    finally:
        cmds.undoInfo(closeChunk=True)


class GimbalFixerWindow(ContextPopupWindow):
    """Gimbal Fixer Window with modern frameless design"""
    
    def __init__(self, anchor_button=None, parent=None):
        super().__init__(anchor_button=anchor_button, parent=parent)
        
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle('Gimbal Fixer')
        self.setFixedSize(320, 420)
        
        # Frameless window
        
        
        
        self._base_opacity = 0.5
        self._hover_opacity = 1.0
        
        self.quality_names = ["Best", "Good", "Moderate", "Average", "Poor", "Worst"]
        # Updated gradient: Green (Best/Good), Orange (Moderate/Average), Red (Poor/Worst)
        self.quality_colors = ["#00E676", "#66BB6A", "#FFCA28", "#FFA726", "#EF5350", "#D32F2F"]
        
        self._setup_ui()
        self.position_window()
        self._setup_selection_callback()
        
        # Initial refresh
        QtCore.QTimer.singleShot(100, self.refresh_data)
        
        # Start with base opacity
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
        self.title_label = QtWidgets.QLabel("Gimbal Fixer")
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
        content_layout.setContentsMargins(15, 10, 15, 15)
        content_layout.setSpacing(10)
        
        # Control info row
        info_widget = QtWidgets.QWidget()
        info_layout = QtWidgets.QHBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(8)
        
        control_label = QtWidgets.QLabel("Control:")
        control_label.setStyleSheet("color: #888; font-size: 11px;")
        info_layout.addWidget(control_label)
        
        self.control_edit = QtWidgets.QLineEdit("No selection")
        self.control_edit.setReadOnly(True)
        self.control_edit.setStyleSheet("""
            QLineEdit {
                background-color: #444444;
                color: #FFF;
                border: 1px solid #666666;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
            }
        """)
        info_layout.addWidget(self.control_edit)
        content_layout.addWidget(info_widget)
        
        # Current order row
        order_widget = QtWidgets.QWidget()
        order_layout = QtWidgets.QHBoxLayout(order_widget)
        order_layout.setContentsMargins(0, 0, 0, 0)
        order_layout.setSpacing(8)
        
        order_label = QtWidgets.QLabel("Current Order:")
        order_label.setStyleSheet("color: #888; font-size: 11px;")
        order_layout.addWidget(order_label)
        
        self.order_edit = QtWidgets.QLineEdit("---")
        self.order_edit.setReadOnly(True)
        self.order_edit.setFixedWidth(60)
        self.order_edit.setAlignment(QtCore.Qt.AlignCenter)
        self.order_edit.setStyleSheet("""
            QLineEdit {
                background-color: #3498DB;
                color: #FFF;
                border: none;
                border-radius: 6px;
                padding: 6px;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        order_layout.addWidget(self.order_edit)
        order_layout.addStretch()
        content_layout.addWidget(order_widget)
        
        # Separator
        line = QtWidgets.QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #666666;")
        content_layout.addWidget(line)
        
        # Instructions label
        instructions = QtWidgets.QLabel("Select Rotation Order (Best → Worst):")
        instructions.setStyleSheet("color: #AAA; font-size: 11px;")
        content_layout.addWidget(instructions)
        
        # Rotation order buttons
        self.order_buttons = []
        for i in range(6):
            btn = QtWidgets.QPushButton()
            btn.setFixedHeight(44)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            self.order_buttons.append(btn)
            content_layout.addWidget(btn)
        
        content_layout.addStretch()
        container_layout.addWidget(content)
        main_layout.addWidget(self.container)
    
    def _setup_selection_callback(self):
        """Setup Maya selection callback"""
        global _selection_callback_id
        
        self._kill_callback()
        
        _selection_callback_id = cmds.scriptJob(
            event=["SelectionChanged", self._on_selection_changed],
            killWithScene=True
        )
    
    def _kill_callback(self):
        """Kill the selection callback"""
        global _selection_callback_id
        if _selection_callback_id is not None:
            try:
                if cmds.scriptJob(exists=_selection_callback_id):
                    cmds.scriptJob(kill=_selection_callback_id, force=True)
            except:
                pass
            _selection_callback_id = None
    
    def _on_selection_changed(self):
        """Called when Maya selection changes"""
        cmds.evalDeferred(self.refresh_data, lowestPriority=True)
    
    def refresh_data(self):
        """Refresh window data based on current selection"""
        sel = cmds.ls(sl=True, transforms=True)
        
        if not sel:
            self.control_edit.setText("No selection")
            self.order_edit.setText("---")
            self.order_edit.setStyleSheet("""
                QLineEdit {
                    background-color: #666666;
                    color: #888;
                    border: none;
                    border-radius: 6px;
                    padding: 6px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
            self._disable_buttons()
            return
        
        obj = sel[0]
        
        if not cmds.attributeQuery('rotateOrder', node=obj, exists=True):
            self.control_edit.setText(obj.split(':')[-1])
            self.order_edit.setText("N/A")
            self._disable_buttons()
            return
        
        self.control_edit.setText(obj.split(':')[-1])
        
        try:
            current_order_idx = cmds.getAttr(f'{obj}.rotateOrder')
            current_order = ROTATE_ORDERS[current_order_idx]
            self.order_edit.setText(current_order.upper())
            self.order_edit.setStyleSheet("""
                QLineEdit {
                    background-color: #3498DB;
                    color: #FFF;
                    border: none;
                    border-radius: 6px;
                    padding: 6px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
        except:
            self.order_edit.setText("---")
            self._disable_buttons()
            return
        
        try:
            sorted_tolerances = analyze_rotation_orders(obj)
        except Exception as e:
            print(f"Error analyzing rotation orders: {e}")
            self._disable_buttons()
            return
        
        for i, (tolerance, order) in enumerate(sorted_tolerances):
            btn = self.order_buttons[i]
            percentage = int(tolerance * 100)
            is_current = (order == current_order)
            
            text = f"  {self.quality_names[i]}:  {order.upper()}  ({percentage}%)"
            if is_current:
                text += "  ◄ CURRENT"
            btn.setText(text)
            btn.setEnabled(True)
            
            try:
                btn.clicked.disconnect()
            except:
                pass
            
            def make_callback(rot_order):
                return lambda: self._on_button_clicked(rot_order)
            
            btn.clicked.connect(make_callback(order))
            
            color = self.quality_colors[i]
            if is_current:
                # Current order: Filled color
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {color}20;
                        color: {color};
                        border: 2px solid {color};
                        border-radius: 8px;
                        padding: 10px 16px;
                        font-size: 11px;
                        font-weight: bold;
                        text-align: left;
                    }}
                    QPushButton:hover {{
                        background-color: {color}40;
                        color: #ffffff;
                    }}
                """)
            else:
                # Other orders: Black with colored hover (Border ONLY)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: #4d4d4d;
                        color: #BBB;
                        border: 1px solid #666666;
                        border-radius: 8px;
                        padding: 10px 16px;
                        font-size: 11px;
                        text-align: left;
                    }}
                    QPushButton:hover {{
                        background-color: #4d4d4d;
                        border: 1px solid {color};
                        color: {color};
                    }}
                    QPushButton:pressed {{
                        background-color: #4d4d4d;
                        border: 1px solid {color};
                        color: #FFF;
                    }}
                """)
    
    def _disable_buttons(self):
        """Disable all buttons"""
        for i, btn in enumerate(self.order_buttons):
            btn.setText(f"  {self.quality_names[i]}:  ---")
            btn.setEnabled(False)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #4d4d4d;
                    color: #555;
                    border: 1px solid #5a5a5a;
                    border-radius: 8px;
                    padding: 10px 16px;
                    font-size: 11px;
                    text-align: left;
                }
            """)
    
    def _on_button_clicked(self, order):
        """Handle button click"""
        sel = cmds.ls(sl=True, transforms=True)
        if not sel:
            cmds.warning("Please select a control.")
            return
        
        obj = sel[0]
        success = apply_rotation_order(obj, order)
        
        if success:
            QtCore.QTimer.singleShot(50, self.refresh_data)
    
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
    
    def closeEvent(self, event):
        """Clean up callback when window closes"""
        global _gimbal_window
        self._kill_callback()
        _gimbal_window = None
        super(GimbalFixerWindow, self).closeEvent(event)


def show(anchor_button=None):
    """Open the Gimbal Fixer window"""
    global _gimbal_window
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows(except_widget=_gimbal_window)
    
    if _gimbal_window is not None:
        existing = uiMod.show_existing_animkey_tool_window(_gimbal_window, anchor_button)
        if existing is not None:
            _gimbal_window = existing
            return _gimbal_window
        _gimbal_window = None
    
    if cmds.window(WINDOW_OBJECT, exists=True):
        cmds.deleteUI(WINDOW_OBJECT)
    
    _gimbal_window = GimbalFixerWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _gimbal_window.show()
    _gimbal_window.raise_()
    return _gimbal_window


def execute(*args, **kwargs):
    """Main entry point for the button"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.gimbalFixer.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))
