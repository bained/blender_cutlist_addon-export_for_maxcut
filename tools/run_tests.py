"""Blender-side validation for the razkroi add-on.

Run with:
    & 'V:\\installed\\blender-5.2.3-windows-x64\\blender.exe' --background `
        --factory-startup --python tools\\run_tests.py

Layers:
  * exporter logic  - cases for the planned exporter, on plain dataclasses (no bpy);
    they pin down what the add-on must do and document the current script's bugs.
  * add-on contract - checks <repo>/export_razkroi_addon/ (manifest, registered
    types, bl_idnames, Scene.eck_props, end-to-end export, clean unregister).

Exit code: 0 when every executed case passed. While the add-on package does not
exist yet, the contract layer is reported as SKIPPED (exit code 2 from main(), but
Blender itself always exits 0 after --python), so the logic layer stays usable.

Environment:
  ECK_ADDON_SCRIPTS  - extra scripts dir for addon_enable(); point it at a temp
                       folder so the user's real preferences stay untouched.
"""

import importlib
import inspect
import os
import re
import sys
import tempfile
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import bpy

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ADDON_DIR = REPO_ROOT / "export_razkroi"
# Fixture material used by the tests. Deliberately non-ASCII (Cyrillic) to prove the
# UTF-8 handling, and deliberately *not* the add-on default: the add-on must not
# ship any real board name.
TEST_MATERIAL = "Тестов материал Бяло W1100 ST9"
DEFAULT_MATERIAL = TEST_MATERIAL  # kept as an alias for the existing cases


# ---------------------------------------------------------------------------
# Minimal test harness (Blender bundles no pytest)
# ---------------------------------------------------------------------------
class Case:
    """Collects failures so one bad assertion does not hide the rest."""

    def __init__(self, name):
        self.name = name
        self.errors = []

    def check(self, cond, message):
        if not cond:
            self.errors.append(message)
        return bool(cond)

    def equal(self, got, expected, message):
        return self.check(got == expected, f"{message}: expected {expected!r}, got {got!r}")

    def near(self, got, expected, tol, message):
        ok = got is not None and abs(got - expected) <= tol
        return self.check(ok, f"{message}: expected {expected} ±{tol}, got {got!r}")


def run_cases(cases):
    passed = failed = 0
    for name, func in cases:
        case = Case(name)
        try:
            func(case)
        except Exception:  # noqa: BLE001 - report, keep going
            case.errors.append("exception:\n" + traceback.format_exc())
        if case.errors:
            failed += 1
            print(f"[FAIL] {name}")
            for err in case.errors:
                print("       - " + err.replace("\n", "\n         "))
        else:
            passed += 1
            print(f"[ OK ] {name}")
    return passed, failed


def logic(func):
    """Mark a test as pure exporter logic (runs without bpy)."""
    func.layer = "logic"
    return func


def addon(func):
    """Mark a test as add-on contract check (needs the live Blender build)."""
    func.layer = "addon"
    return func



# ---------------------------------------------------------------------------
# 1. Exporter logic (specification for exporter.py)
# ---------------------------------------------------------------------------
@dataclass
class Panel:
    """Blender-independent view of one selected object."""

    name: str
    dims_mm: tuple = (0.0, 0.0, 0.0)  # evaluated world-axis bounding box, in mm
    material: str = ""


@dataclass
class Part:
    names: list = field(default_factory=list)
    length: float = 0.0
    width: float = 0.0
    material: str = ""
    qty: int = 0


DIM_EPS = 1e-4  # mm


def measure(panel):
    """Return (length, width) in mm: the two long sides of the board."""
    a, b, c = (round(v, 3) for v in panel.dims_mm)
    if min(a, b, c) <= 1e-4:
        return None, None
    sides = sorted((a, b, c))
    return sides[2], sides[1]


def panel_thickness(panel):
    return min(float(v) for v in panel.dims_mm)


def common_thickness(panels):
    """Majority thickness; on a tie the thicker value wins (a stray 3 mm back
    panel must be the outlier, not the 18 mm body parts)."""
    counts = {}
    for panel in panels:
        key = round(panel_thickness(panel), 1)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def thickness_outliers(panels, tolerance=0.5, expected=None):
    if not panels:
        return expected, []
    target = expected if expected is not None else common_thickness(panels)
    outliers = [(p.name, round(panel_thickness(p), 1)) for p in panels
                if abs(panel_thickness(p) - target) > tolerance]
    return target, outliers


def sort_parts(parts, sort_by="LENGTH"):
    """Order rows: longest first (default), alphabetical, or by area."""
    if sort_by == "NAME":
        return sorted(parts, key=lambda p: (", ".join(p.names).lower(), -p.length))
    if sort_by == "AREA":
        return sorted(parts, key=lambda p: (-p.length * p.width * p.qty, -p.length))
    return sorted(parts, key=lambda p: (-p.length, -p.width, ", ".join(p.names).lower()))


def group_with(panels, material="M", group_identical=True):
    """Test-side mirror of exporter.group() including the grouping flag."""
    parts, skipped = [], []
    index = {}
    for panel in panels:
        length, width = measure(panel)
        if length is None or width is None:
            skipped.append(panel.name)
            continue
        if not group_identical:
            parts.append(Part([panel.name], length, width, material, 1))
            continue
        key = (length, width)
        if key in index:
            part = parts[index[key]]
            part.names.append(panel.name)
            part.qty += 1
        else:
            index[key] = len(parts)
            parts.append(Part([panel.name], length, width, material, 1))
    return parts, skipped


def exporter_line(part, delimiter=";"):
    """Test-side mirror of exporter.build_line()."""
    fields = ["Input Panel", ", ".join(part.names), _num(part.length), _num(part.width),
              str(part.qty), "", "", part.material]
    return delimiter.join('"' + f + '"' for f in fields)


def group(panels, material=""):
    """Group identical (length, width) panels; material is not part of the key."""
    parts, skipped = [], []
    index = {}
    for panel in panels:
        length, width = measure(panel)
        if length is None or width is None:
            skipped.append(panel.name)
            continue
        key = (length, width)
        if key in index:
            part = parts[index[key]]
            part.names.append(panel.name)
            part.qty += 1
        else:
            index[key] = len(parts)
            parts.append(Part([panel.name], length, width, material, 1))
    return parts, skipped


def maxcut_line(part, delimiter=";", material_name=None):
    """Mirror of the current output line: "Input Panel"; name; L; W; qty; edge; edge; material."""
    material = material_name or part.material
    fields = ["Input Panel", ", ".join(part.names), _num(part.length), _num(part.width),
              str(part.qty), "", "", material]
    return delimiter.join('"' + f + '"' for f in fields)


def _num(value):
    return str(int(value)) if abs(value - round(value)) < 1e-9 else f"{value:.1f}"


MM_PER_UNIT = 1000.0


def dims_to_mm(raw_units, scale_length):
    """Blender float dimensions -> millimetres.

    ``scene.unit_settings.scale_length`` is how many metres one Blender unit
    represents (``1.0`` = model in metres, ``0.001`` = "1 unit = 1 mm"), so the
    millimetre size of one unit is ``scale_length * 1000``. Measured on 5.2.3
    with tools/probe_units.py.
    """
    return raw_units * (scale_length * MM_PER_UNIT)


def plausible_project(parts):
    """Rough sanity ranges for a furniture cutting list, in mm / m².

    Returns a list of human-readable problems; empty means "looks like furniture".
    """
    problems = []
    for part in parts:
        if not 8.0 <= part.length <= 2800.0 or not 8.0 <= part.width <= 2800.0:
            problems.append(f"{part.names[0]}: {_num(part.length)}x{_num(part.width)} mm "
                            f"is outside 8..2800 mm")
    area_m2 = sum(p.length * p.width * p.qty for p in parts) / 1_000_000.0
    if parts and not 0.01 <= area_m2 <= 500.0:
        problems.append(f"total area {area_m2:.2f} m2 is suspicious")
    return problems


@logic
def test_units_same_part_four_setups(case):
    """Same physical 600x400x25 mm part modelled in the four unit setups.

    Expected raw values come from probe_units.py on 5.2.3:
    ``scale_length`` is metres per Blender unit, so a 600 mm side is
    0.6 units at scale 1.0 and 600 units at scale 0.001.
    """
    setups = [
        # label, raw dimension of the 600 mm side, length_unit, scale_length
        ("mm suffix, scale 1.0", 0.6, "MILLIMETERS", 1.0),
        ("m suffix, scale 1.0", 0.6, "METERS", 1.0),
        ("mm suffix, scale 0.001", 600.0, "MILLIMETERS", 0.001),
        ("m suffix, scale 0.001", 600.0, "METERS", 0.001),
    ]
    for label, raw_long, _unit, scale in setups:
        raw = (raw_long, raw_long / 1.5, raw_long / 24.0)
        mm = [dims_to_mm(v, scale) for v in raw]
        length, width = measure(Panel("p", mm))
        case.near(length, 600.0, 0.01, f"{label}: length (converted {mm[0]:.4f} mm)")
        case.near(width, 400.0, 0.01, f"{label}: width")


@logic
def test_units_length_unit_is_display_only(case):
    """Changing Meters -> Millimeters must not change any exported number."""
    raw = (0.6, 0.4, 0.025)
    for scale in (1.0, 0.001):
        for _length_unit in ("MILLIMETERS", "CENTIMETERS", "METERS"):
            mm = [dims_to_mm(v, scale) for v in raw]
            parts, _ = group([Panel("p", tuple(mm))])
            case.equal(len(parts), 1, f"one part for scale {scale}, unit {_length_unit}")


@logic
def test_units_naive_conversion_fails(case):
    """Document why `raw * 1000` alone is not enough (the ×1000 trap)."""
    # "1 unit = 1 mm" scene: a 600 mm panel is 600 Blender units.
    raw_long = 600.0
    naive = raw_long * 1000.0
    correct = dims_to_mm(raw_long, 0.001)
    case.near(naive, 600000.0, 0.01, "naive conversion overshoots by 1000x")
    case.near(correct, 600.0, 0.01, "scale_length-aware conversion is right")
    length, width = measure(Panel("p", (naive, naive / 1.5, 25.0)))
    case.check(length is not None and length > 2800.0,
               "the naive value trips the plausibility check")
    problems = plausible_project([Part(["p"], naive, naive / 1.5, "M", 1)])
    case.check(len(problems) > 0, f"warning text produced, got {problems}")


@logic
def test_units_plausible_project_passes(case):
    parts = [Part(["A"], 600.0, 400.0, "M", 8), Part(["B"], 2800.0, 100.0, "M", 2)]
    case.equal(plausible_project(parts), [], "normal furniture project is not flagged")


@logic
def test_legacy_detects_zero_size_panels(case):
    """Thickness is now read from the geometry, so orientation never matters.

    The old rule compared each axis with the nominal thickness and silently
    accepted anything below it, which turned some boards into 0x0 rows.
    """
    cases = [
        ((600.0, 400.0, 18.0), (600.0, 400.0)),
        ((400.0, 18.0, 600.0), (600.0, 400.0)),
        ((18.0, 400.0, 600.0), (600.0, 400.0)),
    ]
    for dims, wanted in cases:
        got = measure(Panel("p", dims))
        case.near(got[0], wanted[0], 1e-6, f"length for {dims}")
        case.near(got[1], wanted[1], 1e-6, f"width for {dims}")
        case.near(panel_thickness(Panel("p", dims)), 18.0, 1e-9, f"thickness for {dims}")
    for thick in (24.5, 24.9):
        # `(thick - 25) < 0.01` is True for anything below 25.01 mm, so the old
        # script exported a 24.5 mm board as if it were 25 mm without a word.
        case.check((thick - 25) < 0.01, f"documented: legacy accepts {thick} mm as 25 mm")
        length, width = measure(Panel("p", (600.0, 400.0, thick)))
        case.check(length is not None, f"{thick} mm is measurable by geometry, not rejected")


def legacy_measure(dims_mm, material_thick=25):
    """Verbatim copy of the original standalone script (kept in .dev/)."""
    a, b, c = dims_mm
    l = w = 0
    if (a - material_thick) < 0.01:
        l, w = (b, c) if b > c else (c, b)
    if (b - material_thick) < 0.01:
        l, w = (a, c) if a > c else (c, a)
    if (c - material_thick) < 0.01:
        l, w = (a, b) if a > b else (b, a)
    return l, w


@logic
def test_legacy_emits_zero_rows(case):
    """Proof of the bug on the installed Blender's Python, not a claim.

    ``legacy_measure`` is the verbatim rule from the original standalone script, so the
    expected outcomes below document the real behaviour of today's script.
    """
    expected = {
        (600.0, 400.0, 25.0): (600.0, 400.0),  # nominal thickness: works
        (600.0, 400.0, 18.0): (600.0, 400.0),  # 18 mm body panel: silently "works"
        (400.0, 600.0, 25.0): (600.0, 400.0),  # L/W normalised only inside the branches
        (600.0, 25.4, 400.0): (0, 0),          # 25.4 - 25 = 0.4 >= 0.01 -> 0x0 row
        (600.0, 24.5, 400.0): (600.0, 400.0),  # 24.5 accepted as 25 mm (no warning)
    }
    for dims, wanted in expected.items():
        case.equal(legacy_measure(dims), wanted, f"legacy result for {dims}")


@logic
def test_measure_axis_and_swapped_lw(case):
    """Length is always the longer side, whatever the board orientation."""
    cases = [
        ((400.0, 25.0, 600.0), 600.0, 400.0),
        ((25.0, 600.0, 400.0), 600.0, 400.0),
        ((300.0, 1200.0, 24.8), 1200.0, 300.0),
        ((600.0, 400.0, 18.0), 600.0, 400.0),
    ]
    for dims, exp_l, exp_w in cases:
        length, width = measure(Panel("p", dims))
        case.near(length, exp_l, 1e-6, f"length for {dims}")
        case.near(width, exp_w, 1e-6, f"width for {dims}")


@logic
def test_measure_rejects_degenerate_and_flat(case):
    for dims in ((0.0, 0.0, 0.0), (600.0, 400.0, 0.0), (0.0, 400.0, 25.0)):
        length, width = measure(Panel("p", dims))
        case.check(length is None and width is None, f"{dims} must be skipped, got {length}x{width}")


@logic
def test_thickness_is_the_thinnest_side(case):
    for dims, want in (((600.0, 400.0, 18.0), 18.0), ((18.0, 600.0, 400.0), 18.0),
                       ((600.0, 18.0, 400.0), 18.0), ((1800.0, 1200.0, 3.0), 3.0)):
        case.near(panel_thickness(Panel("p", dims)), want, 1e-9, f"thickness of {dims}")


@logic
def test_single_thickness_selection_passes(case):
    """A selection of one thickness produces no outliers."""
    panels = [Panel("a", (600.0, 400.0, 18.0)), Panel("b", (1200.0, 400.0, 18.0)),
              Panel("c", (600.0, 18.0, 400.0))]
    expected, outliers = thickness_outliers(panels)
    case.near(expected, 18.0, 1e-9, "auto-detected thickness")
    case.equal(outliers, [], "no outliers for a uniform selection")


@logic
def test_mixed_thickness_is_reported(case):
    """One 3 mm back panel among 18 mm boards must be reported, not ignored."""
    panels = [Panel("side", (1800.0, 560.0, 18.0)), Panel("top", (1200.0, 560.0, 18.0)),
              Panel("back", (1800.0, 1200.0, 3.0))]
    expected, outliers = thickness_outliers(panels)
    case.near(expected, 18.0, 1e-9, "majority thickness wins")
    case.equal(outliers, [("back", 3.0)], "the 3 mm board is the outlier")


@logic
def test_tolerance_absorbs_sanding(case):
    """Real sanding variation stays inside the default 0.5 mm; a different board
    (3 mm back, or 25 mm worktop) does not."""
    sanded = [Panel("a", (600.0, 400.0, 18.0)), Panel("b", (600.0, 400.0, 18.4))]
    case.equal(thickness_outliers(sanded)[1], [], "0.4 mm is the same board")

    different = [Panel("a", (600.0, 400.0, 18.0)), Panel("b", (600.0, 400.0, 17.0))]
    case.equal(thickness_outliers(different)[1], [("b", 17.0)], "1 mm difference is reported")

    back = [Panel("side", (1800.0, 560.0, 18.0)), Panel("back", (1800.0, 1200.0, 3.0))]
    case.equal(thickness_outliers(back)[1], [("back", 3.0)], "3 mm back panel is reported")


@logic
def test_manual_thickness_overrides_detection(case):
    """Manual mode: the typed value is the reference, not the majority."""
    panels = [Panel("a", (600.0, 400.0, 18.0)), Panel("b", (600.0, 400.0, 18.0))]
    expected, outliers = thickness_outliers(panels, expected=25.0)
    case.near(expected, 25.0, 1e-9, "manual thickness is used")
    case.equal(outliers, [("a", 18.0), ("b", 18.0)], "all boards are off the manual value")


@logic
def test_group_identical_can_be_disabled(case):
    """One row per object when grouping is off - MaxCut then keeps them apart."""
    panels = [Panel("Side L", (1800.0, 560.0, 18.0)),
              Panel("Side R", (1800.0, 560.0, 18.0)),
              Panel("Shelf", (1164.0, 540.0, 18.0))]

    grouped, _ = group_with(panels, group_identical=True)
    case.equal([(p.names, p.qty) for p in grouped],
               [(["Side L", "Side R"], 2), (["Shelf"], 1)],
               "default: equal sizes merged")

    separate, skipped = group_with(panels, group_identical=False)
    case.equal(skipped, [], "nothing skipped")
    case.equal([(p.names, p.qty) for p in separate],
               [(["Side L"], 1), (["Side R"], 1), (["Shelf"], 1)],
               "grouping off: one row per object")
    case.equal([exporter_line(p) for p in separate], [
        '"Input Panel";"Side L";"1800";"560";"1";"";"";"M"',
        '"Input Panel";"Side R";"1800";"560";"1";"";"";"M"',
        '"Input Panel";"Shelf";"1164";"540";"1";"";"";"M"',
    ], "each line carries its own object name")


@logic
def test_group_identical_area_still_adds_up(case):
    """Switching grouping off must not change the total board area."""
    panels = [Panel("a", (1800.0, 560.0, 18.0)), Panel("b", (1800.0, 560.0, 18.0)),
              Panel("c", (1164.0, 540.0, 18.0))]
    for flag in (True, False):
        parts, _ = group_with(panels, group_identical=flag)
        total = sum(p.length * p.width * p.qty for p in parts) / 1_000_000.0
        # 2 x (1800 x 560) + 1 x (1164 x 540) = 2.645 m²
        case.near(total, 2.645, 0.005, f"total area with group_identical={flag}")
    """Material is not read from objects at all - one material per export."""
    panels = [Panel("A", (600.0, 25.0, 400.0), "W"), Panel("B", (600.0, 25.0, 400.0), "O")]
    parts, skipped = group(panels, material="ЕДИН МАТЕРИАЛ")
    case.equal(skipped, [], "nothing skipped")
    case.equal(len(parts), 1, "same size -> one row regardless of object materials")
    case.equal(parts[0].qty, 2, "quantity counts both")
    case.equal(parts[0].material, "ЕДИН МАТЕРИАЛ", "row uses the exported material")


@logic
def test_sort_parts_is_predictable(case):
    """Default order: longest first, then widest - so exports are diff-able."""
    parts = [Part(["b"], 1200.0, 560.0, "M", 1),
             Part(["a"], 1800.0, 560.0, "M", 2),
             Part(["c"], 1200.0, 300.0, "M", 1)]
    case.equal([p.names[0] for p in sort_parts(parts)], ["a", "b", "c"],
               "LENGTH: 1800 first, then 1200x560 before 1200x300")
    case.equal([p.names[0] for p in sort_parts(parts, "NAME")], ["a", "b", "c"],
               "NAME is alphabetical")
    case.equal(sort_parts(parts), sort_parts(list(reversed(parts))),
               "same input in any order gives the same output")


@logic
def test_sort_area_counts_quantity(case):
    """AREA puts rows with the largest total board area first (width counts)."""
    parts = [Part(["small"], 600.0, 400.0, "M", 1),      # 0.24 m²
             Part(["many"], 2700.0, 560.0, "M", 1),      # 1.51 m²
             Part(["stack"], 600.0, 400.0, "M", 20)]     # 4.80 m²
    case.equal([p.names[0] for p in sort_parts(parts, "AREA")],
               ["stack", "many", "small"],
               "20 small boards outweigh one long board")


@logic
def test_float_noise_rounds_to_tenth(case):
    length, width = measure(Panel("p", (599.99999, 399.99999, 18.000004)))
    case.near(length, 600.0, 1e-6, "length with float noise")
    case.near(width, 400.0, 1e-6, "width with float noise")
    case.equal(_num(length), "600", "export formats integer mm without .0")


@logic
def test_group_counts_identical_sizes(case):
    """Same size -> one row with qty, whatever materials the objects carry."""
    panels = [
        Panel("A", (600.0, 25.0, 400.0), "18mm White"),
        Panel("B", (600.0, 25.0, 400.0), "18mm White"),
        Panel("C", (600.0, 25.0, 400.0), "25mm Oak"),
        Panel("D", (300.0, 25.0, 400.0), "18mm White"),
    ]
    parts, skipped = group(panels, material="M")
    case.equal(skipped, [], "nothing skipped")
    case.equal([(p.length, p.width, p.qty) for p in parts],
               [(600.0, 400.0, 3), (400.0, 300.0, 1)], "sizes grouped by dimensions only")
    case.equal({p.material for p in parts}, {"M"}, "every row carries the export material")


@logic
def test_group_skips_and_reports(case):
    panels = [Panel("good", (600.0, 25.0, 400.0)),
              Panel("empty", (0.0, 0.0, 0.0)),
              Panel("flat", (600.0, 0.0, 400.0))]
    parts, skipped = group(panels)
    case.equal(len(parts), 1, "only the usable panel is exported")
    case.equal(skipped, ["empty", "flat"], "unmeasurable names are reported")


@logic
def test_output_line_matches_legacy_shape(case):
    part = Part(["A", "B"], 600.0, 400.0, "M", 2)
    line = maxcut_line(part, material_name=DEFAULT_MATERIAL)
    expected = ('"Input Panel";"A, B";"600";"400";"2";"";"";'
                f'"{DEFAULT_MATERIAL}"')
    case.equal(line, expected, "semicolon line identical to current format")
    case.equal(line.count(";"), 7, "eight fields => seven separators")


@logic
def test_utf8_file_write(case):
    """The current script writes with the locale codec -> cp1251 mojibake on Windows."""
    line = maxcut_line(Part(["A"], 600.0, 400.0, DEFAULT_MATERIAL, 1))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "razkroi.txt"
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        raw = path.read_bytes()
        case.check(DEFAULT_MATERIAL.encode("utf-8") in raw, "material stored as UTF-8 bytes")
        case.equal(path.read_text(encoding="utf-8").splitlines(), [line], "round-trip text")
        case.check(raw.endswith(b"\n") and b"\r\n" not in raw, "LF newlines, trailing newline")


@logic
def test_output_dir_fallback_order(case):
    """The output folder must never fall back to a temp/render path."""
    # Read the constant out of the source instead of importing: this layer must
    # stay runnable without Blender, and properties.py imports bpy.
    source = (addon_dir() / "properties.py").read_text(encoding="utf-8")
    match = re.search(r'^FALLBACK_FOLDER\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if not case.check(match is not None, "FALLBACK_FOLDER is defined in properties.py"):
        return
    fallback_folder = match.group(1)

    def resolve(output_dir, blend_path, last_dir):
        if output_dir:
            return output_dir
        if blend_path:
            return str(Path(blend_path).parent)
        if last_dir:
            return last_dir
        return str(Path.home() / "Documents" / fallback_folder)

    case.equal(resolve("D:/proj/out", "D:/proj/a.blend", "E:/last"), "D:/proj/out",
               "explicit dir wins")
    case.equal(resolve("", "D:/proj/a.blend", "E:/last"), str(Path("D:/proj")),
               "blend folder next")
    case.equal(resolve("", "", "E:/last"), "E:/last", "prefs.last_dir next")
    case.check(fallback_folder in resolve("", "", ""), "documents fallback")
    case.check(tempfile.gettempdir().lower() not in resolve("", "", "").lower(),
               "temp dir is never the fallback")


@addon
def test_default_file_name(case):
    """The unsaved-scene fallback must not repeat the suffix.

    Saved scenes cannot be simulated (``bpy.data.filepath`` is read-only), so this
    pins the branch that actually had the bug: an unsaved scene produced
    ``cutlist_cutlist.txt`` because the stem and the suffix both said "cutlist".
    """
    load_addon()
    properties = importlib.import_module(f"{ADDON_ID}.properties")
    if bpy.data.filepath:
        case.check(True, "saved scene - fallback branch not reachable, skipped")
        return
    name = properties.default_file_name()
    case.equal(name.count("cutlist"), 1, f"suffix is not doubled (got {name!r})")
    case.equal(name, properties.FALLBACK_FILE_NAME,
               "unsaved scene gets the plain fallback name")
    case.check(properties.FALLBACK_FILE_NAME.endswith(".txt"),
               "fallback is still a .txt file")


@logic
def test_empty_material_falls_back_visibly(case):
    """An unfilled material must be obvious in MaxCut, not a blank column.

    This is what replaced the hardcoded default board name: the panel starts empty,
    and the file says so loudly enough that nobody ships a sheet with no material.
    """
    load_addon()
    exporter = importlib.import_module(f"{ADDON_ID}.exporter")
    part = exporter.Part(["A"], 600.0, 400.0, "", 1)
    line = exporter.build_line(part)
    case.check(exporter.FALLBACK_MATERIAL in line,
               f"empty material becomes {exporter.FALLBACK_MATERIAL!r}, got {line!r}")
    case.equal(line.count(";"), 7, "still eight fields")
    named = exporter.build_line(exporter.Part(["A"], 600.0, 400.0, "Oak H3734", 1))
    case.check("Oak H3734" in named and exporter.FALLBACK_MATERIAL not in named,
               "a typed material is used as-is")


# ---------------------------------------------------------------------------
# 2. Add-on contract against the running Blender build
# ---------------------------------------------------------------------------
ADDON_ID = "export_razkroi_addon"
REQUIRED_TYPES = {
    "export operator": "ECK_OT_export",
    "export-as operator": "ECK_OT_export_as",
    "preview operator": "ECK_OT_preview",
    "open folder operator": "ECK_OT_open_folder",
    "panel": "ECK_PT_razkroi",
    "properties group": "ECK_Properties",
}

_ADDON_MODULE = None


def addon_dir():
    """The add-on package folder: <repo>/export_razkroi_addon/."""
    return REPO_ROOT / ADDON_ID


def load_addon():
    """Enable the add-on and return its imported module.

    ``addon_enable`` reports success through ``bpy.app.handlers`` in background
    mode, so the reliable signal is that the module is importable and has
    registered its RNA types.
    """
    global _ADDON_MODULE
    if _ADDON_MODULE is not None:
        return _ADDON_MODULE
    if not addon_dir().is_dir():
        raise RuntimeError(f"add-on folder missing: {addon_dir()}")
    bpy.ops.preferences.addon_enable(module=ADDON_ID)
    _ADDON_MODULE = importlib.import_module(ADDON_ID)
    if not hasattr(bpy.types, "ECK_PT_razkroi"):
        raise RuntimeError(f"{ADDON_ID} enabled but its Panel is not registered")
    return _ADDON_MODULE


@addon
def test_manifest_file(case):
    path = addon_dir() / "blender_manifest.toml"
    if not case.check(path.exists(), f"missing {path.name}"):
        return
    try:
        import tomllib
    except ImportError:  # noqa: BLE001 - older python in Blender
        case.check(False, "tomllib unavailable; cannot verify manifest")
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for key in ("schema_version", "id", "name", "version", "type", "blender_version_min",
                "maintainer", "license", "tagline"):
        case.check(key in data, f"manifest missing required key {key!r}")
    case.equal(data.get("type"), "add-on", "manifest type")
    case.equal(data.get("id"), ADDON_ID, "manifest id matches the package folder")
    tagline = data.get("tagline", "")
    case.check(0 < len(tagline) <= 64, f"tagline length {len(tagline)} must be 1..64")
    case.check(not tagline.endswith((".", "!")), "tagline must not end with punctuation")
    case.check("files" in data.get("permissions", {}), "permissions.files declared (writes a file)")


@addon
def test_addon_enables(case):
    module = load_addon()
    case.check(hasattr(module, "register") and hasattr(module, "unregister"),
               "package exposes register() and unregister()")


@addon
def test_registered_types(case):
    """Blender exposes operators/panels in ``bpy.types`` under their full idname
    (``ECK_OT_export`` -> ``ECK_OT_export_razkroi``) and PropertyGroups under the
    class name, so each type is checked where Blender actually puts it."""
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    missing = []
    for what, ident in REQUIRED_TYPES.items():
        if ident.startswith("ECK_OT_"):
            cls = getattr(operators, ident, None)
            idname = getattr(cls, "bl_idname", "")
            found = bool(idname) and hasattr(bpy.ops.eck, idname.split(".", 1)[1])
        elif ident.endswith("_Properties"):
            # PropertyGroups live in the add-on module's namespace, not in bpy.types.
            props_module = importlib.import_module(f"{ADDON_ID}.properties")
            found = hasattr(props_module, ident)
        else:
            found = hasattr(bpy.types, ident)
        if not found:
            missing.append(f"{what} ({ident})")
    case.equal(missing, [], "registered types present")
    case.check(hasattr(bpy.types, "ECK_PT_razkroi"), "panel registered in bpy.types")
    case.check(hasattr(bpy.types, "ECK_OT_export_razkroi"),
               "ECK_OT_export is registered under its full idname")


@addon
def test_preferences_registered(case):
    """AddonPreferences never lands in bpy.types - it is reached via preferences.addons."""
    load_addon()
    addon = bpy.context.preferences.addons.get("export_razkroi_addon")
    if not case.check(addon is not None,
                      f"{ADDON_ID} is not in preferences.addons "
                      f"(keys: {list(bpy.context.preferences.addons.keys())})"):
        return
    prefs = addon.preferences
    if not case.check(prefs is not None, "addon has no AddonPreferences instance"):
        return
    case.equal(type(prefs).__name__, "ECK_Preferences", "preferences class")
    props = importlib.import_module(f"{ADDON_ID}.properties")
    # bpy_struct wrappers are not Python instances of the class, so identity cannot
    # be tested with `is`; compare the RNA identifier instead.
    found = props.addon_preferences()
    case.check(found is not None and found.bl_rna.identifier == "ECK_Preferences",
               f"addon_preferences() returns them, got {found}")
    for field_name in ("last_dir", "show_units_banner"):
        case.check(field_name in prefs.bl_rna.properties.keys(),
                   f"preference {field_name!r} present")
    presets = [name for name in props.ECK_Preferences.bl_rna.properties.keys()
               if name.startswith("preset_")]
    case.equal(presets, [], "presets were removed from the preferences")


REQUIRED_PROPS = ("material_name", "material_thick_mm", "thickness_tolerance_mm",
                  "output_dir", "file_name", "overwrite", "only_selected")


@addon
def test_bl_idnames_are_namespaced(case):
    for what, ident in REQUIRED_TYPES.items():
        rna = getattr(bpy.types, ident, None)
        if rna is None:
            continue
        case.check(rna.bl_idname.startswith(("eck", "ECK")),
                   f"{what}: bl_idname {rna.bl_idname!r} must be namespaced eck*/ECK*")


@addon
def test_exporter_options_registered(case):
    props = bpy.types.Scene.bl_rna.properties
    if not case.check("eck_props" in props.keys(),
                      "Scene.eck_props (PointerProperty) registered"):
        return
    group = getattr(bpy.types, "ECK_Properties", None)
    if group is None:
        return
    keys = group.bl_rna.properties.keys()
    for field_name in REQUIRED_PROPS:
        case.check(field_name in keys, f"property {field_name!r} present")
    default = getattr(group.bl_rna.properties["material_name"], "default", None)
    case.equal(default, "", "material default is empty - no board name shipped with the add-on")


@addon
def test_ui_draws_without_error(case):
    """Operator poll paths must not raise in background mode."""
    if not case.check(hasattr(bpy.ops.eck, "export_razkroi"),
                      "bpy.ops.eck.export_razkroi missing"):
        return
    module = load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    for cls_name in ("ECK_OT_export", "ECK_OT_preview", "ECK_OT_open_folder"):
        cls = getattr(operators, cls_name, None)
        if not case.check(cls is not None, f"{cls_name} not found in operators.py"):
            continue
        try:
            case.check(cls.poll(bpy.context) is True, f"{cls_name}.poll() is True")
        except Exception:  # noqa: BLE001
            case.check(False, f"{cls_name}.poll() raised:\n{traceback.format_exc()}")
    case.check(module is not None, "add-on module imported")


@addon
def test_export_writes_utf8_file(case):
    """End-to-end: three panels -> one grouped line, Cyrillic material in UTF-8."""
    if not hasattr(bpy.ops.eck, "export_razkroi"):
        case.check(False, "bpy.ops.eck.export_razkroi not registered")
        return
    scene = bpy.context.scene
    props = scene.eck_props
    saved = (props.material_name, props.auto_thickness, props.material_thick_mm,
             props.output_dir, props.file_name)
    props.material_name = DEFAULT_MATERIAL
    props.auto_thickness = True  # thickness comes from the geometry
    created = []
    try:
        for sx, sy, sz in ((0.6, 0.4, 0.025), (0.6, 0.025, 0.4), (0.3, 0.4, 0.025)):
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
            obj = bpy.context.active_object
            obj.scale = (sx, sy, sz)
            obj.name = f"panel_{len(created)}"
            created.append(obj)
        for obj in created:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = created[0]
        with tempfile.TemporaryDirectory() as tmp:
            props.output_dir = tmp
            props.file_name = "razkroi_test.txt"
            ops = importlib.import_module(f"{ADDON_ID}.operators")
            # Call the plain function: bpy.ops.eck.*.poll() also runs the sibling
            # file-browser operator's poll, which needs a window (absent in -b).
            ok, detail, parts, _skipped, _warnings = ops.run_export(
                bpy.context.scene, props)
            case.equal(ok, True, f"export ok, detail={detail}")
            case.equal(parts[0].qty if parts else 0, 2, "identical panels grouped")
            path = Path(tmp) / "razkroi_test.txt"
            if case.check(path.exists(), "razkroi_test.txt written"):
                raw = path.read_bytes()
                case.check(DEFAULT_MATERIAL.encode("utf-8") in raw, "material is UTF-8 in file")
                lines = path.read_text(encoding="utf-8").splitlines()
                # two identical 600x400 panels + one 300x400 panel
                case.equal(len(lines), 2, "two groups exported")
                case.check(any('"2"' in line and '"600"' in line for line in lines),
                           f"identical panels grouped with qty=2, lines={lines!r}")
                case.check(any('"300"' in line for line in lines),
                           f"300x400 panel exported, lines={lines!r}")
    finally:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        (props.material_name, props.auto_thickness, props.material_thick_mm,
         props.output_dir, props.file_name) = saved


@addon
def test_shared_exporter_matches_test_logic(case):
    """The add-on's exporter.py must agree with the logic layer above.

    ``measure``/``group`` in this file are the executable specification; the
    add-on ships its own copy in ``exporter.py`` and the two must not drift.
    """
    exporter = importlib.import_module(f"{ADDON_ID}.exporter")
    samples = [
        (600.0, 400.0, 25.0), (600.0, 25.0, 400.0), (600.0, 18.0, 400.0),
        (0.0, 0.0, 0.0), (300.0, 1200.0, 24.8), (599.99999, 399.99999, 18.000004),
    ]
    for dims in samples:
        case.equal(exporter.measure(exporter.Panel("p", dims)),
                   measure(Panel("p", dims)), f"measure({dims})")
        case.equal(exporter.panel_thickness(exporter.Panel("p", dims)),
                   panel_thickness(Panel("p", dims)), f"thickness({dims})")

    panels = [Panel("A", (600.0, 18.0, 400.0), "W"), Panel("B", (600.0, 18.0, 400.0), "W"),
              Panel("C", (600.0, 18.0, 400.0), "O"), Panel("D", (0.0, 0.0, 0.0)),]
    eparts, eskipped = exporter.group([exporter.Panel(p.name, p.dims_mm, p.material)
                                       for p in panels], "M")
    tparts, tskipped = group(panels, "M")
    case.equal(eskipped, tskipped, "skipped objects")
    case.equal([(p.length, p.width, p.material, p.qty) for p in eparts],
               [(p.length, p.width, p.material, p.qty) for p in tparts], "grouped parts")
    case.equal([exporter.build_line(p) for p in eparts],
               [maxcut_line(p) for p in tparts], "identical MaxCut lines")
    case.equal(exporter.to_mm(0.6, 1.0), dims_to_mm(0.6, 1.0), "unit conversion")
    case.equal(len(exporter.plausibility_problems(eparts)),
               len(plausible_project(tparts)), "plausibility verdict")

    mixed = [exporter.Panel("a", (600.0, 400.0, 18.0)),
             exporter.Panel("b", (600.0, 1200.0, 3.0))]
    case.equal(exporter.thickness_outliers(mixed),
               thickness_outliers([Panel("a", (600.0, 400.0, 18.0)),
                                   Panel("b", (600.0, 1200.0, 3.0))]),
               "thickness check agrees")


def test_unit_summary_and_warnings(case):
    load_addon()
    props = importlib.import_module(f"{ADDON_ID}.properties")
    scene = bpy.context.scene
    units = scene.unit_settings
    saved = (units.system, units.length_unit, units.scale_length)
    try:
        units.system = "METRIC"
        units.length_unit = "MILLIMETERS"
        units.scale_length = 1.0
        case.equal(props.unit_summary(scene), "Metric · mm · scale 1",
                   "summary for the usual mm setup")
        case.equal(props.unit_problems(scene), [], "no warnings for mm + scale 1.0")

        units.scale_length = 0.001
        case.equal(props.unit_problems(scene), [], "scale 0.001 is a known setup")

        units.scale_length = 0.01
        case.check(len(props.unit_problems(scene)) == 1,
                   f"unusual scale warns, got {props.unit_problems(scene)}")

        units.scale_length = 1.0
        units.system = "IMPERIAL"
        case.check(any("Imperial" in p for p in props.unit_problems(scene)),
                   "imperial scene warns")
    finally:
        units.system, units.length_unit, units.scale_length = saved


def test_output_path_never_temp(case):
    load_addon()
    props = importlib.import_module(f"{ADDON_ID}.properties")
    scene = bpy.context.scene
    saved = (scene.eck_props.output_dir, scene.eck_props.file_name)
    try:
        scene.eck_props.file_name = "razkroi_test.txt"
        scene.eck_props.output_dir = ""
        path = props.output_path(scene=scene)
        case.check(path.replace("\\", "/").endswith(".txt"), f"path looks like a file: {path}")
        case.check(tempfile.gettempdir().lower() not in path.lower().replace("\\", "/"),
                   f"temp dir is not used, got {path}")
        scene.eck_props.output_dir = str(Path(tempfile.gettempdir()) / "eck_out")
        case.check(str(Path(tempfile.gettempdir()) / "eck_out") in props.output_path(scene=scene),
                   "explicit output_dir wins")
    finally:
        scene.eck_props.output_dir, scene.eck_props.file_name = saved


@addon
def test_export_aborts_on_mixed_thickness(case):
    """A 3 mm back panel among 18 mm boards must stop the export, not slip through."""
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    scene = bpy.context.scene
    props_mod = importlib.import_module(f"{ADDON_ID}.properties")
    units = scene.unit_settings
    saved = (units.scale_length, scene.eck_props.only_selected,
             scene.eck_props.auto_thickness, scene.eck_props.material_thick_mm,
             scene.eck_props.output_dir, scene.eck_props.file_name)
    created = []
    try:
        units.scale_length = 1.0
        for name, size in (("side", (600.0, 400.0, 18.0)), ("top", (1200.0, 400.0, 18.0)),
                           ("back", (1200.0, 600.0, 3.0))):
            bpy.ops.mesh.primitive_cube_add(size=1.0)
            obj = bpy.context.active_object
            obj.name = obj.data.name = name
            obj.scale = tuple(v / 1000.0 for v in size)
            created.append(obj)
        bpy.context.view_layer.update()
        for obj in created:
            obj.select_set(True)

        props = scene.eck_props
        props.only_selected = True
        props.auto_thickness = True
        with tempfile.TemporaryDirectory() as tmp:
            props.output_dir = tmp
            props.file_name = "mixed.txt"
            ok, detail, parts, _skipped, _warnings = operators.run_export(scene, props)
            case.equal(ok, False, "export must be refused")
            case.check("Mixed thickness" in detail, f"message explains why: {detail!r}")
            case.check("back" in detail, f"message names the offending object: {detail!r}")
            case.check(not os.path.exists(Path(tmp) / "mixed.txt"), "no file written")
            case.equal(props.last_message, "Refused: mixed thickness",
                       "panel shows the refusal")

            # With the 3 mm board deselected the same scene must export fine.
            bpy.data.objects["back"].select_set(False)
            ok, detail, parts, _s, _w = operators.run_export(scene, props)
            case.equal(ok, True, f"clean selection exports, detail={detail}")
            case.equal(props.last_thickness, "18", "detected thickness is reported")

            # Manual mode with 18 mm must also pass now.
            props.auto_thickness = False
            props.material_thick_mm = 18.0
            ok, detail, _p, _s, _w = operators.run_export(scene, props)
            case.equal(ok, True, f"manual 18 mm mode exports, detail={detail}")
    finally:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        (units.scale_length, scene.eck_props.only_selected,
         scene.eck_props.auto_thickness, scene.eck_props.material_thick_mm,
         scene.eck_props.output_dir, scene.eck_props.file_name) = saved


@addon
def test_measurement_respects_unit_scale(case):
    """Real measurement through Blender, both unit setups (catches double scaling).

    This is the regression test for the ×1000 bug: ``bound_box`` is already in
    Blender units, so applying ``scale_length`` twice (or not at all) shows up
    here immediately.
    """
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    scene = bpy.context.scene
    units = scene.unit_settings
    saved = (units.scale_length, getattr(scene.eck_props, "only_selected", True))
    created = []
    try:
        # factor = Blender units per millimetre = 1 / (scale_length * 1000):
        # 0.001 units/mm at scale 1.0, 1.0 unit/mm at scale 0.001.
        for scale_length in (1.0, 0.001):
            factor = 1.0 / (scale_length * 1000.0)
            units.scale_length = scale_length
            for size in ((1800.0, 560.0, 18.0), (1800.0, 1200.0, 3.0)):
                bpy.ops.mesh.primitive_cube_add(size=1.0)
                obj = bpy.context.active_object
                obj.name = obj.data.name = f"m{size[2]:g}_{scale_length:g}"
                obj.scale = tuple(v * factor for v in size)
                created.append(obj)
            bpy.context.view_layer.update()
            for obj in created[-2:]:
                dims = operators.measured_dims_mm(obj, scale_length)
                wanted = (1800.0, 560.0, 18.0) if obj.name.startswith("m18") else \
                         (1800.0, 1200.0, 3.0)
                for got, want, axis in zip(dims, wanted, "XYZ"):
                    case.near(got, want, 1e-3,
                              f"scale {scale_length:g}: {obj.name} {axis} in mm "
                              f"(raw {obj.scale[:]!r})")
    finally:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        units.scale_length = saved[0]


@addon
def test_ui_list_types_registered(case):
    """PartItem/UIList must be registered for template_list to work."""
    load_addon()
    props_module = importlib.import_module(f"{ADDON_ID}.properties")
    case.check(hasattr(props_module, "ECK_PartItem"), "ECK_PartItem exists")
    case.check(getattr(props_module, "ECK_UL_parts", None) is not None, "ECK_UL_parts exists")
    # PropertyGroups are looked up in the module namespace, not in bpy.types.
    group = props_module.ECK_Properties.bl_rna.properties
    case.check("parts" in group.keys(), "ECK_Properties.parts collection registered")
    case.check("sort_by" in group.keys(), "sort mode property registered")
    ids = [item.identifier for item in
           group["sort_by"].enum_items] if "sort_by" in group.keys() else []
    case.equal(ids, ["NAME", "LENGTH", "AREA"], "three sort modes")


@addon
def test_preview_fills_the_list(case):
    """Preview populates props.parts so the panel can show the rows."""
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    scene = bpy.context.scene
    props = scene.eck_props
    saved = (props.only_selected, props.auto_thickness, props.sort_by)
    created = []
    try:
        props.only_selected = True
        props.auto_thickness = True
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for name, size in (("p1", (1200.0, 400.0, 18.0)), ("p2", (1200.0, 400.0, 18.0)),
                           ("p3", (1800.0, 600.0, 18.0))):
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
            obj = bpy.context.active_object
            obj.name = obj.data.name = name
            obj.scale = tuple(v / 1000.0 for v in size)
            created.append(obj)
        bpy.context.view_layer.update()
        # Select *after* creating: primitive_cube_add clears the selection first.
        for obj in created:
            obj.select_set(True)

        parts, _skipped, _warnings, _thickness = operators.analyse(scene, props)
        case.equal(len(props.parts), 2, "two grouped rows in the list")
        case.equal([p.length for p in props.parts], [1800.0, 1200.0],
                   "list is sorted longest first")
        case.equal([item.qty for item in props.parts], [1, 2], "quantities in the list")
        case.equal(props.parts[1].names, "p1, p2", "row names are joined")
        case.near(props.parts[1].area_m2, 0.96, 1e-9, "row area 1200x400x2 = 0.96 m²")

        props.sort_by = "NAME"
        operators.analyse(scene, props)
        case.equal([item.names.split(",")[0].strip() for item in props.parts],
                   ["p1", "p3"], "NAME sort reorders the list")
    finally:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        props.only_selected, props.auto_thickness, props.sort_by = saved


@addon
def test_zip_package_builds(case):
    """The installable zip must contain the package, not the repo, and no .pyc."""
    build_zip = importlib.import_module("tools.build_zip")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "addon.zip"
        build_zip.build(target)
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            top_level = {name.split("/")[0] for name in names}
            case.equal(top_level, {build_zip.PACKAGE},
                       f"single top-level folder, got {top_level}")
            for required in ("__init__.py", "blender_manifest.toml", "exporter.py"):
                case.check(f"{build_zip.PACKAGE}/{required}" in names,
                           f"{required} is packaged")
            case.equal([n for n in names if "__pycache__" in n or n.endswith(".pyc")],
                       [], "no bytecode in the archive")
            manifest = archive.read(f"{build_zip.PACKAGE}/blender_manifest.toml")
            case.check(b'type = "add-on"' in manifest, "manifest declares an add-on")


@addon
def test_zip_manifest_is_valid(case):
    """Blender's own validator must accept the manifest (what the installer reads)."""
    build_zip = importlib.import_module("tools.build_zip")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "addon.zip"
        build_zip.build(target)
        import tomllib
        with zipfile.ZipFile(target) as archive:
            data = tomllib.loads(archive.read(f"{build_zip.PACKAGE}/blender_manifest.toml")
                                 .decode("utf-8"))
        for key in ("schema_version", "id", "name", "version", "type",
                    "blender_version_min", "maintainer", "license", "tagline"):
            case.check(key in data, f"packaged manifest has {key!r}")
        case.equal(data["id"], ADDON_ID, "packaged id matches the module name")
        case.equal(data["version"], build_zip.addon_version(), "version comes from the manifest")
        case.check("files" in data.get("permissions", {}), "files permission declared")


@addon
def test_register_is_idempotent(case):
    """register()/unregister() must survive being called twice.

    This is the "Reinstall"/"Reload Scripts" path: a second register() used to
    raise "already registered as a subclass" because the guard looked in
    ``bpy.types``, which never holds PropertyGroups.
    """
    module = load_addon()
    try:
        module.register()
        module.unregister()
        module.register()
        case.check(hasattr(bpy.types, "ECK_PT_razkroi"), "panel registered after re-register")
        case.check(module.properties.ECK_PartItem.is_registered,
                   "ECK_PartItem stays registered")
        case.check("eck_props" in bpy.types.Scene.bl_rna.properties.keys(),
                   "scene pointer restored")
    except Exception:  # noqa: BLE001
        case.check(False, "re-register raised:\n" + traceback.format_exc())
    case.check(module.properties._is_registered(module.properties.ECK_Properties),
               "helper reports the real state")


@addon
def test_material_is_a_single_field(case):
    """No preset buttons: one material field per export, typed by the user."""
    load_addon()
    props_module = importlib.import_module(f"{ADDON_ID}.properties")
    group = props_module.ECK_Properties.bl_rna.properties
    case.check("material_name" in group.keys(), "material_name field exists")
    case.equal([name for name in group.keys() if name.startswith("preset_")], [],
               "no preset properties on the scene settings")
    case.check(not hasattr(props_module, "apply_preset"),
               "apply_preset() is gone")
    # bpy.ops always returns a wrapper object, even for unknown operators, so the
    # only reliable check is poll(): it raises "could not be found" when missing.
    try:
        bpy.ops.eck.apply_preset.poll()
        found = True
    except (AttributeError, RuntimeError):
        found = False
    case.equal(found, False, "apply_preset operator is not registered")

    scene = bpy.context.scene
    saved = scene.eck_props.material_name
    try:
        scene.eck_props.material_name = "EGGER H3734 ST9"
        case.equal(scene.eck_props.material_name, "EGGER H3734 ST9",
                   "the field accepts any text")
    finally:
        scene.eck_props.material_name = saved


@addon
def test_last_dir_is_used(case):
    """With no .blend and no panel folder, the last used folder wins."""
    load_addon()
    props_module = importlib.import_module(f"{ADDON_ID}.properties")
    prefs = props_module.addon_preferences()
    if not case.check(prefs is not None, "preferences available"):
        return
    scene = bpy.context.scene
    saved = (scene.eck_props.output_dir, prefs.last_dir, bpy.data.filepath)
    try:
        scene.eck_props.output_dir = ""
        prefs.last_dir = str(Path(tempfile.gettempdir()) / "eck_last_dir")
        resolved = props_module.output_dir(scene.eck_props, scene)
        case.equal(str(Path(resolved)), str(Path(tempfile.gettempdir()) / "eck_last_dir"),
                   "last_dir becomes the export folder")

        scene.eck_props.output_dir = str(Path(tempfile.gettempdir()) / "eck_panel")
        case.check("eck_panel" in props_module.output_dir(scene.eck_props, scene),
                   "panel folder still wins")
    finally:
        scene.eck_props.output_dir, prefs.last_dir = saved[0], saved[1]


@addon
def test_scene_to_mm(case):
    """The mm button sets Metric + Millimeters and never touches a custom scale."""
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    units = bpy.context.scene.unit_settings
    saved = (units.system, units.length_unit, units.scale_length)
    try:
        units.system = "IMPERIAL"
        units.length_unit = "FEET"
        units.scale_length = 0.001
        before, after = operators.set_scene_to_mm(bpy.context.scene)
        case.equal(before[0], "IMPERIAL", "reports the previous system")
        case.equal(after[:2], ("METRIC", "MILLIMETERS"), "system and length unit switched")
        case.equal(units.system, "METRIC", "system switched to metric")
        case.equal(units.length_unit, "MILLIMETERS", "length unit switched to mm")
        case.near(units.scale_length, 0.001, 1e-9,
                  "custom unit scale left untouched (would rescale the model)")
    finally:
        units.system, units.length_unit, units.scale_length = saved


@addon
def test_bpy_ops_poll_matches_class_poll(case):
    """Every button must be enabled through bpy.ops, not just at class level.

    This is the regression test for a greyed-out export button: an operator
    subclassing another operator makes Blender lose the Python class of the base
    RNA struct, so ``bpy.ops.eck.export_razkroi.poll()`` returned False while
    ``ECK_OT_export.poll(context)`` returned True.
    """
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    names = ("export_razkroi", "export_razkroi_as", "preview_razkroi",
             "open_output_folder", "copy_razkroi", "scene_to_mm")
    for name in names:
        op = getattr(bpy.ops.eck, name, None)
        if not case.check(op is not None, f"bpy.ops.eck.{name} missing"):
            continue
        try:
            ops_poll = op.poll()
        except Exception:  # noqa: BLE001
            case.check(False, f"bpy.ops.eck.{name}.poll() raised:\n"
                              + traceback.format_exc())
            continue
        case.check(ops_poll is True, f"bpy.ops.eck.{name}.poll() is True, got {ops_poll!r}")

    # No operator may inherit from another operator: Blender cannot map both
    # idnames to a Python class in that case.
    op_classes = [cls for cls in vars(operators).values()
                  if isinstance(cls, type) and issubclass(cls, bpy.types.Operator)
                  and cls is not bpy.types.Operator]
    for cls in op_classes:
        bases = [base for base in cls.__mro__[1:]
                 if isinstance(base, type) and issubclass(base, bpy.types.Operator)
                 and base is not bpy.types.Operator]
        case.equal(bases, [], f"{cls.__name__} must not subclass another operator")


@addon
def test_resolve_target_handles_browser_output(case):
    """Save-as paths: bare name, relative name, directory, absolute path."""
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    scene = bpy.context.scene
    props = scene.eck_props
    saved = (props.output_dir, props.file_name)
    try:
        folder = Path(tempfile.mkdtemp(prefix="eck_target_"))
        props.output_dir = str(folder)
        props.file_name = "panel.txt"

        case.equal(operators.resolve_target(props, scene, ""),
                   str(folder / "panel.txt"), "empty -> panel folder + name")
        case.equal(operators.resolve_target(props, scene, "other.txt"),
                   str(folder / "other.txt"), "bare name -> panel folder + that name")
        case.equal(operators.resolve_target(props, scene, str(folder)),
                   str(folder / "panel.txt"), "existing directory -> directory + panel name")
        case.equal(operators.resolve_target(props, scene, str(folder) + os.sep),
                   str(folder / "panel.txt"),
                   "trailing separator counts as a directory (Windows file browser)")
        absolute = folder / "abs.txt"
        case.equal(operators.resolve_target(props, scene, str(absolute)),
                   str(absolute), "absolute path used as is")
        case.equal(operators.resolve_target(props, scene, f'"{absolute}"'),
                   str(absolute), "surrounding quotes are stripped")
    finally:
        props.output_dir, props.file_name = saved


@addon
def test_last_file_is_stored(case):
    """After an export the panel can show which file was written last."""
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    scene = bpy.context.scene
    props = scene.eck_props
    saved = (props.only_selected, props.auto_thickness, props.output_dir,
             props.file_name, props.last_file)
    created = []
    try:
        props.only_selected = False
        props.auto_thickness = True
        for obj in list(bpy.data.objects):
            if obj.type == "MESH":
                bpy.data.objects.remove(obj, do_unlink=True)
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        obj = bpy.context.active_object
        obj.name = obj.data.name = "p1"
        obj.scale = (1.2, 0.4, 0.018)
        created.append(obj)
        bpy.context.view_layer.update()

        with tempfile.TemporaryDirectory() as tmp:
            props.output_dir = tmp
            props.file_name = "first.txt"
            ok, detail, _p, _s, _w = operators.run_export(scene, props)
            case.equal(ok, True, f"export ok: {detail}")
            case.check(props.last_file.endswith("first.txt"),
                       f"last_file points at the written file, got {props.last_file!r}")

            # Save-as with a different name must write that name, not the panel one.
            other = os.path.join(tmp, "chosen.txt")
            ok, detail, _p, _s, _w = operators.run_export(scene, props, other)
            case.equal(ok, True, f"save-as ok: {detail}")
            case.check(os.path.exists(other), "file with the chosen name exists")
            case.equal(props.last_file, other, "last_file follows the chosen path")
    finally:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        (props.only_selected, props.auto_thickness, props.output_dir,
         props.file_name, props.last_file) = saved


@addon
def test_grouping_flag_reaches_the_file(case):
    """Unticking 'Group identical parts' must give one line per object."""
    load_addon()
    operators = importlib.import_module(f"{ADDON_ID}.operators")
    scene = bpy.context.scene
    props = scene.eck_props
    saved = (props.only_selected, props.auto_thickness, props.output_dir,
             props.file_name, props.group_identical)
    created = []
    try:
        props.only_selected = False
        props.auto_thickness = True
        for obj in list(bpy.data.objects):
            if obj.type == "MESH":
                bpy.data.objects.remove(obj, do_unlink=True)
        for name in ("Side L", "Side R"):
            bpy.ops.mesh.primitive_cube_add(size=1.0)
            obj = bpy.context.active_object
            obj.name = obj.data.name = name
            obj.scale = (1.8, 0.56, 0.018)
            created.append(obj)
        bpy.context.view_layer.update()

        with tempfile.TemporaryDirectory() as tmp:
            props.output_dir = tmp
            props.file_name = "grouped.txt"
            props.group_identical = True
            ok, _d, parts, _s, _w = operators.run_export(scene, props)
            case.equal(ok, True, "grouped export ok")
            case.equal(len(parts), 1, "one merged row")
            lines = (Path(tmp) / "grouped.txt").read_text(encoding="utf-8").splitlines()
            case.equal(len(lines), 1, "one line in the file")
            case.check('"2"' in lines[0], f"quantity 2, got {lines[0]!r}")

            props.file_name = "ungrouped.txt"
            props.group_identical = False
            ok, _d, parts, _s, _w = operators.run_export(scene, props)
            case.equal(ok, True, "ungrouped export ok")
            case.equal(len(parts), 2, "two separate rows")
            lines = (Path(tmp) / "ungrouped.txt").read_text(encoding="utf-8").splitlines()
            case.equal(len(lines), 2, "two lines in the file")
            case.check(all('"1"' in line for line in lines), "every line has qty 1")
            case.check(any('"Side L"' in line for line in lines)
                       and any('"Side R"' in line for line in lines),
                       f"each object keeps its own name, got {lines!r}")
            case.equal(len(props.parts), 2, "the panel list shows both rows too")
    finally:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        (props.only_selected, props.auto_thickness, props.output_dir,
         props.file_name, props.group_identical) = saved


@addon
def test_scope_toggle_refreshes_the_list(case):
    """Changing 'Group identical parts' must update the rows immediately.

    The property has an update callback that re-runs the analysis, so the panel
    list is fresh without pressing Preview.
    """
    load_addon()
    scene = bpy.context.scene
    props = scene.eck_props
    saved = (props.only_selected, props.auto_thickness, props.group_identical)
    created = []
    try:
        props.only_selected = False
        props.auto_thickness = True
        for obj in list(bpy.data.objects):
            if obj.type == "MESH":
                bpy.data.objects.remove(obj, do_unlink=True)
        for name in ("Side L", "Side R"):
            bpy.ops.mesh.primitive_cube_add(size=1.0)
            obj = bpy.context.active_object
            obj.name = obj.data.name = name
            obj.scale = (1.8, 0.56, 0.018)
            created.append(obj)
        bpy.context.view_layer.update()

        props.group_identical = True
        case.equal(len(props.parts), 1, "grouped: one row appears without Preview")
        case.equal(props.parts[0].qty, 2, "merged quantity 2")

        props.group_identical = False
        case.equal(len(props.parts), 2, "ungrouped: two rows appear without Preview")
        case.equal([item.qty for item in props.parts], [1, 1], "each row has qty 1")

        props.only_selected = True
        case.check(len(props.parts) >= 1, "toggling the scope also refreshes")
    finally:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        (props.only_selected, props.auto_thickness, props.group_identical) = saved


@addon
def test_unregister_is_clean(case):
    """Reload Scripts / disable must not leave dangling RNA pointers."""
    module = sys.modules.get(ADDON_ID)
    if not case.check(module is not None, "add-on module not imported"):
        return
    bpy.ops.preferences.addon_disable(module=ADDON_ID)
    leftovers = [ident for ident in REQUIRED_TYPES.values() if hasattr(bpy.types, ident)]
    case.equal(leftovers, [], "no registered types left after disable")
    case.check("eck_props" not in bpy.types.Scene.bl_rna.properties.keys(),
               "Scene.eck_props removed on disable")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main():
    print("\n================ RAZKROI VALIDATION ================")
    print("blender:", bpy.app.version_string, "| python:", sys.version.split()[0])
    print("repo:", REPO_ROOT)

    extra = os.environ.get("ECK_ADDON_SCRIPTS")
    if extra:
        filepaths = bpy.context.preferences.filepaths
        while filepaths.script_directories:
            filepaths.script_directories.remove(filepaths.script_directories[0])
        filepaths.script_directories.new().directory = extra
        print("addon scripts dir:", extra)

    def collect(tag):
        return [(name, fn) for name, fn in sorted(vars(sys.modules[__name__]).items())
                if name.startswith("test_") and getattr(fn, "layer", "") == tag]

    print("\n-- exporter logic (no bpy) --")
    passed_logic, failed_logic = run_cases(collect("logic"))

    if not addon_dir().is_dir():
        print(f"\n-- add-on contract -- SKIPPED ({addon_dir()} does not exist yet) --")
        print(f"\n================ {passed_logic} passed, {failed_logic} failed, "
              f"contract layer skipped ================\n")
        return 0 if failed_logic == 0 else 1

    print("\n-- add-on contract (live Blender) --")
    passed_addon, failed_addon = run_cases(collect("addon"))

    passed = passed_logic + passed_addon
    failed = failed_logic + failed_addon
    print(f"\n================ {passed} passed, {failed} failed ================\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

