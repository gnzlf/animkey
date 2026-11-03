"""Large help cards for toolbar buttons and every entry of their menus."""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from AnimKey.core import help_popup
from AnimKey.mods import uiMod


def wait(ms):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    (getattr(loop, "exec", None) or loop.exec_)()


class HelpPopupTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = help_popup.HelpPopupController()
        self.root = QtWidgets.QWidget()
        self.root.setGeometry(200, 200, 400, 80)
        layout = QtWidgets.QHBoxLayout(self.root)
        self.iso = QtWidgets.QPushButton("ISO", objectName="ISO")
        self.iso.setToolTip("Isolate Selection")
        self.unknown = QtWidgets.QPushButton("-", objectName="DECREASE")
        self.unknown.setToolTip(uiMod.create_tooltip_text(
            "Decrease Values", "Decrease keyframe or attribute values by the specified amount"))
        layout.addWidget(self.iso)
        layout.addWidget(self.unknown)
        self.root.show()
        APP.processEvents()
        self.installed = self.ctrl.install_toolbar(self.root)

    def tearDown(self):
        self.ctrl.hide()
        self.root.close()
        self.root.deleteLater()
        APP.processEvents()

    def hover(self, widget):
        QtWidgets.QApplication.sendEvent(widget, QtCore.QEvent(QtCore.QEvent.Enter))
        wait(help_popup.SHOW_DELAY_MS + 80)
        return self.ctrl.card()

    def test_every_toolbar_button_gets_a_card_and_native_tooltip_is_replaced(self):
        self.assertEqual(self.installed, 2)
        card = self.hover(self.iso)
        self.assertTrue(card.isVisible())
        self.assertEqual(card.title.text(), "Isolate")
        self.assertIn("rig", card.description.text())
        self.assertEqual(card.title.font().pixelSize(), 17)
        self.assertGreaterEqual(card.description.font().pixelSize(), 14)
        tooltip = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, QtCore.QPoint(2, 2), QtCore.QPoint(2, 2))
        self.assertTrue(self.ctrl.eventFilter(self.iso, tooltip))
        QtWidgets.QApplication.sendEvent(self.iso, QtCore.QEvent(QtCore.QEvent.Leave))
        self.assertFalse(card.isVisible())

    def test_buttons_without_an_entry_reuse_their_rich_tooltip(self):
        card = self.hover(self.unknown)
        self.assertEqual(card.title.text(), "Decrease Values")
        self.assertIn("Decrease keyframe", card.description.text())

    def test_card_stays_on_screen_and_inside_its_width(self):
        card = self.hover(self.iso)
        self.assertLessEqual(card.width(), help_popup.MAX_CARD_WIDTH)
        area = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.assertTrue(area.contains(card.geometry().topLeft()))

    def test_menu_entries_and_submenus_explain_themselves(self):
        menu = QtWidgets.QMenu(self.iso)
        include = menu.addAction("Include Parented Objects")
        sub = menu.addMenu("Visible Range")
        frames = sub.addAction("+- 24 Frames")
        self.ctrl.install_menu(menu)
        self.assertEqual(help_popup.HelpPopupController.owner_key(sub), "ISO")
        entry = help_popup.menu_entry("ISO", include)
        self.assertIn("swords", entry[1])
        self.assertIsNotNone(help_popup.menu_entry("TRL", frames))
        self.assertTrue(sub.property("animkeyHelpInstalled"))
        menu.popup(QtCore.QPoint(300, 300))
        APP.processEvents()
        menu.hovered.emit(include)
        wait(help_popup.MENU_DELAY_MS + 80)
        card = self.ctrl.card()
        self.assertTrue(card.isVisible())
        self.assertEqual(card.title.text(), "Include Parented Objects")
        menu.close()
        APP.processEvents()
        self.assertFalse(card.isVisible())

    def test_dynamic_labels_are_normalized(self):
        self.assertEqual(help_popup.normalize_label("●  4x"), "{n}x")
        self.assertEqual(help_popup.normalize_label("📤 Export Flash Buttons..."), "Export Flash Buttons...")
        self.assertEqual(help_popup.normalize_label("+- 12 Frames"), "+- N Frames")
        for owner, label in help_popup.MENU_HELP:
            self.assertTrue(owner == "*" or owner in help_popup.BUTTON_HELP, owner)


if __name__ == "__main__":
    unittest.main(verbosity=2)
