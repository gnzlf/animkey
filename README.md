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

## Maya Compatibility

AnimKey supports Maya 2022 through Maya 2027 on Python 3. The runtime chooses
the Qt binding installed by Maya, so the same package works across both Qt
families:

| Maya releases | Qt binding |
| --- | --- |
| 2022, 2023, 2024 | PySide2 / Qt5 |
| 2025, 2026, 2027 | PySide6 / Qt6 |

Maya 2022 must run in its default Python 3 mode. Its optional legacy Python 2
mode is not supported by AnimKey.

## Stable updates and version history

Settings now includes an **Update** tab. It lists published stable releases
from `gnzlf/animkey` and can upgrade, downgrade, or reinstall any release that
contains the verified AnimKey package. Draft releases and prereleases are never
offered by the updater. AnimKey closes its tool windows and reloads the toolbar
automatically after changing versions; Maya does not need to be restarted.

### AnimKey 1.1.1

- Paste Animation and Paste Selected Animation now prepare destination channels
  in bulk and avoid slow seed-key creation when a base-layer target curve can be
  connected directly.
- Full-range library pastes no longer rebuild a trimmed copy of every saved
  curve before pasting.

### AnimKey 1.1.0

- Copy Animation captures selected curves in bulk and writes its cross-instance
  cache in the background.
- Paste Animation transfers all channels through Maya's native multi-curve
  clipboard while preserving Undo, animation layers, tangents and keys outside
  the copied range.
- Copy/Paste Pose keeps an in-memory clipboard and batches layer setup.
- Updating or uninstalling closes AnimKey windows and reloads the toolbar in the
  current Maya session without asking for a Maya restart.

Preferences and personal data live outside the installed package and are kept
when changing versions. Public repositories work without authentication. For a
private repository, start Maya with an `ANIMKEY_GITHUB_TOKEN` environment
variable that has read access to the repository.

### Publishing a stable version

1. Set `__version__` in `AnimKey/version.py` using `MAJOR.MINOR.PATCH`.
2. Commit and push the version you want to publish.
3. In GitHub, open **Actions → Publish stable AnimKey release → Run workflow**.
4. Enter the exact version and optional release notes.

The workflow creates the version tag, a normal (non-prerelease) GitHub Release,
the updater ZIP, and its SHA-256 checksum. Publishing that release is the single
action that makes the version visible and installable in AnimKey.

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

### Mirror

- Click **MIR** to use a saved precise profile when available, with an automatic
  in-memory quick fallback when no snapshot exists.
- Right-click **MIR → Quick Mirror (No Snapshot)** to force the fast pose-only
  path. It never creates files, changes time, or scans the entire rig.
- Right-click **MIR → Build Precise Mirror Snapshot** once from the rig's
  default pose for calibrated FK/IK, custom attributes, rotated rig roots, and
  all Maya rotation orders.
- Left/right pairing is shared with Select Opposite and supports common tokens,
  namespaces, duplicate DAG names, and conservative geometry-based matching.
- Ambiguous off-plane controls are skipped instead of being mirrored onto the
  wrong target. Use Mirror diagnosis to review unresolved controls and pairing
  confidence.

### Crash Recovery

- Recovery is opt-in from the **CRASH** button, keeping normal animation work
  free from background checkpoint processing.
- Once enabled, it captures animation checkpoints at the configured interval.

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

- Autodesk Maya 2022 through Maya 2027
- Python 3 (included with Maya)
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
