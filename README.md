# AnimKey - Advanced Animation Toolset for Maya

<p align="center">
  <strong>A modern, customizable animation toolbar for Maya animators</strong>
</p>

---

## ✨ Features

- **Modern UI Design** - Beautiful, customizable interface with multiple themes
- **Tween Machine** - Quickly blend between keyframes
- **Blend Slider** - Blend to previous/next frames or default values
- **Curve Tools** - Smooth, wave, scale, linearize, and more
- **Modular Buttons** - Each button function is in a separate file for easy customization
- **6 Built-in Themes** - Including Midnight Aurora, Cyber Neon, Forest Depths, and more
- **4K Display Support** - Automatically scales for high-DPI displays

---

## 🚀 Installation

### Method 1: Drag & Drop (Recommended)
1. Drag the `AnimKey_Install.py` file into Maya's 3D viewport
2. AnimKey will install automatically and appear at the top of Maya

### Method 2: Manual Installation
1. Copy the `AnimKey` folder to your Maya scripts directory:
   - Windows: `C:\Users\<username>\Documents\maya\<version>\scripts\`
   - macOS: `~/Library/Preferences/Autodesk/maya/<version>/scripts/`
   - Linux: `~/maya/<version>/scripts/`

2. Add to your `userSetup.py`:
```python
import AnimKey
AnimKey.show()
```

---

## 📖 Usage

### Show/Hide Toolbar
```python
import AnimKey
AnimKey.toggle()  # Toggle visibility
AnimKey.show()    # Show toolbar
AnimKey.reload()  # Reload toolbar
```

### Change Theme
Use the theme dropdown in the toolbar, or:
```python
import AnimKey
from AnimKey.mods.themes import ThemeManager
ThemeManager.set_theme("cyber_neon")
AnimKey.reload()
```

### Available Themes
- `midnight_aurora` - Dark theme with vibrant aurora accents
- `cyber_neon` - Cyberpunk aesthetic with neon highlights
- `forest_depths` - Organic nature-inspired colors
- `sunset_gradient` - Warm sunset gradients
- `arctic_frost` - Clean minimalist light theme
- `maya_classic` - Inspired by Maya's native UI

---

## 🎛️ Toolbar Sections

### Key Frame Controls
- `<` `>` - Move selected keyframes left/right
- `-` `+` - Remove/Add inbetween frames
- `×` - Clear selected keyframes
- `S` - Select all animation in scene

### Tween Slider (T)
Blend between previous and next keyframes:
- 0% = Previous keyframe value
- 50% = Middle (default)
- 100% = Next keyframe value

### Blend Slider (BL)
Multiple modes:
- **BL** (Blend) - Blend to neighboring keyframes
- **PP** (Push/Pull) - Push values away or pull toward center
- **DF** (Default) - Blend to default values

### Curve Tools
Apply modifications to selected curves:
- **Smooth** - Smooth out the curve
- **Wave** - Add wave pattern
- **Scale** - Scale curve values
- **Linear** - Linearize between first and last keys
- **Flat** - Flatten to average value
- **Ease In/Out** - Add easing
- **Noise** - Add random noise

---

## 📁 Project Structure

```
AnimKey/
├── __init__.py           # Main entry point
├── core/
│   └── toolbar.py        # Main toolbar class
├── mods/
│   ├── themes.py         # Theme definitions
│   ├── styleMod.py       # Qt stylesheet generators
│   ├── configMod.py      # Configuration management
│   └── uiMod.py          # Custom UI widgets
├── buttons/              # Button functions (modular)
│   ├── isolate.py
│   ├── resetValues.py
│   ├── copy_paste.py
│   └── select_hierarchy.py
└── data/
    └── config/
        └── default_config.json
```

---

## 🎨 Customization

### Creating Custom Themes
Add a new theme to `AnimKey/mods/themes.py`:

```python
THEMES["my_theme"] = {
    "name": "My Custom Theme",
    "description": "A custom theme",
    "bg_primary": "#1a1a1a",
    "accent_primary": "#ff6600",
    # ... other color definitions
}
```

### Adding Custom Buttons
Create a new file in `AnimKey/buttons/`:

```python
# AnimKey/buttons/my_button.py

import maya.cmds as cmds

def execute(*args):
    """Called when button is clicked"""
    # Your code here
    pass

def get_info():
    return {
        "name": "My Button",
        "tooltip": "Description",
        "icon": "my_icon.svg",
    }
```

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Coming soon... | ... |

---

## 🔧 Requirements

- Autodesk Maya 2020 or later
- Python 3.x (included with Maya 2022+)
- PySide2 or PySide6 (included with Maya)

---

## 📝 License

This project is open source. Feel free to modify and distribute.

---

## 🙏 Credits

AnimKey is an independent animation workflow toolkit for Maya animators.

---

<p align="center">
  Made with ❤️ for the animation community
</p>

