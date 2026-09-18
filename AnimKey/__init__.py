"""
    AnimKey - Advanced Animation Toolset for Maya Animators
    
    A modern, customizable animation toolbar with advanced features
    for professional animators.
    
    Developed with ❤️ for the animation community
"""

from AnimKey.version import __version__
__author__ = "AnimKey Team"


def reload():
    """Reload the AnimKey toolbar"""
    from AnimKey.mods.maya_compat import warn_if_unsupported
    warn_if_unsupported()
    import AnimKey.core.toolbar as t
    t.animkey_toolbar.reload()


def toggle():
    """Toggle the AnimKey toolbar visibility"""
    from AnimKey.mods.maya_compat import warn_if_unsupported
    warn_if_unsupported()
    import AnimKey.core.toolbar as t
    t.animkey_toolbar.toggle()


def show():
    """Show the AnimKey toolbar"""
    from AnimKey.mods.maya_compat import warn_if_unsupported
    warn_if_unsupported()
    import AnimKey.core.toolbar as t
    t.animkey_toolbar.show()


def welcome():
    """Show the welcome window"""
    from AnimKey.core.welcome import show_welcome
    return show_welcome()


def uninstall():
    """
    Uninstall AnimKey completely from Maya.
    Removes all files and cleans up userSetup.py
    """
    import maya.cmds as cmds
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
    
    maya_scripts_dir = cmds.internalVar(userScriptDir=True)
    maya_app_dir = cmds.internalVar(userAppDir=True)
    
    # All possible AnimKey locations
    animkey_paths = [
        os.path.join(maya_app_dir, "AnimKey"),           # New location
        os.path.join(maya_scripts_dir, "AnimKey"),       # Old location
    ]
    user_data_path = os.path.join(maya_app_dir, "AnimKey_user_data")
    plugins_dir = os.path.join(maya_app_dir, "plug-ins")
    plugin_path = os.path.join(plugins_dir, "AnimKey_plugin.py")

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
    
    # 1. Close toolbar first
    try:
        if cmds.workspaceControl("AnimKey_Toolbar", query=True, exists=True):
            cmds.deleteUI("AnimKey_Toolbar", control=True)
            print("\n  ✓ Closed AnimKey toolbar")
    except:
        pass
    
    # 2. Remove from memory
    modules_to_remove = [mod for mod in list(sys.modules.keys()) if 'AnimKey' in mod]
    for mod in modules_to_remove:
        try:
            del sys.modules[mod]
        except:
            pass
    if modules_to_remove:
        print(f"  ✓ Removed {len(modules_to_remove)} modules from memory")
    
    # 3. Remove AnimKey folders
    for animkey_path in animkey_paths:
        if os.path.exists(animkey_path):
            try:
                shutil.rmtree(animkey_path, onerror=_on_rm_error)
                print(f"  ✓ Removed: {animkey_path}")
            except Exception as e:
                print(f"  ✗ Could not remove {animkey_path}: {e}")
    
    # 4. Remove plugin
    if os.path.exists(plugin_path):
        try:
            os.remove(plugin_path)
            print(f"  ✓ Removed plugin: {plugin_path}")
        except Exception as e:
            print(f"  ✗ Could not remove plugin: {e}")
    
    # 5. Clean userSetup.py
    user_setup_path = os.path.join(maya_scripts_dir, "userSetup.py")
    if os.path.exists(user_setup_path):
        try:
            with open(user_setup_path, 'r') as f:
                content = f.read()
            
            if 'AnimKey' in content or '_animkey_' in content:
                lines = content.split('\n')
                new_lines = []
                skip_block = False
                
                for line in lines:
                    # Skip AnimKey related blocks
                    if '# AnimKey Auto-start' in line:
                        skip_block = True
                        continue
                    if 'def _animkey_deferred_startup' in line:
                        skip_block = True
                        continue
                    
                    if skip_block:
                        # End of block detection
                        if line.strip() == '' and len(new_lines) > 0:
                            # Check if next non-empty line is not indented
                            continue
                        if 'AnimKey' in line or '_animkey_' in line:
                            continue
                        if line.startswith(' ') or line.startswith('\t'):
                            continue
                        if line.strip().startswith('try:') or line.strip().startswith('except:') or line.strip() == 'pass':
                            continue
                        # Non-indented, non-AnimKey line = end of block
                        skip_block = False
                    
                    if not skip_block and 'AnimKey' not in line and '_animkey_' not in line:
                        new_lines.append(line)
                
                # Remove excessive blank lines
                while new_lines and new_lines[-1].strip() == '':
                    new_lines.pop()
                
                with open(user_setup_path, 'w') as f:
                    f.write('\n'.join(new_lines))
                
                print(f"  ✓ Cleaned userSetup.py")
        except Exception as e:
            print(f"  ✗ Could not clean userSetup.py: {e}")
    
    # 6. User data was decided in the single uninstall dialog.
    if delete_user_data and os.path.exists(user_data_path):
        try:
            shutil.rmtree(user_data_path, onerror=_on_rm_error)
            print(f"  Removed user data: {user_data_path}")
        except Exception as e:
            print(f"  Could not remove user data: {e}")
    elif os.path.exists(user_data_path):
        print("  User data preserved")
    
    print("\n" + "=" * 60)
    print("  ✓ Uninstallation complete!")
    print("  AnimKey has been removed from the current Maya session.")
    print("=" * 60)
