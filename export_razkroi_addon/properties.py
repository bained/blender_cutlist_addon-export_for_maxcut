"""Property groups: per-scene settings that travel with the .blend file."""

import os

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup, UIList

from .exporter import MM_PER_UNIT

# Any of these is "user works in millimetres", 1.0 is "plain Blender metres".
KNOWN_SCALE_LENGTHS = (1.0, 0.001)
# Tolerance for comparing scale_length values (Blender stores a float).
SCALE_EPSILON = 1e-6

SORT_ITEMS = (
    ("NAME", "Name", "Sort by the first object name"),
    ("LENGTH", "Length", "Longest panels first"),
    ("AREA", "Area", "By row area (L x W x qty)"),
)

# Output file naming, in one place so the panel text and the code cannot drift.
FILE_SUFFIX = "_cutlist.txt"
FALLBACK_FILE_NAME = "cutlist.txt"
FALLBACK_FOLDER = "cutlist"

# Short labels for the panel header. Keys are the identifiers Blender reports in
# ``unit_settings.length_unit`` (measured on 5.2.3, see .dev/probe_units_label.py).
UNIT_LABELS = {
    "ADAPTIVE": "auto",
    "METERS": "m",
    "CENTIMETERS": "cm",
    "MILLIMETERS": "mm",
    "MICROMETERS": "µm",
    "KILOMETERS": "km",
    "FEET": "ft",
    "INCHES": "in",
    "THOU": "thou",
    "MILES": "mi",
}


class ECK_PartItem(PropertyGroup):
    """One cutting-list row, filled by Preview/Export for the UI list."""

    names: StringProperty(name="Objects")
    length: FloatProperty(name="Length", precision=2)
    width: FloatProperty(name="Width", precision=2)
    qty: IntProperty(name="Qty", min=0)
    material: StringProperty(name="Material")

    @property
    def area_m2(self):
        return self.length * self.width * self.qty / 1_000_000.0


def default_output_dir():
    """Folder next to the .blend file, else ~/Documents/cutlist - never a temp dir."""
    blend = bpy.data.filepath
    if blend:
        return os.path.dirname(blend)
    return os.path.join(os.path.expanduser("~"), "Documents", FALLBACK_FOLDER)


def default_file_name():
    """``<blend name>_cutlist.txt``, or just ``cutlist.txt`` for an unsaved scene."""
    blend = bpy.data.filepath
    stem = os.path.splitext(os.path.basename(blend))[0] if blend else ""
    return f"{stem}{FILE_SUFFIX}" if stem else FALLBACK_FILE_NAME


def scene_scale_length(scene=None):
    """What one Blender unit means for this scene (1.0 = metres)."""
    scene = scene or bpy.context.scene
    return scene.unit_settings.scale_length


def is_plain_metre_scale(scale_length):
    """Is this the default ``1 unit = 1 m`` scale?

    Used whenever the panel or an operator has to tell the user "the scale is
    untouched" - the comparison lives here so it cannot drift between callers.
    """
    return abs(float(scale_length) - 1.0) <= SCALE_EPSILON


def scene_uses_millimetres(scene=None):
    scene = scene or bpy.context.scene
    return scene.unit_settings.length_unit == "MILLIMETERS"


class ECK_UL_parts(UIList):
    """Cutting-list rows: name, L x W, quantity, area."""

    bl_idname = "ECK_UL_parts"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname,
                  index=0):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.label(text=item.names[:40], icon="MESH_DATA")
            row.label(text=f"{item.length:g} × {item.width:g}")
            row.label(text=f"×{item.qty}")
            row.label(text=f"{item.area_m2:.2f} m²")
        else:
            layout.label(text=f"{item.length:g}×{item.width:g} ×{item.qty}")


def _refresh_from_ui(self, context):
    """Recompute the list right away when a scope option changes (M5 request).

    Called by Blender on property updates, including on file load and undo, so it
    must never raise: a broken callback would break the whole UI. Import is local
    because operators.py imports this module.
    """
    if context is None or getattr(context, "scene", None) is None:
        return
    try:
        from . import operators

        operators.analyse(context.scene, self)
    except Exception:  # noqa: BLE001 - never break the property update
        import traceback

        traceback.print_exc()


class ECK_Properties(PropertyGroup):
    """Settings for the current scene, saved inside the .blend file."""

    material_name: StringProperty(
        name="Material",
        description="Material name as it must appear in MaxCut (one per export). "
                    "Left empty, the file says 'Unnamed material'",
        default="",
    )
    auto_thickness: BoolProperty(
        name="Thickness from objects",
        description="Use the thinnest side of the selection instead of a typed value",
        default=True,
    )
    material_thick_mm: FloatProperty(
        name="Thickness (mm)",
        description="Nominal thickness; used when auto detection is off",
        default=18.0,
        min=1.0,
        soft_max=60.0,
        step=10,
        precision=1,
    )
    thickness_tolerance_mm: FloatProperty(
        name="Tolerance (mm)",
        description="How far a board may deviate before it counts as a different thickness",
        default=0.5,
        min=0.05,
        soft_max=2.0,
        step=1,
        precision=2,
    )
    output_dir: StringProperty(
        name="Folder",
        description="Where the MaxCut file is written (empty = folder of the .blend file)",
        subtype="DIR_PATH",
        default="",
    )
    file_name: StringProperty(
        name="File name",
        description=f"Output file name (empty = <blend name>{FILE_SUFFIX})",
        default="",
    )
    overwrite: BoolProperty(
        name="Overwrite",
        description="Overwrite an existing file instead of adding _001",
        default=True,
    )
    only_selected: BoolProperty(
        name="Selected only",
        description="Only use selected objects, otherwise every visible object in the scene",
        default=True,
        update=_refresh_from_ui,
    )
    group_identical: BoolProperty(
        name="Group identical parts",
        description="Merge equal sizes into one row with a quantity. Turn off to get "
                    "one row per object, e.g. when the same size needs different edge "
                    "banding in MaxCut",
        default=True,
        update=_refresh_from_ui,
    )
    sort_by: EnumProperty(
        name="Sort",
        description="Row order in the panel and in the file",
        items=SORT_ITEMS,
        default="LENGTH",
    )
    parts: CollectionProperty(type=ECK_PartItem)
    parts_index: IntProperty(default=0)
    # Last preview counters, so the panel can show them without re-scanning.
    last_parts: StringProperty(default="")
    last_area: StringProperty(default="")
    last_thickness: StringProperty(default="")
    last_file: StringProperty(default="")
    last_message: StringProperty(default="")
    last_warnings: StringProperty(default="")
    last_skipped: StringProperty(default="")


def addon_preferences():
    """Return our AddonPreferences, or None when the add-on is not enabled.

    ``AddonPreferences`` never lands in ``bpy.types``; the only handle is
    ``context.preferences.addons[<full module name>]``. Measured on 5.2.3: for the
    zip install the key is ``bl_ext.user_default.export_razkroi_addon`` - the
    *full* package name, not the top-level part, so the lookup must not truncate it.
    """
    package = __package__ or ""
    candidates = [package]
    top = package.split(".")[-1]
    if top not in candidates:
        candidates.append(top)
    addons = bpy.context.preferences.addons
    for name in candidates:
        addon = addons.get(name) if name else None
        if addon is not None and addon.preferences is not None:
            return addon.preferences
    for addon in addons:
        module = (addon.module or "").split(".")[-1]
        if module == top and addon.preferences is not None:
            return addon.preferences
    return None


def output_dir(props=None, scene=None):
    """Resolve the export folder.

    Priority: panel field, folder of the .blend, last used folder from the
    add-on preferences, then ``~/Documents/cutlist``. Never a temp directory and
    never ``scene.render.filepath`` (that one belongs to rendering).
    """
    props = props or (scene or bpy.context.scene).eck_props
    if props.output_dir:
        return bpy.path.abspath(props.output_dir)
    blend = bpy.data.filepath
    if blend:
        return os.path.dirname(blend)
    prefs = addon_preferences()
    if prefs is not None and prefs.last_dir:
        return bpy.path.abspath(prefs.last_dir)
    return default_output_dir()


def output_path(props=None, scene=None):
    """Resolve the full export path."""
    props = props or (scene or bpy.context.scene).eck_props
    name = props.file_name or default_file_name()
    return os.path.join(output_dir(props, scene), name)


SUPPORTED_LENGTH_UNITS = ("MILLIMETERS", "CENTIMETERS", "METERS")


def unit_summary(scene=None):
    """Compact unit state for the panel header, e.g. ``Metric · mm · scale 1``.

    ``length_unit`` is a dynamic enum, so the identifiers are spelled out rather
    than sliced: a two-letter abbreviation happens to be correct today, but a new
    unit would silently produce a wrong label.
    """
    scene = scene or bpy.context.scene
    units = scene.unit_settings
    label = UNIT_LABELS.get(units.length_unit, "bu")
    return f"{units.system.title()} · {label} · scale {units.scale_length:g}"


def unit_problems(scene=None):
    """Warnings about a unit setup that would produce a wrong cutting list."""
    scene = scene or bpy.context.scene
    units = scene.unit_settings
    problems = []
    if units.system != "METRIC":
        problems.append(f"Scene uses {units.system.title()} - MaxCut expects millimetres")
    if not any(abs(units.scale_length - known) < SCALE_EPSILON
               for known in KNOWN_SCALE_LENGTHS):
        problems.append(f"Unusual unit scale {units.scale_length:g}: "
                        f"1 unit = {units.scale_length * MM_PER_UNIT:g} mm")
    return problems


class ECK_Preferences(bpy.types.AddonPreferences):
    """Add-on preferences: remembered folders and panel options."""

    bl_idname = __package__

    last_dir: StringProperty(
        name="Last folder",
        subtype="DIR_PATH",
        default="",
    )
    show_units_banner: BoolProperty(
        name="Show units row",
        description="Show the unit setup at the top of the panel",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "last_dir")
        layout.prop(self, "show_units_banner")


CLASSES = (ECK_PartItem, ECK_UL_parts, ECK_Properties, ECK_Preferences)


def _is_registered(cls):
    """Is this RNA class already registered?

    Measured on 5.2.3: ``cls.bl_rna`` exists even before registration (it points
    at the base struct), and ``bpy.types`` does not hold PropertyGroups at all.
    ``cls.is_registered`` is the only reliable signal: False -> True.
    """
    return bool(getattr(cls, "is_registered", False))


def register():
    # Order matters: the CollectionProperty above needs ECK_PartItem to be a
    # registered RNA type, and scene.eck_props needs ECK_Properties.
    for cls in CLASSES:
        if not _is_registered(cls):
            bpy.utils.register_class(cls)
    bpy.types.Scene.eck_props = PointerProperty(type=ECK_Properties)


def unregister():
    if hasattr(bpy.types.Scene, "eck_props"):
        del bpy.types.Scene.eck_props
    for cls in reversed(CLASSES):
        if _is_registered(cls):
            bpy.utils.unregister_class(cls)


def fill_parts(props, parts):
    """Replace the UI list contents with the given rows."""
    props.parts.clear()
    for part in parts:
        item = props.parts.add()
        item.names = ", ".join(part.names)
        item.length = part.length
        item.width = part.width
        item.qty = part.qty
        item.material = part.material
    props.parts_index = 0