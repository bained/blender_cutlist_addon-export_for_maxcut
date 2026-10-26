"""Operators: preview, export, export-as, open folder, copy, scene units."""

import os

import bpy
import mathutils
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from . import exporter
from .properties import (
    addon_preferences,
    default_file_name,
    fill_parts,
    output_dir,
    scene_scale_length,
    unit_problems,
)

# Runtime report of the last scan, shared by the panel and the operators.
LAST = {"parts": 0, "area": 0.0, "message": "", "warnings": [], "skipped": []}


def iter_objects(scene, only_selected):
    """Yield the mesh objects that belong in the cutting list."""
    if only_selected:
        candidates = list(bpy.context.selected_objects)
    else:
        candidates = list(scene.objects)
    for obj in candidates:
        if obj.type != "MESH":
            continue
        if obj.hide_get() or not obj.visible_get():
            continue
        yield obj


def world_size(obj):
    """World-axis bounding box of the *evaluated* geometry, in Blender units.

    Two traps are handled here, both measured on 5.2.3:

    * ``obj.dimensions`` ignores unapplied modifiers, so the evaluated object is
      used; but ``obj.evaluated_get()`` returns a copy whose ``matrix_world`` is
      not refreshed in ``--background`` (it still reports scale 1), therefore the
      *original* object's matrix is applied to the evaluated bounding box.
    * ``bound_box`` is already in Blender units and ``matrix_world`` is
      unit-agnostic, so nothing is converted here - converting as well would
      double-apply ``scale_length``.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    matrix = obj.matrix_world
    verts = [matrix @ mathutils.Vector(corner) for corner in obj_eval.bound_box]
    mins = [min(v[i] for v in verts) for i in range(3)]
    maxs = [max(v[i] for v in verts) for i in range(3)]
    return tuple(maxs[i] - mins[i] for i in range(3))


def measured_dims_mm(obj, scale_length):
    """Convenience wrapper: world size converted with the scene unit scale."""
    return tuple(exporter.to_mm(size, scale_length) for size in world_size(obj))


def instance_signature(matrix, mesh):
    """Identify copies of the same object *before* measuring.

    Two empties instancing one mesh at different scales are different panels, so
    the mesh data-block alone is not enough: the full world matrix is the key.
    That is exact, unlike comparing rounded dimensions, which merges boards that
    differ by fractions of a millimetre.
    """
    values = tuple(round(value, 6) for row in matrix for value in row)
    return (mesh.name if mesh is not None else "", values)


def collect_panels(scene, props):
    """Measure every relevant object once, skipping instanced duplicates.

    The material is deliberately *not* read from the objects: one export covers
    one material, which the user types in the panel (as in the original script).
    """
    scale_length = scene_scale_length(scene)
    panels, seen, skipped = [], set(), []
    for obj in iter_objects(scene, props.only_selected):
        signature = instance_signature(obj.matrix_world, obj.data)
        if signature in seen:
            continue  # a linked duplicate of an object already in the list
        seen.add(signature)
        dims = measured_dims_mm(obj, scale_length)
        if min(dims) <= 1e-4:
            skipped.append(obj.name)
            continue
        panels.append(exporter.Panel(obj.name, dims))
    return panels, skipped


class ThicknessMismatch(Exception):
    """Raised when the selection mixes boards of different thickness."""

    def __init__(self, expected, outliers):
        self.expected = expected
        self.outliers = outliers
        names = ", ".join(f"{name} ({thickness:g} mm)" for name, thickness in outliers[:6])
        if len(outliers) > 6:
            names += f" ... (+{len(outliers) - 6})"
        super().__init__(
            f"Mixed thickness: expected {expected:g} mm, but {len(outliers)} object(s) "
            f"differ: {names}")


def analyse(scene, props):
    """Run the pipeline. Returns (parts, skipped, warnings, thickness).

    Raises ``ThicknessMismatch`` when the selection is not a single-thickness set,
    so the operator can cancel instead of writing a wrong cutting list.
    """
    panels, skipped = collect_panels(scene, props)
    expected = None if props.auto_thickness else props.material_thick_mm
    thickness, outliers = exporter.thickness_outliers(
        panels, props.thickness_tolerance_mm, expected)
    if outliers:
        fill_parts(props, [])
        raise ThicknessMismatch(thickness, outliers)

    parts, unmeasurable = exporter.group(panels, props.material_name,
                                         props.group_identical)
    skipped.extend(unmeasurable)
    parts = exporter.sort_parts(parts, props.sort_by)
    warnings = unit_problems(scene) + exporter.plausibility_problems(parts)
    fill_parts(props, parts)
    props.last_thickness = f"{thickness:g}" if thickness is not None else ""
    props.last_parts = str(len(parts))
    props.last_area = f"{exporter.total_area_m2(parts):.2f}"
    LAST.update(
        parts=len(parts),
        area=exporter.total_area_m2(parts),
        warnings=warnings,
        skipped=skipped,
        thickness=thickness,
    )
    return parts, skipped, warnings, thickness


def write_file(path, lines):
    """Write the cutting list as UTF-8 with LF newlines."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
        if lines:
            handle.write("\n")


def unique_path(path):
    """Return a non-existing ``_001``/``_002`` variant of ``path``."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for index in range(1, 1000):
        candidate = f"{stem}_{index:03d}{ext}"
        if not os.path.exists(candidate):
            return candidate
    return path


def _store_result(props, parts, skipped, warnings, message):
    """Store the outcome for the panel. analyse() already set rows and counters."""
    props.last_parts = str(len(parts))
    props.last_area = f"{exporter.total_area_m2(parts):.2f}"
    props.last_message = message
    props.last_warnings = "\n".join(warnings)
    props.last_skipped = ", ".join(skipped[:8])
    LAST.update(message=message)

def _report(operator, parts, skipped, warnings, path=None):
    operator.report({"INFO"}, f"Cutlist: {len(parts)} row(s)"
                              + (f" -> {path}" if path else ""))
    if skipped:
        operator.report({"WARNING"}, f"Skipped {len(skipped)}: {', '.join(skipped[:5])}"
                                     + (" ..." if len(skipped) > 5 else ""))
    if warnings:
        operator.report({"WARNING"}, warnings[0])


def run_export(scene, props, filename=""):
    """Do the export. Returns (ok, path_or_message, parts, skipped, warnings)."""
    try:
        parts, skipped, warnings, thickness = analyse(scene, props)
    except ThicknessMismatch as mismatch:
        message = str(mismatch)
        fill_parts(props, [])
        _store_result(props, [], [], [message], "Refused: mixed thickness")
        return False, message, [], [], [message]
    if not parts:
        _store_result(props, parts, skipped, warnings, "Nothing to export")
        return False, "Nothing to export", parts, skipped, warnings

    path = os.path.normpath(resolve_target(props, scene, filename))
    if not props.overwrite and os.path.exists(path):
        path = unique_path(path)
    try:
        write_file(path, exporter.build_lines(parts, ";"))
    except OSError as error:
        _store_result(props, parts, skipped, warnings, f"Error: {error}")
        return False, f"Write failed: {error}", parts, skipped, warnings

    _store_result(props, parts, skipped, warnings, f"Saved: {path}")
    props.last_file = path
    props.last_thickness = f"{thickness:g}"
    prefs = addon_preferences()
    if prefs is not None:
        prefs.last_dir = os.path.dirname(path)
    return True, path, parts, skipped, warnings


def resolve_target(props, scene, selected_path=""):
    """Where to write, given an optional path picked in the Export-As dialog.

    The file browser can hand back a bare name, a name with a relative path, or
    an absolute path (and on some platforms the chosen *directory* lands in the
    name). All of those are normalised to an absolute file path:

    * empty              -> the panel's folder + file name
    * bare name          -> panel folder + that name
    * directory only     -> that directory + the panel's file name
    * absolute path      -> used as is
    """
    chosen = (selected_path or "").strip().strip('"')
    panel_dir = output_dir(props, scene)
    panel_name = props.file_name or default_file_name()

    if not chosen:
        return os.path.join(panel_dir, panel_name)

    chosen = bpy.path.abspath(chosen)
    # A directory the user typed may not exist yet, and on Windows the browser
    # hands back a trailing separator - both must count as a directory.
    looks_like_dir = chosen.endswith(("/", "\\"))
    if os.path.isdir(chosen) or looks_like_dir:
        return os.path.join(chosen, panel_name)
    if os.path.isabs(chosen):
        return chosen
    return os.path.join(panel_dir, chosen)


class ECK_OT_export(Operator):
    """Write the MaxCut cutting list for the selected objects"""

    bl_idname = "eck.export_razkroi"
    bl_label = "Export cutlist"
    bl_options = {"REGISTER"}

    filepath: StringProperty(
        name="File",
        description="Full path; when empty the folder and name from the panel are used",
        subtype="FILE_PATH",
        default="",
    )

    @classmethod
    def poll(cls, context):
        return _export_poll(context)

    def execute(self, context):
        return _do_export(self, context, self.filepath)


class ECK_OT_export_as(Operator):
    """Export with the file browser (Save As)

    Deliberately a *sibling* of ECK_OT_export, not its subclass: on 5.2.3 an
    operator that subclasses another operator makes Blender lose the Python class
    for the base RNA struct ("unable to get Python class for RNA struct"), and
    ``bpy.ops.<base>.poll()`` then returns False, greying out the button.
    """

    bl_idname = "eck.export_razkroi_as"
    bl_label = "Export cutlist as..."
    bl_options = {"REGISTER"}

    # ``directory`` + ``filepath`` + ``check_existing`` is what the file browser
    # needs: without the directory it opens at the last global location and can
    # hand back a name that resolves against the wrong folder.
    directory: StringProperty(subtype="DIR_PATH")
    filepath: StringProperty(
        name="File",
        description="File to write",
        subtype="FILE_PATH",
        default="",
    )
    check_existing: BoolProperty(
        name="Check existing",
        description="Ask before overwriting an existing file",
        default=True,
    )
    filter_glob: StringProperty(default="*.txt;*.csv", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _export_poll(context)

    def invoke(self, context, event):
        props = context.scene.eck_props
        target = resolve_target(props, context.scene, "")
        self.directory = os.path.dirname(target) or "//"
        self.filepath = os.path.basename(target)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        self.layout.label(text="UTF-8, ';' separated", icon="INFO")

    def execute(self, context):
        chosen = os.path.join(self.directory, self.filepath) if self.filepath else ""
        return _do_export(self, context, chosen)


def _export_poll(context):
    """Shared poll for both export buttons."""
    return context.scene is not None and hasattr(context.scene, "eck_props")


def _do_export(operator, context, filename):
    """Shared execute() body for both export buttons."""
    ok, detail, parts, skipped, warnings = run_export(
        context.scene, context.scene.eck_props, filename)
    if not ok:
        extra = f" (skipped: {', '.join(skipped[:5])})" if skipped else ""
        operator.report({"ERROR"}, detail + extra)
        return {"CANCELLED"}
    _report(operator, parts, skipped, warnings, detail)
    return {"FINISHED"}


class ECK_OT_preview(Operator):
    """Compute the cutting list without writing a file"""

    bl_idname = "eck.preview_razkroi"
    bl_label = "Preview"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and hasattr(context.scene, "eck_props")

    def execute(self, context):
        scene = context.scene
        props = scene.eck_props
        try:
            parts, skipped, warnings, thickness = analyse(scene, props)
        except ThicknessMismatch as mismatch:
            message = str(mismatch)
            fill_parts(props, [])
            _store_result(props, [], [], [message], "Refused: mixed thickness")
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        props.last_thickness = f"{thickness:g}" if thickness is not None else ""
        _store_result(props, parts, skipped, warnings,
                      "Ready to export" if parts else "Nothing to export")
        _report(self, parts, skipped, warnings)
        return {"FINISHED"}


class ECK_OT_open_folder(Operator):
    """Open the folder the cutlist is written to"""

    bl_idname = "eck.open_output_folder"
    bl_label = "Open folder"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and hasattr(context.scene, "eck_props")

    def execute(self, context):
        folder = output_dir(context.scene.eck_props, context.scene)
        try:
            os.makedirs(folder, exist_ok=True)
            bpy.ops.wm.path_open(filepath=folder)
        except (OSError, RuntimeError) as error:
            self.report({"ERROR"}, f"Cannot open {folder}: {error}")
            return {"CANCELLED"}
        return {"FINISHED"}


def set_scene_to_mm(scene):
    """Switch the scene to Metric + Millimeters. Returns (before, after) tuples.

    ``scale_length`` is never touched: changing it would visually rescale every
    existing object, while ``length_unit`` is display-only (measured on 5.2.3).
    """
    units = scene.unit_settings
    before = (units.system, units.length_unit, units.scale_length)
    units.system = "METRIC"
    units.length_unit = "MILLIMETERS"
    after = (units.system, units.length_unit, units.scale_length)
    return before, after


class ECK_OT_scene_to_mm(Operator):
    """Set Length = Millimeters so you can model in millimetres"""

    bl_idname = "eck.scene_to_mm"
    bl_label = "Scene to mm"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def execute(self, context):
        before, after = set_scene_to_mm(context.scene)
        note = ""
        if abs(after[2] - 1.0) > 1e-6:
            note = f" (unit scale {after[2]:g} kept)"
        self.report({"INFO"}, f"{before[0].title()} -> Metric, "
                              f"{before[1].title()} -> Millimeters{note}")
        return {"FINISHED"}


class ECK_OT_copy_to_clipboard(Operator):
    """Copy the cutting list to the clipboard (for Excel / MaxCut)"""

    bl_idname = "eck.copy_razkroi"
    bl_label = "Copy"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and hasattr(context.scene, "eck_props")

    def execute(self, context):
        props = context.scene.eck_props
        try:
            parts, skipped, warnings, _thickness = analyse(context.scene, props)
        except ThicknessMismatch as mismatch:
            self.report({"ERROR"}, str(mismatch))
            return {"CANCELLED"}
        if not parts:
            self.report({"ERROR"}, "Nothing to copy")
            return {"CANCELLED"}
        text = "\n".join(exporter.build_lines(parts, ";"))
        try:
            context.window_manager.clipboard = text + "\n"
        except (AttributeError, RuntimeError) as error:
            self.report({"ERROR"}, f"Clipboard is not available: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Copied {len(parts)} row(s) to the clipboard")
        _store_result(props, parts, skipped, warnings, "Copied to clipboard")
        return {"FINISHED"}


CLASSES = (ECK_OT_export, ECK_OT_export_as, ECK_OT_preview, ECK_OT_open_folder,
           ECK_OT_scene_to_mm, ECK_OT_copy_to_clipboard)
