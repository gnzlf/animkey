"""Stress actual toolbar widgets in a disposable Maya/Qt process, never a scene."""
import faulthandler
import os
from pathlib import Path
import sys
from types import SimpleNamespace

faulthandler.enable(all_threads=True)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['MAYA_APP_DIR'] = str(ROOT / 'dist/crash-lifecycle/profile')
from AnimKey.mods.maya_compat import QtCore, QtWidgets, is_qt_object_valid
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name='python')
from AnimKey.core import toolbar
from AnimKey.core.toolbar_style import apply_toolbar_style

gif = next((ROOT/'AnimKey/data/Gifs').glob('*.gif'), None)
for cycle in range(60):
    owner = QtWidgets.QWidget()
    owner.resize(1000, 160)
    layout = QtWidgets.QVBoxLayout(owner)
    workspace = QtWidgets.QWidget(owner)
    layout.addWidget(workspace)
    inner = QtWidgets.QVBoxLayout(workspace)
    strip = toolbar.ToolbarHorizontalScroller(workspace)
    strip.setFixedHeight(44)
    inner.addWidget(strip)
    content = QtWidgets.QWidget()
    content.setObjectName('toolbar_content')
    strip.set_pan_widget(content)
    row = QtWidgets.QHBoxLayout(content)
    controller = toolbar.ToolbarGifPreviewController()
    buttons = []
    for index in range(5):
        button = QtWidgets.QPushButton('Action {}'.format(index))
        row.addWidget(button)
        buttons.append(button)
        if gif: controller.install(button, str(gif))
    bar = SimpleNamespace(content_widget=content, toolbar_scroll_area=strip)
    owner.show()
    apply_toolbar_style(bar)
    app.processEvents()
    glass = bar._material_glass
    for step in range(3):
        owner.move(100 + step * 7, 100 + step * 5)
        owner.resize(1000 + step * 9, 160)
        app.processEvents()
    # Mimic Maya deleting a dock while a geometry callback is still pending.
    glass.schedule_sync()
    print('DELETE cycle {}'.format(cycle), flush=True)
    workspace.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    app.processEvents()
    assert not is_qt_object_valid(glass), 'A dock deletion must destroy its glass'
    assert not is_qt_object_valid(strip), 'A dock deletion must destroy its controls'
    controller.hide()
    controller.deleteLater()
    owner.close()
    owner.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    app.processEvents()
print('PASS 60 native toolbar move/resize/pending-callback/dock-deletion cycles', flush=True)
