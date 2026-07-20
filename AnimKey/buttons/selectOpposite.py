"""
    AnimKey Button: Select Opposite

    Resolves and selects opposite rig controls using AnimKey's own side-token
    resolver. The public functions are kept stable for toolbar and hotkey use.
"""

import re

import maya.cmds as cmds
import maya.mel as mel


_SIDE_SWAP = {
    "l": ("r",),
    "r": ("l",),
    "left": ("right",),
    "right": ("left",),
    "lf": ("rf", "rt"),
    "rf": ("lf",),
    "lt": ("rt",),
    "rt": ("lt", "lf"),
    "lft": ("rgt",),
    "rgt": ("lft",),
    "rg": ("lf",),
    "lhs": ("rhs",),
    "rhs": ("lhs",),
    "izq": ("der",),
    "der": ("izq",),
}

_ALIAS_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])("
    + "|".join(sorted((re.escape(k) for k in _SIDE_SWAP), key=len, reverse=True))
    + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _match_case(template, replacement):
    if template.isupper():
        return replacement.upper()
    if template.islower():
        return replacement.lower()
    if template[:1].isupper():
        return replacement[:1].upper() + replacement[1:].lower()
    return replacement


def _swap_replacements(token):
    return _SIDE_SWAP.get(token.lower(), ())


def _split_dag_path(node_name):
    parent_path, sep, leaf_name = node_name.rpartition("|")
    if sep:
        return parent_path + sep, leaf_name
    return "", leaf_name


def _split_namespace(leaf_name):
    namespace, sep, short_name = leaf_name.rpartition(":")
    if sep:
        return namespace + sep, short_name
    return "", short_name


def _append_unique(items, value):
    if value and value not in items:
        items.append(value)


def _token_swap_candidates(short_name):
    candidates = []
    matches = list(_ALIAS_PATTERN.finditer(short_name))

    for match in matches:
        source = match.group(1)
        for replacement in _swap_replacements(source):
            swapped = short_name[:match.start(1)]
            swapped += _match_case(source, replacement)
            swapped += short_name[match.end(1):]
            _append_unique(candidates, swapped)

    if len(matches) > 1:
        combined = short_name
        offset = 0
        changed = False
        for match in matches:
            source = match.group(1)
            replacements = _swap_replacements(source)
            if not replacements:
                continue
            replacement = _match_case(source, replacements[0])
            start = match.start(1) + offset
            end = match.end(1) + offset
            combined = combined[:start] + replacement + combined[end:]
            offset += len(replacement) - len(source)
            changed = True
        if changed:
            _append_unique(candidates, combined)

    return candidates


def _compact_side_candidates(short_name):
    candidates = []
    keys = sorted(_SIDE_SWAP, key=len, reverse=True)

    for key in keys:
        length = len(key)
        if len(short_name) <= length:
            continue

        prefix = short_name[:length]
        if prefix.lower() == key:
            next_char = short_name[length]
            if next_char.isupper() or next_char.isdigit():
                for replacement in _swap_replacements(prefix):
                    _append_unique(
                        candidates,
                        _match_case(prefix, replacement) + short_name[length:],
                    )

        suffix = short_name[-length:]
        if suffix.lower() == key:
            prev_char = short_name[-length - 1]
            if suffix.isupper() and (prev_char.islower() or prev_char.isdigit()):
                for replacement in _swap_replacements(suffix):
                    _append_unique(
                        candidates,
                        short_name[:-length] + _match_case(suffix, replacement),
                    )

    for index, char in enumerate(short_name):
        if char.lower() not in ("l", "r"):
            continue
        before = short_name[index - 1] if index else ""
        after = short_name[index + 1] if index + 1 < len(short_name) else ""
        left_boundary = not before or not before.isalnum()
        right_boundary = not after or not after.islower()
        compact_suffix = char.isupper() and before.islower() and right_boundary
        if not ((left_boundary and right_boundary) or compact_suffix):
            continue
        for replacement in _swap_replacements(char):
            swapped = short_name[:index]
            swapped += _match_case(char, replacement)
            swapped += short_name[index + 1:]
            _append_unique(candidates, swapped)

    return candidates


def _camel_word_candidates(short_name):
    candidates = []
    word_pairs = (("left", "right"), ("right", "left"))

    for source, replacement in word_pairs:
        pattern = re.compile(re.escape(source), re.IGNORECASE)
        for match in pattern.finditer(short_name):
            before = short_name[match.start() - 1] if match.start() else ""
            after = short_name[match.end()] if match.end() < len(short_name) else ""
            starts_token = not before or not before.isalpha() or before.islower()
            ends_token = not after or not after.islower()
            if not (starts_token and ends_token):
                continue
            swapped = short_name[:match.start()]
            swapped += _match_case(match.group(0), replacement)
            swapped += short_name[match.end():]
            _append_unique(candidates, swapped)

    return candidates


def _candidate_short_names(short_name):
    candidates = []
    for candidate in _token_swap_candidates(short_name):
        _append_unique(candidates, candidate)
    for candidate in _camel_word_candidates(short_name):
        _append_unique(candidates, candidate)
    for candidate in _compact_side_candidates(short_name):
        _append_unique(candidates, candidate)
    return candidates


def _candidate_segment_names(segment_name):
    namespace, short_name = _split_namespace(segment_name)
    candidates = []
    for candidate_short in _candidate_short_names(short_name):
        _append_unique(candidates, namespace + candidate_short)
    return candidates


def _candidate_dag_paths(node_name):
    if "|" not in node_name:
        return []

    parts = node_name.split("|")
    mirrored_parts = []
    changed = False

    for part in parts:
        if not part:
            mirrored_parts.append(part)
            continue

        segment_candidates = _candidate_segment_names(part)
        if segment_candidates:
            mirrored_parts.append(segment_candidates[0])
            changed = True
        else:
            mirrored_parts.append(part)

    if not changed:
        return []

    candidates = []
    mirrored_path = "|".join(mirrored_parts)
    _append_unique(candidates, mirrored_path)

    if mirrored_path.startswith("|"):
        _append_unique(candidates, mirrored_path.lstrip("|"))
    else:
        _append_unique(candidates, "|" + mirrored_path)

    return candidates


def _resolve_existing(candidate_name, original_name=None):
    try:
        matches = cmds.ls(candidate_name, long=True) or []
    except Exception:
        matches = []
    if not matches:
        try:
            if cmds.objExists(candidate_name):
                matches = [candidate_name]
        except Exception:
            pass
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    if original_name and "|" in original_name:
        original_parent = original_name.rpartition("|")[0]
        for match in matches:
            if match.rpartition("|")[0] == original_parent:
                return match

    if original_name:
        _, original_leaf = _split_dag_path(original_name)
        original_namespace, _ = _split_namespace(original_leaf)
        if original_namespace:
            same_namespace = []
            for match in matches:
                _, match_leaf = _split_dag_path(match)
                match_namespace, _ = _split_namespace(match_leaf)
                if match_namespace == original_namespace:
                    same_namespace.append(match)
            if len(same_namespace) == 1:
                return same_namespace[0]

    return None


def find_opposite_name(name):
    """Return the existing opposite control for a Maya node name, if any."""
    for candidate_path in _candidate_dag_paths(name):
        resolved = _resolve_existing(candidate_path, original_name=name)
        if resolved:
            return resolved

    dag_prefix, leaf_name = _split_dag_path(name)
    namespace, short_name = _split_namespace(leaf_name)

    for candidate_short in _candidate_short_names(short_name):
        possible_names = []
        if dag_prefix:
            possible_names.append(dag_prefix + namespace + candidate_short)
        if namespace:
            possible_names.append(namespace + candidate_short)
        possible_names.append(candidate_short)

        for possible_name in possible_names:
            resolved = _resolve_existing(possible_name, original_name=name)
            if resolved:
                return resolved

    return None


def _as_selectable_transform(node):
    try:
        node_type = cmds.nodeType(node)
    except Exception:
        return node

    if node_type in ("transform", "joint"):
        return node

    try:
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    except Exception:
        parents = []
    return parents[0] if parents else node


def _selected_nodes():
    try:
        selected = cmds.ls(selection=True, long=True) or []
    except Exception:
        selected = []
    if not selected:
        selected = cmds.ls(selection=True) or []

    nodes = []
    for node in selected:
        _append_unique(nodes, _as_selectable_transform(node))
    return nodes


def _find_opposites(nodes):
    opposites = []
    missing = []
    seen = set()

    for node in nodes:
        opposite = find_opposite_name(node)
        if opposite and opposite not in seen:
            opposites.append(opposite)
            seen.add(opposite)
        else:
            missing.append(node)

    return opposites, missing


def _select_opposites(add=False):
    selected_nodes = _selected_nodes()
    if not selected_nodes:
        cmds.warning("AnimKey: Please select at least one object.")
        return

    opposites, missing = _find_opposites(selected_nodes)
    if not opposites:
        cmds.warning("AnimKey: No opposite controls were found.")
        return

    if add:
        final_selection = list(selected_nodes)
        for opposite in opposites:
            _append_unique(final_selection, opposite)
    else:
        final_selection = opposites

    cmds.select(final_selection, replace=True, noExpand=True)

    if missing:
        print("AnimKey: No opposite control found for: {}".format(", ".join(missing)))


def select_opposite(*args):
    """Select opposite controls, replacing the current selection."""
    _select_opposites(add=False)


def add_select_opposite(*args):
    """Add opposite controls to the current selection."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.selectOpposite.add_select_opposite"):
        return None
    _select_opposites(add=True)


def execute(*args):
    """Select opposite controls; Shift-click adds them to the selection."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.selectOpposite.execute"):
        return None
    try:
        add_mode = bool(mel.eval("getModifiers") & 1)
    except Exception:
        add_mode = False

    _select_opposites(add=add_mode)


def get_info():
    """Return button information for the toolbar."""
    return {
        "name": "Select Opposite",
        "tooltip": "Select the opposite rig control. Shift + Click adds it to the current selection.",
        "icon": "select_opposite.svg",
        "shortcut": None,
    }
