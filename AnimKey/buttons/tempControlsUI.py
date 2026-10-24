"""Creation and system settings windows for Temp Controls."""
import json
import os
import maya.cmds as cmds
import maya.api.OpenMaya as om

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets, get_maya_main_window
from AnimKey.mods.uiMod import ContextPopupWindow, screen_available_geometry
from AnimKey.core.settings_style import SettingsSwitch, WindowControl
from AnimKey.buttons import tempControls as tc
from AnimKey.core import temp_transform as tf
from AnimKey.core import temp_shapes as shapes


def _run(callback,*args,**kwargs):
    try:
        return callback(*args,**kwargs)
    except Exception as exc:
        cmds.warning("AnimKey Temp Controls: {}".format(exc))
        return None


class TempControlsWindow(ContextPopupWindow):
    def _set_body_margins(self):
        if self.layout():
            self.layout().setContentsMargins(18,16,18,16)

    def __init__(self,anchor_button=None,panel=False):
        super().__init__(anchor_button=anchor_button,parent=get_maya_main_window())
        self.setObjectName("AnimKey_TempControls")
        self.setWindowTitle("AnimKey Temp Controls")
        self.resize(460,min(700,max(480,screen_available_geometry(self).height()-64)))
        self.callbacks = []
        self.refreshing = False
        self._closed = False
        self._appearance_signature = ()
        self._appearance_base = {}
        self._pending_system_selection = None
        self.saved_selection = [tf.NodeRef(n) for n in tf.transforms()]
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18,16,18,16)
        outer.setSpacing(12)
        header = QtWidgets.QHBoxLayout()
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(1)
        title = QtWidgets.QLabel("Temp Controls")
        title.setObjectName("settingsTitle")
        titles.addWidget(title)
        subtitle = QtWidgets.QLabel("AnimKey")
        subtitle.setObjectName("settingsSubtitle")
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        close = WindowControl("close","Close",self)
        close.clicked.connect(self.close)
        header.addWidget(close)
        outer.addLayout(header)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("tempControlTabs")
        outer.addWidget(self.tabs,1)
        self._creation_tab()
        self._panel_tab()
        self._tools_tab()
        outer.addWidget(self.finish_group)
        self.status = self._note("Select rig controls in Maya to create a temporary control.")
        outer.addWidget(self.status)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.setCurrentIndex(1 if panel else 0)
        self._tab_changed()
        self.callbacks.append(om.MEventMessage.addEventCallback("SelectionChanged",self._selection_changed))
        for event in ("Undo","Redo"):
            self.callbacks.append(om.MEventMessage.addEventCallback(event,self._undo_refresh))
        for event in (om.MSceneMessage.kBeforeNew,om.MSceneMessage.kBeforeOpen):
            self.callbacks.append(om.MSceneMessage.addCallback(event,lambda *_: self.close()))
        self.refresh()

    def _note(self,text):
        label = QtWidgets.QLabel(text)
        label.setProperty("muted",True)
        label.setWordWrap(True)
        return label

    def _page(self,title):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4,8,12,8)
        layout.setSpacing(14)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(page)
        self.tabs.addTab(scroll,title)
        return layout

    def _group(self,title,layout):
        group = QtWidgets.QGroupBox(title)
        body = QtWidgets.QVBoxLayout(group)
        body.setContentsMargins(2,4,2,4)
        body.setSpacing(8)
        layout.addWidget(group)
        return group,body

    def _form(self,layout):
        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        return form

    def _combo(self,items):
        widget = QtWidgets.QComboBox()
        widget.addItems(items)
        widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding,QtWidgets.QSizePolicy.Fixed)
        return widget

    def _button(self,title,callback,layout,role=None):
        button = QtWidgets.QPushButton(title)
        if role:
            button.setProperty("role",role)
        button.clicked.connect(lambda *_:self._action(callback))
        layout.addWidget(button)
        return button

    def _action(self,callback):
        if self.refreshing or self._closed:
            return
        result = _run(callback)
        self.refresh()
        return result

    def _creation_tab(self):
        layout = self._page("Create")
        _,setup = self._group("New control",layout)
        form = self._form(setup)
        self.label = QtWidgets.QLineEdit()
        self.label.setPlaceholderText("Automatic name")
        self.family = self._combo(tc.SYSTEMS)
        form.addRow("System",self.family)
        form.addRow("Name",self.label)
        self.help = self._note("")
        setup.addWidget(self.help)
        _,space = self._group("Space",layout)
        form = self._form(space)
        self.position = self._combo(tc.SPACES)
        self.orientation = self._combo(tc.SPACES)
        form.addRow("Position",self.position)
        form.addRow("Orientation",self.orientation)
        self.lock_space = SettingsSwitch("Link position and orientation")
        self.lock_space.setChecked(True)
        self.orientation.setEnabled(False)
        space.addWidget(self.lock_space)
        _,options = self._group("Options",layout)
        self.sample_frames = SettingsSwitch("Preserve motion on every frame")
        self.sample_frames.setChecked(True)
        self.sample_frames.setToolTip("Recommended for animated parents and constraints. Turning this off samples existing keys only; the final Bake still preserves every frame.")
        options.addWidget(self.sample_frames)
        self.defaults = SettingsSwitch("Reset control appearance")
        self.defaults.setToolTip("Use a fresh shape, size and orientation instead of the saved appearance.")
        options.addWidget(self.defaults)
        self.aim_row = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.aim_row)
        form.setContentsMargins(0,0,0,0)
        self.distance = QtWidgets.QDoubleSpinBox()
        self.distance.setRange(0,100000)
        self.distance.setSpecialValueText("Automatic")
        form.addRow("Aim distance",self.distance)
        options.addWidget(self.aim_row)
        layout.addStretch()
        self._button("Create Temp Control",self._create,layout,"primary").setMinimumHeight(34)
        self.family.currentIndexChanged.connect(self._filter_spaces)
        self.position.currentIndexChanged.connect(self._position_changed)
        self.lock_space.toggled.connect(self._lock_changed)
        self._filter_spaces()

    def _filter_spaces(self,*_):
        family = self.family.currentIndex()
        excluded = (1,4) if family == 1 else (4,) if family == 3 else ()
        for combo in (self.position,self.orientation):
            for i in range(6):
                combo.model().item(i).setEnabled(i not in excluded)
            if combo.currentIndex() in excluded:
                combo.setCurrentIndex(0)
        self.aim_row.setVisible(family == 2)
        descriptions = ("One independent control for each selected object.",
                        "A master follows the group animation. Add offsets without losing the original movement.",
                        "Base, Aim and Pole Vector controls with IK and an animatable Twist.",
                        "An ordered FK chain with World, Parent or Local orientation.")
        self.help.setText(descriptions[family])
        for combo in (self.position,self.orientation):
            combo.setToolTip("Relative / Grab Release uses the last selected object as the reference. Object follows the original parent; Child follows the original animation.")

    def _position_changed(self,index):
        if self.lock_space.isChecked():
            self.orientation.setCurrentIndex(index)

    def _lock_changed(self,checked):
        self.orientation.setEnabled(not checked)
        if checked:
            self.orientation.setCurrentIndex(self.position.currentIndex())

    def _create(self):
        nodes = [r.name() for r in self.saved_selection if r.name()]
        result = tc.create(node=nodes,name=self.label.text().strip() or None,system=self.family.currentIndex(),
                           positionSpace=self.position.currentIndex(),orientationSpace=self.orientation.currentIndex(),
                           bake_frames=self.sample_frames.isChecked(),aim_distance=self.distance.value() or None,
                           restoreDefaultProperties=self.defaults.isChecked())
        self.tabs.setCurrentIndex(1)
        self.refresh()
        self.status.setText("Created {} system(s). Select controls in Maya to edit them individually.".format(len(result)))
        return result

    def _panel_tab(self):
        layout = self._page("Controls")
        _,systems = self._group("Systems",layout)
        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setFixedHeight(82)
        self.list.setTextElideMode(QtCore.Qt.ElideMiddle)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.setToolTip("Choose one or more systems. Double click to select their controls in Maya.")
        self.list.itemDoubleClicked.connect(self._select_controls)
        self.list.itemSelectionChanged.connect(self._system_selected)
        systems.addWidget(self.list)
        row = QtWidgets.QHBoxLayout()
        self._button("Select controls",self._select_system_controls,row)
        self._button("Refresh",self.refresh,row)
        systems.addLayout(row)
        self.enabled = SettingsSwitch("Enabled")
        self.enabled.setObjectName("tempControlEnabled")
        self.enabled.setToolTip("Turning off commits the visible motion to the rig. Enable Revert below to restore the original animation instead.")
        self.enabled.toggled.connect(lambda checked:self._action(lambda:self._set_enabled(checked)))
        systems.addWidget(self.enabled)
        self.revert_mode = SettingsSwitch("Revert when disabling")
        self.revert_mode.setToolTip("Off: bake the current result. On: restore the original animation without applying temporary edits.")
        systems.addWidget(self.revert_mode)
        self.appearance_group,appearance = self._group("Appearance",layout)
        self.appearance_hint = self._note("Select a control in Maya, or edit all controls in the chosen systems.")
        appearance.addWidget(self.appearance_hint)
        size_row = QtWidgets.QHBoxLayout()
        size_row.addWidget(QtWidgets.QLabel("Size"))
        self.size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.size_slider.setRange(25,300)
        self.size_slider.setValue(100)
        self.size_slider.setTracking(False)
        self.size_slider.setFixedHeight(28)
        self.size_slider.setAccessibleName("Control display size")
        self.size_value = QtWidgets.QSpinBox()
        self.size_value.setRange(25,300)
        self.size_value.setSuffix(" %")
        self.size_value.setValue(100)
        self.size_value.setFixedWidth(76)
        self.size_value.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.size_value.setKeyboardTracking(False)
        self.size_value.setAlignment(QtCore.Qt.AlignCenter)
        size_row.addWidget(self.size_slider,1)
        size_row.addWidget(self.size_value)
        appearance.addLayout(size_row)
        self.size_slider.setToolTip("Display size relative to when these controls were selected. Rig transforms and animation are unchanged.")
        self.size_slider.valueChanged.connect(self._size_changed)
        self.size_value.valueChanged.connect(self._size_changed)
        form = self._form(appearance)
        self.shape = self._combo(shapes.NAMES)
        self.shape_orientation = self._combo(["X {}  /  Y {}  /  Z {}".format(*angles) for angles in shapes.ORIENTATIONS])
        self.shape.activated.connect(lambda i:self._appearance_changed(shape=i))
        self.shape_orientation.activated.connect(lambda i:self._appearance_changed(orientation=i))
        form.addRow("Shape",self.shape)
        form.addRow("Orientation",self.shape_orientation)
        self._button("Choose color...",self._color,appearance)
        self.finish_group = QtWidgets.QWidget()
        finish = QtWidgets.QHBoxLayout(self.finish_group)
        finish.setContentsMargins(0,6,0,0)
        self._button("Bake && Remove",lambda:self._clear(False),finish,"primary").setToolTip("Keep the visible animation on the rig and remove the selected temporary systems.")
        self._button("Revert && Remove",lambda:self._clear(True),finish).setToolTip("Remove the selected temporary systems and restore their original animation.")
        layout.addStretch()

    def _tools_tab(self):
        layout = self._page("Tools")
        self.tools_hint = self._note("Tools apply to the systems selected in Controls. Select individual controls or rig members in Maya when needed.")
        layout.addWidget(self.tools_hint)
        self.space_group,space = self._group("Change space",layout)
        form = self._form(space)
        self.panel_position = self._combo(tc.SPACES)
        self.panel_orientation = self._combo(tc.SPACES)
        self.chain_orientation = self._combo(("World","Parent","Local"))
        form.addRow("Position",self.panel_position)
        form.addRow("Orientation",self.panel_orientation)
        form.addRow("FK orientation",self.chain_orientation)
        self.chain_label = form.labelForField(self.chain_orientation)
        self.chain_orientation.currentIndexChanged.connect(self._chain_orientation)
        self._button("Apply space",self._change_spaces,space)
        self.extra_group,extras = self._group("Extra controls && pivots",layout)
        row = QtWidgets.QHBoxLayout()
        self._button("Add parent",lambda:self._control_action(lambda:tc.add_extra(parent=True)),row)
        self._button("Add child",lambda:self._control_action(tc.add_extra),row)
        self._button("Remove extra",lambda:self._control_action(tc.remove_extra),row)
        extras.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self._button("Edit pivot / Finish",self._edit_pivot,row)
        self._button("Reset pivot",lambda:self._control_action(tc.reset_pivot),row)
        extras.addLayout(row)
        self.members_group,membership = self._group("Group / FK members",layout)
        row = QtWidgets.QHBoxLayout()
        for label,action in (("Add","add"),("Remove","remove"),("Up","up"),("Down","down"),("Reverse","reverse")):
            self._button(label,lambda a=action:self._edit_members(a),row)
        membership.addLayout(row)
        self.grab_group,grab = self._group("Grab && release",layout)
        row = QtWidgets.QHBoxLayout()
        self._button("Grab",lambda:self._grab("grab"),row)
        self._button("Release",lambda:self._grab("release"),row)
        self._button("Remove",lambda:self._grab("remove"),row)
        grab.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.y_up = SettingsSwitch("Keep Y up")
        row.addWidget(self.y_up)
        self._button("Normalize",lambda:self._grab("normalize"),row)
        grab.addLayout(row)
        self.transitions = self._note("No transitions")
        grab.addWidget(self.transitions)
        _,files = self._group("Setup files",layout)
        row = QtWidgets.QHBoxLayout()
        self._button("Import...",import_setup,row)
        self.export_button = self._button("Export...",lambda:export_setup(self._systems()),row)
        files.addLayout(row)
        self.legacy_recovery = self._button("Recover legacy controls...",self._recover_legacy,files)
        layout.addStretch()

    def _systems(self):
        return [item.data(QtCore.Qt.UserRole) for item in self.list.selectedItems()] or tc.selected_systems()

    def _set_enabled(self,enabled):
        for system in self._systems():
            tc.set_enabled(system,enabled,revert=self.revert_mode.isChecked())

    def _clear(self,revert):
        tc.clear(self._systems(),revert=revert)

    def _select_controls(self,item):
        cmds.select(tc.controls(item.data(QtCore.Qt.UserRole)),replace=True)

    def _select_system_controls(self):
        nodes = [n for system in self._systems() for n in tc.controls(system)]
        if nodes:
            cmds.select(nodes,replace=True)

    def _appearance_nodes(self):
        available = [n for system in self._systems() for n in tc.controls(system)]
        selected = set(tc.selected_controls())
        return [n for n in available if n in selected] or available

    def _control_action(self,callback):
        nodes = self._appearance_nodes()
        if not nodes:
            raise ValueError("Choose a system in Controls first.")
        cmds.select(nodes,replace=True)
        return callback()

    def _edit_pivot(self):
        if tc._pivot_edit:
            return tc.edit_pivot()
        return self._control_action(tc.edit_pivot)

    def _edit_members(self,action):
        systems = self._systems()
        if len(systems) != 1:
            raise ValueError("Choose one Group or FK system in Controls first.")
        cmds.select(tf.transforms()+systems,replace=True)
        return tc.edit_members(action)

    def _appearance_changed(self,**properties):
        if self.refreshing:
            return
        self._action(lambda:self._apply_appearance(**properties))

    def _apply_appearance(self,**properties):
        def apply(nodes):
            for node in nodes:
                shapes.apply(node,**properties)
        self._edit_shapes(apply,"AnimKey Control Appearance")

    def _edit_shapes(self,callback,label):
        nodes = self._appearance_nodes()
        previous = cmds.ls(selection=True,long=True) or []
        self.refreshing = True
        try:
            with tf.undo_chunk(label):
                try:
                    callback(nodes)
                finally:
                    remaining = [n for n in previous if cmds.objExists(n)]
                    cmds.select(remaining,replace=True) if remaining else cmds.select(clear=True)
        finally:
            self.refreshing = False

    def _size_changed(self,value):
        if self.refreshing:
            return
        for widget in (self.size_slider,self.size_value):
            blocked = widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(blocked)
        def apply(nodes):
            for node in nodes:
                identity = cmds.ls(node,uuid=True)[0]
                base = self._appearance_base.get(identity,cmds.getAttr(node+'.shapeSize')[0])
                shapes.apply(node,size=[v*value/100. for v in base])
        self._action(lambda:self._edit_shapes(apply,"AnimKey Control Display Size"))

    def _color(self):
        nodes = self._appearance_nodes()
        if not nodes:
            return
        rgb = [float(v) for v in tf.string(nodes[0],"shapeColor").split(",")]
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*rgb),self,"Control color")
        if color.isValid():
            self._apply_appearance(color=(color.redF(),color.greenF(),color.blueF()))

    def _change_spaces(self):
        selected = tf.transforms()
        mode = self.panel_position.currentIndex()
        orientation = self.panel_orientation.currentIndex()
        relative = selected[-1] if selected and (2 in (mode,orientation)) else None
        tc.change_space(self._systems(),mode,orientation,relative=relative)

    def _chain_orientation(self,index):
        if self.refreshing:
            return
        def apply():
            with tf.undo_chunk("AnimKey FK Orientation"):
                for system in self._systems():
                    if cmds.getAttr(system+".systemID") == 3:
                        nodes = tc.controls(system)
                        samples = tf.capture(nodes,tf.times(nodes,frames=cmds.getAttr(system+".bakeFrames")))
                        cmds.setAttr(system+".chainOrientation",index)
                        tc._apply_control_samples(nodes,samples)
        self._action(apply)

    def _grab(self,action):
        selected = tf.transforms()
        driver = selected[-1] if selected and action == "grab" else None
        if action == "grab" and not driver:
            raise ValueError("Select a system in Controls and an external driver in Maya.")
        for system in self._systems():
            tc.grab(system,driver=driver,remove=action == "remove",normalise=action == "normalize",
                    y_up=self.y_up.isChecked() and action == "normalize")

    def _recover_legacy(self):
        from AnimKey.buttons import tempControlsLegacy
        return tempControlsLegacy.show(anchor_button=self.anchor_button)

    def _tab_changed(self,*_):
        self.finish_group.setVisible(self.tabs.currentIndex() == 1)
        self._defer_refresh()

    def _undo_refresh(self,*_):
        self._appearance_signature = None
        self._defer_refresh()

    def _selection_changed(self,*_):
        if self.refreshing or self._closed:
            return
        nodes = tf.transforms()
        systems = tc.selected_systems(nodes)
        if systems:
            self._pending_system_selection = set(systems)
        elif nodes:
            self.saved_selection = [tf.NodeRef(n) for n in nodes]
        self._defer_refresh()

    def _defer_refresh(self,*_):
        if not self._closed:
            self.refresh_timer.start(0)

    def _system_selected(self):
        if self.refreshing or self._closed:
            return
        self._pending_system_selection = None
        systems = self._systems()
        self.refreshing = True
        try:
            self.enabled.setEnabled(bool(systems))
            self.revert_mode.setEnabled(bool(systems))
            states = [bool(cmds.getAttr(s+".isEnabled")) for s in systems]
            self.enabled.setText("Enabled (mixed selection)" if len(set(states)) > 1 else "Enabled")
            self.enabled.setChecked(bool(states) and all(states))
            for group in (self.appearance_group,self.finish_group,self.space_group,self.extra_group,self.grab_group):
                group.setEnabled(bool(systems))
            self.export_button.setEnabled(bool(systems))
            families = [cmds.getAttr(s+".systemID") for s in systems]
            self.members_group.setVisible(any(f in (1,3) for f in families))
            self.members_group.setEnabled(len(systems) == 1 and families[0] in (1,3))
            self.chain_orientation.setVisible(families == [3])
            self.chain_label.setVisible(families == [3])
            self.tools_hint.setText("Selected: "+", ".join(s.rsplit('|',1)[-1] for s in systems) if systems else "Choose a system in Controls. Import is available below.")
            if systems:
                system = systems[0]
                excluded = set().union(*[(1,4) if f == 1 else (4,) if f == 3 else () for f in families])
                for combo,attr in ((self.panel_position,"positionSpace"),(self.panel_orientation,"orientationSpace")):
                    for i in range(6):
                        combo.model().item(i).setEnabled(i not in excluded)
                    combo.setCurrentIndex(cmds.getAttr(system+"."+attr))
                self.chain_orientation.setEnabled(families == [3])
                self.chain_orientation.setCurrentIndex(cmds.getAttr(system+".chainOrientation"))
                self.transitions.setText(" / ".join("{}: {}".format(cmds.getAttr(e+".frame"),tc._first(e,"driver") or "World")
                                                  for e in tc._grab_events(system) if cmds.getAttr(e+".frame") > -1000000) or "No transitions")
            nodes = self._appearance_nodes()
            signature = tuple(sorted(cmds.ls(nodes,uuid=True) or [])) if nodes else ()
            if signature != self._appearance_signature:
                self._appearance_signature = signature
                self._appearance_base = {cmds.ls(n,uuid=True)[0]:cmds.getAttr(n+'.shapeSize')[0] for n in nodes}
                self.size_slider.setValue(100)
                self.size_value.setValue(100)
            self.appearance_hint.setText("Editing {} control(s). Size affects display only.".format(len(nodes)))
            if nodes:
                self.shape.setCurrentIndex(cmds.getAttr(nodes[0]+'.shapeIndex'))
                self.shape_orientation.setCurrentIndex(cmds.getAttr(nodes[0]+'.shapeOrientation'))
        finally:
            self.refreshing = False

    def refresh(self,*_):
        if self.refreshing or self._closed:
            return
        prior = set(self._systems())
        if self._pending_system_selection is not None:
            prior = self._pending_system_selection
        self._pending_system_selection = None
        self.refreshing = True
        try:
            self.legacy_recovery.setVisible(bool(cmds.ls("*.tempTag",objectsOnly=True)))
            self.list.clear()
            for system in tc.all_systems():
                active = cmds.getAttr(system+".isEnabled")
                title = "{}  -  {}  /  {}".format(system.rsplit("|",1)[-1],tc.SYSTEMS[cmds.getAttr(system+".systemID")],"On" if active else "Off")
                item = QtWidgets.QListWidgetItem(title)
                item.setData(QtCore.Qt.UserRole,system)
                item.setToolTip(system)
                self.list.addItem(item)
                item.setSelected(system in prior)
        finally:
            self.refreshing = False
        self._system_selected()
        if self.tabs.currentIndex() == 0:
            self.status.setText("{} rig object(s) selected. Select them in Maya in the desired order.".format(len([r for r in self.saved_selection if r.name()])))
        else:
            self.status.setText("{} system(s) selected. Appearance changes affect display only.".format(len(self._systems())))

    def closeEvent(self,event):
        self._closed = True
        self.refresh_timer.stop()
        for callback in self.callbacks:
            try:
                om.MMessage.removeCallback(callback)
            except RuntimeError:
                pass
        self.callbacks = []
        super().closeEvent(event)


def _setup_data(systems):
    result = []
    for system in systems:
        controls = tc.controls(system)
        frames = tf.times(controls+tc._sources(system),frames=cmds.getAttr(system+".bakeFrames"))
        result.append({"name":system.rsplit("|",1)[-1],"nodes":tc._sources(system),
                       "system":cmds.getAttr(system+".systemID"),"position":cmds.getAttr(system+".positionSpace"),
                       "orientation":cmds.getAttr(system+".orientationSpace"),"relative":tc._first(system,"relativeSpace"),
                       "bake_frames":bool(cmds.getAttr(system+".bakeFrames")),"color":tc._color(system),
                       "aim_distance":cmds.getAttr(system+".aimDistance"),
                       "extras":[{"base":controls.index(tc._first(c,"baseControl")),"parent":bool(cmds.getAttr(c+".extraIsParent"))}
                                 for c in controls if cmds.objExists(c+".removableExtra")],
                       "appearance":[{"shape":cmds.getAttr(c+".shapeIndex"),"size":cmds.getAttr(c+".shapeSize")[0],
                                      "orientation":cmds.getAttr(c+".shapeOrientation")} for c in controls],
                       "samples":tf.capture(controls,frames),
                       "grab":[{"frame":cmds.getAttr(e+".frame"),"driver":tc._first(e,"driver")} for e in tc._grab_events(system)]})
    return {"format":"animkey.temp-controls","version":1,"temp_controls":result}


def export_setup(systems=None,path=None):
    systems = systems or tc.selected_systems()
    if not systems:
        raise ValueError("Select at least one system to export.")
    if path is None:
        path,_ = QtWidgets.QFileDialog.getSaveFileName(None,"Export Temp Controls","","ANIMKEY Temp Controls (*.aktc.json)")
    if not path:
        return
    from AnimKey.mods.storage import atomic_write_json
    atomic_write_json(path,_setup_data(systems))
    cmds.optionVar(stringValue=("AnimKeyTempControlsQuickFile",path))
    return path


def import_setup(path=None):
    if path is None:
        path,_ = QtWidgets.QFileDialog.getOpenFileName(None,"Import Temp Controls","","ANIMKEY Temp Controls (*.aktc.json)")
    if not path:
        return
    with open(path,"r",encoding="utf-8") as stream:
        data = json.load(stream)
    if data.get("format") != "animkey.temp-controls" or data.get("version") != 1:
        raise ValueError("This is not an ANIMKEY Temp Controls setup version 1.")
    created = []
    # Validate every source before creating anything.
    for record in data["temp_controls"]:
        if not all(cmds.objExists(n) for n in record["nodes"]):
            raise ValueError("Setup sources are missing. Resolve the rig namespace before import.")
    with tf.undo_chunk("AnimKey Import Temp Controls"):
        try:
            for record in data["temp_controls"]:
                systems = tc.create(node=record["nodes"],name=record["name"],system=record["system"],
                                    positionSpace=record["position"],orientationSpace=record["orientation"],
                                    relative=record.get("relative"),color=record["color"],bake_frames=record["bake_frames"],
                                    aim_distance=record["aim_distance"],restoreDefaultProperties=True)
                created.extend(systems)
                system = systems[0]
                for extra in record.get("extras",[]):
                    cmds.select(tc.controls(system)[extra["base"]],replace=True)
                    tc.add_extra(parent=extra["parent"])
                for event in record.get("grab",[]):
                    if event["frame"] > -1000000:
                        tc.grab(system,driver=event["driver"],frame=event["frame"])
                for control,props in zip(tc.controls(system),record["appearance"]):
                    shapes.apply(control,**props)
                tc._apply_control_samples(tc.controls(system),record["samples"])
        except Exception:
            for system in created:
                if cmds.objExists(system):
                    tc.clear([system],revert=True)
            raise
    cmds.optionVar(stringValue=("AnimKeyTempControlsQuickFile",path))
    return created


def _quick_path():
    if not cmds.optionVar(exists="AnimKeyTempControlsQuickFile"):
        raise ValueError("Import or export a setup file first.")
    return cmds.optionVar(query="AnimKeyTempControlsQuickFile")


def show_menu(button=None,pos=None):
    """Compatibility hook: right click opens the complete settings window."""
    return tc.show(button=button,panel=bool(tc.selected_systems()))
