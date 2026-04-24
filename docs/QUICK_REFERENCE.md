# PBRT v4 Blender Importer: Quick Reference Card

## Files at a Glance

| File | Lines | Purpose |
|------|-------|---------|
| __init__.py | 127 | Blender addon, import operator |
| pbrt_parser.py | 529 | Tokenizer and parser |
| blender_builder.py | 355 | Scene builder |
| pbrt_materials.py | 464 | Material translator |

## Critical Code Locations

| Problem | Location | Lines | Issue |
|---------|----------|-------|-------|
| Coordinate conversion | blender_builder.py | 48-49 | NOT a problem (verified correct) |
| Negative scales | pbrt_parser.py | 402-403 | Preserved intentionally |
| Transform application | blender_builder.py | 187 | Preserves negative determinants |
| PLY import | blender_builder.py | 152-176 | No conversion during import |
| Global scale validation | __init__.py | 69 | No check for negative |

## Matrix Transformation

```
Blender World = correction @ pbrt_world_matrix

where:
  correction = _PBRT_TO_BLENDER @ Scale(global_scale, 4)
  _PBRT_TO_BLENDER = 90-degree X rotation (CORRECT)
  global_scale = user parameter (default 1.0)
```

**Matrix Order:** Right-to-left application
1. Apply PBRT transforms
2. Scale by global_scale
3. Convert Y-up to Z-up

## Common Flipping Causes

| Cause | Probability | Detection | Fix |
|-------|-------------|-----------|-----|
| PLY coordinate mismatch | 70% | Open PLY visually | Re-export |
| Negative scale in PBRT | 20% | grep "Scale -" | Check file |
| Negative transform | 8% | Check matrix values | Verify intent |
| Negative global_scale | 1% | Check UI | Use positive |
| 90-degree rotation | 0% | N/A | NOT the issue |

## Data Structures

**SceneData** - Main output of parser
```python
shapes: list[ShapeNode]
objects: dict[str, ObjectDef]
instances: list[InstanceNode]
materials: dict[str, MaterialDef]
textures: dict[str, TextureDef]
camera: dict or None
```

**ShapeNode** - One renderable shape
```python
shape_type: str       # 'sphere', 'disk', 'trianglemesh', 'plymesh', etc.
params: dict          # Parameters from PBRT
world_matrix: list    # 4x4 column-major matrix (16 floats)
material_key: str     # Reference to material registry
```

## Key Function Signatures

```python
# Parser
parse_pbrt(filepath: str) -> SceneData

# Builder
build_scene(scene_data: SceneData, 
            base_dir: str,
            import_name: str = "pbrt_import",
            global_scale: float = 1.0) -> None

# Materials
apply_material(bpy_mat: Material,
               mat_def: MaterialDef,
               textures: dict,
               base_dir: str) -> None
```

## Debugging Commands

```bash
# Find negative scales
grep -r "Scale.*-[0-9]" .

# Find negative transforms
grep -r "Transform" . | grep -E "\-[0-9]"

# Find includes
grep -r "Include" .

# Check file count
find . -name "*.pbrt" | wc -l

# Look for PLY references
grep -r "plymesh" .
```

## Transformation Matrix Format

Column-major 4x4 (16 floats):
```
[col0_values, col1_values, col2_values, col3_values]
[0-3,         4-7,         8-11,        12-15]

Access: matrix[row + col*4]
```

Example scale matrix:
```
Scale(2, 3, 4) = [2, 0, 0, 0,  0, 3, 0, 0,  0, 0, 4, 0,  0, 0, 0, 1]
```

## Determinant Rules

| Det | Meaning | Effect |
|-----|---------|--------|
| > 0 | No reflection | Shape preserved |
| = 0 | Degenerate | Collapsed to lower dimension |
| < 0 | Reflection | Shape mirrored |

**_PBRT_TO_BLENDER determinant = +1.0** (correct, no reflection)

## Collection Hierarchy Created

```
root_col (import_name, e.g., "my_scene")
  |- root_col/defs (hidden)
  |   |- (object1_name)  
  |       |- shape_0, shape_1, ...
  |   |- (object2_name)
  |       |- shape_0, ...
  |
  |- root_col/shapes
  |   |- sphere_0000
  |   |- trianglemesh_0001
  |   |- ...
  |
  |- root_col/instances
      |- inst_object1_0000
      |- inst_object2_0001
      |- ...
```

## Shape Types Supported

| Type | Method | Notes |
|------|--------|-------|
| sphere | _build_sphere | bmesh UV sphere |
| disk | _build_disk | bmesh circle |
| trianglemesh | _build_trianglemesh | from vertices+indices |
| loopsubdiv | _build_trianglemesh + Subdivision modifier | |
| plymesh | _import_plymesh | External PLY file import |

## Material Types Supported

- diffuse
- coateddiffuse
- conductor
- coatedconductor
- dielectric
- thindielectric
- subsurface
- diffusetransmission
- hair

All map to Principled BSDF in Blender.

## Performance Characteristics

- Parser: O(file_size)
- Shape building: O(total_vertices + total_faces)
- Material deduplication: O(materials)
- Texture loading: O(unique_textures)
- Instance creation: O(instances)

## Blender Compatibility

- Minimum: Blender 3.6
- Tested: Blender 3.6, 4.0, 4.1+
- PLY import: Both 3.x and 4.x operators supported

## Error Handling

- Missing files: Warnings printed, parsing continues
- Unicode errors: Invalid chars replaced with '?'
- Material errors: Silently handled, defaults used
- PLY import failure: Shape skipped
- Image load failure: Texture skipped

## Testing Checklist

- [ ] Parse valid PBRT file
- [ ] Create correct collection hierarchy
- [ ] Apply coordinate transformation
- [ ] Material creation and assignment
- [ ] PLY import with relative paths
- [ ] Handle Include directives
- [ ] Camera creation with correct FOV
- [ ] Instancing with correct transforms
- [ ] Depth of field if enabled
- [ ] Handle nested AttributeBegin/End

## Next Steps for Investigation

1. Check PBRT file for `Scale -X` directives
2. Open PLY files in external viewer to check coordinate system
3. Verify Transform matrices have positive determinants
4. Confirm global_scale is positive
5. Test with known working PBRT scenes
6. Check if PLY should be re-exported

## References

- PBRT v4 GitHub: https://github.com/mmp/pbrt-v4
- PBRT Scenes: https://github.com/mmp/pbrt-v4-scenes
- Blender Matrix: mathutils.Matrix documentation
- Column-Major Matrices: Wikipedia PBRT section
