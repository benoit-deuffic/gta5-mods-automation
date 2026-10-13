"""Tests the pure-logic half of the measured body fits in
scripts/body_morphology.py (cross-sections, tape measure, folds, EU bra
sizes, glute shapes, bisection, excess-over-a-wall deformation).
fit_bust()/fit_glutes() themselves (bpy) were verified for real on
CatalinaNude -- see docs/architecture-decisions.md.
"""
import math

import pytest

from scripts.body_morphology import (
    GLUTE_HEIGHTS, GLUTE_SHAPES, Lobe, bra_band_mismatch, bra_size_label, bust_target_cm,
    cleft_top_height, excess_scale, fit_monotonic, mound_contract, fold_height, glute_height_label, glute_shape_label,
    hull_perimeter, parse_bra_size, section_xy, slice_circumference, slice_depth, slice_extent,
    shift_bump, vertical_shift, wall_profile,
)


def _tube(radius_at, heights, n=36):
    """A closed tube mesh: one ring of `n` vertices per height (radius
    radius_at(z)), ring edges + vertical edges between consecutive rings."""
    points, edges = [], []
    for k, z in enumerate(heights):
        r = radius_at(z)
        points += [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z) for i in range(n)]
        edges += [(k * n + i, k * n + (i + 1) % n) for i in range(n)]
        if k:
            edges += [((k - 1) * n + i, k * n + i) for i in range(n)]
    return points, edges


def _ring_xy(radius, n=72):
    return [(radius * math.cos(2 * math.pi * i / n), radius * math.sin(2 * math.pi * i / n)) for i in range(n)]


def test_hull_perimeter_is_a_tape_bridging_concavities():
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert hull_perimeter(square) == pytest.approx(4.0)
    # a notch (the gap between the breasts) doesn't shorten the tape
    assert hull_perimeter(square + [(0.5, 0.9)]) == pytest.approx(4.0)
    assert hull_perimeter([(0, 0), (1, 1)]) == 0.0


def test_sections_interpolate_between_coarse_rings():
    # rings only every 10 cm (like the coarse lowr mesh): a plane between
    # two rings still gets a full section, interpolated along the edges
    points, edges = _tube(lambda z: 0.1 + z, [0.0, 0.1, 0.2], n=72)
    assert len(section_xy(points, edges, 0.15)) == 72
    assert slice_circumference(points, edges, 0.15) == pytest.approx(2 * math.pi * 0.25, rel=0.01)
    assert slice_extent(points, edges, 0.15, (0.0, -1.0, 0.0)) == pytest.approx(0.25, rel=0.01)
    assert slice_depth(points, edges, 0.15, (0.0, -1.0, 0.0)) == pytest.approx(0.5, rel=0.01)
    assert section_xy(points, edges, 0.5) == [] and slice_extent(points, edges, 0.5, (1, 0, 0)) is None


def test_fold_is_where_the_surface_stops_receding_below_the_apex():
    # surface reach per height: bulge from z 0.33 down to the fold
    reach = {0.33: 0.185, 0.32: 0.18, 0.31: 0.17, 0.30: 0.16, 0.29: 0.152, 0.28: 0.147,
             0.27: 0.145, 0.26: 0.144, 0.25: 0.143, 0.24: 0.142}
    heights = sorted(reach)
    points, edges = _tube(lambda z: reach[round(z, 2)], heights)
    forward = (0.0, -1.0, 0.0)
    # 0.28: drop 3.8 cm from the apex and the next step moves only 2 mm (< 3 mm)
    assert fold_height(points, edges, 0.33, forward) == pytest.approx(0.28)
    flat_points, flat_edges = _tube(lambda z: 0.1, heights)
    assert fold_height(flat_points, flat_edges, 0.33, forward) is None  # no bulge, no fold


def test_eu_bra_sizes():
    assert parse_bra_size("80A") == (80, "A")
    assert parse_bra_size(" 75aa ") == (75, "AA")
    assert parse_bra_size("B") == (None, "B")
    for bad in ("", "80", "80Z", "bonnet 80A"):
        with pytest.raises(ValueError):
            parse_bra_size(bad)
    # target uses the MEASURED underbust + middle of the cup range (A = 12-14 cm)
    assert bust_target_cm("80A", 80.4) == pytest.approx(93.4)
    assert bust_target_cm("C", 75.0) == pytest.approx(92.0)
    assert bra_size_label(80.4, 105.7) == "80G"
    assert bra_size_label(80.2, 93.6) == "80A"
    assert bra_size_label(80.0, 85.0) == "80<AA"
    assert not bra_band_mismatch("80A", 80.4)
    assert bra_band_mismatch("90A", 80.4)
    assert not bra_band_mismatch("A", 80.4)


def test_glute_targets_order_from_flat_to_large_and_high_to_low():
    # both waist-to-hip ratios drop as the buttocks grow (more projection, wider hips)
    order = ("slim", "athletic", "round", "large")
    for i in (0, 1):
        ratios = [GLUTE_SHAPES[s][i] for s in order]
        assert ratios == sorted(ratios, reverse=True)
    assert glute_shape_label(0.734) == "round"  # CatalinaNude as imported
    assert glute_shape_label(0.95) == "slim" and glute_shape_label(0.5) == "large"
    positions = [GLUTE_HEIGHTS[h] for h in ("high", "mid", "low")]  # from the cleft top down
    assert positions == sorted(positions)
    assert glute_height_label(0.45) in ("high", "mid") and glute_height_label(0.62) == "low"


def test_fit_monotonic_reaches_target_or_reports_closest_bound():
    x, value, reached = fit_monotonic(lambda s: 80 + 25 * s, 93.4, 0.0, 2.0, 0.1)
    assert reached and value == pytest.approx(93.4, abs=0.1)
    x, value, reached = fit_monotonic(lambda s: 90 + 10 * s, 85.0, 0.0, 2.0, 0.1)
    assert (x, value, reached) == (0.0, 90.0, False)


FRONT = -math.pi / 2  # bulge toward -y (the ped's front)


def _wall_kwargs():
    center = (0.0, 0.0)
    return dict(center=center, wall=wall_profile(_ring_xy(0.12), center))


def _lobe(fold_fade=0.02, axis=FRONT, sector=(80.0, 120.0)):
    return Lobe(axis, sector, bottom_z=0.26, full_z=0.26 + fold_fade, apex_z=0.33, top_z=0.46)


def test_excess_scale_moves_only_the_bulge_over_the_wall():
    front = (0.0, -0.18, 0.33)       # at the apex height: 6 cm of excess over the 12 cm wall
    back = (0.0, 0.18, 0.33)         # opposite side: untouched
    above = (0.0, -0.18, 0.47)       # beyond top_z: untouched
    below_fold = (0.0, -0.18, 0.25)  # under the fold: untouched
    on_wall = (0.0, -0.10, 0.33)     # inside the wall: untouched
    arm = (0.0, -0.18, 0.33)         # same spot but no weight on the region's bones
    points = [front, back, above, on_wall, arm, below_fold]
    weights = [1.0, 1.0, 1.0, 1.0, 0.0, 1.0]

    def run(scale):
        return excess_scale(points, weights, lobes=[(_lobe(), scale)], **_wall_kwargs())
    assert run(0.0)[0] == pytest.approx((0.0, -0.12, 0.33))
    assert run(0.0)[1:] == points[1:]
    assert run(0.5)[0] == pytest.approx((0.0, -0.15, 0.33))
    assert run(1.0)[0] == pytest.approx(front)
    assert run(2.0)[0] == pytest.approx((0.0, -0.24, 0.33))


def test_excess_scale_lobes_act_on_their_own_direction():
    # a posterior lobe (projection) and a lateral one (width) on the same body
    rear = _lobe(axis=math.pi / 2, sector=(50.0, 90.0))
    side = _lobe(axis=0.0, sector=(20.0, 50.0))
    behind, beside = (0.0, 0.18, 0.33), (0.18, 0.0, 0.33)
    out = excess_scale([behind, beside], [1.0, 1.0], lobes=[(rear, 2.0), (side, 0.5)], **_wall_kwargs())
    assert out[0] == pytest.approx((0.0, 0.24, 0.33))   # pushed back, width lobe silent there
    assert out[1] == pytest.approx((0.15, 0.0, 0.33))   # pulled in, projection lobe silent there


def test_overlapping_lobes_blend_instead_of_adding_up():
    # two lobes both at full weight on the same point: the result must stay
    # between their two effects (added, 0.6 + 0.4 reduction = 100% = a dent)
    a, b = _lobe(axis=FRONT), _lobe(axis=FRONT)
    out = excess_scale([(0.0, -0.18, 0.33)], [1.0], lobes=[(a, 0.4), (b, 0.6)], **_wall_kwargs())
    assert out[0] == pytest.approx((0.0, -0.15, 0.33))   # mean scale 0.5 of the 6 cm excess


def test_shift_bump_is_one_at_apex_and_zero_at_the_ends():
    assert shift_bump(0.33, 0.20, 0.33, 0.46) == pytest.approx(1.0)
    assert shift_bump(0.20, 0.20, 0.33, 0.46) == 0.0 and shift_bump(0.46, 0.20, 0.33, 0.46) == 0.0
    assert shift_bump(0.10, 0.20, 0.33, 0.46) == 0.0
    assert 0.0 < shift_bump(0.25, 0.20, 0.33, 0.46) < 1.0


def test_excess_scale_fold_fade_softens_the_bottom_of_the_bulge():
    near_fold = [(0.0, -0.18, 0.28)]  # 2 cm above the fold
    short = excess_scale(near_fold, [1.0], lobes=[(_lobe(0.02), 2.0)], **_wall_kwargs())
    long = excess_scale(near_fold, [1.0], lobes=[(_lobe(0.10), 2.0)], **_wall_kwargs())
    assert short[0][1] == pytest.approx(-0.24)   # full effect already
    assert -0.24 < long[0][1] < -0.18             # partial: no overhanging ledge


def test_vertical_shift_moves_the_apex_and_keeps_the_ends():
    lobe = _lobe(axis=math.pi / 2, sector=(50.0, 90.0))   # posterior, apex at 0.33
    pts = [(0.0, 0.18, 0.33), (0.0, 0.18, 0.26), (0.0, 0.18, 0.46), (0.0, -0.18, 0.33), (0.0, 0.18, 0.30)]
    out = vertical_shift(pts, [1.0] * 5, (0.0, 0.0), lobe, bottom_z=0.26, top_z=0.46, delta=0.02)
    assert out[0][2] == pytest.approx(0.35)          # apex moved by delta
    assert out[1] == pts[1] and out[2] == pts[2]     # fold and top fixed
    assert out[3] == pts[3]                          # front untouched
    assert 0.30 < out[4][2] < 0.35 and out[4][2] < out[0][2]  # re-spread, order kept


def test_cleft_top_is_where_the_midline_stops_lying_behind_the_cheeks():
    # two cheek tubes (x = +/-0.06) and a midline groove, merged above z 0.05
    points, edges = [], []
    for k, z in enumerate([0.0, 0.02, 0.04, 0.06, 0.08]):
        groove = 0.02 if z < 0.05 else 0.0
        ring = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (0.06, 0.12), (0.0, 0.12 - groove), (-0.06, 0.12), (-0.1, 0.1)]
        base = len(points)
        points += [(x, y, z) for x, y in ring]
        edges += [(base + i, base + (i + 1) % len(ring)) for i in range(len(ring))]
        if k:
            edges += [(base - len(ring) + i, base + i) for i in range(len(ring))]
    top = cleft_top_height(points, edges, 0.0, 0.08, 0.0, (0.0, 1.0, 0.0), (1.0, 0.0, 0.0), step=0.01)
    assert 0.03 <= top <= 0.05


def test_zero_fold_fade_leaves_everything_below_the_fold_alone():
    lobe = _lobe(fold_fade=0.0)
    assert lobe.weight(0.25, FRONT) == 0.0
    assert lobe.weight(0.27, FRONT) == 1.0


def test_sloped_wall_counts_only_what_stands_out_of_a_receding_chest():
    # 18 cm forward at the apex (z 0.33, full lobe weight), 35% of the way
    # from the fold (0.26) to 0.46
    point = [(0.0, -0.18, 0.33)]
    kwargs = _wall_kwargs()  # 12 cm wall at the fold
    lobes = [(_lobe(), 0.5)]

    def sloped_to(radius):
        return (0.26, 0.46, tuple(wall_profile(_ring_xy(radius), kwargs["center"])))

    vertical = excess_scale(point, [1.0], lobes=lobes, **kwargs)[0]
    sloped = excess_scale(point, [1.0], lobes=lobes, upper_wall=sloped_to(0.20), **kwargs)[0]
    assert vertical[1] == pytest.approx(-0.15)  # 6 cm of excess halved
    assert sloped[1] == pytest.approx(-0.18 + 0.5 * (0.18 - 0.148))  # wall 14.8 cm there
    # a wall reaching 18 cm at that height: nothing stands out, nothing moves
    on_wall = excess_scale(point, [1.0], lobes=lobes, upper_wall=sloped_to(0.12 + 0.06 / 0.35), **kwargs)[0]
    assert on_wall == pytest.approx(point[0])


def test_mound_contract_shrinks_the_tip_toward_the_apex_and_keeps_the_base():
    kwargs = _wall_kwargs()
    tip_below = (0.0, -0.18, 0.30)    # full apex excess (6 cm), 3 cm under the apex
    base_below = (0.0, -0.12, 0.30)   # on the wall: no excess
    out = mound_contract([tip_below, base_below], [1.0, 1.0], kwargs["center"], kwargs["wall"], None,
                         _lobe(0.0), 0.33, 0.06, 0.5)
    assert out[0][2] == pytest.approx(0.315)  # half-way to the apex
    assert out[1] == base_below
