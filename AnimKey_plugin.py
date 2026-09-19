"""
AnimKey Maya Plugin

This plugin allows AnimKey to be loaded via Maya's Plugin Manager.
When loaded, AnimKey will automatically show the toolbar.

Usage:
    1. Open Window > Settings/Preferences > Plug-in Manager
    2. Find 'AnimKey_plugin.py'
    3. Check 'Loaded' and 'Auto load' to start with Maya

Author: AnimKey
"""

import maya.api.OpenMaya as om
import maya.cmds as cmds


def _prioritize_installation():
    import os
    import sys

    maya_app_dir = cmds.internalVar(userAppDir=True)
    destination = os.path.normcase(os.path.realpath(maya_app_dir))
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.realpath(p)) != destination]
    sys.path.insert(0, maya_app_dir)


def maya_useNewAPI():
    """Tell Maya to use the Python API 2.0"""
    pass


# Plugin information
PLUGIN_NAME = "AnimKey"
_prioritize_installation()
from AnimKey.version import __version__ as PLUGIN_VERSION
PLUGIN_AUTHOR = "AnimKey"
VENDOR = "AnimKey"


def initializePlugin(plugin):
    """Called when the plugin is loaded"""
    pluginFn = om.MFnPlugin(plugin, VENDOR, PLUGIN_VERSION)
    
    # Defer the AnimKey startup to ensure Maya UI is ready
    cmds.evalDeferred(_start_animkey, lowestPriority=True)
    
    print(f"AnimKey plugin v{PLUGIN_VERSION} loaded")


def uninitializePlugin(plugin):
    """Called when the plugin is unloaded"""
    pluginFn = om.MFnPlugin(plugin)
    
    try:
        from AnimKey.mods import uiMod
        uiMod.cleanup_animkey_runtime(full=True)
    except Exception:
        pass

    # Try to close AnimKey toolbar
    try:
        if cmds.workspaceControl("AnimKey_Toolbar", query=True, exists=True):
            cmds.deleteUI("AnimKey_Toolbar", control=True)
    except:
        pass
    
    print("AnimKey plugin unloaded")


def _start_animkey():
    """Start AnimKey after Maya is fully loaded"""
    _prioritize_installation()
    
    try:
        import AnimKey
        AnimKey.show()
        print("AnimKey toolbar started successfully")
    except Exception as e:
        cmds.warning(f"AnimKey: Could not start: {e}")
        print(f"AnimKey error: {e}")
