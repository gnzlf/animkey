"""
    AnimKey Button: Select Hierarchy

    Select controls in the Outliner/DAG hierarchy of the current selection.
"""

import maya.cmds as cmds
import maya.mel as mel


CONTROL_SHAPE_TYPES = ("nurbsCurve",)


def _long_name(node):
    try:
        matches = cmds.ls(node, long=True) or []
        return matches[0] if matches else node
    except Exception:
        return node


def _as_transform(node):
    if not node or not cmds.objExists(node):
        return None

    try:
        if cmds.nodeType(node) == "transform":
            return _long_name(node)

        if cmds.nodeType(node) in CONTROL_SHAPE_TYPES:
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            return _long_name(parents[0]) if parents else None
    except Exception:
        return None

    return None


def _unique_transforms(nodes):
    seen = set()
    result = []

    for node in nodes or []:
        transform = _as_transform(node)
        if not transform or transform in seen:
            continue
        seen.add(transform)
        result.append(transform)

    return result


def _is_visible(node):
    try:
        return bool(cmds.getAttr(node + ".visibility"))
    except Exception:
        return False


def _is_control(node, visible_only=False):
    transform = _as_transform(node)
    if not transform:
        return False

    if visible_only and not _is_visible(transform):
        return False

    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    for shape in shapes:
        if cmds.nodeType(shape) not in CONTROL_SHAPE_TYPES:
            continue
        if visible_only and not _is_visible(shape):
            continue
        return True

    return False


def _hierarchy_nodes(selection, include_roots=True, direct_only=False):
    nodes = []

    for item in selection or []:
        root = _as_transform(item)
        if not root:
            continue

        if include_roots:
            nodes.append(root)

        if direct_only:
            children = cmds.listRelatives(root, children=True, fullPath=True) or []
        else:
            children = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []

        nodes.extend(children)

    return _unique_transforms(nodes)


def _hierarchy_controls(selection, include_roots=True, direct_only=False, visible_only=False):
    return [
        node
        for node in _hierarchy_nodes(
            selection,
            include_roots=include_roots,
            direct_only=direct_only
        )
        if _is_control(node, visible_only=visible_only)
    ]


def select_visible_nurbs_curves(*args):
    """
    Select all visible nurbsCurve controls in the hierarchy of selected objects.
    """
    selected_objects = cmds.ls(selection=True, long=True) or []

    if not selected_objects:
        cmds.warning("AnimKey: Please select at least one object.")
        return

    controls = _hierarchy_controls(
        selected_objects,
        include_roots=True,
        visible_only=True
    )

    if controls:
        cmds.select(controls, replace=True)
        cmds.inViewMessage(amg=f"Selected {len(controls)} visible control(s)", pos="midCenter", fade=True)
    else:
        cmds.warning("AnimKey: No visible NURBS controls found under the selection.")


def execute(*args):
    """
    Select the selected control plus control descendants in the Outliner hierarchy.
    """
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.select_hierarchy.execute"):
        return None
    mods = mel.eval("getModifiers")
    shift_pressed = bool(mods % 2)
    ctrl_pressed = bool((mods // 4) % 2)

    selection = cmds.ls(selection=True, long=True) or []

    if not selection:
        cmds.warning("AnimKey: Please select at least one control")
        return

    controls = _hierarchy_controls(
        selection,
        include_roots=True,
        direct_only=shift_pressed
    )

    if not controls:
        cmds.warning("AnimKey: No controls found in hierarchy")
        return

    if ctrl_pressed:
        cmds.select(controls, add=True)
    else:
        cmds.select(controls, replace=True)

    cmds.inViewMessage(amg=f"Selected {len(controls)} hierarchy control(s)", pos="midCenter", fade=True)


def select_rig_controls(*args):
    """
    Select all rig controls in the Outliner hierarchy.
    """
    selection = cmds.ls(selection=True, long=True) or []

    if not selection:
        cmds.warning("AnimKey: Please select the rig root")
        return

    controls = _hierarchy_controls(selection, include_roots=True)

    if controls:
        cmds.select(controls, replace=True)
        print(f"AnimKey: Selected {len(controls)} rig controls")
    else:
        cmds.warning("AnimKey: No rig controls found in hierarchy")


def select_animated_controls(*args):
    """
    Select only controls in the hierarchy that have animation.
    """
    selection = cmds.ls(selection=True, long=True) or []

    if not selection:
        cmds.warning("AnimKey: Please select the rig root")
        return

    controls = _hierarchy_controls(selection, include_roots=True)
    animated = []

    for control in controls:
        anim_curves = cmds.listConnections(control, type="animCurve") or []
        if anim_curves:
            animated.append(control)

    if animated:
        cmds.select(animated, replace=True)
        print(f"AnimKey: Selected {len(animated)} animated controls")
    else:
        cmds.warning("AnimKey: No animated controls found")


def get_info():
    """Return button information for the toolbar."""
    return {
        "name": "Select Hierarchy",
        "tooltip": "Select selected controls plus controls in their Outliner hierarchy",
        "icon": "select_hierarchy.svg",
        "shortcut": None,
        "modifiers": {
            "Shift": "Select only direct child controls",
            "Ctrl": "Add to current selection"
        }
    }
