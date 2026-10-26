"""Load the add-on on this Blender version and run a real export.

    & blender.exe --background --factory-startup --python tools\\compat_smoke.py
"""

import importlib
import os
import sys
import tempfile
import traceback

import bpy

ADDON = "export_razkroi_addon"


def step(label, func):
    try:
        value = func()
        print(f"[ OK ] {label}: {value}")
        return True
    except Exception:  # noqa: BLE001
        print(f"[FAIL] {label}:\n{traceback.format_exc()}")
        return False


def main():
    print("\n============== COMPAT SMOKE ==============")
    print("blender:", bpy.app.version_string, "| python:", sys.version.split()[0])

    if not step("import add-on", lambda: importlib.import_module(ADDON).__name__):
        return
    module = importlib.import_module(ADDON)
    step("register()", lambda: (module.register(), "done")[1])

    operators = importlib.import_module(f"{ADDON}.operators")
    props_module = importlib.import_module(f"{ADDON}.properties")

    scene = bpy.context.scene
    step("scene has eck_props", lambda: hasattr(scene, "eck_props"))
    props = scene.eck_props

    for obj in list(bpy.data.objects):
        if obj.type == "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
    for name, size in (("Side L", (1800, 560, 18)), ("Side R", (1800, 560, 18)),
                       ("Shelf", (1164, 540, 18))):
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        obj = bpy.context.active_object
        obj.name = obj.data.name = name
        obj.scale = tuple(v / 1000.0 for v in size)
    bpy.context.view_layer.update()

    props.only_selected = False
    props.auto_thickness = True
    props.material_name = "EGGER W1100 ST9"
    scene.unit_settings.scale_length = 1.0

    result = {}

    def analyse():
        parts, skipped, warnings, thickness = operators.analyse(scene, props)
        result["parts"] = parts
        return f"{len(parts)} rows, {thickness:g} mm, skipped={skipped}, warnings={warnings}"

    step("analyse()", analyse)

    def export():
        out = tempfile.mkdtemp(prefix="eck_compat_")
        props.output_dir = out
        props.file_name = "cutlist.txt"
        ok, detail, parts, _s, _w = operators.run_export(scene, props)
        path = os.path.join(out, "cutlist.txt")
        text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        return f"ok={ok} file={os.path.basename(detail)} lines={len(text.splitlines())}"

    step("run_export()", export)

    def grouping():
        props.group_identical = False
        rows = len(props.parts)
        props.group_identical = True
        return f"ungrouped rows={rows}, grouped rows={len(props.parts)}"

    step("update callback on group toggle", grouping)

    def units():
        before, after = operators.set_scene_to_mm(scene)
        return f"{before[:2]} -> {after[:2]}"

    step("set_scene_to_mm()", units)

    def file_lines():
        out = tempfile.mkdtemp(prefix="eck_lines_")
        props.output_dir = out
        props.file_name = "l.txt"
        operators.run_export(scene, props)
        with open(os.path.join(out, "l.txt"), encoding="utf-8") as handle:
            return handle.read().strip().replace("\n", " | ")

    step("file content", file_lines)

    step("unregister()", lambda: (module.unregister(), "done")[1])
    print("=========================================\n")


main()
