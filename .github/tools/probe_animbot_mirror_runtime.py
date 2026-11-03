"""Check the installed AnimBot API in a separate empty Maya process."""
import inspect
import sys

from PySide2 import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name="python")
sys.path.insert(0, r"C:\Users\ANIM-gonzalo\Documents\maya\scripts")

print("ANIMBOT_PROBE: import", flush=True)
import animBot
print("ANIMBOT_PROBE: public entry points=" + str([name for name in vars(animBot)
                                                    if not name.startswith("_") and callable(getattr(animBot, name))]), flush=True)
from animBot._api import core
print("ANIMBOT_PROBE: core imported", flush=True)
state = getattr(core, "CORE", None)
print("ANIMBOT_PROBE: CORE type=" + type(state).__name__, flush=True)
from animBot.mirror import mirror
print("ANIMBOT_PROBE: mirror imported", flush=True)
for name in ("mirrorPose_click", "snapshotMirrorSettings_click", "mirrorToLeft_click", "mirrorToRight_click"):
    method = getattr(mirror.Mirror, name)
    print("ANIMBOT_PROBE: {}{}".format(name, inspect.signature(method)), flush=True)
for name in ("mirror", "rigSnapshot", "selectionTools", "skipDialogs"):
    print("ANIMBOT_PROBE: CORE.{} type={}".format(name, type(getattr(state, name, None)).__name__), flush=True)
for name, value in vars(mirror).items():
    methods = [item for item in dir(value) if any(token in item.lower() for token in
               ("mirrorsnapshot", "snapshotopposite", "mirrorvalue", "mirrorable"))]
    if methods:
        print("ANIMBOT_API: {} {} {}".format(name, type(value).__name__, methods), flush=True)
        if "getMirrorValueAt" in methods:
            try:
                print("ANIMBOT_CONSTRUCTOR: {} {}".format(name, inspect.signature(value.__init__)), flush=True)
            except (ValueError, TypeError):
                pass
        for method in methods:
            try:
                print("ANIMBOT_SIGNATURE: {} {}".format(method, inspect.signature(getattr(value, method))), flush=True)
            except (ValueError, TypeError):
                pass
print("ANIMBOT_MIRROR_METHODS: " + str([name for name in dir(state.mirror)
                                          if "snapshot" in name.lower()]), flush=True)
print("ANIMBOT_PROBE: ready", flush=True)
