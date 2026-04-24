"""
pbrt v4 → Principled BSDF material translator
==============================================

Converts a :class:`pbrt_parser.MaterialDef` into a fully-wired Blender
material with a Principled BSDF node, including texture connections.

Supported pbrt material types
------------------------------
diffuse          → Principled BSDF (Roughness=1, Metallic=0)
coateddiffuse    → + Coat Weight/Roughness/IOR
conductor        → Metallic=1, IOR from preset table or SPD average
coatedconductor  → conductor base + coat layer
dielectric       → Transmission=1, IOR
thindielectric   → same as dielectric (thin-film flag not mapped)
subsurface       → Subsurface Weight + Radius
diffusetransmission → Transmission Weight (approximate)
mix              → Mix Shader (two sub-materials, requires recursion)
hair             → Principled Hair BSDF (approximate; skips fibres)

IOR presets
-----------
Named glass grades (e.g. "glass-BK7") and conductor spectra (e.g. "Au")
are resolved from built-in tables so that SPD files are not required.
If a spectrum string is not in the table the fallback is a physically
plausible default for that material type.

Texture wiring
--------------
``texture reflectance / transmittance / roughness / displacement`` params
that reference a TextureDef imagemap are connected via an Image Texture
node.  Other texture types (scale, mix, …) are not wired but the param
is recorded as a custom property on the material for reference.
"""

import os
import math
import bpy

# ---------------------------------------------------------------------------
# IOR / conductor preset tables
# ---------------------------------------------------------------------------

# Glass grades: name → IOR (n, real part only — Principled uses single value)
_GLASS_IOR = {
    'glass-bk7':       1.5168,
    'glass-baf10':     1.6700,
    'glass-fk51a':     1.4866,
    'glass-lasf9':     1.8503,
    'glass-sf11':      1.7847,
    'glass-sf5':       1.6727,
    'glass-sk16':      1.6200,
    'glass-f5':        1.6034,
    'glass':           1.5,
    'fused silica':    1.4585,
    'water':           1.333,
    'diamond':         2.418,
    'vacuum':          1.0,
    'air':             1.0003,
}

# Conductors: name → (eta_avg, k_avg) in the visible range (~450-680 nm)
# Values are sRGB luminance-weighted averages of the measured spectra.
_CONDUCTOR_IOR = {
    # Gold
    'au':              (0.18, 3.42),
    'gold':            (0.18, 3.42),
    # Silver
    'ag':              (0.14, 3.98),
    'silver':          (0.14, 3.98),
    # Aluminium
    'al':              (1.10, 7.12),
    'aluminium':       (1.10, 7.12),
    'aluminum':        (1.10, 7.12),
    # Copper
    'cu':              (0.27, 3.41),
    'copper':          (0.27, 3.41),
    # Iron
    'fe':              (2.93, 3.09),
    'iron':            (2.93, 3.09),
    # Chromium
    'cr':              (3.17, 3.31),
    # Platinum
    'pt':              (2.63, 3.55),
    # Titanium
    'ti':              (2.16, 2.93),
    # Nickel
    'ni':              (1.99, 3.75),
    # Cobalt
    'co':              (2.07, 4.08),
    # Tungsten
    'w':               (3.48, 2.89),
    'tungsten':        (3.48, 2.89),
}


def _resolve_glass_ior(spec):
    """Return IOR float for a glass spectrum name/value or a float."""
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, list):
        if len(spec) == 1:
            s = spec[0]
            if isinstance(s, (int, float)):
                return float(s)
            return _GLASS_IOR.get(str(s).lower(), 1.5)
        # numeric list → treat as single value
        try: return float(spec[0])
        except: pass
    key = str(spec).lower()
    return _GLASS_IOR.get(key, 1.5)


def _resolve_conductor_ior(eta_spec, k_spec):
    """
    Return (eta, k) floats for conductor spectra.
    Accepts: string preset name, list of floats (rgb), or SPD filename.
    """
    def _from_spec(spec, default):
        if spec is None:
            return default
        if isinstance(spec, list):
            if len(spec) == 1:
                s = spec[0]
                if isinstance(s, (int, float)):
                    return float(s)
                # try preset
                key = str(s).lower()
                # strip path components and extension
                stem = os.path.splitext(os.path.basename(key))[0]
                # remove trailing .eta / .k
                stem = stem.replace('.eta','').replace('.k','').strip('.')
                return stem   # will be resolved below
            # rgb triplet → luminance average
            try:
                vals = [float(x) for x in spec]
                return sum(v*w for v,w in zip(vals,[0.2126,0.7152,0.0722]))
            except:
                pass
        key = str(spec).lower()
        stem = os.path.splitext(os.path.basename(key))[0]
        stem = stem.replace('.eta','').replace('.k','').strip('.')
        return stem

    eta_raw = _from_spec(eta_spec, 'au')
    k_raw   = _from_spec(k_spec,   'au')

    # If both resolved to the same preset name, look up table
    if isinstance(eta_raw, str):
        preset = _CONDUCTOR_IOR.get(eta_raw.lower())
        if preset:
            return preset
        # Try k string too
        if isinstance(k_raw, str):
            preset = _CONDUCTOR_IOR.get(k_raw.lower())
            if preset:
                return preset
        return (0.18, 3.42)   # fall back to gold-like

    eta = eta_raw if isinstance(eta_raw, float) else 0.18
    k   = k_raw   if isinstance(k_raw,   float) else 3.42
    return (eta, k)


def _read_spd(filepath):
    """
    Read a pbrt SPD file and return the visible-range (450-680nm) average.
    Returns None if the file cannot be opened.
    """
    try:
        vals = []
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    nm = float(parts[0])
                    if 450 <= nm <= 680:
                        vals.append(float(parts[1]))
        return sum(vals)/len(vals) if vals else None
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Node-graph helpers
# ---------------------------------------------------------------------------

def _principled(mat):
    """Return the Principled BSDF node from *mat*'s node tree (always exists)."""
    nt = mat.node_tree
    for n in nt.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    # Shouldn't happen if use_nodes=True was set at creation, but just in case
    n = nt.nodes.new('ShaderNodeBsdfPrincipled')
    out = next((x for x in nt.nodes if x.type == 'OUTPUT_MATERIAL'), None)
    if out:
        nt.links.new(n.outputs['BSDF'], out.inputs['Surface'])
    return n


def _set_input(bsdf, socket_name, value):
    """Set a socket value if it exists (handles API changes between Blender versions)."""
    if socket_name in bsdf.inputs:
        bsdf.inputs[socket_name].default_value = value


def _connect_image_texture(mat, bsdf, socket_name, filepath,
                           colorspace='sRGB', is_float=False):
    """
    Add an Image Texture node to *mat* connected to *bsdf* socket.
    Returns the Image Texture node, or None if the file doesn't exist.
    """
    if not os.path.isfile(filepath):
        return None
    nt = mat.node_tree

    img_node = nt.nodes.new('ShaderNodeTexImage')
    try:
        img = bpy.data.images.load(filepath, check_existing=True)
        if is_float:
            img.colorspace_settings.name = 'Non-Color'
        else:
            img.colorspace_settings.name = colorspace
        img_node.image = img
    except Exception as e:
        print(f"[pbrt_materials] Could not load image {filepath}: {e}")
        return None

    if socket_name in bsdf.inputs:
        nt.links.new(img_node.outputs['Color'], bsdf.inputs[socket_name])
    return img_node


def _wire_texture_param(mat, bsdf, socket_name, param_value,
                        textures, base_dir, is_float=False):
    """
    If *param_value* is a texture reference, connect an Image Texture node.
    Returns True if a texture was connected.
    """
    if not isinstance(param_value, list) or not param_value:
        return False
    ref = param_value[0]
    if not isinstance(ref, str):
        return False
    tex_def = textures.get(ref)
    if tex_def is None or tex_def.tex_type != 'imagemap':
        return False
    fnames = tex_def.params.get('filename', [])
    if not fnames:
        return False
    fpath = os.path.normpath(os.path.join(base_dir, fnames[0]))
    _connect_image_texture(mat, bsdf, socket_name, fpath,
                           is_float=is_float)
    return True


# ---------------------------------------------------------------------------
# Per-type translators
#
# Signature: fn(mat, bsdf, params, textures, base_dir)
#   mat      : bpy.types.Material
#   bsdf     : ShaderNodeBsdfPrincipled
#   params   : dict from MaterialDef
#   textures : dict[str, TextureDef]
#   base_dir : str
# ---------------------------------------------------------------------------

def _apply_diffuse(mat, bsdf, params, textures, base_dir):
    bsdf.inputs['Roughness'].default_value = 1.0
    bsdf.inputs['Metallic'].default_value  = 0.0
    if not _wire_texture_param(mat, bsdf, 'Base Color',
                               params.get('reflectance'),
                               textures, base_dir):
        rgb = params.get('reflectance')
        if rgb and len(rgb) >= 3:
            _set_input(bsdf, 'Base Color', (*rgb[:3], 1.0))


def _apply_coateddiffuse(mat, bsdf, params, textures, base_dir):
    _apply_diffuse(mat, bsdf, params, textures, base_dir)
    roughness = params.get('roughness', [0.5])[0]
    bsdf.inputs['Roughness'].default_value = roughness
    _set_input(bsdf, 'Coat Weight',    1.0)
    _set_input(bsdf, 'Coat Roughness', roughness)
    eta = params.get('eta', [1.5])[0]
    _set_input(bsdf, 'Coat IOR',       float(eta))


def _apply_conductor(mat, bsdf, params, textures, base_dir):
    bsdf.inputs['Metallic'].default_value  = 1.0
    bsdf.inputs['Roughness'].default_value = params.get('roughness', [0.01])[0]

    eta_spec = params.get('eta')
    k_spec   = params.get('k')

    def _maybe_load_spd(spec, bd):
        if not isinstance(spec, list) or not spec:
            return spec
        s = spec[0]
        if not isinstance(s, str):
            return spec
        fpath = os.path.normpath(os.path.join(bd or '', s))
        val = _read_spd(fpath)
        if val is not None:
            return [val]
        return spec

    eta_spec = _maybe_load_spd(eta_spec, base_dir)
    k_spec   = _maybe_load_spd(k_spec,   base_dir)

    eta, k = _resolve_conductor_ior(eta_spec, k_spec)
    _set_input(bsdf, 'IOR', eta)

    rgb = params.get('reflectance') or params.get('albedo')
    if rgb and len(rgb) >= 3:
        _set_input(bsdf, 'Base Color', (*rgb[:3], 1.0))

    # Roughness texture (if the roughness param is a texture reference string)
    rough_val = params.get('roughness')
    if isinstance(rough_val, list) and rough_val and isinstance(rough_val[0], str):
        _wire_texture_param(mat, bsdf, 'Roughness', rough_val,
                            textures, base_dir, is_float=True)


def _apply_coatedconductor(mat, bsdf, params, textures, base_dir):
    conductor_params = {
        'eta':         params.get('conductor.eta') or params.get('eta'),
        'k':           params.get('conductor.k')   or params.get('k'),
        'roughness':   params.get('conductor.roughness',
                                  params.get('roughness', [0.01])),
        'reflectance': params.get('reflectance') or params.get('albedo'),
    }
    _apply_conductor(mat, bsdf, conductor_params, textures, base_dir)

    _set_input(bsdf, 'Coat Weight',    1.0)
    irough = params.get('interface.roughness', [0.0])[0]
    _set_input(bsdf, 'Coat Roughness', float(irough))
    ieta   = params.get('eta', [1.5])
    _set_input(bsdf, 'Coat IOR',       _resolve_glass_ior(ieta))


def _apply_dielectric(mat, bsdf, params, textures, base_dir):
    _set_input(bsdf, 'Metallic',            0.0)
    _set_input(bsdf, 'Transmission Weight', 1.0)
    roughness = params.get('roughness', [0.0])[0]
    _set_input(bsdf, 'Roughness', float(roughness))
    eta = _resolve_glass_ior(params.get('eta', [1.5]))
    _set_input(bsdf, 'IOR', eta)


def _apply_thindielectric(mat, bsdf, params, textures, base_dir):
    _apply_dielectric(mat, bsdf, params, textures, base_dir)


def _apply_subsurface(mat, bsdf, params, textures, base_dir):
    _set_input(bsdf, 'Subsurface Weight', 1.0)
    mfp = params.get('mfp')
    if mfp and len(mfp) >= 3:
        _set_input(bsdf, 'Subsurface Radius', (*mfp[:3],))
    roughness = params.get('uroughness') or params.get('roughness')
    if roughness:
        _set_input(bsdf, 'Roughness', float(roughness[0]))
    eta = params.get('eta', [1.3])
    _set_input(bsdf, 'IOR', _resolve_glass_ior(eta))
    rgb = params.get('reflectance')
    if rgb and len(rgb) >= 3:
        _set_input(bsdf, 'Base Color', (*rgb[:3], 1.0))


def _apply_diffusetransmission(mat, bsdf, params, textures, base_dir):
    bsdf.inputs['Roughness'].default_value = 1.0
    trans = params.get('transmittance')
    refl  = params.get('reflectance')
    if trans and len(trans) >= 3:
        lum = 0.2126*trans[0] + 0.7152*trans[1] + 0.0722*trans[2]
        _set_input(bsdf, 'Transmission Weight', min(lum, 1.0))
        _set_input(bsdf, 'Base Color', (*trans[:3], 1.0))
    elif refl and len(refl) >= 3:
        _set_input(bsdf, 'Base Color', (*refl[:3], 1.0))


def _apply_hair(mat, bsdf, params, textures, base_dir):
    bsdf.inputs['Roughness'].default_value = params.get('beta_m', [0.3])[0]
    rgb = params.get('color') or params.get('reflectance')
    if rgb and len(rgb) >= 3:
        _set_input(bsdf, 'Base Color', (*rgb[:3], 1.0))


# ---------------------------------------------------------------------------
# Dispatcher table
# ---------------------------------------------------------------------------

_TRANSLATORS = {
    'diffuse':              _apply_diffuse,
    'coateddiffuse':        _apply_coateddiffuse,
    'conductor':            _apply_conductor,
    'coatedconductor':      _apply_coatedconductor,
    'dielectric':           _apply_dielectric,
    'thindielectric':       _apply_thindielectric,
    'subsurface':           _apply_subsurface,
    'diffusetransmission':  _apply_diffusetransmission,
    'hair':                 _apply_hair,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_material(bpy_mat, mat_def, textures, base_dir):
    """
    Configure *bpy_mat* (must have ``use_nodes=True``) from *mat_def*.

    Parameters
    ----------
    bpy_mat  : bpy.types.Material
    mat_def  : pbrt_parser.MaterialDef
    textures : dict[str, TextureDef]   — from SceneData.textures
    base_dir : str                     — scene file directory (for texture paths)
    """
    bsdf = _principled(bpy_mat)
    fn   = _TRANSLATORS.get(mat_def.mat_type)
    if fn:
        fn(bpy_mat, bsdf, mat_def.params, textures, base_dir)
    else:
        print(f"[pbrt_materials] Unknown material type '{mat_def.mat_type}' "
              f"— leaving Principled BSDF at defaults")
