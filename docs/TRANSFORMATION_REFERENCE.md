# Transformation Pipeline Reference

## Complete Data Flow

PBRT File Input
    |
    v
pbrt_parser.py: Tokenize and Parse
    - Builds transform stack (ctm_stack)
    - Translate / Scale / Rotate / Transform / ConcatTransform
    - Creates ShapeNode with world_matrix
    - Creates InstanceNode with world_matrix
    |
    v
blender_builder.py: Scene Building
    - Creates correction matrix = Rotation(90,X) @ Scale(global_scale,4)
    - For each ShapeNode: world = correction @ pbrt_mat
    - For each InstanceNode: empty.matrix_world = correction @ pbrt_mat
    - For Camera: apply correction AND _CAM_FLIP
    |
    v
Blender Scene with Objects

---

## Matrix Composition Order

For Objects and Instances:
  Blender matrix = _PBRT_TO_BLENDER @ Matrix.Scale(global_scale,4) @ pbrt_world_matrix

For Camera (Special):
  Camera matrix = _PBRT_TO_BLENDER @ inverse(world_to_camera) @ _CAM_FLIP

---

## Example 1: Single Sphere at Origin

PBRT:
  WorldBegin
  Shape "sphere" "float radius" [2]
  WorldEnd

Parser: world_matrix = identity

Builder:
  correction = Rotation(90,X) @ Scale(1.0)
  world = correction @ identity
  Result: Sphere at origin in Blender Z-up

---

## Example 2: Mirrored Geometry (THE FLIP!)

PBRT:
  AttributeBegin
    Scale -1 1 1
    Shape "trianglemesh" ...
  AttributeEnd

Parser CTM after Scale(-1,1,1):
  [[-1,0,0,0], [0,1,0,0], [0,0,1,0], [0,0,0,1]]
  Determinant = -1 (REFLECTION!)

Builder:
  world = Rotation(90,X) @ CTM
  Result: Geometry is MIRRORED in Blender

This is EXPECTED and CORRECT!
The negative scale from PBRT is preserved.

---

## Key Insights

1. Column-Major Layout
   - Indices: [0,1,2,3, 4,5,6,7, 8,9,10,11, 12,13,14,15]
   - Translation at [12,13,14]
   - Scale diagonal at [0,5,10]

2. Right Multiplication in Parser
   - ctm = ctm @ transform
   - Most recent transform applied last (outer)

3. Correction Applied Outside
   - Blender = correction @ pbrt
   - Ensures proper coordinate system conversion

4. Determinant Rule
   - det(M) > 0: No reflection
   - det(M) < 0: Reflection (flipped!)
   - det(Rotation(90,X)) = +1.0
   - det(Scale(-1,1,1)) = -1.0

5. Camera Special Case
   - pbrt camera: looks toward +Z
   - Blender camera: looks toward -Z
   - _CAM_FLIP (180 deg Y) fixes this
   - Only affects camera, NOT geometry

---

## Debugging

Check determinant:
  from mathutils import Matrix
  det = matrix.determinant()
  if det < 0:
      print("Geometry is reflected!")

Print matrix:
  print(matrix)

Check for negative scales:
  grep "Scale.*-" *.pbrt

---

## The Bottom Line

THE 90 DEGREE X-ROTATION IS CORRECT!

Do NOT try to "fix" it. It is the correct Y-up to Z-up conversion.

If your scene appears flipped:
1. Check for Scale -X in PBRT files
2. Check PLY coordinate system
3. Verify global_scale is positive

The coordinate conversion is working as designed.
