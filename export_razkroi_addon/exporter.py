"""Pure cutting-list logic: Blender dimensions -> MaxCut rows.

No ``bpy`` import on purpose: everything here works on plain data, so it can be
tested without Blender and reused by an eventual --background run.

Units rule (measured on 5.2.3, see tools/probe_units.py):
    mm = raw_blender_units * 1000 * scene.unit_settings.scale_length

``length_unit`` (Meters/Millimeters in the UI) changes only the display, so it is
deliberately *not* used here. ``scale_length`` does change what 1.0 means, hence
the multiplication: with scale_length=0.001 a panel is modelled as 600 units and
with scale_length=1.0 as 0.6 units, and both must export as 600 mm.
"""

from dataclasses import dataclass, field

MM_PER_UNIT = 1000.0

# Maximum length of the joined object-name field (MaxCut truncates silently).
MAX_NAME_CHARS = 120

# Plausibility ranges for a furniture cutting list, in millimetres
# (matches the specification pinned by tools/run_tests.py).
SIDE_RANGE_MM = (8.0, 2800.0)
# Total board area of one export, in m². A whole kitchen fits comfortably below
# this; anything above means a unit-scale mistake, not a big project.
AREA_RANGE_M2 = (0.01, 500.0)

# A side shorter than this is degenerate geometry, not a board. Kept in one place
# because both the measuring (export_to_mm callers) and the skipping decide with it.
MIN_SIDE_MM = 1e-4

# First column of every MaxCut line: the part type MaxCut expects for a panel.
MAXCUT_PART_TYPE = "Input Panel"

# Written when the user has not typed a material. Deliberately generic: the add-on
# must never ship somebody's real board name as its default (MaxCut shows this
# string on the sheet, so it has to be recognisable when it slips through).
FALLBACK_MATERIAL = "Unnamed material"



@dataclass
class Panel:
    """Blender-independent view of one selected object."""

    name: str
    dims_mm: tuple = (0.0, 0.0, 0.0)  # evaluated world-axis bounding box, in mm
    material: str = ""


@dataclass
class Part:
    """One row of the cutting list."""

    names: list = field(default_factory=list)
    length: float = 0.0
    width: float = 0.0
    material: str = ""
    qty: int = 0


def to_mm(raw_units, scale_length):
    """Convert raw Blender units to millimetres.

    ``scale_length`` is how many metres one Blender unit represents, so one unit
    is ``scale_length * 1000`` millimetres. Measured on 5.2.3:

    * scale 1.0  (default, model in metres):  1 unit = 1000 mm -> x1000
    * scale 0.001 ("I model in millimetres"): 1 unit = 1 mm    -> x1

    Both factors are kept separate on purpose: chaining
    ``raw * scale_length * 1000`` lets the tiny scale and the 1000 collapse and
    silently returns the raw value (measured on Blender 5.2.3).
    """
    mm_per_unit = float(scale_length) * MM_PER_UNIT
    return float(raw_units) * mm_per_unit


def panel_thickness(panel):
    """Thickness of a panel = its thinnest side, in mm."""
    return min(float(v) for v in panel.dims_mm)


def common_thickness(panels):
    """Thickness of the selection, in mm (None for an empty selection).

    With an odd count the majority wins. On a tie the *thicker* value is used:
    a stray 3 mm back panel next to 18 mm body parts must be reported as the
    outlier, not the other way round.
    """
    counts = {}
    for panel in panels:
        key = round(panel_thickness(panel), 1)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def thickness_outliers(panels, tolerance=0.5, expected=None):
    """Return ``(expected_mm, [(name, thickness_mm), ...])`` for mixed selections.

    ``expected`` overrides the auto-detected majority (manual thickness mode).
    Outliers are only reported - the caller decides whether to abort.
    """
    if not panels:
        return expected, []
    target = expected if expected is not None else common_thickness(panels)
    outliers = []
    for panel in panels:
        thickness = panel_thickness(panel)
        if abs(thickness - target) > tolerance:
            outliers.append((panel.name, round(thickness, 1)))
    return target, outliers


def measure(panel):
    """Return ``(length, width)`` in mm, the two long sides of the board."""
    a, b, c = (round(float(v), 3) for v in panel.dims_mm)
    if min(a, b, c) <= MIN_SIDE_MM:
        return None, None
    sides = sorted((a, b, c))
    length, width = sides[2], sides[1]
    return length, width


def group(panels, material="", group_identical=True):
    """Build cutting-list rows from panels.

    With ``group_identical`` (default) equal ``(length, width)`` panels become one
    row with a quantity, which is what the MaxCut import expects. Turn it off to
    get one row per object - useful when the same size needs different edge
    banding in MaxCut, because MaxCut then cannot merge them back into one item.

    The material is not part of the key: the user exports one material at a time
    and types it into the panel, exactly like the original script did.
    Returns ``(parts, skipped)``; ``skipped`` holds names of unmeasurable objects.
    """
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


def sort_parts(parts, sort_by="LENGTH"):
    """Order the cutting-list rows (TODO.md §5: stable, predictable output).

    ``LENGTH`` (default) goes longest first, then widest, so the biggest boards
    end up on top and two exports of the same scene can be diffed. ``AREA`` uses
    the row area including quantity, ``NAME`` is alphabetical on the first object.
    """
    if sort_by == "NAME":
        return sorted(parts, key=lambda p: (", ".join(p.names).lower(), -p.length))
    if sort_by == "AREA":
        return sorted(parts, key=lambda p: (-p.length * p.width * p.qty, -p.length))
    return sorted(parts, key=lambda p: (-p.length, -p.width, ", ".join(p.names).lower()))


def format_number(value):
    """Format a millimetre value the way MaxCut expects: 600 or 12.5."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def quote(value):
    """Wrap a field in double quotes, escaping embedded quotes."""
    return '"' + str(value).replace('"', '""') + '"'


def join_names(names, max_chars=MAX_NAME_CHARS):
    """Join object names with ", " and truncate to MaxCut's field length."""
    text = ", ".join(names)
    if len(text) <= max_chars:
        return text
    suffix = f" (+{len(names) - 1} more)"
    room = max_chars - len(suffix)
    return text[:room].rstrip(", ") + suffix if room > 0 else text[:max_chars]


def build_line(part, delimiter=";"):
    """Build one MaxCut import line.

    Column order matches the script this add-on replaces:
    ``"Input Panel"; name; length; width; qty; edge1; edge2; material``

    An empty material falls back to ``FALLBACK_MATERIAL`` instead of writing an
    empty field, so a forgotten material is visible on the MaxCut sheet rather
    than silently imported as a blank.
    """
    fields = (
        MAXCUT_PART_TYPE,
        join_names(part.names),
        format_number(part.length),
        format_number(part.width),
        str(part.qty),
        "",  # edge banding, reserved: set manually in MaxCut
        "",
        part.material or FALLBACK_MATERIAL,
    )
    return delimiter.join(quote(field) for field in fields)


def build_lines(parts, delimiter=";"):
    """Render every part as one text line."""
    return [build_line(part, delimiter) for part in parts]


def total_area_m2(parts):
    """Total board area of the list, in square metres."""
    return sum(p.length * p.width * p.qty for p in parts) / 1_000_000.0


def plausibility_problems(parts):
    """Warn when the numbers cannot be furniture dimensions.

    Catches the classic mistakes: a forgotten ``scale_length`` (600 mm shows up as
    600 000 mm) and a model built in metres while the add-on expects millimetres.
    """
    problems = []
    for part in parts:
        label = join_names(part.names, max_chars=40)
        side_min = min(part.length, part.width)
        side_max = max(part.length, part.width)
        if side_min < SIDE_RANGE_MM[0]:
            problems.append(f"{label}: {format_number(side_min)} mm is too small, "
                            f"check the scene unit scale")
        if side_max > SIDE_RANGE_MM[1]:
            problems.append(f"{label}: {format_number(side_max)} mm is too large, "
                            f"check the scene unit scale")
    area = total_area_m2(parts)
    if parts and not AREA_RANGE_M2[0] <= area <= AREA_RANGE_M2[1]:
        problems.append(f"total area {area:.2f} m² looks wrong")
    return problems
