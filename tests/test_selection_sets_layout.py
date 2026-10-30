"""Selection Sets keeps complete buttons inside a resizable transparent canvas."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, os.environ.get('ANIMKEY_TEST_ROOT', str(Path(__file__).resolve().parents[1])))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
if Path('C:/Windows/Fonts/arial.ttf').exists():
    QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')
    APP.setFont(QtGui.QFont('Arial',9))
import maya.standalone
try:
    maya.standalone.initialize(name='python')
except RuntimeError:
    pass
from AnimKey.buttons import selectionSets as sets
from AnimKey.mods import configMod


class SelectionSetsLayoutTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patch=mock.patch.object(configMod,'get_user_folder_path',return_value=self.folder.name)
        patch.start();self.addCleanup(patch.stop)
        self.scroll=QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.canvas=sets.FlowContainer()
        self.scroll.setWidget(self.canvas)
        self.canvas.setAutoFillBackground(False)
        self.scroll.resize(260,160)
        self.scroll.show()
        self.addCleanup(self.scroll.close)

    def settle(self):
        for _ in range(5): APP.processEvents()

    def add(self,name):
        button=sets.SetButton(name,[],members_hidden=False)
        self.canvas.add_button(button)
        self.settle()
        return button

    def assert_rows_fit(self):
        self.canvas.reflow();self.settle()
        for button in self.canvas.buttons:
            self.assertGreaterEqual(button.x(),8)
            self.assertLessEqual(button.geometry().right(),self.scroll.viewport().width()-8)
            self.assertLess(button.geometry().bottom(),self.canvas.height())

    def test_long_names_fit_narrow_rows_and_recover_when_widened(self):
        button=self.add('Left hand IK and finger controls with a very long label')
        for width in (260,120,410,180,520):
            self.scroll.resize(width,160);self.settle()
            self.assert_rows_fit()
        self.assertIn('Left hand',button.set_name)

    def test_adding_many_buttons_and_resizing_keeps_rows_separate_and_scrollable(self):
        for index in range(30): self.add('Character control set '+str(index))
        for width in (190,360,140,500,220):
            self.scroll.resize(width,160);self.settle();self.assert_rows_fit()
            buttons=self.canvas.buttons
            for left,right in zip(buttons,buttons[1:]):
                self.assertFalse(left.geometry().intersects(right.geometry()))
        self.assertGreater(self.scroll.verticalScrollBar().maximum(),0)
        last=self.canvas.buttons[-1]
        self.scroll.ensureWidgetVisible(last,0,0);self.settle()
        visible_rect=QtCore.QRect(last.mapTo(self.scroll.viewport(),QtCore.QPoint()),last.size())
        self.assertTrue(self.scroll.viewport().rect().contains(visible_rect))

    def test_header_wraps_without_overlapping_controls_or_palette_overflow(self):
        page=sets.TabPage()
        page.show();self.addCleanup(page.close)
        for index in range(16):
            page.container.add_button(sets.SetButton('Set '+str(index),[],color='#{:06x}'.format(0x557080+index*0x503)))
        for mode in ('ordered','board','ordered'):
            page.set_layout_mode(mode,save=False)
            for width in (300,520,360,700):
                page.resize(width,260);self.settle()
                controls=[page.ns_combo,page.namespace_lock_btn,page.set_visibility_btn,
                          page.row_mode_btn,page.board_mode_btn,page.snap_check,
                          page.color_filter_scroll,page.add_btn]
                visible=[widget for widget in controls if widget.isVisible()]
                rects=[QtCore.QRect(widget.mapTo(page,QtCore.QPoint()),widget.size()) for widget in visible]
                for rect in rects:self.assertTrue(page.rect().contains(rect), (width,mode,rect))
                for index,rect in enumerate(rects):
                    for other in rects[index+1:]:self.assertFalse(rect.intersects(other),(width,mode,rect,other))
                if width==300:self.assertTrue(page._controls_wrapped)
                if width==700:self.assertFalse(page._controls_wrapped)
        self.assertFalse(page.color_filter_scroll.viewport().autoFillBackground())

    def test_board_to_row_releases_canvas_width_and_reflows_every_button(self):
        for name in ('Root','Left foot IK','Right foot IK'):self.add(name)
        self.canvas.set_layout_mode('board',save=False)
        self.canvas.buttons[-1].board_pos=QtCore.QPoint(900,600)
        self.canvas.reflow();self.settle()
        self.assertGreater(self.canvas.minimumWidth(),900)
        self.canvas.set_layout_mode('ordered',save=False)
        self.settle();self.assert_rows_fit()
        self.assertEqual(self.canvas.minimumWidth(),0)
        self.assertFalse(self.canvas.autoFillBackground())


if __name__ == '__main__':
    unittest.main(verbosity=2)
