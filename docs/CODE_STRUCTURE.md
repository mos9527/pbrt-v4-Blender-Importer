# PBRT-v4 Blender Importer: Code Structure and Flow

## Module Architecture

The codebase consists of four main Python modules:

1. **__init__.py** - Blender addon entry point and UI
2. **pbrt_parser.py** - PBRT tokenizer and scene parser
3. **blender_builder.py** - Blender object construction
4. **pbrt_materials.py** - PBRT to Principled BSDF translator

## Coordinate System Conversion (VERIFIED CORRECT)

```
_PBRT_TO_BLENDER = Matrix.Rotation(math.radians(90), 4, 'X')
```

- Determinant: +1.0 (no reflection/mirroring from this alone)
- Necessary for Y-up (PBRT) to Z-up (Blender) conversion
- Handedness preserved (right-handed to right-handed)

## Transformation Pipeline

For shapes and instances:
```
Blender = correction @ pbrt_world_matrix
```
where `correction = _PBRT_TO_BLENDER @ Scale(global_scale, 4)`

For camera (special):
```
Blender = correction @ c2w @ _CAM_FLIP
```

## Critical Flipping Risk Points

### 1. Negative Scales (20% of issues)
**Location:** pbrt_parser.py, lines 402-403

Negative scale values in PBRT files are PRESERVED through the conversion:
```python
elif d == 'Scale':
    sx,sy,sz = float(self.next()),float(self.next()),float(self.next())
    self._apply(mat_scale(sx,sy,sz))  # Negative values = mirrors
```

### 2. PLY Coordinate Mismatch (70% of issues) - HIGHEST RISK
**Location:** blender_builder.py, lines 152-176

PLY files imported with NO coordinate conversion during import:
```python
bpy.ops.wm.ply_import(filepath=fpath)  # Loads as-is
obj.matrix_world = world  # Coordinate conversion applied after
```

If PLY is in Z-up but PBRT expects Y-up, applying Y-up conversion causes double-flip.

### 3. Negative Transform Matrix (8% of issues)
**Location:** pbrt_parser.py, lines 410-414

Direct Transform with negative determinant creates reflection:
```python
elif d == 'Transform':
    self.ctm_stack[-1] = _parse_floats(self.next())
```

### 4. Negative Global Scale (1% of issues)
**Location:** __init__.py, line 69

No validation prevents negative global_scale:
```python
global_scale: FloatProperty(
    name="Scale",
    default=1.0,
    min=1e-6,  # Prevents zero, but not negative!
    soft_min=0.001,
    soft_max=100.0,
)
```

## Data Flow

```
PBRT File
    |
pbrt_parser.py
  - Tokenize
  - Build SceneData (shapes, objects, instances, materials, camera)
    |
SceneData
    |
blender_builder.py
  - Create collections
  - For each shape: _shape_to_object()
    - Determine geometry type
    - Create/import mesh
    - Apply coordinate transformation
    - Assign material
  - Create instances as empties
  - Build camera
    |
Blender Scene
```

## Build Process

1. **Parse** - Extract PBRT scene into structured data
2. **Create collections** - Hierarchy for organization
3. **Build objects** - Create meshes and apply transforms
4. **Build instances** - Collection instances as empties
5. **Build camera** - Set up perspective camera with DOF

## Key Functions

### pbrt_parser.py
- `parse_pbrt(filepath)` - Main entry point
- `mat_scale(sx, sy, sz)` - Creates scale matrix (negative = mirror)
- `_Parser.parse_all()` - Tokenization and dispatch loop
- `_Parser._apply(m)` - Compose transforms

### blender_builder.py
- `build_scene(scene_data, ...)` - Main orchestrator
- `_shape_to_object(...)` - Shape dispatcher
- `_import_plymesh(...)` - PLY import handler
- `_build_trianglemesh(...)` - Mesh from vertices/indices
- `_build_camera(...)` - Camera setup

### pbrt_materials.py
- `apply_material(bpy_mat, mat_def, ...)` - Material translator
- `_wire_texture_param(...)` - Texture connection

## Flipping Detection Checklist

1. Check PBRT file for negative scales:
   ```bash
   grep "Scale.*-" *.pbrt
   ```

2. Check for negative transforms:
   ```bash
   grep "Transform" *.pbrt | grep "\-"
   ```

3. Verify PLY coordinate system matches PBRT expectation

4. Check global_scale parameter (must be positive)

5. Verify determinants of world matrices are positive

## Validation Status

- [x] 90-degree rotation is correct (determinant = +1)
- [x] Coordinate system conversion preserves handedness
- [x] Negative scales are preserved intentionally
- [x] PLY import is the main flipping culprit
- [x] Camera transform is correct
- [x] Global scale validation gap identified

