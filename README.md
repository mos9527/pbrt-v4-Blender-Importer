pbrt-v4-Blender-Importer
===
**ATTENTION:** Mostly genereated by Claude Code. Use at your own discretion.

pbrt v4 scene importer for Blender.

Supports geometry, instancing, materials and camera from [pbrt-v4](https://github.com/mmp/pbrt-v4) scene files.

Tested against a subset of the [pbrt-v4-scenes](https://github.com/mmp/pbrt-v4-scenes) collection (killeroos, transparent-machines, watercolor).

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

All 8 pbrt v4 built-in material types are translated to a Principled BSDF:

| pbrt type | Principled BSDF mapping |
|---|---|
| `diffuse` | Base Color, Roughness = 1 |
| `coateddiffuse` | + Coat Weight / Roughness / IOR |
| `conductor` | Metallic = 1, IOR from preset table or SPD average |
| `coatedconductor` | conductor base + Coat layer |
| `dielectric` | Transmission Weight = 1, IOR |
| `thindielectric` | same as dielectric |
| `subsurface` | Subsurface Weight + Radius |
| `diffusetransmission` | Transmission Weight (approximate) |

Named conductors (`Au`, `Ag`, `Al`, `Cu`, …) and glass grades (`glass-BK7`, …)
are resolved from a built-in IOR table without needing the SPD files.
`imagemap` textures are wired to the appropriate socket via an Image Texture node.

`MakeNamedMaterial` / `NamedMaterial` are fully supported; the material name
is used as the Blender material name so shared materials produce a single
data-block.

**Camera**
- `perspective` camera with FOV, optional depth-of-field
- `fovaxis` (`x` / `y` / `diagonal` / `smaller`) correctly converted to Blender vertical FOV
- Film resolution applied to `scene.render.resolution_x/y`
- Camera set as the active scene camera

**Coordinate system**
- pbrt (right-hand, Y-up) → Blender (right-hand, Z-up), baked into every object matrix

Installation
---
Download as ZIP then install via **Edit → Preferences → Add-ons → Install**.

Blender 3.6 and 4.x are supported.

Usage
---
**File → Import → pbrt v4 Scene (.pbrt)**

All geometry paths (PLY files, included sub-scenes) are resolved relative to the selected `.pbrt` file.
