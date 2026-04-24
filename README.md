pbrt-v4-Blender-Importer
===
pbrt v4 scene importer for Blender.

Supports geometry, instancing, materials and camera from [pbrt-v4](https://github.com/mmp/pbrt-v4) scene files.

Tested against the [pbrt-v4-scenes](https://github.com/mmp/pbrt-v4-scenes) collection (killeroos, transparent-machines).

Features
---
**Geometry**
- `plymesh` — external PLY files (Blender 3.x and 4.x importers both supported)
- `trianglemesh` — inline vertex / index data
- `loopsubdiv` — inline base mesh + Subdivision Surface modifier
- `sphere`, `disk` — parametric primitives

**Transforms**
- `Translate`, `Rotate`, `Scale`, `Transform`, `ConcatTransform`, `LookAt`, `Identity`
- Full `AttributeBegin` / `AttributeEnd` push–pop stack
- `Include` (recursive, relative paths)

**Instancing**
- `ObjectBegin` / `ObjectEnd` → hidden Blender collections
- `ObjectInstance` → Empty with `instance_type = 'COLLECTION'`

**Materials**
- `Material`, `MakeNamedMaterial`, `NamedMaterial` — material *names* are preserved
- Each unique name becomes one Blender material with a default Principled BSDF, ready for manual editing

**Camera**
- `perspective` camera with FOV, optional depth-of-field
- `fovaxis` (`x` / `y` / `diagonal` / `smaller`) correctly converted to Blender vertical FOV
- Film resolution applied to `scene.render.resolution_x/y`
- Camera set as the active scene camera

**Coordinate system**
- pbrt (right-hand, Y-up) → Blender (right-hand, Z-up) via a root empty with a 90° X-rotation

Installation
---
Download as ZIP then install via **Edit → Preferences → Add-ons → Install**.

Blender 3.6 and 4.x are supported.

Usage
---
**File → Import → pbrt v4 Scene (.pbrt)**

All geometry paths (PLY files, included sub-scenes) are resolved relative to the selected `.pbrt` file.
