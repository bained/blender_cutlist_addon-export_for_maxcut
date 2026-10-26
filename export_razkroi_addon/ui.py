"""N-panel UI: fields, live counters and buttons."""

import os

import bpy
from bpy.types import Panel

from .operators import LAST
from .properties import (
    addon_preferences,
    output_path,
    unit_problems,
    unit_summary,
)


class ECK_PT_razkroi(Panel):
    bl_idname = "ECK_PT_razkroi"
    bl_label = "Cutlist (MaxCut)"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cutlist"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.eck_props
        prefs = addon_preferences()

        # --- units ---------------------------------------------------
        if prefs is None or prefs.show_units_banner:
            box = layout.box()
            row = box.row()
            row.label(text="Units", icon="ORIENTATION_GLOBAL")
            row.label(text=unit_summary(scene))
            for problem in unit_problems(scene):
                box.label(text=problem, icon="ERROR")
            if scene.unit_settings.system != "METRIC" or \
                    scene.unit_settings.length_unit != "MILLIMETERS":
                box.operator("eck.scene_to_mm", icon="DRIVER_DISTANCE",
                             text="Set scene to millimetres")

        # --- material & thickness ------------------------------------
        box = layout.box()
        box.label(text="Material & thickness", icon="MATERIAL")
        box.prop(props, "material_name")
        row = box.row(align=True)
        row.prop(props, "auto_thickness", toggle=True)
        row.prop(props, "thickness_tolerance_mm", text="±mm")
        if not props.auto_thickness:
            box.prop(props, "material_thick_mm")
        row = box.row()
        row.label(text="Detected thickness:")
        row.label(text=f"{props.last_thickness or '-'} mm")

        # --- scope ---------------------------------------------------
        box = layout.box()
        box.label(text="Scope", icon="RESTRICT_SELECT_OFF")
        box.prop(props, "only_selected", toggle=True)
        box.prop(props, "group_identical", toggle=True)
        if not props.group_identical:
            box.label(text="One row per object (no quantity merging)",
                      icon="INFO")

        # --- file ----------------------------------------------------
        box = layout.box()
        box.label(text="File", icon="FILE_TICK")
        box.prop(props, "output_dir", text="")
        row = box.row(align=True)
        row.prop(props, "file_name", text="Name")
        row.prop(props, "overwrite", text="", icon="FILE_REFRESH", toggle=True)
        box.label(text=output_path(props, scene), icon="FILE_BLANK")
        if props.last_file:
            box.label(text=f"Last: {os.path.basename(props.last_file)}",
                      icon="CHECKMARK")

        # --- result --------------------------------------------------
        box = layout.box()
        header = box.row()
        header.label(text="Rows", icon="INFO")
        header.label(text=f"{props.last_parts or '-'} · {props.last_area or '-'} m²")
        if props.last_message:
            bad = props.last_message.startswith(("Error", "Refused", "Write failed"))
            box.label(text=props.last_message, icon="ERROR" if bad else "CHECKMARK")
        if props.last_warnings:
            for warning in props.last_warnings.splitlines()[:4]:
                box.label(text=warning, icon="ERROR")
        if props.last_skipped:
            box.label(text=f"Skipped: {props.last_skipped}", icon="CANCEL")

        if len(props.parts):
            row = box.row()
            row.prop(props, "sort_by", text="")
            row.label(text=f"Total: {len(props.parts)}")
            col = box.column(align=True)
            col.template_list("ECK_UL_parts", "", props, "parts", props, "parts_index",
                              rows=min(8, max(3, len(props.parts))))
        else:
            box.label(text="Press Preview to see the rows", icon="BLANK1")

        # --- actions ---------------------------------------------------
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("eck.export_razkroi", icon="FILE_TICK", text="Export cutlist")
        row = layout.row(align=True)
        row.operator("eck.preview_razkroi", icon="HIDE_OFF", text="Preview")
        row.operator("eck.export_razkroi_as", icon="FILE_NEW", text="Save as…")
        row = layout.row(align=True)
        row.operator("eck.open_output_folder", icon="FILE_FOLDER", text="Open folder")
        row.operator("eck.copy_razkroi", icon="COPYDOWN", text="Copy")


def _menu_export(self, context):
    self.layout.separator()
    self.layout.operator("eck.export_razkroi", icon="FILE_TICK", text="Cutlist (MaxCut)")


CLASSES = (ECK_PT_razkroi,)


def register_menus():
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister_menus():
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
