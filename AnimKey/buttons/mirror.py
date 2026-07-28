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
import pickle
import zlib
import builtins
import math
import itertools
import time

from AnimKey.core import animation_curve_transfer as curve_transfer


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

ROTATION_ORDER_MAP = {
    0: 'xyz', 1: 'yzx', 2: 'zxy',
    3: 'xzy', 4: 'yxz', 5: 'zyx'
}

SNAPSHOT_SCHEMA_VERSION = 10
SNAPSHOT_FOLDER_NAME = "snapshots"
LEGACY_SNAPSHOT_FOLDER_NAME = "snapshots_v10"
AKMIRROR_SNAPSHOT_MAGIC = b"AKMIRROR2\x00"

_snapshot_cache = {}


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

        # Most character rigs are modeled around one dominant world axis. Snap
        # the normal to that axis when the evidence is strong, then solve the
        # actual plane offset from the pair midpoints.
        abs_n = [abs(normal[i]) for i in range(3)]
        if max(abs_n) > 0.72:
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


def _expand_to_candidate_controls(selection):
    """Expand a rough rig/root selection into likely animation controls."""
    if not selection:
        return []
    if len(selection) > 1:
        expanded = []
        for item in selection:
            expanded.extend(_expand_to_candidate_controls([item]))
        return list(dict.fromkeys(c for c in expanded if cmds.objExists(c)))

    root = selection[0]
    candidates = []
    try:
        descendants = cmds.listRelatives(root, allDescendents=True, fullPath=True, type="transform") or []
    except:
        descendants = []

    for node in [root] + descendants:
        if cmds.objExists(node) and _has_animkey_control_shape(node) and _has_keyable_transform_attrs(node):
            candidates.append(node)

    ns = _namespace(root)
    if not candidates and ns:
        try:
            for node in cmds.ls(f"{ns}:*", type="transform") or []:
                if _has_animkey_control_shape(node) and _has_keyable_transform_attrs(node):
                    candidates.append(node)
        except:
            pass

    return list(dict.fromkeys(candidates)) or [root]


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


def _attrs_to_apply(local_vals, attrs_filter=None):
    attrs = list(attrs_filter) if attrs_filter else list(local_vals.keys())
    if not _attrs_allow_scale(attrs_filter):
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


def _snapshot_rest_values(control_cache):
    values = {}
    for control, data in (control_cache or {}).items():
        for attr, value in data.get("rest_attrs", {}).items():
            values[f'{control}.{attr}'] = value
    return values


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


def _build_calibrated_attr_map(source, target, sym_plane, snapshot_cache=None):
    attr_map = _default_attr_map(source, target, sym_plane, snapshot_cache=snapshot_cache)
    source_attrs = _cached_scalar_attrs(source, snapshot_cache)
    target_attrs = _cached_scalar_attrs(target, snapshot_cache)
    if not source_attrs or not target_attrs:
        return attr_map

    # FK, wrist and finger controls are commonly authored so matching L/R
    # channels mean the same pose. For these, direct value copy is safer than
    # trying to infer a mirror sign from world matrices. Bend/tweak controls are
    # copied only when their source channel is actually posed.
    if source != target and (_is_fk_copy_control(source) or _is_hand_or_finger_copy_control(source)):
        return _same_value_attr_map(
            source, target, include_translate=False, skip_zero_delta=False,
            snapshot_cache=snapshot_cache
        )

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

        best = None
        for target_attr in candidates:
            if not _cached_is_modifiable(target, target_attr, snapshot_cache):
                continue
            target_value = target_rest.get(target_attr, 0.0)
            for mult in (-1.0, 1.0):
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
            payload = zlib.decompress(payload[len(AKMIRROR_SNAPSHOT_MAGIC):])
        return pickle.loads(payload)
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


def _create_mirror_snapshot(selected_controls, rig_name=None, rig_label=None, show_message=True):
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
    selected_controls = list(dict.fromkeys(ctrl for ctrl in selected_controls if cmds.objExists(ctrl)))
    snapshot_verbose = os.environ.get("ANIMKEY_MIRROR_SNAPSHOT_VERBOSE") == "1"
    _print = builtins.print

    def print(*args, **kwargs):
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
    cache_elapsed = time.time() - cache_started

    # ── Phase 1: Find all pairs to detect symmetry plane ────────────────────
    detect_started = time.time()
    pairs_positions = []
    pair_map        = {}   # ctrl_short → opp_short
    pair_controls   = {}
    processed       = set()

    for ctrl in selected_controls:
        ctrl_short = control_cache.get(ctrl, {}).get("short") or _strip_namespace(ctrl)
        if ctrl_short in processed:
            continue
        opp = _find_opposite_from_index(ctrl, control_index)
        if opp and opp in control_cache:
            opp_short = control_cache.get(opp, {}).get("short") or _strip_namespace(opp)
            pairs_positions.append((
                _cached_world_pos(ctrl, control_cache),
                _cached_world_pos(opp, control_cache),
            ))
            pair_map[ctrl_short] = opp_short
            pair_map[opp_short]  = ctrl_short
            pair_controls[ctrl] = opp
            pair_controls[opp] = ctrl
            processed.add(ctrl_short)
            processed.add(opp_short)

    sym_plane = SymmetryPlane.detect_from_pairs(pairs_positions)
    detect_elapsed = time.time() - detect_started
    print(f"  Symmetry plane normal: {[round(v,4) for v in sym_plane.normal]}")
    print(f"  Symmetry plane point:  {[round(v,4) for v in sym_plane.point]}")
    print(f"\n  Phase 2: Recording rest-pose data for {len(selected_controls)} controls...")

    # ── Phase 2: Capture rest-pose data ─────────────────────────────────────
    rest_started = time.time()
    snapshot_data = {
        "version"       : SNAPSHOT_SCHEMA_VERSION,
        "rig_name"      : rig_name,
        "rig_label"     : rig_label,
        "symmetry_plane": sym_plane.to_dict(),
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
        if ctrl_short in processed:
            continue

        opp_short = pair_map.get(ctrl_short)
        is_paired = opp_short is not None

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
            "type"           : "paired" if is_paired else "central",
            "opposite"       : opp_short,
            "rot_order"      : rot_order,
            "rest_world_mat" : wm_rest.as_list(),
            "rest_attrs"     : _cached_rest_attrs(ctrl, control_cache),
            "custom_mults"   : custom_mults,
        }
        if is_paired and opp and opp in control_cache:
            ctrl_data["attr_map"] = _build_calibrated_attr_map(
                ctrl, opp, sym_plane, snapshot_cache=control_cache
            )
            opp_attr_map = _invert_attr_map(ctrl_data["attr_map"])
        else:
            ctrl_data["attr_map"] = _build_calibrated_attr_map(
                ctrl, ctrl, sym_plane, snapshot_cache=control_cache
            )
            opp_attr_map = None

        snapshot_data["controls"][ctrl_short] = ctrl_data
        if is_paired:
            snapshot_data["opposites"][ctrl_short] = opp_short
            # Mirror entry for opposite too
            if opp and opp in control_cache:
                opp_cache = control_cache.get(opp, {})
                wm_opp = _cached_world_mat(opp, control_cache)
                rot_order_opp = opp_cache.get("rotate_order", 'xyz')
                opp_data = {
                    "type"           : "paired",
                    "opposite"       : ctrl_short,
                    "rot_order"      : rot_order_opp,
                    "rest_world_mat" : wm_opp.as_list(),
                    "rest_attrs"     : _cached_rest_attrs(opp, control_cache),
                    "custom_mults"   : custom_mults,
                    "attr_map"       : opp_attr_map or _build_calibrated_attr_map(
                        opp, ctrl, sym_plane, snapshot_cache=control_cache
                    ),
                }
                snapshot_data["controls"][opp_short] = {
                    **opp_data
                }
                snapshot_data["opposites"][opp_short] = ctrl_short
            processed.add(opp_short)
            paired_count += 1
            print(f"    ↔  {ctrl_short}  ←→  {opp_short}   [rot:{rot_order}]")
        else:
            central_count += 1
            print(f"    ○  {ctrl_short}  (central)  [rot:{rot_order}]")

        processed.add(ctrl_short)

    # ── Save ─────────────────────────────────────────────────────────────────
    calibrate_elapsed = time.time() - calibrate_started
    snap_file = get_mirror_snapshot_file(rig_name)
    write_started = time.time()
    _write_snapshot_file(snap_file, snapshot_data)
    write_elapsed = time.time() - write_started

    print("\n" + "=" * 65)
    print(f"  ✓  {paired_count} pairs  +  {central_count} central controls")
    print(f"  ✓  Snapshot saved → {snap_file}")
    print(f"  Cache: {cache_elapsed:.2f}s")
    print(f"  Detect: {detect_elapsed:.2f}s")
    print(f"  Rest: {rest_elapsed:.2f}s")
    print(f"  Calibrate: {calibrate_elapsed:.2f}s")
    print(f"  Size: {_file_size_kb(snap_file):.1f} KB")
    print(f"  Write: {write_elapsed:.2f}s")
    print(f"  Time: {time.time() - snapshot_started:.2f}s")
    print("=" * 65)

    if show_message:
        cmds.inViewMessage(
            amg=(f"<span style='color:#a3be8c'>Mirror snapshot saved</span><br>"
             f"<span style='color:#ebcb8b'>{paired_count} pairs - {central_count} central</span>"),
            pos='topCenter', fade=True, fadeStayTime=2500
        )


    return {
        "rig_name": rig_name,
        "rig_label": rig_label,
        "paired": paired_count,
        "central": central_count,
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
        result = _run_with_refresh_suspended(
            _create_mirror_snapshot,
            info["controls"], rig_name=rig_name, rig_label=info["label"],
            show_message=(len(grouped) == 1)
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
            if data.get("version", 1) >= SNAPSHOT_SCHEMA_VERSION:
                controls = data.get("controls", {})
                if controls and not any("rest_attrs" in c for c in controls.values()):
                    continue
                return data
        except:
            pass
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


def _build_basic_mirror_snapshot(seed_controls, rig_name=None, rig_label=None):
    """Build a temporary lightweight snapshot when no calibrated one exists."""
    seeds = [ctrl for ctrl in (seed_controls or []) if cmds.objExists(ctrl)]
    if not seeds:
        return None

    controls = _expand_to_candidate_controls(seeds) or seeds
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

    pairs_positions = []
    pair_map = {}
    pair_controls = {}
    processed = set()

    for ctrl in controls:
        ctrl_short = control_cache.get(ctrl, {}).get("short") or _strip_namespace(ctrl)
        if ctrl_short in processed:
            continue
        opp = _find_opposite_from_index(ctrl, control_index) or find_opposite_by_name(ctrl)
        if opp and cmds.objExists(opp) and opp in control_cache:
            opp_short = control_cache.get(opp, {}).get("short") or _strip_namespace(opp)
            pairs_positions.append((
                _cached_world_pos(ctrl, control_cache),
                _cached_world_pos(opp, control_cache),
            ))
            pair_map[ctrl_short] = opp_short
            pair_map[opp_short] = ctrl_short
            pair_controls[ctrl] = opp
            pair_controls[opp] = ctrl
            processed.add(ctrl_short)
            processed.add(opp_short)

    sym_plane = SymmetryPlane.detect_from_pairs(pairs_positions) if pairs_positions else SymmetryPlane()
    snapshot_data = {
        "version": SNAPSHOT_SCHEMA_VERSION,
        "rig_name": rig_name,
        "rig_label": rig_label,
        "basic_snapshot": True,
        "symmetry_plane": sym_plane.to_dict(),
        "controls": {},
        "opposites": {},
    }

    for ctrl in controls:
        ctrl_cache = control_cache.get(ctrl, {})
        ctrl_short = ctrl_cache.get("short") or _strip_namespace(ctrl)
        opp = pair_controls.get(ctrl)
        opp_short = (control_cache.get(opp, {}).get("short") or _strip_namespace(opp)) if opp else None
        target = opp if opp and cmds.objExists(opp) else ctrl
        wm_rest = _cached_world_mat(ctrl, control_cache)

        snapshot_data["controls"][ctrl_short] = {
            "type": "paired" if opp_short else "central",
            "opposite": opp_short,
            "rot_order": ctrl_cache.get("rotate_order", "xyz"),
            "rest_world_mat": wm_rest.as_list(),
            "rest_attrs": {},
            "custom_mults": {},
            "attr_map": _basic_copy_attr_map(ctrl, target, sym_plane, control_cache),
        }
        if opp_short:
            snapshot_data["opposites"][ctrl_short] = opp_short

    return snapshot_data


def _load_or_build_basic_snapshot(controls, rig_name=None, rig_label=None):
    snapshot = load_snapshot(rig_name or get_rig_identifier(controls))
    if snapshot:
        return snapshot, False
    snapshot = _build_basic_mirror_snapshot(controls, rig_name=rig_name, rig_label=rig_label)
    if snapshot:
        try:
            print("AnimKey Mirror: using temporary basic snapshot for {}".format(
                snapshot.get("rig_label", "rig")
            ))
        except:
            pass
    return snapshot, True


def _snap_sym_plane(snapshot):
    """Extract SymmetryPlane from snapshot dict."""
    if snapshot and "symmetry_plane" in snapshot:
        return SymmetryPlane.from_dict(snapshot["symmetry_plane"])
    return SymmetryPlane()   # default YZ


def _snap_opposite(control, snapshot):
    """Get opposite control full name from snapshot."""
    if not snapshot:
        return None
    ctrl_short = _strip_namespace(control)
    opp_short  = snapshot.get("opposites", {}).get(ctrl_short)
    if not opp_short:
        return None
    return _resolve_control_like(control, opp_short)


def _snap_ctrl_data(control, snapshot):
    """Get per-control snapshot data dict."""
    if not snapshot:
        return None
    ctrl_short = _strip_namespace(control)
    return snapshot.get("controls", {}).get(ctrl_short)


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

def _is_modifiable(control, attr):
    try:
        return bool(cmds.getAttr(f"{control}.{attr}", settable=True))
    except:
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
    values = {}

    if ctrl_data and target_data and (
        (ctrl_data.get("rest_attrs") and target_data.get("rest_attrs"))
        or ctrl_data.get("attr_map")
    ):
        source_rest = ctrl_data.get("rest_attrs", {})
        target_rest = target_data.get("rest_attrs", {})
        attr_map = ctrl_data.get("attr_map") or _default_attr_map(ctrl, target, sym_plane)

        for source_attr, spec in attr_map.items():
            target_attr = spec.get("target", source_attr)
            if attrs_filter and source_attr not in attrs_filter and target_attr not in attrs_filter:
                continue
            if target_attr in SCALE_ATTRS and not _attrs_allow_scale(attrs_filter):
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
        if target_attr in SCALE_ATTRS and not _attrs_allow_scale(attrs_filter):
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
    """Mirror pose from selected controls TO their opposites."""
    _open_animkey_undo_chunk("AnimKey Mirror Pose")
    try:
        selected = cmds.ls(selection=True)
        if not selected:
            cmds.warning("AnimKey: Select at least one control.")
            return

        selected_channels = _get_selected_channels()
        grouped = _group_controls_by_rig(selected)
        total = 0

        for rig_name, info in grouped.items():
            snapshot, is_basic_snapshot = _load_or_build_basic_snapshot(
                info["controls"], rig_name=rig_name, rig_label=info["label"]
            )
            sym_plane = _snap_sym_plane(snapshot)
            if not snapshot:
                continue

            count, _ = _mirror_selected_pose_two_phase(
                info["controls"], snapshot, sym_plane, selected_channels
            )
            total += count

        if total:
            cmds.inViewMessage(
                amg=f"<span style='color:#88c0d0'>Mirrored {total} values</span>",
                pos='topCenter', fade=True, fadeStayTime=800
            )
    except Exception as e:
        cmds.warning(f"AnimKey Mirror: {e}")
        import traceback; traceback.print_exc()
    finally:
        _close_animkey_undo_chunk()


def _mirror_pair_values_from_matrix(source_mat, source_ctrl, target_ctrl,
                                    sym_plane, snapshot, attrs_filter=None):
    return compute_mirror_values(
        source_ctrl, target=target_ctrl, snapshot=snapshot, sym_plane=sym_plane,
        attrs_filter=attrs_filter, is_central=(source_ctrl == target_ctrl)
    )


def _apply_values_to_control(target, values, attrs_filter=None):
    count = 0
    for attr in _attrs_to_apply(values, attrs_filter):
        if attr in values and _set_scalar_attr(target, attr, values[attr]):
            count += 1
    return count


def _mirror_selected_pose_two_phase(controls, snapshot, sym_plane, attrs_filter=None):
    selected = [ctrl for ctrl in controls if cmds.objExists(ctrl)]
    selected_keys = {_strip_namespace(ctrl) for ctrl in selected}
    processed_pairs = set()
    pending = []

    for ctrl in selected:
        opp = _snap_opposite(ctrl, snapshot) if snapshot else find_opposite_smart(ctrl, sym_plane)
        if opp and cmds.objExists(opp):
            pair_key = tuple(sorted((_strip_namespace(ctrl), _strip_namespace(opp))))
            if pair_key in processed_pairs:
                continue
            processed_pairs.add(pair_key)

            if _strip_namespace(opp) in selected_keys:
                vals_to_opp = compute_mirror_values(
                    ctrl, target=opp, snapshot=snapshot, sym_plane=sym_plane,
                    attrs_filter=attrs_filter, is_central=False
                )
                vals_to_ctrl = compute_mirror_values(
                    opp, target=ctrl, snapshot=snapshot, sym_plane=sym_plane,
                    attrs_filter=attrs_filter, is_central=False
                )
                pending.append((opp, vals_to_opp))
                pending.append((ctrl, vals_to_ctrl))
            else:
                vals = compute_mirror_values(
                    ctrl, target=opp, snapshot=snapshot, sym_plane=sym_plane,
                    attrs_filter=attrs_filter, is_central=False
                )
                pending.append((opp, vals))
        else:
            vals = compute_mirror_values(
                ctrl, target=ctrl, snapshot=snapshot, sym_plane=sym_plane,
                attrs_filter=attrs_filter, is_central=True
            )
            pending.append((ctrl, vals))

    total = 0
    touched = {}
    for target, values in pending:
        attrs_to_apply = _attrs_to_apply(values, attrs_filter)
        count = _apply_values_to_control(target, values, attrs_filter)
        total += count
        if count:
            touched.setdefault(target, set()).update(attrs_to_apply)
    return total, touched


def _mirror_current_pose_bidirectional(controls, snapshot, sym_plane, attrs_filter=None):
    processed = set()
    pairs = []
    central = []

    for ctrl in controls:
        if not cmds.objExists(ctrl):
            continue
        opp = _snap_opposite(ctrl, snapshot) if snapshot else find_opposite_smart(ctrl, sym_plane)
        if opp and cmds.objExists(opp):
            pair_key = tuple(sorted((_strip_namespace(ctrl), _strip_namespace(opp))))
            if pair_key in processed:
                continue
            processed.add(pair_key)
            pairs.append((ctrl, opp))
        else:
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
    attr_map = (source_data.get("attr_map") if source_data else None) or _default_attr_map(
        source, target, sym_plane
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
    selected = [ctrl for ctrl in controls if cmds.objExists(ctrl)]
    processed = set()
    jobs = []

    for ctrl in selected:
        opp = _snap_opposite(ctrl, snapshot) if snapshot else find_opposite_smart(ctrl, sym_plane)
        if opp and cmds.objExists(opp):
            pair_key = tuple(sorted((_strip_namespace(ctrl), _strip_namespace(opp))))
            if pair_key in processed:
                continue
            processed.add(pair_key)
            jobs.extend(_animation_channel_jobs_for_direction(
                ctrl, opp, snapshot, sym_plane, attrs_filter, is_central=False
            ))
            jobs.extend(_animation_channel_jobs_for_direction(
                opp, ctrl, snapshot, sym_plane, attrs_filter, is_central=False
            ))
        else:
            jobs.extend(_animation_channel_jobs_for_direction(
                ctrl, ctrl, snapshot, sym_plane, attrs_filter, is_central=True
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


def _mirror_animation_for_rig(controls, attrs_filter=None, time_range=None):
    rig_name = get_rig_identifier(controls)
    snapshot, _ = _load_or_build_basic_snapshot(controls, rig_name=rig_name)
    sym_plane = _snap_sym_plane(snapshot)
    if not snapshot:
        return 0

    controls = _expand_controls_with_opposites(controls, snapshot, sym_plane)
    jobs = _animation_mirror_channel_jobs(controls, snapshot, sym_plane, attrs_filter)
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
        destinations.add((job["target"], job["target_attr"]))
        cache_key = (job["source"], job["source_attr"])
        if cache_key not in capture_cache:
            capture_cache[cache_key] = curve_transfer.capture_curve(
                f"{job['source']}.{job['source_attr']}",
                time_range=effective_range,
                layer_name=layer_name,
            )
        curve_data = capture_cache.get(cache_key)
        if not curve_data:
            continue

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


def mirror_animation(controls=None, attrs_filter=None, time_range=None, manage_undo=True):
    if controls is not None and not isinstance(controls, (list, tuple)):
        controls = None
    controls = list(controls) if controls else (cmds.ls(selection=True) or [])
    if not controls:
        cmds.warning("AnimKey: Select controls to mirror animation.")
        return 0
    grouped = _group_controls_by_rig(controls)
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
        for info in grouped.values():
            total += _mirror_animation_for_rig(
                info["controls"],
                attrs_filter=attrs_filter,
                time_range=time_range,
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
    _open_animkey_undo_chunk("AnimKey All Mirror")
    try:
        selected = cmds.ls(selection=True) or []
        if not selected:
            cmds.warning("AnimKey: Select at least one control.")
            return

        selected_channels = _get_selected_channels()
        grouped = _group_controls_by_rig(selected)
        if len(grouped) > 1:
            start, end, has_selected_range = _time_slider_or_playback_range()
            time_range = (start, end)
            total = 0
            for rig_name, info in grouped.items():
                snapshot, is_basic_snapshot = _load_or_build_basic_snapshot(
                    info["controls"], rig_name=rig_name, rig_label=info["label"]
                )
                sym_plane = _snap_sym_plane(snapshot)
                if not snapshot:
                    continue

                controls_for_keys = _expand_controls_with_opposites(info["controls"], snapshot, sym_plane)
                key_times = _animation_key_times(controls_for_keys, time_range=time_range)
                if key_times:
                    total += mirror_animation(
                        info["controls"],
                        selected_channels,
                        time_range=time_range,
                        manage_undo=False,
                    )
                elif not has_selected_range:
                    count, _ = _mirror_selected_pose_two_phase(
                        info["controls"], snapshot, sym_plane, selected_channels
                    )
                    total += count

            if total:
                cmds.inViewMessage(
                    amg=f"<span style='color:#88c0d0'>Mirrored {total} values</span>",
                    pos='topCenter', fade=True, fadeStayTime=1200
                )
            else:
                cmds.warning("AnimKey All Mirror: No mirrorable values found.")
            return

        rig_name = get_rig_identifier(selected)
        snapshot, is_basic_snapshot = _load_or_build_basic_snapshot(selected, rig_name=rig_name)
        sym_plane = _snap_sym_plane(snapshot)
        if not snapshot:
            cmds.warning("AnimKey All Mirror: Could not build a mirror snapshot for this rig.")
            return

        start, end, has_selected_range = _time_slider_or_playback_range()
        time_range = (start, end)
        controls_for_keys = _expand_controls_with_opposites(selected, snapshot, sym_plane)
        key_times = _animation_key_times(controls_for_keys, time_range=time_range)

        if key_times:
            anim_total = mirror_animation(
                selected,
                selected_channels,
                time_range=time_range,
                manage_undo=False,
            )
            label = f"{start:g}-{end:g}" if has_selected_range else "playback range"
            if anim_total:
                cmds.inViewMessage(
                    amg=f"<span style='color:#88c0d0'>Mirrored animation ({label}): {anim_total} values</span>",
                    pos='topCenter', fade=True, fadeStayTime=1200
                )
            else:
                cmds.warning(f"AnimKey All Mirror: No mirrorable values found in {label}.")
            return

        if has_selected_range:
            cmds.warning(f"AnimKey All Mirror: No keys found in selected range {start:g}-{end:g}.")
            return

        # No animation keys in the playback range: fall back to current pose.
        total, _ = _mirror_selected_pose_two_phase(selected, snapshot, sym_plane, selected_channels)
        if total:
            cmds.inViewMessage(
                amg=f"<span style='color:#88c0d0'>Swapped pose: {total} values</span>",
                pos='topCenter', fade=True, fadeStayTime=800
            )
    except Exception as e:
        cmds.warning(f"AnimKey All Mirror: {e}")
        import traceback; traceback.print_exc()
    finally:
        _close_animkey_undo_chunk()


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
            snapshot = load_snapshot(rig_name)
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
#                      DIAGNOSE
# ═══════════════════════════════════════════════════════════════════════════════

def diagnose_mirror(*args):
    """Print detailed mirror info for selected controls to Script Editor."""
    selected = cmds.ls(selection=True)
    if not selected:
        cmds.warning("AnimKey: Select controls to diagnose.")
        return

    rig_name  = get_rig_identifier(selected)
    snapshot  = load_snapshot(rig_name)
    sym_plane = _snap_sym_plane(snapshot)

    print("\n" + "=" * 65)
    print("  AnimKey Mirror - Diagnosis")
    print("=" * 65)
    print(f"  Rig          : {rig_name}")
    print(f"  Snapshot     : {'LOADED (calibrated)' if snapshot else 'NOT FOUND (using defaults)'}")
    print(f"  Sym plane N  : {[round(v,4) for v in sym_plane.normal]}")
    print(f"  Sym plane pt : {[round(v,4) for v in sym_plane.point]}")

    for ctrl in selected:
        opp       = _snap_opposite(ctrl, snapshot) if snapshot else find_opposite_smart(ctrl, sym_plane)
        ctrl_data = _snap_ctrl_data(ctrl, snapshot)

        print(f"\n  ── {ctrl}")
        print(f"     Opposite  : {opp or 'NONE (central)'}")
        print(f"     Type      : {'PAIRED' if opp else 'CENTRAL'}")
        if ctrl_data:
            print(f"     Rot order : {ctrl_data.get('rot_order','xyz')}")
            custom = ctrl_data.get('custom_mults', {})
            if custom:
                print(f"     Custom inv: {custom}")
            # Show the same calibrated values used by mirror and the slider.
            target = opp if opp and cmds.objExists(opp) else ctrl
            lv = compute_mirror_values(ctrl, target=target, snapshot=snapshot, sym_plane=sym_plane,
                                       is_central=(target == ctrl))
            print(f"     Mirror result preview (calibrated values on target):")
            for a, v in lv.items():
                print(f"       {a:20s} -> {v:.4f}")
        else:
            print(f"     (no snapshot data — run Snapshot Mirror in T-Pose)")

    print("\n" + "=" * 65)
    cmds.warning("AnimKey: Diagnosis printed to Script Editor.")


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

def show_mirror_snapshot_info(*args):
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.mirror.show_mirror_snapshot_info"):
        return None
    diagnose_mirror()

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
    diagnose_mirror()


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
        "tooltip" : "Calibrated pose mirror. Right-click for more options.",
        "icon"    : "mirror.svg",
        "shortcut": None,
    }
