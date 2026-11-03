"""Reload changed copy/paste actions while keeping the current Maya session intact."""
import ast
import importlib
import json
from pathlib import Path
import threading
import traceback
from unittest import mock
import maya.cmds as cmds
from AnimKey.core import animation_curve_transfer, toolbar, settings
from AnimKey.buttons import copyAnimation, flashbuttons
from AnimKey.mods import hotkeysMod

ROOT = Path(__file__).resolve().parents[2]


def state():
    return {"scene": cmds.file(q=True, sceneName=True), "modified": cmds.file(q=True, modified=True),
            "frame": cmds.currentTime(q=True), "selection": cmds.ls(sl=True, long=True) or [],
            "autoKey": cmds.autoKeyframe(q=True, state=True),
            "layers": cmds.ls(type="animLayer") or [],
            "undoName": cmds.undoInfo(q=True, undoName=True),
            "curveCount": len(cmds.ls(type="animCurve") or [])}


def replace_definition(module, name, cls=None):
    path = module.__file__
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    body = tree.body if cls is None else next(item for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == cls).body
    node = next(item for item in body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), module.__dict__, namespace)
    if cls:
        setattr(getattr(module, cls), name, namespace[name])
    else:
        setattr(module, name, namespace[name])


report = {"before": state(), "reloaded": False}
try:
    for worker in threading.enumerate():
        if worker.name == "AnimKeyJsonWriter":
            worker.join(5)
            if worker.is_alive():
                raise RuntimeError("Clipboard cache write is still active; retry after it finishes")
    saved = {name: getattr(copyAnimation, name) for name in (
        "_anim_buffer", "_anim_sources", "_anim_source_snapshots", "_anim_layer_metadata",
        "_last_disk_mtime", "_buffer_is_current", "_pose_buffer", "_pose_buffer_mtime", "_pose_buffer_is_current")}
    for name in ("_library_window", "_save_window"):
        window = getattr(copyAnimation, name, None)
        if window is not None:
            window.close()
            window.deleteLater()
    importlib.reload(animation_curve_transfer)
    importlib.reload(copyAnimation)
    for name, value in saved.items():
        setattr(copyAnimation, name, value)
    for name in ("SmartAnimationLibraryWindow", "AnimationLibraryCard", "SaveAnimationWindow",
                 "show_smart_animation_library", "show_save_animation_window", "_capture_viewport_gif",
                 "_library_window", "_save_window"):
        copyAnimation.__dict__.pop(name, None)
    picker = settings.ACTIVE_PICKER
    importlib.reload(settings)
    settings.ACTIVE_PICKER = picker
    event_filter, registered = hotkeysMod._event_filter, hotkeysMod._registered_hotkeys
    importlib.reload(hotkeysMod)
    hotkeysMod._event_filter, hotkeysMod._registered_hotkeys = event_filter, registered
    # Existing buttons resolve module globals when clicked. Rebind only these
    # actions and menu method, avoiding teardown of unrelated tool callbacks.
    for name in ("copy_animation", "save_animation", "load_animation", "paste_animation",
                 "paste_insert_animation", "paste_opposite_animation", "copy_pose", "paste_pose",
                 "save_pose", "load_pose"):
        setattr(toolbar, name, getattr(copyAnimation, name))
    toolbar.copy_animation_execute = copyAnimation.execute
    replace_definition(toolbar, "_show_copy_animation_context_menu", cls="AnimKeyToolbar")
    replace_definition(flashbuttons, "get_available_actions")
    report["menu"] = []
    def record_menu(menu, position):
        report["menu"] = [action.text() for action in menu.actions() if not action.isSeparator()]
        menu.deleteLater()
    button = toolbar.animkey_toolbar._button_widgets.get("C")
    if button:
        with mock.patch.object(toolbar, "execute_qt", side_effect=record_menu):
            toolbar.animkey_toolbar._show_copy_animation_context_menu(button, toolbar.QtCore.QPoint(0, 0))
    report["libraryRemoved"] = not hasattr(copyAnimation, "SmartAnimationLibraryWindow")
    report["clipboardPreserved"] = all(getattr(copyAnimation, name) is value for name, value in saved.items())
    report["reloaded"] = True
    report["modules"] = [module.__file__ for module in (copyAnimation, animation_curve_transfer,
                          toolbar, settings, hotkeysMod, flashbuttons)]
except Exception:
    report["error"] = traceback.format_exc()
report["after"] = state()
report["sceneStateUnchanged"] = report["before"] == report["after"]
(ROOT / "dist/copy-paste-reload.json").write_text(json.dumps(report, indent=2))
print("ANIMKEY_COPY_UPDATE=" + str(report["reloaded"]))
