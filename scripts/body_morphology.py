"""Body morphology via bone-scale sliders -- the SAME technique GTA's own MP
Freemode character creator uses for body sliders (not a free-form mesh
sculpt/Lattice system): named sliders map to specific skeleton bones AND a
specific LOCAL AXIS of that bone, and scaling via pose-mode + baking the
Armature modifier reshapes the skinned mesh proportionally while leaving
topology/vertex weights untouched -- critical, since it means existing
clothing components stay validly skinned to the SAME skeleton (see
MORPH_SLIDERS docstring below for the real limitation this doesn't solve:
clothing meshes authored for the default proportions will still clip if a
slider pushes far from 1.0).

PER-AXIS PRECISION (added 2026-10-12, see docs/architecture-decisions.md):
each bone's local X/Y/Z axes do NOT consistently map to world
width/depth/height across different bones -- confirmed empirically against
the real templates/template_mp_f_freemode_01_mp_f_bikerdlc_01.blend asset
(a form-fitting MP Freemode outfit mesh, chosen specifically because the
earlier business_02 test ped's rigid blazer barely responded to ANY breast
bone scaling at all -- a structured jacket legitimately shouldn't show
much). Measured per-axis effect on that mesh's real world bounding box
(2x/1.5x test scale):
  SPR_L/R_Breast local X -> world X (+8%, "bust_width")
  SPR_L/R_Breast local Y -> world Y (+12%, "bust_projection")
  SPR_L/R_Breast local Z -> world Z (+15%, cleanest single-axis "bust_size")
  SKEL_Pelvis    local Y -> world Y (+12%, "hip_depth")
  SKEL_Pelvis    local Z -> world X (+12%, "hip_width" -- note the axis
                                      swap vs breast, each bone has its own
                                      local orientation, do NOT assume a
                                      consistent axis<->direction mapping
                                      across different bones)
  SKEL_Pelvis    local X -> world Z (+0.4%, negligible, not exposed)
No dedicated glute/buttock bone exists in this skeleton (confirmed: only
SPR_L/R_Breast are dedicated "soft prop" bones) -- "hip_width" via
SKEL_Pelvis is the closest available lever, but it is NOT an isolated
glute control: it also shifts the thigh attachment point and general
hip/waist region together, since SKEL_Pelvis parents SKEL_L/R_Thigh. A
truly isolated glute slider would need a vertex-group-based regional
deformation (e.g. a Lattice bound to a bounding-box-selected vertex group),
not a bone-scale lever -- out of scope here, flagged as a known gap.

This module is split into a PURE part (no bpy -- slider validation,
clamping, and resolving which bones+axes each slider touches, with
multiple sliders affecting the same bone+axis multiplying together) and a
bpy-dependent part (apply_body_morphology, actually touches the Blender
scene). Only the pure part is unit-testable without a real Blender
process; the bpy part was verified for real against
templates/template_mp_f_freemode_01_mp_f_bikerdlc_01.blend on 2026-10-12.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

# Real bone names confirmed against the actual GTA V human skeleton
# (104 bones, shared by every ped -- verified against both
# template_female_a_f_y_business_02.blend and
# template_mp_f_freemode_01_mp_f_bikerdlc_01.blend, 2026-10-12). Each
# slider maps to {bone_name: (x_mask, y_mask, z_mask)} -- the mask marks
# which of the BONE'S OWN local axes this slider scales (1 = scaled by the
# slider's value, 0 = left at 1.0 / untouched). See the module docstring
# for the empirically-measured per-axis effect of each entry below.
#
# This is a DELIBERATELY SMALL initial vocabulary -- not every bone is
# exposed as a slider, only ones that produce a recognizable, generally-
# safe body-shape change without immediately breaking the silhouette.
MORPH_SLIDERS: dict[str, dict[str, tuple[float, float, float]]] = {
    "height": {"SKEL_ROOT": (1, 1, 1)},
    "shoulder_width": {"SKEL_L_Clavicle": (1, 1, 1), "SKEL_R_Clavicle": (1, 1, 1)},
    "torso_bulk": {
        "SKEL_Spine0": (1, 1, 1), "SKEL_Spine1": (1, 1, 1),
        "SKEL_Spine2": (1, 1, 1), "SKEL_Spine3": (1, 1, 1),
    },
    "leg_length": {
        "SKEL_L_Thigh": (1, 1, 1), "SKEL_R_Thigh": (1, 1, 1),
        "SKEL_L_Calf": (1, 1, 1), "SKEL_R_Calf": (1, 1, 1),
    },
    "arm_bulk": {
        "SKEL_L_UpperArm": (1, 1, 1), "SKEL_R_UpperArm": (1, 1, 1),
        "SKEL_L_Forearm": (1, 1, 1), "SKEL_R_Forearm": (1, 1, 1),
    },
    # Precise, isolated bust control (replaces the old uniform "bust_size").
    "bust_width": {"SPR_L_Breast": (1, 0, 0), "SPR_R_Breast": (1, 0, 0)},
    "bust_projection": {"SPR_L_Breast": (0, 1, 0), "SPR_R_Breast": (0, 1, 0)},
    "bust_size": {"SPR_L_Breast": (0, 0, 1), "SPR_R_Breast": (0, 0, 1)},
    # Hip/waist control via the pelvis -- see module docstring: NOT an
    # isolated glute control, the closest available lever without a
    # vertex-group-based regional system (no dedicated glute bone exists).
    "hip_width": {"SKEL_Pelvis": (0, 0, 1)},
    "hip_depth": {"SKEL_Pelvis": (0, 1, 0)},
}

# Regional fallback for bones a body mesh isn't weighted to. Some custom
# bodies skin the breasts to SKEL_Spine3 with no SPR_*_Breast weights (real
# case: CatalinaNude's 100D body), so scaling the breast bone moves nothing.
# For these bones the same per-axis scale (in the bone's own axes) is
# applied directly to the mesh vertices around an anchor taken from the
# MESH, not the bone: measured on that body, the breast apex sits ~5 cm
# above the bone head (a bigger custom bust than the skeleton was placed
# for), so the bone position doesn't describe the breast. See
# bust_region_anchor(). Full effect within `inner` meters of the anchor,
# smooth fade to none at `outer` (mound measured ~12 cm tall, apex 4.5 cm
# in front of the sternum plane; no arm vertex within 15 cm).
REGIONAL_FALLBACK_RADII: dict[str, tuple[float, float]] = {
    "SPR_L_Breast": (0.06, 0.11),
    "SPR_R_Breast": (0.06, 0.11),
}

# Clamp range for every slider value -- scaling a bone much past this tends
# to visibly break the mesh (self-intersection, skin stretching artifacts)
# even with otherwise-correct weights; found by inspection, not a hard
# engine limit. 1.0 = unchanged.
SLIDER_MIN = 0.6
SLIDER_MAX = 1.6


def _clamp(value: float) -> float:
    return max(SLIDER_MIN, min(SLIDER_MAX, value))


def build_pose_bone_scale_map(slider_values: dict[str, float]) -> dict[str, tuple[float, float, float]]:
    """Pure logic (no bpy): resolves a dict of {slider_name: value} into a
    dict of {bone_name: (x_scale, y_scale, z_scale)}, used by
    apply_body_morphology().

    - Unknown slider names are skipped with a warning (not a crash) --
      e.g. if an LLM-driven caller hallucinates a slider name outside
      MORPH_SLIDERS.
    - Each slider's value is clamped to [SLIDER_MIN, SLIDER_MAX] before use.
    - A slider only scales the axes marked 1 in its MORPH_SLIDERS entry;
      masked-out axes contribute a factor of 1.0 (no-op) for that slider.
    - If two sliders affect the SAME axis of the SAME bone (e.g. a future
      slider addition overlapping an existing one), their scales MULTIPLY
      rather than overwrite, so the caller never silently loses one
      slider's effect to another.
    """
    bone_scales: dict[str, list[float]] = {}
    for slider_name, raw_value in slider_values.items():
        bone_axis_masks = MORPH_SLIDERS.get(slider_name)
        if bone_axis_masks is None:
            logger.warning("unknown body morphology slider %r, skipped", slider_name)
            continue
        value = _clamp(raw_value)
        for bone_name, mask in bone_axis_masks.items():
            current = bone_scales.setdefault(bone_name, [1.0, 1.0, 1.0])
            for axis in range(3):
                if mask[axis]:
                    current[axis] *= value
    return {bone: (scale[0], scale[1], scale[2]) for bone, scale in bone_scales.items()}


def parse_slider_spec(spec: str) -> dict[str, float]:
    """Pure logic (no bpy): parses a CLI slider spec like
    "bust_size=1.2,hip_width=1.1" into {slider_name: value}. Malformed
    entries raise ValueError (a typo on the command line must fail loudly,
    not silently produce an unmorphed ped). Unknown slider NAMES are left
    to build_pose_bone_scale_map(), which warns and skips them.
    """
    sliders: dict[str, float] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, value = entry.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"invalid slider entry {entry!r}, expected name=value")
        sliders[name.strip()] = float(value)
    return sliders


def bust_apex(points, bone_head, forward, search_radius=0.15):
    """Pure logic (no bpy): the most-forward point (along unit `forward`)
    within `search_radius` of `bone_head` on the bone's side of the midline
    (x = 0, GTA peds are symmetric about it) -- the breast apex. None if no
    point qualifies."""
    side = 1.0 if bone_head[0] >= 0 else -1.0
    r_sq = search_radius * search_radius
    candidates = [p for p in points
                  if p[0] * side > 0.01 and sum((p[i] - bone_head[i]) ** 2 for i in range(3)) < r_sq]
    if not candidates:
        return None
    return max(candidates, key=lambda p: _dot(p, forward))


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def bust_region_anchor(points, bone_head, forward, search_radius=0.15):
    """Pure logic (no bpy): the scaling anchor for one breast, from the body
    mesh's own vertices (world xyz tuples): the bust_apex() pushed back
    along `forward` (unit vector) to the depth of the sternum -- the
    front-most midline vertex at the apex's height. Scaling around it then
    shrinks/grows the breast toward the chest wall while the sternum stays
    put. Returns None if no apex or no midline vertex is found (not a
    breast region on this mesh)."""
    apex = bust_apex(points, bone_head, forward, search_radius)
    if apex is None:
        return None
    midline = [p for p in points if abs(p[0]) < 0.01 and abs(p[2] - apex[2]) < 0.02]
    if not midline:
        return None
    back = _dot(apex, forward) - max(_dot(p, forward) for p in midline)
    return tuple(apex[i] - back * forward[i] for i in range(3))


def regional_scale_points(points, center, axes, scale, inner, outer):
    """Pure logic (no bpy): scale `points` (world xyz tuples) around
    `center` along the three unit vectors `axes` (the bone's local X/Y/Z in
    world space) by `scale` (x, y, z): fully within `inner` of the center,
    fading smoothly to no change at `outer` -- falloff (1 - t^2)^2 with
    t = (d - inner) / (outer - inner), zero slope at `outer` so no crease
    forms there. Points at or beyond `outer` are returned unchanged."""
    out = []
    for p in points:
        offset = [p[i] - center[i] for i in range(3)]
        dist = math.sqrt(sum(c * c for c in offset))
        if dist >= outer:
            out.append(tuple(p))
            continue
        t = max(0.0, (dist - inner) / (outer - inner))
        weight = (1.0 - t * t) ** 2
        new = list(center)
        for axis, s in zip(axes, scale):
            along = sum(offset[i] * axis[i] for i in range(3))
            factor = 1.0 + (s - 1.0) * weight
            for i in range(3):
                new[i] += along * factor * axis[i]
        out.append(tuple(new))
    return out


# --- Chest measurement & bra-size fitting -----------------------------------
# Measured like a tape: the tape around a horizontal torso slice follows the
# slice's CONVEX HULL (it bridges between the breasts), so circumference =
# hull perimeter of the slice's (x, y). Units: GTA/Blender meters.

def hull_perimeter(xy) -> float:
    """Perimeter of the convex hull of 2D points (Andrew's monotone chain)."""
    pts = sorted(set(xy))
    if len(pts) < 3:
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return sum(math.dist(hull[i], hull[(i + 1) % len(hull)]) for i in range(len(hull)))


def section_xy(points, edges, z) -> list[tuple[float, float]]:
    """Pure logic (no bpy): the mesh's cross-section by the horizontal plane
    `z` -- (x, y) of every edge/plane intersection (`edges`: index pairs into
    `points`). Exact on any mesh density; a vertex band (the first version)
    caught 2 to 2000 vertices per slice on the coarse lowr mesh."""
    out = []
    for a, b in edges:
        pa, pb = points[a], points[b]
        da, db = pa[2] - z, pb[2] - z
        if da == db or da * db > 0:
            continue
        t = da / (da - db)
        out.append((pa[0] + t * (pb[0] - pa[0]), pa[1] + t * (pb[1] - pa[1])))
    return out


def slice_circumference(points, edges, z) -> float:
    return hull_perimeter(section_xy(points, edges, z))


def slice_extent(points, edges, z, axis) -> float | None:
    """Farthest reach of the section at `z` along horizontal unit `axis`
    (x, y[, z]) -- e.g. the torso front (axis = forward) or back."""
    reach = [x * axis[0] + y * axis[1] for x, y in section_xy(points, edges, z)]
    return max(reach) if reach else None


def slice_depth(points, edges, z, axis) -> float | None:
    """Front-to-back depth of the section at `z` along `axis` (profile view)."""
    reach = [x * axis[0] + y * axis[1] for x, y in section_xy(points, edges, z)]
    return max(reach) - min(reach) if reach else None


def fold_height(points, edges, apex_z, axis, step=0.01, min_drop=0.015, flat=0.003, reach=0.15):
    """Pure logic (no bpy): height of the fold below a bulge whose most
    prominent point along `axis` is at `apex_z` (axis = forward: the
    inframammary fold below the bust; axis = backward: the gluteal fold
    below the buttocks). Walking down `step` at a time, the surface first
    recedes quickly off the bulge, then flattens: the fold is the first
    height, after a drop of at least `min_drop`, where the next step down
    moves the surface by less than `flat` (CatalinaNude bust: apex 18.5 cm,
    fold at 14.4 cm / z 0.262, then 14.2, 14.2, 14.0...). None if no fold
    within `reach`."""
    apex = slice_extent(points, edges, apex_z, axis)
    if apex is None:
        return None
    z = apex_z
    while z > apex_z - reach:
        here = slice_extent(points, edges, z, axis)
        below = slice_extent(points, edges, z - step, axis)
        if here is not None and below is not None and apex - here >= min_drop and here - below < flat:
            return z
        z -= step
    return None


# EU (EN 13402) cup = bust minus underbust circumference, in cm.
EU_CUP_DIFF_CM: dict[str, tuple[float, float]] = {
    "AA": (10, 12), "A": (12, 14), "B": (14, 16), "C": (16, 18), "D": (18, 20),
    "E": (20, 22), "F": (22, 24), "G": (24, 26), "H": (26, 28),
}
BRA_SIZE_RE = re.compile(r"^\s*(\d{2,3})?\s*(AA|[A-H])\s*$", re.IGNORECASE)


def parse_bra_size(spec: str) -> tuple[int | None, str]:
    """"80A" -> (80, "A"); "A" -> (None, "A") (keep the body's own band).
    ValueError on anything else."""
    match = BRA_SIZE_RE.match(spec or "")
    if not match:
        raise ValueError(f"invalid EU bra size {spec!r}, expected e.g. '80A' or 'B'")
    band = int(match.group(1)) if match.group(1) else None
    return band, match.group(2).upper()


def bust_target_cm(spec: str, underbust_cm: float) -> float:
    """Target bust circumference for EU size `spec` on a body whose MEASURED
    underbust is `underbust_cm`: underbust + middle of the cup's range. The
    measured underbust is used (not the nominal band) so the cup difference
    is exact on this body; changing the band itself isn't supported (it is
    a ribcage change, see bra_band_mismatch())."""
    _band, cup = parse_bra_size(spec)
    low, high = EU_CUP_DIFF_CM[cup]
    return underbust_cm + (low + high) / 2


def bra_band_mismatch(spec: str, underbust_cm: float) -> bool:
    """True if `spec` names a band more than half a band step (2.5 cm) away
    from the measured underbust -- that part of the request can't be met."""
    band, _cup = parse_bra_size(spec)
    return band is not None and abs(band - underbust_cm) > 2.5


def bra_size_label(underbust_cm: float, bust_cm: float) -> str:
    """Nearest EU size for measured circumferences, e.g. (80.5, 105.3) -> "80G"."""
    band = 5 * round(underbust_cm / 5)
    diff = bust_cm - underbust_cm
    cups = list(EU_CUP_DIFF_CM.items())
    if diff < cups[0][1][0]:
        return f"{band}<AA"
    if diff >= cups[-1][1][1]:
        return f"{band}>H"
    return f"{band}{next(cup for cup, (low, high) in cups if low <= diff < high)}"


def fit_monotonic(f, target, lo, hi, tol, max_iter=20):
    """Bisection for an INCREASING f on [lo, hi]: returns (x, f(x), reached).
    If target is outside [f(lo), f(hi)] the closest bound is returned with
    reached=False -- the caller reports it instead of pretending."""
    f_lo, f_hi = f(lo), f(hi)
    if target <= f_lo:
        return lo, f_lo, abs(f_lo - target) <= tol
    if target >= f_hi:
        return hi, f_hi, abs(f_hi - target) <= tol
    x, value = (lo + hi) / 2, None
    for _ in range(max_iter):
        x = (lo + hi) / 2
        value = f(x)
        if abs(value - target) <= tol:
            return x, value, True
        if value < target:
            lo = x
        else:
            hi = x
    return x, value, False


# "Excess over a wall" deformation, shared by the bust and glute fits. A
# bulge = the radial excess of the surface over a wall profile taken from a
# MEASURED slice (radius per angle around that slice's center): the
# underbust slice (ribcage) for the bust, the waist slice for the glutes.
# Only that excess is scaled, so the wall itself never moves. Why not the
# anchor-sphere regional scale (point 32): on CatalinaNude (80G) the breast
# mass wraps to the sides (x = +/-0.16 m, behind the sternum plane), and
# scaling in x/z also squeezed the ribcage (underbust 80.4 -> 72.4 cm).
# The excess is scaled per LOBE (a direction + extent, see Lobe): one lobe
# for the bust, a posterior one (projection) and two lateral ones (hip
# width) for the glutes. Each vertex is also weighted by its skin weight on
# the region's bones (arms/hands: 0, so they never move).
WALL_BINS = 72                  # 5 degree angle bins for the wall profile
# Bust: no fade-in above the fold. The excess over the sloped ribcage wall
# (fit_bust) is already ~0 on the fold itself; a 2 cm fade weakened the
# hanging underside of an 80G (z fold..+3 cm) and left a horizontal shelf
# under an 80A. A long fade above the apex: with
# 0.07 an 80A left the upper chest (z +3..+5 cm) 1.2 cm prouder than the
# flattened bust. Harmless higher up: from the upper chest on the front
# recedes inside the underbust wall, so its excess is 0 (CatalinaNude:
# circumferences at z 0.43/0.46 unchanged).
BUST_FOLD_FADE = 0.0
ABOVE_APEX_FADE = 0.13
BUST_SECTOR = (80.0, 120.0)     # degrees from forward: full effect / none (back untouched)


@dataclass(frozen=True)
class Lobe:
    """Where an excess_scale() lobe acts. Vertically: nothing below
    `bottom_z`, smooth fade-in up to `full_z`, full effect up to `apex_z`,
    then (1-t^2)^2 fade-out to `top_z`. Angularly: full effect within
    `sector[0]` degrees of `axis_angle` (radians, xy), none beyond
    `sector[1]`."""
    axis_angle: float
    sector: tuple[float, float]
    bottom_z: float
    full_z: float
    apex_z: float
    top_z: float

    def weight(self, z, theta) -> float:
        if z < self.full_z:
            span = self.full_z - self.bottom_z
            u = (z - self.bottom_z) / span if span > 0 else 0.0  # below bottom_z: nothing
            height = 0.0 if u <= 0 else u * u * (3 - 2 * u)
        elif z <= self.apex_z:
            height = 1.0
        else:
            height = _smooth_fade((z - self.apex_z) / (self.top_z - self.apex_z))
        if height <= 0:
            return 0.0
        off = math.degrees(abs((theta - self.axis_angle + math.pi) % (2 * math.pi) - math.pi))
        full_deg, zero_deg = self.sector
        return height * _smooth_fade((off - full_deg) / (zero_deg - full_deg))


def wall_profile(slice_xy, center, bins=WALL_BINS):
    """Pure logic (no bpy): outer radius per angle bin (around `center`) of a
    horizontal section's (x, y) points. Empty bins are filled from the
    nearest non-empty ones."""
    radii: list[float | None] = [None] * bins
    for p in slice_xy:
        theta, r = _polar(p, center)
        i = int((theta + math.pi) / (2 * math.pi) * bins) % bins
        current = radii[i]
        radii[i] = r if current is None else max(current, r)
    known = [i for i, r in enumerate(radii) if r is not None]
    if not known:
        raise ValueError("empty wall slice")
    for i in range(bins):
        if radii[i] is None:
            nearest = min(known, key=lambda k: min(abs(k - i), bins - abs(k - i)))
            radii[i] = radii[nearest]
    return radii


def _polar(p, center):
    dx, dy = p[0] - center[0], p[1] - center[1]
    return math.atan2(dy, dx), math.hypot(dx, dy)


def _wall_radius(wall, theta):
    bins = len(wall)
    pos = (theta + math.pi) / (2 * math.pi) * bins - 0.5
    i = math.floor(pos)
    frac = pos - i
    return wall[i % bins] * (1 - frac) + wall[(i + 1) % bins] * frac


def _wall_radius_at(wall, upper_wall, theta, z):
    """Wall radius at (theta, z): `wall` alone is a vertical cylinder;
    `upper_wall` = (z0, z1, radii) slopes it linearly from `wall` at z0 to
    `radii` at z1 (constant beyond both ends)."""
    r = _wall_radius(wall, theta)
    if upper_wall is None:
        return r
    z0, z1, upper = upper_wall
    t = min(1.0, max(0.0, (z - z0) / (z1 - z0)))
    return r + (_wall_radius(upper, theta) - r) * t if t else r


def _smooth_fade(t):
    """1 at t <= 0, 0 at t >= 1, smooth (1-t^2)^2 in between."""
    return 1.0 if t <= 0 else 0.0 if t >= 1 else (1 - t * t) ** 2


def excess_scale(points, weights, center, wall, lobes, upper_wall=None):
    """Pure logic (no bpy): scale each point's radial excess over `wall`
    (wall_profile around `center`): `lobes` is a list of (Lobe, scale),
    scale 0 = flat onto the wall, 1 = unchanged. Where lobes overlap their
    scales are BLENDED (weighted mean once the weights sum past 1), not
    added: added, a slim fit (projection 0.63, width 0.47) cut 74% of the
    excess where the posterior and lateral lobes met, carving vertical
    creases into the hips. `weights[i]` in [0, 1] is point i's skin weight
    on the region's bones. `upper_wall`: see _wall_radius_at."""
    out = []
    for p, w in zip(points, weights):
        if w <= 0:
            out.append(tuple(p))
            continue
        theta, r = _polar(p, center)
        lobe_w = [(lobe.weight(p[2], theta), scale) for lobe, scale in lobes]
        total = sum(lw for lw, _s in lobe_w)
        change = sum((scale - 1) * lw for lw, scale in lobe_w) / max(1.0, total)
        excess = r - _wall_radius_at(wall, upper_wall, theta, p[2])
        if change == 0 or excess <= 0:
            out.append(tuple(p))
            continue
        new_r = r + excess * change * min(w, 1.0)
        out.append((center[0] + new_r * math.cos(theta), center[1] + new_r * math.sin(theta), p[2]))
    return out


def shift_bump(z, bottom_z, apex_z, top_z) -> float:
    """1 at apex_z, smoothly 0 at bottom_z and top_z (zero slope at all
    three), 0 outside."""
    if not bottom_z < z < top_z:
        return 0.0
    u = (z - bottom_z) / (apex_z - bottom_z) if z <= apex_z else (top_z - z) / (top_z - apex_z)
    return u * u * (3 - 2 * u)


def vertical_shift(points, weights, center, lobe, bottom_z, top_z, delta):
    """Pure logic (no bpy): move the bulge up (delta > 0) or down: each point
    rises by delta * shift_bump(z) * lobe's angular weight * skin weight --
    the surface between `bottom_z` and `top_z` is re-spread, nothing outside
    moves. Stays a monotonic remap of z while |delta| < (apex - bottom) / 1.5."""
    out = []
    for p, w in zip(points, weights):
        bump = shift_bump(p[2], bottom_z, lobe.apex_z, top_z)
        if w <= 0 or bump == 0:
            out.append(tuple(p))
            continue
        theta, _r = _polar(p, center)
        out.append((p[0], p[1], p[2] + delta * bump * lobe.weight(lobe.apex_z, theta) * min(w, 1.0)))
    return out


def mound_contract(points, weights, center, wall, upper_wall, lobe, apex_z, apex_excess, scale):
    """Pure logic (no bpy): scale the mound's HEIGHT on the chest around
    `apex_z` along with its projection. Each point moves toward apex_z by
    (1 - scale) * q, q = its excess over `wall` relative to the apex's
    (`apex_excess`), capped at 1 -- times lobe and skin weights. The tip
    (q ~ 1) shrinks like a scaled copy, the base (excess 0, on the ribcage)
    stays put. Without it, excess_scale() alone kept an 80G's 12 cm tall
    footprint at 80A: a vertical plateau (14.9 cm deep from z 0.30 to 0.38
    on CatalinaNude), seen as a flattened pancake."""
    out = []
    for p, w in zip(points, weights):
        if w <= 0:
            out.append(tuple(p))
            continue
        theta, r = _polar(p, center)
        q = min(1.0, max(0.0, (r - _wall_radius_at(wall, upper_wall, theta, p[2])) / apex_excess))
        k = (1 - scale) * q * lobe.weight(p[2], theta) * min(w, 1.0)
        out.append((p[0], p[1], p[2] + (apex_z - p[2]) * k) if k else tuple(p))
    return out


@dataclass(frozen=True)
class Deformation:
    """A fitted reshape as plain data (pure logic, no bpy): an optional
    vertical_shift() or mound_contract(), then an excess_scale(), on
    rest-pose points weighted by their skin weight on `prefixes` bones
    (region_weight). The fits bake it into the Blender meshes; the exporter
    (ped_package.py) replays the same one on every LOD of the shipped .ydd
    files, which Blender never imported. `shift` = (lobe, bottom_z, top_z,
    delta), `contract` = (lobe, apex_z, apex_excess, scale), `upper_wall`
    = (z0, z1, radii) (see _wall_radius_at), or None."""
    prefixes: tuple[str, ...]
    center: tuple[float, float]
    wall: tuple[float, ...]
    lobes: tuple[tuple[Lobe, float], ...]
    shift: tuple[Lobe, float, float, float] | None = None
    contract: tuple[Lobe, float, float, float] | None = None
    upper_wall: tuple[float, float, tuple[float, ...]] | None = None

    def apply(self, points, weights):
        if self.shift is not None:
            lobe, bottom_z, top_z, delta = self.shift
            points = vertical_shift(points, weights, self.center, lobe, bottom_z, top_z, delta)
        if self.contract is not None:
            points = mound_contract(points, weights, self.center, self.wall, self.upper_wall, *self.contract)
        return excess_scale(points, weights, self.center, self.wall, self.lobes, self.upper_wall)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Deformation":
        def lobe(x):
            return Lobe(x["axis_angle"], tuple(x["sector"]), x["bottom_z"], x["full_z"], x["apex_z"], x["top_z"])
        shift, contract, upper = d.get("shift"), d.get("contract"), d.get("upper_wall")
        return cls(tuple(d["prefixes"]), tuple(d["center"]), tuple(d["wall"]),
                   tuple((lobe(lb), scale) for lb, scale in d["lobes"]),
                   (lobe(shift[0]), *shift[1:]) if shift else None,
                   (lobe(contract[0]), *contract[1:]) if contract else None,
                   (upper[0], upper[1], tuple(upper[2])) if upper else None)


def region_weight(influences, prefixes) -> float:
    """Skin weight of one vertex on the bones named `prefixes*`, capped at 1;
    `influences` = (bone name, weight) pairs."""
    return min(1.0, sum(w for name, w in influences if name.startswith(prefixes)))


BUST_FIT_RANGE = (0.0, 2.0)
BUST_FIT_TOLERANCE_CM = 0.5
TORSO_BONE_PREFIXES = ("SKEL_Spine", "SKEL_Pelvis", "SKEL_Neck", "SKEL_L_Clavicle", "SKEL_R_Clavicle", "SPR_")


# --- Glutes -----------------------------------------------------------------
# No standard size exists for buttocks (unlike EN 13402 bra sizes). The
# gluteal-aesthetics literature measures three proportions, all used here:
# - LATERAL waist-to-hip ratio (profile): waist depth / depth at the point
#   of maximal gluteal projection. Female range ~0.60-0.95, ideal 0.70.
# - POSTERIOR waist-to-hip ratio (back view): waist width / widest hip
#   width. Female range ~0.60-0.80, ideal 0.65-0.75.
#   (photometric study "Physical attractiveness: analysis of buttocks
#   patterns"; "Redefining the Ideal Buttocks: A Population Analysis", PRS)
# - Vertical position of the most prominent point between the top of the
#   intergluteal cleft and the gluteal fold: 50:50 (midpoint) most often
#   rated ideal (same PRS study).
# The shape/height values are OUR convention on those ranges. CatalinaNude
# measures lateral 0.734, posterior 0.63, apex at 0.43.
GLUTE_SHAPES: dict[str, tuple[float, float]] = {  # shape -> (lateral, posterior) target ratios
    "slim": (0.85, 0.78),      # minces: flat, narrow hips
    "athletic": (0.78, 0.72),  # athletiques: moderate, firm
    "round": (0.70, 0.67),     # bombees: the literature's "ideal" proportions
    "large": (0.62, 0.60),     # grosses: low end of both observed ranges
}
GLUTE_HEIGHTS: dict[str, float] = {  # apex position from cleft top (0) to fold (1)
    "high": 0.40,              # fesses hautes / remontees
    "mid": 0.50,               # the literature's "ideal" midpoint
    "low": 0.60,               # fesses basses / tombantes
}
GLUTE_BONE_PREFIXES = TORSO_BONE_PREFIXES + ("SKEL_L_Thigh", "SKEL_R_Thigh", "RB_L_ThighRoll",
                                             "RB_R_ThighRoll", "SM_")
# Posterior lobe: projection. Lateral lobes: hip width, centered on the
# pure sides; they overlap the posterior one between 40 and 90 deg from the
# back, so the two scales are fitted alternately (coordinate descent).
# Posterior 35-70 deg: the flanks have no gluteal fold, so a posterior lobe
# reaching them (first 50-90) stopped dead at the fold height there and
# left a shelf on the outer thigh of an enlarged buttock; the flanks belong
# to the lateral lobes, which fade down the thigh.
GLUTE_SECTOR = (35.0, 70.0)
GLUTE_SIDE_SECTOR = (20.0, 50.0)
# Fade-in above the gluteal fold (13 cm below the apex on CatalinaNude).
# 2 cm (the bust value) made an enlarged buttock overhang the thigh with a
# sharp jagged crease at its lower outer edge; 6 cm still showed a defined
# crease, 10 cm a soft natural fold -- and slim stayed clean at all three.
GLUTE_FOLD_FADE = 0.10
# The hips are widest at the gluteal fold level (trochanters, CatalinaNude
# 40.3 cm) and there is no fold on the sides: the lateral lobes fade down
# the outer thigh instead, over this distance.
GLUTE_SIDE_THIGH_FADE = 0.15
GLUTE_FIT_RANGE = (0.0, 3.0)
GLUTE_FIT_TOLERANCE = 0.005        # on the ratios
GLUTE_FIT_ROUNDS = 3               # coordinate-descent rounds (projection / width)
GLUTE_SHIFT_RANGE = (-0.06, 0.06)  # m, vertical move of the apex
# The apex shift is spread from this far below the gluteal fold (the fold
# moves a little with it): with the fold pinned, lowering by 4.5 cm squeezed
# the lower buttock into a hard bulge at the fold.
GLUTE_SHIFT_BELOW_FOLD = 0.06
GLUTE_HEIGHT_TOLERANCE = 0.02      # on the apex position (~5 mm on CatalinaNude)
# Only the pelvis region is cut by the glute measurements: edges outside
# this z band (m around the SKEL_Pelvis head) are dropped once, up front.
GLUTE_REGION = (-0.40, 0.45)
GLUTE_APEX_SEARCH = (-0.15, 0.10)  # m around the SKEL_Pelvis head
WAIST_SEARCH = (0.08, 0.30)        # m above the glute apex: narrowest section = natural waist
CLEFT_MIN_DEPTH = 0.005            # m: midline this far behind the cheeks = still in the cleft
CLEFT_HALF_WIDTH = 0.015           # m around the midline


def glute_shape_label(lateral_whr: float) -> str:
    """Nearest GLUTE_SHAPES entry for a measured lateral waist-to-hip ratio."""
    return min(GLUTE_SHAPES, key=lambda shape: abs(GLUTE_SHAPES[shape][0] - lateral_whr))


def glute_height_label(apex_position: float) -> str:
    """Nearest GLUTE_HEIGHTS entry for a measured apex position."""
    return min(GLUTE_HEIGHTS, key=lambda h: abs(GLUTE_HEIGHTS[h] - apex_position))


def cleft_top_height(points, edges, apex_z, top_z, center_x, back, lateral, step=0.005):
    """Pure logic (no bpy): top of the intergluteal cleft -- walking up from
    the glute apex, the last height where the midline (within
    CLEFT_HALF_WIDTH of `center_x` along `lateral`) is at least
    CLEFT_MIN_DEPTH behind the cheeks. None if no cleft below `top_z`."""
    found, z = None, apex_z
    while z < top_z:
        section = section_xy(points, edges, z)
        rear = [x * back[0] + y * back[1] for x, y in section]
        mid = [x * back[0] + y * back[1] for x, y in section
               if abs(x * lateral[0] + y * lateral[1] - center_x) < CLEFT_HALF_WIDTH]
        if not rear or not mid or max(rear) - max(mid) < CLEFT_MIN_DEPTH:
            break
        found, z = z, z + step
    return found


def apply_body_morphology(armature, slider_values: dict[str, float]) -> dict:
    """bpy-dependent: applies `slider_values` (see MORPH_SLIDERS for valid
    keys) to `armature`'s pose bones (per-axis, see build_pose_bone_scale_map),
    then PERMANENTLY bakes the resulting deformation into each affected
    mesh's actual vertex data.

    PITFALL found 2026-10-12 (initial implementation used
    `bpy.ops.pose.armature_apply()`, which looked right but does something
    different): that operator redefines the ARMATURE's rest pose to match
    the current pose -- it does NOT re-bake mesh vertex data. Since the
    Armature modifier deforms a mesh based on "current pose relative to
    rest pose", and armature_apply makes pose == rest pose again, the net
    visible deformation silently becomes ZERO right after "applying" it
    (confirmed by a minimal repro: pose bone scale correctly resets to
    (1,1,1) as if it worked, but cube.data.vertices never changes at all).
    That operator is for a different use case (turning a posed animation
    frame into a new T-pose for retargeting), not permanent reshaping.

    The actually-correct approach, confirmed by the same repro once fixed:
    apply the ARMATURE MODIFIER itself on each affected mesh object
    (bpy.ops.object.modifier_apply) -- this bakes the live pose-induced
    deformation directly into that mesh's vertex coordinates. A fresh
    Armature modifier is then added back (same armature, same implicit
    per-bone vertex group binding -- vertex groups are mesh data and
    survive modifier_apply untouched) so Sollumz still recognizes the mesh
    as skinned for export. Pose bone scales are reset to 1.0 afterwards so
    the shape is the new static baseline, not re-applied cumulatively next
    time this function runs.

    KNOWN LIMITATION, not solved by this function: clothing component
    meshes (uppr/lowr/jbib/...) are skinned to the SAME skeleton but were
    authored for the UNSCALED proportions -- pushing a slider far from 1.0
    will make existing clothing clip/float relative to the new body shape.
    Keeping sliders close to 1.0 (within roughly +/-15%) minimizes this;
    a true fix would need to re-fit clothing meshes too (out of scope here).

    Returns {"applied_bones": {bone: (x,y,z), ...}, "skipped_bones": [...],
    "reshaped_meshes": [mesh_name, ...], "no_effect_bones": [...],
    "regional_bones": [...]} for the caller to log/report ("no_effect_bones":
    scaled bones with no vertex weight on them or their descendants and no
    regional fallback -- the slider changed nothing; "regional_bones": such
    bones handled by the REGIONAL_FALLBACK_RADII vertex-region scale).
    """
    import bpy  # local import: this module must stay importable without bpy
    # for the pure-logic half (build_pose_bone_scale_map) to be unit-tested.

    bone_scale_map = build_pose_bone_scale_map(slider_values)
    bone_names = {b.name for b in armature.data.bones}

    applied = {}
    skipped = []
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='POSE')
    for bone_name, scale in bone_scale_map.items():
        if bone_name not in bone_names:
            skipped.append(bone_name)
            continue
        pose_bone = armature.pose.bones[bone_name]
        pose_bone.scale = scale
        applied[bone_name] = scale
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()

    reshaped = []
    weighted_groups = set()
    for obj in list(armature.children):
        if obj.type != 'MESH':
            continue
        names = {g.index: g.name for g in obj.vertex_groups}
        weighted_groups.update(names[g.group] for v in obj.data.vertices for g in v.groups if g.weight > 0)
    # A scaled bone moves vertices weighted to it OR to any descendant bone
    # (e.g. SKEL_Pelvis drags the thighs). A bone with neither is a silent
    # no-op -- found 2026-10-12: a custom nude body skinned its breasts to
    # SKEL_Spine3 with no SPR_*_Breast weights, so bust_* sliders did nothing.
    no_effect = [
        name for name in applied
        if not ({name} | {c.name for c in armature.data.bones[name].children_recursive}) & weighted_groups
    ]
    for obj in list(armature.children):
        if obj.type != 'MESH':
            continue
        armature_mod = next((m for m in obj.modifiers if m.type == 'ARMATURE' and m.object is armature), None)
        if armature_mod is None:
            continue
        bpy.context.view_layer.objects.active = obj
        for o in bpy.data.objects:
            o.select_set(o is obj)
        bpy.ops.object.modifier_apply(modifier=armature_mod.name)
        new_mod = obj.modifiers.new("Armature", 'ARMATURE')
        new_mod.object = armature
        reshaped.append(obj.name)

    # The shape is now permanently baked into mesh data -- reset the pose so
    # it isn't cumulatively re-applied the next time this function runs.
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='POSE')
    for bone_name in bone_scale_map:
        if bone_name in bone_names:
            armature.pose.bones[bone_name].scale = (1.0, 1.0, 1.0)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Bones with no weighted vertices did nothing above; for those with a
    # regional fallback, apply their scale to the nearby mesh region instead
    # (rest pose now, so editing the base vertex coordinates is exact).
    meshes = _meshes(armature)
    _body, regions = _breast_regions(armature, meshes)
    regional_scales = [(r, applied[r["bone"]]) for r in regions if r["bone"] in no_effect]
    _apply_regions(meshes, regional_scales)
    regional = [r["bone"] for r, _scale in regional_scales]
    no_effect = [name for name in no_effect if name not in regional]

    return {"applied_bones": applied, "skipped_bones": skipped, "reshaped_meshes": reshaped,
            "no_effect_bones": no_effect, "regional_bones": regional}


def _meshes(armature):
    return [obj for obj in armature.children if obj.type == 'MESH' and len(obj.data.vertices)]


def _breast_regions(armature, meshes):
    """bpy: (body_mesh, regions) -- one region per REGIONAL_FALLBACK_RADII
    bone present in the skeleton: its axes, forward direction and the
    mesh-derived anchor (bust_region_anchor) on the body mesh, i.e. the
    mesh holding the vertex closest to the breast bone. All anchors are
    computed on the unmodified body, so the two breasts are independent."""
    regions, body, points = [], None, None
    for bone_name, (inner, outer) in REGIONAL_FALLBACK_RADII.items():
        bone = armature.data.bones.get(bone_name)
        if bone is None or not meshes:
            continue
        head = armature.matrix_world @ bone.head_local
        rot = armature.matrix_world.to_3x3() @ bone.matrix_local.to_3x3()
        axes = [tuple(rot.col[i].normalized()) for i in range(3)]
        forward = tuple(-c for c in axes[1])  # the breast bone points INTO the chest
        if body is None:
            body = min(meshes, key=lambda o: min((o.matrix_world @ v.co - head).length for v in o.data.vertices))
            points = [tuple(body.matrix_world @ v.co) for v in body.data.vertices]
        anchor = bust_region_anchor(points, tuple(head), forward)
        if anchor is None:
            logger.warning("no breast region found on %s for %s", body.name, bone_name)
            continue
        regions.append({"bone": bone_name, "head": tuple(head), "axes": axes, "forward": forward,
                        "anchor": anchor, "inner": inner, "outer": outer})
    return body, regions


def _apply_regions(meshes, region_scales):
    """bpy: bake regional_scale_points() for each (region, (x, y, z) scale)
    into every mesh's rest-pose vertex coordinates."""
    import mathutils
    if not region_scales:
        return
    for obj in meshes:
        world = [tuple(obj.matrix_world @ v.co) for v in obj.data.vertices]
        moved = world
        for region, scale in region_scales:
            moved = regional_scale_points(moved, region["anchor"], region["axes"], scale,
                                          region["inner"], region["outer"])
        to_local = obj.matrix_world.inverted()
        for v, old, new in zip(obj.data.vertices, world, moved):
            if new != old:
                v.co = to_local @ mathutils.Vector(new)
        obj.data.update()


def _region_weight(vertex, names, prefixes) -> float:
    return region_weight(((names[g.group], g.weight) for g in vertex.groups), prefixes)


def _samples(meshes, prefixes):
    """bpy: (points, weights, edges) over `meshes` -- world coordinates of
    the vertices whose DOMINANT bone starts with `prefixes` (arms/hands
    excluded: they would widen the tape), their total weight on those
    bones, and the mesh edges between kept vertices (for section_xy)."""
    points, weights, edges = [], [], []
    for obj in meshes:
        names = {g.index: g.name for g in obj.vertex_groups}
        index = {}
        for v in obj.data.vertices:
            dominant = max(v.groups, key=lambda g: g.weight, default=None)
            if dominant is not None and names[dominant.group].startswith(prefixes):
                index[v.index] = len(points)
                points.append(tuple(obj.matrix_world @ v.co))
                weights.append(_region_weight(v, names, prefixes))
        edges.extend((index[a], index[b]) for a, b in (e.vertices for e in obj.data.edges)
                     if a in index and b in index)
    return points, weights, edges


def _write(meshes, deformation: Deformation):
    """bpy: bake `deformation` into every mesh's rest-pose vertices."""
    import mathutils
    for obj in meshes:
        names = {g.index: g.name for g in obj.vertex_groups}
        world = [tuple(obj.matrix_world @ v.co) for v in obj.data.vertices]
        weights = [_region_weight(v, names, deformation.prefixes) for v in obj.data.vertices]
        moved = deformation.apply(world, weights)
        to_local = obj.matrix_world.inverted()
        for v, old, new in zip(obj.data.vertices, world, moved):
            if new != old:
                v.co = to_local @ mathutils.Vector(new)
        obj.data.update()


def _wall(points, edges, wall_z):
    """(center, wall_profile) of the section at `wall_z`."""
    section = section_xy(points, edges, wall_z)
    center = (sum(x for x, _ in section) / len(section), sum(y for _, y in section) / len(section))
    return center, wall_profile(section, center)


# --- Bust (bpy) -------------------------------------------------------------

def _bust_apex_z(torso, regions):
    apexes = [a for a in (bust_apex(torso, r["head"], r["forward"]) for r in regions) if a is not None]
    return sum(a[2] for a in apexes) / len(apexes) if apexes else None


def _measure_bust(torso, edges, regions):
    """{"underbust_cm", "bust_cm", "size", "apex_z", "fold_z"} or None."""
    apex_z = _bust_apex_z(torso, regions)
    if apex_z is None:
        return None
    fold_z = fold_height(torso, edges, apex_z, regions[0]["forward"])
    if fold_z is None:
        return None
    underbust = 100 * slice_circumference(torso, edges, fold_z)
    bust = 100 * slice_circumference(torso, edges, apex_z)
    return {"underbust_cm": round(underbust, 1), "bust_cm": round(bust, 1),
            "size": bra_size_label(underbust, bust), "apex_z": apex_z, "fold_z": fold_z}


def measure_chest(armature) -> dict | None:
    """bpy: tape-like underbust/bust circumferences of the ped's body mesh
    in rest pose, e.g. {"underbust_cm": 80.4, "bust_cm": 105.7, "size":
    "80G", ...}. None if no breast region or no inframammary fold is found."""
    meshes = _meshes(armature)
    body, regions = _breast_regions(armature, meshes)
    if not regions:
        return None
    torso, _weights, edges = _samples([body], TORSO_BONE_PREFIXES)
    return _measure_bust(torso, edges, regions)


def fit_bust(armature, bra_size: str) -> dict:
    """bpy: reshape the breasts until the MEASURED bust circumference matches
    EU size `bra_size` ("80A", or a cup alone like "B") on this body's own
    measured underbust (bust_target_cm), within BUST_FIT_TOLERANCE_CM.
    Closed loop instead of guessed values: bisection (fit_monotonic) over
    the excess scale of excess_scale() (wall = the measured underbust
    section), evaluated on in-memory torso points -- the meshes are written
    once, with the final scale, then re-measured.

    Returns {"base", "target_bust_cm", "scale", "reached", "result",
    "band_mismatch"} or {"error": ...}. reached=False means the target is
    outside BUST_FIT_RANGE: the closest achievable shape is applied and
    reported, never silently passed off as the requested size."""
    meshes = _meshes(armature)
    body, regions = _breast_regions(armature, meshes)
    if not regions:
        return {"error": "no breast region found on the body mesh"}
    torso, weights, edges = _samples([body], TORSO_BONE_PREFIXES)
    base = _measure_bust(torso, edges, regions)
    if base is None:
        return {"error": f"could not measure the chest of {body.name} (no apex/fold found)"}
    target = bust_target_cm(bra_size, base["underbust_cm"])
    center, wall = _wall(torso, edges, base["fold_z"])
    forward = regions[0]["forward"]
    lobe = Lobe(math.atan2(forward[1], forward[0]), BUST_SECTOR, base["fold_z"],
                min(base["fold_z"] + BUST_FOLD_FADE, base["apex_z"]), base["apex_z"],
                base["apex_z"] + ABOVE_APEX_FADE)

    # The ribcage under the breast recedes going up (CatalinaNude, nipple
    # line: 13.5 cm forward at the fold, 7.9 at z 0.45): a vertical wall at
    # the underbust left that slope counted as breast and turned small
    # sizes into a vertical plateau. Slope the wall up to the section where
    # the lobe stops (untouched upper chest).
    upper_z = lobe.top_z
    upper_wall = (base["fold_z"], upper_z, tuple(wall_profile(section_xy(torso, edges, upper_z), center)))
    # the apex's excess over the wall (both breasts' mean): mound_contract's
    # reference, so the tip shrinks in height as much as in projection
    apexes = [a for a in (bust_apex(torso, r["head"], r["forward"]) for r in regions) if a is not None]
    apex_excess = sum(_polar(a, center)[1] - _wall_radius_at(wall, upper_wall, _polar(a, center)[0], a[2])
                      for a in apexes) / len(apexes)
    if apex_excess <= 0:
        return {"error": f"the bust apex of {body.name} doesn't stand out of its ribcage wall"}

    def deformation(s):
        return Deformation(TORSO_BONE_PREFIXES, center, tuple(wall), ((lobe, s),),
                           contract=(lobe, base["apex_z"], apex_excess, s), upper_wall=upper_wall)

    # Measured at the BASE apex/fold heights throughout (a tape kept at the
    # original nipple height): the most-forward point drops as the breast
    # flattens (z 0.334 -> 0.291 on CatalinaNude), which made a re-detected
    # apex height non-monotonic in the scale and broke the bisection.

    scale, _predicted, reached = fit_monotonic(
        lambda s: 100 * slice_circumference(deformation(s).apply(torso, weights), edges, base["apex_z"]),
        target, *BUST_FIT_RANGE, BUST_FIT_TOLERANCE_CM)
    _write(meshes, deformation(scale))

    final = _samples([body], TORSO_BONE_PREFIXES)[0]  # re-read from the written mesh
    underbust = 100 * slice_circumference(final, edges, base["fold_z"])
    bust = 100 * slice_circumference(final, edges, base["apex_z"])
    result = {"underbust_cm": round(underbust, 1), "bust_cm": round(bust, 1),
              "size": bra_size_label(underbust, bust)}
    return {"base": {k: base[k] for k in ("underbust_cm", "bust_cm", "size")},
            "target_bust_cm": round(target, 1), "scale": round(scale, 3), "reached": reached,
            "result": result, "band_mismatch": bra_band_mismatch(bra_size, base["underbust_cm"]),
            "deformation": deformation(scale)}


# --- Glutes (bpy) -----------------------------------------------------------

def _body_meshes(armature):
    """The skin meshes the hips span: the uppr (torso) and lowr (legs)
    components, recognized by their <comp>_diff_ material."""
    import blender_rig_export as bre
    return [o for o in _meshes(armature)
            if (bre.classify_body_component(o) or ("",))[0] in ("uppr", "lowr")]


def _forward(armature):
    """World forward of the ped (GTA peds face -y in armature space)."""
    import mathutils
    f = armature.matrix_world.to_3x3() @ mathutils.Vector((0.0, -1.0, 0.0))
    f.z = 0.0
    f.normalize()
    return (f.x, f.y, 0.0)


def _rear_apex(points, edges, pelvis_z, back):
    lo, hi = GLUTE_APEX_SEARCH
    rear = [(slice_extent(points, edges, pelvis_z + lo + 0.005 * k, back), pelvis_z + lo + 0.005 * k)
            for k in range(int(round((hi - lo) / 0.005)) + 1)]
    rear = [(r, z) for r, z in rear if r is not None]
    return max(rear)[1] if rear else None


def _width(points, edges, z, lateral):
    return slice_depth(points, edges, z, lateral)


def _widest(points, edges, fold_z, cleft_z, lateral):
    """(width, height) of the widest hip section between fold and cleft top
    -- the posterior ratio's "hip width", wherever it ends up after a fit."""
    return max((_width(points, edges, fold_z + 0.005 * k, lateral) or 0.0, fold_z + 0.005 * k)
               for k in range(int((cleft_z - fold_z) / 0.005) + 1))


def _measure_hips(points, edges, pelvis_z, forward, fixed=None):
    """Glute proportions (see GLUTE_SHAPES/GLUTE_HEIGHTS): {"waist_cm",
    "hips_cm", "waist_depth_cm", "hip_depth_cm", "lateral_whr",
    "waist_width_cm", "hip_width_cm", "posterior_whr", "apex_position",
    "shape", "height"} + the landmark heights ("apex_z", "fold_z",
    "waist_z", "cleft_z", "width_z"), each detected unless given in `fixed`.
    After a fit the gluteal fold is passed in: no deformation moves it (both
    fade to 0 there), but a stretched lower buttock recedes less per cm and
    fold_height() then triggers too early or not at all. None if a landmark
    isn't found."""
    back = tuple(-c for c in forward)
    lateral = (-forward[1], forward[0], 0.0)
    h = dict(fixed or {})
    if "apex_z" not in h:
        h["apex_z"] = _rear_apex(points, edges, pelvis_z, back)
    if h["apex_z"] is None:
        return None
    if "fold_z" not in h:
        h["fold_z"] = fold_height(points, edges, h["apex_z"], back)
    if "waist_z" not in h:
        lo, hi = WAIST_SEARCH
        waists = [(slice_circumference(points, edges, h["apex_z"] + lo + 0.01 * k), h["apex_z"] + lo + 0.01 * k)
                  for k in range(int(round((hi - lo) / 0.01)) + 1)]
        waists = [(c, z) for c, z in waists if c > 0]
        h["waist_z"] = min(waists)[1] if waists else None
    if h["fold_z"] is None or h["waist_z"] is None:
        return None
    if "cleft_z" not in h:
        center, _wall_radii = _wall(points, edges, h["waist_z"])
        h["cleft_z"] = cleft_top_height(points, edges, h["apex_z"], h["waist_z"],
                                        center[0] * lateral[0] + center[1] * lateral[1], back, lateral)
    if h["cleft_z"] is None:
        return None
    if "width_z" not in h:
        h["width_z"] = _widest(points, edges, h["fold_z"], h["cleft_z"], lateral)[1]
    waist_depth = slice_depth(points, edges, h["waist_z"], forward)
    hip_depth = slice_depth(points, edges, h["apex_z"], forward)
    waist_width = _width(points, edges, h["waist_z"], lateral)
    hip_width = _width(points, edges, h["width_z"], lateral)
    if not (waist_depth and hip_depth and waist_width and hip_width):
        return None
    lateral_whr, posterior_whr = waist_depth / hip_depth, waist_width / hip_width
    apex_position = (h["cleft_z"] - h["apex_z"]) / (h["cleft_z"] - h["fold_z"])
    return {"waist_cm": round(100 * slice_circumference(points, edges, h["waist_z"]), 1),
            "hips_cm": round(100 * slice_circumference(points, edges, h["apex_z"]), 1),
            "waist_depth_cm": round(100 * waist_depth, 1), "hip_depth_cm": round(100 * hip_depth, 1),
            "lateral_whr": round(lateral_whr, 3),
            "waist_width_cm": round(100 * waist_width, 1), "hip_width_cm": round(100 * hip_width, 1),
            "posterior_whr": round(posterior_whr, 3),
            "apex_position": round(apex_position, 2),
            "shape": glute_shape_label(lateral_whr), "height": glute_height_label(apex_position),
            **h}


_PUBLIC_HIP_KEYS = ("waist_cm", "hips_cm", "waist_depth_cm", "hip_depth_cm", "lateral_whr",
                    "waist_width_cm", "hip_width_cm", "posterior_whr", "apex_position", "shape", "height")


def _public(m):
    return {k: m[k] for k in _PUBLIC_HIP_KEYS}


def _glute_setup(armature):
    pelvis = armature.data.bones.get("SKEL_Pelvis")
    meshes = _body_meshes(armature)
    if pelvis is None or not meshes:
        return None
    return meshes, (armature.matrix_world @ pelvis.head_local).z, _forward(armature)


def measure_hips(armature) -> dict | None:
    """bpy: glute proportions of the ped's body in rest pose (see
    _measure_hips). None if no pelvis bone or a landmark isn't found."""
    setup = _glute_setup(armature)
    if setup is None:
        return None
    meshes, pelvis_z, forward = setup
    points, _weights, edges = _samples(meshes, GLUTE_BONE_PREFIXES)
    m = _measure_hips(points, edges, pelvis_z, forward)
    return _public(m) if m else None


def _glute_lobes(m, forward):
    """(posterior, left, right) Lobes for measured heights `m`."""
    back_angle = math.atan2(-forward[1], -forward[0])
    rear = Lobe(back_angle, GLUTE_SECTOR, m["fold_z"], min(m["fold_z"] + GLUTE_FOLD_FADE, m["apex_z"]),
                m["apex_z"], m["waist_z"])
    sides = [Lobe(back_angle + sign * math.pi / 2, GLUTE_SIDE_SECTOR, m["width_z"] - GLUTE_SIDE_THIGH_FADE,
                  m["width_z"], m["width_z"], m["waist_z"]) for sign in (1, -1)]
    return rear, sides


def fit_glutes(armature, shape: str | None = None, height: str | None = None) -> dict:
    """bpy: reshape the buttocks to GLUTE_SHAPES[shape] (lateral AND
    posterior waist-to-hip ratios) and/or GLUTE_HEIGHTS[height] (apex
    position), each within tolerance -- same closed loop as fit_bust():
    measure, solve in memory, write the meshes once, re-measure.
    1. height: vertical_shift() of the posterior bulge, delta bisected on
       the re-detected apex height.
    2. shape: excess_scale() with the posterior lobe (projection -> lateral
       ratio, measured at the apex height) and the two lateral lobes (hip
       width -> posterior ratio, measured at the widest-hip height), wall =
       the waist section; the two scales are bisected alternately
       (GLUTE_FIT_ROUNDS) since the lobes overlap.
    The waist and everything below the lobes' fades don't move. Returns
    {"base", "targets", "scales", "shift_cm", "reached", "result"} or
    {"error": ...}; reached=False = closest shape applied."""
    if shape is not None and shape not in GLUTE_SHAPES:
        raise ValueError(f"unknown glute shape {shape!r}, expected one of {list(GLUTE_SHAPES)}")
    if height is not None and height not in GLUTE_HEIGHTS:
        raise ValueError(f"unknown glute height {height!r}, expected one of {list(GLUTE_HEIGHTS)}")
    setup = _glute_setup(armature)
    if setup is None:
        return {"error": "no SKEL_Pelvis bone or no uppr/lowr body mesh"}
    meshes, pelvis_z, forward = setup
    points, weights, edges = _samples(meshes, GLUTE_BONE_PREFIXES)
    lo, hi = pelvis_z + GLUTE_REGION[0], pelvis_z + GLUTE_REGION[1]
    edges = [(a, b) for a, b in edges if lo < points[a][2] < hi or lo < points[b][2] < hi]
    base = _measure_hips(points, edges, pelvis_z, forward)
    if base is None:
        return {"error": "could not measure the hips (no glute apex, gluteal fold, waist or cleft found)"}
    center, wall = _wall(points, edges, base["waist_z"])
    back = tuple(-c for c in forward)
    rear, _sides = _glute_lobes(base, forward)
    reached, targets, delta = True, {}, 0.0

    shift_bottom = base["fold_z"] - GLUTE_SHIFT_BELOW_FOLD

    def shifted(pts, w, d):
        return vertical_shift(pts, w, center, rear, shift_bottom, base["waist_z"], d) if d else pts

    def fold_after(d):
        # the gluteal fold is on the posterior midline (full angular weight):
        # its shifted height is exact, no need to re-detect it (see
        # _measure_hips for why re-detection fails after a fit)
        return {"fold_z": base["fold_z"] + d * shift_bump(base["fold_z"], shift_bottom, base["apex_z"],
                                                          base["waist_z"])}

    scales = {"projection": 1.0, "width": 1.0}
    lobes_at = {"heights": base}

    def lobes(sc):
        rear_lobe, sides = _glute_lobes(lobes_at["heights"], forward)
        return [(rear_lobe, sc["projection"])] + [(side, sc["width"]) for side in sides]

    def deformation(d, sc):
        return Deformation(GLUTE_BONE_PREFIXES, center, tuple(wall), tuple(lobes(sc)),
                           (rear, shift_bottom, base["waist_z"], d) if d else None)

    def transformed(d, sc):
        return deformation(d, sc).apply(points, weights)

    def fit_height():
        # Fully re-measured at each step: the shift also moves the cleft top
        # (on the midline), and the shape lobes move the most prominent point
        # a little, so the apex height alone isn't the proportion. Position
        # decreases as the bulge rises: bisect on its negation.
        def position(d):
            m = _measure_hips(transformed(d, scales), edges, pelvis_z, forward, fold_after(d))
            return -m["apex_position"] if m else 0.0
        d, _p, ok = fit_monotonic(position, -GLUTE_HEIGHTS[height], *GLUTE_SHIFT_RANGE, GLUTE_HEIGHT_TOLERANCE)
        return d, ok

    if height is not None:
        targets["apex_position"] = GLUTE_HEIGHTS[height]
        delta, ok_height = fit_height()
    if delta:
        # the shape lobes follow the moved bulge (fold fixed by construction)
        lobes_at["heights"] = _measure_hips(shifted(points, weights, delta), edges, pelvis_z, forward,
                                            fold_after(delta))
        if lobes_at["heights"] is None:
            return {"error": "could not measure the hips after the apex shift"}
    current = lobes_at["heights"]

    if shape is not None:
        lateral_target, posterior_target = GLUTE_SHAPES[shape]
        targets.update(lateral_whr=lateral_target, posterior_whr=posterior_target)
        lateral = (-forward[1], forward[0], 0.0)
        waist_depth = slice_depth(points, edges, current["waist_z"], forward)
        waist_width = _width(points, edges, current["waist_z"], lateral)
        ok_depth = ok_width = True
        # Coordinate descent: the posterior and lateral lobes overlap, and
        # both move the most prominent point, so the apex shift (if asked)
        # and the scales are re-bisected in turn; the depth is always taken
        # at the CURRENT most prominent point, the ratios last (they are the
        # tighter targets).
        for _ in range(GLUTE_FIT_ROUNDS):
            if height is not None:
                delta, ok_height = fit_height()
            apex_z = _rear_apex(transformed(delta, scales), edges, pelvis_z, back)
            scales["projection"], _v, ok_depth = fit_monotonic(
                lambda s: slice_depth(transformed(delta, {**scales, "projection": s}), edges, apex_z, forward),
                waist_depth / lateral_target, *GLUTE_FIT_RANGE,
                GLUTE_FIT_TOLERANCE * waist_depth / lateral_target ** 2)
            scales["width"], _v, ok_width = fit_monotonic(
                lambda s: _widest(transformed(delta, {**scales, "width": s}),
                                  edges, current["fold_z"], current["cleft_z"], lateral)[0],
                waist_width / posterior_target, *GLUTE_FIT_RANGE,
                GLUTE_FIT_TOLERANCE * waist_width / posterior_target ** 2)
        reached &= ok_depth and ok_width
    if height is not None:
        reached &= ok_height

    applied = deformation(delta, scales)
    _write(_meshes(armature), applied)
    final = _samples(meshes, GLUTE_BONE_PREFIXES)[0]
    result = _measure_hips(final, edges, pelvis_z, forward, fold_after(delta))
    return {"base": _public(base), "targets": targets,
            "scales": {k: round(v, 3) for k, v in scales.items()}, "shift_cm": round(100 * delta, 1),
            "reached": reached, "result": _public(result) if result else None, "deformation": applied}
