"""Cutlist Export (MaxCut) - cutting list exporter for furniture projects."""

import bpy

from . import operators, properties, ui  # noqa: F401  (imported for the classes below)

bl_info = {
    "name": "Cutlist Export (MaxCut)",
    "author": "nedik",
    "version": (1, 0, 0),
    # Verified on 4.0.2 and 5.2.3: the add-on only uses API that exists in 4.0,
    # and 4.2+ additionally reads blender_manifest.toml.
    "blender": (4, 0, 0),
    "location": "3D Viewport > N-panel > Cutlist",
    "description": "Export a MaxCut cutting list from the selected boards",
    "category": "Import-Export",
}


def _register_class_safe(cls):
    """Register a class unless it is already registered (Reload Scripts safe).

    ``cls.is_registered`` is the only reliable check in 5.2.3: ``bl_rna`` exists
    even for unregistered classes, and ``bpy.types`` never holds PropertyGroups.
    """
    if not getattr(cls, "is_registered", False):
        bpy.utils.register_class(cls)


def _unregister_class_safe(cls):
    if getattr(cls, "is_registered", False):
        bpy.utils.unregister_class(cls)


def register():
    properties.register()
    for module in operators, ui:
        for cls in getattr(module, "CLASSES", ()):
            _register_class_safe(cls)
    ui.register_menus()


def unregister():
    ui.unregister_menus()
    for module in (ui, operators):
        for cls in getattr(module, "CLASSES", ()):
            _unregister_class_safe(cls)
    properties.unregister()


if __name__ == "__main__":
    register()
