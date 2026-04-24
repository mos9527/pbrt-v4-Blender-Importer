"""
pbrt v4 scene parser
====================
Parses the subset of pbrt v4 needed for Blender import:

  Transforms   Translate, Rotate, Scale, Transform, ConcatTransform, LookAt, Identity
  Attributes   AttributeBegin / AttributeEnd
  Instancing   ObjectBegin / ObjectEnd / ObjectInstance
  Shapes       plymesh, trianglemesh, loopsubdiv, sphere, disk
  Materials    Material, MakeNamedMaterial, NamedMaterial  (name only, no shading params)
  Camera       perspective  (fov, lensradius, focaldistance)
  Film         xresolution, yresolution
  Includes     Include

Everything else (lights, volumes, samplers, integrators, textures) is skipped.
"""

import math
import os
import re

# ---------------------------------------------------------------------------
# Matrix helpers  (column-major 4×4, same convention as pbrt)
# ---------------------------------------------------------------------------

def mat_identity():
    return [
        1,0,0,0,
        0,1,0,0,
        0,0,1,0,
        0,0,0,1,
    ]

def mat_mul(a, b):
    """Column-major 4×4 multiply: result = a * b"""
    r = [0.0] * 16
    for row in range(4):
        for col in range(4):
            s = 0.0
            for k in range(4):
                s += a[row + k*4] * b[k + col*4]
            r[row + col*4] = s
    return r

def mat_translate(tx, ty, tz):
    m = mat_identity()
    m[12] = tx; m[13] = ty; m[14] = tz
    return m

def mat_scale(sx, sy, sz):
    m = mat_identity()
    m[0] = sx; m[5] = sy; m[10] = sz
    return m

def mat_rotate(angle_deg, ax, ay, az):
    """Axis-angle rotation matrix (axis need not be unit length)."""
    l = math.sqrt(ax*ax + ay*ay + az*az)
    if l < 1e-12:
        return mat_identity()
    ax /= l; ay /= l; az /= l
    a = math.radians(angle_deg)
    c = math.cos(a); s = math.sin(a); t = 1 - c
    return [
        t*ax*ax+c,    t*ax*ay+s*az, t*ax*az-s*ay, 0,
        t*ax*ay-s*az, t*ay*ay+c,    t*ay*az+s*ax, 0,
        t*ax*az+s*ay, t*ay*az-s*ax, t*az*az+c,    0,
        0,            0,            0,             1,
    ]

def mat_lookat(ex, ey, ez, lx, ly, lz, ux, uy, uz):
    """
    pbrt LookAt → camera-to-world matrix (column-major).

    Follows the pbrt-v4 source convention:
        dir   = normalize(look - eye)
        right = normalize(cross(normalize(up), dir))
        newUp = cross(dir, right)
    Column layout: [right | newUp | dir | eye]
    """
    ddx, ddy, ddz = lx-ex, ly-ey, lz-ez
    l = math.sqrt(ddx*ddx + ddy*ddy + ddz*ddz)
    if l < 1e-12:
        return mat_identity()
    ddx /= l; ddy /= l; ddz /= l

    ul = math.sqrt(ux*ux + uy*uy + uz*uz)
    if ul > 1e-12:
        ux /= ul; uy /= ul; uz /= ul

    rx = uy*ddz - uz*ddy
    ry = uz*ddx - ux*ddz
    rz = ux*ddy - uy*ddx
    rl = math.sqrt(rx*rx + ry*ry + rz*rz)
    if rl > 1e-12:
        rx /= rl; ry /= rl; rz /= rl

    nux = ddy*rz - ddz*ry
    nuy = ddz*rx - ddx*rz
    nuz = ddx*ry - ddy*rx

    return [
        rx,  ry,  rz,  0,
        nux, nuy, nuz, 0,
        ddx, ddy, ddz, 0,
        ex,  ey,  ez,  1,
    ]

# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r'"[^"]*"'          # quoted string
    r'|#[^\n]*'         # comment
    r'|\[[^\]]*\]'      # bracketed list  [ ... ]
    r'|[^\s\[\]"#]+'    # bare word / number
)

def tokenize(text):
    """Tokenise a pbrt file; comments are discarded."""
    tokens = []
    for m in _TOKEN_RE.finditer(text):
        t = m.group(0)
        if t.startswith('#'):
            continue
        tokens.append(t)
    return tokens

def _parse_floats(s):
    s = s.strip()
    if s.startswith('['):
        s = s[1:-1]
    return [float(x) for x in s.split()]

# ---------------------------------------------------------------------------
# Scene data structures
# ---------------------------------------------------------------------------

class ShapeNode:
    """A single renderable shape with its world-space transform and material."""
    __slots__ = ('shape_type', 'params', 'world_matrix', 'material_name')

    def __init__(self, shape_type, params, world_matrix, material_name=''):
        self.shape_type    = shape_type    # str: "plymesh", "trianglemesh", …
        self.params        = params        # dict: param_name → value list
        self.world_matrix  = world_matrix  # 16 floats, column-major
        self.material_name = material_name # pbrt material type or named-mat id


class ObjectDef:
    """A named instancing prototype (ObjectBegin … ObjectEnd)."""
    __slots__ = ('name', 'shapes')

    def __init__(self, name):
        self.name   = name
        self.shapes = []   # list[ShapeNode]


class InstanceNode:
    """A placed instance of an ObjectDef (ObjectInstance)."""
    __slots__ = ('object_name', 'world_matrix')

    def __init__(self, object_name, world_matrix):
        self.object_name  = object_name
        self.world_matrix = world_matrix  # 16 floats, column-major


class SceneData:
    """All geometry extracted from one pbrt file."""

    def __init__(self):
        self.shapes    = []  # list[ShapeNode]   — top-level shapes
        self.objects   = {}  # name → ObjectDef  — instancing prototypes
        self.instances = []  # list[InstanceNode]

        # Camera description, or None if not present.
        # Keys: type, ctm (16f col-major), fov (deg), fov_axis,
        #       lensradius, focaldistance, xresolution, yresolution
        self.camera = None

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Directives whose parameter blocks we read and discard entirely
_SKIP_DIRECTIVES = {
    'Sampler', 'Integrator', 'PixelFilter',
    'Texture',
    'LightSource', 'AreaLightSource',
    'MediumInterface', 'MakeNamedMedium',
    'ReverseOrientation', 'CoordSysTransform',
    'ActiveTransform',
}


class _Parser:
    def __init__(self, tokens, base_dir):
        self.tokens   = tokens
        self.pos      = 0
        self.base_dir = base_dir

        # CTM stack; each entry is 16 floats (column-major)
        self.ctm_stack  = [mat_identity()]
        # Attribute stack: list of (ctm_stack_snapshot, material_name)
        self.attr_stack = []

        # Current material name (pbrt type string or named-mat label)
        self._current_mat = ''
        self._named_mats  = {}   # MakeNamedMaterial registry

        self.scene = SceneData()

        # Film resolution — may arrive before or after Camera
        self._xres = 800
        self._yres = 600

        # Set while inside ObjectBegin … ObjectEnd
        self._obj_def = None

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self):
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect_string(self):
        t = self.next()
        return t[1:-1] if t.startswith('"') else t

    def _peek_is_param(self):
        t = self.peek()
        return t is not None and t.startswith('"') and ' ' in t[1:-1]

    def _read_params(self):
        """Consume zero or more  "type name" value  pairs → dict."""
        params = {}
        while True:
            t = self.peek()
            if t is None or not (t.startswith('"') and ' ' in t[1:-1]):
                break
            decl  = self.next()[1:-1]
            parts = decl.split(' ', 1)
            ptype = parts[0]
            pname = parts[1] if len(parts) > 1 else ''

            val = self.peek()
            if val is None:
                break
            if val.startswith('['):
                raw = self.next()[1:-1].split()
            elif val.startswith('"'):
                raw = [self.next()[1:-1]]
            else:
                raw = [self.next()]

            if ptype == 'integer':
                try:
                    params[pname] = [int(x) for x in raw]
                except ValueError:
                    params[pname] = raw
            elif ptype in ('float', 'point2', 'point3',
                           'normal3', 'vector2', 'vector3'):
                try:
                    params[pname] = [float(x) for x in raw]
                except ValueError:
                    params[pname] = raw
            else:
                params[pname] = [x.strip('"') for x in raw]
        return params

    # ------------------------------------------------------------------
    # CTM helpers
    # ------------------------------------------------------------------

    @property
    def ctm(self):
        return self.ctm_stack[-1]

    def _apply(self, m):
        self.ctm_stack[-1] = mat_mul(self.ctm_stack[-1], m)

    def _push_attr(self):
        self.attr_stack.append((list(self.ctm_stack), self._current_mat))
        self.ctm_stack = list(self.ctm_stack)

    def _pop_attr(self):
        if self.attr_stack:
            saved_ctm, saved_mat = self.attr_stack.pop()
            self.ctm_stack    = saved_ctm
            self._current_mat = saved_mat

    # ------------------------------------------------------------------
    # Main dispatch loop
    # ------------------------------------------------------------------

    def parse_all(self):
        while self.pos < len(self.tokens):
            self._dispatch()

    def _dispatch(self):
        t = self.peek()
        if t is None:
            return
        if t.startswith('"'):
            self.next()   # stray quoted token — skip
            return

        d = self.next()   # directive

        # ---- world / attribute scope ----

        if d == 'WorldBegin':
            # Film may follow Camera in the file; patch resolution now
            if self.scene.camera is not None:
                self.scene.camera['xresolution'] = self._xres
                self.scene.camera['yresolution'] = self._yres
            self.ctm_stack = [mat_identity()]

        elif d == 'AttributeBegin':
            self._push_attr()

        elif d == 'AttributeEnd':
            self._pop_attr()

        elif d == 'ObjectBegin':
            name = self.expect_string()
            self._obj_def = ObjectDef(name)
            self.scene.objects[name] = self._obj_def
            self._push_attr()

        elif d == 'ObjectEnd':
            self._obj_def = None
            self._pop_attr()

        elif d == 'ObjectInstance':
            name = self.expect_string()
            self.scene.instances.append(InstanceNode(name, list(self.ctm)))

        # ---- transforms ----

        elif d == 'Translate':
            tx, ty, tz = float(self.next()), float(self.next()), float(self.next())
            self._apply(mat_translate(tx, ty, tz))

        elif d == 'Scale':
            sx, sy, sz = float(self.next()), float(self.next()), float(self.next())
            self._apply(mat_scale(sx, sy, sz))

        elif d == 'Rotate':
            angle = float(self.next())
            ax, ay, az = float(self.next()), float(self.next()), float(self.next())
            self._apply(mat_rotate(angle, ax, ay, az))

        elif d == 'Transform':
            self.ctm_stack[-1] = _parse_floats(self.next())

        elif d == 'ConcatTransform':
            self._apply(_parse_floats(self.next()))

        elif d == 'LookAt':
            nums = []
            while len(nums) < 9:
                nums += _parse_floats(self.next())
            self._apply(mat_lookat(*nums))

        elif d == 'Identity':
            self.ctm_stack[-1] = mat_identity()

        # ---- camera / film ----

        elif d == 'Film':
            self.expect_string()   # film type ("rgb") — ignored
            p = self._read_params()
            if 'xresolution' in p: self._xres = int(p['xresolution'][0])
            if 'yresolution' in p: self._yres = int(p['yresolution'][0])

        elif d == 'Camera':
            cam_type = self.expect_string()
            p = self._read_params()
            self.scene.camera = {
                'type':          cam_type,
                'ctm':           list(self.ctm),
                'fov':           float(p.get('fov',           [90.0])[0]),
                'fov_axis':      p.get('fovaxis', ['y'])[0],
                'lensradius':    float(p.get('lensradius',    [0.0])[0]),
                'focaldistance': float(p.get('focaldistance', [1e30])[0]),
                'xresolution':   self._xres,
                'yresolution':   self._yres,
            }

        # ---- materials (name only) ----

        elif d == 'Material':
            self._current_mat = self.expect_string()
            self._read_params()   # consume shading params, ignore values

        elif d == 'MakeNamedMaterial':
            name = self.expect_string()
            p    = self._read_params()
            label = p.get('type', [name])[0]
            self._named_mats[name] = f"{name}_{label}"

        elif d == 'NamedMaterial':
            name = self.expect_string()
            self._current_mat = self._named_mats.get(name, name)

        # ---- shapes ----

        elif d == 'Shape':
            shape_type = self.expect_string()
            params     = self._read_params()
            node = ShapeNode(shape_type, params, list(self.ctm), self._current_mat)
            if self._obj_def is not None:
                self._obj_def.shapes.append(node)
            else:
                self.scene.shapes.append(node)

        # ---- includes ----

        elif d == 'Include':
            fname = self.expect_string()
            _parse_file_into(os.path.join(self.base_dir, fname), self)

        # ---- skip known non-geometry directives ----

        elif d in _SKIP_DIRECTIVES:
            self._read_params()

        else:
            # Unknown directive: consume any immediately following param list
            if self._peek_is_param():
                self._read_params()


def _parse_file_into(fpath, parser):
    """Tokenise *fpath* and continue parsing into an existing _Parser state."""
    if not os.path.isfile(fpath):
        print(f"[pbrt_parser] WARNING: file not found: {fpath}")
        return
    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    old_tokens  = parser.tokens
    old_pos     = parser.pos
    old_basedir = parser.base_dir

    parser.tokens   = tokenize(text)
    parser.pos      = 0
    parser.base_dir = os.path.dirname(fpath)
    parser.parse_all()

    parser.tokens   = old_tokens
    parser.pos      = old_pos
    parser.base_dir = old_basedir


def parse_pbrt(filepath):
    """Parse *filepath* and return a :class:`SceneData` instance."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    p = _Parser(tokenize(text), os.path.dirname(filepath))
    p.parse_all()
    return p.scene
