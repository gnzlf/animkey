"""
    AnimKey Micro Move (Stable v8)
    
    Uses a temporary driver network for smooth, real-time micro adjustments.
    The user moves a driver node, and only the movement delta is scaled.
    
    v8: Single-step undo for Micro Move edits
"""

import maya.cmds as cmds


# ═══════════════════════════════════════════════════════════════════════════════
#                           SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

_magnitude = 6
MAGNITUDE_PRESETS = [2, 4, 6, 8, 10, 15, 20]


def get_magnitude():
    global _magnitude
    return _magnitude


def set_magnitude(value):
    global _magnitude
    _magnitude = max(2, min(50, value))
    cmds.inViewMessage(
        amg=f"<span style='color:#ebcb8b'>Micro Move: {_magnitude}x</span>",
        pos='topCenter', fade=True, fadeStayTime=1000
    )


# ═══════════════════════════════════════════════════════════════════════════════
#                           STATE
# ═══════════════════════════════════════════════════════════════════════════════

class State:
    def __init__(self):
        self.enabled = False
        self.button = None
        self.originals = []
        self.drivers = []
        self.dividers = []
        self.final_values = {}  # Store final values before cleanup
        self.original_connections = {}
        self.keyed_attrs = set()
        self.mode = None
        self.chunk_open = False
        self.operation_id = 0
        self.undo_was_enabled = True
        self.temp_undo_disabled = False


_s = State()


def _plug_has_animation_source(src_plug):
    try:
        node = src_plug.split(".", 1)[0]
        node_type = cmds.nodeType(node)
        if node_type.startswith("anim") or node_type in ("blendWeighted", "pairBlend"):
            return True
        if node_type == "unitConversion":
            upstream = cmds.listConnections(node, source=True, destination=False) or []
            for upstream_node in upstream:
                upstream_type = cmds.nodeType(upstream_node)
                if upstream_type.startswith("anim") or upstream_type in ("blendWeighted", "pairBlend"):
                    return True
    except:
        pass
    return False


def _attr_has_keys(attr_path):
    try:
        return bool(cmds.keyframe(attr_path, query=True, timeChange=True) or [])
    except:
        return False


def _query_undo_enabled():
    try:
        return bool(cmds.undoInfo(query=True, state=True))
    except:
        return True


def _set_undo_enabled(enabled):
    try:
        cmds.undoInfo(stateWithoutFlush=bool(enabled))
    except:
        pass


def _begin_temp_undo_suppression():
    """Keep Micro Move helper nodes out of Maya's undo stack."""
    global _s
    if _s.temp_undo_disabled:
        return
    _s.undo_was_enabled = _query_undo_enabled()
    if _s.undo_was_enabled:
        _set_undo_enabled(False)
    _s.temp_undo_disabled = True


def _finish_temp_undo_suppression():
    global _s
    if not _s.temp_undo_disabled:
        return
    if _s.undo_was_enabled:
        _set_undo_enabled(True)
    _s.temp_undo_disabled = False


def _apply_final_values(current_time):
    """Apply the captured result as the single undoable Micro Move edit."""
    for obj, vals in _s.final_values.items():
        if not cmds.objExists(obj):
            continue
        for a, v in vals.items():
            attr_path = f"{obj}.{a}"
            try:
                if attr_path in _s.keyed_attrs:
                    cmds.setKeyframe(obj, attribute=a, time=current_time, value=v)
                elif cmds.getAttr(attr_path, settable=True):
                    cmds.setAttr(attr_path, v)
            except:
                pass


def _apply_final_values_as_single_undo(current_time):
    if not _s.final_values:
        return

    if not _s.undo_was_enabled:
        _apply_final_values(current_time)
        return

    _set_undo_enabled(True)
    opened = False
    try:
        cmds.undoInfo(openChunk=True, chunkName="AnimKey Micro Move")
        opened = True
    except:
        opened = False

    try:
        _apply_final_values(current_time)
    finally:
        if opened:
            try:
                cmds.undoInfo(closeChunk=True)
            except:
                pass
        _set_undo_enabled(False)


def _remember_attr_connection(obj, attr):
    """Store incoming animation/driver connections before Micro Move overrides them."""
    global _s
    attr_path = f"{obj}.{attr}"
    try:
        conns = cmds.listConnections(
            attr_path, source=True, destination=False, plugs=True
        ) or []
    except:
        conns = []
    if conns:
        _s.original_connections[attr_path] = conns[:]
        if _attr_has_keys(attr_path) or any(_plug_has_animation_source(src) for src in conns):
            _s.keyed_attrs.add(attr_path)
        return
    if _attr_has_keys(attr_path):
        _s.keyed_attrs.add(attr_path)


def _restore_attr_connection(attr_path):
    """Reconnect any original input that was temporarily displaced."""
    for src in _s.original_connections.get(attr_path, []):
        try:
            if cmds.objExists(src.split(".", 1)[0]):
                cmds.connectAttr(src, attr_path, force=True)
        except:
            pass


def _safe_node_label(node):
    """Build a Maya-safe label from a DAG path or namespaced control."""
    leaf = (node or "control").split("|")[-1] or "control"
    leaf = leaf.replace(":", "_")
    label = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in leaf)
    label = label.strip("_") or "control"
    return label[:48]


def _temp_name(obj, suffix):
    return "AK_micro_{0}_{1}#".format(_safe_node_label(obj), suffix)


def _read_attr_values(node, attrs):
    values = {}
    for attr in attrs:
        try:
            values[attr] = float(cmds.getAttr("{0}.{1}".format(node, attr)))
        except:
            values[attr] = 0.0
    return values


def _attr_is_editable(obj, attr):
    attr_path = "{0}.{1}".format(obj, attr)
    try:
        if not cmds.objExists(attr_path):
            return False
        if cmds.getAttr(attr_path, lock=True):
            return False
        return True
    except:
        return False


def _set_input3d(node, index, axis, value):
    cmds.setAttr(
        "{0}.input3D[{1}].input3D{2}".format(node, index, axis.lower()),
        value
    )


def _output3d(node, axis):
    return "{0}.output3D.output3D{1}".format(node, axis.lower())


def _input3d(node, index, axis):
    return "{0}.input3D[{1}].input3D{2}".format(node, index, axis.lower())


def _long_existing_names(nodes):
    names = []
    for node in nodes or []:
        try:
            found = cmds.ls(node, long=True) or []
            names.extend(found)
        except:
            pass
    return names


def _should_restore_selection(driver_nodes):
    """Only restore if the user is still effectively selecting Micro Move drivers."""
    drivers = set(_long_existing_names(driver_nodes))
    if not drivers:
        return False

    selection = set(_long_existing_names(cmds.ls(selection=True) or []))
    if not selection:
        return True
    return selection.issubset(drivers)


def _create_driver(obj):
    """Create a locator driver at the selected control, in the closest useful space."""
    driver = cmds.spaceLocator(name=_temp_name(obj, "drv"))[0]

    try:
        rotate_order = cmds.getAttr("{0}.rotateOrder".format(obj))
        cmds.setAttr("{0}.rotateOrder".format(driver), rotate_order)
    except:
        pass

    try:
        cmds.matchTransform(driver, obj, position=True, rotation=True)
    except:
        pass

    parents = []
    try:
        parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
    except:
        parents = []

    if parents:
        try:
            parented = cmds.parent(driver, parents[0], absolute=True) or []
            if parented:
                driver = parented[0]
        except:
            pass

    try:
        for axis in ("X", "Y", "Z"):
            cmds.setAttr("{0}.localScale{1}".format(driver, axis), 0.35)
    except:
        pass

    long_name = cmds.ls(driver, long=True) or [driver]
    return long_name[0]


def _setup_delta_network(obj, mode):
    """Create a driver network that scales motion deltas instead of absolute values."""
    global _magnitude

    attrs = (
        ['translateX', 'translateY', 'translateZ']
        if mode == 'translate'
        else ['rotateX', 'rotateY', 'rotateZ']
    )
    axes = ['X', 'Y', 'Z']

    editable = [attr for attr in attrs if _attr_is_editable(obj, attr)]
    if not editable:
        return None, []

    original_values = _read_attr_values(obj, attrs)
    driver = _create_driver(obj)
    driver_start = _read_attr_values(driver, attrs)

    sub = cmds.createNode('plusMinusAverage', name=_temp_name(obj, "delta"))
    div = cmds.createNode('multiplyDivide', name=_temp_name(obj, "scale"))
    add = cmds.createNode('plusMinusAverage', name=_temp_name(obj, "out"))
    helpers = [sub, div, add]

    cmds.setAttr("{0}.operation".format(sub), 2)  # subtract
    cmds.setAttr("{0}.operation".format(div), 2)  # divide
    cmds.setAttr("{0}.operation".format(add), 1)  # add

    connected = False
    for attr, axis in zip(attrs, axes):
        if attr not in editable:
            continue

        try:
            _remember_attr_connection(obj, attr)
            _set_input3d(sub, 1, axis, driver_start.get(attr, 0.0))
            _set_input3d(add, 0, axis, original_values.get(attr, 0.0))
            cmds.setAttr("{0}.input2{1}".format(div, axis), _magnitude)

            cmds.connectAttr(
                "{0}.{1}".format(driver, attr),
                _input3d(sub, 0, axis),
                force=True
            )
            cmds.connectAttr(
                _output3d(sub, axis),
                "{0}.input1{1}".format(div, axis),
                force=True
            )
            cmds.connectAttr(
                "{0}.output{1}".format(div, axis),
                _input3d(add, 1, axis),
                force=True
            )
            cmds.connectAttr(
                _output3d(add, axis),
                "{0}.{1}".format(obj, attr),
                force=True
            )
            connected = True
        except:
            pass

    if not connected:
        try:
            cmds.delete([driver] + helpers)
        except:
            pass
        return None, []

    return driver, helpers


# ═══════════════════════════════════════════════════════════════════════════════
#                           SETUP FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _setup_translate(obj):
    """Create driver and delta network for translate."""
    return _setup_delta_network(obj, 'translate')


def _setup_rotate(obj):
    """Create driver and delta network for rotate."""
    return _setup_delta_network(obj, 'rotate')


# ═══════════════════════════════════════════════════════════════════════════════
#                           CLEANUP FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _capture_final_values(mode='translate'):
    """Capture final control values BEFORE deleting temporary nodes."""
    global _s
    
    _s.final_values = {}
    attrs = ['translateX', 'translateY', 'translateZ'] if mode == 'translate' else ['rotateX', 'rotateY', 'rotateZ']
    
    for obj in _s.originals:
        _s.final_values[obj] = {}
        
        for a in attrs:
            try:
                if cmds.objExists("{0}.{1}".format(obj, a)):
                    _s.final_values[obj][a] = cmds.getAttr("{0}.{1}".format(obj, a))
            except:
                pass


def _deferred_cleanup(operation_id=None):
    """Cleanup that runs after Maya finishes its operations"""
    global _s
    if operation_id is not None and operation_id != _s.operation_id:
        return

    if not _s.temp_undo_disabled:
        _begin_temp_undo_suppression()

    current_time = cmds.currentTime(query=True)
    restore_selection = _should_restore_selection(_s.drivers)
    
    # Delete all helper nodes first
    nodes_to_delete = []
    for n in _s.drivers + _s.dividers:
        if n and cmds.objExists(n):
            nodes_to_delete.append(n)
    
    if nodes_to_delete:
        try:
            cmds.delete(nodes_to_delete)
        except:
            pass

    # Reconnect original animation curves/drivers before setting keys.
    for attr_path in list(_s.original_connections.keys()):
        _restore_attr_connection(attr_path)
    
    _apply_final_values_as_single_undo(current_time)
    
    # Restore selection
    valid_originals = [o for o in _s.originals if cmds.objExists(o)]
    if restore_selection and valid_originals:
        try:
            cmds.select(valid_originals, r=True)
        except:
            pass
    
    # Clear state
    _s.drivers = []
    _s.dividers = []
    _s.originals = []
    _s.final_values = {}
    _s.original_connections = {}
    _s.keyed_attrs = set()
    _s.mode = None
    _s.chunk_open = False
    _s.operation_id += 1
    _finish_temp_undo_suppression()


def _cleanup_all(apply_values=False):
    """Force cleanup of any leftover nodes"""
    global _s
    if not _s.temp_undo_disabled:
        _begin_temp_undo_suppression()

    restore_selection = _should_restore_selection(_s.drivers)

    if _s.chunk_open:
        try:
            cmds.undoInfo(closeChunk=True)
        except:
            pass
        _s.chunk_open = False

    if apply_values and _s.mode and _s.originals:
        try:
            _capture_final_values(_s.mode)
        except:
            pass
    
    nodes = []
    for n in _s.drivers + _s.dividers:
        if n and cmds.objExists(n):
            nodes.append(n)
    
    if nodes:
        try:
            cmds.delete(nodes)
        except:
            pass

    for attr_path in list(_s.original_connections.keys()):
        _restore_attr_connection(attr_path)

    if apply_values and _s.final_values:
        current_time = cmds.currentTime(query=True)
        _apply_final_values_as_single_undo(current_time)

    valid_originals = [o for o in _s.originals if cmds.objExists(o)]
    if restore_selection and valid_originals:
        try:
            cmds.select(valid_originals, r=True)
        except:
            pass
    
    _s.drivers = []
    _s.dividers = []
    _s.originals = []
    _s.final_values = {}
    _s.original_connections = {}
    _s.keyed_attrs = set()
    _s.mode = None
    _s.chunk_open = False
    _s.operation_id += 1
    _finish_temp_undo_suppression()


# ═══════════════════════════════════════════════════════════════════════════════
#                           DRAG HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def _pre_move(*args):
    global _s
    if not _s.enabled:
        return
    
    _cleanup_all(apply_values=True)
    _begin_temp_undo_suppression()
    
    sel = cmds.ls(sl=True, tr=True, long=True)
    if not sel:
        _finish_temp_undo_suppression()
        return
    
    _s.originals = sel[:]
    _s.mode = 'translate'
    _s.chunk_open = False
    
    for obj in sel:
        try:
            d, helpers = _setup_translate(obj)
            if d:
                _s.drivers.append(d)
                _s.dividers.extend(helpers)
        except Exception as e:
            print(f"AnimKey Micro: Setup error - {e}")
    
    # Select drivers for manipulation
    if _s.drivers:
        cmds.select(_s.drivers, r=True)
    else:
        _s.chunk_open = False
        _finish_temp_undo_suppression()


def _post_move(*args):
    global _s
    if not _s.enabled:
        return
    
    # Capture values BEFORE any cleanup
    _capture_final_values('translate')
    
    # Defer the actual cleanup to avoid crash
    cleanup_id = _s.operation_id
    cmds.evalDeferred(lambda operation_id=cleanup_id: _deferred_cleanup(operation_id))


def _pre_rotate(*args):
    global _s
    if not _s.enabled:
        return
    
    _cleanup_all(apply_values=True)
    _begin_temp_undo_suppression()
    
    sel = cmds.ls(sl=True, tr=True, long=True)
    if not sel:
        _finish_temp_undo_suppression()
        return
    
    _s.originals = sel[:]
    _s.mode = 'rotate'
    _s.chunk_open = False
    
    for obj in sel:
        try:
            d, helpers = _setup_rotate(obj)
            if d:
                _s.drivers.append(d)
                _s.dividers.extend(helpers)
        except Exception as e:
            print(f"AnimKey Micro: Setup error - {e}")
    
    if _s.drivers:
        cmds.select(_s.drivers, r=True)
    else:
        _s.chunk_open = False
        _finish_temp_undo_suppression()


def _post_rotate(*args):
    global _s
    if not _s.enabled:
        return
    
    _capture_final_values('rotate')
    cleanup_id = _s.operation_id
    cmds.evalDeferred(lambda operation_id=cleanup_id: _deferred_cleanup(operation_id))


# ═══════════════════════════════════════════════════════════════════════════════
#                           CONTEXT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

_move_ctx = "microMoveCtx"
_rotate_ctx = "microRotateCtx"
_job = None


def _create_ctx():
    _delete_ctx()
    
    cmds.manipMoveContext(_move_ctx)
    cmds.manipMoveContext(_move_ctx, e=True,
        preDragCommand=(_pre_move, 'transform'),
        postDragCommand=(_post_move, 'transform'))
    
    cmds.manipRotateContext(_rotate_ctx)
    cmds.manipRotateContext(_rotate_ctx, e=True,
        preDragCommand=(_pre_rotate, 'transform'),
        postDragCommand=(_post_rotate, 'transform'))


def _delete_ctx():
    for c in [_move_ctx, _rotate_ctx]:
        if cmds.contextInfo(c, exists=True):
            try:
                cmds.deleteUI(c, toolContext=True)
            except:
                pass


def _switch_ctx():
    global _s
    if not _s.enabled:
        return
    
    try:
        c = cmds.currentCtx()
        if c in [_move_ctx, _rotate_ctx]:
            return
        
        if 'move' in c.lower() or c == 'moveSuperContext':
            if cmds.contextInfo(_move_ctx, exists=True):
                cmds.setToolTo(_move_ctx)
        elif 'rotate' in c.lower() or c == 'RotateSuperContext':
            if cmds.contextInfo(_rotate_ctx, exists=True):
                cmds.setToolTo(_rotate_ctx)
    except:
        pass  # Silently ignore - context may have been deleted


def _on_tool_change():
    if _s.enabled:
        cmds.evalDeferred(_switch_ctx)


def _start_job():
    global _job
    _stop_job()
    _job = cmds.scriptJob(event=['ToolChanged', _on_tool_change])


def _stop_job():
    global _job
    if _job and cmds.scriptJob(exists=_job):
        try:
            cmds.scriptJob(kill=_job, force=True)
        except:
            pass
    _job = None


# ═══════════════════════════════════════════════════════════════════════════════
#                           PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def execute(button=None):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.microMove.execute"):
        return None
    global _s
    
    _s.enabled = not _s.enabled
    _s.button = button
    
    if _s.enabled:
        if button:
            set_button_active(button, True)
        
        _create_ctx()
        _start_job()
        _switch_ctx()
        
        cmds.inViewMessage(
            amg=f"<span style='color:#a3be8c'>Micro Move ON ({_magnitude}x)</span>",
            pos='topCenter', fade=True, fadeStayTime=1500)
    else:
        # First disable state to prevent callbacks
        _s.enabled = False
        
        if button:
            set_button_active(button, False)
        
        # Stop job BEFORE anything else
        _stop_job()
        _cleanup_all(apply_values=True)
        
        # Delete contexts
        _delete_ctx()
        
        # Switch back to normal move tool
        try:
            import maya.mel as mel
            mel.eval('setToolTo moveSuperContext')
        except:
            pass
        
        cmds.inViewMessage(
            amg="<span style='color:#bf616a'>Micro Move OFF</span>",
            pos='topCenter', fade=True, fadeStayTime=1000)


def _set_tool(t):
    try:
        import maya.mel as mel
        mel.eval(f'setToolTo {t}')
    except:
        pass


def is_active():
    return _s.enabled


def set_button_active(button, active):
    try:
        from AnimKey.mods.themes import ThemeManager
        theme = ThemeManager.get_current_theme()
        
        if active:
            button.setStyleSheet('''
                QPushButton {
                    color: white; background-color: #ebcb8b;
                    border: 2px solid #ebcb8b; border-radius: 4px;
                    font-size: 9px; font-weight: bold;
                }
                QPushButton:hover { background-color: #d4b679; }
            ''')
        else:
            button.setStyleSheet(f'''
                QPushButton {{
                    color: #ebcb8b; background-color: {theme["button_bg"]};
                    border: 1px solid {theme["border_color"]}; border-radius: 4px;
                    font-size: 9px; font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {theme["button_hover"]}; border-color: #ebcb8b; }}
            ''')
    except:
        pass

