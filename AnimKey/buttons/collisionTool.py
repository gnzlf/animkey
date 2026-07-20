# -*- coding: utf-8 -*-
"""
    AnimKey - Collision Tool
    
    Sistema de colision de esferas para rigs FK en Maya.
    Rotacion jerarquica: ultimo control rota mas, primero rota menos.
    Single control: usa traslacion en vez de rotacion.
    
    Datos se almacenan en la escena usando DAG containers.
    
    Modern frameless UI matching Retimer style.
"""

from __future__ import division, print_function, absolute_import

import math
import os
import json

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance

import maya.cmds as cmds
from AnimKey.mods.uiMod import ContextPopupWindow
import maya.api.OpenMaya as om2
import maya.OpenMayaUI as omui

TOOL_NAME = "Collision Tool"
SPHERE_PREFIX = "col_sphere_"
GRP_PREFIX = "col_grp_"
WINDOW_OBJECT = "AnimKey_CollisionTool"
COLLISION_CONTAINER_NAME = "animkey_collisions"
COLLISION_DATA_ATTR = "collisionData"

# Global window reference
_ui = None

def get_maya_main_window():
    return wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget)

def _get_icon_path(icon_name=None):
    if icon_name is None:
        icon_name = "animkey_outliner_minimal_32.png"
    current_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(current_dir, "..", "data", "icons", icon_name)
    normalized_path = os.path.normpath(path)
    if os.path.exists(normalized_path):
        return normalized_path
    return ""

# =============================================================================
# DAG CONTAINER MANAGEMENT
# =============================================================================

def _create_animkey_container():
    if not cmds.objExists("AnimKey"):
        container = cmds.container(type='dagContainer', name="AnimKey")
        icon_path = _get_icon_path()
        if icon_path:
            try:
                cmds.setAttr(container + '.iconName', icon_path, type='string')
            except: pass
        for attr in ["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ", "visibility"]:
            try: cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
            except: pass

def _create_collision_container():
    _create_animkey_container()
    icon_path = _get_icon_path()
    if not cmds.objExists(COLLISION_CONTAINER_NAME):
        container = cmds.container(type='dagContainer', name=COLLISION_CONTAINER_NAME)
        if icon_path:
            try: cmds.setAttr(container + '.iconName', icon_path, type='string')
            except: pass
        if cmds.objExists("AnimKey"):
            cmds.parent(container, "AnimKey")
        for attr in ["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ", "visibility"]:
            try: cmds.setAttr(container + "." + attr, lock=True, keyable=False, channelBox=False)
            except: pass
        if not cmds.attributeQuery(COLLISION_DATA_ATTR, node=container, exists=True):
            cmds.addAttr(container, longName=COLLISION_DATA_ATTR, dataType="string")
            cmds.setAttr(f"{container}.{COLLISION_DATA_ATTR}", "{}", type="string")
    else:
        if icon_path:
            try: cmds.setAttr(COLLISION_CONTAINER_NAME + '.iconName', icon_path, type='string')
            except: pass
        if not cmds.attributeQuery(COLLISION_DATA_ATTR, node=COLLISION_CONTAINER_NAME, exists=True):
            cmds.addAttr(COLLISION_CONTAINER_NAME, longName=COLLISION_DATA_ATTR, dataType="string")
            cmds.setAttr(f"{COLLISION_CONTAINER_NAME}.{COLLISION_DATA_ATTR}", "{}", type="string")
    return COLLISION_CONTAINER_NAME

def _save_to_scene(data):
    container = _create_collision_container()
    try:
        json_str = json.dumps(data)
        cmds.setAttr(f"{container}.{COLLISION_DATA_ATTR}", json_str, type="string")
    except Exception as e:
        cmds.warning(f"CollisionTool: Failed to save to scene: {e}")

def _load_from_scene():
    if not cmds.objExists(COLLISION_CONTAINER_NAME): return None
    if not cmds.attributeQuery(COLLISION_DATA_ATTR, node=COLLISION_CONTAINER_NAME, exists=True): return None
    try:
        json_str = cmds.getAttr(f"{COLLISION_CONTAINER_NAME}.{COLLISION_DATA_ATTR}")
        if json_str: return json.loads(json_str)
    except Exception as e:
        cmds.warning(f"CollisionTool: Failed to load from scene: {e}")
    return None

# =============================================================================
# COLLISION LOGIC
# =============================================================================

def get_mesh_fn(mesh_name):
    sel = om2.MSelectionList()
    sel.add(mesh_name)
    dag_path = sel.getDagPath(0)
    if dag_path.node().hasFn(om2.MFn.kTransform):
        dag_path.extendToShape()
    return om2.MFnMesh(dag_path)

def get_world_position(node):
    pos = cmds.xform(node, q=True, ws=True, t=True)
    return om2.MPoint(pos[0], pos[1], pos[2])

def check_collision(sphere_pos, radius, mesh_name):
    try:
        mesh_fn = get_mesh_fn(mesh_name)
        result = mesh_fn.getClosestPointAndNormal(sphere_pos, om2.MSpace.kWorld)
        closest, normal = result[0], result[1]
        to_sphere = om2.MVector(sphere_pos - closest)
        dist = to_sphere.length()
        if dist < radius:
            push = normal * (radius - dist)
            return True, push
        return False, om2.MVector(0, 0, 0)
    except: return False, om2.MVector(0, 0, 0)

# =============================================================================
# COLLISION CHAIN
# =============================================================================

class CollisionChain:
    def __init__(self, name, controls, radius=1.0, manager=None):
        self.name = name
        self.controls = controls
        self.radius = radius
        self.sphere = None
        self.offset_grp = None
        self.jobs = []
        self.manager = manager
        self.use_x = True
        self.use_y = True
        self.use_z = True
    
    def get_meshes(self):
        return self.manager.meshes if self.manager else []
    
    def create(self):
        tip = self.controls[-1]
        sphere_name = SPHERE_PREFIX + self.name
        grp_name = GRP_PREFIX + self.name
        for n in [sphere_name, grp_name]:
            if cmds.objExists(n): cmds.delete(n)
        self.offset_grp = cmds.group(empty=True, name=grp_name)
        cmds.matchTransform(self.offset_grp, tip)
        cmds.parentConstraint(tip, self.offset_grp, mo=True)
        self.sphere = cmds.polySphere(name=sphere_name, radius=self.radius, subdivisionsX=12, subdivisionsY=8, ch=False)[0]
        cmds.parent(self.sphere, self.offset_grp)
        for attr in [".translate", ".rotate"]: cmds.setAttr(self.sphere + attr, 0, 0, 0)
        cmds.setAttr(self.sphere + ".scale", 1, 1, 1)
        cmds.setAttr(self.sphere + ".overrideEnabled", 1)
        cmds.setAttr(self.sphere + ".overrideColor", 18)
        _create_collision_container()
        if cmds.objExists(COLLISION_CONTAINER_NAME):
            try: cmds.parent(self.offset_grp, COLLISION_CONTAINER_NAME)
            except: pass
        return self.sphere

    def reconnect(self):
        sphere_name = SPHERE_PREFIX + self.name
        grp_name = GRP_PREFIX + self.name
        if cmds.objExists(sphere_name) and cmds.objExists(grp_name):
            self.sphere, self.offset_grp = sphere_name, grp_name
            return True
        return False

    def rename(self, new_name):
        new_sphere, new_grp = SPHERE_PREFIX + new_name, GRP_PREFIX + new_name
        if self.offset_grp and cmds.objExists(self.offset_grp):
            self.offset_grp = cmds.rename(self.offset_grp, new_grp)
        if self.sphere and cmds.objExists(self.sphere):
            self.sphere = cmds.rename(self.sphere, new_sphere)
        self.name = new_name

    def set_editable(self, editable):
        if not self.sphere or not cmds.objExists(self.sphere): return
        for attr in ['tx', 'ty', 'tz', 'sx', 'sy', 'sz']:
            try: cmds.setAttr("{}.{}".format(self.sphere, attr), lock=not editable)
            except: pass
        cmds.setAttr(self.sphere + ".overrideColor", 18 if editable else 15)

    def set_rotation_axes(self, x, y, z):
        self.use_x, self.use_y, self.use_z = x, y, z

    def get_radius(self):
        if self.sphere and cmds.objExists(self.sphere):
            return abs(cmds.getAttr(self.sphere + ".scaleX")) * self.radius
        return self.radius

    def start(self):
        self.stop()
        self.jobs = [
            cmds.scriptJob(event=["timeChanged", self._update], killWithScene=True),
            cmds.scriptJob(event=["idle", self._update], killWithScene=True)
        ]

    def stop(self):
        for j in self.jobs:
            try:
                if cmds.scriptJob(exists=j): cmds.scriptJob(kill=j, force=True)
            except: pass
        self.jobs = []

    def _update(self):
        if not self.sphere or not cmds.objExists(self.sphere): return
        current_meshes = self.get_meshes()
        if not current_meshes: return
        pos, rad = get_world_position(self.sphere), self.get_radius()
        total_push, hit = om2.MVector(0, 0, 0), False
        for m in current_meshes:
            if cmds.objExists(m):
                collide, pv = check_collision(pos, rad, m)
                if collide: hit, total_push = True, total_push + pv
        if hit and total_push.length() > 0.001:
            if len(self.controls) == 1: self._correct_translation(total_push)
            else: self._correct_hierarchical(total_push)

    def _correct_translation(self, push):
        ctrl = self.controls[0]
        if not cmds.objExists(ctrl): return
        dx, dy, dz = (push.x if self.use_x else 0), (push.y if self.use_y else 0), (push.z if self.use_z else 0)
        current_pos = cmds.xform(ctrl, q=True, ws=True, t=True)
        damping = 0.5
        new_pos = [current_pos[0] + dx * damping, current_pos[1] + dy * damping, current_pos[2] + dz * damping]
        cmds.xform(ctrl, ws=True, t=new_pos)

    def _correct_hierarchical(self, push):
        sphere_pos = get_world_position(self.sphere)
        target_pos = om2.MPoint(sphere_pos.x + push.x, sphere_pos.y + push.y, sphere_pos.z + push.z)
        num_controls = len(self.controls)
        if num_controls == 0: return
        for iteration in range(4):
            if om2.MVector(target_pos - get_world_position(self.sphere)).length() < 0.005: break
            for i, ctrl in enumerate(reversed(self.controls)):
                if not cmds.objExists(ctrl): continue
                ctrl_pos, sphere_pos_now = get_world_position(ctrl), get_world_position(self.sphere)
                to_sphere = om2.MVector(sphere_pos_now - ctrl_pos)
                if to_sphere.length() < 0.001: continue
                to_target = om2.MVector(target_pos - ctrl_pos)
                to_sphere_n, to_target_n = to_sphere.normalize(), to_target.normalize()
                axis = to_sphere_n ^ to_target_n
                if axis.length() < 0.0001: continue
                axis = axis.normalize()
                dot = max(-1.0, min(1.0, to_sphere_n * to_target_n))
                angle = math.acos(dot)
                if angle < 0.0005: continue
                # Weights: last (tip) rotates more, first (root) less
                if num_controls == 1: weight = 1.0
                elif i == 0: weight = 0.4 # Tip
                elif i == 1: weight = 0.3 # Middle
                elif i == 2: weight = 0.3 # Root
                else: weight = 0.1
                angle *= weight * 0.5
                rot = cmds.xform(ctrl, q=True, ws=True, ro=True)
                q = om2.MQuaternion(angle, axis)
                e = q.asEulerRotation()
                dx, dy, dz = (math.degrees(e.x) if self.use_x else 0), (math.degrees(e.y) if self.use_y else 0), (math.degrees(e.z) if self.use_z else 0)
                cmds.xform(ctrl, ws=True, ro=[rot[0] + dx, rot[1] + dy, rot[2] + dz])

    def cleanup(self):
        self.stop()
        if self.offset_grp and cmds.objExists(self.offset_grp): cmds.delete(self.offset_grp)
        self.sphere = self.offset_grp = None

    def to_dict(self):
        return {"name": self.name, "controls": self.controls, "radius": self.radius, "use_x": self.use_x, "use_y": self.use_y, "use_z": self.use_z}

    @classmethod
    def from_dict(cls, data, manager=None):
        chain = cls(data["name"], data["controls"], data.get("radius", 1.0), manager)
        chain.use_x, chain.use_y, chain.use_z = data.get("use_x", True), data.get("use_y", True), data.get("use_z", True)
        return chain

# =============================================================================
# MANAGER
# =============================================================================

class Manager:
    _inst = None
    def __init__(self):
        self.chains, self.meshes = [], []
        self.use_x, self.use_y, self.use_z = True, True, True
    @classmethod
    def get(cls):
        if not cls._inst:
            cls._inst = cls()
            cls._inst.load_data()
        return cls._inst
    @classmethod
    def reset(cls):
        if cls._inst: cls._inst.stop_all()
        cls._inst = None
    def save_data(self):
        data = {"meshes": self.meshes, "chains": [c.to_dict() for c in self.chains], "use_x": self.use_x, "use_y": self.use_y, "use_z": self.use_z}
        _save_to_scene(data)
    def load_data(self):
        data = _load_from_scene()
        if not data: return
        try:
            self.use_x, self.use_y, self.use_z = data.get("use_x", True), data.get("use_y", True), data.get("use_z", True)
            self.meshes = [m for m in data.get("meshes", []) if cmds.objExists(m)]
            for chain_data in data.get("chains", []):
                name, controls = chain_data.get("name", ""), chain_data.get("controls", [])
                if self.get_chain(name): continue
                if all(cmds.objExists(c) for c in controls):
                    chain = CollisionChain.from_dict(chain_data, self)
                    if chain.reconnect():
                        chain.start()
                        self.chains.append(chain)
        except Exception as e: cmds.warning(f"CollisionTool: Load data failed: {e}")
    def add_chain(self, chain):
        chain.manager = self
        chain.set_rotation_axes(self.use_x, self.use_y, self.use_z)
        self.chains.append(chain)
        self.save_data()
    def get_chain(self, name):
        for c in self.chains:
            if c.name == name: return c
        return None
    def add_mesh(self, mesh):
        if mesh not in self.meshes: self.meshes.append(mesh); self.save_data()
    def remove_mesh(self, mesh):
        if mesh in self.meshes: self.meshes.remove(mesh); self.save_data()
    def remove_chain(self, name):
        for c in self.chains[:]:
            if c.name == name: c.cleanup(); self.chains.remove(c); self.save_data(); return
    def set_axes(self, x, y, z):
        self.use_x, self.use_y, self.use_z = x, y, z
        for c in self.chains: c.set_rotation_axes(x, y, z)
        self.save_data()
    def stop_all(self):
        for c in self.chains: c.stop()
    def set_all_editable(self, editable):
        for c in self.chains: c.set_editable(editable)
    def clear_invalid(self):
        to_remove = [c.name for c in self.chains if not cmds.objExists(SPHERE_PREFIX + c.name)]
        for name in to_remove: self.remove_chain(name)
        self.meshes = [m for m in self.meshes if cmds.objExists(m)]
        self.save_data()

# =============================================================================
# MODERN UI COMPONENTS
# =============================================================================

class CollisionWindow(ContextPopupWindow):
    def __init__(self, anchor_button=None, parent=None):
        super().__init__(anchor_button=anchor_button, parent=parent)
        self.setObjectName(WINDOW_OBJECT)
        self.setFixedSize(360, 520)
        
        
        self._base_opacity, self._hover_opacity = 0.5, 1.0
        Manager.reset(); self.mgr = Manager.get()
        self._setup_ui()
        self.position_window()
        self.mgr.clear_invalid(); self._refresh()
        self.setWindowOpacity(self._base_opacity)

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, self._tail_height + 1)
        main_layout.setSpacing(0)
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet("""
            QFrame#mainContainer {
                background-color: transparent;
                border: none;
            }
        """)
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(10, 10, 10, 10)
        self.title_label = QtWidgets.QLabel("COLLISION TOOL PRO")
        self.title_label.setStyleSheet("color: #AAA; font-size: 11px; font-weight: 500; border: none;")
        header.addWidget(self.title_label)
        header.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        container_layout.addLayout(header)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(15, 15, 15, 15)
        content_layout.setSpacing(12)
        
        # --- SECTION: CREATE ---
        create_label = QtWidgets.QLabel("CREATE COLLISION")
        create_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 1px;")
        content_layout.addWidget(create_label)
        
        field_row = QtWidgets.QHBoxLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("Name (e.g. hand_L)")
        self.name_edit.setStyleSheet("QLineEdit { background-color: #444444; color: #FFF; border: 1px solid #5a5a5a; border-radius: 6px; padding: 6px; font-size: 11px; } QLineEdit:focus { border-color: #3498DB; }")
        field_row.addWidget(self.name_edit)
        
        self.radius_spin = QtWidgets.QDoubleSpinBox()
        self.radius_spin.setRange(0.1, 50.0); self.radius_spin.setValue(1.0); self.radius_spin.setPrefix("R: ")
        self.radius_spin.setStyleSheet("QDoubleSpinBox { background-color: #444444; color: #FFF; border: 1px solid #5a5a5a; border-radius: 6px; padding: 5px; font-size: 11px; }")
        field_row.addWidget(self.radius_spin)
        content_layout.addLayout(field_row)
        
        self.create_btn = QtWidgets.QPushButton("CREATE SPHERE")
        self.create_btn.setFixedHeight(38)
        self.create_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.create_btn.setStyleSheet("QPushButton { background-color: #2d5d3d; color: #a3be8c; border: 1px solid #5aaa5a; border-radius: 7px; font-weight: bold; } QPushButton:hover { background-color: #3d6d4d; }")
        self.create_btn.clicked.connect(self._create)
        content_layout.addWidget(self.create_btn)
        
        # --- SECTION: CHAINS ---
        chains_label = QtWidgets.QLabel("FK CHAINS")
        chains_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 1px; margin-top: 5px;")
        content_layout.addWidget(chains_label)
        self.chains_list = QtWidgets.QListWidget()
        self.chains_list.setFixedHeight(100)
        self.chains_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.chains_list.setStyleSheet("QListWidget { background-color: #3a3a3a; color: #BBB; border: 1px solid #5a5a5a; border-radius: 6px; font-size: 11px; } QListWidget::item:selected { background-color: #3d4d6d; color: #FFF; }")
        self.chains_list.customContextMenuRequested.connect(self._show_context_menu)
        self.chains_list.itemDoubleClicked.connect(self._select_sphere)
        content_layout.addWidget(self.chains_list)
        
        # --- SECTION: MESHES ---
        meshes_label = QtWidgets.QLabel("COLLISION MESHES")
        meshes_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 1px; margin-top: 5px;")
        content_layout.addWidget(meshes_label)
        self.mesh_list = QtWidgets.QListWidget()
        self.mesh_list.setFixedHeight(80)
        self.mesh_list.setStyleSheet("QListWidget { background-color: #3a3a3a; color: #BBB; border: 1px solid #5a5a5a; border-radius: 6px; font-size: 11px; }")
        content_layout.addWidget(self.mesh_list)
        
        mesh_ctrl_row = QtWidgets.QHBoxLayout()
        self.add_mesh_btn = QtWidgets.QPushButton("+ ADD")
        self.rem_mesh_btn = QtWidgets.QPushButton("- REM")
        btn_style = "QPushButton { background-color: #5a5a5a; color: #AAA; border: 1px solid #666666; border-radius: 6px; font-size: 10px; padding: 4px; } QPushButton:hover { background-color: #666666; color: #FFF; }"
        self.add_mesh_btn.setStyleSheet(btn_style); self.rem_mesh_btn.setStyleSheet(btn_style)
        self.add_mesh_btn.clicked.connect(self._add_mesh); self.rem_mesh_btn.clicked.connect(self._rem_mesh)
        mesh_ctrl_row.addWidget(self.add_mesh_btn); mesh_ctrl_row.addWidget(self.rem_mesh_btn)
        content_layout.addLayout(mesh_ctrl_row)
        
        # --- SECTION: AXES ---
        axes_label = QtWidgets.QLabel("AXIS RESTRICTIONS")
        axes_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 1px; margin-top: 5px;")
        content_layout.addWidget(axes_label)
        axes_row = QtWidgets.QHBoxLayout()
        self.check_x = QtWidgets.QCheckBox("X"); self.check_y = QtWidgets.QCheckBox("Y"); self.check_z = QtWidgets.QCheckBox("Z")
        check_style = "QCheckBox { color: #888; font-size: 11px; } QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid #666666; background: #4d4d4d; } QCheckBox::indicator:checked { background: #3498DB; border-color: #3498DB; }"
        for cb in [self.check_x, self.check_y, self.check_z]: 
            cb.setStyleSheet(check_style); cb.setChecked(True); cb.toggled.connect(self._update_axes); axes_row.addWidget(cb)
        content_layout.addLayout(axes_row)
        
        content_layout.addStretch()
        container_layout.addWidget(content)
        main_layout.addWidget(self.container)

    def _refresh(self):
        self.mesh_list.clear()
        for m in self.mgr.meshes: self.mesh_list.addItem(m)
        self.chains_list.clear()
        for c in self.mgr.chains: self.chains_list.addItem(c.name)
        self.check_x.blockSignals(True); self.check_x.setChecked(self.mgr.use_x); self.check_x.blockSignals(False)
        self.check_y.blockSignals(True); self.check_y.setChecked(self.mgr.use_y); self.check_y.blockSignals(False)
        self.check_z.blockSignals(True); self.check_z.setChecked(self.mgr.use_z); self.check_z.blockSignals(False)

    def _update_axes(self, *args):
        self.mgr.set_axes(self.check_x.isChecked(), self.check_y.isChecked(), self.check_z.isChecked())

    def _add_mesh(self):
        for obj in cmds.ls(sl=True):
            if cmds.listRelatives(obj, shapes=True, type="mesh"): self.mgr.add_mesh(obj)
        self._refresh()

    def _rem_mesh(self):
        item = self.mesh_list.currentItem()
        if item: self.mgr.remove_mesh(item.text()); self._refresh()

    def _create(self):
        sel = cmds.ls(sl=True, type="transform")
        if not sel: return cmds.warning("Select FK controls")
        name = self.name_edit.text().strip()
        if not name: return cmds.warning("Enter a name")
        if self.mgr.get_chain(name): return cmds.warning("Name already exists")
        chain = CollisionChain(name, sel, self.radius_spin.value())
        chain.create(); chain.set_editable(True); self.mgr.add_chain(chain); chain.start()
        self._refresh(); self.name_edit.clear(); cmds.select(chain.sphere)

    def _show_context_menu(self, pos):
        item = self.chains_list.itemAt(pos)
        if not item: return
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #4d4d4d; color: #AAA; border: 1px solid #5a5a5a; } QMenu::item:selected { background-color: #3498DB; color: #FFF; }")
        select_act = menu.addAction("Select Sphere")
        rename_act = menu.addAction("Rename")
        menu.addSeparator()
        delete_act = menu.addAction("Delete")
        action = menu.exec_(self.chains_list.mapToGlobal(pos))
        if action == select_act: self._select_sphere(item)
        elif action == rename_act: self._rename_chain(item)
        elif action == delete_act: self._delete_chain(item)

    def _select_sphere(self, item):
        c = self.mgr.get_chain(item.text())
        if c and c.sphere and cmds.objExists(c.sphere): cmds.select(c.sphere)

    def _rename_chain(self, item):
        old = item.text()
        new, ok = QtWidgets.QInputDialog.getText(self, "Rename", "New name:", text=old)
        if ok and new and new != old:
            if self.mgr.get_chain(new): return cmds.warning("Name already exists")
            c = self.mgr.get_chain(old)
            if c: c.rename(new); self.mgr.save_data(); self._refresh()

    def _delete_chain(self, item):
        name = item.text()
        if cmds.confirmDialog(title="Delete", message=f"Delete '{name}'?", b=["Yes","No"], db="No") == "Yes":
            self.mgr.remove_chain(name); self._refresh()

    def enterEvent(self, e): self._animate(self._hover_opacity)
    def leaveEvent(self, e): self._animate(self._base_opacity)
    def _animate(self, val):
        if hasattr(self, '_anim') and self._anim.state() == QtCore.QPropertyAnimation.Running: self._anim.stop()
        self._anim = QtCore.QPropertyAnimation(self, b"windowOpacity"); self._anim.setDuration(150); self._anim.setEndValue(val); self._anim.start()

    def closeEvent(self, event):
        global _ui; _ui = None; super(CollisionWindow, self).closeEvent(event)

def show(anchor_button=None):
    global _ui
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows(except_widget=_ui)
    if _ui:
        existing = uiMod.show_existing_animkey_tool_window(_ui, anchor_button)
        if existing is not None:
            _ui = existing
            return _ui
        _ui = None
    if cmds.window(WINDOW_OBJECT, exists=True): cmds.deleteUI(WINDOW_OBJECT)
    _ui = CollisionWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _ui.show(); _ui.raise_()
    return _ui

def execute(*args, **kwargs):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.collisionTool.execute"):
        return None
    return show(anchor_button=kwargs.get("button"))

if __name__ == "__main__": show()
