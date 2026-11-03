"""
    AnimKey Button: Isolate Selection
    
    Isolates the selected objects in the viewport.
    Based on AnimKey's isolate_master functionality.
"""

import maya.cmds as cmds
import maya.mel as mel
import json

from AnimKey.mods import configMod as config


# Global variable for "down one level" option (can be extended later)
down_one_level_var = False
INCLUDE_PARENTED_SETTING = "isolate_include_parented_objects"


def include_parented_objects_enabled():
    """Return whether isolate should include objects parented under the selection."""
    return bool(config.get_setting(INCLUDE_PARENTED_SETTING, False))


def set_include_parented_objects(enabled):
    """Persist the isolate parented-objects option."""
    config.set_setting(INCLUDE_PARENTED_SETTING, bool(enabled))


def _append_unique(items, item):
    if item and item not in items:
        items.append(item)


def _get_active_model_panel():
    # Reuse the viewport resolver that matches the actual M3dView widget, even
    # when a toolbar has focus or several panels display the same camera.
    from AnimKey.buttons.followCam import _get_active_panel
    return _get_active_panel()


def _set_isolate_state(panel, enabled):
    # Maya's native command also configures/locks the editor selection
    # connection. Without it, restoring the animator's selection can replace
    # the isolated hierarchy with only the selected control.
    try:
        mel.eval("enableIsolateSelect({}, {});".format(json.dumps(panel), int(enabled)))
    except RuntimeError:
        pass
    cmds.isolateSelect(panel, state=int(enabled))


def _load_isolate_objects(panel, objects):
    """Load objects into Maya's isolate set explicitly."""
    valid_objects = []
    for obj in objects:
        if cmds.objExists(obj):
            _append_unique(valid_objects, obj)

    if not valid_objects:
        return False

    try:
        cmds.select(valid_objects, replace=True)
        _set_isolate_state(panel, True)
        # ``addSelected`` retains members from Maya's prior isolate set.  A
        # fresh AnimKey isolate must replace that set so another rig cannot
        # remain visible from an earlier isolation.
        cmds.isolateSelect(panel, loadSelected=True)
    except Exception:
        try:
            _set_isolate_state(panel, False)
        except Exception:
            pass
        return False

    try:
        cmds.isolateSelect(panel, update=True)
    except Exception:
        pass

    return bool(cmds.isolateSelect(panel, query=True, state=True))


def _node_type(node):
    try:
        return cmds.nodeType(node)
    except Exception:
        return ""


def _namespace(node):
    short_name = node.split("|")[-1]
    if ":" not in short_name:
        return ""
    return short_name.rsplit(":", 1)[0]


def _is_descendant_of(node, parent):
    return node == parent or node.startswith(parent + "|")


def _short_name(node):
    return (node or "").split("|")[-1].split(":")[-1]


def _child_short_names(node):
    try:
        children = cmds.listRelatives(node, children=True, fullPath=True, type="transform") or []
    except Exception:
        children = []
    return {_short_name(child).lower() for child in children}


def _is_rig_root_candidate(node):
    """
    Detect a likely rig root without climbing into a shared scene group.

    This is intentionally conservative: internal groups like rig_modules are
    not roots by themselves, but a transform that owns those rig markers is.
    """
    name = _short_name(node).lower()
    if name in {"rig_modules", "visible_modules", "hidden_modules"}:
        return False

    child_names = _child_short_names(node)
    rig_markers = {
        "rig_modules",
        "visible_modules",
        "hidden_modules",
        "motionsystem",
        "controls",
        "ctrls",
        "geo",
        "geometry",
        "model",
        "mesh",
    }

    if child_names.intersection(rig_markers):
        return True

    if name.endswith("_rig") or name.endswith("rig") or "_rig_" in name:
        try:
            return bool(cmds.listRelatives(node, children=True, fullPath=True, type="transform"))
        except Exception:
            return True

    return False


def _has_sibling_rig_root(parent, child):
    try:
        siblings = cmds.listRelatives(parent, children=True, fullPath=True, type="transform") or []
    except Exception:
        siblings = []

    child_short = _short_name(child)
    for sibling in siblings:
        if sibling == child or _short_name(sibling) == child_short:
            continue
        if _is_rig_root_candidate(sibling):
            return True

    return False


def _should_stop_before_parent(node, parent, start_namespace):
    parent_namespace = _namespace(parent)

    # Referenced rigs often live under a shared scene group. If the selected
    # node has a namespace, stop before climbing out to a non-rig/group node
    # or into another namespace, otherwise isolate would include sibling rigs.
    if start_namespace and parent_namespace != start_namespace:
        return True

    # Imported/no-namespace rigs can sit under a shared "characters" group.
    # Stop at the selected rig root if the parent also contains other rig roots.
    if _is_rig_root_candidate(node) and _has_sibling_rig_root(parent, node):
        return True

    return False


def _namespace_root_from_path(long_path, down_one_level=False):
    """
    Topmost ancestor that still belongs to the node's namespace.

    Pure string work on the long DAG path, so it costs no Maya calls. A
    namespaced (referenced) rig ends where its namespace ends, which also
    keeps shared scene groups and other rigs out of the isolate set.
    """
    parts = long_path.split("|")
    namespace = _namespace(parts[-1])
    index = len(parts) - 1
    while index - 1 >= 1 and _namespace(parts[index - 1]) == namespace:
        index -= 1
    if down_one_level and index < len(parts) - 1:
        index += 1
    return "|".join(parts[:index + 1])


def _namespace_top_nodes(namespace):
    """Every top-level DAG branch of a namespace (rig_grp, geo_grp, ...)."""
    if not namespace:
        return []
    try:
        nodes = cmds.ls(namespace + ":*", long=True, type="transform") or []
    except Exception:
        return []

    tops = []
    for node in nodes:
        if not isinstance(node, str):
            continue
        parts = node.split("|")
        if len(parts) > 2 and _namespace(parts[-2]) == namespace:
            continue
        tops.append(node)
    return tops


def _parent_path(node):
    if not isinstance(node, str) or "|" not in node:
        return ""
    return node.rsplit("|", 1)[0]


class _IsolateRoots(object):
    """Minimal set of DAG roots to isolate (Maya shows their children)."""

    def __init__(self, expand_namespaces=True):
        self.roots = []
        self.expand_namespaces = expand_namespaces
        self._expanded_namespaces = set()
        self._root_cache = {}

    def contains(self, node):
        return any(_is_descendant_of(node, root) for root in self.roots)

    def _resolve(self, node):
        if node not in self._root_cache:
            try:
                self._root_cache[node] = get_root_node(node)
            except Exception:
                self._root_cache[node] = None
        return self._root_cache[node]

    def add_node(self, node):
        """Add the whole rig that ``node`` belongs to."""
        if not isinstance(node, str) or not node or self.contains(node):
            return False
        return self.add_root(self._resolve(node))

    def add_root(self, root):
        if not isinstance(root, str) or not root or self.contains(root):
            return False
        self.roots = [r for r in self.roots if not _is_descendant_of(r, root)]
        self.roots.append(root)
        if self.expand_namespaces:
            self.expand_namespace(_namespace(root))
        return True

    def expand_namespace(self, namespace):
        if not namespace or namespace in self._expanded_namespaces:
            return False
        self._expanded_namespaces.add(namespace)
        changed = False
        for top in _namespace_top_nodes(namespace):
            changed = self.add_root(top) or changed
        return changed


def _skin_link_groups():
    """
    [influences + skinned geometry] per skinCluster.

    Lets a click on separated geometry find its skeleton/rig (and the other
    way around) for rigs without a namespace. Skins whose geometry lives in
    the skinCluster's own namespace are skipped: namespace expansion already
    covers them, which keeps referenced scenes fast.
    """
    try:
        skins = cmds.ls(type="skinCluster") or []
    except Exception:
        return []

    groups = []
    for skin in skins:
        if not isinstance(skin, str):
            continue
        try:
            geometry = list(cmds.skinCluster(skin, query=True, geometry=True) or [])
            skin_namespace = _namespace(skin)
            if skin_namespace and all(_namespace(geo) == skin_namespace for geo in geometry):
                continue
            influences = list(cmds.skinCluster(skin, query=True, influence=True) or [])
            members = cmds.ls(influences + geometry, long=True) or []
        except Exception:
            continue
        members = [m for m in members if isinstance(m, str)]
        if len(members) > 1:
            groups.append(members)
    return groups


def _constraint_link_groups():
    """
    [driven, driver] for every constraint that may link two rigs/props.

    One batched listConnections for the whole scene; constraints internal to
    one referenced rig are discarded by namespace before any extra call.
    """
    try:
        constraints = cmds.ls(type="constraint") or []
        if not constraints:
            return []
        pairs = cmds.listConnections(
            constraints,
            source=True,
            destination=False,
            connections=True,
            plugs=False,
            type="transform",
        ) or []
    except Exception:
        return []

    long_cache = {}

    def to_long(name):
        if name not in long_cache:
            try:
                found = cmds.ls(name, long=True) or []
            except Exception:
                found = []
            long_cache[name] = found[0] if len(found) == 1 else None
        return long_cache[name]

    groups = []
    seen = set()
    for index in range(0, len(pairs) - 1, 2):
        plug, driver = pairs[index], pairs[index + 1]
        if not isinstance(plug, str) or not isinstance(driver, str):
            continue
        if ".target[" not in plug:
            continue
        constraint = plug.split(".", 1)[0]
        key = (constraint, driver)
        if key in seen:
            continue
        seen.add(key)

        constraint_namespace = _namespace(constraint)
        if constraint_namespace and constraint_namespace == _namespace(driver):
            continue

        constraint_long = to_long(constraint)
        driver_long = to_long(driver)
        driven = _parent_path(constraint_long)
        if driven and driver_long:
            groups.append([driven, driver_long])
    return groups


def _apply_link_groups(state, groups):
    """When a group touches the isolated rigs, pull in the rest of it."""
    changed = False
    for group in groups:
        inside = [node for node in group if state.contains(node)]
        if not inside or len(inside) == len(group):
            continue
        for node in group:
            if not state.contains(node):
                changed = state.add_node(node) or changed
    return changed


def _is_attachment_parent(parent):
    """A control, joint or mesh a prop is parented to (not a plain group)."""
    if _node_type(parent) == "joint":
        return True
    try:
        return bool(cmds.listRelatives(parent, shapes=True, fullPath=True))
    except Exception:
        return False


def _climb_attachment_parents(state):
    """Selected a sword parented under a hand control -> add the character."""
    changed = False
    for root in list(state.roots):
        parent = _parent_path(root)
        if parent and _is_attachment_parent(parent):
            changed = state.add_node(parent) or changed
    return changed


def _expand_descendant_namespaces(state):
    """Props parented inside a rig also bring their other top groups."""
    try:
        nodes = cmds.ls(state.roots, dag=True, long=True, type="transform") or []
    except Exception:
        return False
    namespaces = set()
    for node in nodes:
        if isinstance(node, str):
            namespaces.add(_namespace(node))
    changed = False
    for namespace in namespaces:
        changed = state.expand_namespace(namespace) or changed
    return changed


def _collect_isolate_roots(selected_objects, include_parented=False):
    state = _IsolateRoots(expand_namespaces=not down_one_level_var)
    for selected_object in selected_objects:
        try:
            if not cmds.objectType(selected_object, isAType="dagNode"):
                continue
        except Exception:
            continue
        state.add_root(get_root_node(selected_object, down_one_level=down_one_level_var))

    if not state.roots or down_one_level_var:
        return state.roots

    skin_groups = None
    constraint_groups = None
    for _ in range(8):
        if skin_groups is None:
            skin_groups = _skin_link_groups()
        changed = _apply_link_groups(state, skin_groups)
        if include_parented:
            changed = _climb_attachment_parents(state) or changed
            changed = _expand_descendant_namespaces(state) or changed
            if constraint_groups is None:
                constraint_groups = _constraint_link_groups()
            changed = _apply_link_groups(state, constraint_groups) or changed
        if not changed:
            break
    return state.roots


def get_root_node(node, down_one_level=False):
    """
    Get the root node of a rig/hierarchy.

    Namespaced nodes resolve to the top of their namespace inside the DAG
    path (no Maya calls). Others climb until the next parent is a shared
    group that holds other rigs.
    """
    previous_node = None

    # Get the full name of the node to avoid duplicate name conflicts
    node = cmds.ls(node, long=True)[0]
    start_namespace = _namespace(node)
    if start_namespace:
        return _namespace_root_from_path(node, down_one_level)

    while True:
        parents = cmds.listRelatives(node, parent=True, fullPath=True)

        if not parents:
            return (previous_node or node) if down_one_level else node

        parent = parents[0]

        if _should_stop_before_parent(node, parent, start_namespace):
            return previous_node if down_one_level and previous_node else node

        previous_node = node
        node = parent


def execute(*args):
    """Toggle rig isolation in the active viewport, including toolbar clicks."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.isolate.execute"):
        return None
    panel = _get_active_model_panel()
    if not panel:
        cmds.warning("AnimKey: No active viewport. Click on a viewport first.")
        return False
    if cmds.isolateSelect(panel, query=True, state=True):
        _set_isolate_state(panel, False)
        _update_maya_isolate_icon(panel, False)
        return False

    current_selection = cmds.ls(selection=True, long=True) or []
    selected_objects = cmds.ls(selection=True, long=True, objectsOnly=True) or []
    if not selected_objects:
        cmds.warning("AnimKey: No objects selected.")
        return False
    try:
        isolate_objects = _collect_isolate_roots(
            selected_objects,
            include_parented=include_parented_objects_enabled(),
        )
        if not _load_isolate_objects(panel, isolate_objects):
            cmds.warning("AnimKey: Could not isolate the selected objects in this viewport.")
            return False
        _update_maya_isolate_icon(panel, True)
        return True
    finally:
        if current_selection:
            cmds.select(current_selection, replace=True)
        else:
            cmds.select(clear=True)


def _update_maya_isolate_icon(panel, value):
    """Update the actual panel's button without hardcoded layout/version paths."""
    try:
        bar = cmds.modelPanel(panel, query=True, barLayout=True)
        if not bar:
            return
        for control in cmds.lsUI(type="iconTextCheckBox", long=True) or []:
            if control.startswith(bar + "|") and control.rsplit("|", 1)[-1] == "IsolateSelectedBtn":
                cmds.iconTextCheckBox(control, edit=True, value=bool(value))
    except RuntimeError:
        pass


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Isolate",
        "tooltip": "Isolate selected objects in viewport",
        "icon": "isolate.svg",
        "shortcut": "Shift+I",
    }

