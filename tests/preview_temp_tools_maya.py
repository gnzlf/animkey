"""Render the actual Qt widgets in a disposable Maya standalone process."""
import os
import sys
import tempfile
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide2 import QtWidgets,QtGui,QtCore,QtTest
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
font_id = QtGui.QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\segoeui.ttf")
assert font_id >= 0
app.setFont(QtGui.QFont("Segoe UI",10))
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from AnimKey.mods import configMod
preview_data = tempfile.mkdtemp(prefix="animkey_preview_")
configMod.get_user_folder_path = lambda:preview_data
from AnimKey.buttons.tempControlsUI import TempControlsWindow
from AnimKey.buttons import tempControls as tc
from AnimKey.buttons import tempPivot
from AnimKey.mods import mediaMod
from AnimKey.mods import uiMod
standalone_parent = QtWidgets.QWidget()
uiMod.get_maya_main_window = lambda:standalone_parent

out = os.path.join(os.path.dirname(os.path.dirname(__file__)),"dist","temp-tools-preview")
os.makedirs(out,exist_ok=True)
nodes = [cmds.createNode("transform",name="Hand_L"),cmds.createNode("transform",name="Prop")]
cmds.setAttr(nodes[1]+".translateX",5)
cmds.select(nodes)
window = TempControlsWindow()
assert window.sample_frames.isChecked()
# Render a normal desktop size; the constructor also clamps to small screens.
assert window.height() <= max(480,uiMod.screen_available_geometry(window).height()-64)
window.resize(460,700)
window.show()
app.processEvents()
for index in (1,2,0):
    QtTest.QTest.mouseClick(window.tabs.tabBar(),QtCore.Qt.LeftButton,
                           pos=window.tabs.tabBar().tabRect(index).center())
    app.processEvents()
    assert window.tabs.currentIndex() == index, 'Tab click was intercepted by window dragging'
    assert not window._drag_active
assert window.position.currentIndex() == window.orientation.currentIndex()
window.family.setCurrentIndex(1)
assert not window.position.model().item(1).isEnabled()
assert not window.position.model().item(4).isEnabled()
window.family.setCurrentIndex(3)
assert not window.position.model().item(4).isEnabled()
window.family.setCurrentIndex(0)
window.grab().save(os.path.join(out,"create.png"))
window._create()
app.processEvents()
settle_loop = QtCore.QEventLoop()
QtCore.QTimer.singleShot(180,settle_loop.quit)
settle_loop.exec_()
assert len(tc.all_systems()) == 2
assert window.list.count() == 2
window.list.item(0).setSelected(True)
app.processEvents()
window.grab().save(os.path.join(out,"systems.png"))
# Size uses an initial baseline, preserves aspect ratio, and never scales the rig.
target = tc.controls(window._systems()[0])[0]
cmds.select(target,replace=True)
app.processEvents()
before_size = cmds.getAttr(target+'.shapeSize')[0]
source = tc._first(target,'source')
before_world = cmds.xform(source,query=True,worldSpace=True,matrix=True)
window.size_slider.setValue(150)
app.processEvents()
assert window.size_value.value() == 150
assert all(abs(a*1.5-b)<1e-6 for a,b in zip(before_size,cmds.getAttr(target+'.shapeSize')[0]))
window.size_value.setValue(200)
app.processEvents()
assert window.size_slider.value() == 200
assert all(abs(a*2-b)<1e-6 for a,b in zip(before_size,cmds.getAttr(target+'.shapeSize')[0]))
assert cmds.xform(source,query=True,worldSpace=True,matrix=True) == before_world
assert cmds.ls(selection=True,long=True) == [target]
cmds.undo()
app.processEvents()
assert all(abs(a*1.5-b)<1e-6 for a,b in zip(before_size,cmds.getAttr(target+'.shapeSize')[0]))
cmds.redo()
app.processEvents()
assert all(abs(a*2-b)<1e-6 for a,b in zip(before_size,cmds.getAttr(target+'.shapeSize')[0]))
window.shape.setCurrentIndex(2)
window.shape.activated.emit(2)
app.processEvents()
assert cmds.getAttr(target+'.shapeIndex') == 2
assert cmds.ls(selection=True,long=True) == [target]
assert cmds.xform(source,query=True,worldSpace=True,matrix=True) == before_world
window.enabled.click()
app.processEvents()
assert all(not cmds.getAttr(s+'.isEnabled') for s in window._systems())
window.enabled.click()
app.processEvents()
assert all(cmds.getAttr(s+'.isEnabled') for s in window._systems())
QtTest.QTest.mouseClick(window.tabs.tabBar(),QtCore.Qt.LeftButton,pos=window.tabs.tabBar().tabRect(2).center())
app.processEvents()
window.grab().save(os.path.join(out,'tools.png'))
assert window.tabs.currentIndex() == 2
window.close()
assert not window.callbacks
assert not window.refresh_timer.isActive()
strip = QtWidgets.QWidget()
strip.setStyleSheet("background:#222832; color:#eeeeee; font-family:Segoe UI;")
layout = QtWidgets.QHBoxLayout(strip)
for name in ("WS","PIV","TMC","RUL"):
    button = QtWidgets.QPushButton()
    button.setObjectName(name)
    button.setFixedSize(48,48)
    path = mediaMod.get_button_icon(name)
    assert path and os.path.exists(path),name
    icon = QtGui.QIcon(path)
    assert not icon.isNull(),name
    button.setIcon(icon)
    button.setIconSize(QtCore.QSize(40,40))
    layout.addWidget(button)
strip.show()
app.processEvents()
strip.grab().save(os.path.join(out,"buttons.png"))
strip.close()
print("TEMP_UI_OK",out)
from AnimKey.core import toolbar
actual_strip = QtWidgets.QWidget()
actual_layout = QtWidgets.QHBoxLayout(actual_strip)
toolbar.animkey_toolbar._add_tool_buttons_to_layout(actual_layout)
actual_strip.show()
app.processEvents()
ordered = [actual_layout.itemAt(i).widget().objectName() for i in range(actual_layout.count()) if actual_layout.itemAt(i).widget()]
assert ordered.index("TMC") == ordered.index("PIV")+1,ordered
buttons = {b.objectName():b for b in actual_strip.findChildren(QtWidgets.QPushButton)}
assert buttons["PIV"].receivers("2clicked(bool)") >= 1
assert buttons["TMC"].receivers("2clicked(bool)") >= 1
buttons["TMC"].click()
app.processEvents()
assert tc._window and tc._window.isVisible()
tc.shutdown()
# A context-menu event must open a full window and never instantiate QMenu.
from unittest import mock
with mock.patch.object(QtWidgets,'QMenu',side_effect=AssertionError('Unexpected right-click menu')):
    buttons['TMC'].customContextMenuRequested.emit(QtCore.QPoint(5,5))
    app.processEvents()
    assert tc._window and tc._window.isVisible()
    assert tc._window.tabs.currentIndex() == 1
    tc.shutdown()
    cmds.select(nodes,replace=True)
    buttons['PIV'].customContextMenuRequested.emit(QtCore.QPoint(5,5))
    app.processEvents()
    pivot_window = tempPivot._window
    assert pivot_window and pivot_window.isVisible()
    pivot_window.toggle.click()
    assert tempPivot.is_active()
    pivot_window.mode.setCurrentIndex(2)
    pivot_window._run(pivot_window._activate)
    assert tempPivot._session.mode == 'center'
    pivot_window.toggle.click()
    assert not tempPivot.is_active()
    pivot_window.grab().save(os.path.join(out,'pivot.png'))
    tempPivot.cleanup_orphans()
    assert tempPivot._window is None
actual_strip.close()
from AnimKey.mods import uiMod
uiMod.cleanup_animkey_runtime()
print("TEMP_TOOLBAR_OK",ordered[ordered.index("PIV")-1:ordered.index("TMC")+2])
maya.standalone.uninitialize()
