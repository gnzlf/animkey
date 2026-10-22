"""Creation popup, persistent systems panel and menus for Temp Controls."""
import json
import os
import maya.cmds as cmds
import maya.api.OpenMaya as om

from AnimKey.mods.maya_compat import QtCore, QtGui, QtWidgets, execute_qt, get_maya_main_window
from AnimKey.mods.uiMod import ContextPopupWindow
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
    def __init__(self,anchor_button=None,panel=False):
        super().__init__(anchor_button=anchor_button,parent=get_maya_main_window())
        self.setObjectName("AnimKey_TempControls")
        self.setWindowTitle("Temp Controls Panel" if panel else "Create Temp Control")
        self.resize(410,620)
        self.callbacks = []
        self.refreshing = False
        self.saved_selection = [tf.NodeRef(n) for n in tf.transforms()]
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12,16,12,20)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("TEMP CONTROLS")
        title.setStyleSheet("font-weight: bold; color: #5bc0be; font-size: 14px;")
        header.addWidget(title)
        header.addStretch()
        close = QtWidgets.QPushButton("×")
        close.setFixedSize(24,24)
        close.clicked.connect(self.close)
        header.addWidget(close)
        outer.addLayout(header)
        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs)
        self._creation_tab()
        self._panel_tab()
        self.tabs.setCurrentIndex(1 if panel else 0)
        self.status = QtWidgets.QLabel("Select rig controls in the order you want to control them.")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.callbacks.append(om.MEventMessage.addEventCallback("SelectionChanged",self._selection_changed))
        for event in ("Undo","Redo"):
            self.callbacks.append(om.MEventMessage.addEventCallback(event,self._defer_refresh))
        for event in (om.MSceneMessage.kBeforeNew,om.MSceneMessage.kBeforeOpen):
            self.callbacks.append(om.MSceneMessage.addCallback(event,lambda *_: self.close()))
        self.refresh()

    def _combo(self,items):
        widget = QtWidgets.QComboBox()
        widget.addItems(items)
        return widget

    def _button(self,title,callback,layout):
        button = QtWidgets.QPushButton(title)
        button.clicked.connect(lambda *_: self._action(callback))
        layout.addWidget(button)
        return button

    def _action(self,callback):
        result = _run(callback)
        self.refresh()
        return result

    def _creation_tab(self):
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab,"Create")
        layout = QtWidgets.QVBoxLayout(tab)
        form = QtWidgets.QFormLayout()
        self.label = QtWidgets.QLineEdit()
        self.label.setPlaceholderText("Optional Label")
        self.family = self._combo(tc.SYSTEMS)
        self.position = self._combo(tc.SPACES)
        self.orientation = self._combo(tc.SPACES)
        self.lock_space = QtWidgets.QCheckBox("Link position and orientation")
        self.lock_space.setChecked(True)
        self.orientation.setEnabled(False)
        self.bake = self._combo(("Bake Keys","Bake Frames"))
        if cmds.optionVar(exists="AnimKeyTempBakeFrames"):
            self.bake.setCurrentIndex(int(bool(cmds.optionVar(query="AnimKeyTempBakeFrames"))))
        self.bake.setToolTip("Keys: sample existing key times. Frames: sample every frame, including dependency animation.")
        self.defaults = QtWidgets.QCheckBox("Reset Properties")
        self.defaults.setToolTip("Recompute shape, size and orientation for this system instead of recalling saved appearance.")
        self.distance = QtWidgets.QDoubleSpinBox()
        self.distance.setRange(0,100000)
        self.distance.setSpecialValueText("Automatic")
        self.distance.setEnabled(False)
        form.addRow("Label",self.label)
        form.addRow("System",self.family)
        form.addRow("Position",self.position)
        form.addRow("Orientation",self.orientation)
        form.addRow("",self.lock_space)
        form.addRow("Baking",self.bake)
        form.addRow("Aim Distance",self.distance)
        form.addRow("",self.defaults)
        layout.addLayout(form)
        self.help = QtWidgets.QLabel()
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        self._button("Create Temp Control",self._create,layout).setMinimumHeight(34)
        layout.addStretch()
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
        self.distance.setEnabled(family == 2)
        descriptions = ("One independent control per source.",
                        "A master and a subcontrol per source. Master scale changes spacing.",
                        "Base, Aim and Pole Vector controls, with IK and an animatable Twist channel.",
                        "An ordered FK chain with World, Parent and Local orientation modes.")
        self.help.setText(descriptions[family]+"\nRelative / Grab Release: the last selected object supplies the space. Object: follows the rig parent. Child: follows the original animation.")

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
                           bake_frames=self.bake.currentIndex() == 1,aim_distance=self.distance.value() or None,
                           restoreDefaultProperties=self.defaults.isChecked())
        self.tabs.setCurrentIndex(1)
        self.status.setText("Created {} system(s). Select a control to edit its properties.".format(len(result)))
        return result

    def _panel_tab(self):
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab,"Systems")
        layout = QtWidgets.QVBoxLayout(tab)
        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(110)
        self.list.itemDoubleClicked.connect(self._select_controls)
        layout.addWidget(self.list)
        self.list.itemSelectionChanged.connect(self._system_selected)
        actions = QtWidgets.QGridLayout()
        for index,(title,callback) in enumerate((("Toggle + Bake",lambda:self._toggle(False)),("Toggle + Revert",lambda:self._toggle(True)),
                                                ("Clear + Bake",lambda:self._clear(False)),("Clear + Revert",lambda:self._clear(True)))):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(lambda *_,cb=callback:self._action(cb))
            actions.addWidget(button,index//2,index%2)
        layout.addLayout(actions)
        form = QtWidgets.QFormLayout()
        self.panel_position = self._combo(tc.SPACES)
        self.panel_orientation = self._combo(tc.SPACES)
        self.chain_orientation = self._combo(("World","Parent","Local"))
        form.addRow("Position",self.panel_position)
        form.addRow("Orientation",self.panel_orientation)
        form.addRow("FK Orientation",self.chain_orientation)
        self.chain_orientation.currentIndexChanged.connect(self._chain_orientation)
        layout.addLayout(form)
        self._button("Apply Spaces (preserve keyed poses)",self._change_spaces,layout)
        appearance = QtWidgets.QHBoxLayout()
        for title,callback in (("Size −",lambda:tc.appearance(size_percent=-10)),("Size +",lambda:tc.appearance(size_percent=10)),
                               ("Shape",lambda:tc.appearance(shape_step=1)),("Orient",lambda:tc.appearance(orientation_step=1)),("Color",self._color)):
            self._button(title,callback,appearance)
        layout.addLayout(appearance)
        extras = QtWidgets.QHBoxLayout()
        self._button("Add Parent",lambda:tc.add_extra(parent=True),extras)
        self._button("Add Child",tc.add_extra,extras)
        self._button("Remove Extra",tc.remove_extra,extras)
        layout.addLayout(extras)
        pivot = QtWidgets.QHBoxLayout()
        self._button("Edit Pivot / Finish",tc.edit_pivot,pivot)
        self._button("Reset Pivot",tc.reset_pivot,pivot)
        layout.addLayout(pivot)
        membership = QtWidgets.QHBoxLayout()
        for label,action in (("Add Member","add"),("Remove Member","remove"),("↑","up"),("↓","down"),("Reverse","reverse")):
            self._button(label,lambda a=action:tc.edit_members(a),membership)
        layout.addLayout(membership)
        grab = QtWidgets.QHBoxLayout()
        self._button("Add Grab",lambda:self._grab("grab"),grab)
        self._button("Release",lambda:self._grab("release"),grab)
        self._button("Remove",lambda:self._grab("remove"),grab)
        self._button("Normalize",lambda:self._grab("normalize"),grab)
        layout.addLayout(grab)
        self.transitions = QtWidgets.QLabel("Grab Release: no transitions")
        self.transitions.setWordWrap(True)
        layout.addWidget(self.transitions)
        files = QtWidgets.QHBoxLayout()
        self._button("Import Setup",lambda:import_setup(),files)
        self._button("Export Setup",lambda:export_setup(self._systems()),files)
        self._button("Refresh",self.refresh,files)
        layout.addLayout(files)

    def _systems(self):
        return [item.data(QtCore.Qt.UserRole) for item in self.list.selectedItems()] or tc.selected_systems()

    def _toggle(self,revert):
        tc.toggle(self._systems(),revert=revert)

    def _clear(self,revert):
        tc.clear(self._systems(),revert=revert)

    def _select_controls(self,item):
        cmds.select(tc.controls(item.data(QtCore.Qt.UserRole)),replace=True)

    def _color(self):
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor("#33d9bf"),self,"Temp Control Color")
        if color.isValid():
            tc.appearance(color=(color.redF(),color.greenF(),color.blueF()))

    def _change_spaces(self):
        selected = tf.transforms()
        systems = self._systems()
        mode = self.panel_position.currentIndex()
        orientation = self.panel_orientation.currentIndex()
        relative = selected[-1] if selected and (2 in (mode,orientation)) else None
        tc.change_space(systems,mode,orientation,relative=relative)

    def _chain_orientation(self,index):
        if self.refreshing:
            return
        with tf.undo_chunk("AnimKey FK Orientation"):
            for system in self._systems():
                if cmds.getAttr(system+".systemID") == 3:
                    nodes = tc.controls(system)
                    samples = tf.capture(nodes,tf.times(nodes,frames=cmds.getAttr(system+".bakeFrames")))
                    cmds.setAttr(system+".chainOrientation",index)
                    tc._apply_control_samples(nodes,samples)

    def _grab(self,action):
        selected = tf.transforms()
        systems = self._systems()
        driver = selected[-1] if selected and action == "grab" else None
        if action == "grab" and not driver:
            raise ValueError("Select a system in the panel and an external driver in Maya.")
        for system in systems:
            tc.grab(system,driver=driver,remove=action == "remove",normalise=action == "normalize")

    def _selection_changed(self,*_):
        if self.refreshing:
            return
        nodes = tf.transforms()
        if nodes and not tc.selected_systems(nodes):
            self.saved_selection = [tf.NodeRef(n) for n in nodes]
        self._defer_refresh()

    def _defer_refresh(self,*_):
        QtCore.QTimer.singleShot(0,self.refresh)

    def _system_selected(self):
        systems = self._systems()
        if not systems or self.refreshing:
            return
        system = systems[0]
        self.refreshing = True
        try:
            family = cmds.getAttr(system+".systemID")
            excluded = (1,4) if family == 1 else (4,) if family == 3 else ()
            for combo,attr in ((self.panel_position,"positionSpace"),(self.panel_orientation,"orientationSpace")):
                for i in range(6):
                    combo.model().item(i).setEnabled(i not in excluded)
                combo.setCurrentIndex(cmds.getAttr(system+"."+attr))
            self.chain_orientation.setEnabled(family == 3)
            self.chain_orientation.setCurrentIndex(cmds.getAttr(system+".chainOrientation"))
            self.transitions.setText("Grab Release: " + (" · ".join("{}: {}".format(cmds.getAttr(e+".frame"),tc._first(e,"driver") or "World")
                                                         for e in tc._grab_events(system) if cmds.getAttr(e+".frame") > -1000000) or "no transitions"))
        finally:
            self.refreshing = False

    def refresh(self,*_):
        if self.refreshing or not self.isVisible() and not self.list:
            return
        prior = set(self._systems())
        self.refreshing = True
        try:
            self.list.clear()
            for system in tc.all_systems():
                active = cmds.getAttr(system+".isEnabled")
                title = "{}  {} · {}".format("●" if active else "○",system.rsplit("|",1)[-1],tc.SYSTEMS[cmds.getAttr(system+".systemID")])
                item = QtWidgets.QListWidgetItem(title)
                item.setData(QtCore.Qt.UserRole,system)
                item.setForeground(QtGui.QColor("#5bc0be" if active else "#999999"))
                self.list.addItem(item)
                item.setSelected(system in prior)
        finally:
            self.refreshing = False
        self._system_selected()

    def closeEvent(self,event):
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
    menu = QtWidgets.QMenu(button)
    def action(label,callback):
        menu.addAction(label,lambda:_run(callback))
    bake = menu.addMenu("Baking Mode")
    for label,value in (("Bake Keys",0),("Bake Frames",1)):
        item = bake.addAction(label)
        item.setCheckable(True)
        preference = cmds.optionVar(query="AnimKeyTempBakeFrames") if cmds.optionVar(exists="AnimKeyTempBakeFrames") else 0
        item.setChecked(preference == value)
        item.triggered.connect(lambda *_,v=value:cmds.optionVar(intValue=("AnimKeyTempBakeFrames",v)))
    menu.addSeparator()
    for index,name in enumerate(tc.SPACES):
        action(name+" Space",lambda i=index:tc.create(space=i,bake_frames=bool(cmds.optionVar(query="AnimKeyTempBakeFrames")) if cmds.optionVar(exists="AnimKeyTempBakeFrames") else False))
    menu.addSeparator()
    action("Quick Import",lambda:import_setup(_quick_path()))
    action("Quick Export",lambda:export_setup(path=_quick_path()))
    action("Import Setup…",import_setup)
    action("Export Setup…",export_setup)
    menu.addSeparator()
    action("Toggle and Revert",lambda:tc.toggle(revert=True))
    action("Toggle and Bake",lambda:tc.toggle(revert=False))
    action("Clear and Revert",lambda:tc.clear(revert=True))
    action("Clear and Bake",lambda:tc.clear(revert=False))
    menu.addSeparator()
    anim = menu.addMenu("Anim Controls")
    for label,callback in (("Increase Size",lambda:tc.appearance(size_percent=10)),("Decrease Size",lambda:tc.appearance(size_percent=-10)),
                           ("Next Shape",lambda:tc.appearance(shape_step=1)),("Next Orientation",lambda:tc.appearance(orientation_step=1)),
                           ("Add Child Control",tc.add_extra),("Add Parent Control",lambda:tc.add_extra(parent=True)),("Remove Control",tc.remove_extra)):
        anim.addAction(label,lambda *_,cb=callback:_run(cb))
    anim.addAction("Edit Pivot / Finish",lambda:_run(tc.edit_pivot))
    anim.addAction("Reset Pivot",lambda:_run(tc.reset_pivot))
    gr = menu.addMenu("Grab Release")
    for label,kwargs in (("Add Grab",{}),("Add Release",{"driver":None}),("Remove Grab",{"remove":True}),
                         ("Normalize Transitions",{"normalise":True}),("Normalize Y Up",{"normalise":True,"y_up":True})):
        def apply_gr(options=kwargs,title=label):
            systems = tc.selected_systems()
            options = dict(options)
            if title == "Add Grab":
                selected = tf.transforms()
                options["driver"] = selected[-1] if selected else None
            for system in systems:
                tc.grab(system,**options)
        gr.addAction(label,lambda *_,cb=apply_gr:_run(cb))
    action("Temp Controls Panel",lambda:tc.show(button=button,panel=True))
    if cmds.ls("*.tempTag",objectsOnly=True):
        action("Recover Legacy Temp Controls…",lambda:__import__("AnimKey.buttons.tempControlsLegacy",fromlist=["show"]).show(anchor_button=button))
    execute_qt(menu,button.mapToGlobal(pos) if button is not None and pos is not None else QtGui.QCursor.pos())
