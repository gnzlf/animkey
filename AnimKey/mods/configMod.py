"""
    AnimKey Configuration Module
    
    Handles all configuration loading, saving, and user preferences.
"""

import os
import json
import copy
import maya.cmds as cmds

from AnimKey.mods.storage import atomic_write_json, backup_corrupt_file, deep_merge
from AnimKey.version import __version__ as ANIMKEY_VERSION


# ═══════════════════════════════════════════════════════════════════════════════
#                           PATH CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

def get_install_path():
    """Get the installation path of AnimKey"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_user_folder_path():
    """Get the user folder path for storing preferences and data"""
    maya_app_dir = cmds.internalVar(userAppDir=True)
    user_folder = os.path.join(maya_app_dir, "AnimKey_user_data")
    
    # Create folder if it doesn't exist
    if not os.path.exists(user_folder):
        os.makedirs(user_folder)
    
    return user_folder


def get_preferences_path():
    """Get the path to the preferences file"""
    return os.path.join(get_user_folder_path(), "preferences.json")


# ═══════════════════════════════════════════════════════════════════════════════
#                           DEFAULT CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    # UI Settings
    "theme": "maya_classic",
    "toolbar_icon_size": 28,
    "toolbar_height": 38,
    "toolbar_scale": 1.0,
    "show_tooltips": True,
    "show_labels": False,
    "tangent_buttons_collapsed": False,
    
    # Slider Settings
    "tween_slider_width": 140,
    "blend_slider_width": 160,
    "curve_slider_width": 140,
    
    # Behavior Settings
    "auto_key": False,
    "use_animation_layers": True,
    "remember_last_selection": True,
    "isolate_include_parented_objects": False,
    "align_objects_channels": {
        "translate": False,
        "rotate": False,
        "scale": False,
    },
    "viewport_roll_gimbal_enabled": False,
    "tumble_around_selection_enabled": False,
    # Recovery is opt-out: it starts with AnimKey unless the animator turns it off.
    "crash_recovery_enabled": True,
    # Full-scene copies are intentionally less frequent than the lightweight
    # animation checkpoints.  Zero disables the automatic .mb copies.
    "crash_recovery_scene_snapshot_minutes": 10,
    
    # Window Settings
    "window_opacity": 1.0,
    "always_on_top": False,
    
    # Custom Graph Settings
    "custom_graph_enabled": True,
    "custom_graph_auto_load": True,
    
    # Hotkeys
    "hotkeys_enabled": True,

    # Storage
    "animation_backup_folder": "",

    # Timeline
    "timeline_channel_box_key_filter": False,
    "channel_box_multi_selection_helper": False,
    
    # Advanced
    "debug_mode": False,
    "check_updates": True,
}


# ═══════════════════════════════════════════════════════════════════════════════
#                           CONFIGURATION MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ConfigManager:
    """
    Singleton class to manage AnimKey configuration
    """
    
    _instance = None
    _config = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance
    
    def _load_config(self):
        """Load configuration from file or create default"""
        config_path = get_preferences_path()
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    loaded = json.load(f)
                self._config = deep_merge(DEFAULT_CONFIG, loaded)
            except (json.JSONDecodeError, IOError):
                backup_corrupt_file(config_path)
                self._config = copy.deepcopy(DEFAULT_CONFIG)
                self._save_config()
        else:
            self._config = copy.deepcopy(DEFAULT_CONFIG)
            self._save_config()
    
    def _save_config(self):
        """Save current configuration to file"""
        config_path = get_preferences_path()
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        
        try:
            atomic_write_json(config_path, self._config)
        except IOError as e:
            cmds.warning(f"AnimKey: Could not save configuration: {e}")
    
    def get(self, key, default=None):
        """Get a configuration value"""
        return self._config.get(key, default if default is not None else DEFAULT_CONFIG.get(key))
    
    def set(self, key, value):
        """Set a configuration value and save"""
        self._config[key] = value
        self._save_config()
    
    def reset(self, key=None):
        """Reset configuration to defaults"""
        if key:
            if key in DEFAULT_CONFIG:
                self._config[key] = copy.deepcopy(DEFAULT_CONFIG[key])
        else:
            self._config = copy.deepcopy(DEFAULT_CONFIG)
        self._save_config()
    
    def get_all(self):
        """Get all configuration as dictionary"""
        return copy.deepcopy(self._config)
    
    def update(self, config_dict):
        """Update multiple configuration values at once"""
        self._config.update(config_dict)
        self._save_config()


# ═══════════════════════════════════════════════════════════════════════════════
#                           CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_config():
    """Get the ConfigManager instance"""
    return ConfigManager()


def get_setting(key, default=None):
    """Shortcut to get a setting value"""
    return ConfigManager().get(key, default)


def set_setting(key, value):
    """Shortcut to set a setting value"""
    ConfigManager().set(key, value)


def get_animation_backup_folder(create=True):
    """Return the folder used for animation backup files."""
    configured = get_setting("animation_backup_folder", "")
    folder = configured or os.path.join(
        get_user_folder_path(),
        "tools",
        "copy_paste_animation",
        "backups",
    )

    folder = os.path.normpath(folder)
    if create and not os.path.exists(folder):
        os.makedirs(folder)
    return folder


# ═══════════════════════════════════════════════════════════════════════════════
#                           VERSION INFO
# ═══════════════════════════════════════════════════════════════════════════════

VERSION_INFO = {
    "version": ANIMKEY_VERSION,
    "build": "001",
    "name": "AnimKey",
    "author": "AnimKey Team",
    "website": "",
}


def get_version():
    """Get the current version string"""
    return VERSION_INFO["version"]


def get_build():
    """Get the current build number"""
    return VERSION_INFO["build"]


def get_full_version():
    """Get full version string"""
    return f"{VERSION_INFO['name']} v{VERSION_INFO['version']} (Build {VERSION_INFO['build']})"


# ═══════════════════════════════════════════════════════════════════════════════
#                           WORKSPACE CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Default workspace - all elements visible
DEFAULT_WORKSPACE = {
    "buttons": {
        "ISO": True, "ALN": True, "OPP": True, "TRL": True,
        "RST": True, "TMP": True, "OFF": True, "HIR": True, "MIR": True,
        "C": True, "LKN": True, "CAM": True, "WS": True, "PIV": True, "RUL": True,
        "PLT": True, "STP": True, "FLT": True, "LIN": True, "CLP": True, "SPL": True, "AUT": True,
        "RBK": True, "GMB": True, "SWT": True, "BAK": True, "RTM": True, "ACL": True, "BTNS": True, "SETS": True, "CRASH": True
    },

    "sliders": {
        "Tweener": True,
        "Push/Pull": True,
        "Blend to Neighbors": True,
        "Blend to Ease": True,
        "Blend to Undo": True,
        "Mirror Blend": True
    },
    "other": {
        "Selector": True,
        "KeyNav": True
    }
}


def get_workspace_file():
    """Get the path to the workspace configuration file"""
    import os
    return os.path.join(get_user_folder_path(), "workspace.json")


def load_workspace():
    """Load workspace visibility settings from file"""
    import os, json
    workspace_file = get_workspace_file()
    
    if os.path.exists(workspace_file):
        try:
            with open(workspace_file, 'r') as f:
                saved_workspace = json.load(f)
                
                # Merge with defaults for any missing keys
                workspace = {
                    "buttons": DEFAULT_WORKSPACE["buttons"].copy(),
                    "sliders": DEFAULT_WORKSPACE["sliders"].copy(),
                    "other": DEFAULT_WORKSPACE["other"].copy()
                }
                if "buttons" in saved_workspace:
                    saved_buttons = saved_workspace["buttons"].copy()
                    if "SWT" not in saved_buttons and "COL" in saved_buttons:
                        saved_buttons["SWT"] = saved_buttons["COL"]
                    saved_buttons.pop("COL", None)
                    workspace["buttons"].update(saved_buttons)
                    legacy_trail_key = "T" + "RC"
                    if legacy_trail_key in workspace["buttons"]:
                        workspace["buttons"]["TRL"] = workspace["buttons"].pop(legacy_trail_key)
                if "sliders" in saved_workspace:
                    workspace["sliders"].update(saved_workspace["sliders"])
                if "other" in saved_workspace:
                    workspace["other"].update(saved_workspace["other"])
                if "order" in saved_workspace:
                    workspace["order"] = saved_workspace["order"]
                    legacy_trail_key = "T" + "RC"
                    workspace["order"] = [
                        "TRL" if item == legacy_trail_key else
                        "SWT" if item == "COL" else item
                        for item in workspace["order"]
                    ]
                    
                return workspace
        except (json.JSONDecodeError, IOError):
            backup_corrupt_file(workspace_file)
    
    return {
        "buttons": DEFAULT_WORKSPACE["buttons"].copy(),
        "sliders": DEFAULT_WORKSPACE["sliders"].copy(),
        "other": DEFAULT_WORKSPACE["other"].copy()
    }


def save_workspace(workspace):
    """Save workspace visibility settings to file"""
    import os, json
    import maya.cmds as cmds
    workspace_file = get_workspace_file()
    
    try:
        os.makedirs(os.path.dirname(workspace_file), exist_ok=True)
        atomic_write_json(workspace_file, workspace)
        return True
    except IOError as e:
        cmds.warning(f"AnimKey: Could not save workspace: {e}")
        return False


def export_workspace_preset(filepath):
    """Export current workspace to a file for sharing"""
    import json
    import maya.cmds as cmds
    workspace = load_workspace()
    
    try:
        atomic_write_json(filepath, workspace)
        return True
    except IOError as e:
        cmds.warning(f"AnimKey: Could not export workspace preset: {e}")
        return False


def import_workspace_preset(filepath):
    """Import workspace preset from a file"""
    import json
    import maya.cmds as cmds
    try:
        with open(filepath, 'r') as f:
            workspace = json.load(f)
        
        # Validate structure
        if "buttons" not in workspace or "sliders" not in workspace:
            cmds.warning("AnimKey: Invalid workspace preset file")
            return False
        
        save_workspace(workspace)
        return True
    except (json.JSONDecodeError, IOError) as e:
        cmds.warning(f"AnimKey: Could not import workspace preset: {e}")
        return False


def reset_workspace():
    """Reset workspace to default (all visible)"""
    workspace = {
        "buttons": DEFAULT_WORKSPACE["buttons"].copy(),
        "sliders": DEFAULT_WORKSPACE["sliders"].copy(),
        "other": DEFAULT_WORKSPACE["other"].copy()
    }
    return save_workspace(workspace)


def is_element_visible(category, element_name):
    """Check if a specific element should be visible"""
    workspace = load_workspace()
    if category in workspace and element_name in workspace[category]:
        return workspace[category][element_name]
    return True  # Default to visible
