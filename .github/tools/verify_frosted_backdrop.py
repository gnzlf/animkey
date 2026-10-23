"""Verify real alpha and visible backdrop detail in an owned Qt calibration UI."""
import argparse
import json
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
try:
    import maya.cmds
except ImportError:
    maya = ModuleType('maya')
    cmds = ModuleType('maya.cmds')
    cmds.about = lambda **kwargs: '2025'
    maya.cmds = cmds
    sys.modules.update({'maya': maya, 'maya.cmds': cmds})

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
from AnimKey.core.window_style import apply_window_style

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
parser.add_argument('--hold-ms', type=int, default=0)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Calibration(QtWidgets.QWidget):
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        for x in range(0, self.width(), 12):
            painter.fillRect(x, 0, 12, self.height(),
                             QtGui.QColor('#dc925f' if x % 24 else '#3685af'))
        painter.fillRect(0, 340, self.width(), 180, QtGui.QColor('#c0d9bd'))
        painter.setPen(QtGui.QColor('#202020'))
        font = painter.font()
        font.setPixelSize(32)
        painter.setFont(font)
        painter.drawText(55, 430, 'Maya backdrop  |  Sharp outside, frosted inside')


owner = Calibration()
owner.setWindowTitle('AnimKey frosted glass verification')
owner.setGeometry(40, 50, 1060, 650)
owner.show()
windows = []
for index in range(2):
    window = QtWidgets.QWidget(owner, QtCore.Qt.Tool)
    window.setWindowTitle('AnimKey frosted panel {}'.format(index + 1))
    window.setGeometry(90 + index * 480, 175, 400, 400)
    apply_window_style(window)
    layout = QtWidgets.QVBoxLayout(window)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.addWidget(QtWidgets.QLabel('AnimKey — frosted neutral grey'))
    layout.addStretch()
    layout.addWidget(QtWidgets.QPushButton('Rounded controls'))
    window.show()
    windows.append(window)


def verify():
    report = {'qt': QtCore.qVersion(), 'dpr': owner.devicePixelRatioF(), 'panels': []}
    for index, window in enumerate(windows):
        surface = window._material_surface
        assert not surface.active
        assert window.property('nativeBlurBackend') == 'qt'
        assert window.property('materialBackdropMode') == 'portable'
        raw = surface._backdrop_image
        assert raw is not None
        image = window.grab().toImage()
        center = image.pixelColor(image.width() // 2, image.height() // 2)
        assert 180 < center.alpha() < 255, center.getRgb()
        for x, y in ((1, 1), (image.width() - 2, 1), (1, image.height() - 2),
                     (image.width() - 2, image.height() - 2)):
            assert image.pixelColor(x, y).alpha() == 0
        values = [raw.pixelColor(x, 70).red() for x in range(30, raw.width() - 30)]
        assert max(values) - min(values) < 50, 'Sharp background stripes were not blurred'
        assert abs(raw.pixelColor(100, 60).green() - raw.pixelColor(100, 320).green()) > 30
        window.grab().save(str(output / ('panel-{}.png'.format(index))))
        report['panels'].append({'mode': 'portable', 'alpha': center.alpha(),
                                 'corners_transparent': True, 'blur_verified': True})
    (output / 'result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    if not args.hold_ms:
        app.quit()


QtCore.QTimer.singleShot(200, verify)
QtCore.QTimer.singleShot(max(500, args.hold_ms), app.quit)
(getattr(app, 'exec', None) or app.exec_)()
