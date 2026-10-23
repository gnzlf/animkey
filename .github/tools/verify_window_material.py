"""Exercise real Qt tool windows in Maya standalone using disposable user data.

Run with mayapy; pass an output directory for PNG previews. No scene is opened
or saved and no installed AnimKey files or preferences are changed.
"""

import argparse
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock


parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", required=True)
parser.add_argument("--native", action="store_true", help="Exercise the Windows compositor instead of offscreen Qt")
parser.add_argument("--early-polish", action="store_true", help="Simulate Maya polishing the base before tool controls exist")
args = parser.parse_args()
output = Path(args.output_dir).resolve()
output.mkdir(parents=True, exist_ok=True)
os.environ["MAYA_APP_DIR"] = str(output / "maya-user")
if not args.native:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
app.setFont(QtGui.QFont("Segoe UI", 9))
import maya.standalone

maya.standalone.initialize(name="python")

from AnimKey.mods import configMod, uiMod
from AnimKey.core import settings, window_style
from AnimKey.buttons import (bakeAnim, gimbalFixer, retimer, animCleaner,
                             animCrash, tempControlsUI, switcher, selectionSets,
                             copyAnimation, flashbuttons, brush)
from AnimKey.core.welcome import WelcomeWindow

checks = []
failures = []


def check(condition, detail):
    if not condition:
        failures.append(detail)


if args.early_polish:
    original_popup_init = uiMod.ContextPopupWindow.__init__

    def early_polish_init(self, *arguments, **keywords):
        original_popup_init(self, *arguments, **keywords)
        self.ensurePolished()

    uiMod.ContextPopupWindow.__init__ = early_polish_init


def verify(name, factory):
    window = factory()
    if isinstance(window, flashbuttons.FlashButtonsPanel):
        window.setWindowOpacity(1.0)
        for button in window.buttons:
            button.show()
    window.show()
    app.processEvents()
    app.processEvents()
    if isinstance(window, gimbalFixer.GimbalFixerWindow):
        window.refresh_data()
    if isinstance(window, flashbuttons.FlashButtonsPanel):
        # Flash has floating circular buttons with no shared rectangular body.
        image = window.grab().toImage()
        window.grab().save(str(output / (name + ".png")))
        check(not hasattr(window, "_material_surface"), name + ": no rectangular glass base")
        check(image.pixelColor(10, window.height() // 2).alpha() == 0,
              name + ": transparent space around buttons")
        for button in window.buttons:
            button_image = button.grab().toImage()
            check(button.width() == button.height(), name + ": circular proportions")
            check(button_image.pixelColor(1, 1).alpha() == 0,
                  name + ": no square background behind circle")
            check(button_image.pixelColor(button.rect().center()).alpha() > 0,
                  name + ": painted circle")
        check(window.lock_btn.geometry().bottom() < window.button_container.geometry().top(),
              name + ": lock control fits above button board")
        window.close()
        app.processEvents()
        checks.append(name)
        print("PASS " + name, flush=True)
        return
    surface = window._material_surface
    if isinstance(window, (animCrash.RecoveryWindow,
                           retimer.RetimerWindow, animCleaner.AnimCleanerWindow)):
        check(not window.container.styleSheet(), name + ": legacy container style removed")
    if args.native:
        check(not surface.active and window.property('nativeBlurBackend') == 'qt',
              name + ": no rectangular native backdrop")
        check(window.property("nativeBlurHandle") == int(window.winId()), name + ": cached HWND")
        check(window.property("nativeFrameHandle") == int(window.winId()), name + ": no native outer frame")
    check(bool(window.windowFlags() & QtCore.Qt.FramelessWindowHint), name + ": frameless")
    check(window.testAttribute(QtCore.Qt.WA_TranslucentBackground), name + ": alpha")
    corners = (QtCore.QPoint(0, 0), QtCore.QPoint(window.width() - 1, 0),
               QtCore.QPoint(0, window.height() - 1),
               QtCore.QPoint(window.width() - 1, window.height() - 1))
    check(all(not window.mask().contains(point) for point in corners),
          name + ": all four exterior corners clipped")
    center = QtCore.QPoint(window.width() // 2, window.height() // 2)
    check(window.mask().contains(center), name + ": visible body")
    pixmap = window.grab()
    pixmap.save(str(output / (name + ".png")))
    image = pixmap.toImage()
    image_corners = ((0, 0), (image.width() - 1, 0), (0, image.height() - 1),
                     (image.width() - 1, image.height() - 1))
    check(all(image.pixelColor(x, y).alpha() == 0 for x, y in image_corners),
          name + ": all four exterior corner pixels transparent")
    check(image.pixelColor(center).alpha() > 0, name + ": painted body")
    for widget in window.findChildren(QtWidgets.QAbstractButton):
        if widget.isVisible() and widget.text().strip() and widget.width() > 40:
            check(widget.height() >= 16, name + ": usable button " + widget.text())
    if isinstance(window, settings.SettingsWindow):
        check(window.layout().contentsMargins().left() == 0, "Settings: no exterior margin")
        check(window.container.geometry().topLeft() == QtCore.QPoint(0, 0), "Settings: flush panel")
        for index in range(window.tab_widget.count()):
            window.tab_widget.setCurrentIndex(index)
            app.processEvents()
        window.tab_widget.setCurrentIndex(0)
    if isinstance(window, selectionSets.PopupInput):
        check(all(color.lower() in button.styleSheet().lower() for button, color in window._color_buttons),
              name + ": preserved palette colors")
    if isinstance(window, flashbuttons.FlashButtonsPanel):
        check(window.lock_btn.height() >= 24, name + ": usable lock control")
        check(window.lock_btn.geometry().bottom() < window.button_container.geometry().top(),
              name + ": controls fit above the button board")
    if isinstance(window, gimbalFixer.GimbalFixerWindow):
        with mock.patch.object(gimbalFixer.cmds, "ls", return_value=["materialTest"]), \
             mock.patch.object(gimbalFixer.cmds, "attributeQuery", return_value=True), \
             mock.patch.object(gimbalFixer.cmds, "getAttr", return_value=0), \
             mock.patch.object(gimbalFixer, "analyze_rotation_orders", return_value=[
                 (index / 10.0, order) for index, order in enumerate(gimbalFixer.ROTATE_ORDERS)
             ]):
            window.refresh_data()
        check(all(button.isEnabled() and button.text() for button in window.order_buttons),
              name + ": populated rotation orders")
        check(all("#4d4d4d" not in button.styleSheet() for button in window.order_buttons),
              name + ": material survives selection refresh")
        window._disable_buttons()
        check(all(not button.isEnabled() for button in window.order_buttons), name + ": disabled state")
    if isinstance(window, uiMod.ContextPopupWindow):
        # Magnetic anchoring keeps a flat edge and rounded mask during detachment.
        window._tail_on_top = True
        window._tail_x = window.width() // 2
        window._set_body_margins()
        window.repaint()
        app.processEvents()
        check(window.mask().contains(QtCore.QPoint(40, 2)), name + ": flat top edge")
        window.detach_from_anchor()
        check(not window.mask().contains(QtCore.QPoint(0, 0)), name + ": detached corner stays clipped")
        window.repaint()
        app.processEvents()
        check(window.layout().contentsMargins().top() == 1, name + ": detached margins")
    before = (window.size(), surface._region)
    window.hide()
    window.show()
    app.processEvents()
    check((window.size(), surface._region) == before, name + ": reopen shape {} -> {}".format(before[0], window.size()))
    if window.minimumWidth() != window.maximumWidth():
        window.resize(window.width() + 30, window.height() + 20)
        app.processEvents()
        # Qt.Popup may hide on native focus changes; render also delivers a
        # hidden widget's deferred resize event before checking its region.
        window.grab()
        check(window.mask().boundingRect().width() >= window.width() - 2, name + ": resized region")
    window.close()
    app.processEvents()
    checks.append(name)
    print("PASS " + name, flush=True)


with mock.patch.object(uiMod, "close_animkey_tool_windows"), \
     mock.patch.object(uiMod, "get_maya_main_window", return_value=None), \
     mock.patch.object(configMod, "get_setting", side_effect=lambda key, default=None: False if key == 'check_updates' else default), \
     mock.patch.object(configMod, "get_user_folder_path", return_value=str(output / "preferences")):
    for name, factory in (
        ("settings", settings.SettingsWindow),
        ("bake", bakeAnim.BakeFactoryWindow),
        ("gimbal", gimbalFixer.GimbalFixerWindow),
        ("retimer", retimer.RetimerWindow),
        ("cleaner", animCleaner.AnimCleanerWindow),
        ("recovery", animCrash.RecoveryWindow),
        ("temp-controls", tempControlsUI.TempControlsWindow),
        ("switcher", switcher.SwitcherWindow),
        ("sets", selectionSets.SetManagerWindow),
        ("set-input", lambda: selectionSets.PopupInput(palette=selectionSets.SET_COLOR_PALETTE)),
        ("flash-buttons", flashbuttons.FlashButtonsPanel),
        ("flash-editor", lambda: flashbuttons.ButtonEditDialog(SimpleNamespace(label_text="Test", button_id=0))),
        ("onion", lambda: brush.OnionSettingsDialog(None)),
        ("uninstall", settings.AnimKeyUninstallDialog),
        ("welcome", WelcomeWindow),
    ):
        try:
            verify(name, factory)
        except Exception as error:
            import traceback
            traceback.print_exc()
            failures.append(name + ": " + repr(error))

print("Verified {} windows; {} failures".format(len(checks), len(failures)), flush=True)
for failure in failures:
    print("FAIL " + failure, flush=True)
sys.exit(1 if failures else 0)
