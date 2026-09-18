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

STARTUP_BEGIN = "# >>> AnimKey startup >>>"
STARTUP_END = "# <<< AnimKey startup <<<"
REQUIRED_PACKAGE_FILES = (
    "__init__.py",
    os.path.join("core", "toolbar.py"),
    os.path.join("mods", "maya_compat.py"),
    os.path.join("buttons", "animCrash.py"),
)


def _missing_required_package_files(package_root):
    """Return critical runtime files absent from a source or staged copy."""
    return [
        relative_path
        for relative_path in REQUIRED_PACKAGE_FILES
        if not os.path.isfile(os.path.join(package_root, relative_path))
    ]

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


def _is_within(path, parent):
    try:
        return os.path.commonpath((os.path.realpath(path), os.path.realpath(parent))) == os.path.realpath(parent)
    except Exception:
        return False


def _safe_remove_tree(path, allowed_parent):
    if not path or not _is_within(path, allowed_parent):
        raise RuntimeError("Refusing to remove path outside Maya app directory: {}".format(path))
    if os.path.exists(path):
        shutil.rmtree(path, onerror=_on_rm_error)


def _atomic_write_text(path, content):
    temporary = "{}.{}.tmp".format(path, os.getpid())
    try:
        with open(temporary, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except Exception:
                pass


def _remove_marked_startup(content):
    while STARTUP_BEGIN in content and STARTUP_END in content:
        start = content.index(STARTUP_BEGIN)
        end = content.index(STARTUP_END, start) + len(STARTUP_END)
        content = (content[:start].rstrip() + "\n" + content[end:].lstrip("\r\n"))
    return content

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

    try:
        from AnimKey.mods import uiMod
        uiMod.cleanup_animkey_runtime(full=True)
    except Exception:
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


def install_animkey(launch=True):
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

    missing_source = _missing_required_package_files(animkey_source)
    if missing_source:
        cmds.error(
            "AnimKey source is incomplete. Missing required files: {}".format(
                ", ".join(missing_source)
            )
        )
        return
    
    if not _is_within(animkey_dest, maya_app_dir) or os.path.basename(animkey_dest) != "AnimKey":
        cmds.error("AnimKey destination did not resolve inside Maya's application directory.")
        return

    staging_path = animkey_dest + ".installing_{}".format(os.getpid())
    backup_path = animkey_dest + ".backup_{}".format(os.getpid())

    # Stage and fully copy the new package before touching the working install.
    print("\n  Copying new AnimKey files...")
    try:
        _safe_remove_tree(staging_path, maya_app_dir)
        _safe_remove_tree(backup_path, maya_app_dir)
        shutil.copytree(animkey_source, staging_path, ignore=_copy_ignore)
        missing_staged = _missing_required_package_files(staging_path)
        if missing_staged:
            raise RuntimeError(
                "Staged package is incomplete. Missing: {}".format(
                    ", ".join(missing_staged)
                )
            )
    except Exception as e:
        try:
            _safe_remove_tree(staging_path, maya_app_dir)
        except Exception:
            pass
        cmds.error(f"Could not copy AnimKey: {e}")
        return

    clean_previous_installation()
    try:
        if os.path.exists(animkey_dest):
            os.replace(animkey_dest, backup_path)
        os.replace(staging_path, animkey_dest)
        print("    - Files copied successfully")
    except Exception as e:
        try:
            if not os.path.exists(animkey_dest) and os.path.exists(backup_path):
                os.replace(backup_path, animkey_dest)
        except Exception:
            pass
        cmds.error(f"Could not activate the new AnimKey installation: {e}")
        return

    try:
        _safe_remove_tree(backup_path, maya_app_dir)
    except Exception as e:
        cmds.warning("AnimKey updated, but the old backup could not be removed: {}".format(e))
    
    # Add maya_app_dir to Python path (where AnimKey is now installed)
    if maya_app_dir not in sys.path:
        sys.path.insert(0, maya_app_dir)
    
    # Create userSetup.py entry if it doesn't exist
    user_setup_path = os.path.join(maya_scripts_dir, "userSetup.py")
    animkey_startup_code = STARTUP_BEGIN + '''
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
''' + STARTUP_END + '\n'
    
    # Check if userSetup.py exists and if AnimKey is already in it
    if os.path.exists(user_setup_path):
        with open(user_setup_path, 'r') as f:
            content = f.read()
        
        content = _remove_marked_startup(content)
        if '_animkey_deferred_startup' not in content:
            _atomic_write_text(user_setup_path, content.rstrip() + '\n\n' + animkey_startup_code)
            print("    - Added AnimKey to userSetup.py")
        else:
            print("    - AnimKey already in userSetup.py")
    else:
        # Create new userSetup.py
        _atomic_write_text(user_setup_path, animkey_startup_code)
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
            plugin_temp = plugin_dest + ".{}.tmp".format(os.getpid())
            shutil.copy2(plugin_source, plugin_temp)
            os.replace(plugin_temp, plugin_dest)
            print("    - Installed AnimKey plugin to plug-ins folder")
            print("      (You can enable 'Auto load' in Plugin Manager)")
        except Exception as e:
            print(f"    - Note: Could not copy plugin: {e}")
    
    print("\n  Installation complete!")
    print("=" * 60)

    if not launch:
        print("\n  Files installed successfully. Maya launch was skipped.")
        return animkey_dest
    
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
        print("\n  Run this command to start AnimKey:")
        print("    import AnimKey; AnimKey.show()")

    return animkey_dest


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

    try:
        from AnimKey.mods import uiMod
        uiMod.cleanup_animkey_runtime(full=True)
        uiMod.unload_animkey_entry_plugin()
    except Exception:
        pass
    
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
            content = f.read()
        cleaned = _remove_marked_startup(content)
        if cleaned != content:
            _atomic_write_text(user_setup_path, cleaned)
            print("  ✓ Removed AnimKey from userSetup.py")
        else:
            print("  AnimKey startup block is legacy/unmarked; left userSetup.py unchanged for safety")
    
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
    print("  AnimKey has been removed from the current Maya session.")
    print("=" * 60)


# If running directly (not as drag-drop)
if __name__ == "__main__":
    install_animkey()
