"""Large, readable help cards for toolbar buttons and their sub-menus.

Hovering a toolbar button, or an entry of the menu it opens, shows a short
explanation in a card: big title, one or two plain sentences and an optional
hint (modifiers / right click). It replaces Qt's small grey tooltip there.

Event filters are installed only on the toolbar's own buttons, and menus are
hooked when the toolbar opens them, so Maya's other widgets are untouched.
"""
import os
import re

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets


DEFAULT_ACCENT = "#5bc0be"
SHOW_DELAY_MS = 380
MENU_DELAY_MS = 220
MAX_CARD_WIDTH = 360
GIF_WIDTH = 480

# Settings keys (stored in AnimKey's config, edited in Settings > General > Popups).
ENABLED_KEY = "help_popups_enabled"
DELAY_KEY = "help_popups_delay_ms"
OPACITY_KEY = "help_popups_opacity"

GIF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "Gifs", "toolbar")

# Toolbar button objectName -> demo GIF in data/Gifs/toolbar.
BUTTON_GIFS = {
    "ISO": "isolate.gif",
    "OPP": "select_opposite.gif",
    "TRL": "motion_trail.gif",
    "RST": "reset_values.gif",
    "OFF": "animation_offset.gif",
    "HIR": "select_hierarchy.gif",
}


def _setting(key, default):
    try:
        from AnimKey.mods import configMod
        return configMod.get_setting(key, default)
    except Exception:
        return default


def popups_enabled():
    return bool(_setting(ENABLED_KEY, True))


def popup_delay_ms():
    try:
        return max(0, min(5000, int(_setting(DELAY_KEY, SHOW_DELAY_MS))))
    except (TypeError, ValueError):
        return SHOW_DELAY_MS


def popup_opacity():
    """Card opacity 0.2..1.0 (stored as percent)."""
    try:
        percent = float(_setting(OPACITY_KEY, 100))
    except (TypeError, ValueError):
        percent = 100.0
    return max(20.0, min(100.0, percent)) / 100.0


def button_gif(key):
    name = BUTTON_GIFS.get(key)
    if not name:
        return None
    path = os.path.join(GIF_DIR, name)
    return path if os.path.isfile(path) else None


# ---------------------------------------------------------------------------
# Help texts. Buttons: objectName -> (title, description, hint, accent).
# ---------------------------------------------------------------------------

BUTTON_HELP = {
    "ISO": ("Isolate", "Shows only the whole rig of the selected control or geo in the active viewport. Click again to show everything.", "Right click: include parented props", "#5bc0be"),
    "ALN": ("Align", "Snaps the selected objects to the last selected one.", "Right click: choose channels", "#88c0d0"),
    "OPP": ("Select Opposite", "Selects the mirrored control on the other side of the rig.", "Right click: add the opposite to the selection", "#d08770"),
    "TRL": ("Motion Trail", "Draws the path of the selected control across time in the viewport.", "Right click: trail options", "#a3be8c"),
    "RST": ("Reset Values", "Returns the selected controls to their default pose.", "Right click: snapshot your own defaults", "#ebcb8b"),
    "OFF": ("Animation Offset", "Moves a control on one frame and carries that change through the whole animation.", "Right click: offset status", "#d08770"),
    "HIR": ("Select Hierarchy", "Selects the controls below the current selection.", "Right click: more selection modes", "#ebcb8b"),
    "MIR": ("Mirror", "Copies the pose to the other side of the rig.", "Right click: mirror modes and snapshots", "#81a1c1"),
    "C": ("Copy Animation", "Copies the animation of the selection to paste on other controls.", "Right click: paste, poses and files", "#a3be8c"),
    "LKN": ("Link Objects", "Keeps one object following another while you animate.", "Right click: copy / paste link ranges", "#b48ead"),
    "CAM": ("Follow Cam", "Makes the viewport camera follow the selected control.", "Right click: follow modes", "#5e81ac"),
    "WS": ("World Space", "Copies the world position of the selection to paste it back after changes.", "Shift: paste · Ctrl: auto pin · Right click: ranges", "#a3be8c"),
    "PIV": ("Temp Pivot", "Rotate or move the selection around a temporary pivot without touching the rig.", "Shift: last object · Ctrl: center · Alt: edit · Right click: options", "#5bc0be"),
    "TMC": ("Temp Controls", "Creates temporary controls to animate the rig in another space, then bakes back.", "Ctrl: relative · Shift: object · Alt: bake · Right click: window", "#5bc0be"),
    "RUL": ("Micro Move", "Slows the manipulator down for very fine adjustments.", "Right click: precision", "#ebcb8b"),
    "PLT": ("Plateau Tangents", "Smooth curves that never overshoot past the keys.", None, "#88c0d0"),
    "STP": ("Step Tangents", "Holds every key until the next one. Ideal for blocking.", None, "#bf616a"),
    "FLT": ("Flat Tangents", "Eases in and out of every selected key.", None, "#a3be8c"),
    "LIN": ("Linear Tangents", "Straight, constant-speed motion between keys.", None, "#ebcb8b"),
    "CLP": ("Clamped Tangents", "Smooth curves, flat when neighbouring keys match.", None, "#d08770"),
    "SPL": ("Spline Tangents", "Fully smooth curves through the keys.", None, "#b48ead"),
    "AUT": ("Auto Tangents", "Maya's automatic smooth tangents without overshoot.", None, "#5e81ac"),
    "GMB": ("Gimbal Fixer", "Finds a rotate order that avoids gimbal lock and converts the animation.", None, "#d08770"),
    "SWT": ("Switcher", "Switches IK / FK or spaces while keeping the pose.", None, "#5bc0be"),
    "BAK": ("Bake Animation", "Bakes the animation of the selection to keys on every frame.", None, "#a3be8c"),
    "RTM": ("Retimer", "Changes the timing of your keys without breaking the poses.", None, "#b48ead"),
    "ACL": ("Animation Cleaner", "Removes redundant keys and static curves from the selection.", None, "#88c0d0"),
    "BTNS": ("Flash Buttons", "Opens your panel of custom quick buttons.", "Right click: import / export", "#5bc0be"),
    "SETS": ("Selection Sets", "Saves groups of controls to select them in one click.", "Right click: import, export and clear", "#3498db"),
    "BRUSH": ("Brush", "Draw over the viewport to sketch poses and arcs, frame by frame.", None, "#e8a0bf"),
    "ACR": ("Animation Recovery", "Autosaves your animation and lets you restore any checkpoint.", None, "#bf616a"),
    "SETTINGS": ("Settings", "AnimKey preferences, shortcuts and toolbar layout.", None, "#9aa3b2"),
    "SELECTOR": ("Selector", "How many objects are selected. Click to open the selector window.", None, "#9aa3b2"),
    "TANGENT_TOGGLE": ("Tangents", "Shows or hides the tangent buttons.", None, "#88c0d0"),
}

# Sub-menu entries: (owner button objectName, label) -> description.
# "*" applies to every menu.
MENU_HELP = {
    ("ISO", "Include Parented Objects"): "Also isolates props, swords or controls parented or constrained to the rig.",

    ("ALN", "Translate"): "Align position.",
    ("ALN", "Rotate"): "Align rotation.",
    ("ALN", "Scale"): "Align scale.",
    ("ALN", "Apply Align"): "Aligns the selection now using the checked channels.",

    ("RST", "Snapshot Default Values"): "Saves the current pose as the default of these controls.",
    ("RST", "Delete Snapshot"): "Forgets the saved default of the selected controls.",
    ("RST", "Clear All Saved Data"): "Removes every saved default snapshot.",

    ("OPP", "Add Select Opposite"): "Adds the opposite controls to the current selection.",

    ("MIR", "All Mirror (Swap Both Sides)"): "Swaps the left and right poses.",
    ("MIR", "Mirror to Left"): "Copies the right side onto the left.",
    ("MIR", "Mirror to Right"): "Copies the left side onto the right.",
    ("MIR", "Quick Mirror (No Snapshot)"): "Mirrors the pose now, detecting sides automatically.",
    ("MIR", "Auto Mirror (Real-time)"): "Mirrors live while you pose one side.",
    ("MIR", "Snapshot"): "Stores the neutral pose used to mirror accurately.",
    ("MIR", "Delete Snapshot"): "Removes the stored mirror neutral pose.",

    ("C", "Copy Animation"): "Copies the keys of the selection.",
    ("C", "Save Animation"): "Saves the selection's animation to a file.",
    ("C", "Load Animation"): "Loads an animation file onto the selection.",
    ("C", "Paste Animation"): "Pastes the copied keys, replacing the current ones.",
    ("C", "Paste Insert"): "Pastes at the current frame, pushing later keys forward.",
    ("C", "Paste Opposite"): "Pastes onto the opposite side of the rig.",
    ("C", "Copy Pose"): "Copies the pose on the current frame.",
    ("C", "Paste Pose"): "Pastes the copied pose on the current frame.",
    ("C", "Save Pose"): "Saves the current pose to a file.",
    ("C", "Load Pose"): "Applies a saved pose file.",

    ("HIR", "Select Hierarchy Controls"): "Selects every control below the selection.",
    ("HIR", "Select Rig Controls"): "Selects all controls of the selected rig.",
    ("HIR", "Select Animated Controls"): "Selects only the controls that have keys.",
    ("HIR", "Select Visible NURBS Curves"): "Selects every visible curve control in the scene.",

    ("OFF", "Animation Offset Active"): "An offset is running. Click the button to finish it.",
    ("OFF", "No Active Offset"): "Click the button to start an offset on the selection.",

    ("CAM", "Follow Translation & Rotation"): "Camera follows position and rotation.",
    ("CAM", "Follow Translation Only"): "Camera follows position only.",
    ("CAM", "Follow Rotation Only"): "Camera follows rotation only.",
    ("CAM", "Remove Follow Cam"): "Stops following and restores the camera.",

    ("LKN", "Copy Link Frame"): "Stores the link offset on this frame.",
    ("LKN", "Copy Link Playback Range"): "Stores the link offset over the playback range.",
    ("LKN", "Copy Link All Animation"): "Stores the link offset over all keys.",
    ("LKN", "Copy Link Selected Timeline Range"): "Stores the link offset over the highlighted range.",
    ("LKN", "Paste Link Frame"): "Applies the stored link on this frame.",
    ("LKN", "Paste Link Playback Range"): "Applies the stored link over the playback range.",
    ("LKN", "Paste Link All Animation"): "Applies the stored link over all keys.",
    ("LKN", "Paste Link Selected Timeline Range"): "Applies the stored link over the highlighted range.",
    ("LKN", "Auto-Link"): "Keeps the link updating while you animate.",

    ("WS", "Copy Worldspace - All Animation"): "Copies world positions of every keyed frame.",
    ("WS", "Copy Worldspace - Selected Timeline Range"): "Copies world positions in the highlighted range.",
    ("WS", "Copy Worldspace - Playback Range"): "Copies world positions in the playback range.",
    ("WS", "Copy Worldspace - Current Frame"): "Copies the world position on this frame.",
    ("WS", "Paste Worldspace Animation"): "Puts the selection back on the copied world path.",
    ("WS", "Paste Worldspace - Selected Timeline Range"): "Pastes world positions in the highlighted range.",
    ("WS", "Paste Worldspace - Playback Range"): "Pastes world positions in the playback range.",
    ("WS", "Paste Worldspace Frame"): "Pastes the copied world position on this frame.",
    ("WS", "Auto Worldspace Pin"): "Keeps the selection pinned in world space automatically.",

    ("RUL", "Enable"): "Turns fine manipulation on.",
    ("RUL", "Disable"): "Turns fine manipulation off.",
    ("RUL", "{n}x"): "How many times slower the manipulator moves.",
    ("RUL", "Select Hierarchy Controls"): "Selects every control below the selection.",

    ("SETS", "Export Sets..."): "Saves all selection sets to a file.",
    ("SETS", "Import Sets..."): "Loads selection sets from a file.",
    ("SETS", "Clear All"): "Deletes every selection set.",

    ("BTNS", "Export Flash Buttons..."): "Saves your Flash Buttons to a file.",
    ("BTNS", "Import Flash Buttons..."): "Loads Flash Buttons from a file.",

    ("TRL", "Refresh Trail"): "Recalculates the trail after big changes.",
    ("TRL", "Reset Trail Settings"): "Restores the default trail options.",
    ("TRL", "Show / Hide Trail"): "Toggles the trail without deleting it.",
    ("TRL", "Show Key Markers"): "Marks the frames that have keys.",
    ("TRL", "Show Frame Markers"): "Draws a dot on every frame.",
    ("TRL", "Camera Space"): "Draws the trail relative to the camera.",
    ("TRL", "Editable Key Handles"): "Drag key dots in the viewport to edit poses.",
    ("TRL", "Editable Tangent Handles"): "Drag tangent handles in the viewport.",
    ("TRL", "Show Pop Warnings"): "Highlights sudden jumps in the motion.",
    ("TRL", "Quality"): "How densely the trail is sampled.",
    ("TRL", "Visible Range"): "How many frames around the current one are drawn.",
    ("TRL", "Pop Sensitivity"): "How big a jump must be to be flagged.",
    ("TRL", "Color"): "Trail colours and palettes.",
    ("TRL", "Color Mode"): "How colour changes along the trail.",
    ("TRL", "Custom Colors"): "Pick your own trail colours.",
    ("TRL", "Every Frame"): "One sample per frame.",
    ("TRL", "Fine (2 Dots per Frame)"): "Two samples per frame for smoother arcs.",
    ("TRL", "Full Trail"): "Draws the whole animation range.",
    ("TRL", "Sensitive"): "Flags even small jumps.",
    ("TRL", "Normal"): "Balanced jump detection.",
    ("TRL", "Relaxed"): "Flags only big jumps.",
}

_FRAMES_RE = re.compile(r"^\+-\s*\d+\s*Frames$")
_MULTIPLIER_RE = re.compile(r"^\d+x$")
_LEADING_DECORATION_RE = re.compile(r"^[^\w(+]+")


def normalize_label(text):
    """Menu text without mnemonics, bullets, emoji or numeric variants."""
    text = (text or "").replace("&", "").strip()
    text = _LEADING_DECORATION_RE.sub("", text).strip()
    if _MULTIPLIER_RE.match(text):
        return "{n}x"
    if _FRAMES_RE.match(text):
        return "+- N Frames"
    return text


MENU_HELP[("TRL", "+- N Frames")] = "Frames drawn before and after the current one."


def _plain_tooltip(tooltip):
    """Split an existing (possibly rich) tooltip into title/description/hint."""
    tooltip = (tooltip or "").strip()
    if not tooltip:
        return None
    if re.search(r"<\s*(b|br|div|span|p|html)\b", tooltip, re.IGNORECASE):
        document = QtGui.QTextDocument()
        document.setHtml(tooltip)
        tooltip = document.toPlainText()
    lines = [line.strip() for line in tooltip.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) == 1:
        line = lines[0]
        for separator in (" — ", " - ", " | "):
            if separator in line:
                title, rest = line.split(separator, 1)
                parts = rest.split(" | ")
                return title.strip(), parts[0].strip(), " · ".join(p.strip() for p in parts[1:]) or None
        return line, None, None
    hint = " · ".join(line for line in lines[2:] if not line.endswith(":")) or None
    return lines[0], lines[1], hint


def button_entry(button):
    """(title, description, hint, accent) for a toolbar button, or None."""
    key = button.objectName()
    if key in BUTTON_HELP:
        return BUTTON_HELP[key] + (button_gif(key),)
    parsed = _plain_tooltip(button.toolTip())
    if not parsed:
        return None
    title, description, hint = parsed
    return title, description, hint, DEFAULT_ACCENT, None


def menu_entry(owner, action):
    """(title, description, hint, accent) for a menu action, or None."""
    if action is None or action.isSeparator():
        return None
    label = normalize_label(action.text())
    if not label:
        return None
    description = MENU_HELP.get((owner, label)) or MENU_HELP.get(("*", label))
    if description is None:
        tooltip = (action.toolTip() or "").strip()
        if tooltip and normalize_label(tooltip) != label:
            description = tooltip
    if description is None:
        return None
    accent = BUTTON_HELP.get(owner, (None, None, None, DEFAULT_ACCENT))[3]
    title = action.text().replace("&", "").strip()
    title = _LEADING_DECORATION_RE.sub("", title).strip() or label
    return title, description, None, accent, None


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

class HelpCard(QtWidgets.QWidget):
    """Frameless, non-activating card with a soft shadow and accent strip."""

    SHADOW = 10
    RADIUS = 12

    def __init__(self):
        super(HelpCard, self).__init__(
            None,
            QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowDoesNotAcceptFocus | QtCore.Qt.NoDropShadowWindowHint,
        )
        self.setObjectName("AnimKeyHelpCard")
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._accent = QtGui.QColor(DEFAULT_ACCENT)

        margin = self.SHADOW
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(margin + 20, margin + 14, margin + 18, margin + 16)
        layout.setSpacing(6)

        # Demo animation above the text (only for buttons that have a GIF).
        self.preview = QtWidgets.QLabel()
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet("background: transparent;")
        self.preview.setVisible(False)
        layout.addWidget(self.preview)
        self._movie = None
        self._gif = None

        self.title = QtWidgets.QLabel()
        self.description = QtWidgets.QLabel()
        self.hint = QtWidgets.QLabel()
        for label in (self.title, self.description, self.hint):
            label.setWordWrap(True)
            label.setTextFormat(QtCore.Qt.PlainText)
            layout.addWidget(label)
        self._fonts()

        self._fade = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(120)
        self._fade.setEasingCurve(QtCore.QEasingCurve.OutCubic)

    def _fonts(self):
        # Same typeface as every AnimKey window (window_style uses it too).
        try:
            from AnimKey.mods.maya_compat import get_maya_font
            base = get_maya_font()
        except Exception:
            base = QtWidgets.QApplication.font()
        for label, size, weight in ((self.title, 17, QtGui.QFont.DemiBold),
                                    (self.description, 14, QtGui.QFont.Normal),
                                    (self.hint, 12, QtGui.QFont.Medium)):
            font = QtGui.QFont(base)
            font.setPixelSize(size)
            font.setWeight(weight)
            label.setFont(font)
        self.title.setStyleSheet("color: #f4f5f7; background: transparent;")
        self.description.setStyleSheet("color: #c9ced6; background: transparent;")

    def _set_gif(self, path):
        if path == self._gif and self._movie is not None:
            return
        self.stop_movie()
        self._gif = None
        if self._movie is not None:
            self._movie.deleteLater()
            self._movie = None
        self.preview.clear()
        if not path:
            self.preview.setVisible(False)
            return
        movie = QtGui.QMovie(path, parent=self)
        if not movie.isValid():
            self.preview.setVisible(False)
            return
        movie.setCacheMode(QtGui.QMovie.CacheNone)
        size = QtGui.QImageReader(path).size()
        if size.isValid() and size.width() > 0:
            size = QtCore.QSize(GIF_WIDTH, int(round(size.height() * GIF_WIDTH / float(size.width()))))
        else:
            size = QtCore.QSize(GIF_WIDTH, int(GIF_WIDTH * 0.4))
        movie.setScaledSize(size)
        self.preview.setFixedSize(size)
        self.preview.setMovie(movie)
        self.preview.setVisible(True)
        self._movie = movie
        self._gif = path

    def stop_movie(self):
        if self._movie is not None:
            self._movie.stop()

    def set_entry(self, entry):
        title, description, hint, accent = entry[:4]
        gif = entry[4] if len(entry) > 4 else None
        self._set_gif(gif)
        self._accent = QtGui.QColor(accent or DEFAULT_ACCENT)
        self.title.setText(title or "")
        self.description.setText(description or "")
        self.description.setVisible(bool(description))
        self.hint.setText(hint or "")
        self.hint.setVisible(bool(hint))
        self.hint.setStyleSheet("color: {}; background: transparent;".format(self._accent.lighter(115).name()))
        chrome = 2 * self.SHADOW + 38
        if self._movie is not None:
            width = self.preview.width() + chrome
            inner = self.preview.width()
        else:
            inner = MAX_CARD_WIDTH - chrome
            natural = max(self.title.fontMetrics().horizontalAdvance(title or ""),
                          self.description.fontMetrics().horizontalAdvance(description or ""),
                          self.hint.fontMetrics().horizontalAdvance(hint or ""))
            width = min(MAX_CARD_WIDTH, max(200, natural + 2 * self.SHADOW + 40))
        for label in (self.title, self.description, self.hint):
            label.setMaximumWidth(inner)
        self.setFixedWidth(width)
        self.layout().activate()
        self.setFixedHeight(self.layout().heightForWidth(width) if self.layout().hasHeightForWidth()
                            else self.sizeHint().height())

    def show_at(self, point):
        self.move(point)
        opacity = popup_opacity()
        if self._movie is not None:
            self._movie.jumpToFrame(0)
            self._movie.start()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            self._fade.stop()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(opacity)
            self._fade.start()
        else:
            self._fade.stop()
            self.setWindowOpacity(opacity)
            self.update()

    def hideEvent(self, event):
        self.stop_movie()
        super(HelpCard, self).hideEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        body = QtCore.QRectF(self.rect()).adjusted(self.SHADOW, self.SHADOW, -self.SHADOW, -self.SHADOW)
        for step in range(self.SHADOW, 0, -2):
            shadow = QtGui.QPainterPath()
            shadow.addRoundedRect(body.adjusted(-step, -step + 3, step, step + 3),
                                  self.RADIUS + step, self.RADIUS + step)
            painter.fillPath(shadow, QtGui.QColor(0, 0, 0, 7))
        path = QtGui.QPainterPath()
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)
        painter.fillPath(path, QtGui.QColor(36, 38, 44, 246))
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 30), 1))
        painter.drawPath(path)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QtCore.QRectF(body.left(), body.top(), 5, body.height()), self._accent)
        painter.restore()
        painter.end()


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

def _alive(obj):
    try:
        from AnimKey.mods.maya_compat import is_qt_object_valid
        return obj is not None and is_qt_object_valid(obj)
    except Exception:
        return obj is not None


def _screen_rect(point):
    screen = QtWidgets.QApplication.screenAt(point) or QtWidgets.QApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1920, 1080)


class HelpPopupController(QtCore.QObject):
    def __init__(self, parent=None):
        super(HelpPopupController, self).__init__(parent)
        self._card = None
        self._pending = None
        self._buttons = set()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_pending)

    # -- card -------------------------------------------------------------
    def card(self):
        if self._card is None or not _alive(self._card):
            self._card = HelpCard()
        return self._card

    def hide(self):
        self._timer.stop()
        self._pending = None
        if self._card is not None and _alive(self._card):
            self._card.hide()

    def _schedule(self, delay, entry, place):
        if not popups_enabled():
            self.hide()
            return
        self._pending = (entry, place)
        self._timer.start(delay)

    def _show_pending(self):
        if not self._pending:
            return
        entry, place = self._pending
        self._pending = None
        card = self.card()
        card.set_entry(entry)
        point = place(card)
        if point is not None:
            card.show_at(point)

    # -- toolbar buttons -----------------------------------------------------
    def install_button(self, button):
        if button in self._buttons:
            return
        button.installEventFilter(self)
        self._buttons.add(button)
        button.destroyed.connect(lambda *_, b=button: self._buttons.discard(b))

    def install_toolbar(self, root):
        count = 0
        for button in root.findChildren(QtWidgets.QAbstractButton):
            if button.objectName() in BUTTON_HELP or button.toolTip():
                self.install_button(button)
                count += 1
        return count

    def _place_below(self, widget):
        def place(card):
            if not _alive(widget) or not widget.isVisible():
                return None
            shadow = HelpCard.SHADOW
            top_left = widget.mapToGlobal(QtCore.QPoint(0, 0))
            x = top_left.x() + widget.width() // 2 - card.width() // 2
            y = top_left.y() + widget.height() + 8 - shadow
            area = _screen_rect(top_left)
            if y + card.height() > area.bottom():
                y = top_left.y() - card.height() - 8 + shadow
            x = max(area.left(), min(x, area.right() - card.width()))
            return QtCore.QPoint(x, max(area.top(), y))
        return place

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QtCore.QEvent.Enter:
            entry = button_entry(obj) if obj.isEnabled() else None
            if entry:
                self._schedule(popup_delay_ms(), entry, self._place_below(obj))
        elif kind in (QtCore.QEvent.Leave, QtCore.QEvent.MouseButtonPress,
                      QtCore.QEvent.MouseButtonDblClick, QtCore.QEvent.Hide,
                      QtCore.QEvent.Wheel, QtCore.QEvent.ContextMenu):
            self.hide()
        elif kind == QtCore.QEvent.ToolTip:
            # The card replaces Qt's small tooltip on covered buttons.
            if button_entry(obj):
                return True
        return False

    # -- menus ---------------------------------------------------------------
    @staticmethod
    def owner_key(menu):
        widget = menu.parentWidget()
        while widget is not None:
            if isinstance(widget, QtWidgets.QAbstractButton) and widget.objectName():
                return widget.objectName()
            widget = widget.parentWidget()
        return "*"

    def install_menu(self, menu, owner=None):
        if menu is None or menu.property("animkeyHelpInstalled"):
            return
        menu.setProperty("animkeyHelpInstalled", True)
        owner = owner or self.owner_key(menu)
        try:
            menu.setToolTipsVisible(False)
        except AttributeError:
            pass
        menu.hovered.connect(lambda action, m=menu, o=owner: self._menu_hovered(m, o, action))
        menu.aboutToHide.connect(self.hide)
        for submenu in menu.findChildren(QtWidgets.QMenu):
            self.install_menu(submenu, owner)

    def _menu_hovered(self, menu, owner, action):
        entry = menu_entry(owner, action)
        if not entry:
            self.hide()
            return
        # Menu entries follow the same setting, a little quicker than buttons.
        delay = int(popup_delay_ms() * MENU_DELAY_MS / float(SHOW_DELAY_MS))
        self._schedule(delay, entry, self._place_beside(menu, action))

    def _place_beside(self, menu, action):
        def place(card):
            if not _alive(menu) or not menu.isVisible():
                return None
            shadow = HelpCard.SHADOW
            rect = menu.actionGeometry(action)
            origin = menu.mapToGlobal(QtCore.QPoint(0, rect.top()))
            area = _screen_rect(origin)
            x = origin.x() + menu.width() + 6 - shadow
            if x + card.width() > area.right():
                x = origin.x() - card.width() - 6 + shadow
            y = origin.y() + rect.height() // 2 - card.height() // 2
            y = max(area.top(), min(y, area.bottom() - card.height()))
            return QtCore.QPoint(max(area.left(), x), y)
        return place


_controller = None


def controller():
    global _controller
    if _controller is None or not _alive(_controller):
        _controller = HelpPopupController(QtWidgets.QApplication.instance())
    return _controller


def install_toolbar(root):
    return controller().install_toolbar(root)


def install_menu(menu, owner=None):
    return controller().install_menu(menu, owner)


def hide():
    if _controller is not None and _alive(_controller):
        _controller.hide()
