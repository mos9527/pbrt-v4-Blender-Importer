# pbrt-v4 Blender Importer: Horizontal Flipping Analysis

## Executive Summary

This codebase implements a PBRT v4 scene importer for Blender that handles:
- Scene parsing (geometry, materials, transforms, cameras)
- Coordinate system conversion (pbrt Y-up to Blender Z-up)
- Multiple geometry types (PLY, trianglemesh, loopsubdiv, sphere, disk)

CRITICAL FINDING: The coordinate system conversion applies a 90 degree rotation around the X-axis to every imported object.

---

## Part 1: Scene Import Pipeline

The import operator (`__init__.py`) takes a .pbrt file and performs:
1. Parse the PBRT file into a SceneData structure
2. Build Blender objects from the parsed data

Key parameter: `global_scale` (default 1.0) - uniform scale for entire scene.

---

## Part 2: PBRT File Parsing (pbrt_parser.py)

### 2.1 Matrix Math System (Column-Major)

The parser uses 4x4 matrices in column-major layout:
- `mat_scale(sx, sy, sz)` - scaling
- WARNING: NEGATIVE VALUES = MIRRORING/FLIPPING!
  - `Scale -1 1 1` = horizontal flip
  - `Scale 1 -1 1` = vertical flip
  - `Scale 1 1 -1` = depth flip

### 2.2 Transform Stack

Parser maintains:
- `ctm_stack` - Current Transform Matrix stack
- `attr_stack` - AttributeBegin/End scope stack

Transform operations: Translate, Scale, Rotate, Transform, ConcatTransform, Identity

### 2.3 The LookAt Matrix

`mat_lookat()` returns **world-to-camera** matrix (not camera-to-world).
This is inverted later in the builder.

---

## Part 3: CRITICAL Coordinate System Conversion

### 3.1 The Conversion Matrix

In `blender_builder.py` lines 48-49:

```python
_PBRT_TO_BLENDER = Matrix.Rotation(math.radians(90), 4, 'X')
_CAM_FLIP        = Matrix.Rotation(math.radians(180), 4, 'Y')
```

Coordinate systems:
- pbrt:    X-right, Y-up,    Z-forward
- Blender: X-right, Z-up,    Y-forward

Conversion: Rotate +90 degrees around X-axis
Effect: (x, y, z) in pbrt -> (x, -z, y) in Blender

Key property: Determinant = +1 (NO reflection/mirroring from this conversion)

### 3.2 How Correction is Applied

In `build_scene()`:
```python
correction = _PBRT_TO_BLENDER @ Matrix.Scale(global_scale, 4)

# For each shape:
world = correction @ _pbrt_mat_to_blender(shape_node.world_matrix)
obj.matrix_world = world
```

Order (right-to-left): pbrt matrix -> coord conversion -> global scale

### 3.3 All Transformation Locations

```python
# Shapes (line 187)
world = correction @ _pbrt_mat_to_blender(shape_node.world_matrix)

# Instances (line 344)
empty.matrix_world = correction @ _pbrt_mat_to_blender(inst.world_matrix)

# Camera (line 283) - SPECIAL
w2c = _pbrt_mat_to_blender(cam_data['ctm'])
c2w = w2c.inverted()
obj.matrix_world = correction @ c2w @ _CAM_FLIP
```

Camera needs _CAM_FLIP because:
- pbrt camera looks down +Z
- Blender camera looks down -Z

---

## Part 4: Possible Sources of Horizontal Flipping

### 4.1 Negative Scale in PBRT File (MOST LIKELY)

Example:
  Scale -1 1 1
  Shape "trianglemesh" "point3 P" [...]

This flips geometry left-right. Detection:
  grep -n "Scale.*-[0-9]" scene.pbrt

### 4.2 PLY File Coordinate System Mismatch (CRITICAL!)

PLY files imported via Blender's native importer:
```python
bpy.ops.wm.ply_import(filepath=fpath)  # No coord conversion!
obj.matrix_world = world
```

If PLY was generated in Blender's Z-up but pbrt expects Y-up -> double-flip bug!

### 4.3 Transform Matrix with Negative Determinant

Any matrix with negative scale values has det < 0 = reflection.
The 90 degree X-rotation has det = +1, so it's not the issue.

### 4.4 Global Scale Parameter

Currently doesn't prevent negative values. Could cause flipping.

---

## Part 5: Detailed Examples

### Example 1: Simple Shape

  PBRT: Scale 2 2 2; Translate 1 0 0; Shape...

  Parser CTM:
  [2 0 0 0]
  [0 2 0 0]
  [0 0 2 0]
  [1 0 0 1]

  Builder: world = correction @ CTM
  Result: Object at (1, -0, 2) with scale (2,2,2) in Z-up

### Example 2: Mirrored Geometry

  PBRT: Scale -1 2 2; Shape...

  Parser matrix:
  [-1 0 0 0]
  [0  2 0 0]
  [0  0 2 0]
  [0  0 0 1]

  Builder preserves the -1!
  Result: Geometry is mirrored

---

## Part 6: Geometry File Formats

Supported types:
- plymesh - External PLY files
- trianglemesh - Inline vertex/index data
- loopsubdiv - Mesh + Subdivision modifier
- sphere - UV sphere via bmesh
- disk - Filled circle via bmesh

PLY Import is the critical path:
No coordinate conversion is applied during import!

---

## Part 7: Camera Transformations

`_fov_to_angle_y()` converts pbrt FOV to Blender's vertical FOV.
Supports: x, y, diagonal, smaller

No flipping applied - just angular conversion.

---

## Debugging Checklist

### Where to Look

1. In the .pbrt file:
   grep -n "Scale.*-" scene.pbrt
   grep -n "Transform" scene.pbrt

2. In PLY files:
   - What coordinate system? (Y-up or Z-up?)
   - Does it match pbrt's expectation?

3. In Include files:
   - Check for Scale -1 directives

4. In ObjectInstance transforms:
   - Check for negative scale

5. Global scale parameter:
   - Could be negative

---

## Top Causes of Horizontal Flipping

Cause | Location | Severity | Solution
Scale -1 y z in pbrt | pbrt_parser.py:402 | CRITICAL | Fix source file
PLY coord system mismatch | blender_builder.py:163 | CRITICAL | Verify PLY origin
Negative Transform matrix | pbrt_parser.py:410 | CRITICAL | Check transforms
Negative global_scale | __init__.py:58 | HIGH | Clamp to positive
Include with Scale -1 | pbrt_parser.py:493 | HIGH | Recursively check
Instance with negative scale | pbrt_parser.py:391 | HIGH | Inspect matrices

---

## Key Verification

1. The 90 degree X-rotation is CORRECT for Y-up to Z-up
   - Determinant = +1 (no reflection)
   - Handedness preserved
   - This is NOT the problem

2. The problem is likely:
   - Negative scale in pbrt file, OR
   - PLY file coordinate system mismatch

3. Test:
   grep "Scale -" *.pbrt
   
   Verify PLY coordinate system by:
   Export simple PLY from Blender and check axis directions

---

## Code Recommendations

1. Add negative scale detection in parser
2. Add global_scale validation (must be positive)
3. Add determinant checking in builder
4. Document PLY coordinate system requirements
5. Consider adding PLY coordinate conversion option

---

## VISUAL GUIDE: Coordinate System Transformation

### Before Transformation (PBRT Y-up)

```
      Y (up)
      |
      |  Z (forward)
      | /
------+------ X (right)
```

### After Transformation (Blender Z-up)

```
      Z (up)
      |
      |  Y (forward)
      | /
------+------ X (right)
```

### Transformation Matrix (90 deg rotation around X)

```
[1  0  0  0]       In matrix form
[0  0 -1  0]       Y becomes -Z
[0  1  0  0]       Z becomes Y
[0  0  0  1]
```

This is the CORRECT transformation for Y-up to Z-up!
Determinant = +1, so no mirroring from this operation alone.

---

## QUICK REFERENCE: Critical Code Locations

File: blender_builder.py

Line 48-49: Coordinate conversion matrices
  _PBRT_TO_BLENDER = Matrix.Rotation(math.radians(90), 4, 'X')
  _CAM_FLIP = Matrix.Rotation(math.radians(180), 4, 'Y')

Line 311: Apply correction to all objects
  correction = _PBRT_TO_BLENDER @ Matrix.Scale(global_scale, 4)

Line 187: Shape transformation
  world = correction @ _pbrt_mat_to_blender(shape_node.world_matrix)

Line 344: Instance transformation
  empty.matrix_world = correction @ _pbrt_mat_to_blender(inst.world_matrix)

Line 283: Camera transformation (special - includes _CAM_FLIP)
  obj.matrix_world = correction @ c2w @ _CAM_FLIP

---

File: pbrt_parser.py

Line 40-44: Scaling function (WATCH FOR NEGATIVE VALUES!)
  def mat_scale(sx, sy, sz):
      m = mat_identity(); m[0]=sx; m[5]=sy; m[10]=sz; return m

Line 402-403: Scale directive parsing
  elif d == 'Scale':
      sx,sy,sz = float(self.next()),float(self.next()),float(self.next())
      self._apply(mat_scale(sx,sy,sz))

Line 410-411: Direct matrix transformation
  elif d == 'Transform':
      self.ctm_stack[-1] = _parse_floats(self.next())

Line 413-414: Concatenate matrix
  elif d == 'ConcatTransform':
      self._apply(_parse_floats(self.next()))

---

## SUMMARY FOR DEBUGGING

1. The 90 degree X-rotation in blender_builder.py is CORRECT
   - Do NOT try to "fix" it
   - It is necessary for Y-up to Z-up conversion

2. Check PBRT files for Scale with negative values
   - These are intentional mirrors in the source
   - Either fix the source or accept the flip

3. Check PLY files for coordinate system mismatch
   - This is the MOST LIKELY source of unexpected flipping
   - Blender's PLY importer may assume Z-up
   - PBRT's Y-up expectation + Blender's Z-up import = double-flip!

4. Verify global_scale is positive
   - Current code doesn't prevent negative values
   - Could cause unintended flipping

---

## RELATED COMMITS

From git history:
- 779130f: Fix camera alignment: LookAt now returns world-to-camera
- 0aa1299: Add global_scale import option; fix Y-up→Z-up coordinate conversion
- c4e917d: Initial release: pbrt v4 geometry/camera importer for Blender

The coordinate conversion was introduced in commit 0aa1299 and is intentional.

---

End of Analysis
