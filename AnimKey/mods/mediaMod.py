"""
    AnimKey Media Module
    
    Handles loading and managing icons and other media resources.
"""

import os


# ═══════════════════════════════════════════════════════════════════════════════
#                           PATH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_icons_path():
    """Get the path to the icons directory"""
    # Get the directory where this file (mediaMod.py) is located
    # mediaMod.py is in AnimKey/mods/, so we go up one level to AnimKey/, then into data/icons/
    current_dir = os.path.dirname(os.path.abspath(__file__))
    animkey_dir = os.path.dirname(current_dir)  # Go up from mods/ to AnimKey/
    icons_path = os.path.join(animkey_dir, "data", "icons")
    return icons_path


def get_icon(icon_name):
    """
    Get the full path to an icon file.
    
    Args:
        icon_name: Name of the icon file (e.g., "isolate.svg")
    
    Returns:
        Full path to the icon file
    """
    icons_path = get_icons_path()
    icon_path = os.path.join(icons_path, icon_name)
    
    # Check if icon exists
    if os.path.exists(icon_path):
        return icon_path
    
    # Try with different extensions
    base_name = os.path.splitext(icon_name)[0]
    for ext in ['.svg', '.png', '.jpg']:
        test_path = os.path.join(icons_path, base_name + ext)
        if os.path.exists(test_path):
            return test_path
    
    # Return the original path even if it doesn't exist
    # (Qt will handle missing icons gracefully)
    return icon_path


# ═══════════════════════════════════════════════════════════════════════════════
#                           ICON DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

# Tool Icons (legacy)
ICON_ISOLATE = get_icon("isolate.svg")
ICON_ALIGN = get_icon("magnet.svg")
ICON_TRAIL = get_icon("trail.svg")
ICON_RESET = get_icon("eraser.svg")
ICON_DELETE = get_icon("trash.svg")

# Selection Icons (legacy)
ICON_SELECT_OPPOSITE = get_icon("select_opposite.svg")
ICON_COPY_OPPOSITE = get_icon("copy_opposite.svg")
ICON_MIRROR = get_icon("mirror.svg")
ICON_SELECT_HIERARCHY = get_icon("select_hierarchy.svg")
ICON_SELECTOR = get_icon("selector.svg")

# Animation Icons (legacy)
ICON_COPY_ANIMATION = get_icon("copy_paste_animation.svg")
ICON_PASTE_ANIMATION = get_icon("paste_animation.svg")
ICON_PASTE_INSERT = get_icon("paste_insert_animation.svg")
ICON_COPY_POSE = get_icon("copy_pose.svg")
ICON_PASTE_POSE = get_icon("paste_pose.svg")
ICON_ANIMATION_OFFSET = get_icon("animation_offset.svg")
ICON_BAKE = get_icon("bake_animation.svg")

# Worldspace Icons (legacy)
ICON_COPY_WORLDSPACE = get_icon("copy_worldspace_animation.svg")
ICON_PASTE_WORLDSPACE = get_icon("paste_worldspace_animation.svg")

# Utility Icons (legacy)
ICON_LINK = get_icon("link_relative.svg")
ICON_CAMERA = get_icon("camera.svg")
ICON_TEMP_PIVOT = get_icon("temp_pivot.svg")
ICON_RULER = get_icon("ruler.svg")

# ═══════════════════════════════════════════════════════════════════════════════
#                           BUTTON ICONS (NEW)
# ═══════════════════════════════════════════════════════════════════════════════

# Toolbar Button Icons - 128px PNG versions
ICON_BTN_ISO = get_icon("animkey_btn_iso_128.png")      # Isolate
ICON_BTN_ALN = get_icon("animkey_btn_aln_128.png")      # Align
ICON_BTN_OPP = get_icon("animkey_btn_opp_128.png")      # Select Opposite
ICON_BTN_TRL = get_icon("animkey_btn_trl_128.png")      # Trail
ICON_BTN_OFF = get_icon("animkey_btn_off_128.png")      # Animation Offset
ICON_BTN_RST = get_icon("animkey_btn_rst_128.png")      # Reset Values
ICON_BTN_HIR = get_icon("animkey_btn_hir_128.png")      # Select Hierarchy
ICON_BTN_MIR = get_icon("animkey_btn_mir_128.png")      # Mirror
ICON_BTN_COPY = get_icon("animkey_btn_copy_128.png")    # Copy Animation
ICON_BTN_LKN = get_icon("animkey_btn_lkn_128.png")      # Link Objects
ICON_BTN_CAM = get_icon("animkey_btn_cam_128.png")      # Follow Cam
ICON_BTN_WS = get_icon("animkey_btn_ws_128.png")        # World Space Copy
ICON_BTN_PIV = get_icon("animkey_btn_piv_128.png")      # Temp Control
ICON_BTN_RUL = get_icon("animkey_btn_rul_128.png")      # Micro Move (Ruler)

# Tangent Button Icons
ICON_BTN_TANGENT_AUTO = get_icon("animkey_tangent_auto_128.png")
ICON_BTN_TANGENT_SPLINE = get_icon("animkey_tangent_spline_128.png")
ICON_BTN_TANGENT_LINEAR = get_icon("animkey_tangent_linear_128.png")
ICON_BTN_TANGENT_STEP = get_icon("animkey_tangent_step_128.png")
ICON_BTN_TANGENT_CLAMPED = get_icon("animkey_tangent_clamped_128.png")
ICON_BTN_TANGENT_FLAT = get_icon("animkey_tangent_flat_128.png")
ICON_BTN_TANGENT_PLATEAU = get_icon("animkey_tangent_plateau_128.png")

# Extra Button Icons
ICON_BTN_REBLOCK = get_icon("animkey_btn_reblock_128.png")
ICON_BTN_GIMBAL = get_icon("animkey_btn_gmb_128.png")
ICON_BTN_BAKE = get_icon("animkey_btn_bak_128.png")
ICON_BTN_RTM = get_icon("animkey_btn_rtm_128.png")
ICON_BTN_ACL = get_icon("animkey_btn_acl_128.png")
ICON_BTN_SETS = get_icon("animkey_btn_Sel_128.png")
ICON_BTN_BRUSH = get_icon("animkey_btn_brush_128.png")
ICON_BTN_CRASH = get_icon("animkey_btn_crash_128.png")

# Button icon mapping by abbreviation
BUTTON_ICONS = {
    "ISO": ICON_BTN_ISO,
    "ALN": ICON_BTN_ALN,
    "OPP": ICON_BTN_OPP,
    "TRL": ICON_BTN_TRL,
    "OFF": ICON_BTN_OFF,
    "RST": ICON_BTN_RST,
    "HIR": ICON_BTN_HIR,
    "MIR": ICON_BTN_MIR,
    "C": ICON_BTN_COPY,
    "LKN": ICON_BTN_LKN,
    "CAM": ICON_BTN_CAM,
    "WS": ICON_BTN_WS,
    "PIV": ICON_BTN_PIV,
    "RUL": ICON_BTN_RUL,
    "S": get_icon("animkey_btn_save_128.png"),      # Save Animation
    # Tangent buttons
    "PLT": ICON_BTN_TANGENT_PLATEAU,
    "STP": ICON_BTN_TANGENT_STEP,
    "FLT": ICON_BTN_TANGENT_FLAT,
    "LIN": ICON_BTN_TANGENT_LINEAR,
    "CLP": ICON_BTN_TANGENT_CLAMPED,
    "SPL": ICON_BTN_TANGENT_SPLINE,
    "AUT": ICON_BTN_TANGENT_AUTO,
    # Extra buttons
    "RBK": ICON_BTN_REBLOCK,
    "GMB": ICON_BTN_GIMBAL,
    "SWT": get_icon("animkey_btn_col_128.png"),  # Switcher
    "BAK": ICON_BTN_BAKE,
    "RTM": ICON_BTN_RTM,
    "ACL": ICON_BTN_ACL,
    "SETS": ICON_BTN_SETS,
    "BRUSH": ICON_BTN_BRUSH,
    "BTNS": get_icon("animkey_btn_btns_128.png"),  # Flash Buttons
    "ACR": ICON_BTN_CRASH,    # Anim Crash / Recovery
    "CRASH": ICON_BTN_CRASH,
}

def get_button_icon(button_abbrev):
    """
    Get the icon path for a toolbar button by its abbreviation.
    
    Args:
        button_abbrev: Button abbreviation (e.g., "ISO", "ALN", "MIR")
    
    Returns:
        Full path to the icon file, or None if not found
    """
    return BUTTON_ICONS.get(button_abbrev)

# Tangent Icons
ICON_AUTO_TANGENT = get_icon("auto_tangent.svg")
ICON_SPLINE_TANGENT = get_icon("spline_tangent.svg")
ICON_LINEAR_TANGENT = get_icon("linear_tangent.svg")
ICON_STEP_TANGENT = get_icon("step_tangent.svg")

# UI Icons
ICON_SETTINGS = get_icon("settings.svg")
ICON_HELP = get_icon("help.svg")
ICON_CLOSE = get_icon("close.svg")
ICON_ADD = get_icon("add.svg")
ICON_REMOVE = get_icon("remove.svg")

# Selection Set Icons
ICON_SELECTION_SETS = get_icon("selection_sets.svg")
ICON_ADD_SET = get_icon("add_selection_set.svg")
ICON_RENAME_SET = get_icon("rename_selection_set.svg")
ICON_REMOVE_SET = get_icon("remove_selection_set.svg")
ICON_CHANGE_COLOR = get_icon("change_selection_set_color.svg")

# Graph Editor Icons
ICON_CUSTOM_GRAPH = get_icon("customGraph.svg")
ICON_SHARE_KEYS = get_icon("share_keys.svg")
ICON_REBLOCK = get_icon("reblock.svg")
ICON_MATCH_CYCLE = get_icon("match_curve_cycle.svg")
ICON_BOUNCY = get_icon("bouncy_curve.svg")


# ═══════════════════════════════════════════════════════════════════════════════
#                           ICON LOADER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class IconLoader:
    """
    Utility class for loading and caching icons.
    """
    
    _cache = {}
    
    @classmethod
    def get(cls, icon_name):
        """
        Get an icon path, using cache if available.
        """
        if icon_name not in cls._cache:
            cls._cache[icon_name] = get_icon(icon_name)
        return cls._cache[icon_name]
    
    @classmethod
    def clear_cache(cls):
        """Clear the icon cache."""
        cls._cache = {}
    
    @classmethod
    def exists(cls, icon_name):
        """Check if an icon file exists."""
        return os.path.exists(get_icon(icon_name))
