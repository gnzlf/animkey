"""
AnimKey mirror engine.

Original AnimKey implementation. One mirror table per rig stores, for each
control, its partner, its rest values and a per-axis sign vector measured at
the rest pose. Mirroring is then a cheap per-channel copy:

    value_dest = rest_dest + sign * (value_src - rest_src)

* Signs are measured from world matrices with OpenMaya (no temporary nodes),
  so building a table for a full rig takes a fraction of a second.
* Rotation signs use the general rule for a reflected frame
  (angle sign = axis_sign * det(axis_signs)), which stays correct even when a
  pair's measured frame is not the textbook case.
* Values are mirrored relative to the rest pose, so controls whose rest values
  are not zero (offset channels, custom defaults) mirror correctly.
* Tables are keyed by namespace-less names, so one table serves every
  reference of the same rig.
* Per-control "invert" and "keep" exceptions are stored with the table.
"""

import json
import os

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

from AnimKey.buttons import selectOpposite as side_names


TABLE_VERSION = 1
TRANSLATE_ATTRS = ("translateX", "translateY", "translateZ")
ROTATE_ATTRS = ("rotateX", "rotateY", "rotateZ")
IGNORED_ATTRS = {"visibility", "lodVisibility", "intermediateObject", "tag"}
_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}
# World axis across which the rig is mirrored (0 = X, i.e. the YZ plane).
MIRROR_WORLD_AXIS = 0

_table_cache = {}


# ═══════════════════════════════════════════════════════════════════════════════
#                              NAMES / RIG SCOPE
# ═══════════════════════════════════════════════════════════════════════════════

def _long(node):
    found = cmds.ls(node, long=True) or []
    return found[0] if found else node


def _namespace(node):
    leaf = node.rpartition("|")[2]
    namespace, sep, _ = leaf.rpartition(":")
    return namespace + sep


def _key(node):
    """Namespace-less leaf name: identical for every reference of a rig."""
    return node.rpartition("|")[2].rpartition(":")[2]


def _rig_id(node):
    from AnimKey.buttons import mirror as mirror_button
    return mirror_button.get_rig_identifier([node])


def _table_path(rig_id):
    from AnimKey.buttons import mirror as mirror_button
    folder = os.path.join(mirror_button.get_user_data_folder(), "tools", "mirror", "tables")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(rig_id))
    return os.path.join(folder, safe + ".json")


def _resolve(reference, key):
    """Find the scene node for ``key`` living next to ``reference``."""
    if not key:
        return None
    namespace = _namespace(reference)
    parent = reference.rpartition("|")[0]
    for candidate in ((parent + "|" + namespace + key) if parent else None, namespace + key, key):
        if not candidate:
            continue
        try:
            resolved = side_names._resolve_existing(candidate, original_name=reference)
        except Exception:
            resolved = None
        if resolved:
            return resolved
    return None


def _has_curve_shape(node):
    shapes = cmds.listRelatives(node, shapes=True, noIntermediate=True, fullPath=True) or []
    return any(cmds.nodeType(shape) == "nurbsCurve" for shape in shapes)


def rig_controls(seed):
    """All animation controls of the rig ``seed`` belongs to."""
    seed = _long(seed)
    namespace = _namespace(seed)
    if namespace:
        shapes = cmds.ls(namespace + "*", type="nurbsCurve", long=True, noIntermediate=True) or []
    else:
        root = "|" + seed.split("|")[1] if seed.startswith("|") else seed
        shapes = cmds.listRelatives(root, allDescendents=True, type="nurbsCurve",
                                    fullPath=True) or []
        shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
    controls = []
    seen = set()
    for shape in shapes:
        parent = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
        if parent and parent not in seen and _keyable_attrs(parent):
            seen.add(parent)
            controls.append(parent)
    if seed not in seen:
        controls.append(seed)
    return controls


# ═══════════════════════════════════════════════════════════════════════════════
#                                 CHANNELS
# ═══════════════════════════════════════════════════════════════════════════════

def _keyable_attrs(node):
    try:
        attrs = cmds.listAttr(node, keyable=True, unlocked=True, scalar=True) or []
    except Exception:
        return []
    return [a for a in attrs if a not in IGNORED_ATTRS and "." not in a]


def _get(plug):
    try:
        value = cmds.getAttr(plug)
    except Exception:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _default(node, attr):
    try:
        value = cmds.attributeQuery(attr, node=node, listDefault=True)
        if value:
            return float(value[0])
    except Exception:
        pass
    return 1.0 if attr.startswith("scale") else 0.0


def _is_keyed(plug):
    try:
        return bool(cmds.keyframe(plug, query=True, keyframeCount=True))
    except Exception:
        return False


def selected_channels(nodes=None):
    """Long names of the channels highlighted in the Channel Box (or None)."""
    try:
        box = mel.eval("global string $gChannelBoxName; $animKeyMirrorCb = $gChannelBoxName;")
    except Exception:
        box = "mainChannelBox"
    attrs = []
    for flag in ("selectedMainAttributes", "selectedShapeAttributes", "selectedHistoryAttributes"):
        try:
            attrs.extend(cmds.channelBox(box, query=True, **{flag: True}) or [])
        except Exception:
            pass
    if not attrs:
        return None
    result = set()
    for attr in attrs:
        long_name = attr
        for node in nodes or []:
            try:
                if cmds.attributeQuery(attr, node=node, exists=True):
                    long_name = cmds.attributeQuery(attr, node=node, longName=True)
                    break
            except Exception:
                continue
        result.add(long_name)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#                              AXIS MEASUREMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _world_matrix(node):
    try:
        selection = om.MSelectionList()
        selection.add(node)
        return selection.getDagPath(0).inclusiveMatrix()
    except Exception:
        return None


def _axis_row(matrix, index):
    return [matrix.getElement(index, column) for column in range(3)]


def _reflect(vector):
    vector = list(vector)
    vector[MIRROR_WORLD_AXIS] = -vector[MIRROR_WORLD_AXIS]
    return vector


def _pair_axis(source_matrix, target_matrix):
    """Sign per local axis: does the target axis follow the reflected source axis?"""
    axis = []
    for index in range(3):
        reflected = _reflect(_axis_row(source_matrix, index))
        target = _axis_row(target_matrix, index)
        dot = sum(a * b for a, b in zip(reflected, target))
        axis.append(-1 if dot < 0.0 else 1)
    return axis


def _center_axis(matrix):
    """The local axis pointing most across the mirror plane flips; the others don't."""
    weights = []
    for index in range(3):
        row = _axis_row(matrix, index)
        length = sum(c * c for c in row) ** 0.5 or 1.0
        weights.append(abs(row[MIRROR_WORLD_AXIS]) / length)
    axis = [1, 1, 1]
    axis[weights.index(max(weights))] = -1
    return axis


def _world_position(matrix):
    return [matrix.getElement(3, column) for column in range(3)]


def _geometric_partners(controls, matrices, taken):
    """Pair leftover controls by mirrored world position (mutual nearest)."""
    free = [c for c in controls if c not in taken and matrices.get(c) is not None]
    positions = {c: _world_position(matrices[c]) for c in free}

    def nearest(control):
        target = _reflect(positions[control])
        travel = sum((a - b) ** 2 for a, b in zip(positions[control], target)) ** 0.5
        if travel < 1e-4:
            return None
        best, best_distance = None, None
        for other in free:
            if other == control:
                continue
            distance = sum((a - b) ** 2 for a, b in zip(positions[other], target)) ** 0.5
            if best_distance is None or distance < best_distance:
                best, best_distance = other, distance
        if best is not None and best_distance <= max(travel * 0.05, 1e-3):
            return best
        return None

    pairs = {}
    for control in free:
        if control in pairs:
            continue
        match = nearest(control)
        if match and match not in pairs and nearest(match) == control:
            pairs[control] = match
            pairs[match] = control
    return pairs


# ═══════════════════════════════════════════════════════════════════════════════
#                                   TABLES
# ═══════════════════════════════════════════════════════════════════════════════

def _empty_table():
    return {"version": TABLE_VERSION, "controls": {}, "exceptions": {}}


def load_table(rig_id):
    if rig_id in _table_cache:
        return _table_cache[rig_id]
    path = _table_path(rig_id)
    table = None
    if os.path.exists(path):
        try:
            with open(path, "r") as handle:
                table = json.load(handle)
        except (OSError, ValueError):
            table = None
    if not table or table.get("version") != TABLE_VERSION:
        previous = (table or {}).get("exceptions") or {}
        table = _empty_table()
        table["exceptions"] = previous
    _table_cache[rig_id] = table
    return table


def save_table(rig_id, table):
    path = _table_path(rig_id)
    temp = path + ".tmp"
    with open(temp, "w") as handle:
        json.dump(table, handle, indent=1, sort_keys=True)
    os.replace(temp, path)
    _table_cache[rig_id] = table


def delete_table(rig_id):
    path = _table_path(rig_id)
    _table_cache.pop(rig_id, None)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def _measure_rest(controls):
    """Rest values and rest world matrices, without touching undo or keys."""
    current = {}
    rest_values = {}
    for control in controls:
        values = {}
        defaults = {}
        for attr in _keyable_attrs(control):
            value = _get(control + "." + attr)
            if value is None:
                continue
            values[attr] = value
            defaults[attr] = _default(control, attr)
        current[control] = values
        rest_values[control] = defaults

    undo_state = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    matrices = {}
    try:
        for control, defaults in rest_values.items():
            for attr, value in defaults.items():
                try:
                    cmds.setAttr(control + "." + attr, value)
                except Exception:
                    pass
        # Measure in rig space: the rig's top DAG node defines the mirror plane,
        # so characters moved or rotated in the shot still mirror correctly.
        root_inverse = None
        if controls:
            first = _long(controls[0])
            root = "|" + first.split("|")[1] if first.startswith("|") else first
            root_matrix = _world_matrix(root)
            if root_matrix is not None:
                root_inverse = root_matrix.inverse()
        for control in controls:
            matrix = _world_matrix(control)
            if matrix is not None and root_inverse is not None:
                matrix = matrix * root_inverse
            matrices[control] = matrix
    finally:
        for control, values in current.items():
            for attr, value in values.items():
                try:
                    cmds.setAttr(control + "." + attr, value)
                except Exception:
                    pass
        cmds.undoInfo(stateWithoutFlush=undo_state)
        try:
            cmds.currentTime(cmds.currentTime(query=True), edit=True, update=True)
        except Exception:
            pass
    return rest_values, matrices


def build_table(seed, force=False):
    """Measure (or complete) the mirror table for the rig ``seed`` belongs to."""
    rig_id = _rig_id(seed)
    table = _empty_table() if force else load_table(rig_id)
    if force:
        table["exceptions"] = load_table(rig_id).get("exceptions", {})
    controls = rig_controls(seed)
    rest_values, matrices = _measure_rest(controls)

    partners = {}
    for control in controls:
        if control in partners:
            continue
        opposite = side_names.find_opposite_name(control)
        opposite = _long(opposite) if opposite else None
        if opposite and opposite != control and opposite not in partners:
            if opposite not in matrices:
                extra_rest, extra_matrix = _measure_rest([opposite])
                rest_values.update(extra_rest)
                matrices.update(extra_matrix)
            partners[control] = opposite
            partners[opposite] = control
    partners.update(_geometric_partners(controls, matrices, partners))

    entries = table["controls"]
    for control in set(controls) | set(partners):
        matrix = matrices.get(control)
        if matrix is None:
            continue
        partner = partners.get(control)
        if partner and matrices.get(partner) is not None:
            axis = _pair_axis(matrix, matrices[partner])
        else:
            partner = None
            axis = _center_axis(matrix)
        entries[_key(control)] = {
            "partner": _key(partner) if partner else None,
            "axis": axis,
            "rest": rest_values.get(control, {}),
            "side": _side(control, matrix),
        }
    save_table(rig_id, table)
    return table


def _side(control, matrix=None):
    side = side_names.detect_side(control)
    if side:
        return side
    matrix = matrix if matrix is not None else _world_matrix(control)
    if matrix is None:
        return None
    x = _world_position(matrix)[MIRROR_WORLD_AXIS]
    if abs(x) < 1e-3:
        return None
    return "left" if x > 0 else "right"


def table_for(controls):
    """Table covering every control in ``controls`` (built on first use)."""
    tables = {}
    for control in controls:
        rig_id = _rig_id(control)
        table = tables.get(rig_id) or load_table(rig_id)
        if _key(control) not in table["controls"]:
            table = build_table(control)
        tables[rig_id] = table
    return tables


# ═══════════════════════════════════════════════════════════════════════════════
#                                 CHANNEL RULES
# ═══════════════════════════════════════════════════════════════════════════════

def partner_of(control, table):
    entry = table["controls"].get(_key(control))
    if not entry:
        return None
    if not entry.get("partner"):
        return control
    return _resolve(control, entry["partner"])


def side_of(control, table):
    entry = table["controls"].get(_key(control)) or {}
    return entry.get("side") or _side(control)


def channel_sign(axis, attr):
    """+1 or -1 for ``attr`` given the measured axis signs."""
    index = _AXIS_INDEX.get(attr[-1:])
    if index is None:
        return 1
    if attr in TRANSLATE_ATTRS:
        return axis[index]
    if attr in ROTATE_ATTRS:
        determinant = axis[0] * axis[1] * axis[2]
        # Conjugating a rotation by diag(axis) scales the angle about axis i
        # by axis[i] * det(axis).
        return 1 if axis[index] * determinant > 0 else -1
    return 1


def channel_rule(source, target, attr, table):
    """(sign, source_rest, target_rest) for one channel, or None to leave it."""
    source_entry = table["controls"].get(_key(source)) or {}
    target_entry = table["controls"].get(_key(target)) or {}
    exceptions = table.get("exceptions", {})
    rule = (exceptions.get(_key(target)) or {}).get(attr) or \
           (exceptions.get(_key(source)) or {}).get(attr)
    if rule == "keep":
        return None
    axis = source_entry.get("axis") or target_entry.get("axis") or [1, 1, 1]
    sign = channel_sign(axis, attr)
    if rule == "invert":
        sign = -sign
    source_rest = (source_entry.get("rest") or {}).get(attr)
    target_rest = (target_entry.get("rest") or {}).get(attr)
    if source_rest is None:
        source_rest = _default(source, attr)
    if target_rest is None:
        target_rest = _default(target, attr)
    return sign, source_rest, target_rest


def mirrored_value(value, rule):
    sign, source_rest, target_rest = rule
    return target_rest + sign * (value - source_rest)


# ═══════════════════════════════════════════════════════════════════════════════
#                                  PLANNING
# ═══════════════════════════════════════════════════════════════════════════════

def plan_pairs(selection, mode="to_opposite", target_side=None):
    """
    Return ([(source, target)], tables).

    mode "to_opposite": selected controls drive their partners (centers flip).
    mode "swap":        each pair exchanges values; centers flip.
    target_side:        "left"/"right" - only write that side, whichever side
                        of the pair was selected.
    """
    selection = [_long(node) for node in selection if cmds.objExists(node)]
    tables = table_for(selection)
    pairs = []
    seen = set()
    for control in selection:
        table = tables[_rig_id(control)]
        partner = partner_of(control, table)
        if not partner:
            continue
        if partner == control:
            if target_side:
                continue
            if control not in seen:
                seen.add(control)
                pairs.append((control, control))
            continue
        if target_side:
            if side_of(partner, table) == target_side:
                source, target = control, partner
            elif side_of(control, table) == target_side:
                source, target = partner, control
            else:
                continue
            if target not in seen:
                seen.add(target)
                pairs.append((source, target))
        elif mode == "swap":
            for source, target in ((control, partner), (partner, control)):
                if target not in seen:
                    seen.add(target)
                    pairs.append((source, target))
        else:
            if partner not in seen:
                seen.add(partner)
                pairs.append((control, partner))
    return pairs, tables


def _channel_jobs(pairs, tables, attr_filter=None):
    jobs = []
    for source, target in pairs:
        table = tables[_rig_id(source)]
        target_attrs = set(_keyable_attrs(target))
        for attr in _keyable_attrs(source):
            if attr not in target_attrs:
                continue
            if attr_filter and attr not in attr_filter:
                continue
            rule = channel_rule(source, target, attr, table)
            if rule is not None:
                jobs.append((source, target, attr, rule))
    return jobs


# ═══════════════════════════════════════════════════════════════════════════════
#                                   APPLY
# ═══════════════════════════════════════════════════════════════════════════════

def mirror_pose(pairs, tables, attr_filter=None):
    """Mirror current values. All sources are read before anything is written."""
    jobs = _channel_jobs(pairs, tables, attr_filter)
    values = {}
    for source, _, attr, _ in jobs:
        plug = source + "." + attr
        if plug not in values:
            values[plug] = _get(plug)
    now = cmds.currentTime(query=True)
    written = 0
    for source, target, attr, rule in jobs:
        value = values.get(source + "." + attr)
        if value is None:
            continue
        plug = target + "." + attr
        new_value = mirrored_value(value, rule)
        try:
            if _is_keyed(plug):
                cmds.setKeyframe(plug, time=(now, now), value=new_value)
            else:
                cmds.setAttr(plug, new_value)
            written += 1
        except Exception:
            pass
    return written


def _source_curve(plug):
    curves = cmds.listConnections(plug, source=True, destination=False, type="animCurve") or []
    return curves[0] if curves else None


def mirror_animation(pairs, tables, time_range=None, attr_filter=None, key_times=None):
    """
    Mirror animation curves.

    time_range: (start, end) to replace only that range; None = whole curves.
    key_times:  {source_plug: (start, end)} from Graph Editor key selection.
    """
    jobs = _channel_jobs(pairs, tables, attr_filter)
    targets = set(target + "." + attr for _, target, attr, _ in jobs)
    work = []
    for source, target, attr, rule in jobs:
        source_plug = source + "." + attr
        scope = key_times.get(source_plug) if key_times is not None else time_range
        if key_times is not None and scope is None:
            continue
        if not _is_keyed(source_plug):
            continue
        work.append((source_plug, target + "." + attr, rule, scope))

    # Sources that are also written in this pass (swaps, centers) are copied
    # first so every target reads the original animation.
    undo_state = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    copies = {}
    try:
        for source_plug, _, _, _ in work:
            if source_plug in targets and source_plug not in copies:
                curve = _source_curve(source_plug)
                if curve:
                    copies[source_plug] = cmds.duplicate(curve)[0]
    finally:
        cmds.undoInfo(stateWithoutFlush=undo_state)

    written = 0
    try:
        for source_plug, target_plug, rule, scope in work:
            source = copies.get(source_plug, source_plug)
            try:
                if cmds.getAttr(target_plug, lock=True):
                    continue
                if scope:
                    if not cmds.copyKey(source, time=scope):
                        continue
                    cmds.pasteKey(target_plug, option="replace")
                else:
                    cmds.copyKey(source)
                    cmds.pasteKey(target_plug, option="replaceCompletely")
            except Exception:
                continue
            sign, source_rest, target_rest = rule
            edit = {"time": scope} if scope else {}
            try:
                if sign < 0:
                    cmds.scaleKey(target_plug, valueScale=-1, valuePivot=source_rest, **edit)
                if abs(target_rest - source_rest) > 1e-9:
                    cmds.keyframe(target_plug, edit=True, relative=True,
                                  valueChange=target_rest - source_rest, **edit)
            except Exception:
                pass
            written += 1
    finally:
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            for curve in copies.values():
                if cmds.objExists(curve):
                    cmds.delete(curve)
        finally:
            cmds.undoInfo(stateWithoutFlush=undo_state)
    return written


def selected_graph_keys():
    """{plug: (first, last)} for keys selected in the Graph Editor, or None."""
    curves = cmds.keyframe(query=True, selected=True, name=True) or []
    if not curves:
        return None
    result = {}
    for curve in curves:
        times = cmds.keyframe(curve, query=True, selected=True, timeChange=True) or []
        plugs = cmds.listConnections(curve + ".output", plugs=True, source=False,
                                     destination=True) or []
        plug = None
        for candidate in plugs:
            node, _, attr = candidate.partition(".")
            if cmds.nodeType(node).startswith("animBlendNode"):
                continue
            plug = _long(node) + "." + cmds.attributeQuery(attr, node=node, longName=True)
            break
        if plug and times:
            result[plug] = (min(times), max(times))
    return result or None


# ═══════════════════════════════════════════════════════════════════════════════
#                                ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def run(selection, mode="to_opposite", target_side=None, time_range=None,
        whole_animation=False, pose_only=False, attr_filter=None, use_graph_keys=True):
    """
    One undoable mirror. Scope priority: Graph Editor keys > time range >
    whole animation (when ``whole_animation`` and the targets are keyed) > pose.
    """
    if not selection:
        cmds.warning("AnimKey: Select controls to mirror.")
        return 0
    if attr_filter is None:
        attr_filter = selected_channels(selection)
    graph_keys = selected_graph_keys() if (use_graph_keys and not pose_only) else None

    pairs, tables = plan_pairs(selection, mode=mode, target_side=target_side)
    if not pairs:
        cmds.warning("AnimKey: No mirror partners found for the selection.")
        return 0

    cmds.undoInfo(openChunk=True, chunkName="AnimKey Mirror")
    try:
        if pose_only:
            count = mirror_pose(pairs, tables, attr_filter)
        elif graph_keys:
            count = mirror_animation(pairs, tables, attr_filter=attr_filter, key_times=graph_keys)
        elif time_range:
            count = mirror_animation(pairs, tables, time_range=tuple(time_range),
                                     attr_filter=attr_filter)
        elif whole_animation and any(_is_keyed(s + "." + a) for s, _ in pairs
                                     for a in _keyable_attrs(s)):
            count = mirror_animation(pairs, tables, attr_filter=attr_filter)
        else:
            count = mirror_pose(pairs, tables, attr_filter)
    finally:
        cmds.undoInfo(closeChunk=True)

    undo_state = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        cmds.currentTime(cmds.currentTime(query=True), edit=True, update=True)
    finally:
        cmds.undoInfo(stateWithoutFlush=undo_state)
    return count


# ═══════════════════════════════════════════════════════════════════════════════
#                                 EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def set_exception(controls, attrs, rule):
    """rule: "invert", "keep" or None (remove). Applies to the pair."""
    changed = 0
    for rig_id, table in table_for(controls).items():
        exceptions = table.setdefault("exceptions", {})
        for control in controls:
            if _rig_id(control) != rig_id:
                continue
            partner = partner_of(control, table)
            for node in {control, partner or control}:
                entry = exceptions.setdefault(_key(node), {})
                for attr in attrs:
                    if rule:
                        entry[attr] = rule
                    else:
                        entry.pop(attr, None)
                    changed += 1
                if not entry:
                    exceptions.pop(_key(node), None)
        save_table(rig_id, table)
    return changed


def clear_exceptions(controls):
    for rig_id, table in table_for(controls).items():
        table["exceptions"] = {}
        save_table(rig_id, table)


def list_exceptions(controls):
    result = {}
    for _, table in table_for(controls).items():
        for key, attrs in (table.get("exceptions") or {}).items():
            result[key] = dict(attrs)
    return result
