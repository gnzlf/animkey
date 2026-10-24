"""Temp Pivot options in the shared floating tool window."""
import maya.cmds as cmds

from AnimKey.buttons import tempPivot as pivot
from AnimKey.core import temp_transform as tf
from AnimKey.core.executionGuard import animkey_execution
from AnimKey.mods.maya_compat import QtWidgets, get_maya_main_window
from AnimKey.mods.uiMod import ContextPopupWindow


class TempPivotWindow(ContextPopupWindow):
    MODES = ("standard", "last", "center", "world")

    def _set_body_margins(self):
        if self.layout():
            self.layout().setContentsMargins(12,16,12,20)

    def __init__(self, anchor_button=None):
        super().__init__(anchor_button=anchor_button,parent=get_maya_main_window())
        self.setObjectName("AnimKey_TempPivot")
        self.setWindowTitle("Temp Pivot")
        self.resize(330,240)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12,16,12,20)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("TEMP PIVOT")
        title.setStyleSheet("font-weight:bold; color:#5bc0be; font-size:14px;")
        header.addWidget(title)
        header.addStretch()
        close = QtWidgets.QPushButton("×")
        close.setFixedSize(24,24)
        close.clicked.connect(self.close)
        header.addWidget(close)
        layout.addLayout(header)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(("Standard / Saved Pivot", "Last Selected Object", "Selection Center", "World Space"))
        if pivot.is_active():
            self.mode.setCurrentIndex(self.MODES.index(pivot._session.mode))
        form = QtWidgets.QFormLayout()
        form.addRow("Pivot Mode",self.mode)
        layout.addLayout(form)
        self.toggle = self._button("",self._toggle,layout)
        self._button("Apply Mode to Selection",self._activate,layout)
        row = QtWidgets.QHBoxLayout()
        self.edit = self._button("Edit Pivot / Finish",pivot.toggle_edit_temp_pivot,row)
        self.reset = self._button("Reset Pivot",pivot.reset_temp_pivot_offsets,row)
        layout.addLayout(row)
        self.status = QtWidgets.QLabel("Select rig controls in Maya. Left click PIV toggles the pivot; right click opens these options.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    def _button(self,label,callback,layout):
        button = QtWidgets.QPushButton(label)
        button.clicked.connect(lambda *_:self._run(callback))
        layout.addWidget(button)
        return button

    def _run(self,callback):
        try:
            with animkey_execution("Temp Pivot window"):
                return callback()
        except Exception as exc:
            cmds.warning("AnimKey Temp Pivot: {}".format(exc))
        finally:
            self.refresh()

    def _activate(self):
        nodes = tf.transforms()
        if pivot.is_active():
            if not nodes or pivot._session.proxy.name() in nodes:
                nodes = [r.name() for r in pivot._session.targets if r.name()]
                if pivot._session.mode == "last" and pivot._session.anchor.name() not in nodes:
                    nodes.append(pivot._session.anchor.name())
            pivot.deactivate_temp_pivot()
        return pivot.activate_temp_pivot(objects=nodes,pivot_mode=self.MODES[self.mode.currentIndex()])

    def _toggle(self):
        return pivot.deactivate_temp_pivot() if pivot.is_active() else self._activate()

    def refresh(self):
        self.toggle.setText("Deactivate Temp Pivot" if pivot.is_active() else "Activate Temp Pivot")
        self.edit.setEnabled(pivot.is_active())
        self.reset.setEnabled(pivot.is_active())
