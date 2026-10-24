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
- **Temp Pivot + Temp Controls** - Separate adjacent buttons: a temporary pivot
  proxy and persistent Simple, Group, Aim and FK animation systems.

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

The drag-and-drop installer uses `Documents/maya/AnimKey` on Windows. If a
source checkout was loaded instead, Update locates the existing Maya installation
and reloads that installed copy after upgrading or downgrading. The source
checkout is kept untouched.

### AnimKey 1.2.7

- Unify the toolbar and floating tools with neutral grey glass, compact Brush
  controls, a round Flash Buttons background and stronger button hover feedback.
  Use Maya's own UI font and animate windows and slider menus upward on opening.
- Keep the Selection Sets canvas transparent in Row and Board modes, including
  resizing, new tabs and custom background images.
- Restore Temp Controls group following and frame-by-frame baking, including
  selected parent/child hierarchies. Organize the tool into Create, Controls and
  Tools, with switches, a display-size slider and working tab navigation.
- Move Temp Pivot options into its own window. Right clicks on Temp Controls
  and Temp Pivot open their settings directly, without nested menus.
- Fix Follow Cam viewport selection in split and four-panel layouts, even when
  panels share a camera, and restore previous views when replacing a follow cam.

### AnimKey 1.2.6

- Remove repeated GPU readbacks and blur work from window dragging and resizing.
  Pan a cached, half-resolution application backdrop immediately as the window
  moves; refresh the scene snapshot once movement stops and the mouse is released.
- Preserve rounded transparent corners, neutral grey glass and the proximity
  fade (50% away, full opacity nearby). Release the cached image on hide/close.

### AnimKey 1.2.5

- Restore floating tool windows to 50% opacity when the mouse moves away and
  full opacity when it approaches. Keep owned dropdowns and mouse drags fully
  visible; modal dialogs and the toolbar keep their normal opacity.
- Update the frosted backdrop during continuous window movement and resizing,
  instead of postponing every update until movement stops. Throttle captures
  according to their cost and stop viewport reads when the window is idle.
- Cancel proximity checks, opacity animations and pending backdrop work when
  a tool hides or closes.

### AnimKey 1.2.4

- Paint frosted glass inside the rounded Qt alpha surface. Disable the separate
  Windows backdrop that could draw a square canvas outside the curved corners.
- Include Maya's GPU viewport image in the application backdrop instead of
  relying only on QWidget rendering, which could leave the glass black or flat.
  Blur the cached image offscreen on open, move and resize; no continuous
  viewport readback or desktop capture is used.
- Use a lighter neutral grey so windows and the toolbar stand out from Maya.
  Keep text contrast and translucent rounded controls.

### AnimKey 1.2.3

- Improve Windows glass compatibility, with a cached, blurred application
  backdrop when native composition is unavailable or transparency is disabled.
- Clip native window corners in physical pixels at different display scales.
  Magnetically anchored panels keep a rounded body without pointed tails;
  detaching them preserves their mask immediately.
- Use neutral dark grey for the toolbar, controls and default Tweener slider.
  Keep native toolbar rehosting disabled in Maya to preserve dock stability.
- Stop pending toolbar animations and previews during a full runtime unload.

### AnimKey 1.2.2

- Fix Maya crashes when refreshing, floating or closing the toolbar. Controls
  stay inside Maya's dock; the additional native acrylic toolbar window is
  disabled in Maya. Tool windows retain their dark frosted material.
- Refresh waits until the current UI callback finishes, coalesces repeated
  requests and stops pending animations and previews before rebuilding.
- Flash Buttons return to floating circles without a square background.
- Draw uses a compact horizontal control bar with a combined header.

### AnimKey 1.2.1

- Dark frosted glass windows and toolbar, with native Windows blur, rounded
  corners and translucent buttons, fields and sliders. The docked toolbar
  follows Maya when moving, resizing, floating, docking or minimizing.
- Settings and tool windows share the same material, including Recovery,
  Retimer, Animation Cleaner and Temp Controls. Outer square borders are removed.
- Temp Pivot uses a separate proxy while preserving the rig's original pivots
  and hierarchy. Temp Controls adds persistent Simple, Group, Aim and FK Chain
  systems, space controls, Bake/Revert and setup import/export.
- Copy/Paste Animation and Pose gain file-based Save/Load and improved
  namespace matching, range boundaries and animation-layer preservation.
- Draw uses separate floating controls and a transparent viewport canvas,
  with deferred stroke rendering and lighter viewport synchronization.

### AnimKey 1.2.0

- Bake Animation resamples channels on the active animation layer, preserving
  additive and Override layer values, layer weights, other layers, and keys
  outside the bake range. The bake remains a single Undo operation.
- Tumble Around Selection uses Maya's selection pivot without switching the
  orbit center to the object under the mouse. Disabling it restores normal
  center-of-interest navigation so framing with F remains stable when tumbling.
- Navigation settings left by earlier versions are repaired even when Tumble
  Around Selection is disabled, and callbacks are cleaned up after reloading.

### AnimKey 1.1.2

- Draw now keeps sketch strokes aligned when Maya viewports are resized,
  docked side by side, or maximized with the spacebar.
- The Draw renderer uses the active camera framing consistently for painting,
  preview, pan/zoom, and export so strokes no longer stretch with panel changes.

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
3. Either push a matching `vMAJOR.MINOR.PATCH` tag, or in GitHub open
   **Actions → Publish stable AnimKey release → Run workflow**.
4. If running the workflow manually, enter the exact version and optional
   release notes.

The workflow creates a normal (non-prerelease) GitHub Release, the updater ZIP,
and its SHA-256 checksum. Publishing that release is the single action that
makes the version visible and installable in AnimKey.

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

### Copy/paste animation and poses

Click **C** to copy animation. Right-click C for Copy/Paste Animation, Paste
Insert, Copy/Paste Pose, and Save/Load Animation or Pose. Save opens a file
chooser so you can store `.animkey_anim` and `.animkey_pose` files anywhere.

Select the source controls and copy, then select the destination controls and
paste. Matching control names work across namespaces and selection order. A
single copied control can be pasted to multiple selected controls.

Paste Animation uses the original times. Paste Insert aligns the copied range
to the current frame and replaces keys inside that range, keeping keys outside
it. Copy/Paste Pose uses the evaluated pose and keys the destination at the
current frame, including on additive or Override layers.

With an animation layer active, Copy Animation captures that layer and Paste
Animation targets the layer active at paste time. Copying from Base Animation
captures the control's layer stack; a clip with multiple layers retains that
stack. Existing shared layer settings and weight keys stay intact. Timeline
ranges include interpolated boundary keys, and pasted automatic endpoint
tangents are fixed to preserve the copied curve shape.

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

- Click **MIR** to use that rig's saved snapshot automatically, or the standard
  mirror when that rig has no snapshot. Each selected rig is resolved separately.
- Right-click **MIR → Snapshot** once from the rig's
  default pose for calibrated FK/IK, custom attributes, rotated rig roots, and
  all Maya rotation orders.
- Left/right pairing is shared with Select Opposite and supports common tokens,
  namespaces, duplicate DAG names, and conservative geometry-based matching.
- Ambiguous off-plane controls are skipped instead of being mirrored onto the
  wrong target. Use Mirror diagnosis to review unresolved controls and pairing
  confidence.

### Worldspace and Link Objects

- Both menus offer Copy and Paste for **All Animation**, **Selected Timeline
  Range**, **Playback Range**, and **Current Frame**.
- All Animation copies the full animation start/end range. Playback Range uses
  the playback start/end; Selected Timeline Range uses the highlighted frames
  and requires an active timeline selection.
- Link Objects uses the last selected object as the driver. Range copies record
  the evaluated relationship on every frame, including fractional key times.
  A copied current-frame relationship can also be repeated over a paste range.
- Click copies a frame; Shift-click pastes a frame. Live pin/auto-link options
  are no longer exposed in the menus or shortcuts.

### Temp Pivot and Temp Control

- **PIV** toggles a temporary proxy. Shift uses the last selected object as an
  unaffected anchor, Ctrl centers on the selected transform positions, and
  Ctrl+Shift keeps the reference frame fixed in world space. Alt edits the
  proxy pivot; Ctrl+Alt resets it. Rig pivots remain unchanged.
- **TCT**, next to PIV, opens creation of Simple, Group, Aim or FK Chain
  controls. Position and Orientation spaces can be linked or independent.
  Aim starts with an editable setup and is finalized in the panel.
- The Temp Controls panel exposes Bake/Revert, Grab/Release, control appearance,
  extra parents/children, pivots and Group/FK membership. Closing the panel
  leaves the scene systems intact. Copy Animation is between HIR and MIR.
- Import/export uses AnimKey JSON and preserves the control setup.

### Crash Recovery

- Recovery is opt-in from the **CRASH** button, keeping normal animation work
  free from background checkpoint processing.
- Once enabled, it captures animation checkpoints at the configured interval.

---

## 📁 Project Structure

Development tests and release utilities are kept in `.github/tests` and
`.github/tools`. They are excluded from the installer ZIP and the Maya package.
Run updater tests from the repository root with
`python -m unittest discover -s .github/tests -p test_updater.py`, and build a
release with `python .github/tools/build_release.py --version MAJOR.MINOR.PATCH`.

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
