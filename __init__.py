"""
pbrt v4 Blender Importer
========================
Imports geometry, instancing, materials and camera from a pbrt v4 scene file.

Installation
------------
Download as ZIP then install via Blender → Edit → Preferences → Add-ons → Install.

Supports Blender 3.6 and 4.x.
"""

bl_info = {
    "name":        "pbrt v4 Importer",
    "author":      "mos9527",
    "version":     (1, 1, 0),
    "blender":     (3, 6, 0),
    "location":    "File > Import > pbrt v4 Scene (.pbrt)",
    "description": "Import geometry, instancing, materials and camera from a pbrt v4 scene file",
    "category":    "Import-Export",
}

import os
import sys
import importlib

import bpy
from bpy.props import StringProperty, BoolProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper

# Ensure sibling modules are importable when loaded as a Blender addon
_addon_dir = os.path.dirname(__file__)
if _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)


def _reload_modules():
    for name in ('pbrt_parser', 'pbrt_materials', 'blender_builder'):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


class IMPORT_OT_pbrt(bpy.types.Operator, ImportHelper):
    """Import a pbrt v4 scene file"""
    bl_idname  = "import_scene.pbrt"
    bl_label   = "Import pbrt v4 Scene"
    bl_options = {'UNDO'}

    filename_ext = ".pbrt"
    filter_glob: StringProperty(default="*.pbrt", options={'HIDDEN'})

    use_filename_as_collection: BoolProperty(
        name="Use filename as collection name",
        description="Name the root collection after the imported file",
        default=True,
    )

    global_scale: FloatProperty(
        name="Scale",
        description=(
            "Uniform scale applied to the entire scene on import. "
            "pbrt has no built-in unit system; use this to match your scene. "
            "Common values: 0.01 (cm → m), 0.001 (mm → m), 1.0 (keep as-is)"
        ),
        default=1.0,
        min=1e-6,
        soft_min=0.001,
        soft_max=100.0,
    )

    def execute(self, context):
        _reload_modules()
        import pbrt_parser    as pp
        import blender_builder as bb

        filepath = self.filepath
        if not os.path.isfile(filepath):
            self.report({'ERROR'}, f"File not found: {filepath}")
            return {'CANCELLED'}

        import_name = (
            os.path.splitext(os.path.basename(filepath))[0]
            if self.use_filename_as_collection
            else "pbrt_import"
        )

        self.report({'INFO'}, f"Parsing {os.path.basename(filepath)} …")
        try:
            scene_data = pp.parse_pbrt(filepath)
        except Exception as e:
            self.report({'ERROR'}, f"Parse error: {e}")
            import traceback; traceback.print_exc()
            return {'CANCELLED'}

        self.report({'INFO'},
            f"Building: {len(scene_data.shapes)} shapes, "
            f"{len(scene_data.objects)} object defs, "
            f"{len(scene_data.instances)} instances …")
        try:
            bb.build_scene(scene_data, os.path.dirname(filepath), import_name,
                           global_scale=self.global_scale)
        except Exception as e:
            self.report({'ERROR'}, f"Build error: {e}")
            import traceback; traceback.print_exc()
            return {'CANCELLED'}

        self.report({'INFO'}, "pbrt import complete.")
        return {'FINISHED'}


def _menu_import(self, context):
    self.layout.operator(IMPORT_OT_pbrt.bl_idname, text="pbrt v4 Scene (.pbrt)")


def register():
    bpy.utils.register_class(IMPORT_OT_pbrt)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.utils.unregister_class(IMPORT_OT_pbrt)


if __name__ == "__main__":
    register()
