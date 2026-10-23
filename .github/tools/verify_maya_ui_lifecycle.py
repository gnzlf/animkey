"""Run only in a separately launched Maya with a disposable MAYA_APP_DIR."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import traceback
from unittest import mock

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
    patch = state.pop('motion_mouse_patch', None)
    if patch is not None:
        patch.stop()
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
        if os.environ.get('ANIMKEY_TEST_LIVE_MATERIAL') == '1':
            # Warm the snapshot once; no GPU capture is permitted during motion.
            QtCore.QTimer.singleShot(350, start_motion)
            return
        # Let the owned viewport readback and blur finish before close/refresh.
        delay = int(os.environ.get('ANIMKEY_LIFECYCLE_HOLD_MS', '250')) if cycle == 0 else 250
        QtCore.QTimer.singleShot(delay, close_and_reload)
    except Exception:
        finish(traceback.format_exc())


def start_motion():
    try:
        surface = state['window']._material_surface
        if surface._capture_count == 0 and state.get('warmup_attempts', 0) < 30:
            state['warmup_attempts'] = state.get('warmup_attempts', 0) + 1
            QtCore.QTimer.singleShot(100, start_motion)
            return
        assert surface._backdrop_image is not None, 'No backdrop: captures={}, pending={}, buttons={}'.format(
            surface._capture_count, surface._capture_timer.isActive(), QtWidgets.QApplication.mouseButtons())
        state['warmup_attempts'] = 0
        state['motion_frames_before'] = surface._reproject_count
        state['motion_captures_before'] = surface._capture_count
        state['motion_moves'] = 0
        state['move_costs_ms'] = []
        # Model an actual held drag without sending input to the user's desktop.
        state['motion_mouse_patch'] = mock.patch.object(QtWidgets.QApplication, 'mouseButtons',
                                                       return_value=QtCore.Qt.LeftButton)
        state['motion_mouse_patch'].start()
        state['motion_clock'] = QtCore.QElapsedTimer()
        state['motion_clock'].start()
        timer = QtCore.QTimer(state['window'])
        state['motion_timer'] = timer
        timer.setInterval(12)
        timer.setTimerType(QtCore.Qt.PreciseTimer)
        timer.timeout.connect(advance_motion)
        timer.start()
    except Exception:
        finish(traceback.format_exc())


def advance_motion():
    try:
        window = state['window']
        state['motion_moves'] += 1
        cost = QtCore.QElapsedTimer()
        cost.start()
        window.move(150 + state['motion_moves'] * 3, 160 + state['cycle'] * 2)
        state['move_costs_ms'].append(cost.nsecsElapsed() / 1000000)
        if state['motion_clock'].elapsed() >= 1500 and state['motion_moves'] >= 10:
            state['motion_timer'].stop()
            state.pop('motion_mouse_patch').stop()
            surface = window._material_surface
            state['motion_frames'] = surface._reproject_count - state['motion_frames_before']
            costs = sorted(state['move_costs_ms'])
            report['last_motion'] = {'frames': state['motion_frames'],
                                     'moves': state['motion_moves'],
                                     'gpu_captures': surface._capture_count - state['motion_captures_before'],
                                     'move_p95_ms': costs[int(len(costs) * .95)],
                                     'move_max_ms': max(costs),
                                     'capture_ms': surface._capture_duration_ms}
            assert state['motion_frames'] >= 2, 'Backdrop stayed frozen throughout movement'
            assert report['last_motion']['gpu_captures'] == 0, 'Movement performed expensive GPU captures'
            QtCore.QTimer.singleShot(350, close_and_reload)
    except Exception:
        state['motion_timer'].stop()
        finish(traceback.format_exc())


def close_and_reload():
    try:
        cycle = state['cycle']
        bar = toolbar.animkey_toolbar
        window = state['window']
        mode = window.property('materialBackdropMode')
        report['materials'].append({'window': type(window).__name__, 'mode': mode,
                                   'viewports': window.property('materialViewportCount'),
                                   'moving_frames': state.get('motion_frames'),
                                   'capture_ms': window._material_surface._capture_duration_ms})
        if os.environ.get('ANIMKEY_TEST_LIVE_MATERIAL') == '1':
            report['materials'][-1]['motion'] = report['last_motion'].copy()
            assert window._material_surface._capture_count == state['motion_captures_before'] + 1
        assert mode == 'portable', 'Application backdrop did not run: ' + str(mode)
        assert window.property('nativeBlurBackend') == 'qt'
        assert window._material_surface._backdrop_image is not None
        if cycle in (0, 4):
            window.grab().save(str(OUTPUT / ('material-{}.png'.format(cycle))))
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
        from AnimKey.mods.maya_compat import get_maya_main_window
        get_maya_main_window().setWindowTitle('AnimKey frosted material validation - isolated test')
        QtCore.QTimer.singleShot(500, step)
    except Exception:
        finish(traceback.format_exc())

maya.utils.executeDeferred(start)
