"""
    AnimKey Themes Module
    
    Theme system for AnimKey - Maya Classic theme that integrates
    seamlessly with Maya's native interface.
"""

from AnimKey.mods import material_palette as material
# ═══════════════════════════════════════════════════════════════════════════════
#                              COLOR PALETTES
# ═══════════════════════════════════════════════════════════════════════════════

THEMES = {
    
    # ─────────────────────────────────────────────────────────────────────────
    # MAYA CLASSIC - Inspired by Maya's native UI (DEFAULT)
    # ─────────────────────────────────────────────────────────────────────────
    "maya_classic": {
        "name": "Maya Classic",
        "description": "Theme inspired by Maya's native interface",
        
        # Base colors - Match Maya's grey tones
        "bg_primary": material.SURFACE,
        "bg_secondary": material.CONTROL,
        "bg_tertiary": "#2b3341",
        "bg_hover": material.HOVER,
        "bg_pressed": material.PRESSED,
        
        # Text colors
        "text_primary": material.TEXT,
        "text_secondary": material.SECONDARY,
        "text_muted": material.MUTED,
        "text_accent": "#88aadd",
        
        # Accent colors - Colorful palette for buttons
        "accent_primary": "#88aadd",    # Blue
        "accent_secondary": "#a3be8c",  # Green
        "accent_tertiary": "#ebcb8b",   # Yellow
        "accent_success": "#a3be8c",    # Green
        "accent_warning": "#d08770",    # Orange
        "accent_danger": "#bf616a",     # Red
        
        # Slider colors
        "slider_bg": "#151a23",
        "slider_groove": "#303a49",
        "slider_handle": "#bdc9da",
        "slider_handle_hover": "#d9e4f2",
        "slider_tick": "#6699cc",
        
        # Button colors
        "button_bg": material.CONTROL,
        "button_hover": material.HOVER,
        "button_pressed": material.PRESSED,
        "button_border": material.BORDER,
        
        # Border & Effects
        "border_color": material.BORDER,
        "border_radius": "8px",
        "shadow_color": "rgba(0, 0, 0, 0.2)",
        "glow_color": "rgba(102, 153, 204, 0.1)",
        
        # Font
        "font_family": "sans-serif",
        "font_size_small": "10px",
        "font_size_normal": "11px",
        "font_size_large": "13px",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
#                           THEME MANAGER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class ThemeManager:
    """
    Manages theme loading and customization
    """
    
    _current_theme = "maya_classic"
    _custom_overrides = {}
    
    @classmethod
    def get_current_theme(cls):
        """Returns the current theme dictionary with any custom overrides applied"""
        theme = THEMES.get(cls._current_theme, THEMES["maya_classic"]).copy()
        theme.update(cls._custom_overrides)
        from AnimKey.mods.maya_compat import get_maya_font_family
        theme["font_family"] = get_maya_font_family()
        return theme
    
    @classmethod
    def set_theme(cls, theme_name):
        """Switch to a different theme"""
        if theme_name in THEMES:
            cls._current_theme = theme_name
            cls._custom_overrides = {}
            return True
        return False
    
    @classmethod
    def get_available_themes(cls):
        """Returns a list of available theme names"""
        return list(THEMES.keys())
    
    @classmethod
    def get_theme_info(cls, theme_name):
        """Returns name and description of a theme"""
        theme = THEMES.get(theme_name)
        if theme:
            return {"name": theme["name"], "description": theme["description"]}
        return None
    
    @classmethod
    def override_color(cls, key, value):
        """Override a specific color in the current theme"""
        cls._custom_overrides[key] = value
    
    @classmethod
    def reset_overrides(cls):
        """Reset all custom color overrides"""
        cls._custom_overrides = {}
    
    @classmethod
    def get_color(cls, key, fallback=None):
        """Get a specific color from the current theme"""
        theme = cls.get_current_theme()
        return theme.get(key, fallback or "#ffffff")


# ═══════════════════════════════════════════════════════════════════════════════
#                           COLORFUL BUTTON PALETTE
# ═══════════════════════════════════════════════════════════════════════════════

# Nord-inspired color palette for tool buttons
BUTTON_COLORS = {
    "red": "#bf616a",
    "orange": "#d08770", 
    "yellow": "#ebcb8b",
    "green": "#a3be8c",
    "teal": "#8fbcbb",
    "cyan": "#5bc0be",
    "light_blue": "#88c0d0",
    "blue": "#81a1c1",
    "dark_blue": "#5e81ac",
    "purple": "#b48ead",
    "pink": "#d4879c",
}


def get_button_color(color_name):
    """Get a button color by name"""
    return BUTTON_COLORS.get(color_name, BUTTON_COLORS["blue"])


# ═══════════════════════════════════════════════════════════════════════════════
#                           SELECTION SET COLORS
# ═══════════════════════════════════════════════════════════════════════════════

SELECTION_SET_COLORS = {
    "_01": {"normal": "#878A90", "hover": "#A0A5AF", "text": "#333333"},  # Grey
    "_02": {"normal": "#D7CDAF", "hover": "#EEE3C2", "text": "#333333"},  # Yellow
    "_03": {"normal": "#96BEC7", "hover": "#ABD9E3", "text": "#333333"},  # Light Blue
    "_04": {"normal": "#598693", "hover": "#77ABBA", "text": "#333333"},  # Dark Blue
    "_05": {"normal": "#8190B8", "hover": "#A1AFD9", "text": "#333333"},  # Purple
    "_06": {"normal": "#619C8D", "hover": "#83C4B3", "text": "#333333"},  # Green
    "_07": {"normal": "#C2827C", "hover": "#D99993", "text": "#333333"},  # Light Red
    "_08": {"normal": "#AD4D4E", "hover": "#D46668", "text": "#ffffff"},  # Dark Red
}


def get_selection_set_color(color_code, state="normal"):
    """Get color for selection set buttons"""
    color_data = SELECTION_SET_COLORS.get(color_code, SELECTION_SET_COLORS["_01"])
    return color_data.get(state, color_data["normal"])
