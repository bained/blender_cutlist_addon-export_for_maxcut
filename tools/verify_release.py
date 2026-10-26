"""Verify a release zip: install it, then run a real export.

    & blender.exe --background --factory-startup --python tools\\verify_release.py
    & blender.exe --background --factory-startup --python tools\\verify_release.py -- --url

With ``--url`` the file is downloaded from the GitHub release, so the published
asset itself is tested instead of the local build.
"""

import importlib
import os
import sys
import tempfile
import urllib.request

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLED = "bl_ext.user_default.export_razkroi_addon"
VERSION = "1.0.0"
RELEASE_URL = ("https://github.com/bained/blender_cutlist_addon-export_for_maxcut"
               f"/releases/latest/download/export_razkroi_addon-{VERSION}.zip")


def local_zip():
    dist = os.path.join(REPO, "dist")
    if not os.path.isdir(dist):
        return None
    zips = sorted(f for f in os.listdir(dist) if f.endswith(".zip"))
    return os.path.join(dist, zips[-1]) if zips else None


def release_zip():
    """Download the published asset so the released bytes are what gets tested."""
    folder = tempfile.mkdtemp(prefix="eck_dl_")
    target = os.path.join(folder, f"export_razkroi_addon-{VERSION}.zip")
    with urllib.request.urlopen(RELEASE_URL, timeout=60) as response:
        payload = response.read()
    with open(target, "wb") as handle:
        handle.write(payload)
    return target


def poll_fails(name):
    try:
        return not getattr(bpy.ops.eck, name).poll()
    except Exception:  # noqa: BLE001
        return True


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    use_url = "--url" in sys.argv
    path = release_zip() if use_url else local_zip()
    if path is None:
        raise SystemExit("no zip found - run tools/build_zip.py first")

    print("\n=========== RELEASE VERIFICATION ===========")
    print("blender:", bpy.app.version_string)
    print("source:", "GitHub release download" if use_url else "local dist/")
    print("zip:", os.path.basename(path), f"({os.path.getsize(path) / 1024:.1f} KB)")

    result = bpy.ops.extensions.package_install_files(filepath=path, repo="user_default")
    print("install:", result)

    module = importlib.import_module(INSTALLED)
    operators = importlib.import_module(f"{INSTALLED}.operators")
    # 4.2+ strips bl_info from extension modules, so read the manifest.
    import tomllib

    manifest = os.path.join(os.path.dirname(module.__file__), "blender_manifest.toml")
    with open(manifest, encoding="utf-8") as handle:
        data = tomllib.loads(handle.read())
    print("version:", data["version"], "| blender_min:", data["blender_version_min"])
    print("panel:", hasattr(bpy.types, "ECK_PT_razkroi"),
          "| list:", hasattr(bpy.types, "ECK_UL_parts"))
    failing = [n for n in ("export_razkroi", "export_razkroi_as", "preview_razkroi",
                           "open_output_folder", "copy_razkroi", "scene_to_mm")
               if poll_fails(n)]
    print("operators failing poll:", failing or "none")

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 1.0
    props = scene.eck_props
    props.only_selected = True
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
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.select_set(True)

    ok, detail, _p, _s, _w = operators.run_export(scene, props)
    print("\nA) mixed selection (3 mm back panel included):")
    print(f"   ok={ok} | {detail}")
    print(f"   file written: {os.path.exists(os.path.join(out_dir, 'cutlist.txt'))}")

    bpy.data.objects["Back"].select_set(False)
    ok, detail, parts, _s, _w = operators.run_export(scene, props)
    print("\nB) 18 mm boards only:")
    print(f"   ok={ok} | {len(parts)} rows | thickness {props.last_thickness} mm")
    target = os.path.join(out_dir, "cutlist.txt")
    if os.path.exists(target):
        with open(target, encoding="utf-8") as handle:
            for line in handle.read().strip().splitlines():
                print(f"   {line}")

    props.group_identical = False
    print("\nC) grouping off:")
    for item in props.parts:
        print(f"   {item.names:<12} {item.length:.0f}x{item.width:.0f}  x{item.qty}")
    print("===========================================\n")


main()
