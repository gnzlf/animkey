"""Shared dark material colors, independent of Qt and Maya."""

SURFACE = "#191d25"
CONTROL = "#252b36"
HOVER = "#313a48"
PRESSED = "#1b2029"
BORDER = "#46505f"
TEXT = "#edf1f7"
SECONDARY = "#c5cdd9"
MUTED = "#9da8b9"
ACCENT = "#8fc5ff"

# Dark glass: keep about a third of the blurred backdrop visible. The former
# dark layers compounded to over 90% opacity, hiding the glass entirely.
NATIVE_TINT = 0x861D1916
SURFACE_RGBA = (20, 23, 30, 64)
FALLBACK_RGBA = (25, 29, 37, 250)
OUTLINE_RGBA = (222, 233, 250, 94)
