"""Window lifecycle checks, only in a disposable GUI Maya process."""
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
import maya.utils
from AnimKey.mods.maya_compat import QtCore,QtWidgets,get_maya_main_window
from AnimKey.mods.maya_compat import PYSIDE_MAJOR
if PYSIDE_MAJOR == 6:
    from PySide6 import QtTest
else:
    from PySide2 import QtTest
from AnimKey.mods import configMod,uiMod
from AnimKey.buttons import tempControls as tc,tempPivot as pivot
from AnimKey.core import toolbar,temp_transform as tf

assert Path(cmds.internalVar(userAppDir=True)).resolve() == PROFILE
state = {'cycle':0,'window':None}
report = {'passed':False,'cycles':0}

def finish(error=None):
    if error:
        report['error'] = error
    else:
        report['passed'] = True
    uiMod.cleanup_animkey_runtime(full=True)
    (OUTPUT/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if os.environ.get('ANIMKEY_TEST_QUIT') == '1':
        QtCore.QTimer.singleShot(500,lambda:maya.utils.executeDeferred(lambda:cmds.quit(force=True)))
    else:
        # Test UI cleanup separately from Maya/plugin shutdown. The launcher
        # owns this process and terminates it after collecting the report.
        QtCore.QTimer.singleShot(25000,settled)

def settled():
    report['settled_after_cleanup'] = True
    (OUTPUT/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

def open_window():
    try:
        if state['cycle'] == 8:
            return finish()
        cmds.select(state['node'],replace=True)
        name = 'PIV' if state['cycle']%2 == 0 else 'TMC'
        with mock.patch.object(QtWidgets,'QMenu',side_effect=AssertionError('Unexpected submenu')):
            state['buttons'][name].customContextMenuRequested.emit(QtCore.QPoint(5,5))
        window = pivot._window if name == 'PIV' else tc._window
        assert window and window.isVisible()
        assert window.layout().contentsMargins().left() == (12 if name == 'PIV' else 18)
        state['window'] = window
        if name == 'PIV':
            window.toggle.click()
            assert pivot.is_active()
            window.mode.setCurrentIndex(2)
            window._run(window._activate)
            assert pivot._session.mode == 'center'
            window.toggle.click()
            assert not pivot.is_active()
        else:
            for index in (1,2,0):
                QtTest.QTest.mouseClick(window.tabs.tabBar(),QtCore.Qt.LeftButton,
                                       pos=window.tabs.tabBar().tabRect(index).center())
                assert window.tabs.currentIndex() == index
                assert not window._drag_active
        QtCore.QTimer.singleShot(300,close_window)
    except Exception:
        finish(traceback.format_exc())

def close_window():
    try:
        window = state['window']
        window.move(window.pos()+QtCore.QPoint(25,10))
        assert window._material_surface is not None
        assert not window.mask().contains(QtCore.QPoint(0,0))
        if state['cycle'] < 2:
            window.grab().save(str(OUTPUT/('window-{}.png'.format(state['cycle']))))
        window.close()
        if hasattr(window,'callbacks'):
            assert not window.callbacks
        assert max(abs(a-b) for a,b in zip(list(tf.world(state['node'])),state['matrix'])) < 1e-8
        assert cmds.keyframe(state['node'],query=True,timeChange=True) == state['keys']
        window.deleteLater()
        state['window'] = None
        state['cycle'] += 1
        report['cycles'] = state['cycle']
        QtCore.QTimer.singleShot(100,open_window)
    except Exception:
        finish(traceback.format_exc())

def start():
    try:
        configMod.set_setting('check_updates',False)
        get_maya_main_window().setWindowTitle('AnimKey Temp tools - isolated validation')
        toolbar.animkey_toolbar.show()
        app = QtWidgets.QApplication.instance()
        state['buttons'] = {b.objectName():b for w in app.topLevelWidgets()
                            for b in w.findChildren(QtWidgets.QPushButton) if b.objectName() in ('PIV','TMC')}
        assert set(state['buttons']) == {'PIV','TMC'}
        state['node'] = cmds.createNode('transform',name='TempToolWindowProbe')
        cmds.setKeyframe(state['node'],attribute='translateX',time=1,value=0)
        cmds.setKeyframe(state['node'],attribute='translateX',time=12,value=5)
        cmds.currentTime(6)
        state['matrix'] = list(tf.world(state['node']))
        state['keys'] = cmds.keyframe(state['node'],query=True,timeChange=True)
        QtCore.QTimer.singleShot(400,open_window)
    except Exception:
        finish(traceback.format_exc())

maya.utils.executeDeferred(start)
