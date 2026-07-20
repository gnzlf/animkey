"""
    AnimKey - Drag & Drop Installer for Maya
    
    Simply drag and drop this file into Maya's viewport to install AnimKey.
    
    Usage:
        1. Drag this file into Maya's 3D viewport
        2. AnimKey will be installed automatically
        3. The toolbar will appear at the top of Maya
        
    To uninstall:
        Run: AnimKey.uninstall() in the Script Editor
"""

import maya.cmds as cmds
import maya.mel as mel
import os
import sys
import shutil
import stat

def _on_rm_error(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

def _copy_ignore(src, names):
    """
    Skip generated/cache folders.

    The motion-trail viewport plugin lives inside AnimKey/plugins and must be
    copied with the toolbar package so the runtime loader can find it.
    """
    ignored = set()
    for name in names:
        lower_name = name.lower()
        if lower_name in {"__pycache__", ".git", ".pytest_cache"}:
            ignored.add(name)
        elif lower_name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored

def onMayaDroppedPythonFile(*args):
    """
    This function is automatically called when the file is dropped into Maya.
    """
    install_animkey()


def clean_previous_installation():
    """
    Clean up any previous AnimKey installation from memory and disk.
    """
    print("\n  Cleaning previous installation...")
    
    # 1. Close workspace if exists
    try:
        if cmds.workspaceControl("AnimKey_Toolbar", query=True, exists=True):
            cmds.deleteUI("AnimKey_Toolbar", control=True)
            print("    - Closed existing toolbar")
    except:
        pass
    
    # 2. Remove modules from memory
    modules_to_remove = [mod for mod in list(sys.modules.keys()) if 'AnimKey' in mod]
    for mod in modules_to_remove:
        try:
            del sys.modules[mod]
        except:
            pass
    if modules_to_remove:
        print(f"    - Removed {len(modules_to_remove)} modules from memory")
    
    # 3. Remove from sys.path if present
    maya_scripts_dir = cmds.internalVar(userScriptDir=True)
    paths_to_check = [
        maya_scripts_dir,
        os.path.join(maya_scripts_dir, "AnimKey"),
    ]
    for path in paths_to_check:
        if path in sys.path:
            try:
                sys.path.remove(path)
            except:
                pass


def install_animkey():
    """
    Install AnimKey to Maya's user directory.
    """
    # Get the directory where this installer is located
    installer_path = os.path.dirname(os.path.abspath(__file__))
    animkey_source = os.path.join(installer_path, "AnimKey")
    
    # Get Maya's app directory (documents/maya) - NOT the version-specific scripts folder
    # This puts AnimKey next to AnimKey_user_data for cleaner organization
    maya_app_dir = cmds.internalVar(userAppDir=True)
    maya_scripts_dir = cmds.internalVar(userScriptDir=True)
    animkey_dest = os.path.join(maya_app_dir, "AnimKey")
    
    print("\n" + "=" * 60)
    print("  AnimKey Installation")
    print("=" * 60)
    print(f"\n  Source: {animkey_source}")
    print(f"  Destination: {animkey_dest}")
    
    # Check if source exists
    if not os.path.exists(animkey_source):
        cmds.error("AnimKey source folder not found. Please ensure the AnimKey folder is in the same directory as this installer.")
        return
    
    # CLEAN PREVIOUS INSTALLATION FIRST
    clean_previous_installation()
    
    # Remove existing installation from disk
    if os.path.exists(animkey_dest):
        print("\n  Removing previous installation from disk...")
        try:
            shutil.rmtree(animkey_dest, onerror=_on_rm_error)
            print("    - Removed old AnimKey folder")
        except Exception as e:
            cmds.warning(f"Could not remove existing installation: {e}")
            cmds.warning("Please close Maya, delete the AnimKey folder manually, and try again.")
            return
    
    # Copy AnimKey to scripts directory
    print("\n  Copying new AnimKey files...")
    try:
        shutil.copytree(animkey_source, animkey_dest, ignore=_copy_ignore)
        print("    - Files copied successfully")
    except Exception as e:
        if os.path.exists(animkey_dest):
            try:
                shutil.rmtree(animkey_dest, onerror=_on_rm_error)
            except Exception:
                pass
        cmds.error(f"Could not copy AnimKey: {e}")
        return
    
    # Add maya_app_dir to Python path (where AnimKey is now installed)
    if maya_app_dir not in sys.path:
        sys.path.insert(0, maya_app_dir)
    
    # Create userSetup.py entry if it doesn't exist
    user_setup_path = os.path.join(maya_scripts_dir, "userSetup.py")
    animkey_startup_code = '''
# AnimKey Auto-start
def _animkey_deferred_startup():
    """Deferred startup to ensure Maya is fully loaded"""
    import sys
    import maya.cmds as cmds
    
    # Add AnimKey installation path to Python path
    maya_app_dir = cmds.internalVar(userAppDir=True)
    if maya_app_dir not in sys.path:
        sys.path.insert(0, maya_app_dir)
    
    try:
        import AnimKey
        AnimKey.show()
    except Exception as e:
        print(f"AnimKey: Could not auto-start: {e}")

try:
    import maya.utils
    maya.utils.executeDeferred(_animkey_deferred_startup)
except:
    pass
'''
    
    # Check if userSetup.py exists and if AnimKey is already in it
    if os.path.exists(user_setup_path):
        with open(user_setup_path, 'r') as f:
            content = f.read()
        
        if 'AnimKey' not in content:
            # Append to existing userSetup.py
            with open(user_setup_path, 'a') as f:
                f.write('\n' + animkey_startup_code)
            print("    - Added AnimKey to userSetup.py")
        else:
            print("    - AnimKey already in userSetup.py")
    else:
        # Create new userSetup.py
        with open(user_setup_path, 'w') as f:
            f.write(animkey_startup_code)
        print("    - Created userSetup.py with AnimKey startup")
    
    # Install plugin to Maya's plug-ins folder
    maya_app_dir = cmds.internalVar(userAppDir=True)
    plugins_dir = os.path.join(maya_app_dir, "plug-ins")
    plugin_source = os.path.join(installer_path, "AnimKey_plugin.py")
    plugin_dest = os.path.join(plugins_dir, "AnimKey_plugin.py")
    
    if os.path.exists(plugin_source):
        # Ensure plugins directory exists
        if not os.path.exists(plugins_dir):
            os.makedirs(plugins_dir)
        
        # Copy plugin file
        try:
            shutil.copy2(plugin_source, plugin_dest)
            print("    - Installed AnimKey plugin to plug-ins folder")
            print("      (You can enable 'Auto load' in Plugin Manager)")
        except Exception as e:
            print(f"    - Note: Could not copy plugin: {e}")
    
    print("\n  Installation complete!")
    print("=" * 60)
    
    # Launch AnimKey
    print("\n  Launching AnimKey...")
    
    try:
        # Force fresh import
        import importlib
        import AnimKey
        importlib.reload(AnimKey)
        
        # Reload submodules
        from AnimKey.mods import themes, styleMod, configMod, uiMod
        importlib.reload(themes)
        importlib.reload(styleMod)
        importlib.reload(configMod)
        importlib.reload(uiMod)
        
        from AnimKey.core import toolbar, welcome
        importlib.reload(toolbar)
        importlib.reload(welcome)
        
        # Show the toolbar
        AnimKey.show()
        
        # Show welcome window on first install
        try:
            from AnimKey.core.welcome import show_welcome
            show_welcome()
            print("\n  ✓ Welcome window displayed!")
        except Exception as we:
            print(f"\n  Note: Could not show welcome window: {we}")
        
        print("\n  ✓ AnimKey is now running!")
        print("\n  To show/hide the toolbar, run:")
        print("    import AnimKey; AnimKey.toggle()")
        print("\n" + "=" * 60)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        cmds.warning(f"AnimKey installed but could not start automatically: {e}")
        print("\n  Please restart Maya or run:")
        print("    import AnimKey; AnimKey.show()")


def uninstall_animkey():
    """
    Uninstall AnimKey from Maya.
    """
    maya_scripts_dir = cmds.internalVar(userScriptDir=True)
    maya_app_dir = cmds.internalVar(userAppDir=True)
    
    # AnimKey can be in either location (old: scripts, new: maya_app_dir)
    animkey_path_new = os.path.join(maya_app_dir, "AnimKey")
    animkey_path_old = os.path.join(maya_scripts_dir, "AnimKey")
    user_data_path = os.path.join(maya_app_dir, "AnimKey_user_data")

    try:
        from AnimKey.core.settings import ask_uninstall_choice, AnimKeyUninstallDialog
        choice = ask_uninstall_choice(None, user_data_exists=os.path.exists(user_data_path))
        if choice is None:
            print("  AnimKey uninstall cancelled")
            return
        delete_user_data = choice == AnimKeyUninstallDialog.DELETE_ALL
    except Exception as e:
        cmds.warning(f"AnimKey: Could not show uninstall dialog, keeping user data by default: {e}")
        delete_user_data = False
    
    print("=" * 60)
    print("  AnimKey Uninstallation")
    print("=" * 60)
    
    # Clean from memory first
    clean_previous_installation()
    
    # Remove AnimKey folder from new location
    if os.path.exists(animkey_path_new):
        try:
            shutil.rmtree(animkey_path_new, onerror=_on_rm_error)
            print("\n  ✓ Removed AnimKey folder (from maya app dir)")
        except Exception as e:
            cmds.warning(f"Could not remove AnimKey folder: {e}")
    
    # Also check old location (scripts folder) and remove if exists
    if os.path.exists(animkey_path_old):
        try:
            shutil.rmtree(animkey_path_old, onerror=_on_rm_error)
            print("  ✓ Removed AnimKey folder (from scripts)")
        except Exception as e:
            cmds.warning(f"Could not remove old AnimKey folder: {e}")
    
    if not os.path.exists(animkey_path_new) and not os.path.exists(animkey_path_old):
        print("\n  AnimKey folder not found")
    
    # Remove from userSetup.py
    user_setup_path = os.path.join(maya_scripts_dir, "userSetup.py")
    if os.path.exists(user_setup_path):
        with open(user_setup_path, 'r') as f:
            lines = f.readlines()
        
        # Filter out AnimKey related lines (including the deferred function)
        new_lines = []
        skip_block = False
        for line in lines:
            # Start skipping at AnimKey Auto-start comment
            if '# AnimKey Auto-start' in line:
                skip_block = True
                continue
            # Also skip the deferred function definition
            if 'def _animkey_deferred_startup' in line:
                skip_block = True
                continue
            # Stop skipping after we see a line that ends the block
            if skip_block:
                # End of block: empty line followed by non-indented code, or next comment block
                if line.strip() == '' and not line.startswith(' ') and not line.startswith('\t'):
                    skip_block = False
                    continue
                # Still in the AnimKey block
                if 'AnimKey' in line or 'animkey' in line.lower() or line.startswith(' ') or line.startswith('\t'):
                    continue
                # Line doesn't belong to AnimKey block anymore
                if not line.startswith(' ') and not line.startswith('\t') and line.strip() != '':
                    skip_block = False
            if 'AnimKey' in line or '_animkey_deferred_startup' in line:
                continue
            new_lines.append(line)
        
        with open(user_setup_path, 'w') as f:
            f.writelines(new_lines)
        
        print("  ✓ Removed AnimKey from userSetup.py")
    
    # Remove plugin from plug-ins folder
    plugins_dir = os.path.join(maya_app_dir, "plug-ins")
    plugin_path = os.path.join(plugins_dir, "AnimKey_plugin.py")
    if os.path.exists(plugin_path):
        try:
            os.remove(plugin_path)
            print("  ✓ Removed AnimKey plugin")
        except Exception as e:
            print(f"  - Note: Could not remove plugin: {e}")
    
    # User data was decided in the single uninstall dialog.
    if delete_user_data and os.path.exists(user_data_path):
        try:
            shutil.rmtree(user_data_path, onerror=_on_rm_error)
            print("  Removed AnimKey user data folder")
        except Exception as e:
            cmds.warning(f"Could not remove user data: {e}")
    elif os.path.exists(user_data_path):
        print("  User data preserved")
    
    print("\n  Uninstallation complete!")
    print("  Please restart Maya to complete the process.")
    print("=" * 60)


# If running directly (not as drag-drop)
if __name__ == "__main__":
    install_animkey()
