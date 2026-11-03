# Hover, Parent ranges and Tumble: 2026-10-21

## Changes

- Toolbar buttons grow visually by about 6% on hover over 110 ms. Widget size,
  layout spacing and clickable areas stay fixed. Disabled/hidden buttons reset.
- Slider tracks keep their size; only the moving handle grows from radius 7 to
  8.5 pixels. Hover does not emit animation-edit signals. Preset dots grow subtly.
- Parent now exposes Copy/Paste All Animation and Selected Timeline Range,
  alongside Current Frame and Playback Range. New actions support shortcuts.
- Parent range copying samples evaluated relative matrices at every frame and
  retains fractional upstream keys, rather than repeating the current pose.
- Range pastes preserve current time, selection and Auto Key, use one undo chunk,
  and leave external keys intact. Parent/child targets are solved in DAG order.
- Frame and animation clipboards are separate. Frame paste does not overwrite
  the active animation clipboard. Latest copied data survives session reloads.
- Tumble preferences are excluded from animation undo without clearing history.
  Lightweight scene/tool callbacks repair native settings even when the option
  is off. Closing AnimKey removes them and cancels pending deferred repairs.
- Enabled mode uses native selection-pivot navigation; disabled mode keeps the
  center of interest established by Frame Selection. Cursor-hit pivot is off in
  both modes. No camera transforms or rig transforms are edited by this helper.

World Space already had the requested scopes. Tests now verify its range
boundaries against Parent and its playback-only paste behavior.

## Verification

Maya 2024 standalone, Windows: 37 focused tests passed:

```text
test_hover_and_parent_menu
test_link_objects_ranges
test_channelbox_viewport_helpers
test_toolbar_tangent_transition.ToolbarTransitionTests.test_transition_finishes_without_full_refresh_per_button_per_tick
```

Qt render comparisons were inspected in `dist/hover-check`. A native QPushButton
was rendered alongside the new button. Standalone needed an explicitly loaded
Arial font for the render test, not for production code.

Navigation tests use native viewFit/tumble commands with mocked tumble contexts
because interactive tool contexts are not available in Maya batch. They verify
the selected target remains centered while orbiting, with both modes, without
changing the object's matrix. Actual Alt+mouse interaction remains to be checked
in a running Maya UI. No user scene was opened or saved.

The older toolbar lifecycle suites could not fully run: this checkout lacks
`AnimKey.core.runtime_lifecycle`, which those tests import. This unrelated
missing module was not reconstructed as part of these changes. Maya 2022/2025/
2026 folders exist but contain no mayapy executable at the expected paths, so
this validation does not certify other Maya versions or Qt6.

## Installed Copy

Six changed modules were copied to `C:/Users/ANIM-gonzalo/Documents/maya/AnimKey`
and verified against source using SHA256. The previous installed files are in:

`dist/install-backup-20261021-91b013d2`

No release, Git commit or push was made for this request. Reopen Maya to load
the updated modules.

## Native Navigation Reference

[Autodesk Tumble Tool options](https://help.autodesk.com/cloudhelp/ENU/MayaCRE-Basics/files/GUID-A3305185-DFBD-42BF-8A70-59208C2C9887.htm)
distinguishes the cursor-hit pivot from the automatically updated selection pivot.
