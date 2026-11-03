"""Extract the supplied Animo Temp Pivot data engine without launcher actions."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Path('C:/Users/ANIM-gonzalo/Desktop/Animo_v10.7.5/Animo_v10.7.5/Animo_Data/Animo_Launcher/temp_pivot_launcher.py')
DEST = ROOT / 'AnimKey/core/transfer_reference'
source = REFERENCE.read_text(encoding='utf-8-sig')
tree = ast.parse(source)
helpers = {'get_mobject', 'obj_exists', 'get_matrix', 'set_pivot_matrix',
           'hierarchy_depth', 'get_current_time', 'set_selection',
           'has_keyframes', 'get_key_count', 'is_attr_locked', 'run_euler_filter'}
methods = {'__init__', 'reset_state', 'matrices_equal', 'matrix_moved_significantly',
           'cache_locked_attrs', 'build_locators', 'refresh_locators', 'follow',
           'store_pivot_offset', 'apply_pivot_from_ref', 'on_timer_tick',
           'on_time_changed', 'save_relationship_to_disk', 'apply_stored_offset'}
body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in helpers]
original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LiveTempPivot')
selected = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in methods]

class NativeNamesAndLifecycle(ast.NodeTransformer):
    def visit_Constant(self, node):
        if node.value == 'Animo_PivotLoc_{}':
            return ast.copy_location(ast.Constant('AnimKey_Temp_Pivot_Locator_{}'), node)
        return node

    def visit_Assign(self, node):
        if any(ast.unparse(target) == 'self.time_job' for target in node.targets) and isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == 'cmds.scriptJob':
            return None  # AnimKey owns a single persistent, removable time callback.
        return self.generic_visit(node)

selected = [NativeNamesAndLifecycle().visit(n) for n in selected]
body.append(ast.ClassDef(name='LiveTempPivot', bases=[], keywords=[], body=selected, decorator_list=[]))
module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
header = '''"""Temp Pivot data engine from the user-provided Animo v10.7.5 source.
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

'''
(DEST / 'temp_pivot.py').write_text(header + ast.unparse(module) + '\n', encoding='utf-8')
metadata = {
    'reference_version': 'Animo v10.7.5',
    'source_sha256': hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
    'runtime_reference_dependency': False,
    'helpers': sorted(helpers), 'methods': sorted(methods),
    'interface_changes': [
        'Internal locator names use AnimKey.',
        'No launcher autorun, application event filter, timer or scriptJob installation in the kernel.',
        'AnimKey supplies the proxy name, atomic preference storage and identity-aware session.',
        'The native time-change method does not rearm a scriptJob: AnimKey owns one removable callback.',
    ],
}
(DEST / 'temp_pivot_provenance.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
print('Bundled Temp Pivot:', len(selected), 'methods,', len(helpers), 'helpers')
