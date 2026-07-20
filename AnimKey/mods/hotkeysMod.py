"""
    AnimKey Hotkeys Module
    
    Direct Qt-based hotkey system that bypasses Maya's Hotkey Editor entirely.
    Uses a Qt event filter installed on Maya's main window to intercept key presses.
"""

import maya.cmds as cmds
import maya.mel as mel
from AnimKey.core.settings import load_shortcuts, save_shortcuts

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtCore import Qt
    from PySide2.QtGui import QKeySequence
    import shiboken2 as shiboken
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    import shiboken6 as shiboken

import maya.OpenMayaUI as omui
from AnimKey.core.executionGuard import animkey_execution


# ═══════════════════════════════════════════════════════════════════════════════
#                           GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════════════

_event_filter = None
_registered_hotkeys = {}
ANIMKEY_MENU_NAME = "AnimKeyMenu"


def normalize_shortcut_string(shortcut_string):
    """Normalize a shortcut string for consistent matching."""
    if not shortcut_string:
        return None

    parts = [p.strip() for p in str(shortcut_string).split('+') if p.strip()]

    modifiers = []
    key = None

    for part in parts:
        upper = part.upper()
        if upper in ('CTRL', 'CONTROL'):
            modifiers.append('Ctrl')
        elif upper in ('ALT', 'OPTION'):
            modifiers.append('Alt')
        elif upper in ('SHIFT',):
            modifiers.append('Shift')
        elif upper in ('META', 'CMD', 'COMMAND', 'WIN'):
            modifiers.append('Meta')
        else:
            key = part.upper()

    if not key and not modifiers:
        return None

    modifiers = sorted(dict.fromkeys(modifiers))
    if modifiers:
        return '+'.join(modifiers) + ('+' + key if key else '')
    return key


def get_maya_main_window():
    """Get Maya's main window as a Qt widget."""
    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr:
        return shiboken.wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#                           QT EVENT FILTER (CORE HOTKEY SYSTEM)
# ═══════════════════════════════════════════════════════════════════════════════

class AnimKeyEventFilter(QtCore.QObject):
    """
    Qt Event Filter that intercepts keyboard events in Maya.
    This bypasses Maya's Hotkey Editor entirely for AnimKey shortcuts.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.shortcuts = {}  # Format: {"B": "ISO", "Ctrl+Shift+M": "MIR", ...}
        self.enabled = True
    
    def set_shortcuts(self, shortcuts_dict):
        """
        Set the shortcuts dictionary.
        
        Args:
            shortcuts_dict: Dict mapping action names to shortcut strings
                           e.g. {"ISO": "B", "MIR": "Ctrl+Shift+M"}
        """
        # Invert the dictionary: shortcut -> action
        self.shortcuts = {}
        for action, shortcut in shortcuts_dict.items():
            if shortcut:
                # Normalize the shortcut string
                normalized = self._normalize_shortcut(shortcut)
                if normalized:
                    self.shortcuts[normalized] = action
                    print(f"AnimKey: Registered shortcut '{normalized}' -> {action}")
    
    def _normalize_shortcut(self, shortcut_string):
        """Normalize a shortcut string for consistent matching."""
        return normalize_shortcut_string(shortcut_string)
    
    def _key_event_to_string(self, event):
        """Convert a QKeyEvent to a normalized shortcut string."""
        key = event.key()
        modifiers = event.modifiers()
        
        # Build modifier string
        mod_parts = []
        if modifiers & Qt.ControlModifier: mod_parts.append('Ctrl')
        if modifiers & Qt.AltModifier: mod_parts.append('Alt')
        if modifiers & Qt.ShiftModifier: mod_parts.append('Shift')
        if modifiers & Qt.MetaModifier: mod_parts.append('Meta')
        mod_parts.sort()

        # If it's a modifier-only key, we only return if it matches exactly
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            if mod_parts:
                return '+'.join(mod_parts)
            return None
        
        # Build modifier string
        mod_parts = []
        if modifiers & Qt.ControlModifier:
            mod_parts.append('Ctrl')
        if modifiers & Qt.AltModifier:
            mod_parts.append('Alt')
        if modifiers & Qt.ShiftModifier:
            mod_parts.append('Shift')
        if modifiers & Qt.MetaModifier:
            mod_parts.append('Meta')
        
        mod_parts.sort()
        
        # Convert key to string
        key_text = QKeySequence(key).toString().upper()
        
        if not key_text:
            return None
        
        if mod_parts:
            return '+'.join(mod_parts) + '+' + key_text
        else:
            return key_text
    
    def eventFilter(self, obj, event):
        """Filter events - intercept key presses for AnimKey shortcuts."""
        if not self.enabled:
            return False
        
        if event.type() == QtCore.QEvent.KeyPress:
            # Don't intercept if a text input has focus
            focus_widget = QtWidgets.QApplication.focusWidget()
            if focus_widget:
                if isinstance(focus_widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, 
                                            QtWidgets.QPlainTextEdit, QtWidgets.QSpinBox,
                                            QtWidgets.QDoubleSpinBox)):
                    return False
            
            # Convert event to shortcut string
            shortcut = self._key_event_to_string(event)
            
            if shortcut and shortcut in self.shortcuts:
                action = self.shortcuts[shortcut]
                print(f"AnimKey: Triggered '{action}' via shortcut '{shortcut}'")
                
                # Execute the action
                try:
                    execute_action(action)
                except Exception as e:
                    print(f"AnimKey: Error executing action '{action}': {e}")
                    import traceback
                    traceback.print_exc()
                
                # Consume the event (don't pass to Maya's hotkey system)
                return True
        
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#                           ACTION EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

def get_action_function(action_name):
    """
    Get the function to execute for a given action name.
    
    Args:
        action_name: Name of the action (e.g., "ISO", "ALN", "Tweener")
    
    Returns:
        Python code string to execute
    """
    action_map = {
        # Tool buttons
        "ISO": "import AnimKey.buttons.isolate as mod; mod.execute()",
        "ALN": "import AnimKey.buttons.align_objects as mod; mod.execute()",
        "OPP": "import AnimKey.buttons.selectOpposite as mod; mod.execute()",
        "TRL": "import AnimKey.buttons.trail as mod; mod.execute()",
        "OFF": "import AnimKey.buttons.animation_offset as mod; mod.execute()",
        "RST": "import AnimKey.buttons.resetValues as mod; mod.execute_shortcut()",
        "TMP": "import AnimKey.buttons.tempPivot as mod; mod.execute()",
        "HIR": "import AnimKey.buttons.select_hierarchy as mod; mod.execute()",
        "SEL": "import AnimKey.core.toolbar as mod; toolbar = mod.AnimKeyToolbar(); toolbar._open_selector_window()",
        "MIR": "import AnimKey.buttons.mirror as mod; mod.execute()",
        "C": "import AnimKey.buttons.copyAnimation as mod; mod.execute()",
        "LKN": "import AnimKey.buttons.linkObjects as mod; mod.execute()",
        "CAM": "import AnimKey.buttons.followCam as mod; mod.execute()",
        "WS": "import AnimKey.buttons.copyWorldspace as mod; mod.execute()",
        "PIV": "# Temp Control - not implemented yet",
        "RUL": "import AnimKey.buttons.microMove as mod; mod.execute()",
        "RTM": "import AnimKey.buttons.retimer as mod; mod.execute()",
        
        # ─────────────────────────────────────────────────────────────────────
        # TANGENT BUTTONS
        # ─────────────────────────────────────────────────────────────────────
        "PLT": "import AnimKey.buttons.tangents as mod; mod.execute_plateau()",
        "STP": "import AnimKey.buttons.tangents as mod; mod.execute_step()",
        "FLT": "import AnimKey.buttons.tangents as mod; mod.execute_flat()",
        "LIN": "import AnimKey.buttons.tangents as mod; mod.execute_linear()",
        "CLP": "import AnimKey.buttons.tangents as mod; mod.execute_clamped()",
        "SPL": "import AnimKey.buttons.tangents as mod; mod.execute_spline()",
        "AUT": "import AnimKey.buttons.tangents as mod; mod.execute_auto()",
        
        # ─────────────────────────────────────────────────────────────────────
        # EXTRA TOOL BUTTONS
        # ─────────────────────────────────────────────────────────────────────
        "RBK": "import AnimKey.buttons.reblock as mod; mod.execute()",
        "GMB": "import AnimKey.buttons.gimbalFixer as mod; mod.execute()",
        "COL": "import AnimKey.buttons.collisionTool as mod; mod.execute()",
        "BAK": "import AnimKey.buttons.bakeAnim as mod; mod.execute()",
        "SETS": "import AnimKey.buttons.selectionSets as mod; mod.execute()",
        "ACR": "import AnimKey.buttons.animCrash as mod; mod.execute()",
        "BTNS": "import AnimKey.buttons.flashbuttons as mod; mod.execute()",
        
        # ─────────────────────────────────────────────────────────────────────
        # ALIGN SUBMENU
        # ─────────────────────────────────────────────────────────────────────
        "Align Position": "from AnimKey.buttons.align_objects import align_position; align_position()",
        "Align Orientation": "from AnimKey.buttons.align_objects import align_orientation; align_orientation()",
        "Align Scale": "from AnimKey.buttons.align_objects import align_scale; align_scale()",
        
        # ─────────────────────────────────────────────────────────────────────
        # RESET VALUES SUBMENU
        # ─────────────────────────────────────────────────────────────────────
        "Set Default Values": "from AnimKey.buttons.resetValues import save_default_values; save_default_values()",
        "Restore Default Values": "from AnimKey.buttons.resetValues import remove_default_values_for_selected_object; remove_default_values_for_selected_object()",
        "Clear All Saved Data": "from AnimKey.buttons.resetValues import restore_default_data; restore_default_data()",
        
        # ─────────────────────────────────────────────────────────────────────
        # SELECT OPPOSITE SUBMENU
        # ─────────────────────────────────────────────────────────────────────
        "Add Select Opposite": "from AnimKey.buttons.selectOpposite import add_select_opposite; add_select_opposite()",
        
        # ─────────────────────────────────────────────────────────────────────
        # MIRROR SUBMENU
        # ─────────────────────────────────────────────────────────────────────
        "All Mirror": "from AnimKey.buttons.mirror import all_mirror; all_mirror()",
        "Toggle Auto Mirror": "from AnimKey.buttons.mirror import toggle_auto_mirror; toggle_auto_mirror()",
        "Snapshot Mirror Settings": "from AnimKey.buttons.mirror import snapshot_mirror_settings; snapshot_mirror_settings()",
        "Show Snapshot Info": "from AnimKey.buttons.mirror import show_mirror_snapshot_info; show_mirror_snapshot_info()",
        "Delete Snapshot": "from AnimKey.buttons.mirror import delete_mirror_snapshot; delete_mirror_snapshot()",
        "Add Mirror Invert Exception": "from AnimKey.buttons.mirror import add_mirror_invert_exception; add_mirror_invert_exception()",
        "Add Mirror Keep Exception": "from AnimKey.buttons.mirror import add_mirror_keep_exception; add_mirror_keep_exception()",
        "Remove Mirror Exception": "from AnimKey.buttons.mirror import remove_mirror_exception; remove_mirror_exception()",
        "Show Mirror Exceptions": "from AnimKey.buttons.mirror import show_exceptions; show_exceptions()",
        "Clear All Mirror Exceptions": "from AnimKey.buttons.mirror import clear_all_exceptions; clear_all_exceptions()",
        
        # ─────────────────────────────────────────────────────────────────────
        # COPY ANIMATION SUBMENU
        # ─────────────────────────────────────────────────────────────────────
        "Copy Animation": "from AnimKey.buttons.copyAnimation import copy_animation; copy_animation()",
        "Paste Animation": "from AnimKey.buttons.copyAnimation import paste_animation; paste_animation()",
        "Paste Insert": "from AnimKey.buttons.copyAnimation import paste_insert_animation; paste_insert_animation()",
        "Paste Opposite": "from AnimKey.buttons.copyAnimation import paste_opposite_animation; paste_opposite_animation()",
        "Copy Pose": "from AnimKey.buttons.copyAnimation import copy_pose; copy_pose()",
        "Paste Pose": "from AnimKey.buttons.copyAnimation import paste_pose; paste_pose()",
        
        # ─────────────────────────────────────────────────────────────────────
        # FOLLOW CAM SUBMENU
        # ─────────────────────────────────────────────────────────────────────
        "Follow Cam Translation & Rotation": "from AnimKey.buttons.followCam import create_follow_cam; create_follow_cam(translation=True, rotation=True)",
        "Follow Cam Translation Only": "from AnimKey.buttons.followCam import create_follow_cam; create_follow_cam(translation=True, rotation=False)",
        "Follow Cam Rotation Only": "from AnimKey.buttons.followCam import create_follow_cam; create_follow_cam(translation=False, rotation=True)",
        "Remove Follow Cam": "from AnimKey.buttons.followCam import remove_follow_cam; remove_follow_cam()",
        
        # ─────────────────────────────────────────────────────────────────────
        # LINK OBJECTS SUBMENU
        # ─────────────────────────────────────────────────────────────────────
        "Copy Link Frame": "from AnimKey.buttons.linkObjects import copy_link_frame; copy_link_frame()",
        "Copy Link Playback Range": "from AnimKey.buttons.linkObjects import copy_link_playback_range; copy_link_playback_range()",
        "Paste Link Frame": "from AnimKey.buttons.linkObjects import paste_link_frame; paste_link_frame()",
        "Paste Link Next Frame": "from AnimKey.buttons.linkObjects import paste_link_next_frame; paste_link_next_frame()",
        "Paste Link All Keys": "from AnimKey.buttons.linkObjects import paste_link_all_keys; paste_link_all_keys()",
        "Toggle Auto Link": "from AnimKey.buttons.linkObjects import toggle_auto_link; toggle_auto_link()",
        
        # ─────────────────────────────────────────────────────────────────────
        # WORLDSPACE SUBMENU
        # ─────────────────────────────────────────────────────────────────────
        "Copy Worldspace All Animation": "from AnimKey.buttons.copyWorldspace import copy_worldspace_all_animation; copy_worldspace_all_animation()",
        "Copy Worldspace Selected Range": "from AnimKey.buttons.copyWorldspace import copy_worldspace_selected_range; copy_worldspace_selected_range()",
        "Copy Worldspace Current Frame": "from AnimKey.buttons.copyWorldspace import copy_worldspace_current_frame; copy_worldspace_current_frame()",
        "Paste Worldspace Animation": "from AnimKey.buttons.copyWorldspace import paste_worldspace_animation; paste_worldspace_animation()",
        "Paste Worldspace Frame": "from AnimKey.buttons.copyWorldspace import paste_worldspace_current_frame; paste_worldspace_current_frame()",
        "Toggle Auto Worldspace": "from AnimKey.buttons.copyWorldspace import toggle_auto_worldspace; toggle_auto_worldspace()",
        
        # Keyframe controls
        "Increase Values": "import AnimKey.buttons.increase_decrease as mod; mod.execute(0.001, increase=True)",
        "Decrease Values": "import AnimKey.buttons.increase_decrease as mod; mod.execute(0.001, increase=False)",
        "Move Keys Left": "import AnimKey.core.toolbar as mod; toolbar = mod.AnimKeyToolbar(); toolbar._move_keyframes(-1)",
        "Move Keys Right": "import AnimKey.core.toolbar as mod; toolbar = mod.AnimKeyToolbar(); toolbar._move_keyframes(1)",
        "Clear Keys": "import AnimKey.core.toolbar as mod; toolbar = mod.AnimKeyToolbar(); toolbar._clear_selected_keys()",
        "Select All Animation": "import AnimKey.core.toolbar as mod; toolbar = mod.AnimKeyToolbar(); toolbar._select_all_animation()",
    }
    
    return action_map.get(action_name, "")


def execute_action(action_name):
    """Execute an AnimKey action by name."""
    command = get_action_function(action_name)
    
    if not command or command.startswith("#"):
        print(f"AnimKey: Action '{action_name}' is not executable (slider or not implemented)")
        return False
    
    try:
        with animkey_execution("hotkey", action_name):
            exec(command)
        return True
    except Exception as e:
        print(f"AnimKey: Error executing '{action_name}': {e}")
        import traceback
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#                           HOTKEY SYSTEM MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def install_event_filter():
    """Install the AnimKey event filter on Maya's main window."""
    global _event_filter
    
    # Remove existing filter if any
    uninstall_event_filter()
    
    # Get Maya's main window
    main_window = get_maya_main_window()
    if not main_window:
        print("AnimKey: Could not get Maya's main window for hotkey installation")
        return False
    
    # Create and install the event filter
    _event_filter = AnimKeyEventFilter(main_window)
    main_window.installEventFilter(_event_filter)
    
    print("AnimKey: Installed Qt event filter for hotkeys")
    return True


def uninstall_event_filter():
    """Remove the AnimKey event filter from Maya's main window."""
    global _event_filter
    
    main_window = get_maya_main_window()
    if main_window:
        # First remove the one we know about
        if _event_filter:
            main_window.removeEventFilter(_event_filter)
            
        # Then search for any orphaned filters from previous module reloads
        count = 0
        for child in main_window.children():
            if child.__class__.__name__ == "AnimKeyEventFilter":
                main_window.removeEventFilter(child)
                child.deleteLater()
                count += 1
                
        if count > 0 or _event_filter:
            print(f"AnimKey: Removed {count if count > 0 else 1} Qt event filter(s)")
            
    _event_filter = None


def load_and_register_all_hotkeys():
    """Load shortcuts from file and register them with the Qt event filter."""
    global _event_filter
    
    # Ensure event filter is installed
    if not _event_filter:
        install_event_filter()
    
    # Load shortcuts from saved configuration
    shortcuts = load_shortcuts()
    
    if _event_filter:
        _event_filter.set_shortcuts(shortcuts)
        print(f"AnimKey: Loaded {len(shortcuts)} shortcuts")
    
    # Also remove the AnimKey menu if it exists
    create_animkey_menu()


def refresh_hotkeys():
    """Reload and re-register all hotkeys from saved configuration."""
    print("AnimKey: Refreshing hotkeys...")
    load_and_register_all_hotkeys()
    print("AnimKey: Hotkey refresh complete!")


def enable_hotkeys():
    """Enable AnimKey hotkeys."""
    global _event_filter
    if _event_filter:
        _event_filter.enabled = True
        print("AnimKey: Hotkeys enabled")


def disable_hotkeys():
    """Disable AnimKey hotkeys (allows Maya's default hotkeys to work)."""
    global _event_filter
    if _event_filter:
        _event_filter.enabled = False
        print("AnimKey: Hotkeys disabled")


def are_hotkeys_enabled():
    """Check if AnimKey hotkeys are currently enabled."""
    global _event_filter
    return _event_filter is not None and _event_filter.enabled


# ═══════════════════════════════════════════════════════════════════════════════
#                           MENU REMOVAL
# ═══════════════════════════════════════════════════════════════════════════════

def create_animkey_menu():
    """
    Deprecated: Menu disabled.
    Ensures the menu is removed if it exists.
    """
    if cmds.menu(ANIMKEY_MENU_NAME, exists=True):
        try:
            cmds.deleteUI(ANIMKEY_MENU_NAME, menu=True)
            print("AnimKey: Removed legacy menu from menu bar")
        except:
            pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#                           LEGACY COMPATIBILITY (for settings.py)
# ═══════════════════════════════════════════════════════════════════════════════

def register_hotkey(action_name, shortcut_string):
    """
    Register a hotkey for an action.
    Now just refreshes the event filter with updated shortcuts.
    """
    # Just refresh all hotkeys - the Qt system handles everything
    refresh_hotkeys()
    return True


def unregister_hotkey(action_name):
    """
    Unregister a hotkey for an action.
    Now just refreshes the event filter with updated shortcuts.
    """
    refresh_hotkeys()


def unregister_all_hotkeys():
    """Unregister all hotkeys (disable the event filter)."""
    global _event_filter
    if _event_filter:
        _event_filter.shortcuts = {}
        print("AnimKey: Cleared all hotkey registrations")


def register_all_commands_in_hotkey_editor():
    """
    Legacy function for backwards compatibility.
    With the Qt event filter system, this just ensures hotkeys are loaded.
    """
    load_and_register_all_hotkeys()

