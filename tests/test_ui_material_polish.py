"""Native font, transparent picker canvas and animated slider popup regressions."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from AnimKey.mods.maya_compat import QtCore,QtGui,QtWidgets,execute_qt
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')
APP.setFont(QtGui.QFont('Arial',9))
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.mods import uiMod,maya_compat,configMod
from AnimKey.mods.themes import ThemeManager
from AnimKey.buttons import selectionSets as sets
from AnimKey.core.window_style import apply_window_style


def settle(ms=200):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms,loop.quit)
    execute_qt(loop)


class MaterialPolishTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True,force=True)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patch = mock.patch.object(configMod,'get_user_folder_path',return_value=self.folder.name)
        patch.start();self.addCleanup(patch.stop)
        self.owner = QtWidgets.QWidget()
        self.owner.setFont(QtGui.QFont('Arial',9))
        self.owner.resize(900,700)
        self.owner.show()
        patch = mock.patch.object(maya_compat,'get_maya_main_window',return_value=self.owner)
        patch.start();self.addCleanup(patch.stop)
        self.addCleanup(self.owner.close)

    def manager(self):
        window = sets.SetManagerWindow(self.owner)
        window._skip_save = True
        window.show()
        settle()
        self.addCleanup(window.close)
        return window

    def test_material_windows_and_theme_follow_maya_font(self):
        window = QtWidgets.QWidget(self.owner,QtCore.Qt.Tool)
        apply_window_style(window)
        window.show();APP.processEvents()
        self.addCleanup(window.close)
        self.assertEqual(QtGui.QFontInfo(window.font()).family(),'Arial')
        self.assertEqual(ThemeManager.get_current_theme()['font_family'],'Arial')
        self.owner.setFont(QtGui.QFont('Courier New',10))
        other = QtWidgets.QWidget(self.owner,QtCore.Qt.Tool)
        apply_window_style(other)
        self.assertEqual(QtGui.QFontInfo(other.font()).family(),QtGui.QFontInfo(self.owner.font()).family())
        self.assertEqual(ThemeManager.get_current_theme()['font_family'],QtGui.QFontInfo(self.owner.font()).family())

    def test_batch_font_fallback_does_not_require_a_gui_application(self):
        with mock.patch.object(maya_compat,'get_maya_main_window',return_value=None), \
                mock.patch.object(QtWidgets.QApplication,'instance',return_value=object()):
            self.assertIsInstance(maya_compat.get_maya_font(),QtGui.QFont)
            self.assertTrue(maya_compat.get_maya_font_family())

    def test_selection_canvas_has_no_opaque_scroll_layer_after_resize(self):
        window = self.manager()
        for size in ((700,500),(360,300),(820,540),(320,220),(640,420)):
            window.resize(*size);APP.processEvents()
            page = window.current_page()
            for canvas in (page.scroll.viewport(),page.container):
                self.assertFalse(canvas.autoFillBackground())
                self.assertFalse(canvas.testAttribute(QtCore.Qt.WA_OpaquePaintEvent))
            point = page.scroll.viewport().mapTo(window,QtCore.QPoint(15,15))
            color = window.grab().toImage().pixelColor(point)
            self.assertGreater(min(color.red(),color.green(),color.blue()),20)
            self.assertLess(color.alpha(),255,'Opaque canvas hides the window material')
        output = Path(__file__).resolve().parents[1]/'dist/ui-polish'
        window.grab().save(str(output/'selection-sets.png'))

    def test_board_uses_the_window_material_and_keeps_custom_images(self):
        window = self.manager()
        canvas = window.current_page().container
        canvas.layout_mode = 'board'
        canvas.update();APP.processEvents()
        point = canvas.mapTo(window,QtCore.QPoint(10,10))
        color = window.grab().toImage().pixelColor(point)
        self.assertLess(color.alpha(),255)
        image = QtGui.QImage(100,100,QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor('#cc5544'))
        path = str(Path(self.folder.name)/'board.png')
        image.save(path)
        canvas.set_background_image(path,save=False)
        APP.processEvents()
        color = window.grab().toImage().pixelColor(point)
        self.assertGreater(color.red(),color.green()+30)
        canvas.clear_background_image(save=False)
        APP.processEvents()
        self.assertLess(window.grab().toImage().pixelColor(point).alpha(),255)

    def test_new_tabs_also_keep_the_canvas_transparent(self):
        window = self.manager()
        page = window.add_new_tab('Extra')
        APP.processEvents()
        self.assertFalse(page.container.autoFillBackground())
        self.assertFalse(page.scroll.viewport().autoFillBackground())

    def test_slider_menu_rises_without_bounce_and_keeps_selection(self):
        label = uiMod.DropdownLabel(parent=self.owner)
        label.move(100,550)
        label.show()
        selected = mock.Mock()
        label.optionSelected.connect(selected)
        pos = label.mapToGlobal(QtCore.QPoint(0,-4))
        label._show_menu();APP.processEvents()
        menu = label._menu
        reveal = menu._window_reveal
        target = reveal._target
        self.assertTrue(reveal.running)
        positions = [menu.y()]
        for ms in (30,60,90,120,150,180):
            reveal._animation.setCurrentTime(ms)
            menu.repaint()
            positions.append(menu.y())
        self.assertEqual(positions,sorted(positions,reverse=True))
        self.assertEqual(menu.pos(),target)
        menu._on_item_clicked('Blend to Ease')
        selected.assert_called_once_with('Blend to Ease')
        self.assertEqual(label.current_option,'Blend to Ease')
        self.assertFalse(reveal.running)
        self.assertFalse(label._menu_visible)
        label._show_menu();APP.processEvents()
        self.assertEqual(menu._window_reveal._target,target)
        menu.hide()
        self.assertFalse(reveal.running)
        self.assertFalse(reveal._timer.isActive())

    def test_replacing_slider_options_closes_the_previous_popup(self):
        label = uiMod.DropdownLabel(parent=self.owner)
        label.show();label._show_menu();APP.processEvents()
        old = label._menu
        label.set_options([('New','Description',QtGui.QColor('#cccccc'))])
        self.assertFalse(old.isVisible())
        self.assertFalse(old._window_reveal.running)
        self.assertIsNot(label._menu,old)

    def test_button_hover_has_more_growth_without_moving_neighbours(self):
        self.owner.setStyleSheet('background:#202020;')
        layout = QtWidgets.QHBoxLayout(self.owner)
        button = uiMod.HoverButton('Button')
        button.setFixedSize(100,36)
        button.setStyleSheet('QPushButton {background:#606060; color:white; border:1px solid #aaaaaa; border-radius:6px;}')
        neighbour = QtWidgets.QPushButton('Next')
        layout.addWidget(button);layout.addWidget(neighbour)
        APP.processEvents()
        geometry = button.geometry(),neighbour.geometry()
        def painted_width(amount):
            button._set_hover_amount(amount)
            image = button.grab().toImage()
            # Measure the visible outline, excluding the stylesheet's fill.
            xs = [x for x in range(image.width()) for y in range(image.height())
                  if min(image.pixelColor(x,y).getRgb()[:3]) > 145]
            return max(xs)-min(xs)+1
        idle = painted_width(0)
        active = painted_width(1)
        self.assertGreaterEqual(active-idle,8)
        self.assertEqual((button.geometry(),neighbour.geometry()),geometry)


if __name__ == '__main__':
    unittest.main(verbosity=2)
