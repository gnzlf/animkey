"""Run only in a disposable GUI Maya profile to verify real viewport isolation."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import traceback
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path(os.environ['MAYA_APP_DIR']).resolve()
assert PROFILE.is_relative_to(ROOT/'dist/crash-lifecycle')
OUTPUT = Path(os.environ['ANIMKEY_LIFECYCLE_OUTPUT']).resolve()
SOURCE = Path(os.environ['ANIMKEY_LIFECYCLE_SOURCE']).resolve()
sys.path.insert(0, str(SOURCE))
fault = (OUTPUT/'python-fault.log').open('w')
faulthandler.enable(file=fault, all_threads=True)
import maya.cmds as cmds
import maya.mel as mel
import maya.utils
import maya.api.OpenMayaUI as omui
from AnimKey.mods.maya_compat import QtCore, QtWidgets, wrap_instance, PYSIDE_MAJOR, get_maya_main_window
import maya.OpenMayaUI as legacy_ui
if PYSIDE_MAJOR == 6:
    from PySide6 import QtTest
else:
    from PySide2 import QtTest
from AnimKey.buttons import isolate
from AnimKey.core.executionGuard import animkey_execution

report = {'passed':False, 'cases':[], 'context_note':
          'Hidden test windows cannot receive OS viewport focus. The panel '
          'resolver is supplied the target panel while focus/pointer queries '
          'report the toolbar; all panels and isolate operations are native. '
          'Viewport resolution is covered separately by the unit regressions.'}
state = {'layout':0, 'panel':0}
layouts = [('single',1), ('vertical2',2), ('quad',4)]


def write():
    (OUTPUT/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def finish(error=None):
    if error:
        report['error'] = error
    report['passed'] = error is None and bool(report['cases']) and all(c['passed'] for c in report['cases'])
    report['complete'] = True
    write()
    # The launcher terminates only this owned, disposable process after reading
    # the report; leaving Maya's Qt callback avoids native shutdown races.


def click():
    original = cmds.getPanel
    def toolbar_focus(**kwargs):
        if kwargs.get('withFocus') or kwargs.get('underPointer'):
            return 'AnimKeyToolbar'
        return original(**kwargs)
    with mock.patch.object(cmds, 'getPanel', side_effect=toolbar_focus), \
            mock.patch.object(isolate, '_get_active_model_panel',
                              return_value=state['panels'][state['panel']]):
        QtTest.QTest.mouseClick(state['button'], QtCore.Qt.LeftButton)


def next_panel():
    try:
        if state['panel'] == len(state['panels']):
            state['layout'] += 1
            return layout()
        panel = state['panels'][state['panel']]
        view = omui.M3dView.getM3dViewFromModelPanel(panel)
        widget = wrap_instance(int(view.widget()), QtWidgets.QWidget)
        cmds.setFocus(panel)
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton, pos=QtCore.QPoint(10,10))
        QtCore.QTimer.singleShot(100, exercise)
    except Exception:
        finish(traceback.format_exc())


def exercise():
    try:
        panel = state['panels'][state['panel']]
        case = {'layout':layouts[state['layout']][0], 'expected_panel':panel}
        report['current_case'] = case
        other_states = {p:bool(cmds.isolateSelect(p,q=True,state=True)) for p in state['panels'] if p != panel}
        cmds.select(state['other'],replace=True)
        mel.eval('enableIsolateSelect("{}", 1);'.format(panel))
        cmds.isolateSelect(panel,loadSelected=True)
        mel.eval('enableIsolateSelect("{}", 0);'.format(panel))
        cmds.select(state['control'], replace=True)
        # The test windows are not mapped onto the user's desktop. Mark the
        # native editor active explicitly, as a real viewport click would do.
        cmds.modelEditor(panel, edit=True, activeView=True)
        selection = cmds.ls(selection=True, long=True)
        click()
        case['clicks'] = state.get('invocations', 0)
        case['resolved'] = state.get('resolved')
        case['all_states'] = {p:bool(cmds.isolateSelect(p,q=True,state=True)) for p in cmds.getPanel(type='modelPanel')}
        case['enabled'] = bool(cmds.isolateSelect(panel,q=True,state=True))
        case['view_selected'] = bool(cmds.modelEditor(panel,q=True,viewSelected=True))
        members = cmds.isolateSelect(panel,q=True,viewObjects=True)
        case['members'] = cmds.ls(cmds.sets(members,q=True) or [],long=True) if members else []
        assert case['enabled'] and case['view_selected'], case
        assert any(m == state['root'] or m.startswith(state['root']+'|') for m in case['members']), case
        assert state['other'] not in case['members'], case
        assert cmds.ls(selection=True,long=True) == selection
        assert {p:bool(cmds.isolateSelect(p,q=True,state=True)) for p in other_states} == other_states
        click()
        assert not cmds.isolateSelect(panel,q=True,state=True)
        assert not cmds.modelEditor(panel,q=True,viewSelected=True)
        assert cmds.ls(selection=True,long=True) == selection
        cmds.select(state['geometry']+'.vtx[0]',replace=True)
        component_selection = cmds.ls(selection=True,long=True)
        click()
        assert cmds.isolateSelect(panel,q=True,state=True)
        assert cmds.ls(selection=True,long=True) == component_selection
        cmds.select(clear=True)
        click()
        assert not cmds.isolateSelect(panel,q=True,state=True)
        assert not cmds.ls(selection=True)
        case.update(passed=True, toggled_off=True, components_preserved=True, empty_selection_exit=True)
        report['cases'].append(case)
        write()
        state['panel'] += 1
        QtCore.QTimer.singleShot(100,next_panel)
    except Exception:
        finish(traceback.format_exc())


def layout():
    try:
        if state['layout'] == len(layouts):
            return finish()
        if state.get('window'):
            cmds.deleteUI(state['window'],window=True)
        configuration,count = layouts[state['layout']]
        state['window'] = cmds.window(title='AnimKey Isolate disposable viewports',widthHeight=(1000,650))
        pane = cmds.paneLayout(configuration=configuration)
        state['panels'] = [cmds.modelPanel(parent=pane,camera='persp') for _ in range(count)]
        window_widget = wrap_instance(int(legacy_ui.MQtUtil.findWindow(state['window'])), QtWidgets.QWidget)
        window_widget.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        cmds.showWindow(state['window'])
        state['panel'] = 0
        QtCore.QTimer.singleShot(300,next_panel)
    except Exception:
        finish(traceback.format_exc())


def start():
    try:
        assert Path(cmds.internalVar(userAppDir=True)).resolve() == PROFILE
        main_window = get_maya_main_window()
        main_window.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        main_window.show()
        cmds.file(new=True,force=True)
        cmds.namespace(add='Hero')
        root = cmds.group(empty=True,name='Hero:rig')
        control = cmds.circle(name='Hero:ctrl')[0]
        geometry = cmds.polyCube(name='Hero:geometry')[0]
        cmds.parent([control,geometry],root)
        state['root'] = cmds.ls(root,long=True)[0]
        state['control'] = cmds.ls(control,long=True)[0]
        state['geometry'] = cmds.ls(geometry,long=True)[0]
        state['other'] = cmds.ls(cmds.polyCube(name='OtherAsset')[0],long=True)[0]
        cmds.setAttr(state['other']+'.translateX',5)
        state['toolbar'] = QtWidgets.QWidget(None,QtCore.Qt.Tool)
        state['toolbar'].setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        row = QtWidgets.QHBoxLayout(state['toolbar'])
        state['button'] = QtWidgets.QPushButton('ISO')
        row.addWidget(state['button'])
        def invoke():
            state['invocations'] = state.get('invocations', 0) + 1
            state['resolved'] = isolate._get_active_model_panel()
            with animkey_execution('native Isolate toolbar click'):
                isolate.execute()
        state['button'].clicked.connect(invoke)
        state['toolbar'].show()
        layout()
    except Exception:
        finish(traceback.format_exc())


maya.utils.executeDeferred(start)
