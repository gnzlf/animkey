"""Read-only production scene inventory; edits/tests never save the source."""
import os
import time
from AnimKey.mods.maya_compat import QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
from AnimKey.core import animation_curve_transfer as transfer
scene = os.environ['ANIMKEY_TEST_SCENE']
started = time.perf_counter()
cmds.file(scene, open=True, force=True, prompt=False, executeScriptNodes=False)
print('LOAD_SECONDS', time.perf_counter() - started, flush=True)
print('LAYERS', cmds.ls(type='animLayer'), flush=True)
print('CURVES', len(cmds.ls(type='animCurve') or []), flush=True)
for layer in cmds.ls(type='animLayer') or []:
    print('LAYER', layer, [(a, cmds.getAttr(layer+'.'+a)) for a in ('weight','mute','lock','selected','preferred')], flush=True)
controls = []
for curve in cmds.ls(type='animCurve') or []:
    times = cmds.keyframe(curve, query=True, timeChange=True) or []
    if len(times) < 3:
        continue
    for plug in transfer.driven_plugs_for_curve(curve):
        node = plug.rsplit('.',1)[0]
        if cmds.nodeType(node) == 'transform' and node not in controls:
            controls.append(node)
            print('CONTROL', node, plug, curve, times[:4], times[-2:], flush=True)
    if len(controls) >= 8:
        break
print('AUDIT_DONE', flush=True)
maya.standalone.uninitialize()
