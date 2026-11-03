"""Temp Pivot data engine from the user-provided Animo v10.7.5 source.
AnimKey owns UI, undo boundaries, storage, identity and callback lifetimes.
See temp_pivot_provenance.json for source identity and interface changes.
"""
import json
import math
import os
import time
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

PIVOT_NULL = None
TR_ATTRS = ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']
_cache_valid = False
_storage_path = None
_memory_override = None

def get_prefs_path():
    if not _storage_path:
        raise RuntimeError('AnimKey Temp Pivot storage was not configured')
    return _storage_path

def load_offsets():
    if _memory_override is not None:
        return _memory_override
    try:
        with open(get_prefs_path(), 'r', encoding='utf-8') as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError('Invalid pivot memory')
    except (OSError, ValueError):
        data = {}
    data.setdefault('single_offsets', {})
    data.setdefault('multi_offsets', [])
    data.setdefault('last_selection', [])
    return data

def save_offsets(data):
    path = get_prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
    os.replace(temporary, path)

def get_mobject(name):
    sel = om.MSelectionList()
    try:
        sel.add(name)
        return sel.getDependNode(0)
    except:
        return None

def obj_exists(name):
    sel = om.MSelectionList()
    try:
        sel.add(name)
        return True
    except:
        return False

def get_matrix(name):
    return list(cmds.getAttr(name + '.worldMatrix[0]'))

def set_pivot_matrix(matrix):
    target = om.MMatrix(matrix)
    order = cmds.getAttr(PIVOT_NULL + '.rotateOrder')
    euler = om.MTransformationMatrix(target).rotation(False).reorder(order)
    rot_values = {'rx': math.degrees(euler.x), 'ry': math.degrees(euler.y), 'rz': math.degrees(euler.z)}
    for attr in ['rx', 'ry', 'rz']:
        try:
            cmds.setAttr(PIVOT_NULL + '.' + attr, rot_values[attr])
        except:
            pass
    current = om.MMatrix(cmds.getAttr(PIVOT_NULL + '.matrix'))
    for (index, attr) in [(12, 'tx'), (13, 'ty'), (14, 'tz')]:
        plug = PIVOT_NULL + '.' + attr
        try:
            cmds.setAttr(plug, cmds.getAttr(plug) + target[index] - current[index])
        except:
            pass

def hierarchy_depth(name):
    if not name or not obj_exists(name):
        return 0
    paths = cmds.ls(name, long=True) or [name]
    return paths[0].count('|')

def get_current_time():
    return oma.MAnimControl.currentTime().value

def set_selection(names):
    sel = om.MSelectionList()
    for name in names:
        try:
            sel.add(name)
        except:
            pass
    om.MGlobal.setActiveSelectionList(sel)

def has_keyframes(name, attrs):
    if not obj_exists(name):
        return False
    for attr in attrs:
        try:
            keys = cmds.keyframe(name + '.' + attr, q=True, keyframeCount=True) or 0
            if keys > 0:
                return True
        except:
            pass
    return False

def get_key_count(name, attrs):
    count = 0
    if not obj_exists(name):
        return 0
    for attr in attrs:
        try:
            count += cmds.keyframe(name + '.' + attr, q=True, keyframeCount=True) or 0
        except:
            pass
    return count

def is_attr_locked(name, attr):
    obj = get_mobject(name)
    if not obj:
        return True
    fn = om.MFnDependencyNode(obj)
    try:
        return fn.findPlug(attr, False).isLocked
    except:
        return True

def run_euler_filter(name):
    if not obj_exists(name):
        return
    curves = []
    for attr in ['rx', 'ry', 'rz']:
        try:
            if cmds.keyframe(name + '.' + attr, q=True, keyframeCount=True):
                curves.append(name + '.' + attr)
        except:
            pass
    if curves:
        try:
            cmds.filterCurve(curves, filter='euler')
        except:
            pass

class LiveTempPivot:

    def __init__(self):
        self.timer = None
        self.event_filter = None
        self.original_timeline_connection = None
        self.time_job = None
        self.selection_job = None
        self.undo_job = None
        self.redo_job = None
        self.manip_chunk_open = False
        self.reapply_delay = 0.6
        self.cooldown_duration = 0.1
        self.reset_state()

    def reset_state(self):
        self.sel = []
        self.locators = []
        self.locked_attrs = {}
        self.last_pivot_matrix = None
        self.pivot_to_ref_offset = None
        self.ref_obj = None
        self.objects_had_keys = False
        self.keys_set_this_session = False
        self.is_time_changing = False
        self.last_pivot_key_count = 0
        self.last_change_time = 0
        self.pending_reapply = False
        self.time_change_cooldown = 0
        self.awaiting_real_manipulation = False
        self.saved_pivot = None

    def matrices_equal(self, m1, m2, tolerance=0.0001):
        if m1 is None or m2 is None:
            return False
        for i in range(16):
            if abs(m1[i] - m2[i]) > tolerance:
                return False
        return True

    def matrix_moved_significantly(self, m1, m2, threshold=0.01):
        if m1 is None or m2 is None:
            return False
        if abs(m1[12] - m2[12]) > threshold or abs(m1[13] - m2[13]) > threshold or abs(m1[14] - m2[14]) > threshold:
            return True
        for i in range(12):
            if abs(m1[i] - m2[i]) > 0.005:
                return True
        return False

    def cache_locked_attrs(self):
        self.locked_attrs = {}
        for obj in self.sel:
            skip_t = [a for a in 'xyz' if is_attr_locked(obj, 't' + a)]
            skip_r = [a for a in 'xyz' if is_attr_locked(obj, 'r' + a)]
            self.locked_attrs[obj] = {'skip_t': skip_t, 'skip_r': skip_r}

    def build_locators(self):
        for (obj, loc) in self.locators:
            if obj_exists(loc):
                cmds.delete(loc)
        self.locators = []
        for (index, obj) in enumerate(self.sel):
            loc = cmds.spaceLocator(name='AnimKey_Temp_Pivot_Locator_{}'.format(index))[0]
            cmds.matchTransform(loc, obj, pos=True, rot=True)
            loc = cmds.parent(loc, PIVOT_NULL)[0]
            cmds.setAttr(loc + '.visibility', 0)
            self.locators.append((obj, loc))

    def refresh_locators(self):
        for (obj, loc) in self.locators:
            if obj_exists(obj) and obj_exists(loc):
                cmds.matchTransform(loc, obj, pos=True, rot=True)

    def follow(self):
        if not obj_exists(PIVOT_NULL):
            return
        pairs = [p for p in self.locators if obj_exists(p[0]) and obj_exists(p[1])]
        pairs.sort(key=lambda p: hierarchy_depth(p[0]))
        for _ in range(len(pairs) + 1):
            changed = False
            for (obj, loc) in pairs:
                locked = self.locked_attrs.get(obj, {})
                pos = len(locked.get('skip_t', [])) == 0
                rot = len(locked.get('skip_r', [])) == 0
                if not pos and (not rot):
                    continue
                before = get_matrix(obj)
                try:
                    cmds.matchTransform(obj, loc, pos=pos, rot=rot)
                except:
                    continue
                if not self.matrices_equal(before, get_matrix(obj), 1e-06):
                    changed = True
            if not changed:
                break

    def store_pivot_offset(self):
        if not obj_exists(PIVOT_NULL) or not self.ref_obj or (not obj_exists(self.ref_obj)):
            return
        ref_matrix = om.MMatrix(get_matrix(self.ref_obj))
        pivot_matrix = om.MMatrix(get_matrix(PIVOT_NULL))
        self.pivot_to_ref_offset = pivot_matrix * ref_matrix.inverse()

    def apply_pivot_from_ref(self):
        if not obj_exists(PIVOT_NULL) or not self.ref_obj or (not obj_exists(self.ref_obj)):
            return
        if self.pivot_to_ref_offset is None:
            return
        ref_matrix = om.MMatrix(get_matrix(self.ref_obj))
        set_pivot_matrix(list(self.pivot_to_ref_offset * ref_matrix))
        self.last_pivot_matrix = get_matrix(PIVOT_NULL)

    def on_timer_tick(self):
        if not obj_exists(PIVOT_NULL):
            self.stop_timer()
            return
        if self.is_time_changing:
            return
        now = time.time()
        if now < self.time_change_cooldown:
            self.maybe_finalize()
            return
        current_matrix = get_matrix(PIVOT_NULL)
        if self.awaiting_real_manipulation:
            if self.matrix_moved_significantly(self.last_pivot_matrix, current_matrix):
                self.awaiting_real_manipulation = False
            else:
                self.last_pivot_matrix = current_matrix
                self.maybe_finalize()
                return
        changed = self.last_pivot_matrix is None or not self.matrices_equal(self.last_pivot_matrix, current_matrix, 0.0005)
        if changed:
            self.open_manip_chunk()
            self.last_pivot_matrix = current_matrix
            self.follow()
            self.store_pivot_offset()
            self.last_change_time = now
            self.pending_reapply = True
        self.maybe_finalize()
        if get_key_count(PIVOT_NULL, TR_ATTRS) > self.last_pivot_key_count:
            self.open_manip_chunk()
            pivot_matrix_before = get_matrix(PIVOT_NULL)
            for obj in self.sel:
                if obj_exists(obj):
                    cmds.setKeyframe(obj, attribute=TR_ATTRS)
            self.keys_set_this_session = True
            for attr in TR_ATTRS:
                try:
                    cmds.cutKey(PIVOT_NULL, attribute=attr, clear=True)
                except:
                    pass
            set_pivot_matrix(pivot_matrix_before)
            self.store_pivot_offset()
            self.last_pivot_matrix = pivot_matrix_before
            self.awaiting_real_manipulation = False
            self.pending_reapply = False
            self.last_pivot_key_count = 0
            self.close_manip_chunk()

    def on_time_changed(self):
        if not obj_exists(PIVOT_NULL):
            return
        self.is_time_changing = True
        autokey_state = cmds.autoKeyframe(q=True, state=True)
        if autokey_state:
            cmds.autoKeyframe(state=False)
        opened_here = not self.manip_chunk_open
        self.open_manip_chunk()
        try:
            for attr in TR_ATTRS:
                try:
                    cmds.cutKey(PIVOT_NULL, attribute=attr, clear=True)
                except:
                    pass
            self.apply_pivot_from_ref()
            self.refresh_locators()
            self.store_pivot_offset()
        finally:
            if autokey_state:
                cmds.autoKeyframe(state=True)
            if opened_here:
                self.close_manip_chunk()
        self.last_pivot_matrix = get_matrix(PIVOT_NULL)
        self.last_pivot_key_count = 0
        self.time_change_cooldown = time.time() + self.cooldown_duration
        self.awaiting_real_manipulation = True
        self.is_time_changing = False

    def save_relationship_to_disk(self):
        if not obj_exists(PIVOT_NULL) or not self.sel:
            return
        if not self.ref_obj or not obj_exists(self.ref_obj):
            self.ref_obj = self.sel[-1]
        self.store_pivot_offset()
        if self.pivot_to_ref_offset is None:
            return
        selection = list(self.sel)
        ref_obj = self.ref_obj
        data = load_offsets()
        data['last_selection'] = selection
        pivot_point = list(cmds.getAttr(PIVOT_NULL + '.rotatePivot')[0])
        self.saved_pivot = pivot_point
        offset_data = {'offset_matrix': list(self.pivot_to_ref_offset), 'pivot': pivot_point, 'ref_obj': ref_obj}
        if len(selection) == 1:
            data['single_offsets'][ref_obj] = offset_data
        else:
            sel_sorted = sorted(selection)
            found = False
            for group in data['multi_offsets']:
                if sorted(group['objects']) == sel_sorted:
                    group['offset_data'] = offset_data
                    found = True
                    break
            if not found:
                data['multi_offsets'].append({'objects': list(selection), 'offset_data': offset_data})
        save_offsets(data)

    def apply_stored_offset(self, selection):
        if not obj_exists(PIVOT_NULL):
            return False
        if not os.path.exists(get_prefs_path()):
            return False
        global _cache_valid
        _cache_valid = False
        data = load_offsets()
        offset_data = None
        if len(selection) == 1:
            offset_data = data.get('single_offsets', {}).get(selection[0])
        else:
            sel_sorted = sorted(selection)
            for group in data.get('multi_offsets', []):
                if sorted(group.get('objects', [])) == sel_sorted:
                    offset_data = group.get('offset_data')
                    break
        if not offset_data or 'offset_matrix' not in offset_data:
            return False
        ref_obj = offset_data.get('ref_obj', selection[-1])
        if not obj_exists(ref_obj):
            ref_obj = selection[-1]
        pivot_point = offset_data.get('pivot', [0, 0, 0])
        cmds.setAttr(PIVOT_NULL + '.rotatePivot', pivot_point[0], pivot_point[1], pivot_point[2])
        cmds.setAttr(PIVOT_NULL + '.scalePivot', pivot_point[0], pivot_point[1], pivot_point[2])
        ref_matrix = om.MMatrix(get_matrix(ref_obj))
        set_pivot_matrix(list(om.MMatrix(offset_data['offset_matrix']) * ref_matrix))
        self.saved_pivot = list(pivot_point)
        return True
