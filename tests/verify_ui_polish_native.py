"""Check fonts, Selection Sets resize and slider popup motion in disposable Maya."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path(os.environ['MAYA_APP_DIR']).resolve()
assert PROFILE.is_relative_to(ROOT/'dist/crash-lifecycle')
OUTPUT = Path(os.environ['ANIMKEY_LIFECYCLE_OUTPUT'])
sys.path.insert(0,os.environ['ANIMKEY_LIFECYCLE_SOURCE'])
fault = (OUTPUT/'python-fault.log').open('w')
faulthandler.enable(file=fault,all_threads=True)
import maya.cmds as cmds
import maya.utils
from AnimKey.mods.maya_compat import QtCore,QtGui,QtWidgets,get_maya_main_window,get_maya_font_family,wrap_instance
from AnimKey.mods import configMod,uiMod
from AnimKey.core import toolbar,settings
from AnimKey.buttons import selectionSets as sets

state = {'cycle':0,'size':0,'menu_index':0}
report = {'passed':False,'resizes':[],'menus':[]}
sizes = [(720,460),(340,250),(800,540),(360,300)]

def stamp():
    return {'selection':cmds.ls(selection=True,long=True),'time':cmds.currentTime(q=True),
            'keys':cmds.keyframe(state['target'],q=True,timeChange=True),
            'values':cmds.keyframe(state['target'],q=True,valueChange=True)}

def finish(error=None):
    report['passed'] = error is None
    if error:
        report['error'] = error
    (OUTPUT/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

def open_picker():
    try:
        if state['cycle'] == 3:
            return open_menu()
        window = sets.SetManagerWindow(get_maya_main_window())
        window._skip_save = True
        canvas = window.current_page().container
        canvas.add_button(sets.SetButton('Character',[state['target']]))
        canvas.set_layout_mode('board' if state['cycle']%2 else 'ordered',save=False)
        window.show()
        state['window'] = window
        QtCore.QTimer.singleShot(250,resize_picker)
    except Exception:
        finish(traceback.format_exc())

def resize_picker():
    try:
        window = state['window']
        if state['size'] == len(sizes):
            assert QtGui.QFontInfo(window.font()).family() == report['maya_font']
            if state['cycle'] == 0:
                window.grab().save(str(OUTPUT/'selection-sets.png'))
            window.close();window.deleteLater()
            state['cycle'] += 1
            state['size'] = 0
            return QtCore.QTimer.singleShot(150,open_picker)
        window.resize(*sizes[state['size']])
        QtCore.QTimer.singleShot(120,inspect_picker)
    except Exception:
        finish(traceback.format_exc())

def inspect_picker():
    try:
        window = state['window']
        page = window.current_page()
        for widget in (page.container,page.scroll.viewport()):
            assert not widget.autoFillBackground()
            assert not widget.testAttribute(QtCore.Qt.WA_OpaquePaintEvent)
        viewport = page.scroll.viewport()
        point = viewport.mapTo(window,QtCore.QPoint(15,viewport.height()-20))
        pixel = window.grab().toImage().pixelColor(point)
        assert min(pixel.red(),pixel.green(),pixel.blue()) > 20, pixel.getRgb()
        assert pixel.alpha() < 255, pixel.getRgb()
        assert stamp() == state['stamp']
        report['resizes'].append({'mode':page.container.layout_mode,'size':sizes[state['size']],
                                  'pixel':pixel.getRgb()})
        state['size'] += 1
        QtCore.QTimer.singleShot(50,resize_picker)
    except Exception:
        finish(traceback.format_exc())

class PopupTrace(QtCore.QObject):
    def __init__(self,menu):
        super().__init__(menu)
        self.menu = menu
        self.offsets = []
        menu.installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type() == QtCore.QEvent.Paint or (
                event.type() == QtCore.QEvent.Move and watched._window_reveal.applying):
            target = watched._window_reveal._target
            if target is not None:
                self.offsets.append(watched.y()-target.y())
        return False

def open_menu():
    try:
        if state['menu_index'] == len(state['labels']):
            return finish()
        label = state['labels'][state['menu_index']]
        state['trace'] = PopupTrace(label._menu)
        label._show_menu()
        QtCore.QTimer.singleShot(250,inspect_menu)
    except Exception:
        finish(traceback.format_exc())

def inspect_menu():
    try:
        label = state['labels'][state['menu_index']]
        menu = label._menu
        trace = state['trace'].offsets
        debug = {'offsets':trace,'visible':menu.isVisible(),'running':menu._window_reveal.running,
                 'pending':menu._window_reveal.pending,'time':menu._window_reveal._animation.currentTime(),
                 'position':[menu.x(),menu.y()], 'target':str(menu._window_reveal._target),
                 'label_visible':label.isVisible()}
        assert trace and trace[0] > 0 and trace[-1] == 0, debug
        assert trace == sorted(trace,reverse=True), trace
        assert not menu._window_reveal.running
        assert QtGui.QFontInfo(menu.font()).family() == report['maya_font']
        menu.close()
        assert not menu._window_reveal._timer.isActive()
        assert stamp() == state['stamp']
        report['menus'].append({'label':label.current_option,'offsets':trace})
        state['menu_index'] += 1
        QtCore.QTimer.singleShot(100,open_menu)
    except Exception:
        finish(traceback.format_exc())

def start():
    try:
        configMod.set_setting('check_updates',False)
        get_maya_main_window().setWindowTitle('AnimKey UI polish - isolated validation')
        report['maya_font'] = QtGui.QFontInfo(get_maya_main_window().font()).family()
        import maya.OpenMayaUI as omui
        probe_window = cmds.window()
        cmds.columnLayout()
        probe_control = cmds.text(label='Native font probe')
        probe_widget = wrap_instance(int(omui.MQtUtil.findControl(probe_control)),QtWidgets.QWidget)
        report['native_control_font'] = QtGui.QFontInfo(probe_widget.font()).family()
        report['application_font'] = QtGui.QFontInfo(QtWidgets.QApplication.font()).family()
        cmds.deleteUI(probe_window,window=True)
        assert get_maya_font_family() == report['maya_font']
        toolbar.animkey_toolbar.show()
        content = toolbar.animkey_toolbar.content_widget
        assert QtGui.QFontInfo(content.font()).family() == report['maya_font']
        probe = settings.SettingsWindow()
        assert QtGui.QFontInfo(probe.font()).family() == report['maya_font']
        probe.close();probe.deleteLater()
        state['labels'] = content.findChildren(uiMod.DropdownLabel)
        assert state['labels']
        state['target'] = cmds.createNode('transform',name='UiPolishTarget')
        cmds.setKeyframe(state['target'],attribute='translateX',time=1,value=0)
        cmds.setKeyframe(state['target'],attribute='translateX',time=12,value=5)
        cmds.currentTime(6);cmds.select(state['target'])
        state['stamp'] = stamp()
        QtCore.QTimer.singleShot(400,open_picker)
    except Exception:
        finish(traceback.format_exc())

maya.utils.executeDeferred(start)
