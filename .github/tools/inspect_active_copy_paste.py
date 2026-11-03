"""Read-only Maya state for planning a validated module reload."""
import json
from pathlib import Path
import sys
import maya.cmds as cmds

ROOT = Path(__file__).resolve().parents[2]
report = {"scene": cmds.file(q=True, sceneName=True), "modified": cmds.file(q=True, modified=True),
          "frame": cmds.currentTime(q=True), "selection": cmds.ls(sl=True, long=True),
          "undoName": cmds.undoInfo(q=True, undoName=True),
          "modules": {name: getattr(sys.modules.get(name), "__file__", None) for name in (
              "AnimKey", "AnimKey.buttons.copyAnimation", "AnimKey.core.animation_curve_transfer",
              "AnimKey.core.toolbar", "AnimKey.mods.hotkeysMod", "AnimKey.core.settings")},
          "layers": cmds.ls(type="animLayer") or []}
(ROOT / "dist/copy-paste-active-readonly.json").write_text(json.dumps(report, indent=2))
