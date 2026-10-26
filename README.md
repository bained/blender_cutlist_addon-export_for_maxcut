# Cutlist Export for MaxCut

A Blender add-on that turns the boards selected in a scene into a cutting list
ready to import into **MaxCut**.

Select the parts of one material and one thickness, press **Export**, and get a
`;`-separated text file with the length, width and quantity of every part. No more
copying dimensions by hand into a spreadsheet.

```
"Input Panel";"Side L, Side R";"1800";"560";"2";"";"";"EGGER W1100 ST9"
"Input Panel";"Shelf";"1164";"540";"1";"";"";"EGGER W1100 ST9"
```

## Features

- **Measures the objects for you** — no typing dimensions. Works with rotated
  boards and includes unapplied Bevel / Solidify / Array modifiers.
- **Detects the thickness** from the geometry (the thinnest side of a board), so
  you do not have to declare it.
- **Refuses mixed selections** — if one object has a different thickness, nothing
  is written and the message names the offending objects. This catches the classic
  mistake of exporting a 3 mm back panel together with 18 mm body parts.
- **Groups identical sizes** into one row with a quantity, or turns grouping off
  when a size needs different edge banding in MaxCut.
- **Preview first** if you want: the same computation without touching any file.
- **Unit aware** — works whether the scene is modelled in metres or in millimetres.
- **Saves where you work** — default folder is the folder of the `.blend` file,
  not a temporary directory.

## Install

1. Download `export_razkroi_addon-<version>.zip` from the
   [latest release](https://github.com/bained/blender_cutlist_addon-export_for_maxcut/releases/latest).
2. In Blender open `Edit → Preferences → Add-ons`.

| Blender version | How to install |
|---|---|
| **4.2 and newer** | Top right `⋮` → **Install from Disk…** → pick the zip |
| **4.0 – 4.1** | Button **Install…** at the top → pick the zip, then tick the checkbox next to **Cutlist Export (MaxCut)** |

3. The add-on appears in the 3D Viewport → press `N` → tab **Cutlist**.

> The same zip works on every supported version — it contains both
> `blender_manifest.toml` (read by 4.2+) and `bl_info` (used below 4.2).

### Supported versions

Verified end to end on **Blender 4.0.2** and **Blender 5.2.3 LTS**: the same scene
produces byte-identical output on both.

## Usage

1. Select the boards of **one material and one thickness**.
2. Type the material for this export into the **Material** field (it is saved with
   the `.blend` file, so each project keeps its own value).
3. Press **Preview** to check the rows, or go straight to **Export cutlist**.

The panel is in the 3D Viewport sidebar (`N`) under the **Cutlist** tab.

### Buttons

| Button | What it does |
|---|---|
| **Export cutlist** | Writes the file to the folder and name from the *File* section |
| **Preview** | Computes and shows the rows without writing anything |
| **Save as…** | Opens a file browser to write this one export somewhere else — the panel settings stay untouched |
| **Open folder** | Opens the folder the file goes to |
| **Copy** | Copies the rows to the clipboard (handy for Excel) |
| **Set scene to millimetres** | Shown only when the scene is not set up in millimetres |

### Scope options

- **Selected only** — use the selection, or every visible object in the scene.
- **Group identical parts** — ticked by default. Equal sizes become one row with a
  quantity. Clear it to get one row per object, which is what you want when the same
  size needs different edge banding in MaxCut.

Changing either option recalculates the list immediately, no Preview needed.

### Row order

**Length** (default), **Area** or **Name**. The same order is used in the panel and
in the exported file, so two exports of the same project can be compared line by line.

## What gets measured

- **Thickness** = the thinnest side of the board. No manual entry.
  Turn off *Thickness from objects* if you prefer to type it as a check instead.
- **Length** = the longer of the two remaining sides, **width** = the shorter one.
- Sizes are measured on the **evaluated** geometry, so modifiers are included.
- Objects with a zero dimension are skipped and listed under *Skipped*.

### Units

`mm = raw × (scale_length × 1000)`. With the default unit scale of `1.0` this is
`raw × 1000`; on a scene set to "1 unit = 1 mm" (`scale_length = 0.001`) it is `raw × 1`.
Switching `Meters ↔ Millimeters` in the scene settings only changes what is
displayed — the numbers stay the same.

If the resulting sizes look implausible (for example 600 mm showing up as 600 000 mm),
the panel warns you to check the unit setup.

## Output format

```
"Input Panel";"object name(s)";"length";"width";"qty";"";"";"material"
```

- separator `;`, every field wrapped in double quotes
- UTF-8 without BOM, LF line endings
- **length** is always the longer side, so the file is independent of how a board is
  rotated in the scene
- identical sizes become one row with a quantity (see *Group identical parts*)
- columns 6 and 7 are the edge-banding fields — they are written empty and are meant
  to be filled in MaxCut

Import the file in MaxCut with its parts-import function; the add-on does not depend
on a particular MaxCut version, it only writes this text format.

## Behaviour on problems

| Situation | What happens |
|---|---|
| Mixed thickness in the selection | Export is refused, the message lists the objects that differ, no file is written |
| Nothing selected (with *Selected only*) | `Nothing to export`, no file is written |
| Object with a zero dimension | Skipped, listed under *Skipped* |
| Sizes outside 8–2800 mm | Warning to check the scene units |
| Unusual unit scale | Warning in the panel |
| Target file exists and *Overwrite* is off | A `_001`, `_002` suffix is added |

## Limitations

- Edge banding is not modelled — set it in MaxCut.
- Only rectangular boards are supported; round or oval parts are not detected.
- Metric scenes only (an Imperial scene shows a warning, nothing is converted).

## Development

The add-on lives in `export_razkroi_addon/`; `tools/` holds the build and test helpers.
Pure logic (measuring, grouping, line building) is in `exporter.py` and does not
import `bpy`, so it can be reasoned about and tested separately.

```powershell
# build the installable zip into dist\
python tools\build_zip.py

# run the full test suite against a real Blender installation
powershell -ExecutionPolicy Bypass -File tools\run_blender_tests.ps1

# quick smoke test on another Blender version
& 'C:\Program Files\Blender Foundation\Blender 4.0\blender.exe' --background `
    --factory-startup --python tools\compat_smoke.py
```

`run_blender_tests.ps1` accepts `-Blender <path>` (or the `BLENDER` environment
variable) and runs with `--factory-startup` in a throw-away scripts folder, so your
own Blender settings and installed add-ons are never touched.

There are two kinds of tests in `tools/run_tests.py`:

- **logic tests** — pure Python, they pin down the measuring and grouping rules
  (axis handling, tolerance, mixed thickness, sorting, output format);
- **contract tests** — run against a live Blender and check the add-on itself:
  registration, `bpy.ops` availability, the `Scene.eck_props` properties, an
  end-to-end export, and a clean uninstall.

## License

GPL-2.0-or-later — see [LICENSE](LICENSE). Required for add-ons that use Blender's
Python API.
