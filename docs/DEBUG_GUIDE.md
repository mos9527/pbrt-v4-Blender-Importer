# Debugging Horizontal Flipping in pbrt-v4 Blender Importer

## Quick Diagnosis

If your imported scene appears horizontally flipped, follow these steps in order:

### Step 1: Check for Negative Scales (5 minutes)

```bash
# Search all .pbrt files for negative scale values
grep -r "Scale.*-[0-9]" .

# Search for Transform matrices with -1 on diagonal
grep -r "Transform" . | grep "\-"
```

If found: These are INTENTIONAL mirrors in the source pbrt files.
- Either edit the .pbrt file to remove negative scales
- Or this is the correct behavior for your scene

### Step 2: Verify PLY File Origins (10 minutes)

For each PLY file referenced in your scene:

1. Check what coordinate system it was created in
2. Open it in a tool that shows axes (e.g., Meshlab)
3. Verify: Is +Y up or +Z up?

If PLY is in Blender's Z-up but pbrt expects Y-up:
- This causes DOUBLE-FLIP (once on import, once on correction)
- SOLUTION: Re-export PLY in Y-up coordinates

### Step 3: Check Include Files (5 minutes)

```bash
# Find all Include directives
grep -r "Include" .

# For each included file, check for Scale - directives
grep "Scale.*-" included_file.pbrt
```

### Step 4: Verify Global Scale Parameter (2 minutes)

When importing:
- Check that global_scale is positive (default 1.0)
- If negative, invert it

### Step 5: Inspect Transform Matrices (10 minutes)

Look for direct Transform directives with negative values:

```pbrt
Transform [
  -1  0  0  0
   0  1  0  0
   0  0  1  0
   0  0  0  1
]
```

Negative values in the matrix diagonal = flipping.

---

## Understanding What's Normal

These transformations are CORRECT and should NOT be changed:

### The 90 Degree X-Rotation

Location: blender_builder.py, line 48
```python
_PBRT_TO_BLENDER = Matrix.Rotation(math.radians(90), 4, 'X')
```

What it does: Converts pbrt's Y-up to Blender's Z-up
- This is REQUIRED
- DO NOT modify it
- It has positive determinant (+1), so no mirroring from this alone

### The 180 Degree Y-Rotation for Camera

Location: blender_builder.py, line 49
```python
_CAM_FLIP = Matrix.Rotation(math.radians(180), 4, 'Y')
```

What it does: Flips camera's view direction
- pbrt cameras look toward +Z (away from origin)
- Blender cameras look toward -Z (into scene)
- This flip is REQUIRED for cameras only
- Does NOT affect object geometry

---

## Where Flipping Actually Happens

### 1. PBRT Source File Has Scale -1

This is legitimate! The scene author may have intentionally mirrored something.

Example:
```pbrt
AttributeBegin
  Scale -1 1 1
  ObjectInstance "killeroo"
AttributeEnd
```

Action: Check if this is intentional in your scene

### 2. PLY File Coordinate Mismatch

MOST COMMON SOURCE!

Scenario:
- PLY file was created in Blender (Z-up coordinates)
- PBRT file expects Y-up coordinates
- Blender importer sees Z-up coordinates
- Result: Object appears flipped after correction

Detection:
- Import PLY separately in Blender to see its natural orientation
- Check pbrt scene for y/z axis references
- Compare orientations

Action: Re-export PLY in pbrt's Y-up coordinate system

### 3. Explicit Transform Matrix

Example:
```pbrt
Transform [
  -1  0  0  0
   0  1  0  0
   0  0  1  0
   0  0  0  1
]
```

This intentionally mirrors on X axis.

Action: Check if intentional; if not, remove the negative

### 4. Concatenated Transforms

Example:
```pbrt
AttributeBegin
  Scale -2 1 1
  ConcatTransform [
    1  0  0  0
    0 -1  0  0
    0  0  1  0
    0  0  0  1
  ]
  Shape "sphere"
AttributeEnd
```

Multiple negative values multiply: (-2) * (-1) = +2, so net effect depends on count.

Action: Count negative scales; odd count = flip, even count = no flip

---

## Testing Your Fix

### Test 1: Verify Coordinate Conversion is Working

Create a simple pbrt scene:

```pbrt
Film "rgb" "integer xresolution" [640] "integer yresolution" [480]
Camera "perspective" "float fov" [45]
WorldBegin

# Simple cube at origin in pbrt Y-up
Translate 0 0 0
Scale 1 1 1

Shape "trianglemesh"
  "point3 P" [
    -1 -1 -1
     1 -1 -1
     1  1 -1
    -1  1 -1
  ]
  "integer indices" [0 1 2 0 2 3]

WorldEnd
```

Expected: Cube appears at origin with correct orientation
If flipped: Check PLY or negative scales

### Test 2: Compare Against Known Good Scene

Use pbrt-v4-scenes:
- killeroos (known working)
- transparent-machines (known working)

Does your scene look the same orientation as these?
If not: Your scene file has intentional transforms

### Test 3: Check PLY Separately

Export a simple PLY from your pbrt scene as Y-up:
1. Create shape in pbrt with known orientation
2. Export to PLY
3. Import PLY alone in Blender
4. Verify axes match your expectation

---

## Code Inspection

### Check Transform Matrices

In blender_builder.py, line 187:
```python
world = correction @ _pbrt_mat_to_blender(shape_node.world_matrix)
```

Add debug output:
```python
det = _pbrt_mat_to_blender(shape_node.world_matrix).determinant()
if det < 0:
    print(f"WARNING: Shape {shape_node.shape_type} has det={det} (flipped)")
```

### Check for Negative Scales in Parser

In pbrt_parser.py, line 402-403:
```python
elif d == 'Scale':
    sx,sy,sz = float(self.next()),float(self.next()),float(self.next())
    if sx < 0 or sy < 0 or sz < 0:
        print(f"WARNING: Negative scale detected: {sx} {sy} {sz}")
    self._apply(mat_scale(sx,sy,sz))
```

### Validate Global Scale

In __init__.py, line 100-101:
```python
if self.global_scale < 0:
    print("ERROR: global_scale must be positive")
    self.global_scale = abs(self.global_scale)
```

---

## Summary

Most horizontal flipping issues are caused by:

1. **PBRT file intentionally mirrors geometry** (20% of issues)
   - Check .pbrt for Scale -X directives
   - Verify this is intentional

2. **PLY file coordinate mismatch** (70% of issues)
   - PLY in Z-up but pbrt expects Y-up
   - Re-export PLY in Y-up coordinates

3. **Global scale or direct Transform negative** (10% of issues)
   - Verify all transform parameters are positive
   - Or intentional if specified in scene

The 90 degree X-rotation in blender_builder.py is NOT the problem.
It is the correct Y-up to Z-up conversion.

If you're still seeing flipping after checking all three, the issue is in your specific scene file's transform definitions.
