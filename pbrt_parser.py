"""
pbrt v4 scene parser
====================
Parses the subset of pbrt v4 needed for Blender import:

  Transforms   Translate, Rotate, Scale, Transform, ConcatTransform, LookAt, Identity
  Attributes   AttributeBegin / AttributeEnd
  Instancing   ObjectBegin / ObjectEnd / ObjectInstance
  Shapes       plymesh, trianglemesh, loopsubdiv, sphere, disk
  Materials    Material, MakeNamedMaterial, NamedMaterial  (type + all params)
  Textures     Texture  (imagemap only; stored for material lookup)
  Camera       perspective  (fov, lensradius, focaldistance)
  Film         xresolution, yresolution
  Includes     Include

Everything else (lights, volumes, samplers, integrators) is skipped.
"""

import math
import os
import re

# ---------------------------------------------------------------------------
# Matrix helpers  (column-major 4×4, same convention as pbrt)
# ---------------------------------------------------------------------------

def mat_identity():
    return [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

def mat_mul(a, b):
    r = [0.0] * 16
    for row in range(4):
        for col in range(4):
            s = 0.0
            for k in range(4):
                s += a[row + k*4] * b[k + col*4]
            r[row + col*4] = s
    return r

def mat_translate(tx, ty, tz):
    m = mat_identity(); m[12]=tx; m[13]=ty; m[14]=tz; return m

def mat_scale(sx, sy, sz):
    m = mat_identity(); m[0]=sx; m[5]=sy; m[10]=sz; return m

def mat_rotate(angle_deg, ax, ay, az):
    l = math.sqrt(ax*ax + ay*ay + az*az)
    if l < 1e-12: return mat_identity()
    ax/=l; ay/=l; az/=l
    a=math.radians(angle_deg); c=math.cos(a); s=math.sin(a); t=1-c
    return [t*ax*ax+c,    t*ax*ay+s*az, t*ax*az-s*ay, 0,
            t*ax*ay-s*az, t*ay*ay+c,    t*ay*az+s*ax, 0,
            t*ax*az+s*ay, t*ay*az-s*ax, t*az*az+c,    0,
            0,            0,            0,             1]

def mat_lookat(ex, ey, ez, lx, ly, lz, ux, uy, uz):
    """pbrt LookAt → camera-to-world (column-major)."""
    ddx,ddy,ddz = lx-ex,ly-ey,lz-ez
    l = math.sqrt(ddx*ddx+ddy*ddy+ddz*ddz)
    if l < 1e-12: return mat_identity()
    ddx/=l; ddy/=l; ddz/=l
    ul = math.sqrt(ux*ux+uy*uy+uz*uz)
    if ul>1e-12: ux/=ul; uy/=ul; uz/=ul
    rx=uy*ddz-uz*ddy; ry=uz*ddx-ux*ddz; rz=ux*ddy-uy*ddx
    rl=math.sqrt(rx*rx+ry*ry+rz*rz)
    if rl>1e-12: rx/=rl; ry/=rl; rz/=rl
    nux=ddy*rz-ddz*ry; nuy=ddz*rx-ddx*rz; nuz=ddx*ry-ddy*rx
    return [rx,  ry,  rz,  0,
            nux, nuy, nuz, 0,
            ddx, ddy, ddz, 0,
            ex,  ey,  ez,  1]

# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r'"[^"]*"'       # quoted string
    r'|#[^\n]*'      # comment
    r'|\[[^\]]*\]'   # bracketed list
    r'|[^\s\[\]"#]+'  # bare word / number
)

def tokenize(text):
    tokens = []
    for m in _TOKEN_RE.finditer(text):
        t = m.group(0)
        if not t.startswith('#'):
            tokens.append(t)
    return tokens

def _parse_floats(s):
    s = s.strip()
    if s.startswith('['): s = s[1:-1]
    return [float(x) for x in s.split()]

# ---------------------------------------------------------------------------
# Scene data structures
# ---------------------------------------------------------------------------

class MaterialDef:
    """
    One pbrt material definition (anonymous or named).

    Attributes
    ----------
    mat_type : str
        pbrt material type: "diffuse", "conductor", "dielectric", …
    params : dict
        Parsed parameter dict from ``_read_params``.
        Keys are param names (str); values are lists.
        Texture references are stored as strings in params under their name.
    name : str
        Human-readable key used in the scene's material registry and as the
        Blender material name.  For named materials this is the pbrt name;
        for anonymous ones it is ``"<type>_<hex>"`` (hash of frozen params).
    """
    __slots__ = ('mat_type', 'params', 'name')

    def __init__(self, mat_type, params, name):
        self.mat_type = mat_type
        self.params   = params
        self.name     = name


class TextureDef:
    """
    A parsed pbrt Texture directive.

    Only ``imagemap`` textures are stored (other types are ignored).

    Attributes
    ----------
    tex_name   : str  — pbrt texture name
    tex_type   : str  — "imagemap" (others ignored)
    color_type : str  — "spectrum", "float", etc.
    params     : dict — same format as MaterialDef.params
    """
    __slots__ = ('tex_name', 'tex_type', 'color_type', 'params')

    def __init__(self, tex_name, color_type, tex_type, params):
        self.tex_name   = tex_name
        self.color_type = color_type
        self.tex_type   = tex_type
        self.params     = params


class ShapeNode:
    """A single renderable shape with its world-space transform and material key."""
    __slots__ = ('shape_type', 'params', 'world_matrix', 'material_key')

    def __init__(self, shape_type, params, world_matrix, material_key=''):
        self.shape_type   = shape_type    # str
        self.params       = params        # dict
        self.world_matrix = world_matrix  # 16 floats, column-major
        self.material_key = material_key  # key into SceneData.materials


class ObjectDef:
    __slots__ = ('name', 'shapes')
    def __init__(self, name):
        self.name   = name
        self.shapes = []


class InstanceNode:
    __slots__ = ('object_name', 'world_matrix')
    def __init__(self, object_name, world_matrix):
        self.object_name  = object_name
        self.world_matrix = world_matrix


class SceneData:
    """All data extracted from one pbrt file."""

    def __init__(self):
        self.shapes    = []  # list[ShapeNode]
        self.objects   = {}  # name → ObjectDef
        self.instances = []  # list[InstanceNode]

        # Material registry: key → MaterialDef
        # Key = pbrt name (MakeNamedMaterial) or "<type>_<hex>" (anonymous)
        self.materials = {}

        # Texture registry: tex_name → TextureDef  (imagemap only)
        self.textures  = {}

        # Camera (None if absent)
        # Keys: type, ctm, fov, fov_axis, lensradius, focaldistance,
        #       xresolution, yresolution
        self.camera = None

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_SKIP_DIRECTIVES = {
    'Sampler', 'Integrator', 'PixelFilter',
    'LightSource', 'AreaLightSource',
    'MediumInterface', 'MakeNamedMedium',
    'ReverseOrientation', 'CoordSysTransform',
    'ActiveTransform',
}


def _mat_key(mat_type, params):
    """Stable key for an anonymous material: '<type>_<8-hex-hash>'."""
    try:
        h = hash(frozenset(
            (k, tuple(v) if isinstance(v, list) else v)
            for k, v in params.items()
        )) & 0xFFFFFFFF
    except TypeError:
        h = id(params) & 0xFFFFFFFF
    return f"{mat_type}_{h:08x}"


class _Parser:
    def __init__(self, tokens, base_dir):
        self.tokens   = tokens
        self.pos      = 0
        self.base_dir = base_dir

        self.ctm_stack  = [mat_identity()]
        # attr stack: (ctm_stack_snapshot, current_mat_key)
        self.attr_stack = []

        self._current_mat_key = ''
        self._named_mat_keys  = {}   # pbrt name → registry key

        self.scene = SceneData()

        self._xres = 800
        self._yres = 600

        self._obj_def = None

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self):
        t = self.tokens[self.pos]; self.pos += 1; return t

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
            if val is None: break
            if val.startswith('['):
                raw = self.next()[1:-1].split()
            elif val.startswith('"'):
                raw = [self.next()[1:-1]]
            else:
                raw = [self.next()]

            if ptype == 'integer':
                try:    params[pname] = [int(x) for x in raw]
                except: params[pname] = raw
            elif ptype in ('float','point2','point3','normal3','vector2','vector3'):
                try:    params[pname] = [float(x) for x in raw]
                except: params[pname] = raw
            elif ptype in ('rgb', 'color'):
                try:    params[pname] = [float(x) for x in raw]
                except: params[pname] = raw
            else:
                # string, spectrum, texture, bool → keep as strings
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
        self.attr_stack.append((list(self.ctm_stack), self._current_mat_key))
        self.ctm_stack = list(self.ctm_stack)

    def _pop_attr(self):
        if self.attr_stack:
            saved_ctm, saved_key = self.attr_stack.pop()
            self.ctm_stack        = saved_ctm
            self._current_mat_key = saved_key

    # ------------------------------------------------------------------
    # Material registration helpers
    # ------------------------------------------------------------------

    def _register_material(self, mat_type, params, name=None):
        """
        Add a MaterialDef to the scene registry and return its key.

        If *name* is given (MakeNamedMaterial) that is the key.
        Otherwise a stable hash key is derived from (type, params).
        Duplicate anonymous materials (same type+params) share one entry.
        """
        if name:
            key = name
        else:
            key = _mat_key(mat_type, params)

        if key not in self.scene.materials:
            self.scene.materials[key] = MaterialDef(mat_type, params, key)
        return key

    # ------------------------------------------------------------------
    # Main dispatch loop
    # ------------------------------------------------------------------

    def parse_all(self):
        while self.pos < len(self.tokens):
            self._dispatch()

    def _dispatch(self):
        t = self.peek()
        if t is None: return
        if t.startswith('"'):
            self.next(); return

        d = self.next()

        # ---- scope ----

        if d == 'WorldBegin':
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
            self.scene.instances.append(
                InstanceNode(self.expect_string(), list(self.ctm)))

        # ---- transforms ----

        elif d == 'Translate':
            tx,ty,tz = float(self.next()),float(self.next()),float(self.next())
            self._apply(mat_translate(tx,ty,tz))

        elif d == 'Scale':
            sx,sy,sz = float(self.next()),float(self.next()),float(self.next())
            self._apply(mat_scale(sx,sy,sz))

        elif d == 'Rotate':
            angle = float(self.next())
            ax,ay,az = float(self.next()),float(self.next()),float(self.next())
            self._apply(mat_rotate(angle,ax,ay,az))

        elif d == 'Transform':
            self.ctm_stack[-1] = _parse_floats(self.next())

        elif d == 'ConcatTransform':
            self._apply(_parse_floats(self.next()))

        elif d == 'LookAt':
            nums = []
            while len(nums) < 9: nums += _parse_floats(self.next())
            self._apply(mat_lookat(*nums))

        elif d == 'Identity':
            self.ctm_stack[-1] = mat_identity()

        # ---- camera / film ----

        elif d == 'Film':
            self.expect_string()
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

        # ---- textures ----

        elif d == 'Texture':
            # Texture "name" "color_type" "tex_type"  [params]
            tex_name   = self.expect_string()
            color_type = self.expect_string()
            tex_type   = self.expect_string()
            params     = self._read_params()
            if tex_type == 'imagemap':
                self.scene.textures[tex_name] = TextureDef(
                    tex_name, color_type, tex_type, params)
            # Other texture types (scale, mix, …) are ignored

        # ---- materials ----

        elif d == 'Material':
            mat_type = self.expect_string()
            params   = self._read_params()
            key = self._register_material(mat_type, params)
            self._current_mat_key = key

        elif d == 'MakeNamedMaterial':
            name   = self.expect_string()
            params = self._read_params()
            # "type" param carries the pbrt material type
            mat_type = params.pop('type', [name])[0]
            key = self._register_material(mat_type, params, name=name)
            self._named_mat_keys[name] = key

        elif d == 'NamedMaterial':
            name = self.expect_string()
            self._current_mat_key = self._named_mat_keys.get(name, name)

        # ---- shapes ----

        elif d == 'Shape':
            shape_type = self.expect_string()
            params     = self._read_params()
            node = ShapeNode(shape_type, params,
                             list(self.ctm), self._current_mat_key)
            if self._obj_def is not None:
                self._obj_def.shapes.append(node)
            else:
                self.scene.shapes.append(node)

        # ---- includes ----

        elif d == 'Include':
            _parse_file_into(
                os.path.join(self.base_dir, self.expect_string()), self)

        elif d in _SKIP_DIRECTIVES:
            self._read_params()

        else:
            if self._peek_is_param():
                self._read_params()


def _parse_file_into(fpath, parser):
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
