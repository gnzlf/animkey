"""
    AnimKey Button: Isolate Selection
    
    Isolates the selected objects in the viewport.
    Based on AnimKey's isolate_master functionality.
"""

import maya.cmds as cmds

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
        cmds.isolateSelect(panel, state=1)
        cmds.isolateSelect(panel, addSelected=True)
    except Exception:
        try:
            cmds.select(valid_objects, replace=True)
            cmds.isolateSelect(panel, state=1)
            cmds.isolateSelect(panel, loadSelected=True)
        except Exception:
            return False

    try:
        cmds.isolateSelect(panel, update=True)
    except Exception:
        pass

    return True


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


def _is_shape(node):
    try:
        return bool(cmds.objectType(node, isAType="shape"))
    except Exception:
        return False


def _shape_types(node):
    try:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True) or []
    except Exception:
        shapes = []
    return [_node_type(shape) for shape in shapes]


def _has_visible_asset_shape(node):
    """True for visible prop/geo shapes, while avoiding most rig controls."""
    ignored_shape_types = {"nurbsCurve", "locator", "annotationShape"}
    return any(shape_type and shape_type not in ignored_shape_types for shape_type in _shape_types(node))


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


def _branch_objects(node):
    """Return node plus its descendants for a found external prop branch."""
    branch = []
    _append_unique(branch, node)
    try:
        descendants = cmds.listRelatives(node, allDescendents=True, fullPath=True) or []
    except Exception:
        descendants = []
    descendants.sort(key=lambda item: item.count("|"))
    for descendant in descendants:
        _append_unique(branch, descendant)
    return branch


def _append_branch(items, node):
    for branch_node in _branch_objects(node):
        _append_unique(items, branch_node)


def _is_external_parented_branch(node, rig_root, rig_namespace):
    if node == rig_root or _is_shape(node):
        return False

    node_namespace = _namespace(node)

    if node_namespace and node_namespace != rig_namespace:
        node_type = _node_type(node)
        if node_type in ("joint", "ikHandle", "effector", "constraint", "objectSet"):
            return False
        return True

    # Same-namespace/no-namespace props can happen when an imported asset is
    # parented under the rig. Include geometry-like branches, but avoid adding
    # nurbsCurve controls.
    node_type = _node_type(node)
    if node_type in ("joint", "ikHandle", "effector", "constraint", "objectSet"):
        return False

    if _has_visible_asset_shape(node):
        return True

    return False


def _is_external_constrained_target(node, rig_namespace, rig_root):
    """True for constrained props/assets driven by the rig."""
    if not node or _is_shape(node):
        return False

    if rig_root and _is_descendant_of(node, rig_root):
        return False

    node_type = _node_type(node)
    if node_type in ("joint", "ikHandle", "effector", "constraint", "objectSet"):
        return False

    node_namespace = _namespace(node)
    if node_namespace != rig_namespace:
        return True

    return _has_visible_asset_shape(node)


def _constraint_external_objects_for_nodes(nodes, rig_namespace, rig_root=None):
    """Find external transforms constrained by any of the given rig nodes."""
    extras = []
    if not nodes:
        return extras

    try:
        constraints = cmds.listConnections(
            nodes,
            source=False,
            destination=True,
            type="constraint",
        ) or []
    except Exception:
        constraints = []

    seen_constraints = set()
    for constraint in constraints:
        if constraint in seen_constraints:
            continue
        seen_constraints.add(constraint)

        possible_nodes = []
        try:
            possible_nodes.extend(cmds.listRelatives(constraint, parent=True, fullPath=True) or [])
        except Exception:
            pass
        try:
            possible_nodes.extend(cmds.listConnections(constraint, source=False, destination=True, type="transform") or [])
        except Exception:
            pass

        for possible in possible_nodes:
            try:
                long_nodes = cmds.ls(possible, long=True) or []
            except Exception:
                long_nodes = []
            for long_node in long_nodes:
                if _is_external_constrained_target(long_node, rig_namespace, rig_root):
                    _append_branch(extras, long_node)

    return extras


def _find_parented_external_objects(search_root, rig_root):
    """
    Return only top external branches parented under the selected rig.

    This avoids traversing and adding the whole rig. It looks for direct child
    branches whose namespace differs from the rig or that are visible geometry.
    """
    extras = []
    rig_namespace = _namespace(rig_root)
    try:
        descendants = cmds.listRelatives(
            search_root,
            allDescendents=True,
            fullPath=True,
            type="transform",
        ) or []
    except Exception:
        descendants = []

    descendants.sort(key=lambda item: item.count("|"))
    covered_external_branches = []
    internal_rig_nodes = [rig_root]

    for node in descendants:
        if any(_is_descendant_of(node, branch_root) for branch_root in covered_external_branches):
            continue

        if _is_external_parented_branch(node, rig_root, rig_namespace):
            _append_branch(extras, node)
            covered_external_branches.append(node)
            continue

        if _namespace(node) == rig_namespace:
            internal_rig_nodes.append(node)

    for constrained_obj in _constraint_external_objects_for_nodes(internal_rig_nodes, rig_namespace, rig_root):
        _append_unique(extras, constrained_obj)

    return extras


def get_root_node(node, down_one_level=False):
    """
    Get the root node of a hierarchy.
    
    Args:
        node: The node to find the root for
        down_one_level: If True, returns the node one level down from root
    
    Returns:
        The root node (or parent of root if down_one_level is True)
    """
    previous_node = None
    
    # Get the full name of the node to avoid duplicate name conflicts
    node = cmds.ls(node, long=True)[0]
    start_namespace = _namespace(node)
    
    while True:
        parents = cmds.listRelatives(node, parent=True, fullPath=True)
        
        if not parents:
            # If down_one_level is enabled, we want the node previous to the root node
            # If we're at the root node and down_one_level is enabled, return previous_node
            # If down_one_level is not enabled, simply return the current node
            return previous_node if down_one_level else node

        parent = parents[0]

        if _should_stop_before_parent(node, parent, start_namespace):
            return previous_node if down_one_level and previous_node else node
        
        # Save the current node before moving to the next parent node
        previous_node = node
        
        # Update the current node to the parent node for the next iteration
        node = parent


def execute(*args):
    """
    Main function to execute when button is clicked.
    Isolates selected objects in the current viewport.
    Based on AnimKey's isolate_master functionality.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.isolate.execute"):
        return None
    global down_one_level_var
    down_one_level = down_one_level_var
    include_parented = include_parented_objects_enabled()
    
    # Save current selection
    current_selection = cmds.ls(selection=True, long=True) or []
    
    # Get currently selected objects
    selected_objects = cmds.ls(selection=True, long=True) or []
    currentPanel = cmds.getPanel(withFocus=True)
    
    # Check if current panel is a model panel
    if cmds.getPanel(typeOf=currentPanel) != "modelPanel":
        cmds.warning("AnimKey: Please focus on a model panel to use isolate")
        return
    
    currentState = cmds.isolateSelect(currentPanel, query=True, state=True)
    
    # If no objects selected and isolation state is 0, exit the function
    if not selected_objects and currentState == 0:
        cmds.warning("AnimKey: No objects selected.")
        return
    
    # If no objects selected but isolation is active, deactivate it
    elif not selected_objects and currentState == 1:
        cmds.isolateSelect(currentPanel, state=0)
        _update_maya_isolate_icon(currentPanel, False)
        return
    else:
        # For each selected object, find and select the root object.
        isolate_objects = []
        selected_roots = []
        for selected_object in selected_objects:
            root_object = get_root_node(selected_object, down_one_level=down_one_level)
            if root_object:
                _append_unique(isolate_objects, root_object)
                _append_unique(selected_roots, root_object)

        if include_parented:
            for root_object in selected_roots:
                for obj in _find_parented_external_objects(root_object, root_object):
                    _append_unique(isolate_objects, obj)
        
        # Select all objects that should be included in isolate.
        if isolate_objects:
            cmds.select(isolate_objects, replace=True)
        
        # Get Maya version for icon fix
        maya_version = cmds.about(version=True)
        
        if currentState == 0:
            # Activate isolation
            _load_isolate_objects(currentPanel, isolate_objects)
            
            # Fix to activate/deactivate Maya's isolate icon (for Maya 2024/2025)
            _update_maya_isolate_icon(currentPanel, True)
        else:
            # Deactivate isolation
            cmds.isolateSelect(currentPanel, state=0)
            cmds.isolateSelect(currentPanel, removeSelected=True)
            
            # Fix to activate/deactivate Maya's isolate icon
            _update_maya_isolate_icon(currentPanel, False)
    
    # Restore original selection
    if current_selection:
        cmds.select(current_selection, replace=True)


def _update_maya_isolate_icon(panel, value):
    """
    Update Maya's isolate icon in the viewport toolbar.
    This is a fix for Maya 2024/2025 where the icon layout changed.
    
    Args:
        panel: The model panel name
        value: True to activate, False to deactivate
    """
    maya_version = cmds.about(version=True)
    
    # Icon paths for different Maya versions and panels
    icon_paths = {
        "modelPanel1": {
            "2024": "MainPane|viewPanes|modelPanel1|modelPanel1|modelEditorIconBar|flowLayout3|formLayout24|IsolateSelectedBtn",
            "2025": "MainPane|viewPanes|modelPanel1|modelPanel1|modelEditorIconBar|flowLayout3|formLayout24|IsolateSelectedBtn",
            "default": "MainPane|viewPanes|modelPanel1|modelPanel1|modelEditorIconBar|flowLayout3|formLayout25|IsolateSelectedBtn"
        },
        "modelPanel2": {
            "2024": "MainPane|viewPanes|modelPanel2|modelPanel2|modelEditorIconBar|flowLayout4|formLayout31|IsolateSelectedBtn",
            "2025": "MainPane|viewPanes|modelPanel2|modelPanel2|modelEditorIconBar|flowLayout4|formLayout31|IsolateSelectedBtn",
            "default": "MainPane|viewPanes|modelPanel2|modelPanel2|modelEditorIconBar|flowLayout4|formLayout32|IsolateSelectedBtn"
        },
        "modelPanel3": {
            "2024": "MainPane|viewPanes|modelPanel3|modelPanel3|modelEditorIconBar|flowLayout5|formLayout38|IsolateSelectedBtn",
            "2025": "MainPane|viewPanes|modelPanel3|modelPanel3|modelEditorIconBar|flowLayout5|formLayout38|IsolateSelectedBtn",
            "default": "MainPane|viewPanes|modelPanel3|modelPanel3|modelEditorIconBar|flowLayout5|formLayout39|IsolateSelectedBtn"
        },
        "modelPanel4": {
            "2024": "MainPane|viewPanes|modelPanel4|modelPanel4|modelEditorIconBar|flowLayout6|formLayout45|IsolateSelectedBtn",
            "2025": "MainPane|viewPanes|modelPanel4|modelPanel4|modelEditorIconBar|flowLayout6|formLayout45|IsolateSelectedBtn",
            "default": "MainPane|viewPanes|modelPanel4|modelPanel4|modelEditorIconBar|flowLayout6|formLayout46|IsolateSelectedBtn"
        }
    }
    
    if panel in icon_paths:
        # Get the correct icon path for this Maya version
        if maya_version in ["2024", "2025"]:
            icon_path = icon_paths[panel].get(maya_version)
        else:
            icon_path = icon_paths[panel].get("default")
        
        # Try to update the icon if it exists
        if icon_path and cmds.objExists(icon_path):
            try:
                cmds.iconTextCheckBox(icon_path, edit=True, value=value)
            except:
                # Icon path might not exist in all Maya setups, ignore error
                pass


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Isolate",
        "tooltip": "Isolate selected objects in viewport",
        "icon": "isolate.svg",
        "shortcut": "Shift+I",
    }


