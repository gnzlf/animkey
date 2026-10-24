"""Small upward entrances with window-owned timers and animation lifetimes."""

from AnimKey.mods.maya_compat import QtCore, is_qt_object_valid


class WindowReveal(QtCore.QObject):
    aboutToStart = QtCore.Signal()
    settled = QtCore.Signal()
    DURATION_MS = 180
    DISTANCE = 14

    def __init__(self, window, enabled=True):
        super().__init__(window)
        self.window = window
        self.enabled = enabled
        self.applying = False
        self.pending = False
        self._target = None
        self._last_applied = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)
        self._animation = QtCore.QVariantAnimation(self)
        self._animation.setDuration(self.DURATION_MS)
        self._animation.setEasingCurve(QtCore.QEasingCurve.OutQuad)
        self._animation.setStartValue(float(self.DISTANCE))
        self._animation.setEndValue(0.0)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self.settled.emit)
        window.installEventFilter(self)

    @property
    def running(self):
        return self._animation.state() == QtCore.QAbstractAnimation.Running

    def _move(self, position):
        # Maya may deliver Hide from QWidget's native destructor, after the
        # Python wrapper has already been invalidated by shiboken.
        if not is_qt_object_valid(self.window):
            return
        self.applying = True
        try:
            self._last_applied = QtCore.QPoint(position)
            self.window.move(position)
        finally:
            self.applying = False

    def _step(self, offset):
        if self._target is not None:
            self._move(self._target + QtCore.QPoint(0, round(offset)))

    def _prime(self):
        window = self.window
        if (not self.enabled or not is_qt_object_valid(window) or not window.isVisible()
                or window.isMaximized() or window.isFullScreen()):
            return
        self._target = window.pos()
        self.pending = True
        self.aboutToStart.emit()
        # Set the entrance offset inside Show, before the first paint. Waiting
        # for a timer here briefly displayed the final position, then jumped down.
        self._step(float(self.DISTANCE))
        self._timer.start(0)

    def _start(self):
        self.pending = False
        if is_qt_object_valid(self.window) and self.window.isVisible():
            self._animation.start()

    def cancel(self, restore=False):
        active = self.running or self.pending
        self._timer.stop()
        self._animation.stop()
        self.pending = False
        if restore and active and self._target is not None:
            self._move(self._target)
        if active and is_qt_object_valid(self.window):
            self.settled.emit()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QtCore.QEvent.Show and self.enabled:
            self._prime()
        elif kind == QtCore.QEvent.Hide:
            self.cancel(restore=True)
        elif kind == QtCore.QEvent.WindowStateChange:
            self.cancel()
        elif kind == QtCore.QEvent.Move and not self.applying:
            # Native move notifications can arrive after QWidget.move returns.
            # An echo of our last position must not cancel or restart an entrance.
            if event.pos() == self._last_applied:
                return False
            if self.pending:
                # Accept anchors assigned immediately after show(), while still
                # keeping the very first rendered frame at the entrance offset.
                self._target = QtCore.QPoint(event.pos())
                self._step(float(self.DISTANCE))
            elif self.running:
                self.cancel()
        return False


def apply_window_reveal(window, enabled=True):
    reveal = getattr(window, '_window_reveal', None)
    if reveal is None:
        reveal = WindowReveal(window, enabled=enabled)
        window._window_reveal = reveal
    return reveal
