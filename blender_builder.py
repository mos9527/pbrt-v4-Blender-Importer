"""
Blender scene builder
=====================
Converts a :class:`pbrt_parser.SceneData` into Blender objects.

Geometry
--------
  plymesh      → ``bpy.ops.wm.ply_import`` (Blender 4.x) /
                  ``bpy.ops.import_mesh.ply`` (Blender 3.x)
  trianglemesh → ``bpy.data.meshes`` via ``from_pydata``
  loopsubdiv   → same as trianglemesh + Subdivision Surface modifier
  sphere       → UV sphere (bmesh)
  disk         → filled circle (bmesh)

Instancing
----------
  ObjectBegin/End → hidden ``bpy.data.collections``
  ObjectInstance  → Empty with ``instance_type = 'COLLECTION'``

Materials
---------
  Each unique pbrt material becomes one Blender material with a fully
  configured Principled BSDF.  See :mod:`pbrt_materials` for the mapping.

Camera
------
  A single perspective camera is created and set as the active scene camera.
  Resolution is applied to ``scene.render.resolution_x/y``.

Coordinate system
-----------------
  pbrt uses a right-handed, Y-up system; Blender uses right-handed, Z-up.
  The conversion matrix (_PBRT_TO_BLENDER) is baked into every object's
  world matrix so the result is independent of parenting.
"""

import os
import math
import bpy
import bmesh
from mathutils import Matrix


# ---------------------------------------------------------------------------
# Coordinate-system constants
# ---------------------------------------------------------------------------

_PBRT_TO_BLENDER = Matrix.Rotation(math.radians(90), 4, 'X')
_CAM_FLIP        = Matrix.Rotation(math.radians(180), 4, 'Y')


# ---------------------------------------------------------------------------
# Matrix conversion
# ---------------------------------------------------------------------------

def _pbrt_mat_to_blender(m16):
    # m16 is stored column-major (as built by pbrt_parser matrix helpers).
    # Mathutils Matrix([[row0],[row1],...]) is row-major, so we transpose:
    # element at blender row r, col c  =  m16[r + c*4]
    rows = [[m16[row + col*4] for col in range(4)] for row in range(4)]
    return Matrix(rows)


# ---------------------------------------------------------------------------
# Collection / object helpers
# ---------------------------------------------------------------------------

def _ensure_collection(name, parent_col):
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    col = bpy.data.collections.new(name)
    parent_col.children.link(col)
    return col


def _new_object(name, data, collection):
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    return obj


# ---------------------------------------------------------------------------
# Material helpers
# ---------------------------------------------------------------------------

def _get_or_create_material(mat_key, mat_def, textures, base_dir):
    """
    Return a ``bpy.data.material`` for *mat_key*, creating and configuring it
    if it does not yet exist.  Returns ``None`` when *mat_key* is empty.
    """
    if not mat_key:
        return None
    if mat_key in bpy.data.materials:
        return bpy.data.materials[mat_key]

    mat = bpy.data.materials.new(name=mat_key)
    mat.use_nodes = True

    if mat_def is not None:
        import pbrt_materials as pm
        pm.apply_material(mat, mat_def, textures, base_dir)

    return mat


def _assign_material(obj, mat_key, mat_def, textures, base_dir):
    """Assign material to *obj*'s first material slot."""
    if obj is None or obj.type != 'MESH':
        return
    mat = _get_or_create_material(mat_key, mat_def, textures, base_dir)
    if mat is None:
        return
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


# ---------------------------------------------------------------------------
# Mesh builders
# ---------------------------------------------------------------------------

def _build_trianglemesh(params, name):
    pts = params.get('P', [])
    idx = params.get('indices', [])
    if len(pts) < 3 or len(idx) < 3:
        return None
    verts = [(pts[i], pts[i+1], pts[i+2]) for i in range(0, len(pts), 3)]
    faces = [(int(idx[i]), int(idx[i+1]), int(idx[i+2])) for i in range(0, len(idx), 3)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()

    # UV coordinates — pbrt uses "uv" or "st", stored as flat [u0,v0, u1,v1, ...]
    # one pair per vertex (indexed by the same indices array).
    raw_uv = params.get('uv', params.get('st', []))
    if len(raw_uv) >= 2:
        uv_layer = me.uv_layers.new(name='UVMap')
        loop_uvs = uv_layer.data
        for loop in me.loops:
            vi = loop.vertex_index
            u = raw_uv[vi * 2]     if vi * 2     < len(raw_uv) else 0.0
            v = raw_uv[vi * 2 + 1] if vi * 2 + 1 < len(raw_uv) else 0.0
            loop_uvs[loop.index].uv = (u, v)

    return me


def _build_bilinearmesh(params, name):
    """
    pbrt bilinearmesh: 4 vertices per patch, arranged as a 2×2 grid.
    Vertex order within each patch: [00, 10, 01, 11]  (u-major, then v).
    We tessellate each patch into 2 triangles.

    UV: pbrt supplies one (u,v) pair per unique vertex.  When absent we
    generate a simple [0,0 1,0 0,1 1,1] per patch.
    """
    pts = params.get('P', [])
    if len(pts) < 12:   # need at least one patch (4 verts × 3 floats)
        return None

    n_patches = len(pts) // 12
    raw_uv    = params.get('uv', params.get('st', []))
    raw_idx   = params.get('indices', [])  # optional per-patch vertex indices

    verts = [(pts[i], pts[i+1], pts[i+2]) for i in range(0, len(pts), 3)]
    faces = []
    uv_per_vert = []  # parallel to verts

    if raw_uv:
        uv_per_vert = [(raw_uv[i], raw_uv[i+1]) for i in range(0, len(raw_uv), 2)]
    else:
        # Default UV: replicate [0,0 1,0 0,1 1,1] for every 4 vertices
        default_patch_uv = [(0.0,0.0),(1.0,0.0),(0.0,1.0),(1.0,1.0)]
        uv_per_vert = default_patch_uv * (len(verts) // 4 + 1)

    if raw_idx:
        # Explicit patch indices: 4 indices per patch
        for p in range(n_patches):
            i00, i10, i01, i11 = (int(raw_idx[p*4+k]) for k in range(4))
            faces.append((i00, i10, i11))
            faces.append((i00, i11, i01))
    else:
        # Implicit: vertices are laid out 4 per patch in order
        for p in range(n_patches):
            base = p * 4
            i00, i10, i01, i11 = base, base+1, base+2, base+3
            faces.append((i00, i10, i11))
            faces.append((i00, i11, i01))

    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()

    if uv_per_vert:
        uv_layer = me.uv_layers.new(name='UVMap')
        loop_uvs = uv_layer.data
        for loop in me.loops:
            vi = loop.vertex_index
            if vi < len(uv_per_vert):
                loop_uvs[loop.index].uv = uv_per_vert[vi]

    return me


def _build_sphere(params, name):
    radius = params.get('radius', [1.0])[0]
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=radius)
    bm.to_mesh(me); bm.free(); me.update()
    return me


def _build_disk(params, name):
    radius = params.get('radius', [1.0])[0]
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=True, cap_tris=False,
                             segments=32, radius=radius)
    bm.to_mesh(me); bm.free(); me.update()
    return me


def _import_plymesh(params, name, base_dir, collection):
    fn_list = params.get('filename', [])
    if not fn_list:
        return None
    fpath = os.path.normpath(os.path.join(base_dir, fn_list[0]))
    if not os.path.isfile(fpath):
        print(f"[pbrt_builder] PLY not found: {fpath}")
        return None

    bpy.ops.object.select_all(action='DESELECT')
    try:
        bpy.ops.wm.ply_import(filepath=fpath)          # Blender 4.x
    except AttributeError:
        bpy.ops.import_mesh.ply(filepath=fpath)         # Blender 3.x

    imported = bpy.context.selected_objects
    if not imported:
        return None

    obj = imported[0]
    obj.name = name
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    collection.objects.link(obj)
    return obj


# ---------------------------------------------------------------------------
# Shape dispatcher
# ---------------------------------------------------------------------------

def _shape_to_object(shape_node, idx, base_dir, collection, correction,
                     scene_data):
    st       = shape_node.shape_type
    name     = f"{st}_{idx:04d}"
    world    = correction @ _pbrt_mat_to_blender(shape_node.world_matrix)
    mat_key  = shape_node.material_key
    mat_def  = scene_data.materials.get(mat_key)

    def _assign(obj):
        _assign_material(obj, mat_key, mat_def,
                         scene_data.textures, base_dir)

    if st == 'plymesh':
        obj = _import_plymesh(shape_node.params, name, base_dir, collection)
        if obj:
            obj.matrix_world = world
            _assign(obj)
        return obj

    if st in ('trianglemesh', 'loopsubdiv'):
        me = _build_trianglemesh(shape_node.params, name)
        if me is None:
            return None
        obj = _new_object(name, me, collection)
        obj.matrix_world = world
        _assign(obj)
        if st == 'loopsubdiv':
            levels = int(shape_node.params.get('levels', [2])[0])
            mod = obj.modifiers.new('Subdivision', 'SUBSURF')
            mod.levels = mod.render_levels = levels
        return obj

    if st == 'bilinearmesh':
        me = _build_bilinearmesh(shape_node.params, name)
        if me is None:
            return None
        obj = _new_object(name, me, collection)
        obj.matrix_world = world
        _assign(obj)
        return obj

    if st == 'sphere':
        obj = _new_object(name, _build_sphere(shape_node.params, name), collection)
        obj.matrix_world = world
        _assign(obj)
        return obj

    if st == 'disk':
        obj = _new_object(name, _build_disk(shape_node.params, name), collection)
        obj.matrix_world = world
        _assign(obj)
        return obj

    print(f"[pbrt_builder] Unsupported shape type: {st}")
    return None


# ---------------------------------------------------------------------------
# Camera builder
# ---------------------------------------------------------------------------

def _fov_to_angle_y(fov_deg, fov_axis, xres, yres):
    """
    Convert pbrt fov + fovaxis to Blender vertical FOV (radians).

    pbrt's ``fovaxis`` values: ``"x"`` | ``"y"`` | ``"diagonal"`` | ``"smaller"``
    (``"smaller"`` is the default for non-square renders in pbrt-v4).
    """
    fov = math.radians(fov_deg)
    aspect = xres / yres if yres else 1.0

    if fov_axis == 'x':
        return 2 * math.atan(math.tan(fov / 2) / aspect)
    if fov_axis == 'y':
        return fov
    if fov_axis == 'diagonal':
        diag = math.sqrt(1 + aspect**2)
        return 2 * math.atan(math.tan(fov / 2) / diag)
    # 'smaller': the fov applies to whichever axis is smaller
    if xres <= yres:
        return 2 * math.atan(math.tan(fov / 2) / aspect)
    return fov


def _build_camera(cam_data, import_name, root_col, correction):
    xres = cam_data['xresolution']
    yres = cam_data['yresolution']

    cam_name = import_name + "_camera"
    if cam_name in bpy.data.cameras:
        bpy.data.cameras.remove(bpy.data.cameras[cam_name])

    cam = bpy.data.cameras.new(cam_name)
    cam.type      = 'PERSP'
    cam.lens_unit = 'FOV'
    cam.angle     = _fov_to_angle_y(cam_data['fov'], cam_data['fov_axis'], xres, yres)

    if cam_data['lensradius'] > 0:
        cam.dof.use_dof        = True
        # Scale focus distance by the same factor as scene geometry
        scale = correction.to_scale()[0]   # uniform scale — all axes equal
        cam.dof.focus_distance = cam_data['focaldistance'] * scale

    obj = _new_object(cam_name, cam, root_col)
    # CTM is world-to-camera (pbrt convention).  Invert to get camera-to-world,
    # apply coord-system correction (Y-up → Z-up + scale), then flip the
    # camera forward axis (pbrt +Z → Blender -Z).
    w2c = _pbrt_mat_to_blender(cam_data['ctm'])
    c2w = w2c.inverted()
    obj.matrix_world = correction @ c2w @ _CAM_FLIP

    scene = bpy.context.scene
    scene.render.resolution_x = xres
    scene.render.resolution_y = yres
    scene.camera = obj

    print(f"[pbrt_builder] Camera: fov={cam_data['fov']}° ({cam_data['fov_axis']}), {xres}×{yres}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_scene(scene_data, base_dir, import_name="pbrt_import", global_scale=1.0):
    """
    Build Blender objects from *scene_data*.

    Parameters
    ----------
    scene_data   : pbrt_parser.SceneData
    base_dir     : str   — directory of the top-level .pbrt file
    import_name  : str   — name prefix for all created data-blocks
    global_scale : float — uniform scale applied to every world matrix
                           (e.g. 0.01 for cm→m, 0.001 for mm→m)
    """
    # Pre-compose: coordinate-system flip + uniform scale
    # Applied as:  blender_world = correction @ pbrt_world
    correction = _PBRT_TO_BLENDER @ Matrix.Scale(global_scale, 4)

    root_col = _ensure_collection(import_name, bpy.context.scene.collection)

    # ObjectBegin prototypes → hidden collections
    defs_col = _ensure_collection(import_name + "_defs", root_col)
    defs_col.hide_viewport = True
    defs_col.hide_render   = True

    obj_collections = {}
    for obj_name, obj_def in scene_data.objects.items():
        ocol = _ensure_collection(obj_name.replace(' ', '_'), defs_col)
        for i, shape in enumerate(obj_def.shapes):
            _shape_to_object(shape, i, base_dir, ocol, correction, scene_data)
        obj_collections[obj_name] = ocol

    # Top-level shapes
    shapes_col = _ensure_collection(import_name + "_shapes", root_col)
    for i, shape in enumerate(scene_data.shapes):
        _shape_to_object(shape, i, base_dir, shapes_col, correction, scene_data)

    # ObjectInstance → collection-instance empties
    inst_col = _ensure_collection(import_name + "_instances", root_col)
    for i, inst in enumerate(scene_data.instances):
        ocol = obj_collections.get(inst.object_name)
        if ocol is None:
            print(f"[pbrt_builder] ObjectInstance '{inst.object_name}' not defined — skipped")
            continue
        empty = bpy.data.objects.new(
            f"inst_{inst.object_name.replace(' ', '_')}_{i:04d}", None)
        empty.empty_display_type  = 'PLAIN_AXES'
        empty.instance_type       = 'COLLECTION'
        empty.instance_collection = ocol
        empty.matrix_world        = correction @ _pbrt_mat_to_blender(inst.world_matrix)
        inst_col.objects.link(empty)

    # Camera
    if scene_data.camera:
        _build_camera(scene_data.camera, import_name, root_col, correction)

    print(f"[pbrt_builder] Done — "
          f"{len(scene_data.shapes)} shapes, "
          f"{len(scene_data.objects)} object defs, "
          f"{len(scene_data.instances)} instances, "
          f"scale={global_scale}.")
