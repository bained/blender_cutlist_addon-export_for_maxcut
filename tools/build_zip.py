"""Build the installable zip for Blender's "Install from File..." button.

    python tools/build_zip.py            # -> dist/export_razkroi_addon-<version>.zip
    python tools/build_zip.py --out X.zip

The archive contains a single top-level folder with the package, which is what
Blender expects for both installers:

* 4.2+  "Install from File" (Extensions) reads ``blender_manifest.toml``;
* older "Install Add-on from File" reads ``bl_info`` from ``__init__.py``.

``__pycache__`` and other build leftovers are never packaged - a stale ``.pyc``
would shadow the real code after installation.
"""

import argparse
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = "export_razkroi_addon"
DIST = REPO / "dist"
EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip", ".blend1"}
REQUIRED = ("__init__.py", "blender_manifest.toml")


def clean_cache():
    """Drop dist/__pycache__, which a test or demo run may have created."""
    import shutil

    cache = DIST / "__pycache__"
    if cache.is_dir():
        shutil.rmtree(cache, ignore_errors=True)


def addon_version():
    """Version string from blender_manifest.toml (single source of truth)."""
    manifest = REPO / PACKAGE / "blender_manifest.toml"
    match = re.search(r'^version\s*=\s*"([^"]+)"', manifest.read_text(encoding="utf-8"),
                      re.MULTILINE)
    if not match:
        raise SystemExit(f"no version in {manifest}")
    return match.group(1)


def iter_files():
    """Yield (path_on_disk, archive_name) for everything that belongs in the zip."""
    root = REPO / PACKAGE
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            if path.name in EXCLUDE_DIRS:
                continue
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if path.name.startswith("."):
            continue
        yield path, f"{PACKAGE}/{path.relative_to(root).as_posix()}"


def validate():
    """Fail before writing a broken package."""
    root = REPO / PACKAGE
    if not root.is_dir():
        raise SystemExit(f"missing package folder: {root}")
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    if missing:
        raise SystemExit(f"missing files in {PACKAGE}: {', '.join(missing)}")
    bl_info = (root / "__init__.py").read_text(encoding="utf-8")
    if '"version"' not in bl_info:
        print("warning: __init__.py has no bl_info version (legacy installer will not "
              "show a version number)", file=sys.stderr)


def build(out_path):
    validate()
    clean_cache()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    files = list(iter_files())
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, name in files:
            archive.write(source, name)
    size_kb = out_path.stat().st_size / 1024
    print(f"{out_path}  ({len(files)} files, {size_kb:.1f} KB)")
    for _source, name in files:
        print(f"  {name}")
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None,
                        help="target zip (default: dist/<package>-<version>.zip)")
    args = parser.parse_args(argv)
    version = addon_version()
    out_path = args.out or (REPO / "dist" / f"{PACKAGE}-{version}.zip")
    return 0 if build(out_path) else 1


if __name__ == "__main__":
    sys.exit(main())
