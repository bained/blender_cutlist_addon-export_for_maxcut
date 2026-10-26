"""Verify the release zip installs and works on a clean profile.

    & blender.exe --background --factory-startup --python tools\\verify_release.py
"""

import importlib
import os
import sys
import tempfile

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLED = "bl_ext.user_default.export_razkroi_addon"


def newest_zip():
    dist = os.path.join(REPO, "dist")
    zips = sorted(f for f in os.listdir(dist) if f.endswith(".zip"))
    if not zips:
        raise SystemExit("no zip in dist/ - run tools/build_zip.py first")
    return os.path.join(dist, zips[-1])


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    path = newest_zip()
    print("\n=========== RELEASE VERIFICATION ===========")
    print("blender:", bpy.app.version_string)
    print("zip:", os.path.basename(path))

    result = bpy.ops.extensions.package_install_files(filepath=path, repo="user_default")
    print("install:", result)

    module = importlib.import_module(INSTALLED)
    operators = importlib.import_module(f"{INSTALLED}.operators")
    # On 4.2+ Blender strips bl_info from extension modules - read the manifest.
    bl_info = getattr(module, "bl_info", None)
    if bl_info is None:
        manifest = os.path.join(os.path.dirname(module.__file__), "blender_manifest.toml")
        import tomllib

        with open(manifest, encoding="utf-8") as handle:
            data = tomllib.loads(handle.read())
        print("version:", data["version"], "| blender_min:", data["blender_version_min"],
              "(from manifest; bl_info is stripped on 4.2+)")
    else:
        print("version:", bl_info["version"], "| blender_min:", bl_info["blender"])
    print("panel:", hasattr(bpy.types, "ECK_PT_razkroi"),
          "| list:", hasattr(bpy.types, "ECK_UL_parts"))
    missing = [n for n in ("export_razkroi", "export_razkroi_as", "preview_razkroi",
                           "open_output_folder", "copy_razkroi", "scene_to_mm")
               if getattr(getattr(bpy.ops, "eck", None), n, None) is None
               or _poll_fails(n)]
    print("operators failing poll:", missing or "none")

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 1.0
    props = scene.eck_props
    props.only_selected = False
    props.auto_thickness = True
    props.material_name = "EGGER W1100 ST9"

    for obj in list(bpy.data.objects):
        if obj.type == "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
    for name, size in (("Side L", (1800, 560, 18)), ("Side R", (1800, 560, 18)),
                       ("Shelf", (1164, 540, 18)), ("Back", (1800, 1200, 3))):
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        obj = bpy.context.active_object
        obj.name = obj.data.name = name
        obj.scale = tuple(v / 1000.0 for v in size)
    bpy.context.view_layer.update()

    out_dir = tempfile.mkdtemp(prefix="eck_release_")
    props.output_dir = out_dir
    props.file_name = "cutlist.txt"

    ok, detail, _p, _s, _w = operators.run_export(scene, props)
    print("\nmixed selection (3 mm back panel present):")
    print(f"  ok={ok} | {detail}")
    print(f"  file written: {os.path.exists(os.path.join(out_dir, 'cutlist.txt'))}")

    bpy.data.objects["Back"].select_set(False)
    props.only_selected = True
    for name in ("Side L", "Side R", "Shelf"):
        bpy.data.objects[name].select_set(True)
    ok, detail, parts, _s, _w = operators.run_export(scene, props)
    print("\nclean selection:")
    print(f"  ok={ok} | {len(parts)} rows | thickness {props.last_thickness} mm")
    target = os.path.join(out_dir, "cutlist.txt")
    if os.path.exists(target):
        with open(target, encoding="utf-8") as handle:
            for line in handle.read().strip().splitlines():
                print(f"  {line}")
    print("===========================================\n")


def _poll_fails(name):
    try:
        return not getattr(bpy.ops.eck, name).poll()
    except Exception:  # noqa: BLE001
        return True


if __name__ == "__main__":
    main()
