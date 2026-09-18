"""
    AnimKey Button: Mirror  (Calibrated Delta Engine)

    Sistema de mirror calibrado por snapshot: detecta el plano del rig,
    aprende los ejes reales de cada par y aplica deltas locales estables.
    
    Filosofía central:
        1.  Snapshot: en T-Pose, captura la world matrix de cada control
            y calcula el plano de simetría REAL del rig.
        2.  Mirror: para cada control seleccionado...
            a. Calcula su world matrix ACTUAL.
            b. La refleja a través del plano de simetría detectado.
            c. Usa la jerarquía del rig para convertir esa world matrix
               en valores de atributos locales correctos.
            d. Aplica esos valores — NUNCA toca scale para corregir rotación.
        
    Compatible con:
        - Rigs propios / custom riggers
        - Advanced Skeleton
        - mGear
        - Cualquier rig con controles FK/IK/Blend
    
    Features:
        - Smart Snapshot  : detecta plano de simetría, jerarquía de padres,
                            rot order, offsets de cada control.
        - Mirror          : mirror one-shot sobre controles seleccionados.
        - All Mirror      : swap simétrico entre ambos lados.
        - Auto Mirror     : mirror en tiempo real mientras animas.
        - Diagnose        : imprime datos de mirror para debugging.
"""

import maya.cmds as cmds
import maya.mel as mel
import os
import json
import copy
import pickle
import zlib
import io
import builtins
import math
import itertools
import time
import hashlib

from AnimKey.core import animation_curve_transfer as curve_transfer
from AnimKey.buttons import selectOpposite as side_resolver


# ═══════════════════════════════════════════════════════════════════════════════
#                           MIRROR PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

SIDE_PATTERNS = [
    # Priority order matters: longer/more specific patterns first
    ('_lf_', '_rt_'), ('_rt_', '_lf_'),
    ('_lf',  '_rt'),  ('_rt',  '_lf'),
    ('_left_', '_right_'), ('_right_', '_left_'),
    ('_left',  '_right'),  ('_right',  '_left'),
    ('Left',   'Right'),   ('Right',   'Left'),
    ('LEFT',   'RIGHT'),   ('RIGHT',   'LEFT'),
    ('left_',  'right_'),  ('right_',  'left_'),
    ('L_',     'R_'),      ('R_',      'L_'),
    ('_L_',    '_R_'),     ('_R_',     '_L_'),
    ('_L',     '_R'),      ('_R',      '_L'),
    ('l_',     'r_'),      ('r_',      'l_'),
    ('_l_',    '_r_'),     ('_r_',     '_l_'),
    (':L_',    ':R_'),     (':R_',     ':L_'),
    ('LF_',    'RF_'),     ('RF_',     'LF_'),
]

ATTRIBUTES_TO_IGNORE = {"tag", "visibility", "lodVisibility", "intermediateObject"}
TRANSFORM_ATTRS      = ["translateX", "translateY", "translateZ",
                        "rotateX",    "rotateY",    "rotateZ",
                        "scaleX",     "scaleY",     "scaleZ"]
SCALE_ATTRS          = {"scaleX", "scaleY", "scaleZ"}
ROTATE_ATTRS         = {"rotateX", "rotateY", "rotateZ"}
POSE_TRANSFORM_ATTRS = ("translateX", "translateY", "translateZ",
                        "rotateX", "rotateY", "rotateZ")
FINGER_MAPPING_VERSION = 2

ROTATION_ORDER_MAP = {
    0: 'xyz', 1: 'yzx', 2: 'zxy',
    3: 'xzy', 4: 'yxz', 5: 'zyx'
}

SNAPSHOT_SCHEMA_VERSION = 11
MIN_SUPPORTED_SNAPSHOT_VERSION = 10
SNAPSHOT_FOLDER_NAME = "snapshots"
LEGACY_SNAPSHOT_FOLDER_NAME = "snapshots_v10"
AKMIRROR_SNAPSHOT_MAGIC = b"AKMIRROR2\x00"
SNAPSHOT_FRAME = -10000.0
SNAPSHOT_EVALUATION_PAUSES = ()
QUICK_PROFILE_CACHE_LIMIT = 32

_snapshot_cache = {}
_quick_profile_cache = {}
_missing_snapshot_cache = set()


# ═══════════════════════════════════════════════════════════════════════════════
#                           MATH — 4x4 MATRIX ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
# All matrices are row-major lists of 16 floats (Maya's xform format).
# m[0..3]   = X axis + 0
# m[4..7]   = Y axis + 0
# m[8..11]  = Z axis + 0
# m[12..15] = translation + 1

class Mat4:
    """Lightweight immutable 4×4 matrix wrapper."""

    __slots__ = ('_m',)

    def __init__(self, data=None):
        if data is None:
            self._m = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
        else:
            self._m = list(data)

    # ── raw access ──────────────────────────────────────────────────────────
    def __getitem__(self, i):   return self._m[i]
    def as_list(self):          return list(self._m)

    # ── rows / axes ─────────────────────────────────────────────────────────
    def row(self, r):           return self._m[r*4:r*4+4]
    def x_axis(self):           return self._m[0:3]
    def y_axis(self):           return self._m[4:7]
    def z_axis(self):           return self._m[8:11]
    def translation(self):      return self._m[12:15]

    # ── operators ────────────────────────────────────────────────────────────
    def __mul__(self, other):
        a, b = self._m, other._m
        out = [0.0]*16
        for r in range(4):
            for c in range(4):
                out[r*4+c] = sum(a[r*4+k]*b[k*4+c] for k in range(4))
        return Mat4(out)

    def transposed(self):
        m = self._m
        return Mat4([
            m[0],m[4],m[8], m[12],
            m[1],m[5],m[9], m[13],
            m[2],m[6],m[10],m[14],
            m[3],m[7],m[11],m[15],
        ])

    def inverse(self):
        """Full 4×4 inverse via cofactor expansion (handles non-orthogonal)."""
        m = self._m
        # 2x2 sub-determinants
        s0 = m[0]*m[5]  - m[4]*m[1]
        s1 = m[0]*m[6]  - m[4]*m[2]
        s2 = m[0]*m[7]  - m[4]*m[3]
        s3 = m[1]*m[6]  - m[5]*m[2]
        s4 = m[1]*m[7]  - m[5]*m[3]
        s5 = m[2]*m[7]  - m[6]*m[3]
        c5 = m[10]*m[15] - m[14]*m[11]
        c4 = m[9]*m[15]  - m[13]*m[11]
        c3 = m[9]*m[14]  - m[13]*m[10]
        c2 = m[8]*m[15]  - m[12]*m[11]
        c1 = m[8]*m[14]  - m[12]*m[10]
        c0 = m[8]*m[13]  - m[12]*m[9]
        det = s0*c5 - s1*c4 + s2*c3 + s3*c2 - s4*c1 + s5*c0
        if abs(det) < 1e-12:
            return Mat4()   # identity fallback
        inv = 1.0 / det
        return Mat4([
            ( m[5]*c5 - m[6]*c4 + m[7]*c3)*inv,
            (-m[1]*c5 + m[2]*c4 - m[3]*c3)*inv,
            ( m[13]*s5- m[14]*s4+ m[15]*s3)*inv,
            (-m[9]*s5 + m[10]*s4- m[11]*s3)*inv,
            (-m[4]*c5 + m[6]*c2 - m[7]*c1)*inv,
            ( m[0]*c5 - m[2]*c2 + m[3]*c1)*inv,
            (-m[12]*s5+ m[14]*s2- m[15]*s1)*inv,
            ( m[8]*s5 - m[10]*s2+ m[11]*s1)*inv,
            ( m[4]*c4 - m[5]*c2 + m[7]*c0)*inv,
            (-m[0]*c4 + m[1]*c2 - m[3]*c0)*inv,
            ( m[12]*s4- m[13]*s2+ m[15]*s0)*inv,
            (-m[8]*s4 + m[9]*s2 - m[11]*s0)*inv,
            (-m[4]*c3 + m[5]*c1 - m[6]*c0)*inv,
            ( m[0]*c3 - m[1]*c1 + m[2]*c0)*inv,
            (-m[12]*s3+ m[13]*s1- m[14]*s0)*inv,
            ( m[8]*s3 - m[9]*s1 + m[10]*s0)*inv,
        ])

    # ── static constructors ──────────────────────────────────────────────────
    @staticmethod
    def identity():
        return Mat4()

    @staticmethod
    def from_translation(t):
        m = Mat4()
        m._m[12], m._m[13], m._m[14] = t[0], t[1], t[2]
        return m

    @staticmethod
    def from_euler_xyz(rx, ry, rz, order='xyz'):
        """Build rotation matrix from Euler angles (degrees) respecting rotation order."""
        rx, ry, rz = math.radians(rx), math.radians(ry), math.radians(rz)
        cx, sx = math.cos(rx), math.sin(rx)
        cy, sy = math.cos(ry), math.sin(ry)
        cz, sz = math.cos(rz), math.sin(rz)
        Rx = Mat4([1,0,0,0,  0,cx,-sx,0,  0,sx,cx,0,  0,0,0,1])
        Ry = Mat4([cy,0,sy,0, 0,1,0,0,  -sy,0,cy,0,  0,0,0,1])
        Rz = Mat4([cz,-sz,0,0, sz,cz,0,0, 0,0,1,0,   0,0,0,1])
        ops = {'x': Rx, 'y': Ry, 'z': Rz}
        result = Mat4()
        for axis in order:
            result = result * ops[axis]
        return result

    @staticmethod
    def reflection_plane(normal, point=None):
        """
        Build a reflection matrix across the plane defined by 'normal' 
        passing through 'point' (default origin).
        M = I - 2 * n*nT  for the 3x3 rotation part, then translate.
        """
        nx, ny, nz = _normalize3(normal)
        m = [
            1-2*nx*nx, -2*nx*ny, -2*nx*nz, 0,
           -2*ny*nx, 1-2*ny*ny, -2*ny*nz, 0,
           -2*nz*nx, -2*nz*ny, 1-2*nz*nz, 0,
            0,        0,        0,         1
        ]
        result = Mat4(m)
        if point:
            # Translation: 2 * dot(point, normal) * normal
            d = 2.0 * (point[0]*nx + point[1]*ny + point[2]*nz)
            result._m[12] = d * nx
            result._m[13] = d * ny
            result._m[14] = d * nz
        return result


# ── free math helpers ────────────────────────────────────────────────────────

def _dot3(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

def _len3(v):
    return math.sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2])

def _normalize3(v):
    l = _len3(v)
    if l < 1e-10: return (1.0, 0.0, 0.0)
    return (v[0]/l, v[1]/l, v[2]/l)

def _cross3(a, b):
    return (a[1]*b[2]-a[2]*b[1],
            a[2]*b[0]-a[0]*b[2],
            a[0]*b[1]-a[1]*b[0])

def _dist3(a, b):
    return math.sqrt((a[0]-b[0])**2+(a[1]-b[1])**2+(a[2]-b[2])**2)

def _mat4_from_maya(control):
    """World matrix from Maya as Mat4."""
    try:
        matrix = cmds.getAttr(f"{control}.worldMatrix[0]")
        if isinstance(matrix, (list, tuple)) and len(matrix) == 1 and isinstance(matrix[0], (list, tuple)):
            matrix = matrix[0]
        return Mat4(matrix)
    except:
        pass
    try:
        return Mat4(cmds.xform(control, q=True, ws=True, matrix=True))
    except:
        return Mat4.identity()

def _world_pos(control):
    try:
        matrix = cmds.getAttr(f"{control}.worldMatrix[0]")
        if isinstance(matrix, (list, tuple)) and len(matrix) == 1 and isinstance(matrix[0], (list, tuple)):
            matrix = matrix[0]
        return [matrix[12], matrix[13], matrix[14]]
    except:
        pass
    try:
        return cmds.xform(control, q=True, ws=True, t=True)
    except:
        return [0.0, 0.0, 0.0]


def _cached_scalar_attrs(node, snapshot_cache=None):
    if snapshot_cache and node in snapshot_cache:
        return list(snapshot_cache[node].get("attrs", []))
    return _scalar_keyable_attrs(node)


def _cached_modifiable_attrs(node, snapshot_cache=None):
    if snapshot_cache and node in snapshot_cache:
        data = snapshot_cache[node]
        if "modifiable_attrs_set" in data:
            return data["modifiable_attrs_set"]
        return set(data.get("modifiable_attrs", data.get("attrs", [])))
    return {attr for attr in _scalar_keyable_attrs(node) if _is_modifiable(node, attr)}


def _cached_is_modifiable(node, attr, snapshot_cache=None):
    if snapshot_cache and node in snapshot_cache:
        return attr in _cached_modifiable_attrs(node, snapshot_cache)
    return _is_modifiable(node, attr)


def _cached_rest_attrs(node, snapshot_cache=None):
    if snapshot_cache and node in snapshot_cache:
        return dict(snapshot_cache[node].get("rest_attrs", {}))
    return _capture_rest_attrs(node)


def _cached_world_mat(node, snapshot_cache=None):
    if snapshot_cache and node in snapshot_cache:
        return snapshot_cache[node].get("world_mat") or _mat4_from_maya(node)
    return _mat4_from_maya(node)


def _cached_world_pos(node, snapshot_cache=None):
    if snapshot_cache and node in snapshot_cache:
        return list(snapshot_cache[node].get("world_pos", (0.0, 0.0, 0.0)))
    return _world_pos(node)

def _extract_3x3_scale(mat):
    """Extract scale from a Mat4 (length of each axis vector)."""
    return [_len3(mat.x_axis()), _len3(mat.y_axis()), _len3(mat.z_axis())]

def _normalize_rotation_in_matrix(mat):
    """Return a copy with unit-length axis vectors (removes scale)."""
    sx = _len3(mat.x_axis()) or 1.0
    sy = _len3(mat.y_axis()) or 1.0
    sz = _len3(mat.z_axis()) or 1.0
    m = mat._m[:]
    for i in range(3): m[i]   /= sx
    for i in range(3): m[4+i] /= sy
    for i in range(3): m[8+i] /= sz
    return Mat4(m)

def _matrix_to_euler(mat, order='xyz'):
    """
    Decompose rotation matrix to Euler angles (degrees) for given order.
    Handles all 6 rotation orders correctly without gimbal lock heuristics.
    """
    m = _normalize_rotation_in_matrix(mat)
    r = m._m

    def clamp(v, lo, hi): return max(lo, min(hi, v))

    if order == 'xyz':
        sy = clamp(r[2], -1.0, 1.0)
        ry = math.asin(sy)
        if abs(r[2]) < 0.9999:
            rx = math.atan2(-r[6], r[10])
            rz = math.atan2(-r[1], r[0])
        else:
            rx = math.atan2(r[9], r[5])
            rz = 0.0
    elif order == 'xzy':
        sz = clamp(-r[1], -1.0, 1.0)
        rz = math.asin(sz)
        if abs(r[1]) < 0.9999:
            rx = math.atan2(r[9], r[5])
            ry = math.atan2(r[2], r[0])
        else:
            rx = math.atan2(-r[6], r[10])
            ry = 0.0
    elif order == 'yxz':
        sx = clamp(-r[6], -1.0, 1.0)
        rx = math.asin(sx)
        if abs(r[6]) < 0.9999:
            ry = math.atan2(r[2], r[10])
            rz = math.atan2(r[4], r[5])
        else:
            ry = math.atan2(-r[8], r[0])
            rz = 0.0
    elif order == 'yzx':
        sx = clamp(r[4], -1.0, 1.0)
        rz = math.asin(sx)  # note: yzx
        if abs(r[4]) < 0.9999:
            rx = math.atan2(-r[6], r[5])
            ry = math.atan2(-r[8], r[0])
        else:
            rx = 0.0
            ry = math.atan2(r[9], r[10])
    elif order == 'zxy':
        sx = clamp(r[9], -1.0, 1.0)
        rx = math.asin(sx)
        if abs(r[9]) < 0.9999:
            ry = math.atan2(-r[8], r[10])
            rz = math.atan2(-r[1], r[5])
        else:
            rz = math.atan2(r[4], r[0])
            ry = 0.0
    elif order == 'zyx':
        sy = clamp(-r[8], -1.0, 1.0)
        ry = math.asin(sy)
        if abs(r[8]) < 0.9999:
            rx = math.atan2(r[9], r[10])
            rz = math.atan2(r[4], r[0])
        else:
            rx = 0.0
            rz = math.atan2(-r[1], r[5])
    else:  # fallback xyz
        sy = clamp(r[2], -1.0, 1.0)
        ry = math.asin(sy)
        rx = math.atan2(-r[6], r[10]) if abs(r[2]) < 0.9999 else math.atan2(r[9], r[5])
        rz = math.atan2(-r[1], r[0])  if abs(r[2]) < 0.9999 else 0.0

    return (math.degrees(rx), math.degrees(ry), math.degrees(rz))


# ═══════════════════════════════════════════════════════════════════════════════
#                      SYMMETRY PLANE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

class SymmetryPlane:
    """
    Represents the mirror plane of a rig.
    Stores: normal (unit vector) + a point on the plane.
    Default: YZ plane (normal=[1,0,0], point=[0,0,0]).
    """
    def __init__(self, normal=(1,0,0), point=(0,0,0)):
        self.normal = tuple(_normalize3(normal))
        self.point  = tuple(point)

    def reflect_mat4(self, mat):
        """Reflect a world matrix through this plane."""
        R = Mat4.reflection_plane(self.normal, self.point)
        # Reflect position
        pos = mat.translation()
        d   = 2.0*(_dot3(pos, self.normal) - _dot3(self.point, self.normal))
        new_pos = [pos[i] - d*self.normal[i] for i in range(3)]
        # Reflect rotation (only the 3x3 part)
        # R_mirror = Reflect * R_orig * Reflect  (conjugate)
        # For a pure reflection matrix R_refl: R_mirror = R_refl * R_orig * R_refl
        # Since reflection is its own inverse: R_refl^-1 = R_refl
        m = mat._m[:]
        # Reflect each axis vector
        def refl(v):
            d2 = 2.0*_dot3(v, self.normal)
            return [v[i] - d2*self.normal[i] for i in range(3)]

        xa = refl(mat.x_axis())
        ya = refl(mat.y_axis())
        za = refl(mat.z_axis())

        # The reflection of a rotation matrix through a plane produces a matrix
        # with determinant -1 (it's a reflective orientation). We need to fix this
        # by negating one axis to restore det=+1 (proper rotation).
        # We negate the axis most aligned with the mirror normal.
        axes = [xa, ya, za]
        dots = [abs(_dot3(a, self.normal)) for a in axes]
        idx  = dots.index(max(dots))
        axes[idx] = [-v for v in axes[idx]]

        return Mat4([
            axes[0][0], axes[0][1], axes[0][2], 0,
            axes[1][0], axes[1][1], axes[1][2], 0,
            axes[2][0], axes[2][1], axes[2][2], 0,
            new_pos[0], new_pos[1], new_pos[2], 1
        ])

    def to_dict(self):
        return {'normal': list(self.normal), 'point': list(self.point)}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get('normal',[1,0,0]), d.get('point',[0,0,0]))

    @classmethod
    def detect_from_pairs(cls, pairs):
        """
        Given a list of (L_pos, R_pos) world positions, fit the symmetry plane
        as the robust perpendicular bisector plane of all pairs.
        """
        if not pairs:
            return cls()

        midpoints  = []
        diff_vecs  = []
        weights    = []
        for lp, rp in pairs:
            dv = [rp[i]-lp[i] for i in range(3)]
            w = _len3(dv)
            if w < 1e-5:
                continue
            midpoints.append([(lp[i]+rp[i])*0.5 for i in range(3)])
            diff_vecs.append(dv)
            weights.append(w)

        if not diff_vecs:
            return cls()

        # Normal = weighted average of left-right vectors. Longer limb pairs
        # are more reliable than tiny facial/finger offsets for finding the
        # body-cutting plane.
        normal = [0.0, 0.0, 0.0]
        for dv, w in zip(diff_vecs, weights):
            ndv = _normalize3(dv)
            for i in range(3):
                normal[i] += ndv[i] * w
        normal = _normalize3(normal)

        # Snap only a plane that is already axis-aligned. A looser threshold
        # turns a rotated character back into a world-axis mirror plane.
        abs_n = [abs(normal[i]) for i in range(3)]
        if max(abs_n) > 0.9999:
            dominant = abs_n.index(max(abs_n))
            snapped  = [0.0, 0.0, 0.0]
            snapped[dominant] = 1.0 if normal[dominant] > 0 else -1.0
            normal = tuple(snapped)

        offsets = sorted(_dot3(mp, normal) for mp in midpoints)
        mid = len(offsets) // 2
        if len(offsets) % 2:
            plane_offset = offsets[mid]
        else:
            plane_offset = (offsets[mid - 1] + offsets[mid]) * 0.5
        point = [normal[i] * plane_offset for i in range(3)]

        return cls(normal, point)


# ═══════════════════════════════════════════════════════════════════════════════
#                      OPPOSITE CONTROL DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def find_opposite_by_name(name):
    """Find the opposite control, preferring the same namespace or rig branch."""
    try:
        resolved = side_resolver.find_opposite_name(name)
        if resolved and cmds.objExists(resolved):
            return resolved
    except Exception:
        pass

    leaf = name.rsplit('|', 1)[-1]
    namespace, _, ctrl_name = leaf.rpartition(':')
    for pat, opp_pat in SIDE_PATTERNS:
        if pat in ctrl_name:
            new_name = ctrl_name.replace(pat, opp_pat, 1)
            new_leaf = f'{namespace}:{new_name}' if namespace else new_name
            candidates = []

            parent_path = _dag_parent_path(name)
            if parent_path:
                candidates.append(f"{parent_path}|{new_leaf}")
            candidates.append(new_leaf)

            scope_kind, scope_path, _ = _scope_for_control(name)
            if scope_kind == "root":
                scoped = _find_transform_named_under(scope_path, new_leaf)
                if scoped:
                    candidates.insert(0, scoped)

            try:
                candidates.extend(cmds.ls(f"*{new_leaf}", type="transform", long=True) or [])
            except:
                pass

            for full in candidates:
                if full and cmds.objExists(full):
                    return full
    return None


def _build_snapshot_control_index(controls):
    index = {}
    for control in controls or []:
        short_name = _strip_namespace(control)
        index.setdefault(short_name, [])
        if control not in index[short_name]:
            index[short_name].append(control)
    return index


def _find_opposite_from_index(control, control_index):
    leaf = control.rsplit('|', 1)[-1]
    namespace, _, ctrl_name = leaf.rpartition(':')
    parent_path = _dag_parent_path(control)

    resolved = find_opposite_by_name(control)
    if resolved and cmds.objExists(resolved):
        resolved_long = _long_name(resolved)
        for indexed in control_index.get(_strip_namespace(resolved), []):
            if _long_name(indexed) == resolved_long:
                return resolved_long

    for pat, opp_pat in SIDE_PATTERNS:
        if pat not in ctrl_name:
            continue
        opp_short = ctrl_name.replace(pat, opp_pat, 1)
        matches = list(control_index.get(opp_short, []))
        if namespace:
            matches = [match for match in matches if _namespace(match) == namespace]
        if parent_path:
            same_parent = [match for match in matches if _dag_parent_path(match) == parent_path]
            if len(same_parent) == 1:
                return same_parent[0]
        if len(matches) == 1:
            return matches[0]

        fallback = find_opposite_by_name(control)
        if fallback and cmds.objExists(fallback):
            return fallback
    return None


def find_opposite_smart(control, sym_plane=None):
    """
    Find opposite using name patterns + world-space position verification.
    If sym_plane is provided, uses it for position verification.
    """
    candidate = find_opposite_by_name(control)
    if not candidate:
        return None

    try:
        src_pos = _world_pos(control)
        opp_pos = _world_pos(candidate)

        if sym_plane:
            # Reflect source through detected plane
            d       = _dot3(src_pos, sym_plane.normal) - _dot3(sym_plane.point, sym_plane.normal)
            exp_pos = [src_pos[i] - 2*d*sym_plane.normal[i] for i in range(3)]
        else:
            # Default: reflect across X=0
            exp_pos = [-src_pos[0], src_pos[1], src_pos[2]]

        dist = _dist3(opp_pos, exp_pos)
        # Allow up to 10% of rig scale as tolerance
        rig_scale = max(_dist3(src_pos,[0,0,0]), 1.0)
        if dist < rig_scale * 0.15:
            return candidate
        # Soft fallback: just require X signs are opposite
        if abs(opp_pos[0] + src_pos[0]) < 1.0:
            return candidate
    except:
        pass

    return candidate   # Name match is still returned (user knows their rig)


def _side_from_plane(control, sym_plane, tolerance=0.0001):
    if not sym_plane:
        return None
    try:
        position = _world_pos(control)
        distance = (
            _dot3(position, sym_plane.normal)
            - _dot3(sym_plane.point, sym_plane.normal)
        )
    except Exception:
        return None
    if abs(distance) <= tolerance:
        return None
    # Snapshot planes are oriented from the named left side toward the right.
    return "left" if distance < 0.0 else "right"


def _ordered_pair_positions(left_or_right, opposite, control_cache=None):
    """Return a deterministic (left_position, right_position) tuple."""
    source_side = side_resolver.detect_side(left_or_right)
    opposite_side = side_resolver.detect_side(opposite)
    source_position = _cached_world_pos(left_or_right, control_cache)
    opposite_position = _cached_world_pos(opposite, control_cache)
    if source_side == "right" and opposite_side == "left":
        return opposite_position, source_position
    return source_position, opposite_position


def _control_pair_signature(control, control_cache=None):
    data = (control_cache or {}).get(control, {})
    attrs = data.get("attrs")
    if attrs is None:
        attrs = _scalar_keyable_attrs(control)
    transform_attrs = tuple(attr for attr in POSE_TRANSFORM_ATTRS if attr in attrs)
    custom_attrs = tuple(sorted(attr for attr in attrs if attr not in TRANSFORM_ATTRS))
    try:
        node_type = cmds.nodeType(control)
    except Exception:
        node_type = "transform"
    return node_type, transform_attrs, custom_attrs


def _pair_signatures_compatible(left, right, control_cache=None):
    left_type, left_transforms, left_custom = _control_pair_signature(left, control_cache)
    right_type, right_transforms, right_custom = _control_pair_signature(right, control_cache)
    if left_type != right_type or left_transforms != right_transforms:
        return False
    if not left_custom and not right_custom:
        return True
    shared = len(set(left_custom).intersection(right_custom))
    total = max(len(set(left_custom).union(right_custom)), 1)
    return (float(shared) / float(total)) >= 0.75


def _candidate_geometry_planes(controls):
    point = [0.0, 0.0, 0.0]
    axes = [(-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)]
    anchor = _common_dag_ancestor(controls)
    if anchor and cmds.objExists(anchor):
        anchor_matrix = _mat4_from_maya(anchor)
        point = anchor_matrix.translation()
        axes = [
            tuple(-value for value in _normalize3(anchor_matrix.x_axis())),
            tuple(-value for value in _normalize3(anchor_matrix.y_axis())),
            tuple(-value for value in _normalize3(anchor_matrix.z_axis())),
        ]
    return [SymmetryPlane(axis, point) for axis in axes]


def _reflected_position(position, sym_plane):
    distance = (
        _dot3(position, sym_plane.normal)
        - _dot3(sym_plane.point, sym_plane.normal)
    )
    return [
        position[index] - (2.0 * distance * sym_plane.normal[index])
        for index in range(3)
    ]


def _control_is_central(control, sym_plane, controls=None, control_cache=None):
    leaf = _strip_namespace(control)
    lower_leaf = leaf.lower()
    center_suffixes = ("_m", "_c", "_mid", "_middle", "_center", "_centre")
    center_prefixes = ("m_", "c_", "mid_", "center_", "centre_")
    center_words = {
        "m", "c", "mid", "middle", "center", "centre",
        "main", "global", "master", "root", "world",
    }
    name_words = set(lower_leaf.replace("-", "_").split("_"))
    if (lower_leaf in {"main", "global", "master", "root", "world"}
            or lower_leaf.endswith(center_suffixes)
            or lower_leaf.startswith(center_prefixes)
            or name_words.intersection(center_words)):
        return True
    try:
        named_side = side_resolver.detect_side(control)
    except Exception:
        named_side = None
    if not named_side and any(token in lower_leaf for token in (
        "teeth", "tongue", "jaw",
    )):
        return True
    if not sym_plane:
        return False
    position = _cached_world_pos(control, control_cache)
    distance = abs(
        _dot3(position, sym_plane.normal)
        - _dot3(sym_plane.point, sym_plane.normal)
    )
    positions = [
        _cached_world_pos(item, control_cache)
        for item in (controls or [control]) if cmds.objExists(item)
    ]
    if positions:
        extents = []
        for axis in range(3):
            values = [item[axis] for item in positions]
            extents.append(max(values) - min(values))
        rig_extent = max(extents + [1.0])
    else:
        rig_extent = 1.0
    return distance <= max(0.001, rig_extent * 0.01)


def _geometry_pair_solution(controls, control_cache, occupied=None, preferred_plane=None):
    """Conservatively pair unnamed controls by mutual reflected proximity."""
    occupied = set(occupied or ())
    available = [
        control for control in controls or []
        if control not in occupied and cmds.objExists(control)
    ]
    if len(available) < 2:
        return {}, preferred_plane, {}

    positions = {
        control: _cached_world_pos(control, control_cache)
        for control in available
    }
    all_positions = list(positions.values())
    bounds = []
    for axis in range(3):
        values = [position[axis] for position in all_positions]
        bounds.append(max(values) - min(values))
    rig_extent = max(bounds + [1.0])
    pair_tolerance = max(0.001, rig_extent * 0.035)
    center_tolerance = max(0.0001, pair_tolerance * 0.2)

    def solve(plane):
        nearest = {}
        for source in available:
            source_distance = (
                _dot3(positions[source], plane.normal)
                - _dot3(plane.point, plane.normal)
            )
            if abs(source_distance) <= center_tolerance:
                continue
            expected = _reflected_position(positions[source], plane)
            ranked = []
            for target in available:
                if target == source:
                    continue
                target_distance = (
                    _dot3(positions[target], plane.normal)
                    - _dot3(plane.point, plane.normal)
                )
                if source_distance * target_distance >= 0.0:
                    continue
                if not _pair_signatures_compatible(source, target, control_cache):
                    continue
                ranked.append((_dist3(expected, positions[target]), target))
            if ranked:
                ranked.sort(key=lambda item: item[0])
                nearest[source] = ranked

        pairs = {}
        confidences = {}
        errors = []
        for source, ranked in nearest.items():
            distance, target = ranked[0]
            reverse = nearest.get(target)
            if not reverse or reverse[0][1] != source or distance > pair_tolerance:
                continue
            second_distance = ranked[1][0] if len(ranked) > 1 else None
            if (second_distance is not None and second_distance <= pair_tolerance
                    and distance > second_distance * 0.65):
                continue
            pairs[source] = target
            confidence = max(0.0, 1.0 - (distance / pair_tolerance))
            if second_distance:
                confidence *= max(0.0, min(1.0, 1.0 - (distance / second_distance)))
            confidences[source] = max(0.5, confidence)
            errors.append(distance)
        unique_pairs = len({tuple(sorted((a, b))) for a, b in pairs.items()})
        average_error = (sum(errors) / len(errors)) if errors else float("inf")
        return pairs, confidences, unique_pairs, average_error

    planes = [preferred_plane] if preferred_plane else _candidate_geometry_planes(available)
    best = None
    for plane in planes:
        pairs, confidences, pair_count, average_error = solve(plane)
        candidate = (pair_count, -average_error, plane, pairs, confidences)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if not best or best[0] <= 0:
        return {}, preferred_plane, {}
    return best[3], best[2], best[4]


def is_central_control(control, sym_plane=None):
    """True if the control has no opposite (midline control)."""
    opposite = find_opposite_by_name(control)
    if opposite and cmds.objExists(opposite):
        return False
    # Position check: near the symmetry plane
    try:
        pos = _world_pos(control)
        if sym_plane:
            d = abs(_dot3(pos, sym_plane.normal) - _dot3(sym_plane.point, sym_plane.normal))
            return d < 0.5
        return abs(pos[0]) < 0.5
    except:
        return True


# ═══════════════════════════════════════════════════════════════════════════════
#                      PARENT MATRIX & LOCAL SPACE UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _strip_namespace(node):
    leaf = node.rsplit('|', 1)[-1]
    return leaf.rsplit(':', 1)[-1]


def _namespace(node):
    leaf = node.rsplit('|', 1)[-1]
    return leaf.rpartition(':')[0] if ':' in leaf else ''


def _dag_parent_path(node):
    if '|' not in node:
        return ''
    return node.rsplit('|', 1)[0]


def _resolve_control_like(reference, short_name):
    ns = _namespace(reference)
    leaf = f"{ns}:{short_name}" if ns else short_name
    parent_path = _dag_parent_path(reference)
    candidates = []
    if parent_path:
        candidates.append(f"{parent_path}|{leaf}")
    scope_kind, scope_path, _ = _scope_for_control(reference)
    if scope_kind == "root":
        scoped = _find_transform_named_under(scope_path, leaf)
        if scoped:
            candidates.append(scoped)
    candidates.append(leaf)
    try:
        candidates.extend(cmds.ls(f"*{leaf}", type="transform", long=True) or [])
    except:
        pass
    for candidate in candidates:
        if candidate and cmds.objExists(candidate):
            return candidate
    return leaf


def _has_animkey_control_shape(node):
    try:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        for shape in shapes:
            if cmds.nodeType(shape) in ("nurbsCurve", "locator"):
                return True
    except:
        pass
    return False


def _has_keyable_transform_attrs(node):
    try:
        return bool(cmds.listAttr(node, keyable=True) or [])
    except:
        return False


def _control_candidates_under(root):
    candidates = []
    if not root or not cmds.objExists(root):
        return candidates
    try:
        descendants = cmds.listRelatives(
            root, allDescendents=True, fullPath=True, type="transform"
        ) or []
    except:
        descendants = []
    for node in [root] + descendants:
        if (_has_animkey_control_shape(node) and _has_keyable_transform_attrs(node)):
            candidates.append(node)
    return candidates


def _control_candidates_in_namespace(namespace):
    if not namespace:
        return []
    candidates = []
    try:
        transforms = cmds.ls(f"{namespace}:*", type="transform", long=True) or []
    except:
        transforms = []
    for node in transforms:
        if (_has_animkey_control_shape(node) and _has_keyable_transform_attrs(node)):
            candidates.append(node)
    return candidates


def _expand_to_candidate_controls(selection):
    """Expand a rough rig/root/control selection into likely animation controls."""
    if not selection:
        return []

    candidates = []
    explicit = []
    scanned_namespaces = set()
    scanned_roots = set()
    scanned_items = set()

    for item in selection:
        if not item or not cmds.objExists(item):
            continue
        explicit.append(item)
        namespace = _namespace(item)
        if namespace and namespace not in scanned_namespaces:
            candidates.extend(_control_candidates_in_namespace(namespace))
            scanned_namespaces.add(namespace)

        try:
            scope_kind, scope_path, _ = _scope_for_control(item)
        except Exception:
            scope_kind, scope_path = None, None
        if scope_kind == "root" and scope_path not in scanned_roots:
            candidates.extend(_control_candidates_under(scope_path))
            scanned_roots.add(scope_path)

        if (not namespace and scope_kind != "root"
                and item not in scanned_items):
            candidates.extend(_control_candidates_under(item))
            scanned_items.add(item)
        if _has_keyable_transform_attrs(item):
            candidates.append(item)

    if not candidates:
        candidates = explicit

    return list(dict.fromkeys(c for c in candidates if cmds.objExists(c)))


def _attr_exists(node, attr):
    try:
        return cmds.objExists(f"{node}.{attr}")
    except:
        return False


def _safe_scalar_attr(node, attr, default=0.0):
    try:
        value = cmds.getAttr(f"{node}.{attr}")
        if isinstance(value, (list, tuple)):
            if len(value) == 1:
                value = value[0]
                if isinstance(value, (list, tuple)) and len(value) == 1:
                    value = value[0]
            else:
                return default
        return float(value)
    except:
        return default


def _set_scalar_attr(node, attr, value):
    if not _attr_exists(node, attr) or not _is_modifiable(node, attr):
        return False
    try:
        if cmds.attributeQuery(attr, node=node, minExists=True):
            value = max(value, cmds.attributeQuery(attr, node=node, minimum=True)[0])
        if cmds.attributeQuery(attr, node=node, maxExists=True):
            value = min(value, cmds.attributeQuery(attr, node=node, maximum=True)[0])
        cmds.setAttr(f"{node}.{attr}", value)
        return True
    except:
        return False


def _query_attr_limits(node, attr):
    min_value = None
    max_value = None
    try:
        if cmds.attributeQuery(attr, node=node, minExists=True):
            min_value = cmds.attributeQuery(attr, node=node, minimum=True)[0]
    except:
        min_value = None
    try:
        if cmds.attributeQuery(attr, node=node, maxExists=True):
            max_value = cmds.attributeQuery(attr, node=node, maximum=True)[0]
    except:
        max_value = None
    return min_value, max_value


def _cached_attr_limits(node, attr, snapshot_cache=None):
    if snapshot_cache and node in snapshot_cache:
        data = snapshot_cache[node]
        limits = data.setdefault("attr_limits", {})
        if attr not in limits:
            limits[attr] = _query_attr_limits(node, attr)
        return limits[attr]
    return _query_attr_limits(node, attr)


def _set_scalar_attr_cached(node, attr, value, snapshot_cache=None):
    if snapshot_cache and node in snapshot_cache:
        if not _cached_is_modifiable(node, attr, snapshot_cache):
            return False
    elif not _attr_exists(node, attr) or not _is_modifiable(node, attr):
        return False
    try:
        min_value, max_value = _cached_attr_limits(node, attr, snapshot_cache)
        if min_value is not None:
            value = max(value, min_value)
        if max_value is not None:
            value = min(value, max_value)
        cmds.setAttr(f"{node}.{attr}", value)
        return True
    except:
        return False


def _attrs_allow_scale(attrs_filter):
    return bool(attrs_filter and any(attr in SCALE_ATTRS for attr in attrs_filter))


def _attrs_to_apply(local_vals, attrs_filter=None, include_scale=False):
    # compute_mirror_values() has already applied the source/target channel
    # filter. Its keys are destination channels and may intentionally differ
    # from the selected source channel (for example eyebrow tx -> opposite ty).
    # Reusing the source filter here silently discarded those remapped values.
    attrs = list(local_vals.keys())
    if not include_scale and not _attrs_allow_scale(attrs_filter):
        attrs = [attr for attr in attrs if attr not in SCALE_ATTRS]
    return attrs


def _open_animkey_undo_chunk(name):
    try:
        if not cmds.undoInfo(q=True, state=True):
            cmds.undoInfo(state=True)
    except:
        pass
    try:
        cmds.undoInfo(openChunk=True, chunkName=name)
    except:
        cmds.undoInfo(openChunk=True)


def _close_animkey_undo_chunk():
    try:
        cmds.undoInfo(closeChunk=True)
    except:
        pass


def _dominant_mirror_axis(sym_plane):
    normal = sym_plane.normal if sym_plane else (1.0, 0.0, 0.0)
    return max(range(3), key=lambda i: abs(normal[i]))


def _axis_index_from_attr(attr):
    if attr.endswith("X"):
        return 0
    if attr.endswith("Y"):
        return 1
    if attr.endswith("Z"):
        return 2
    return None


def _default_attr_sign(attr, sym_plane):
    axis = _axis_index_from_attr(attr)
    mirror_axis = _dominant_mirror_axis(sym_plane)
    if attr.startswith("translate"):
        return -1.0 if axis == mirror_axis else 1.0
    if attr.startswith("rotate"):
        return 1.0 if axis == mirror_axis else -1.0
    return 1.0


def _scalar_keyable_attrs(node):
    attrs = []
    for attr in (cmds.listAttr(node, keyable=True) or []):
        if attr in ATTRIBUTES_TO_IGNORE:
            continue
        try:
            value = cmds.getAttr(f"{node}.{attr}")
            if isinstance(value, (list, tuple)):
                if len(value) != 1:
                    continue
                value = value[0]
                if isinstance(value, (list, tuple)) and len(value) != 1:
                    continue
            float(value[0] if isinstance(value, (list, tuple)) else value)
            attrs.append(attr)
        except:
            pass
    return attrs


def _snapshot_scalar_attr_data(node):
    attrs = []
    rest_attrs = {}
    for attr in (cmds.listAttr(node, keyable=True) or []):
        if attr in ATTRIBUTES_TO_IGNORE:
            continue
        try:
            value = cmds.getAttr(f"{node}.{attr}")
            if isinstance(value, (list, tuple)):
                if len(value) != 1:
                    continue
                value = value[0]
                if isinstance(value, (list, tuple)):
                    if len(value) != 1:
                        continue
                    value = value[0]
            value = float(value)
            attrs.append(attr)
            rest_attrs[attr] = value
        except:
            pass
    return attrs, rest_attrs


def _attr_candidates(source_attr, target_attrs):
    if source_attr.startswith("translate"):
        return [a for a in ("translateX", "translateY", "translateZ") if a in target_attrs]
    if source_attr.startswith("rotate"):
        return [a for a in ("rotateX", "rotateY", "rotateZ") if a in target_attrs]
    if source_attr.startswith("scale"):
        return [a for a in ("scaleX", "scaleY", "scaleZ") if a in target_attrs]
    return [source_attr] if source_attr in target_attrs else []


def _calibration_delta(attr):
    if attr.startswith("translate"):
        return 1.0
    if attr.startswith("rotate"):
        return 10.0
    if attr.startswith("scale"):
        return 0.1
    return 0.25


def _shortest_angle_delta(target, source):
    return ((target - source + 180.0) % 360.0) - 180.0


def _closest_angle_to(value, reference):
    return reference + _shortest_angle_delta(value, reference)


def _matrix_error(a, b):
    err = _dist3(a.translation(), b.translation()) * 10.0
    err += _dist3(_normalize3(a.x_axis()), _normalize3(b.x_axis()))
    err += _dist3(_normalize3(a.y_axis()), _normalize3(b.y_axis()))
    err += _dist3(_normalize3(a.z_axis()), _normalize3(b.z_axis()))
    return err


def _sub3(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _matrix_delta_error(base_a, test_a, base_b, test_b):
    """Compare what changed from base->test, ignoring absolute rest mismatch."""
    err = _dist3(_sub3(test_a.translation(), base_a.translation()),
                 _sub3(test_b.translation(), base_b.translation())) * 10.0
    for axis_getter in (Mat4.x_axis, Mat4.y_axis, Mat4.z_axis):
        da = _sub3(_normalize3(axis_getter(test_a)), _normalize3(axis_getter(base_a)))
        db = _sub3(_normalize3(axis_getter(test_b)), _normalize3(axis_getter(base_b)))
        err += _dist3(da, db)
    return err


def _capture_rest_attrs(node):
    return {attr: _safe_scalar_attr(node, attr) for attr in _scalar_keyable_attrs(node)}


def _build_snapshot_control_cache(controls):
    cache = {}
    for control in controls or []:
        if not cmds.objExists(control):
            continue
        attrs, rest_attrs = _snapshot_scalar_attr_data(control)
        modifiable_attrs = [attr for attr in attrs if _is_modifiable(control, attr)]
        world_mat = _mat4_from_maya(control)
        try:
            rotate_order_idx = cmds.getAttr(f'{control}.rotateOrder')
        except:
            rotate_order_idx = 0
        cache[control] = {
            "short": _strip_namespace(control),
            "attrs": attrs,
            "modifiable_attrs": modifiable_attrs,
            "attrs_set": set(attrs),
            "modifiable_attrs_set": set(modifiable_attrs),
            "rest_attrs": rest_attrs,
            "world_mat": world_mat,
            "world_pos": world_mat.translation(),
            "rotate_order_idx": rotate_order_idx,
            "rotate_order": ROTATION_ORDER_MAP.get(rotate_order_idx, 'xyz'),
        }
    return cache


def _control_structure_fingerprint(control, snapshot_cache=None):
    data = (snapshot_cache or {}).get(control, {})
    attrs = data.get("attrs")
    if attrs is None:
        try:
            attrs = [
                attr for attr in (cmds.listAttr(control, keyable=True) or [])
                if attr not in ATTRIBUTES_TO_IGNORE
            ]
        except Exception:
            attrs = []
    attrs = list(attrs)
    try:
        node_type = cmds.nodeType(control)
    except Exception:
        node_type = "unknown"
    try:
        rotate_order = int(cmds.getAttr(f"{control}.rotateOrder"))
    except Exception:
        rotate_order = 0
    payload = repr((
        node_type,
        _strip_namespace(_dag_parent_path(_long_name(control))),
        rotate_order,
        tuple(attrs),
    )).encode("utf-8", "replace")
    return hashlib.sha1(payload).hexdigest()


def _snapshot_rest_values(control_cache):
    values = {}
    for control, data in (control_cache or {}).items():
        for attr, value in data.get("rest_attrs", {}).items():
            values[f'{control}.{attr}'] = value
    return values


def _matrix_attr_values(node, attr):
    try:
        value = cmds.getAttr(f"{node}.{attr}")
        if isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        if isinstance(value, (list, tuple)) and len(value) == 16:
            return [float(item) for item in value]
    except Exception:
        pass
    return Mat4.identity().as_list()


def _vector_attr_values(node, attr):
    try:
        value = cmds.getAttr(f"{node}.{attr}")
        if isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return [float(value[0]), float(value[1]), float(value[2])]
    except Exception:
        pass
    return [0.0, 0.0, 0.0]


def _snapshot_transform_metadata(control, control_cache=None):
    cache_data = (control_cache or {}).get(control, {})
    try:
        parent_world = _get_parent_world_matrix(control).as_list()
    except Exception:
        parent_world = Mat4.identity().as_list()
    return {
        "rest_local_mat": _matrix_attr_values(control, "matrix"),
        "rest_parent_world_mat": parent_world,
        "offset_parent_mat": _matrix_attr_values(control, "offsetParentMatrix"),
        "rotate_order_index": int(cache_data.get("rotate_order_idx", 0)),
        "joint_orient": _vector_attr_values(control, "jointOrient"),
        "rotate_axis": _vector_attr_values(control, "rotateAxis"),
        "modifiable_attrs": list(cache_data.get("modifiable_attrs", [])),
        "attr_limits": {
            attr: list(values)
            for attr, values in cache_data.get("attr_limits", {}).items()
        },
    }


def _restore_attrs(node, values, snapshot_cache=None):
    for attr, value in values.items():
        _set_scalar_attr_cached(node, attr, value, snapshot_cache)


def _restore_attr(node, attr, value, snapshot_cache=None):
    _set_scalar_attr_cached(node, attr, value, snapshot_cache)


def _default_attr_map(source, target, sym_plane, snapshot_cache=None):
    target_attrs = set(_cached_scalar_attrs(target, snapshot_cache))
    attr_map = {}
    for attr in _cached_scalar_attrs(source, snapshot_cache):
        if attr in target_attrs:
            attr_map[attr] = {"target": attr, "mult": _default_attr_sign(attr, sym_plane)}
    return attr_map


def _lower_name(control):
    return _strip_namespace(control).lower()


def _control_side(control, sym_plane=None):
    """Return a control side from shared name tokens, then rig-local geometry."""
    try:
        named_side = side_resolver.detect_side(control)
    except Exception:
        named_side = None
    if named_side:
        return named_side
    return _side_from_plane(control, sym_plane)


def _is_fk_copy_control(control):
    name = _lower_name(control)
    if "ik" in name:
        return False
    return "fk" in name


def _is_hand_or_finger_copy_control(control):
    name = _lower_name(control)
    blocked = ("ik", "pv", "pole", "vector")
    if any(token in name for token in blocked):
        return False
    tokens = (
        "wrist", "hand", "palm",
        "finger", "fingers", "fng", "fngr", "digit",
        "thumb", "thb", "thm", "index", "idx", "middle", "mid",
        "ring", "rng", "pinky", "pinkie", "pnk",
        "metacarp", "carpal", "knuckle", "phal", "phalanx",
        "curl", "spread", "cup", "fist",
    )
    return any(token in name for token in tokens)


def _is_finger_control(control):
    name = _lower_name(control)
    return any(token in name for token in (
        "finger", "fingers", "fng", "fngr", "digit",
        "thumb", "thb", "thm", "index", "idx", "middle",
        "ring", "rng", "pinky", "pinkie", "pnk", "phal",
    ))


def _is_bend_like_control(control):
    name = _lower_name(control)
    tokens = ("bend", "bendy", "tweak", "twist", "roll", "ribbon")
    return any(token in name for token in tokens)


def _same_value_attr_map(source, target, include_translate=False, skip_zero_delta=False, snapshot_cache=None):
    target_attrs = set(_cached_scalar_attrs(target, snapshot_cache))
    attr_map = {}
    for attr in _cached_scalar_attrs(source, snapshot_cache):
        if attr not in target_attrs or attr in SCALE_ATTRS:
            continue
        if attr.startswith("translate") and not include_translate:
            continue
        attr_map[attr] = {
            "target": attr,
            "mult": 1.0,
            "mode": "copy_value",
            "skip_zero_delta": bool(skip_zero_delta),
        }
    return attr_map


def _invert_attr_map(attr_map):
    inverted = {}
    for source_attr, spec in (attr_map or {}).items():
        if not isinstance(spec, dict):
            continue
        target_attr = spec.get("target")
        if not target_attr:
            continue
        reverse_spec = dict(spec)
        reverse_spec["target"] = source_attr
        inverted[target_attr] = reverse_spec
    return inverted


def _calibrate_axis_group(source, target, sym_plane, source_attrs, target_attrs,
                          source_rest, target_rest, group_attrs,
                          source_rest_mat=None, target_rest_mat=None, snapshot_cache=None):
    src_group = [a for a in group_attrs if a in source_attrs and _cached_is_modifiable(source, a, snapshot_cache)]
    tgt_group = [a for a in group_attrs if a in target_attrs and _cached_is_modifiable(target, a, snapshot_cache)]
    if not src_group or len(tgt_group) < len(src_group):
        return {}

    source_rest_mat = source_rest_mat or _mat4_from_maya(source)
    target_rest_mat = target_rest_mat or _mat4_from_maya(target)
    desired_base = sym_plane.reflect_mat4(source_rest_mat)
    source_effects = {}

    for source_attr in src_group:
        delta = _calibration_delta(source_attr)
        source_value = source_rest.get(source_attr, 0.0)
        if not _set_scalar_attr_cached(source, source_attr, source_value + delta, snapshot_cache):
            continue
        source_effects[source_attr] = (desired_base, sym_plane.reflect_mat4(_mat4_from_maya(source)), delta)
        _restore_attr(source, source_attr, source_value, snapshot_cache)

    if len(source_effects) != len(src_group):
        return {}

    target_effects = {}
    group_delta = _calibration_delta(src_group[0])
    for target_attr in tgt_group:
        if not _cached_is_modifiable(target, target_attr, snapshot_cache):
            continue
        target_value = target_rest.get(target_attr, 0.0)
        for mult in (-1.0, 1.0):
            if not _set_scalar_attr_cached(target, target_attr, target_value + (group_delta * mult), snapshot_cache):
                continue
            target_effects[(target_attr, mult)] = _mat4_from_maya(target)
            _restore_attr(target, target_attr, target_value, snapshot_cache)

    if not target_effects:
        _restore_attrs(source, source_rest, snapshot_cache)
        _restore_attrs(target, target_rest, snapshot_cache)
        return {}

    best = None
    for perm in itertools.permutations(tgt_group, len(src_group)):
        for signs in itertools.product((-1.0, 1.0), repeat=len(src_group)):
            total = 0.0
            mapping = {}
            for source_attr, target_attr, mult in zip(src_group, perm, signs):
                desired_base_i, desired_i, delta = source_effects[source_attr]
                candidate = target_effects.get((target_attr, mult))
                if candidate is None:
                    total += 999999.0
                    continue
                total += _matrix_delta_error(desired_base_i, desired_i, target_rest_mat, candidate)
                mapping[source_attr] = {"target": target_attr, "mult": mult}
            if best is None or total < best[0]:
                best = (total, mapping)

    _restore_attrs(source, source_rest, snapshot_cache)
    _restore_attrs(target, target_rest, snapshot_cache)
    return best[1] if best else {}


def _build_calibrated_attr_map(source, target, sym_plane, snapshot_cache=None,
                               allow_finger_calibration=False):
    attr_map = _default_attr_map(source, target, sym_plane, snapshot_cache=snapshot_cache)
    source_attrs = _cached_scalar_attrs(source, snapshot_cache)
    target_attrs = _cached_scalar_attrs(target, snapshot_cache)
    if not source_attrs or not target_attrs:
        return attr_map

    # A hand pose can be captured at any frame. Finger controls therefore use
    # their paired local channel convention directly, rather than inferring
    # it from a potentially curled pose during a snapshot.
    if (source != target and _is_finger_control(source)
            and not allow_finger_calibration):
        return _legacy_finger_attr_map(source, attr_map)

    if _is_bend_like_control(source):
        return _same_value_attr_map(
            source, target, include_translate=True, skip_zero_delta=True,
            snapshot_cache=snapshot_cache
        )

    source_rest = _cached_rest_attrs(source, snapshot_cache)
    target_rest = _cached_rest_attrs(target, snapshot_cache)
    source_rest_mat = _cached_world_mat(source, snapshot_cache)
    target_rest_mat = _cached_world_mat(target, snapshot_cache)

    for group in (
        ("translateX", "translateY", "translateZ"),
        ("rotateX", "rotateY", "rotateZ"),
    ):
        attr_map.update(_calibrate_axis_group(
            source, target, sym_plane, source_attrs, target_attrs,
            source_rest, target_rest, group,
            source_rest_mat=source_rest_mat,
            target_rest_mat=target_rest_mat,
            snapshot_cache=snapshot_cache,
        ))

    desired_base = sym_plane.reflect_mat4(source_rest_mat)

    for source_attr in source_attrs:
        if source_attr in SCALE_ATTRS or source_attr in TRANSFORM_ATTRS:
            continue
        candidates = _attr_candidates(source_attr, target_attrs)
        if not candidates:
            continue
        if not _cached_is_modifiable(source, source_attr, snapshot_cache):
            continue

        delta = _calibration_delta(source_attr)
        source_value = source_rest.get(source_attr, 0.0)
        if not _set_scalar_attr_cached(source, source_attr, source_value + delta, snapshot_cache):
            continue

        desired = sym_plane.reflect_mat4(_mat4_from_maya(source))
        _restore_attr(source, source_attr, source_value, snapshot_cache)

        # Most custom attributes are switches or scalar settings that do not
        # move their own control.  Treating a zero matrix effect as a negative
        # calibration was a major source of flipped IK/FK and space switches.
        if _matrix_error(desired_base, desired) <= 0.000001:
            target_attr = source_attr if source_attr in candidates else candidates[0]
            target_value = target_rest.get(target_attr, 0.0)
            mult = -1.0 if (
                abs(source_value + target_value) < 0.001
                and abs(source_value - target_value) >= 0.001
            ) else 1.0
            try:
                attr_type = cmds.getAttr(f"{source}.{source_attr}", type=True)
            except Exception:
                attr_type = "double"
            spec = {"target": target_attr, "mult": mult}
            if attr_type in ("bool", "enum", "byte", "char", "short", "long"):
                spec["mode"] = "copy_value"
                spec["mult"] = 1.0
            attr_map[source_attr] = spec
            continue

        best = None
        for target_attr in candidates:
            if not _cached_is_modifiable(target, target_attr, snapshot_cache):
                continue
            target_value = target_rest.get(target_attr, 0.0)
            for mult in (1.0, -1.0):
                _set_scalar_attr_cached(target, target_attr, target_value + (delta * mult), snapshot_cache)
                err = _matrix_delta_error(desired_base, desired, target_rest_mat, _mat4_from_maya(target))
                if best is None or err < best[0]:
                    best = (err, target_attr, mult)
                _restore_attr(target, target_attr, target_value, snapshot_cache)

        if best:
            attr_map[source_attr] = {"target": best[1], "mult": best[2]}

    _restore_attrs(source, source_rest, snapshot_cache)
    _restore_attrs(target, target_rest, snapshot_cache)
    return attr_map


def _get_parent_world_matrix(control):
    """World matrix of the first transform parent, or identity if none."""
    try:
        parents = cmds.listRelatives(control, parent=True, fullPath=True)
        if parents:
            parent = parents[0]
            if cmds.nodeType(parent) in ('transform', 'joint'):
                return _mat4_from_maya(parent)
    except:
        pass
    return Mat4.identity()


def _get_joint_orient_matrix(control):
    """Joint orient as a rotation matrix (identity for non-joints)."""
    if cmds.nodeType(control) != 'joint':
        return Mat4.identity()
    try:
        jo = cmds.getAttr(f'{control}.jointOrient')[0]
        rot_order = ROTATION_ORDER_MAP.get(
            cmds.getAttr(f'{control}.rotateOrder'), 'xyz')
        return Mat4.from_euler_xyz(jo[0], jo[1], jo[2], rot_order)
    except:
        return Mat4.identity()


def _get_rotate_axis_matrix(control):
    """rotateAxis offset as rotation matrix."""
    try:
        ra = cmds.getAttr(f'{control}.rotateAxis')[0]
        return Mat4.from_euler_xyz(ra[0], ra[1], ra[2], 'xyz')
    except:
        return Mat4.identity()


def world_matrix_to_local_transforms(control, target_world_mat):
    """
    Given a desired world matrix for 'control', compute the translate/rotate/scale
    attribute values that would produce it, respecting the control's parent,
    jointOrient, rotateAxis, and rotateOrder.
    
    Returns a dict: {attr_name: value}  (only modifiable, non-locked attrs)
    """
    result = {}

    # ── 1. Parent inverse world matrix ──────────────────────────────────────
    parent_wm  = _get_parent_world_matrix(control)
    parent_wm_inv = parent_wm.inverse()

    # ── 2. Local matrix = inv(parentWM) * targetWM ──────────────────────────
    local_mat  = parent_wm_inv * target_world_mat

    # ── 3. Extract scale from local matrix ──────────────────────────────────
    sx = _len3(local_mat.x_axis())
    sy = _len3(local_mat.y_axis())
    sz = _len3(local_mat.z_axis())

    # ── 4. Extract translation ───────────────────────────────────────────────
    t = local_mat.translation()

    # ── 5. Extract pure rotation (remove scale) ──────────────────────────────
    rot_mat = _normalize_rotation_in_matrix(local_mat)

    # ── 6. Remove jointOrient & rotateAxis ──────────────────────────────────
    jo_inv  = _get_joint_orient_matrix(control).inverse()
    ra_inv  = _get_rotate_axis_matrix(control).inverse()
    # Maya's transform: M = T * RO * RA * R * JO * S
    # Solve for R: R = RA_inv * rot_pure * JO_inv
    pure_rot = ra_inv * rot_mat * jo_inv

    # ── 7. Euler decomposition respecting rotateOrder ────────────────────────
    rot_order_idx = 0
    try:
        rot_order_idx = cmds.getAttr(f'{control}.rotateOrder')
    except:
        pass
    rot_order = ROTATION_ORDER_MAP.get(rot_order_idx, 'xyz')
    rx, ry, rz = _matrix_to_euler(pure_rot, rot_order)

    # ── 8. Build result dict ─────────────────────────────────────────────────
    def settable(attr):
        return _is_modifiable(control, attr)

    if settable('translateX'): result['translateX'] = t[0]
    if settable('translateY'): result['translateY'] = t[1]
    if settable('translateZ'): result['translateZ'] = t[2]
    if settable('rotateX'):    result['rotateX']    = rx
    if settable('rotateY'):    result['rotateY']    = ry
    if settable('rotateZ'):    result['rotateZ']    = rz
    # Scale: only set if it differs meaningfully from snapshot rest scale
    # (avoids the -1 scale bug: we NEVER use negative scale for mirroring)
    if settable('scaleX'):     result['scaleX']     = sx
    if settable('scaleY'):     result['scaleY']     = sy
    if settable('scaleZ'):     result['scaleZ']     = sz

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#                      CUSTOM ATTRIBUTE MIRRORING
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_custom_attr_multiplier(control, opposite, attr, snapshot_vals):
    """
    For non-transform attributes (IK blend, stretchy, etc.), determine if
    mirroring should be symmetric (value copies 1:1) or inverted.
    
    Strategy:
        At snapshot time both sides should be in default/rest pose.
        If the attribute values are equal → copy as-is (mult=1).
        If they differ significantly → they might be side-dependent,
            but without a test we can't know: default to 1.0.
        
    For spatial custom attrs (like twist offsets named with side tokens),
    we test the world effect by nudging the attribute.
    """
    # Check snapshot default values
    src_default  = snapshot_vals.get(f'{control}.{attr}')
    opp_default  = snapshot_vals.get(f'{opposite}.{attr}')

    if src_default is None or opp_default is None:
        return 1.0

    # If both sides are at zero at rest, we can't determine direction → keep 1.0
    if abs(src_default) < 0.001 and abs(opp_default) < 0.001:
        return 1.0

    # If they're equal at rest, copy 1:1
    if abs(src_default - opp_default) < 0.001:
        return 1.0

    # If they're negated at rest, mirror should negate
    if abs(src_default + opp_default) < 0.001:
        return -1.0

    return 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#                      SNAPSHOT SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_file_key(text):
    text = str(text or "default_rig")
    safe = []
    for char in text:
        if char.isalnum() or char in ("_", "-", "."):
            safe.append(char)
        else:
            safe.append("_")
    key = "".join(safe).strip("_")
    return key or "default_rig"


def _long_name(node):
    try:
        return (cmds.ls(node, long=True) or [node])[0]
    except:
        return node


def _dag_parts(node):
    return [part for part in _long_name(node).split("|") if part]


def _is_shared_scene_container(name):
    clean = "".join(ch for ch in name.lower() if ch.isalnum())
    shared_names = {
        "characters", "character", "chars", "char",
        "rigs", "assets", "references", "refs",
        "scene", "world", "root", "main",
    }
    return clean in shared_names or clean.endswith("characters") or clean.endswith("rigs")


def _scope_for_control(control):
    ns = _namespace(control)
    if ns:
        return "namespace", ns, ns

    parts = _dag_parts(control)
    if not parts:
        return "scene", "default", "Scene"

    root_index = 0
    while root_index < len(parts) - 1 and _is_shared_scene_container(parts[root_index]):
        root_index += 1
    scope_parts = parts[:root_index + 1]
    scope_path = "|" + "|".join(scope_parts)
    return "root", scope_path, scope_parts[-1]


def _find_transform_named_under(scope_path, leaf_name):
    if not scope_path or not cmds.objExists(scope_path):
        return None
    try:
        descendants = cmds.listRelatives(
            scope_path, allDescendents=True, fullPath=True, type="transform"
        ) or []
    except:
        descendants = []
    for node in descendants:
        if node.rsplit("|", 1)[-1] == leaf_name:
            return node
    return None


def _common_dag_ancestor(controls):
    paths = [_dag_parts(control) for control in controls or [] if cmds.objExists(control)]
    paths = [path for path in paths if path]
    if not paths:
        return None

    common = []
    for parts in zip(*paths):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return "|" + "|".join(common) if common else None


def _namespace_free_parts(node):
    return [part.rsplit(":", 1)[-1] for part in _dag_parts(node)]


def _build_profile_bindings(controls):
    """Create stable profile ids while keeping legacy short ids when unique."""
    valid_controls = [control for control in controls or [] if cmds.objExists(control)]
    short_counts = {}
    for control in valid_controls:
        short = _strip_namespace(control)
        short_counts[short] = short_counts.get(short, 0) + 1

    anchor = _common_dag_ancestor(valid_controls)
    anchor_parts = _dag_parts(anchor) if anchor else []
    ids_by_control = {}
    bindings = {}
    short_index = {}
    for control in valid_controls:
        short = _strip_namespace(control)
        full_parts = _dag_parts(control)
        relative_parts = full_parts[len(anchor_parts):] if anchor_parts else full_parts
        if not relative_parts:
            relative_parts = full_parts[-1:]
        relative_parts = [part.rsplit(":", 1)[-1] for part in relative_parts]
        control_id = short if short_counts.get(short) == 1 else "|".join(relative_parts)
        if control_id in bindings:
            control_id = "|".join(_namespace_free_parts(control))
        ids_by_control[control] = control_id
        bindings[control_id] = {
            "short": short,
            "relative_parts": relative_parts,
        }
        short_index.setdefault(short, []).append(control_id)
    return ids_by_control, bindings, short_index


def _profile_key_for_control(control, snapshot):
    if not snapshot:
        return None
    controls_data = snapshot.get("controls", {}) or {}
    short = _strip_namespace(control)
    short_index = snapshot.get("short_index", {}) or {}
    candidates = list(short_index.get(short, []))
    if not candidates and short in controls_data:
        candidates = [short]
    if len(candidates) == 1:
        return candidates[0]

    path_parts = _namespace_free_parts(control)
    bindings = snapshot.get("bindings", {}) or {}
    best = None
    for control_id in candidates:
        relative = bindings.get(control_id, {}).get("relative_parts") or []
        score = _path_suffix_match_count(path_parts, relative)
        if relative and score == len(relative):
            return control_id
        if best is None or score > best[0]:
            best = (score, control_id)
    return best[1] if best and best[0] else None


def _resolve_profile_control(reference, control_id, snapshot):
    bindings = snapshot.get("bindings", {}) if snapshot else {}
    binding = bindings.get(control_id, {})
    short = binding.get("short") or str(control_id).rsplit("|", 1)[-1]
    relative = binding.get("relative_parts") or []
    if not binding:
        return _resolve_control_like(reference, short)

    namespace = _namespace(reference)
    leaf = f"{namespace}:{short}" if namespace else short
    try:
        candidates = cmds.ls(leaf, type="transform", long=True) or []
    except Exception:
        candidates = []
    if not candidates:
        try:
            candidates = cmds.ls(f"*{leaf}", type="transform", long=True) or []
        except Exception:
            candidates = []

    reference_scope = _scope_for_control(reference)[:2]
    ranked = []
    for candidate in candidates:
        try:
            same_scope = int(_scope_for_control(candidate)[:2] == reference_scope)
        except Exception:
            same_scope = 0
        suffix_score = _path_suffix_match_count(_namespace_free_parts(candidate), relative)
        ranked.append((same_scope, suffix_score, candidate))
    if ranked:
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if len(ranked) == 1 or ranked[0][:2] > ranked[1][:2]:
            return ranked[0][2]
    return _resolve_control_like(reference, short)


def _snapshot_mirror_space(controls):
    anchor = _common_dag_ancestor(controls)
    if not anchor or not cmds.objExists(anchor):
        return {}
    try:
        return {
            "anchor_path": anchor,
            "anchor_parts": _dag_parts(anchor),
            "rest_world_mat": _mat4_from_maya(anchor).as_list(),
        }
    except:
        return {}


def get_rig_label(controls):
    if not controls:
        return "Scene"
    _, _, label = _scope_for_control(controls[0])
    return label or "Scene"


def get_user_data_folder():
    maya_app_dir = cmds.internalVar(userAppDir=True)
    return os.path.join(maya_app_dir, "AnimKey_user_data")

def get_mirror_snapshot_file(rig_name):
    folder = os.path.join(get_user_data_folder(), "tools", "mirror", SNAPSHOT_FOLDER_NAME)
    return os.path.join(folder, f"{_safe_file_key(rig_name)}_mirror.json")


def _get_binary_mirror_snapshot_file(rig_name):
    folder = os.path.join(get_user_data_folder(), "tools", "mirror", SNAPSHOT_FOLDER_NAME)
    return os.path.join(folder, f"{_safe_file_key(rig_name)}_mirror.akmirror")


def _get_legacy_mirror_snapshot_file(rig_name):
    folder = os.path.join(get_user_data_folder(), "tools", "mirror", LEGACY_SNAPSHOT_FOLDER_NAME)
    return os.path.join(folder, f"{_safe_file_key(rig_name)}_mirror_v10.json")


def _mirror_snapshot_candidates(rig_name):
    candidates = [
        get_mirror_snapshot_file(rig_name),
        _get_binary_mirror_snapshot_file(rig_name),
        _get_legacy_mirror_snapshot_file(rig_name),
    ]
    candidates = list(dict.fromkeys(candidates))
    existing = [path for path in candidates if os.path.exists(path)]
    missing = [path for path in candidates if path not in existing]
    existing.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return existing + missing


def _write_snapshot_file(path, snapshot_data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{int(time.time() * 1000000)}.tmp"
    try:
        if path.endswith(".akmirror"):
            payload = pickle.dumps(snapshot_data, protocol=pickle.HIGHEST_PROTOCOL)
            payload = AKMIRROR_SNAPSHOT_MAGIC + zlib.compress(payload, 1)
            with open(tmp_path, "wb", buffering=1024 * 1024) as f:
                f.write(payload)
        else:
            with open(tmp_path, "w") as f:
                json.dump(snapshot_data, f, separators=(",", ":"))
        os.replace(tmp_path, path)
        _snapshot_cache.pop(path, None)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass


def _read_snapshot_file(path):
    if path.endswith(".akmirror"):
        with open(path, "rb", buffering=1024 * 1024) as f:
            payload = f.read()
        if payload.startswith(AKMIRROR_SNAPSHOT_MAGIC):
            decoder = zlib.decompressobj()
            payload = decoder.decompress(
                payload[len(AKMIRROR_SNAPSHOT_MAGIC):],
                64 * 1024 * 1024 + 1,
            )
            if len(payload) > 64 * 1024 * 1024 or not decoder.eof:
                raise ValueError("Legacy mirror snapshot is too large or incomplete")

        class SnapshotUnpickler(pickle.Unpickler):
            _SAFE_BUILTINS = {
                "dict", "list", "tuple", "set", "frozenset",
                "str", "bytes", "int", "float", "bool",
            }

            def find_class(self, module, name):
                if module in ("builtins", "__builtin__") and name in self._SAFE_BUILTINS:
                    return getattr(builtins, name)
                raise pickle.UnpicklingError(
                    "Unsupported value in legacy mirror snapshot"
                )

        snapshot = SnapshotUnpickler(io.BytesIO(payload)).load()
        if not isinstance(snapshot, dict):
            raise ValueError("Invalid legacy mirror snapshot")
        return snapshot
    with open(path) as f:
        return json.load(f)


def _file_size_kb(path):
    try:
        return os.path.getsize(path) / 1024.0
    except:
        return 0.0


def get_rig_identifier(controls):
    if not controls:
        return "default_rig"
    kind, scope, _ = _scope_for_control(controls[0])
    if kind == "root":
        try:
            opposite = find_opposite_by_name(controls[0])
            if opposite and cmds.objExists(opposite):
                opposite_kind, opposite_scope, _ = _scope_for_control(opposite)
                if opposite_kind == kind:
                    scope = min(scope, opposite_scope)
        except Exception:
            pass
    return _safe_file_key(f"{kind}_{scope}")


def _group_controls_by_rig(controls):
    grouped = {}
    for ctrl in controls:
        if not cmds.objExists(ctrl):
            continue
        rig_name = get_rig_identifier([ctrl])
        if rig_name not in grouped:
            grouped[rig_name] = {
                "label": get_rig_label([ctrl]),
                "controls": [],
            }
        grouped[rig_name]["controls"].append(ctrl)
    return grouped


def _run_with_refresh_suspended(callback, *args, **kwargs):
    suspended = False
    undo_suspended = False
    try:
        cmds.refresh(suspend=True)
        suspended = True
    except:
        pass
    try:
        if cmds.undoInfo(q=True, state=True):
            cmds.undoInfo(stateWithoutFlush=False)
            undo_suspended = True
    except:
        undo_suspended = False
    try:
        return callback(*args, **kwargs)
    finally:
        if suspended:
            try:
                cmds.refresh(suspend=False)
            except:
                pass
        if undo_suspended:
            try:
                cmds.undoInfo(stateWithoutFlush=True)
            except:
                pass


def _attribute_default_value(node, attr):
    try:
        values = cmds.attributeQuery(attr, node=node, listDefault=True)
        if values:
            return float(values[0])
    except Exception:
        pass
    if attr in SCALE_ATTRS:
        return 1.0
    return 0.0


def _capture_scalar_attr_values(controls):
    values = {}
    for control in controls or []:
        if not cmds.objExists(control):
            continue
        for attr in _scalar_keyable_attrs(control):
            plug = f"{control}.{attr}"
            try:
                values[plug] = cmds.getAttr(plug)
            except Exception:
                pass
    return values


def _restore_scalar_attr_values(values):
    for plug, value in (values or {}).items():
        try:
            if cmds.objExists(plug) and not cmds.getAttr(plug, lock=True):
                cmds.setAttr(plug, value)
        except Exception:
            pass


def _reset_controls_to_default_values(controls):
    reset_count = 0
    for control in controls or []:
        if not cmds.objExists(control):
            continue
        for attr in _scalar_keyable_attrs(control):
            plug = f"{control}.{attr}"
            try:
                if cmds.getAttr(plug, lock=True):
                    continue
            except Exception:
                continue
            if _set_scalar_attr(control, attr, _attribute_default_value(control, attr)):
                reset_count += 1
    return reset_count


def _reset_captured_values_to_defaults(values):
    reset_count = 0
    for plug in (values or {}):
        node, separator, attr = plug.rpartition(".")
        if not separator or not cmds.objExists(plug):
            continue
        try:
            if cmds.getAttr(plug, lock=True):
                continue
        except Exception:
            continue
        if _set_scalar_attr(node, attr, _attribute_default_value(node, attr)):
            reset_count += 1
    return reset_count


def _controls_match_attribute_defaults(controls, tolerance=0.0001):
    """True when a snapshot can be captured without mutating the scene."""
    for control in controls or []:
        if not cmds.objExists(control):
            continue
        for attr in _scalar_keyable_attrs(control):
            current = _safe_scalar_attr(control, attr)
            default = _attribute_default_value(control, attr)
            if attr in ROTATE_ATTRS:
                difference = abs(_shortest_angle_delta(current, default))
            else:
                difference = abs(current - default)
            if difference > tolerance:
                return False
    return True


def _capture_selected_keyframes():
    result = []
    try:
        curves = cmds.keyframe(query=True, selected=True, name=True) or []
    except Exception:
        return result
    for curve in curves:
        try:
            times = cmds.keyframe(curve, query=True, selected=True, timeChange=True) or []
        except Exception:
            times = []
        for frame in times:
            result.append((curve, float(frame)))
    return result


def _restore_selected_keyframes(selection):
    if not selection:
        return
    try:
        cmds.selectKey(clear=True)
        for curve, frame in selection:
            if cmds.objExists(curve):
                cmds.selectKey(curve, time=(frame, frame), keyframe=True, add=True)
    except Exception:
        pass


def _evaluate_snapshot_pose(controls):
    """Force deterministic DG evaluation without wall-clock sleeps."""
    try:
        cmds.dgdirty(controls)
    except Exception:
        pass
    # Reading evaluated output plugs is the synchronization point Maya needs;
    # sleeping does not make the dependency graph more correct and imposed a
    # fixed cost on every snapshot.
    for control in controls or []:
        try:
            cmds.getAttr(f"{control}.worldMatrix[0]")
        except Exception:
            pass
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _create_default_pose_mirror_snapshot(selected_controls, rig_name=None,
                                         rig_label=None, show_message=True,
                                         expand_controls=True, persist=True,
                                         return_snapshot=False,
                                         print_report=True, force_reset=False):
    controls = (_expand_to_candidate_controls(selected_controls)
                if expand_controls else list(selected_controls or []))
    controls = list(dict.fromkeys(ctrl for ctrl in controls if cmds.objExists(ctrl)))
    if not controls:
        return None

    rig_name = rig_name or get_rig_identifier(controls)
    rig_label = rig_label or get_rig_label(controls)
    if not force_reset and _controls_match_attribute_defaults(controls):
        default_values = _capture_scalar_attr_values(controls)
        try:
            default_modified = bool(cmds.file(query=True, modified=True))
        except Exception:
            default_modified = None
        direct_auto_state = globals().get("_auto_mirror_state")
        direct_auto_enabled = bool(direct_auto_state and direct_auto_state.enabled)
        if direct_auto_state:
            direct_auto_state.enabled = False
        try:
            return _run_with_refresh_suspended(
                _create_mirror_snapshot,
                controls,
                rig_name=rig_name,
                rig_label=rig_label,
                show_message=show_message,
                allow_finger_calibration=True,
                persist=persist,
                return_snapshot=return_snapshot,
                print_report=print_report,
            )
        finally:
            _restore_scalar_attr_values(default_values)
            if default_modified is not None:
                try:
                    cmds.file(modified=default_modified)
                except Exception:
                    pass
            if direct_auto_state:
                direct_auto_state.enabled = direct_auto_enabled
    original_time = cmds.currentTime(query=True)
    original_selection = cmds.ls(selection=True, long=True) or []
    original_key_selection = _capture_selected_keyframes()
    original_values = _capture_scalar_attr_values(controls)
    try:
        original_modified = bool(cmds.file(query=True, modified=True))
    except Exception:
        original_modified = None
    prior_undo_state = True
    undo_open = False
    refresh_suspended = False
    auto_state = globals().get("_auto_mirror_state")
    auto_mirror_was_enabled = bool(auto_state and auto_state.enabled)
    if auto_state:
        auto_state.enabled = False

    try:
        prior_undo_state = cmds.undoInfo(query=True, state=True)
    except Exception:
        prior_undo_state = True

    try:
        cmds.waitCursor(state=True)
    except Exception:
        pass
    try:
        cmds.refresh(suspend=True)
        refresh_suspended = True
    except Exception:
        pass

    result = None
    try:
        try:
            cmds.undoInfo(stateWithoutFlush=True)
        except Exception:
            pass
        cmds.currentTime(SNAPSHOT_FRAME, edit=True, update=True)
        cmds.select(controls, replace=True)
        cmds.undoInfo(openChunk=True, chunkName="AnimKey Mirror Default Snapshot")
        undo_open = True
        try:
            try:
                cmds.cutKey(controls, clear=True)
            except (RuntimeError, TypeError, ValueError):
                pass
            _reset_captured_values_to_defaults(original_values)
            _evaluate_snapshot_pose(controls)
            result = _create_mirror_snapshot(
                controls,
                rig_name=rig_name,
                rig_label=rig_label,
                show_message=show_message,
                allow_finger_calibration=True,
                persist=persist,
                return_snapshot=return_snapshot,
                print_report=print_report,
            )
        finally:
            if undo_open:
                try:
                    cmds.undoInfo(closeChunk=True)
                except Exception:
                    pass
                undo_open = False
            try:
                cmds.undo()
            except (RuntimeError, TypeError, ValueError):
                pass
            _restore_scalar_attr_values(original_values)
    finally:
        try:
            cmds.currentTime(original_time, edit=True, update=True)
        except Exception:
            pass
        try:
            cmds.select(original_selection, replace=True)
        except Exception:
            pass
        _restore_selected_keyframes(original_key_selection)
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        try:
            cmds.waitCursor(state=False)
        except Exception:
            pass
        try:
            cmds.undoInfo(stateWithoutFlush=prior_undo_state)
        except Exception:
            pass
        if original_modified is not None:
            try:
                cmds.file(modified=original_modified)
            except Exception:
                pass
        if auto_state:
            auto_state.enabled = auto_mirror_was_enabled
    return result


def _create_mirror_snapshot(selected_controls, rig_name=None, rig_label=None,
                            show_message=True, allow_finger_calibration=False,
                            persist=True, return_snapshot=False,
                            print_report=True):
    """
    Create mirror snapshot from T-Pose / default pose.

    Captures:
        - Symmetry plane of the rig (from paired control positions)
        - World matrices at rest for each control
        - Default attribute values at rest
        - Rotation order and jointOrient for each control
        - Custom attribute multipliers
        - Paired/central classification
    """
    if not selected_controls:
        return None
    selected_controls = list(dict.fromkeys(
        _long_name(ctrl) for ctrl in selected_controls if cmds.objExists(ctrl)
    ))
    snapshot_verbose = os.environ.get("ANIMKEY_MIRROR_SNAPSHOT_VERBOSE") == "1"
    _print = builtins.print

    def print(*args, **kwargs):
        if not print_report:
            return None
        if (not snapshot_verbose and args and isinstance(args[0], str)
                and args[0].startswith("    ")):
            return
        return _print(*args, **kwargs)

    snapshot_started = time.time()

    rig_name = rig_name or get_rig_identifier(selected_controls)
    rig_label = rig_label or get_rig_label(selected_controls)

    print("\n" + "=" * 65)
    print(f"  AnimKey Mirror Snapshot  -  {rig_label}")
    print("=" * 65)
    print("  Phase 1: Detecting symmetry plane...")
    cache_started = time.time()
    control_cache = _build_snapshot_control_cache(selected_controls)
    control_index = _build_snapshot_control_index(selected_controls)
    control_ids, bindings, short_index = _build_profile_bindings(selected_controls)
    cache_elapsed = time.time() - cache_started

    # ── Phase 1: Find all pairs to detect symmetry plane ────────────────────
    detect_started = time.time()
    pairs_positions = []
    pair_map        = {}   # stable control id -> opposite id
    pair_controls   = {}
    pair_confidence = {}
    pair_method     = {}
    processed       = set()

    for ctrl in selected_controls:
        ctrl_short = control_cache.get(ctrl, {}).get("short") or _strip_namespace(ctrl)
        ctrl_id = control_ids.get(ctrl, ctrl_short)
        if ctrl_id in processed:
            continue
        opp = _find_opposite_from_index(ctrl, control_index)
        if opp and opp in control_cache:
            opp_short = control_cache.get(opp, {}).get("short") or _strip_namespace(opp)
            opp_id = control_ids.get(opp, opp_short)
            pairs_positions.append(_ordered_pair_positions(ctrl, opp, control_cache))
            pair_map[ctrl_id] = opp_id
            pair_map[opp_id] = ctrl_id
            pair_controls[ctrl] = opp
            pair_controls[opp] = ctrl
            pair_confidence[ctrl] = 1.0
            pair_confidence[opp] = 1.0
            pair_method[ctrl] = "name"
            pair_method[opp] = "name"
            processed.add(ctrl_id)
            processed.add(opp_id)

    named_plane = SymmetryPlane.detect_from_pairs(pairs_positions) if pairs_positions else None
    geometric_pairs, geometric_plane, geometric_confidence = _geometry_pair_solution(
        selected_controls,
        control_cache,
        occupied=pair_controls.keys(),
        preferred_plane=named_plane,
    )
    sym_plane = geometric_plane or named_plane or SymmetryPlane()
    for ctrl, opp in geometric_pairs.items():
        if ctrl in pair_controls or opp in pair_controls:
            continue
        ctrl_short = control_cache.get(ctrl, {}).get("short") or _strip_namespace(ctrl)
        opp_short = control_cache.get(opp, {}).get("short") or _strip_namespace(opp)
        ctrl_id = control_ids.get(ctrl, ctrl_short)
        opp_id = control_ids.get(opp, opp_short)
        pair_map[ctrl_id] = opp_id
        pair_map[opp_id] = ctrl_id
        pair_controls[ctrl] = opp
        pair_controls[opp] = ctrl
        confidence = min(
            geometric_confidence.get(ctrl, 0.5),
            geometric_confidence.get(opp, 0.5),
        )
        pair_confidence[ctrl] = confidence
        pair_confidence[opp] = confidence
        pair_method[ctrl] = "geometry"
        pair_method[opp] = "geometry"
    detect_elapsed = time.time() - detect_started
    print(f"  Symmetry plane normal: {[round(v,4) for v in sym_plane.normal]}")
    print(f"  Symmetry plane point:  {[round(v,4) for v in sym_plane.point]}")
    print(f"\n  Phase 2: Recording rest-pose data for {len(selected_controls)} controls...")

    # ── Phase 2: Capture rest-pose data ─────────────────────────────────────
    rest_started = time.time()
    snapshot_data = {
        "version"       : SNAPSHOT_SCHEMA_VERSION,
        "profile_kind"  : "precise",
        "created_at"    : time.time(),
        "rig_name"      : rig_name,
        "rig_label"     : rig_label,
        "symmetry_plane": sym_plane.to_dict(),
        "finger_mapping_version": FINGER_MAPPING_VERSION,
        "calibrated_fingers": bool(allow_finger_calibration),
        "mirror_space"  : _snapshot_mirror_space(
            list(selected_controls) + list(pair_controls.values())
        ),
        "bindings"      : bindings,
        "short_index"   : short_index,
        "controls"      : {},
        "opposites"     : {}
    }

    # First pass: capture all rest values
    rest_values = {}   # "ctrl.attr" → value
    rest_values = _snapshot_rest_values(control_cache)
    for ctrl in ():
        for attr in (cmds.listAttr(ctrl, keyable=True) or []):
            if attr in ATTRIBUTES_TO_IGNORE:
                continue
            key = f'{ctrl}.{attr}'
            try:
                rest_values[key] = cmds.getAttr(key)
            except:
                pass

    # ── Phase 3: Per-control data ────────────────────────────────────────────
    rest_elapsed = time.time() - rest_started
    calibrate_started = time.time()
    processed = set()
    paired_count  = 0
    central_count = 0

    for ctrl in selected_controls:
        ctrl_cache = control_cache.get(ctrl, {})
        ctrl_short = ctrl_cache.get("short") or _strip_namespace(ctrl)
        ctrl_id = control_ids.get(ctrl, ctrl_short)
        if ctrl_id in processed:
            continue

        opp_id = pair_map.get(ctrl_id)
        is_paired = opp_id is not None

        # Reconstruct full opposite name
        opp = pair_controls.get(ctrl) if is_paired else None

        # World matrix at rest
        wm_rest = _cached_world_mat(ctrl, control_cache)

        # Rotation order
        rot_order = ctrl_cache.get("rotate_order", 'xyz')

        # Custom attribute multipliers
        custom_mults = {}
        if is_paired and opp:
            for attr in ctrl_cache.get("attrs", []):
                if attr in ATTRIBUTES_TO_IGNORE or attr in TRANSFORM_ATTRS:
                    continue
                if not _cached_is_modifiable(opp, attr, control_cache):
                    continue
                mult = _detect_custom_attr_multiplier(ctrl, opp, attr, rest_values)
                if mult != 1.0:
                    custom_mults[attr] = mult

        ctrl_data = {
            "type"           : (
                "paired" if is_paired else
                "central" if _control_is_central(
                    ctrl, sym_plane, selected_controls, control_cache
                ) else "unpaired"
            ),
            "opposite"       : opp_id,
            "pair_method"    : pair_method.get(ctrl),
            "pair_confidence": pair_confidence.get(ctrl, 0.0),
            "rot_order"      : rot_order,
            "rest_world_mat" : wm_rest.as_list(),
            "rest_attrs"     : _cached_rest_attrs(ctrl, control_cache),
            "custom_mults"   : custom_mults,
            "structure_fingerprint": _control_structure_fingerprint(ctrl, control_cache),
        }
        if is_paired and opp and opp in control_cache:
            ctrl_data["attr_map"] = _build_calibrated_attr_map(
                ctrl, opp, sym_plane, snapshot_cache=control_cache,
                allow_finger_calibration=allow_finger_calibration,
            )
            opp_attr_map = _invert_attr_map(ctrl_data["attr_map"])
        else:
            ctrl_data["attr_map"] = _build_calibrated_attr_map(
                ctrl, ctrl, sym_plane, snapshot_cache=control_cache,
                allow_finger_calibration=allow_finger_calibration,
            )
            opp_attr_map = None
        ctrl_data.update(_snapshot_transform_metadata(ctrl, control_cache))

        snapshot_data["controls"][ctrl_id] = ctrl_data
        if is_paired:
            snapshot_data["opposites"][ctrl_id] = opp_id
            # Mirror entry for opposite too
            if opp and opp in control_cache:
                opp_cache = control_cache.get(opp, {})
                opp_short = opp_cache.get("short") or _strip_namespace(opp)
                wm_opp = _cached_world_mat(opp, control_cache)
                rot_order_opp = opp_cache.get("rotate_order", 'xyz')
                opp_data = {
                    "type"           : "paired",
                    "opposite"       : ctrl_id,
                    "pair_method"    : pair_method.get(opp),
                    "pair_confidence": pair_confidence.get(opp, 0.0),
                    "rot_order"      : rot_order_opp,
                    "rest_world_mat" : wm_opp.as_list(),
                    "rest_attrs"     : _cached_rest_attrs(opp, control_cache),
                    "custom_mults"   : custom_mults,
                    "structure_fingerprint": _control_structure_fingerprint(opp, control_cache),
                    "attr_map"       : opp_attr_map or _build_calibrated_attr_map(
                        opp, ctrl, sym_plane, snapshot_cache=control_cache,
                        allow_finger_calibration=allow_finger_calibration,
                    ),
                }
                opp_data.update(_snapshot_transform_metadata(opp, control_cache))
                snapshot_data["controls"][opp_id] = {
                    **opp_data
                }
                snapshot_data["opposites"][opp_id] = ctrl_id
            processed.add(opp_id)
            paired_count += 1
            print(f"    PAIR  {ctrl_short}  <->  {opp_short}   [rot:{rot_order}]")
        else:
            if ctrl_data["type"] == "central":
                central_count += 1
                print(f"    CENTER  {ctrl_short}  [rot:{rot_order}]")
            else:
                print(f"    !  {ctrl_short}  (unpaired)  [rot:{rot_order}]")

        processed.add(ctrl_id)

    # ── Save ─────────────────────────────────────────────────────────────────
    calibrate_elapsed = time.time() - calibrate_started
    snapshot_data["validation"] = {
        "paired_controls": paired_count * 2,
        "central_controls": central_count,
        "unpaired_controls": sorted(
            name for name, data in snapshot_data["controls"].items()
            if data.get("type") == "unpaired"
        ),
        "minimum_pair_confidence": min(
            [data.get("pair_confidence", 1.0)
             for data in snapshot_data["controls"].values()
             if data.get("type") == "paired"] or [1.0]
        ),
    }
    write_started = time.time()
    snap_file = None
    if persist:
        snap_file = get_mirror_snapshot_file(rig_name)
        _write_snapshot_file(snap_file, snapshot_data)
        _missing_snapshot_cache.discard((get_user_data_folder(), str(rig_name)))
        _invalidate_quick_profiles(rig_name)
    write_elapsed = time.time() - write_started
    total_elapsed = time.time() - snapshot_started

    print("\n" + "=" * 65)
    print(f"  OK  {paired_count} pairs  +  {central_count} central controls")
    print(
        f"  OK  Snapshot saved -> {snap_file}"
        if persist else "  OK  In-memory auto profile ready"
    )
    print(f"  Cache: {cache_elapsed:.2f}s")
    print(f"  Detect: {detect_elapsed:.2f}s")
    print(f"  Rest: {rest_elapsed:.2f}s")
    print(f"  Calibrate: {calibrate_elapsed:.2f}s")
    print(f"  Size: {_file_size_kb(snap_file):.1f} KB" if snap_file else "  Size: memory only")
    print(f"  Write: {write_elapsed:.2f}s")
    print(f"  Time: {total_elapsed:.2f}s")
    print("=" * 65)

    if show_message and persist:
        unresolved_count = len(snapshot_data["validation"]["unpaired_controls"])
        cmds.inViewMessage(
            amg=(f"<span style='color:#a3be8c'>Mirror snapshot saved</span><br>"
             f"<span style='color:#ebcb8b'>{paired_count} pairs - {central_count} central"
             f" - {unresolved_count} unresolved - {total_elapsed:.2f}s</span>"),
            pos='topCenter', fade=True, fadeStayTime=2500
        )

    if return_snapshot:
        return snapshot_data
    return {
        "rig_name": rig_name,
        "rig_label": rig_label,
        "paired": paired_count,
        "central": central_count,
        "unresolved": len(snapshot_data["validation"]["unpaired_controls"]),
        "elapsed": total_elapsed,
    }


def snapshot_mirror_settings(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.snapshot_mirror_settings"):
        return None
    selected_controls = _expand_to_candidate_controls(cmds.ls(selection=True) or [])
    if not selected_controls:
        cmds.warning("AnimKey: Select all rig controls in T-Pose / default pose.")
        return

    grouped = _group_controls_by_rig(selected_controls)
    if not grouped:
        cmds.warning("AnimKey: No valid rig controls found for snapshot.")
        return

    results = []
    for rig_name, info in grouped.items():
        result = _create_default_pose_mirror_snapshot(
            info["controls"],
            rig_name=rig_name,
            rig_label=info["label"],
            show_message=(len(grouped) == 1),
            expand_controls=False,
        )
        if result:
            results.append(result)

    if len(results) > 1:
        rig_count = len(results)
        pair_count = sum(item["paired"] for item in results)
        central_count = sum(item["central"] for item in results)
        cmds.inViewMessage(
            amg=(f"<span style='color:#a3be8c'>Mirror snapshots saved</span><br>"
                 f"<span style='color:#ebcb8b'>{rig_count} rigs - {pair_count} pairs - {central_count} central</span>"),
            pos='topCenter', fade=True, fadeStayTime=2500
        )


def _safe_getattr(node, attr, default=None):
    try:
        return cmds.getAttr(f'{node}.{attr}')
    except:
        return default


def load_snapshot(rig_name=None):
    """Load calibrated snapshot data. Falls back to None if it is missing."""
    if rig_name is None:
        sel = cmds.ls(selection=True)
        if sel:
            rig_name = get_rig_identifier(sel)
        else:
            return None
    missing_key = (get_user_data_folder(), str(rig_name))
    if missing_key in _missing_snapshot_cache:
        return None
    for path in _mirror_snapshot_candidates(rig_name):
        if not os.path.exists(path):
            continue
        try:
            fingerprint = (os.path.getmtime(path), os.path.getsize(path))
            cached = _snapshot_cache.get(path)
            if cached and cached[0] == fingerprint:
                data = cached[1]
            else:
                data = _read_snapshot_file(path)
                _snapshot_cache[path] = (fingerprint, data)
            if data.get("version", 1) >= MIN_SUPPORTED_SNAPSHOT_VERSION:
                controls = data.get("controls", {})
                if controls and not any("rest_attrs" in c for c in controls.values()):
                    continue
                data.setdefault("profile_kind", "precise")
                _missing_snapshot_cache.discard(missing_key)
                return data
        except:
            pass
    _missing_snapshot_cache.add(missing_key)
    return None


def _basic_copy_attr_map(source, target, sym_plane, snapshot_cache=None):
    target_attrs = set(_cached_scalar_attrs(target, snapshot_cache))
    attr_map = {}
    for attr in _cached_scalar_attrs(source, snapshot_cache):
        if attr in ATTRIBUTES_TO_IGNORE or attr not in target_attrs:
            continue
        if attr in TRANSFORM_ATTRS:
            mult = _default_attr_sign(attr, sym_plane)
        else:
            mult = 1.0
        attr_map[attr] = {
            "target": attr,
            "mult": mult,
            "mode": "copy_value",
        }
    return attr_map


def _quick_profile_cache_key(controls, rig_name=None):
    """Build a cheap scene-local fingerprint for selected-control profiles."""
    entries = []
    for control in controls or []:
        if not cmds.objExists(control):
            continue
        long_name = _long_name(control)
        opposite = find_opposite_by_name(control)
        try:
            rotate_order = int(cmds.getAttr(f"{control}.rotateOrder"))
        except Exception:
            rotate_order = 0
        try:
            attrs = tuple(cmds.listAttr(control, keyable=True) or ())
        except Exception:
            attrs = ()
        entries.append((long_name, _long_name(opposite) if opposite else "", rotate_order, attrs))
    return (str(rig_name or ""), tuple(sorted(entries)))


def _invalidate_quick_profiles(rig_name=None):
    if rig_name is None:
        _quick_profile_cache.clear()
        return
    for cache_key in list(_quick_profile_cache):
        if cache_key and cache_key[0] == str(rig_name):
            _quick_profile_cache.pop(cache_key, None)


def _quick_rig_space_plane(controls):
    """Return a stable rig-local YZ plane without using the current pose.

    Named left/right controls can be far from symmetric in an animated shot.
    Fitting a plane to those positions makes quick mirror reversible but
    visually wrong. The shared top DAG node is cheap to query and supplies the
    conventional local-X mirror axis while following a moved or rotated rig.
    """
    valid = [_long_name(ctrl) for ctrl in controls or [] if cmds.objExists(ctrl)]
    if not valid:
        return None

    anchors = []
    for control in valid:
        try:
            kind, scope, _ = _scope_for_control(control)
        except Exception:
            continue
        if kind == "root" and scope and cmds.objExists(scope):
            anchors.append(scope)
            continue
        parts = _dag_parts(control)
        if parts:
            candidate = "|" + parts[0]
            if cmds.objExists(candidate):
                anchors.append(candidate)

    anchors = list(dict.fromkeys(anchors))
    if len(anchors) != 1:
        return None
    try:
        anchor_matrix = _mat4_from_maya(anchors[0])
        return SymmetryPlane(anchor_matrix.x_axis(), anchor_matrix.translation())
    except Exception:
        return None


def _quick_default_attrs(control, control_cache=None):
    data = (control_cache or {}).get(control, {})
    attrs = data.get("attrs") or _scalar_keyable_attrs(control)
    return {
        attr: _attribute_default_value(control, attr)
        for attr in attrs
        if attr not in ATTRIBUTES_TO_IGNORE
    }


def _is_quick_face_control(control):
    name = _lower_name(control)
    if "eyebrow" in name:
        return False
    return any(token in name for token in (
        "eye", "brow", "cheek", "nose", "nostril", "mouth", "lip",
        "jaw", "chin", "tongue", "phoneme", "emotion",
    ))


def _quick_parent_axis_mirror_sign(source, target, attr, sym_plane,
                                   source_parent=None, target_parent=None):
    """Infer a same-channel translation sign from the two local spaces."""
    axis_index = _axis_index_from_attr(attr)
    if axis_index is None:
        return 1.0
    source_parent = source_parent or _get_parent_world_matrix(source)
    target_parent = target_parent or _get_parent_world_matrix(target)
    source_axis = (
        source_parent.x_axis(), source_parent.y_axis(), source_parent.z_axis()
    )[axis_index]
    target_axis = (
        target_parent.x_axis(), target_parent.y_axis(), target_parent.z_axis()
    )[axis_index]
    normal = sym_plane.normal if sym_plane else (1.0, 0.0, 0.0)
    reflected = [
        source_axis[index] - (2.0 * _dot3(source_axis, normal) * normal[index])
        for index in range(3)
    ]
    alignment = _dot3(_normalize3(reflected), _normalize3(target_axis))
    return -1.0 if alignment < 0.0 else 1.0


def _quick_structural_attr_value(node, attr, fallback, is_control=False):
    """Return a pose-independent channel value for structural axis analysis."""
    plug = f"{node}.{attr}"
    try:
        is_driven = bool(cmds.connectionInfo(plug, isDestination=True))
    except Exception:
        is_driven = False
    if is_driven or is_control:
        return _attribute_default_value(node, attr)
    value = _safe_scalar_attr(node, attr)
    return fallback if value is None else value


def _quick_maya_euler_matrix(values, order="xyz"):
    """Return Mat4.from_euler_xyz in Maya's row-vector matrix convention."""
    raw = Mat4.from_euler_xyz(*values, order=order).as_list()
    transposed = list(raw)
    for row in range(3):
        for column in range(3):
            transposed[(row * 4) + column] = raw[(column * 4) + row]
    return Mat4(transposed)


def _quick_structural_local_matrix(node):
    """Approximate the authored rest-axis matrix without editing the scene."""
    is_control = _has_animkey_control_shape(node)
    rotate = tuple(
        _quick_structural_attr_value(
            node, f"rotate{axis}", 0.0, is_control=is_control
        )
        for axis in "XYZ"
    )
    scale = tuple(
        _quick_structural_attr_value(
            node, f"scale{axis}", 1.0, is_control=is_control
        )
        for axis in "XYZ"
    )
    try:
        rotate_axis = cmds.getAttr(f"{node}.rotateAxis")[0]
    except Exception:
        rotate_axis = (0.0, 0.0, 0.0)
    try:
        rotate_order = ROTATION_ORDER_MAP.get(
            int(cmds.getAttr(f"{node}.rotateOrder")), "xyz"
        )
    except Exception:
        rotate_order = "xyz"

    scale_matrix = Mat4([
        scale[0], 0, 0, 0,
        0, scale[1], 0, 0,
        0, 0, scale[2], 0,
        0, 0, 0, 1,
    ])
    local = (
        scale_matrix
        * _quick_maya_euler_matrix(rotate_axis, order="xyz")
        * _quick_maya_euler_matrix(rotate, order=rotate_order)
    )
    if cmds.nodeType(node) == "joint":
        try:
            joint_orient = cmds.getAttr(f"{node}.jointOrient")[0]
        except Exception:
            joint_orient = (0.0, 0.0, 0.0)
        local = local * _quick_maya_euler_matrix(joint_orient, order="xyz")

    try:
        opm_plug = f"{node}.offsetParentMatrix"
        opm_driven = bool(cmds.connectionInfo(opm_plug, isDestination=True))
        opm_value = cmds.getAttr(opm_plug)
        if (not opm_driven and opm_value and len(opm_value) == 16):
            local = local * Mat4(opm_value)
    except Exception:
        pass
    return local


def _quick_structural_world_matrix(node, cache=None):
    cache = cache if cache is not None else {}
    if node in cache:
        return cache[node]
    local = _quick_structural_local_matrix(node)
    parent = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
    try:
        inherits = bool(cmds.getAttr(f"{node}.inheritsTransform"))
    except Exception:
        inherits = True
    if parent and inherits and cmds.nodeType(parent) in ("transform", "joint"):
        world = local * _quick_structural_world_matrix(parent, cache)
    else:
        world = local
    cache[node] = world
    return world


def _quick_structural_parent_world_matrix(control, cache=None):
    parent = (cmds.listRelatives(control, parent=True, fullPath=True) or [None])[0]
    if not parent or cmds.nodeType(parent) not in ("transform", "joint"):
        return Mat4.identity()
    return _quick_structural_world_matrix(parent, cache)


def _quick_local_space_transform_mults(source, target, sym_plane,
                                       control_cache=None):
    """Infer mirror signs from the handedness of a paired local space.

    Translation is a polar vector, while rotation is an axial vector.  The
    latter therefore gets the parity of the complete local-space reflection.
    This handles both ordinary facial GUI controls and negatively-scaled
    eyebrow/lid spaces without a numerical snapshot.
    """
    structural_cache = None
    if isinstance(control_cache, dict):
        structural_cache = control_cache.setdefault(
            "__quick_structural_world_matrices__", {}
        )
    source_parent = _quick_structural_parent_world_matrix(
        source, structural_cache
    )
    target_parent = _quick_structural_parent_world_matrix(
        target, structural_cache
    )
    translate_signs = {
        attr: _quick_parent_axis_mirror_sign(
            source, target, attr, sym_plane,
            source_parent=source_parent,
            target_parent=target_parent,
        )
        for attr in ("translateX", "translateY", "translateZ")
    }
    parity = (
        translate_signs["translateX"]
        * translate_signs["translateY"]
        * translate_signs["translateZ"]
    )
    return {
        **translate_signs,
        "rotateX": parity * translate_signs["translateX"],
        "rotateY": parity * translate_signs["translateY"],
        "rotateZ": parity * translate_signs["translateZ"],
        "scaleX": 1.0,
        "scaleY": 1.0,
        "scaleZ": 1.0,
    }


def _quick_local_space_attr_map(source, target, sym_plane, attr_map,
                                control_cache=None):
    """Map paired channels through their authored local coordinate systems.

    A facial source X axis can correspond to target Y when one side uses a
    rotated or negatively-scaled parent.  The best signed axis permutation
    handles that without touching the rig or requiring a snapshot.  Rotation
    channels use axial-vector parity so handedness remains correct.
    """
    structural_cache = None
    if isinstance(control_cache, dict):
        structural_cache = control_cache.setdefault(
            "__quick_structural_world_matrices__", {}
        )
    source_parent = _quick_structural_parent_world_matrix(source, structural_cache)
    target_parent = _quick_structural_parent_world_matrix(target, structural_cache)
    source_axes = tuple(_normalize3(axis) for axis in (
        source_parent.x_axis(), source_parent.y_axis(), source_parent.z_axis()
    ))
    target_axes = tuple(_normalize3(axis) for axis in (
        target_parent.x_axis(), target_parent.y_axis(), target_parent.z_axis()
    ))
    normal = sym_plane.normal if sym_plane else (1.0, 0.0, 0.0)
    reflected_axes = tuple(tuple(
        axis[index] - (2.0 * _dot3(axis, normal) * normal[index])
        for index in range(3)
    ) for axis in source_axes)

    best = None
    for permutation in itertools.permutations(range(3)):
        alignments = [
            _dot3(reflected_axes[source_index], target_axes[target_index])
            for source_index, target_index in enumerate(permutation)
        ]
        score = sum(abs(value) for value in alignments)
        if best is None or score > best[0]:
            best = (score, permutation, alignments)
    if best is None:
        return attr_map

    _score, permutation, alignments = best
    signs = [-1.0 if value < 0.0 else 1.0 for value in alignments]
    inversions = sum(
        1 for left in range(3) for right in range(left + 1, 3)
        if permutation[left] > permutation[right]
    )
    permutation_sign = -1.0 if inversions % 2 else 1.0
    axial_parity = permutation_sign * signs[0] * signs[1] * signs[2]

    source_attrs = set(_cached_scalar_attrs(source, control_cache))
    target_attrs = set(_cached_scalar_attrs(target, control_cache))
    axes = "XYZ"
    for source_index, target_index in enumerate(permutation):
        for prefix, multiplier in (
            ("translate", signs[source_index]),
            ("rotate", axial_parity * signs[source_index]),
            ("scale", 1.0),
        ):
            source_attr = "{}{}".format(prefix, axes[source_index])
            target_attr = "{}{}".format(prefix, axes[target_index])
            if source_attr not in source_attrs or target_attr not in target_attrs:
                continue
            attr_map[source_attr] = {
                "target": target_attr,
                "mult": multiplier,
                "mode": "copy_value",
            }
    return attr_map


def _quick_semantic_attr_map(source, target, sym_plane, control_cache=None):
    """Fast channel conventions for common rig control families.

    The mapping deliberately avoids temporary edits, so it also works when
    animation curves or layers own the channels. Precise profiles still use
    numerical default-pose calibration for unusual rigs.
    """
    attr_map = _basic_copy_attr_map(
        source, target, sym_plane, snapshot_cache=control_cache
    )
    source_name = _lower_name(source)
    if source == target:
        center_name = source_name
        if "ikspine" in center_name and "ikhybridspine" not in center_name:
            return attr_map
        if "mouth_main" in center_name:
            return attr_map
        if any(token in center_name for token in (
            "fkheadupper", "fkheadmiddle", "fkheadlower",
        )):
            transform_mults = {
                "translateX": -1.0, "translateY": 1.0, "translateZ": 1.0,
                "rotateX": -1.0, "rotateY": 1.0, "rotateZ": -1.0,
                "scaleX": 1.0, "scaleY": 1.0, "scaleZ": 1.0,
            }
        else:
            center_face_space = any(token in center_name for token in (
                "spine", "chest", "head", "neck", "jaw", "teeth",
                "nose", "hip", "pelvis", "mouth",
            ))
            if not center_face_space:
                return attr_map
            transform_mults = {
                "translateX": 1.0, "translateY": 1.0, "translateZ": -1.0,
                "rotateX": -1.0, "rotateY": -1.0, "rotateZ": 1.0,
                "scaleX": 1.0, "scaleY": 1.0, "scaleZ": 1.0,
            }
    elif "aimeye" in source_name:
        return attr_map
    elif any(token in source_name for token in ("nostril", "nosetril")):
        transform_mults = {
            "translateX": -1.0, "translateY": -1.0, "translateZ": -1.0,
            "rotateX": 1.0, "rotateY": 1.0, "rotateZ": 1.0,
            "scaleX": 1.0, "scaleY": 1.0, "scaleZ": 1.0,
        }
    elif "lip" in source_name:
        transform_mults = {attr: 1.0 for attr in TRANSFORM_ATTRS}
        for attr in ("translateX", "translateY", "translateZ"):
            transform_mults[attr] = _quick_parent_axis_mirror_sign(
                source, target, attr, sym_plane
            )
    elif "eyebrow" in source_name:
        return _quick_local_space_attr_map(
            source, target, sym_plane, attr_map,
            control_cache=control_cache,
        )
    elif source_name.startswith(("upperlid", "lowerlid")):
        # These secondary facial controls already live in mirrored SDK spaces;
        # their displayed channels transfer directly between sides.
        transform_mults = {attr: 1.0 for attr in TRANSFORM_ATTRS}
    elif source_name.startswith("ctrl") and any(
        token in source_name for token in (
            "brow", "eye", "cheek", "nose", "mouth", "lip",
        )
    ):
        return _quick_local_space_attr_map(
            source, target, sym_plane, attr_map,
            control_cache=control_cache,
        )
    elif ("brow" in source_name and "fkbrow" not in source_name
          and "eyebrow" not in source_name):
        transform_mults = {attr: 1.0 for attr in TRANSFORM_ATTRS}
    elif _is_quick_face_control(source):
        transform_mults = {
            "translateX": 1.0,
            "translateY": 1.0,
            "translateZ": -1.0,
            "rotateX": -1.0,
            "rotateY": -1.0,
            "rotateZ": 1.0,
            "scaleX": 1.0,
            "scaleY": 1.0,
            "scaleZ": 1.0,
        }
    elif (_is_fk_copy_control(source)
          or _is_hand_or_finger_copy_control(source)):
        transform_mults = {
            "translateX": -1.0,
            "translateY": -1.0,
            "translateZ": -1.0,
            "rotateX": 1.0,
            "rotateY": 1.0,
            "rotateZ": 1.0,
            "scaleX": 1.0,
            "scaleY": 1.0,
            "scaleZ": 1.0,
        }
    elif _is_bend_like_control(source):
        transform_mults = {
            "translateX": -1.0,
            "translateY": -1.0,
            "translateZ": -1.0,
            "rotateX": 1.0,
            "rotateY": 1.0,
            "rotateZ": 1.0,
            "scaleX": 1.0,
            "scaleY": 1.0,
            "scaleZ": 1.0,
        }
    else:
        return attr_map

    target_attrs = set(_cached_scalar_attrs(target, control_cache))
    for attr, mult in transform_mults.items():
        if attr in target_attrs and _attr_exists(source, attr):
            # Animbot-style quick mirror swaps the displayed local channel
            # values themselves.  Defaults are useful for classification, but
            # must not be treated as a delta origin here (notably on controls
            # whose authored default is non-zero, such as a global/Main).
            attr_map[attr] = {
                "target": attr,
                "mult": mult,
                "mode": "copy_value",
            }
    return attr_map


def _build_basic_mirror_snapshot(seed_controls, rig_name=None, rig_label=None,
                                 expand_rig=False):
    """Build a temporary lightweight snapshot when no calibrated one exists."""
    seeds = [_long_name(ctrl) for ctrl in (seed_controls or []) if cmds.objExists(ctrl)]
    if not seeds:
        return None

    # A quick profile must remain proportional to the current selection.  Full
    # rig discovery belongs exclusively to the explicit snapshot command.
    controls = (_expand_to_candidate_controls(seeds) or seeds) if expand_rig else list(seeds)
    expanded = list(controls)
    for ctrl in list(controls):
        opp = find_opposite_by_name(ctrl)
        if opp and cmds.objExists(opp):
            expanded.append(opp)
    controls = list(dict.fromkeys(ctrl for ctrl in expanded if cmds.objExists(ctrl)))
    if not controls:
        return None

    rig_name = rig_name or get_rig_identifier(controls)
    rig_label = rig_label or get_rig_label(controls)
    control_cache = _build_snapshot_control_cache(controls)
    control_index = _build_snapshot_control_index(controls)
    control_ids, bindings, short_index = _build_profile_bindings(controls)

    pairs_positions = []
    pair_map = {}
    pair_controls = {}
    pair_confidence = {}
    pair_method = {}
    processed = set()

    for ctrl in controls:
        ctrl_short = control_cache.get(ctrl, {}).get("short") or _strip_namespace(ctrl)
        ctrl_id = control_ids.get(ctrl, ctrl_short)
        if ctrl_id in processed:
            continue
        opp = _find_opposite_from_index(ctrl, control_index) or find_opposite_by_name(ctrl)
        if opp and cmds.objExists(opp) and opp in control_cache:
            opp_short = control_cache.get(opp, {}).get("short") or _strip_namespace(opp)
            opp_id = control_ids.get(opp, opp_short)
            pairs_positions.append(_ordered_pair_positions(ctrl, opp, control_cache))
            pair_map[ctrl_id] = opp_id
            pair_map[opp_id] = ctrl_id
            pair_controls[ctrl] = opp
            pair_controls[opp] = ctrl
            pair_confidence[ctrl] = 1.0
            pair_confidence[opp] = 1.0
            pair_method[ctrl] = "name"
            pair_method[opp] = "name"
            processed.add(ctrl_id)
            processed.add(opp_id)

    named_plane = SymmetryPlane.detect_from_pairs(pairs_positions) if pairs_positions else None
    geometric_pairs, geometric_plane, geometric_confidence = _geometry_pair_solution(
        controls,
        control_cache,
        occupied=pair_controls.keys(),
        preferred_plane=named_plane,
    )
    rig_space_plane = _quick_rig_space_plane(controls) if pairs_positions else None
    sym_plane = rig_space_plane or geometric_plane or named_plane or SymmetryPlane()
    for ctrl, opp in geometric_pairs.items():
        if ctrl in pair_controls or opp in pair_controls:
            continue
        ctrl_short = control_cache.get(ctrl, {}).get("short") or _strip_namespace(ctrl)
        opp_short = control_cache.get(opp, {}).get("short") or _strip_namespace(opp)
        ctrl_id = control_ids.get(ctrl, ctrl_short)
        opp_id = control_ids.get(opp, opp_short)
        pair_map[ctrl_id] = opp_id
        pair_map[opp_id] = ctrl_id
        pair_controls[ctrl] = opp
        pair_controls[opp] = ctrl
        confidence = min(
            geometric_confidence.get(ctrl, 0.5),
            geometric_confidence.get(opp, 0.5),
        )
        pair_confidence[ctrl] = confidence
        pair_confidence[opp] = confidence
        pair_method[ctrl] = "geometry"
        pair_method[opp] = "geometry"
    snapshot_data = {
        "version": SNAPSHOT_SCHEMA_VERSION,
        "profile_kind": "quick",
        "calibrated_fingers": True,
        "rig_name": rig_name,
        "rig_label": rig_label,
        "basic_snapshot": True,
        "symmetry_plane": sym_plane.to_dict(),
        "mirror_space": _snapshot_mirror_space(
            list(controls) + list(pair_controls.values())
        ),
        "bindings": bindings,
        "short_index": short_index,
        "controls": {},
        "opposites": {},
    }

    for ctrl in controls:
        ctrl_cache = control_cache.get(ctrl, {})
        ctrl_short = ctrl_cache.get("short") or _strip_namespace(ctrl)
        ctrl_id = control_ids.get(ctrl, ctrl_short)
        opp = pair_controls.get(ctrl)
        opp_short = (control_cache.get(opp, {}).get("short") or _strip_namespace(opp)) if opp else None
        opp_id = control_ids.get(opp, opp_short) if opp else None
        target = opp if opp and cmds.objExists(opp) else ctrl
        wm_rest = _cached_world_mat(ctrl, control_cache)

        control_type = (
            "paired" if opp_short else
            "central" if _control_is_central(
                ctrl, sym_plane, controls, control_cache
            ) else "unpaired"
        )
        snapshot_data["controls"][ctrl_id] = {
            "type": control_type,
            "opposite": opp_id,
            "pair_method": pair_method.get(ctrl),
            "pair_confidence": pair_confidence.get(ctrl, 0.0),
            "rot_order": ctrl_cache.get("rotate_order", "xyz"),
            "rest_world_mat": wm_rest.as_list(),
            "rest_attrs": _quick_default_attrs(ctrl, control_cache),
            "custom_mults": {},
            "structure_fingerprint": _control_structure_fingerprint(ctrl, control_cache),
            "attr_map": _quick_semantic_attr_map(
                ctrl, target, sym_plane, control_cache
            ),
        }
        if opp_id:
            snapshot_data["opposites"][ctrl_id] = opp_id

    snapshot_data["validation"] = {
        "unpaired_controls": sorted(
            name for name, data in snapshot_data["controls"].items()
            if data.get("type") == "unpaired"
        ),
    }

    return snapshot_data


def _snapshot_covers_controls(snapshot, controls):
    if not snapshot or snapshot.get("basic_snapshot"):
        return False
    if snapshot.get("version", 1) < MIN_SUPPORTED_SNAPSHOT_VERSION:
        return False
    snapshot_controls = snapshot.get("controls", {}) or {}
    for control in controls or []:
        if not cmds.objExists(control):
            continue
        control_id = _profile_key_for_control(control, snapshot)
        data = snapshot_controls.get(control_id) if control_id else None
        if not data:
            return False
        if not data.get("attr_map"):
            return False
        if "rest_world_mat" not in data or "rest_attrs" not in data:
            return False
        stored_fingerprint = data.get("structure_fingerprint")
        if (stored_fingerprint
                and stored_fingerprint != _control_structure_fingerprint(control)):
            return False
    return True


def _load_or_build_basic_snapshot(controls, rig_name=None, rig_label=None):
    """Return a saved precise profile, otherwise an instant quick profile."""
    rig_name = rig_name or get_rig_identifier(controls)
    rig_label = rig_label or get_rig_label(controls)
    snapshot = load_snapshot(rig_name)
    if _snapshot_covers_controls(snapshot, controls):
        return snapshot, False

    return _get_quick_profile(
        controls, rig_name=rig_name, rig_label=rig_label
    ), True


def _get_quick_profile(controls, rig_name=None, rig_label=None):
    rig_name = rig_name or get_rig_identifier(controls)
    rig_label = rig_label or get_rig_label(controls)

    cache_key = _quick_profile_cache_key(controls, rig_name)
    snapshot = _quick_profile_cache.get(cache_key)
    if snapshot is None:
        snapshot = _build_basic_mirror_snapshot(
            controls,
            rig_name=rig_name,
            rig_label=rig_label,
            expand_rig=False,
        )
        if snapshot:
            if len(_quick_profile_cache) >= QUICK_PROFILE_CACHE_LIMIT:
                try:
                    _quick_profile_cache.pop(next(iter(_quick_profile_cache)))
                except Exception:
                    _quick_profile_cache.clear()
            _quick_profile_cache[cache_key] = snapshot
    return snapshot


def _prepared_snapshot_groups(controls, force_quick=False):
    prepared = []
    for rig_name, info in _group_controls_by_rig(controls).items():
        if force_quick:
            snapshot = _get_quick_profile(
                info["controls"], rig_name=rig_name, rig_label=info["label"]
            )
            is_basic_snapshot = True
        else:
            snapshot, is_basic_snapshot = _load_or_build_basic_snapshot(
                info["controls"],
                rig_name=rig_name,
                rig_label=info["label"],
            )
        if snapshot:
            prepared.append({
                "rig_name": rig_name,
                "info": info,
                "snapshot": snapshot,
                "sym_plane": _snap_sym_plane(snapshot),
                "is_basic_snapshot": is_basic_snapshot,
            })
    return prepared


def _snap_sym_plane(snapshot):
    """Extract SymmetryPlane from snapshot dict."""
    if snapshot and "symmetry_plane" in snapshot:
        return SymmetryPlane.from_dict(snapshot["symmetry_plane"])
    return SymmetryPlane()   # default YZ


def _path_suffix_match_count(path_parts, reference_parts):
    count = 0
    for path_part, reference_part in zip(reversed(path_parts), reversed(reference_parts)):
        if path_part != reference_part:
            break
        count += 1
    return count


def _resolve_snapshot_anchor(mirror_space, controls):
    if not mirror_space:
        return None
    anchor_path = mirror_space.get("anchor_path")
    if anchor_path and cmds.objExists(anchor_path):
        return anchor_path

    reference_parts = mirror_space.get("anchor_parts") or _dag_parts(anchor_path or "")
    if not reference_parts:
        return None
    leaf = reference_parts[-1]
    candidates = []
    current_common = _common_dag_ancestor(controls)
    if current_common:
        candidates.append(current_common)
    try:
        candidates.extend(cmds.ls(leaf, type="transform", long=True) or [])
    except:
        pass

    best = None
    for candidate in dict.fromkeys(candidates):
        if not candidate or not cmds.objExists(candidate):
            continue
        score = _path_suffix_match_count(_dag_parts(candidate), reference_parts)
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best and best[0] else None


def _transform_point_by_mat4(point, matrix):
    m = matrix._m
    x, y, z = point
    return [
        x * m[0] + y * m[4] + z * m[8] + m[12],
        x * m[1] + y * m[5] + z * m[9] + m[13],
        x * m[2] + y * m[6] + z * m[10] + m[14],
    ]


def _transform_direction_by_mat4(direction, matrix):
    m = matrix._m
    x, y, z = direction
    return [
        x * m[0] + y * m[4] + z * m[8],
        x * m[1] + y * m[5] + z * m[9],
        x * m[2] + y * m[6] + z * m[10],
    ]


def _median(values):
    values = sorted(values)
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) * 0.5


def _infer_runtime_symmetry_plane(snapshot, fallback_plane, controls):
    reference = (controls or [None])[0]
    if not reference or not snapshot:
        return None

    pair_entries = []
    processed = set()
    for control_id, opposite_id in snapshot.get("opposites", {}).items():
        pair_key = tuple(sorted((control_id, opposite_id)))
        if pair_key in processed:
            continue
        processed.add(pair_key)
        source_data = snapshot.get("controls", {}).get(control_id, {})
        target_data = snapshot.get("controls", {}).get(opposite_id, {})
        if not source_data.get("rest_world_mat") or not target_data.get("rest_world_mat"):
            continue
        source = _resolve_profile_control(reference, control_id, snapshot)
        target = _resolve_profile_control(reference, opposite_id, snapshot)
        if not cmds.objExists(source) or not cmds.objExists(target):
            continue
        try:
            rest_source = Mat4(source_data["rest_world_mat"]).translation()
            rest_target = Mat4(target_data["rest_world_mat"]).translation()
            current_source = _world_pos(source)
            current_target = _world_pos(target)
        except:
            continue
        rest_direction = _sub3(rest_target, rest_source)
        current_direction = _sub3(current_target, current_source)
        if _len3(rest_direction) < 0.001 or _len3(current_direction) < 0.001:
            continue
        if _dot3(rest_direction, fallback_plane.normal) < 0.0:
            current_direction = [-value for value in current_direction]
        pair_entries.append((
            _normalize3(current_direction),
            [(current_source[index] + current_target[index]) * 0.5 for index in range(3)],
        ))
        if len(pair_entries) >= 128:
            break

    if len(pair_entries) < 3:
        return None
    median_direction = _normalize3([
        _median([entry[0][index] for entry in pair_entries])
        for index in range(3)
    ])
    aligned = [entry for entry in pair_entries if _dot3(entry[0], median_direction) > 0.8]
    if len(aligned) < 2:
        return None
    normal = _normalize3([
        sum(entry[0][index] for entry in aligned)
        for index in range(3)
    ])
    plane_offset = _median([_dot3(entry[1], normal) for entry in aligned])
    return SymmetryPlane(normal, [normal[index] * plane_offset for index in range(3)])


def _runtime_symmetry_plane(snapshot, fallback_plane, controls):
    mirror_space = snapshot.get("mirror_space", {}) if snapshot else {}
    rest_matrix = mirror_space.get("rest_world_mat") if mirror_space else None
    anchor = _resolve_snapshot_anchor(mirror_space, controls)
    if rest_matrix and anchor:
        try:
            delta = Mat4(rest_matrix).inverse() * _mat4_from_maya(anchor)
            return SymmetryPlane(
                _transform_direction_by_mat4(fallback_plane.normal, delta),
                _transform_point_by_mat4(fallback_plane.point, delta),
            )
        except:
            pass
    return _infer_runtime_symmetry_plane(snapshot, fallback_plane, controls) or fallback_plane


def _snap_opposite(control, snapshot):
    """Get opposite control full name from snapshot."""
    if not snapshot:
        return None
    control_id = _profile_key_for_control(control, snapshot)
    opposite_id = snapshot.get("opposites", {}).get(control_id)
    if not opposite_id:
        return None
    resolved = _resolve_profile_control(control, opposite_id, snapshot)
    if resolved and "|" not in str(control):
        leaf = resolved.rsplit("|", 1)[-1]
        try:
            if len(cmds.ls(leaf, long=True) or []) == 1:
                return leaf
        except Exception:
            pass
    return resolved


def _snap_ctrl_data(control, snapshot):
    """Get per-control snapshot data dict."""
    if not snapshot:
        return None
    control_id = _profile_key_for_control(control, snapshot)
    return snapshot.get("controls", {}).get(control_id) if control_id else None


def _snapshot_rest_matrix(control, snapshot):
    data = _snap_ctrl_data(control, snapshot)
    if data and data.get("rest_world_mat"):
        try:
            return Mat4(data["rest_world_mat"])
        except:
            pass
    return _mat4_from_maya(control)


# ═══════════════════════════════════════════════════════════════════════════════
#                      CORE MIRROR ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _legacy_finger_attr_map(control, base_map=None):
    """Return the stable paired local-channel mapping for finger controls."""
    attr_map = {
        attr: dict(spec) for attr, spec in (base_map or {}).items()
    }
    is_thumb = any(token in _lower_name(control) for token in ("thumb", "thb", "thm"))
    multipliers = {
        "translateX": -1.0,
        "translateY": -1.0,
        "translateZ": -1.0,
        "rotateX": 1.0 if is_thumb else -1.0,
        "rotateY": -1.0 if is_thumb else 1.0,
        "rotateZ": 1.0,
    }
    for attr, mult in multipliers.items():
        attr_map[attr] = {"target": attr, "mult": mult, "mode": "copy_value"}
    return attr_map


def _effective_attr_map(source, target, snapshot, sym_plane, source_data=None):
    """Resolve a snapshot map, correcting pre-finger-calibration snapshots."""
    attr_map = (source_data.get("attr_map") if source_data else None)
    if not attr_map:
        attr_map = _default_attr_map(source, target, sym_plane)
    # Do not use a momentary snapshot pose as a finger rest pose. This also
    # upgrades pre-existing snapshots, including ones made with a curled hand.
    if _is_finger_control(source) and not snapshot.get("calibrated_fingers"):
        return _legacy_finger_attr_map(source, attr_map)
    return attr_map


def _is_modifiable(control, attr):
    try:
        return bool(cmds.getAttr(f"{control}.{attr}", settable=True))
    except:
        return False


def _has_unit_local_scale(control):
    for attr in SCALE_ATTRS:
        if not _attr_exists(control, attr):
            continue
        if abs(_safe_scalar_attr(control, attr, 1.0) - 1.0) > 0.0001:
            return False
    return True


def _can_apply_world_mirror(source, target, attrs_filter=None, snapshot=None):
    if snapshot and snapshot.get("profile_kind") == "quick":
        return False
    if (_is_fk_copy_control(source) or _is_fk_copy_control(target)
            or _is_hand_or_finger_copy_control(source)
            or _is_hand_or_finger_copy_control(target)):
        return False
    if not (_is_primary_world_space_ik_control(source)
            or _is_primary_world_space_ik_control(target)):
        return False
    if attrs_filter:
        requested = set(attrs_filter)
        if not set(POSE_TRANSFORM_ATTRS).issubset(requested):
            return False
        if requested.intersection(SCALE_ATTRS):
            return False
    if not all(_is_modifiable(target, attr) for attr in TRANSFORM_ATTRS):
        return False
    return _has_unit_local_scale(source) and _has_unit_local_scale(target)


def _apply_world_matrix_pose(target, desired_world):
    before = _mat4_from_maya(target)
    try:
        cmds.xform(target, worldSpace=True, matrix=desired_world.as_list())
    except:
        return False
    if _matrix_error(desired_world, _mat4_from_maya(target)) <= 0.01:
        return True
    try:
        cmds.xform(target, worldSpace=True, matrix=before.as_list())
    except:
        pass
    return False


def _compute_mirror_transform_values(ctrl, target, sym_plane):
    wm_src = _mat4_from_maya(ctrl)
    return _compute_mirror_transform_values_from_mat(wm_src, target, sym_plane)


def _compute_mirror_transform_values_from_mat(wm_src, target, sym_plane):
    wm_reflected = sym_plane.reflect_mat4(wm_src)
    return world_matrix_to_local_transforms(target, wm_reflected)


def _compute_mirror_custom_values(ctrl, target, snapshot, attrs_filter=None):
    ctrl_data = _snap_ctrl_data(ctrl, snapshot)
    custom_mults = ctrl_data.get('custom_mults', {}) if ctrl_data else {}
    source_rest = ctrl_data.get("rest_attrs", {}) if ctrl_data else {}
    values = {}

    for attr in (cmds.listAttr(ctrl, keyable=True) or []):
        if attr in ATTRIBUTES_TO_IGNORE or attr in TRANSFORM_ATTRS:
            continue
        if attrs_filter and attr not in attrs_filter:
            continue
        if not _attr_exists(target, attr) or not _is_modifiable(target, attr):
            continue
        try:
            val = cmds.getAttr(f"{ctrl}.{attr}")
            if isinstance(val, (list, tuple)):
                continue
            values[attr] = float(val) * custom_mults.get(attr, 1.0)
        except:
            pass
    return values


def _transform_attrs_requested(attrs_filter):
    attrs = set(attrs_filter or TRANSFORM_ATTRS)
    if not attrs_filter:
        attrs.difference_update(SCALE_ATTRS)
    return {a for a in attrs if a in TRANSFORM_ATTRS and a not in SCALE_ATTRS}


def _looks_like_ik_translate_control(control):
    name = _strip_namespace(control).lower()
    tokens = ("ik", "pv", "pole", "vector", "foot", "ankle", "heel", "toe", "hand", "wrist")
    return any(token in name for token in tokens)


def _is_primary_world_space_ik_control(control):
    name = _strip_namespace(control).lower()
    if any(token in name for token in (
        "roll", "toe", "heel", "ball", "measure", "messure",
        "bend", "micro", "locator",
    )):
        return False
    return (
        name.startswith(("ikarm", "ikleg", "ikhand", "ikfoot"))
        or name.startswith(("pole", "pv_", "pv", "vector"))
    )


def _has_translate_pose_delta(control, snapshot):
    data = _snap_ctrl_data(control, snapshot)
    rest = data.get("rest_attrs", {}) if data else {}
    for attr in ("translateX", "translateY", "translateZ"):
        if not _attr_exists(control, attr):
            continue
        current = _safe_scalar_attr(control, attr, rest.get(attr, 0.0))
        if abs(current - rest.get(attr, 0.0)) > 0.0001:
            return True
    return False


def _allow_dynamic_translate(control, snapshot, attrs_filter=None):
    if attrs_filter and any(attr.startswith("translate") for attr in attrs_filter):
        return True
    return _looks_like_ik_translate_control(control) or _has_translate_pose_delta(control, snapshot)


def _filtered_transform_candidate(values, target, attrs_filter=None):
    requested = _transform_attrs_requested(attrs_filter)
    result = {}
    for attr, value in values.items():
        if attr not in requested:
            continue
        if attr in SCALE_ATTRS:
            continue
        if not _attr_exists(target, attr) or not _is_modifiable(target, attr):
            continue
        if attr in ROTATE_ATTRS:
            value = _closest_angle_to(value, _safe_scalar_attr(target, attr))
        result[attr] = value
    return result


def _solve_dynamic_transform_values(ctrl, target, snapshot, sym_plane, base_values,
                                    attrs_filter=None, is_central=False):
    """
    Runtime matrix solving is intentionally disabled for production mirroring.
    Some rigs evaluate constraints/nonlinear spaces explosively when temporary
    matrix-derived values are tested. Keep mirror application strictly snapshot
    based and non-destructive.
    """
    return base_values


def _mirror_pair_width(ctrl, target, snapshot):
    try:
        return _dist3(_snapshot_rest_matrix(ctrl, snapshot).translation(),
                      _snapshot_rest_matrix(target, snapshot).translation())
    except:
        return _dist3(_world_pos(ctrl), _world_pos(target))


def _source_world_delta(ctrl, snapshot):
    try:
        return _dist3(_mat4_from_maya(ctrl).translation(),
                      _snapshot_rest_matrix(ctrl, snapshot).translation())
    except:
        return 0.0


def _sanitize_mirror_values(ctrl, target, snapshot, values):
    """Reject values that are likely to explode a rig."""
    if not values:
        return {}
    pair_width = _mirror_pair_width(ctrl, target, snapshot)
    source_delta = _source_world_delta(ctrl, snapshot)
    max_translate_delta = max(10.0, pair_width * 2.5, source_delta * 4.0)

    target_data = _snap_ctrl_data(target, snapshot)
    target_rest = target_data.get("rest_attrs", {}) if target_data else {}
    safe = {}

    for attr, value in values.items():
        try:
            value = float(value)
        except:
            continue
        if not math.isfinite(value):
            continue

        if attr.startswith("translate"):
            rest = target_rest.get(attr, _safe_scalar_attr(target, attr))
            if ctrl == target:
                current = _safe_scalar_attr(target, attr)
                max_translate_delta = max(
                    max_translate_delta,
                    abs(current) * 2.5 + 1.0,
                    abs(rest) * 2.5 + 1.0,
                )
            if abs(value - rest) > max_translate_delta:
                continue
        elif attr in ROTATE_ATTRS:
            current = _safe_scalar_attr(target, attr)
            value = _closest_angle_to(value, current)
            if abs(value - current) > 181.0:
                continue

        safe[attr] = value

    return safe


def compute_mirror_values(ctrl, target=None, snapshot=None, sym_plane=None,
                          attrs_filter=None, is_central=False):
    if not ctrl or not cmds.objExists(ctrl):
        return {}
    if not snapshot:
        snapshot = load_snapshot(get_rig_identifier([ctrl]))
    if not sym_plane:
        sym_plane = _snap_sym_plane(snapshot)
    if not snapshot:
        return {}
    if target is None:
        target = ctrl if is_central else (_snap_opposite(ctrl, snapshot) or find_opposite_smart(ctrl, sym_plane))
    if not target or not cmds.objExists(target):
        return {}

    ctrl_data = _snap_ctrl_data(ctrl, snapshot)
    target_data = _snap_ctrl_data(target, snapshot)
    include_default_scale = bool(
        snapshot and snapshot.get("profile_kind") == "quick"
    )
    values = {}

    if ctrl_data and target_data and (
        (ctrl_data.get("rest_attrs") and target_data.get("rest_attrs"))
        or ctrl_data.get("attr_map")
    ):
        source_rest = ctrl_data.get("rest_attrs", {})
        target_rest = target_data.get("rest_attrs", {})
        attr_map = _effective_attr_map(
            ctrl, target, snapshot, sym_plane, source_data=ctrl_data
        )

        for source_attr, spec in attr_map.items():
            target_attr = spec.get("target", source_attr)
            if attrs_filter and source_attr not in attrs_filter and target_attr not in attrs_filter:
                continue
            if (target_attr in SCALE_ATTRS
                    and not include_default_scale
                    and not _attrs_allow_scale(attrs_filter)):
                continue
            if not _attr_exists(ctrl, source_attr) or not _attr_exists(target, target_attr):
                continue
            current = _safe_scalar_attr(ctrl, source_attr, source_rest.get(source_attr, 0.0))
            source_base = source_rest.get(source_attr, 0.0)
            target_base = target_rest.get(target_attr, _safe_scalar_attr(target, target_attr))
            mult = float(spec.get("mult", 1.0))
            if spec.get("skip_zero_delta") and abs(current - source_base) <= 0.0001:
                continue
            if spec.get("mode") == "copy_value":
                values[target_attr] = current * mult
            else:
                values[target_attr] = target_base + ((current - source_base) * mult)
        values = _sanitize_mirror_values(ctrl, target, snapshot, values)
        return _solve_dynamic_transform_values(
            ctrl, target, snapshot, sym_plane, values,
            attrs_filter=attrs_filter, is_central=is_central
        )

    attr_map = _default_attr_map(ctrl, target, sym_plane)
    for source_attr, spec in attr_map.items():
        target_attr = spec.get("target", source_attr)
        if attrs_filter and source_attr not in attrs_filter and target_attr not in attrs_filter:
            continue
        if (target_attr in SCALE_ATTRS
                and not include_default_scale
                and not _attrs_allow_scale(attrs_filter)):
            continue
        current = _safe_scalar_attr(ctrl, source_attr)
        values[target_attr] = current * float(spec.get("mult", 1.0))
    values = _sanitize_mirror_values(ctrl, target, snapshot, values)
    return _solve_dynamic_transform_values(
        ctrl, target, snapshot, sym_plane, values,
        attrs_filter=attrs_filter, is_central=is_central
    )


def get_opposite_control(control, snapshot=None):
    if not snapshot:
        snapshot = load_snapshot(get_rig_identifier([control]))
    sym_plane = _snap_sym_plane(snapshot)
    return _snap_opposite(control, snapshot) or find_opposite_smart(control, sym_plane)


def _apply_mirror_to_control(ctrl, opposite, sym_plane, snapshot,
                              attrs_filter=None, is_central=False):
    """
    Core calibrated mirror for a single control.
    
    ctrl          : source control
    opposite      : target control (same as ctrl if central)
    sym_plane     : SymmetryPlane
    snapshot      : loaded snapshot dict (can be None)
    attrs_filter  : list of attrs to process (None = all keyable)
    is_central    : True -> mirror onto itself
    
    Returns count of attributes set.
    """
    count = 0

    target = ctrl if is_central else opposite
    local_vals = compute_mirror_values(
        ctrl, target=target, snapshot=snapshot, sym_plane=sym_plane,
        attrs_filter=attrs_filter, is_central=is_central
    )

    # Clamp scale if the user explicitly mirrors scale.
    for s in ('scaleX', 'scaleY', 'scaleZ'):
        if s in local_vals:
            val = local_vals[s]
            if val < 0.001:
                # Restore from snapshot rest scale or default to 1
                ctrl_data = _snap_ctrl_data(target, snapshot)
                if ctrl_data:
                    rest_wm   = Mat4(ctrl_data['rest_world_mat'])
                    rest_scale = _extract_3x3_scale(rest_wm)
                    axes = {'scaleX':0,'scaleY':1,'scaleZ':2}
                    local_vals[s] = rest_scale[axes[s]]
                else:
                    local_vals[s] = 1.0

    # ── 5. Apply transform values ─────────────────────────────────────────────
    attrs_to_set = _attrs_to_apply(local_vals, attrs_filter)
    for attr in attrs_to_set:
        if attr not in local_vals:
            continue
        if attr in ATTRIBUTES_TO_IGNORE:
            continue
        try:
            if not _set_scalar_attr(target, attr, local_vals[attr]):
                continue
            count += 1
        except:
            pass

    # ── 6. Mirror custom attributes ───────────────────────────────────────────
    ctrl_data = _snap_ctrl_data(ctrl, snapshot)
    custom_mults = ctrl_data.get('custom_mults', {}) if ctrl_data else {}
    source_rest = ctrl_data.get("rest_attrs", {}) if ctrl_data else {}

    all_keyable = cmds.listAttr(ctrl, keyable=True) or []
    custom_attrs = [a for a in all_keyable
                    if a not in ATTRIBUTES_TO_IGNORE
                    and a not in TRANSFORM_ATTRS]

    for attr in custom_attrs:
        if attr in local_vals:
            continue
        if attrs_filter and attr not in attrs_filter:
            continue
        if not _is_modifiable(target, attr):
            continue
        try:
            val  = cmds.getAttr(f"{ctrl}.{attr}")
            if _is_bend_like_control(ctrl) and abs(float(val) - source_rest.get(attr, 0.0)) <= 0.0001:
                continue
            mult = custom_mults.get(attr, 1.0)
            cmds.setAttr(f"{target}.{attr}", val * mult)
            count += 1
        except:
            pass

    return count


# ═══════════════════════════════════════════════════════════════════════════════
#                      PUBLIC API FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def mirror(*args):
    """Mirror the selected pose, or the selected time-slider range when present."""
    try:
        mirror_started = time.perf_counter()
        selected = cmds.ls(selection=True)
        if not selected:
            cmds.warning("AnimKey: Select at least one control.")
            return

        selected_channels = _get_selected_channels()
        start, end, has_selected_range = _time_slider_or_playback_range()
        if has_selected_range:
            total = mirror_selected_animation(
                selected,
                attrs_filter=selected_channels,
                time_range=(start, end),
                manage_undo=True,
            )
            if total:
                cmds.inViewMessage(
                    amg=(
                        "<span style='color:#88c0d0'>Mirrored selected animation "
                        "(%s-%s): %s keys</span>" % (start, end, total)
                    ),
                    pos="topCenter", fade=True, fadeStayTime=1000,
                )
            else:
                cmds.warning(
                    "AnimKey Mirror: No animation keys found in selected range %s-%s."
                    % (start, end)
                )
            return total

        graph_total = _mirror_selected_graph_keys(
            selected,
            attrs_filter=selected_channels,
            manage_undo=True,
        )
        if graph_total is not None:
            if graph_total:
                cmds.inViewMessage(
                    amg=(
                        "<span style='color:#88c0d0'>Mirrored selected graph keys: "
                        "%s</span>" % graph_total
                    ),
                    pos="topCenter", fade=True, fadeStayTime=900,
                )
            else:
                cmds.warning("AnimKey Mirror: No mirrored channels found for selected graph keys.")
            return graph_total

        prepared_groups = _prepared_snapshot_groups(selected)
        total = 0

        _open_animkey_undo_chunk("AnimKey Mirror")
        try:
            for prepared in prepared_groups:
                info = prepared["info"]
                count, _ = _mirror_selected_pose_two_phase(
                    info["controls"],
                    prepared["snapshot"],
                    prepared["sym_plane"],
                    selected_channels,
                )
                total += count
        finally:
            _close_animkey_undo_chunk()

        if total:
            elapsed_ms = (time.perf_counter() - mirror_started) * 1000.0
            cmds.inViewMessage(
                amg=(f"<span style='color:#88c0d0'>Mirror: "
                     f"{total} values ({elapsed_ms:.1f} ms)</span>"),
                pos='topCenter', fade=True, fadeStayTime=800
            )
    except Exception as e:
        cmds.warning(f"AnimKey Mirror: {e}")
        import traceback; traceback.print_exc()


def _mirror_to_side(target_side, controls=None, attrs_filter=None):
    """Run Mirror Pose only from the selected source side to ``target_side``."""
    controls = list(controls) if controls else (cmds.ls(selection=True) or [])
    if not controls:
        cmds.warning("AnimKey: Select source-side controls to mirror.")
        return 0

    prepared_groups = _prepared_snapshot_groups(controls)
    _open_animkey_undo_chunk("AnimKey Mirror To %s" % target_side.title())
    try:
        total = 0
        for prepared in prepared_groups:
            info = prepared["info"]
            snapshot = prepared["snapshot"]
            sym_plane = prepared["sym_plane"]
            runtime_plane = _runtime_symmetry_plane(
                snapshot, sym_plane, info["controls"]
            )
            source_controls = []
            for source in info["controls"]:
                target = (
                    (_snap_opposite(source, snapshot) if snapshot else None)
                    or find_opposite_smart(source, sym_plane)
                )
                if (target and cmds.objExists(target)
                        and _control_side(target, runtime_plane) == target_side):
                    source_controls.append(source)
            count, _ = _mirror_selected_pose_two_phase(
                source_controls, snapshot, sym_plane, attrs_filter
            )
            total += count
        if total:
            cmds.inViewMessage(
                amg="<span style='color:#88c0d0'>Mirrored to %s: %s values</span>" % (
                    target_side, total
                ),
                pos="topCenter", fade=True, fadeStayTime=800,
            )
        else:
            cmds.warning("AnimKey: No opposite controls found for Mirror to %s." % target_side.title())
        return total
    finally:
        _close_animkey_undo_chunk()


def mirror_to_left(*args):
    """Mirror selected right-side controls onto their left-side counterparts."""
    return _mirror_to_side("left")


def mirror_to_right(*args):
    """Mirror selected left-side controls onto their right-side counterparts."""
    return _mirror_to_side("right")


def quick_mirror(*args):
    """Mirror the current pose using only the fast in-memory rig profile."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.quick_mirror"):
        return None

    selected = cmds.ls(selection=True) or []
    if not selected:
        cmds.warning("AnimKey: Select controls to quick mirror.")
        return 0

    started = time.perf_counter()
    selected_channels = _get_selected_channels()
    prepared_groups = _prepared_snapshot_groups(selected, force_quick=True)
    total = 0
    _open_animkey_undo_chunk("AnimKey Quick Mirror")
    try:
        for prepared in prepared_groups:
            info = prepared["info"]
            count, _ = _mirror_selected_pose_two_phase(
                info["controls"],
                prepared["snapshot"],
                prepared["sym_plane"],
                selected_channels,
            )
            total += count
    finally:
        _close_animkey_undo_chunk()

    if total:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        cmds.inViewMessage(
            amg=("<span style='color:#88c0d0'>Quick Mirror: {} values "
                 "({:.1f} ms)</span>".format(total, elapsed_ms)),
            pos="topCenter", fade=True, fadeStayTime=800,
        )
    else:
        cmds.warning("AnimKey Quick Mirror: No opposite controls found.")
    return total


def _mirror_pair_values_from_matrix(source_mat, source_ctrl, target_ctrl,
                                    sym_plane, snapshot, attrs_filter=None):
    return compute_mirror_values(
        source_ctrl, target=target_ctrl, snapshot=snapshot, sym_plane=sym_plane,
        attrs_filter=attrs_filter, is_central=(source_ctrl == target_ctrl)
    )


def _apply_values_to_control(target, values, attrs_filter=None,
                             excluded_attrs=None, include_scale=False,
                             prevalidated=False):
    count = 0
    excluded_attrs = set(excluded_attrs or ())
    for attr in _attrs_to_apply(
            values, attrs_filter, include_scale=include_scale):
        if attr in excluded_attrs or attr not in values:
            continue
        if prevalidated:
            try:
                cmds.setAttr(f"{target}.{attr}", values[attr])
                count += 1
                continue
            except Exception:
                pass
        if _set_scalar_attr(target, attr, values[attr]):
            count += 1
    return count


def _capture_pending_mirror_state(pending, attrs_filter=None, include_scale=False):
    scalar_values = {}
    world_matrices = {}
    for target, values, desired_world in pending:
        if not target or not cmds.objExists(target):
            continue
        if desired_world is not None and target not in world_matrices:
            world_matrices[target] = _mat4_from_maya(target)
        for attr in _attrs_to_apply(
                values, attrs_filter, include_scale=include_scale):
            key = (target, attr)
            if key not in scalar_values and _attr_exists(target, attr):
                scalar_values[key] = _safe_scalar_attr(target, attr)
    return scalar_values, world_matrices


def _restore_pending_mirror_state(scalar_values, world_matrices):
    # Restore matrices first, then exact scalar values so constrained/local
    # channels finish in the same state they had before the transaction.
    for target, matrix in world_matrices.items():
        try:
            cmds.xform(target, worldSpace=True, matrix=matrix.as_list())
        except Exception:
            pass
    for (target, attr), value in scalar_values.items():
        try:
            if cmds.objExists(f"{target}.{attr}") and _is_modifiable(target, attr):
                cmds.setAttr(f"{target}.{attr}", value)
        except Exception:
            pass


def _apply_pending_mirror(pending, attrs_filter=None, include_scale=False):
    """Validate/apply a two-phase mirror packet with rollback on failure."""
    clean_pending = []
    for target, values, desired_world in pending:
        if not target or not cmds.objExists(target):
            continue
        clean_values = {}
        for attr, value in (values or {}).items():
            try:
                value = float(value)
            except Exception:
                continue
            if math.isfinite(value) and _attr_exists(target, attr) and _is_modifiable(target, attr):
                # Avoid expensive DG dirtiness for channels that already hold
                # the mirrored value.  A full-character quick mirror commonly
                # contains hundreds of zero/default channels, so this is the
                # main difference between interactive and sluggish feedback.
                if abs(_safe_scalar_attr(target, attr, value) - value) <= 1e-10:
                    continue
                clean_values[attr] = value
        if clean_values or desired_world is not None:
            clean_pending.append((target, clean_values, desired_world))

    scalar_values, world_matrices = _capture_pending_mirror_state(
        clean_pending, attrs_filter, include_scale=include_scale
    )
    total = 0
    touched = {}
    try:
        for target, values, desired_world in clean_pending:
            attrs_to_apply = _attrs_to_apply(
                values, attrs_filter, include_scale=include_scale
            )
            world_applied = bool(
                desired_world is not None
                and _apply_world_matrix_pose(target, desired_world)
            )
            excluded_attrs = set(POSE_TRANSFORM_ATTRS) if world_applied else set()
            if include_scale:
                count = _apply_values_to_control(
                    target, values, attrs_filter, excluded_attrs,
                    include_scale=True,
                    prevalidated=True,
                )
            else:
                count = _apply_values_to_control(
                    target, values, attrs_filter, excluded_attrs,
                    prevalidated=True,
                )
            if world_applied:
                transformed_attrs = set(POSE_TRANSFORM_ATTRS).intersection(attrs_to_apply)
                count += len(transformed_attrs)
                attrs_to_apply = [
                    attr for attr in attrs_to_apply if attr not in excluded_attrs
                ]
                attrs_to_apply.extend(sorted(transformed_attrs))
            total += count
            if count:
                touched.setdefault(target, set()).update(attrs_to_apply)
    except BaseException:
        _restore_pending_mirror_state(scalar_values, world_matrices)
        raise
    return total, touched


def _selected_mirror_directions(controls, snapshot, sym_plane):
    """Resolve the same ordered source/target pairs for pose and animation.

    A single selected side mirrors only to its counterpart.  When both sides
    of a pair are selected, both directions are returned so callers can make a
    proper two-phase swap after capturing every source value first.
    """
    selected = [ctrl for ctrl in controls if cmds.objExists(ctrl)]
    selected_keys = {_long_name(ctrl) for ctrl in selected}
    processed_pairs = set()
    directions = []

    for ctrl in selected:
        opposite = (
            (_snap_opposite(ctrl, snapshot) if snapshot else None) or
            find_opposite_smart(ctrl, sym_plane)
        )
        if opposite and cmds.objExists(opposite):
            pair_key = tuple(sorted((_long_name(ctrl), _long_name(opposite))))
            if pair_key in processed_pairs:
                continue
            processed_pairs.add(pair_key)
            directions.append((ctrl, opposite, False))
            if _long_name(opposite) in selected_keys:
                directions.append((opposite, ctrl, False))
        elif (_snap_ctrl_data(ctrl, snapshot) or {}).get("type") == "central":
            directions.append((ctrl, ctrl, True))

    return directions


def _mirror_selected_pose_two_phase(controls, snapshot, sym_plane, attrs_filter=None):
    selected = [ctrl for ctrl in controls if cmds.objExists(ctrl)]
    directions = _selected_mirror_directions(selected, snapshot, sym_plane)
    if not directions:
        return 0, {}
    pending = []
    runtime_plane = _runtime_symmetry_plane(snapshot, sym_plane, selected)
    source_matrices = {
        source: _mat4_from_maya(source)
        for source, _, _ in directions
    }

    def add_pending(source, target, is_central=False):
        values = compute_mirror_values(
            source, target=target, snapshot=snapshot, sym_plane=sym_plane,
            attrs_filter=attrs_filter, is_central=is_central
        )
        desired_world = None
        if _can_apply_world_mirror(
                source, target, attrs_filter, snapshot=snapshot):
            desired_world = runtime_plane.reflect_mat4(source_matrices[source])
        pending.append((target, values, desired_world))

    for source, target, is_central in directions:
        add_pending(source, target, is_central=is_central)

    return _apply_pending_mirror(
        pending,
        attrs_filter,
        include_scale=bool(snapshot and snapshot.get("profile_kind") == "quick"),
    )


def _mirror_current_pose_bidirectional(controls, snapshot, sym_plane, attrs_filter=None):
    processed = set()
    pairs = []
    central = []

    for ctrl in controls:
        if not cmds.objExists(ctrl):
            continue
        opp = _snap_opposite(ctrl, snapshot) if snapshot else find_opposite_smart(ctrl, sym_plane)
        if opp and cmds.objExists(opp):
            pair_key = tuple(sorted((_long_name(ctrl), _long_name(opp))))
            if pair_key in processed:
                continue
            processed.add(pair_key)
            pairs.append((ctrl, opp))
        else:
            ctrl_data = _snap_ctrl_data(ctrl, snapshot) or {}
            if ctrl_data.get("type") == "central":
                central.append(ctrl)

    matrices = {}
    for a, b in pairs:
        matrices[a] = _mat4_from_maya(a)
        matrices[b] = _mat4_from_maya(b)
    for ctrl in central:
        matrices[ctrl] = _mat4_from_maya(ctrl)

    total = 0
    touched = set()
    for a, b in pairs:
        vals_a = _mirror_pair_values_from_matrix(matrices[b], b, a, sym_plane, snapshot, attrs_filter)
        vals_b = _mirror_pair_values_from_matrix(matrices[a], a, b, sym_plane, snapshot, attrs_filter)
        total += _apply_values_to_control(a, vals_a, attrs_filter)
        total += _apply_values_to_control(b, vals_b, attrs_filter)
        touched.update((a, b))

    for ctrl in central:
        vals = _mirror_pair_values_from_matrix(matrices[ctrl], ctrl, ctrl, sym_plane, snapshot, attrs_filter)
        total += _apply_values_to_control(ctrl, vals, attrs_filter)
        touched.add(ctrl)

    return total, touched


def _time_slider_or_playback_range():
    try:
        slider = mel.eval('$tmp=$gPlayBackSlider')
        if cmds.timeControl(slider, q=True, rangeVisible=True):
            selected_range = cmds.timeControl(slider, q=True, rangeArray=True) or []
            if len(selected_range) >= 2:
                start, end = float(selected_range[0]), float(selected_range[1])
                if end > start:
                    end -= 1.0
                if end >= start:
                    return start, end, True
    except:
        pass
    return (
        float(cmds.playbackOptions(q=True, min=True)),
        float(cmds.playbackOptions(q=True, max=True)),
        False,
    )


def _animation_key_times(controls, time_range=None):
    if time_range:
        start, end = time_range
    else:
        start = cmds.playbackOptions(q=True, min=True)
        end = cmds.playbackOptions(q=True, max=True)
    times = set()
    layer_name = curve_transfer.active_animation_layer()
    for ctrl in controls:
        for attr in (cmds.listAttr(ctrl, keyable=True) or []):
            curve = curve_transfer.resolve_anim_curve(
                f"{ctrl}.{attr}",
                layer_name=layer_name,
            )
            if not curve:
                continue
            try:
                keys = cmds.keyframe(
                    curve,
                    q=True,
                    time=(start, end),
                    timeChange=True,
                ) or []
                for key in keys:
                    times.add(float(key))
            except:
                pass
    return sorted(times)


def _attribute_key_times(control, attr, time_range=None):
    if time_range:
        start, end = time_range
    else:
        start = cmds.playbackOptions(q=True, min=True)
        end = cmds.playbackOptions(q=True, max=True)
    curve = curve_transfer.resolve_anim_curve(
        f"{control}.{attr}",
        layer_name=curve_transfer.active_animation_layer(),
    )
    if not curve:
        return []
    try:
        return sorted({float(t) for t in (cmds.keyframe(
            curve, q=True, time=(start, end), timeChange=True
        ) or [])})
    except:
        return []


def _capture_key_tangent(control, attr, time_value, value_mult=1.0):
    tangent = {}
    try:
        in_type = cmds.keyTangent(
            control, attribute=attr, q=True, time=(time_value, time_value), inTangentType=True
        ) or []
        out_type = cmds.keyTangent(
            control, attribute=attr, q=True, time=(time_value, time_value), outTangentType=True
        ) or []
        if in_type:
            tangent["itt"] = in_type[0]
        if out_type:
            tangent["ott"] = out_type[0]
    except:
        pass
    angle_sign = -1.0 if value_mult < 0 else 1.0
    for query_flag, key in (
        ("inAngle", "ia"),
        ("outAngle", "oa"),
        ("inWeight", "iw"),
        ("outWeight", "ow"),
        ("lock", "lock"),
        ("weightLock", "weightLock"),
    ):
        try:
            values = cmds.keyTangent(
                control, attribute=attr, q=True, time=(time_value, time_value), **{query_flag: True}
            ) or []
            if not values:
                continue
            value = values[0]
            if key in ("ia", "oa"):
                value = float(value) * angle_sign
            tangent[key] = value
        except:
            pass
    return tangent


def _apply_key_tangent(control, attr, time_value, tangent):
    if not tangent:
        return
    type_kwargs = {}
    if tangent.get("itt"):
        type_kwargs["inTangentType"] = tangent["itt"]
    if tangent.get("ott"):
        type_kwargs["outTangentType"] = tangent["ott"]
    if type_kwargs:
        try:
            cmds.keyTangent(control, attribute=attr, time=(time_value, time_value), **type_kwargs)
        except:
            pass

    shape_kwargs = {}
    for key, maya_key in (
        ("ia", "inAngle"),
        ("oa", "outAngle"),
        ("iw", "inWeight"),
        ("ow", "outWeight"),
        ("lock", "lock"),
        ("weightLock", "weightLock"),
    ):
        if key in tangent:
            shape_kwargs[maya_key] = tangent[key]
    if not shape_kwargs:
        return
    try:
        cmds.keyTangent(control, attribute=attr, time=(time_value, time_value), **shape_kwargs)
    except:
        pass


def _animation_channel_jobs_for_direction(source, target, snapshot, sym_plane,
                                          attrs_filter=None, is_central=False):
    source_data = _snap_ctrl_data(source, snapshot)
    target_data = _snap_ctrl_data(target, snapshot)
    source_rest = source_data.get("rest_attrs", {}) if source_data else {}
    target_rest = target_data.get("rest_attrs", {}) if target_data else {}
    attr_map = _effective_attr_map(
        source, target, snapshot, sym_plane, source_data=source_data
    )
    jobs = []

    for source_attr, spec in (attr_map or {}).items():
        target_attr = spec.get("target", source_attr)
        if attrs_filter and source_attr not in attrs_filter and target_attr not in attrs_filter:
            continue
        if target_attr in SCALE_ATTRS and not _attrs_allow_scale(attrs_filter):
            continue
        if not _attr_exists(source, source_attr) or not _attr_exists(target, target_attr):
            continue
        if not _is_modifiable(target, target_attr):
            continue
        jobs.append({
            "source": source,
            "target": target,
            "source_attr": source_attr,
            "target_attr": target_attr,
            "source_rest": source_rest.get(source_attr, 0.0),
            "target_rest": target_rest.get(target_attr, _safe_scalar_attr(target, target_attr)),
            "mult": float(spec.get("mult", 1.0)),
            "mode": spec.get("mode"),
            "skip_zero_delta": bool(spec.get("skip_zero_delta")),
            "is_central": is_central,
        })
    return jobs


def _animation_mirror_channel_jobs(controls, snapshot, sym_plane, attrs_filter=None):
    jobs = []
    for source, target, is_central in _selected_mirror_directions(
            controls, snapshot, sym_plane):
        jobs.extend(_animation_channel_jobs_for_direction(
            source, target, snapshot, sym_plane, attrs_filter,
            is_central=is_central,
        ))
    return jobs


def _selected_animation_mirror_channel_jobs(controls, snapshot, sym_plane,
                                             attrs_filter=None):
    """Build animation work from the exact same pairs used by Mirror Pose."""
    jobs = []
    for source, target, is_central in _selected_mirror_directions(
            controls, snapshot, sym_plane):
        jobs.extend(_animation_channel_jobs_for_direction(
            source, target, snapshot, sym_plane, attrs_filter,
            is_central=is_central,
        ))
    return jobs


def _value_from_animation_job(job):
    current = _safe_scalar_attr(
        job["source"], job["source_attr"], job.get("source_rest", 0.0)
    )
    source_base = job.get("source_rest", 0.0)
    if job.get("skip_zero_delta") and abs(current - source_base) <= 0.0001:
        return None
    if job.get("mode") == "copy_value":
        return current * job.get("mult", 1.0)
    return job.get("target_rest", 0.0) + ((current - source_base) * job.get("mult", 1.0))


def _expand_controls_with_opposites(controls, snapshot, sym_plane):
    expanded = []
    seen = set()
    for ctrl in controls:
        if not cmds.objExists(ctrl):
            continue
        opp = (_snap_opposite(ctrl, snapshot) if snapshot else None) or find_opposite_smart(ctrl, sym_plane)
        for item in (ctrl, opp):
            if item and cmds.objExists(item) and item not in seen:
                expanded.append(item)
                seen.add(item)
    return expanded


def _key_touched_controls(controls, attrs_filter=None):
    if isinstance(controls, dict):
        items = controls.items()
    else:
        attrs = list(attrs_filter) if attrs_filter else [a for a in TRANSFORM_ATTRS if a not in SCALE_ATTRS]
        items = ((ctrl, attrs) for ctrl in controls)

    for ctrl, attrs in items:
        for attr in attrs:
            if _attr_exists(ctrl, attr):
                try:
                    cmds.setKeyframe(ctrl, attribute=attr)
                except:
                    pass


def _transfer_animation_jobs(jobs, time_range=None, clear_empty_sources=False):
    if not jobs:
        return 0

    layer_name = curve_transfer.active_animation_layer()
    additive_layer = curve_transfer.is_additive_layer(layer_name)
    effective_range = time_range or (
        float(cmds.playbackOptions(query=True, min=True)),
        float(cmds.playbackOptions(query=True, max=True)),
    )
    captures = []
    capture_cache = {}
    destinations = set()
    for job in jobs:
        cache_key = (job["source"], job["source_attr"])
        if cache_key not in capture_cache:
            capture_cache[cache_key] = curve_transfer.capture_curve(
                f"{job['source']}.{job['source_attr']}",
                time_range=effective_range,
                layer_name=layer_name,
            )
        curve_data = capture_cache.get(cache_key)
        if not curve_data:
            if clear_empty_sources:
                # A real two-sided swap must also move an empty channel.  In
                # other words, keys that only existed on the opposite side are
                # moved rather than duplicated on both sides.
                destinations.add((job["target"], job["target_attr"]))
            continue

        # One-way mirroring keeps an unkeyed source from erasing a sparse
        # destination channel. Two-sided swaps opt into clearing above.
        destinations.add((job["target"], job["target_attr"]))

        multiplier = float(job.get("mult", 1.0))
        value_offset = 0.0
        if not additive_layer and job.get("mode") != "copy_value":
            value_offset = float(job.get("target_rest", 0.0)) - (
                float(job.get("source_rest", 0.0)) * multiplier
            )
        captures.append((
            job["target"],
            job["target_attr"],
            curve_data,
            multiplier,
            value_offset,
        ))

    for target, attr in destinations:
        curve_transfer.clear_attr_range(
            f"{target}.{attr}",
            effective_range,
            layer_name=layer_name,
        )

    total = 0
    for target, attr, curve_data, multiplier, value_offset in captures:
        pasted, key_count = curve_transfer.paste_curve(
            f"{target}.{attr}",
            curve_data,
            layer_name=layer_name,
            clear_existing=False,
            value_scale=multiplier,
            value_offset=value_offset,
        )
        if pasted:
            total += key_count
    return total


def _animation_jobs_have_keys(jobs, time_range):
    """Cheap key probe limited to the channels that will actually transfer."""
    if not jobs:
        return False
    layer_name = curve_transfer.active_animation_layer()
    checked = set()
    for job in jobs:
        key = (job["source"], job["source_attr"])
        if key in checked:
            continue
        checked.add(key)
        curve = curve_transfer.resolve_anim_curve(
            f"{job['source']}.{job['source_attr']}",
            layer_name=layer_name,
        )
        if not curve:
            continue
        try:
            if cmds.keyframe(
                    curve, query=True, time=time_range, timeChange=True):
                return True
        except Exception:
            pass
    return False


def _attr_variants(node, attr):
    variants = {attr}
    short_to_long = {
        "tx": "translateX", "ty": "translateY", "tz": "translateZ",
        "rx": "rotateX", "ry": "rotateY", "rz": "rotateZ",
        "sx": "scaleX", "sy": "scaleY", "sz": "scaleZ",
    }
    long_to_short = {value: key for key, value in short_to_long.items()}
    if attr in short_to_long:
        variants.add(short_to_long[attr])
    if attr in long_to_short:
        variants.add(long_to_short[attr])
    try:
        variants.add(cmds.attributeQuery(attr, node=node, longName=True))
    except Exception:
        pass
    try:
        variants.add(cmds.attributeQuery(attr, node=node, shortName=True))
    except Exception:
        pass
    return {value for value in variants if value}


def _attrs_equivalent(node, left_attr, right_attr):
    return bool(_attr_variants(node, left_attr).intersection(
        _attr_variants(node, right_attr)
    ))


def _same_control_node(left_node, right_node):
    if not left_node or not right_node:
        return False
    if left_node == right_node:
        return True
    try:
        if _long_name(left_node) == _long_name(right_node):
            return True
    except Exception:
        pass
    return _strip_namespace(left_node) == _strip_namespace(right_node)


def _control_for_graph_node(node, controls):
    for control in controls:
        if cmds.objExists(control) and _same_control_node(node, control):
            return control
    return None


def _selected_graph_curve_entries():
    try:
        from AnimKey.sliders import slider_utils
        slider_utils.get_graph_editor_selection()
        entries = getattr(slider_utils, "_GRAPH_EDITOR_CURVE_ENTRIES", []) or []
    except Exception:
        return []

    cleaned = []
    seen = set()
    for entry in entries:
        curve = entry.get("curve")
        if curve and not cmds.objExists(curve):
            continue
        frames = []
        for frame in entry.get("frames") or []:
            try:
                frames.append(float(frame))
            except Exception:
                pass
        frames = sorted(set(frames))
        if not frames:
            continue
        for plug in entry.get("driven_plugs") or []:
            if not plug or "." not in plug or not cmds.objExists(plug):
                continue
            node, attr = plug.rsplit(".", 1)
            key = (curve, _long_name(node), attr, tuple(frames))
            if key in seen:
                continue
            seen.add(key)
            cleaned.append({
                "curve": curve,
                "plug": plug,
                "node": node,
                "attr": attr,
                "frames": frames,
            })
    return cleaned


def _frame_matches_selected(frame, selected_frames):
    frame = float(frame)
    return any(
        abs(frame - float(selected_frame)) <= curve_transfer.FRAME_EPSILON
        for selected_frame in selected_frames
    )


def _trim_curve_data_to_selected_frames(curve_data, selected_frames):
    frames = curve_data.get("keyframes", []) if isinstance(curve_data, dict) else []
    keep = [
        index for index, frame in enumerate(frames)
        if _frame_matches_selected(frame, selected_frames)
    ]
    if not keep:
        return None

    result = {}
    for key, value in curve_data.items():
        if isinstance(value, list) and len(value) == len(frames):
            result[key] = [value[index] for index in keep]
        else:
            result[key] = copy.deepcopy(value)
    result["full_curve"] = False
    return result


def _mirror_selected_graph_keys_for_rig(controls, snapshot, sym_plane,
                                        attrs_filter=None, graph_entries=None):
    graph_entries = graph_entries if graph_entries is not None else _selected_graph_curve_entries()
    if not graph_entries:
        return None
    if not snapshot:
        return 0

    jobs = _selected_animation_mirror_channel_jobs(
        controls, snapshot, sym_plane, attrs_filter
    )
    if not jobs:
        return 0

    layer_name = curve_transfer.active_animation_layer()
    additive_layer = curve_transfer.is_additive_layer(layer_name)
    captures = []
    destinations = {}
    capture_cache = {}

    for entry in graph_entries:
        source_control = _control_for_graph_node(entry["node"], controls)
        if not source_control:
            continue
        for job in jobs:
            if not _same_control_node(source_control, job["source"]):
                continue
            if not _attrs_equivalent(job["source"], job["source_attr"], entry["attr"]):
                continue

            frames = tuple(entry["frames"])
            source_curve = entry.get("curve") or f"{job['source']}.{job['source_attr']}"
            cache_key = (source_curve, tuple(frames))
            if cache_key not in capture_cache:
                capture_cache[cache_key] = curve_transfer.capture_curve(
                    source_curve,
                    time_range=(min(frames), max(frames)),
                    layer_name=layer_name,
                )
            curve_data = _trim_curve_data_to_selected_frames(
                capture_cache.get(cache_key), frames
            )
            if not curve_data:
                continue

            multiplier = float(job.get("mult", 1.0))
            value_offset = 0.0
            if not additive_layer and job.get("mode") != "copy_value":
                value_offset = float(job.get("target_rest", 0.0)) - (
                    float(job.get("source_rest", 0.0)) * multiplier
                )
            dest_plug = f"{job['target']}.{job['target_attr']}"
            destinations.setdefault(dest_plug, set()).update(curve_data["keyframes"])
            captures.append((dest_plug, curve_data, multiplier, value_offset))
            break

    for dest_plug, frames in destinations.items():
        for frame in frames:
            curve_transfer.clear_attr_range(
                dest_plug,
                (float(frame), float(frame)),
                layer_name=layer_name,
            )

    total = 0
    for dest_plug, curve_data, multiplier, value_offset in captures:
        pasted, key_count = curve_transfer.paste_curve(
            dest_plug,
            curve_data,
            layer_name=layer_name,
            clear_existing=False,
            value_scale=multiplier,
            value_offset=value_offset,
        )
        if pasted:
            total += key_count
    return total


def _mirror_selected_graph_keys(controls=None, attrs_filter=None, manage_undo=True):
    controls = list(controls) if controls else (cmds.ls(selection=True) or [])
    if not controls:
        return 0

    graph_entries = _selected_graph_curve_entries()
    if not graph_entries:
        return None

    prepared_groups = _prepared_snapshot_groups(controls)
    if manage_undo:
        _open_animkey_undo_chunk("AnimKey Mirror Selected Graph Keys")
    refresh_suspended = False
    try:
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            pass
        total = 0
        for prepared in prepared_groups:
            info = prepared["info"]
            total += _mirror_selected_graph_keys_for_rig(
                info["controls"],
                prepared["snapshot"],
                prepared["sym_plane"],
                attrs_filter=attrs_filter,
                graph_entries=graph_entries,
            ) or 0
        return total
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        if manage_undo:
            _close_animkey_undo_chunk()


def _mirror_animation_for_rig(controls, attrs_filter=None, time_range=None):
    rig_name = get_rig_identifier(controls)
    snapshot, _ = _load_or_build_basic_snapshot(controls, rig_name=rig_name)
    sym_plane = _snap_sym_plane(snapshot)
    if not snapshot:
        return 0

    controls = _expand_controls_with_opposites(controls, snapshot, sym_plane)
    jobs = _animation_mirror_channel_jobs(controls, snapshot, sym_plane, attrs_filter)
    return _transfer_animation_jobs(
        jobs, time_range=time_range, clear_empty_sources=True
    )


def _mirror_selected_animation_for_rig(controls, attrs_filter=None, time_range=None):
    rig_name = get_rig_identifier(controls)
    snapshot, _ = _load_or_build_basic_snapshot(controls, rig_name=rig_name)
    sym_plane = _snap_sym_plane(snapshot)
    if not snapshot:
        return 0

    jobs = _selected_animation_mirror_channel_jobs(
        controls, snapshot, sym_plane, attrs_filter
    )
    return _transfer_animation_jobs(jobs, time_range=time_range)


def mirror_animation(controls=None, attrs_filter=None, time_range=None,
                     manage_undo=True, swap_both_sides=True):
    """Mirror keyed animation, preserving the historical two-sided default.

    ``mirror_selected_animation`` passes ``swap_both_sides=False`` for the
    one-way time-slider workflow used by the main Mirror button. Direct callers
    and All Mirror retain the established swap behavior.
    """
    if controls is not None and not isinstance(controls, (list, tuple)):
        controls = None
    controls = list(controls) if controls else (cmds.ls(selection=True) or [])
    if not controls:
        cmds.warning("AnimKey: Select controls to mirror animation.")
        return 0
    prepared_groups = _prepared_snapshot_groups(controls)
    if manage_undo:
        _open_animkey_undo_chunk("AnimKey Mirror Animation")
    refresh_suspended = False
    try:
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            pass
        total = 0
        for prepared in prepared_groups:
            info = prepared["info"]
            controls_for_jobs = info["controls"]
            if swap_both_sides:
                controls_for_jobs = _expand_controls_with_opposites(
                    controls_for_jobs,
                    prepared["snapshot"],
                    prepared["sym_plane"],
                )
            jobs = _selected_animation_mirror_channel_jobs(
                controls_for_jobs,
                prepared["snapshot"],
                prepared["sym_plane"],
                attrs_filter,
            )
            total += _transfer_animation_jobs(
                jobs,
                time_range=time_range,
                clear_empty_sources=swap_both_sides,
            )
        return total
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        if manage_undo:
            _close_animkey_undo_chunk()


def mirror_selected_animation(controls=None, attrs_filter=None, time_range=None,
                              manage_undo=True):
    """Mirror selected controls over time without overwriting the source side."""
    return mirror_animation(
        controls=controls,
        attrs_filter=attrs_filter,
        time_range=time_range,
        manage_undo=manage_undo,
        swap_both_sides=False,
    )


def _legacy_all_mirror(*args):
    """Swap pose between left and right controls simultaneously."""
    _open_animkey_undo_chunk("AnimKey Legacy All Mirror")
    try:
        selected = cmds.ls(selection=True)
        if not selected:
            cmds.warning("AnimKey: Select at least one control.")
            return

        selected_channels = _get_selected_channels()
        rig_name  = get_rig_identifier(selected)
        snapshot  = load_snapshot(rig_name)
        sym_plane = _snap_sym_plane(snapshot)

        processed = set()
        total     = 0

        # Capture all values BEFORE writing anything (two-phase swap)
        # Phase A: collect
        side_data = {}   # ctrl → {attr: value}
        for ctrl in selected:
            opp = (_snap_opposite(ctrl, snapshot)
                   if snapshot else find_opposite_smart(ctrl, sym_plane))
            if not opp or not cmds.objExists(opp):
                continue
            ctrl_key = ctrl.rsplit(':',1)[-1]
            opp_key  = opp.rsplit(':',1)[-1]
            if (ctrl_key, opp_key) in processed or (opp_key, ctrl_key) in processed:
                continue
            processed.add((ctrl_key, opp_key))

            # Store both world matrices
            side_data[ctrl] = _mat4_from_maya(ctrl)
            side_data[opp]  = _mat4_from_maya(opp)

        # Phase B: apply reflected
        for ctrl, wm in side_data.items():
            opp_name = _snap_opposite(ctrl, snapshot) if snapshot else find_opposite_smart(ctrl, sym_plane)
            if not opp_name:
                continue
            if opp_name not in side_data:
                continue

            # Apply the OPPOSITE's world matrix reflected to THIS control
            wm_opp        = side_data[opp_name]
            wm_reflected  = sym_plane.reflect_mat4(wm_opp)
            local_vals    = world_matrix_to_local_transforms(ctrl, wm_reflected)

            attrs_to_set  = selected_channels if selected_channels else list(local_vals.keys())
            for attr in attrs_to_set:
                if attr not in local_vals: continue
                if not _is_modifiable(ctrl, attr): continue
                try:
                    cmds.setAttr(f"{ctrl}.{attr}", local_vals[attr])
                    total += 1
                except:
                    pass

        if total:
            cmds.inViewMessage(
                amg=f"<span style='color:#88c0d0'>Swapped {total} values</span>",
                pos='topCenter', fade=True, fadeStayTime=800
            )
    except Exception as e:
        cmds.warning(f"AnimKey All Mirror: {e}")
        import traceback; traceback.print_exc()
    finally:
        _close_animkey_undo_chunk()


# ═══════════════════════════════════════════════════════════════════════════════
#                      AUTO MIRROR
# ═══════════════════════════════════════════════════════════════════════════════

def all_mirror(*args):
    """Mirror/swap animation over the selected time range, or playback range."""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.all_mirror"):
        return None
    try:
        selected = cmds.ls(selection=True) or []
        if not selected:
            cmds.warning("AnimKey: Select at least one control.")
            return

        selected_channels = _get_selected_channels()
        prepared_groups = _prepared_snapshot_groups(selected)
        start, end, has_selected_range = _time_slider_or_playback_range()
        time_range = (start, end)

        _open_animkey_undo_chunk("AnimKey All Mirror")
        refresh_suspended = False
        try:
            try:
                cmds.refresh(suspend=True)
                refresh_suspended = True
            except Exception:
                pass
            total = 0
            animation_mode = False
            missing_range_keys = False

            for prepared in prepared_groups:
                info = prepared["info"]
                snapshot = prepared["snapshot"]
                sym_plane = prepared["sym_plane"]
                controls_for_keys = _expand_controls_with_opposites(
                    info["controls"], snapshot, sym_plane
                )
                jobs = _animation_mirror_channel_jobs(
                    controls_for_keys,
                    snapshot,
                    sym_plane,
                    selected_channels,
                )
                if _animation_jobs_have_keys(jobs, time_range):
                    animation_mode = True
                    total += _transfer_animation_jobs(
                        jobs,
                        time_range=time_range,
                        clear_empty_sources=True,
                    )
                elif not has_selected_range:
                    count, _ = _mirror_selected_pose_two_phase(
                        info["controls"], snapshot, sym_plane, selected_channels
                    )
                    total += count
                else:
                    missing_range_keys = True
        finally:
            if refresh_suspended:
                try:
                    cmds.refresh(suspend=False)
                except Exception:
                    pass
            _close_animkey_undo_chunk()

        if total:
            if animation_mode:
                label = f"{start:g}-{end:g}" if has_selected_range else "playback range"
                cmds.inViewMessage(
                    amg=f"<span style='color:#88c0d0'>Mirrored animation ({label}): {total} values</span>",
                    pos='topCenter', fade=True, fadeStayTime=1200
                )
            else:
                cmds.inViewMessage(
                    amg=f"<span style='color:#88c0d0'>Swapped pose: {total} values</span>",
                    pos='topCenter', fade=True, fadeStayTime=1200
                )
            return

        if has_selected_range and missing_range_keys:
            cmds.warning(f"AnimKey All Mirror: No keys found in selected range {start:g}-{end:g}.")
            return
        cmds.warning("AnimKey All Mirror: No mirrorable values found.")
    except Exception as e:
        cmds.warning(f"AnimKey All Mirror: {e}")
        import traceback; traceback.print_exc()


class AutoMirrorState:
    enabled       = False
    script_job_id = None
    last_run      = 0.0
    last_signature = None

_auto_mirror_state = AutoMirrorState()


def _auto_mirror_callback():
    if not _auto_mirror_state.enabled:
        return
    try:
        now = time.perf_counter()
        if now - _auto_mirror_state.last_run < (1.0 / 30.0):
            return
        _auto_mirror_state.last_run = now
        selected = cmds.ls(selection=True)
        if not selected:
            return

        signature_values = []
        for ctrl in selected:
            try:
                matrix = cmds.getAttr(f"{ctrl}.worldMatrix[0]")
                if matrix and isinstance(matrix[0], (list, tuple)):
                    matrix = matrix[0]
                signature_values.extend(round(float(value), 6) for value in matrix)
            except Exception:
                pass
            for attr in (cmds.listAttr(ctrl, userDefined=True, keyable=True) or []):
                value = _safe_getattr(ctrl, attr)
                if isinstance(value, (int, float)):
                    signature_values.append(round(float(value), 6))
        signature = (
            tuple(selected),
            round(float(cmds.currentTime(query=True)), 4),
            tuple(signature_values),
        )
        if signature == _auto_mirror_state.last_signature:
            return
        _auto_mirror_state.last_signature = signature

        for rig_name, info in _group_controls_by_rig(selected).items():
            snapshot, _ = _load_or_build_basic_snapshot(
                info["controls"], rig_name=rig_name, rig_label=info["label"]
            )
            if not snapshot:
                continue
            _mirror_selected_pose_two_phase(
                info["controls"],
                snapshot,
                _snap_sym_plane(snapshot),
            )
    except:
        pass


def enable_auto_mirror(*args):
    global _auto_mirror_state
    if _auto_mirror_state.enabled:
        return
    _auto_mirror_state.script_job_id = cmds.scriptJob(
        event=["idle", _auto_mirror_callback], killWithScene=True
    )
    _auto_mirror_state.enabled = True
    _auto_mirror_state.last_run = 0.0
    _auto_mirror_state.last_signature = None
    cmds.inViewMessage(amg="<span style='color:#a3be8c'>Auto Mirror: ON</span>",
                       pos='topCenter', fade=True, fadeStayTime=1000)


def disable_auto_mirror(*args):
    global _auto_mirror_state
    _auto_mirror_state.enabled = False
    _auto_mirror_state.last_signature = None
    if _auto_mirror_state.script_job_id:
        try:
            if cmds.scriptJob(exists=_auto_mirror_state.script_job_id):
                cmds.scriptJob(kill=_auto_mirror_state.script_job_id, force=True)
        except:
            pass
        _auto_mirror_state.script_job_id = None
    cmds.inViewMessage(amg="<span style='color:#bf616a'>Auto Mirror: OFF</span>",
                       pos='topCenter', fade=True, fadeStayTime=1000)


def toggle_auto_mirror(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.toggle_auto_mirror"):
        return None
    disable_auto_mirror() if _auto_mirror_state.enabled else enable_auto_mirror()


def is_auto_mirror_enabled():
    return _auto_mirror_state.enabled


# ═══════════════════════════════════════════════════════════════════════════════
#                      HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_selected_channels():
    try:
        cb = mel.eval('global string $gChannelBoxName; $temp=$gChannelBoxName;')
        return cmds.channelBox(cb, q=True, selectedMainAttributes=True) or []
    except:
        return []


def delete_mirror_snapshot(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.delete_mirror_snapshot"):
        return None
    selected = cmds.ls(selection=True)
    if not selected:
        cmds.warning("AnimKey: Select a control to identify the rig.")
        return
    rig_name = get_rig_identifier(selected)
    removed = False
    for path in _mirror_snapshot_candidates(rig_name):
        if os.path.exists(path):
            os.remove(path)
            _snapshot_cache.pop(path, None)
            removed = True
    _invalidate_quick_profiles(rig_name)
    _missing_snapshot_cache.add((get_user_data_folder(), str(rig_name)))
    if removed:
        cmds.warning(f"AnimKey: Snapshot deleted for '{get_rig_label(selected)}'.")
    else:
        cmds.warning(f"AnimKey: No calibrated snapshot found for '{get_rig_label(selected)}'.")


# ═══════════════════════════════════════════════════════════════════════════════
#                 BACKWARD COMPATIBILITY  (mirror_blend.py etc.)
# ═══════════════════════════════════════════════════════════════════════════════

def find_opposite_name(name):
    return find_opposite_by_name(name)

def find_opposite_smart_compat(control, position_threshold=0.5):
    return find_opposite_smart(control)

def load_exceptions():
    return {}

def is_attribute_modifiable(control, attr):
    return _is_modifiable(control, attr)

_mirror_cache = {}

def get_mirror_value(control, attr, current_value, exceptions=None, snapshot=None,
                     is_central=False, opposite_control=None):
    """
    Calibrated mirror value calculation for sliders.
    Caches the per-control result during a slider operation.
    """
    global _mirror_cache

    target_ctrl = control if is_central else opposite_control
    if not target_ctrl or not cmds.objExists(target_ctrl):
        return current_value

    cache_key = (control, target_ctrl)
    if cache_key in _mirror_cache:
        return _mirror_cache[cache_key].get(attr, current_value)

    try:
        rig_name = get_rig_identifier([control])
        if not snapshot:
            snapshot = load_snapshot(rig_name)
        sym_plane = _snap_sym_plane(snapshot)
        transforms = compute_mirror_values(
            control, target=target_ctrl, snapshot=snapshot, sym_plane=sym_plane,
            attrs_filter=None, is_central=is_central
        )
        _mirror_cache[cache_key] = transforms
        return transforms.get(attr, current_value)
    except Exception:
        return current_value

def clear_mirror_cache():
    """Clear the mirror value cache for new slider operations."""
    global _mirror_cache
    _mirror_cache = {}

def add_mirror_invert_exception(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.add_mirror_invert_exception"):
        return None
    cmds.warning("AnimKey: Exceptions are handled by the calibrated snapshot. Run Snapshot Mirror.")

def add_mirror_keep_exception(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.add_mirror_keep_exception"):
        return None
    cmds.warning("AnimKey: Exceptions are handled by the calibrated snapshot. Run Snapshot Mirror.")

def remove_mirror_exception(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.remove_mirror_exception"):
        return None
    cmds.warning("AnimKey: No exceptions needed. The calibrated snapshot maps the rig automatically.")

def clear_all_exceptions(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.clear_all_exceptions"):
        return None
    cmds.warning("AnimKey: No exceptions needed. The calibrated snapshot maps the rig automatically.")

def show_exceptions(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.show_exceptions"):
        return None
    cmds.warning("AnimKey: Exceptions are handled automatically by the mirror profile.")


# ═══════════════════════════════════════════════════════════════════════════════
#                      BUTTON API
# ═══════════════════════════════════════════════════════════════════════════════

def mirror_pose(*args):
    mirror(*args)


def execute(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.execute"):
        return None
    mirror()


def get_info():
    return {
        "name"    : "Mirror",
        "tooltip" : "Mirror pose, or the selected time-slider range. Right-click for more options.",
        "icon"    : "mirror.svg",
        "shortcut": None,
    }
