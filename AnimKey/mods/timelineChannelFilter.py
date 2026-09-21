"""
    Timeline Channel Box key tick filtering.

    Uses Maya's native timeControl showKeys mode so the Time Slider displays
    ticks for selected Channel Box attributes when any are selected.
"""

import maya.cmds as cmds
import maya.mel as mel

from AnimKey.mods import configMod as config


SETTING_KEY = "timeline_channel_box_key_filter"
CHANNEL_BOX_NAME = "mainChannelBox"
_active = False
_original_show_keys = None
_original_show_keys_combined = None


def _get_time_slider():
    """Return Maya's main Time Slider control name."""
    try:
        return mel.eval("$tmpVar=$gPlayBackSlider")
    except Exception:
        return None


def _get_channel_box():
    try:
        return mel.eval('global string $gChannelBoxName; $temp=$gChannelBoxName;') or CHANNEL_BOX_NAME
    except Exception:
        return CHANNEL_BOX_NAME


def is_enabled():
    """Return whether AnimKey should filter key ticks by Channel Box selection."""
    return bool(config.get_setting(SETTING_KEY, False))


def set_enabled(enabled):
    """Persist and apply the Channel Box key tick filter setting."""
    config.set_setting(SETTING_KEY, bool(enabled))
    apply()


def apply(enabled=None):
    """
    Apply the key tick mode to Maya's Time Slider.

    Enabled:
        showKeys="mainChannelBox" + showKeysCombined=True
        If channels are selected, only those channel keys are shown.
        If no channels are selected, Maya falls back to active object keys.

    Disabled:
        Restore Maya's regular active-object key ticks.
    """
    global _active, _original_show_keys, _original_show_keys_combined
    if enabled is None:
        enabled = is_enabled()

    time_slider = _get_time_slider()
    if not time_slider:
        return False

    try:
        if enabled:
            if not _active:
                current = cmds.timeControl(time_slider, query=True, showKeys=True)
                combined = cmds.timeControl(
                    time_slider, query=True, showKeysCombined=True
                )
                # A previous AnimKey version may have left its mode active
                # when this module was reloaded. Recover Maya's default.
                if current == _get_channel_box() and combined:
                    current, combined = "active", False
                _original_show_keys = current or "active"
                _original_show_keys_combined = bool(combined)
            cmds.timeControl(
                time_slider,
                edit=True,
                showKeys=_get_channel_box(),
                showKeysCombined=True,
                forceRefresh=True,
            )
            _active = True
        else:
            cmds.timeControl(
                time_slider,
                edit=True,
                showKeys=_original_show_keys or "active",
                showKeysCombined=(
                    _original_show_keys_combined
                    if _original_show_keys_combined is not None else False
                ),
                forceRefresh=True,
            )
            _active = False
            _original_show_keys = None
            _original_show_keys_combined = None
        return True
    except Exception as exc:
        cmds.warning("AnimKey: Could not apply Timeline Channel Box key filter: {0}".format(exc))
        return False
