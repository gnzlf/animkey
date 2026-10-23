"""Run only in a separately launched Maya with a disposable MAYA_APP_DIR."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(os.environ['ANIMKEY_LIFECYCLE_OUTPUT']).resolve()
SOURCE = Path(os.environ['ANIMKEY_LIFECYCLE_SOURCE']).resolve()
PROFILE = Path(os.environ['MAYA_APP_DIR']).resolve()
assert PROFILE.is_relative_to(ROOT / 'dist/crash-lifecycle'), 'Disposable profile required'
OUTPUT.mkdir(parents=True, exist_ok=True)
trace = (OUTPUT/'python-fault.log').open('w', encoding='utf-8')
faulthandler.enable(file=trace, all_threads=True)
sys.path.insert(0, str(SOURCE))
import maya.cmds as cmds
import maya.utils
from AnimKey.mods.maya_compat import QtCore, QtWidgets
from AnimKey.core import toolbar, settings
from AnimKey.mods import configMod
from AnimKey.buttons import animCrash, retimer, animCleaner, tempControlsUI

assert Path(cmds.internalVar(userAppDir=True)).resolve() == PROFILE
assert Path(toolbar.__file__).resolve() == SOURCE/'AnimKey/core/toolbar.py'
report = {'events': [], 'passed': False, 'source': str(toolbar.__file__), 'materials': []}
state = {'cycle': 0, 'window': None, 'scene': None}

def scene_stamp():
    return {'selection': cmds.ls(sl=True, long=True),
            'time': cmds.currentTime(q=True),
            'key_times': cmds.keyframe('lifecycleProbe.translateX', q=True, tc=True),
            'key_values': cmds.keyframe('lifecycleProbe.translateX', q=True, vc=True)}

def record(message):
    report['events'].append(message)
    (OUTPUT/'progress.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

def finish(error=None):
    if error: report['error'] = error
    report['passed'] = error is None
    report['animation_before'] = state['scene']
    report['animation_after'] = scene_stamp() if cmds.objExists('lifecycleProbe') else None
    record('finished')
    from AnimKey.mods import uiMod
    uiMod.cleanup_animkey_runtime(full=True)
    if cmds.workspaceControl(toolbar.WORKSPACE_NAME, query=True, exists=True):
        cmds.deleteUI(toolbar.WORKSPACE_NAME, control=True)
    report['runtime_cleanup_complete'] = True
    (OUTPUT/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    QtCore.QTimer.singleShot(500, lambda: cmds.quit(force=True))

def step():
    try:
        cycle = state['cycle']
        if cycle == 20:
            return finish()
        bar = toolbar.animkey_toolbar
        if getattr(bar, '_reload_pending', False):
            QtCore.QTimer.singleShot(100, step)
            return
        assert scene_stamp() == state['scene'], 'UI operations changed the test animation'
        record('cycle {}: settings open/move/close'.format(cycle))
        factory = (settings.SettingsWindow, animCrash.RecoveryWindow, retimer.RetimerWindow,
                   animCleaner.AnimCleanerWindow, tempControlsUI.TempControlsWindow)[cycle % 5]
        window = factory()
        state['window'] = window
        if os.environ.get('ANIMKEY_TEST_PORTABLE_GLASS') == '1':
            window.setProperty('forcePortableMaterial', True)
        window.show()
        window.move(120 + cycle * 3, 160 + cycle * 2)
        window.resize(780 + (cycle % 3) * 10, 760)
        # Let the owned compatibility capture run before movement/close/refresh.
        QtCore.QTimer.singleShot(180, close_and_reload)
    except Exception:
        finish(traceback.format_exc())


def close_and_reload():
    try:
        cycle = state['cycle']
        bar = toolbar.animkey_toolbar
        window = state['window']
        mode = window.property('materialBackdropMode')
        report['materials'].append({'window': type(window).__name__, 'mode': mode})
        if os.environ.get('ANIMKEY_TEST_PORTABLE_GLASS') == '1':
            assert mode == 'portable', 'Compatibility capture did not run: ' + str(mode)
        assert not window.mask().contains(QtCore.QPoint(0, 0))
        assert window.mask().contains(QtCore.QPoint(40, 2))
        close_buttons = [b for b in window.findChildren(QtWidgets.QAbstractButton)
                         if getattr(b, 'kind', '') == 'close']
        if close_buttons:
            close_buttons[0].click()
        else:
            window.close()
        window.deleteLater()
        state['window'] = None
        record('cycle {}: dock floating/move'.format(cycle))
        cmds.workspaceControl(toolbar.WORKSPACE_NAME, edit=True, floating=bool(cycle % 2))
        if cycle % 3 == 2:
            record('cycle {}: external dock delete/reopen'.format(cycle))
            cmds.deleteUI(toolbar.WORKSPACE_NAME, control=True)
            bar.show()
        record('cycle {}: reload'.format(cycle))
        bar.reload()
        bar.reload()
        state['cycle'] += 1
        QtCore.QTimer.singleShot(350, step)
    except Exception:
        finish(traceback.format_exc())

def start():
    try:
        record('show toolbar')
        configMod.set_setting('check_updates', False)
        toolbar.animkey_toolbar.show()
        # Retimer intentionally creates its setup on first use. Prepare that
        # before measuring UI-only movement/close/reload scene preservation.
        retimer.Retimer('main').create_curve()
        node = cmds.createNode('transform', name='lifecycleProbe')
        cmds.setKeyframe(node, attribute='translateX', time=1, value=0)
        cmds.setKeyframe(node, attribute='translateX', time=24, value=7)
        cmds.currentTime(12)
        cmds.select(node)
        state['scene'] = scene_stamp()
        QtCore.QTimer.singleShot(500, step)
    except Exception:
        finish(traceback.format_exc())

maya.utils.executeDeferred(start)
