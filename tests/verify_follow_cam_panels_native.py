"""Follow Cam integration checks in a disposable GUI Maya profile only."""
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
OUTPUT = Path(os.environ['ANIMKEY_LIFECYCLE_OUTPUT'])
SOURCE = Path(os.environ['ANIMKEY_LIFECYCLE_SOURCE'])
sys.path.insert(0,str(SOURCE))
fault = (OUTPUT/'python-fault.log').open('w')
faulthandler.enable(file=fault,all_threads=True)
import maya.cmds as cmds
import maya.mel as mel
import maya.utils
import maya.api.OpenMayaUI as omui
import maya.OpenMayaUI as legacy_ui
from AnimKey.mods.maya_compat import QtCore,QtWidgets,wrap_instance,PYSIDE_MAJOR,get_maya_main_window
if PYSIDE_MAJOR == 6:
    from PySide6 import QtTest
else:
    from PySide2 import QtTest
from AnimKey.core import toolbar
from AnimKey.core.executionGuard import animkey_execution
from AnimKey.buttons import followCam as fc
from AnimKey.mods import configMod

report = {'passed':False,'cases':[]}
state = {'case':0}
scenarios = [('Two Panes Side by Side',False),('Two Panes Side by Side',True),
             ('Two Panes Stacked',False),('Four View',True),('Single Perspective View',False)]

def write():
    (OUTPUT/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

def finish(error=None):
    if error:
        report['error'] = error
    report['passed'] = error is None and all(c.get('passed') for c in report['cases'])
    write()

def active_context():
    view = omui.M3dView.active3dView()
    raw = {'widget':str(view.widget()),'camera':view.getCamera().fullPathName()}
    try:
        raw['control'] = legacy_ui.MQtUtil.fullName(int(view.widget()))
        raw['containing'] = cmds.getPanel(containing=raw['control'])
    except Exception as exc:
        raw['error'] = str(exc)
    raw['resolved'] = fc._active_3d_view_context()
    raw['focus'] = cmds.getPanel(withFocus=True)
    raw['pointer'] = cmds.getPanel(underPointer=True)
    return raw

def layout():
    try:
        if state['case'] == len(scenarios):
            return finish()
        name,shared = scenarios[state['case']]
        # Use owned model panels; Maya's startup workspace restoration can
        # overwrite viewPanes while this verifier is running.
        configuration = {'Two Panes Side by Side':'vertical2','Two Panes Stacked':'horizontal2',
                         'Four View':'quad','Single Perspective View':'single'}[name]
        count = 4 if name == 'Four View' else 1 if name == 'Single Perspective View' else 2
        if state.get('viewport_window'):
            cmds.deleteUI(state['viewport_window'],window=True)
        window = cmds.window(title='Follow Cam disposable viewports',widthHeight=(1100,650))
        pane = cmds.paneLayout(configuration=configuration)
        state['expected_panels'] = [cmds.modelPanel(parent=pane) for _ in range(count)]
        state['viewport_window'] = window
        cmds.showWindow(window)
        state.update(layout=name,shared=shared,panel_index=0)
        QtCore.QTimer.singleShot(500,prepare)
    except Exception:
        finish(traceback.format_exc())

def prepare():
    try:
        panels = state['expected_panels']
        for panel in panels:
            widget = wrap_instance(int(omui.M3dView.getM3dViewFromModelPanel(panel).widget()),QtWidgets.QWidget)
            assert widget.isVisible(), (state['layout'],panel,'not visible')
        for index,panel in enumerate(panels):
            camera = state['cameras'][0 if state['shared'] else index%len(state['cameras'])]
            cmds.lookThru(panel,camera)
        state['panels'] = panels
        QtCore.QTimer.singleShot(300,focus_panel)
    except Exception:
        finish(traceback.format_exc())

def focus_panel():
    try:
        if state['panel_index'] == len(state['panels']):
            state['case'] += 1
            return layout()
        panel = state['panels'][state['panel_index']]
        view = omui.M3dView.getM3dViewFromModelPanel(panel)
        widget = wrap_instance(int(view.widget()),QtWidgets.QWidget)
        cmds.setFocus(panel)
        QtTest.QTest.mouseClick(widget,QtCore.Qt.LeftButton,pos=QtCore.QPoint(20,20))
        cmds.select(state['target'],replace=True)
        state['before'] = {p:fc._panel_camera(p) for p in state['panels']}
        QtCore.QTimer.singleShot(200,create)
    except Exception:
        finish(traceback.format_exc())

def create():
    try:
        panel = state['panels'][state['panel_index']]
        case = {'layout':state['layout'],'shared_camera':state['shared'],'expected':panel,
                'before':state['before'],'pre_toolbar':active_context()}
        original_resolve = fc._get_active_panel
        original_get_panel = cmds.getPanel
        def toolbar_panel(**kwargs):
            # QTest does not move the physical cursor. Simulate the focus and
            # pointer being on the toolbar, while retaining the real M3dView.
            if kwargs.get('withFocus') or kwargs.get('underPointer'):
                return 'AnimKeyToolbar'
            return original_get_panel(**kwargs)
        def resolve():
            case['at_toolbar'] = active_context()
            case['resolved'] = original_resolve()
            return case['resolved']
        with mock.patch.object(fc,'_get_active_panel',side_effect=resolve), \
                mock.patch.object(cmds,'getPanel',side_effect=toolbar_panel):
            QtTest.QTest.mouseClick(state['button'],QtCore.Qt.LeftButton)
        roots = fc._find_follow_roots()
        case['after'] = {p:fc._panel_camera(p) for p in state['panels']}
        case['created'] = bool(roots)
        case['passed'] = bool(roots) and case.get('resolved') == panel and all(
            case['after'][p] == state['before'][p] for p in state['panels'] if p != panel)
        if roots:
            follow = fc._follow_cameras(roots[0])[0]
            source = state['before'][panel]
            source_matrix = cmds.xform(source,q=True,worldSpace=True,matrix=True)
            follow_matrix = cmds.xform(follow,q=True,worldSpace=True,matrix=True)
            case['initial_matrix_error'] = max(abs(a-b) for a,b in zip(source_matrix,follow_matrix))
            case['passed'] = case['passed'] and case['initial_matrix_error'] < 1e-6
            with animkey_execution('native test'):
                fc.remove_follow_cam()
            case['restored'] = {p:fc._panel_camera(p) for p in state['panels']}
            case['passed'] = case['passed'] and case['restored'] == state['before']
        report['cases'].append(case)
        write()
        state['panel_index'] += 1
        QtCore.QTimer.singleShot(200,focus_panel)
    except Exception:
        finish(traceback.format_exc())

def start():
    try:
        configMod.set_setting('check_updates',False)
        get_maya_main_window().setWindowTitle('AnimKey Follow Cam panels - isolated validation')
        toolbar.animkey_toolbar.show()
        state['button'] = next(b for w in QtWidgets.QApplication.topLevelWidgets()
                               for b in w.findChildren(QtWidgets.QPushButton) if b.objectName() == 'CAM')
        state['target'] = cmds.circle(name='FollowCamTarget')[0]
        cmds.setAttr(state['target']+'.translateX',3)
        state['cameras'] = []
        for i in range(4):
            cam = cmds.camera(name='FollowCamShot{}'.format(i))[0]
            cmds.xform(cam,worldSpace=True,translation=(12+i*2,8,18),rotation=(-12,32+i*5,0))
            state['cameras'].append(cam)
        QtCore.QTimer.singleShot(500,layout)
    except Exception:
        finish(traceback.format_exc())

maya.utils.executeDeferred(start)
